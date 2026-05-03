"""``wenji aggregate`` sub-app — utility commands for the aggregate cache.

Aggregation itself is a query-time operation exposed via Web (chat panel) and
the Python API (``wenji.aggregate.Aggregator``); the CLI surface is limited
to cache management.
"""

from __future__ import annotations

from pathlib import Path

import typer

from wenji.aggregate.cache import cache_clear
from wenji.core.db import connect

app = typer.Typer(
    name="aggregate",
    help="wenji.aggregate cache management.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command(
    name="clear-cache",
    help="Wipe the aggregate_cache table for a wenji DB (clears topic/concept and ask caches).",
)
def clear_cache(
    db: Path = typer.Option(..., "--db", exists=True, dir_okay=False, file_okay=True),
) -> None:
    conn = connect(db)
    try:
        deleted = cache_clear(conn)
    finally:
        conn.close()
    typer.echo(f"cleared {deleted} row(s) from aggregate_cache in {db}")
