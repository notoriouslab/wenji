"""Tests for ``wenji.search.ranker`` — RankerHook protocol + ChunkHitBooster."""

from __future__ import annotations

from wenji.search.ranker import ChunkHitBooster, RankerHook, apply_ranker_hooks


def test_chunk_hit_booster_caps_at_max_hits():
    b = ChunkHitBooster(weight=0.05, max_hits_capped=5)
    article = {"chunk_hits": 10}
    assert b.boost(article, "q", {}) == 0.05 * 5  # capped


def test_chunk_hit_booster_zero_hits_returns_zero():
    b = ChunkHitBooster()
    assert b.boost({"chunk_hits": 0}, "q", {}) == 0.0
    assert b.boost({}, "q", {}) == 0.0  # missing key
    assert b.boost({"chunk_hits": None}, "q", {}) == 0.0  # None defended


def test_chunk_hit_booster_below_cap_proportional():
    b = ChunkHitBooster(weight=0.1, max_hits_capped=10)
    assert abs(b.boost({"chunk_hits": 3}, "q", {}) - 0.3) < 1e-9


class _FixedHook:
    """Custom hook duck-typed against RankerHook Protocol."""

    def __init__(self, value: float):
        self.value = value

    def boost(self, article, query, context):
        return self.value


def test_custom_hook_satisfies_protocol():
    h = _FixedHook(0.2)
    assert isinstance(h, RankerHook)


def test_apply_ranker_hooks_chain_accumulates():
    articles = [{"article_id": "a1", "_rankingScore": 0.5, "chunk_hits": 3}]
    hooks = [ChunkHitBooster(weight=0.05, max_hits_capped=5), _FixedHook(0.1)]
    out = apply_ranker_hooks(articles, "q", hooks)
    expected = 0.5 + (0.05 * 3) + 0.1
    assert abs(out[0]["_rankingScore"] - expected) < 1e-9


def test_apply_ranker_hooks_empty_list_unchanged():
    articles = [{"article_id": "a1", "_rankingScore": 0.5}]
    out = apply_ranker_hooks(articles, "q", [])
    assert out[0]["_rankingScore"] == 0.5


def test_apply_ranker_hooks_none_unchanged():
    articles = [{"article_id": "a1", "_rankingScore": 0.5}]
    out = apply_ranker_hooks(articles, "q", None)
    assert out[0]["_rankingScore"] == 0.5


def test_apply_ranker_hooks_missing_score_treated_as_zero():
    articles = [{"article_id": "a1"}]  # no _rankingScore
    out = apply_ranker_hooks(articles, "q", [_FixedHook(0.3)])
    assert out[0]["_rankingScore"] == 0.3


def test_apply_ranker_hooks_preserves_metadata():
    articles = [
        {"article_id": "a1", "_rankingScore": 0.5, "title": "x", "chunk_hits": 2}
    ]
    apply_ranker_hooks(articles, "q", [ChunkHitBooster()])
    assert articles[0]["title"] == "x"
    assert articles[0]["chunk_hits"] == 2
