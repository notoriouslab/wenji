# wenji 文集

> A Chinese-first markdown RAG engine: hybrid BM25 + vector + rerank,
> multi-axis classification, eval-aware. Drop `.md`, get search.

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/notoriouslab/wenji/actions/workflows/ci.yml/badge.svg)](https://github.com/notoriouslab/wenji/actions/workflows/ci.yml)

> ⚠️ **Pre-1.0 — API may change between minor versions.** Production-tested at logos.notoriouslab.com but not yet on PyPI.

🇬🇧 [English](#english) · 🇹🇼 [繁體中文](#繁體中文)

---

## English

### At a glance

| | |
|---|---|
| **What** | Drop a folder of markdown files in, get hybrid search + a web UI out. |
| **Who for** | Anyone with a Chinese (or mixed-language) markdown corpus — sermons, lecture notes, legal text, classical poetry, blog posts — who wants real search without renting a vector DB. |
| **Stack** | SQLite FTS5 (BM25) + ONNX BGE-M3 (vector) + libsimple (CJK tokenizer) + FastAPI + Jinja2 |
| **Indexing cost** | **Zero LLM calls.** Deterministic, byte-identical rebuild from disk. |
| **LLM use** | Optional, query-time only, cached, with a structured fallback that works without any LLM. |
| **Deploy size** | One Python process, one SQLite file. No external services. |
| **Tested on** | Python 3.10 / 3.11 / 3.12 — 260 tests (253 unit + 7 integration). |

### Why wenji?

Most RAG frameworks are built around an "LLM-default" assumption: extract
entities with an LLM during ingest, build community summaries with an LLM,
re-rank with an LLM. The cost grows with the corpus, and the system stops
working when the LLM is unavailable.

wenji is built on the opposite premise — **LLM-essential, not LLM-default**:

1. The indexing pipeline performs **zero** LLM calls.
2. LLM use is restricted to query time, must be cached, and must have a
   deterministic structured fallback.
3. Entity dictionaries, classification axes, and chunking strategies are
   user-supplied, not LLM-derived.

The result: LLM cost scales with `unique queries × cache miss rate`, not with
corpus size. Indexing 1,000 articles or 1,000,000 articles is the same
deterministic pipeline.

### Core modules

| Module | Purpose |
|---|---|
| `wenji.ingest` | Disk-as-SSOT markdown ingest: frontmatter, NFKC normalization, deterministic IDs, content hashing, 4 chunking strategies. |
| `wenji.search` | Hybrid retrieval: SQLite FTS5 (BM25) + ONNX BGE-M3 vector + optional cross-encoder rerank + optional LLM query rewrite. |
| `wenji.classify` | Corpus-agnostic multi-axis rule engine. Drop your `axes.yaml`, get tagged articles. |
| `wenji.eval` | JSONL-driven eval runner with jitter-aware gates for query-rewrite stochasticity. |

Plus a 9-subcommand CLI (`ingest`, `search`, `classify`, `rebuild`, `eval`,
`serve`, `inspect-chunks`, `set-chunk-strategy`, `download`) and a Jinja2
SSR web UI with chunk-level deep linking and per-axis sidebar filters.

### Installation

```bash
pip install wenji
```

Python 3.10–3.12 supported. The first `wenji ingest` or `wenji search` run
will auto-download the ONNX BGE-M3 INT8 embedding model (~600 MB) into your
user cache. macOS arm64 and Linux x86_64 ship with prebuilt `libsimple`
binaries; other platforms fall back to runtime download via
`wenji download`.

### Quickstart (3 commands)

```bash
pip install wenji
wenji ingest examples/articles/ --db wenji.db
wenji serve --db wenji.db --port 8000
```

Then open `http://127.0.0.1:8000` in your browser.

To search from the command line instead of the web UI:

```bash
wenji search "勞動契約" --db wenji.db --top-k 5
```

### Quickstart with the `corpus-christian` example (v0.3.6+)

For Chinese Christian-knowledge corpora, wenji ships a domain-specific
example with neutral theological vocabulary and apologetics keywords:

```bash
wenji serve --db wenji.db --port 8000 \
  --entity-source example:corpus-christian \
  --intent-source example:corpus-christian
```

This wires `EntityScorer` and `IntentClassifier` into the search
pipeline (`Searcher.search` → RRF merge with intent boost → entity
scoring + filter). For other domains, omit the flags — the pipeline
degrades to RRF + chunk signals without entity/intent layers. See
`docs/extending.md` for composing multiple sources and writing custom
`RankerHook` implementations.

### Configuration

wenji uses plain YAML config (no Pydantic), loaded from `wenji.yaml` in the
working directory or via `--config <path>`. Define your classification axes
in `axes.yaml`:

```yaml
axes:
  - name: source_type
    rules:
      - match: { tag: sermon }
        value: sermon
      - match: { tag: tutorial }
        value: tutorial
```

See `examples/` for full configuration samples.

### LLM query rewrite (optional, v0.3.2+)

`QueryRewriter` rewrites user queries before retrieval (e.g. `因信稱義` →
`因信稱義 救恩論 神學`), improving recall on short queries. Rewrites are
cached in SQLite (`query_rewrite_cache` table) with a configurable TTL, so
identical queries don't re-hit the LLM.

Enable by setting these environment variables (any OpenAI-compatible endpoint
works — Groq, OpenRouter, Together, Gemini OpenAI-compat, vLLM, llama.cpp, …):

```bash
export WENJI_LLM_BASE_URL=https://api.groq.com/openai/v1
export WENJI_LLM_API_KEY=<your-key>
export WENJI_LLM_MODEL=llama-3.3-70b-versatile
# optional
export WENJI_LLM_TIMEOUT=10.0                # seconds; rewriter uses 1.5s internally
export WENJI_LLM_REWRITE_CACHE_TTL_DAYS=30
```

When all three required vars are set, `wenji serve` / `wenji search` /
`/api/search` automatically wire a rewriter into the Searcher. To override
the default for a single invocation:

```bash
wenji serve --enable-rewrite       # force on (errors if env incomplete)
wenji serve --no-rewrite           # force off (e.g. for baseline reproducibility)
```

The `/api/search` JSON response includes a `rewritten_query` field when the
LLM changed the query (else `null`), so frontends can surface the rewrite to
the user. For A/B baseline runs:

```bash
wenji eval run-benchmark --enable-rewrite --db ... --out r0_on.json
wenji eval run-benchmark --no-rewrite     --db ... --out r0_off.json
```

The `run_id` field is suffixed `_rewrite_on` / `_rewrite_off` and the run
metadata records `rewrite_enabled: bool`.

If `WENJI_LLM_API_KEY` is unset, no rewriter is instantiated and the Searcher
runs identically to v0.3.1 — there is no behaviour change for users who
don't opt in.

### Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, testing, and the PR
flow. The TL;DR:

```bash
pip install -e ".[dev]"
ruff check src/wenji tests/wenji
pytest                    # unit
pytest -m integration     # ~600 MB model download, real ONNX
```

### License

[MIT](LICENSE) © 2026 notoriouslab

---

## 繁體中文

### 一眼看懂

| | |
|---|---|
| **是什麼** | 丟一個 markdown 資料夾進去，得到混合搜尋 + Web UI。 |
| **給誰用** | 手上有中文（或中英混合）markdown 語料的人 — 講道、課堂筆記、法條、古典詩詞、部落格 — 想要真正能用的搜尋但不想租 vector DB。 |
| **技術棧** | SQLite FTS5（BM25）+ ONNX BGE-M3（向量）+ libsimple（CJK 切詞）+ FastAPI + Jinja2 |
| **建索引成本** | **零 LLM 呼叫。** 從硬碟重建保證 byte-identical。 |
| **LLM 使用** | 可選，只在查詢時、必有 cache、必有不依賴 LLM 的結構化退回路徑。 |
| **部署規模** | 一個 Python 行程 + 一個 SQLite 檔。沒有外部服務。 |
| **測試覆蓋** | Python 3.10 / 3.11 / 3.12 — 260 個測試（253 unit + 7 integration）。 |

### 為什麼選 wenji？

大多數 RAG 框架預設「LLM-default」：建索引時用 LLM 抽 entity、用 LLM 做
community summary、用 LLM 重排序。成本隨語料 size 線性成長，LLM 不可用時整
個系統停擺。

wenji 走相反路線 —— **LLM-essential, not LLM-default**：

1. 索引 pipeline **零** LLM 呼叫。
2. LLM 只允許在查詢時用，必須 cache，必須有不依賴 LLM 的結構化 fallback。
3. Entity 詞典、分類軸（axes）、切塊策略都是 user-supplied，不是 LLM 推
   出來的。

結果：LLM 成本 = `unique queries × cache miss rate`，跟語料大小無關。索
引 1,000 篇還是 1,000,000 篇，都是同一個 deterministic pipeline。

### 核心模組

| 模組 | 作用 |
|---|---|
| `wenji.ingest` | Disk-as-SSOT 切入：frontmatter、NFKC 正規化、deterministic ID、content hash、4 種切塊策略。 |
| `wenji.search` | 混合檢索：SQLite FTS5（BM25）+ ONNX BGE-M3 向量 + 可選 cross-encoder rerank + 可選 LLM query rewrite。 |
| `wenji.classify` | 跟語料無關的多軸 rule engine。丟你的 `axes.yaml` 進來，拿到分好類的文章。 |
| `wenji.eval` | JSONL 驅動的 eval runner，內建處理 query-rewrite 抖動的 gate。 |

外加 9 個 CLI 子命令（`ingest`、`search`、`classify`、`rebuild`、`eval`、
`serve`、`inspect-chunks`、`set-chunk-strategy`、`download`）跟一個 Jinja2
SSR Web UI（含 chunk-level deep link 跟分軸 sidebar 篩選）。

### 安裝

```bash
pip install wenji
```

支援 Python 3.10–3.12。第一次跑 `wenji ingest` 或 `wenji search` 會自動把
ONNX BGE-M3 INT8 embed model（~600 MB）下載到 user cache。macOS arm64 跟
Linux x86_64 內建 `libsimple` binary，其他平台用 `wenji download` 在
runtime 抓。

### 三行快速上手

```bash
pip install wenji
wenji ingest examples/articles/ --db wenji.db
wenji serve --db wenji.db --port 8000
```

打開瀏覽器 `http://127.0.0.1:8000` 就有完整 Web UI。

如果只想從 CLI 搜：

```bash
wenji search "勞動契約" --db wenji.db --top-k 5
```

### 設定

wenji 用單純 YAML（不用 Pydantic），從工作目錄的 `wenji.yaml` 讀，或用
`--config <path>` 指定。分類軸定義在 `axes.yaml`：

```yaml
axes:
  - name: source_type
    rules:
      - match: { tag: sermon }
        value: sermon
      - match: { tag: tutorial }
        value: tutorial
```

完整範例看 `examples/`。

### 開發

本地端開發、測試、PR 流程詳見 [CONTRIBUTING.md](CONTRIBUTING.md)。簡版：

```bash
pip install -e ".[dev]"
ruff check src/wenji tests/wenji
pytest                    # unit
pytest -m integration     # 會下載 ~600 MB 模型、跑真實 ONNX
```

### 授權

[MIT](LICENSE) © 2026 notoriouslab
