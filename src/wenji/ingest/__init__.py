"""wenji.ingest — markdown → SQLite pipeline (idempotent, disk = SSOT).

Public API:

- :func:`ingest_one` — single .md file
- :func:`ingest_dir` — directory traversal, calls ingest_one per file
- :func:`rebuild_from_disk` — drop derived tables + re-ingest entire corpus
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from wenji.core.chunk import chunk as chunk_text
from wenji.core.errors import IngestError
from wenji.core.hash import content_hash
from wenji.core.normalize import normalize
from wenji.ingest.frontmatter import load_article
from wenji.ingest.jieba_setup import tokenize_for_fts


class EmbedderProtocol(Protocol):
    """Duck-typed interface for embedders. See :class:`wenji.ingest.embed.Embedder`."""

    DIM: int

    def encode_batch(self, texts: list[str]) -> Any: ...


def _stable_article_id(path: Path, content_hash_value: str) -> str:
    """Stable identifier from relative path stem + content hash."""
    return f"{path.stem}-{content_hash_value[:8]}"


def _serialise_tags(value: Any) -> str:
    """Tags may arrive as list or string; canonical storage is JSON list string."""
    if value is None:
        return ""
    if isinstance(value, str):
        # User wrote a single string; preserve as JSON list of one
        return json.dumps([value], ensure_ascii=False)
    if isinstance(value, list):
        return json.dumps([str(t) for t in value], ensure_ascii=False)
    return json.dumps([str(value)], ensure_ascii=False)


def _join_tags_for_fts(value: Any) -> str:
    """Tags for FTS column: space-joined plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(t) for t in value)
    return str(value)


