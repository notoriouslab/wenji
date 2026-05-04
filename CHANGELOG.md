# Changelog

All notable changes to **wenji** will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed (v0.3.6.1)

- **`QueryRewriter` prompt aligned with logos production** — the
  v0.3.6 prompt asked the LLM for a "vector-friendly single-line
  query" with synonym expansion, which produced natural-language
  sentence rewrites; on the 80q baseline this regressed -10pp vs
  rewrite-off (67.5% vs 77.5% pass@3 partial+, OPEN-10). Replaced
  with logos production prompt shape (1-3 keyword groups separated
  by `|`, BM25-friendly, with few-shot examples). Post-fix
  rewrite-on baseline: **pass@3 partial+ 73.8%**, -1.2pp aligned vs
  logos R13 75.0%. New regression test
  `test_default_prompt_template_targets_keyword_form_aligned_with_logos`
  locks the prompt shape.
- Note: rewrite-on 73.8% is still below wenji rewrite-off 77.5%
  because wenji's vector recall is strong enough that LLM rewrite
  injects noise. Logos production depends on rewrite-on (vector
  weaker pre-port). Default remains rewrite-off; v0.3.7 logos
  migration will revisit how rewrite is wired in production.

### Added / Changed (v0.3.6)

- **`wenji.search.rrf`** — 2-way boost-style RRF merge ports
  ``logos/scripts/rag/ranking.py:rrf_merge`` (logos production v1.1
  ranker, 75.8% baseline). `rrf_merge(main_merged, chunk_signals,
  intent_boost_types, k=60)` combines hybrid (BM25 + vector) ranking
  with chunk-level BM25 roll-up; intent boost layer adds `1/(k+1)` per
  matching `source_type`. Falls back to main-only sort + 0.15 additive
  boost when `chunk_signals` is empty. Includes `chunk_bm25_search`
  helper that over-fetches chunks and aggregates per-article in Python
  (SQLite FTS5 does not support `MIN(bm25())` as aggregate).
- **`wenji.search.entity`** — `EntityScorer` class with
  caller-injected `entity_dict` and `alias_map`; ports the dual-signal
  scoring model `final = alpha * relevance + (1 - alpha) * entity_coverage`
  (alpha=0.5 default, matches logos `entity_scorer.py:308`). Provides
  `detect_query_entities`, `expand_query_with_aliases`, `score_and_rerank`
  (with hard-filter for person/org subject misses), and the
  `_check_entity_in_text` helper. Subject promotion rules favor
  concept/person/org over location.
- **`wenji.search.intent`** — `IntentClassifier` class with
  caller-injected `intent_keywords` and `intent_source_types` maps.
  Provides `detect_intent` (shallow keyword match → intent name),
  `classify_intent` (structured → scripture/person/topic with alpha
  and keyword_boost), and `get_boost_types` (intent → source_type set
  for RRF intent boost layer).
- **`wenji.search.ranker`** — `RankerHook` Protocol with
  `boost(article, query, context) -> float`; built-in `ChunkHitBooster`
  (uses `chunk_hits` already populated by Searcher). Custom hooks
  satisfy the Protocol via duck typing.
- **`Searcher` pipeline rewrite** — `Searcher.search()` now executes
  the v0.3.6 11-step pipeline: rewrite → entity detect → intent detect
  → alias expand → BM25+vector → chunk BM25 → RRF merge with intent
  boost → entity scoring + filter → ranker hooks → reranker (existing
  hook) → snippet hydration. The Searcher input/output schema is
  preserved (BREAKING-free). `alpha` (linear hybrid combine weight) is
  retained as a fallback BM25/vector internal fusion weight; primary
  sort key is `_rankingScore` (post-RRF + entity + hooks).
- **`Searcher.__init__` new optional parameters** — `entity_scorer`,
  `intent_classifier`, `ranker_hooks` accept dependency-injected
  components for the pipeline. Defaults to None for all three:
  pipeline degrades to pure RRF + chunk_signals when none provided
  (still a strict improvement over v0.3.5 linear hybrid).
