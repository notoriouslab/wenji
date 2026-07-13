# Design: chunk-level-vectors

## Context

診斷（logos `tests/v3_rewrite_robustness_diagnosis.md`，2026-07-12）：改寫查詢下 BM25 系全滅、doc-vector 單通道苦撐、chunk 向量增益實證（mean +0.062）。本 change 補上 chunk 語意通道。

關鍵現況事實（2026-07-12 實讀）：
- `chunks_fts` 每 chunk 有 deterministic `chunk_id = f"{article_id}-{idx:04d}"`（ingest/__init__.py 實讀）
- `ingest_one` 的 content-unchanged fast path 在 hash 比對後**提早 return**（ingest/__init__.py:239，於任何 FTS 寫入之前）— unchanged 時 chunks_fts 與 chunk_vectors 都不被觸碰（G1 審查更正：先前「chunks 每次重寫」判讀有誤；實況更穩固）。另 `unchanged` 變數（:230/:376）為 dead code，`if unchanged:` 區塊永不觸發 — apply 時順手清
- content 變更**必然 mint 新 article_id**（`article_id = f"{stem}-{chash[:8]}"`，hash 變 → id 變）— 舊向量掛在舊 id 下
- `rrf_merge` = 兩個 rank dict 各貢獻 `1/(k+rank)` 相加，無權重機制（rrf.py:51-86）
- `vector_search` 的 axis filter 在 SQL 層（`article_axes` JOIN）+ `category != 'excluded'`；矩陣快取 per-(db, axis)、corpus fingerprint = COUNT+MAX(indexed_at)（vector.py）
- `embed.py` 明文禁止 batch>1（2026-07-09 G4 實測：INT8 漂移 cosine ~0.98 且無吞吐收益 0.97x）→ 124k chunks 只能逐一 encode
- parity db：123,929 chunks / 12,100 articles（chunks/article median 6, mean ~10）

## Goals / Non-Goals

**Goals:**
- chunk 向量通道端到端落地：產出（ingest + backfill）、儲存（schema v4）、檢索（3-way RRF）、退化（空表 = 0.5.0 行為）、可觀測（doctor 覆蓋率）
- G4 判準兌現：v3 hit@3 上升且 80q+r14 不降 → keep；否則 discard 整路
- 升級零強制 rebuild：既有 db in-place 升 v4 + backfill 補向量

**Non-Goals:**
- INT8 向量量化（O1 拍板 fp32 先行）
- prod 換庫策略（rsync vs 原地 backfill — logos 側）
- ANN 索引（124k 規模暴力 dot 夠快，過早優化）
- chunk 檢索結果的 UI 呈現變更（roll-up 後仍是 article 列表，`matched_chunks` 機制不變）

## Decisions

### D1: chunk_vectors 表設計與 schema v4 in-place migration

| 方案 | 內容 | 取捨 |
|------|------|------|
| A（採用） | `CREATE TABLE chunk_vectors (chunk_id TEXT PRIMARY KEY, article_id TEXT NOT NULL, vec BLOB NOT NULL)` + `idx_chunk_vectors_article ON (article_id)`；`SCHEMA_VERSION="4"`；`initialise_schema` 檢測 v3 → CREATE（冪等）+ stamp v4 | 與 doc_vectors 同構、per-article 刪改走 index；migration 零資料搬移；v2 db 走既有 v2→v3 再 v3→v4 鏈 |
| B | vec 塞進 chunks_fts 的 UNINDEXED 欄 | FTS5 虛表放 4KB BLOB 反模式；rebuild FTS 就丟向量，與「backfill 不動 FTS」目標衝突 |
| C | 獨立 sidecar db 檔 | 跨檔事務一致性自己扛；doctor/rsync/備份面全部複雜化 |

**空表 = 合法中間態**：v4 升級後、backfill 前，`chunk_vectors` 為空 — 檢索按 D6 退化，不是錯誤。migration 鏈：v2→v3（drop rewrite cache）→ v3→v4（create chunk_vectors），`initialise_schema` 順序執行兩步，任一舊版一次跑到 v4。

### D2: 3-way RRF 形狀 — chunk-vector 為獨立第三通道

