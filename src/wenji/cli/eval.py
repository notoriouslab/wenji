"""``wenji eval`` subapp — multi-path eval against a running wenji serve.

Subcommands:
- ``legacy``: original single-path baseline runner (auto-pass keyword threshold).
- ``run``: multi-path eval against a JSONL file with ``gold_paths``.
- ``migrate-jsonl``: wrap legacy single-path JSONL into multi-path schema.
- ``run-benchmark``: 80-question v2 baseline runner (added by task 7.1).
- ``sanity-eyeball``: subjective gate for stage-1 baseline (added by task 8.4).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="eval",
    help="Multi-path eval and baseline tooling.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command("run")
def run_command(
    candidates: Path = typer.Option(..., "--candidates", exists=True, help="JSONL eval set path."),
    port: int = typer.Option(8000, help="wenji serve port."),
    db: Path | None = typer.Option(None, help="SQLite DB path (required when --clear-cache)."),
    clear_cache: bool = typer.Option(
        False, "--clear-cache", help="Wipe query_rewrite_cache before queries fire."
    ),
    output: Path | None = typer.Option(
        None, "-o", "--output", help="Write full per-question JSON to this path."
    ),
    top_k: int = typer.Option(20, help="candidate window per question (multi-path default 20)."),
) -> None:
    """Run a multi-path eval against a running ``wenji serve``."""
    from wenji.eval import run_baseline

    api_url = f"http://localhost:{port}/api/search"
    typer.echo(f"running multi-path eval against {api_url}", err=True)
    if clear_cache:
        if db is None:
            typer.echo("--clear-cache requires --db <path>", err=True)
            sys.exit(2)
        typer.echo(f"clearing query_rewrite_cache in {db}", err=True)

    result = run_baseline(
        candidates,
        api_url=api_url,
        db_path=db,
        clear_cache=clear_cache,
        top_k=top_k,
    )

    summary = result["summary"]
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"wrote full results → {output}", err=True)

    sys.exit(0 if summary.get("pass_count", 0) > 0 else 1)


@app.command("run-benchmark")
def run_benchmark_command(
    snapshot: Path = typer.Option(
        Path("tests/benchmark_80_v2_snapshot.json"),
        "--snapshot",
        help="logos benchmark v2 snapshot file (frozen via D4 git commit hash).",
    ),
    db: Path = typer.Option(..., "--db", help="wenji.db path (must be ingested)."),
    port: int = typer.Option(8000, help="wenji serve port."),
    top_k: int = typer.Option(20, help="top-K candidate window per question."),
    out: Path = typer.Option(
        Path("tests/wenji_r0_run.json"),
        "-o",
        "--out",
        help="Output file for v2 schema run report.",
    ),
    pipeline_mode: str = typer.Option(
        "rag_full", help="Tag for pipeline mode in run output (e.g. rag_full / hybrid_only)."
    ),
    enable_rewrite: bool = typer.Option(
        False,
        "--enable-rewrite",
        help="Tag this run as rewrite-on (server must be started with rewrite enabled).",
    ),
    no_rewrite: bool = typer.Option(
        False,
        "--no-rewrite",
        help="Tag this run as rewrite-off (overrides env-derived default).",
    ),
) -> None:
    """Run the 80-question v2 baseline against a running wenji serve.

    Produces ``wenji_r0_<date>.json`` (v2 schema run output) plus a
    ``<out>.summary.json`` digest. The output conforms to the logos benchmark
    v2 schema: each question gets per-hit ``gold_path_match`` (none/partial/full)
    and a question-level ``pass`` plus ``passing_paths``.

    The ``--enable-rewrite`` / ``--no-rewrite`` flags do NOT control the
    running server's rewrite state — they only tag the run output's
    ``rewrite_enabled`` field for A/B comparison. Start the server with the
    matching flag (e.g. ``wenji serve --enable-rewrite``) before running.
    """
    if enable_rewrite and no_rewrite:
        typer.echo("--enable-rewrite and --no-rewrite are mutually exclusive", err=True)
        sys.exit(2)
    rewrite_enabled = enable_rewrite or (
        not no_rewrite
        and __import__("wenji.config", fromlist=["load_llm_config_from_env"])
        .load_llm_config_from_env()
        .enabled
    )
    import datetime
    import time

    from wenji.eval import run_baseline as _run_baseline_internal
    from wenji.eval.loader_logos_v2 import load_logos_v2_snapshot

    cands, meta = load_logos_v2_snapshot(snapshot)
    typer.echo(
        f"loaded {len(cands)} candidates from snapshot (commit={meta.logos_source_commit[:8]})",
        err=True,
    )

    api_url = f"http://localhost:{port}/api/search"
    typer.echo(f"running benchmark against {api_url}", err=True)

    t0 = time.time()
    result = _run_baseline_internal(
        candidates_path="ignored",
        candidates=cands,
        api_url=api_url,
        db_path=db,
        top_k=top_k,
    )
    elapsed = time.time() - t0

    # Wrap in logos-v2-compatible run output schema.
    suffix = "_rewrite_on" if rewrite_enabled else "_rewrite_off"
    run_id = f"wenji_r0_{datetime.date.today().isoformat()}{suffix}"
    wenji_version = _detect_wenji_version()
    run_output = {
        "run_id": run_id,
        "schema_version": "v2",
        "wenji_version": wenji_version,
        "logos_source_commit": meta.logos_source_commit,
        "snapshot_taken_at": meta.snapshot_taken_at,
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pipeline_mode": pipeline_mode,
        "top_k_requested": top_k,
        "rewrite_enabled": rewrite_enabled,
        "questions": result["results"],
        "summary": {
            **result["summary"],
            "elapsed_total_sec": round(elapsed, 2),
        },
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(run_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    digest_path = out.with_suffix(out.suffix + ".summary.json")
    digest_path.write_text(
        json.dumps(run_output["summary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(f"wrote run output → {out}", err=True)
    typer.echo(f"wrote summary digest → {digest_path}", err=True)
    typer.echo(json.dumps(run_output["summary"], ensure_ascii=False, indent=2))
    sys.exit(0 if run_output["summary"].get("pass_count", 0) > 0 else 1)


def _detect_wenji_version() -> str:
    try:
        from importlib.metadata import version

        return version("wenji")
    except Exception:
        return "unknown"


@app.command("sanity-eyeball")
def sanity_eyeball_command(
    wenji_r0: Path = typer.Option(..., "--wenji-r0", exists=True, help="wenji_r0 run output."),
    logos_r13: Path = typer.Option(..., "--logos-r13", exists=True, help="logos_r13 run output."),
    n: int = typer.Option(8, help="Number of questions to sample for eyeball."),
    seed: int | None = typer.Option(None, help="Random seed for reproducible sampling."),
    out: Path = typer.Option(
        Path("tests/wenji_r0_baseline.json"),
        "--out",
        help="Marker file to write on dual-gate pass.",
    ),
) -> None:
    """Run the dual-gate sanity check (objective overlap + subjective eyeball).

    Step 1: compute per-question top-10 hits overlap; abort with diagnostic if
    mean overlap < 0.70.
    Step 2: sample N questions and present wenji top-5 vs logos top-5 side by
    side; user enters comma-separated qids that look unreasonable. > 1 flag
    means subjective gate fails.
    Both pass → write promotion marker.
    """
    from wenji.eval.sanity_check import (
        compute_objective_overlap,
        emit_objective_diagnostic,
        evaluate_subjective_gate,
        sample_eyeball_questions,
        write_promotion_marker,
    )

    wenji_data = json.loads(wenji_r0.read_text(encoding="utf-8"))
    logos_data = json.loads(logos_r13.read_text(encoding="utf-8"))

    typer.echo("=== objective gate ===", err=True)
    obj = compute_objective_overlap(wenji_data, logos_data)
    typer.echo(emit_objective_diagnostic(obj))
    if not obj.passed:
        typer.echo(
            f"FAIL: mean_overlap={obj.mean_overlap:.4f} < threshold={obj.threshold:.2f}; "
            "investigate root cause (do NOT lower threshold).",
            err=True,
        )
        sys.exit(1)
    typer.echo(f"PASS: mean_overlap={obj.mean_overlap:.4f} ≥ {obj.threshold:.2f}", err=True)

    typer.echo(f"\n=== subjective gate ({n} questions) ===", err=True)
    samples = sample_eyeball_questions(wenji_data, logos_data, n=n, seed=seed)
    for s in samples:
        typer.echo(f"\n--- Q{s.qid}: {s.query[:80]} ---")
        typer.echo("wenji top-5:")
        for r in s.wenji_top5:
            typer.echo(f"  rank={r.get('rank')} title={(r.get('title') or '')[:80]}")
        typer.echo("logos top-5:")
        for r in s.logos_top5:
            typer.echo(f"  rank={r.get('rank')} title={(r.get('title') or '')[:80]}")
    typer.echo("\nEnter comma-separated qids that look unreasonable (or empty if all OK):")
    raw = typer.prompt("flagged qids", default="", show_default=False)
    flagged = [int(x.strip()) for x in raw.split(",") if x.strip()]
    sampled_ids = [s.qid for s in samples]
    subj = evaluate_subjective_gate(flagged, sampled_ids)
    if not subj.passed:
        typer.echo(
            f"FAIL: {len(flagged)} flagged > threshold={subj.threshold}; "
            "fix retrieval before promoting baseline.",
            err=True,
        )
        sys.exit(1)
    typer.echo(f"PASS: {len(flagged)} flagged ≤ threshold={subj.threshold}", err=True)

    write_promotion_marker(out, objective=obj, subjective=subj, wenji_r0_path=str(wenji_r0))
    typer.echo(f"wrote promotion marker → {out}", err=True)


@app.command("migrate-jsonl")
def migrate_jsonl_command(
    src: Path = typer.Argument(..., exists=True, help="Legacy single-path JSONL file."),
    dst: Path = typer.Argument(..., help="Output multi-path JSONL file."),
) -> None:
    """Wrap legacy single-path JSONL entries as single-element ``gold_paths``.

    Entries already in multi-path schema pass through unchanged. The legacy
    fields ``expected_keywords`` / ``expected_article_hints`` are removed and
    wrapped into a single gold_path with ``path_tag="default"``.
    """
    from wenji.eval.jsonl import wrap_legacy_candidate

    out_lines: list[str] = []
    n_wrapped = 0
    n_passthrough = 0
    n_skipped = 0
    with src.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                out_lines.append(raw.rstrip("\n"))
                n_skipped += 1
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                typer.echo(f"line {lineno}: invalid JSON: {exc}", err=True)
                sys.exit(2)
            had_gold = "gold_paths" in obj
            new = wrap_legacy_candidate(obj)
            if had_gold:
                n_passthrough += 1
            else:
                n_wrapped += 1
            out_lines.append(json.dumps(new, ensure_ascii=False))

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    typer.echo(
        f"migrated {n_wrapped} legacy entries, {n_passthrough} passthrough, "
        f"{n_skipped} blank/comment → {dst}",
        err=True,
    )


def command(*args, **kwargs) -> None:
    """Backward-compat shim for callers expecting a single ``command`` symbol."""
    return run_command(*args, **kwargs)
