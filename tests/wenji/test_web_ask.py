"""Tests for POST /api/ask endpoint."""

from __future__ import annotations

import base64
import json
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


def test_api_ask_accepts_history_and_rewrites_for_retrieval(file_db: Path) -> None:
    """A follow-up turn is condensed once, then answered from that retrieval."""
    prompts: list[str] = []

    class _RecordingLLM:
        calls = 0

        def chat(self, messages):
            _RecordingLLM.calls += 1
            prompts.append(messages[0]["content"])
            if "<followup>" in messages[0]["content"]:
                return "民法總則規範什麼？"
            return "答案 [1]"

    c = _make_client(file_db, llm=_RecordingLLM())
    r = c.post(
        "/api/ask",
        json={
            "q": "那民法呢？",
            "history": [
                {"role": "user", "content": "因信稱義是什麼"},
                {"role": "assistant", "content": "宗教改革核心教義"},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["answer"] == "答案 [1]"
    assert len(prompts) == 2, "one rewrite, one answer"
    assert "<followup>" in prompts[0]
    # the rewritten text is never surfaced to the caller
    assert "民法總則規範什麼？" not in r.text


@pytest.mark.parametrize(
    ("history", "expected_fragment"),
    [
        ("not-a-list", "must be a list"),
        (["not-an-object"], "must be objects"),
        ([{"role": "system", "content": "x"}], "role must be"),
        ([{"role": "user"}], "content must be"),
        ([{"role": "user", "content": "   "}], "content must be"),
        ([{"role": "user", "content": "q"}] * 11, "at most"),
    ],
)
def test_api_ask_400_on_malformed_history(
    file_db: Path,
    history: object,
    expected_fragment: str,
) -> None:
    """History validation mirrors the 400-on-bad-body style of this endpoint."""
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.post("/api/ask", json={"q": "因信稱義", "history": history})
    assert r.status_code == 400
    assert expected_fragment in r.json()["detail"]


def test_api_ask_history_null_behaves_as_single_turn(file_db: Path) -> None:
    llm = _FakeLLM(response="答案")
    c = _make_client(file_db, llm=llm)
    r = c.post("/api/ask", json={"q": "因信稱義", "history": None})
    assert r.status_code == 200
    assert llm.calls == 1, "no rewrite call for a single-turn request"


class _StreamLLM:
    """Duck-typed LLMClient for the SSE endpoint."""

    def __init__(self, pieces=("答", "案"), *, raise_exc: Exception | None = None):
        self.pieces = pieces
        self.raise_exc = raise_exc
        self.stream_calls = 0

    def chat(self, messages):
        return "改寫後問題"

    def chat_stream(self, messages):
        self.stream_calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        yield from self.pieces


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name is not None:
            events.append((name, data or {}))
    return events


def test_api_ask_stream_event_order_and_content_type(file_db: Path) -> None:
    llm = _StreamLLM(pieces=("因信", "稱義"))
    c = _make_client(file_db, llm=llm)
    r = c.get("/api/ask/stream", params={"q": "因信稱義"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"

    events = _parse_sse(r.text)
    assert [name for name, _ in events] == ["meta", "delta", "delta", "done"]
    assert events[0][1]["citations"], "citations arrive before answer text"
    assert "".join(d["text"] for name, d in events if name == "delta") == "因信稱義"


def test_api_ask_stream_cached_answer_replays_without_llm(file_db: Path) -> None:
    llm = _StreamLLM(pieces=("答案",))
    c = _make_client(file_db, llm=llm)
    c.get("/api/ask/stream", params={"q": "因信稱義"})
    assert llm.stream_calls == 1

    r = c.get("/api/ask/stream", params={"q": "因信稱義"})
    events = _parse_sse(r.text)
    assert llm.stream_calls == 1, "second request must be served from cache"
    assert [name for name, _ in events] == ["meta", "delta", "done"]
    assert events[1][1]["text"] == "答案"


def test_api_ask_stream_503_without_llm(file_db: Path) -> None:
    c = _make_client(file_db, llm=None)
    r = c.get("/api/ask/stream", params={"q": "因信稱義"})
    assert r.status_code == 503


def test_api_ask_stream_400_on_empty_q(file_db: Path) -> None:
    c = _make_client(file_db, llm=_StreamLLM())
    r = c.get("/api/ask/stream", params={"q": "   "})
    assert r.status_code == 400


def test_api_ask_stream_accepts_base64_history(file_db: Path) -> None:
    llm = _StreamLLM(pieces=("答案",))
    c = _make_client(file_db, llm=llm)
    history = base64.b64encode(
        json.dumps([{"role": "user", "content": "因信稱義是什麼"}]).encode()
    ).decode()
    r = c.get("/api/ask/stream", params={"q": "那民法呢？", "history_b64": history})
    assert r.status_code == 200
    assert [name for name, _ in _parse_sse(r.text)][0] == "meta"


@pytest.mark.parametrize("bad", ["not-base64!!", "aGVsbG8="])
def test_api_ask_stream_400_on_bad_history_b64(file_db: Path, bad: str) -> None:
    """Non-base64 and base64-of-non-JSON both fail the same way."""
    c = _make_client(file_db, llm=_StreamLLM())
    r = c.get("/api/ask/stream", params={"q": "因信稱義", "history_b64": bad})
    assert r.status_code == 400
    assert "history_b64" in r.json()["detail"]
