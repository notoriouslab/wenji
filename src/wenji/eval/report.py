"""Generate the wenji_r0 / wenji_r1 baseline markdown report.

Sections (per spec ``Baseline report``):

1. Run metadata (wenji_version, source_commit, date, corpus size).
2. Summary metrics (pass_rate_pct, by_category, elapsed_total_sec).
3. Sanity check results (objective overlap, subjective eyeball verdict).
4. Per-question table (id, query, category, pass, passing_paths, top-3 hits).
5. wenji_r0 vs reference baseline hits overlap distribution histogram.
6. examples/eval.jsonl classical poetry pre/post diff appendix.

For r1 reports, an extra section records the trim manifest.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path


def _section_metadata(run: dict, corpus_size: int | None) -> str:
    lines = [
        "## 1. Run Metadata",
        "",
        f"- **run_id**: `{run.get('run_id', 'n/a')}`",
        f"- **wenji_version**: `{run.get('wenji_version', 'unknown')}`",
        f"- **source_commit**: `{run.get('source_commit', 'n/a')}`",
        f"- **snapshot_taken_at**: {run.get('snapshot_taken_at', 'n/a')}",
        f"- **date**: {run.get('date', 'n/a')}",
        f"- **pipeline_mode**: `{run.get('pipeline_mode', 'n/a')}`",
        f"- **top_k_requested**: {run.get('top_k_requested', 'n/a')}",
    ]
    if corpus_size is not None:
        lines.append(f"- **corpus_size**: {corpus_size:,} articles")
    return "\n".join(lines)


def _section_summary(run: dict) -> str:
    s = run.get("summary", {})
    lines = [
        "## 2. Summary Metrics",
        "",
        f"- **pass_count**: {s.get('pass_count', 0)} / {s.get('total', 0)}",
        f"- **pass_rate_pct**: {s.get('pass_rate_pct', 0)}%",
        f"- **partial_pass_count**: {s.get('partial_pass_count', 0)}",
        f"- **mean_passing_path_count**: {s.get('mean_passing_path_count', 0)}",
        f"- **mrr_at_5**: {s.get('mrr_at_5', 0)}",
        f"- **elapsed_total_sec**: {s.get('elapsed_total_sec', 0)}s",
        "",
        "### By Category",
        "",
        "| category | count | pass_count | pass_rate_pct |",
        "|---|---:|---:|---:|",
    ]
    for cat, stats in sorted(s.get("by_category", {}).items()):
        lines.append(
            f"| {cat} | {stats.get('count', 0)} | "
            f"{stats.get('pass_count', 0)} | {stats.get('pass_rate_pct', 0)}% |"
        )
    return "\n".join(lines)


def _section_sanity(sanity_marker: dict | None) -> str:
    if sanity_marker is None:
        return (
            "## 3. Sanity Check Results\n\n"
            "_No promotion marker file supplied; sanity check not yet run._"
        )
    obj = sanity_marker.get("objective_gate", {})
    subj = sanity_marker.get("subjective_gate", {})
    return "\n".join(
        [
            "## 3. Sanity Check Results",
            "",
            "### Objective Gate (top-10 hits overlap, content_hash + title)",
            f"- **mean_overlap**: {obj.get('mean_overlap', 0)}",
            f"- **threshold**: {obj.get('threshold', 0)}",
            f"- **passed**: {obj.get('passed', False)}",
            "",
            "### Subjective Gate (human eyeball review, 8-question sample)",
            f"- **sampled_qids**: {subj.get('sampled_qids', [])}",
            f"- **flagged_qids**: {subj.get('flagged_qids', [])}",
            f"- **threshold**: ≤ {subj.get('threshold', 1)} flagged",
            f"- **passed**: {subj.get('passed', False)}",
            "",
            f"_Promoted at {sanity_marker.get('promoted_at', 'n/a')}._",
        ]
    )


def _section_per_question(run: dict, top_n_hits: int = 3) -> str:
    lines = [
        "## 4. Per-Question Verdict",
        "",
        f"| id | category | query | pass | passing_paths | top-{top_n_hits} hits |",
        "|---:|---|---|:---:|---|---|",
    ]
    for q in run.get("questions", []):
        hits = (q.get("article_results") or q.get("hits") or [])[:top_n_hits]
        hits_str = "; ".join(f"#{h.get('rank')}:{(h.get('title') or '')[:30]}" for h in hits)
        passing = ",".join(q.get("passing_paths", []) or [])
        passed = "✅" if q.get("pass") else "❌"
        lines.append(
            f"| {q.get('id')} | {q.get('category', '')[:20]} | "
            f"{(q.get('query') or '')[:40]} | {passed} | {passing[:40]} | {hits_str} |"
        )
    return "\n".join(lines)


def _section_overlap_histogram(per_question_overlaps: list[dict] | None) -> str:
    if not per_question_overlaps:
        return (
            "## 5. wenji_r0 vs Reference Baseline Overlap Distribution\n\n"
            "_No overlap data supplied (sanity check artifact not loaded)._"
        )
    bins = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
    counter: Counter[str] = Counter()
    for pq in per_question_overlaps:
        rate = float(pq.get("overlap_rate", 0))
        for lo, hi in zip(bins, bins[1:], strict=False):
            if lo <= rate < hi:
                counter[f"[{lo:.1f}, {hi:.1f})"] += 1
                break
    lines = [
        "## 5. wenji_r0 vs Reference Baseline Overlap Distribution",
        "",
        "| bin | count | bar |",
        "|---|---:|---|",
    ]
    max_count = max(counter.values()) if counter else 1
    for label in sorted(counter.keys()):
        count = counter[label]
        bar = "█" * int(40 * count / max_count) if max_count else ""
        lines.append(f"| {label} | {count} | {bar} |")
    return "\n".join(lines)


def _section_classical_appendix(diff_block: str | None) -> str:
    body = (
        diff_block
        if diff_block
        else (
            "_Pre/post diff for examples/eval.jsonl 10 classical poetry items "
            "(from D7 schema migration). To be filled when running the diff "
            "command in CI._"
        )
    )
    return "\n".join(
        [
            "## 6. examples/eval.jsonl Schema Migration Appendix",
            "",
            "### D7 multi-path wrap of 10 classical poetry questions",
            "",
            body,
        ]
    )


def _section_trim_manifest(trim_manifest: dict | None) -> str:
    if trim_manifest is None:
        return ""
    lines = [
        "## 7. Trim Manifest (r1 only)",
        "",
        f"- **removed_count**: {trim_manifest.get('removed_count', 0)}",
        f"- **corpus_size_before**: {trim_manifest.get('corpus_size_before', 0)}",
        f"- **corpus_size_after**: {trim_manifest.get('corpus_size_after', 0)}",
    ]
    by_st = trim_manifest.get("removed_by_source_type", {})
    if by_st:
        lines.append("\n### Removed by source_type")
        lines.append("")
        lines.append("| source_type | removed |")
        lines.append("|---|---:|")
        for st, n in sorted(by_st.items()):
            lines.append(f"| {st} | {n} |")
    return "\n".join(lines)


def render_baseline_report(
    run: dict,
    *,
    sanity_marker: dict | None = None,
    per_question_overlaps: list[dict] | None = None,
    classical_diff_block: str | None = None,
    trim_manifest: dict | None = None,
    corpus_size: int | None = None,
) -> str:
    """Render a markdown baseline report from run output + optional auxiliaries."""
    title = "wenji_r1 Baseline Report" if trim_manifest else "wenji_r0 Baseline Report"
    parts = [
        f"# {title}",
        "",
        _section_metadata(run, corpus_size),
        "",
        _section_summary(run),
        "",
        _section_sanity(sanity_marker),
        "",
        _section_per_question(run),
        "",
        _section_overlap_histogram(per_question_overlaps),
        "",
        _section_classical_appendix(classical_diff_block),
    ]
    if trim_manifest is not None:
        parts.append("")
        parts.append(_section_trim_manifest(trim_manifest))
    return "\n".join(parts) + "\n"


def write_baseline_report(out_path: str | Path, report_md: str) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(report_md, encoding="utf-8")
    return p
