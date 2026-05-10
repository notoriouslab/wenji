"""``wenji doctor`` — db consistency health check.

Read-only. Reports L2 (cross-table derived-from sanity, sub-rules c/d/e)
+ L3 (sample MATCH validation) inconsistencies. Exit 0 if OK, 1 if any
issue detected. Optional ``--sample-keywords`` CSV overrides the default
Chinese keyword set for non-Chinese corpora.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer


def command(
    db: Path = typer.Option(
        Path(os.environ.get("WENJI_DB_PATH", "data/wenji.db")),
        help="SQLite DB path (defaults to $WENJI_DB_PATH or data/wenji.db).",
    ),
    sample_keywords: str = typer.Option(
        "",
        "--sample-keywords",
        help=(
            "CSV of keywords for FTS sample MATCH validation. "
            "Empty / whitespace = use default Chinese set "
            "(神, 人, 心, 天, 之)."
        ),
    ),
) -> None:
    """Check wenji db consistency (cross-table sanity + sample MATCH)."""
    from wenji.core.db import connect
    from wenji.observability.health import DEFAULT_SAMPLE_KEYWORDS, check_consistency

    if not db.exists():
        typer.echo(f"error: DB not found at {db}", err=True)
        sys.exit(2)

    parsed = [k.strip() for k in sample_keywords.split(",") if k.strip()]
    keywords = tuple(parsed) if parsed else DEFAULT_SAMPLE_KEYWORDS

    conn = connect(db)
    try:
        report = check_consistency(conn, keywords)
    finally:
        conn.close()

    typer.echo(report.format())
    sys.exit(0 if report.ok else 1)
