"""Smoke tests for wenji CLI: each subcommand --help + basic invocation paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wenji.cli import app

runner = CliRunner()


def test_main_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("ingest", "search", "classify", "rebuild", "eval", "serve"):
        assert cmd in result.stdout


@pytest.mark.parametrize("subcommand", ["ingest", "search", "classify", "rebuild", "eval", "serve"])
def test_subcommand_help(subcommand: str):
    result = runner.invoke(app, [subcommand, "--help"])
    assert result.exit_code == 0


def test_ingest_missing_corpus_dir_exits_nonzero():
    result = runner.invoke(app, ["ingest", "dir", "/nonexistent/corpus/dir"])
    assert result.exit_code != 0


def test_classify_missing_config_exits_nonzero(tmp_path):
    result = runner.invoke(
        app,
        ["classify", "--db", str(tmp_path / "x.db"), "--config", "/nonexistent.yaml"],
    )
    assert result.exit_code != 0


def test_eval_missing_candidates_exits_nonzero():
    result = runner.invoke(app, ["eval", "run", "--candidates", "/nonexistent.jsonl"])
    assert result.exit_code != 0


def test_eval_clear_cache_without_db_exits_2(tmp_path):
    candidates = tmp_path / "c.jsonl"
    candidates.write_text(
        '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "d", "keywords": ["a"]}]}\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["eval", "run", "--candidates", str(candidates), "--clear-cache"]
    )
    assert result.exit_code == 2
    assert "--clear-cache requires --db" in result.stderr


def test_classify_end_to_end(tmp_path: Path):
    """ingest a tiny corpus then classify with a one-axis config."""
    from wenji.core.db import connect, initialise_schema

    db = tmp_path / "wenji.db"
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    (sermons / "s1.md").write_text(
        "---\ntitle: Test\nsource_type: sermon\n---\n第一段內容夠長足以建索引。",
        encoding="utf-8",
    )

    # seed an article directly so we don't depend on Embedder (Group 9 ONNX)
    conn = connect(db)
    initialise_schema(conn)
    conn.execute(
        "INSERT INTO articles_meta (article_id, path, title, source_type, "
        "content_length, chunk_count, content_hash, indexed_at) "
        "VALUES ('a1', 'sermons/s1.md', 'T', 'sermon', 10, 0, 'abc', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    cfg = tmp_path / "axes.yaml"
    cfg.write_text(
        "axes:\n  - id: x\n    name: X\n    order: 1\n"
        "    rules:\n      - {source_type: sermon, primary: true}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["classify", "--db", str(db), "--config", str(cfg), "--validate"])
    assert result.exit_code == 0
    assert "PASS" in result.stdout


def test_serve_enable_rewrite_without_env_exits_nonzero(monkeypatch):
    for v in ("WENJI_LLM_BASE_URL", "WENJI_LLM_API_KEY", "WENJI_LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)
    result = runner.invoke(app, ["serve", "--enable-rewrite"])
    assert result.exit_code != 0
    assert "missing" in result.stderr.lower() or "env" in result.stderr.lower()


def test_search_accepts_no_rewrite_flag():
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    assert "--no-rewrite" in result.stdout
    assert "--enable-rewrite" in result.stdout
