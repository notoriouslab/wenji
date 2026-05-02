"""Integration tests for wenji.search.Searcher."""

from __future__ import annotations

import pytest

from wenji.search import Searcher
from wenji.search.rerank import CrossEncoderReranker


def test_searcher_returns_results(populated_db, mock_embedder):
    s = Searcher(populated_db, mock_embedder)
    results = s.search("因信稱義", limit=5)
    assert len(results) >= 1
    for r in results:
        assert "article_id" in r
        assert "hybrid_score" in r
        assert "content_snippet" in r


def test_searcher_limit_caps(populated_db, mock_embedder):
    s = Searcher(populated_db, mock_embedder)
    results = s.search("禱告", limit=1)
    assert len(results) <= 1


def test_searcher_axis_filter(populated_db, mock_embedder):
    s = Searcher(populated_db, mock_embedder)
    theology = s.search("query", axis="theology", limit=10)
    nonexistent = s.search("query", axis="nonexistent_axis", limit=10)
    assert nonexistent == []
    if theology:
        assert all(r.get("source_type") for r in theology)


def test_searcher_excludes_excluded_category(populated_db, mock_embedder):
    s = Searcher(populated_db, mock_embedder)
    results = s.search("宣教", limit=10)
    titles = [r.get("title", "") for r in results]
    assert "普世宣教使命" not in titles


def test_searcher_alpha_zero_skips_bm25(populated_db, mock_embedder):
    s = Searcher(populated_db, mock_embedder, alpha=0.0)
    results = s.search("禱告", limit=5)
    for r in results:
        assert r.get("bm25_score", 0.0) == 0.0


def test_searcher_alpha_one_skips_vector(populated_db):
    s = Searcher(populated_db, embedder=None, alpha=1.0)
    results = s.search("禱告", limit=5)
    for r in results:
        assert r.get("cosine_score", 0.0) == 0.0


def test_searcher_alpha_validates():
    with pytest.raises(ValueError):
        Searcher(None, None, alpha=2.0)


def test_searcher_alpha_lt_one_requires_embedder():
    with pytest.raises(ValueError, match="embedder is required"):
        Searcher(None, embedder=None, alpha=0.5)


def test_searcher_empty_query_returns_empty(populated_db, mock_embedder):
    s = Searcher(populated_db, mock_embedder)
    assert s.search("") == []


def test_searcher_with_disabled_reranker_unchanged(populated_db, mock_embedder):
    rer = CrossEncoderReranker(enabled=False)
    s = Searcher(populated_db, mock_embedder, reranker=rer)
    results = s.search("禱告", limit=5)
    assert "rerank_score" not in (results[0] if results else {})


def test_searcher_with_mock_reranker_reorders(populated_db, mock_embedder):
    class MockRer:
        enabled = True

        def score(self, query, candidates):
            for i, c in enumerate(candidates):
                c["rerank_score"] = float(len(candidates) - i)  # reverse order from input
            return candidates

    s = Searcher(populated_db, mock_embedder, reranker=MockRer())
    results = s.search("禱告", limit=5)
    if len(results) >= 2:
        scores = [r["rerank_score"] for r in results]
        assert scores == sorted(scores, reverse=True)


def test_make_snippet_escapes_html_content():
    """Regression: make_snippet must HTML-escape excerpt to prevent XSS via |safe."""
    from wenji.search import make_snippet

    # Untrusted corpus content with raw HTML
    content = "前文 <script>alert(1)</script> 後文 含 query 詞 終結"
    out = make_snippet(content, ["query"], window=80)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    # query term still wrapped in mark
    assert "<mark>query</mark>" in out


def test_make_snippet_escapes_when_no_match():
    from wenji.search import make_snippet

    content = "no-match <img src=x onerror=alert(1)>"
    out = make_snippet(content, ["nonexistent"], window=80)
    assert "<img" not in out
    assert "&lt;img" in out


def test_searcher_results_include_chunk_hits_and_matched_chunks(populated_db, mock_embedder):
    """Even when chunks_fts is empty/no match, fields exist on results."""
    s = Searcher(populated_db, mock_embedder)
    results = s.search("禱告", limit=5)
    for r in results:
        # populated_db tiny corpus has no chunks ingested, so chunk_hits=0
        # but the fields MUST be present for downstream consumers (web template).
        assert "chunk_hits" in r
        assert "matched_chunks" in r
        assert isinstance(r["matched_chunks"], list)


def test_searcher_with_rewriter_uses_rewritten_query(populated_db, mock_embedder):
    class MockRewriter:
        def rewrite(self, raw):
            return "禱告" if raw == "raw query" else raw

    s = Searcher(populated_db, mock_embedder, rewriter=MockRewriter())
    results = s.search("raw query", limit=5)
    # Rewriter changed query to 禱告; should hit prayer article
    titles = [r.get("title", "") for r in results]
    assert any("禱告" in t for t in titles)
