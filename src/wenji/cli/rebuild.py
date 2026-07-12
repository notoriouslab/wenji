"""``wenji rebuild`` subcommand — drop derived tables + re-ingest from disk."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from wenji.core.db import connect, initialise_schema


def command(
    corpus_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB path."),
    config: Path | None = typer.Option(None, "--config"),
    skip_bad: bool = typer.Option(
        False,
        "--skip-bad",
        help="Skip files whose frontmatter fails to parse (listed at the end, "
        "exit code 1) instead of aborting on the first bad file.",
    ),
) -> None:
    """Wipe derived tables and re-ingest the corpus (byte-identical rebuild).

    To RESUME an interrupted bulk run, do NOT re-run rebuild (it always
    starts from a wipe) — run `wenji ingest dir` with the same arguments
    instead: completed articles take the content-hash fast path.
    """
    from wenji.ingest import rebuild_from_disk
    from wenji.ingest.embed import Embedder

    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    initialise_schema(conn)

    from wenji.config import load_config

    cfg = load_config(config)
    directory_map = cfg.directory_map
    chunk_strategies = cfg.chunk_strategies

    typer.echo(f"rebuilding {db} from {corpus_dir}", err=True)
    bad_files: list[tuple[str, str]] = []
    article_ids = rebuild_from_disk(
        conn,
        corpus_dir,
        Embedder(),
        directory_map=directory_map,
        chunk_strategies=chunk_strategies,
        skip_bad=skip_bad,
        bad_files_out=bad_files,
    )
    conn.close()
    payload: dict = {"rebuilt": len(article_ids)}
    if bad_files:
        payload["skipped_bad"] = [{"path": p, "error": e} for p, e in bad_files]
    typer.echo(json.dumps(payload, ensure_ascii=False))
    sys.exit(1 if bad_files else 0)
