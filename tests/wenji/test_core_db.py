"""Tests for wenji.core.db."""

from __future__ import annotations

import sqlite3

import pytest

from wenji.core.db import SCHEMA_VERSION, connect, initialise_schema
from wenji.core.errors import SchemaError, WenjiError


def test_connect_in_memory_returns_connection():
    conn = connect(":memory:")
    assert isinstance(conn, sqlite3.Connection)


def test_connect_foreign_keys_enabled():
    conn = connect(":memory:")
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_initialise_schema_creates_required_tables():
    conn = connect(":memory:")
    initialise_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')")
    }
    required = {
        "wenji_meta",
        "articles_meta",
        "articles_fts",
        "chunks_fts",
        "doc_vectors",
        "article_axes",
        "query_rewrite_cache",
    }
    assert required.issubset(tables)


def test_initialise_schema_seeds_version():
    conn = connect(":memory:")
    initialise_schema(conn)
    row = conn.execute("SELECT value FROM wenji_meta WHERE key = 'schema_version'").fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION


def test_initialise_schema_seeds_exactly_live_keys():
    conn = connect(":memory:")
    initialise_schema(conn)
    keys = {row[0] for row in conn.execute("SELECT key FROM wenji_meta")}
    assert keys == {"schema_version", "embedder"}


def test_initialise_schema_deletes_pre_0_4_0_stale_keys():
    conn = connect(":memory:")
    initialise_schema(conn)
    # Simulate a db created before v0.4.0, whose seed included the dead
    # build-telemetry keys (dropped in v0.4.0; see release-v0-4-0 D2/D3).
    conn.executemany(
        "INSERT INTO wenji_meta (key, value) VALUES (?, ?)",
        [
            ("build_started_at", ""),
            ("build_completed_at", ""),
            ("n_articles", "0"),
            ("n_chunks", "0"),
            ("n_doc_vectors", "0"),
        ],
    )
    conn.commit()
    initialise_schema(conn)
    keys = {row[0] for row in conn.execute("SELECT key FROM wenji_meta")}
    assert keys == {"schema_version", "embedder"}
    row = conn.execute("SELECT value FROM wenji_meta WHERE key = 'embedder'").fetchone()
    assert row[0] == "BGE-M3-INT8-ONNX"


def test_initialise_schema_idempotent():
    conn = connect(":memory:")
    initialise_schema(conn)
    initialise_schema(conn)
    n_versions = conn.execute(
        "SELECT COUNT(*) FROM wenji_meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    assert n_versions == 1


def test_initialise_schema_detects_version_mismatch():
    conn = connect(":memory:")
    initialise_schema(conn)
    conn.execute("UPDATE wenji_meta SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    with pytest.raises(SchemaError, match="schema_version"):
        initialise_schema(conn)


def test_article_axes_primary_uniqueness():
    conn = connect(":memory:")
    initialise_schema(conn)
    conn.execute("INSERT INTO articles_meta (article_id, path, title) VALUES ('a1', 'a1.md', 't1')")
    conn.execute("INSERT INTO article_axes (article_id, axis_id, is_primary) VALUES ('a1', 'x', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO article_axes (article_id, axis_id, is_primary) VALUES ('a1', 'y', 1)"
        )


def test_libsimple_load_failure_raises_wenji_error():
    with pytest.raises(WenjiError, match="libsimple"):
        connect(":memory:", libsimple_path="/nonexistent/path/libsimple.so")


def test_file_db_uses_wal_normal(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # synchronous: 1 = NORMAL
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
    conn.close()
