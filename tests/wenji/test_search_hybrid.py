"""Tests for wenji.search.hybrid."""

from __future__ import annotations

import pytest

from wenji.search.hybrid import DEFAULT_ALPHA, hybrid_combine


def _bm(article_id: str, score: float, **extra) -> dict:
    return {"article_id": article_id, "bm25_score": score, **extra}


def _vec(article_id: str, score: float) -> dict:
    return {"article_id": article_id, "cosine_score": score}


def test_default_alpha_value():
    assert DEFAULT_ALPHA == 0.25


def test_alpha_validates_range():
    with pytest.raises(ValueError):
        hybrid_combine([], [], alpha=-0.1)
    with pytest.raises(ValueError):
        hybrid_combine([], [], alpha=1.5)


def test_only_bm25_no_vector():
    out = hybrid_combine([_bm("a", 1.0), _bm("b", 0.5)], [], alpha=1.0)
    assert out[0]["article_id"] == "a"
    assert out[0]["hybrid_score"] == pytest.approx(1.0)
    assert out[1]["hybrid_score"] == pytest.approx(0.5)


def test_only_vector_no_bm25():
    out = hybrid_combine([], [_vec("x", 0.9), _vec("y", 0.4)], alpha=0.0)
    assert out[0]["article_id"] == "x"
    assert out[0]["hybrid_score"] == pytest.approx(0.9)


def test_hybrid_merges_overlapping_articles():
    bm = [_bm("a", 1.0), _bm("b", 0.5)]
    vec = [_vec("a", 0.8), _vec("c", 0.6)]
    out = hybrid_combine(bm, vec, alpha=0.5)
    by_id = {d["article_id"]: d for d in out}
    assert by_id["a"]["hybrid_score"] == pytest.approx(0.5 * 1.0 + 0.5 * 0.8)
    assert by_id["b"]["hybrid_score"] == pytest.approx(0.5 * 0.5)
    assert by_id["c"]["hybrid_score"] == pytest.approx(0.5 * 0.6)


def test_hybrid_default_alpha_favours_cosine():
    bm = [_bm("a", 1.0)]
    vec = [_vec("b", 1.0)]
    out = hybrid_combine(bm, vec)  # default 0.25
    by_id = {d["article_id"]: d for d in out}
    assert by_id["b"]["hybrid_score"] > by_id["a"]["hybrid_score"]


def test_hybrid_preserves_bm25_metadata():
    bm = [_bm("a", 1.0, title="A", source_type="sermon")]
    out = hybrid_combine(bm, [], alpha=1.0)
    assert out[0]["title"] == "A"
    assert out[0]["source_type"] == "sermon"


def test_hybrid_limit_truncates_output():
    bm = [_bm(f"a{i}", float(10 - i)) for i in range(10)]
    out = hybrid_combine(bm, [], alpha=1.0, limit=3)
    assert len(out) == 3


def test_hybrid_results_sorted_descending():
    out = hybrid_combine(
        [_bm("a", 0.3), _bm("b", 0.9), _bm("c", 0.6)],
        [],
        alpha=1.0,
    )
    scores = [d["hybrid_score"] for d in out]
    assert scores == sorted(scores, reverse=True)
