"""Query segmentation trace for /api/segment and ``wenji segment``.

Surfaces, for one input query, what wenji's search-side query pipeline sees:

- ``tokens``: jieba.posseg view (text + POS), via the shared
  :func:`wenji.ingest.jieba_setup.jieba_cut_pos` helper.
- ``normalized_query``: the post-normalization string. v0.3.x intentionally
  treats this as the identity transform — Searcher's BM25 path does not
  case-fold or strip punctuation. The hook stays so future normalization can
  be added in one place without breaking trace fidelity.
- ``fts_form``: the actual FTS5 MATCH expression Searcher constructs, via the
  shared :func:`wenji.search.bm25.build_fts_query` helper. Exposed so users
  can see the char-level expansion that a jieba word-level view does not
  reveal.
- ``dict_hits``: jieba tokens whose text is present in the loaded user_dict.
- ``rewrite``: result of v0.3.2 LLM rewrite (cache-aware), or null when the
  rewriter is unconfigured / falls back / errors.
"""

from __future__ import annotations

import time
from typing import TypedDict

from wenji.ingest.jieba_setup import jieba_cut_pos
from wenji.search.bm25 import build_fts_query
from wenji.search.entity import EntityScorer
from wenji.search.intent import IntentClassifier
from wenji.search.rewrite import QueryRewriter


class TokenInfo(TypedDict):
    text: str
    pos: str


class RewriteInfo(TypedDict):
    rewritten_query: str
    source: str  # "llm" or "cache"
    latency_ms: int


class EntityInfo(TypedDict):
    name: str
    type: str
    role: str
    aliases: list[str]


class IntentInfo(TypedDict):
    intent: str
    boost_types: list[str] | None


class SegmentTrace(TypedDict):
    query: str
    tokens: list[TokenInfo]
    normalized_query: str
    fts_form: str
    dict_hits: list[str]
    rewrite: RewriteInfo | None
    entities: list[EntityInfo] | None
    intent: IntentInfo | None


def _normalize(query: str) -> str:
    """v0.3.x identity normalization (see module docstring)."""
    return query


def _dict_hits(tokens: list[TokenInfo]) -> list[str]:
    """Subset of token texts present in jieba's loaded user_dict.

    Order-preserving and de-duplicated. Reads
    :func:`wenji.ingest.jieba_setup.loaded_user_terms`, which wenji maintains
    independently — ``jieba.dt.user_word_tag_tab`` gets cleared by
    ``jieba.posseg.cut`` on first call and is unreliable here.
    """
    from wenji.ingest.jieba_setup import loaded_user_terms

    user_terms = loaded_user_terms()
    if not user_terms:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        text = tok["text"]
        if text in user_terms and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _rewrite(query: str, rewriter: QueryRewriter | None) -> RewriteInfo | None:
    if rewriter is None or not query.strip():
        return None
    cached = rewriter.peek_cache(query)
    start = time.perf_counter()
    try:
        rewritten = rewriter.rewrite(query)
    except Exception:
        return None
    latency_ms = int((time.perf_counter() - start) * 1000)
    if not rewritten or rewritten == query:
        return None
    source = "cache" if cached is not None else "llm"
    return {
        "rewritten_query": rewritten,
        "source": source,
        "latency_ms": latency_ms,
    }


def compute_segment_trace(
    query: str,
    *,
    rewriter: QueryRewriter | None = None,
    entity_scorer: EntityScorer | None = None,
    intent_classifier: IntentClassifier | None = None,
) -> SegmentTrace:
    """Return the segmentation trace for ``query``.

    Empty / whitespace-only queries are caller-validated; this function
    returns an empty-tokens trace rather than raising, to keep the public
    contract straightforward (HTTP 400 lives in the route handler).

    v0.3.6: ``entities`` and ``intent`` fields surface EntityScorer /
    IntentClassifier output when those dependencies are injected; remain
    ``None`` when not provided (backward compatible with v0.3.3 schema).
    """
    raw_tokens = jieba_cut_pos(query)
    tokens: list[TokenInfo] = [{"text": t, "pos": p} for t, p in raw_tokens]
    normalized = _normalize(query)

    entities: list[EntityInfo] | None = None
    if entity_scorer is not None:
        detected = entity_scorer.detect_query_entities(query)
        entities = [
            {
                "name": e.name,
                "type": e.type,
                "role": e.role,
                "aliases": list(e.aliases),
            }
            for e in detected
        ]

    intent: IntentInfo | None = None
    if intent_classifier is not None:
        name = intent_classifier.detect_intent(query)
        boost = intent_classifier.get_boost_types(name)
        intent = {
            "intent": name,
            "boost_types": sorted(boost) if boost else None,
        }

    return {
        "query": query,
        "tokens": tokens,
        "normalized_query": normalized,
        "fts_form": build_fts_query(normalized),
        "dict_hits": _dict_hits(tokens),
        "rewrite": _rewrite(query, rewriter),
        "entities": entities,
        "intent": intent,
    }
