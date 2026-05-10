# Proposal: Doctor CLI + startup consistency check

## Why

Mode 3 拍板（2026-05-10）後 wenji = OSS SSOT，prod logos sandbox 暴露的 silent retrieval failure 範式（chunks_fts 0 rows + `wenji_meta.n_chunks=0` 的「假一致」）是結構性風險：counter 與 row count 同時為 0 + table 真的 empty 時，應用層 retrieval 走 fallback path silent fail（HTTP 200 但 `results=[]`），無告警。

`fail-loud-runtime` spec（2026-05-10 ship，PR #4 `b69ead3`）只能擋 SQLite-level `OperationalError`，**無法**擋這種「正常 query 但 0 rows」的 silent failure—因為 `chunks_fts MATCH "神"` 對 empty table 不會 raise，正常回 0 rows。本 spec 補兩條防線：

1. **`wenji doctor` CLI**：可主動診斷的 health check（row count vs counter + sample MATCH 驗 FTS 真能搜出東西），讓 OSS user 在 deploy 前自己驗 db 狀態
2. **Startup consistency check**：retrieval 入口（`serve` / `eval` / `search`）啟動時自動跑同一個檢查，不一致拒絕啟動（hard fail，exit non-zero / FastAPI 不 bind port）

兩件事 share 同一個核心 function（DRY），doctor 是 CLI wrapper、startup 是 lifecycle gate。

## What Changes

### A — New module: `wenji.observability.health`

放在 `src/wenji/observability/health.py`，與既有 `observability/stats.py` 並排（conceptual home：health monitoring）。

- `ConsistencyReport` dataclass：
  - `schema_version: int`
  - `counters: dict[str, int]`（`n_articles` / `n_chunks` / `n_doc_vectors`）
  - `row_counts: dict[str, int]`（`articles_meta` / `chunks_fts` / `doc_vectors`）
  - `sample_match_hits: dict[str, dict[str, int]]`（keyword → {`articles_fts`: hit_count, `chunks_fts`: hit_count}）
  - `issues: list[str]`（human-readable issues; empty = OK）
  - `@property ok -> bool`（`not issues`）
  - `def format() -> str`（multi-line summary for stdout / log）
- `check_consistency(conn, sample_keywords) -> ConsistencyReport`：純函式，read-only
- `DEFAULT_SAMPLE_KEYWORDS = ("神", "人", "心", "天", "之")`
- `class StartupError(WenjiError)` in `wenji.core.errors`（新 exception）

### B — New CLI: `wenji doctor`

`src/wenji/cli/doctor.py` thin wrapper：

- `--db PATH`（沿襲 `stats` / `inspect-chunks` 慣例）
- `--sample-keywords k1,k2,k3`（CSV，覆寫 default；給純非中文 corpus user）
- 印 `report.format()`；OK exit 0、FAIL exit 1

Register: `cli/__init__.py` 加 `app.command(name="doctor", help="...")(...)`

### C — Startup gate integration

- **`wenji serve`** (`web/app.py`)：加 `lifespan=lifespan` async context manager，跑 `check_consistency`，FAIL → raise `StartupError` → server 不 bind port
- **`wenji eval`** 只 gate **有 db parameter 且涉及 retrieval** 的 subcommand：
  - ✅ `run` (`db: Path | None`)：當 `db is not None` 時 gate；`db is None` 時 skip（用 cache）
  - ✅ `run-benchmark` (`db: Path` required)：always gate
  - ❌ `sanity-eyeball`：純 JSON 比對，無 db parameter，skip
  - ❌ `migrate-jsonl`：純 JSONL 格式轉換，無 db parameter，skip
- **`wenji search`** (`cli/search.py`) thin-client fallback path（in-process Searcher）：db open 後 call `_ensure_consistency`
- **不**整合：`ingest` / `rebuild` / `inspect-chunks` / `set-chunk-strategy` / `stats` / `corpus` / `download-model` / `classify` / `aggregate` / `segment` （要嘛是修狀態 chicken-and-egg、要嘛是 read-only diagnose、要嘛與 retrieval 無關）

