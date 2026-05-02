"""Rule matching logic for classification.

Match fields (all AND-combined when present on a rule):

- ``source_type`` (required): exact match against ``articles_meta.source_type``
- ``subtype`` (optional): exact match against ``articles_meta.subtype``
- ``title_regex`` (optional): regex search against ``articles_meta.title``
- ``tag`` (optional): exact match against any element of the tags JSON list
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from wenji.classify.axes_loader import Rule


@dataclass(frozen=True)
class Article:
    """Subset of articles_meta needed for classification."""

    article_id: str
    source_type: str | None
    subtype: str | None
    title: str | None
    tags_json: str | None


def _parse_tags(tags_json: str | None) -> list[str]:
    if not tags_json:
        return []
    try:
        loaded = json.loads(tags_json)
    except (ValueError, TypeError):
        return []
    if isinstance(loaded, list):
        return [str(t) for t in loaded]
    if isinstance(loaded, str):
        return [loaded]
    return []


def rule_matches(rule: Rule, article: Article) -> bool:
    """Return True iff ``rule`` matches ``article`` on all configured fields."""
    if rule.source_type != (article.source_type or ""):
        return False
    if rule.subtype is not None and rule.subtype != (article.subtype or ""):
        return False
    if rule._compiled_regex is not None:
        if not article.title:
            return False
        if not rule._compiled_regex.search(article.title):
            return False
    if rule.tag is not None:
        if rule.tag not in _parse_tags(article.tags_json):
            return False
    return True
