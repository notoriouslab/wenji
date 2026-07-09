"""Tests for the per-db candidate matrix cache (ingest-throughput D7)."""

from __future__ import annotations

import numpy as np
import pytest

import wenji.search.vector as vector_mod
from wenji.core.db import connect, initialise_schema
from wenji.search.vector import clear_candidate_cache, vector_search


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_candidate_cache()
    yield
    clear_candidate_cache()


def _seed(conn, n=3, offset=0):
    for i in range(offset, offset + n):
        vec = np.zeros(1024, dtype=np.float32)
        vec[i % 1024] = 1.0
        conn.execute(
            "INSERT INTO articles_meta (article_id, path, title, indexed_at) VALUES (?, ?, ?, ?)",
            (f"a{i}", f"a{i}.md", f"t{i}", f"2026-07-09T00:00:{i:02d}"),
        )
        conn.execute(
            "INSERT INTO doc_vectors (article_id, vec) VALUES (?, ?)",
            (f"a{i}", vec.tobytes()),
        )
    conn.commit()


def _query():
    q = np.zeros(1024, dtype=np.float32)
    q[0] = 1.0
    return q


def test_repeated_queries_load_candidates_once(tmp_path, monkeypatch):
    db = tmp_path / "cache.db"
    conn = connect(db)
    initialise_schema(conn)
    _seed(conn)

    calls = {"n": 0}
    real = vector_mod._load_candidates

    def counting(c, axis):
        calls["n"] += 1
        return real(c, axis)

    monkeypatch.setattr(vector_mod, "_load_candidates", counting)
    vector_search(conn, _query())
    vector_search(conn, _query())
    assert calls["n"] == 1
    conn.close()


def test_external_ingest_invalidates_cache(tmp_path):
    db = tmp_path / "cache.db"
    conn = connect(db)
    initialise_schema(conn)
    _seed(conn, n=2)
    assert len(vector_search(conn, _query())) == 2

    # Second connection (external process analogue) adds an article.
    conn2 = connect(db)
    _seed(conn2, n=1, offset=2)
    conn2.close()

    results = vector_search(conn, _query())
    assert len(results) == 3  # fingerprint change rebuilt the matrix
    conn.close()


def test_memory_db_not_cached(monkeypatch):
    conn = connect(":memory:")
    initialise_schema(conn)
    _seed(conn)
    calls = {"n": 0}
    real = vector_mod._load_candidates

    def counting(c, axis):
        calls["n"] += 1
        return real(c, axis)

    monkeypatch.setattr(vector_mod, "_load_candidates", counting)
    vector_search(conn, _query())
    vector_search(conn, _query())
    assert calls["n"] == 2  # no caching for :memory:
    assert vector_mod._CANDIDATE_CACHE == {}
    conn.close()
