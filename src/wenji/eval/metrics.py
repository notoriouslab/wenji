"""Per-question and aggregate metric computation (multi-path schema, v0.3.1).

Three-level gold_path_match scoring per (hit, path):

- ``none``: zero keywords from the path matched in the article content
- ``partial``: at least one but not all keywords matched
- ``full``: all keywords matched

Question-level ``pass`` is True iff at least one ``gold_path`` achieves
``full`` match on at least one hit within top-K. Matching operates on
**article-level content** post chunk-to-article rollup (union of retrieved
chunks belonging to the article; the original article body in DB is NOT
read — the baseline measures retrieval-returned content).
"""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

DEFAULT_TOP_K = 20
DEFAULT_FUZZY_THRESHOLD = 0.6


def count_keyword_hits(text: str, keywords: Sequence[str]) -> tuple[int, list[str]]:
    """Return (hit count, list of matched keywords). Case-insensitive substring."""
    if not keywords:
        return 0, []
    text_lower = text.lower()
    hits: list[str] = []
    for kw in keywords:
        if kw and kw.lower() in text_lower:
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


def score_gold_path(article_content: str, gold_path) -> str:
    """Score a gold_path against article-level content.

    Returns one of ``"none"`` / ``"partial"`` / ``"full"``.

    - ``full``: all keywords in ``gold_path.keywords`` matched
    - ``partial``: at least one but not all keywords matched
    - ``none``: zero keywords matched

    Matching is case-insensitive substring (identical to logos benchmark v2
    metric definition). ``article_content`` is expected to be the union of
    retrieved chunks belonging to the article (see
    :func:`rollup_chunks_to_articles`), not the full article body from DB.
    """
    keywords = gold_path.keywords
    if not keywords:
        return "none"
    n, _ = count_keyword_hits(article_content, keywords)
    if n == 0:
        return "none"
    if n >= len(keywords):
        return "full"
    return "partial"


def rollup_chunks_to_articles(hits: list[dict], *, top_k: int | None = None) -> list[dict]:
    """Aggregate chunk-level retrieval results into article-level entries.

    For each unique ``article_id``:
    1. Select the chunk with the highest score as canonical (its rank survives).
    2. Concatenate content of all retrieved chunks belonging to that article
       as ``article_content_union`` (NOT the full article body from DB).
    3. Preserve the canonical chunk's rank.

    Order in the returned list matches the canonical rank ascending. Optional
    ``top_k`` caps the number of returned article-level entries.

    Each input hit dict SHALL have: ``article_id``, ``rank`` (1-indexed),
    ``score``, and one of ``content_full`` / ``content_raw`` / ``content``.
    Optional: ``title``, ``content_hash``, ``source_type``.
    """
    by_article: dict[str, dict] = {}
    for h in hits:
        aid = h.get("article_id")
        if aid is None:
            continue
        score = float(h.get("score") or h.get("hybrid_score") or 0.0)
        rank = int(h.get("rank") or 0)
        content = h.get("content_full") or h.get("content_raw") or h.get("content") or ""
        entry = by_article.get(aid)
        if entry is None:
            by_article[aid] = {
                "article_id": aid,
                "rank": rank,
                "score": score,
                "title": h.get("title") or "",
                "content_hash": h.get("content_hash") or "",
                "source_type": h.get("source_type") or "",
                "article_content_union": content,
                "_chunk_count": 1,
            }
        else:
            entry["_chunk_count"] += 1
            entry["article_content_union"] = entry["article_content_union"] + "\n" + content
            if score > entry["score"]:
                entry["score"] = score
                entry["rank"] = rank
                # keep canonical metadata from highest-scoring chunk
                if h.get("title"):
                    entry["title"] = h["title"]
                if h.get("content_hash"):
                    entry["content_hash"] = h["content_hash"]

    rolled = sorted(by_article.values(), key=lambda r: r["rank"] if r["rank"] > 0 else 1_000_000)
    if top_k is not None:
        rolled = rolled[:top_k]
    return rolled


