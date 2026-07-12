"""wenji CLI entry point.

Aggregates 6 subcommands (ingest / search / classify / rebuild / eval / serve)
each thin layer over the corresponding ``wenji.*`` library module. Heavy
imports (``onnxruntime``, ``fastapi``) are deferred to subcommand bodies so
``wenji --help`` stays fast.
"""

from __future__ import annotations

import typer

from wenji.cli import aggregate as _aggregate
from wenji.cli import classify as _classify
from wenji.cli import corpus as _corpus
from wenji.cli import doctor as _doctor
from wenji.cli import download as _download
from wenji.cli import eval as _eval
from wenji.cli import ingest as _ingest
from wenji.cli import inspect as _inspect
from wenji.cli import rebuild as _rebuild
from wenji.cli import search as _search
from wenji.cli import segment as _segment
from wenji.cli import serve as _serve
from wenji.cli import set_chunk_strategy as _set_chunk_strategy
from wenji.cli import stats as _stats

app = typer.Typer(
    name="wenji",
    help="wenji — generic Chinese markdown RAG framework.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(_ingest.app, name="ingest")
app.command(
    name="search",
    help="Run a search; tries local server first, falls back to in-process.",
)(_search.command)
app.command(name="classify", help="Apply axes.yaml classification to articles.")(_classify.command)
app.command(name="rebuild", help="Drop derived tables and re-ingest from disk.")(_rebuild.command)
app.add_typer(_eval.app, name="eval")
app.command(name="serve", help="Start the FastAPI search server.")(_serve.command)
app.command(
    name="download-model",
    help="Fetch the BGE-M3 embed ONNX model from HuggingFace.",
)(_download.command)
app.command(
    name="inspect-chunks",
    help="Preview how a markdown file is split under a given chunk strategy.",
)(_inspect.command)
app.command(
    name="set-chunk-strategy",
    help="Batch-write chunk_strategy into the frontmatter of all .md under a path.",
)(_set_chunk_strategy.command)
app.add_typer(_aggregate.app, name="aggregate")
app.add_typer(_corpus.app, name="corpus")
app.command(name="stats", help="Print corpus + index stats (mirrors GET /api/stats).")(
    _stats.command
)
app.command(
    name="doctor",
    help="Check db consistency (row count vs counter, sample FTS MATCH).",
)(_doctor.command)
app.command(
    name="segment",
    help="Trace how a query is segmented (jieba tokens, FTS form, dict hits).",
)(_segment.command)


__all__ = ["app"]
