"""Tests for wenji.aggregate (Aggregator + Filter + cache + LLMClient)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from wenji.aggregate import (
    MAX_CHUNKS_PER_TOPIC_SOURCE,
    TOPIC_SOURCES_CHAR_BUDGET,
    Aggregator,
    ConceptPerspectives,
    Filter,
    TopicSummary,
    _assemble_source_blocks,
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
def chunked_db(tmp_path_factory, mock_embedder) -> sqlite3.Connection:
    """A corpus whose articles are actually chunked.

    ``aggregate_db`` holds only ~50-character articles with no chunk
    strategy, so every chunk table stays empty there and any test written
    against it silently exercises the whole-content fallback instead of the
    chunk path.
    """
    root = tmp_path_factory.mktemp("chunked_corpus")
    laws = root / "laws"
    laws.mkdir()
    (laws / "long-labor.md").write_text(
        "---\ntitle: 勞動條件彙編\ntags: [勞動]\npubDate: 2023-06-01\n---\n"
        + "前言段落與本題無關的鋪陳文字。\n\n" * 8
        + "工資給付段落：雇主應於每月五日前給付工資，延遲者依法加計利息。\n\n"
        + "休假段落：勞工每七日中應有二日之休息，其中一日為例假。\n\n"
        + "尾段補充說明，與查詢詞無關的收尾文字。\n",
        encoding="utf-8",
    )
    conn = connect(":memory:")
    initialise_schema(conn)
    ingest_dir(
        root,
        conn,
        mock_embedder,
        directory_map={"laws": "law"},
        chunk_strategies={"law": {"strategy": "paragraph", "min_chars": 1, "max_chars": 120}},
    )
    yield conn
    conn.close()


@pytest.fixture
def mock_llm_client():
    return _MockLLMClient


def test_prompts_are_domain_neutral() -> None:
    """wenji is a generic framework: aggregation prompts must not assume a
    theological (or any other) corpus domain."""
    from wenji.aggregate.prompts import CONCEPT_PROMPT, TOPIC_PROMPT

    for template in (TOPIC_PROMPT, CONCEPT_PROMPT):
        assert "神學" not in template
        assert "基督教" not in template


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

    def test_output_transform_applied_to_chat(self) -> None:
        """chat() runs its result through output_transform (used for s2twp)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "这来"}}]})

        client = LLMClient(
            base_url="https://example.test/v1",
            model="m",
            api_key="k",
            # A transform that visibly changes the text — not str.upper, which
            # is a no-op on Chinese and would make this assertion tautological.
            output_transform=lambda s: f"[[{s}]]",
            _transport=httpx.MockTransport(handler),
        )
        assert client.chat([{"role": "user", "content": "hi"}]) == "[[这来]]"

    def test_output_transform_applied_to_each_stream_piece(self) -> None:
        pieces = ["因信", "稱義"]
        body = TestLLMClientStream._sse_body(pieces)
        client = LLMClient(
            base_url="https://example.test/v1",
            model="m",
            api_key="k",
            output_transform=lambda s: f"[{s}]",
            _transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)),
        )
        assert list(client.chat_stream([{"role": "user", "content": "q"}])) == ["[因信]", "[稱義]"]

    def test_no_transform_is_identity(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "原文"}}]})

        assert _make_client(handler).chat([{"role": "user", "content": "hi"}]) == "原文"

    def test_module_import_does_not_hit_network(self) -> None:
        """Executing the module body must not perform any network call.

        Executed in a throwaway namespace rather than via ``importlib.reload``:
        reloading rebinds ``LLMClientError`` in the live module, so any later
        test in this file would ``pytest.raises`` on a stale class object and
        fail for reasons unrelated to its subject.
        """
        import importlib.util
        import sys

        import wenji.aggregate.llm as llm_module

        spec = importlib.util.spec_from_file_location("_llm_import_probe", llm_module.__file__)
        assert spec is not None and spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        # dataclass creation looks the module up in sys.modules; register the
        # probe under its own throwaway name and drop it again afterwards.
        sys.modules[spec.name] = probe
        try:
            spec.loader.exec_module(probe)
        finally:
            sys.modules.pop(spec.name, None)
        assert hasattr(probe, "LLMClient")
        assert llm_module.LLMClientError is LLMClientError, "live module must stay untouched"


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