def _extract_first_h1(body: str) -> str | None:
    """Return the first ``# Heading`` line text, or None if there isn't one near the top.

    Looks at lines until the first non-empty non-H1 line; if the first content
    line is an H1, returns it stripped. Used as a title fallback when frontmatter
    doesn't supply one.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
        return None
    return None


def ingest_one(
    md_path: str | Path,
    conn: sqlite3.Connection,
    embedder: EmbedderProtocol,
    *,
    directory_map: Mapping[str, str] | None = None,
    chunk_strategies: Mapping[str, Mapping[str, Any]] | None = None,
    indexed_at: str | None = None,
) -> str:
    """Ingest a single markdown file, idempotent on ``content_hash``.

    Returns the resulting ``article_id``.

    Args:
        md_path: Path to ``.md`` file.
        conn: Open SQLite connection (schema already initialised).
        embedder: Object exposing ``encode_batch(list[str]) -> ndarray``.
        directory_map: parent-dir-name → source_type fallback when frontmatter
            lacks ``source_type``.
        chunk_strategies: ``source_type`` → ``{"strategy": <name>, **kwargs}``
            mapping. Source types not present here are not chunked.
        indexed_at: ISO timestamp for ``articles_meta.indexed_at``; defaults
            to ``datetime.now(timezone.utc).isoformat()``.
    """
    path = Path(md_path)
    article = load_article(path, directory_map=directory_map)
    body_norm = normalize(article.body)
    if not body_norm:
        raise IngestError(f"empty body after normalize: {path}")

    chash = content_hash(body_norm)
    article_id = _stable_article_id(path, chash)
    indexed_at = indexed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    fm_title = article.metadata.get("title")
    if fm_title:
        title = str(fm_title)
    else:
        h1 = _extract_first_h1(body_norm)
        title = h1 if h1 else path.stem
    pub_date = article.metadata.get("pubDate") or article.metadata.get("pub_date") or ""
    pub_year_raw = article.metadata.get("pub_year")
    pub_year: int | None
    if pub_year_raw is not None:
        try:
            pub_year = int(pub_year_raw)
        except (TypeError, ValueError):
            pub_year = None
    elif isinstance(pub_date, str) and len(pub_date) >= 4 and pub_date[:4].isdigit():
        pub_year = int(pub_date[:4])
    else:
        pub_year = None

    tags_raw = article.metadata.get("tags")
    tags_json = _serialise_tags(tags_raw)
    tags_for_fts = _join_tags_for_fts(tags_raw)

    category = str(article.metadata.get("category") or "")
    subtype = str(article.metadata.get("subtype") or "")
    author = str(article.metadata.get("author") or "")
    # Accept several common frontmatter keys for the article's external URL.
    source_url = str(
        article.metadata.get("source_url")
        or article.metadata.get("source")
        or article.metadata.get("link")
        or ""
    )
    description = str(article.metadata.get("description") or "")

    title_tok = tokenize_for_fts(title)
    body_tok = tokenize_for_fts(body_norm)
    tags_tok = tokenize_for_fts(tags_for_fts)

    # Idempotent upsert articles_meta. Skip body re-embed if hash unchanged.
    existing = conn.execute(
        "SELECT content_hash FROM articles_meta WHERE article_id = ?",
        (article_id,),
    ).fetchone()
    unchanged = existing is not None and existing[0] == chash

    chunk_count = 0
    chunks: list[str] = []
    # Frontmatter `chunk_strategy: <preset name>` overrides source_type default.
    fm_chunk_strategy = article.metadata.get("chunk_strategy")
    if fm_chunk_strategy:
        chunks = chunk_text(body_norm, strategy=str(fm_chunk_strategy))
        chunk_count = len(chunks)
    else:
        strategy_for_type = (chunk_strategies or {}).get(article.source_type)
        if strategy_for_type:
            strategy_name = str(strategy_for_type.get("strategy", "paragraph"))
            kwargs = {k: v for k, v in strategy_for_type.items() if k != "strategy"}
            chunks = chunk_text(body_norm, strategy=strategy_name, **kwargs)
            chunk_count = len(chunks)

    conn.execute(
        """
        INSERT INTO articles_meta (
            article_id, title, source_type, pub_date, pub_year,
            content_length, chunk_count, content_hash, indexed_at,
            category, author, source_url, subtype, tags, description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            title          = excluded.title,
            source_type    = excluded.source_type,
            pub_date       = excluded.pub_date,
            pub_year       = excluded.pub_year,
            content_length = excluded.content_length,
            chunk_count    = excluded.chunk_count,
            content_hash   = excluded.content_hash,
            indexed_at     = excluded.indexed_at,
            category       = excluded.category,
            author         = excluded.author,
            source_url     = excluded.source_url,
            subtype        = excluded.subtype,
            tags           = excluded.tags,
            description    = excluded.description
        """,
        (
            article_id,
            title,
            article.source_type,
            pub_date,
            pub_year,
            len(body_norm),
            chunk_count,
            chash,
            indexed_at,
            category,
            author,
            source_url,
            subtype,
            tags_json,
            description,
        ),
    )

    # FTS: delete + re-insert (FTS5 has no ON CONFLICT)
    conn.execute("DELETE FROM articles_fts WHERE article_id = ?", (article_id,))
    conn.execute(
        """
        INSERT INTO articles_fts (
            article_id, title, title_raw, content, content_raw,
            tags, tags_raw, category, source_type, pub_date, pub_year
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article_id,
            title_tok,
            title,
            body_tok,
            body_norm,
            tags_tok,
            tags_for_fts,
            category,
            article.source_type,
            pub_date,
            str(pub_year) if pub_year is not None else "",
        ),
    )

    conn.execute("DELETE FROM chunks_fts WHERE article_id = ?", (article_id,))
    for idx, ch in enumerate(chunks):
        ch_tok = tokenize_for_fts(ch)
        conn.execute(
            """
            INSERT INTO chunks_fts (
                chunk_id, article_id, chunk_index, title, title_raw,
                chunk_text, chunk_text_raw, tags, tags_raw,
                source_type, pub_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{article_id}-{idx:04d}",
                article_id,
                idx,
                title_tok,
                title,
                ch_tok,
                ch,
                tags_tok,
                tags_for_fts,
                article.source_type,
                str(pub_year) if pub_year is not None else "",
            ),
        )

    if unchanged:
        # content unchanged → reuse stored vec, no re-embed
        return article_id

    vec_array = embedder.encode_batch([body_norm])
    vec_bytes = vec_array[0].astype("float32").tobytes()
    if len(vec_bytes) != embedder.DIM * 4:
        raise IngestError(f"embedder returned {len(vec_bytes)} bytes; expected {embedder.DIM * 4}")
    conn.execute(
        "INSERT INTO doc_vectors (article_id, vec) VALUES (?, ?) "
        "ON CONFLICT(article_id) DO UPDATE SET vec = excluded.vec",
        (article_id, vec_bytes),
    )
    return article_id


def ingest_dir(
    dir_path: str | Path,
    conn: sqlite3.Connection,
    embedder: EmbedderProtocol,
    *,
    recursive: bool = True,
    directory_map: Mapping[str, str] | None = None,
    chunk_strategies: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Ingest all ``.md`` files under ``dir_path``. Returns list of article_ids."""
    root = Path(dir_path)
    if not root.is_dir():
        raise IngestError(f"not a directory: {root}")
    pattern = "**/*.md" if recursive else "*.md"
    article_ids: list[str] = []
    for md in sorted(root.glob(pattern)):
        article_ids.append(
            ingest_one(
                md,
                conn,
                embedder,
                directory_map=directory_map,
                chunk_strategies=chunk_strategies,
            )
        )
    conn.commit()
    return article_ids


def rebuild_from_disk(
    conn: sqlite3.Connection,
    corpus_dir: str | Path,
    embedder: EmbedderProtocol,
    *,
    directory_map: Mapping[str, str] | None = None,
    chunk_strategies: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Drop derived tables, re-init schema, re-ingest entire corpus.

    Idempotent + byte-identical: two consecutive runs on the same disk produce
    identical ``articles_fts.content`` and ``doc_vectors.vec`` (subject to
    deterministic embedder).
    """
    from wenji.core.db import initialise_schema

    for tbl in ("articles_meta", "articles_fts", "chunks_fts", "doc_vectors", "article_axes"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.execute(
        "UPDATE wenji_meta SET value = '0' WHERE key IN ('n_articles', 'n_chunks', 'n_doc_vectors')"
    )
    conn.commit()

    # Re-run schema init (idempotent — tables already exist with IF NOT EXISTS guard)
    initialise_schema(conn)

    return ingest_dir(
        corpus_dir,
        conn,
        embedder,
        recursive=True,
        directory_map=directory_map,
        chunk_strategies=chunk_strategies,
    )


__all__ = ["ingest_one", "ingest_dir", "rebuild_from_disk", "EmbedderProtocol"]
