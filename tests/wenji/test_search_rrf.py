"""Tests for ``wenji.search.rrf`` — RRF merge + chunk-level BM25 helper."""

from __future__ import annotations

import sqlite3

from wenji.search.rrf import DEFAULT_RRF_K, chunk_bm25_search, rrf_merge


def _article(aid: str, score: float, source_type: str = "general") -> dict:
    return {
        "article_id": aid,
        "_rankingScore": score,
        "source_type": source_type,
        "title": f"title-{aid}",
    }


def test_rrf_merge_two_dicts_combines_ranks():
    """RRF formula: 1/(k+main_rank) + 1/(k+chunk_rank)."""
    main = {
        "a1": _article("a1", 0.9),
        "a2": _article("a2", 0.7),
        "a3": _article("a3", 0.5),
    }
    chunk = {"a3": -2.0, "a2": -3.0, "a1": -4.0}  # a3 best chunk, then a2, then a1
    out = rrf_merge(main, chunk, k=60)
    by_id = {r["article_id"]: r["_rankingScore"] for r in out}
    expected_a1 = 1 / (60 + 1) + 1 / (60 + 3)  # main rank 1, chunk rank 3
    expected_a2 = 1 / (60 + 2) + 1 / (60 + 2)
    expected_a3 = 1 / (60 + 3) + 1 / (60 + 1)
    assert abs(by_id["a1"] - expected_a1) < 1e-9
    assert abs(by_id["a2"] - expected_a2) < 1e-9
    assert abs(by_id["a3"] - expected_a3) < 1e-9


def test_rrf_merge_intent_boost_with_chunks_adds_constant():
    main = {
        "a1": _article("a1", 0.9, source_type="apologetics"),
        "a2": _article("a2", 0.7, source_type="general"),
    }
    chunk = {"a1": -2.0, "a2": -3.0}
    out_no_boost = rrf_merge(main, chunk, k=60)
    by_id_no = {r["article_id"]: r["_rankingScore"] for r in out_no_boost}
    out_boost = rrf_merge(
        {
            "a1": _article("a1", 0.9, source_type="apologetics"),
            "a2": _article("a2", 0.7, source_type="general"),
        },
        chunk,
        intent_boost_types={"apologetics"},
        k=60,
    )
    by_id_with = {r["article_id"]: r["_rankingScore"] for r in out_boost}
    boost = 1 / (60 + 1)
    assert abs((by_id_with["a1"] - by_id_no["a1"]) - boost) < 1e-9
    # non-matching source_type unchanged
    assert abs(by_id_with["a2"] - by_id_no["a2"]) < 1e-9


def test_rrf_merge_empty_chunks_falls_back_to_main_with_0_15_boost():
    main = {
        "a1": _article("a1", 0.9, source_type="apologetics"),
        "a2": _article("a2", 0.7, source_type="general"),
    }
    out = rrf_merge(main, {}, intent_boost_types={"apologetics"}, k=60)
    by_id = {r["article_id"]: r["_rankingScore"] for r in out}
    assert abs(by_id["a1"] - (0.9 + 0.15)) < 1e-9
    assert abs(by_id["a2"] - 0.7) < 1e-9
    assert out[0]["article_id"] == "a1"


def test_rrf_merge_empty_chunks_no_boost_pure_sort():
    main = {
        "a1": _article("a1", 0.5),
        "a2": _article("a2", 0.9),
    }
    out = rrf_merge(main, {}, intent_boost_types=None, k=60)
    assert [r["article_id"] for r in out] == ["a2", "a1"]


def test_rrf_merge_limit_truncates():
    main = {f"a{i}": _article(f"a{i}", 1.0 - i * 0.1) for i in range(5)}
    out = rrf_merge(main, {}, limit=3)
    assert len(out) == 3


def test_rrf_default_k_is_60():
    assert DEFAULT_RRF_K == 60


def test_chunk_bm25_search_empty_query_returns_empty():
    conn = sqlite3.connect(":memory:")
    assert chunk_bm25_search(conn, "", limit=10) == {}
    assert chunk_bm25_search(conn, "   ", limit=10) == {}


def test_chunk_bm25_search_against_populated_db(populated_db):
    """populated_db has 0 chunks (default chunk strategy); function returns empty."""
    out = chunk_bm25_search(populated_db, "因信稱義", limit=10)
    assert isinstance(out, dict)
    # populated_db's default ingest produces chunk_count=0; chunk_signals is empty
    assert out == {}


def test_chunk_bm25_search_dedups_per_article():
    """Multiple matching chunks per article SHALL collapse to one entry per article."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, article_id, chunk_index, title, title_raw,
            chunk_text, chunk_text_raw, tags, tags_raw, source_type, pub_year,
            tokenize='unicode61'
        );
        """
    )
    # Same article with 3 chunks all matching "因信稱義" (char-level)
    for i in range(3):
        conn.execute(
            "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, "
            "title, title_raw, chunk_text, chunk_text_raw, tags, tags_raw, "
            "source_type, pub_year) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"a1-c{i}",
                "a1",
                i,
                "title",
                "title",
                "因 信 稱 義 是 救 恩 論",
                "因信稱義是救恩論",
                "",
                "",
                "general",
                2024,
            ),
        )
    conn.commit()
    out = chunk_bm25_search(conn, "因信稱義", limit=10)
    assert len(out) == 1
    assert "a1" in out
    # best (most negative) bm25 score retained
    assert out["a1"] < 0
