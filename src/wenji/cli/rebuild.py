"""``wenji rebuild`` subcommand — drop derived tables + re-ingest from disk."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
import yaml

from wenji.core.db import connect, initialise_schema


def command(
    corpus_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB path."),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    from wenji.ingest import rebuild_from_disk
    from wenji.ingest.embed import Embedder

    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    initialise_schema(conn)

    directory_map: dict[str, str] = {}
    chunk_strategies: dict = {}
    if config is not None:
        cfg = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        directory_map = cfg.get("directory_map", {}) or {}
        chunk_strategies = cfg.get("chunk_strategies", {}) or {}

    typer.echo(f"rebuilding {db} from {corpus_dir}", err=True)
    article_ids = rebuild_from_disk(
        conn,
        corpus_dir,
        Embedder(),
        directory_map=directory_map,
        chunk_strategies=chunk_strategies,
    )
    conn.close()
    typer.echo(json.dumps({"rebuilt": len(article_ids)}, ensure_ascii=False))
    sys.exit(0)
