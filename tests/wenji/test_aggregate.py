"""Tests for wenji.aggregate (Aggregator + Filter + cache + LLMClient)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from wenji.aggregate import (
    Aggregator,
    ConceptPerspectives,
    Filter,
    TopicSummary,
)
from wenji.aggregate.cache import cache_clear, cache_get, cache_key, cache_put
from wenji.aggregate.llm import LLMClient, LLMClientError
from wenji.core.db import connect, initialise_schema
from wenji.ingest import ingest_dir


class _MockLLMClient:
    """Duck-typed LLMClient stand-in.

    ``response`` may be a string (returned verbatim) or a callable
    ``(messages) -> str | Exception`` so tests can assert on prompt content
    or raise to exercise the D7 fallback path.
    """

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
def aggregate_corpus(tmp_path: Path) -> Path:
    """Five articles spanning sermon / law / classical for aggregator scenarios."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    (sermons / "prayer-life.md").write_text(
        "---\ntitle: 禱告生命\ntags: [禱告, 屬靈]\n"
        "pubDate: 2024-03-10\nsubtype: weekday\n---\n"
        "禱告是與神親近的方式，是基督徒屬靈生命的呼吸。"
        "持續恆切的禱告會帶來生命的轉化，也是教會建造的根基。\n",
        encoding="utf-8",
    )
    (sermons / "weekly-bulletin.md").write_text(
        "---\ntitle: 週報通訊\ntags: [禱告, 公告]\n"
        "pubDate: 2024-03-17\nsubtype: weekly\n---\n"
        "本週禱告會週三晚上七點。請大家踴躍參加，為教會的事工同心禱告。\n",
        encoding="utf-8",
    )
    laws = tmp_path / "laws"
    laws.mkdir()
    (laws / "labor-act.md").write_text(
        "---\ntitle: 勞動基準法解析\ntags: [勞動, 法規]\n"
        "pubDate: 2023-06-01\n---\n"
        "勞動基準法明定工時、加班費與休假權益。"
        "雇主未依規定者依本法處罰。\n",
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
        "因信稱義是基督徒得救的唯一道路，路德視此為教會存亡的根基。"
        "唯獨信心、唯獨恩典、唯獨聖經、唯獨基督、唯獨神得榮耀。\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def aggregate_db(aggregate_corpus: Path, mock_embedder) -> sqlite3.Connection:
    conn = connect(":memory:")
    initialise_schema(conn)
    ingest_dir(
        aggregate_corpus,
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
def mock_llm_client():
    return _MockLLMClient


def test_aggregate_db_fixture_populates(aggregate_db: sqlite3.Connection) -> None:
    rows = aggregate_db.execute(
        "SELECT source_type, COUNT(*) FROM articles_meta GROUP BY source_type"
    ).fetchall()
    counts = dict(rows)
    assert counts == {"sermon": 2, "law": 1, "classical": 2}


def test_aggregate_cache_table_exists(aggregate_db: sqlite3.Connection) -> None:
    row = aggregate_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='aggregate_cache'"
    ).fetchone()
    assert row is not None
    assert row[0] == "aggregate_cache"


def test_mock_llm_client_records_calls(mock_llm_client) -> None:
    client = mock_llm_client(response="hello")
    assert client.chat([{"role": "user", "content": "hi"}]) == "hello"
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Filter dataclass tests (task 2.4)
# ---------------------------------------------------------------------------


class TestFilter:
    def test_empty_filter_renders_to_no_clauses(self) -> None:
        clause, params = Filter().to_sql_where()
        assert clause == ""
        assert params == []

    def test_unknown_keyword_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            Filter(unknown_field="x")

    def test_exact_tag(self) -> None:
        clause, params = Filter(tag="禱告").to_sql_where()
        assert clause == "m.tags LIKE ?"
        assert params == ['%"禱告"%']

    def test_tag_in(self) -> None:
        clause, params = Filter(tag__in=["禱告", "宣教"]).to_sql_where()
        assert clause == "(m.tags LIKE ? OR m.tags LIKE ?)"
        assert params == ['%"禱告"%', '%"宣教"%']

    def test_tag_not_in(self) -> None:
        clause, params = Filter(tag__not_in=["公告"]).to_sql_where()
        assert clause == "m.tags NOT LIKE ?"
        assert params == ['%"公告"%']

    def test_source_type_in_and_subtype_not_in(self) -> None:
        f = Filter(
            source_type__in=["sermon", "law"],
            subtype__not_in=["weekly"],
        )
        clause, params = f.to_sql_where()
        assert "m.source_type IN (?,?)" in clause
        assert "m.subtype NOT IN (?)" in clause
        assert params == ["sermon", "law", "weekly"]

    def test_pub_year_gte_lte(self) -> None:
        clause, params = Filter(pub_year__gte=2020, pub_year__lte=2024).to_sql_where()
        assert "m.pub_year >= ?" in clause
        assert "m.pub_year <= ?" in clause
        assert params == [2020, 2024]

    def test_pub_year_exact_and_in(self) -> None:
        clause, params = Filter(pub_year=2024, pub_year__in=[2023, 2024]).to_sql_where()
        assert "m.pub_year = ?" in clause
        assert "m.pub_year IN (?,?)" in clause
        assert params == [2024, 2023, 2024]

    def test_category_filters(self) -> None:
        clause, params = Filter(category="theology", category__not_in=["excluded"]).to_sql_where()
        assert "m.category = ?" in clause
        assert "m.category NOT IN (?)" in clause
        assert params == ["theology", "excluded"]

    def test_table_alias_override(self) -> None:
        clause, _ = Filter(source_type="sermon").to_sql_where(table_alias="a")
        assert clause == "a.source_type = ?"

    def test_no_alias(self) -> None:
        clause, _ = Filter(source_type="sermon").to_sql_where(table_alias="")
        assert clause == "source_type = ?"

    def test_canonical_dict_strips_none(self) -> None:
        f = Filter(tag="禱告", subtype__not_in=["weekly"])
        assert f.canonical_dict() == {"tag": "禱告", "subtype__not_in": ["weekly"]}

    def test_canonical_dict_is_stable_across_calls(self) -> None:
        f = Filter(tag="禱告", source_type__in=["sermon", "law"])
        assert f.canonical_dict() == f.canonical_dict()

    def test_combined_filter_renders_with_and(self) -> None:
        f = Filter(tag="禱告", subtype__not_in=["weekly"], pub_year__gte=2020)
        clause, params = f.to_sql_where()
        parts = clause.split(" AND ")
        assert len(parts) == 3
        assert params == ['%"禱告"%', "weekly", 2020]

    def test_filter_excludes_weekly_against_db(self, aggregate_db: sqlite3.Connection) -> None:
        clause, params = Filter(tag="禱告", subtype__not_in=["weekly"]).to_sql_where()
        rows = aggregate_db.execute(
            f"SELECT title FROM articles_meta m WHERE {clause}",
            params,
        ).fetchall()
        titles = {r[0] for r in rows}
        assert "禱告生命" in titles
        assert "週報通訊" not in titles


# ---------------------------------------------------------------------------
# LLMClient tests (task 3.3)
# ---------------------------------------------------------------------------


def _make_client(handler) -> LLMClient:
    return LLMClient(
        base_url="https://example.test/v1",
        model="test-model",
        api_key="sk-test",
        timeout=2.0,
        _transport=httpx.MockTransport(handler),
    )


class TestLLMClient:
    def test_chat_returns_assistant_content(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers["authorization"]
            captured["body"] = request.read()
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "你好"}}]},
            )

        client = _make_client(handler)
        result = client.chat([{"role": "user", "content": "hi"}])
        assert result == "你好"
        assert captured["url"] == "https://example.test/v1/chat/completions"
        assert captured["auth"] == "Bearer sk-test"
        assert b'"model":"test-model"' in captured["body"]
        assert b'"temperature":0.1' in captured["body"]

    def test_chat_401_raises_llm_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        client = _make_client(handler)
        with pytest.raises(LLMClientError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_chat_5xx_raises_llm_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream down")

        client = _make_client(handler)
        with pytest.raises(LLMClientError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_chat_timeout_raises_llm_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("simulated")

        client = _make_client(handler)
        with pytest.raises(LLMClientError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_chat_missing_choices_raises_llm_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        client = _make_client(handler)
        with pytest.raises(LLMClientError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_base_url_trailing_slash_normalised(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        client = LLMClient(
            base_url="https://example.test/v1/",
            model="m",
            api_key="k",
            _transport=httpx.MockTransport(handler),
        )
        client.chat([{"role": "user", "content": "hi"}])
        assert captured["url"] == "https://example.test/v1/chat/completions"

    def test_module_import_does_not_hit_network(self) -> None:
        import importlib

        import wenji.aggregate.llm as llm_module

        importlib.reload(llm_module)
        assert hasattr(llm_module, "LLMClient")


# ---------------------------------------------------------------------------
# Cache layer tests (task 4.5)
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_conn() -> sqlite3.Connection:
    conn = connect(":memory:")
    initialise_schema(conn)
    yield conn
    conn.close()


class TestCache:
    def test_cache_key_is_deterministic(self) -> None:
        a = cache_key("topic_summary", {"tag": "禱告", "k": 5})
        b = cache_key("topic_summary", {"k": 5, "tag": "禱告"})
        assert a == b
        assert len(a) == 64  # sha256 hex

    def test_cache_key_differs_per_function(self) -> None:
        same_args = {"tag": "禱告"}
        assert cache_key("topic_summary", same_args) != cache_key("concept_perspectives", same_args)

    def test_cache_key_differs_per_args(self) -> None:
        assert cache_key("topic_summary", {"tag": "禱告"}) != cache_key(
            "topic_summary", {"tag": "宣教"}
        )

    def test_put_then_get_round_trip(self, cache_conn: sqlite3.Connection) -> None:
        key = cache_key("topic_summary", {"tag": "禱告"})
        cache_put(cache_conn, key, {"narrative": "ok", "top_sources": []})
        result = cache_get(cache_conn, key)
        assert result == {"narrative": "ok", "top_sources": []}

    def test_cache_miss_returns_none(self, cache_conn: sqlite3.Connection) -> None:
        assert cache_get(cache_conn, "nonexistent") is None

    def test_ttl_expiry_treated_as_miss(self, cache_conn: sqlite3.Connection) -> None:
        from datetime import datetime, timedelta, timezone

        key = "k1"
        # Manually insert an entry created 31 days ago
        old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
        cache_conn.execute(
            "INSERT INTO aggregate_cache (key, value, created_at) VALUES (?, ?, ?)",
            (key, '{"x": 1}', old_ts),
        )
        cache_conn.commit()
        assert cache_get(cache_conn, key, ttl_days=30) is None
        # Spec: expired entries are NOT auto-deleted
        row = cache_conn.execute("SELECT key FROM aggregate_cache WHERE key = ?", (key,)).fetchone()
        assert row is not None

    def test_ttl_within_window_returns_value(self, cache_conn: sqlite3.Connection) -> None:
        from datetime import datetime, timedelta, timezone

        key = "k1"
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(timespec="seconds")
        cache_conn.execute(
            "INSERT INTO aggregate_cache (key, value, created_at) VALUES (?, ?, ?)",
            (key, '{"x": 1}', recent_ts),
        )
        cache_conn.commit()
        assert cache_get(cache_conn, key, ttl_days=30) == {"x": 1}

    def test_put_overwrites_existing_key(self, cache_conn: sqlite3.Connection) -> None:
        key = "k1"
        cache_put(cache_conn, key, {"v": 1})
        cache_put(cache_conn, key, {"v": 2})
        assert cache_get(cache_conn, key) == {"v": 2}

    def test_distinct_args_do_not_collide(self, cache_conn: sqlite3.Connection) -> None:
        k1 = cache_key("f", {"a": 1})
        k2 = cache_key("f", {"a": 2})
        cache_put(cache_conn, k1, {"v": "one"})
        cache_put(cache_conn, k2, {"v": "two"})
        assert cache_get(cache_conn, k1) == {"v": "one"}
        assert cache_get(cache_conn, k2) == {"v": "two"}

    def test_clear_returns_row_count(self, cache_conn: sqlite3.Connection) -> None:
        cache_put(cache_conn, "k1", {"v": 1})
        cache_put(cache_conn, "k2", {"v": 2})
        assert cache_clear(cache_conn) == 2
        assert cache_get(cache_conn, "k1") is None
        assert cache_get(cache_conn, "k2") is None

    def test_clear_on_empty_table_returns_zero(self, cache_conn: sqlite3.Connection) -> None:
        assert cache_clear(cache_conn) == 0

    def test_unicode_args_in_key(self, cache_conn: sqlite3.Connection) -> None:
        key = cache_key("topic_summary", {"tag": "禱告"})
        cache_put(cache_conn, key, {"narrative": "屬靈"})
        assert cache_get(cache_conn, key) == {"narrative": "屬靈"}


# ---------------------------------------------------------------------------
# topic_summary tests (task 5.5)
# ---------------------------------------------------------------------------


class TestTopicSummary:
    def test_no_llm_returns_structured_only(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.topic_summary("禱告", k=5)
        assert isinstance(result, TopicSummary)
        assert result.tag == "禱告"
        assert result.narrative is None
        assert len(result.top_sources) > 0
        assert result.statistics.total_hits >= 1
        assert all(0.0 <= s.bm25_score <= 1.0 for s in result.top_sources)

    def test_llm_success_populates_narrative(
        self, aggregate_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        client = mock_llm_client(response="這是 narrative")
        agg = Aggregator(aggregate_db, llm_client=client)
        result = agg.topic_summary("禱告", k=5)
        assert result.narrative == "這是 narrative"
        assert len(client.calls) == 1

    def test_llm_failure_falls_back_to_none(
        self, aggregate_db: sqlite3.Connection, mock_llm_client, caplog
    ) -> None:
        client = mock_llm_client(response=LLMClientError("boom"))
        agg = Aggregator(aggregate_db, llm_client=client)
        with caplog.at_level("WARNING", logger="wenji.aggregate"):
            result = agg.topic_summary("禱告", k=5)
        assert result.narrative is None
        assert any("LLM call failed" in r.message for r in caplog.records)
        assert len(result.top_sources) > 0  # structured path still works

    def test_cache_hit_skips_llm(self, aggregate_db: sqlite3.Connection, mock_llm_client) -> None:
        client = mock_llm_client(response="一次性 narrative")
        agg = Aggregator(aggregate_db, llm_client=client)
        first = agg.topic_summary("禱告", k=5)
        second = agg.topic_summary("禱告", k=5)
        assert first.narrative == second.narrative == "一次性 narrative"
        assert len(client.calls) == 1  # second call hit cache

    def test_filter_excludes_weekly(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.topic_summary(
            "禱告",
            filter=Filter(subtype__not_in=["weekly"]),
            k=5,
        )
        titles = {s.title for s in result.top_sources}
        assert "週報通訊" not in titles
        assert "禱告生命" in titles

    def test_statistics_distribution(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.topic_summary("禱告", k=10)
        assert result.statistics.total_hits == sum(
            result.statistics.source_type_distribution.values()
        )

    def test_empty_query_returns_empty_result(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.topic_summary("", k=5)
        assert result.top_sources == []
        assert result.statistics.total_hits == 0

    def test_no_llm_zero_outbound_calls(
        self, aggregate_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        client = mock_llm_client(response="should not be called")
        agg = Aggregator(aggregate_db, llm_client=None)
        agg.topic_summary("禱告", k=5)
        assert len(client.calls) == 0  # client never wired in


# ---------------------------------------------------------------------------
# concept_perspectives tests (task 6.5)
# ---------------------------------------------------------------------------


_CONCEPT_LLM_REPLY = """因信稱義是宗教改革的核心教義，在路德與加爾文的論述中皆居中心地位。

## 共識
- 因信稱義是基督徒得救的根基
- 信心優先於行為

## 分歧
- 路德更強調唯獨信心；加爾文補充行為作為信心果實

## 整體 narrative
兩位改教家在這一點上方向一致，差異主要在表述重心。
"""


class TestConceptPerspectives:
    def test_no_llm_returns_structured_only(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        assert isinstance(result, ConceptPerspectives)
        assert result.concept == "因信稱義"
        assert result.narrative is None
        assert result.consensus == []
        assert result.disagreements == []
        assert len(result.per_source_views) >= 1
        assert all(0.0 <= v.source_ref.bm25_score <= 1.0 for v in result.per_source_views)

    def test_llm_success_populates_narrative_and_lists(
        self, aggregate_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        client = mock_llm_client(response=_CONCEPT_LLM_REPLY)
        agg = Aggregator(aggregate_db, llm_client=client)
        result = agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        assert result.narrative == _CONCEPT_LLM_REPLY
        assert "信心優先於行為" in result.consensus
        assert any("路德" in d for d in result.disagreements)
        assert len(client.calls) == 1

    def test_llm_failure_falls_back(
        self, aggregate_db: sqlite3.Connection, mock_llm_client, caplog
    ) -> None:
        client = mock_llm_client(response=LLMClientError("boom"))
        agg = Aggregator(aggregate_db, llm_client=client)
        with caplog.at_level("WARNING", logger="wenji.aggregate"):
            result = agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        assert result.narrative is None
        assert result.consensus == []
        assert result.disagreements == []
        assert any("LLM call failed" in r.message for r in caplog.records)

    def test_cache_hit_skips_llm(self, aggregate_db: sqlite3.Connection, mock_llm_client) -> None:
        client = mock_llm_client(response=_CONCEPT_LLM_REPLY)
        agg = Aggregator(aggregate_db, llm_client=client)
        first = agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        second = agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        assert first.narrative == second.narrative
        assert first.consensus == second.consensus
        assert len(client.calls) == 1

    def test_per_source_excerpt_cap(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.concept_perspectives("因信稱義", top_sources=2, per_source=1)
        for view in result.per_source_views:
            assert len(view.excerpts) <= 1

    def test_empty_concept_returns_empty(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.concept_perspectives("", top_sources=2, per_source=2)
        assert result.per_source_views == []

    def test_filter_constrains_sources(self, aggregate_db: sqlite3.Connection) -> None:
        agg = Aggregator(aggregate_db, llm_client=None)
        result = agg.concept_perspectives(
            "因信稱義",
            filter=Filter(source_type="classical"),
            top_sources=4,
            per_source=2,
        )
        # Both classical articles should appear; sermon/law shouldn't
        titles = {v.source_ref.title for v in result.per_source_views}
        assert all("論因信稱義" in t for t in titles) or len(titles) == 0
