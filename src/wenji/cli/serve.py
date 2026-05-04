"""``wenji serve`` subcommand — start the FastAPI search server.

On startup prints a banner with PID / port / model_dir / "Ctrl+C to stop"
hint (UX borrowed from open-design ``tools-dev run``).

LLM query rewrite (v0.3.2): if ``WENJI_LLM_BASE_URL`` / ``WENJI_LLM_API_KEY``
/ ``WENJI_LLM_MODEL`` are all set, a ``QueryRewriter`` is wired into the
``Searcher`` automatically by ``wenji.web.app``. ``--enable-rewrite`` /
``--no-rewrite`` flags override the env-derived default for this invocation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from wenji.config import load_llm_config_from_env
from wenji.ingest.embed import DEFAULT_CACHE_DIR


def command(
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB path."),
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev)."),
    enable_rewrite: bool = typer.Option(
        False,
        "--enable-rewrite",
        help="Force LLM query rewrite on (requires WENJI_LLM_* env vars).",
    ),
    no_rewrite: bool = typer.Option(
        False,
        "--no-rewrite",
        help="Force LLM query rewrite off (overrides env-derived default).",
    ),
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

    if enable_rewrite and no_rewrite:
        typer.echo("--enable-rewrite and --no-rewrite are mutually exclusive", err=True)
        sys.exit(2)

    if enable_rewrite:
        cfg = load_llm_config_from_env()
        if not cfg.enabled:
            typer.echo(
                "--enable-rewrite specified but the following env vars are missing: "
                + ", ".join(cfg.missing_fields()),
                err=True,
            )
            sys.exit(2)
        os.environ["WENJI_REWRITE_OVERRIDE"] = "enabled"
    elif no_rewrite:
        os.environ["WENJI_REWRITE_OVERRIDE"] = "disabled"
    # else: leave unset — web app uses env-derived LLMConfig.enabled

    if entity_source:
        os.environ["WENJI_ENTITY_SOURCES"] = ",".join(entity_source)
    if intent_source:
        os.environ["WENJI_INTENT_SOURCES"] = ",".join(intent_source)

    model_dir = Path(os.environ.get("WENJI_MODEL_DIR", DEFAULT_CACHE_DIR))
    rewrite_state = (
        "forced ON"
        if enable_rewrite
        else ("forced OFF" if no_rewrite else "env-derived")
    )
    typer.echo(
        "\n".join(
            [
                "─" * 60,
                "  wenji serve",
                f"  pid       = {os.getpid()}",
                f"  url       = http://{host}:{port}",
                f"  db        = {db}",
                f"  model_dir = {model_dir}",
                f"  rewrite   = {rewrite_state}",
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
