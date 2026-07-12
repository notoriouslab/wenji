"""Tests for search.* config wiring (0.5.0): WENJI_CONFIG env, --config flag,
default-parity lock, and per-request limit precedence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wenji.cli import app as cli_app
from wenji.config import load_config, resolve_config_path
from wenji.config.defaults import DEFAULT_SEARCH_CONFIG
from wenji.core.errors import ConfigError
from wenji.search.hybrid import DEFAULT_ALPHA
from wenji.web.app import create_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
    monkeypatch.delenv("WENJI_CONFIG", raising=False)


def _write_yaml(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _file_db(populated_db, tmp_path: Path) -> Path:
    file_db = tmp_path / "wenji.db"
    backup = sqlite3.connect(str(file_db))
    populated_db.backup(backup)
    backup.close()
    return file_db


# ----- resolution order -----


def test_resolve_config_path_flag_beats_env(monkeypatch, tmp_path):
    env_yaml = _write_yaml(tmp_path, "env.yaml", "search:\n  alpha: 0.9\n")
    flag_yaml = _write_yaml(tmp_path, "flag.yaml", "search:\n  alpha: 0.5\n")
    monkeypatch.setenv("WENJI_CONFIG", str(env_yaml))
    assert resolve_config_path(flag_yaml) == flag_yaml
    cfg = load_config(resolve_config_path(flag_yaml))
    assert cfg.search.alpha == 0.5


def test_resolve_config_path_env_when_no_flag(monkeypatch, tmp_path):
    env_yaml = _write_yaml(tmp_path, "env.yaml", "search:\n  alpha: 0.9\n")
    monkeypatch.setenv("WENJI_CONFIG", str(env_yaml))
    cfg = load_config(resolve_config_path(None))
    assert cfg.search.alpha == 0.9


def test_resolve_config_path_defaults_when_unset():
    assert resolve_config_path(None) is None


def test_no_config_defaults_equal_0_4_hardcoded_values():
    """Parity lock: all-defaults config must reproduce the pre-0.5 constants."""
    cfg = load_config(None).search
    assert cfg.alpha == DEFAULT_ALPHA == 0.25
    assert cfg.candidate_pool == 50
    assert cfg.default_limit == 10
    assert DEFAULT_SEARCH_CONFIG == {
        "alpha": 0.25,
        "candidate_pool": 50,
        "default_limit": 10,
    }


# ----- web factory injection -----


def test_web_factory_injects_config_into_searcher(monkeypatch, tmp_path, populated_db):
    yaml_path = _write_yaml(tmp_path, "w.yaml", "search:\n  alpha: 0.9\n  default_limit: 3\n")
    monkeypatch.setenv("WENJI_CONFIG", str(yaml_path))

    captured: dict = {}

    class SpySearcher:
        def __init__(self, conn, embedder, **kwargs):
            captured.update(kwargs)

        def search(self, query, *, axis=None, limit=10):
            captured["limit"] = limit
            return []

    class FakeEmbedder:
        DIM = 8

    import wenji.ingest.embed as embed_mod
    import wenji.web.app as app_mod

    monkeypatch.setattr(app_mod, "Searcher", SpySearcher)
    monkeypatch.setattr(embed_mod, "Embedder", FakeEmbedder)

    app = create_app(db_path=_file_db(populated_db, tmp_path), searcher=None)
    client = TestClient(app)
    client.get("/api/search?q=禱告")

    assert captured.get("alpha") == 0.9
    assert captured.get("candidate_pool") == 50  # unset key keeps default
    assert captured.get("limit") == 3  # default_limit applied when unset


def test_web_explicit_limit_beats_config_default(monkeypatch, tmp_path, populated_db):
    yaml_path = _write_yaml(tmp_path, "w.yaml", "search:\n  default_limit: 3\n")
    monkeypatch.setenv("WENJI_CONFIG", str(yaml_path))

    captured: dict = {}

    class SpySearcher:
        def __init__(self, conn, embedder, **kwargs):
            pass

        def search(self, query, *, axis=None, limit=10):
            captured["limit"] = limit
            return []

    class FakeEmbedder:
        DIM = 8

    import wenji.ingest.embed as embed_mod
    import wenji.web.app as app_mod

    monkeypatch.setattr(app_mod, "Searcher", SpySearcher)
    monkeypatch.setattr(embed_mod, "Embedder", FakeEmbedder)

    app = create_app(db_path=_file_db(populated_db, tmp_path), searcher=None)
    client = TestClient(app)
    client.get("/api/search?q=禱告&limit=7")
    assert captured["limit"] == 7


def test_web_broken_config_fails_factory_loudly(monkeypatch, tmp_path, populated_db):
    bad = _write_yaml(tmp_path, "bad.yaml", "search: [not, a, mapping\n")
    monkeypatch.setenv("WENJI_CONFIG", str(bad))
    with pytest.raises(ConfigError):
        create_app(db_path=_file_db(populated_db, tmp_path), searcher=None)


# ----- CLI single entry point (ingest / rebuild / search share ConfigError) -----


def test_broken_yaml_fails_identically_across_commands(tmp_path):
    bad = _write_yaml(tmp_path, "bad.yaml", "directory_map: [broken\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    messages = []
    for args in (
        ["ingest", "dir", str(corpus), "--db", str(tmp_path / "a.db"), "--config", str(bad)],
        ["rebuild", str(corpus), "--db", str(tmp_path / "b.db"), "--config", str(bad)],
        ["search", "q", "--config", str(bad)],
    ):
        result = runner.invoke(cli_app, args)
        assert result.exit_code != 0, args
        messages.append(str(result.exception))
    # all three surface the loader's ConfigError with the same parse message
    assert messages[0] == messages[1] == messages[2]
    assert "YAML parse error" in messages[0]
