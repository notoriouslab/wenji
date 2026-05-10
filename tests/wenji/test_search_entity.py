"""Tests for ``wenji.search.entity`` — EntityScorer + multi-source loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wenji.search.entity import (
    EntityScorer,
    QueryEntity,
    _check_entity_in_text,
)


@pytest.fixture
def scorer():
    entity_dict = {
        "因信稱義": "concept",
        "馬丁路德": "person",
        "教會": "org",
    }
    alias_map = {
        "馬丁路德": ["路德", "Martin Luther"],
    }
    return EntityScorer(entity_dict=entity_dict, alias_map=alias_map, alpha=0.5)


def test_detect_query_entities_finds_concept(scorer):
    entities = scorer.detect_query_entities("因信稱義是什麼意思")
    assert len(entities) == 1
    assert entities[0].name == "因信稱義"
    assert entities[0].type == "concept"
    assert entities[0].role == "subject"


def test_detect_query_entities_attaches_aliases(scorer):
    entities = scorer.detect_query_entities("馬丁路德的神學")
    assert entities[0].name == "馬丁路德"
    assert "路德" in entities[0].aliases
    assert "Martin Luther" in entities[0].aliases


def test_detect_query_entities_subject_promotion_skips_location():
    """Concept SHALL be promoted to subject over location even if location is first."""
    scorer = EntityScorer(
        entity_dict={"耶路撒冷": "location", "救贖": "concept"},
        alias_map={},
    )
    entities = scorer.detect_query_entities("耶路撒冷的救贖意義")
    by_name = {e.name: e.role for e in entities}
    assert by_name["救贖"] == "subject"
    assert by_name["耶路撒冷"] == "supporting"


def test_detect_query_entities_empty_dict_returns_empty():
    scorer = EntityScorer(entity_dict={}, alias_map={})
    assert scorer.detect_query_entities("任何查詢") == []


def test_expand_query_with_aliases_appends_unique(scorer):
    entities = scorer.detect_query_entities("馬丁路德的神學")
    expanded = scorer.expand_query_with_aliases("馬丁路德的神學", entities)
    assert "路德" in expanded
    assert "Martin Luther" in expanded
    # original tokens preserved
    assert "馬丁路德" in expanded


def test_expand_query_with_aliases_skips_already_in_query(scorer):
    entities = [
        QueryEntity(
            name="馬丁路德",
            type="person",
            role="subject",
            weight=1.0,
            aliases=["路德"],
        )
    ]
    # "路德" already in query → not appended
    expanded = scorer.expand_query_with_aliases("路德的神學", entities)
    assert expanded.count("路德") == 1


def test_check_entity_in_text_alias_match():
    assert _check_entity_in_text("馬丁路德", ["路德", "Luther"], "路德的書")
    assert not _check_entity_in_text("馬丁路德", ["路德"], "完全不相關文字")


def test_score_and_rerank_alpha_blends_correctly(scorer):
    """Linear hybrid formula: final = alpha * relevance + (1 - alpha) * entity_coverage."""
    articles = [
        {
            "article_id": "a1",
            "_rankingScore": 0.5,
            "title": "因信稱義",
            "content": "因信稱義是宗教改革的核心",
        },
        {
            "article_id": "a2",
            "_rankingScore": 0.5,
            "title": "其他主題",
            "content": "完全沒提到那個概念",
        },
    ]
    out, _ = scorer.score_and_rerank(articles, "因信稱義是什麼")
    by_id = {r["article_id"]: r["_entityScore"] for r in out}
    # a1: title hit (signal 1.0) → coverage 1.0; final = 0.5 * 0.5 + 0.5 * 1.0 = 0.75
    # a2: no hit, concept type → soft penalty entity_coverage = 0.05; final = 0.5 * 0.5 + 0.5 * 0.05 = 0.275
    assert by_id["a1"] > by_id["a2"]
    assert abs(by_id["a1"] - 0.75) < 1e-3
    assert abs(by_id["a2"] - 0.275) < 1e-3


def test_score_and_rerank_hard_filters_person_subject_miss(scorer):
    """When subject is person and not in article, article SHALL be filtered."""
    articles = [
        {
            "article_id": "a1",
            "_rankingScore": 0.9,
            "title": "其他人的故事",
            "content": "完全沒提到任何特定人物",
        },
    ]
    out, entities = scorer.score_and_rerank(articles, "馬丁路德的神學")
    assert entities[0].name == "馬丁路德"
    assert entities[0].type == "person"
    assert out == []  # hard-filtered


def test_score_and_rerank_no_entities_passes_through(scorer):
    """No entities detected → articles pass through with explanation marker."""
    articles = [{"article_id": "a1", "_rankingScore": 0.5}]
    out, entities = scorer.score_and_rerank(articles, "完全沒實體的查詢abc")
    assert entities == []
    assert out[0]["_explanation"] == "純文字搜尋"


def test_score_and_rerank_higher_alpha_weights_relevance_more(scorer):
    """alpha closer to 1 SHALL favour articles with high _rankingScore over high entity coverage."""
    articles = [
        {
            "article_id": "high_rel_low_ent",
            "_rankingScore": 1.0,
            "title": "其他主題",
            "content": "沒提及概念",
        },
        {
            "article_id": "low_rel_high_ent",
            "_rankingScore": 0.1,
            "title": "因信稱義",
            "content": "因信稱義是核心",
        },
    ]
    # alpha=0.9 → relevance dominates
    out_high_alpha, _ = scorer.score_and_rerank([dict(a) for a in articles], "因信稱義", alpha=0.9)
    # alpha=0.1 → entity dominates
    out_low_alpha, _ = scorer.score_and_rerank([dict(a) for a in articles], "因信稱義", alpha=0.1)
    # under high alpha, low_rel_high_ent SHALL be lower-ranked than under low alpha
    high_alpha_rank = next(
        i for i, r in enumerate(out_high_alpha) if r["article_id"] == "low_rel_high_ent"
    )
    low_alpha_rank = next(
        i for i, r in enumerate(out_low_alpha) if r["article_id"] == "low_rel_high_ent"
    )
    assert high_alpha_rank > low_alpha_rank or out_high_alpha[0]["article_id"] == "high_rel_low_ent"


def test_from_sources_rejects_http():
    with pytest.raises(ValueError, match="network sources"):
        EntityScorer.from_sources(["https://example.com/x.json"])


def test_from_sources_rejects_unknown_example():
    with pytest.raises(FileNotFoundError):
        EntityScorer.from_sources(["example:nonexistent-corpus-xyz"])


def test_from_sources_loads_file_path(tmp_path: Path):
    p = tmp_path / "dict.json"
    p.write_text(json.dumps({"測試": "concept"}), encoding="utf-8")
    scorer = EntityScorer.from_sources([str(p)])
    assert scorer.entity_dict == {"測試": "concept"}


def test_from_sources_last_write_wins(tmp_path: Path):
    p1 = tmp_path / "a.json"
    p2 = tmp_path / "b.json"
    p1.write_text(json.dumps({"x": "concept", "y": "person"}), encoding="utf-8")
    p2.write_text(json.dumps({"x": "person"}), encoding="utf-8")
    scorer = EntityScorer.from_sources([str(p1), str(p2)])
    assert scorer.entity_dict == {"x": "person", "y": "person"}


def test_from_sources_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        EntityScorer.from_sources([str(tmp_path / "does_not_exist.json")])
