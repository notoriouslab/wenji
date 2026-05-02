"""Tests for ``wenji aggregate clear-cache`` CLI subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wenji.aggregate.cache import cache_put
from wenji.cli import app
from wenji.core.db import connect, initialise_schema

runner = CliRunner()


@pytest.fixture
def cache_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "wenji.db"
    conn = connect(db_path)
    initialise_schema(conn)
    cache_put(conn, "k1", {"v": 1})
    cache_put(conn, "k2", {"v": 2})
    cache_put(conn, "k3", {"v": 3})
    conn.close()
    return db_path


def test_clear_cache_reports_row_count(cache_db: Path) -> None:
    result = runner.invoke(app, ["aggregate", "clear-cache", "--db", str(cache_db)])
    assert result.exit_code == 0
    assert "cleared 3 row(s)" in result.stdout


def test_clear_cache_actually_empties_table(cache_db: Path) -> None:
    runner.invoke(app, ["aggregate", "clear-cache", "--db", str(cache_db)])
    conn = connect(cache_db)
    rows = conn.execute("SELECT COUNT(*) FROM aggregate_cache").fetchone()
    conn.close()
    assert rows[0] == 0


def test_clear_cache_on_empty_table(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    conn = connect(db_path)
    initialise_schema(conn)
    conn.close()
    result = runner.invoke(app, ["aggregate", "clear-cache", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "cleared 0 row(s)" in result.stdout


def test_clear_cache_missing_db_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["aggregate", "clear-cache", "--db", str(tmp_path / "nonexistent.db")]
    )
    assert result.exit_code != 0
