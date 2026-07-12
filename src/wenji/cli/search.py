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


def _in_process_search(
    db: Path,
    query: str,
    axis: str | None,
    limit: int,
    search_cfg,
) -> dict:
    from wenji.core.db import connect
    from wenji.ingest.embed import Embedder
    from wenji.observability.health import _ensure_consistency
    from wenji.search import Searcher

    _ensure_consistency(db)
    conn = connect(db)
    searcher = Searcher(
        conn,
        Embedder(),
        alpha=search_cfg.alpha,
        candidate_pool=search_cfg.candidate_pool,
    )
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
    limit: int | None = typer.Option(
        None, help="Top-K results to return (default: search.default_limit from config, 10)."
    ),
    config: Path | None = typer.Option(
        None, "--config", help="wenji.yaml path (beats WENJI_CONFIG; unset = env then defaults)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    from wenji.config import load_config, resolve_config_path

    search_cfg = load_config(resolve_config_path(config)).search
    if limit is None:
        limit = search_cfg.default_limit

    payload = _try_server(server, query, axis, limit)
    if payload is None:
        typer.echo(
            f"server at {server} not responding; running in-process search "
            f"(this loads the embed model, ~5s cold start)",
            err=True,
        )
        payload = _in_process_search(db, query, axis, limit, search_cfg)

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        results = payload.get("results", [])
        if not results:
            typer.echo("(no results)")
        for i, r in enumerate(results, start=1):
            title = r.get("title", "(no title)")
            score = r.get("hybrid_score") or 0.0
            src = r.get("source_type", "")
            typer.echo(f"  [{i:>2}] [{score:.3f}] {title}  — {src}")
            snippet = r.get("content_snippet", "")
            if snippet:
                typer.echo(f"        {snippet[:160]}")
    sys.exit(0)
