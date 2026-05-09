<div align="center">

# wenji 文集

**丟一個 markdown 資料夾，得到混合搜尋 + Web UI。零 LLM 建索引，語料越大越穩。**

[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/notoriouslab/wenji/actions/workflows/ci.yml/badge.svg)](https://github.com/notoriouslab/wenji/actions/workflows/ci.yml)

🇹🇼 [繁體中文](#繁體中文) · 🇬🇧 [English](#english)

</div>

> ⚠️ **Pre-1.0 — API 可能在 minor 版本間變更。** 尚未上 PyPI；請從 source 安裝。

---

## 繁體中文

### 為什麼選 wenji？

大多數 RAG 框架預設「LLM-default」：建索引用 LLM 抽 entity、LLM 做 community summary、LLM 重排序。成本隨語料線性成長，LLM 不可用時整個系統停擺。

wenji 走相反路線 — **LLM-essential, not LLM-default**：建索引零 LLM 呼叫；LLM 只允許在查詢時用，必須 cache，必須有確定性 fallback。結果：LLM 成本 = `unique queries × cache miss rate`，與語料大小無關。

**典型使用場景**

- 📚 **中文知識語料搜尋** — 講道、課堂筆記、法律條文、古典詩詞、技術文章，中英混合支援
- 🖥️ **本地部署，零外部服務** — 一個 Python 行程 + 一個 SQLite 檔，不租 vector DB
- 🏷️ **Tag + 分類軸瀏覽** — Web UI sidebar 篩選，`/tags` tag 索引頁，`?tag=X` 精確過濾
- 🤖 **選配 LLM query rewrite** — 短查詢擴詞，任何 OpenAI-compatible endpoint，結果 SQLite cache
- 📊 **Eval 對齊** — JSONL eval runner，80q 基準，pass@3 partial+ 77.5%（v0.3.6 rewrite-off）

**3 大特色**

| 特色 | 實現方式 |
|------|---------|
| 零 LLM 建索引 | SQLite FTS5 (BM25) + ONNX BGE-M3 INT8，byte-identical 重建 |
| 完全本地 | 無 vector DB，無外部 API，一個 SQLite 檔 |
| 可量測 | JSONL eval runner，80q 基準，pass@3 partial+ 77.5%（v0.3.6 rewrite-off） |

---

### 三行快速上手

> ⚠️ PyPI 尚未發行，先從 source 安裝：

```bash
git clone https://github.com/notoriouslab/wenji && cd wenji
pip install -e .
wenji ingest dir examples/articles/ --db wenji.db
wenji serve --db wenji.db --port 8000
```

打開瀏覽器 `http://127.0.0.1:8000`，得到完整 Web UI（搜尋 + tag 瀏覽 + 文章閱讀器）。

支援 Python 3.10–3.12（3.13 尚未支援）。第一次跑 `wenji ingest` 或 `wenji search` 會自動下載 ONNX BGE-M3 INT8 embed model（~600 MB）至 user cache。macOS arm64 / Linux x86_64 內建 `libsimple` binary；其他平台用 `wenji download-model` 手動抓。

---

### 常見使用路徑

#### 場景 1：CLI 快速搜尋

```bash
wenji search "勞動契約" --db wenji.db --top-k 5
```

輸出前 5 筆，含 BM25 / 向量分數、chunk-level 摘要、chunk 索引（可 deep link 至 `/article/<id>#c<n>`）。

#### 場景 2：Web UI + tag 瀏覽

```bash
wenji serve --db wenji.db --port 8000
```

| 路由 | 功能 |
|------|------|
| `/` | 搜尋頁，含分軸 sidebar filter |
| `/tags` | tag 索引頁（tag → 文章數） |
| `/tag/<name>` | 單一 tag 的文章列表 |
| `/article/<id>` | 文章閱讀器（sticky TOC、scroll-spy、query-aware 自動捲動） |

> ⚠️ **Production 部署 checklist**（`wenji serve` 預設沒有認證、沒有速率限制）：
> - 設 `WENJI_API_KEY=<random-32-bytes>` 開啟 API key auth；同時關掉 `/docs` `/openapi.json` 自動文件（沒設 API key 時這兩個端點公開）
> - 設 `WENJI_CORS_ORIGINS=https://your-frontend.example.com`（預設 empty 拒所有 cross-origin）
> - 綁 `127.0.0.1` 走反代（nginx / Caddy）+ 反代層做 rate limit（`/api/ask` 一次呼叫等於一次 LLM 計費）
> - `axes.yaml` 為選配；缺檔不影響 ingest/search，只是 sidebar 不會有分軸

#### 場景 3：選配 LLM query rewrite

```bash
export WENJI_LLM_BASE_URL=https://api.groq.com/openai/v1
export WENJI_LLM_API_KEY=<your-key>
export WENJI_LLM_MODEL=llama-3.3-70b-versatile
wenji serve --db wenji.db
```

rewrite 格式：1-3 組關鍵詞以 `|` 分隔（BM25-friendly）；結果 SQLite cache（預設 30 天 TTL）。

> **注意**：v0.3.6 rewrite-on 比 rewrite-off 低 3.7pp（73.8% vs 77.5%），wenji 預設 rewrite-off。建議先跑 rewrite-off 基準再決定是否開啟。

#### 場景 4：Domain corpus（corpus-christian 範例）

```bash
wenji serve --db wenji.db \
  --entity-source example:corpus-christian \
  --intent-source example:corpus-christian
```

啟動 entity scoring + intent classification 層，Searcher pipeline 升級為：RRF merge with intent boost → entity scoring/filter。省略 flags 時退化為純 RRF + chunk signals（仍優於 v0.3.5 線性 hybrid）。

#### 場景 5：Eval A/B 基準測試

> 前置：先在另一 terminal 跑 `wenji serve --db wenji.db`（eval runner 透過 `/api/search` 打 80q 基準）；snapshot `tests/benchmark_80_v2_snapshot.json` 已內建 repo 內。

```bash
wenji eval run-benchmark --no-rewrite     --db wenji.db --out r0_off.json
wenji eval run-benchmark --enable-rewrite --db wenji.db --out r0_on.json
```

輸出標準 JSON 格式（per-question `gold_path_match`、`pass@3`、`MRR@5`）。`run_id` 自動補 `_rewrite_on` / `_rewrite_off` 後綴。用 `wenji eval sanity-eyeball --baseline-output <path>` 做人工雙閘門驗收。

> 數值有 LLM 抖動：rewrite-on 數值 ±1.5pp 視為 jitter 範圍內（同一基準重跑可能差 ±1.5pp）；超過 1.5pp 才算 retrieval regression。

---

### 核心概念

#### 搜尋架構

`Searcher.search()` v0.3.6 執行 11 步 pipeline：

```
query rewrite → entity detect → intent detect → alias expand
  → BM25 + vector → chunk BM25 → RRF merge (intent boost)
  → entity scoring + filter → ranker hooks → cross-encoder rerank → snippet hydration
```

省略 `--entity-source` / `--intent-source` 時，entity/intent 步驟 skip，降級為 RRF + chunk signals。

#### 核心模組

| 模組 | 作用 |
|------|------|
| `wenji.ingest` | Disk-as-SSOT 切入：frontmatter、NFKC 正規化、deterministic ID、content hash、4 種切塊策略 |
| `wenji.search` | 混合檢索 11 步 pipeline（BM25 + vector + RRF + entity/intent + rerank） |
| `wenji.classify` | 跟語料無關的多軸 rule engine，user-supplied `axes.yaml` |
| `wenji.eval` | JSONL eval runner，multi-path gold set，jitter-aware gate |
| `wenji.ask` | RAG 問答（`POST /api/ask`），chunk-level citation，30 天 LLM cache |
| `wenji.observability` | corpus 快照 + query pipeline trace |

#### 分類引擎

```yaml
# axes.yaml — 摘錄自 examples/axes.yaml
axes:
  - id: sermon
    name: 講道
    short: 講道
    order: 1
    description: 講道 / 信仰主題長文
    rules:
      - source_type: sermon
        primary: true
```

每條 rule 支援 `source_type` / `tag` / `title_regex`（regex search）/ `subtype` 多欄位 AND 組合，及 hierarchical `parent: <id>`。Axes 是 derived data，隨時 `wenji classify` / `wenji rebuild` 重建，不動原始 markdown。

#### Observability

```bash
wenji stats   --db wenji.db            # articles / chunks / indices 快照
wenji segment "因信稱義" --db wenji.db  # query pipeline trace（tokens、fts_form、rewrite）
```

等效 HTTP 端點：`GET /api/stats`、`GET /api/segment?q=`。

---

### 進階設定

#### LLM Query Rewrite

任何 OpenAI-compatible endpoint（Groq、OpenRouter、Together、Gemini、vLLM、llama.cpp …）。**強烈建議**用 `.env` + `direnv` 載入，避免 API key 寫進 shell rc 或被 process listing 看到：

```bash
# .env （請複製 .env.example 為 .env 後填入；不要 commit）
WENJI_LLM_BASE_URL=https://api.groq.com/openai/v1
WENJI_LLM_API_KEY=<your-key>
WENJI_LLM_MODEL=llama-3.3-70b-versatile
WENJI_LLM_TIMEOUT=10.0                # 選配，預設 10s
WENJI_LLM_REWRITE_CACHE_TTL_DAYS=30   # 選配，預設 30 天
```

> ⚠️ 確認 `.gitignore` 含 `.env` 與 `.env.*`（已內建）。**不要** `export WENJI_LLM_API_KEY=...` 寫進 `~/.zshrc`／`~/.bashrc`，也不要傳 `-e WENJI_LLM_API_KEY=...` 給 docker（會被 `ps` 看到）。

單次覆蓋：`wenji serve --enable-rewrite` / `--no-rewrite`（兩者互斥）。

**LLM 失敗 fallback**：rewrite endpoint 超時 / 5xx → rewriter skip，retrieval pipeline 不受影響（仍走原 query）；`/api/ask` 在 LLM 失敗時 `answer=null` 但 `citations` 仍正常填值。

#### Entity / Intent Sources

多來源，last-write-wins on key collision：

```bash
wenji serve --db wenji.db \
  --entity-source example:corpus-christian \
  --entity-source /path/to/my_entities.json \
  --intent-source example:corpus-christian
```

或 env var（comma-separated）：

```bash
export WENJI_ENTITY_SOURCES=example:corpus-christian,/path/to/my_entities.json
export WENJI_INTENT_SOURCES=example:corpus-christian
```

Network URLs（`http://`、`https://`）被 source loader 拒絕；只接受 `example:<name>` 和本機路徑。**注意**：本機路徑目前沒有沙箱（Path traversal 防護待 v0.4），請只指向你信任的目錄。多來源 last-write-wins：右邊覆蓋左邊（`--entity-source A --entity-source B` → B 的 keys 優先）。

#### Web 部署：站點 URL / SEO / CORS（v0.3.7+）

對外發行時，由 env vars 控制 SEO meta 與 CORS（**全部 unset 時預設不暴露任何品牌 / 不允許任何 cross-origin**，最安全 zero-config）：

```bash
# .env
WENJI_SITE_URL=https://wenji.example.com           # 啟用 canonical / og:* / JSON-LD
WENJI_SITE_NAME=My Wenji                          # 可選，最長 256 字
WENJI_OG_IMAGE_URL=https://wenji.example.com/og.png  # 可選；⚠️ 此 host 會收到所有訪客 IP / UA
WENJI_CORS_ORIGINS=https://my-frontend.example.com,https://api.example.com
```

URL host 啟動時做白名單驗證：拒 userinfo（`https://a@b.com`）、私網 IP、IDN homograph、控制字元、非預設 port、percent-encoded host —— fail-fast 啟動失敗。CORS 拒 `*` / `null` / wildcard subdomain / 非 https。

> Local dev SPA：跑 `localhost:5173` 連 `/api/*` 會被預設 CORS 擋下；開發時設 `WENJI_CORS_ORIGINS=http://localhost:5173 WENJI_ALLOW_HTTP_CORS=1`。

#### 升級指南

wenji 用 markdown 為 SSOT，舊 db 升級沒有 migration script —— 直接重建：

```bash
rm wenji.db && wenji ingest dir <markdown-dir> --db wenji.db
```

支援平台：

| 平台 | 狀態 | 備註 |
|------|------|------|
| macOS arm64（M1+） | ✅ supported | 內建 libsimple binary |
| Linux x86_64 | ✅ supported | 內建 libsimple binary |
| macOS x86_64（Intel） | ⚠️ experimental | 需自行編譯 libsimple |
| Linux ARM | ⚠️ experimental | 需自行編譯 libsimple |
| Windows | ❌ unsupported | libsimple 無 .dll |

中國大陸 / 受限網路：Hugging Face 模型下載可設 `HF_ENDPOINT=https://hf-mirror.com`。

---

### 進階參考

#### CLI 子命令

| 命令 | 用途 |
|------|------|
| `wenji ingest dir <path>` | 從 markdown 目錄建索引 |
| `wenji search <query>` | CLI 搜尋 |
| `wenji serve` | 啟動 FastAPI + Web UI |
| `wenji classify` | 套用 axes.yaml |
| `wenji rebuild` | 重建 derived tables（byte-identical） |
| `wenji stats` | corpus 快照 |
| `wenji segment <query>` | query pipeline trace |
| `wenji eval run-benchmark` | 跑 80q 基準 |
| `wenji eval sanity-eyeball` | 人工雙閘門驗收 |
| `wenji eval migrate-jsonl` | 舊版 eval JSONL 轉換 |
| `wenji inspect-chunks <file>` | 預覽單檔切塊結果 |
| `wenji set-chunk-strategy` | 寫 frontmatter `chunk_strategy` |
| `wenji corpus trim` | 按 article_id / content_hash 刪除 |
| `wenji download-model` | 手動下載 ONNX model + libsimple |
| `wenji aggregate clear-cache` | 清除 LLM cache |

#### 選型建議

**何時開 rewrite？**

| 情境 | 建議 |
|------|------|
| 短查詢（1-2 字）召回率差 | 嘗試 rewrite-on，先跑 A/B 確認效果 |
| 向量召回已夠強 | 預設 off（rewrite 可能注入噪音） |
| Baseline 重現 | `--no-rewrite` 鎖定 |

**何時用 entity/intent？**

| 情境 | 建議 |
|------|------|
| 純 RRF 效果已夠 | 省略 `--entity-source`，不需準備詞典 |
| 專業語料（神學、法律、醫學） | `--entity-source` + `--intent-source` 提升精確度 |
| 自訂 domain | Python API: `EntityScorer.from_sources()` / `IntentClassifier.from_sources()` |

---

### 整合與生態

**notoriouslab 相關專案**

| 專案 | 說明 |
|------|------|
| [trad-zh-search](https://github.com/notoriouslab/trad-zh-search) | 繁體中文文本預處理工具 — CKIP 分詞 + bigram 索引生成，附可選擇的領域字典系統；可單獨搭配主流搜尋引擎使用 |
| [vault-search](https://github.com/notoriouslab/vault-search) | Obsidian 本地語意搜尋與發掘 — 中文友善，無雲端、無 API Key、無訂閱費 |

**擴展點**：`RankerHook` Protocol — `boost(article, query, context) -> float`，duck typing 滿足即可。詳見 [docs/extending.md](docs/extending.md)。

---

### 貢獻

```bash
pip install -e ".[dev]"
ruff check src/wenji tests/wenji   # linter
pytest                              # unit（634 tests）
pytest -m integration              # 真實 ONNX（需下載 ~600 MB）
```

詳見 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 授權

[MIT](LICENSE) © 2026 notoriouslab

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
| **Tested on** | Python 3.10 / 3.11 / 3.12 — 641 tests (634 unit + 7 integration). |

### Why wenji?

Most RAG frameworks are built around an "LLM-default" assumption: extract entities with an LLM during ingest, build community summaries with an LLM, re-rank with an LLM. The cost grows with the corpus, and the system stops working when the LLM is unavailable.

wenji is built on the opposite premise — **LLM-essential, not LLM-default**:

1. The indexing pipeline performs **zero** LLM calls.
2. LLM use is restricted to query time, must be cached, and must have a deterministic structured fallback.
3. Entity dictionaries, classification axes, and chunking strategies are user-supplied, not LLM-derived.

The result: LLM cost scales with `unique queries × cache miss rate`, not with corpus size.

### Quickstart (4 commands)

> ⚠️ Not yet on PyPI — install from source. The `examples/` corpus ships only with the source checkout.

```bash
git clone https://github.com/notoriouslab/wenji && cd wenji
pip install -e .
wenji ingest dir examples/articles/ --db wenji.db
wenji serve --db wenji.db --port 8000
```

Then open `http://127.0.0.1:8000` — full Web UI with search, tag browsing, and article viewer.

To search from the command line:

```bash
wenji search "勞動契約" --db wenji.db --top-k 5
```

### Common paths

| Scenario | Command |
|----------|---------|
| CLI search | `wenji search "<query>" --db wenji.db` |
| Web UI + tag browsing | `wenji serve --db wenji.db` → `/tags`, `/tag/<name>` |
| LLM query rewrite | Set `WENJI_LLM_*` env vars (see below) |
| Domain corpus (entity/intent) | `wenji serve --entity-source example:corpus-christian --intent-source example:corpus-christian` |
| Eval A/B | `wenji eval run-benchmark --no-rewrite` vs `--enable-rewrite` |

### Search pipeline (v0.3.6)

`Searcher.search()` runs an 11-step pipeline:

```
query rewrite → entity detect → intent detect → alias expand
  → BM25 + vector → chunk BM25 → RRF merge (intent boost)
  → entity scoring + filter → ranker hooks → cross-encoder rerank → snippet hydration
```

Without `--entity-source` / `--intent-source`, the entity/intent steps are skipped and the pipeline degrades to RRF + chunk signals (still an improvement over v0.3.5 linear hybrid).

### Core modules

| Module | Purpose |
|--------|---------|
| `wenji.ingest` | Disk-as-SSOT markdown ingest: frontmatter, NFKC normalization, deterministic IDs, content hashing, 4 chunking strategies. |
| `wenji.search` | Hybrid retrieval: 11-step pipeline (BM25 + vector + RRF + entity/intent + rerank). |
| `wenji.classify` | Corpus-agnostic multi-axis rule engine. Drop your `axes.yaml`, get tagged articles. |
| `wenji.eval` | JSONL-driven eval runner with jitter-aware gates. |
| `wenji.ask` | RAG question answering (`POST /api/ask`), chunk-level citations, 30-day LLM cache. |
| `wenji.observability` | Corpus snapshot + query pipeline trace (`/api/stats`, `/api/segment`). |

### LLM query rewrite (optional, v0.3.2+)

Any OpenAI-compatible endpoint (Groq, OpenRouter, Together, Gemini, vLLM, llama.cpp…):

```bash
export WENJI_LLM_BASE_URL=https://api.groq.com/openai/v1
export WENJI_LLM_API_KEY=<your-key>
export WENJI_LLM_MODEL=llama-3.3-70b-versatile
```

Per-invocation override: `wenji serve --enable-rewrite` / `--no-rewrite`. The `/api/search` response includes a `rewritten_query` field (`null` when unchanged).

> **Note**: v0.3.6 rewrite-on (73.8%) is 3.7pp below rewrite-off (77.5%) because wenji's vector recall is already strong. Default is rewrite-off. Run A/B with `wenji eval run-benchmark` before enabling in production.

### Configuration

```yaml
# axes.yaml — excerpt from examples/axes.yaml
axes:
  - id: sermon
    name: 講道
    short: 講道
    order: 1
    description: 講道 / 信仰主題長文
    rules:
      - source_type: sermon
        primary: true
```

Each rule supports `source_type` / `tag` / `title_regex` (regex search) / `subtype` fields combined with AND, plus hierarchical `parent: <id>`. Axes are derived data — `wenji rebuild` always regenerates them deterministically.

### Ecosystem

| Project | Description |
|---------|-------------|
| [trad-zh-search](https://github.com/notoriouslab/trad-zh-search) | Traditional Chinese text preprocessing: CKIP segmentation + bigram index generation, with optional domain dictionaries. Works standalone with any major search engine. |
| [vault-search](https://github.com/notoriouslab/vault-search) | Obsidian local semantic search and discovery — Chinese-friendly, no cloud, no API key, no subscription. Your notes never leave your machine. |

Custom ranking: implement the `RankerHook` Protocol (`boost(article, query, context) -> float`) and pass it to `Searcher`. Duck typing — no import required. See [docs/extending.md](docs/extending.md).

### Star History

[![Star History Chart](https://api.star-history.com/svg?repos=notoriouslab/wenji&type=Date)](https://star-history.com/#notoriouslab/wenji&Date)

### Development

```bash
pip install -e ".[dev]"
ruff check src/wenji tests/wenji
pytest                    # unit
pytest -m integration     # ~600 MB model download, real ONNX
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full PR flow.

### License

[MIT](LICENSE) © 2026 notoriouslab
