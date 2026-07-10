"""Vector cosine retrieval against doc_vectors.

doc_vectors stores L2-normalised float32 1024d vectors. We load the candidate
set into a numpy matrix and compute dot product (= cosine because both sides
are L2-normalised).

The candidate matrix is memoized per (db file, axis): rebuilding a
(12k, 1024) matrix from 12k ``np.frombuffer`` calls on every query was the
largest corpus-size-proportional latency term. All entries for a db share
one corpus fingerprint (``COUNT(*)`` + ``MAX(indexed_at)`` of
``articles_meta``); any change invalidates every axis entry (accepted
over-invalidation — corpus changes are rare relative to queries).
In-memory databases are never cached (each ``:memory:`` conn is a distinct
db; tests rely on exact per-conn state).

For corpus sizes ≤ ~200K this is sub-second; v0.2 may add ANN if needed.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from wenji.core.errors import SearchError

VECTOR_DIM = 1024

# (db_file_path) → {"fingerprint": tuple, "entries": {axis_key: (ids, matrix)}}
_CANDIDATE_CACHE: dict[str, dict[str, Any]] = {}


def clear_candidate_cache() -> None:
    """Drop all memoized candidate matrices (test/maintenance hook)."""
    _CANDIDATE_CACHE.clear()


def _db_file(conn: sqlite3.Connection) -> str:
    """Main database file path, or '' for in-memory/temporary databases."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return row[2] or ""


def _corpus_fingerprint(conn: sqlite3.Connection) -> tuple[int, str]:
    n, max_indexed = conn.execute(
        "SELECT COUNT(*), IFNULL(MAX(indexed_at), '') FROM articles_meta"
    ).fetchone()
    return (n, max_indexed)


def _load_candidates_cached(
    conn: sqlite3.Connection,
    axis: str | None,
) -> tuple[list[str], np.ndarray]:
    db_file = _db_file(conn)
    if not db_file:
        return _load_candidates(conn, axis)

    fingerprint = _corpus_fingerprint(conn)
    cached = _CANDIDATE_CACHE.get(db_file)
    if cached is None or cached["fingerprint"] != fingerprint:
        cached = {"fingerprint": fingerprint, "entries": {}}
        _CANDIDATE_CACHE[db_file] = cached
    axis_key = axis if axis is not None else ""
    if axis_key not in cached["entries"]:
        cached["entries"][axis_key] = _load_candidates(conn, axis)
    return cached["entries"][axis_key]


def _load_candidates(
    conn: sqlite3.Connection,
    axis: str | None,
) -> tuple[list[str], np.ndarray]:
    if axis is None:
        rows = conn.execute(
            """
            SELECT v.article_id, v.vec
            FROM doc_vectors v
            LEFT JOIN articles_meta m ON v.article_id = m.article_id
            WHERE IFNULL(m.category, '') != 'excluded'
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT v.article_id, v.vec
            FROM doc_vectors v
            JOIN article_axes a ON v.article_id = a.article_id
            LEFT JOIN articles_meta m ON v.article_id = m.article_id
            WHERE a.axis_id = ?
              AND IFNULL(m.category, '') != 'excluded'
            """,
            (axis,),
        ).fetchall()

    if not rows:
        return [], np.zeros((0, VECTOR_DIM), dtype=np.float32)

    article_ids = [r[0] for r in rows]
    matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    if matrix.shape[1] != VECTOR_DIM:
        raise SearchError(f"doc_vectors dim mismatch: expected {VECTOR_DIM}, got {matrix.shape[1]}")
    return article_ids, matrix


def vector_search(
    conn: sqlite3.Connection,
    query_vec: np.ndarray,
    *,
    axis: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Cosine ranking against doc_vectors.

    ``query_vec`` is expected shape ``(VECTOR_DIM,)``. It will be L2-normalised
    here so callers don't need to.
    """
    if query_vec.shape != (VECTOR_DIM,):
        raise SearchError(f"query_vec shape {query_vec.shape}; expected ({VECTOR_DIM},)")
    qv = query_vec.astype(np.float32, copy=False)
    qv_norm = float(np.linalg.norm(qv)) or 1.0
    qv = qv / qv_norm

    article_ids, matrix = _load_candidates_cached(conn, axis)
    if not article_ids:
        return []

    sims = matrix @ qv
    if limit >= len(sims):
        order = np.argsort(-sims)
    else:
        # argpartition for top-k, then sort the slice
        partial = np.argpartition(-sims, limit - 1)[:limit]
        order = partial[np.argsort(-sims[partial])]

    return [{"article_id": article_ids[int(i)], "cosine_score": float(sims[int(i)])} for i in order]