`_ensure_consistency(db_path)` helper（放 `wenji.observability.health` 同 module）：open conn → call `check_consistency` → FAIL print issues + sys.exit(1)；OK 靜默繼續。

### D — Inconsistency definition (3 layers, L2 has 4 sub-rules)

任一層任一 sub-rule FAIL → `issues` 累積 → `ok = False`：

**L1 — counter ↔ matching table row count**：
- `n_articles` ↔ `articles_meta`、`n_chunks` ↔ `chunks_fts`、`n_doc_vectors` ↔ `doc_vectors`，數值不等即 FAIL

**L2 — cross-table derived-from sanity**（chunks / doc_vectors 應該由 articles 派生）：
- L2.a: counter > 0 但對應 table empty（counter not zeroed after manual truncate）
- L2.b: 對應 table 非 empty 但 counter = 0（counter not updated after ingest）
- L2.c: `articles_meta` rows > 0 但 `chunks_fts` rows = 0 → **prod bug 範式**：ingest 寫了 articles 卻沒寫 chunks；L1 看 `n_chunks=0 == chunks_fts=0` 假一致，但 cross-table 暴露 chunks 應該由 articles 派生卻缺
- L2.d: `articles_meta` rows > 0 但 `doc_vectors` rows = 0 → embedding 步驟缺漏

**L3 — sample MATCH validation**：所有 keyword 對 `articles_fts` AND 對 `chunks_fts` 都 0 hits（FTS index 壞了 / corpus 純非中文）。

L3 FAIL 時 issue 帶 hint `"all sample keywords missed both FTS indices; if your corpus is non-Chinese, override with --sample-keywords"`。

### D' — Issue message templates

每個 layer 失敗時的 issue 字串範本（讓 test assertion 與 implementation 對齊）：

- L1 mismatch: `"counter <key> = <counter_val>, but SELECT COUNT(*) FROM <table> = <row_count>"`
- L2.a: `"counter <key> = <counter_val> > 0 but <table> is empty"`
- L2.b: `"<table> has <row_count> rows but counter <key> = 0"`
- L2.c: `"articles_meta has <N> rows but chunks_fts is empty (chunks should be derived from articles)"`
- L2.d: `"articles_meta has <N> rows but doc_vectors is empty (embeddings missing)"`
- L3: `"all sample keywords missed both FTS indices; if your corpus is non-Chinese, override with --sample-keywords"`

### D'' — `--sample-keywords` flag edge cases

- Unset / `None` → 用 `DEFAULT_SAMPLE_KEYWORDS`
- 空字串 `""` 或全空白 `"   "` → 用 `DEFAULT_SAMPLE_KEYWORDS`（同 unset）
- 含空 element 如 `"神,,人"` → split + strip + filter empty → `("神", "人")`
- 全部 element 都 empty 如 `",,, "` → 退化成空 tuple → 用 `DEFAULT_SAMPLE_KEYWORDS`（不允許關閉 L3 check）

### D''' — Startup gate error message format

當 startup gate FAIL，error message 模板：

```
wenji db consistency check FAILED at <db_path>

Issues:
  - <issue 1>
  - <issue 2>
  ...

Run `wenji doctor --db <db_path>` for full diagnostic, or
`wenji ingest dir <path> --db <db_path> --rebuild` to rebuild.
```

`StartupError` 的 `args[0]` 用這個訊息；CLI gate `_ensure_consistency` 印到 stderr 後 `sys.exit(1)`。

### E — Tests

- Unit: `tests/wenji/test_observability_health.py`
  - `test_check_consistency_ok_state`
  - `test_check_consistency_counter_mismatch`（L1）
  - `test_check_consistency_table_empty_counter_zero`（L2 prod-bug-style）
  - `test_check_consistency_sample_match_all_miss`（L3）
