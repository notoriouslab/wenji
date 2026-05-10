# Design: Fail-loud runtime

## Decision context

兩個 SQLite FTS5 retrieval entry：article-level `bm25_search` 已 fail-loud（`raise SearchError`），chunk-level `chunk_bm25_search` 仍 silent fallback。本 spec 把後者對齊前者；同時為了運維可追溯，兩處都補 `logger.warning(...)`。

## D1 — Exception class

選 **`SearchError`**（既存於 `wenji.core.errors`，bm25.py:107 已用）。

| 方案 | + | − |
|---|---|---|
| **`SearchError`（pick）** | 與 bm25.py:107 對稱；既有 import path 穩定 | — |
| 直接 propagate `sqlite3.OperationalError` | 零依賴 | caller 要 import sqlite3，coupling 差 |
| 新增 `RetrievalError` 階層 | 語意專一 | 過度設計、與 bm25.py 不對稱、未來 search 子層多重抽象 |

## D2 — Logger.warning + raise（方案 B）

選 **方案 B（fail-loud + warning log + 兩處對稱）**。

| 方案 | 兩處 catch site 行為 | + | − |
|---|---|---|---|
| **B（pick）** | 都 `logger.warning(...)` + `raise SearchError(...)` | 兩處對稱；運維可追蹤 | rrf.py / bm25.py 各加 2 行 import |
| A | 只 `raise SearchError`，不 log | 最少 code | uvicorn traceback 才看得到，運維不便 |
| C | 條件式：`no such table` 仍 return `{}`、其他 raise | 對手動半成品 db graceful | 字串比對 brittle、違反 fail-loud 精神、schema_version=2 已守住唯一合理 case |

主公 2026-05-10 偏好 B：個人偏好留 warning trace；對稱性勝過行數。

## D3 — Logger 命名 convention

選 **`logging.getLogger(__name__)`**（多數 module 用法）。

`src/wenji/` 既有 8 處 logger 設置：6 處用 `__name__`（`web/app.py`、`web/branding.py`、`aggregate`、`ingest`、`ask`、`browse/tag`）；2 處用顯式字串（`classify`、`search/rewrite`）。新加的 `rrf` / `bm25` 沿多數派、與相鄰 `web/app.py` 一致。

## D4 — Warning message format

選 `logger.warning("<table> query failed: %s", exc, exc_info=True)`：

- `chunks_fts` / `articles_fts` 帶 raw exception message
- `exc_info=True` 讓 logging framework 自動印 stack trace（不手寫進 message）
- 用 `%s` lazy formatting 避免無 handler 時做 string.format

## Out-of-scope decisions（記錄留 reference，本 spec 不處理）

- 是否 audit 其他 11 處 OperationalError catch：留下個 spec 個案評估，可能多數是 graceful degrade legitimate use（如 web/app.py 的 optional column probe）。
- 是否引入 `RetrievalError` 階層：暫不需要，`SearchError` 已涵蓋。

## Migration risk

- wenji 0.3.x **未 PyPI publish**，無外部 frozen consumer。
- Logos 是唯一 sibling consumer（Mode 3 後 editable install）。本機開發 `pip install -e ../wenji` 自動拿到改動；prod logos 未跟 Mode 3 對齊（`reference_wenji_logos_topology.md`），下次 prod 手動 deploy 時才會吃到本 spec。
- 行為改變範圍：chunks_fts query 觸發 OperationalError 時 `/api/search` 從「silent main-only fallback HTTP 200」變「HTTP 500（FastAPI default handler；`web/app.py` 未自定 SearchError handler）+ log warning」。Logos 是 personal sandbox 可接受、且這正是想要的 fail-loud。
- CHANGELOG 標 Changed/BREAKING 但**不需要** deprecation period（無外部 frozen consumer）。

## Test coverage strategy

- **Negative path**（觸發 raise）：`monkeypatch` 把 `conn.execute` 換成會 raise `sqlite3.OperationalError("simulated lock")` 的 stub，呼叫 `chunk_bm25_search` → assert raises `SearchError`，且 `__cause__` 是原 OperationalError。
- **Logging**：用 pytest `caplog` fixture，filter level=WARNING，assert `chunks_fts query failed` 在 message 中。
- **Positive path 不破壞**：既有 `test_chunk_bm25_search_against_populated_db` 與 `test_chunk_bm25_search_dedups_per_article` 應全綠（正常 query 不進 except branch）。
