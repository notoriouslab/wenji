"""Query-time RAG question answering.

The :class:`Asker` sits on top of an existing wenji database and exposes a
single ``ask(query, ...)`` method that retrieves top-K results via the wenji
:class:`~wenji.search.Searcher`, composes a prompt, calls an LLM, and returns
an :class:`Answer` carrying the LLM-generated text plus chunk-level
:class:`Citation` entries.

Mirrors the :class:`~wenji.aggregate.Aggregator` design (D1 / D7 reused):
LLM call is best-effort; on failure ``answer`` becomes ``None`` while
``retrieval`` and ``citations`` remain populated. Cache rows live in the
``aggregate_cache`` table introduced by ``wenji-aggregate-v0-2`` (D6),
keyed under the function name ``"ask"``.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from wenji.aggregate import Filter, SourceRef
from wenji.aggregate.cache import cache_get, cache_key, cache_put
from wenji.aggregate.llm import LLMClient, LLMClientError
from wenji.ask.prompts import ASK_PROMPT
from wenji.core.safety import sanitize_prompt_input
from wenji.search import Searcher
from wenji.search.bm25 import build_fts_query

logger = logging.getLogger(__name__)

__all__ = [
    "Asker",
    "Answer",
    "Citation",
    "Filter",
    "LLMClient",
    "LLMClientError",
    "SourceRef",
]


@dataclass
class Citation:
    """Chunk-level citation referenced by an :class:`Answer`."""

    article_id: str
    chunk_index: int
    title: str
    snippet: str
    bm25_score: float


@dataclass
class Answer:
    """RAG answer payload returned by :meth:`Asker.ask`."""

    query: str
    answer: str | None
    citations: list[Citation]
    retrieval: list[SourceRef]


class Asker:
    """Query-time RAG question answering on top of a wenji corpus.

    Parameters
    ----------
    db
        Open SQLite connection (schema initialised + corpus ingested).
    llm_client
        Configured :class:`LLMClient`. Required — passing ``None`` raises
        :class:`TypeError` at construction.
    searcher
        Pre-built :class:`Searcher`. Optional; lazy-constructed via
        ``Searcher(db, Embedder())`` when omitted.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        llm_client: LLMClient,
        searcher: Searcher | None = None,
    ) -> None:
        if llm_client is None:
            raise TypeError("llm_client is required for Asker")
        self.db = db
        self.llm_client = llm_client
        self.searcher = searcher

    def _get_searcher(self) -> Searcher:
        if self.searcher is None:
            from wenji.config import load_config, resolve_config_path
            from wenji.ingest.embed import Embedder

            # Standalone use resolves search.* from WENJI_CONFIG like every
            # other Searcher entry point; the web app injects its own.
            cfg = load_config(resolve_config_path()).search
            self.searcher = Searcher(
                self.db,
                Embedder(),
                alpha=cfg.alpha,
                candidate_pool=cfg.candidate_pool,
            )
        return self.searcher

    @staticmethod
    def _cache_key(
        query: str,
        k: int,
        axis: str | None,
        filter: Filter | None,
    ) -> str:
        canonical = {
            "query": query,
            "k": k,
            "axis": axis,
            "filter": filter.canonical_dict() if filter is not None else None,
        }
        return cache_key("ask", canonical)

    def _retrieve(
        self,
        query: str,
        *,
        k: int,
        axis: str | None,
        filter: Filter | None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []

        searcher = self._get_searcher()
        # Over-fetch when a Filter is supplied so post-filter still leaves k hits.
        fetch_limit = k * 3 if filter is not None else k
        raw = searcher.search(query, axis=axis, limit=fetch_limit)
        if filter is None or not raw:
            return raw[:k]

        ids = [r["article_id"] for r in raw]
        clause, params = filter.to_sql_where(table_alias="m")
        if not clause:
            return raw[:k]

        placeholders = ",".join(["?"] * len(ids))
        sql = (
            f"SELECT m.article_id FROM articles_meta m "
            f"WHERE m.article_id IN ({placeholders}) AND {clause}"
        )
        allowed_rows = self.db.execute(sql, [*ids, *params]).fetchall()
        allowed = {row[0] for row in allowed_rows}
        return [r for r in raw if r["article_id"] in allowed][:k]

    @staticmethod
    def _to_source_refs(raw: list[dict[str, Any]]) -> list[SourceRef]:
        return [
            SourceRef(
                article_id=r["article_id"],
                title=r.get("title") or "",
                snippet=r.get("content_snippet") or r.get("snippet") or "",
                bm25_score=float(r.get("bm25_score") or 0.0),
            )
            for r in raw
        ]

    @staticmethod
    def _compose_prompt(query: str, retrieval: list[SourceRef]) -> str:
        sources_block = "\n\n".join(
            f"[{i + 1}] {sr.title} — {sr.snippet}" for i, sr in enumerate(retrieval)
        )
        return ASK_PROMPT.format(
            query=sanitize_prompt_input(query),
            sources=sanitize_prompt_input(sources_block),
        )

    def _build_citations(
        self,
        query: str,
        retrieval: list[SourceRef],
    ) -> list[Citation]:
        if not retrieval:
            return []
        fts_query = build_fts_query(query, column="chunk_text") if query.strip() else ""
        citations: list[Citation] = []
        for sr in retrieval:
            chunk_index = 0
            if fts_query:
                try:
                    row = self.db.execute(
                        "SELECT chunk_index FROM chunks_fts "
                        "WHERE chunks_fts MATCH ? AND article_id = ? "
                        "ORDER BY bm25(chunks_fts) ASC LIMIT 1",
                        (fts_query, sr.article_id),
                    ).fetchone()
                    if row is not None:
                        chunk_index = int(row[0])
                except sqlite3.OperationalError:
                    chunk_index = 0
            citations.append(
                Citation(
                    article_id=sr.article_id,
                    chunk_index=chunk_index,
                    title=sr.title,
                    snippet=sr.snippet,
                    bm25_score=sr.bm25_score,
                )
            )
        return citations

    def ask(
        self,
        query: str,
        *,
        k: int = 5,
        axis: str | None = None,
        filter: Filter | None = None,
    ) -> Answer:
        key = self._cache_key(query, k, axis, filter)
        cached = cache_get(self.db, key)
        if cached is not None:
            return _answer_from_dict(cached)

        raw = self._retrieve(query, k=k, axis=axis, filter=filter)
        retrieval = self._to_source_refs(raw)
        citations = self._build_citations(query, retrieval)

        answer_text: str | None = None
        if retrieval:
            prompt = self._compose_prompt(query, retrieval)
            try:
                answer_text = self.llm_client.chat([{"role": "user", "content": prompt}])
            except LLMClientError as exc:
                logger.warning(
                    "Asker LLM call failed (%s); falling back to answer=None",
                    exc,
                )
                answer_text = None

        answer = Answer(
            query=query,
            answer=answer_text,
            citations=citations,
            retrieval=retrieval,
        )
        cache_put(self.db, key, _answer_to_dict(answer))
        return answer


def _answer_to_dict(answer: Answer) -> dict[str, Any]:
    return asdict(answer)


def _answer_from_dict(payload: dict[str, Any]) -> Answer:
    return Answer(
        query=payload["query"],
        answer=payload.get("answer"),
        citations=[Citation(**c) for c in payload.get("citations") or []],
        retrieval=[SourceRef(**s) for s in payload.get("retrieval") or []],
    )
