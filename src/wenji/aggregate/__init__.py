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
from wenji.core.safety import sanitize_prompt_input
from wenji.search.bm25 import build_fts_query, build_fts_query_or

logger = logging.getLogger(__name__)

# 主題彙總餵入層的邊界，與 ask 的引用餵入同標準（每段 1200 字元）。
MAX_CHUNKS_PER_TOPIC_SOURCE = 2
MAX_CHUNK_CHARS_IN_PROMPT = 1200
# 整個 sources 區塊的總字數上限（含標題與框架字元，不只 chunk 本體）。
# 留在 sanitize_prompt_input 的 10,000 之下並保留餘裕：sanitize 會攔腰截斷
# 超長輸入，尾端來源連標頭都靜默消失。只算 chunk 本體、不算標題與「來源 N:」
# 框架，正是這裡踩過的坑（k 大＋標題稍長就破表）。
TOPIC_SOURCES_CHAR_BUDGET = 9_000
# 每段 chunk 的目標下限：某來源分到的額度連一段都湊不出這個長度時，
# 該來源只餵標題（header-only），而不是硬塞一小截或把總量墊爆。
MIN_CHUNK_CHARS_IN_PROMPT = 200

# Prompt 形狀的版本號。餵入策略或模板改動時 MUST 加一，否則既有部署會在
# 30 天 TTL 內繼續回舊快取，改動等於沒上線。
# rev 4: sources 組裝改為總量預算（標題＋框架計入），topic/concept 共用。
PROMPT_REVISION = 4


