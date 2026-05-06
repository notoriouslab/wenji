"""``wenji corpus`` subapp — direct corpus mutation for stage-2 trim.

Subcommands:
- ``trim``: delete articles by article_id or content_hash, atomically across
  ``articles_meta`` / ``articles_fts`` / ``chunks_fts`` / ``doc_vectors``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="corpus",
    help="Direct corpus mutation utilities (e.g. trim).",
    no_args_is_help=True,
    add_completion=False,
)

_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def detect_id_kind(token: str) -> str:
    """Return ``"hash"`` if token is a SHA-256 hex; ``"article_id"`` otherwise.

    Empty / non-string inputs raise ``ValueError``.
    """
    if not isinstance(token, str) or not token:
        raise ValueError("token must be a non-empty string")
    if _HASH_RE.match(token):
        return "hash"
    return "article_id"


def parse_id_list(text: str) -> list[tuple[str, str]]:
    """Parse a newline-delimited id list into ``[(kind, value), ...]``.

    Raises ``ValueError`` on any invalid line, naming the line number. Empty
    lines and ``#`` comments are skipped.
    """
    out: list[tuple[str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        # detect_id_kind validates non-empty + str
        try:
            kind = detect_id_kind(s)
        except ValueError as exc:
            raise ValueError(f"line {lineno}: {exc}") from exc
        # additional sanity: article_id should not contain whitespace
        if kind == "article_id" and any(c.isspace() for c in s):
            raise ValueError(f"line {lineno}: article_id contains whitespace: {s!r}")
        out.append((kind, s))
    return out


def _resolve_article_ids(conn: sqlite3.Connection, items: list[tuple[str, str]]) -> list[str]:
    """Resolve a (kind, value) list to a list of article_ids.

    Hashes are resolved by querying ``articles_meta.content_hash``.
    """
    ids: list[str] = []
    for kind, value in items:
        if kind == "article_id":
            ids.append(value)
        else:  # hash
            row = conn.execute(
                "SELECT article_id FROM articles_meta WHERE content_hash = ?", (value,)
            ).fetchone()
            if row is None:
                raise ValueError(f"content_hash not found in articles_meta: {value}")
            ids.append(row[0])
    return ids


def trim_corpus(db_path: str | Path, ids_text: str) -> dict:
    """Atomic trim of articles by article_id or content_hash list.

    Returns a manifest dict with ``removed_count`` /
    ``corpus_size_before`` / ``corpus_size_after`` / ``removed_by_source_type``.

    Raises ``ValueError`` for any invalid line in ``ids_text``. Raises
    ``sqlite3.Error`` for DB issues; transaction rolls back atomically on any
    error.
    """
    items = parse_id_list(ids_text)  # validate first; raises before any DB write
    if not items:
        raise ValueError("id list is empty after stripping comments / blanks")

    db = Path(db_path)
    conn = sqlite3.connect(str(db))
    try:
        before = conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
        article_ids = _resolve_article_ids(conn, items)

        # Capture source_type distribution BEFORE deletion for the manifest.
        placeholders = ",".join(["?"] * len(article_ids))
        st_rows = conn.execute(
            f"SELECT source_type, COUNT(*) FROM articles_meta WHERE article_id IN ({placeholders}) GROUP BY source_type",
            article_ids,
        ).fetchall()
        removed_by_source_type = {row[0] or "(unknown)": row[1] for row in st_rows}

        conn.execute("BEGIN")
        try:
            for table in ("doc_vectors", "chunks_fts", "articles_fts", "articles_meta"):
                conn.execute(
                    f"DELETE FROM {table} WHERE article_id IN ({placeholders})",
                    article_ids,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        after = conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
        removed_count = before - after
        return {
            "removed_count": removed_count,
            "corpus_size_before": before,
            "corpus_size_after": after,
            "removed_by_source_type": removed_by_source_type,
        }
    finally:
        conn.close()


@app.command("trim")
def trim_command(
    ids: Path = typer.Option(
        ..., "--ids", exists=True, help="Newline-delimited id list (article_id or content_hash)."
    ),
    db: Path = typer.Option(..., "--db", help="wenji.db path."),
) -> None:
    """Delete articles atomically and emit a summary."""
    text = ids.read_text(encoding="utf-8")
    try:
        manifest = trim_corpus(db, text)
    except ValueError as exc:
        typer.echo(f"trim aborted: {exc}", err=True)
        sys.exit(2)
    typer.echo(json.dumps(manifest, ensure_ascii=False, indent=2))
