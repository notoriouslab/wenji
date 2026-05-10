"""Tests for wenji.search.bm25."""

from __future__ import annotations

import logging
import sqlite3
from unittest.mock import MagicMock

import pytest

from wenji.core.errors import SearchError
from wenji.search.bm25 import bm25_search


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
    fake_conn.execute = MagicMock(
        side_effect=sqlite3.OperationalError("simulated lock")
    )

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
