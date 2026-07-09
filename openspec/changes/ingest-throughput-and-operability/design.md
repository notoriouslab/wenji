# Design: Ingest throughput and operability

## Decision context

事實基礎（全部當日實測或考古驗證，見 common-ground T1-T5 與 memory `reference_logos_prior_art`）：

- `ingest_one` 每篇呼叫 `encode_batch([body_norm])`（`ingest/__init__.py:366`）— batch 維度閒置；`Embedder.encode_batch` 內建 batch_size=32 切分
- batch=1 是歷史撤退：logos embed-server 時代大文章 150+ chunks HTTP 斷連（logos `89dec87`）；wenji 為 in-process onnxruntime，無該層
- FTS DELETE `WHERE article_id=?` 走 FTS5 全表掃（`article_id UNINDEXED`，EXPLAIN 驗證）；fresh insert 也無條件執行兩次（`ingest/__init__.py:313,336`）
- `ingest_one` 已有 content-hash fast path（`:224-238`）：同 path 同 hash → 只 UPDATE indexed_at 即返回 — **de facto resume 已存在**，只是 rebuild 會 wipe、無人知道用 ingest dir 續跑
- 實戰速率：rebuild 前 2,000 篇 ~1.1s/篇，7,600 篇時 ~2.9s/篇（O(N²) DELETE + 長文混合效應）
- prod 2 cores（Oracle free tier 政策縮減，原 4 核 — THREADS=3 是遺物，部署參數另行處理不在本 change）

## Goals / Non-Goals

**Goals**：rebuild 全量時間砍半以上；長工全程可觀測；單篇壞檔不再有機會沉默燒掉整晚；查詢延遲去掉隨語料成長項。
**Non-Goals**：chunk-level vectors（roadmap）；reranker（backlog 緩議）；ingest 並行多進程（單機 2 核無收益）；改變 fail-loud 預設哲學。

## D1 — Batch embedding 策略

選 **字元預算打包 + 長文獨行 + 失敗降級逐篇**。

| 方案 | + | − |
|---|---|---|
| **字元預算打包（pick）** | 短文積極併批（週報類最受益）；長文（ccbible 註釋類）獨行，避免 tokenizer padding 對齊最長文造成 11G ARM 記憶體尖峰 — 正是舊傷疤在新架構的類比形態；預算內迭代順序固定 → 批次組成確定性 → byte-identical 保持 | 打包邏輯 ~30 行 |
| 固定 K 篇一批 | 更簡單 | 一批 32 篇長文 = padding 後大 tensor，重演記憶體風險；短文批太小浪費 |
| 維持 batch=1 | 零風險 | 放棄 2-4x，B 級最大單項白給 |

實作：累積 buffer 直到 `sum(len(text)) > BUDGET`（初值 32,000 字元，約 8-16 篇短文或 1-2 篇長文）或檔案迭代結束 → `encode_batch(texts)` → 寫回各篇。單篇超預算者獨行。批次 `encode_batch` 拋例外 → 逐篇重試該批（隔離壞篇，配合 D4）。

兩層保證（釐清與 rebuild byte-identical 承諾的關係）：

1. **Run-to-run byte-identity（無條件）**：打包是 `sorted(root.glob())` 迭代順序（`ingest/__init__.py:394` 既有）的純函數 → 同語料兩次 rebuild 批次組成完全相同 → docstring 的 byte-identical 承諾**不管下一條結果如何都成立**。
2. **Batch-vs-single 等價（品質 gate）**：同一 10 篇樣本（含最長篇）batch vs 單篇 encode，斷言逐元素相等；若 onnxruntime 批次化引入浮點差異 → 降級斷言 cosine > 0.99999，且 CHANGELOG 明載「向量與 v0.4.0 單篇計算值有微小差異」（一次性、有記錄的轉變，非承諾破壞）+ 80q baseline 不動。

### D1 G4 判定：DISCARD（2026-07-09 實驗，主公核准撤案）

等價 gate + 吞吐 benchmark（M2、真 BGE-M3 INT8）雙殺：

- **吞吐 0.97x**（32 篇逐篇 15.35s vs 一批 15.89s）— CPU INT8 推論 compute-bound，batch 紅利屬 GPU，健檢預估的 2-4x 不適用本棧
- **向量漂移 cosine floor 0.98**（gate 門檻 0.99999）— INT8 量化 + padding 改變數值路徑，模型層現實非 code bug

零收益 + 實質漂移 → 撤案。附帶發現：`Embedder.encode_batch` 內建 batch 機制從未被 >1 使用、且有漂移 — embed.py 加 docstring 警告。速度真槓桿確認為 D2（DELETE O(N²)）。

