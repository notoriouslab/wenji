"""Integration tests for v0.3.6 Searcher pipeline (RRF + entity + intent + hooks).

Covers:
- Pipeline degrades gracefully without entity_scorer / intent_classifier
- Pipeline runs full stack when fully configured
- Output schema preserved across both modes
"""

from __future__ import annotations

from wenji.search import Searcher
from wenji.search.entity import EntityScorer
from wenji.search.intent import IntentClassifier
from wenji.search.ranker import ChunkHitBooster


def test_searcher_degrades_without_entity_intent(populated_db, mock_embedder):
    """Pipeline runs without entity_scorer / intent_classifier (RRF + chunk_signals only)."""
    searcher = Searcher(populated_db, mock_embedder, alpha=0.25)
    results = searcher.search("因信稱義", limit=5)
    assert isinstance(results, list)
    # Pipeline did not raise; result schema sane
    if results:
        r = results[0]
        for key in (
            "article_id",
            "title",
            "source_type",
            "bm25_score",
            "cosine_score",
            "hybrid_score",
            "_rankingScore",
            "content_snippet",
            "chunk_hits",
            "matched_chunks",
        ):
            assert key in r, f"missing key {key} in result"


def test_searcher_with_entity_and_intent(populated_db, mock_embedder):
    """Full pipeline with all dependencies injected."""
    entity_scorer = EntityScorer(
        entity_dict={"因信稱義": "concept"},
        alias_map={},
    )
    intent_classifier = IntentClassifier(
        intent_keywords={"theology": ["稱義"], "general": []},
        intent_source_types={"theology": {"sermon"}},
    )
    searcher = Searcher(
        populated_db,
        mock_embedder,
        alpha=0.25,
        entity_scorer=entity_scorer,
        intent_classifier=intent_classifier,
    )
    results = searcher.search("因信稱義", limit=5)
    assert isinstance(results, list)


def test_searcher_with_ranker_hooks(populated_db, mock_embedder):
    """ChunkHitBooster integration through full pipeline."""
    searcher = Searcher(
        populated_db,
        mock_embedder,
        alpha=0.25,
        ranker_hooks=[ChunkHitBooster()],
    )
    results = searcher.search("因信稱義", limit=5)
    # No exception; schema preserved
    if results:
        assert "_rankingScore" in results[0]


def test_searcher_empty_query_returns_empty(populated_db, mock_embedder):
    searcher = Searcher(populated_db, mock_embedder, alpha=0.25)
    assert searcher.search("", limit=5) == []
    assert searcher.search("   ", limit=5) == []


def test_searcher_alpha_zero_uses_vector_only(populated_db, mock_embedder):
    """alpha=0 → BM25 skipped, vector-only retrieval still flows through pipeline."""
    searcher = Searcher(populated_db, mock_embedder, alpha=0.0)
    results = searcher.search("因信稱義", limit=5)
    assert isinstance(results, list)


def test_searcher_alpha_one_uses_bm25_only(populated_db):
    """alpha=1 → vector skipped, BM25-only retrieval (no embedder needed)."""
    searcher = Searcher(populated_db, embedder=None, alpha=1.0)
    results = searcher.search("因信稱義", limit=5)
    assert isinstance(results, list)


def test_searcher_pipeline_calls_entity_scorer_when_injected(populated_db, mock_embedder):
    """Sanity: with entity_scorer + matching dict, hard-filter SHALL apply for person miss."""
    entity_scorer = EntityScorer(
        entity_dict={"非常罕見人名XYZ": "person"},
        alias_map={},
    )
    searcher = Searcher(populated_db, mock_embedder, alpha=0.25, entity_scorer=entity_scorer)
    results = searcher.search("非常罕見人名XYZ的故事", limit=5)
    # Person subject with no article match → hard-filtered → empty
    assert results == []


def test_searcher_intent_boost_changes_ranking_or_passes(populated_db, mock_embedder):
    """Smoke test: intent boost should not crash and SHALL produce valid output."""
    intent_classifier = IntentClassifier(
        intent_keywords={"sermon_intent": ["禱告"], "general": []},
        intent_source_types={"sermon_intent": {"sermon"}},
    )
    searcher = Searcher(
        populated_db, mock_embedder, alpha=0.25, intent_classifier=intent_classifier
    )
    # Query that triggers intent
    results_with_boost = searcher.search("禱告的意義", limit=5)
    # Query that does not trigger intent
    results_without = searcher.search("一般查詢", limit=5)
    # No crashes; both lists valid (may be empty for tiny corpus)
    assert isinstance(results_with_boost, list)
    assert isinstance(results_without, list)


def test_searcher_response_hydrates_content_full_for_all_hits(populated_db, mock_embedder):
    # Vector-only hits historically reached the response without content_raw,
    # leaving content_snippet/content_full empty and breaking the eval metric
    # (metrics.py reads content_full|content_raw|content for keyword scoring).
    # alpha=0.0 forces vector-only ranking — most reliable trigger for the
    # vector-only branch.
    searcher = Searcher(populated_db, mock_embedder, alpha=0.0)
    results = searcher.search("因信稱義", limit=5)
    assert results, "fixture corpus should yield at least one hit"
    for r in results:
        assert "content_full" in r, f"missing content_full in {r['article_id']}"
        assert isinstance(r["content_full"], str)
        assert len(r["content_full"]) <= 500
        assert "content_snippet" in r