| 方案 | 內容 | 取捨 |
|------|------|------|
| A（採用） | `rrf_merge` 增加第三個排名列 `chunk_vec_signals: dict[article_id, float]`（chunk top-K cosine → per-article 取 max → 排名）；三通道各貢獻 `1/(k+rank)` | 通道獨立 = 改寫查詢時不被 BM25 零訊號拖累；可單獨 ablation（G4 需要）；`rrf_merge` 簽名向後相容擴充（新參數 default None = 現行為） |
| B | chunk-vec 與 chunk-BM25 先融合成單一 chunk 通道 | 改寫場景 chunk-BM25 零訊號會稀釋融合分數 — 診斷數據直接反對 |
| C | chunk-vec 併入 main 的 hybrid_combine（doc/chunk cosine 取 max） | 被 alpha 線性混合稀釋；doc 與 chunk 的 cosine 分布不同尺度，max 語意混濁 |

細節：
- **chunk 通道檢索形狀**：query 向量 vs 全 chunk 矩陣 dot → top-`candidate_pool` chunks → per-article max cosine → article 排名列（roll-up 與 chunk-BM25 同哲學）
- **axis filter 位置**：chunk 矩陣**不做** per-axis 切分（124k×axis 數的記憶體不可行）；roll-up 出 article 分數後 JOIN `article_axes` 過濾（axis 查詢時 chunk 通道候選數可能減少 — 可接受，axis 查詢本來就是子集場景）。`category='excluded'` 同點過濾
- **rrf_merge 分支守衛必須擴充**：現行 `if chunk_signals:`（rrf.py:51）在 chunk-BM25 空時走 main-only fallback — 而本 change 的招牌場景（BM25 全滅、僅 chunk-vec 有訊號）正是 chunk-BM25 空。守衛改為 `if chunk_signals or chunk_vec_signals:`；chunk-vec 空且 chunk-BM25 有值時第三項貢獻全零、併集不變 → 與 0.5.0 逐位一致（退化合約的可實作性依據）
- **intent boost 不變**：仍在 RRF 分數上加 `1/(k+1)`，與通道數無關

### D3: chunk 矩陣快取與記憶體

| 方案 | 內容 | 取捨 |
|------|------|------|
| A（採用） | 複用 vector.py 模式但 chunk 矩陣**只建 no-axis 一份**（axis 過濾在 roll-up 後，見 D2）；fingerprint 沿用 corpus 級（COUNT+MAX(indexed_at) of articles_meta — chunk 與 article 同事務寫入，article 指紋變 = chunk 也變） | 單份 508MB 常駐，不隨 axis 數翻倍；失效語意與 doc 矩陣一致 |
| B | per-(db, axis) 各建 chunk 矩陣 | axis 數 × 508MB 記憶體爆炸 |
| C | 不快取，每 query 重建 | 124k 列 frombuffer+stack 每次 ~1-2s，不可接受（doc 矩陣快取就是為此而生，change 2） |

**指紋缺表容忍（Round 2 C3）**：三元組的 `chunk_vectors` COUNT 在**doc 通道**的 `_load_candidates_cached` 每 query 執行 — v3 db 唯讀時該查詢先於任何 chunk loader 觸發，MUST 容忍缺表（sqlite_master 存在檢查或 try/except → 計 0；缺表指紋 = (N, T, 0) 與空表同值，退化語意一致）。D6 的「捕獲於 channel loader」處方不涵蓋此路徑，故在指紋層獨立設防。

記憶體帳（prod 2-core ARM 11G）：chunk 矩陣 508MB + doc 矩陣 49MB + onnx session + uvicorn ≈ 峰值 <2GB — 可承受；**apply 時在 parity 環境實測 RSS 並記錄**（tasks 列項）。查詢延遲：124k×1024 fp32 dot ≈ 127M FLOPs，M2 毫秒級 / 2-core ARM 預估 +50-100ms，實測 task 驗證。

### D4: ingest 與 backfill 語意 — fast path 對齊 + 單 text encode

