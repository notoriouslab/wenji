"""Reciprocal Rank Fusion + chunk-level BM25 retrieval for wenji v0.3.6.

Ports an upstream RAG ``rrf_merge`` (production v1.1 ranker, 75.8%
baseline). Two-way boost-style RRF: ``main_merged`` is the
hybrid (BM25 + vector cosine) result keyed by article_id with a
``_rankingScore`` field; ``chunk_signals`` is a per-article best-chunk
BM25 dict. An optional intent-boost layer adds a constant per-article
contribution when ``source_type`` is in the boost set.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from wenji.search.bm25 import build_fts_query

DEFAULT_RRF_K = 60


def rrf_merge(
    main_merged: dict[str, dict[str, Any]],
    chunk_signals: dict[str, float],
    intent_boost_types: set[str] | None = None,
    k: int = DEFAULT_RRF_K,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Merge main + chunk rankings via RRF; apply optional intent boost.

    Args:
        main_merged: dict mapping article_id to article dict; each value MUST
            contain ``_rankingScore`` (the upstream hybrid score).
        chunk_signals: dict mapping article_id to chunk-level BM25 best score.
            Empty dict triggers a fallback (main-only sort + 0.15 intent
            additive boost) per the upstream behaviour.
        intent_boost_types: set of source_type strings whose articles receive
            an additional ``1/(k+1)`` (when chunks present) or ``0.15``
            (chunks absent) per the upstream behaviour. None or empty = no boost.
        k: RRF constant (default 60, the upstream default).
        limit: if provided, truncate the returned list to this length;
            otherwise return all merged articles.

    Returns:
        list[dict]: articles sorted by RRF score descending, each with
        ``_rankingScore`` overwritten by the post-RRF score.
    """
    if chunk_signals:
        main_ranked = sorted(
            main_merged.keys(),
            key=lambda x: main_merged[x].get("_rankingScore", 0.0),
            reverse=True,
        )
        main_rank = {aid: i + 1 for i, aid in enumerate(main_ranked)}

        chunk_ranked = sorted(chunk_signals.keys(), key=lambda x: chunk_signals[x], reverse=True)
        chunk_rank = {aid: i + 1 for i, aid in enumerate(chunk_ranked)}

        all_ids = set(main_merged.keys()) | set(chunk_signals.keys())
        rrf_scores: dict[str, float] = {
            aid: (1.0 / (k + main_rank[aid]) if aid in main_rank else 0.0)
            + (1.0 / (k + chunk_rank[aid]) if aid in chunk_rank else 0.0)
            for aid in all_ids
        }

        if intent_boost_types:
            boost = 1.0 / (k + 1)
            for aid in all_ids:
                art = main_merged.get(aid)
                if art and art.get("source_type") in intent_boost_types:
                    rrf_scores[aid] += boost

        sorted_ids = sorted(all_ids, key=lambda x: rrf_scores[x], reverse=True)
        if limit is not None:
            sorted_ids = sorted_ids[:limit]

        articles: list[dict[str, Any]] = []
        for aid in sorted_ids:
            if aid in main_merged:
                hit = main_merged[aid]
                hit["_rankingScore"] = rrf_scores[aid]
                articles.append(hit)
        return articles

    if intent_boost_types:
        for art in main_merged.values():
            if art.get("source_type") in intent_boost_types:
                art["_rankingScore"] = art.get("_rankingScore", 0.0) + 0.15

    sorted_articles = sorted(
        main_merged.values(),
        key=lambda x: x.get("_rankingScore", 0.0),
        reverse=True,
    )
    return sorted_articles[:limit] if limit is not None else sorted_articles


def chunk_bm25_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 50,
    over_fetch_factor: int = 5,
) -> dict[str, float]:
    """Run chunk-level BM25 against ``chunks_fts`` and roll up to article level.

    SQLite FTS5 does not support ``MIN(bm25(...))`` as an aggregate (verified
    during v0.3.4 RRF spike); we over-fetch raw chunks and aggregate per
    article in Python, keeping the best (most negative = most relevant)
    bm25 score per article.

    Args:
        conn: SQLite connection with chunks_fts available.
        query: query string; tokenised via ``build_fts_query`` against the
            ``chunk_text`` column (matches the production Searcher path).
        limit: maximum number of articles to return.
        over_fetch_factor: multiplier applied to ``limit`` when fetching raw
            chunks before per-article dedup.

    Returns:
        dict[str, float]: at most ``limit`` entries keyed by article_id with
        the best raw bm25 score per article. Empty dict on no FTS5 matches.
    """
    if not query.strip():
        return {}
    fts_query = build_fts_query(query, column="chunk_text")
    if not fts_query:
        return {}

    raw_limit = max(limit * over_fetch_factor, limit)
    try:
        rows = conn.execute(
            "SELECT article_id, bm25(chunks_fts) AS rs "
            "FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY rs ASC LIMIT ?",
            (fts_query, raw_limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    best: dict[str, float] = {}
    for aid, rs in rows:
        if aid not in best or rs < best[aid]:
            best[aid] = float(rs)
    if len(best) <= limit:
        return best
    sorted_pairs = sorted(best.items(), key=lambda x: x[1])[:limit]
    return dict(sorted_pairs)
