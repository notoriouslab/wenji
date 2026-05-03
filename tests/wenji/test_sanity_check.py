"""Tests for stage-1 baseline sanity check (dual gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wenji.eval.sanity_check import (
    OBJECTIVE_THRESHOLD,
    SUBJECTIVE_MAX_FLAGS,
    compute_objective_overlap,
    emit_objective_diagnostic,
    evaluate_subjective_gate,
    sample_eyeball_questions,
    write_promotion_marker,
)


def _q(qid: int, hashes: list[str]) -> dict:
    """Helper: build a v2 question entry with hits identified by content_hash."""
    return {
        "id": qid,
        "query": f"Q{qid}",
        "article_results": [
            {"article_id": f"a{i}", "content_hash": h, "title": f"T{i}", "rank": i}
            for i, h in enumerate(hashes, start=1)
        ],
    }


def test_objective_perfect_overlap_passes():
    wenji = {"questions": [_q(1, ["h1", "h2", "h3"]), _q(2, ["h4", "h5"])]}
    logos = {"questions": [_q(1, ["h1", "h2", "h3"]), _q(2, ["h4", "h5"])]}
    res = compute_objective_overlap(wenji, logos)
    assert res.mean_overlap == 1.0
    assert res.passed is True


def test_objective_zero_overlap_fails():
    wenji = {"questions": [_q(1, ["h1", "h2"])]}
    logos = {"questions": [_q(1, ["x1", "x2"])]}
    res = compute_objective_overlap(wenji, logos)
    assert res.mean_overlap == 0.0
    assert res.passed is False


def test_objective_partial_overlap_70_pct_passes():
    # 7 / 10 wenji hits intersect with logos hits → 0.70 exactly.
    wenji_hashes = [f"h{i}" for i in range(10)]
    logos_hashes = [f"h{i}" for i in range(7)] + ["x1", "x2", "x3"]
    wenji = {"questions": [_q(1, wenji_hashes)]}
    logos = {"questions": [_q(1, logos_hashes)]}
    res = compute_objective_overlap(wenji, logos)
    assert abs(res.mean_overlap - 0.70) < 1e-6
    assert res.passed is True


def test_objective_per_question_diagnostic_sorted_ascending():
    wenji = {
        "questions": [
            _q(1, ["a", "b"]),  # overlaps with logos by 1/2 = 0.5
            _q(2, ["c", "d"]),  # overlaps by 0/2 = 0.0
            _q(3, ["e", "f"]),  # overlaps by 2/2 = 1.0
        ]
    }
    logos = {
        "questions": [
            _q(1, ["a", "x"]),
            _q(2, ["x", "y"]),
            _q(3, ["e", "f"]),
        ]
    }
    res = compute_objective_overlap(wenji, logos)
    # ascending order
    rates = [pq.overlap_rate for pq in res.per_question]
    assert rates == sorted(rates)
    assert res.per_question[0].qid == 2  # 0.0 first
    assert res.per_question[-1].qid == 3  # 1.0 last


def test_objective_question_missing_in_logos_skipped():
    wenji = {"questions": [_q(1, ["h1"]), _q(2, ["h2"])]}
    logos = {"questions": [_q(1, ["h1"])]}  # q2 missing
    res = compute_objective_overlap(wenji, logos)
    assert len(res.per_question) == 1
    assert res.per_question[0].qid == 1


def test_subjective_gate_passes_with_zero_flags():
    res = evaluate_subjective_gate([], [1, 2, 3])
    assert res.passed is True


def test_subjective_gate_passes_with_one_flag():
    res = evaluate_subjective_gate([2], [1, 2, 3])
    assert res.passed is True


def test_subjective_gate_fails_with_two_flags():
    res = evaluate_subjective_gate([2, 3], [1, 2, 3])
    assert res.passed is False


def test_sample_eyeball_questions_deterministic_with_seed():
    wenji = {"questions": [_q(i, [f"h{i}"]) for i in range(1, 21)]}
    logos = {"questions": [_q(i, [f"h{i}"]) for i in range(1, 21)]}
    s1 = sample_eyeball_questions(wenji, logos, n=8, seed=42)
    s2 = sample_eyeball_questions(wenji, logos, n=8, seed=42)
    assert [s.qid for s in s1] == [s.qid for s in s2]
    assert len(s1) == 8


def test_promotion_marker_only_written_when_both_pass(tmp_path):
    wenji = {"questions": [_q(1, ["h1"])]}
    logos = {"questions": [_q(1, ["h1"])]}
    obj = compute_objective_overlap(wenji, logos)
    subj = evaluate_subjective_gate([], [1])
    marker = tmp_path / "promo.json"
    write_promotion_marker(marker, objective=obj, subjective=subj, wenji_r0_path="x.json")
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["objective_gate"]["passed"] is True
    assert data["subjective_gate"]["passed"] is True


def test_promotion_marker_refuses_when_objective_fails(tmp_path):
    wenji = {"questions": [_q(1, ["h1"])]}
    logos = {"questions": [_q(1, ["x1"])]}
    obj = compute_objective_overlap(wenji, logos)
    subj = evaluate_subjective_gate([], [1])
    with pytest.raises(RuntimeError, match="gates did not both pass"):
        write_promotion_marker(
            tmp_path / "promo.json", objective=obj, subjective=subj, wenji_r0_path="x"
        )


def test_diagnostic_emit_format():
    wenji = {"questions": [_q(1, ["a", "b"])]}
    logos = {"questions": [_q(1, ["a", "x"])]}
    res = compute_objective_overlap(wenji, logos)
    text = emit_objective_diagnostic(res)
    assert "Objective gate diagnostic" in text
    assert "qid" in text and "overlap" in text
