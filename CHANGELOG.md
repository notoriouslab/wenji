# Changelog

All notable changes to **wenji** will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
