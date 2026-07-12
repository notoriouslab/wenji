"""Query intent classification with dependency-injected keyword maps.

Ports an upstream RAG query module: ``detect_intent`` (keyword-based,
shallow intent → boost type set) drives the RRF intent-boost layer.

The wenji port keeps the algorithm corpus-agnostic — keyword lists,
and intent→source_type mappings are caller-injected
or constructor-defaulted (no upstream curation bundled).
"""

from __future__ import annotations

import json
from importlib import resources

from wenji.search._sources import merge_sources

DEFAULT_INTENT = "general"
DEFAULT_ALPHA = 0.5
DEFAULT_KEYWORD_BOOST = 1.0


class IntentClassifier:
    """Detect and classify query intent against caller-injected keyword maps.

    :meth:`detect_intent` does a shallow keyword match → intent name (e.g.,
    "apologetics" / "general") and drives the RRF intent-boost layer via
    :meth:`get_boost_types`.
    """

    def __init__(
        self,
        intent_keywords: dict[str, list[str]],
        intent_source_types: dict[str, list[str] | set[str]] | None = None,
        default_intent: str = DEFAULT_INTENT,
    ) -> None:
        self.intent_keywords = intent_keywords
        self.intent_source_types: dict[str, set[str]] = {
            k: set(v) for k, v in (intent_source_types or {}).items()
        }
        self.default_intent = default_intent

    def detect_intent(self, query: str) -> str:
        """Match query against keyword lists; return first matching intent name.

        Iteration order follows ``intent_keywords`` insertion order. Returns
        ``default_intent`` when no keyword in any list is found.
        """
        for intent, keywords in self.intent_keywords.items():
            if any(kw in query for kw in keywords):
                return intent
        return self.default_intent

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
            raise FileNotFoundError(f"example {name} has no intent_keywords.json")
        return json.loads(ref.read_text(encoding="utf-8"))

    @classmethod
    def from_sources(
        cls,
        sources: list[str],
        intent_source_types: dict[str, list[str] | set[str]] | None = None,
        default_intent: str = DEFAULT_INTENT,
    ) -> IntentClassifier:
        """Compose ``intent_keywords`` from multiple sources (last-write-wins).

        ``intent_source_types`` is corpus-deployment-specific and is NOT
        loaded from examples — pass it via this constructor argument when
        the deployment needs RRF intent boosts.
        """
        merged: dict[str, list[str]] = merge_sources(sources, cls.load_example)
        return cls(
            intent_keywords=merged,
            intent_source_types=intent_source_types,
            default_intent=default_intent,
        )
