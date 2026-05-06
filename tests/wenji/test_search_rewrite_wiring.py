"""Tests for /api/search rewrite wiring (v0.3.2).

These tests use the FastAPI TestClient with an injected Searcher whose
rewriter is mocked. They verify the response payload `rewritten_query`
field across enabled / disabled / fallback paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from wenji.core.db import connect, initialise_schema
from wenji.search import Searcher
from wenji.web.app import create_app


@pytest.fixture
def db_conn(tmp_path):
    db = tmp_path / "w.db"
    conn = connect(db)
    initialise_schema(conn)
    yield conn, db
    conn.close()


def _make_searcher_with_mocked_rewriter(conn, rewriter_returns: str | None):
    """Build a Searcher whose rewriter.rewrite returns a fixed value, and whose
    search() returns a single canned result."""
    rewriter = None
    if rewriter_returns is not None:
        rewriter = MagicMock()
        rewriter.rewrite = MagicMock(return_value=rewriter_returns)
    s = MagicMock(spec=Searcher)
    s.rewriter = rewriter
    s.search = MagicMock(return_value=[{"article_id": "a1", "title": "T", "hybrid_score": 0.5}])
    return s


def test_rewritten_query_exposed_when_rewrite_changes_query(db_conn):
    conn, db = db_conn
    s = _make_searcher_with_mocked_rewriter(conn, rewriter_returns="因信稱義 救恩論")
    app = create_app(db_path=db, searcher=s)
    client = TestClient(app)

    resp = client.get("/api/search?q=因信稱義")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rewritten_query"] == "因信稱義 救恩論"
    assert body["query"] == "因信稱義"


def test_rewritten_query_null_when_disabled(db_conn):
    conn, db = db_conn
    s = _make_searcher_with_mocked_rewriter(conn, rewriter_returns=None)
    app = create_app(db_path=db, searcher=s)
    client = TestClient(app)

    resp = client.get("/api/search?q=anything")
    assert resp.status_code == 200
    assert resp.json()["rewritten_query"] is None


def test_rewritten_query_null_when_rewrite_returns_same_query(db_conn):
    conn, db = db_conn
    s = _make_searcher_with_mocked_rewriter(conn, rewriter_returns="raw")
    app = create_app(db_path=db, searcher=s)
    client = TestClient(app)

    resp = client.get("/api/search?q=raw")
    assert resp.status_code == 200
    assert resp.json()["rewritten_query"] is None


def test_rewritten_query_null_on_rewriter_exception(db_conn):
    """Rewriter raises (timeout simulated) → response stays 200, rewritten_query=null."""
    conn, db = db_conn
    s = _make_searcher_with_mocked_rewriter(conn, rewriter_returns="should-not-leak")
    s.rewriter.rewrite = MagicMock(side_effect=RuntimeError("simulated timeout"))
    app = create_app(db_path=db, searcher=s)
    client = TestClient(app)

    resp = client.get("/api/search?q=因信稱義")
    assert resp.status_code == 200
    assert resp.json()["rewritten_query"] is None
