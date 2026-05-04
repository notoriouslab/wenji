"""Tests for wenji.search.rewrite (mocked HTTP)."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from wenji.core.db import connect, initialise_schema
from wenji.search import rewrite as rewrite_mod
from wenji.search.rewrite import QueryRewriter


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = connect(":memory:")
    initialise_schema(c)
    yield c
    c.close()


def _make_rewriter(conn, **overrides) -> QueryRewriter:
    return QueryRewriter(
        conn,
        api_url="https://api.example/v1/chat/completions",
        api_key="test-key",
        timeout=overrides.pop("timeout", 1.5),
        ttl_days=overrides.pop("ttl_days", 30),
        **overrides,
    )


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_rewrite_calls_api_and_caches(conn, monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _FakeResponse({"choices": [{"message": {"content": "rewritten 因信稱義 grace"}}]})

    monkeypatch.setattr(rewrite_mod.httpx, "post", fake_post)
    r = _make_rewriter(conn)

    out1 = r.rewrite("因信稱義")
    assert out1 == "rewritten 因信稱義 grace"
    assert len(calls) == 1

    out2 = r.rewrite("因信稱義")
    assert out2 == out1
    assert len(calls) == 1  # cache hit


def test_rewrite_timeout_falls_back_to_raw(conn, monkeypatch):
    def fake_post(*a, **kw):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(rewrite_mod.httpx, "post", fake_post)
    r = _make_rewriter(conn, timeout=0.1)
    assert r.rewrite("因信稱義") == "因信稱義"


def test_rewrite_http_error_falls_back(conn, monkeypatch):
    def fake_post(*a, **kw):
        raise httpx.HTTPStatusError(
            "boom", request=httpx.Request("POST", "x"), response=httpx.Response(500)
        )

    monkeypatch.setattr(rewrite_mod.httpx, "post", fake_post)
    r = _make_rewriter(conn)
    assert r.rewrite("query") == "query"


def test_rewrite_empty_returns_empty(conn):
    r = _make_rewriter(conn)
    assert r.rewrite("") == ""
    assert r.rewrite("   ") == "   "


def test_clear_cache(conn, monkeypatch):
    def fake_post(*a, **kw):
        return _FakeResponse({"choices": [{"message": {"content": "X"}}]})

    monkeypatch.setattr(rewrite_mod.httpx, "post", fake_post)
    r = _make_rewriter(conn)
    r.rewrite("q1")
    r.rewrite("q2")
    n_before = conn.execute("SELECT COUNT(*) FROM query_rewrite_cache").fetchone()[0]
    assert n_before == 2
    r.clear_cache()
    n_after = conn.execute("SELECT COUNT(*) FROM query_rewrite_cache").fetchone()[0]
    assert n_after == 0


def test_ttl_expiry_invalidates_cache(conn, monkeypatch):
    def fake_post(*a, **kw):
        return _FakeResponse({"choices": [{"message": {"content": "X"}}]})

    monkeypatch.setattr(rewrite_mod.httpx, "post", fake_post)
    r = _make_rewriter(conn, ttl_days=0)
    r.rewrite("q")
    # ttl_days=0 → cutoff is now → previous row's created_at <= cutoff so no hit
    assert conn.execute("SELECT COUNT(*) FROM query_rewrite_cache").fetchone()[0] == 1
    # Force-old created_at to ensure cache miss
    conn.execute("UPDATE query_rewrite_cache SET created_at = '1970-01-01T00:00:00+00:00'")
    conn.commit()
    out = r.rewrite("q")
    assert out == "X"  # API called again


def test_default_prompt_template_targets_keyword_form_aligned_with_logos():
    # v0.3.6.1: rewrite-on regressed -10pp vs rewrite-off because the v0.3.6
    # prompt asked for a "vector-friendly single-line query" which produced
    # natural-language sentence expansions; logos production prompt asks for
    # keyword groups separated by `|` (BM25-friendly), and that form matches
    # how the eval metric scores hits. Lock the prompt shape so future edits
    # don't silently regress.
    template = rewrite_mod.REWRITE_PROMPT_TEMPLATE
    assert "{query}" in template, "template must include {query} placeholder"
    assert "|" in template, "prompt must instruct keyword-group form (logos parity)"
    assert "範例" in template, "prompt must include few-shot example section"
    assert "1-3" in template, "prompt must specify 1-3 keyword groups"
    # Negative: must not push the LLM toward sentence expansion.
    assert "向量檢索" not in template, "prompt must not bias output toward vector form"
    assert "近義同義詞" not in template, "prompt must not request synonym expansion"
