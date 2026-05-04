"""Tests for ``wenji.observability.compute_stats``."""

from __future__ import annotations

from wenji.classify.axes_loader import (
    UNCLASSIFIED,
    Axis,
    AxesConfig,
    ValidationBounds,
)
from wenji.core.db import connect, initialise_schema
from wenji.observability import compute_stats


def _empty_db():
    conn = connect(":memory:")
    initialise_schema(conn)
    return conn


def test_compute_stats_empty_db_returns_zero_counts_and_null_timestamp():
    conn = _empty_db()
    try:
        stats = compute_stats(conn, axes_config=None)
    finally:
        conn.close()

    assert stats["articles"] == 0
    assert stats["chunks"] == 0
    assert stats["indices"]["fts5_articles"] == 0
    assert stats["indices"]["fts5_chunks"] == 0
    assert stats["indices"]["vector_count"] == 0
    assert stats["indices"]["vector_dims"] == 0
    assert stats["source_types"] == {}
    assert stats["axes"] == {}
    assert stats["last_ingest_at"] is None


def test_compute_stats_populated_db_reports_counts(populated_db):
    stats = compute_stats(populated_db, axes_config=None)

    assert stats["articles"] >= 1
    # populated_db's default chunk strategy may produce 0 chunks; assert
    # consistency rather than a specific count.
    assert stats["chunks"] == stats["indices"]["fts5_chunks"]
    assert stats["indices"]["fts5_articles"] == stats["articles"]
    assert stats["indices"]["vector_count"] == stats["articles"]
    assert stats["indices"]["vector_dims"] == 1024
    assert "sermon" in stats["source_types"]
    assert stats["last_ingest_at"] is not None
    assert "T" in stats["last_ingest_at"]  # ISO8601


def test_compute_stats_axes_empty_when_config_none(populated_db):
    """populated_db fixture inserts an axis assignment but axes_config=None
    means we can't translate axis_id -> label, so axes={} per Decision 1."""
    stats = compute_stats(populated_db, axes_config=None)
    assert stats["axes"] == {}


def test_compute_stats_axes_populated_with_config(populated_db):
    cfg = AxesConfig(
        version=1,
        axes=(
            Axis(id="theology", name="神學", order=1, rules=()),
        ),
        validation=ValidationBounds(),
    )
    stats = compute_stats(populated_db, axes_config=cfg)
    assert stats["axes"] == {"神學": 1}


def test_compute_stats_axes_skips_unclassified(populated_db):
    """UNCLASSIFIED axis_id should be filtered out even if assignments exist."""
    populated_db.execute(
        "INSERT INTO article_axes (article_id, axis_id, is_primary) "
        "SELECT article_id, ?, 0 FROM articles_meta LIMIT 1",
        (UNCLASSIFIED,),
    )
    populated_db.commit()
    cfg = AxesConfig(
        version=1,
        axes=(Axis(id="theology", name="神學", order=1, rules=()),),
        validation=ValidationBounds(),
    )
    stats = compute_stats(populated_db, axes_config=cfg)
    assert UNCLASSIFIED not in stats["axes"]
    assert stats["axes"] == {"神學": 1}
