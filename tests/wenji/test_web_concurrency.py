"""Tests for web-layer concurrency hardening (web-concurrency-hardening change).

Covers: double-checked singleton init, query-lock smoke under concurrent
requests, TagBrowser TTL refresh + atomic swap, and year-param resilience.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import wenji.browse.tag as tag_mod
from wenji.browse.tag import TagBrowser
from wenji.web.app import create_app


@pytest.fixture
def file_db(populated_db, tmp_path: Path) -> Path:
    path = tmp_path / "wenji.db"
    backup_conn = sqlite3.connect(str(path))
    populated_db.backup(backup_conn)
    backup_conn.close()
    return path


class _SlowFakeEmbedder:
    """Counts constructions; slow init widens the check-then-act race window."""

    DIM = 1024  # vector search validates query_vec shape against stored dim
    constructions = 0
    _count_lock = threading.Lock()

    def __init__(self):
        with _SlowFakeEmbedder._count_lock:
            _SlowFakeEmbedder.constructions += 1
        time.sleep(0.2)

    def encode_batch(self, texts):
        import numpy as np

        return np.zeros((len(texts), self.DIM), dtype="float32")


def test_searcher_built_exactly_once_under_concurrency(file_db, monkeypatch):
    _SlowFakeEmbedder.constructions = 0
    monkeypatch.setattr("wenji.ingest.embed.Embedder", _SlowFakeEmbedder)
    app = create_app(db_path=file_db, searcher=None)
    client = TestClient(app)

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: client.get("/api/search?q=禱告").status_code, range(8)))

    assert all(s == 200 for s in statuses)
    assert _SlowFakeEmbedder.constructions == 1


class _SlowFakeSearcher:
    """Deterministic results with a sleep so concurrent calls overlap."""

    def search(self, query, *, axis=None, limit=10):
        time.sleep(0.05)
        return [
            {
                "article_id": "a1",
                "title": "t",
                "source_type": "sermon",
                "category": "",
                "pub_date": "2024-01-15",
                "hybrid_score": 0.5,
                "content_snippet": "s",
            }
        ]


def test_concurrent_searches_all_succeed(file_db):
    app = create_app(db_path=file_db, searcher=_SlowFakeSearcher())
    client = TestClient(app)
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: client.get("/api/search?q=信心").status_code, range(16)))
    assert all(s == 200 for s in statuses)


def test_tag_browser_ttl_refresh(file_db, monkeypatch):
    browser = TagBrowser(str(file_db))
    before = dict(browser.list_tags())

    conn = sqlite3.connect(str(file_db))
    conn.execute(
        "INSERT INTO articles_meta (article_id, path, title, tags) VALUES (?, ?, ?, ?)",
        ("ttl1", "ttl1.md", "TTL 新文章", '["新標籤"]'),
    )
    conn.commit()
    conn.close()

    # Within TTL: cache still serves the old view.
    assert dict(browser.list_tags()) == before

    # Past TTL: refresh picks up the new article.
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        tag_mod.time, "monotonic", lambda: real_monotonic() + tag_mod.REFRESH_TTL_SECONDS + 1
    )
    after = dict(browser.list_tags())
    assert after.get("新標籤") == 1


def test_tag_browser_concurrent_reads_no_keyerror(file_db, monkeypatch):
    # TTL=0 forces a rebuild on every call — maximum swap pressure.
    monkeypatch.setattr(tag_mod, "REFRESH_TTL_SECONDS", 0)
    browser = TagBrowser(str(file_db))
    tags = [t for t, _ in browser.list_tags()]
    assert tags, "populated_db fixture should carry tagged articles"

    errors: list[Exception] = []

    def hammer(_):
        try:
            for t in tags:
                browser.get_tag_detail(t)
                browser.get_related_tags(t)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(hammer, range(8)))
    assert errors == []


def test_year_param_non_numeric_returns_200(file_db):
    app = create_app(db_path=file_db, searcher=_SlowFakeSearcher())
    client = TestClient(app)
    assert client.get("/?year=abc").status_code == 200
    assert client.get("/?q=禱告&year=abc").status_code == 200
    assert client.get("/?year=2024").status_code == 200
