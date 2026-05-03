"""Tests for wenji eval run-benchmark --enable-rewrite / --no-rewrite flags."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from wenji.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "tests" / "benchmark_80_v2_snapshot.json"


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, params=None, **kw):
        return _FakeResp(self.payload)

    def close(self):
        pass


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in (
        "WENJI_LLM_BASE_URL",
        "WENJI_LLM_API_KEY",
        "WENJI_LLM_MODEL",
    ):
        monkeypatch.delenv(v, raising=False)


def _run(args: list[str], tmp_path):
    out = tmp_path / "r.json"

    def fake_client_factory(*a, **kw):
        return _FakeClient({"results": []})

    with patch("httpx.Client", side_effect=fake_client_factory):
        runner.invoke(
            app,
            [
                "eval",
                "run-benchmark",
                "--snapshot",
                str(SNAPSHOT),
                "--db",
                str(tmp_path / "noop.db"),
                "--out",
                str(out),
                *args,
            ],
        )
    return json.loads(out.read_text(encoding="utf-8"))


def test_no_rewrite_flag_tags_run_off(tmp_path):
    data = _run(["--no-rewrite"], tmp_path)
    assert data["rewrite_enabled"] is False
    assert data["run_id"].endswith("_rewrite_off")


def test_enable_rewrite_flag_tags_run_on(tmp_path, monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "u")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    data = _run(["--enable-rewrite"], tmp_path)
    assert data["rewrite_enabled"] is True
    assert data["run_id"].endswith("_rewrite_on")


def test_default_when_env_unset_tags_off(tmp_path):
    data = _run([], tmp_path)
    assert data["rewrite_enabled"] is False
    assert data["run_id"].endswith("_rewrite_off")


def test_default_when_env_set_tags_on(tmp_path, monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "u")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    data = _run([], tmp_path)
    assert data["rewrite_enabled"] is True
    assert data["run_id"].endswith("_rewrite_on")


def test_mutually_exclusive_flags_fail(tmp_path):
    out = tmp_path / "r.json"
    result = runner.invoke(
        app,
        [
            "eval",
            "run-benchmark",
            "--snapshot",
            str(SNAPSHOT),
            "--db",
            str(tmp_path / "noop.db"),
            "--out",
            str(out),
            "--enable-rewrite",
            "--no-rewrite",
        ],
    )
    assert result.exit_code == 2
