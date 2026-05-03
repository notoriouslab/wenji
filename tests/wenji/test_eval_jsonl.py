"""Tests for wenji.eval.jsonl (multi-path schema, v0.3.1)."""

from __future__ import annotations

import pytest

from wenji.core.errors import IngestError
from wenji.eval.jsonl import (
    Candidate,
    GoldPath,
    load_candidates,
    wrap_legacy_candidate,
)


def write_jsonl(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_minimal_multi_path(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "因信稱義", "gold_paths": ['
            '{"path_tag": "p1", "keywords": ["恩典", "稱義"]}'
            "]}",
        ],
    )
    cands = load_candidates(p)
    assert len(cands) == 1
    c = cands[0]
    assert c.id == 1
    assert c.query == "因信稱義"
    assert len(c.gold_paths) == 1
    gp = c.gold_paths[0]
    assert gp.path_tag == "p1"
    assert gp.keywords == ("恩典", "稱義")
    assert gp.article_hints == ()


def test_load_multiple_paths(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 7, "query": "Q", "gold_paths": ['
            '{"path_tag": "a", "keywords": ["x"], "article_hints": ["t1"]},'
            '{"path_tag": "b", "keywords": ["y"], "expected_direction": "support"}'
            "], \"category\": \"theology\", \"source\": \"test\"}",
        ],
    )
    c = load_candidates(p)[0]
    assert len(c.gold_paths) == 2
    assert c.gold_paths[0].article_hints == ("t1",)
    assert c.gold_paths[1].expected_direction == "support"
    assert c.category == "theology"
    assert c.source == "test"


def test_skip_blank_and_comment_lines(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            "# comment line",
            "",
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "d", "keywords": ["a"]}]}',
            "   # indented comment",
            '{"id": 2, "query": "Q2", "gold_paths": [{"path_tag": "d", "keywords": ["b"]}]}',
        ],
    )
    cands = load_candidates(p)
    assert [c.id for c in cands] == [1, 2]


def test_legacy_schema_rejected_with_migration_hint(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "expected_keywords": ["a"]}',
        ],
    )
    with pytest.raises(IngestError, match="migrate-jsonl"):
        load_candidates(p)


def test_missing_gold_paths_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q"}',
        ],
    )
    with pytest.raises(IngestError, match="missing required field 'gold_paths'"):
        load_candidates(p)


def test_empty_gold_paths_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": []}',
        ],
    )
    with pytest.raises(IngestError, match="at least one entry"):
        load_candidates(p)


def test_gold_path_missing_keywords_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "p"}]}',
        ],
    )
    with pytest.raises(IngestError, match="missing required field 'keywords'"):
        load_candidates(p)


def test_gold_path_empty_keywords_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "p", "keywords": []}]}',
        ],
    )
    with pytest.raises(IngestError, match="must be non-empty"):
        load_candidates(p)


def test_missing_query_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "gold_paths": [{"path_tag": "d", "keywords": ["a"]}]}',
        ],
    )
    with pytest.raises(IngestError, match="missing required field 'query'"):
        load_candidates(p)


def test_invalid_json_raises_with_lineno(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "d", "keywords": ["a"]}]}',
            "{not json",
        ],
    )
    with pytest.raises(IngestError, match="line 2"):
        load_candidates(p)


def test_empty_query_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "  ", "gold_paths": [{"path_tag": "d", "keywords": ["a"]}]}',
        ],
    )
    with pytest.raises(IngestError, match="non-empty"):
        load_candidates(p)


def test_id_auto_assigned_from_lineno(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"query": "Q1", "gold_paths": [{"path_tag": "d", "keywords": ["a"]}]}',
            '{"query": "Q2", "gold_paths": [{"path_tag": "d", "keywords": ["b"]}]}',
        ],
    )
    cands = load_candidates(p)
    assert [c.id for c in cands] == [1, 2]


def test_keywords_string_coerced_to_tuple(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "d", "keywords": "single"}]}',
        ],
    )
    c = load_candidates(p)[0]
    assert c.gold_paths[0].keywords == ("single",)


def test_extras_preserved(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "d", "keywords": ["a"]}], '
            '"custom_field": "x"}',
        ],
    )
    c = load_candidates(p)[0]
    assert c.extras == {"custom_field": "x"}


def test_missing_file_raises(tmp_path):
    with pytest.raises(IngestError, match="not found"):
        load_candidates(tmp_path / "nope.jsonl")


def test_wrap_legacy_candidate_basic():
    old = {
        "id": 1,
        "query": "Q",
        "expected_keywords": ["a", "b"],
        "expected_article_hints": ["t1"],
    }
    new = wrap_legacy_candidate(old)
    assert "expected_keywords" not in new
    assert "expected_article_hints" not in new
    assert new["gold_paths"] == [
        {"path_tag": "default", "keywords": ["a", "b"], "article_hints": ["t1"]}
    ]


def test_wrap_legacy_candidate_already_multi_path_passthrough():
    old = {"id": 1, "query": "Q", "gold_paths": [{"path_tag": "p", "keywords": ["a"]}]}
    new = wrap_legacy_candidate(old)
    assert new == old
    assert new is not old  # returns a copy


def test_wrap_legacy_candidate_missing_keywords_raises():
    with pytest.raises(IngestError, match="missing 'expected_keywords'"):
        wrap_legacy_candidate({"id": 1, "query": "Q"})


def test_wrap_legacy_candidate_string_keywords():
    old = {"id": 1, "query": "Q", "expected_keywords": "single"}
    new = wrap_legacy_candidate(old)
    assert new["gold_paths"][0]["keywords"] == ["single"]
