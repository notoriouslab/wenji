"""``wenji serve`` subcommand — start the FastAPI search server.

On startup prints a banner with PID / port / model_dir / "Ctrl+C to stop"
hint (UX borrowed from open-design ``tools-dev run``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from wenji.ingest.embed import DEFAULT_CACHE_DIR


def command(
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB path."),
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev)."),
    entity_source: list[str] = typer.Option(
        None,
        "--entity-source",
        help=(
            "Entity dictionary source for EntityScorer. Repeatable. "
            "Format: 'example:<name>' or absolute/relative .json path. "
            "Sets WENJI_ENTITY_SOURCES (last-write-wins on key collisions)."
        ),
    ),
    intent_source: list[str] = typer.Option(
        None,
        "--intent-source",
        help=(
            "Intent keywords source for IntentClassifier. Repeatable. "
            "Format: 'example:<name>' or absolute/relative .json path. "
            "Sets WENJI_INTENT_SOURCES."
        ),
    ),
) -> None:
    import uvicorn

    if entity_source:
        os.environ["WENJI_ENTITY_SOURCES"] = ",".join(entity_source)
    if intent_source:
        os.environ["WENJI_INTENT_SOURCES"] = ",".join(intent_source)

    model_dir = Path(os.environ.get("WENJI_MODEL_DIR", DEFAULT_CACHE_DIR))
    typer.echo(
        "\n".join(
            [
                "─" * 60,
                "  wenji serve",
                f"  pid       = {os.getpid()}",
                f"  url       = http://{host}:{port}",
                f"  db        = {db}",
                f"  model_dir = {model_dir}",
                "  Ctrl+C to stop",
                "─" * 60,
            ]
        ),
        err=True,
    )

    os.environ["WENJI_DB_PATH"] = str(db)
    uvicorn.run(
        "wenji.web.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
    sys.exit(0)