- **`examples/corpus-christian/` shipped in wheel** — first
  domain-specific reference example, contains `entity_concepts.json`
  (46 neutral theological concepts) and `intent_keywords.json` (65
  apologetics keywords). Filtered to exclude political-ethics terms
  (同性婚姻 / 墮胎 / 安樂死) per the corpus-examples-neutral spec.
- **Multi-source loading API** — `EntityScorer.from_sources(...)` and
  `IntentClassifier.from_sources(...)` accept a list of
  `"example:<name>"` references and absolute/relative paths;
  last-write-wins on key collisions. `EntityScorer.load_example` and
  `IntentClassifier.load_example` provide low-level access. Network
  URLs (`http://`, `https://`) are rejected.
- **Web app + CLI integration** — `wenji serve` accepts
  `--entity-source` / `--intent-source` flags (repeatable). Env vars
  `WENJI_ENTITY_SOURCES` / `WENJI_INTENT_SOURCES`
  (comma-separated) auto-load components into `Searcher`.
- **`/api/segment` schema extension** — adds `entities` and `intent`
  fields when `EntityScorer` / `IntentClassifier` are configured.
  Both are `null` by default (backward compatible with v0.3.3 schema
  apart from the additional optional keys).
- **`QueryRewriter.peek_cache`** — already added in v0.3.3; v0.3.6
  doesn't change it.
- **80q baseline (vs logos R13 75.0%)** — v0.3.6 reaches **pass@3
  partial+ = 77.5%** on 12,090-article SQLite corpus with
  rewrite-off, **+2.5pp aligned** vs logos R13 (`tests/benchmark_v2_r13.json`,
  2026-04-24). Secondary metrics: pass@1 51.2% / pass@5 88.8% /
  pass@10 92.5%. Rewrite-on (Groq llama-3.3-70b-versatile) shows a
  -10pp regression vs rewrite-off — `Searcher` defaults to
  rewrite-off pending an audit of the rewrite prompt vs logos
  production behaviour (see proposal OPEN-10).
- **`Searcher.search()` response now hydrates `content_full`** —
  added a final batch query against `articles_fts` that populates
  `content_full=content_raw[:500]` and `content_snippet` for every
  hit in `top_n`, regardless of which retrieval branch produced
  the hit. Vector-only hits previously reached the response with
  empty content fields (no BM25 match → no `content_raw`), which
  silently broke the v0.3.1 eval metric (`metrics.py:103` reads
  `content_full | content_raw | content`) and the UI snippet
  surface. New regression test
  `test_searcher_response_hydrates_content_full_for_all_hits`
  forces the vector-only path with `alpha=0.0`.

### Added (v0.3.3)

- **Observability endpoints** — read-only `GET /api/stats` and
  `GET /api/segment?q=` for surfacing corpus state and query-pipeline
  internals. Stats reports `articles`, `chunks`, `indices` (FTS5 +
  vector counts and dim), `source_types` (flat dict), `axes` (flat dict
  via axes.yaml; empty when unconfigured), `last_ingest_at` (ISO8601 or
  null). Segment reports `tokens` (jieba.posseg view), `normalized_query`,
  `fts_form` (Searcher's char-level MATCH expression), `dict_hits` (jieba
  user_dict matches), and `rewrite` (v0.3.2 LLM trace, null when disabled).
  No caching — fresh per request; measured ~26ms on a 900-article SQLite
  with 12,866 chunks.
- **`wenji stats` and `wenji segment <query>` CLI** — same data as the
  HTTP endpoints, with human-readable formatters by default and `--json`
  flag for pipe-friendly output that matches the endpoint schema. Useful
  for dev / debug / CI sanity without bringing up the server.
- **`wenji.observability` module** — `compute_stats(conn, axes_config)` and
  `compute_segment_trace(query, rewriter=None)` are the public callables
  used by both API and CLI. Stable, dependency-light entry point for
  third-party integrations.
- **`wenji.ingest.jieba_setup.jieba_cut_pos`** — public helper returning
  `(text, pos)` tuples; canonical entry-point for any code path that
  needs jieba's word-level view of a query (segment trace, future
  alias/synonym expansion). NOTE: the v0.3.x Searcher does NOT call jieba
  at query time — it uses char-level FTS expansion via `build_fts_query`.
