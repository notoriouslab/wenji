# Proposal: Chunk-level vectors (3-way retrieval)

## Why

v3 held-out 改寫魯棒性診斷（2026-07-12，logos `tests/v3_rewrite_robustness_diagnosis.md`）給出通道級證據：對禁抄措辭的改寫查詢，17 題 miss 中 article-BM25 與 chunk-BM25 **雙雙 0/17** 進 top-200 — 現有 3-way RRF 實質退化為 doc-vector 單通道，而 doc 向量又被多主題長文稀釋（拾穗類 best-chunk vs doc cosine gain 高達 +0.14~+0.22；整體 mean gain +0.062、12/17 題 >0.02，4 題 best-chunk 已達 hit 題勝出水位）。chunk 級向量通道是對症武器：給改寫查詢第二條語意通道，並讓長文的具體段落可被直接檢索。主 KPI = v3 hit@3-identity（現 51.4%），80q+r14（75/80）防回歸。

## What Changes

- **Schema v3→v4（BREAKING）**：新表 `chunk_vectors(chunk_id PRIMARY KEY, article_id, vec BLOB fp32)` + article_id 索引；`initialise_schema` 沿 0.5.0 先例 in-place 升級（CREATE 冪等 + version stamp，零資料搬移）。**表空 = 合法中間態**（backfill 前），檢索自動退化（見下）
- **Ingest 產出 chunk 向量**：`ingest_one` 在寫 `chunks_fts` 的同一事務內逐 chunk encode（**單 text encode — batch>1 有實測 INT8 漂移 cosine ~0.98，embed.py 明文禁止**）；content-hash fast path 與 resume 自然涵蓋
- **Backfill 子命令**：`wenji ingest backfill-chunk-vectors --db <path>`（暫名，design 定案）— 對既有 db 只補 chunk 向量、不動 FTS/doc 向量，可中斷續跑；讓 parity db 與 prod 升級**不需全 rebuild**（全 rebuild 因逐 chunk encode 會從 ~6.5h 膨脹到 15-20h 級，backfill 只付 embed 成本）
- **3-way RRF**：chunk-vector 通道（chunk top-K → article roll-up 排名列）作為獨立第三通道進 RRF；`chunk_vectors` 空或缺 embedder 時**自動退化為現行 2-way**（= 0.5.0 行為，升級不破壞既有 db 檢索）
- **chunk 矩陣快取**：複用 `search/vector.py` 的 per-(db, axis) memoize + corpus fingerprint 模式；fp32 常駐 ~508MB（O1 拍板 fp32 先行，INT8 留未來優化）
- **G4 對照實驗（change 內）**：BM25 零訊號場景的向量通道權重 fallback — 與 3-way 分開量測，keep/discard 各自判
- **doctor 覆蓋率行**：`chunk_vectors` 覆蓋率（vs `chunks_fts` 列數）進報告，資訊行不影響 exit code（與 0.5.0 environment 段同哲學）

## Capabilities

### New Capabilities

- `chunk-vector-retrieval`: chunk 向量的產出（ingest/backfill）、儲存（schema v4）、檢索（3-way RRF + 退化合約）、可觀測（doctor 覆蓋率）

### Modified Capabilities

（無 — Searcher 的六參數建構合約不變：chunk 通道內部複用既有 embedder 參數，不新增建構參數，故既有 spec 無 requirement 級變更）

## Impact

- **Code**: `core/schema.sql`+`core/db.py`（v4）、`ingest/__init__.py`（chunk encode + backfill）、`cli/ingest.py`（子命令）、`search/vector.py`（chunk 矩陣快取）、`search/rrf.py`（3-way）、`search/__init__.py`（pipeline 接線）、`observability/health.py`+`cli/doctor.py`（覆蓋率行）、tests
- **量化成本**：parity db backfill ≈ 124k 次單 chunk encode（M2 一次性數小時級，實測後精算）；db +~500MB（1.15G→1.65G）；查詢時 chunk 矩陣常駐 ~508MB、額外 dot ~127M FLOPs/query（2-core ARM 預估 +50-100ms，design 列實測 task）
- **版本**: 0.6.0（schema v4 + 檢索行為變更集中一版）；唯一 consumer = logos
- **驗證**: G4 主實驗 = parity db backfill 後 v3 Part A hit@3（51.4% → ?，**keep 判準 = v3 上升且 80q+r14 不降**，不設目標值防調參）；eval-regression-guard 全程；v3 永不調參；`scripts/audit_release.sh` 檢查 exit code
- **不在 scope**: INT8 量化（未來優化）；prod 換庫策略（rsync vs 原地 backfill，logos 側決策）；A' 開關啟用（獨立 ops 工作）

---

## G1 審查紀錄（2026-07-13，fresh reviewer 三輪）

**Round 1：FAIL（2 critical / 3 warning）→ 修正**

