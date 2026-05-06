"""Tests for wenji.eval.run_baseline (multi-path schema, mocked HTTP)."""

from __future__ import annotations

import pytest

from wenji.core.db import connect, initialise_schema
from wenji.core.errors import SearchError
from wenji.eval import clear_rewrite_cache, run_baseline
from wenji.eval.jsonl import Candidate, GoldPath


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


def _multi_path_cand(cid: int, query: str, keywords: tuple[str, ...]) -> Candidate:
    return Candidate(
        id=cid,
        query=query,
        gold_paths=(GoldPath(path_tag="default", keywords=keywords),),
    )


def test_run_baseline_basic_with_injected_client_multi_path():
    cands = [
        _multi_path_cand(1, "因信稱義", ("恩典", "稱義")),
        _multi_path_cand(2, "禱告", ("禱告",)),
    ]
    fake_client = _FakeClient(
        [
            {
                "results": [
                    {
                        "article_id": "a1",
                        "title": "X",
                        "rank": 1,
                        "score": 0.9,
                        "content_full": "恩典 稱義 主",
                    }
                ]
            },
            {
                "results": [
                    {
                        "article_id": "a2",
                        "title": "X",
                        "rank": 1,
                        "score": 0.9,
                        "content_full": "禱告",
                    }
                ]
            },
        ]
    )

    out = run_baseline(
        candidates_path="ignored",
        candidates=cands,
        http_client=fake_client,
    )
    assert out["summary"]["total"] == 2
    assert out["summary"]["pass_count"] == 2
    assert out["summary"]["pass_rate_pct"] == 100.0
    assert len(out["results"]) == 2
    assert len(fake_client.requests) == 2


def test_run_baseline_clear_cache_requires_db_path():
    with pytest.raises(SearchError, match="clear_cache=True requires db_path"):
        run_baseline("ignored", clear_cache=True, candidates=[])


def test_run_baseline_clear_cache_wipes_table(db_with_cache):
    cands = [_multi_path_cand(1, "Q", ("kw",))]
    fake_client = _FakeClient([{"results": []}])
    run_baseline(
        candidates_path="ignored",
        candidates=cands,
        clear_cache=True,
        db_path=db_with_cache,
        http_client=fake_client,
    )
    n_remaining = clear_rewrite_cache(db_with_cache)
    assert n_remaining == 0


def test_run_baseline_summary_schema_multi_path():
    cands = [
        Candidate(
            id=1,
            query="Q",
            gold_paths=(GoldPath(path_tag="p1", keywords=("kw",)),),
            category="cat-A",
            source="src-1",
        ),
    ]
    fake_client = _FakeClient(
        [
            {
                "results": [
                    {
                        "article_id": "a1",
                        "title": "X",
                        "rank": 1,
                        "score": 0.9,
                        "content_full": "kw kw kw",
                    }
                ]
            },
        ]
    )
    out = run_baseline(
        candidates_path="ignored",
        candidates=cands,
        http_client=fake_client,
    )
    summary = out["summary"]
    assert summary["pass_count"] == 1
    assert summary["pass_rate_pct"] == 100.0
    assert summary["partial_pass_count"] == 0
    assert summary["mean_passing_path_count"] == 1.0
    assert "mrr_at_5" in summary
    assert "elapsed_total_sec" in summary
    assert summary["by_category"]["cat-A"]["count"] == 1


def test_run_baseline_loads_from_jsonl(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text(
        '{"id": 1, "query": "Q", "gold_paths": [{"path_tag": "default", "keywords": ["kw"]}]}\n',
        encoding="utf-8",
    )
    fake_client = _FakeClient([{"results": []}])
    out = run_baseline(
        candidates_path=p,
        http_client=fake_client,
    )
    assert out["summary"]["total"] == 1
    assert out["summary"]["pass_count"] == 0


def test_run_baseline_per_question_has_multi_path_metrics():
    cands = [
        Candidate(
            id=1,
            query="Q",
            gold_paths=(
                GoldPath(path_tag="p1", keywords=("a",)),
                GoldPath(path_tag="p2", keywords=("b",)),
            ),
        )
    ]
    fake_client = _FakeClient(
        [
            {
                "results": [
                    {"article_id": "x", "title": "T", "rank": 1, "score": 0.9, "content_full": "a"}
                ]
            }
        ]
    )
    out = run_baseline(
        candidates_path="ignored",
        candidates=cands,
        http_client=fake_client,
    )
    r0 = out["results"][0]
    assert r0["pass"] is True
    assert r0["passing_paths"] == ["p1"]
    assert r0["article_results"][0]["gold_path_match"] == {"p1": "full", "p2": "none"}