- **ingest_one**：chunk encode 置於 doc embed 同側（unchanged 提早 return 之後的路徑，自動只跑於 new/changed）；encode 對象 = **原始 chunk 字串（即 `chunk_text_raw` 欄內容），絕不用 jieba tokenized 的 `chunk_text`**；content 變更時的清理放**舊 article_id 清理區塊**（比照 doc_vectors 於 :243-245 的 `DELETE ... WHERE article_id = old_article_id`）— content 變更 mint 新 id，清錯 id = 孤兒列；新向量逐 chunk 單 text encode + INSERT，與 chunks_fts 同一事務
- **backfill 子命令**：`wenji ingest backfill-chunk-vectors --db <path>`。語意 = 掃 `chunks_fts` 中無對應 `chunk_vectors` 列的 chunks，**讀 `chunk_text_raw`** 逐一 encode 補寫（與 ingest 路徑 bytewise 等價 — 誤讀 tokenized 欄 = 靜默向量污染，G4 只會誤判 feature 無效而不現形根因），per-article commit → **天然可中斷續跑**（重跑跳過已有向量的 chunk）；進度 log 沿用 change 2 格式（n/total, rate, ETA）；結束時寫 0.5.0 環境戳（`_record_build_environment` 複用 — backfill 也是批量向量寫入）
- **單 text encode 鐵律**：沿 embed.py 警告，逐 chunk `encode_batch([text])`；G4 曾判 batch 化 DISCARD，本 change 不重開

| 備選（backfill 形狀） | 取捨 |
|------|------|
| 獨立子命令（採用） | 明確、可排程、與 rebuild 解耦 |
| ingest dir 自動偵測補漏 | 隱式行為，12k 篇每次 ingest 都掃差集 — 慢查詢常駐化 |
| 只能 rebuild | 全 rebuild 因逐 chunk encode 膨脹到 15-20h 級，強迫 consumer 付不必要成本 |

### D5: G4 實驗設計 — 主實驗與權重 fallback 對照分開判

- **兩階段執行（維護者 2026-07-13 拍板）**：先導 = 注入式 backfill（v3 gold + 各題 top-50 候選 ≈ 1.2 萬 chunks，<1h）跑 v3 — 偏差方向已知（未覆蓋文章在 chunk 通道隱形 → 只會**高估**改善），故先導無改善即可提前 discard 省下全量；先導有效才付全量成本做真判決（keep 判決永遠以全量為準，先導分數不作 keep 依據）。threads 前置驗證（2 vs 8 bytewise）決定本機 backfill 併發度。
- **主實驗(3-way)**：parity db **全量** backfill → 跑 v3 Part A（35 題 hit@1/3/10 + MRR）+ 80q+r14 → 對照 baseline（v3 51.4% / 80q 75）。**keep = v3 hit@3 上升 且 80q pass 數與 miss 清單不劣化**；v3 下降或 80q 劣化 → discard（feature flag 不留，整路 revert — 半殘通道是維護債）
- **對照實驗（向量權重 fallback）**：BM25 通道（article+chunk-BM25）皆零訊號時，RRF 中 vector 系通道權重 ×w（w 掃 1.5/2.0）— 在 3-way 架構上疊加量測；獨立 keep/discard
- **量測紀錄**：兩實驗的 per-題翻轉清單（哪些 miss→hit、哪些 hit→miss）入 change 附錄 — 防「總分升但個題劣化被平均掩蓋」
- 環境：本機 parity db + ort 1.26 鎖版；`unset WENJI_CONFIG`

### D6: 退化與可觀測合約

- **退化**：`chunk_vectors` 空、**表不存在**（v3 db 走唯讀入口 — serve 從不 migrate，prod 升級第一步就是這個狀態）、或 embedder 缺 → chunk-vec 通道回傳空排名列 → `rrf_merge` 第三參數 None/空 = 現行 2-way 路徑。**表不存在 ≡ 空表**：通道 loader 捕獲 `sqlite3.OperationalError` 回空、絕不外拋（對照組警示：`chunk_bm25_search` 對缺表是 fail-loud raise SearchError — chunk-vec 通道不得沿用該模式，因 v3 db 唯讀服務是合法常態而非資料損壞）。**升 v4 未 backfill 的 db 與 v3 db 的檢索行為皆與 0.5.0 逐位一致**（80q 持平為 G3 驗證項）
- **部分覆蓋**：backfill 中斷的 db（部分 chunk 有向量）→ 通道照常運作、只是候選少 — 不擋不警（doctor 顯示覆蓋率即可）
- **doctor**：報告加 `chunk_vectors  = <n>/<n_chunks> (<pct>%)` 行；**v3 db 缺表時渲染為零覆蓋、不報錯**（migration 窗口期 — pip 升級後、首次寫入口前 — 正是 doctor 被拿來對帳的時刻）；資訊性、不影響 exit code（0.5.0 environment 段同哲學）

