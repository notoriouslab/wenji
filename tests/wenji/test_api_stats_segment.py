"""Integration tests for /api/stats and /api/segment endpoints."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wenji.web.app import create_app


@pytest.fixture
def db_path(populated_db, tmp_path: Path) -> Path:
    """Persist the populated_db fixture to a real file for create_app's lazy connect."""
    out = tmp_path / "wenji.db"
    src = populated_db
    dst = sqlite3.connect(out)
    src.backup(dst)
    dst.close()
    return out


@pytest.fixture
def client(db_path: Path) -> TestClient:
    app = create_app(db_path=db_path)
    return TestClient(app)


def test_stats_endpoint_returns_full_schema(client: TestClient):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "articles",
        "chunks",
        "indices",
        "source_types",
        "axes",
        "last_ingest_at",
    }
    assert set(body["indices"].keys()) == {
        "fts5_articles",
        "fts5_chunks",
        "vector_dims",
        "vector_count",
    }
    assert isinstance(body["articles"], int)
    assert isinstance(body["source_types"], dict)


def test_stats_endpoint_handles_empty_corpus(tmp_path: Path):
    from wenji.core.db import connect, initialise_schema

    empty_path = tmp_path / "empty.db"
    conn = connect(empty_path)
    initialise_schema(conn)
    conn.close()

    app = create_app(db_path=empty_path)
    c = TestClient(app)
    r = c.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["articles"] == 0
    assert body["chunks"] == 0
    assert body["source_types"] == {}
    assert body["axes"] == {}
    assert body["last_ingest_at"] is None


def test_segment_endpoint_returns_full_schema(client: TestClient):
    r = client.get("/api/segment", params={"q": "因信稱義"})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "query",
        "tokens",
        "normalized_query",
        "fts_form",
        "dict_hits",
        "rewrite",
    }


def test_segment_endpoint_rejects_empty_query(client: TestClient):
    r = client.get("/api/segment", params={"q": ""})
    assert r.status_code == 400
    assert "error" in r.json()


def test_segment_endpoint_rejects_missing_query(client: TestClient):
    r = client.get("/api/segment")
    assert r.status_code == 400
    assert "error" in r.json()
