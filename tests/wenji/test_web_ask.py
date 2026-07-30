"""Tests for POST /api/ask endpoint."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import wenji.web.app as app_module
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
    # 0.6.1: done carries the markdown-rendered whole answer so the client can
    # swap the plain-text stream for proper HTML (same renderer as POST).
    assert "因信稱義" in events[-1][1]["narrative_html"]


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


def test_api_ask_stream_enforces_demo_source_scope(
    file_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SSE path must inherit the demo-source constraint.

    /api/ask/stream builds its filter server-side (EventSource is GET-only, so
    there is no request body to carry one). If the demo constraint were dropped
    there, a tenant-scoped deployment would leak documents outside its corpus.
    The corpus is all source_type=sermon, so pinning the demo source to a type
    that does not exist must yield no citations at all.
    """
    monkeypatch.setenv("WENJI_DEMO_SOURCE", "law")
    c = _make_client(file_db, llm=_StreamLLM(pieces=("答案",)))
    events = _parse_sse(c.get("/api/ask/stream", params={"q": "因信稱義"}).text)
    meta = next(data for name, data in events if name == "meta")
    assert meta["citations"] == [], "demo scope must exclude every non-demo document"

    monkeypatch.delenv("WENJI_DEMO_SOURCE")
    c2 = _make_client(file_db, llm=_StreamLLM(pieces=("答案",)))
    events2 = _parse_sse(c2.get("/api/ask/stream", params={"q": "因信稱義"}).text)
    meta2 = next(data for name, data in events2 if name == "meta")
    assert meta2["citations"], "control: without a demo source the same query cites documents"


def test_api_ask_stream_closes_db_when_client_disconnects_early(
    file_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abandoning the stream must still close the per-request DB connection.

    The close lives in the generator's finally rather than the handler body,
    because the generator outlives the handler. Without it, every reader who
    closes the tab mid-answer would leak a SQLite connection.
    """
    closed: list[bool] = []
    real_connect = app_module.connect

    class _TrackingConnection:
        """Delegating proxy — sqlite3.Connection.close cannot be reassigned."""

        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def close(self) -> None:
            closed.append(True)
            self._conn.close()

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    def tracking_connect(*args, **kwargs):
        return _TrackingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(app_module, "connect", tracking_connect)

    # A long stream so the client can walk away in the middle of it.
    llm = _StreamLLM(pieces=tuple(f"片段{i}" for i in range(50)))
    c = _make_client(file_db, llm=llm)
    with c.stream("GET", "/api/ask/stream", params={"q": "因信稱義"}) as r:
        for i, _line in enumerate(r.iter_lines()):
            if i >= 2:
                break  # walk away mid-answer

    assert closed, "the generator's finally must close the connection on disconnect"


# ---------------------------------------------------------------------------
# GET /ask page (phase 5A)
# ---------------------------------------------------------------------------


def test_ask_page_renders_and_is_noindex(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.get("/ask")
    assert r.status_code == 200
    assert 'name="robots"' in r.text and "noindex" in r.text
    assert "AI 自由問答" in r.text
    # default copy comes from config, not a hardcoded template string
    assert "直接輸入問題，由 AI 從語料中檢索並總結回答。" in r.text
    assert "例如：靈命成長的關鍵是什麼？" in r.text


def test_ask_page_prefills_query_from_querystring(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.get("/ask", params={"q": "特休假怎麼算"})
    assert r.status_code == 200
    assert "特休假怎麼算" in r.text
    assert "window.WENJI_ASK_AUTOSUBMIT = true" in r.text


def test_ask_page_does_not_autosubmit_without_query(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    assert "window.WENJI_ASK_AUTOSUBMIT = false" in c.get("/ask").text


def test_ask_page_escapes_query_into_the_textarea(file_db: Path) -> None:
    """?q= is attacker-controlled and lands in HTML."""
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.get("/ask", params={"q": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_ask_page_hides_examples_when_unset(file_db: Path) -> None:
    c = _make_client(file_db, llm=_FakeLLM())
    assert "ask-examples" not in c.get("/ask").text


def test_ask_page_renders_configured_examples(
    file_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / "wenji.yaml"
    cfg.write_text(
        "web:\n  ask_hint: 問規章\n  ask_placeholder: 例如：婚假幾天\n"
        "  ask_examples:\n    - 婚假可以請幾天\n    - 借會議室怎麼申請\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WENJI_CONFIG", str(cfg))
    c = _make_client(file_db, llm=_FakeLLM())
    r = c.get("/ask")
    assert "問規章" in r.text
    assert "例如：婚假幾天" in r.text
    assert "婚假可以請幾天" in r.text
    assert "借會議室怎麼申請" in r.text


def test_robots_txt_disallows_ask(file_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WENJI_SITE_URL", "https://example.test")
    c = _make_client(file_db, llm=_FakeLLM())
    body = c.get("/robots.txt").text
    assert "Disallow: /ask" in body
    assert "Disallow: /aggregate" in body

    # Without a site URL the whole site is already denied, which covers /ask.
    monkeypatch.delenv("WENJI_SITE_URL")
    c2 = _make_client(file_db, llm=_FakeLLM())
    assert c2.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"


def test_ask_js_ships_on_ask_page_only(file_db: Path) -> None:
    """0.6.1: with the side panel gone, ask.js belongs to the /ask page alone.

    Exactly one tag there (two would double-bind every handler), zero
    elsewhere (dead weight on pages with nothing for it to wire).
    """
    c = _make_client(file_db, llm=_FakeLLM())
    assert c.get("/ask").text.count("/static/ask.js") == 1
    assert "/static/ask.js" not in c.get("/").text


def test_ask_copy_comes_from_config(
    file_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.6.1: the ask copy lives on the /ask page only — the site-wide side
    panel that used to repeat it on every page is retired."""
    cfg = tmp_path / "wenji.yaml"
    cfg.write_text(
        "web:\n  ask_hint: 用口語問一句\n  ask_placeholder: 例如：婚假幾天？\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WENJI_CONFIG", str(cfg))
    c = _make_client(file_db, llm=_FakeLLM())
    body = c.get("/ask").text
    assert "用口語問一句" in body
    assert "例如：婚假幾天？" in body
    # the 0.5.2 hardcoded strings must be gone
    assert "靈命成長的關鍵是什麼" not in body
    for path in ("/", "/tags"):
        assert "用口語問一句" not in c.get(path).text, f"{path} still renders panel copy"
