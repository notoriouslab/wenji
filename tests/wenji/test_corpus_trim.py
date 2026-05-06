"""Tests for ``wenji corpus trim`` (stage-2 trim CLI)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from wenji.cli.corpus import detect_id_kind, parse_id_list, trim_corpus


def _build_wenji_db(db_path: Path, articles: list[dict]) -> Path:
    """Build a minimal wenji-shape sqlite db for trim tests."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE articles_meta ("
        "article_id TEXT PRIMARY KEY, title TEXT, source_type TEXT, content_hash TEXT)"
    )
    conn.execute("CREATE TABLE articles_fts (article_id TEXT PRIMARY KEY, content TEXT)")
    conn.execute("CREATE TABLE chunks_fts (article_id TEXT, chunk_idx INTEGER, content TEXT)")
    conn.execute("CREATE TABLE doc_vectors (article_id TEXT PRIMARY KEY, vec BLOB)")
    for a in articles:
        conn.execute(
            "INSERT INTO articles_meta VALUES (?, ?, ?, ?)",
            (a["article_id"], a.get("title", "T"), a["source_type"], a.get("content_hash", "")),
        )
        conn.execute(
            "INSERT INTO articles_fts VALUES (?, ?)",
            (a["article_id"], a.get("content", "")),
        )
        conn.execute(
            "INSERT INTO chunks_fts VALUES (?, ?, ?)",
            (a["article_id"], 0, a.get("content", "")),
        )
        conn.execute(
            "INSERT INTO doc_vectors VALUES (?, ?)",
            (a["article_id"], b"\x00" * 8),
        )
    conn.commit()
    conn.close()
    return db_path


def test_detect_id_kind_hash():
    h = hashlib.sha256(b"x").hexdigest()
    assert detect_id_kind(h) == "hash"


def test_detect_id_kind_article_id():
    assert detect_id_kind("a1") == "article_id"
    assert detect_id_kind("logos-abc-123") == "article_id"


def test_detect_id_kind_64_chars_non_hex_is_article_id():
    # 64 chars but contains 'g' which is not hex
    assert detect_id_kind("g" * 64) == "article_id"


def test_parse_id_list_skips_blank_and_comment():
    text = "\n# comment\n\n  # indented\nabc\n"
    items = parse_id_list(text)
    assert items == [("article_id", "abc")]


def test_parse_id_list_rejects_whitespace_in_article_id():
    with pytest.raises(ValueError, match="whitespace"):
        parse_id_list("foo bar")


def test_trim_by_article_id(tmp_path):
    db = _build_wenji_db(
        tmp_path / "w.db",
        [
            {"article_id": "a1", "source_type": "sermon"},
            {"article_id": "a2", "source_type": "youtube"},
            {"article_id": "a3", "source_type": "sermon"},
        ],
    )
    manifest = trim_corpus(db, "a1\na3\n")
    assert manifest["removed_count"] == 2
    assert manifest["corpus_size_before"] == 3
    assert manifest["corpus_size_after"] == 1
    assert manifest["removed_by_source_type"] == {"sermon": 2}


def test_trim_by_content_hash(tmp_path):
    h_a = hashlib.sha256(b"a").hexdigest()
    h_b = hashlib.sha256(b"b").hexdigest()
    db = _build_wenji_db(
        tmp_path / "w.db",
        [
            {"article_id": "a1", "source_type": "sermon", "content_hash": h_a},
            {"article_id": "a2", "source_type": "youtube", "content_hash": h_b},
        ],
    )
    manifest = trim_corpus(db, f"{h_a}\n")
    assert manifest["removed_count"] == 1
    assert manifest["corpus_size_after"] == 1


def test_trim_mixed_id_and_hash_list(tmp_path):
    h_b = hashlib.sha256(b"b").hexdigest()
    db = _build_wenji_db(
        tmp_path / "w.db",
        [
            {"article_id": "a1", "source_type": "x"},
            {"article_id": "a2", "source_type": "y", "content_hash": h_b},
            {"article_id": "a3", "source_type": "z"},
        ],
    )
    manifest = trim_corpus(db, f"a1\n{h_b}\n")
    assert manifest["removed_count"] == 2


def test_trim_invalid_line_aborts_before_any_delete(tmp_path):
    db = _build_wenji_db(
        tmp_path / "w.db",
        [
            {"article_id": "a1", "source_type": "x"},
            {"article_id": "a2", "source_type": "y"},
        ],
    )
    with pytest.raises(ValueError, match="line 2"):
        trim_corpus(db, "a1\nbad with space\n")
    # confirm nothing was deleted
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
    conn.close()
    assert n == 2


def test_trim_unresolved_hash_raises(tmp_path):
    db = _build_wenji_db(tmp_path / "w.db", [{"article_id": "a1", "source_type": "x"}])
    fake_hash = "0" * 64
    with pytest.raises(ValueError, match="content_hash not found"):
        trim_corpus(db, f"{fake_hash}\n")


def test_trim_cleans_all_four_tables(tmp_path):
    db = _build_wenji_db(
        tmp_path / "w.db",
        [{"article_id": "a1", "source_type": "x"}, {"article_id": "a2", "source_type": "y"}],
    )
    trim_corpus(db, "a1\n")
    conn = sqlite3.connect(str(db))
    for table in ("articles_meta", "articles_fts", "chunks_fts", "doc_vectors"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE article_id='a1'").fetchone()[0]
        assert n == 0, f"a1 row still in {table}"
    conn.close()


def test_trim_summary_groups_by_source_type(tmp_path):
    db = _build_wenji_db(
        tmp_path / "w.db",
        [
            {"article_id": "a1", "source_type": "sermon"},
            {"article_id": "a2", "source_type": "sermon"},
            {"article_id": "a3", "source_type": "youtube"},
        ],
    )
    manifest = trim_corpus(db, "a1\na2\na3\n")
    assert manifest["removed_by_source_type"] == {"sermon": 2, "youtube": 1}
