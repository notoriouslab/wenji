"""Tests for the wheel-bundled corpus-christian example loader."""

from __future__ import annotations

import pytest

from wenji.search.entity import EntityScorer
from wenji.search.intent import IntentClassifier


def test_entity_load_example_returns_dict():
    d = EntityScorer.load_example("corpus-christian")
    assert isinstance(d, dict)
    assert len(d) >= 40
    # all values must be valid entity types
    assert all(v in ("concept", "person", "org", "location") for v in d.values())


def test_entity_load_example_contains_core_concepts():
    d = EntityScorer.load_example("corpus-christian")
    for term in ("因信稱義", "三位一體", "宗教改革", "改革宗"):
        assert term in d, f"{term} should be in corpus-christian entity_concepts"


def test_entity_load_example_excludes_political_terms():
    """OPEN-2 / Decision 3 review: political-ethics terms SHALL NOT appear."""
    d = EntityScorer.load_example("corpus-christian")
    for term in ("同性婚姻", "墮胎", "安樂死"):
        assert term not in d, f"{term} should be filtered out (political ethics)"


def test_entity_from_sources_with_example():
    scorer = EntityScorer.from_sources(["example:corpus-christian"])
    assert "因信稱義" in scorer.entity_dict
    entities = scorer.detect_query_entities("因信稱義是什麼")
    assert any(e.name == "因信稱義" for e in entities)


def test_intent_load_example_returns_dict():
    k = IntentClassifier.load_example("corpus-christian")
    assert isinstance(k, dict)
    assert "apologetics" in k
    assert "general" in k
    assert isinstance(k["apologetics"], list)
    assert len(k["apologetics"]) >= 30


def test_intent_load_example_contains_apologetics_kw():
    k = IntentClassifier.load_example("corpus-christian")
    for kw in ("無神論", "進化論", "證明", "苦難"):
        assert kw in k["apologetics"]


def test_intent_from_sources_detects_apologetics():
    classifier = IntentClassifier.from_sources(["example:corpus-christian"])
    assert classifier.detect_intent("無神論的論證") == "apologetics"
    assert classifier.detect_intent("禱告的意義") == "general"


def test_intent_from_sources_inject_source_types():
    """intent_source_types is corpus-deployment-specific, not loaded from example."""
    classifier = IntentClassifier.from_sources(
        ["example:corpus-christian"],
        intent_source_types={"apologetics": ["bol", "teaching"]},
    )
    assert classifier.get_boost_types("apologetics") == {"bol", "teaching"}
    # Without injection, source_types is empty
    classifier_bare = IntentClassifier.from_sources(["example:corpus-christian"])
    assert classifier_bare.get_boost_types("apologetics") is None


def test_unknown_example_raises():
    with pytest.raises(FileNotFoundError):
        EntityScorer.load_example("does-not-exist")
    with pytest.raises(FileNotFoundError):
        IntentClassifier.load_example("does-not-exist")