## D2 — Fresh-insert 跳過 FTS DELETE

選 **`existing` 判斷內縮 DELETE**：兩個 DELETE 移入「content 變更」分支（fast path 與 fresh insert 都不執行）。

| 方案 | + | − |
|---|---|---|
| **條件內縮（pick）** | 兩行移位；rebuild（全 fresh）完全免掃；語意精確（只清真正存在的舊列） | 無 |
| 給 FTS 表加 article_id 索引輔助表 | 老 db 也加速 | schema 變更 + 維護一張映射表，為已被消除的成本付結構代價 |

## D3 — 進度輸出

選 **每 200 篇 `logger.info`**，格式沿用 logos `5f33c5b`：`ingest: 2400/12090 (19.9%) rate=1.2/s eta=134min`。CLI 已把 logging 導到 stderr；nohup log 直接可讀。tqdm 不採（nohup 下 `\r` 進度條反而污染 log — 本次實戰所見）。

## D4 — 壞檔政策

選 **`--skip-bad` opt-in，預設 fail-fast**。

| 方案 | + | − |
|---|---|---|
| **opt-in flag（pick）** | fail-loud 預設哲學不動（單篇損壞立即可見）；營運場景（12k 全量、無人值守）顯式選擇韌性；skip 清單結尾彙報 + exit 1（不假裝成功） | 使用者要知道 flag 存在（CLI help + 文件） |
| 預設 skip+report | 無人值守友善 | 顛倒既有哲學；小語料使用者的單檔錯誤被軟化 |
| 維持 fail-fast only | 零改動 | 2026-07-08 實戰：第 390 篇 crash 燒掉一晚 — 營運上不可接受 |

skip 時記錄 `(path, error)` 清單，結尾 `logger.error` 逐條列出 + JSON 輸出 `{"ingested": N, "skipped_bad": [...]}` + exit code 1。

## D5 — Resume 揭露（文件為主）

選 **文件 + CLI help 提示，不新增 wipe-skipping flag**。

content-hash fast path 已提供正確的續跑語意：中斷後 `wenji ingest dir <同一目錄> --db <同顆 db> --config <同 config>` — 已完成篇走 hash 比對（無 embed），未完成篇正常 ingest。rebuild 的 wipe 是其「byte-identical 全量重建」語意的一部分，不加 `--resume` 去汙染它（要續跑就用 ingest dir，兩個動詞語意分明）。落點：rebuild CLI help + README 運維段 + docstring。

替代（rebuild --resume flag）不採：與 wipe 語意衝突，且孤兒列（來源已刪的文章）處理會把 flag 變成半個 sync 引擎 — ingest dir 續跑後若需精確一致，重跑完整 rebuild 或用差集 patch（logos `ea3f17f` 模式，留待有需求再做）。

## D6 — synchronous=NORMAL

`connect()` 在 WAL 分支加 `PRAGMA synchronous = NORMAL`。WAL 下 NORMAL 不損資料庫完整性（OS crash 最多丟最後 transaction）；ingest 每篇 commit 的 fsync 稅直接減免。全域生效（serve 讀路徑無感）。

## D7 — 查詢向量矩陣快取

選 **Searcher 內 memoize + 失效指紋**。

| 方案 | + | − |
|---|---|---|
| **memoize + 指紋（pick）** | 每 query 省 12k 次 `np.frombuffer` + 49MB 重建；指紋 = `SELECT COUNT(*), MAX(indexed_at) FROM articles_meta`（單條快查詢），ingest 後自動失效 | 指紋查詢每 query 一次（微小）；axis 過濾變體需按 axis 分 key |
| 永久快取 + 手動失效 | 最快 | serve 常駐 + 外部 ingest 的組合會供舊向量，重演 TagBrowser 舊病 |
| 不快取 | 零改動 | 隨語料線性惡化的最大查詢延遲項 |

與 change 1 的 `_query_lock` 相容：快取讀寫都發生在鎖內的 search 呼叫中，無新併發面。

## 驗證策略

- 單元：打包器（預算邊界、單篇超限、確定性順序）、DELETE 條件（fresh/unchanged/changed 三態）、skip-bad（壞檔清單 + exit code）、矩陣快取失效（ingest 後指紋變 → 重載）
- 等價 gate：batch vs single 向量逐元素比對（含最長樣本）
- G4 實驗：本機 parity db 全量 rebuild before/after 計時 + 80q baseline 兩側跑（eval-regression-guard 流程）
- 全套 pytest + ruff
