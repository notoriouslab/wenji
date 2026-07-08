# Proposal: Web concurrency hardening

## Why

2026-07-08 三鏡片程式碼健檢（A 級 findings，全數經逐行驗證）：wenji web 層在 uvicorn 單 process 多 thread 模型下有四處併發/防呆缺陷 — searcher 單例無鎖 check-then-act（併發冷啟各自載入完整 ONNX model，2-core 11G prod 有記憶體尖峰風險 + 輸家 conn 洩漏）、共享 `sqlite3.Connection` 跨 thread 併發呼叫無鎖（且 `_get_conn` 註解以 file lock 辯護是錯的 — file lock 管跨連線，不管同一 Connection 物件的併發方法呼叫）、TagBrowser 快取有寫入交錯 race 且**永不刷新**（serve 運行中 ingest 的文章永遠不出現在 /tags，無錯誤無 log）、首頁 `?year=abc` 裸 `int()` 直接 500。prod 已於 Mode 3 migration 重上線，這些會在真實流量下咬人。

## What Changes

- `web/app.py`：`_get_searcher` / `_get_tag_browser` 加 double-checked locking（module 級 `threading.Lock`）；`Searcher.search()` 呼叫點以同一把 query lock 序列化（含 rewrite cache 寫入路徑）；`_get_conn` 錯誤註解改寫為真實理由
- `browse/tag.py`：`_refresh_if_needed` 改為 TTL 刷新（啟用既有的 `_last_load` 殘欄）+ build-then-swap 原子替換（先建好本地 dicts，鎖內一次性替換三個屬性，消滅交錯 race）
- `web/app.py:454,461,946`：`int(year)` 三處加防呆（非數字 → 忽略該 filter，不 500）
- `web/app.py:1002-1032`：axes sidebar 區塊的 `conn.close()` 移入 `finally`，與全檔其他路由一致
- tests：併發 init 單次性測試（慢 loader + 多 thread）、TagBrowser TTL 與原子性測試、`?year=abc` 回 200 測試

無 BREAKING（行為變更僅：/tags 資料從「永不更新」變「TTL 內最多延遲 5 分鐘」，是修 bug 不是破壞相容）。

## Capabilities

### New Capabilities

- `web-concurrency`: web 層在多 thread serving 下的併發安全與輸入防呆規格

### Modified Capabilities

（無）

## Impact

- **Code**: `src/wenji/web/app.py`、`src/wenji/browse/tag.py`、`tests/wenji/`（新增 3 組測試）
- **行為**: 查詢路徑序列化（2-core prod 上查詢本為 CPU-bound，序列化對吞吐影響趨近於零；無外部使用者、無 SLA）；/tags 最多 5 分鐘延遲
- **不在 scope**: B 級（ingest 吞吐）、C 級（API 減肥）— 依維護者 2026-07-08 拍板順序另案

---

## G1 審查紀錄（2026-07-08）

- Self-Review：S1 placeholder 0 hits；S2-S5 過（D1-D4 各 2-3 方案）。spectra analyze 4 維度 Clean（1 SUGGEST → 已補 TTL scenario 具體例）。
- Sub-Agent Review（haiku + sequential-thinking）：PASS，0 critical / 0 warning / 2 info（性能假設註記，設計已不依賴）；獨立核實 8/8（行號、無鎖現況、CPU-bound 論證、rewrite cache 寫入路徑）。
- G2 Coverage：D1→1.1-1.2、D2→2.1-2.3、D3→3.1-3.3、D4→4.1-4.3、驗證策略→Phase 5，零缺口。

## Apply 階段 drift corrections（2026-07-08）

1. **鎖覆蓋範圍擴大**：propose 只列 `s.search` 兩個呼叫點；apply 掃描發現另兩條共享 conn 路徑 — `/api/ask`（Asker 持共享 searcher 檢索）與 `/api/segment`（`compute_segment_trace._rewrite` 會寫 rewrite cache）— 均已納入 `_query_lock`。/api/ask 的 LLM 延遲在鎖內：單租戶低流量可接受，註解標記流量成長時重訪。
2. **讀端配對快照**：D3 的原子 swap 只保證寫端；`get_tag_detail` / `get_related_tags` 兩次屬性讀取可跨代 — 補鎖內配對快照。
3. 測試發現 fake embedder 需 DIM=1024（vector 層驗維度），非任意值。
