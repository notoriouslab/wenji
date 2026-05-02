"""wenji.search — hybrid BM25 + vector retrieval with optional rerank / rewrite.

Public API:
- :class:`Searcher` — main entry, takes a connection + embedder + optional
  reranker / rewriter, exposes :meth:`Searcher.search`.

Modular pieces are exported for advanced users / tests:
- :func:`bm25_search` from :mod:`wenji.search.bm25`
- :func:`vector_search` from :mod:`wenji.search.vector`
- :func:`hybrid_combine` from :mod:`wenji.search.hybrid`
"""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol

import numpy as np
from markdown_it import MarkdownIt
from markdown_it.token import Token

from wenji.core.errors import SearchError
from wenji.search.bm25 import bm25_search, build_fts_query
from wenji.search.hybrid import DEFAULT_ALPHA, hybrid_combine
from wenji.search.rerank import CrossEncoderReranker
from wenji.search.rewrite import QueryRewriter
from wenji.search.vector import vector_search


class EmbedderProtocol(Protocol):
    DIM: int

    def encode_batch(self, texts: list[str]) -> np.ndarray: ...


_MD_SNIPPET = MarkdownIt("commonmark", {"html": False, "linkify": False})

_BLOCK_BOUNDARY_TOKENS = frozenset(
    {
        "paragraph_close",
        "heading_close",
        "blockquote_close",
        "list_item_close",
        "bullet_list_close",
        "ordered_list_close",
        "code_block",
        "fence",
    }
)


def _strip_markdown_for_snippet(text: str) -> str:
    """Convert Markdown text to plain text via the markdown-it AST.

    Replaces an earlier regex approach that mangled URLs with underscores
    (``Foo_bar`` → ``Foobar``) and code spans containing punctuation. The AST
    walker accumulates ``text`` and ``code_inline`` content, treating soft /
    hard breaks and block-level closes as whitespace. Avoids introducing
    BeautifulSoup as a dependency by walking tokens directly instead of
    rendering to HTML.
    """
    if not text:
        return text

    parts: list[str] = []

    def walk(tokens: list[Token]) -> None:
        for tok in tokens:
            if tok.type == "inline" and tok.children:
                walk(tok.children)
            elif tok.type in ("text", "code_inline"):
                parts.append(tok.content)
            elif tok.type in ("softbreak", "hardbreak"):
                parts.append(" ")
            elif tok.type in ("code_block", "fence"):
                # Block-level code keeps its raw content but as plain text.
                parts.append(tok.content)
                parts.append(" ")
            elif tok.type in _BLOCK_BOUNDARY_TOKENS:
                parts.append(" ")

    walk(_MD_SNIPPET.parse(text))
    return " ".join("".join(parts).split())