- Integration:
  - `tests/wenji/test_cli_doctor.py`：doctor exit 0 / exit 1 + output format
  - `tests/wenji/test_web_startup.py`：FastAPI lifespan raises StartupError on bad db
  - `tests/wenji/test_cli_search_startup_gate.py`：`wenji search` 對 bad db exit 1

### F — CHANGELOG

`[Unreleased]` Added (vNext)：

- `wenji doctor` CLI 提供 db consistency health check（counter vs row count、sample MATCH 驗 FTS）
- `wenji serve` / `wenji eval *` / `wenji search` 啟動時自動跑同一個 check，不一致拒絕啟動

## Out of scope

- 不修 prod logos `chunks_fts` 0-rows state（doctor 上線後主公自己決定要不要 ssh oracle 重 ingest）
- 不加 `wenji doctor --repair` mode（將來 spec；本 spec doctor read-only）
- 不改 `observability/stats.py` 既有 logic（reuse not refactor）
- 不影響 wenji serve route handler 行為（startup gate 失敗時 server 沒起來，handler 不需要 defensive code）
- 不引入新 RetrievalError 階層（沿用既有 `WenjiError` / `SearchError`，加 `StartupError`）
- 不對齊 prod Mode 2 / Mode 3：prod logos 仍在 Mode 2，下次手動 deploy 才會吃到 startup gate；如果 prod 不一致 startup 會 fail，主公要先重 ingest 才能起 server（這是想要的行為）

## Impact assessment

- **Logos prod 影響**：startup gate 起來後，prod 下次 deploy 會發現 chunks_fts 0-rows，server 不 bind port。主公要先 `wenji ingest dir articles/ --rebuild` 才能起。這是 fail-loud 設計目標、不是 regression。
- **OSS user**：build db 不完整 → startup fail，error message 指向 `wenji doctor` 拿 detail。
- **CLI startup latency**：每個 retrieval CLI 多跑 ~5-10 SQL count + 5 sample MATCH，約 +50-100ms（local SSD）。可接受。
- **新 dep**：無，純 stdlib + sqlite3。

## G1 review record

- **2026-05-10 Self-Review S4 修正**：L2 cross-table sanity 原寫「counter > 0 但 table empty 反之亦然」不夠精確、無法 cover prod bug 範式（counter=0 + chunks_fts=0 + articles_meta=12090 的「假一致」）。拆成 L2.a/b/c/d 4 sub-rule，L2.c 明確抓「articles_meta > 0 但 chunks_fts = 0」場景。proposal D / design D4 / spec.md L2 三處對齊。
- **2026-05-10 Sub-Agent Review C1 修正**：原寫「`wenji eval` 4 subcommand 都加 startup gate」，sub-agent 獨立 grep 發現 `sanity-eyeball` / `migrate-jsonl` **無 db parameter**（純 JSON / JSONL 處理）不應 gate。proposal C / tasks.md 3.2 / spec.md scenario 三處改成只 gate `run` (when db is not None) + `run-benchmark`。
- **2026-05-10 Sub-Agent Review C2 修正**：原 tasks 3.2「subcommand body 開頭 call _ensure_consistency(db_path)」沒說 db_path 來源。改成明確「用該 subcommand 既有的 db parameter；無 db parameter 時 skip」。
- **2026-05-10 Sub-Agent Review W1 修正**：L2.c / L2.d issue message 字串不明確（test 寫「類似 phrase」會造成 assertion 鬆散）。proposal D' 加 6 個 issue message template 對齊 implementation 與 test。
- **2026-05-10 Sub-Agent Review W2 修正**：`--sample-keywords` flag 對 `""` / `"   "` / `",,, "` 等 edge case 行為未明。proposal D'' 列 4 個 edge case + 對應行為（全退化用 default，不允許關閉 L3）。
- **2026-05-10 Sub-Agent Review W3 修正**：startup gate error message format 未明，test 只驗 exit code 不驗 message → implementation 可能寫各種格式。proposal D''' 給 multi-line message template + StartupError args 規範。
