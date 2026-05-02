"""Tests for wenji.ingest.frontmatter."""

from __future__ import annotations

from pathlib import Path

import pytest

from wenji.core.errors import IngestError
from wenji.ingest.frontmatter import (
    derive_source_type,
    load_article,
    parse_markdown,
)


def write_md(path: Path, frontmatter_yaml: str, body: str) -> None:
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


def test_parse_markdown_basic(tmp_path):
    md = tmp_path / "x.md"
    write_md(md, "title: Hello\ntags: [a, b]", "Body text.")
    meta, body = parse_markdown(md)
    assert meta["title"] == "Hello"
    assert meta["tags"] == ["a", "b"]
    assert body.strip() == "Body text."


def test_parse_markdown_missing_file_raises(tmp_path):
    with pytest.raises(IngestError, match="not found"):
        parse_markdown(tmp_path / "nope.md")


def test_parse_markdown_invalid_yaml_raises(tmp_path):
    md = tmp_path / "bad.md"
    md.write_text("---\ntitle: [unclosed\n---\nbody", encoding="utf-8")
    with pytest.raises(IngestError, match="failed to parse"):
        parse_markdown(md)


def test_derive_source_type_from_frontmatter():
    meta = {"source_type": "sermon"}
    assert derive_source_type(meta, "/anywhere/x.md") == "sermon"


def test_derive_source_type_from_directory_map(tmp_path):
    md = tmp_path / "sermons" / "x.md"
    md.parent.mkdir()
    md.write_text("body", encoding="utf-8")
    assert derive_source_type({}, md, directory_map={"sermons": "sermon"}) == "sermon"


def test_derive_source_type_unmapped_directory_raises(tmp_path):
    md = tmp_path / "unknown_dir" / "x.md"
    md.parent.mkdir()
    md.write_text("body", encoding="utf-8")
    with pytest.raises(IngestError, match="unmapped directory"):
        derive_source_type({}, md, directory_map={"sermons": "sermon"})


def test_derive_source_type_no_map_no_frontmatter_raises():
    with pytest.raises(IngestError, match="no source_type"):
        derive_source_type({}, "/some/x.md")


def test_load_article_combines(tmp_path):
    md = tmp_path / "sermons" / "s1.md"
    md.parent.mkdir()
    write_md(md, "title: T1", "Body.")
    article = load_article(md, directory_map={"sermons": "sermon"})
    assert article.metadata["title"] == "T1"
    assert article.body.strip() == "Body."
    assert article.source_type == "sermon"
    assert article.path == md