## Implementation Contract

1. **Schema**：新建 db `schema_version=4` 含空 `chunk_vectors`；v3 db 走任一寫入口 → 表被建、version=4、資料完好；v2 db 一次升到 v4（先 drop rewrite cache 再建表）；讀入口不遷移照常
2. **Ingest**：ingest 一篇新文章 → `chunk_vectors` 列數 = 該篇 chunks 數、每 vec 1024×fp32；content unchanged 重 ingest → chunk_vectors 不重算（計數 encode 呼叫斷言）；content 變更 → 舊列刪、新列全
3. **Backfill**：對「FTS 有 chunks、向量缺」的 db 跑子命令 → 覆蓋率 100%；中斷重跑 → 已有向量的 chunk 不重 encode；完成時寫環境戳
4. **檢索**：改寫式查詢（BM25 零命中）在有 chunk 向量的 db 上，gold 可經 chunk-vec 通道進入 RRF（單元層：構造 BM25 miss + chunk cosine top 的 fixture，斷言 3-way 結果含該 article）；`chunk_vectors` 空表 db 的 80q 結果與 0.5.0 **逐題一致**；**v3 db（無表）唯讀搜尋正常回結果、零錯誤**
5. **快取**：連續兩次 search 只建一次 chunk 矩陣；ingest 後指紋變 → 重建（doc 矩陣同款 tests 的 chunk 版）
6. **doctor**：三態 db（全覆蓋/部分/零）各顯示正確覆蓋率行，exit code 不受影響
7. **Gate**：G4 兩實驗數據入 change 附錄；80q+r14 全程不降；`audit_release.sh` exit 0；pytest/ruff（check+format）全綠

**Scope 邊界**：in = 上述 + CHANGELOG 0.6.0 + README/extending 同步；out = INT8、ANN、prod 換庫、A'。

## Risks / Trade-offs

- [backfill 的 124k 次單 text encode 實際時長未知（估數小時級）] → tasks 先跑 500-chunk 抽樣計時外推，超過 12h 級再回頭談批次策略（帶著 change 2 的漂移數據）
- [chunk 矩陣 508MB 在 prod 2-core ARM 排擠其他記憶體] → parity 環境實測 RSS；超標則 0.6.x 做 INT8（已列 non-goal 但留 escape）
- [3-way 讓非改寫查詢（80q 型）劣化 — chunk 通道引入噪音] → G4 判準本身就是防線（80q 不降才 keep）；per-題翻轉清單抓平均掩蓋
- [同文多 chunk 高分 → roll-up max 後單文獨大、近重複文叢分票加劇] → v3 的近重複場景會直接反映在 hit@3；不預先做去重工程（等數據）
- [corpus fingerprint 用 articles_meta，backfill 只寫 chunk_vectors 不動 articles_meta → 指紋不變 → 快取不失效、看不到新向量] → **已知陷阱**：backfill 完成時 touch 指紋（更新任一 meta 或 fingerprint 改為含 chunk_vectors COUNT）— design 定案：fingerprint 擴為 (articles COUNT, MAX(indexed_at), chunk_vectors COUNT) 三元組
- [v2→v4 跨版 migration 鏈未被 0.5.0 tests 覆蓋] → 專屬 test：v2 fixture 一次升 v4

## Migration Plan

1. 0.6.0 tag → PyPI（trusted publishing）
2. logos 側（out of scope，記順序）：pip 升級 → serve 照常（v3 db 退化 2-way，行為 = 0.5.0）→ 擇機 backfill（本機 rsync 或 prod 原地）→ chunk 通道生效
3. 回退：pip 釘回 0.5.0 — v4 db 的 chunk_vectors 表對 0.5.0 是未知表（SQLite 不在乎）、schema_version=4 會被 0.5.0 的 `initialise_schema` fail-loud 擋寫入口（預期）、讀入口照常

## Open Questions

（無 — O1 fp32 / O2 一版全包已拍板；D1-D6 定案，G1 有翻案回此節記錄）
