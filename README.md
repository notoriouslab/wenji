<div align="center">

# wenji 文集

**丟一個 markdown 資料夾，得到混合搜尋 + Web UI。零 LLM 建索引，語料越大越穩。**

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/notoriouslab/wenji/actions/workflows/ci.yml/badge.svg)](https://github.com/notoriouslab/wenji/actions/workflows/ci.yml)

🇹🇼 [繁體中文](#繁體中文) · 🇬🇧 [English](#english)

</div>

> ⚠️ **Pre-1.0 — API 可能在 minor 版本間變更。**

---

## 繁體中文

### 為什麼選 wenji？

大多數 RAG 框架預設「LLM-default」：建索引用 LLM 抽 entity、LLM 做摘要、LLM 重排序 — 成本隨語料線性成長，LLM 不可用時整個系統停擺。

wenji 走相反路線 — **LLM-essential, not LLM-default**：建索引零 LLM 呼叫、byte-identical 可重建；LLM 只允許在查詢時用（`/api/ask` 問答），必須 cache、必須有確定性 fallback。LLM 成本 = `unique queries × cache miss rate`，與語料大小無關。

| 特色 | 實現方式 |
|------|---------|
| 零 LLM 建索引 | SQLite FTS5 (BM25) + ONNX BGE-M3 INT8，byte-identical 重建 |
| 完全本地 | 一個 Python 行程 + 一個 SQLite 檔，無 vector DB、無外部 API |
| 可量測 | JSONL eval runner + 80 題回歸基準，檢索改動必過 before/after |

適合：講道、課堂筆記、法律條文、古典詩詞、技術文章等中文（或中英混合）語料的本地搜尋與瀏覽。

### 快速上手

```bash
pip install wenji
wenji ingest dir <你的-markdown-目錄>/ --db wenji.db
wenji serve --db wenji.db --port 8000
```

打開 `http://127.0.0.1:8000` — 完整 Web UI：搜尋（分軸 sidebar）、`/tags` 瀏覽、文章閱讀器（TOC + query-aware 捲動）。沒有語料可先 `git clone` 本 repo 用 `examples/articles/` 示範集。

CLI 搜尋：

```bash
wenji search "勞動契約" --db wenji.db
```

首次執行自動下載 embed model（~600 MB）。支援 Python 3.10–3.12；平台支援與大語料運維（續跑、`--skip-bad`）見 [docs/deployment.md](docs/deployment.md)。

### 搜尋架構

`Searcher.search()` 執行 8 步 pipeline：

```
entity detect → intent detect → alias expand
  → BM25 + vector → chunk BM25 → RRF merge (intent boost)
  → entity scoring + filter → snippet hydration
```

entity/intent 層為選配（`--entity-source example:corpus-christian --intent-source example:corpus-christian` 啟用，可多來源組合，詳見 [docs/extending.md](docs/extending.md)）；省略時退化為純 RRF + chunk signals。`search.alpha` 等調參走 `wenji.yaml`（[docs/deployment.md](docs/deployment.md#search-tuning-wenji_config)）。

### 分類引擎

```yaml
# axes.yaml — 摘錄自 examples/axes.yaml
axes:
  - id: sermon
    name: 講道
    rules:
      - source_type: sermon
        primary: true
```

每條 rule 支援 `source_type` / `tag` / `title_regex` / `subtype` 的 AND 組合與階層 `parent`。Axes 是 derived data — `wenji classify` 隨時重建，不動原始 markdown。

### 常用命令

| 命令 | 用途 |
|------|------|
| `wenji ingest dir <path>` | 建索引（中斷後重跑同命令即續跑） |
| `wenji search` / `wenji serve` | CLI 搜尋 / Web UI |
| `wenji classify --config axes.yaml` | 套用分類軸 |
| `wenji doctor` | db 一致性 + 建庫環境健檢（部署前必跑） |
| `wenji eval run-benchmark` | 80 題回歸基準（改檢索前後各跑一次） |
| `wenji stats` / `wenji segment <q>` | corpus 快照 / query pipeline trace |

完整子命令 `wenji --help`；`/api/ask` 問答的 LLM 設定（任何 OpenAI-compatible endpoint）與密鑰安全見 [docs/deployment.md](docs/deployment.md#secrets-hygiene-wenji_llm_)。

### 部署

`wenji serve` 預設無認證、無速率限制 — 對外發布前請照 [docs/deployment.md](docs/deployment.md) 走一遍：API key / CORS / 反代 / SEO meta / `wenji doctor` 驗庫。

### 生態

| 專案 | 說明 |
|------|------|
| [trad-zh-search](https://github.com/notoriouslab/trad-zh-search) | 繁體中文文本預處理 — CKIP 分詞 + bigram 索引，可搭配主流搜尋引擎 |
| [vault-search](https://github.com/notoriouslab/vault-search) | Obsidian 本地語意搜尋 — 中文友善、無雲端、無 API Key |

### 貢獻與授權

```bash
pip install -e ".[dev]"
ruff check src/wenji tests/wenji && ruff format --check src/wenji tests/wenji
pytest                              # unit
pytest -m integration               # 真實 ONNX（需下載 ~600 MB）
```

詳見 [CONTRIBUTING.md](CONTRIBUTING.md)。[MIT](LICENSE) © 2026 notoriouslab

---

## English

### At a glance

| | |
|---|---|
| **What** | Drop a folder of markdown files in, get hybrid search + a web UI out. |
| **Who for** | Anyone with a Chinese (or mixed-language) markdown corpus who wants real search without renting a vector DB. |
| **Stack** | SQLite FTS5 (BM25) + ONNX BGE-M3 (vector) + libsimple (CJK tokenizer) + FastAPI |
| **Indexing** | **Zero LLM calls.** Deterministic, byte-identical rebuild from disk. |
| **LLM use** | Optional, query-time only (`/api/ask`), cached, with a deterministic fallback. |
| **Deploy** | One Python process, one SQLite file. Python 3.10–3.12. |

### Quickstart

```bash
pip install wenji
wenji ingest dir <your-markdown-dir>/ --db wenji.db
wenji serve --db wenji.db --port 8000
```

Open `http://127.0.0.1:8000` for the full web UI (search, tag browsing, article viewer). No corpus handy? Clone this repo and ingest `examples/articles/`.

### Search pipeline

```
entity detect → intent detect → alias expand
  → BM25 + vector → chunk BM25 → RRF merge (intent boost)
  → entity scoring + filter → snippet hydration
```

The entity/intent layer is optional (`--entity-source example:corpus-christian`); without it the pipeline degrades to RRF + chunk signals. Classification axes come from a user-supplied `axes.yaml` (rules on `source_type` / `tag` / `title_regex` / `subtype`) and are derived data — rebuilt any time.

### Going further

- **Deployment** (auth, CORS, SEO meta, secrets hygiene, platforms, ops): [docs/deployment.md](docs/deployment.md)
- **Extending** (entity/intent dictionaries, `wenji.yaml` tuning, `from_sources`): [docs/extending.md](docs/extending.md)
- **Eval**: `wenji eval run-benchmark` — run before/after any retrieval change; pass counts and miss lists must not regress.

### Ecosystem

| Project | Description |
|---------|-------------|
| [trad-zh-search](https://github.com/notoriouslab/trad-zh-search) | Traditional Chinese preprocessing: CKIP segmentation + bigram index generation. |
| [vault-search](https://github.com/notoriouslab/vault-search) | Obsidian local semantic search — Chinese-friendly, no cloud, no API key. |

### Star History

[![Star History Chart](https://api.star-history.com/svg?repos=notoriouslab/wenji&type=Date)](https://star-history.com/#notoriouslab/wenji&Date)

### Development & license

```bash
pip install -e ".[dev]"
ruff check src/wenji tests/wenji && ruff format --check src/wenji tests/wenji
pytest && pytest -m integration
```

See [CONTRIBUTING.md](CONTRIBUTING.md). [MIT](LICENSE) © 2026 notoriouslab
