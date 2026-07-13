# Tasks: chunk-level-vectors

> 每個 Phase 結尾是 commit boundary；apply 時每個 boundary 後停下向維護者回報（chunking 偏好）。commit gate 一律 = `ruff check` + `ruff format --check` + `pytest`（0.5.0 的 CI format 教訓）。

## Phase 0 — Pre-flight + 成本抽樣

- [ ] 0.1 `pwd` + `git remote -v` 確認 wenji repo、tree clean、main 最新（含 0.5.0）；切 branch `chunk-level-vectors`
- [ ] 0.2 檢查 parity db 存活（`ls -la /tmp/parity_after.db`，不在 → 按 handoff memory 重建法，burn-guard 先確認）；確認本機 ort 1.26（`python -c "import onnxruntime; print(onnxruntime.__version__)"`）
- [ ] 0.3 **backfill 時長抽樣**（D5 防線 / Risk 1）：腳本對 parity db 隨機 500 chunks 逐一單 text encode 計時 → 外推 124k 總時長並記錄；**> 12h 級 → 停下呈報維護者議批次策略**，否則繼續；驗證 = 外推數字記在本檔此行
- [ ] 0.4 v3 + 80q baseline 確認在案：v3 51.4%（`logos/tests/v3a_results_20260710.json`）、80q 75/80（`/tmp/eval_slim_before.json` 或重跑 5 min）

## Phase 1 — D1: chunk_vectors 表設計與 schema v4 in-place migration + ingest 產出

- [ ] 1.1 落實 D1: chunk_vectors 表設計與 schema v4 in-place migration：`schema.sql` 加表定義 + `idx_chunk_vectors_article` + version seed `'4'`；`db.py` `SCHEMA_VERSION="4"` + `initialise_schema` v3 檢測（CREATE 冪等 + stamp）串接既有 v2 步驟成鏈；行為 = 新建 db version=4 含空表（滿足 spec requirement: Schema v4 with chained in-place migration）
- [ ] 1.2 migration tests：fresh v4 / v3→v4（資料保全斷言）/ v2→v4 一次跑到（cache 表消失 + chunk_vectors 出現）/ 非 2、3、4 SchemaError / 讀入口開 v3 不遷移
- [ ] 1.3 `ingest_one` chunk 向量產出：encode 置於 doc embed 同側（unchanged 提早 return 自動對齊）、**encode 原始 chunk 字串（chunk_text_raw 內容）非 tokenized**、content 變更時 DELETE 放**舊 article_id 清理區塊**（:243-245 比照 doc_vectors）、與 chunks_fts 同事務、逐 chunk `encode_batch([text])` 單 text；順手清 dead 變數 `unchanged`（:230/:376，永不觸發）（滿足 spec requirement: Chunk vectors are produced alongside chunk FTS rows）
- [ ] 1.4 ingest tests：fresh 全覆蓋（6 chunks → 6 rows × 1024 fp32）/ unchanged 零 encode（embedder call-count mock）/ changed 6→4 以**總列數 + 舊 id 消失**斷言（防孤兒誤過）
- [ ] 1.5 commit gate 三連 → **commit boundary**

## Phase 2 — D4: backfill 子命令

- [ ] 2.1 落實 D4: ingest 與 backfill 語意 — fast path 對齊 + 單 text encode：`wenji ingest backfill-chunk-vectors --db`（cli/ingest.py 子命令 + ingest/__init__.py 實作）— 差集掃描（chunks_fts LEFT JOIN chunk_vectors WHERE NULL）、逐 chunk 單 text encode、per-article commit、進度 log（change 2 格式）、完成時 `_record_build_environment`（滿足 spec requirement: Existing databases backfill without a rebuild）
- [ ] 2.2 backfill tests：零向量 db → 100% 覆蓋 + 環境戳 / 中斷續跑（mock 60/100 kill → 重跑只 encode 40，call-count 斷言）/ **bytewise 一致性：同 chunk 經 backfill 與 fresh ingest 產出的 vec BLOB 逐位相等**（C2 核心驗收）
- [ ] 2.3 commit gate 三連 → **commit boundary**

## Phase 3 — D2/D3/D6: 檢索側（矩陣快取 + 3-way RRF + 退化）+ doctor

