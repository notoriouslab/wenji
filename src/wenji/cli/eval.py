"""``wenji eval`` subcommand — black-box eval against running wenji serve."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer


def command(
    candidates: Path = typer.Option(..., "--candidates", exists=True, help="JSONL eval set path."),
    port: int = typer.Option(8000, help="wenji serve port."),
    db: Path | None = typer.Option(None, help="SQLite DB path (required when --clear-cache)."),
    clear_cache: bool = typer.Option(
        False, "--clear-cache", help="Wipe query_rewrite_cache before queries fire."
    ),
    output: Path | None = typer.Option(
        None, "-o", "--output", help="Write full per-question JSON to this path."
    ),
    min_hits: int = typer.Option(3, help="auto_pass threshold."),
    top_k: int = typer.Option(5, help="candidate window per question."),
) -> None:
    from wenji.eval import run_baseline

    api_url = f"http://localhost:{port}/api/search"
    typer.echo(f"running eval against {api_url}", err=True)
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
        min_hits=min_hits,
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
