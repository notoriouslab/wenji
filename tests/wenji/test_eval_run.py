"""Tests for wenji.eval.run_baseline (mocked HTTP)."""

from __future__ import annotations

import pytest

from wenji.core.db import connect, initialise_schema
from wenji.core.errors import SearchError
from wenji.eval import clear_rewrite_cache, run_baseline
from wenji.eval.jsonl import Candidate


@pytest.fixture
def db_with_cache(tmp_path):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    initialise_schema(conn)
    conn.execute(
        "INSERT INTO query_rewrite_cache (raw, rewritten, created_at) VALUES (?, ?, ?)",
        ("Q1", "rewritten", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return db_path


def test_clear_rewrite_cache_returns_count(db_with_cache):
    n = clear_rewrite_cache(db_with_cache)
    assert n == 1
    n2 = clear_rewrite_cache(db_with_cache)
    assert n2 == 0


class _FakeClient:
    def __init__(self, payloads):
        """payloads: list of dicts to return per call (in order)."""
        self.payloads = list(payloads)
        self.requests = []

    def get(self, url, params=None, **kw):
        self.requests.append((url, params))
        payload = self.payloads.pop(0) if self.payloads else {"results": []}
        return _FakeResponse(payload)

    def close(self):
        pass


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_run_baseline_basic_with_injected_client(tmp_path):
    cands = [
        Candidate(id=1, query="因信稱義", expected_keywords=("恩典", "稱義")),
        Candidate(id=2, query="禱告", expected_keywords=("禱告",)),
    ]
    fake_client = _FakeClient(
        [
            {"results": [{"article_id": "a1", "title": "X", "content_raw": "恩典 稱義 主"}]},
            {"results": [{"article_id": "a2", "title": "X", "content_raw": "禱告"}]},
        ]
    )

    out = run_baseline(
        candidates_path="ignored",
        candidates=cands,
        http_client=fake_client,
    )
    assert out["summary"]["total"] == 2
    assert len(out["results"]) == 2
    assert len(fake_client.requests) == 2


def test_run_baseline_clear_cache_requires_db_path():
    with pytest.raises(SearchError, match="clear_cache=True requires db_path"):
        run_baseline("ignored", clear_cache=True, candidates=[])


def test_run_baseline_clear_cache_wipes_table(db_with_cache):
    cands = [Candidate(id=1, query="Q", expected_keywords=("kw",))]
    fake_client = _FakeClient([{"results": []}])
    run_baseline(
        candidates_path="ignored",
        candidates=cands,
        clear_cache=True,
        db_path=db_with_cache,
        http_client=fake_client,
    )
    n_remaining = clear_rewrite_cache(db_with_cache)
    assert n_remaining == 0  # already cleared by run_baseline


def test_run_baseline_summary_schema(tmp_path):
    cands = [
        Candidate(
            id=1,
            query="Q",
            expected_keywords=("kw",),
            category="cat-A",
            source="src-1",
        ),
    ]
    fake_client = _FakeClient(
        [
            {"results": [{"article_id": "a1", "title": "X", "content_raw": "kw kw kw"}]},
        ]
    )
    out = run_baseline(
        candidates_path="ignored",
        candidates=cands,
        http_client=fake_client,
    )
    summary = out["summary"]
    assert "pass_count" in summary
    assert "pass_rate_pct" in summary
    assert "by_predicate" in summary
    assert "by_category" in summary
    assert "by_source" in summary
    assert summary["by_category"]["cat-A"]["total"] == 1


def test_run_baseline_loads_from_jsonl(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(
        '{"id": 1, "query": "Q", "expected_keywords": ["kw"]}\n',
        encoding="utf-8",
    )
    fake_client = _FakeClient([{"results": []}])
    out = run_baseline(
        candidates_path=p,
        http_client=fake_client,
    )
    assert out["summary"]["total"] == 1
