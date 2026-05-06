"""Tests for ``wenji eval migrate-jsonl`` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wenji.cli import app
from wenji.eval.jsonl import load_candidates

runner = CliRunner()


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_migrate_round_trip_equivalence(tmp_path):
    src = _write(
        tmp_path / "src.jsonl",
        [
            '{"id": 1, "query": "Q", "expected_keywords": ["a", "b"], '
            '"expected_article_hints": ["t1"], "category": "c", "source": "s"}',
        ],
    )
    dst = tmp_path / "dst.jsonl"

    result = runner.invoke(app, ["eval", "migrate-jsonl", str(src), str(dst)])
    assert result.exit_code == 0
    assert "migrated 1 legacy" in result.stderr if hasattr(result, "stderr") else True

    cands = load_candidates(dst)
    assert len(cands) == 1
    c = cands[0]
    assert c.id == 1
    assert c.query == "Q"
    assert c.category == "c"
    assert c.source == "s"
    assert len(c.gold_paths) == 1
    gp = c.gold_paths[0]
    assert gp.path_tag == "default"
    assert gp.keywords == ("a", "b")
    assert gp.article_hints == ("t1",)


def test_migrate_already_multi_path_passthrough(tmp_path):
    src = _write(
        tmp_path / "src.jsonl",
        [
            '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "p", "keywords": ["a"]}]}',
        ],
    )
    dst = tmp_path / "dst.jsonl"
    result = runner.invoke(app, ["eval", "migrate-jsonl", str(src), str(dst)])
    assert result.exit_code == 0

    out_text = dst.read_text(encoding="utf-8")
    out_obj = json.loads(out_text.strip().split("\n")[0])
    assert out_obj["gold_paths"] == [{"path_tag": "p", "keywords": ["a"]}]


def test_migrate_preserves_blank_and_comment_lines(tmp_path):
    src = _write(
        tmp_path / "src.jsonl",
        [
            "# comment",
            "",
            '{"id": 1, "query": "Q", "expected_keywords": ["a"]}',
        ],
    )
    dst = tmp_path / "dst.jsonl"
    runner.invoke(app, ["eval", "migrate-jsonl", str(src), str(dst)])
    text = dst.read_text(encoding="utf-8")
    lines = text.strip().split("\n")
    assert lines[0] == "# comment"
    assert lines[1] == ""
    assert "gold_paths" in lines[2]


def test_migrate_string_keywords(tmp_path):
    src = _write(
        tmp_path / "src.jsonl",
        [
            '{"id": 1, "query": "Q", "expected_keywords": "single"}',
        ],
    )
    dst = tmp_path / "dst.jsonl"
    runner.invoke(app, ["eval", "migrate-jsonl", str(src), str(dst)])
    cands = load_candidates(dst)
    assert cands[0].gold_paths[0].keywords == ("single",)


def test_classical_examples_loadable_after_migration():
    """The repo's examples/eval.jsonl SHALL be multi-path (already migrated).

    This is a regression test: if anyone reverts examples/eval.jsonl to legacy
    single-path schema, this test catches it.
    """
    examples = Path(__file__).resolve().parents[2] / "examples" / "eval.jsonl"
    cands = load_candidates(examples)
    assert len(cands) == 10
    for c in cands:
        assert c.gold_paths, f"candidate {c.id} has empty gold_paths"
        assert c.gold_paths[0].path_tag == "default"
