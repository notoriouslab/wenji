"""Tests for POST /api/ask endpoint."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wenji.aggregate.llm import LLMClientError
from wenji.web.app import create_app


class _FakeLLM:
    """Duck-typed LLMClient with a configurable response."""

    def __init__(self, response="伺服器中已收到問題", *, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class _FakeSearcher:
    def search(self, query, *, axis=None, limit=10):
        return [
            {
                "article_id": "a1",
                "title": "因信稱義講道",
                "content_snippet": "<mark>因信稱義</mark>是宗教改革核心",
                "bm25_score": 0.92,
            }
        ]


@pytest.fixture
def file_db(populated_db, tmp_path: Path) -> Path:
    db_path = tmp_path / "wenji.db"
    backup_conn = sqlite3.connect(str(db_path))
    populated_db.backup(backup_conn)
    backup_conn.close()
    return db_path


def _make_client(file_db: Path, *, llm=None, searcher=None) -> TestClient:
    app = create_app(
        db_path=file_db,
        searcher=searcher if searcher is not None else _FakeSearcher(),
        llm_client=llm,
    )
    return TestClient(app)


def test_api_ask_returns_answer_on_llm_success(file_db: Path) -> None:
    llm = _FakeLLM(response="因信稱義是…")
    c = _make_client(file_db, llm=llm)
    r = c.post("/api/ask", json={"q": "因信稱義", "k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "因信稱義"
    assert body["answer"] == "因信稱義是…"
    assert body["retrieval"], "retrieval should be present"
    assert body["citations"], "citations should be present"
    assert body["narrative_html"] is not None
    assert llm.calls == 1


def test_api_ask_returns_200_with_null_answer_on_llm_failure(file_db: Path) -> None:
    llm = _FakeLLM(raise_exc=LLMClientError("upstream timed out"))
    c = _make_client(file_db, llm=llm)
    r = c.post("/api/ask", json={"q": "因信稱義"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] is None
    assert body["narrative_html"] is None
    assert body["retrieval"], "retrieval should survive LLM failure"


def test_api_ask_400_missing_q(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.post("/api/ask", json={})
    assert r.status_code == 400
    assert "q" in r.json()["detail"]


def test_api_ask_400_empty_q(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.post("/api/ask", json={"q": "   "})
    assert r.status_code == 400


def test_api_ask_400_non_positive_k(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.post("/api/ask", json={"q": "x", "k": 0})
    assert r.status_code == 400


def test_api_ask_400_malformed_json(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.post(
        "/api/ask",
        content="{not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_api_ask_400_invalid_filter_field(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.post(
        "/api/ask",
        json={"q": "因信稱義", "filter": {"unknown_field": "v"}},
    )
    assert r.status_code == 400
    assert "filter" in r.json()["detail"]


def test_api_ask_503_when_llm_not_configured(file_db: Path) -> None:
    c = _make_client(file_db, llm=None)
    r = c.post("/api/ask", json={"q": "因信稱義"})
    assert r.status_code == 503
