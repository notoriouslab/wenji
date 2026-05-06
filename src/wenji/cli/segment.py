"""``wenji segment <query>`` — show how a query passes through wenji's pipeline."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from wenji.cli._format import format_segment_human


def command(
    query: str = typer.Argument(..., help="Query string to trace."),
    db: Path = typer.Option(
        Path(os.environ.get("WENJI_DB_PATH", "data/wenji.db")),
        help="SQLite DB path (defaults to $WENJI_DB_PATH or data/wenji.db).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
    enable_rewrite: bool = typer.Option(
        False, "--enable-rewrite", help="Force LLM query rewrite on."
    ),
    no_rewrite: bool = typer.Option(False, "--no-rewrite", help="Force LLM query rewrite off."),
) -> None:
    """Print jieba tokens, FTS form, dict hits, and (optional) LLM rewrite for a query."""
    if enable_rewrite and no_rewrite:
        typer.echo("--enable-rewrite and --no-rewrite are mutually exclusive", err=True)
        sys.exit(2)

    if not query.strip():
        typer.echo("error: query must be non-empty", err=True)
        sys.exit(2)

    from wenji.config import load_llm_config_from_env
    from wenji.core.db import connect
    from wenji.observability import compute_segment_trace
    from wenji.search.rewrite import QueryRewriter

    llm_cfg = load_llm_config_from_env()
    rewrite_on = enable_rewrite or (llm_cfg.enabled and not no_rewrite)
    rewriter: QueryRewriter | None = None
    conn = None
    try:
        if rewrite_on and llm_cfg.enabled:
            if not db.exists():
                typer.echo(
                    f"warning: DB not found at {db}; rewrite cache disabled",
                    err=True,
                )
            else:
                conn = connect(db)
                rewriter = QueryRewriter(
                    conn,
                    api_url=llm_cfg.base_url.rstrip("/") + "/chat/completions",
                    api_key=llm_cfg.api_key,
                    model=llm_cfg.model,
                    timeout=1.5,
                    ttl_days=llm_cfg.rewrite_cache_ttl_days,
                )
        trace = compute_segment_trace(query, rewriter=rewriter)
    finally:
        if conn is not None:
            conn.close()

    if json_output:
        typer.echo(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        typer.echo(format_segment_human(trace))
    sys.exit(0)
