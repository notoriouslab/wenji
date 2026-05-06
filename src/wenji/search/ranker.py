"""RankerHook protocol + built-in implementations for v0.3.6 ranker pipeline.

Hooks are applied after entity scoring; each hook returns an additive
boost to ``_rankingScore``. Custom hooks may implement ``RankerHook``
duck-style (any object with a callable ``boost`` method satisfying the
signature works thanks to ``typing.Protocol``).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RankerHook(Protocol):
    """Protocol for additive ranking boosters applied after entity scoring."""

    def boost(self, article: dict[str, Any], query: str, context: dict[str, Any]) -> float:
        """Return the additive boost to apply to article['_rankingScore']."""
        ...


class ChunkHitBooster:
    """Boost articles by chunk_hits (number of matching chunks per article).

    Reads ``article['chunk_hits']`` (populated by
    ``wenji.search.__init__._hydrate_chunk_hits`` after RRF + entity
    scoring). The score is capped at ``max_hits_capped`` to avoid runaway
    boosts when an article has dozens of trivial keyword hits.
    """

    def __init__(self, weight: float = 0.05, max_hits_capped: int = 5) -> None:
        self.weight = weight
        self.max_hits_capped = max_hits_capped

    def boost(self, article: dict[str, Any], query: str, context: dict[str, Any]) -> float:
        hits = int(article.get("chunk_hits", 0) or 0)
        return self.weight * min(hits, self.max_hits_capped)


def apply_ranker_hooks(
    articles: list[dict[str, Any]],
    query: str,
    hooks: list[RankerHook] | None,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply each hook in order, additively to each article's _rankingScore.

    Mutates and returns the same article list. Empty / None ``hooks`` is a
    no-op. Articles with no ``_rankingScore`` are treated as starting at
    0.0 (matches RRF / entity-scoring behaviour).
    """
    if not hooks:
        return articles
    ctx = context or {}
    for art in articles:
        base = float(art.get("_rankingScore", 0.0) or 0.0)
        added = 0.0
        for hook in hooks:
            added += float(hook.boost(art, query, ctx))
        art["_rankingScore"] = base + added
    return articles
