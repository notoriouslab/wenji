"""End-to-end eval runner: HTTP client → search server → multi-path metrics.

The runner is a black-box test against a running ``wenji serve``. It does NOT
load ``Searcher`` in-process.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from wenji.core.errors import SearchError
from wenji.eval.jsonl import (
    Candidate,
    GoldPath,
    load_candidates,
    wrap_legacy_candidate,
)
from wenji.eval.metrics import (
    DEFAULT_TOP_K,
    aggregate,
    evaluate_question,
    rollup_chunks_to_articles,
    score_gold_path,
)


def _query_server(
    client: httpx.Client,
    api_url: str,
    query: str,
    top_k: int,
) -> dict:
    t0 = time.time()
    try:
        resp = client.get(api_url, params={"q": query, "limit": top_k})
        resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise SearchError(f"connection refused at {api_url}; start `wenji serve` first") from exc
    elapsed_ms = int((time.time() - t0) * 1000)
    payload = resp.json()
    payload["elapsed_ms"] = elapsed_ms
    return payload


def run_baseline(
    candidates_path: str | Path,
    *,
    api_url: str = "http://localhost:8000/api/search",
    db_path: str | Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    request_timeout: float = 30.0,
    candidates: Iterable[Candidate] | None = None,
    http_client: httpx.Client | None = None,
) -> dict:
    """Run an end-to-end multi-path eval against ``api_url``.

    Args:
        candidates_path: JSONL file (used when ``candidates`` not supplied).
        api_url: Search endpoint (typically ``wenji serve`` on localhost).
        db_path: SQLite path (reserved for future direct-DB assertions).
        top_k: candidate window per question (default 20, multi-path baseline).
        candidates: Pre-loaded candidates iterable (skips file load — used in tests).
        http_client: Pre-built ``httpx.Client`` (test injection).

    Returns:
        ``{"summary": {...}, "results": [...per-question multi-path metrics...]}``.
    """
    if candidates is None:
        candidates = load_candidates(candidates_path)

    candidates = list(candidates)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=request_timeout)

    per_question: list[dict[str, Any]] = []
    try:
        for cand in candidates:
            response = _query_server(client, api_url, cand.query, top_k)
            per_question.append(evaluate_question(cand, response, top_k=top_k))
    finally:
        if owns_client:
            client.close()

    return {
        "summary": aggregate(per_question, top_k=top_k),
        "results": per_question,
    }


__all__ = [
    "run_baseline",
    "load_candidates",
    "wrap_legacy_candidate",
    "evaluate_question",
    "aggregate",
    "rollup_chunks_to_articles",
    "score_gold_path",
    "Candidate",
    "GoldPath",
]
