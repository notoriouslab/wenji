"""Tests for the baseline report generator."""

from __future__ import annotations

from wenji.eval.report import render_baseline_report, write_baseline_report


def _sample_run() -> dict:
    return {
        "run_id": "wenji_r0_2026-05-03",
        "schema_version": "v2",
        "wenji_version": "0.3.1",
        "source_commit": "413642afa95ccc824d72a41c427b94f2cbc2e10c",
        "snapshot_taken_at": "2026-05-03",
        "date": "2026-05-03T10:00:00+00:00",
        "pipeline_mode": "rag_full",
        "top_k_requested": 20,
        "questions": [
            {
                "id": 1,
                "category": "神的存在與本質",
                "query": "到底有沒有神",
                "pass": True,
                "passing_paths": ["classical_theistic_arguments"],
                "article_results": [
                    {"rank": 1, "title": "為真道爭辯"},
                    {"rank": 2, "title": "宇宙論論證"},
                ],
            }
        ],
        "summary": {
            "total": 1,
            "pass_count": 1,
            "pass_rate_pct": 100.0,
            "partial_pass_count": 0,
            "mean_passing_path_count": 1.0,
            "mrr_at_5": 1.0,
            "elapsed_total_sec": 12.34,
            "by_category": {
                "神的存在與本質": {
                    "count": 1,
                    "pass_count": 1,
                    "pass_rate_pct": 100.0,
                }
            },
        },
    }


def test_report_has_six_sections():
    md = render_baseline_report(_sample_run(), corpus_size=13955)
    assert "# wenji_r0 Baseline Report" in md
    assert "## 1. Run Metadata" in md
    assert "## 2. Summary Metrics" in md
    assert "## 3. Sanity Check Results" in md
    assert "## 4. Per-Question Verdict" in md
    assert "## 5. wenji_r0 vs Reference Baseline Overlap Distribution" in md
    assert "## 6. examples/eval.jsonl Schema Migration Appendix" in md


def test_report_metadata_includes_corpus_size():
    md = render_baseline_report(_sample_run(), corpus_size=13955)
    assert "13,955" in md


def test_report_summary_by_category_table():
    md = render_baseline_report(_sample_run())
    assert "神的存在與本質" in md
    assert "100.0%" in md


def test_report_per_question_table_has_pass_emoji():
    md = render_baseline_report(_sample_run())
    assert "✅" in md
    assert "為真道爭辯" in md


def test_report_with_sanity_marker():
    marker = {
        "promoted_at": "2026-05-03T10:30:00+00:00",
        "objective_gate": {
            "mean_overlap": 0.78,
            "threshold": 0.70,
            "passed": True,
        },
        "subjective_gate": {
            "sampled_qids": [1, 5, 10, 20, 30, 40, 60, 75],
            "flagged_qids": [],
            "threshold": 1,
            "passed": True,
        },
    }
    md = render_baseline_report(_sample_run(), sanity_marker=marker)
    assert "0.78" in md
    assert "threshold" in md.lower()
    assert "Promoted at 2026-05-03" in md


def test_report_with_overlap_histogram():
    overlaps = [{"qid": i, "overlap_rate": 0.05 + i * 0.05} for i in range(20)]
    md = render_baseline_report(_sample_run(), per_question_overlaps=overlaps)
    assert "[0.0, 0.1)" in md
    assert "█" in md  # has bars


def test_report_r1_has_trim_manifest_section():
    trim = {
        "removed_count": 200,
        "corpus_size_before": 13955,
        "corpus_size_after": 13755,
        "removed_by_source_type": {"sermon": 100, "youtube": 100},
    }
    md = render_baseline_report(_sample_run(), trim_manifest=trim)
    assert "# wenji_r1 Baseline Report" in md
    assert "## 7. Trim Manifest (r1 only)" in md
    assert "200" in md
    assert "13,955" in md or "13955" in md


def test_write_baseline_report(tmp_path):
    out = tmp_path / "docs" / "wenji_r0_baseline.md"
    md = render_baseline_report(_sample_run())
    p = write_baseline_report(out, md)
    assert p.exists()
    assert p.read_text(encoding="utf-8") == md
