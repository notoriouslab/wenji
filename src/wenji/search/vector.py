"""Vector cosine retrieval against doc_vectors.

doc_vectors stores L2-normalised float32 1024d vectors. We load the candidate
set into a numpy matrix and compute dot product (= cosine because both sides
are L2-normalised).

For corpus sizes ≤ ~200K this is sub-second; v0.2 may add ANN if needed.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from wenji.core.errors import SearchError

VECTOR_DIM = 1024


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

    article_ids, matrix = _load_candidates(conn, axis)
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
