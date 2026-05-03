"""Tests for wenji.eval.metrics (multi-path schema, v0.3.1)."""

from __future__ import annotations

from wenji.eval.jsonl import Candidate, GoldPath
from wenji.eval.metrics import (
    aggregate,
    count_keyword_hits,
    evaluate_question,
    score_gold_path,
    title_fuzzy_match,
)


def test_count_keyword_hits_case_insensitive():
    n, hits = count_keyword_hits("The Cat sat on a Mat", ["cat", "MAT"])
    assert n == 2
    assert hits == ["cat", "MAT"]


def test_count_keyword_hits_chinese():
    n, hits = count_keyword_hits("神創造了萬物", ["神", "創造", "缺"])
    assert n == 2
    assert hits == ["神", "創造"]


def test_count_keyword_hits_empty_keywords():
    assert count_keyword_hits("anything", []) == (0, [])


def test_score_gold_path_full():
    gp = GoldPath(path_tag="p", keywords=("神", "創造", "聖經"))
    assert score_gold_path("神創造了聖經中的世界", gp) == "full"


def test_score_gold_path_partial():
    gp = GoldPath(path_tag="p", keywords=("神", "創造", "聖經"))
    assert score_gold_path("神在創造中", gp) == "partial"


def test_score_gold_path_none():
    gp = GoldPath(path_tag="p", keywords=("神", "創造"))
    assert score_gold_path("hello world", gp) == "none"


def test_score_gold_path_empty_keywords_returns_none():
    gp = GoldPath(path_tag="p", keywords=())
    assert score_gold_path("anything", gp) == "none"


def test_score_gold_path_case_insensitive():
    gp = GoldPath(path_tag="p", keywords=("Hello", "World"))
    assert score_gold_path("hello WORLD here", gp) == "full"


def test_evaluate_question_pass_via_full_match():
    cand = Candidate(
        id=1,
        query="Q",
        gold_paths=(GoldPath(path_tag="p1", keywords=("神", "創造")),),
    )
    resp = {
        "results": [
            {"article_id": "a1", "title": "T", "rank": 1, "score": 0.9, "content_full": "神 創造"},
        ],
        "elapsed_ms": 100,
    }
    out = evaluate_question(cand, resp)
    assert out["pass"] is True
    assert out["passing_paths"] == ["p1"]
    assert out["article_results"][0]["gold_path_match"] == {"p1": "full"}


def test_evaluate_question_fail_via_partial_only():
    cand = Candidate(
        id=1,
        query="Q",
        gold_paths=(GoldPath(path_tag="p1", keywords=("神", "創造", "聖經")),),
    )
    resp = {
        "results": [
            {"article_id": "a1", "title": "T", "rank": 1, "score": 0.9, "content_full": "神在"},
        ],
    }
    out = evaluate_question(cand, resp)
    assert out["pass"] is False
    assert out["passing_paths"] == []
    assert out["partial_only"] is True


def test_evaluate_question_pass_via_any_path():
    cand = Candidate(
        id=1,
        query="Q",
        gold_paths=(
            GoldPath(path_tag="p1", keywords=("神", "創造", "缺失")),
            GoldPath(path_tag="p2", keywords=("禱告",)),
        ),
    )
    resp = {
        "results": [
            {"article_id": "a1", "title": "T", "rank": 1, "score": 0.9, "content_full": "禱告"},
        ],
    }
    out = evaluate_question(cand, resp)
    assert out["pass"] is True
    assert out["passing_paths"] == ["p2"]


def test_evaluate_question_chunk_union_rolls_up():
    cand = Candidate(
        id=1,
        query="Q",
        gold_paths=(GoldPath(path_tag="p1", keywords=("神", "創造")),),
    )
    resp = {
        "results": [
            {"article_id": "a1", "title": "T", "rank": 1, "score": 0.9, "content_full": "神"},
            {"article_id": "a1", "title": "T", "rank": 4, "score": 0.5, "content_full": "創造"},
        ],
    }
    out = evaluate_question(cand, resp)
    assert out["pass"] is True
    assert len(out["article_results"]) == 1


def test_evaluate_question_per_path_metrics():
    cand = Candidate(
        id=1,
        query="Q",
        gold_paths=(
            GoldPath(path_tag="p1", keywords=("神",)),
            GoldPath(path_tag="p2", keywords=("缺失",)),
        ),
    )
    resp = {
        "results": [
            {"article_id": "a1", "title": "T", "rank": 1, "score": 0.9, "content_full": "神"},
        ],
    }
    out = evaluate_question(cand, resp)
    assert out["rank_p1"] == 1
    assert out["hit1_p1"] == 1
    assert out["hit3_p1"] == 1
    assert out["hit5_p1"] == 1
    assert out["rr_p1"] == 1.0
    assert out["rank_p2"] is None
    assert out["rr_p2"] == 0.0


def test_aggregate_summary_seven_fields():
    per_q = [
        {
            "id": 1,
            "category": "cat-A",
            "source": "src-1",
            "pass": True,
            "passing_paths": ["p1"],
            "n_passing_paths": 1,
            "partial_only": False,
            "rr_at_5": 1.0,
            "elapsed_ms": 100,
        },
        {
            "id": 2,
            "category": "cat-A",
            "source": "src-1",
            "pass": False,
            "passing_paths": [],
            "n_passing_paths": 0,
            "partial_only": True,
            "rr_at_5": 0.0,
            "elapsed_ms": 200,
        },
    ]
    summary = aggregate(per_q)
    assert summary["pass_count"] == 1
    assert summary["pass_rate_pct"] == 50.0
    assert summary["partial_pass_count"] == 1
    assert summary["mean_passing_path_count"] == 1.0
    assert summary["mrr_at_5"] == 0.5
    assert summary["elapsed_total_sec"] == 0.3
    assert summary["by_category"]["cat-A"]["count"] == 2
    assert summary["by_category"]["cat-A"]["pass_count"] == 1
    assert summary["by_category"]["cat-A"]["pass_rate_pct"] == 50.0


def test_aggregate_empty_returns_zero_summary():
    summary = aggregate([])
    assert summary["total"] == 0
    assert summary["pass_count"] == 0
    assert summary["pass_rate_pct"] == 0.0
    assert summary["mrr_at_5"] == 0.0


def test_title_fuzzy_match_basic():
    matched, hint = title_fuzzy_match("論因信稱義", ["因信稱義", "其他"])
    assert matched
    assert hint == "因信稱義"


def test_title_fuzzy_match_no_match():
    matched, hint = title_fuzzy_match("論信仰", ["禱告"])
    assert not matched
    assert hint == ""
