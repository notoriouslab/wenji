"""Tests for ``wenji.observability.health.check_consistency`` and the env
truthy-whitelist helper.

Covers L2 (cross-table sanity, sub-rules c/d/e) + L3 (sample MATCH) and the
``WENJI_DISABLE_STARTUP_CHECK`` env semantics.

Note: L1 (counter ↔ row count) and L2.a / L2.b were removed during apply
(see proposal.md G1 drift correction #2 — wenji_meta build counters are
dead schema columns since v0.1.0). No counter-related assertions here.
"""

from __future__ import annotations

import pytest

from wenji.core.db import connect, initialise_schema
from wenji.observability.health import (
    _is_startup_check_disabled,
    check_consistency,
)


def test_check_consistency_ok_state(healthy_db):
    # Pin keyword to "神" — present in all 3 sermons in tiny_corpus — so the
    # test does not silently rely on default-keyword borderline coverage.
    report = check_consistency(healthy_db, sample_keywords=("神",))
    assert report.ok is True
    assert report.issues == []
    # Row counts should be aligned across articles_meta / chunks_fts /
    # doc_vectors (all derived from the same ingest pass).
    assert report.row_counts["articles_meta"] > 0
    assert report.row_counts["chunks_fts"] > 0
    assert report.row_counts["doc_vectors"] > 0


def test_check_consistency_empty_db_passes():
    """A freshly-initialised empty db reports OK (no L2 / L3 fault).

    Healthy operator workflow `wenji ingest dir ... && wenji serve` would
    otherwise be blocked at startup before any data exists. L2.c/d/e all
    require their respective non-empty side as guard; L3 is gated on both
    FTS indices being non-empty.
    """
    conn = connect(":memory:")
    initialise_schema(conn)

    report = check_consistency(conn)

    assert report.ok is True
    assert report.issues == []
    assert all(v == 0 for v in report.row_counts.values())
    conn.close()


def test_check_consistency_chunks_empty_articles_present(healthy_db):
    """L2.c: articles_meta > 0 but chunks_fts empty (prod bug 範式)."""
    healthy_db.execute("DELETE FROM chunks_fts")
    healthy_db.commit()

    report = check_consistency(healthy_db)

    assert report.ok is False
    joined = "\n".join(report.issues)
    assert "chunks_fts is empty" in joined
    assert "articles_meta has" in joined


def test_check_consistency_doc_vectors_empty_articles_present(healthy_db):
    """L2.d: articles_meta > 0 but doc_vectors empty (embedding missing)."""
    healthy_db.execute("DELETE FROM doc_vectors")
    healthy_db.commit()

    report = check_consistency(healthy_db)

    assert report.ok is False
    joined = "\n".join(report.issues)
    assert "doc_vectors is empty" in joined


def test_check_consistency_reverse_broken_articles_empty_chunks_present(healthy_db):
    """L2.e: chunks_fts > 0 but articles_meta empty (reverse broken state)."""
    healthy_db.execute("DELETE FROM articles_meta")
    healthy_db.commit()

    report = check_consistency(healthy_db)

    assert report.ok is False
    joined = "\n".join(report.issues)
    assert "articles_meta is empty" in joined
    assert "chunks_fts has" in joined


def test_check_consistency_sample_match_all_miss(healthy_db):
    """L3: sample keywords with zero hits → FAIL with override hint."""
    report = check_consistency(healthy_db, sample_keywords=("zzzzz",))

    assert report.ok is False
    joined = "\n".join(report.issues)
    assert "sample keywords missed" in joined
    assert "--sample-keywords" in joined


# ---------------------------------------------------------------------------
# WENJI_DISABLE_STARTUP_CHECK env truthy whitelist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "on", " 1 ", "Yes"])
def test_is_startup_check_disabled_recognises_truthy(monkeypatch, val):
    monkeypatch.setenv("WENJI_DISABLE_STARTUP_CHECK", val)
    assert _is_startup_check_disabled() is True


@pytest.mark.parametrize("val", ["0", "false", "False", "no", "off", "", " ", "anything-else"])
def test_is_startup_check_disabled_does_not_skip_on_footgun_values(monkeypatch, val):
    """Production-safety contract: '0' / 'false' / blank MUST NOT disable.

    Operator intent of '=0 means off' is the opposite of what Python's
    default truthy coercion would do; the helper uses a whitelist so the
    gate stays on for these values.
    """
    monkeypatch.setenv("WENJI_DISABLE_STARTUP_CHECK", val)
    assert _is_startup_check_disabled() is False


def test_is_startup_check_disabled_unset_returns_false(monkeypatch):
    monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)
    assert _is_startup_check_disabled() is False


def test_is_startup_check_disabled_emits_warning(monkeypatch, caplog):
    """Audit trail: a recognised truthy value MUST log a WARNING."""
    monkeypatch.setenv("WENJI_DISABLE_STARTUP_CHECK", "1")
    import logging

    with caplog.at_level(logging.WARNING, logger="wenji.observability.health"):
        _is_startup_check_disabled()

    assert any(
        "DISABLED" in rec.message and "Production deploys MUST NOT" in rec.message
        for rec in caplog.records
    )
