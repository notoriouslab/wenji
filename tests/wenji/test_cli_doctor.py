"""Tests for ``wenji doctor`` CLI subcommand."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wenji.cli import app
from wenji.core.db import connect

runner = CliRunner()


def test_doctor_ok_exits_zero(healthy_db_file: Path):
    result = runner.invoke(app, ["doctor", "--db", str(healthy_db_file)])
    assert result.exit_code == 0, result.stdout
    assert "OK" in result.stdout


def test_doctor_inconsistent_exits_one(healthy_db_file: Path):
    # Corrupt: drop chunks rows so L2.c (articles_meta > 0 but chunks_fts
    # empty) fires.
    conn = connect(healthy_db_file)
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["doctor", "--db", str(healthy_db_file)])
    assert result.exit_code == 1, result.stdout
    assert "FAIL" in result.stdout
    assert "chunks_fts is empty" in result.stdout


def test_doctor_sample_keywords_override(healthy_db_file: Path):
    """`--sample-keywords` CSV is parsed and used in the report output."""
    result = runner.invoke(
        app,
        [
            "doctor",
            "--db",
            str(healthy_db_file),
            "--sample-keywords",
            "zzzzz,qqqqq",
        ],
    )
    # Custom keywords with zero hits → L3 fails → exit 1
    assert result.exit_code == 1, result.stdout
    assert "zzzzz" in result.stdout
    assert "qqqqq" in result.stdout
    # Default Chinese keywords MUST NOT have been used
    assert "'神'" not in result.stdout