- **`wenji.ingest.jieba_setup.loaded_user_terms`** — frozenset of terms
  loaded via `configure_jieba(custom_dicts=...)`. wenji maintains this
  independently because `jieba.posseg.cut` clears
  `jieba.dt.user_word_tag_tab` on first call, making it unreliable as a
  ground-truth source for observability.
- **`wenji.search.rewrite.QueryRewriter.peek_cache(raw)`** — public
  accessor that returns a cached rewrite without making an LLM call. Used
  by segment trace to label `rewrite.source` as `"cache"` vs `"llm"`.

### Added (v0.3.2)

- **LLM query rewrite wiring** — `QueryRewriter` (v0.3.0) now wired into
  `wenji serve` / `wenji search` / `/api/search` / `wenji eval run-benchmark`.
  Previously implemented but unreachable from user-facing entries.
- **`wenji.config.LLMConfig`** dataclass + `load_llm_config_from_env()`
  loader, shared by `wenji.aggregate.llm.LLMClient` and
  `wenji.search.rewrite.QueryRewriter`. Env vars: `WENJI_LLM_BASE_URL` /
  `WENJI_LLM_API_KEY` / `WENJI_LLM_MODEL` (required to enable) +
  `WENJI_LLM_TIMEOUT` (default 10s) + `WENJI_LLM_REWRITE_CACHE_TTL_DAYS`
  (default 30).
- **`--enable-rewrite` / `--no-rewrite` flags** on `wenji serve`,
  `wenji search`, and `wenji eval run-benchmark`. Mutually exclusive;
  override env-derived default for that invocation.
- **`/api/search` response** adds `rewritten_query` field
  (`null` when rewrite disabled / fallback / unchanged; otherwise the
  LLM-rewritten string used for retrieval). Frontend can surface to user.
- **`wenji eval run-benchmark`** records `rewrite_enabled: bool` in run
  output metadata + `_rewrite_on` / `_rewrite_off` suffix on `run_id` for
  A/B baseline comparison.

### Backward compat (v0.3.2)

- Default behaviour unchanged from v0.3.1: if `WENJI_LLM_API_KEY` is unset,
  no rewriter is instantiated and Searcher runs identically to v0.3.1.
- No BREAKING changes from v0.3.1 → v0.3.2.

### Added (v0.3.1)

- **Multi-path eval schema** — `Candidate` upgraded to `gold_paths: tuple[GoldPath, ...]`
  where each `GoldPath` is one independently-valid answer trajectory
  (`path_tag` / `keywords` / `article_hints` / `expected_direction`). A question
  passes when ANY one of its `gold_paths` achieves `full` keyword match in top-K
  hits (OR semantics). Aligned with logos benchmark v2 schema.
- **`wenji eval migrate-jsonl <old> <new>`** — wrap legacy single-path JSONL
  entries as single-element `gold_paths` (`path_tag="default"`) for backward
  compatibility of user-supplied JSONL files.
- **`wenji eval run-benchmark`** — 80-question v2 baseline runner against a
  running `wenji serve`. Produces `wenji_r0_<date>.json` (logos-v2-compatible
  schema with per-hit `gold_path_match` none/partial/full + question-level
  `pass` + `passing_paths`) plus a `<out>.summary.json` digest.
- **`wenji eval sanity-eyeball`** — dual-gate sanity check for stage-1 baseline
  promotion: objective top-10 hits overlap (`(content_hash, normalized_title)`
  dual-key set, mean ≥ 0.70) + subjective 8-question eyeball review (≤ 1 flagged).
- **`wenji ingest from-logos-db --src --out`** — adapter that dumps a logos
  sqlite database to a markdown corpus directory ready for `wenji ingest dir`.
  Each article → one `.md` with YAML frontmatter (`title`, `pubDate`, `tags`,
  `source_type`, `article_id`, `content_hash`, optional `source_url`).
  Atomic semantics via temp dir staging.
- **`wenji corpus trim --ids --db`** — direct deletion of articles from a
  wenji.db by `article_id` or `content_hash` (auto-detected by SHA-256 hex
  format), atomic across `articles_meta` / `articles_fts` / `chunks_fts` /
  `doc_vectors`. Powers stage-2 corpus trim for `wenji_r1` long-term baseline.
