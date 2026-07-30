"""Tests for wenji.search.bm25."""

from __future__ import annotations

import logging
import sqlite3
from unittest.mock import MagicMock

import pytest

from wenji.core.errors import SearchError
from wenji.search.bm25 import (
    MAX_OR_TERMS,
    bm25_search,
    build_fts_query,
    build_fts_query_or,
)


def test_bm25_returns_results(populated_db):
    results = bm25_search(populated_db, "因信稱義")
    assert len(results) > 0
    assert all("article_id" in r for r in results)


def test_bm25_score_normalised_to_unit_range(populated_db):
    results = bm25_search(populated_db, "禱告")
    for r in results:
        assert 0.0 <= r["bm25_score"] <= 1.0


def test_bm25_top_result_has_max_score(populated_db):
    results = bm25_search(populated_db, "禱告 屬靈")
    if results:
        assert abs(results[0]["bm25_score"] - 1.0) < 1e-6


def test_bm25_empty_query_returns_empty(populated_db):
    assert bm25_search(populated_db, "") == []
    assert bm25_search(populated_db, "   ") == []


def test_bm25_excludes_excluded_category(populated_db):
    results = bm25_search(populated_db, "宣教")
    assert all(r["category"] != "excluded" for r in results)


def test_bm25_axis_filter(populated_db):
    results = bm25_search(populated_db, "因信稱義", axis="theology")
    assert len(results) >= 1
    no_axis_results = bm25_search(populated_db, "因信稱義", axis="nonexistent")
    assert no_axis_results == []


def test_bm25_axis_filter_matches_propagated_rows(populated_db):
    """Propagated ancestor rows from hierarchical classify match axis filter."""
    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
    ).fetchone()[0]
    populated_db.execute(
        "INSERT INTO article_axes (article_id, axis_id, is_primary) VALUES (?, ?, 0)",
        (aid, "meta_theology"),
    )
    populated_db.commit()
    results = bm25_search(populated_db, "因信稱義", axis="meta_theology")
    assert any(r["article_id"] == aid for r in results)


def test_bm25_limit_caps_results(populated_db):
    results = bm25_search(populated_db, "禱告", limit=1)
    assert len(results) <= 1


def test_bm25_search_logs_warning_on_operational_error(caplog):
    """OperationalError must emit WARNING and preserve existing SearchError raise."""
    fake_conn = MagicMock(spec=sqlite3.Connection)
    fake_conn.execute = MagicMock(side_effect=sqlite3.OperationalError("simulated lock"))

    caplog.set_level(logging.WARNING, logger="wenji.search.bm25")

    with pytest.raises(SearchError) as excinfo:
        bm25_search(fake_conn, "test query", limit=10)

    # Existing raise behaviour preserved unchanged (message + cause chain)
    assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)
    assert "FTS5 query failed" in str(excinfo.value)

    # New: warning emitted with table name + stack trace
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) >= 1
    assert "articles_fts query failed" in warnings[0].getMessage()
    assert warnings[0].exc_info is not None


def test_build_fts_query_or_matches_natural_language_question():
    """A Chinese question must produce a query that can actually match.

    The AND builder collapses a space-free question into one phrase demanding
    every character appear consecutively, so it matches nothing.
    """
    question = "開公務車出車禍，我自己要付多少錢？"
    and_query = build_fts_query(question, column="chunk_text")
    or_query = build_fts_query_or(question, column="chunk_text")

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE chunks_fts USING fts5("
        "article_id UNINDEXED, chunk_index UNINDEXED, chunk_text, "
        "tokenize = 'unicode61')"
    )
    conn.execute(
        "INSERT INTO chunks_fts (article_id, chunk_index, chunk_text) VALUES (?, ?, ?)",
        ("a", 6, " ".join("開車者需負責一半費用上限8000元")),
    )

    and_hits = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (and_query,)
    ).fetchone()[0]
    or_hits = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (or_query,)
    ).fetchone()[0]
    conn.close()

    assert and_hits == 0
    assert or_hits == 1


def test_build_fts_query_or_drops_interrogative_and_filler_terms():
    """Interrogatives, fillers and adverbs are stripped; content terms survive.

    `補助最多可以拿多少？` reduces to a single content term because 最多 (POS d)
    and 可以 (POS c) fall to DROP_POS while 拿 and 多少 are stopwords, so this
    case asserts no OR — see the multi-term case below for the OR shape.
    """
    out = build_fts_query_or("補助最多可以拿多少？")
    for dropped in ("多 少", "可 以", "拿", "最 多"):
        assert f'"{dropped}"' not in out
    assert out == '"補 助"'

    multi = build_fts_query_or("霸凌申訴送出去之後，多久會查完？")
    assert '"霸 凌"' in multi
    assert '"申 訴"' in multi
    assert '"多 久"' not in multi
    assert " OR " in multi


def test_build_fts_query_or_drops_punctuation_only_tokens():
    out = build_fts_query_or("補助，？！")
    assert out == '"補 助"'


def test_build_fts_query_or_returns_empty_when_nothing_survives():
    assert build_fts_query_or("") == ""
    assert build_fts_query_or("，？") == ""
    assert build_fts_query_or("這個可以嗎？") == ""


def test_build_fts_query_or_column_prefixes_every_phrase():
    out = build_fts_query_or("公務車 肇事", column="chunk_text")
    phrases = out.split(" OR ")
    assert len(phrases) >= 2
    assert all(p.startswith("chunk_text:") for p in phrases)


def test_build_fts_query_and_builder_is_unchanged():
    """The AND builder keeps its contract — callers depend on the semantics."""
    assert build_fts_query("因信稱義") == '"因 信 稱 義"'
    assert build_fts_query("因信稱義", column="chunk_text") == 'chunk_text:"因 信 稱 義"'
    assert build_fts_query("因信 稱義") == '"因 信" "稱 義"'
    assert "OR" not in build_fts_query("因信 稱義")
    assert build_fts_query("") == ""


def test_build_fts_query_or_caps_term_count():
    """A pathologically long query must not become an unbounded FTS expression."""
    long_query = "".join(f"規章{i}辦法{i} " for i in range(500))
    out = build_fts_query_or(long_query, column="chunk_text")
    assert out.count(" OR ") + 1 == MAX_OR_TERMS


def test_build_fts_query_caps_term_count():
    """The AND builder shares the same cap as the OR builder.

    Uncapped, a pasted wall of terms becomes a phrase-per-term MATCH
    expression whose cost grows superlinearly (measured: 3200 terms →
    22.9 s per request, degrading concurrent searches 0.03 s → 1.7 s).
    """
    long_query = " ".join(f"規章{i}" for i in range(500))
    out = build_fts_query(long_query)
    # Each surviving term becomes exactly one quoted phrase.
    assert out.count('"') == MAX_OR_TERMS * 2
