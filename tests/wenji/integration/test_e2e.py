"""End-to-end integration tests against the bundled examples corpus.

Run with: ``pytest -m integration``

These tests download the BGE-M3 ONNX model (~600 MB, cached after first run)
and exercise the full pipeline: ingest → classify → search → eval. They are
the canonical smoke test before a wenji release.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"

DIRECTORY_MAP = {
    "sermon": "sermon",
    "article": "article",
    "law": "law",
    "classical": "classical",
    "tech": "tech",
}
CHUNK_STRATEGIES = {
    "sermon": {"strategy": "paragraph", "min_chars": 300, "max_chars": 1500},
    "article": {"strategy": "markdown-heading"},
    "law": {"strategy": "markdown-heading"},
    "tech": {"strategy": "paragraph", "min_chars": 150, "max_chars": 1000},
}


@pytest.fixture(scope="module")
def model_cache() -> Path:
    """Download embed model once (HF cache → idempotent)."""
    from wenji.core.model_download import download_embed_model

    return download_embed_model()


@pytest.fixture(scope="module")
def populated_e2e_db(tmp_path_factory, model_cache: Path):
    from wenji.classify import AxesClassifier, load_axes_config
    from wenji.core.db import connect, initialise_schema
    from wenji.ingest import ingest_dir
    from wenji.ingest.embed import Embedder

    db_dir = tmp_path_factory.mktemp("e2e")
    db_path = db_dir / "wenji.db"

    conn = connect(db_path)
    initialise_schema(conn)
    embedder = Embedder(model_dir=model_cache)

    article_ids = ingest_dir(
        EXAMPLES / "articles",
        conn,
        embedder,
        directory_map=DIRECTORY_MAP,
        chunk_strategies=CHUNK_STRATEGIES,
    )

    config = load_axes_config(EXAMPLES / "axes.yaml")
    classifier = AxesClassifier(conn, config)
    classifier.classify_all()

    yield {"conn": conn, "embedder": embedder, "article_ids": article_ids}
    conn.close()


@pytest.mark.integration
def test_ingest_all_examples(populated_e2e_db):
    # 1 sermon + 2 article + 1 law + 3 verse + 2 tech
    assert len(populated_e2e_db["article_ids"]) == 9


@pytest.mark.integration
def test_classify_validation_passes(populated_e2e_db):
    from wenji.classify import AxesClassifier, load_axes_config

    config = load_axes_config(EXAMPLES / "axes.yaml")
    classifier = AxesClassifier(populated_e2e_db["conn"], config)
    report = classifier.validate()
    assert report.passed, f"validation failed: {report.failures}"
    per_axis = report.metrics["per_axis"]
    # article axis covers source_type=article + law (1:N mapping demo)
    assert per_axis == {"sermon": 1, "article": 3, "verse": 3, "tutorial": 2}


@pytest.mark.integration
def test_search_明月_finds_tang_poems(populated_e2e_db):
    from wenji.search import Searcher

    searcher = Searcher(populated_e2e_db["conn"], populated_e2e_db["embedder"])
    results = searcher.search("明月", limit=5)
    titles = [r.get("title", "") for r in results]
    assert any("夜思" in t or "山居" in t or "月" in t for t in titles), (
        f"'明月' did not surface a Tang poem; got titles: {titles}"
    )


@pytest.mark.integration
def test_search_FTS5_finds_tutorial(populated_e2e_db):
    from wenji.search import Searcher

    searcher = Searcher(populated_e2e_db["conn"], populated_e2e_db["embedder"])
    results = searcher.search("FTS5 全文搜尋", limit=3)
    titles = [r.get("title", "") for r in results]
    assert any("FTS5" in t for t in titles), (
        f"'FTS5' did not surface SQLite tutorial; got titles: {titles}"
    )


@pytest.mark.integration
def test_search_axis_filter_constrains_to_classical(populated_e2e_db):
    from wenji.search import Searcher

    searcher = Searcher(populated_e2e_db["conn"], populated_e2e_db["embedder"])
    results = searcher.search("秋", axis="verse", limit=5)
    assert results, "axis-filtered search returned 0 — verse axis empty?"
    leak = [
        (r.get("title"), r.get("source_type"))
        for r in results
        if r.get("source_type") != "classical"
    ]
    assert not leak, f"verse axis leaked non-classical: {leak}"


@pytest.mark.integration
def test_eval_jsonl_passes_majority(populated_e2e_db):
    """Run the bundled eval.jsonl in-process; expect ≥6/10 to auto-pass."""
    from wenji.eval import aggregate, evaluate_question
    from wenji.eval.jsonl import load_candidates
    from wenji.search import Searcher

    searcher = Searcher(populated_e2e_db["conn"], populated_e2e_db["embedder"])
    candidates = load_candidates(EXAMPLES / "eval.jsonl")
    assert len(candidates) == 10

    per_question = []
    for cand in candidates:
        hits = searcher.search(cand.query, limit=5)
        response = {
            "results": [
                {
                    "article_id": r.get("article_id"),
                    "title": r.get("title", ""),
                    "content_raw": r.get("content_raw") or r.get("content_snippet") or "",
                    "source_type": r.get("source_type", ""),
                    "hybrid_score": r.get("hybrid_score", 0.0),
                }
                for r in hits
            ],
            "elapsed_ms": 0,
        }
        # use min_hits=1 (lenient) since example corpus is intentionally small
        per_question.append(evaluate_question(cand, response, min_hits=1))

    summary = aggregate(per_question)
    fails = [(q["query"], q["max_keyword_hits"]) for q in per_question if not q["auto_pass"]]
    assert summary["pass_count"] >= 6, f"only {summary['pass_count']}/10 passed; misses: {fails}"


@pytest.mark.integration
def test_byte_identical_rebuild(populated_e2e_db, model_cache, tmp_path):
    """rebuild twice on the same disk → identical FTS content + vec bytes."""
    from wenji.core.db import connect, initialise_schema
    from wenji.ingest import rebuild_from_disk
    from wenji.ingest.embed import Embedder

    def snapshot():
        db_path = tmp_path / f"rebuild_{Path(tmp_path).name}.db"
        conn = connect(db_path)
        initialise_schema(conn)
        rebuild_from_disk(
            conn,
            EXAMPLES / "articles",
            Embedder(model_dir=model_cache),
            directory_map=DIRECTORY_MAP,
            chunk_strategies=CHUNK_STRATEGIES,
        )
        fts_rows = conn.execute(
            "SELECT article_id, content FROM articles_fts ORDER BY article_id"
        ).fetchall()
        vec_rows = conn.execute(
            "SELECT article_id, vec FROM doc_vectors ORDER BY article_id"
        ).fetchall()
        conn.close()
        db_path.unlink()
        return fts_rows, vec_rows

    snap_a = snapshot()
    snap_b = snapshot()
    assert snap_a == snap_b, "rebuild not byte-identical"
