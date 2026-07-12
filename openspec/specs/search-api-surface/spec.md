# search-api-surface Specification

## Purpose

TBD - created by archiving change 'api-slim-0-5'. Update Purpose after archive.

## Requirements

### Requirement: Searcher construction contract is six parameters

`Searcher.__init__` SHALL accept exactly `conn`, `embedder`, and keyword-only `alpha`, `candidate_pool`, `entity_scorer`, `intent_classifier`. It MUST NOT accept `rewriter`, `reranker`, or `ranker_hooks`.

#### Scenario: removed keyword arguments fail loudly

- **WHEN** caller invokes `Searcher(conn, embedder, rewriter=obj)` (likewise `reranker=` or `ranker_hooks=`)
- **THEN** Python raises `TypeError` (unexpected keyword argument)

#### Scenario: six-parameter construction succeeds

- **WHEN** caller invokes `Searcher(conn, embedder, alpha=0.25, candidate_pool=50, entity_scorer=None, intent_classifier=None)`
- **THEN** the instance is constructed and `search()` runs the pipeline without rewrite, rerank, or ranker-hook steps

---
### Requirement: Removed retrieval paths leave no runtime trace

The modules `wenji/search/rewrite.py`, `wenji/search/rerank.py`, and `wenji/search/ranker.py` SHALL NOT exist. `wenji.search` MUST NOT export `QueryRewriter`, `CrossEncoderReranker`, `RankerHook`, or `apply_ranker_hooks`. `IntentClassifier.classify_intent` SHALL NOT exist (`detect_intent` and `get_boost_types` remain). No production code path SHALL read `WENJI_REWRITE_OVERRIDE` or `WENJI_LLM_REWRITE_CACHE_TTL_DAYS`. The eval tooling SHALL carry no rewrite surface: `eval.clear_rewrite_cache` SHALL NOT exist and `wenji eval run-benchmark` SHALL NOT offer `--clear-cache`, `--enable-rewrite`, or `--no-rewrite` (run artifacts drop the rewrite tag; pass/miss comparison fields are unchanged). `core/model_download.py` SHALL retain only the embedder model path (no reranker download function); `wenji download` SHALL offer no reranker option. `LLMConfig`/`LLMClient` (ask/aggregate consumers) MUST remain functional. Retained production identifiers that merely contain generic wording — `EntityScorer.score_and_rerank` (entity-layer re-ranking, kept) and the URL-rewrite comment in `web/branding.py` — are NOT in scope of this removal. The literal table name `query_rewrite_cache` necessarily survives in exactly two places — the v2→v3 DROP statement in `core/db.py` and the schema.sql version-history comment — and is therefore excluded from the audit symbol set. Likewise the removed-keyword contract test (`test_search_searcher.py`) necessarily names the rejected parameters as string literals; that single test function is the only permitted hit.

#### Scenario: removed symbols are import errors

- **WHEN** code executes `from wenji.search import QueryRewriter`
- **THEN** Python raises `ImportError`

#### Scenario: repository-wide residue audit by exact symbols

- **WHEN** `rg "QueryRewriter|CrossEncoderReranker|RankerHook|apply_ranker_hooks|ranker_hooks|ChunkHitBooster|WENJI_REWRITE_OVERRIDE|WENJI_RERANKER_DIR|WENJI_LLM_REWRITE_CACHE_TTL_DAYS|rewrite_cache_ttl_days|download_reranker_model|RERANKER_MODEL_DEFAULT|RewriteConfig|RerankConfig|RewriteInfo|clear_rewrite_cache|rewritten_query" src/ tests/` runs on the final tree
- **THEN** the only hit is the removed-keyword contract test in `test_search_searcher.py` (CHANGELOG and openspec history are outside the audited paths; the generic-word production identifiers listed in the requirement stay untouched and are not matched by this symbol set)

#### Scenario: search API response drops the rewrite field

- **WHEN** `GET /api/search?q=禱告` returns under 0.5.0
- **THEN** the JSON payload contains `results` and `query` and no `rewritten_query` key

#### Scenario: eval clear-cache path cannot crash on v3

- **WHEN** `wenji eval run-benchmark --clear-cache` is invoked under 0.5.0
- **THEN** the CLI rejects the unknown option with exit code 2 (the flag no longer exists, so no code path queries the dropped `query_rewrite_cache` table)

#### Scenario: ask keeps its LLM client

