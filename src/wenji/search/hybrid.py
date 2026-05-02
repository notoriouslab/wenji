"""Linear-combination hybrid scoring for BM25 + vector results.

``hybrid_score = α × bm25_score + (1 − α) × cosine_score``

Articles found in only one source get 0 for the missing component. Default
``α = 0.25`` per logos v1.1 calibration (favours vector recall but BM25 still
breaks ties on exact-token matches).
"""

from __future__ import annotations

from typing import Any

DEFAULT_ALPHA = 0.25


def hybrid_combine(
    bm25_results: list[dict[str, Any]],
    vector_results: list[dict[str, Any]],
    *,
    alpha: float = DEFAULT_ALPHA,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Merge BM25 and vector results into a single ranked list.

    ``alpha`` is clipped to [0, 1]. Output dicts contain at minimum
    ``article_id``, ``bm25_score``, ``cosine_score``, ``hybrid_score``, plus any
    metadata fields present on the BM25 side (vector results carry article_id +
    cosine_score only).
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1]; got {alpha}")

    merged: dict[str, dict[str, Any]] = {}
    for r in bm25_results:
        aid = r["article_id"]
        merged[aid] = dict(r)
        merged[aid].setdefault("cosine_score", 0.0)
    for r in vector_results:
        aid = r["article_id"]
        if aid in merged:
            merged[aid]["cosine_score"] = r.get("cosine_score", 0.0)
        else:
            merged[aid] = {
                "article_id": aid,
                "bm25_score": 0.0,
                "cosine_score": r.get("cosine_score", 0.0),
            }

    for d in merged.values():
        d["hybrid_score"] = alpha * d.get("bm25_score", 0.0) + (1.0 - alpha) * d.get(
            "cosine_score", 0.0
        )

    return sorted(merged.values(), key=lambda d: -d["hybrid_score"])[:limit]
