"""``wenji download-model`` subcommand — fetch the embed ONNX model from HF."""

from __future__ import annotations

import sys
from pathlib import Path

import typer


def command(
    target: str = typer.Argument(
        "embed",
        help="Which model to download: 'embed' (BGE-M3).",
    ),
    repo_id: str | None = typer.Option(None, help="HF repo id (override default)."),
    cache_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Local target directory (default: ~/.cache/wenji/<model>).",
    ),
) -> None:
    from wenji.core.model_download import download_embed_model

    if target == "embed":
        kwargs = {"target_dir": cache_dir}
        if repo_id is not None:
            kwargs["repo_id"] = repo_id
        out = download_embed_model(**kwargs)
        typer.echo(f"embed model ready at {out}")
    else:
        typer.echo(f"unknown target: {target!r}; expected 'embed'", err=True)
        sys.exit(2)
    sys.exit(0)
