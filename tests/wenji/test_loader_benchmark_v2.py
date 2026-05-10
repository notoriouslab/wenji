"""Tests for loader_benchmark_v2 (v2 benchmark snapshot loader)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from wenji.core.errors import IngestError
from wenji.eval.loader_benchmark_v2 import load_benchmark_v2_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "tests" / "benchmark_80_v2_snapshot.json"


def test_snapshot_loads_80_candidates():
    cands, meta = load_benchmark_v2_snapshot(SNAPSHOT_PATH)
    assert len(cands) == 80
    assert meta.source_commit


def test_snapshot_metadata_fields_present():
    _, meta = load_benchmark_v2_snapshot(SNAPSHOT_PATH)
    assert len(meta.source_commit) >= 8  # git short hash minimum
    assert meta.snapshot_taken_at
    assert meta.snapshot_source_path != ""


def test_each_question_has_at_least_one_path():
    cands, _ = load_benchmark_v2_snapshot(SNAPSHOT_PATH)
    for c in cands:
        assert len(c.gold_paths) >= 1, f"q{c.id} missing gold_paths"
        for gp in c.gold_paths:
            assert gp.keywords, f"q{c.id} path {gp.path_tag} has empty keywords"


def test_path_tags_unique_within_question():
    cands, _ = load_benchmark_v2_snapshot(SNAPSHOT_PATH)
    for c in cands:
        tags = [gp.path_tag for gp in c.gold_paths]
        assert len(set(tags)) == len(tags), f"q{c.id} duplicate path_tags: {tags}"


def test_question_ids_unique():
    cands, _ = load_benchmark_v2_snapshot(SNAPSHOT_PATH)
    ids = [c.id for c in cands]
    assert len(set(ids)) == len(ids)


def test_categories_set_to_18():
    cands, _ = load_benchmark_v2_snapshot(SNAPSHOT_PATH)
    cats = {c.category for c in cands}
    assert len(cats) == 18


def test_missing_commit_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "version": "v2",
                "categories": [{"name": "x", "questions": []}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IngestError, match="source_commit"):
        load_benchmark_v2_snapshot(bad)


def test_missing_file_raises(tmp_path):
    with pytest.raises(IngestError, match="snapshot file not found"):
        load_benchmark_v2_snapshot(tmp_path / "no.json")


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(IngestError, match="not valid JSON"):
        load_benchmark_v2_snapshot(p)


def test_empty_categories_raises(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(
        json.dumps({"source_commit": "abc", "categories": []}),
        encoding="utf-8",
    )
    with pytest.raises(IngestError, match="categories"):
        load_benchmark_v2_snapshot(p)


def test_duplicate_question_id_raises(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(
        json.dumps(
            {
                "source_commit": "abc",
                "categories": [
                    {
                        "name": "c",
                        "questions": [
                            {
                                "id": 1,
                                "query": "Q",
                                "gold_paths": [{"path_tag": "p", "keywords": ["a"]}],
                            },
                            {
                                "id": 1,
                                "query": "Q2",
                                "gold_paths": [{"path_tag": "p", "keywords": ["b"]}],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IngestError, match="duplicate question id"):
        load_benchmark_v2_snapshot(p)


def test_non_wenji_repo_path_emits_warning(tmp_path):
    p = tmp_path / "stray.json"
    p.write_text(
        json.dumps(
            {
                "source_commit": "abc",
                "version": "v2",
                "categories": [
                    {
                        "name": "c",
                        "questions": [
                            {
                                "id": 1,
                                "query": "Q",
                                "gold_paths": [{"path_tag": "p", "keywords": ["a"]}],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        load_benchmark_v2_snapshot(p)
        assert any("not inside a wenji repository" in str(rec.message) for rec in w)


def test_legacy_logos_source_commit_only_rejected(tmp_path):
    """Task 5.7: loader must NOT silently accept the legacy logos_source_commit
    key as a fallback. Without source_commit, must fail loud."""
    p = tmp_path / "legacy.json"
    p.write_text(
        json.dumps(
            {
                "logos_source_commit": "413642afa95ccc824d72a41c427b94f2cbc2e10c",
                # NOTE: no `source_commit` field
                "categories": [
                    {
                        "name": "c",
                        "questions": [
                            {
                                "id": 1,
                                "query": "Q",
                                "gold_paths": [{"path_tag": "p", "keywords": ["a"]}],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IngestError, match="source_commit"):
        load_benchmark_v2_snapshot(p)
