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
    "你是搜尋查詢改寫器。使用者會用口語問問題，你要改寫成更適合搜尋引擎的查詢。\n\n"
    "規則：\n"
    "1. 展開縮寫和暱稱（例如：周牧師 → 周神助牧師、巽正 → 周巽正）\n"
    "2. 輸出 1-3 個搜尋關鍵詞組合，用 | 分隔\n"
    "3. 只輸出改寫結果，不要解釋\n"
    "4. 如果原始查詢已經很精確，原樣返回即可\n\n"
    "範例：\n"
    "因信稱義 → 因信稱義 教義 | 路德 改教 因信稱義 | 保羅 羅馬書 信心\n"
    "禱告怎麼禱告 → 禱告 方法 步驟 | 主禱文 教導 | 禱告 蒙應允 條件\n\n"
    "使用者查詢：{query}"
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