def evaluate_question(
    candidate,
    response: dict,
    *,
    top_k: int = DEFAULT_TOP_K,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> dict:
    """Compute per-question multi-path metrics.

    ``response`` SHALL have a ``results`` list of chunk-level hits. The
    function rolls them up to article-level entries, scores each gold_path
    against the article_content_union, and returns:

    - ``pass``: True iff ≥1 gold_path achieves ``full`` on ≥1 article in top-K
    - ``passing_paths``: list of path_tag values that achieved ``full``
    - per-hit ``gold_path_match``: dict ``{path_tag: "none" | "partial" | "full"}``
    - per-path ``rank_*`` / ``hit1_*`` / ``hit3_*`` / ``hit5_*`` / ``rr_*``
    """
    raw = response.get("results", [])
    # Ensure each raw hit has a rank (1-indexed by position if not provided).
    raw_with_rank = []
    for i, h in enumerate(raw, start=1):
        h2 = dict(h)
        h2.setdefault("rank", i)
        raw_with_rank.append(h2)

    rolled = rollup_chunks_to_articles(raw_with_rank, top_k=top_k)

    article_results: list[dict] = []
    for art in rolled:
        title = art.get("title") or ""
        content_union = art.get("article_content_union") or ""
        per_path: dict[str, str] = {}
        for path in candidate.gold_paths:
            per_path[path.path_tag] = score_gold_path(content_union, path)
        # Title fuzzy match against any path's article_hints (informational; not
        # used for pass determination).
        all_hints = tuple(h for path in candidate.gold_paths for h in path.article_hints)
        fuzzy_ok, matched_hint = title_fuzzy_match(title, all_hints, fuzzy_threshold)
        article_results.append(
            {
                "article_id": art["article_id"],
                "content_hash": art.get("content_hash", ""),
                "rank": art["rank"],
                "title": title[:120],
                "score": round(art["score"], 4),
                "source_type": art.get("source_type"),
                "gold_path_match": per_path,
                "fuzzy_match": matched_hint if fuzzy_ok else None,
            }
        )

    # Question-level pass: ≥1 path achieves "full" on ≥1 hit within top-K.
    passing_paths: list[str] = []
    for path in candidate.gold_paths:
        if any(r["gold_path_match"].get(path.path_tag) == "full" for r in article_results):
            passing_paths.append(path.path_tag)
    is_pass = len(passing_paths) > 0

    # Per-path rank/hit/rr metrics. rank_* is the rank of the first "full" match
    # for that path, or None if no "full" within top-K.
    path_metrics: dict[str, Any] = {}
    min_rank_full: int | None = None
    for path in candidate.gold_paths:
        rank: int | None = None
        for r in article_results:
            if r["gold_path_match"].get(path.path_tag) == "full":
                rank = r["rank"]
                break
        if rank is not None and (min_rank_full is None or rank < min_rank_full):
            min_rank_full = rank
        path_metrics[f"rank_{path.path_tag}"] = rank
        path_metrics[f"hit1_{path.path_tag}"] = 1 if rank == 1 else 0
        path_metrics[f"hit3_{path.path_tag}"] = 1 if rank and rank <= 3 else 0
        path_metrics[f"hit5_{path.path_tag}"] = 1 if rank and rank <= 5 else 0
        path_metrics[f"rr_{path.path_tag}"] = (1.0 / rank) if rank else 0.0

    rr_at_5 = (1.0 / min_rank_full) if (min_rank_full and min_rank_full <= 5) else 0.0

    # Partial credit: at least one path achieves "partial" but no path achieves "full".
    has_any_partial = any(
        any(r["gold_path_match"].get(p.path_tag) == "partial" for r in article_results)
        for p in candidate.gold_paths
    )
    partial_only = (not is_pass) and has_any_partial

    return {
        "id": candidate.id,
        "query": candidate.query,
        "category": candidate.category,
        "source": candidate.source,
        "pass": is_pass,
        "passing_paths": passing_paths,
        "partial_only": partial_only,
        "n_paths": len(candidate.gold_paths),
        "n_passing_paths": len(passing_paths),
        "top_k_results": [r["article_id"] for r in article_results],
        "article_results": article_results,
        "rr_at_5": rr_at_5,
        "elapsed_ms": response.get("elapsed_ms"),
        **path_metrics,
    }


def aggregate(per_question: list[dict], *, top_k: int = DEFAULT_TOP_K) -> dict:
    """Summarise per-question metrics for a multi-path eval run."""
    total = len(per_question)
    if total == 0:
        return {
            "total": 0,
            "pass_count": 0,
            "pass_rate_pct": 0.0,
            "partial_pass_count": 0,
            "mean_passing_path_count": 0.0,
            "mrr_at_5": 0.0,
            "elapsed_total_sec": 0.0,
            "by_category": {},
            "by_source": {},
            "top_k": top_k,
        }

    pass_count = sum(1 for r in per_question if r.get("pass"))
    partial_pass_count = sum(1 for r in per_question if r.get("partial_only"))
    mean_passing_paths = (
        sum(r.get("n_passing_paths", 0) for r in per_question if r.get("pass")) / pass_count
        if pass_count
        else 0.0
    )
    mrr_at_5 = sum(r.get("rr_at_5", 0.0) for r in per_question) / total
    elapsed_ms_total = sum(int(r.get("elapsed_ms") or 0) for r in per_question)

    by_category: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, Any]] = {}
    for r in per_question:
        cat = r.get("category", "") or "(uncategorised)"
        src = r.get("source", "") or "(unsourced)"
        bc = by_category.setdefault(cat, {"count": 0, "pass_count": 0})
        bs = by_source.setdefault(src, {"count": 0, "pass_count": 0})
        bc["count"] += 1
        bs["count"] += 1
        if r.get("pass"):
            bc["pass_count"] += 1
            bs["pass_count"] += 1

    for stats in by_category.values():
        stats["pass_rate_pct"] = round(stats["pass_count"] / stats["count"] * 100, 2)
    for stats in by_source.values():
        stats["pass_rate_pct"] = round(stats["pass_count"] / stats["count"] * 100, 2)

    return {
        "total": total,
        "pass_count": pass_count,
        "pass_rate_pct": round(pass_count / total * 100, 1),
        "partial_pass_count": partial_pass_count,
        "mean_passing_path_count": round(mean_passing_paths, 3),
        "mrr_at_5": round(mrr_at_5, 4),
        "elapsed_total_sec": round(elapsed_ms_total / 1000.0, 2),
        "by_category": by_category,
        "by_source": by_source,
        "top_k": top_k,
    }