def _hydrate_chunk_hits(
    conn: sqlite3.Connection,
    query: str,
    article_ids: list[str],
    top_per_article: int = 3,
) -> dict[str, dict[str, Any]]:
    """For each article_id, return chunk-level hit count + top matched chunks.

    Output: ``{article_id: {"chunk_hits": int, "matched_chunks": [{
        chunk_index, chunk_text, snippet, score
    }]}}``.

    chunk_hits = total chunks in that article matching the query (multi-hit
    count). matched_chunks = top-K (by BM25) ready for deep-link rendering.
    """
    if not article_ids:
        return {}
    # Column-restrict the chunk-level MATCH to chunk_text so title-only matches
    # do not count toward chunk_hits (v0.2 L1 fix).
    fts_query = build_fts_query(query, column="chunk_text")
    if not fts_query:
        return {}
    placeholders = ",".join("?" for _ in article_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT article_id, chunk_index, chunk_text_raw, bm25(chunks_fts) AS rs
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
              AND article_id IN ({placeholders})
            ORDER BY rs ASC
            """,
            (fts_query, *article_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        aid, chunk_index, chunk_text_raw, rs = r
        info = grouped.setdefault(aid, {"chunk_hits": 0, "matched_chunks": []})
        info["chunk_hits"] += 1
        if len(info["matched_chunks"]) < top_per_article:
            plain = _strip_markdown_for_snippet(chunk_text_raw or "")
            info["matched_chunks"].append(
                {
                    "chunk_index": int(chunk_index),
                    "chunk_text": chunk_text_raw or "",
                    "snippet": make_snippet(plain, [query], window=160),
                    "score": float(rs),
                }
            )
    return grouped


def make_snippet(content: str, query_terms: list[str], window: int = 200) -> str:
    """Return content excerpt around the first matching term, with ``<mark>`` highlights.

    Output is HTML-safe: the excerpt is HTML-escaped first, then matching
    query terms are wrapped in ``<mark>`` (also escaped). Templates can render
    via ``|safe`` without an XSS surface from untrusted corpus content.
    """
    import html as _html

    if not content:
        return ""
    text = content
    lowered = text.lower()
    for term in query_terms:
        if not term:
            continue
        idx = lowered.find(term.lower())
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(text), idx + len(term) + window // 2)
            excerpt = _html.escape(text[start:end])
            for t in query_terms:
                if t:
                    et = _html.escape(t)
                    excerpt = excerpt.replace(et, f"<mark>{et}</mark>")
            return excerpt
    return _html.escape(text[:window])


class Searcher:
    """Hybrid retrieval entry point.

    Args:
        conn: Open SQLite connection (schema initialised + corpus ingested).
        embedder: Object exposing ``encode_batch`` (skip if alpha == 1.0).
        alpha: BM25 weight (0..1). Default 0.25 favours cosine.
        reranker: Optional :class:`CrossEncoderReranker`. None or
            ``enabled=False`` skips rerank.
        rewriter: Optional :class:`QueryRewriter`. None skips rewrite.
        candidate_pool: Top-K from each retriever before hybrid merge.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        embedder: EmbedderProtocol | None,
        *,
        alpha: float = DEFAULT_ALPHA,
        reranker: CrossEncoderReranker | None = None,
        rewriter: QueryRewriter | None = None,
        candidate_pool: int = 50,
    ) -> None:
        if not 0.0 <= alpha <= 1.0:
            raise ValueError(f"alpha must be in [0, 1]; got {alpha}")
        if alpha < 1.0 and embedder is None:
            raise ValueError("embedder is required when alpha < 1.0")
        self.conn = conn
        self.embedder = embedder
        self.alpha = alpha
        self.reranker = reranker
        self.rewriter = rewriter
        self.candidate_pool = candidate_pool

    def search(
        self,
        query: str,
        *,
        axis: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Run the full pipeline and return top-``limit`` results."""
        if not query.strip():
            return []

        effective_query = self.rewriter.rewrite(query) if self.rewriter else query

        bm25 = (
            bm25_search(self.conn, effective_query, axis=axis, limit=self.candidate_pool)
            if self.alpha > 0
            else []
        )

        vector: list[dict[str, Any]] = []
        if self.alpha < 1.0:
            if self.embedder is None:
                raise SearchError("embedder missing for vector branch")
            qv = self.embedder.encode_batch([effective_query])[0]
            vector = vector_search(self.conn, qv, axis=axis, limit=self.candidate_pool)

        merged = hybrid_combine(bm25, vector, alpha=self.alpha, limit=self.candidate_pool)

        # Hydrate metadata for entries that came only from the vector branch
        missing_meta = [m for m in merged if "title" not in m]
        if missing_meta:
            ids = [m["article_id"] for m in missing_meta]
            placeholders = ",".join("?" for _ in ids)
            meta_rows = self.conn.execute(
                f"""
                SELECT article_id, title, source_type, category, pub_date, pub_year
                FROM articles_meta WHERE article_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
            meta_map = {r[0]: r for r in meta_rows}
            for m in missing_meta:
                row = meta_map.get(m["article_id"])
                if row is not None:
                    m["title"] = row[1]
                    m["source_type"] = row[2]
                    m["category"] = row[3]
                    m["pub_date"] = row[4]
                    m["pub_year"] = row[5]

        if self.reranker is not None and self.reranker.enabled:
            merged = self.reranker.score(effective_query, merged)
            merged.sort(key=lambda d: -d.get("rerank_score", 0.0))

        # Multi-hit count + matched chunks per article (for deep-link rendering)
        chunk_data = _hydrate_chunk_hits(
            self.conn,
            effective_query,
            [m["article_id"] for m in merged[:limit]],
        )
        for m in merged[:limit]:
            info = chunk_data.get(m["article_id"], {})
            m["chunk_hits"] = info.get("chunk_hits", 0)
            m["matched_chunks"] = info.get("matched_chunks", [])

        for r in merged:
            content_raw = r.get("content_raw") or ""
            r["content_snippet"] = make_snippet(content_raw, [effective_query])

        return merged[:limit]


__all__ = [
    "Searcher",
    "EmbedderProtocol",
    "bm25_search",
    "vector_search",
    "hybrid_combine",
    "make_snippet",
    "DEFAULT_ALPHA",
    "CrossEncoderReranker",
    "QueryRewriter",
]