- C1（v3 db 唯讀服務缺 chunk_vectors 表 → chunk-vec 通道 fail-loud → prod 升級第一步全站搜尋崩潰；退化合約只涵蓋「空表」漏「缺表」）→ 已修：spec 明文「表不存在 ≡ 空表，loader 捕獲 OperationalError 回空」+ 專屬 scenario；design D6 含對照組警示（不得沿用 chunk_bm25_search 的 fail-loud）
- C2（encode 欄位未指定 — 誤讀 tokenized `chunk_text` = 靜默向量污染，唯一 backstop 是 G4 且只會誤判 discard 不現形根因）→ 已修：兩路徑明訂 `chunk_text_raw` + bytewise 一致性 scenario/test
- W1（design 對 unchanged fast path 的現況描述錯誤 — 實為 :239 提早 return 而非「chunks 每次重寫」）→ 已修並標記更正；dead 變數 `unchanged` 列順手清
- W2（content 變更 mint 新 article_id — DELETE 未指明放舊 id 清理區塊會留孤兒；scenario 只查新 id 會誤過）→ 已修：D4 明訂位置、scenario 改 lineage 總列數斷言
- W3（rrf_merge 守衛 `if chunk_signals:` 會把招牌場景閘進 fallback）→ 已修：D2 明文守衛擴為 `or chunk_vec_signals` + 逐位一致論證收錄

**Round 2：FAIL（新 1 critical / 1 warning — 修正組合處的復發路徑）→ 修正**

- C3（D3 三元組指紋在 **doc 通道**每 query 執行 `COUNT(chunk_vectors)` — v3 db 先於任何 chunk loader 撞缺表，C1 的 channel-loader 處方不涵蓋；且 in-memory fixture 慣例跳過指紋路徑會讓測試假綠）→ 已修：指紋 COUNT 容忍缺表計 0（缺表指紋 = 空表指紋，退化語意一致）+ tasks 3.4 強制 file-backed fixture 並明文理由
- W4（doctor 覆蓋率行對 v3 缺表崩潰 — 恰在 migration 窗口期，doctor 正是對帳工具）→ 已修：缺表渲染零覆蓋不報錯 + 「doctor survives the migration window」scenario

**Round 3：PASS（Critical 0 / Warning 0）** — 七項全數逐項實證 RESOLVED、無回歸無新矛盾；reviewer 枚舉 v3 唯讀路徑的全部三個 chunk_vectors 觸點（loader / 指紋 / doctor）確認各有缺表合約與測試。判語「可進 apply」，並標記兩處 apply 高危細節：file-backed fixture 要求（:memory: 會假綠）、task 2.2 bytewise 斷言（全 change 價值的守門測試）。

**G2 Coverage**：D1→1.1-1.2、D2→3.2-3.3、D3→3.1、D4→1.3-1.4+2.1-2.2、D5→4.3-4.4（+0.3 抽樣防線、4.1 退化基準、4.2 全量）、D6→3.4-3.5；六 Risk 全數有 mitigation task；孤立 task 皆 pre-flight/commit gate/收尾 infra。Type Check：表名/欄位/子命令/三元組/判準用語跨四檔一致。零缺口。

---

## G4 判決：DISCARD（維護者 2026-07-13 裁決，整路不上線）

**兩階段實驗數據**（本機 parity db v4 副本 + 注入式先導 9,836 chunks，ort 1.26 / threads=8 bytewise 驗證）：

| 指標 | baseline | 先導 3-way（偏差方向：只會高估） | 權重 fallback w=1.5/2.0/3.0 |
|------|----------|--------------------------------|------------------------------|
| **hit@3（主 KPI）** | 18/35 (51.4%) | **18/35 持平** | **全部 18/35 持平** |
| hit@1 | 8/35 | 11/35 (+3) | 10/35 |
| hit@10 | 20/35 | 22/35 (+2) | 23/35 |
| MRR@10 | 0.368 | 0.418 | 0.409–0.414 |

Per-題翻轉：9/35 題動、全為 ±1-2 名微調；**進 top3 = 0、掉出 top3 = 0**。80q 退化持平驗證（4.1）：v4 空表 db = 75/80 逐題一致 ✓（退化合約真機兌現）。

**根因（比分數更有價值的發現）**：chunk 通道確實把診斷指望的題目從 >10 撈進 4-10（受洗 >10→9、牧師離職 >10→10 — 通道方向正確），但卡 top3 的擋路者是 main 通道自己 rank 1-3 的同主題單篇強文 — **v3 真正卡分的病是「近重複/同主題分票」，不是通道缺失**；任何通道權重都推不過佔位者。判準（v3 hit@3 上升且 80q 不降）未滿足 → 整路 discard；先導只會高估，全量（10h）依判準免跑。

**處置**：code 不上線（本地 branch `chunk-level-vectors` 保留備考古，不 push）；schema 維持 v3、無任何釋出；specs 以 `--skip-specs` 歸檔（合約未生效）。工程副產物中**經實測可信**的部分：v2→v4 chain migration 在真 db 瞬時完成、backfill 差集/續跑/bytewise 等價全綠、退化合約全綠 — 未來若攻分票問題需要 chunk 向量作原料，此 branch 可直接復活。

**下一戰改題**：近重複/同主題分票（候選方向：結果去重/MMR、同主題文叢的 identity 判別）— 先診斷分票的型態再選武器，勿直接開工程。
