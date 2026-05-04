"""Human-readable formatters for `wenji stats` / `wenji segment`.

Pure-string output, no rich/ansi dependency — keeps wenji CLI consistent
with the existing typer-echo style (search.py, etc.).
"""

from __future__ import annotations

from typing import Any


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def format_stats_human(stats: dict[str, Any]) -> str:
    """Render :func:`wenji.observability.compute_stats` output as text."""
    lines: list[str] = []
    lines.append("Corpus")
    lines.append(f"  articles : {_fmt_int(stats['articles'])}")
    lines.append(f"  chunks   : {_fmt_int(stats['chunks'])}")
    lines.append("")

    idx = stats["indices"]
    lines.append("Indices")
    lines.append(f"  fts5_articles : {_fmt_int(idx['fts5_articles'])}")
    lines.append(f"  fts5_chunks   : {_fmt_int(idx['fts5_chunks'])}")
    lines.append(f"  vector_count  : {_fmt_int(idx['vector_count'])}")
    lines.append(f"  vector_dims   : {idx['vector_dims']}")
    lines.append("")

    lines.append("Source Types")
    src = stats["source_types"]
    if not src:
        lines.append("  (none)")
    else:
        for name in sorted(src, key=lambda k: (-src[k], k)):
            lines.append(f"  {name:<28} {_fmt_int(src[name])}")
    lines.append("")

    lines.append("Axes")
    axes = stats["axes"]
    if not axes:
        lines.append("  (none — axes.yaml not configured or no classifications)")
    else:
        for name in sorted(axes, key=lambda k: (-axes[k], k)):
            lines.append(f"  {name:<28} {_fmt_int(axes[name])}")
    lines.append("")

    lines.append("Last Ingest")
    lines.append(f"  {stats['last_ingest_at'] or '(never)'}")
    return "\n".join(lines)


def format_segment_human(trace: dict[str, Any]) -> str:
    """Render :func:`wenji.observability.compute_segment_trace` output as text."""
    lines: list[str] = []
    lines.append(f"Query: {trace['query']}")
    lines.append(f"Normalized: {trace['normalized_query']}")
    lines.append("")

    lines.append("Tokens (jieba.posseg)")
    if not trace["tokens"]:
        lines.append("  (empty)")
    else:
        for tok in trace["tokens"]:
            lines.append(f"  {tok['text']:<10} [{tok['pos']}]")
    lines.append("")

    lines.append("FTS form (Searcher MATCH expression)")
    lines.append(f"  {trace['fts_form'] or '(empty)'}")
    lines.append("")

    lines.append("Dict hits (jieba user_dict)")
    hits = trace["dict_hits"]
    if not hits:
        lines.append("  (none)")
    else:
        for h in hits:
            lines.append(f"  {h}")
    lines.append("")

    lines.append("Rewrite (LLM)")
    rw = trace["rewrite"]
    if rw is None:
        lines.append("  (disabled / unchanged / fallback)")
    else:
        lines.append(f"  rewritten   : {rw['rewritten_query']}")
        lines.append(f"  source      : {rw['source']}")
        lines.append(f"  latency_ms  : {rw['latency_ms']}")
    return "\n".join(lines)
