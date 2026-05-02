"""``wenji set-chunk-strategy`` — batch-write ``chunk_strategy`` into ``.md`` frontmatter.

For the 5% case where 50+ articles in a directory all need a non-default
strategy. Atomic write: temp file + ``os.replace``. ``--dry-run`` prints
intended changes without writing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import frontmatter
import typer

from wenji.core.chunk import STRATEGIES


def command(
    target: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=True),
    strategy: str = typer.Option(..., "--strategy", help=f"One of {sorted(STRATEGIES)}."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    skip_existing: bool = typer.Option(
        False,
        "--skip-existing",
        help="Don't overwrite when frontmatter already has chunk_strategy.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing."),
) -> None:
    if strategy not in STRATEGIES:
        typer.echo(f"unknown strategy {strategy!r}; choices: {sorted(STRATEGIES)}", err=True)
        raise typer.Exit(2)

    if target.is_file():
        files = [target]
    else:
        pattern = "**/*.md" if recursive else "*.md"
        files = sorted(target.glob(pattern))

    n_total = len(files)
    n_changed = 0
    n_skipped = 0

    for f in files:
        try:
            post = frontmatter.load(f)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  SKIP (parse error) {f}: {exc}", err=True)
            n_skipped += 1
            continue

        existing = post.metadata.get("chunk_strategy")
        if existing == strategy:
            typer.echo(f"  unchanged {f}  (already {strategy})")
            continue
        if existing and skip_existing:
            typer.echo(f"  skip-existing {f}  (current: {existing})")
            n_skipped += 1
            continue

        post["chunk_strategy"] = strategy
        if dry_run:
            typer.echo(f"  [dry] would set chunk_strategy={strategy} on {f}")
            n_changed += 1
            continue

        text = frontmatter.dumps(post, sort_keys=False)
        # Atomic write: temp file + replace
        tmp_dir = f.parent
        with tempfile.NamedTemporaryFile(
            "w",
            dir=tmp_dir,
            delete=False,
            encoding="utf-8",
            suffix=".md.tmp",
        ) as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        os.replace(tmp_path, f)
        typer.echo(f"  ✓ {f}  → chunk_strategy={strategy}")
        n_changed += 1

    typer.echo()
    typer.echo(
        f"summary: {n_changed} changed / {n_skipped} skipped / {n_total} total "
        f"({'dry-run' if dry_run else 'written'})"
    )
    raise typer.Exit(0)
