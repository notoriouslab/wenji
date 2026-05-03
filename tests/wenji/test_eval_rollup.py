"""Tests for chunk-to-article rollup (wenji.eval.metrics.rollup_chunks_to_articles)."""

from __future__ import annotations

from wenji.eval.metrics import rollup_chunks_to_articles


def test_rollup_three_chunks_same_article_merge_to_one_entry():
    hits = [
        {"article_id": "A1", "rank": 2, "score": 0.9, "content_full": "alpha"},
        {"article_id": "A2", "rank": 3, "score": 0.8, "content_full": "beta"},
        {"article_id": "A1", "rank": 5, "score": 0.5, "content_full": "gamma"},
        {"article_id": "A1", "rank": 9, "score": 0.4, "content_full": "delta"},
    ]
    rolled = rollup_chunks_to_articles(hits)
    ids = [r["article_id"] for r in rolled]
    assert ids == ["A1", "A2"]  # A1 first because its highest score chunk has rank 2
    a1 = next(r for r in rolled if r["article_id"] == "A1")
    assert a1["rank"] == 2  # highest-scoring chunk's rank survives
    assert "alpha" in a1["article_content_union"]
    assert "gamma" in a1["article_content_union"]
    assert "delta" in a1["article_content_union"]


def test_rollup_keyword_distributed_across_chunks_matches_at_article_level():
    from wenji.eval.jsonl import GoldPath
    from wenji.eval.metrics import score_gold_path

    hits = [
        {"article_id": "A1", "rank": 1, "score": 0.9, "content_full": "神"},
        {"article_id": "A1", "rank": 4, "score": 0.5, "content_full": "創造"},
    ]
    rolled = rollup_chunks_to_articles(hits)
    article = rolled[0]
    gp = GoldPath(path_tag="p", keywords=("神", "創造"))
    # keyword distributed across chunks would yield "partial" if scored
    # chunk-by-chunk; rollup unions them so the article-level score is "full".
    assert score_gold_path(article["article_content_union"], gp) == "full"


def test_rollup_preserves_canonical_rank_from_highest_scoring_chunk():
    hits = [
        {"article_id": "A1", "rank": 7, "score": 0.3, "content_full": "low"},
        {"article_id": "A1", "rank": 2, "score": 0.95, "content_full": "high"},
        {"article_id": "A1", "rank": 12, "score": 0.1, "content_full": "lowest"},
    ]
    rolled = rollup_chunks_to_articles(hits)
    assert rolled[0]["rank"] == 2  # rank of the chunk with score 0.95


def test_rollup_caps_at_top_k():
    hits = [
        {"article_id": f"A{i}", "rank": i, "score": 1.0 - i * 0.01, "content_full": "x"}
        for i in range(1, 31)
    ]
    rolled = rollup_chunks_to_articles(hits, top_k=10)
    assert len(rolled) == 10
    assert [r["article_id"] for r in rolled] == [f"A{i}" for i in range(1, 11)]


def test_rollup_empty_input():
    assert rollup_chunks_to_articles([]) == []


def test_rollup_skips_hits_without_article_id():
    hits = [
        {"article_id": None, "rank": 1, "score": 0.9, "content_full": "x"},
        {"article_id": "A1", "rank": 2, "score": 0.8, "content_full": "y"},
    ]
    rolled = rollup_chunks_to_articles(hits)
    assert [r["article_id"] for r in rolled] == ["A1"]


def test_rollup_metadata_from_highest_scoring_chunk():
    hits = [
        {
            "article_id": "A1",
            "rank": 5,
            "score": 0.3,
            "content_full": "low",
            "title": "low-title",
            "content_hash": "lowhash",
        },
        {
            "article_id": "A1",
            "rank": 1,
            "score": 0.95,
            "content_full": "high",
            "title": "high-title",
            "content_hash": "highhash",
        },
    ]
    rolled = rollup_chunks_to_articles(hits)
    assert rolled[0]["title"] == "high-title"
    assert rolled[0]["content_hash"] == "highhash"
