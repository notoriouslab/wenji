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
from dataclasses import asdict, dataclass, field
from typing import Any

from wenji.aggregate import Filter, SourceRef
from wenji.aggregate.cache import cache_get, cache_key, cache_put
from wenji.aggregate.llm import LLMClient, LLMClientError
from wenji.ask.prompts import ASK_PROMPT, FOLLOWUP_REWRITE_PROMPT
from wenji.core.safety import sanitize_prompt_input
from wenji.search import Searcher, strip_markdown_for_snippet
from wenji.search.bm25 import build_fts_query_or

logger = logging.getLogger(__name__)

#: Chunks fed per cited article. Measured on a policy corpus: the top-ranked
#: chunk alone held the answer 2/5 times, the top 3 covered 5/5.
MAX_CHUNKS_PER_CITATION = 3

#: Per-chunk character cap in the prompt, bounding token cost for long chunks.
MAX_CHUNK_CHARS_IN_PROMPT = 1200

#: Conversation turns considered when rewriting a follow-up. The web layer
#: rejects longer histories; the library slices defensively for direct callers.
MAX_HISTORY_TURNS = 10

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
    """Chunk-level citation referenced by an :class:`Answer`.

    ``chunk_texts`` carries the clause text that actually grounded the answer
    (top-3 matching chunks, markdown-stripped) and ``chunk_indexes`` their
    positions; ``chunk_index`` stays as the first index for backward
    compatibility. Both lists default to empty so answers cached before these
    fields existed still deserialise (:func:`_answer_from_dict` unpacks cache
    rows straight into this dataclass).
    """

    article_id: str
    chunk_index: int
    title: str
    snippet: str
    bm25_score: float
    chunk_texts: list[str] = field(default_factory=list)
    chunk_indexes: list[int] = field(default_factory=list)


@dataclass
class Answer:
    """RAG answer payload returned by :meth:`Asker.ask`."""

    query: str
    answer: str | None
    citations: list[Citation]
    retrieval: list[SourceRef]


