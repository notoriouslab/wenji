"""``wenji classify`` subcommand — apply axes.yaml classification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from wenji.core.db import connect


def command(
    db: Path = typer.Option(Path("data/wenji.db"), help="SQLite DB path."),
    config: Path = typer.Option(..., "--config", exists=True, help="axes.yaml path."),
    validate: bool = typer.Option(False, "--validate", help="Run validate() after classify_all."),
) -> None:
    from wenji.classify import AxesClassifier, load_axes_config

    cfg = load_axes_config(config)
    conn = connect(db)
    cls = AxesClassifier(conn, cfg)

    typer.echo(f"classifying with {len(cfg.axes)} axis rules → {db}", err=True)
    results = cls.classify_all()
    typer.echo(json.dumps({"classified": len(results)}, ensure_ascii=False))

    if validate:
        report = cls.validate()
        typer.echo(
            json.dumps(
                {
                    "validation": "PASS" if report.passed else "FAIL",
                    "metrics": report.metrics,
                    "failures": report.failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if not report.passed:
            conn.close()
            sys.exit(1)

    conn.close()
    sys.exit(0)
