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

import json
import sqlite3
from typing import Any, Protocol

import numpy as np
from markdown_it import MarkdownIt

from wenji.core.errors import SearchError
from wenji.search.bm25 import bm25_search, build_fts_query
from wenji.search.entity import EntityScorer, QueryEntity
from wenji.search.hybrid import DEFAULT_ALPHA, hybrid_combine
from wenji.search.intent import IntentClassifier
from wenji.search.ranker import RankerHook, apply_ranker_hooks
from wenji.search.rerank import CrossEncoderReranker
from wenji.search.rewrite import QueryRewriter
from wenji.search.rrf import chunk_bm25_search, rrf_merge
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
    """Strip markdown markers for clean search snippets via AST parse.

    Walks markdown-it-py tokens and emits plain text only. Replaces an
    earlier naive ``.replace('_', '')`` implementation that mangled URLs
    (``Foo_bar`` → ``Foobar``) and inline code spans
    (``code_with_underscore`` → ``codewithunderscore``) — underscores not
    used as emphasis markers must survive into the snippet.
    """
    if not text:
        return ""
    parts: list[str] = []
    for tok in _MD_SNIPPET.parse(text):
        if tok.type in _BLOCK_BOUNDARY_TOKENS:
            parts.append(" ")
            continue
        if tok.type in {"fence", "code_block"}:
            parts.append(tok.content)
            parts.append(" ")
            continue
        if tok.type != "inline" or not tok.children:
            continue
        for child in tok.children:
            if child.type in {"text", "code_inline"}:
                parts.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                parts.append(" ")
            # image / link_open / link_close / em_open / em_close /
            # strong_open / strong_close → skip the markers; their visible
            # text comes through as separate `text` children (or, for
            # images, is intentionally dropped, matching the previous
            # regex behaviour).
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
    """Hybrid retrieval + RRF + entity / intent / ranker pipeline (v0.3.6).

    Pipeline (see ``openspec/specs/wenji-search-engine/spec.md``):

    1. Optional LLM rewrite via ``rewriter``.
    2. Optional entity detection via ``entity_scorer``.
    3. Optional intent detection via ``intent_classifier``.
    4. Optional alias-based query expansion (when entities + scorer present).
    5. Article-level BM25 + vector retrieval, hybrid linearly combined
       (``alpha`` controls BM25/vector internal fusion, retained as fallback
       weight; primary sort is post-RRF).
    6. Chunk-level BM25 produces ``chunk_signals`` per-article roll-up.
    7. RRF merge with optional intent boost layer.
    8. Optional entity scoring + filter (alpha=entity_alpha).
    9. Optional ``RankerHook`` chain.
    10. Hydrate ``chunk_hits`` / ``matched_chunks``.

    Args:
        conn: Open SQLite connection (schema initialised + corpus ingested).
        embedder: Object exposing ``encode_batch`` (skip if alpha == 1.0).
        alpha: BM25 weight (0..1) for the linear hybrid_combine inside step 5.
            Default 0.25. Primary sort is RRF, not this α.
        reranker: Optional :class:`CrossEncoderReranker`. Reserved hook;
            v0.3.6 default still routes around RRF when reranker enabled.
        rewriter: Optional :class:`QueryRewriter`.
        candidate_pool: Top-K from each retriever before hybrid merge / RRF.
        entity_scorer: Optional :class:`EntityScorer` enabling steps 2/4/8.
        intent_classifier: Optional :class:`IntentClassifier` enabling step 3
            and the RRF intent-boost layer in step 7.
        ranker_hooks: Optional list of :class:`RankerHook` applied in order
            after entity scoring (step 9).
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
        entity_scorer: EntityScorer | None = None,
        intent_classifier: IntentClassifier | None = None,
        ranker_hooks: list[RankerHook] | None = None,
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
        self.entity_scorer = entity_scorer
        self.intent_classifier = intent_classifier
        self.ranker_hooks = ranker_hooks

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

        # Step 1: LLM rewrite (existing v0.3.2)
        effective_query = self.rewriter.rewrite(query) if self.rewriter else query

        # Step 2 + 3: entity detection + intent detection
        query_entities: list[QueryEntity] = []
        if self.entity_scorer is not None:
            query_entities = self.entity_scorer.detect_query_entities(effective_query)

        intent: str | None = None
        boost_types: set[str] | None = None
        if self.intent_classifier is not None:
            intent = self.intent_classifier.detect_intent(effective_query)
            boost_types = self.intent_classifier.get_boost_types(intent)

        # Step 4: alias-based query expansion (only if entities + scorer)
        retrieve_query = (
            self.entity_scorer.expand_query_with_aliases(effective_query, query_entities)
            if (self.entity_scorer is not None and query_entities)
            else effective_query
        )

        # Step 5: article-level BM25 + vector + hybrid linear combine
        bm25 = (
            bm25_search(self.conn, retrieve_query, axis=axis, limit=self.candidate_pool)
            if self.alpha > 0
            else []
        )
        vector: list[dict[str, Any]] = []
        if self.alpha < 1.0:
            if self.embedder is None:
                raise SearchError("embedder missing for vector branch")
            qv = self.embedder.encode_batch([retrieve_query])[0]
            vector = vector_search(self.conn, qv, axis=axis, limit=self.candidate_pool)

        merged = hybrid_combine(bm25, vector, alpha=self.alpha, limit=self.candidate_pool)

        # Hydrate metadata for entries that came only from the vector branch
        missing_meta = [m for m in merged if "title" not in m]
        if missing_meta:
            ids = [m["article_id"] for m in missing_meta]
            placeholders = ",".join("?" for _ in ids)
            meta_rows = self.conn.execute(
                f"""
                SELECT article_id, title, source_type, category, pub_date, pub_year, tags
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
                    m["tags"] = json.loads(row[6]) if row[6] else []

        # Step 6: chunk-level BM25 → article roll-up
        chunk_signals = chunk_bm25_search(self.conn, retrieve_query, limit=self.candidate_pool)

        # Seed the dict needed by rrf_merge (keyed by article_id with _rankingScore)
        main_merged: dict[str, dict[str, Any]] = {}
        for art in merged:
            art["_rankingScore"] = float(art.get("hybrid_score", 0.0))
            main_merged[art["article_id"]] = art

        # Step 7: RRF merge (with optional intent boost)
        ranked = rrf_merge(
            main_merged,
            chunk_signals,
            intent_boost_types=boost_types,
            limit=self.candidate_pool,
        )

        # Step 8: entity scoring + hard filter
        if self.entity_scorer is not None and query_entities:
            ranked, _ = self.entity_scorer.score_and_rerank(
                ranked, effective_query, query_entities=query_entities
            )

        # Step 9: ranker hook chain
        if self.ranker_hooks:
            ranked = apply_ranker_hooks(
                ranked,
                effective_query,
                self.ranker_hooks,
                context={"intent": intent, "query_entities": query_entities},
            )
            ranked.sort(key=lambda r: -float(r.get("_rankingScore", 0.0)), reverse=False)

        # Optional cross-encoder reranker (existing hook, retained but unused
        # in v0.3.6 baseline — see proposal Non-Goals: blog verified
        # ARM CPU latency unacceptable).
        if self.reranker is not None and self.reranker.enabled:
            ranked = self.reranker.score(effective_query, ranked)
            ranked.sort(key=lambda d: -d.get("rerank_score", 0.0))

        # Step 10: hydrate chunk_hits + matched_chunks for top-N
        top_n = ranked[:limit]
        chunk_data = _hydrate_chunk_hits(
            self.conn,
            effective_query,
            [m["article_id"] for m in top_n],
        )
        for m in top_n:
            info = chunk_data.get(m["article_id"], {})
            m["chunk_hits"] = info.get("chunk_hits", 0)
            m["matched_chunks"] = info.get("matched_chunks", [])

        # Step 11: hydrate content_full for top_n from articles_fts.
        # Vector-only hits (no BM25 match) reach hybrid_combine without
        # content_raw, so without this step downstream consumers (eval
        # metric, UI snippet) see empty content for those entries. One
        # batch query keeps cost O(top_n). content_full is truncated to 500
        # characters to match the upstream R13 baseline shape that metrics.py
        # was ported against.
        if top_n:
            ids = [m["article_id"] for m in top_n]
            placeholders = ",".join("?" for _ in ids)
            rows = self.conn.execute(
                f"SELECT article_id, content_raw, tags_raw FROM articles_fts "
                f"WHERE article_id IN ({placeholders})",
                ids,
            ).fetchall()
            content_map = {row[0]: (row[1] or "", row[2]) for row in rows}
            for r in top_n:
                cr, tr = content_map.get(r["article_id"], ("", None))
                r["content_full"] = cr[:500]
                r["content_snippet"] = make_snippet(cr, [effective_query])
                if "tags" not in r:
                    try:
                        r["tags"] = json.loads(tr) if tr and tr.strip() else []
                    except (json.JSONDecodeError, ValueError):
                        r["tags"] = []

        return top_n


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