- **`tests/benchmark_80_v2_snapshot.json`** — frozen snapshot of logos
  benchmark v2 80-question gold set (commit `413642af`) with `logos_source_commit`
  + `snapshot_taken_at` provenance metadata.
- **Three-level `gold_path_match`** scoring (`none` / `partial` / `full`) +
  chunk-to-article rollup (`rollup_chunks_to_articles`) using union of retrieved
  chunks (NOT the full DB body) for keyword matching.
- **MRR@5** added to summary aggregation.
- **`src/wenji/eval/report.py`** — markdown baseline report generator
  (6 sections: metadata / summary / sanity / per-question / overlap histogram /
  classical poetry schema migration appendix; r1 reports add a 7th trim manifest
  section).

### Changed (v0.3.1, BREAKING)

- **`wenji.eval.jsonl.Candidate`** — removed `expected_keywords` /
  `expected_article_hints`. JSONL eval files using legacy schema raise
  `IngestError` with a migration hint; run `wenji eval migrate-jsonl`.
- **`wenji.eval.metrics`** — removed `kw1` / `kw3` / `fuzzy` / `pass` predicate
  family; replaced with multi-path `gold_path_match` + per-path `rank_*` /
  `hit1_*` / `hit3_*` / `hit5_*` / `rr_*` metrics.
- **`wenji ingest <dir>`** → `wenji ingest dir <dir>` (subapp form). Legacy
  positional form removed.
- **`wenji eval --candidates ...`** → `wenji eval run --candidates ...` (subapp
  form). Legacy positional form removed.
- **`examples/eval.jsonl`** 10 classical poetry questions migrated to multi-path
  schema (`path_tag="default"`, single-path wrap; demo path preserved).

### Added (v0.3.0)

- **`wenji.ask` module** — query-time RAG question answering on top of an
  existing wenji DB:
  - `Asker(db, llm_client, searcher=None)` with `ask(query, *, k=5,
    axis=None, filter=None) -> Answer`. `llm_client` is required; passing
    `None` raises `TypeError`.
  - `Answer` dataclass `{query, answer, citations, retrieval}`. LLM failure
    → `answer=None` while `retrieval` and `citations` remain populated
    (D7 fallback inherited from `wenji.aggregate`).
  - `Citation` dataclass `{article_id, chunk_index, title, snippet,
    bm25_score}` — chunk-level so frontends can deep-link to
    `/article/<id>#c<n>` (D3).
  - Cached in the existing `aggregate_cache` table under the function name
    `"ask"` (D6); `wenji aggregate clear-cache --db PATH` clears every
    cache including ask.
- **`POST /api/ask` web endpoint** — JSON `{q, k?, axis?, filter?}`,
  returns `asdict(answer)` augmented with `narrative_html`. Malformed
  input / unknown filter → 400. LLM failures stay 200 with `answer=null`.
  503 returned only when the LLM client is not configured at startup.
- **「自由問答」 chat-style answer panel UI** in the search page (collapsed
  `<details>` parallel to the v0.2 「文章彙整」 panel; D8). Renders
  `narrative_html` plus a numbered citations list whose links jump to
  `/article/<id>#c<chunk_index>`. New `static/ask.js` + `style.css`
  block, axis dropdown auto-populated from `/api/axes`.
- **Hierarchical axis support** in `axes.yaml` — optional `parent: <id>`
  field forms a tree (D4). `wenji classify` now propagates ancestors into
  `article_axes` (`is_primary=0` for ancestors, leaf keeps its primary
  flag; D5). The existing `Searcher` axis filter automatically matches
  every descendant via the propagated rows. `GET /api/axes` includes a
  `parent` field per axis; the search-page sidebar indents descendants.
  Backward-compatible — flat `axes.yaml` files behave identically to
  v0.2.
- **Chunk anchor URL fragments** — article viewer wraps each chunk in
  `<section id="c{chunk_index}">` (renamed from `chunk-{N}` for shorter
  fragments; chunk_count=0 articles still fall back to whole-content
  rendering). Search-result title links carry `#c<matched_chunk_index>`
  when a chunk-level match is identifiable.