def _truncate_chunk(text: str, limit: int = MAX_CHUNK_CHARS_IN_PROMPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _assemble_source_blocks(items: list[tuple[str, list[str]]]) -> str:
    """把 ``(標題, chunk 文字清單)`` 組成 ``來源 N: 標題 / - chunk`` 區塊，
    並保證**整段**長度（標題＋框架＋本體）落在 TOPIC_SOURCES_CHAR_BUDGET 內。

    sanitize_prompt_input 會對組好的字串攔腰截斷，所以只替 chunk 本體編預算、
    不算標題與「來源 N:」框架，來源一多就會超過 sanitize 上限、尾端來源整批
    （連標頭）被靜默丟掉。這裡把上限平均分給每個來源，先扣掉該來源標頭的固定
    成本，剩下的才留給本體；湊不到一段下限就只餵標頭，總量因此永遠不破表。
    """
    kept = [(title, [t for t in texts if t and t.strip()]) for title, texts in items]
    n = len(kept)
    if n == 0:
        return ""
    headers = [f"來源 {i + 1}: {title}" for i, (title, _) in enumerate(kept)]
    # 標頭與區塊間隔是固定成本，先扣掉；剩下的才是本體可用預算。
    fixed = sum(len(h) for h in headers) + max(0, n - 1) * len("\n\n")
    body_budget = max(0, TOPIC_SOURCES_CHAR_BUDGET - fixed)
    # 預算擠不下人人一段時，集中餵給排名最前、每篇仍能拿到 ≥ 下限的來源，
    # 其餘只列標頭：勝過人人分到碎屑，更勝過來源多到人人都是 header-only。
    fundable = min(n, body_budget // MIN_CHUNK_CHARS_IN_PROMPT) if body_budget else 0
    per_source_body = body_budget // fundable if fundable else 0

    blocks: list[str] = []
    for i, (_title, texts) in enumerate(kept):
        header = headers[i]
        if i < fundable and texts:
            keep = max(1, min(len(texts), per_source_body // MIN_CHUNK_CHARS_IN_PROMPT))
            per_text = per_source_body // keep - len("\n- ")  # 扣掉每行框架
            if per_text > 0:
                lines = "\n".join(f"- {_truncate_chunk(t, per_text)}" for t in texts[:keep])
                blocks.append(f"{header}\n{lines}")
                continue
        blocks.append(header)
    return "\n\n".join(blocks)


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
            "prompt_revision": PROMPT_REVISION,
        }
        key = cache_key("topic_summary", args)
        cached = cache_get(self.db, key)
        if cached is not None:
            return _topic_summary_from_dict(cached)

        result = self._topic_summary_structured(tag, filter, k)
        try:
            if self.llm_client is not None:
                result.narrative = self._topic_summary_narrative(result)
        except LLMClientError as exc:
            # LLM 失敗不入快取：429/超時是暫時的，寫進 30 天快取會把殘缺
            # 結果釘死（與 ask 同判準）。降級回傳結構化結果。
            logger.warning(
                "topic_summary LLM call failed (%s); falling back to narrative=None", exc
            )
            return result
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
        """May raise LLMClientError — the caller decides whether to cache."""
        if self.llm_client is None or not summary.top_sources:
            return None
        # 餵 chunk 原文而非 16-token snippet：標題＋摘要級的餵入答不出
        # 內文細節（ask 對照實驗實測）。chunk 無命中時退全文（截斷），
        # snippet 只是 content 也拿不到時的最後手段。
        chunks_by_id = self._topic_chunk_texts(
            summary.tag, [s.article_id for s in summary.top_sources]
        )
        items = [
            (s.title, chunks_by_id.get(s.article_id) or ([s.snippet] if s.snippet else []))
            for s in summary.top_sources
        ]
        sources_block = _assemble_source_blocks(items)
        prompt = TOPIC_PROMPT.format(
            tag=sanitize_prompt_input(summary.tag),
            sources=sanitize_prompt_input(sources_block),
        )
        return self.llm_client.chat([{"role": "user", "content": prompt}])

    def _topic_chunk_texts(
        self, tag: str, article_ids: list[str], per_source: int = MAX_CHUNKS_PER_TOPIC_SOURCE
    ) -> dict[str, list[str]]:
        """Best-matching chunk texts per top source.

        OR-combined query bounded to the already-selected top-k articles —
        the bounded scope is what makes OR safe here (unbounded OR against
        the whole corpus measurably hurt ranking on the 80q baseline).
        """
        out: dict[str, list[str]] = {aid: [] for aid in article_ids}
        if not article_ids:
            return out
        chunk_query = build_fts_query_or(tag, column="chunk_text")
        if chunk_query:
            placeholders = ",".join("?" for _ in article_ids)
            rows = self.db.execute(
                f"SELECT article_id, chunk_text_raw, bm25(chunks_fts) AS rs "
                f"FROM chunks_fts "
                f"WHERE chunks_fts MATCH ? AND article_id IN ({placeholders}) "
                f"ORDER BY rs ASC",
                (chunk_query, *article_ids),
            ).fetchall()
            for aid, text, _rs in rows:
                if len(out[aid]) < per_source:
                    out[aid].append(text or "")
        # 沒有 chunk 命中的來源（未分塊的短文，或 OR 查詢在該篇無命中）
        # 退回全文（截斷）：仍然是真原文，勝過 16-token snippet。
        missing = [aid for aid, texts in out.items() if not texts]
        if missing:
            placeholders = ",".join("?" for _ in missing)
            content_rows = self.db.execute(
                f"SELECT article_id, content_raw FROM articles_fts "
                f"WHERE article_id IN ({placeholders})",
                missing,
            ).fetchall()
            for aid, content in content_rows:
                if content:
                    out[aid].append(content)
        return out

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
            "prompt_revision": PROMPT_REVISION,
        }
        key = cache_key("concept_perspectives", args)
        cached = cache_get(self.db, key)
        if cached is not None:
            return _concept_perspectives_from_dict(cached)

        result = self._concept_perspectives_structured(concept, filter, top_sources, per_source)
        try:
            if self.llm_client is not None:
                self._concept_perspectives_apply_llm(result)
        except LLMClientError as exc:
            # 同 topic_summary：LLM 失敗不入快取。
            logger.warning("concept_perspectives LLM call failed (%s); narrative=None", exc)
            return result
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
        """May raise LLMClientError — the caller decides whether to cache."""
        if self.llm_client is None or not result.per_source_views:
            return
        items = [(v.source_ref.title, list(v.excerpts)) for v in result.per_source_views]
        per_source_block = _assemble_source_blocks(items)
        prompt = CONCEPT_PROMPT.format(
            concept=sanitize_prompt_input(result.concept),
            per_source_views=sanitize_prompt_input(per_source_block),
        )
        text = self.llm_client.chat([{"role": "user", "content": prompt}])
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
