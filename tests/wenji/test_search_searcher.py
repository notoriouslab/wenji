"""Integration tests for wenji.search.Searcher."""

from __future__ import annotations

import pytest

from wenji.core.db import connect, initialise_schema
from wenji.ingest import ingest_one
from wenji.search import Searcher, _hydrate_chunk_hits, _strip_markdown_for_snippet


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


# ---------------------------------------------------------------------------
# L1: chunk_hits column-restricted to chunk_text (title-only matches → 0)
# ---------------------------------------------------------------------------


@pytest.fixture
def chunk_hit_db(tmp_path, mock_embedder):
    """Two articles: one with query word ONLY in title, one with query word in
    multiple chunks. Used to verify L1 column-restriction behavior."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    # Title contains '復興'; chunks do NOT.
    (sermons / "title-only.md").write_text(
        "---\ntitle: 教會復興的歷史\n---\n"
        "第一段：教會發展的早期紀錄。\n\n"
        "第二段：宗教改革與後續的傳播。\n",
        encoding="utf-8",
    )
    # Title does NOT contain '復興'; 3 of N chunks do.
    (sermons / "multi-hit.md").write_text(
        "---\ntitle: 教會發展史略\n---\n"
        "第一段：早期會眾。\n\n"
        "第二段：復興浪潮席捲。\n\n"
        "第三段：日常生活。\n\n"
        "第四段：第二次復興運動。\n\n"
        "第五段：第三次復興出現。\n",
        encoding="utf-8",
    )
    conn = connect(":memory:")
    initialise_schema(conn)
    for md in sorted(sermons.iterdir()):
        ingest_one(
            md,
            conn,
            mock_embedder,
            directory_map={"sermons": "sermon"},
            chunk_strategies={
                "sermon": {"strategy": "paragraph", "min_chars": 1, "max_chars": 200}
            },
            corpus_root=tmp_path,
        )
    conn.commit()
    yield conn
    conn.close()


def test_chunk_hits_title_only_match_yields_zero(chunk_hit_db):
    """L1: article whose title matches query but whose chunks don't → chunk_hits=0."""
    aid = chunk_hit_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%復興的歷史%'"
    ).fetchone()[0]
    info = _hydrate_chunk_hits(chunk_hit_db, "復興", [aid])
    # No chunk content matched → article is absent from grouped output entirely
    assert aid not in info or info[aid]["chunk_hits"] == 0


def test_chunk_hits_multi_chunk_content_match_counts_each(chunk_hit_db):
    """L1: chunks containing query in their content all increment chunk_hits."""
    aid = chunk_hit_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%發展史略%'"
    ).fetchone()[0]
    info = _hydrate_chunk_hits(chunk_hit_db, "復興", [aid])
    assert aid in info
    # Three chunks contain '復興' (paragraphs 2, 4, 5 of the body).
    assert info[aid]["chunk_hits"] == 3
    assert len(info[aid]["matched_chunks"]) >= 1


# ---------------------------------------------------------------------------
# L2: snippet markdown strip via AST (preserves URLs with underscores, code spans)
# ---------------------------------------------------------------------------


def test_snippet_strip_preserves_url_with_underscore():
    """L2: URLs containing ``_`` are not mangled by the AST-based strip."""
    text = "See https://en.wikipedia.org/wiki/Foo_bar for context."
    out = _strip_markdown_for_snippet(text)
    assert "Foo_bar" in out
    assert "Foobar" not in out


def test_snippet_strip_renders_code_and_emphasis_as_plain_text():
    """L2: ``**bold**`` and ``code_with_underscore`` extract to plain text."""
    text = "Use **bold** and `code_with_underscore` here."
    out = _strip_markdown_for_snippet(text)
    assert "bold" in out
    assert "code_with_underscore" in out
    assert "**" not in out
    assert "`" not in out


def test_snippet_strip_handles_empty_input():
    """L2: empty / whitespace-only inputs short-circuit cleanly."""
    assert _strip_markdown_for_snippet("") == ""
    assert _strip_markdown_for_snippet("plain text") == "plain text"


def test_searcher_rejects_removed_keyword_arguments(populated_db, mock_embedder):
    """0.5.0 contract: the removed rewrite/rerank/hook params fail loudly."""
    for kwarg in ("rewriter", "reranker", "ranker_hooks"):
        with pytest.raises(TypeError):
            Searcher(populated_db, mock_embedder, **{kwarg: object()})


def test_searcher_six_parameter_construction(populated_db, mock_embedder):
    s = Searcher(
        populated_db,
        mock_embedder,
        alpha=0.25,
        candidate_pool=50,
        entity_scorer=None,
        intent_classifier=None,
    )
    assert s.search("禱告", limit=3) is not None


def test_content_snippet_strips_markdown_image_syntax(populated_db, mock_embedder):
    """Raw image markdown in content_raw must not leak into search snippets
    (prod report 2026-07-13: snippets showed ![](https://...jpeg))."""
    populated_db.execute(
        "INSERT INTO articles_meta (article_id, title, source_type, path, indexed_at) "
        "VALUES ('img1', '圖片文', 'sermon', '/tmp/img1.md', '2026-07-13T00:00:00')"
    )
    body = "![](https://example.com/photo.jpeg)\n\n這是關於禱告生活的一段內文，前面有一張圖片。"
    populated_db.execute(
        "INSERT INTO articles_fts (article_id, title, title_raw, content, content_raw, "
        "tags, tags_raw, category, source_type, pub_date, pub_year) "
        "VALUES ('img1', '圖片文', '圖片文', ?, ?, '', '', '', 'sermon', '', '')",
        (body, body),
    )
    import numpy as np

    vec = np.ones(1024, dtype=np.float32) / 32.0
    populated_db.execute(
        "INSERT INTO doc_vectors (article_id, vec) VALUES ('img1', ?)", (vec.tobytes(),)
    )
    populated_db.commit()

    s = Searcher(populated_db, mock_embedder)
    results = s.search("禱告生活", limit=10)
    target = next((r for r in results if r["article_id"] == "img1"), None)
    assert target is not None
    assert "![](" not in target["content_snippet"]
    assert "https://example.com/photo.jpeg" not in target["content_snippet"]
    assert "禱告生活" in target["content_snippet"].replace("<mark>", "").replace("</mark>", "")
