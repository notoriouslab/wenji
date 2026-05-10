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


def test_lifespan_uses_env_db_path_when_arg_omitted(healthy_db_file: Path, monkeypatch):
    """C1 regression: production `app = create_app()` (no arg) MUST resolve
    `db_path` from `WENJI_DB_PATH` and run the gate against that resolved
    path. Earlier code had `if db_path is not None and not env_disabled`
    which made the gate dead in production (module-level `app`).
    """
    monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)
    # Corrupt the db so the gate has something to fire on
    conn = connect(healthy_db_file)
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    monkeypatch.setenv("WENJI_DB_PATH", str(healthy_db_file))

    app = create_app()  # no db_path arg — production module-level pattern
    with pytest.raises(StartupError) as exc_info:
        with TestClient(app):
            pass
    assert "chunks_fts is empty" in str(exc_info.value)


def test_lifespan_disable_env_zero_does_not_skip(healthy_db_file: Path, monkeypatch):
    """C2 regression: WENJI_DISABLE_STARTUP_CHECK=0 MUST NOT skip the gate
    (operator intent of '=0 means off'). Earlier code used Python truthy
    coercion which read '0' as truthy → silently bypassed.
    """
    # Corrupt the db
    conn = connect(healthy_db_file)
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    monkeypatch.setenv("WENJI_DISABLE_STARTUP_CHECK", "0")

    app = create_app(db_path=healthy_db_file)
    with pytest.raises(StartupError):
        with TestClient(app):
            pass


def test_lifespan_disable_env_one_skips(healthy_db_file: Path, monkeypatch):
    """Sanity: the supported truthy value '1' DOES skip the gate."""
    # Corrupt the db
    conn = connect(healthy_db_file)
    conn.execute("DELETE FROM chunks_fts")
    conn.commit()
    conn.close()

    monkeypatch.setenv("WENJI_DISABLE_STARTUP_CHECK", "1")

    app = create_app(db_path=healthy_db_file)
    # gate skipped → app starts fine even with corrupted db
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
