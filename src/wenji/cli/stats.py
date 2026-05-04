"""``wenji stats`` — corpus + index observability snapshot."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from wenji.cli._format import format_stats_human


def command(
    db: Path = typer.Option(
        Path(os.environ.get("WENJI_DB_PATH", "data/wenji.db")),
        help="SQLite DB path (defaults to $WENJI_DB_PATH or data/wenji.db).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """Print wenji corpus + index stats. Mirrors GET /api/stats output."""
    from wenji.classify.axes_loader import ConfigError, load_axes_config
    from wenji.core.db import connect
    from wenji.observability import compute_stats

    axes_path = os.environ.get("WENJI_AXES_YAML")
    axes_config = None
    if axes_path:
        try:
            axes_config = load_axes_config(axes_path)
        except (ConfigError, FileNotFoundError, OSError) as exc:
            typer.echo(
                f"warning: WENJI_AXES_YAML set but failed to load ({exc}); axes will be empty",
                err=True,
            )

    if not db.exists():
        typer.echo(f"error: DB not found at {db}", err=True)
        sys.exit(2)

    conn = connect(db)
    try:
        stats = compute_stats(conn, axes_config)
    finally:
        conn.close()

    if json_output:
        typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        typer.echo(format_stats_human(stats))
    sys.exit(0)
