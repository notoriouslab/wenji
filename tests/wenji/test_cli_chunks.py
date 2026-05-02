"""Tests for `wenji inspect-chunks` and `wenji set-chunk-strategy` subcommands."""

from __future__ import annotations

from pathlib import Path

import frontmatter
from typer.testing import CliRunner

from wenji.cli import app

runner = CliRunner()


def test_inspect_chunks_default_paragraph(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("---\ntitle: T\n---\nP1.\n\nP2.\n\nP3.", encoding="utf-8")
    r = runner.invoke(app, ["inspect-chunks", str(md), "--strategy", "paragraph"])
    assert r.exit_code == 0
    assert "strategy   : paragraph" in r.stdout


def test_inspect_chunks_bible_verses(tmp_path: Path):
    md = tmp_path / "v.md"
    md.write_text(
        "---\ntitle: T\n---\n1:1 first.\n1:2 second.\n1:3 third.",
        encoding="utf-8",
    )
    r = runner.invoke(app, ["inspect-chunks", str(md), "--strategy", "bible-verses"])
    assert r.exit_code == 0
    assert "chunks     : 3" in r.stdout


def test_inspect_chunks_unknown_strategy_exits_2(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("body", encoding="utf-8")
    r = runner.invoke(app, ["inspect-chunks", str(md), "--strategy", "nope"])
    assert r.exit_code == 2


def test_set_chunk_strategy_writes_frontmatter(tmp_path: Path):
    a = tmp_path / "a.md"
    a.write_text("---\ntitle: A\n---\nbody A", encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("body B without frontmatter", encoding="utf-8")

    r = runner.invoke(
        app,
        ["set-chunk-strategy", str(tmp_path), "--strategy", "bible-verses"],
    )
    assert r.exit_code == 0

    post_a = frontmatter.load(a)
    post_b = frontmatter.load(b)
    assert post_a["chunk_strategy"] == "bible-verses"
    assert post_b["chunk_strategy"] == "bible-verses"


def test_set_chunk_strategy_dry_run_does_not_write(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text("---\ntitle: X\n---\nbody", encoding="utf-8")
    before = md.read_text(encoding="utf-8")
    r = runner.invoke(
        app,
        ["set-chunk-strategy", str(tmp_path), "--strategy", "bible-verses", "--dry-run"],
    )
    assert r.exit_code == 0
    after = md.read_text(encoding="utf-8")
    assert before == after  # unchanged


def test_set_chunk_strategy_skip_existing(tmp_path: Path):
    md = tmp_path / "x.md"
    md.write_text(
        "---\ntitle: X\nchunk_strategy: paragraph\n---\nbody",
        encoding="utf-8",
    )
    r = runner.invoke(
        app,
        [
            "set-chunk-strategy",
            str(tmp_path),
            "--strategy",
            "bible-verses",
            "--skip-existing",
        ],
    )
    assert r.exit_code == 0
    post = frontmatter.load(md)
    assert post["chunk_strategy"] == "paragraph"  # unchanged


def test_set_chunk_strategy_unknown_exits_2(tmp_path: Path):
    r = runner.invoke(
        app,
        ["set-chunk-strategy", str(tmp_path), "--strategy", "nope"],
    )
    assert r.exit_code == 2
