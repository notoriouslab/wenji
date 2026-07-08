# Design: Web concurrency hardening

## Decision context

併發模型（common-ground W2，ESTABLISHED）：uvicorn 單 process `--workers 1`，FastAPI sync routes 走 threadpool → 多 thread 同時進入 handler 是常態。修法原則（W3）：最小侵入、單人維護可讀性優先，不做 async 重構。

## D1 — 單例初始化競態（searcher / tag_browser）

選 **double-checked locking（module 級鎖）**。

| 方案 | + | − |
|---|---|---|
| **Double-checked lock（pick）** | 改動局部（~12 行）；保留 lazy 語意（degraded mode、測試不需 model 檔都不受影響）；鎖只在冷啟競態窗口內有成本 | 慣用但需寫對（check → acquire → re-check） |
| Lifespan eager init | 徹底消滅 lazy 競態類 | `Embedder()` 初始化可能觸發 ~600MB model 下載 — 測試環境與無模型部署會在 startup 爆炸或變慢；改變 degraded-mode 語意，血量遠超本 change |
| 維持現狀 + 文件警告 | 零改動 | 2-core 11G 上 N 個 thread 同時載 BGE-M3 是實測級 OOM 風險，不可接受 |

實作：module 級 `_init_lock = threading.Lock()`，`_get_searcher` / `_get_tag_browser` 進鎖後 re-check `state[...]`。

## D2 — 共享 Connection 的跨 thread 併發

選 **全域 query lock 序列化 `Searcher.search()` 呼叫**。

| 方案 | + | − |
|---|---|---|
| **Query lock（pick）** | 一把鎖解掉「同一 Connection 物件併發方法呼叫」整類問題（含 QueryRewriter 的 INSERT+commit）；查詢在 2-core 上本為 CPU-bound（embed + 12k 向量矩陣），序列化的吞吐損失趨近於零；正確性可直接論證 | 未來多核部署時吞吐受限（屆時再演進到方案二） |
| threading.local per-thread conns | 真併發 | 每 thread 一顆 conn + 各自 PRAGMA；rewrite cache 寫入仍需協調；threadpool thread 數不定，conn 生命週期管理複雜度高於收益 |
| 只鎖寫入路徑（rewrite cache） | 鎖粒度細 | Python sqlite3 對同 Connection 的「併發讀」同樣非安全（官方文件明言 caller 自行序列化）；細鎖治標 |

同時改寫 `_get_conn` 註解：刪除「file lock 序列化寫入」的錯誤辯護，改為「同一 Connection 的併發呼叫由 query lock 序列化；per-request conn 用完即關」。

## D3 — TagBrowser 刷新與原子性

選 **TTL 300s + build-then-swap**。

| 方案 | + | − |
|---|---|---|
| **TTL 300s + 原子替換（pick）** | 修 staleness（serve 中 ingest 最多 5 分鐘可見）+ 修 race（本地建好 `tag_to_articles`/`article_to_meta`/`tag_counts`，鎖內一次替換）；啟用既有 `_last_load` 殘欄 — 原作者本意即 TTL（`tag.py:17,23` 註解為證）；12k rows 重載實測等級 <1s，5 分鐘一次成本可忽略 | 5 分鐘內看舊資料（可接受：修 bug 前是「永遠舊資料」） |
| per-request 重載 | 永遠新 | 每個 /tags 請求全表掃 12k rows + JSON parse，熱路由不值 |
| ingest generation counter 失效 | 精準 | ingest 是獨立 process（CLI），跨 process 通知需 db 內 version 表 — 血量大，B 級 change 的向量快取失效機制做好後可共用，屆時再併 |

## D4 — 輸入防呆與資源清理

- `int(year)`（`app.py:454,461,946` 三處）：`year.strip().isdigit()` 才轉換，否則忽略該 filter — 與現行 `if year:` guard 合併為一個 helper `_parse_year(year) -> int | None`，三處呼叫（消除三處重複的同時修 bug）
- `app.py:1002-1032`：`conn.close()` 移入 `finally`，與全檔其他 10 處 `_get_conn()` 呼叫點的慣例一致

替代方案（回 400 錯誤頁）不採：瀏覽情境下手滑參數應優雅降級，不該給使用者看錯誤頁；三處呼叫端行為一致化本身就是消歧。

## 驗證策略

- 併發 init：monkeypatch 慢 loader（`time.sleep(0.2)`）+ 8 threads 同時呼叫，斷言 loader 只被呼叫一次
- TagBrowser：race 用 build-then-swap 後以「兩 thread 併發 list_tags + get_tag_detail 不 KeyError」冒煙；TTL 用 monkeypatch `time.monotonic`
- year：TestClient `GET /?year=abc` → 200；`?year=2023` 行為不變
- 全套 pytest + ruff（G3）
