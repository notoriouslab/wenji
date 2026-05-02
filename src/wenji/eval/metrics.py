"""Per-question and aggregate metric computation.

Three predicate families (per-question):

- ``kw1``: at least one expected keyword present in title + content
- ``kw3``: at least :data:`DEFAULT_MIN_HITS` keywords present (the canonical
  "auto-pass" predicate)
- ``fuzzy``: title fuzzy match against any ``expected_article_hint``
- ``pass``: ``kw3 OR fuzzy`` (final composite — same as ``auto_pass``)

For each predicate we emit ``rank_*`` / ``hit1_*`` / ``hit3_*`` / ``hit5_*`` /
``rr_*``. Aggregate report includes ``pass_count``, ``pass_rate_pct``, hit@k,
MRR@5, ``elapsed_*``, and ``by_category`` / ``by_source`` breakdowns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

DEFAULT_MIN_HITS = 3
DEFAULT_FUZZY_THRESHOLD = 0.6
DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class _Predicate:
    name: str
    test: Any  # callable[[dict], bool]


def count_keyword_hits(text: str, keywords: Sequence[str]) -> tuple[int, list[str]]:
    """Return (hit count, list of matched keywords)."""
    hits: list[str] = []
    for kw in keywords:
        if kw and kw in text:
            hits.append(kw)
    return len(hits), hits


def title_fuzzy_match(
    title: str, hints: Sequence[str], threshold: float = DEFAULT_FUZZY_THRESHOLD
) -> tuple[bool, str]:
    """Return (matched?, matching hint or empty string)."""
    if not title or not hints:
        return False, ""
    for hint in hints:
        if not hint:
            continue
        if SequenceMatcher(None, title, hint).ratio() >= threshold:
            return True, hint
    return False, ""


def evaluate_question(
    candidate,
    response: dict,
    *,
    min_hits: int = DEFAULT_MIN_HITS,
    top_k: int = DEFAULT_TOP_K,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> dict:
    """Compute per-question metrics from one candidate + one search response.

    ``response`` is a dict with ``results`` list-of-dicts containing at least
    ``article_id``, ``title``, ``content_raw`` (or ``content``).
    """
    keywords = candidate.expected_keywords
    hints = candidate.expected_article_hints

    raw_results = response.get("results", [])[:top_k]
    article_results: list[dict] = []
    max_hits = 0
    for art in raw_results:
        title = art.get("title") or ""
        content = art.get("content_raw") or art.get("content") or ""
        text = f"{title}\n{content}"
        hit_n, matched = count_keyword_hits(text, keywords)
        fuzzy_ok, matched_hint = title_fuzzy_match(title, hints, fuzzy_threshold)
        article_results.append(
            {
                "article_id": art.get("article_id"),
                "title": title[:120],
                "source_type": art.get("source_type"),
                "hybrid_score": round(art.get("hybrid_score", 0.0) or 0.0, 4),
                "keyword_hits": hit_n,
                "matched_keywords": matched,
                "fuzzy_match": matched_hint if fuzzy_ok else None,
            }
        )
        max_hits = max(max_hits, hit_n)

    auto_pass = any(
        (r["keyword_hits"] >= min_hits) or (r["fuzzy_match"] is not None) for r in article_results
    )

    def _first_rank(predicate) -> int | None:
        for i, r in enumerate(article_results):
            if predicate(r):
                return i + 1
        return None

    predicates = [
        _Predicate("kw1", lambda r: r["keyword_hits"] >= 1),
        _Predicate("kw3", lambda r: r["keyword_hits"] >= min_hits),
        _Predicate("fuzzy", lambda r: r["fuzzy_match"] is not None),
        _Predicate(
            "pass",
            lambda r: (r["keyword_hits"] >= min_hits) or (r["fuzzy_match"] is not None),
        ),
    ]

    metrics: dict[str, Any] = {}
    for p in predicates:
        rank = _first_rank(p.test)
        metrics[f"rank_{p.name}"] = rank
        metrics[f"hit1_{p.name}"] = 1 if rank == 1 else 0
        metrics[f"hit3_{p.name}"] = 1 if rank and rank <= 3 else 0
        metrics[f"hit5_{p.name}"] = 1 if rank else 0
        metrics[f"rr_{p.name}"] = (1.0 / rank) if rank else 0.0

    return {
        "id": candidate.id,
        "query": candidate.query,
        "category": candidate.category,
        "source": candidate.source,
        "auto_pass": auto_pass,
        "max_keyword_hits": max_hits,
        "n_keywords": len(keywords),
        "top_k_results": [r["article_id"] for r in article_results],
        "article_results": article_results,
        "elapsed_ms": response.get("elapsed_ms"),
        **metrics,
    }


def aggregate(per_question: list[dict], *, top_k: int = DEFAULT_TOP_K) -> dict:
    """Compute summary metrics over a list of per-question dicts."""
    total = len(per_question)
    if total == 0:
        return {
            "total": 0,
            "pass_count": 0,
            "pass_rate_pct": 0.0,
            "elapsed_total_s": 0.0,
            "elapsed_avg_ms": 0.0,
            "by_predicate": {},
            "by_category": {},
            "by_source": {},
        }

    pass_count = sum(1 for r in per_question if r["auto_pass"])
    elapsed_ms_total = sum(int(r.get("elapsed_ms") or 0) for r in per_question)

    by_predicate: dict[str, dict[str, Any]] = {}
    for name in ("kw1", "kw3", "fuzzy", "pass"):
        h1 = sum(r.get(f"hit1_{name}", 0) for r in per_question)
        h3 = sum(r.get(f"hit3_{name}", 0) for r in per_question)
        h5 = sum(r.get(f"hit5_{name}", 0) for r in per_question)
        mrr = sum(r.get(f"rr_{name}", 0.0) for r in per_question) / total
        by_predicate[name] = {
            "hit1": h1,
            "hit1_pct": round(h1 / total * 100, 2),
            "hit3": h3,
            "hit3_pct": round(h3 / total * 100, 2),
            "hit5": h5,
            "hit5_pct": round(h5 / total * 100, 2),
            "mrr_at_5": round(mrr, 4),
        }

    by_category: dict[str, dict[str, int]] = {}
    by_source: dict[str, dict[str, int]] = {}
    for r in per_question:
        cat = r.get("category", "") or "(uncategorised)"
        src = r.get("source", "") or "(unsourced)"
        bc = by_category.setdefault(cat, {"total": 0, "pass": 0})
        bs = by_source.setdefault(src, {"total": 0, "pass": 0})
        bc["total"] += 1
        bs["total"] += 1
        if r["auto_pass"]:
            bc["pass"] += 1
            bs["pass"] += 1

    return {
        "total": total,
        "pass_count": pass_count,
        "pass_rate_pct": round(pass_count / total * 100, 2),
        "elapsed_total_s": round(elapsed_ms_total / 1000.0, 2),
        "elapsed_avg_ms": round(elapsed_ms_total / total, 2),
        "top_k": top_k,
        "by_predicate": by_predicate,
        "by_category": by_category,
        "by_source": by_source,
    }
