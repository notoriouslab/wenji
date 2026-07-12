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


def test_initialise_schema_migrates_v2_in_place():
    """v2 db (rewrite cache present, corpus rows populated) upgrades to v3
    with the cache table dropped and every other row preserved."""
    conn = connect(":memory:")
    initialise_schema(conn)
    # Reconstruct a v2 database: re-create the dropped table + stamp v2.
    conn.execute(
        "CREATE TABLE query_rewrite_cache ("
        "raw TEXT PRIMARY KEY, rewritten TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO query_rewrite_cache VALUES ('q', 'r', '2026-01-01')")
    conn.execute("UPDATE wenji_meta SET value = '2' WHERE key = 'schema_version'")
    conn.execute(
        "INSERT INTO articles_meta (article_id, title, source_type, path) "
        "VALUES ('a1', 't', 's', '/tmp/a1.md')"
    )
    conn.commit()

    initialise_schema(conn)

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "query_rewrite_cache" not in tables
    row = conn.execute("SELECT value FROM wenji_meta WHERE key = 'schema_version'").fetchone()
    assert row[0] == "3"
    kept = conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
    assert kept == 1  # data preserved through migration


def test_connect_alone_does_not_migrate_v2(tmp_path):
    """Read-only entry points call connect() without initialise_schema and
    must leave a v2 database untouched."""
    db = tmp_path / "v2.db"
    conn = connect(db)
    initialise_schema(conn)
    conn.execute(
        "CREATE TABLE query_rewrite_cache ("
        "raw TEXT PRIMARY KEY, rewritten TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("UPDATE wenji_meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    conn = connect(db)  # read path: no initialise_schema
    row = conn.execute("SELECT value FROM wenji_meta WHERE key = 'schema_version'").fetchone()
    assert row[0] == "2"
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "query_rewrite_cache" in tables
    conn.close()


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
