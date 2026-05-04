# Extending wenji's Ranker

wenji v0.3.6 ships a multi-stage retrieval pipeline that combines:

1. Hybrid (BM25 + vector cosine) retrieval
2. Chunk-level BM25 rollup
3. Reciprocal Rank Fusion (RRF) merge with optional intent boost
4. Entity scoring + hard filter (optional)
5. Ranker hooks (optional, additive boosts)

This document explains how to wire your corpus into the pipeline.

## Loading the wheel-bundled `corpus-christian` example

```python
from wenji.search.entity import EntityScorer
from wenji.search.intent import IntentClassifier

scorer = EntityScorer.from_sources(["example:corpus-christian"])
classifier = IntentClassifier.from_sources(["example:corpus-christian"])
```

`example:<name>` resolves to `wenji.examples.<name>` namespace package.
The wheel bundles `entity_concepts.json` and `intent_keywords.json`
under each example directory (see `src/wenji/examples/corpus_christian/`).

## Composing multiple sources

```python
scorer = EntityScorer.from_sources([
    "example:corpus-christian",       # public neutral theological vocab
    "/private/my_aliases.json",       # corpus-specific aliases
    "./additional_concepts.json",     # local additions
])
```

Sources are merged with **last-write-wins** semantics: `additional_concepts.json`
overrides earlier sources on key collision.

Network URLs (`http://`, `https://`) are rejected to prevent accidental
remote fetch.

## Wiring components into `Searcher`

```python
from wenji.search import Searcher
from wenji.search.ranker import ChunkHitBooster

searcher = Searcher(
    conn,
    embedder,
    rewriter=rewriter,                # v0.3.2 LLM query rewrite
    entity_scorer=scorer,             # v0.3.6 entity scoring
    intent_classifier=classifier,     # v0.3.6 intent boost in RRF
    ranker_hooks=[ChunkHitBooster()], # v0.3.6 additive boosters
)
results = searcher.search("因信稱義", limit=10)
```

When `entity_scorer`, `intent_classifier`, and `ranker_hooks` are all
unset (None / empty), the pipeline degrades to pure RRF + chunk_signals
— still a strict improvement over the v0.3.5 hybrid linear combine.

## Setting components from environment variables

`wenji serve` reads:

| Variable                   | Effect                                         |
|----------------------------|------------------------------------------------|
| `WENJI_ENTITY_SOURCES`     | Comma-separated source list → `EntityScorer`   |
| `WENJI_INTENT_SOURCES`     | Comma-separated source list → `IntentClassifier` |
| `WENJI_LLM_*` (v0.3.2)     | LLM rewrite (see v0.3.2 changelog)             |

CLI flags `--entity-source` and `--intent-source` (repeatable) override
the env-derived defaults for one invocation.

## Writing a custom `RankerHook`

```python
from wenji.search.ranker import RankerHook

class TitleLengthBooster:
    """Tiny boost favoring shorter titles."""

    def __init__(self, weight: float = 0.01):
        self.weight = weight

    def boost(self, article, query, context) -> float:
        title = article.get("title") or ""
        return -self.weight * len(title)
```

`RankerHook` is a `typing.Protocol`, so any class with a matching
`boost` signature is usable. Pass a list of hooks to `Searcher`:

```python
searcher = Searcher(
    conn, embedder,
    ranker_hooks=[ChunkHitBooster(), TitleLengthBooster()],
)
```

Hooks are applied **additively** to `_rankingScore` in list order, after
entity scoring.

## Setting `intent_source_types` (RRF intent boost)

Intent → source_type mapping is corpus-deployment-specific and is NOT
loaded from examples. Pass it via the `IntentClassifier` constructor:

```python
classifier = IntentClassifier.from_sources(
    sources=["example:corpus-christian"],
    intent_source_types={
        "apologetics": ["bol", "teaching"],   # boost these source_types
    },
)
```

When detected intent matches a key, the RRF merge adds `1/(k+1) ≈ 0.0164`
(with k=60) to articles whose `source_type` is in the set.

## Spec / Decision references

- `openspec/specs/wenji-search-engine/spec.md` — modified Searcher pipeline
- `openspec/specs/wenji-ranker-pipeline/spec.md` — RRF / EntityScorer /
  IntentClassifier / RankerHook contracts
- `openspec/specs/wenji-corpus-examples/spec.md` — wheel-bundled examples + `from_sources` API
- Change proposal: `wenji-ranker-port-v0-3-6` (v0.3.6)
