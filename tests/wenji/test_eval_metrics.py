"""Tests for wenji.eval.metrics."""

from __future__ import annotations

from wenji.eval.jsonl import Candidate
from wenji.eval.metrics import (
    aggregate,
    count_keyword_hits,
    evaluate_question,
    title_fuzzy_match,
)


def _cand(**overrides) -> Candidate:
    base = {
        "id": 1,
        "query": "查詢",
        "expected_keywords": ("恩典", "稱義"),
        "expected_article_hints": (),
        "category": "theology",
        "source": "test",
    }
    base.update(overrides)
    return Candidate(**base)


def _resp(*results, elapsed_ms: int = 50) -> dict:
    return {"results": list(results), "elapsed_ms": elapsed_ms}


def _r(article_id: str, title: str = "T", content: str = "") -> dict:
    return {
        "article_id": article_id,
        "title": title,
        "content_raw": content,
        "source_type": "sermon",
        "hybrid_score": 0.5,
    }


def test_count_keyword_hits_basic():
    n, hits = count_keyword_hits("我談恩典與稱義", ["恩典", "稱義", "禱告"])
    assert n == 2
    assert sorted(hits) == ["恩典", "稱義"]


def test_count_keyword_hits_no_match():
    n, hits = count_keyword_hits("無關文字", ["恩典"])
    assert n == 0
    assert hits == []


def test_title_fuzzy_match_high_similarity():
    ok, hint = title_fuzzy_match("論恩典的意義", ["論恩典"])
    assert ok is True
    assert hint == "論恩典"


def test_title_fuzzy_match_below_threshold():
    ok, _ = title_fuzzy_match("完全不同的標題", ["論恩典"])
    assert ok is False


def test_evaluate_question_pass_via_kw3():
    cand = _cand(expected_keywords=("a", "b", "c"))
    resp = _resp(_r("a1", title="X", content="a b c"), _r("a2", content="a"))
    result = evaluate_question(cand, resp, min_hits=3)
    assert result["auto_pass"] is True
    assert result["max_keyword_hits"] == 3
    assert result["rank_kw3"] == 1
    assert result["hit1_kw3"] == 1


def test_evaluate_question_pass_via_fuzzy():
    cand = _cand(
        expected_keywords=("nope",),
        expected_article_hints=("論恩典",),
    )
    resp = _resp(_r("a1", title="論恩典的意義"))
    result = evaluate_question(cand, resp, min_hits=3)
    assert result["auto_pass"] is True
    assert result["rank_fuzzy"] == 1


def test_evaluate_question_no_pass():
    cand = _cand(expected_keywords=("xyz",))
    resp = _resp(_r("a1", title="完全無關"))
    result = evaluate_question(cand, resp, min_hits=3)
    assert result["auto_pass"] is False
    assert result["rank_pass"] is None


def test_evaluate_question_top_k_truncates():
    cand = _cand(expected_keywords=("kw",))
    long_resp = _resp(*[_r(f"a{i}", content="kw kw kw") for i in range(20)])
    result = evaluate_question(cand, long_resp, top_k=5)
    assert len(result["top_k_results"]) == 5


def test_evaluate_question_rank_at_three():
    cand = _cand(expected_keywords=("kw1", "kw2", "kw3"))
    resp = _resp(
        _r("a1", content="nothing"),
        _r("a2", content="nothing"),
        _r("a3", content="kw1 kw2 kw3"),
    )
    result = evaluate_question(cand, resp, min_hits=3)
    assert result["rank_kw3"] == 3
    assert result["hit1_kw3"] == 0
    assert result["hit3_kw3"] == 1
    assert result["rr_kw3"] == 1.0 / 3.0


def test_aggregate_empty():
    out = aggregate([])
    assert out["total"] == 0
    assert out["pass_count"] == 0
    assert out["pass_rate_pct"] == 0.0


def test_aggregate_basic():
    qs = [
        {
            "auto_pass": True,
            "category": "theo",
            "source": "s1",
            "elapsed_ms": 100,
            "hit1_kw1": 1,
            "hit3_kw1": 1,
            "hit5_kw1": 1,
            "rr_kw1": 1.0,
            "hit1_kw3": 1,
            "hit3_kw3": 1,
            "hit5_kw3": 1,
            "rr_kw3": 1.0,
            "hit1_fuzzy": 0,
            "hit3_fuzzy": 0,
            "hit5_fuzzy": 0,
            "rr_fuzzy": 0.0,
            "hit1_pass": 1,
            "hit3_pass": 1,
            "hit5_pass": 1,
            "rr_pass": 1.0,
        },
        {
            "auto_pass": False,
            "category": "prac",
            "source": "s1",
            "elapsed_ms": 200,
            "hit1_kw1": 0,
            "hit3_kw1": 0,
            "hit5_kw1": 0,
            "rr_kw1": 0.0,
            "hit1_kw3": 0,
            "hit3_kw3": 0,
            "hit5_kw3": 0,
            "rr_kw3": 0.0,
            "hit1_fuzzy": 0,
            "hit3_fuzzy": 0,
            "hit5_fuzzy": 0,
            "rr_fuzzy": 0.0,
            "hit1_pass": 0,
            "hit3_pass": 0,
            "hit5_pass": 0,
            "rr_pass": 0.0,
        },
    ]
    out = aggregate(qs)
    assert out["total"] == 2
    assert out["pass_count"] == 1
    assert out["pass_rate_pct"] == 50.0
    assert out["elapsed_total_s"] == 0.30
    assert out["by_predicate"]["pass"]["mrr_at_5"] == 0.5
    assert out["by_category"] == {
        "theo": {"total": 1, "pass": 1},
        "prac": {"total": 1, "pass": 0},
    }
    assert out["by_source"]["s1"] == {"total": 2, "pass": 1}
