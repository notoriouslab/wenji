"""Optional LLM query rewrite with on-disk cache and timeout fallback.

Default disabled — caller must instantiate with explicit ``api_url`` + key.
On timeout / API error, falls back to the original query (logged but not
raised). Cache hits avoid the API entirely; ``clear_cache()`` is provided for
jitter-aware eval reruns.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("wenji.search.rewrite")

DEFAULT_TIMEOUT_S = 1.5
DEFAULT_TTL_DAYS = 30
DEFAULT_MODEL = "llama-3.3-70b-versatile"

REWRITE_PROMPT_TEMPLATE = (
    "你是一個搜尋查詢改寫助理。把使用者的中文查詢改寫成更適合做向量檢索的版本，"
    "保留所有專有名詞與關鍵概念，可加入近義同義詞，但不要超過原查詢長度的兩倍。"
    "只輸出改寫後的單一行查詢，不要解釋。\n\n查詢：{query}"
)


class QueryRewriter:
    """LLM-driven query rewriter with cache + timeout fallback."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        api_url: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_S,
        ttl_days: int = DEFAULT_TTL_DAYS,
        prompt_template: str = REWRITE_PROMPT_TEMPLATE,
    ) -> None:
        self.conn = conn
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.ttl = timedelta(days=ttl_days)
        self.prompt_template = prompt_template

    def _get_cached(self, raw: str) -> str | None:
        cutoff = (datetime.now(timezone.utc) - self.ttl).isoformat(timespec="seconds")
        row = self.conn.execute(
            "SELECT rewritten FROM query_rewrite_cache WHERE raw = ? AND created_at > ?",
            (raw, cutoff),
        ).fetchone()
        return row[0] if row else None

    def _set_cached(self, raw: str, rewritten: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO query_rewrite_cache (raw, rewritten, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(raw) DO UPDATE SET rewritten = excluded.rewritten, "
            "created_at = excluded.created_at",
            (raw, rewritten, now),
        )
        self.conn.commit()

    def _call_api(self, raw: str) -> str:
        prompt = self.prompt_template.format(query=raw)
        response = httpx.post(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    def rewrite(self, raw: str) -> str:
        """Return rewritten query, or ``raw`` on cache miss + API failure."""
        if not raw.strip():
            return raw
        cached = self._get_cached(raw)
        if cached is not None:
            return cached
        try:
            rewritten = self._call_api(raw)
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            logger.warning("rewrite fallback to raw query: %s", exc)
            return raw
        if not rewritten:
            return raw
        self._set_cached(raw, rewritten)
        return rewritten

    def clear_cache(self) -> None:
        """Wipe all cached rewrites (useful before jitter-aware eval reruns)."""
        self.conn.execute("DELETE FROM query_rewrite_cache")
        self.conn.commit()

    def peek_cache(self, raw: str) -> str | None:
        """Return cached rewrite without calling the LLM.

        Public read-only accessor used by observability (``/api/segment``) to
        report whether a rewrite would be served from cache or freshly called.
        Returns the cached string when a non-expired entry exists, else None.
        """
        if not raw.strip():
            return None
        return self._get_cached(raw)
