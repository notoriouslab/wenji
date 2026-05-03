"""``wenji search`` subcommand — thin-client fallback (server → in-process)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

DEFAULT_SERVER = "http://localhost:8000"
SERVER_PROBE_TIMEOUT = 0.3


def _try_server(server: str, query: str, axis: str | None, limit: int) -> dict | None:
    """Probe a running ``wenji serve`` instance; return JSON or None on failure."""
    import httpx

    params: dict[str, str | int] = {"q": query, "limit": limit}
    if axis is not None:
        params["axis"] = axis
    try:
        resp = httpx.get(
            f"{server.rstrip('/')}/api/search",
            params=params,
            timeout=SERVER_PROBE_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
        return None


def _build_rewriter(conn, *, force_enable: bool, force_disable: bool):
    """Conditionally build a QueryRewriter from env config and CLI flags.

    Returns (rewriter | None, error_msg | None). When force_enable but env
    incomplete, returns (None, error_msg) so the CLI can exit fast.
    """
    if force_disable:
        return None, None
    from wenji.config import load_llm_config_from_env

    cfg = load_llm_config_from_env()
    if force_enable and not cfg.enabled:
        return (
            None,
            "--enable-rewrite specified but missing env vars: "
            + ", ".join(cfg.missing_fields()),
        )
    if not (force_enable or cfg.enabled):
        return None, None
    from wenji.search.rewrite import QueryRewriter

    return (
        QueryRewriter(
            conn,
            api_url=cfg.base_url.rstrip("/") + "/chat/completions",
            api_key=cfg.api_key,
            model=cfg.model,
            timeout=1.5,
            ttl_days=cfg.rewrite_cache_ttl_days,
        ),
        None,
    )


def _in_process_search(
    db: Path,
    query: str,
    axis: str | None,
    limit: int,
    *,
    force_enable_rewrite: bool = False,
    force_no_rewrite: bool = False,
) -> dict:
    from wenji.core.db import connect
    from wenji.ingest.embed import Embedder
    from wenji.search import Searcher

    conn = connect(db)
    rewriter, err = _build_rewriter(
        conn, force_enable=force_enable_rewrite, force_disable=force_no_rewrite
    )
    if err is not None:
        conn.close()
        typer.echo(err, err=True)
        sys.exit(2)
    searcher = Searcher(conn, Embedder(), rewriter=rewriter)
    results = searcher.search(query, axis=axis, limit=limit)
    conn.close()
    return {"results": results, "query": query}


def command(
    query: str = typer.Argument(..., help="Search query."),
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB (used in fallback)."),
    server: str = typer.Option(
        DEFAULT_SERVER, help="Server URL to probe before in-process fallback."
    ),
    axis: str | None = typer.Option(None, help="Filter to a specific axis_id."),
    limit: int = typer.Option(10, help="Top-K results to return."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
    enable_rewrite: bool = typer.Option(
        False, "--enable-rewrite", help="Force LLM query rewrite on (in-process path)."
    ),
    no_rewrite: bool = typer.Option(
        False, "--no-rewrite", help="Force LLM query rewrite off (in-process path)."
    ),
) -> None:
    if enable_rewrite and no_rewrite:
        typer.echo("--enable-rewrite and --no-rewrite are mutually exclusive", err=True)
        sys.exit(2)

    payload = _try_server(server, query, axis, limit)
    if payload is None:
        typer.echo(
            f"server at {server} not responding; running in-process search "
            f"(this loads the embed model, ~5s cold start)",
            err=True,
        )
        payload = _in_process_search(
            db,
            query,
            axis,
            limit,
            force_enable_rewrite=enable_rewrite,
            force_no_rewrite=no_rewrite,
        )

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        results = payload.get("results", [])
        if not results:
            typer.echo("(no results)")
        for i, r in enumerate(results, start=1):
            title = r.get("title", "(no title)")
            score = r.get("hybrid_score") or r.get("rerank_score") or 0.0
            src = r.get("source_type", "")
            typer.echo(f"  [{i:>2}] [{score:.3f}] {title}  — {src}")
            snippet = r.get("content_snippet", "")
            if snippet:
                typer.echo(f"        {snippet[:160]}")
    sys.exit(0)
