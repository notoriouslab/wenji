"""Tests for ``wenji.observability.compute_segment_trace``."""

from __future__ import annotations

from wenji.ingest.jieba_setup import jieba_cut_pos, reset_for_test
from wenji.observability import compute_segment_trace
from wenji.search.bm25 import build_fts_query


def test_segment_chinese_query_returns_tokens_and_fts_form():
    trace = compute_segment_trace("因信稱義是什麼")

    assert trace["query"] == "因信稱義是什麼"
    assert trace["normalized_query"] == "因信稱義是什麼"
    assert len(trace["tokens"]) > 0
    for tok in trace["tokens"]:
        assert "text" in tok and "pos" in tok
    # Searcher's MATCH form: char-level + space + phrase-quoted
    assert trace["fts_form"] == build_fts_query("因信稱義是什麼")
    assert trace["rewrite"] is None  # no rewriter passed


def test_segment_empty_query_returns_empty_tokens():
    trace = compute_segment_trace("")
    assert trace["query"] == ""
    assert trace["tokens"] == []
    assert trace["fts_form"] == ""
    assert trace["dict_hits"] == []
    assert trace["rewrite"] is None


def test_segment_uses_shared_jieba_helper():
    """Trace.tokens MUST equal direct jieba_cut_pos output (Requirement: shared helpers)."""
    q = "禱告與屬靈生命"
    direct = jieba_cut_pos(q)
    trace = compute_segment_trace(q)
    assert trace["tokens"] == [{"text": t, "pos": p} for t, p in direct]


def test_segment_uses_shared_fts_form_helper():
    """Trace.fts_form MUST equal direct build_fts_query output (Requirement: shared helpers)."""
    q = "宣教 大使命"
    assert compute_segment_trace(q)["fts_form"] == build_fts_query(q)


def test_segment_dict_hits_picks_user_dict_tokens(tmp_path):
    """When jieba user_dict has been loaded via configure_jieba, dict_hits surfaces it.

    Goes through wenji's configure_jieba (not jieba.load_userdict directly) so
    the term lands in :func:`loaded_user_terms` — the source of truth for
    observability since posseg.cut resets jieba.dt.user_word_tag_tab.
    """
    from wenji.ingest.jieba_setup import configure_jieba

    reset_for_test()
    dict_path = tmp_path / "user_dict.txt"
    dict_path.write_text("因信稱義 1000 n\n", encoding="utf-8")
    configure_jieba(custom_dicts=[dict_path])

    trace = compute_segment_trace("因信稱義是什麼")
    assert "因信稱義" in trace["dict_hits"]


class _StubRewriter:
    """Stand-in QueryRewriter without httpx dependency."""

    def __init__(self, *, rewritten: str | None, raises: bool = False):
        self._rewritten = rewritten
        self._raises = raises
        self._cache: dict[str, str] = {}

    def peek_cache(self, raw: str) -> str | None:
        return self._cache.get(raw)

    def rewrite(self, raw: str) -> str:
        if self._raises:
            raise RuntimeError("simulated LLM failure")
        if raw in self._cache:
            return self._cache[raw]
        if self._rewritten is None:
            return raw
        self._cache[raw] = self._rewritten
        return self._rewritten


def test_segment_rewrite_llm_path():
    rewriter = _StubRewriter(rewritten="因信稱義")
    trace = compute_segment_trace("信稱義", rewriter=rewriter)
    assert trace["rewrite"] is not None
    assert trace["rewrite"]["rewritten_query"] == "因信稱義"
    assert trace["rewrite"]["source"] == "llm"
    assert trace["rewrite"]["latency_ms"] >= 0


def test_segment_rewrite_cache_path():
    rewriter = _StubRewriter(rewritten="因信稱義")
    # warm cache
    compute_segment_trace("信稱義", rewriter=rewriter)
    trace = compute_segment_trace("信稱義", rewriter=rewriter)
    assert trace["rewrite"] is not None
    assert trace["rewrite"]["source"] == "cache"


def test_segment_rewrite_returns_null_on_unchanged():
    rewriter = _StubRewriter(rewritten=None)  # rewrite returns the raw query
    trace = compute_segment_trace("因信稱義", rewriter=rewriter)
    assert trace["rewrite"] is None


def test_segment_rewrite_returns_null_on_exception():
    rewriter = _StubRewriter(rewritten=None, raises=True)
    trace = compute_segment_trace("因信稱義", rewriter=rewriter)
    assert trace["rewrite"] is None


def test_segment_no_rewriter_returns_null():
    trace = compute_segment_trace("因信稱義", rewriter=None)
    assert trace["rewrite"] is None
