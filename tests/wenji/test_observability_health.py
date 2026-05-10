"""Tests for ``wenji.observability.health.check_consistency``.

Covers L2 (cross-table sanity, sub-rules c/d) + L3 (sample MATCH).
Uses the ``healthy_db`` fixture (in-memory) since check_consistency takes a
connection directly.

Note: L1 (counter ↔ row count) and L2.a / L2.b were removed during apply
(see proposal.md G1 drift correction #2 — wenji_meta build counters are
dead schema columns since v0.1.0). No counter-related assertions here.
"""

from __future__ import annotations

from wenji.observability.health import check_consistency


def test_check_consistency_ok_state(healthy_db):
    report = check_consistency(healthy_db)
    assert report.ok is True
    assert report.issues == []
    # Row counts should be aligned across articles_meta / chunks_fts /
    # doc_vectors (all derived from the same ingest pass).
    assert report.row_counts["articles_meta"] > 0
    assert report.row_counts["chunks_fts"] > 0
    assert report.row_counts["doc_vectors"] > 0


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


def test_check_consistency_sample_match_all_miss(healthy_db):
    """L3: sample keywords with zero hits → FAIL with override hint."""
    report = check_consistency(healthy_db, sample_keywords=("zzzzz",))

    assert report.ok is False
    joined = "\n".join(report.issues)
    assert "sample keywords missed" in joined
    assert "--sample-keywords" in joined