- [ ] 3.1 落實 D3: chunk 矩陣快取與記憶體：`search/vector.py` 加 chunk 矩陣 loader（單份 no-axis、memoize）+ **corpus fingerprint 擴為三元組**（articles COUNT, MAX(indexed_at), chunk_vectors COUNT — Risk 5 的 backfill 失效陷阱）且 **COUNT 容忍缺表計 0**（C3 — doc 通道每 query 跑指紋，v3 db 會先於 chunk loader 撞缺表）；doc 矩陣共用新指紋；tests = 單次建構 / backfill 後失效重建（滿足 spec requirement: Chunk matrix is cached with backfill-aware invalidation）
- [ ] 3.2 落實 D2: 3-way RRF 形狀 — chunk-vector 為獨立第三通道：chunk-vec 檢索（query vec × chunk 矩陣 → top candidate_pool → per-article max cosine → 排名列）+ roll-up 後 axis/excluded 過濾；`rrf_merge` 第三參數（default None = 現行為）+ **分支守衛擴為 `if chunk_signals or chunk_vec_signals`**（W3 — 否則招牌場景被 chunk-BM25 空值閘掉）；`search/__init__.py` pipeline 接線（滿足 spec requirement: Chunk-vector channel joins RRF as an independent third ranking）
- [ ] 3.3 3-way tests：診斷縮影 fixture（BM25 全 miss + chunk cosine top → 3-way 撈回 gold）/ axis 過濾在 roll-up 後 / rrf_merge 雙參數呼叫行為不變（向後相容斷言）
- [ ] 3.4 落實 D6: 退化與可觀測合約（檢索半）：空表/**表不存在（v3 db 唯讀，OperationalError 捕獲回空、不沿用 chunk_bm25_search 的 fail-loud）**/缺 embedder → 通道空列 → 2-way 路徑；tests = 空表 db 檢索與 0.5.0 邏輯逐位一致 + **v3 db（無表）搜尋正常零錯誤 — MUST 用 file-backed（tmp_path）v3 fixture：in-memory db 跳過指紋路徑（vector.py:56-58），用 :memory: 會測綠而 prod 崩** + 部分覆蓋照常無警（滿足 spec requirement: Missing vectors degrade gracefully to two-way behavior）
- [ ] 3.5 doctor 覆蓋率行：`observability/health.py` + report format，**v3 缺表渲染零覆蓋不報錯（W4）**；tests = 全/部分/零三態 + **v3 缺表（file-backed fixture）** + exit code 不受影響（滿足 spec requirement: Doctor reports chunk-vector coverage）
- [ ] 3.6 commit gate 三連 → **commit boundary**

## Phase 4 — D5: G4 實驗（主實驗 + 權重 fallback 對照）

- [ ] 4.1 **退化持平驗證**（先行）：v4 未 backfill 的 parity db 副本跑 80q+r14 → 必須 = 75/80 同 miss 清單（D6 合約的真基準驗證）
- [ ] 4.2a **兩階段 G4 之先導（cheap-first）**：(i) threads 前置驗證 — 同一 chunk 在 `WENJI_ONNX_THREADS=2` vs 8 下 encode，vec **bytewise 比對**（漂移 → 全程鎖 2；一致 → 本機開高加速）；(ii) 注入式 backfill — 只對 v3 35 題的 gold + 各題現有 top-50 候選文章（去重 ~1,500 篇 ≈ 1.2 萬 chunks，<1h）建向量（臨時腳本按 article_id 白名單跑 backfill 差集邏輯）；(iii) 跑 v3 Part A — **偏差方向已知（只會高估）→ 先導無改善 = 呈維護者提前 discard，全量不跑**；有改善 → 4.2b
- [ ] 4.2b **（條件式）parity db backfill 全量**：先清掉先導向量（DELETE 全表）再全量跑（避免混批），threads 按 4.2a 結果；nohup + `caffeinate` 過夜、中斷用續跑；記錄實際時長 + doctor 覆蓋率 100%；實測查詢延遲（before/after 各 20 query 中位數）與 serve RSS（D3 記憶體帳驗證）
- [ ] 4.3 落實 D5: G4 實驗設計 — 主實驗與權重 fallback 對照分開判。主實驗：backfill 後跑 v3 Part A（hit@1/3/10 + MRR）+ 80q+r14；判準 = **v3 hit@3 上升 且 80q 不劣化** → keep，否則整路 revert；per-題翻轉清單記入 change 附錄
- [ ] 4.4 對照實驗：BM25 零訊號時 vector 系權重 ×1.5 / ×2.0 疊加量測（v3 + 80q 同跑）；獨立 keep/discard；結果入附錄
- [ ] 4.5 G4 判決記錄 + 維護者確認 → **commit boundary**（判決為 discard 時：revert 檢索側、保留 schema/backfill 基建與否交維護者裁決）

## Phase 5 — 文件 + PR + 收尾

- [ ] 5.1 CHANGELOG 0.6.0 條目（精簡風格；BREAKING = schema v4）；README pipeline 圖（8 步 → 含 chunk-vec 通道）+ extending.md + doctor 文件同步
- [ ] 5.2 Code Change Self-Review 6 點 + `scripts/audit_release.sh` 存 exit code 判斷（openspec artifacts 用「維護者」）
- [ ] 5.3 push + PR（body 含 G4 兩實驗數據表 + per-題翻轉清單）+ CI 全綠 → 維護者核可後 merge
- [ ] 5.4 tag v0.6.0 push（PyPI trusted publishing）→ PyPI 對帳
- [ ] 5.5 `spectra archive chunk-level-vectors` + memory 更新（roadmap：v3 新分數；topology：0.6.0 升級 + backfill 指引；handoff）
