"""Query intent classification with dependency-injected keyword maps.

Ports ``logos/scripts/rag/query.py``: ``detect_intent`` (keyword-based,
shallow intent → boost type set) and ``classify_intent`` (scripture /
person / topic detection with per-intent alpha and keyword_boost).

The wenji port keeps the algorithm corpus-agnostic — keyword lists,
intent→source_type mappings, and scripture patterns are caller-injected
or constructor-defaulted (no logos curation bundled).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

DEFAULT_INTENT = "general"
DEFAULT_ALPHA = 0.5
DEFAULT_KEYWORD_BOOST = 1.0


class IntentClassifier:
    """Detect and classify query intent against caller-injected keyword maps.

    Two complementary methods:

    - :meth:`detect_intent`: shallow keyword match → intent name (e.g.,
      "apologetics" / "general"). Drives the RRF intent-boost layer via
      :meth:`get_boost_types`.
    - :meth:`classify_intent`: structured classification (scripture /
      person / topic) returning per-intent alpha and keyword_boost
      configuration for downstream tuning.
    """

    def __init__(
        self,
        intent_keywords: dict[str, list[str]],
        intent_source_types: dict[str, list[str] | set[str]] | None = None,
        default_intent: str = DEFAULT_INTENT,
        scripture_pattern: re.Pattern[str] | None = None,
        generic_entities: set[str] | None = None,
    ) -> None:
        self.intent_keywords = intent_keywords
        self.intent_source_types: dict[str, set[str]] = {
            k: set(v) for k, v in (intent_source_types or {}).items()
        }
        self.default_intent = default_intent
        self.scripture_pattern = scripture_pattern
        self.generic_entities = generic_entities or set()

    def detect_intent(self, query: str) -> str:
        """Match query against keyword lists; return first matching intent name.

        Iteration order follows ``intent_keywords`` insertion order. Returns
        ``default_intent`` when no keyword in any list is found.
        """
        for intent, keywords in self.intent_keywords.items():
            if any(kw in query for kw in keywords):
                return intent
        return self.default_intent

    def classify_intent(
        self, query: str, entities: list[Any] | None = None
    ) -> dict[str, Any]:
        """Structured classification → ``{intent, alpha, keyword_boost}``.

        Recognises:

        - ``scripture``: scripture reference matched (alpha=0.3, boost=2.0)
        - ``person``: subject entity is person and not in generic_entities
          (alpha=0.7, boost=1.0)
        - ``topic``: fallback (alpha=0.5, boost=1.0)
        """
        if self.scripture_pattern is not None and self.scripture_pattern.search(query):
            return {"intent": "scripture", "alpha": 0.3, "keyword_boost": 2.0}
        if entities:
            first = entities[0]
            if isinstance(first, dict):
                subj_name = first.get("name", "")
                subj_type = first.get("type", "")
            else:
                subj_name = getattr(first, "name", "")
                subj_type = getattr(first, "type", "")
            if subj_type == "person" and subj_name not in self.generic_entities:
                return {"intent": "person", "alpha": 0.7, "keyword_boost": 1.0}
        return {"intent": "topic", "alpha": DEFAULT_ALPHA, "keyword_boost": DEFAULT_KEYWORD_BOOST}

    def get_boost_types(self, intent: str) -> set[str] | None:
        """Return the source_type boost set for the given intent, or None.

        ``general`` (or any value equal to ``default_intent``) always
        returns None — no boost layer applied to general queries.
        """
        if intent == self.default_intent:
            return None
        return self.intent_source_types.get(intent)

    # ----- Multi-source loading API (Decision 4) -----

    @classmethod
    def load_example(cls, name: str) -> dict[str, list[str]]:
        """Return raw intent_keywords mapping from a wheel-bundled example."""
        pkg = "wenji.examples." + name.replace("-", "_")
        try:
            ref = resources.files(pkg).joinpath("intent_keywords.json")
        except (ModuleNotFoundError, AttributeError) as exc:
            raise FileNotFoundError(f"unknown example: {name}") from exc
        if not ref.is_file():
            raise FileNotFoundError(
                f"example {name} has no intent_keywords.json"
            )
        return json.loads(ref.read_text(encoding="utf-8"))

    @classmethod
    def from_sources(
        cls,
        sources: list[str],
        intent_source_types: dict[str, list[str] | set[str]] | None = None,
        default_intent: str = DEFAULT_INTENT,
        scripture_pattern: re.Pattern[str] | None = None,
        generic_entities: set[str] | None = None,
    ) -> "IntentClassifier":
        """Compose ``intent_keywords`` from multiple sources (last-write-wins).

        ``intent_source_types`` is corpus-deployment-specific and is NOT
        loaded from examples — pass it via this constructor argument when
        the deployment needs RRF intent boosts.
        """
        merged: dict[str, list[str]] = {}
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
        return cls(
            intent_keywords=merged,
            intent_source_types=intent_source_types,
            default_intent=default_intent,
            scripture_pattern=scripture_pattern,
            generic_entities=generic_entities,
        )
