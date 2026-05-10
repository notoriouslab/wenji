"""Tests for the FastAPI ``lifespan`` startup consistency gate.

These tests MUST ``monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK")`` to
re-enable the gate (the conftest autouse fixture sets it for the rest of the
suite, where partial test fixtures would otherwise be blocked by the gate).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wenji.core.db import connect
from wenji.core.errors import StartupError
from wenji.web.app import create_app


def test_lifespan_passes_on_healthy_db(healthy_db_file: Path, monkeypatch):
    monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)

    app = create_app(db_path=healthy_db_file)
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_lifespan_raises_startup_error_on_bad_db(healthy_db_file: Path, monkeypatch):
    monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)

    # Corrupt: drop chunks rows so L2.c fires
    conn = connect(healthy_db_file)
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    app = create_app(db_path=healthy_db_file)
    with pytest.raises(StartupError) as exc_info:
        with TestClient(app):
            pass
    assert "consistency check FAILED" in str(exc_info.value)
    assert "chunks_fts is empty" in str(exc_info.value)
