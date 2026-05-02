"""Query-time topic and concept aggregation (LLM-essential, not LLM-default).

The :class:`Aggregator` sits on top of an existing wenji database and provides
two query-time aggregation methods:

* :meth:`Aggregator.topic_summary` — BM25 top-K + statistics for a tag.
* :meth:`Aggregator.concept_perspectives` — cross-source viewpoint comparison.

Both methods operate without any LLM call when ``llm_client`` is ``None``;
when an :class:`LLMClient` is provided they additionally produce a Markdown
``narrative`` field via the configured OpenAI-compatible endpoint.

See :mod:`wenji.aggregate.cache` for the result cache and
:mod:`wenji.aggregate.llm` for the LLM client.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from wenji.aggregate.cache import cache_get, cache_key, cache_put
from wenji.aggregate.llm import LLMClient, LLMClientError
from wenji.aggregate.prompts import CONCEPT_PROMPT, TOPIC_PROMPT
from wenji.search.bm25 import build_fts_query

logger = logging.getLogger(__name__)

__all__ = [
    "Aggregator",
    "ConceptPerspectives",
    "Filter",
    "LLMClient",
    "LLMClientError",
    "PerSourceView",
    "SourceRef",
    "TopicStatistics",
    "TopicSummary",
]


@dataclass
class SourceRef:
    article_id: str
    title: str
    snippet: str
    bm25_score: float


@dataclass
class TopicStatistics:
    total_hits: int
    source_type_distribution: dict[str, int]
    pub_year_distribution: dict[str, int]


@dataclass
class TopicSummary:
    tag: str
    top_sources: list[SourceRef]
    statistics: TopicStatistics
    narrative: str | None = None


@dataclass
class PerSourceView:
    source_ref: SourceRef
    excerpts: list[str]


@dataclass
class ConceptPerspectives:
    concept: str
    per_source_views: list[PerSourceView]
    consensus: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    narrative: str | None = None


@dataclass
class Filter:
    """Pre-filter for aggregate queries.

    Supported fields and lookup suffixes (see spec table):

    | Field         | Exact | __in | __not_in | __gte | __lte |
    |---------------|-------|------|----------|-------|-------|
    | tag           |   ✓   |  ✓   |    ✓     |       |       |
    | source_type   |   ✓   |  ✓   |    ✓     |       |       |
    | subtype       |   ✓   |  ✓   |    ✓     |       |       |
    | pub_year      |   ✓   |  ✓   |    ✓     |   ✓   |   ✓   |
    | category      |   ✓   |  ✓   |    ✓     |       |       |

    ``tag`` is matched against ``articles_meta.tags`` (a JSON list string)
    with a quoted-substring LIKE pattern; the other fields query their
    direct columns. Unsupported keyword arguments raise ``TypeError`` at
    construction (dataclass default behaviour).
    """

    tag: str | None = None
    tag__in: list[str] | None = None
    tag__not_in: list[str] | None = None

    source_type: str | None = None
    source_type__in: list[str] | None = None
    source_type__not_in: list[str] | None = None

    subtype: str | None = None
    subtype__in: list[str] | None = None
    subtype__not_in: list[str] | None = None

    pub_year: int | None = None
    pub_year__in: list[int] | None = None
    pub_year__not_in: list[int] | None = None
    pub_year__gte: int | None = None
    pub_year__lte: int | None = None

    category: str | None = None
    category__in: list[str] | None = None
    category__not_in: list[str] | None = None

    def to_sql_where(self, table_alias: str = "m") -> tuple[str, list[Any]]:
        """Render this filter to a ``WHERE`` clause + bind params.

        Returns a tuple ``(clause, params)`` where ``clause`` is suitable for
        appending after an existing ``WHERE`` (joined with ``AND``) or used as
        ``"WHERE " + clause``. Returns ``("", [])`` when the filter is empty.
        """
        clauses: list[str] = []
        params: list[Any] = []
        prefix = f"{table_alias}." if table_alias else ""

        if self.tag is not None:
            clauses.append(f"{prefix}tags LIKE ?")
            params.append(f'%"{self.tag}"%')
        if self.tag__in:
            sub = " OR ".join([f"{prefix}tags LIKE ?"] * len(self.tag__in))
            clauses.append(f"({sub})")
            params.extend(f'%"{t}"%' for t in self.tag__in)
        if self.tag__not_in:
            for t in self.tag__not_in:
                clauses.append(f"{prefix}tags NOT LIKE ?")
                params.append(f'%"{t}"%')

        for col in ("source_type", "subtype", "category"):
            exact = getattr(self, col)
            in_list = getattr(self, f"{col}__in")
            not_in_list = getattr(self, f"{col}__not_in")
            if exact is not None:
                clauses.append(f"{prefix}{col} = ?")
                params.append(exact)
            if in_list:
                placeholders = ",".join(["?"] * len(in_list))
                clauses.append(f"{prefix}{col} IN ({placeholders})")
                params.extend(in_list)
            if not_in_list:
                placeholders = ",".join(["?"] * len(not_in_list))
                clauses.append(f"{prefix}{col} NOT IN ({placeholders})")
                params.extend(not_in_list)

        if self.pub_year is not None:
            clauses.append(f"{prefix}pub_year = ?")
            params.append(self.pub_year)
        if self.pub_year__in:
            placeholders = ",".join(["?"] * len(self.pub_year__in))
            clauses.append(f"{prefix}pub_year IN ({placeholders})")
            params.extend(self.pub_year__in)
        if self.pub_year__not_in:
            placeholders = ",".join(["?"] * len(self.pub_year__not_in))
            clauses.append(f"{prefix}pub_year NOT IN ({placeholders})")
            params.extend(self.pub_year__not_in)
        if self.pub_year__gte is not None:
            clauses.append(f"{prefix}pub_year >= ?")
            params.append(self.pub_year__gte)
        if self.pub_year__lte is not None:
            clauses.append(f"{prefix}pub_year <= ?")
            params.append(self.pub_year__lte)

        return (" AND ".join(clauses), params)

    def canonical_dict(self) -> dict[str, Any]:
        """Return non-null fields as a stable dict for cache-key composition."""
        return {k: v for k, v in asdict(self).items() if v is not None}


class Aggregator:
    def __init__(
        self,
        db: sqlite3.Connection,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.db = db
        self.llm_client = llm_client

    def topic_summary(
        self,
        tag: str,
        *,
        filter: Filter | None = None,
        k: int = 5,
    ) -> TopicSummary:
        args = {
            "tag": tag,
            "filter": filter.canonical_dict() if filter is not None else None,
            "k": k,
        }
        key = cache_key("topic_summary", args)
        cached = cache_get(self.db, key)
        if cached is not None:
            return _topic_summary_from_dict(cached)

        result = self._topic_summary_structured(tag, filter, k)
        if self.llm_client is not None:
            result.narrative = self._topic_summary_narrative(result)
        cache_put(self.db, key, _topic_summary_to_dict(result))
        return result

    def _topic_summary_structured(
        self,
        tag: str,
        filter: Filter | None,
        k: int,
    ) -> TopicSummary:
        fts_query = build_fts_query(tag)
        if not fts_query:
            empty_stats = TopicStatistics(0, {}, {})
            return TopicSummary(tag=tag, top_sources=[], statistics=empty_stats)

        filter_clause, filter_params = (
            filter.to_sql_where(table_alias="m") if filter is not None else ("", [])
        )
        extra_where = f" AND {filter_clause}" if filter_clause else ""

        params: list[Any] = [fts_query, *filter_params]
        sources_sql = (
            "SELECT f.article_id, m.title, "
            "snippet(articles_fts, 3, '<mark>', '</mark>', '...', 16), "
            "bm25(articles_fts) AS bm25_raw, "
            "m.source_type, m.pub_year "
            "FROM articles_fts f "
            "JOIN articles_meta m ON m.article_id = f.article_id "
            "WHERE articles_fts MATCH ? "
            "AND IFNULL(m.category, '') != 'excluded'"
            f"{extra_where} "
            "ORDER BY bm25_raw ASC LIMIT ?"
        )
        rows = self.db.execute(sources_sql, [*params, k]).fetchall()
        bm25_raws = [r[3] for r in rows]
        max_abs = max((abs(x) for x in bm25_raws), default=1.0) or 1.0
        top_sources = [
            SourceRef(
                article_id=row[0],
                title=row[1] or "",
                snippet=row[2] or "",
                bm25_score=abs(row[3]) / max_abs,
            )
            for row in rows
        ]

        stats_sql = (
            "SELECT m.source_type, m.pub_year "
            "FROM articles_fts f "
            "JOIN articles_meta m ON m.article_id = f.article_id "
            "WHERE articles_fts MATCH ? "
            "AND IFNULL(m.category, '') != 'excluded'"
            f"{extra_where}"
        )
        stat_rows = self.db.execute(stats_sql, params).fetchall()
        source_type_dist = Counter(r[0] or "unknown" for r in stat_rows)
        pub_year_dist = Counter(str(r[1]) if r[1] is not None else "unknown" for r in stat_rows)
        statistics = TopicStatistics(
            total_hits=len(stat_rows),
            source_type_distribution=dict(source_type_dist),
            pub_year_distribution=dict(pub_year_dist),
        )

        return TopicSummary(tag=tag, top_sources=top_sources, statistics=statistics)

    def _topic_summary_narrative(self, summary: TopicSummary) -> str | None:
        if self.llm_client is None or not summary.top_sources:
            return None
        sources_block = "\n\n".join(
            f"來源 {i + 1}: {s.title}\n摘要: {s.snippet}" for i, s in enumerate(summary.top_sources)
        )
        prompt = TOPIC_PROMPT.format(tag=summary.tag, sources=sources_block)
        try:
            return self.llm_client.chat([{"role": "user", "content": prompt}])
        except LLMClientError as exc:
            logger.warning(
                "topic_summary LLM call failed (%s); falling back to narrative=None", exc
            )
            return None

    def concept_perspectives(
        self,
        concept: str,
        *,
        filter: Filter | None = None,
        top_sources: int = 4,
        per_source: int = 3,
    ) -> ConceptPerspectives:
        args = {
            "concept": concept,
            "filter": filter.canonical_dict() if filter is not None else None,
            "top_sources": top_sources,
            "per_source": per_source,
        }
        key = cache_key("concept_perspectives", args)
        cached = cache_get(self.db, key)
        if cached is not None:
            return _concept_perspectives_from_dict(cached)

        result = self._concept_perspectives_structured(concept, filter, top_sources, per_source)
        if self.llm_client is not None:
            self._concept_perspectives_apply_llm(result)
        cache_put(self.db, key, _concept_perspectives_to_dict(result))
        return result

    def _concept_perspectives_structured(
        self,
        concept: str,
        filter: Filter | None,
        top_sources: int,
        per_source: int,
    ) -> ConceptPerspectives:
        article_query = build_fts_query(concept)
        if not article_query:
            return ConceptPerspectives(concept=concept, per_source_views=[])

        filter_clause, filter_params = (
            filter.to_sql_where(table_alias="m") if filter is not None else ("", [])
        )
        extra_where = f" AND {filter_clause}" if filter_clause else ""

        sources_sql = (
            "SELECT f.article_id, m.title, "
            "snippet(articles_fts, 3, '<mark>', '</mark>', '...', 16), "
            "bm25(articles_fts) AS bm25_raw "
            "FROM articles_fts f "
            "JOIN articles_meta m ON m.article_id = f.article_id "
            "WHERE articles_fts MATCH ? "
            "AND IFNULL(m.category, '') != 'excluded'"
            f"{extra_where} "
            "ORDER BY bm25_raw ASC LIMIT ?"
        )
        rows = self.db.execute(sources_sql, [article_query, *filter_params, top_sources]).fetchall()
        if not rows:
            return ConceptPerspectives(concept=concept, per_source_views=[])

        bm25_raws = [r[3] for r in rows]
        max_abs = max((abs(x) for x in bm25_raws), default=1.0) or 1.0
        article_ids = [r[0] for r in rows]
        ref_by_id = {
            r[0]: SourceRef(
                article_id=r[0],
                title=r[1] or "",
                snippet=r[2] or "",
                bm25_score=abs(r[3]) / max_abs,
            )
            for r in rows
        }

        chunk_query = build_fts_query(concept, column="chunk_text")
        excerpts_by_article: dict[str, list[str]] = {aid: [] for aid in article_ids}
        if chunk_query:
            placeholders = ",".join("?" for _ in article_ids)
            chunk_rows = self.db.execute(
                f"SELECT article_id, chunk_text_raw, bm25(chunks_fts) AS rs "
                f"FROM chunks_fts "
                f"WHERE chunks_fts MATCH ? AND article_id IN ({placeholders}) "
                f"ORDER BY rs ASC",
                (chunk_query, *article_ids),
            ).fetchall()
            for aid, text, _rs in chunk_rows:
                if len(excerpts_by_article[aid]) < per_source:
                    excerpts_by_article[aid].append(text or "")

        per_source_views = [
            PerSourceView(source_ref=ref_by_id[aid], excerpts=excerpts_by_article[aid])
            for aid in article_ids
        ]
        return ConceptPerspectives(concept=concept, per_source_views=per_source_views)

    def _concept_perspectives_apply_llm(self, result: ConceptPerspectives) -> None:
        if self.llm_client is None or not result.per_source_views:
            return
        per_source_block = "\n\n".join(
            f"來源 {i + 1}: {v.source_ref.title}\n" + "\n".join(f"- {ex}" for ex in v.excerpts)
            for i, v in enumerate(result.per_source_views)
        )
        prompt = CONCEPT_PROMPT.format(concept=result.concept, per_source_views=per_source_block)
        try:
            text = self.llm_client.chat([{"role": "user", "content": prompt}])
        except LLMClientError as exc:
            logger.warning("concept_perspectives LLM call failed (%s); narrative=None", exc)
            return
        result.narrative = text
        result.consensus, result.disagreements = _parse_consensus_disagreements(text)


def _topic_summary_to_dict(s: TopicSummary) -> dict[str, Any]:
    return asdict(s)


def _topic_summary_from_dict(d: dict[str, Any]) -> TopicSummary:
    return TopicSummary(
        tag=d["tag"],
        top_sources=[SourceRef(**sr) for sr in d["top_sources"]],
        statistics=TopicStatistics(**d["statistics"]),
        narrative=d.get("narrative"),
    )


def _concept_perspectives_to_dict(c: ConceptPerspectives) -> dict[str, Any]:
    return asdict(c)


def _concept_perspectives_from_dict(d: dict[str, Any]) -> ConceptPerspectives:
    return ConceptPerspectives(
        concept=d["concept"],
        per_source_views=[
            PerSourceView(
                source_ref=SourceRef(**v["source_ref"]),
                excerpts=list(v["excerpts"]),
            )
            for v in d["per_source_views"]
        ],
        consensus=list(d.get("consensus") or []),
        disagreements=list(d.get("disagreements") or []),
        narrative=d.get("narrative"),
    )


def _parse_consensus_disagreements(text: str) -> tuple[list[str], list[str]]:
    """Best-effort extraction of bullet items under `## 共識` / `## 分歧` headings.

    Returns ``([], [])`` if the LLM did not follow the requested heading
    structure; the caller falls back to ``narrative`` only.
    """
    sections: dict[str, list[str]] = {"共識": [], "分歧": []}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            heading = line[3:].strip()
            current = heading if heading in sections else None
            continue
        if current and line.startswith(("-", "*", "•")):
            item = line.lstrip("-*• ").strip()
            if item:
                sections[current].append(item)
    return sections["共識"], sections["分歧"]
