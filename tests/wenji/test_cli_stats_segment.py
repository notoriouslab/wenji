"""Tests for wenji stats / wenji segment CLI commands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wenji.cli import app

runner = CliRunner()


@pytest.fixture
def db_path(populated_db, tmp_path: Path) -> Path:
    out = tmp_path / "wenji.db"
    dst = sqlite3.connect(out)
    populated_db.backup(dst)
    dst.close()
    return out


def test_cli_stats_human_output(db_path: Path):
    result = runner.invoke(app, ["stats", "--db", str(db_path)])
    assert result.exit_code == 0
    for label in ("Corpus", "Indices", "Source Types", "Axes", "Last Ingest"):
        assert label in result.stdout


def test_cli_stats_json_output_matches_endpoint_schema(db_path: Path):
    """CLI --json output SHALL match /api/stats schema."""
    from fastapi.testclient import TestClient

    from wenji.web.app import create_app

    cli_result = runner.invoke(app, ["stats", "--db", str(db_path), "--json"])
    assert cli_result.exit_code == 0
    cli_payload = json.loads(cli_result.stdout)

    api_payload = TestClient(create_app(db_path=db_path)).get("/api/stats").json()
    assert cli_payload == api_payload


def test_cli_stats_missing_db_exits_nonzero(tmp_path: Path):
    missing = tmp_path / "nope.db"
    result = runner.invoke(app, ["stats", "--db", str(missing)])
    assert result.exit_code != 0


def test_cli_segment_human_output(db_path: Path):
    result = runner.invoke(
        app, ["segment", "因信稱義", "--db", str(db_path)]
    )
    assert result.exit_code == 0
    for label in ("Query:", "Tokens", "FTS form", "Dict hits", "Rewrite"):
        assert label in result.stdout


def test_cli_segment_json_output_parses(db_path: Path):
    result = runner.invoke(
        app, ["segment", "因信稱義", "--db", str(db_path), "--json"]
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert set(body.keys()) == {
        "query",
        "tokens",
        "normalized_query",
        "fts_form",
        "dict_hits",
        "rewrite",
        "entities",
        "intent",
    }


def test_cli_segment_empty_query_exits_nonzero(db_path: Path):
    result = runner.invoke(app, ["segment", "", "--db", str(db_path)])
    assert result.exit_code != 0


def test_cli_segment_mutually_exclusive_rewrite_flags(db_path: Path):
    result = runner.invoke(
        app,
        [
            "segment",
            "因信稱義",
            "--db",
            str(db_path),
            "--enable-rewrite",
            "--no-rewrite",
        ],
    )
    assert result.exit_code != 0
