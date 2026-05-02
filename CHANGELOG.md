# Changelog

All notable changes to **wenji** will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
