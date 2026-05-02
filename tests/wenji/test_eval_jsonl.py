"""Tests for wenji.eval.jsonl."""

from __future__ import annotations

import pytest

from wenji.core.errors import IngestError
from wenji.eval.jsonl import load_candidates


def write_jsonl(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_minimal(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "因信稱義", "expected_keywords": ["恩典", "稱義"]}',
        ],
    )
    cands = load_candidates(p)
    assert len(cands) == 1
    c = cands[0]
    assert c.id == 1
    assert c.query == "因信稱義"
    assert c.expected_keywords == ("恩典", "稱義")
    assert c.expected_article_hints == ()


def test_load_full_schema(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 7, "query": "Q", "expected_keywords": ["a"], '
            '"expected_article_hints": ["t1"], "category": "theology", "source": "test"}',
        ],
    )
    c = load_candidates(p)[0]
    assert c.expected_article_hints == ("t1",)
    assert c.category == "theology"
    assert c.source == "test"


def test_skip_blank_and_comment_lines(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            "# comment line",
            "",
            '{"id": 1, "query": "Q", "expected_keywords": ["a"]}',
            "   # indented comment",
            '{"id": 2, "query": "Q2", "expected_keywords": ["b"]}',
        ],
    )
    cands = load_candidates(p)
    assert [c.id for c in cands] == [1, 2]


def test_missing_query_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "expected_keywords": ["a"]}',
        ],
    )
    with pytest.raises(IngestError, match="missing required field 'query'"):
        load_candidates(p)


def test_missing_expected_keywords_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q"}',
        ],
    )
    with pytest.raises(IngestError, match="expected_keywords"):
        load_candidates(p)


def test_invalid_json_raises_with_lineno(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "expected_keywords": ["a"]}',
            "{not json",
        ],
    )
    with pytest.raises(IngestError, match="line 2"):
        load_candidates(p)


def test_empty_query_raises(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "  ", "expected_keywords": ["a"]}',
        ],
    )
    with pytest.raises(IngestError, match="non-empty"):
        load_candidates(p)


def test_id_auto_assigned_from_lineno(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"query": "Q1", "expected_keywords": ["a"]}',
            '{"query": "Q2", "expected_keywords": ["b"]}',
        ],
    )
    cands = load_candidates(p)
    assert [c.id for c in cands] == [1, 2]


def test_keywords_string_coerced_to_tuple(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "expected_keywords": "single"}',
        ],
    )
    c = load_candidates(p)[0]
    assert c.expected_keywords == ("single",)


def test_extras_preserved(tmp_path):
    p = write_jsonl(
        tmp_path / "c.jsonl",
        [
            '{"id": 1, "query": "Q", "expected_keywords": ["a"], "custom_field": "x"}',
        ],
    )
    c = load_candidates(p)[0]
    assert c.extras == {"custom_field": "x"}


def test_missing_file_raises(tmp_path):
    with pytest.raises(IngestError, match="not found"):
        load_candidates(tmp_path / "nope.jsonl")
