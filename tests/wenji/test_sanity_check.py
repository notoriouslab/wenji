"""Tests for stage-1 baseline sanity check (dual gate) + D10 validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wenji.cli import app as wenji_app
from wenji.eval.sanity_check import (
    MAX_BASELINE_FILE_BYTES,
    MAX_STRING_BYTES,
    PerQuestionOverlap,
    SubjectiveSample,
    compute_objective_overlap,
    emit_objective_diagnostic,
    evaluate_subjective_gate,
    load_baseline_output,
    sample_eyeball_questions,
    strip_control_chars,
    write_promotion_marker,
)

runner = CliRunner()


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
    baseline = {"questions": [_q(1, ["h1", "h2", "h3"]), _q(2, ["h4", "h5"])]}
    res = compute_objective_overlap(wenji, baseline)
    assert res.mean_overlap == 1.0
    assert res.passed is True


def test_objective_zero_overlap_fails():
    wenji = {"questions": [_q(1, ["h1", "h2"])]}
    baseline = {"questions": [_q(1, ["x1", "x2"])]}
    res = compute_objective_overlap(wenji, baseline)
    assert res.mean_overlap == 0.0
    assert res.passed is False


def test_objective_partial_overlap_70_pct_passes():
    wenji_hashes = [f"h{i}" for i in range(10)]
    baseline_hashes = [f"h{i}" for i in range(7)] + ["x1", "x2", "x3"]
    wenji = {"questions": [_q(1, wenji_hashes)]}
    baseline = {"questions": [_q(1, baseline_hashes)]}
    res = compute_objective_overlap(wenji, baseline)
    assert abs(res.mean_overlap - 0.70) < 1e-6
    assert res.passed is True


def test_objective_per_question_diagnostic_sorted_ascending():
    wenji = {
        "questions": [
            _q(1, ["a", "b"]),
            _q(2, ["c", "d"]),
            _q(3, ["e", "f"]),
        ]
    }
    baseline = {
        "questions": [
            _q(1, ["a", "x"]),
            _q(2, ["x", "y"]),
            _q(3, ["e", "f"]),
        ]
    }
    res = compute_objective_overlap(wenji, baseline)
    rates = [pq.overlap_rate for pq in res.per_question]
    assert rates == sorted(rates)
    assert res.per_question[0].qid == 2
    assert res.per_question[-1].qid == 3


def test_objective_question_missing_in_baseline_skipped():
    wenji = {"questions": [_q(1, ["h1"]), _q(2, ["h2"])]}
    baseline = {"questions": [_q(1, ["h1"])]}
    res = compute_objective_overlap(wenji, baseline)
    assert len(res.per_question) == 1
    assert res.per_question[0].qid == 1


def test_per_question_overlap_uses_baseline_count_field():
    """PerQuestionOverlap dataclass MUST expose baseline_count (not logos_count)."""
    wenji = {"questions": [_q(1, ["a", "b"])]}
    baseline = {"questions": [_q(1, ["a", "x"])]}
    res = compute_objective_overlap(wenji, baseline)
    pq = res.per_question[0]
    assert isinstance(pq, PerQuestionOverlap)
    assert pq.baseline_count == 2
    assert pq.wenji_count == 2
    assert not hasattr(pq, "logos_count")


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
    baseline = {"questions": [_q(i, [f"h{i}"]) for i in range(1, 21)]}
    s1 = sample_eyeball_questions(wenji, baseline, n=8, seed=42)
    s2 = sample_eyeball_questions(wenji, baseline, n=8, seed=42)
    assert [s.qid for s in s1] == [s.qid for s in s2]
    assert len(s1) == 8


def test_subjective_sample_uses_baseline_top5_field():
    """SubjectiveSample dataclass MUST expose baseline_top5 (not logos_top5)."""
    wenji = {"questions": [_q(1, ["a"])]}
    baseline = {"questions": [_q(1, ["b"])]}
    samples = sample_eyeball_questions(wenji, baseline, n=1, seed=0)
    assert len(samples) == 1
    s = samples[0]
    assert isinstance(s, SubjectiveSample)
    assert isinstance(s.baseline_top5, list)
    assert isinstance(s.wenji_top5, list)
    assert not hasattr(s, "logos_top5")


def test_promotion_marker_only_written_when_both_pass(tmp_path):
    wenji = {"questions": [_q(1, ["h1"])]}
    baseline = {"questions": [_q(1, ["h1"])]}
    obj = compute_objective_overlap(wenji, baseline)
    subj = evaluate_subjective_gate([], [1])
    marker = tmp_path / "promo.json"
    write_promotion_marker(marker, objective=obj, subjective=subj, wenji_r0_path="x.json")
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["objective_gate"]["passed"] is True
    assert data["subjective_gate"]["passed"] is True


def test_promotion_marker_refuses_when_objective_fails(tmp_path):
    wenji = {"questions": [_q(1, ["h1"])]}
    baseline = {"questions": [_q(1, ["x1"])]}
    obj = compute_objective_overlap(wenji, baseline)
    subj = evaluate_subjective_gate([], [1])
    with pytest.raises(RuntimeError, match="gates did not both pass"):
        write_promotion_marker(
            tmp_path / "promo.json", objective=obj, subjective=subj, wenji_r0_path="x"
        )


def test_diagnostic_emit_format():
    wenji = {"questions": [_q(1, ["a", "b"])]}
    baseline = {"questions": [_q(1, ["a", "x"])]}
    res = compute_objective_overlap(wenji, baseline)
    text = emit_objective_diagnostic(res)
    assert "Objective gate diagnostic" in text
    assert "qid" in text and "overlap" in text
    assert "baseline" in text
    assert "logos" not in text


# ---- D10: load_baseline_output validator ----


def _write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_baseline_output_happy_path(tmp_path):
    p = _write_json(tmp_path / "baseline.json", {"questions": [_q(1, ["h1"])]})
    data = load_baseline_output(p)
    assert data["questions"][0]["id"] == 1


def test_load_baseline_output_rejects_missing_path(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_baseline_output(tmp_path / "missing.json")


def test_load_baseline_output_rejects_directory(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        load_baseline_output(d)


def test_load_baseline_output_rejects_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_non_object_top_level(tmp_path):
    p = _write_json(tmp_path / "list.json", [1, 2, 3])
    with pytest.raises(ValueError, match="top-level value must be a JSON object"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_missing_questions(tmp_path):
    p = _write_json(tmp_path / "no_q.json", {"summary": {}})
    with pytest.raises(ValueError, match="missing required 'questions' array"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_question_missing_id(tmp_path):
    p = _write_json(tmp_path / "no_id.json", {"questions": [{"query": "x"}]})
    with pytest.raises(ValueError, match=r"questions\[0\] is missing 'id'"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_question_not_object(tmp_path):
    p = _write_json(tmp_path / "bad_q.json", {"questions": [42]})
    with pytest.raises(ValueError, match=r"questions\[0\] is not an object"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_non_array_hits(tmp_path):
    p = _write_json(
        tmp_path / "bad_hits.json",
        {"questions": [{"id": 1, "article_results": "not-a-list"}]},
    )
    with pytest.raises(ValueError, match="must be an array if present"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_oversized_file(tmp_path, monkeypatch):
    """File size cap is checked PRE-parse; we shrink the cap in this test to
    avoid materialising a 10 MB fixture."""
    monkeypatch.setattr("wenji.eval.sanity_check.MAX_BASELINE_FILE_BYTES", 100)
    p = _write_json(tmp_path / "big.json", {"questions": [_q(i, ["h"]) for i in range(50)]})
    assert p.stat().st_size > 100
    with pytest.raises(ValueError, match="exceeds limit"):
        load_baseline_output(p)


def test_load_baseline_output_rejects_oversized_string(tmp_path, monkeypatch):
    monkeypatch.setattr("wenji.eval.sanity_check.MAX_STRING_BYTES", 16)
    p = _write_json(
        tmp_path / "long.json",
        {"questions": [{"id": 1, "query": "x" * 100}]},
    )
    with pytest.raises(ValueError, match="exceeds .* bytes"):
        load_baseline_output(p)


def test_max_baseline_file_bytes_is_10_mb():
    assert MAX_BASELINE_FILE_BYTES == 10 * 1024 * 1024


def test_max_string_bytes_is_64_kb():
    assert MAX_STRING_BYTES == 64 * 1024


# ---- D10: control-character strip ----


def test_strip_control_chars_removes_ansi_escape():
    raw = "\x1b[2J<fake success>"
    assert strip_control_chars(raw) == "[2J<fake success>"


def test_strip_control_chars_removes_low_bytes():
    raw = "a\x00b\x07c\x1ed"
    assert strip_control_chars(raw) == "abcd"


def test_strip_control_chars_preserves_tab_and_newline_strips_cr():
    """Spec regex `[\\x00-\\x08\\x0b-\\x1f\\x7f]` excludes 0x09 (tab) and 0x0a (LF)
    but includes 0x0d (CR), so CR is stripped (CRLF injection defence)."""
    assert strip_control_chars("a\tb\nc") == "a\tb\nc"
    assert strip_control_chars("a\rb") == "ab"


def test_strip_control_chars_removes_del():
    assert strip_control_chars("a\x7fb") == "ab"


def test_strip_control_chars_preserves_unicode():
    raw = "中文 內容 — 測試 字符"
    assert strip_control_chars(raw) == raw


# ---- 4.6: CLI legacy --logos-r13 hard-fail ----


def test_cli_legacy_logos_r13_flag_rejected_with_helpful_message(tmp_path):
    """Spec scenario "Legacy flag rejected": exit non-zero AND mention --baseline-output."""
    wenji_path = _write_json(tmp_path / "wenji.json", {"questions": [_q(1, ["h"])]})
    legacy_path = _write_json(tmp_path / "legacy.json", {"questions": [_q(1, ["h"])]})
    result = runner.invoke(
        wenji_app,
        [
            "eval",
            "sanity-eyeball",
            "--wenji-r0",
            str(wenji_path),
            "--logos-r13",
            str(legacy_path),
        ],
    )
    assert result.exit_code != 0
    assert "--baseline-output" in result.output
    assert "--logos-r13" in result.output


def test_cli_baseline_output_required_when_neither_flag_given(tmp_path):
    wenji_path = _write_json(tmp_path / "wenji.json", {"questions": [_q(1, ["h"])]})
    result = runner.invoke(
        wenji_app,
        ["eval", "sanity-eyeball", "--wenji-r0", str(wenji_path)],
    )
    assert result.exit_code != 0
    assert "--baseline-output" in result.output