- **Entity facet sidebar** — `GET /api/facets?top=N` (default 15, capped
  at 50) returns top tags + source_types ordered by count. Search-page
  sidebar gains a collapsed `<details>「熱門 Tag / 類型」` block where
  every entry is a hyperlink that re-issues the search with `?tag=X` or
  `?source_type=Y` appended. The `/` route post-filters search results
  by these parameters, joined with the existing `q` and `axis`.
- **`WENJI_AXES_YAML` env var** — when set, `wenji serve` loads the
  hierarchical axis config so `/api/axes` and the sidebar can render the
  tree. Unset → flat behaviour (every axis treated as a root).

- **`wenji.aggregate` module** — query-time topic and concept aggregation,
  positioned as the differentiation surface vs. NotebookLM / GraphRAG / KAG
  per the LLM-essential-not-LLM-default philosophy:
  - `Aggregator(db, llm_client=None)` with `topic_summary(tag, filter, k)`
    and `concept_perspectives(concept, filter, top_sources, per_source)`.
  - `Filter` dataclass with Django-style lookup suffixes (`__in`,
    `__not_in`, `__gte`, `__lte`) over `tag`, `source_type`, `subtype`,
    `pub_year`, `category`.
  - `LLMClient(base_url, model, api_key, timeout=10.0)` — zero-abstraction
    wrapper around any OpenAI-compatible `chat/completions` endpoint
    (Groq, OpenRouter, Together, Gemini OpenAI-compat, vLLM, …).
  - `LLMClientError` raised on timeout / 4xx / 5xx / response-shape
    mismatch; caught at the Aggregator boundary, falls back to
    `narrative=None` with a logged warning.
  - 30-day query-level cache keyed on
    `sha256(function + canonical_args_json)`; identical query reuses cache
    on subsequent calls.
- **`aggregate_cache` table** added to the schema (CREATE IF NOT EXISTS;
  schema_version unchanged at 2 — backward-compatible with existing v0.2
  databases on `initialise_schema`).
- **Web chat panel** — collapsed-by-default `<details>` element on the
  search page with topic/concept tabs, single-turn submission, exclude-
  subtype filter input. Renders narrative server-side as Markdown HTML.
- **`POST /api/aggregate/topic` and `/api/aggregate/concept`** endpoints
  return `asdict(result)` plus a `narrative_html` field; LLM failures or
  missing client surface as `narrative: null` (200, never 5xx).
- **`wenji aggregate clear-cache --db PATH`** CLI subcommand for cache
  invalidation. Aggregation itself has no CLI entry point — the user
  surface is the Web chat panel and the Python API.
- **`WENJI_LLM_BASE_URL` / `WENJI_LLM_MODEL` / `WENJI_LLM_API_KEY` /
  `WENJI_LLM_TIMEOUT`** environment variables wire an LLM into
  `wenji serve` for the chat panel.

### Changed (BREAKING)

- **Schema bumped to version 2.** v0.1.0 databases must be rebuilt from disk
  (`wenji rebuild --db <path>`); migration is not provided since v0.1.0 had no
  external users.
- `articles_meta` now declares `path TEXT UNIQUE NOT NULL` and uses it as the
  article identity key. Re-ingesting the same path with changed content now
  cleanly removes the prior row and its derived data (FTS, vectors, axes).
- `articles_meta` adds `source_urls_json TEXT NOT NULL DEFAULT ''` for
  multi-source citation (populated when frontmatter provides `source_urls`).

### Fixed

- **L1**: Search result `chunk_hits` no longer counts title-only matches.
  The chunk-level FTS query is now column-restricted to `chunk_text`, so
  `chunk_hits` reports only chunks whose content matches the query.
- **L2**: Snippet plain-text extraction switched from regex stripping to
  Markdown AST walking. URLs containing `_` (e.g. `wikipedia.org/wiki/Foo_bar`)
  and code spans are no longer mangled.
- **L3**: `source_url` frontmatter now accepts `string`, `list[str]` (first
  non-empty entry used), or `dict` with a `url` field; previously a list/dict
  produced a weird `repr()` string.
