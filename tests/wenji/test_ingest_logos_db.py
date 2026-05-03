"""Tests for the logos.db ingest adapter."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from wenji.core.errors import IngestError
from wenji.ingest.loader_logos_db import dump_logos_db


def _build_logos_db(
    db_path: Path,
    articles: list[dict],
    *,
    extra_meta_cols: list[str] | None = None,
) -> Path:
    """Build a minimal logos-compatible sqlite db for testing."""
    conn = sqlite3.connect(str(db_path))
    base_meta_cols = [
        "article_id TEXT PRIMARY KEY",
        "title TEXT",
        "source_type TEXT",
        "pub_date TEXT",
        "tags TEXT",
        "author TEXT",
        "source_url TEXT",
        "subtype TEXT",
        "description TEXT",
        "category TEXT",
        "content_hash TEXT",
    ]
    if extra_meta_cols:
        base_meta_cols.extend(extra_meta_cols)
    conn.execute(f"CREATE TABLE articles_meta ({', '.join(base_meta_cols)})")
    conn.execute(
        "CREATE TABLE articles_fts (article_id TEXT PRIMARY KEY, content TEXT)"
    )
    for a in articles:
        meta_keys = [c.split()[0] for c in base_meta_cols]
        meta_vals = [a.get(k) for k in meta_keys]
        placeholders = ",".join(["?"] * len(meta_keys))
        conn.execute(
            f"INSERT INTO articles_meta ({','.join(meta_keys)}) VALUES ({placeholders})",
            meta_vals,
        )
        conn.execute(
            "INSERT INTO articles_fts (article_id, content) VALUES (?, ?)",
            (a["article_id"], a.get("content", "")),
        )
    conn.commit()
    conn.close()
    return db_path


def test_dump_basic(tmp_path):
    db = _build_logos_db(
        tmp_path / "src.db",
        [
            {
                "article_id": "a1",
                "title": "T1",
                "source_type": "sermon",
                "pub_date": "2024-01-01",
                "tags": '["t1", "t2"]',
                "content": "body of article one",
            },
            {
                "article_id": "a2",
                "title": "T2",
                "source_type": "youtube",
                "pub_date": "2024-02-01",
                "tags": "",
                "content": "body of article two",
            },
        ],
    )
    out = tmp_path / "dump"
    manifest = dump_logos_db(db, out)
    assert manifest.article_count == 2
    assert (out / "a1.md").exists()
    assert (out / "a2.md").exists()
    assert (out / "_manifest.json").exists()


def test_frontmatter_has_seven_required_fields(tmp_path):
    db = _build_logos_db(
        tmp_path / "src.db",
        [
            {
                "article_id": "abc",
                "title": "Hello",
                "source_type": "sermon",
                "pub_date": "2024-03-15",
                "tags": '["x", "y"]',
                "source_url": "https://example.com/x",
                "content": "body",
            }
        ],
    )
    out = tmp_path / "dump"
    dump_logos_db(db, out)
    md = (out / "abc.md").read_text(encoding="utf-8")
    fm_block = md.split("---", 2)[1]
    fm = yaml.safe_load(fm_block)
    assert fm["title"] == "Hello"
    assert fm["source_type"] == "sermon"
    assert fm["article_id"] == "abc"
    assert "content_hash" in fm
    assert fm["pubDate"] == "2024-03-15"
    assert fm["tags"] == ["x", "y"]
    assert fm["source_url"] == "https://example.com/x"


def test_content_hash_propagated(tmp_path):
    body = "Hello world"
    db = _build_logos_db(
        tmp_path / "src.db",
        [
            {
                "article_id": "h1",
                "title": "T",
                "source_type": "sermon",
                "content": body,
            }
        ],
    )
    out = tmp_path / "dump"
    dump_logos_db(db, out)
    md = (out / "h1.md").read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---", 2)[1])
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert fm["content_hash"] == expected


def test_manifest_records_source_type_distribution(tmp_path):
    db = _build_logos_db(
        tmp_path / "src.db",
        [
            {"article_id": "x1", "title": "T", "source_type": "a", "content": "c"},
            {"article_id": "x2", "title": "T", "source_type": "a", "content": "c"},
            {"article_id": "x3", "title": "T", "source_type": "b", "content": "c"},
        ],
    )
    out = tmp_path / "dump"
    dump_logos_db(db, out)
    manifest = json.loads((out / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["article_count"] == 3
    assert manifest["source_type_distribution"]["a"] == 2
    assert manifest["source_type_distribution"]["b"] == 1


def test_unrecognised_column_aborts_atomically(tmp_path):
    db = _build_logos_db(
        tmp_path / "src.db",
        [{"article_id": "a", "title": "T", "source_type": "s", "content": "c"}],
        extra_meta_cols=["mystery_col TEXT"],
    )
    out = tmp_path / "dump"
    with pytest.raises(IngestError, match="unrecognised columns"):
        dump_logos_db(db, out)
    assert not out.exists() or not any(out.iterdir())


def test_missing_required_table_raises(tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(IngestError, match="articles_meta"):
        dump_logos_db(db, tmp_path / "dump")


def test_existing_non_empty_out_dir_rejected(tmp_path):
    db = _build_logos_db(
        tmp_path / "src.db",
        [{"article_id": "a", "title": "T", "source_type": "s", "content": "c"}],
    )
    out = tmp_path / "dump"
    out.mkdir()
    (out / "stale.md").write_text("x", encoding="utf-8")
    with pytest.raises(IngestError, match="already exists"):
        dump_logos_db(db, out)


def test_yaml_boundary_protection(tmp_path):
    """Article body starting with '---' must not break YAML frontmatter parsing."""
    db = _build_logos_db(
        tmp_path / "src.db",
        [
            {
                "article_id": "boundary",
                "title": "T",
                "source_type": "s",
                "content": "---\nthis would clash with YAML\n",
            }
        ],
    )
    out = tmp_path / "dump"
    dump_logos_db(db, out)
    md = (out / "boundary.md").read_text(encoding="utf-8")
    # YAML loader should parse without error
    fm_block = md.split("---", 2)[1]
    fm = yaml.safe_load(fm_block)
    assert fm["title"] == "T"


def test_tags_comma_fallback(tmp_path):
    """Non-JSON tags column falls back to comma-split."""
    db = _build_logos_db(
        tmp_path / "src.db",
        [
            {
                "article_id": "ct",
                "title": "T",
                "source_type": "s",
                "tags": "alpha, beta, gamma",
                "content": "c",
            }
        ],
    )
    out = tmp_path / "dump"
    dump_logos_db(db, out)
    md = (out / "ct.md").read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---", 2)[1])
    assert fm["tags"] == ["alpha", "beta", "gamma"]
