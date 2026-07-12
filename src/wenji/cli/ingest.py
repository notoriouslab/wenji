"""``wenji ingest`` subapp.

Subcommands:
- ``dir``: ingest a markdown corpus directory.

Note: ``wenji ingest <path>`` (no subcommand) is the legacy form and is no
longer supported in v0.3.1. Use ``wenji ingest dir <path>`` explicitly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="ingest",
    help="Ingest markdown corpora and external sources.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command("dir")
def dir_command(
    corpus_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB path."),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="YAML mapping directory_map / chunk_strategies; structure: "
        "{directory_map: {<dirname>: <source_type>}, chunk_strategies: {<source_type>: {strategy: paragraph, min_chars: 200}}}",
    ),
    recursive: bool = typer.Option(True, help="Recurse into subdirectories."),
    skip_bad: bool = typer.Option(
        False,
        "--skip-bad",
        help="Skip files whose frontmatter fails to parse (listed at the end, "
        "exit code 1) instead of aborting on the first bad file.",
    ),
) -> None:
    """Ingest a markdown corpus directory into a wenji DB.

    Interrupted runs resume by re-running the same command: unchanged
    articles take the content-hash fast path without re-embedding.
    """
    from wenji.core.db import connect, initialise_schema
    from wenji.ingest import ingest_dir
    from wenji.ingest.embed import Embedder

    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    initialise_schema(conn)

    from wenji.config import load_config

    cfg = load_config(config)
    directory_map = cfg.directory_map
    chunk_strategies = cfg.chunk_strategies

    embedder = Embedder()
    typer.echo(f"ingesting {corpus_dir} → {db}", err=True)
    bad_files: list[tuple[str, str]] = []
    article_ids = ingest_dir(
        corpus_dir,
        conn,
        embedder,
        recursive=recursive,
        directory_map=directory_map,
        directory_map_overrides_frontmatter=cfg.directory_map_overrides_frontmatter,
        chunk_strategies=chunk_strategies,
        skip_bad=skip_bad,
        bad_files_out=bad_files,
    )
    conn.close()
    payload: dict = {"ingested": len(article_ids)}
    if bad_files:
        payload["skipped_bad"] = [{"path": p, "error": e} for p, e in bad_files]
    typer.echo(json.dumps(payload, ensure_ascii=False), err=False)
    sys.exit(1 if bad_files else 0)


# Backward-compat shim for callers expecting a single ``command`` symbol.
def command(*args, **kwargs) -> None:
    return dir_command(*args, **kwargs)
