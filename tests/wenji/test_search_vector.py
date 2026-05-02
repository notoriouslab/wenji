"""Tests for wenji.search.vector."""

from __future__ import annotations

import numpy as np
import pytest

from wenji.core.errors import SearchError
from wenji.search.vector import VECTOR_DIM, vector_search


def test_vector_search_returns_results(populated_db, mock_embedder):
    qv = mock_embedder.encode_batch(["因信稱義"])[0]
    results = vector_search(populated_db, qv)
    assert len(results) >= 1
    for r in results:
        assert "article_id" in r
        assert -1.0 - 1e-5 <= r["cosine_score"] <= 1.0 + 1e-5


def test_vector_search_excludes_excluded_category(populated_db, mock_embedder):
    qv = mock_embedder.encode_batch(["query"])[0]
    results = vector_search(populated_db, qv, limit=10)
    excluded_titles = ["普世宣教使命"]
    fetched_ids = {r["article_id"] for r in results}
    excluded_id = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title=?", (excluded_titles[0],)
    ).fetchone()[0]
    assert excluded_id not in fetched_ids


def test_vector_search_axis_filter(populated_db, mock_embedder):
    qv = mock_embedder.encode_batch(["query"])[0]
    results = vector_search(populated_db, qv, axis="theology")
    assert len(results) >= 1
    none = vector_search(populated_db, qv, axis="nonexistent_axis")
    assert none == []


def test_vector_search_dimension_mismatch_raises(populated_db):
    bad_vec = np.zeros((10,), dtype=np.float32)
    with pytest.raises(SearchError, match="shape"):
        vector_search(populated_db, bad_vec)


def test_vector_search_results_sorted_descending(populated_db, mock_embedder):
    qv = mock_embedder.encode_batch(["禱告"])[0]
    results = vector_search(populated_db, qv, limit=10)
    scores = [r["cosine_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_vector_constant_dim():
    assert VECTOR_DIM == 1024