def _truncate(text: str) -> str:
    """Cap a chunk at :data:`MAX_CHUNK_CHARS_IN_PROMPT`, marking elision."""
    if len(text) <= MAX_CHUNK_CHARS_IN_PROMPT:
        return text
    return text[:MAX_CHUNK_CHARS_IN_PROMPT] + "…"


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
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Key an answer by its inputs.

        The same follow-up ("那病假呢？") means different things under different
        histories, so the turns participate in the key. They are keyed rather
        than the rewritten query so that a cache hit costs **zero** LLM calls —
        keying on the rewrite would force a rewrite call before every lookup.

        Single-turn keys stay byte-identical to 0.5.x (the ``history`` entry is
        omitted when absent) so existing cache rows keep hitting.
        """
        canonical: dict[str, Any] = {
            "query": query,
            "k": k,
            "axis": axis,
            "filter": filter.canonical_dict() if filter is not None else None,
        }
        if history:
            canonical["history"] = [
                {"role": t.get("role"), "content": t.get("content")}
                for t in history[-MAX_HISTORY_TURNS:]
            ]
        return cache_key("ask", canonical)

    def _rewrite_for_retrieval(self, query: str, history: list[dict[str, Any]]) -> str:
        """Condense history + follow-up into a self-contained retrieval query.

        Used for retrieval only — the answer is always composed against the
        user's own wording. On LLM failure this degrades to concatenating the
        last user turn with the follow-up rather than aborting the request.
        """
        recent = history[-MAX_HISTORY_TURNS:]
        fallback_parts = [
            t.get("content", "")
            for t in recent
            if t.get("role") == "user" and str(t.get("content") or "").strip()
        ]
        fallback = f"{fallback_parts[-1]} {query}".strip() if fallback_parts else query

        rendered = "\n".join(
            f"{t.get('role')}: {sanitize_prompt_input(str(t.get('content') or ''))}" for t in recent
        )
        prompt = FOLLOWUP_REWRITE_PROMPT.format(
            history=rendered,
            followup=sanitize_prompt_input(query),
        )
        try:
            raw = self.llm_client.chat([{"role": "user", "content": prompt}])
        except LLMClientError as exc:
            logger.warning(
                "follow-up rewrite failed (%s); retrieving on concatenated turns",
                exc,
            )
            return fallback
        first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
        return first_line or fallback

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
    def _compose_prompt(query: str, citations: list[Citation]) -> str:
        """Compose the ask prompt, grounding the LLM on clause text.

        Feeding title + document-level snippet leaves the model unable to answer
        anything whose answer is a number inside a clause (measured 0/5 on a
        policy corpus). Each source block therefore carries the matched chunk
        text wrapped in ``<條文>``.

        ``sanitize_prompt_input`` XML-escapes its argument, so the title and
        each chunk are sanitised individually and the literal ``<條文>`` markers
        are assembled afterwards — sanitising the joined block would escape the
        markers into ``&lt;條文&gt;`` and destroy the structure.
        """
        blocks: list[str] = []
        for i, c in enumerate(citations):
            body_parts = [
                sanitize_prompt_input(_truncate(text)) for text in c.chunk_texts if text.strip()
            ]
            body = "\n".join(body_parts) if body_parts else sanitize_prompt_input(c.snippet)
            blocks.append(f"[{i + 1}] {sanitize_prompt_input(c.title)}\n<條文>{body}</條文>")
        return ASK_PROMPT.format(
            query=sanitize_prompt_input(query),
            sources="\n\n".join(blocks),
        )

    def _build_citations(
        self,
        query: str,
        retrieval: list[SourceRef],
    ) -> list[Citation]:
        """Locate the clauses backing each retrieved article.

        Uses :func:`build_fts_query_or`; the AND builder collapses a space-free
        Chinese question into one never-matching phrase, which silently left
        every citation anchored at chunk 0.
        """
        if not retrieval:
            return []
        fts_query = build_fts_query_or(query, column="chunk_text") if query.strip() else ""
        citations: list[Citation] = []
        for sr in retrieval:
            chunk_indexes: list[int] = []
            chunk_texts: list[str] = []
            if fts_query:
                try:
                    rows = self.db.execute(
                        "SELECT chunk_index, chunk_text_raw FROM chunks_fts "
                        "WHERE chunks_fts MATCH ? AND article_id = ? "
                        "ORDER BY bm25(chunks_fts) ASC LIMIT ?",
                        (fts_query, sr.article_id, MAX_CHUNKS_PER_CITATION),
                    ).fetchall()
                except sqlite3.OperationalError:
                    logger.warning(
                        "chunk lookup failed for %s; citation falls back to chunk 0",
                        sr.article_id,
                        exc_info=True,
                    )
                    rows = []
                for chunk_index, chunk_text_raw in rows:
                    plain = strip_markdown_for_snippet(chunk_text_raw or "").strip()
                    if not plain:
                        continue
                    chunk_indexes.append(int(chunk_index))
                    chunk_texts.append(plain)
            citations.append(
                Citation(
                    article_id=sr.article_id,
                    chunk_index=chunk_indexes[0] if chunk_indexes else 0,
                    title=sr.title,
                    snippet=sr.snippet,
                    bm25_score=sr.bm25_score,
                    chunk_texts=chunk_texts,
                    chunk_indexes=chunk_indexes,
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
        history: list[dict[str, Any]] | None = None,
    ) -> Answer:
        # Keyed on the turns, not the rewrite, so a repeat question costs no LLM
        # call at all (rate limits are a real constraint on the deployments).
        key = self._cache_key(query, k, axis, filter, history)
        cached = cache_get(self.db, key)
        if cached is not None:
            return _answer_from_dict(cached)

        # A follow-up ("那病假呢？") is not searchable on its own, so retrieval
        # runs on a rewritten, self-contained query while the answer is still
        # composed against the user's own wording.
        retrieval_query = self._rewrite_for_retrieval(query, history) if history else query

        raw = self._retrieve(retrieval_query, k=k, axis=axis, filter=filter)
        retrieval = self._to_source_refs(raw)
        # Citations carry the clause text the prompt is grounded on, so they are
        # built first and handed to _compose_prompt (SourceRef has no chunk text).
        citations = self._build_citations(retrieval_query, retrieval)

        answer_text: str | None = None
        llm_failed = False
        if retrieval:
            prompt = self._compose_prompt(query, citations)
            try:
                answer_text = self.llm_client.chat([{"role": "user", "content": prompt}])
            except LLMClientError as exc:
                logger.warning(
                    "Asker LLM call failed (%s); falling back to answer=None",
                    exc,
                )
                answer_text = None
                llm_failed = True

        answer = Answer(
            query=query,
            answer=answer_text,
            citations=citations,
            retrieval=retrieval,
        )
        # A transient LLM failure (rate limit, timeout) must not be cached: the
        # TTL is 30 days, so one 429 would freeze "no answer" for that question
        # for a month. Retrieval-only results (no LLM call attempted) are stable
        # and still cached.
        if not llm_failed:
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
