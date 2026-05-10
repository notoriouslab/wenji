"""Verify ``wenji search`` in-process fallback runs the startup consistency gate.

The conftest autouse fixture sets ``WENJI_DISABLE_STARTUP_CHECK`` for the rest
of the suite (partial fixtures shouldn't trip the gate); these tests
``monkeypatch.delenv`` to re-enable it and confirm the gate fires before the
embedder is loaded.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wenji.cli import app
from wenji.cli import search as search_cli
from wenji.core.db import connect

runner = CliRunner()


def test_search_cli_exits_one_on_inconsistent_db(healthy_db_file: Path, monkeypatch) -> None:
    monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)
    # Force the in-process path (no live server probe)
    monkeypatch.setattr(search_cli, "_try_server", lambda *a, **kw: None)

    # Corrupt: drop chunks rows so L2.c fires before Embedder construction
    conn = connect(healthy_db_file)
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    result = runner.invoke(
        app,
        ["search", "因信稱義", "--db", str(healthy_db_file)],
    )
    assert result.exit_code == 1, result.stdout
    assert "consistency check FAILED" in result.stderr
    assert "chunks_fts is empty" in result.stderr
