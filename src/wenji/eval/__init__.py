"""End-to-end eval runner: HTTP client → search server → metric aggregation.

Spec: eval is a black-box test against a running ``wenji serve``. Runner does
NOT load ``Searcher`` in-process. Optional ``--clear-cache`` wipes
``query_rewrite_cache`` on a directly-attached SQLite DB before queries fire,
so repeat runs are deterministic for jitter-aware comparison.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx

from wenji.core.errors import SearchError
from wenji.eval.jsonl import Candidate, load_candidates
from wenji.eval.metrics import (
    DEFAULT_MIN_HITS,
    DEFAULT_TOP_K,
    aggregate,
    evaluate_question,
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


def clear_rewrite_cache(db_path: str | Path) -> int:
    """Delete every row from ``query_rewrite_cache`` of the given DB.

    Returns the number of rows deleted. Used by ``--clear-cache`` to make
    LLM-rewrite-on eval runs reproducible.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        before = conn.execute("SELECT COUNT(*) FROM query_rewrite_cache").fetchone()[0]
        conn.execute("DELETE FROM query_rewrite_cache")
        conn.commit()
    finally:
        conn.close()
    return int(before)


def run_baseline(
    candidates_path: str | Path,
    *,
    api_url: str = "http://localhost:8000/api/search",
    db_path: str | Path | None = None,
    clear_cache: bool = False,
    min_hits: int = DEFAULT_MIN_HITS,
    top_k: int = DEFAULT_TOP_K,
    request_timeout: float = 30.0,
    candidates: Iterable[Candidate] | None = None,
    http_client: httpx.Client | None = None,
) -> dict:
    """Run an end-to-end eval against ``api_url``.

    Args:
        candidates_path: JSONL file (used when ``candidates`` not supplied).
        api_url: Search endpoint (typically ``wenji serve`` on localhost).
        db_path: SQLite path. Required when ``clear_cache=True``.
        clear_cache: If True, wipe ``query_rewrite_cache`` before queries fire.
        min_hits: ``auto_pass`` threshold (default 3).
        top_k: candidate window per question (default 5).
        candidates: Pre-loaded candidates iterable (skips file load — used in tests).
        http_client: Pre-built ``httpx.Client`` (test injection).

    Returns:
        ``{"summary": {...}, "results": [...per-question...]}``.
    """
    if clear_cache:
        if db_path is None:
            raise SearchError("clear_cache=True requires db_path")
        clear_rewrite_cache(db_path)

    if candidates is None:
        candidates = load_candidates(candidates_path)

    candidates = list(candidates)
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=request_timeout)

    per_question: list[dict[str, Any]] = []
    try:
        for cand in candidates:
            response = _query_server(client, api_url, cand.query, top_k)
            per_question.append(evaluate_question(cand, response, min_hits=min_hits, top_k=top_k))
    finally:
        if owns_client:
            client.close()

    return {
        "summary": aggregate(per_question, top_k=top_k),
        "results": per_question,
    }


__all__ = [
    "run_baseline",
    "clear_rewrite_cache",
    "load_candidates",
    "evaluate_question",
    "aggregate",
    "Candidate",
]
