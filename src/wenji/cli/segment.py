"""``wenji segment <query>`` — show how a query passes through wenji's pipeline."""

from __future__ import annotations

import json
import sys

import typer

from wenji.cli._format import format_segment_human


def command(
    query: str = typer.Argument(..., help="Query string to trace."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """Print jieba tokens, FTS form, and dict hits for a query."""
    if not query.strip():
        typer.echo("error: query must be non-empty", err=True)
        sys.exit(2)

    from wenji.observability import compute_segment_trace

    trace = compute_segment_trace(query)

    if json_output:
        typer.echo(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        typer.echo(format_segment_human(trace))
    sys.exit(0)
