"""``wenji inspect-chunks`` subcommand — preview chunking output for a markdown file.

Sanity check before committing to a strategy. Pure preview, no DB writes.
"""

from __future__ import annotations

from pathlib import Path

import typer

from wenji.core.chunk import STRATEGIES, chunk
from wenji.core.normalize import normalize
from wenji.ingest.frontmatter import parse_markdown


def command(
    md_path: Path = typer.Argument(..., exists=True, dir_okay=False, file_okay=True),
    strategy: str = typer.Option(
        "paragraph",
        "--strategy",
        help=f"Chunk strategy: one of {sorted(STRATEGIES)}.",
    ),
    head_chars: int = typer.Option(80, "--head", help="Per-chunk preview length in characters."),
) -> None:
    if strategy not in STRATEGIES:
        typer.echo(f"unknown strategy {strategy!r}; choices: {sorted(STRATEGIES)}", err=True)
        raise typer.Exit(2)

    metadata, body = parse_markdown(md_path)
    body_norm = normalize(body)

    # Honour frontmatter override if present (matches ingest behaviour)
    fm_strategy = metadata.get("chunk_strategy")
    used = strategy
    if fm_strategy:
        typer.echo(
            f"[frontmatter chunk_strategy={fm_strategy!r} would override --strategy]", err=True
        )
        used = str(fm_strategy)
        if used not in STRATEGIES:
            typer.echo(f"frontmatter strategy {used!r} not registered", err=True)
            raise typer.Exit(2)

    chunks = chunk(body_norm, strategy=used)
    typer.echo(f"file       : {md_path}")
    typer.echo(f"strategy   : {used}")
    typer.echo(f"body_chars : {len(body_norm)}")
    typer.echo(f"chunks     : {len(chunks)}")
    typer.echo()
    for i, ch in enumerate(chunks):
        head = ch[:head_chars].replace("\n", " ⏎ ")
        suffix = "..." if len(ch) > head_chars else ""
        typer.echo(f"  [{i:>3}] ({len(ch)} chars) {head}{suffix}")
    raise typer.Exit(0)