class TestAssembleSourceBlocks:
    """Direct tests for the shared source-block assembler.

    sanitize_prompt_input caps its input at 10,000 chars, silently dropping
    the tail. The assembler must keep the whole block (titles + framing +
    bodies) under that ceiling so no source is lost — the failure the
    per-body-only budget kept re-introducing.
    """

    SANITIZE_LIMIT = 10_000

    @pytest.mark.parametrize(
        "n,title_len,chunk_len,chunks",
        [
            (1, 4, 5000, 1),
            (5, 40, 5000, 1),
            (50, 4, 5000, 1),
            (50, 40, 5000, 1),  # 長標題 × 大 k：先前修法在這裡破表
            (20, 40, 1200, 10),  # concept 上限：top_sources=20, per_source=10
        ],
    )
    def test_total_stays_under_sanitize_limit(self, n, title_len, chunk_len, chunks):
        items = [("標" * title_len + str(i), ["文" * chunk_len] * chunks) for i in range(n)]
        block = _assemble_source_blocks(items)
        assert len(block) < self.SANITIZE_LIMIT, f"n={n} 破表：{len(block)}"
        # 餘裕：軟上限之上再加一點 rounding，但務必遠低於硬上限。
        assert len(block) <= TOPIC_SOURCES_CHAR_BUDGET + 200
        for i in range(1, n + 1):
            assert f"來源 {i}:" in block, f"來源 {i} 不見了（會被 sanitize 靜默丟）"

    def test_bodies_front_loaded_when_budget_is_tight(self):
        # 50 篇長標題：預算不足以人人一段，內文集中給排名最前者，尾端只列標頭。
        items = [("勞動基準法函釋彙編第" + f"{i:02d}條", ["內" * 5000]) for i in range(50)]
        block = _assemble_source_blocks(items)
        first = block.split("來源 2:")[0]
        last = block.split("來源 50:")[1]
        assert "\n- " in first, "排名第一的來源必須帶內文"
        assert "\n- " not in last, "預算吃緊時尾端來源應只列標頭"
        assert "來源 50:" in block, "但尾端來源的標頭仍須保留"

    def test_edge_cases(self):
        assert _assemble_source_blocks([]) == ""
        # 空 / 空白 texts → 只列標頭，不炸。
        out = _assemble_source_blocks([("甲", []), ("乙", ["   ", ""])])
        assert "來源 1: 甲" in out
        assert "來源 2: 乙" in out
        assert "\n- " not in out


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

    def test_prompt_revision_bump_busts_cache(
        self, aggregate_db: sqlite3.Connection, mock_llm_client, monkeypatch
    ) -> None:
        """「餵入策略或模板改動時 PROMPT_REVISION MUST 加一」的規則靠這條
        守住：revision 若沒進 cache key，既有部署會在 30 天 TTL 內繼續回
        舊快取，prompt 改動等於沒上線。"""
        import wenji.aggregate as aggregate_module

        client = mock_llm_client(response="ok")
        agg = Aggregator(aggregate_db, llm_client=client)
        agg.topic_summary("禱告", k=5)
        monkeypatch.setattr(
            aggregate_module, "PROMPT_REVISION", aggregate_module.PROMPT_REVISION + 1
        )
        agg.topic_summary("禱告", k=5)
        assert len(client.calls) == 2, "revision 加一必須繞過既有快取、重新呼叫 LLM"

    def test_llm_failure_is_not_cached(
        self, aggregate_db: sqlite3.Connection, mock_llm_client, caplog
    ) -> None:
        """429/超時是暫時的；快取殘缺結果會釘死 30 天（與 ask 同判準）。"""
        client = mock_llm_client(response=LLMClientError("boom"))
        agg = Aggregator(aggregate_db, llm_client=client)
        with caplog.at_level("WARNING", logger="wenji.aggregate"):
            agg.topic_summary("禱告", k=5)
            agg.topic_summary("禱告", k=5)
        assert len(client.calls) == 2, "failed result must not be served from cache"

    def test_narrative_grounds_on_whole_content_when_unchunked(
        self, aggregate_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        """未分塊的短文走全文後援，餵的仍是真原文而非 <mark> snippet。"""
        client = mock_llm_client(response="ok")
        agg = Aggregator(aggregate_db, llm_client=client)
        agg.topic_summary("勞動", k=5)
        prompt = client.calls[0][0]["content"]
        assert "雇主未依規定者依本法處罰" in prompt
        assert "<mark>" not in prompt

    def test_narrative_grounds_on_matching_chunks(
        self, chunked_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        """分塊語料要走 chunk 分支：餵命中段落，而非文章開頭。

        用真的有 chunk 的語料，否則 chunk 查詢整條改成空字串測試也不會紅
        （aggregate_db 的文章全部沒有 chunk）。
        """
        assert chunked_db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] > 0
        client = mock_llm_client(response="ok")
        agg = Aggregator(chunked_db, llm_client=client)
        agg.topic_summary("工資", k=5)
        prompt = client.calls[0][0]["content"]
        assert "雇主應於每月五日前給付工資" in prompt, "命中段落必須進 prompt"
        assert "前言段落與本題無關的鋪陳文字" not in prompt, "不該退回文章開頭"

    def test_chunk_feed_respects_per_source_cap(
        self, chunked_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        client = mock_llm_client(response="ok")
        agg = Aggregator(chunked_db, llm_client=client)
        agg.topic_summary("段落", k=5)
        body = client.calls[0][0]["content"]
        sources_block = body.split("<sources>")[1].split("</sources>")[0]
        assert sources_block.count("\n- ") <= MAX_CHUNKS_PER_TOPIC_SOURCE

    def test_prompt_stays_within_sanitize_limit(
        self, chunked_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        """來源多＋標題長時，組裝的 sources 區塊不得被 sanitize 靜默截斷。

        k 開到上限 50，且標題用真實長度（30 字）——這正是先前修法漏掉的
        失效區：預算只算 chunk 本體、不算標題與「來源 N:」框架，標題一長
        總量就破 sanitize 的 10,000 上限，尾端來源連標頭一起被砍。用短標題
        （如「長文0」）會剛好卡在界內、測不到，所以這裡刻意放長。
        """
        # FTS 的 content 欄存的是斷詞後以空白分隔的文字（中文逐字，見 ingest）。
        tokenised = "工 資 " + "填 充 " * 3000
        raw = "工資" + "填充" * 3000
        k = 50
        long_title = "勞動基準法施行細則暨相關函釋彙編第"  # 30 字級真實標題
        for i in range(k):
            aid = f"synthetic-{i}"
            title = f"{long_title}{i:02d}條"
            chunked_db.execute(
                "INSERT INTO articles_fts (article_id,title,title_raw,content,content_raw,tags,"
                "tags_raw) VALUES (?,?,?,?,?,?,?)",
                (aid, title, title, tokenised, raw, "勞動", "勞動"),
            )
            chunked_db.execute(
                "INSERT INTO articles_meta (article_id,path,title,source_type,chunk_count) "
                "VALUES (?,?,?,?,0)",
                (aid, f"/tmp/{aid}.md", title, "law"),
            )
        chunked_db.commit()
        client = mock_llm_client(response="ok")
        agg = Aggregator(chunked_db, llm_client=client)
        result = agg.topic_summary("工資", k=k)
        assert len(result.top_sources) == k, "fixture 必須真的產生 k 個來源，否則測不到失效區"
        sources = client.calls[0][0]["content"].split("<sources>")[1].split("</sources>")[0]
        # 每個來源的標頭都在 = 尾端沒有被 sanitize 靜默截掉。
        for i in range(1, k + 1):
            assert f"來源 {i}:" in sources, f"來源 {i} 被靜默丟棄"
        # 排名最前的來源要真的帶到內文（header-only 只該落在尾端）。
        assert "\n- " in sources.split("來源 2:")[0], "排名第一的來源沒帶到內文"

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

    def test_source_block_routes_through_bounded_assembler(
        self, aggregate_db: sqlite3.Connection, mock_llm_client
    ) -> None:
        """concept 的 sources 區塊必須走共用組裝器（受總量預算約束）。

        先前 concept 完全沒編總預算，靠 sanitize 硬砍尾端。上限拉到最大
        （top_sources=20, per_source=10）也要留在 sanitize 的 10,000 內；
        並鎖住它產出的是組裝器格式，避免有人日後繞過。"""
        client = mock_llm_client(response=_CONCEPT_LLM_REPLY)
        agg = Aggregator(aggregate_db, llm_client=client)
        agg.concept_perspectives("因信稱義", top_sources=20, per_source=10)
        prompt = client.calls[0][0]["content"]
        sources = prompt.split("<per_source_views>")[1].split("</per_source_views>")[0]
        assert len(sources) < 10_000
        assert "來源 1:" in sources

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

    def test_prompt_revision_bump_busts_cache(
        self, aggregate_db: sqlite3.Connection, mock_llm_client, monkeypatch
    ) -> None:
        """concept 路徑的 cache key 也帶 PROMPT_REVISION，同 topic 一起守。"""
        import wenji.aggregate as aggregate_module

        client = mock_llm_client(response=_CONCEPT_LLM_REPLY)
        agg = Aggregator(aggregate_db, llm_client=client)
        agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        monkeypatch.setattr(
            aggregate_module, "PROMPT_REVISION", aggregate_module.PROMPT_REVISION + 1
        )
        agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        assert len(client.calls) == 2, "revision 加一必須繞過既有快取、重新呼叫 LLM"

    def test_llm_failure_is_not_cached(
        self, aggregate_db: sqlite3.Connection, mock_llm_client, caplog
    ) -> None:
        client = mock_llm_client(response=LLMClientError("boom"))
        agg = Aggregator(aggregate_db, llm_client=client)
        with caplog.at_level("WARNING", logger="wenji.aggregate"):
            agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
            agg.concept_perspectives("因信稱義", top_sources=2, per_source=2)
        assert len(client.calls) == 2, "failed result must not be served from cache"

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


class TestLLMClientStream:
    """chat_stream parses OpenAI-compatible SSE frames."""

    @staticmethod
    def _sse_body(pieces: list[str], *, done: bool = True) -> bytes:
        lines = []
        for p in pieces:
            frame = {"choices": [{"delta": {"content": p}}]}
            lines.append(f"data: {json.dumps(frame, ensure_ascii=False)}")
        if done:
            lines.append("data: [DONE]")
        return ("\n\n".join(lines) + "\n\n").encode()

    def test_chat_stream_yields_content_pieces(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.read())
            return httpx.Response(200, content=self._sse_body(["因信", "稱義"]))

        client = _make_client(handler)
        assert list(client.chat_stream([{"role": "user", "content": "q"}])) == ["因信", "稱義"]
        assert captured["body"]["stream"] is True
        assert captured["body"]["temperature"] == 0.1

    def test_chat_stream_skips_malformed_frames(self) -> None:
        """One bad frame must not abort a stream that is otherwise fine."""
        body = (
            b'data: {"choices": [{"delta": {"content": "\xe5\xa5\xbd"}}]}\n\n'
            b"data: not-json\n\n"
            b'data: {"choices": []}\n\n'
            b": comment line\n\n"
            b'data: {"choices": [{"delta": {"content": "\xe7\x9a\x84"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        client = _make_client(lambda request: httpx.Response(200, content=body))
        assert list(client.chat_stream([{"role": "user", "content": "q"}])) == ["好", "的"]

    def test_chat_stream_raises_on_http_error(self) -> None:
        client = _make_client(lambda request: httpx.Response(429, content=b"rate limited"))
        with pytest.raises(LLMClientError) as exc:
            list(client.chat_stream([{"role": "user", "content": "q"}]))
        assert "stream failed" in str(exc.value)

    def test_chat_stream_raises_when_no_content_produced(self) -> None:
        client = _make_client(lambda request: httpx.Response(200, content=b"data: [DONE]\n\n"))
        with pytest.raises(LLMClientError) as exc:
            list(client.chat_stream([{"role": "user", "content": "q"}]))
        assert "no content" in str(exc.value)

    def test_chat_stream_redacts_bearer_token_in_errors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed with {request.headers['authorization']}")

        client = _make_client(handler)
        with pytest.raises(LLMClientError) as exc:
            list(client.chat_stream([{"role": "user", "content": "q"}]))
        assert "sk-test" not in str(exc.value)
        assert "Bearer ***" in str(exc.value)
