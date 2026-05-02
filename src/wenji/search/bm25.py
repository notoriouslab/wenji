"""BM25 retrieval against articles_fts (FTS5).

Tokenises the query with jieba (matching ingest-side pre-tokenisation) and
runs FTS5 ``MATCH``. ``bm25()`` returns negative numbers (more negative =
more relevant); we normalise to ``[0, 1]`` so hybrid combination works.

Excludes ``category = 'excluded'`` by default. Optional ``axis`` filter joins
``article_axes``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from wenji.core.errors import SearchError


def build_fts_query(raw: str) -> str:
    """Build FTS5 phrase MATCH query from user input.

    The query and indexed content both use char-level + space-join (see
    :func:`wenji.ingest.jieba_setup.tokenize_for_fts`). Each whitespace-
    separated user term is char-level expanded (``"因信稱義"`` → ``"因 信 稱 義"``)
    and wrapped in phrase quotes so FTS5 ``unicode61`` tokenises the phrase to
    individual char tokens and requires they appear consecutively in the
    indexed content. Multi-term inputs become AND-combined phrases.

    This avoids both char-AND false matches (which char-only without phrase
    suffers from) and jieba segmentation drift between query and ingest time.
    """
    quoted: list[str] = []
    for term in raw.split():
        chars = " ".join(c for c in term if not c.isspace() and c != '"')
        if chars:
            quoted.append(f'"{chars}"')
    return " ".join(quoted)


def _normalise_bm25_scores(rows: list[tuple[Any, ...]]) -> list[float]:
    """Map raw bm25 (negative) values to ``[0, 1]`` rank-preserving scores."""
    if not rows:
        return []
    raws = [r[1] for r in rows]
    min_raw = min(raws)
    if min_raw == 0:
        return [0.0] * len(raws)
    factor = abs(min_raw)
    return [abs(raw) / factor for raw in raws]


def bm25_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    axis: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Run FTS5 BM25 against articles_fts.

    Returns list of dicts with article_id, bm25_raw, bm25_score (normalised
    [0,1]), title, source_type, category, pub_date, pub_year, content_raw,
    tags_raw.
    """
    if not query.strip():
        return []

    fts_query = build_fts_query(query)
    if not fts_query:
        return []

    sql_parts = [
        """
        SELECT
            f.article_id,
            bm25(articles_fts) AS bm25_raw,
            f.title_raw,
            f.source_type,
            f.category,
            f.pub_date,
            f.pub_year,
            f.content_raw,
            f.tags_raw
        FROM articles_fts f
        LEFT JOIN articles_meta m ON m.article_id = f.article_id
        WHERE articles_fts MATCH ?
          AND IFNULL(m.category, '') != 'excluded'
        """
    ]
    params: list[Any] = [fts_query]
    if axis is not None:
        sql_parts.append(
            " AND f.article_id IN (SELECT article_id FROM article_axes WHERE axis_id = ?)"
        )
        params.append(axis)
    sql_parts.append(" ORDER BY bm25_raw ASC LIMIT ?")
    params.append(limit)

    try:
        rows = conn.execute("".join(sql_parts), params).fetchall()
    except sqlite3.OperationalError as exc:
        raise SearchError(f"FTS5 query failed: {exc}") from exc

    norms = _normalise_bm25_scores(rows)
    return [
        {
            "article_id": r[0],
            "bm25_raw": r[1],
            "bm25_score": norm,
            "title": r[2],
            "source_type": r[3],
            "category": r[4],
            "pub_date": r[5],
            "pub_year": r[6],
            "content_raw": r[7],
            "tags_raw": r[8],
        }
        for r, norm in zip(rows, norms, strict=True)
    ]
