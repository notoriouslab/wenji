# Extending wenji's Retrieval Pipeline

wenji ships a multi-stage retrieval pipeline that combines:

1. Hybrid (BM25 + vector cosine) retrieval
2. Chunk-level BM25 rollup
3. Reciprocal Rank Fusion (RRF) merge with optional intent boost
4. Entity scoring + hard filter (optional)

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

searcher = Searcher(
    conn,
    embedder,
    alpha=0.25,                       # BM25/vector fusion weight
    candidate_pool=50,                # top-K per retriever before RRF
    entity_scorer=scorer,             # entity scoring + hard filter
    intent_classifier=classifier,     # intent boost in RRF
)
results = searcher.search("因信稱義", limit=10)
```

When `entity_scorer` and `intent_classifier` are unset (None), the
pipeline degrades to pure RRF + chunk_signals.

## Tuning via `wenji.yaml` (v0.5.0)

`search.alpha`, `search.candidate_pool`, and `search.default_limit` are
read from the config file at every Searcher entry point (web app, `wenji
search` fallback, `Asker`):

```yaml
search:
  alpha: 0.25
  candidate_pool: 50
  default_limit: 10
```

Resolution order: CLI `--config` flag > `WENJI_CONFIG` environment
variable > built-in defaults. An explicit per-request `limit` always
beats `default_limit`.

## Setting components from environment variables

`wenji serve` reads:

| Variable               | Effect                                           |
|------------------------|--------------------------------------------------|
| `WENJI_CONFIG`         | Path to `wenji.yaml` (search tuning, see above)  |
| `WENJI_ENTITY_SOURCES` | Comma-separated source list → `EntityScorer`     |
| `WENJI_INTENT_SOURCES` | Comma-separated source list → `IntentClassifier` |

CLI flags `--entity-source` and `--intent-source` (repeatable) override
the env-derived defaults for one invocation.

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

## Declaring directory structure as source-type truth (v0.5.0)

By default, an article's frontmatter `source_type` beats the
`directory_map` fallback. Deployments whose taxonomy is carried by
directory layout can invert that:

```yaml
directory_map:
  tgc: tgc-theology
directory_map_overrides_frontmatter: true
```

With the flag on, a `directory_map` hit wins over frontmatter; a miss
still falls back to frontmatter (never an error for files mapped
elsewhere).

## Spec / Decision references

- `openspec/specs/` — capability specs, including `search-api-surface`
  (Searcher construction contract, v0.5.0) and `source-type-resolution`
- Removed in v0.5.0: LLM query rewrite, cross-encoder reranker hook, and
  the `RankerHook` chain — see the 0.5.0 CHANGELOG entry for rationale
