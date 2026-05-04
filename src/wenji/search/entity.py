"""Entity-aware scoring with dependency-injected entity_dict and alias_map.

Ports the dual-signal scoring model from
``logos/scripts/entity_scorer.py``:

  ``final_score = alpha * relevance + (1 - alpha) * entity_coverage``

The wenji port keeps the algorithm corpus-agnostic — entity_dict and
alias_map are caller-injected (no logos curation bundled). For the
multi-source loader that composes wheel-bundled examples with private
dictionaries see :class:`EntityScorer.from_sources`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

DEFAULT_ALPHA = 0.5
SUBJECT_PRIORITY = {"concept", "person", "org"}
CO_OCCURRENCE_BONUS = 0.15


@dataclass
class QueryEntity:
    name: str
    type: str  # person / org / location / concept
    role: str  # subject / supporting
    weight: float  # subject=1.0, supporting=0.3
    aliases: list[str] = field(default_factory=list)


@dataclass
class EntityMatch:
    entity: str
    role: str
    in_title: bool = False
    in_content: bool = False
    signal: float = 0.0


@dataclass
class ScoringResult:
    original_score: float
    entity_coverage: float
    final_score: float
    matches: list[EntityMatch] = field(default_factory=list)
    co_occurrence_bonus: float = 0.0
    filtered: bool = False
    explanation: str = ""


def _check_entity_in_text(entity_name: str, aliases: list[str], text: str) -> bool:
    """Return True iff entity name OR any alias appears literally in text."""
    if entity_name in text:
        return True
    return any(alias in text for alias in aliases)


def _check_co_occurrence(entities: list[QueryEntity], content: str) -> float:
    """Return CO_OCCURRENCE_BONUS if subject + any supporting entity share a paragraph."""
    if len(entities) < 2 or not content:
        return 0.0
    subject = entities[0]
    supporters = entities[1:]
    for para in content.split("\n\n"):
        if len(para) < 10:
            continue
        if not _check_entity_in_text(subject.name, subject.aliases, para):
            continue
        for sup in supporters:
            if _check_entity_in_text(sup.name, sup.aliases, para):
                return CO_OCCURRENCE_BONUS
    return 0.0


class EntityScorer:
    """Dual-signal entity scorer (relevance × entity_coverage blend).

    Both ``entity_dict`` and ``alias_map`` are caller-injected. The class
    bundles no corpus-specific data; loaders are provided separately via
    :meth:`load_example` and :meth:`from_sources`.
    """

    def __init__(
        self,
        entity_dict: dict[str, str],
        alias_map: dict[str, str | list[str]] | None = None,
        alpha: float = DEFAULT_ALPHA,
    ) -> None:
        self.entity_dict = entity_dict
        self.alias_map = alias_map or {}
        self.alpha = alpha

    def detect_query_entities(self, query: str) -> list[QueryEntity]:
        """Extract entities from query via longest-match-first dictionary lookup.

        First entity (by position, with concept/person/org priority for
        subject promotion) is the subject (weight 1.0); rest are supporting
        (weight 0.3). Aliases attached when present in alias_map.
        """
        if not self.entity_dict:
            return []
        candidates = sorted(self.entity_dict.keys(), key=len, reverse=True)

        found: list[tuple[int, int, str, str]] = []
        used: list[tuple[int, int]] = []
        for term in candidates:
            start = 0
            while True:
                idx = query.find(term, start)
                if idx == -1:
                    break
                end = idx + len(term)
                if not any(not (end <= us or idx >= ue) for us, ue in used):
                    found.append((idx, end, term, self.entity_dict[term]))
                    used.append((idx, end))
                start = idx + 1
        found.sort(key=lambda x: x[0])

        subject_idx = 0
        if found and found[0][3] not in SUBJECT_PRIORITY:
            for j, (_, _, _, etype) in enumerate(found):
                if etype in SUBJECT_PRIORITY:
                    subject_idx = j
                    break

        entities: list[QueryEntity] = []
        for i, (_, _, name, etype) in enumerate(found):
            role = "subject" if i == subject_idx else "supporting"
            weight = 1.0 if i == subject_idx else 0.3
            aliases = self._aliases_for(name)
            entities.append(
                QueryEntity(
                    name=name,
                    type=etype,
                    role=role,
                    weight=weight,
                    aliases=aliases,
                )
            )
        return entities

    def _aliases_for(self, name: str) -> list[str]:
        out: list[str] = []
        if name in self.alias_map:
            v = self.alias_map[name]
            out.extend([v] if isinstance(v, str) else list(v))
        for alias_key, alias_val in self.alias_map.items():
            targets = [alias_val] if isinstance(alias_val, str) else alias_val
            if name in targets and alias_key not in out:
                out.append(alias_key)
        return out

    def expand_query_with_aliases(
        self, query: str, entities: list[QueryEntity]
    ) -> str:
        """Append unique alias terms to query for richer BM25 matching."""
        extra: list[str] = []
        for ent in entities:
            for alias in ent.aliases:
                if alias not in query and alias not in extra:
                    extra.append(alias)
        return query + " " + " ".join(extra) if extra else query

    def score_article(
        self,
        article: dict[str, Any],
        entities: list[QueryEntity],
        alpha: float | None = None,
    ) -> ScoringResult:
        """Compute single-article ScoringResult per dual-signal model."""
        a = self.alpha if alpha is None else alpha
        relevance = float(article.get("_rankingScore", 0.0))
        title = article.get("title") or ""
        content = article.get("content") or article.get("description") or ""

        if not entities:
            return ScoringResult(
                original_score=relevance,
                entity_coverage=0.0,
                final_score=relevance,
                explanation="純文字搜尋（查詢無實體）",
            )

        matches: list[EntityMatch] = []
        total_weight = sum(e.weight for e in entities)
        weighted_signal_sum = 0.0
        for ent in entities:
            in_title = _check_entity_in_text(ent.name, ent.aliases, title)
            in_content = _check_entity_in_text(ent.name, ent.aliases, content)
            if in_title:
                signal = 1.0
            elif in_content:
                signal = 0.6
            else:
                signal = 0.0
            weighted_signal_sum += ent.weight * signal
            matches.append(
                EntityMatch(
                    entity=ent.name,
                    role=ent.role,
                    in_title=in_title,
                    in_content=in_content,
                    signal=signal,
                )
            )

        entity_coverage = (
            weighted_signal_sum / total_weight if total_weight > 0 else 0.0
        )
        co_bonus = _check_co_occurrence(entities, content)
        entity_coverage = min(1.0, entity_coverage + co_bonus)

        subject_match = matches[0]
        subject_type = entities[0].type
        if subject_match.signal == 0.0:
            if subject_type in ("person", "org"):
                return ScoringResult(
                    original_score=relevance,
                    entity_coverage=0.0,
                    final_score=0.0,
                    matches=matches,
                    co_occurrence_bonus=co_bonus,
                    filtered=True,
                    explanation=f"已過濾：主詞「{subject_match.entity}」未出現在文章中",
                )
            entity_coverage = 0.05

        # Logos formula: alpha is the relevance weight (NOT entity weight).
        final = a * relevance + (1 - a) * entity_coverage

        parts: list[str] = []
        for m in matches:
            loc = "標題" if m.in_title else ("內容" if m.in_content else "未命中")
            parts.append(f"{m.entity}（{loc}）")
        explanation = "命中：" + "、".join(parts)
        if co_bonus > 0:
            explanation += "｜共現加分"

        return ScoringResult(
            original_score=relevance,
            entity_coverage=entity_coverage,
            final_score=final,
            matches=matches,
            co_occurrence_bonus=co_bonus,
            explanation=explanation,
        )

    def score_and_rerank(
        self,
        articles: list[dict[str, Any]],
        query: str,
        query_entities: list[QueryEntity] | None = None,
        alpha: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[QueryEntity]]:
        """Score and rerank articles; hard-filter person/org subject misses.

        Returns ``(reranked_articles, entities)``. Each kept article is
        annotated with ``_entityScore``, ``_entityCoverage``, ``_explanation``,
        ``_originalScore``. Articles failing hard-filter are dropped.
        """
        entities = (
            query_entities
            if query_entities is not None
            else self.detect_query_entities(query)
        )

        if not entities:
            for art in articles:
                art.setdefault("_explanation", "純文字搜尋")
            return articles, entities

        scored: list[tuple[float, dict[str, Any]]] = []
        for art in articles:
            result = self.score_article(art, entities, alpha)
            if result.filtered:
                continue
            art["_entityScore"] = round(result.final_score, 4)
            art["_entityCoverage"] = round(result.entity_coverage, 4)
            art["_explanation"] = result.explanation
            art["_originalScore"] = round(result.original_score, 4)
            art["_rankingScore"] = result.final_score
            scored.append((result.final_score, art))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [art for _, art in scored], entities

    # ----- Multi-source loading API (Decision 4) -----

    @classmethod
    def load_example(cls, name: str) -> dict[str, str]:
        """Return raw entity_dict from a wheel-bundled example.

        ``name`` matches a directory under ``wenji.examples.<name>`` (with
        hyphens replaced by underscores), and the file ``entity_concepts.json``
        SHALL exist there.
        """
        pkg = "wenji.examples." + name.replace("-", "_")
        try:
            ref = resources.files(pkg).joinpath("entity_concepts.json")
        except (ModuleNotFoundError, AttributeError) as exc:
            raise FileNotFoundError(f"unknown example: {name}") from exc
        if not ref.is_file():
            raise FileNotFoundError(
                f"example {name} has no entity_concepts.json"
            )
        return json.loads(ref.read_text(encoding="utf-8"))

    @classmethod
    def from_sources(
        cls,
        sources: list[str],
        alias_map: dict[str, str | list[str]] | None = None,
        alpha: float = DEFAULT_ALPHA,
    ) -> "EntityScorer":
        """Compose entity_dict from multiple sources (last-write-wins).

        Each ``source`` SHALL be one of:

        - ``"example:<name>"`` — wheel-bundled example
        - absolute or relative filesystem path ending in ``.json``

        Network URLs (``http://``, ``https://``) are rejected to prevent
        accidental remote fetch.
        """
        merged: dict[str, str] = {}
        for src in sources:
            if src.startswith(("http://", "https://")):
                raise ValueError(
                    f"network sources not supported in v0.3.6: {src}"
                )
            if src.startswith("example:"):
                merged.update(cls.load_example(src[len("example:") :]))
                continue
            path = Path(src)
            if not path.exists():
                raise FileNotFoundError(f"source not found: {src}")
            merged.update(json.loads(path.read_text(encoding="utf-8")))
        return cls(entity_dict=merged, alias_map=alias_map, alpha=alpha)
