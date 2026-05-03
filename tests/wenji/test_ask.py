"""Tests for wenji.ask (Asker + Answer/Citation + cache + LLM fallback)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from wenji.aggregate.cache import cache_put
from wenji.aggregate.llm import LLMClientError
from wenji.ask import Answer, Asker, Citation, Filter, SourceRef, _answer_to_dict
from wenji.ask.prompts import ASK_PROMPT
from wenji.core.db import connect, initialise_schema
from wenji.ingest import ingest_dir
from wenji.search import Searcher


class _MockLLMClient:
    """Duck-typed LLMClient stand-in (mirrors test_aggregate)."""

    def __init__(self, response: str | Callable[[list[dict]], str | Exception] = "") -> None:
        self.response = response
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        result = self.response(messages) if callable(self.response) else self.response
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def ask_corpus(tmp_path: Path) -> Path:
    """Four articles spanning sermon / law / classical for ask scenarios."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    (sermons / "justification-sermon.md").write_text(
        "---\ntitle: 因信稱義講道\ntags: [因信稱義, 救恩]\n"
        "pubDate: 2024-04-10\nsubtype: weekday\n---\n"
        "因信稱義是宗教改革的核心。我們因信靠基督而被神稱為義，"
        "這完全是出於恩典，不是出於行為。今天我們看羅馬書三章 28 節。\n",
        encoding="utf-8",
    )
    laws = tmp_path / "laws"
    laws.mkdir()
    (laws / "civil-code.md").write_text(
        "---\ntitle: 民法總則\ntags: [民法, 法規]\n"
        "pubDate: 2023-09-01\n---\n"
        "民法規範私人間之權利義務關係，總則編闡明法律行為之要件。\n",
        encoding="utf-8",
    )
    classical = tmp_path / "classical"
    classical.mkdir()
    (classical / "calvin-justification.md").write_text(
        "---\ntitle: 加爾文論因信稱義\ntags: [因信稱義, 教義]\n"
        "pubDate: 1559-01-01\n---\n"
        "因信稱義乃宗教改革的核心教義，加爾文以此確立罪人在神面前的地位。"
        "信心是領受恩典的器皿，行為是信心的果實。\n",
        encoding="utf-8",
    )
    (classical / "luther-justification.md").write_text(
        "---\ntitle: 路德論因信稱義\ntags: [因信稱義, 教義]\n"
        "pubDate: 1520-01-01\n---\n"
        "因信稱義是基督徒得救的唯一道路，路德視此為教會存亡的根基。\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def ask_db(ask_corpus: Path, mock_embedder) -> sqlite3.Connection:
    conn = connect(":memory:")
    initialise_schema(conn)
    ingest_dir(
        ask_corpus,
        conn,
        mock_embedder,
        directory_map={
            "sermons": "sermon",
            "laws": "law",
            "classical": "classical",
        },
    )
    yield conn
    conn.close()


@pytest.fixture
def mock_llm_client() -> _MockLLMClient:
    return _MockLLMClient(response="")


@pytest.fixture
def ask_searcher(ask_db: sqlite3.Connection, mock_embedder) -> Searcher:
    return Searcher(ask_db, mock_embedder)


@pytest.fixture
def asker(
    ask_db: sqlite3.Connection,
    mock_llm_client: _MockLLMClient,
    ask_searcher: Searcher,
) -> Asker:
    return Asker(ask_db, llm_client=mock_llm_client, searcher=ask_searcher)


class _CountingSearcher:
    """Wrap a real Searcher to count search() calls."""

    def __init__(self, inner: Searcher) -> None:
        self.inner = inner
        self.calls = 0

    def search(self, *args, **kwargs):
        self.calls += 1
        return self.inner.search(*args, **kwargs)


def test_ask_module_imports() -> None:
    """Smoke test: public surface importable, dataclasses + class present."""
    assert Asker is not None
    assert Answer is not None
    assert Citation is not None
    assert SourceRef is not None
    assert Filter is not None


def test_answer_is_json_serialisable() -> None:
    """Spec: Answer + Citation round-trip via asdict + json.dumps without raising."""
    import json
    from dataclasses import asdict

    answer = Answer(
        query="因信稱義是什麼",
        answer="因信稱義是宗教改革的核心教義。",
        citations=[
            Citation(
                article_id="abc123",
                chunk_index=2,
                title="加爾文論因信稱義",
                snippet="因信稱義乃宗教改革的核心",
                bm25_score=0.87,
            ),
        ],
        retrieval=[
            SourceRef(
                article_id="abc123",
                title="加爾文論因信稱義",
                snippet="因信稱義乃宗教改革的核心",
                bm25_score=0.87,
            ),
        ],
    )
    payload = asdict(answer)
    assert payload["query"] == "因信稱義是什麼"
    assert payload["citations"][0]["chunk_index"] == 2
    assert payload["retrieval"][0]["article_id"] == "abc123"
    json.dumps(payload, ensure_ascii=False)


def test_asker_requires_llm_client(ask_db: sqlite3.Connection) -> None:
    """Spec: Asker(db, llm_client=None) raises TypeError at construction."""
    with pytest.raises(TypeError, match="llm_client"):
        Asker(ask_db, llm_client=None)


def test_ask_cache_miss_invokes_searcher(
    ask_db: sqlite3.Connection,
    mock_llm_client: _MockLLMClient,
    ask_searcher: Searcher,
) -> None:
    """Cache miss path runs searcher.search exactly once."""
    counting = _CountingSearcher(ask_searcher)
    asker = Asker(ask_db, llm_client=mock_llm_client, searcher=counting)
    result = asker.ask("因信稱義", k=3)
    assert counting.calls == 1
    assert result.retrieval, "retrieval should be populated on a cache miss"
    assert all(isinstance(sr, SourceRef) for sr in result.retrieval)


def test_ask_cache_hit_skips_searcher(
    ask_db: sqlite3.Connection,
    mock_llm_client: _MockLLMClient,
    ask_searcher: Searcher,
) -> None:
    """Pre-populated cache row short-circuits retrieval."""
    counting = _CountingSearcher(ask_searcher)
    asker = Asker(ask_db, llm_client=mock_llm_client, searcher=counting)
    primed = Answer(
        query="因信稱義",
        answer="(cached)",
        citations=[
            Citation(
                article_id="cached1",
                chunk_index=0,
                title="cached title",
                snippet="cached snippet",
                bm25_score=1.0,
            )
        ],
        retrieval=[
            SourceRef(
                article_id="cached1",
                title="cached title",
                snippet="cached snippet",
                bm25_score=1.0,
            )
        ],
    )
    cache_put(
        ask_db,
        Asker._cache_key("因信稱義", 5, None, None),
        _answer_to_dict(primed),
    )

    result = asker.ask("因信稱義")
    assert counting.calls == 0
    assert result.answer == "(cached)"
    assert result.citations[0].article_id == "cached1"
    assert result.retrieval[0].article_id == "cached1"


def test_ask_filter_changes_cache_key(
    ask_db: sqlite3.Connection,
    mock_llm_client: _MockLLMClient,
) -> None:
    """Filter dataclass canonical_dict participates in the cache key."""
    base = Asker._cache_key("因信稱義", 5, None, None)
    with_filter = Asker._cache_key("因信稱義", 5, None, Filter(source_type="sermon"))
    other_filter = Asker._cache_key("因信稱義", 5, None, Filter(source_type="law"))
    assert base != with_filter
    assert with_filter != other_filter


def test_ask_prompt_template_has_required_clauses() -> None:
    """ASK_PROMPT must enforce sources-only, fallback phrase, citation numbering."""
    assert "{query}" in ASK_PROMPT
    assert "{sources}" in ASK_PROMPT
    assert "資料中未提及" in ASK_PROMPT
    assert "[1]" in ASK_PROMPT


def test_ask_compose_prompt_lists_sources() -> None:
    sources = [
        SourceRef(article_id="a", title="加爾文論因信稱義", snippet="信心是器皿", bm25_score=1.0),
        SourceRef(article_id="b", title="路德論因信稱義", snippet="唯獨信心", bm25_score=0.8),
    ]
    prompt = Asker._compose_prompt("因信稱義是什麼", sources)
    assert "[1] 加爾文論因信稱義 — 信心是器皿" in prompt
    assert "[2] 路德論因信稱義 — 唯獨信心" in prompt
    assert "因信稱義是什麼" in prompt


def test_ask_llm_success_populates_answer_and_citations(
    ask_db: sqlite3.Connection,
    ask_searcher: Searcher,
) -> None:
    """LLM success path → answer carries LLM text, citations include chunk_index."""
    llm = _MockLLMClient(response="因信稱義是宗教改革核心 [1]")
    asker = Asker(ask_db, llm_client=llm, searcher=ask_searcher)
    result = asker.ask("因信稱義", k=3)
    assert result.answer == "因信稱義是宗教改革核心 [1]"
    assert len(llm.calls) == 1
    assert result.citations, "citations should be populated"
    for citation in result.citations:
        assert citation.chunk_index >= 0


def test_ask_llm_failure_falls_back_to_null_answer(
    ask_db: sqlite3.Connection,
    ask_searcher: Searcher,
) -> None:
    """LLMClientError → answer=None, retrieval + citations still populated, no raise."""
    llm = _MockLLMClient(response=LLMClientError("boom"))
    asker = Asker(ask_db, llm_client=llm, searcher=ask_searcher)
    result = asker.ask("因信稱義")
    assert result.answer is None
    assert result.retrieval, "retrieval should survive LLM failure"
    assert result.citations, "citations should survive LLM failure"
    assert len(llm.calls) == 1


def test_ask_second_call_hits_cache_no_extra_llm_request(
    ask_db: sqlite3.Connection,
    ask_searcher: Searcher,
) -> None:
    """Same query twice → exactly one LLM request total (cache_put → cache_get)."""
    llm = _MockLLMClient(response="cached answer")
    asker = Asker(ask_db, llm_client=llm, searcher=ask_searcher)
    first = asker.ask("因信稱義", k=3)
    second = asker.ask("因信稱義", k=3)
    assert len(llm.calls) == 1
    assert first.answer == second.answer == "cached answer"
    assert [c.article_id for c in first.citations] == [c.article_id for c in second.citations]
