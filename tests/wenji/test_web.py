"""Tests for wenji.web.app FastAPI surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wenji.web.app import create_app


class _FakeSearcher:
    """Returns deterministic results without loading any model."""

    def __init__(self, results=None):
        self._results = results or []

    def search(self, query, *, axis=None, limit=10):
        return [dict(r, query=query, axis=axis) for r in self._results]


@pytest.fixture
def client(populated_db, tmp_path: Path):
    # populated_db is the in-memory connection from conftest; we need a file so create_app can re-open
    # Save populated_db to a real file for FastAPI test
    file_db = tmp_path / "wenji.db"
    backup_conn = __import__("sqlite3").connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()

    fake_searcher = _FakeSearcher(
        results=[
            {
                "article_id": "a1",
                "title": "禱告與信心",
                "source_type": "sermon",
                "category": "",
                "pub_date": "2024-01-15",
                "hybrid_score": 0.812,
                "content_snippet": "<mark>禱告</mark>是與神對話",
            }
        ]
    )
    app = create_app(db_path=file_db, searcher=fake_searcher)
    return TestClient(app)


@pytest.fixture
def client_no_model(populated_db, tmp_path: Path):
    file_db = tmp_path / "wenji.db"
    backup_conn = __import__("sqlite3").connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()
    # No searcher injected → lazy load will fail (no model files)
    app = create_app(db_path=file_db, searcher=None)
    return TestClient(app)


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["searcher_ready"] is True


def test_api_axes_returns_known_axis(client):
    r = client.get("/api/axes")
    assert r.status_code == 200
    body = r.json()
    axis_ids = {a["id"] for a in body["axes"]}
    assert "theology" in axis_ids


def test_api_search_returns_results(client):
    r = client.get("/api/search", params={"q": "禱告"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "禱告"
    assert body["results"][0]["article_id"] == "a1"


def test_api_search_504_when_searcher_unavailable(client_no_model, monkeypatch):
    # Force searcher lazy-load to fail
    from wenji.web import app as app_mod

    def boom(*a, **kw):
        from wenji.core.errors import ConfigError

        raise ConfigError("model not found")

    monkeypatch.setattr(app_mod, "Searcher", boom)
    r = client_no_model.get("/api/search", params={"q": "Q"})
    assert r.status_code == 504
    assert "starting up" in r.json()["error"]


def test_index_renders_with_query(client):
    r = client.get("/", params={"q": "禱告"})
    assert r.status_code == 200
    assert "禱告" in r.text
    assert "wenji" in r.text
    # axis sidebar present
    assert "theology" in r.text


def test_index_renders_without_query(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "wenji" in r.text


def test_index_shows_error_banner_when_searcher_unavailable(client_no_model, monkeypatch):
    from wenji.web import app as app_mod

    def boom(*a, **kw):
        from wenji.core.errors import ConfigError

        raise ConfigError("missing model")

    monkeypatch.setattr(app_mod, "Searcher", boom)
    r = client_no_model.get("/", params={"q": "Q"})
    assert r.status_code == 200
    assert "error-banner" in r.text
    assert "搜尋引擎啟動中" in r.text


def test_article_viewer_renders(client, populated_db):
    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
    ).fetchone()[0]
    r = client.get(f"/article/{aid}")
    assert r.status_code == 200
    assert "因信稱義" in r.text or "因信" in r.text
    assert "← 回搜尋" in r.text


def test_article_viewer_with_query_renders_back_link(client, populated_db):
    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
    ).fetchone()[0]
    r = client.get(f"/article/{aid}", params={"q": "禱告"})
    assert r.status_code == 200
    # back link carries the query so user can return to original search
    assert "回搜尋" in r.text
    assert "禱告" in r.text


def test_article_viewer_sidebar_renders_when_chunked(client, tmp_path):
    """When article has chunks, sidebar TOC renders with hit + non-hit chunks."""
    import sqlite3

    file_db = tmp_path / "wenji.db"
    conn = sqlite3.connect(str(file_db))
    aid = conn.execute("SELECT article_id FROM articles_meta LIMIT 1").fetchone()[0]
    conn.execute("UPDATE articles_meta SET chunk_count = 2 WHERE article_id = ?", (aid,))
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, title, "
        "title_raw, chunk_text, chunk_text_raw, tags, tags_raw, source_type, pub_year) "
        "VALUES (?, ?, ?, '', '', '', '段一預覽內容', '', '', '', '')",
        (f"{aid}-0", aid, "0"),
    )
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, title, "
        "title_raw, chunk_text, chunk_text_raw, tags, tags_raw, source_type, pub_year) "
        "VALUES (?, ?, ?, '', '', '', '段二預覽內容', '', '', '', '')",
        (f"{aid}-1", aid, "1"),
    )
    conn.commit()
    conn.close()

    r = client.get(f"/article/{aid}")
    assert r.status_code == 200
    assert "article-sidebar" in r.text
    assert "toc-list" in r.text
    assert "¶1" in r.text
    assert "¶2" in r.text
    assert "段一預覽內容" in r.text


def test_article_viewer_doc_actions_renders_source_url(client, tmp_path):
    import sqlite3

    file_db = tmp_path / "wenji.db"
    conn = sqlite3.connect(str(file_db))
    aid = conn.execute("SELECT article_id FROM articles_meta LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE articles_meta SET source_url = ? WHERE article_id = ?",
        ("https://example.com/src", aid),
    )
    conn.commit()
    conn.close()

    r = client.get(f"/article/{aid}")
    assert r.status_code == 200
    assert "doc-actions" in r.text
    assert "https://example.com/src" in r.text
    assert "原始來源" in r.text


def test_article_viewer_404_when_missing(client):
    r = client.get("/article/nonexistent-id")
    assert r.status_code == 404


def test_static_css_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "wenji-spin" in r.text  # spinner keyframes are present


def test_factory_uses_env_var_for_db(monkeypatch, tmp_path):
    monkeypatch.setenv("WENJI_DB_PATH", str(tmp_path / "from-env.db"))
    app = create_app()  # no db_path passed
    # state.db_path should reflect the env override
    # We probe via /healthz
    c = TestClient(app)
    r = c.get("/healthz")
    assert "from-env.db" in r.json()["db_path"]
