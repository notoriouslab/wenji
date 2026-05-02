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
