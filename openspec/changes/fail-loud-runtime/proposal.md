# Proposal: Fail-loud runtime — chunk-level FTS5 OperationalError propagation

## Why

`src/wenji/search/rrf.py:130-137` 的 `chunk_bm25_search` 把所有 `sqlite3.OperationalError` 吞掉回 `{}`，導致 chunks_fts 真出 SQLite-level 錯誤時（table missing、schema corrupt、lock busy）retrieval pipeline silently fallback 到 main-only ranking，HTTP 200 但結果偏差，無告警。同 package 的 article-level `bm25_search` (`src/wenji/search/bm25.py:105-108`) 已經 fail-loud（`raise SearchError`），兩條對稱 retrieval path 行為不一致。

對應已知 prod 風險範式（`reference_prod_retrieval_bug.md`，2026-05-10 ssh oracle 確認）：logos production sandbox 的 chunks_fts 0 rows + wenji_meta counter 全 0，是「ingest 中斷 / schema migration 漏帶資料」造成的 silent failure。**重要 caveat**：那個 case 走的是「chunks_fts table 存在但 empty，MATCH query 正常回 0 rows」path，**不會**觸發 `OperationalError`、不在本 spec 範圍。本 spec 只防「下次別的 SQLite-level 錯誤被吞」這個結構性風險；修 prod-bug-style「table 存在但空」的 silent failure 是 `add-doctor-and-startup-check` spec 的 sample MATCH check 任務。

Mode 3 拍板（2026-05-10）後 wenji = OSS SSOT、logos = sibling consumer。結構性消滅 silent failure 對 OSS 用戶也受益。

## What Changes

### Behavioral

- `chunk_bm25_search` 在 chunks_fts SQLite query 觸發 `OperationalError` 時：先 `logger.warning(...)` 留 trace，再 `raise SearchError(...) from exc` 往上 propagate（不再 silent return `{}`）。
- `bm25_search` (`src/wenji/search/bm25.py:105-108`) 既有 `raise SearchError` 不變，**配套**補 `logger.warning(...)` 與 chunk-level 對稱（fail-loud 行為不變、log trace 補齊）。

### Code

- `src/wenji/search/rrf.py`: 加 `import logging` + `logger = logging.getLogger(__name__)` + `from wenji.core.errors import SearchError`；line 136-137 改成 `logger.warning(...)` + `raise SearchError(...) from exc`。
- `src/wenji/search/bm25.py`: 加 `import logging` + `logger = logging.getLogger(__name__)`；line 107 在既有 `raise SearchError(...) from exc` 之前插入 `logger.warning(...)`。

### Tests

- 新增 `test_chunk_bm25_search_raises_on_operational_error`：用 `monkeypatch` patch `conn.execute` 觸發 `sqlite3.OperationalError`，assert 抛 `SearchError`、cause chain 保留原 OperationalError。
- 新增 `test_chunk_bm25_search_logs_warning_on_operational_error`：用 `caplog` 驗 warning emit。
- 對稱 `test_bm25_search_logs_warning_on_operational_error`：bm25 既有 raise 行為不動，加驗 warning emit。

### CHANGELOG

- `[Unreleased]` Changed / BREAKING：`chunk_bm25_search` 不再對 `OperationalError` silent fallback；改 raise `SearchError`（與 `bm25_search` 一致）。兩條 retrieval path 觸發 `OperationalError` 時 emit `WARNING` log。

## Out of scope

- 其他 11 處 `OperationalError` catch（`web/app.py` 8 處、`ask/__init__.py:189`、`search/__init__.py:108`、`core/db.py:53` 已 fail-loud）—— common-ground assumption #1 鎖死本 spec 只動 search 層的兩處。
- `wenji doctor` CLI、startup consistency check —— 由 `add-doctor-and-startup-check` spec 接手。
- Prod logos `chunks_fts` 0-rows 修復（沒外部 user，不在本 spec scope；Mode 3 Phase C doctor 上線後可重新評估）。
- 引入 `RetrievalError` 階層 —— `SearchError` 已涵蓋，不過度抽象。

## Impact assessment

- **Caller chain**：`Searcher.search()` (`src/wenji/search/__init__.py:290`) 直接呼叫 `chunk_bm25_search`；它是 `wenji serve` `/api/search` route 與 `wenji ask` 的核心依賴。`SearchError` 從這裡 propagate 上去；驗證 `web/app.py` **未** 註冊自定 exception handler，所以走 FastAPI default → HTTP 500 + 標準 error body。bm25.py 早已是這個 path（既有 `raise SearchError`），本 spec 改 chunk-level 後兩條 retrieval path 一致。
- **Tests**：grep 既有 `tests/wenji/test_search_rrf.py` 與 `test_search_bm25.py`，**無**「chunks_fts 不存在 → return `{}`」silent-return assertion；本 spec 只新增 positive test、不需刪舊 test。
- **Migration**：wenji 0.3.x 未 PyPI publish；唯一消費者 logos 是 personal sandbox（Mode 3 後 sibling editable install），fail-loud 正是想要的行為。CHANGELOG 標 BREAKING 但無 deprecation period。

## G1 審查修正紀錄

- **2026-05-10 Self-Review S4 修正**：proposal「Caller chain」與 design「行為改變範圍」原寫「（或 framework 既有 handler）」歧義。獨立驗證 `web/app.py` 未註冊 SearchError exception handler，改寫成「FastAPI default handler；wenji 未自定 SearchError handler」。
- **2026-05-10 Sub-Agent Review C1 修正**：tasks 2.4 原只寫「既有 SearchError raise 行為不變」當 assumption，未寫實際 `pytest.raises(SearchError)` assertion。已補上 `with pytest.raises(SearchError)` + `__cause__` 驗證，確保「既有 raise 行為保留」是被測試 enforced 的。
- **2026-05-10 Sub-Agent Review W1 修正**：tasks 2.2 / 2.3 / 2.4 原寫「monkeypatch patch conn.execute」太抽象，實作時要試 mock target。已改成具體 `MagicMock(side_effect=sqlite3.OperationalError(...))` fake conn 模式，與 wenji 其他 test 的 in-memory db pattern 區分。