- **WHEN** `wenji ask`-backed aggregate flow runs with `WENJI_LLM_*` env configured
- **THEN** `LLMClient` instantiates and answers exactly as in 0.4.0

---
### Requirement: search config takes effect at every Searcher entry point

The three Searcher entry points (web app factory, `wenji search` in-process fallback, `Asker` lazy construction) SHALL resolve `search.alpha`, `search.candidate_pool`, and `search.default_limit` from `load_config`. Resolution order MUST be: CLI `--config` flag (where a CLI exists) > `WENJI_CONFIG` environment variable > built-in defaults. `default_limit` applies only when the caller does not pass an explicit limit (CLI `--limit` / web `limit` query param); an explicit per-request limit MUST always win over config. With no config provided, effective values MUST equal 0.4.0 hardcoded behavior (alpha 0.25, candidate_pool 50, limit 10).

#### Scenario: yaml alpha reaches the Searcher

- **WHEN** `WENJI_CONFIG` points to a yaml containing `search: {alpha: 0.9}` and the web app builds its Searcher
- **THEN** the constructed Searcher has `alpha == 0.9` and `candidate_pool == 50` (unset keys keep defaults)

#### Scenario: CLI flag beats environment

- **WHEN** `WENJI_CONFIG` points to yaml A (alpha 0.9) and `wenji search --config B.yaml` runs where B sets alpha 0.5
- **THEN** the fallback Searcher is constructed with alpha 0.5

#### Scenario: no config means bit-identical 0.4.0 behavior

- **WHEN** neither `WENJI_CONFIG` nor `--config` is set
- **THEN** the 80q+r14 regression benchmark before/after this change reports identical pass results

---
### Requirement: CLI config parsing has a single entry point

`wenji ingest dir` and `wenji rebuild` SHALL obtain `directory_map` and `chunk_strategies` via `load_config` instead of hand-rolled yaml parsing. Malformed yaml MUST surface as `ConfigError` with the loader's message.

#### Scenario: broken yaml fails identically across commands

- **WHEN** the same syntactically-invalid yaml is passed as `--config` to `ingest dir`, `rebuild`, and `search`
- **THEN** each command reports the same `ConfigError`-derived message and exits non-zero

---
### Requirement: Schema v3 removes the rewrite cache with in-place migration

`SCHEMA_VERSION` SHALL be `"3"` and `schema.sql` SHALL NOT define `query_rewrite_cache`. `initialise_schema` on a v2 database MUST drop `query_rewrite_cache` (if present), set `schema_version` to `3`, and preserve all other data. Versions other than 2 and 3 MUST raise `SchemaError` unchanged. Read-only entry points (`connect` without `initialise_schema`: serve, search, doctor) MUST NOT perform migration and MUST operate normally on a v2 database.

#### Scenario: fresh database is v3 without the cache table

- **WHEN** `initialise_schema` runs on an empty database
- **THEN** `sqlite_master` contains no `query_rewrite_cache` and `wenji_meta.schema_version` is `3`

#### Scenario: v2 database upgrades on next write entry

- **WHEN** `wenji ingest dir` runs against a 0.4.0-built database (v2, cache table present, corpus populated)
- **THEN** after the run the cache table is gone, `schema_version` is `3`, and article/chunk/vector row counts are unchanged except for the ingested delta

#### Scenario: v2 database serves reads without migration

- **WHEN** `wenji serve` or `wenji doctor` opens a v2 database under 0.5.0
- **THEN** no schema change occurs and search/health behavior is normal

---
### Requirement: segment trace drops rewrite instrumentation

`wenji segment` output SHALL NOT contain a rewrite section, and the flags `--enable-rewrite` / `--no-rewrite` SHALL NOT exist.

#### Scenario: trace shape after removal

- **WHEN** `wenji segment "馬丁路德的神學"` runs
- **THEN** the JSON output contains tokenization/entity/intent/fts-query sections and no `rewrite` key

#### Scenario: stale flag is rejected

- **WHEN** `wenji segment "query" --no-rewrite` runs
- **THEN** the CLI exits with code 2 (unknown option)

#### Scenario: serve and search reject stale rewrite flags symmetrically

- **WHEN** `wenji serve --no-rewrite` or `wenji search "query" --enable-rewrite` runs under 0.5.0
- **THEN** each CLI exits with code 2 (unknown option) — no sibling command silently accepts a removed rewrite flag