- **L4**: Title fallback (when frontmatter lacks `title`) now extracts via
  Markdown AST, supporting Setext headings (`Title\n===`) and stripping inline
  formatting (e.g. `# **Bold** Title` → `Bold Title`).
- **L5**: Same path with changed content no longer leaks the previous
  `article_id` row; `articles_meta.path` is now a UNIQUE column and ingest
  performs a path-based DELETE before INSERT.

### UI

- Search result chunk-hits text changed from "+N 段更多" / "命中 N 段" to
  "+N 段內容命中" / "內容命中 N 段" to clarify that the count reports
  content-level matches (not title-only).

## [0.1.0] — 2026-05-XX

Initial public release.

### Added

#### Core engine

- **`wenji.ingest`** — disk-as-SSOT markdown ingest pipeline: frontmatter parsing,
  Traditional-Chinese normalization (NFKC), deterministic article IDs, content
  hashing, and 4 chunking strategies (`paragraph`, `markdown-heading`,
  `bible-verses`, `numbered-entries`) with frontmatter-level `chunk_strategy`
  override. Per-article and per-chunk dual indexing.
- **`wenji.search`** — hybrid retrieval: SQLite FTS5 (BM25) + ONNX BGE-M3 vector
  search + optional cross-encoder rerank + optional LLM query rewrite. Returns
  per-result `chunk_hits` and `matched_chunks` for chunk-level deep linking.
- **`wenji.classify`** — corpus-agnostic multi-axis rule engine driven by
  user-supplied `axes.yaml`; supports `tag-match`, `regex-match`, and
  composable `all-of`/`any-of` rules. Rebuild-friendly (axes are derived,
  never authored).
- **`wenji.eval`** — JSONL-driven eval runner with jitter-aware gate (recommend
  running twice and taking the best) for handling LLM-rewrite stochasticity.

#### CLI (9 subcommands)

- `wenji ingest` — ingest a markdown directory into a wenji DB
- `wenji search` — query a DB or a running `wenji serve` instance
- `wenji classify` — apply `axes.yaml` to existing articles
- `wenji rebuild` — drop derived tables, re-ingest from disk (byte-identical guarantee)
- `wenji eval` — run a JSONL eval set against `wenji serve`
- `wenji serve` — start the FastAPI search/UI server
- `wenji inspect-chunks` — preview how a single markdown file would chunk
- `wenji set-chunk-strategy` — write `chunk_strategy:` into a markdown file's frontmatter
- `wenji download` — fetch ONNX embed model + libsimple binary on first run

#### Web UI (Jinja2 SSR, no SPA)

- Search results page with chunk-level snippets, multi-hit badges, and
  per-result chunk pill list (`+N more`).
- Article viewer with 280px sticky sidebar TOC, scroll-spy, query-aware
  auto-scroll to first matched chunk, and `<mark>` highlighting on query terms.
- Server-side markdown rendering via `markdown-it-py` (with HTML sanitization).
- Per-axis sidebar filter (`?axis_<name>=<value>` query param).

#### Distribution

- Pure-Python wheel + sdist on PyPI
- macOS arm64 + linux x86_64 prebuilt libsimple binaries (other platforms
  fall back to runtime download via `wenji download`)
- ONNX BGE-M3 INT8 model auto-downloaded on first ingest/search
- Configuration via plain YAML + dataclasses (no Pydantic dependency)

#### Examples corpus

- 10 example articles across 5 source types (`sermon`, `article`, `law`,
  `classical`, `tech`) and 4 axes — bundled in the repo for `wenji ingest
  examples/articles/` quickstart.

### Design philosophy

- **LLM-essential, not LLM-default**: indexing pipeline performs zero LLM
  calls. LLM use is restricted to query-time, must be cached, and must have
  a deterministic structured fallback. See [docs](docs/) for the full D0/D10
  design rationale.

### Test coverage

- 253 unit tests + 7 integration tests (260 total) on Python 3.10 / 3.11 / 3.12

[0.1.0]: https://github.com/notoriouslab/wenji/releases/tag/v0.1.0
