"""Adapter that dumps a logos sqlite database to a wenji markdown corpus.

The adapter is strictly upstream of the standard wenji ingest pipeline. It
reads ``articles_meta`` JOIN ``articles_fts`` from logos.db, writes one
``.md`` file per article (YAML frontmatter + markdown body) into a target
directory, then exits. The user runs ``wenji ingest <out>`` afterwards to
perform the actual jieba pre-tokenization, BGE-M3 embedding, and chunking.

Atomicity model:

- Output is first written to a temporary directory next to ``--out``
  (sibling). On full success, the temp directory is renamed (atomic on
  POSIX) to the final ``--out`` path.
- On any error (unrecognized schema, OS error, encoding failure, etc.), the
  temp directory is removed and ``--out`` is left untouched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from wenji.core.errors import IngestError

REQUIRED_META_COLS = {"article_id", "title", "source_type"}
REQUIRED_FTS_COLS = {"article_id", "content"}
RECOGNISED_META_COLS = {
    "article_id",
    "title",
    "source_type",
    "pub_date",
    "pub_year",
    "content_length",
    "chunk_count",
    "content_hash",
    "indexed_at",
    "category",
    "author",
    "source_url",
    "subtype",
    "tags",
    "description",
}


@dataclass(frozen=True)
class DumpManifest:
    """Summary of a logos.db dump operation."""

    article_count: int
    dump_timestamp: str
    src_db: str
    out_dir: str
    source_type_distribution: dict[str, int]


def _validate_schema(conn: sqlite3.Connection) -> None:
    """Ensure logos.db has the expected schema; raise IngestError otherwise."""
    meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(articles_meta)")}
    if not meta_cols:
        raise IngestError("logos.db: 'articles_meta' table not found")
    missing_meta = REQUIRED_META_COLS - meta_cols
    if missing_meta:
        raise IngestError(
            f"logos.db: 'articles_meta' missing required columns: {missing_meta}"
        )
    unknown_meta = meta_cols - RECOGNISED_META_COLS
    if unknown_meta:
        raise IngestError(
            f"logos.db: 'articles_meta' has unrecognised columns: {unknown_meta}. "
            "Refusing to dump (would lose metadata silently). Update "
            "loader_logos_db.RECOGNISED_META_COLS or revise logos.db schema."
        )

    fts_cols = {row[1] for row in conn.execute("PRAGMA table_info(articles_fts)")}
    if not fts_cols:
        raise IngestError("logos.db: 'articles_fts' table not found")
    missing_fts = REQUIRED_FTS_COLS - fts_cols
    if missing_fts:
        raise IngestError(
            f"logos.db: 'articles_fts' missing required columns: {missing_fts}"
        )


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t) for t in parsed if t]
        except json.JSONDecodeError:
            pass
    # fallback: comma-separated
    return [s.strip() for s in raw.split(",") if s.strip()]


def _safe_filename(article_id: str) -> str:
    """Produce a filesystem-safe filename from article_id (alphanum + -._)."""
    safe = "".join(c if (c.isalnum() or c in "-._") else "_" for c in article_id)
    return f"{safe}.md"


def _row_to_markdown(row: dict) -> tuple[str, str, str]:
    """Convert a logos row dict into (filename, content, source_type)."""
    article_id = row["article_id"]
    body = row.get("content") or ""
    if body.lstrip().startswith("---"):
        # Avoid YAML frontmatter boundary clash by inserting a leading newline.
        body = "\n" + body

    content_hash = row.get("content_hash") or hashlib.sha256(body.encode("utf-8")).hexdigest()

    fm: dict = {
        "title": row.get("title") or "",
        "source_type": row.get("source_type") or "general",
        "article_id": article_id,
        "content_hash": content_hash,
    }
    if row.get("pub_date"):
        fm["pubDate"] = row["pub_date"]
    tags = _parse_tags(row.get("tags"))
    if tags:
        fm["tags"] = tags
    if row.get("source_url"):
        fm["source_url"] = row["source_url"]
    if row.get("subtype"):
        fm["subtype"] = row["subtype"]
    if row.get("author"):
        fm["author"] = row["author"]
    if row.get("description"):
        fm["description"] = row["description"]
    if row.get("category"):
        fm["category"] = row["category"]

    yaml_block = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    md = f"---\n{yaml_block}\n---\n\n{body}\n"
    return _safe_filename(article_id), md, fm["source_type"]


def _iter_rows(conn: sqlite3.Connection) -> Iterator[dict]:
    """Yield dict rows from articles_meta JOIN articles_fts."""
    cur = conn.execute(
        """
        SELECT
            m.article_id, m.title, m.source_type, m.pub_date, m.tags,
            m.author, m.source_url, m.subtype, m.description, m.category,
            m.content_hash, f.content
        FROM articles_meta m
        LEFT JOIN articles_fts f ON m.article_id = f.article_id
        ORDER BY m.article_id
        """
    )
    cols = [d[0] for d in cur.description]
    for r in cur:
        yield dict(zip(cols, r, strict=True))


def dump_logos_db(src_db: str | Path, out_dir: str | Path) -> DumpManifest:
    """Dump a logos.db to a wenji markdown corpus directory.

    Atomic semantics: the output dir is first staged in a temp directory and
    renamed on full success. Any error rolls back without touching ``out_dir``.

    Returns a :class:`DumpManifest`. Also writes ``<out_dir>/_manifest.json``.
    """
    src = Path(src_db)
    if not src.exists():
        raise IngestError(f"logos.db not found: {src}")
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise IngestError(f"out_dir already exists and is non-empty: {out}")

    conn = sqlite3.connect(str(src))
    try:
        _validate_schema(conn)

        # Stage in a sibling temp dir for atomic rename on success.
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}_tmp_", dir=str(out.parent)))
        try:
            count = 0
            source_type_dist: dict[str, int] = {}
            for row in _iter_rows(conn):
                if not row.get("article_id"):
                    raise IngestError("encountered row with empty article_id")
                if row.get("content") is None:
                    # missing FTS row for this article; skip silently
                    continue
                fname, md, st = _row_to_markdown(row)
                (tmp / fname).write_text(md, encoding="utf-8")
                source_type_dist[st] = source_type_dist.get(st, 0) + 1
                count += 1

            timestamp = datetime.now(timezone.utc).isoformat()
            manifest = DumpManifest(
                article_count=count,
                dump_timestamp=timestamp,
                src_db=str(src),
                out_dir=str(out),
                source_type_distribution=source_type_dist,
            )
            (tmp / "_manifest.json").write_text(
                json.dumps(
                    {
                        "article_count": count,
                        "dump_timestamp": timestamp,
                        "src_db": str(src),
                        "out_dir": str(out),
                        "source_type_distribution": source_type_dist,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # Atomic rename (POSIX). If out exists empty, remove it first.
            if out.exists():
                out.rmdir()
            tmp.rename(out)
            return manifest
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    finally:
        conn.close()
