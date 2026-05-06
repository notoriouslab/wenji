"""Tests for ``wenji.search.intent`` — IntentClassifier + multi-source loader."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wenji.search.entity import QueryEntity
from wenji.search.intent import IntentClassifier


@pytest.fixture
def classifier():
    return IntentClassifier(
        intent_keywords={
            "apologetics": ["護教", "信仰問答", "證明有神"],
            "general": [],
        },
        intent_source_types={"apologetics": {"bol", "teaching"}},
        default_intent="general",
    )


def test_detect_intent_keyword_match(classifier):
    assert classifier.detect_intent("護教學是什麼") == "apologetics"


def test_detect_intent_falls_back_to_default(classifier):
    assert classifier.detect_intent("一般禱告問題") == "general"


def test_detect_intent_iteration_order(classifier):
    """First matching intent wins (insertion order)."""
    c = IntentClassifier(
        intent_keywords={
            "first": ["x"],
            "second": ["x", "y"],
        }
    )
    assert c.detect_intent("xyz") == "first"


def test_get_boost_types_returns_set(classifier):
    out = classifier.get_boost_types("apologetics")
    assert out == {"bol", "teaching"}


def test_get_boost_types_general_returns_none(classifier):
    assert classifier.get_boost_types("general") is None


def test_get_boost_types_unknown_intent_returns_none(classifier):
    assert classifier.get_boost_types("nonexistent") is None


def test_get_boost_types_no_source_types_configured():
    c = IntentClassifier(intent_keywords={"x": ["a"]})
    assert c.get_boost_types("x") is None


def test_classify_intent_scripture_match():
    pat = re.compile(r"羅馬書\s*\d+")
    c = IntentClassifier(intent_keywords={}, scripture_pattern=pat)
    out = c.classify_intent("羅馬書 8 章探討")
    assert out["intent"] == "scripture"
    assert out["alpha"] == 0.3
    assert out["keyword_boost"] == 2.0


def test_classify_intent_person_with_dataclass_entity(classifier):
    entities = [QueryEntity(name="馬丁路德", type="person", role="subject", weight=1.0)]
    out = classifier.classify_intent("馬丁路德的神學", entities)
    assert out["intent"] == "person"
    assert out["alpha"] == 0.7


def test_classify_intent_person_with_dict_entity(classifier):
    entities = [{"name": "馬丁路德", "type": "person"}]
    out = classifier.classify_intent("馬丁路德的神學", entities)
    assert out["intent"] == "person"


def test_classify_intent_generic_entity_falls_back_to_topic():
    c = IntentClassifier(
        intent_keywords={},
        generic_entities={"耶穌", "基督"},
    )
    entities = [{"name": "耶穌", "type": "person"}]
    out = c.classify_intent("耶穌的教導", entities)
    assert out["intent"] == "topic"


def test_classify_intent_default_topic(classifier):
    out = classifier.classify_intent("一般查詢")
    assert out["intent"] == "topic"
    assert out["alpha"] == 0.5
    assert out["keyword_boost"] == 1.0


def test_from_sources_rejects_network():
    with pytest.raises(ValueError, match="network sources"):
        IntentClassifier.from_sources(["http://example.com/x.json"])


def test_from_sources_rejects_unknown_example():
    with pytest.raises(FileNotFoundError):
        IntentClassifier.from_sources(["example:nonexistent"])


def test_from_sources_loads_file_path(tmp_path: Path):
    p = tmp_path / "intent.json"
    p.write_text(json.dumps({"apologetics": ["護教"], "general": []}), encoding="utf-8")
    c = IntentClassifier.from_sources(
        [str(p)],
        intent_source_types={"apologetics": ["bol"]},
    )
    assert c.detect_intent("護教學") == "apologetics"
    assert c.get_boost_types("apologetics") == {"bol"}


def test_from_sources_last_write_wins(tmp_path: Path):
    p1 = tmp_path / "a.json"
    p2 = tmp_path / "b.json"
    p1.write_text(json.dumps({"x": ["aa"], "y": ["bb"]}), encoding="utf-8")
    p2.write_text(json.dumps({"x": ["cc"]}), encoding="utf-8")
    c = IntentClassifier.from_sources([str(p1), str(p2)])
    assert c.intent_keywords == {"x": ["cc"], "y": ["bb"]}
