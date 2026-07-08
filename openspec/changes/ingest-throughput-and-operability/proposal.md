# Proposal: Ingest throughput and operability

## Why

2026-07-08 健檢 B 級 findings + 同日 prod rebuild 實戰痛點：12,090 篇全量 rebuild 在 2-core ARM 上跑 ~9-12 小時，期間 (a) embedding 從未 batch 化（`ingest_one` 每篇餵單元素 list，batch 機制存在但閒置 — 歷史考古證實 batch 是 logos embed-server 時代因大 payload 斷連而撤退，該架構已不存在）；(b) FTS DELETE 對 fresh insert 無條件執行且走全表掃描（EXPLAIN 驗證），成本隨 chunks_fts 增長呈 O(N²)，rebuild 後段實測從 1.1s/篇 掉到 2.9s/篇；(c) 12k 迴圈零進度輸出（9 小時黑盒）；(d) 單篇壞 frontmatter 中止全局（實戰：第 390 篇 crash，8 小時後才發現）；(e) 每次查詢重建 49MB 向量矩陣。logos 母體史提供全套 prior art（resume pattern、progress 公式、diff 補缺 — 見 memory `reference_logos_prior_art`）。

## What Changes

- **Batch embedding**（`ingest/__init__.py` + `ingest/embed.py`）：`ingest_dir` 累積文章按字元預算打包後呼叫 `encode_batch`（內建 32 切分沿用）；長文自動獨行；批次失敗降級逐篇重試。向量數值等價以 gate 驗證（批次化必須是確定性的，保持 rebuild byte-identical 承諾）
- **Fresh-insert 跳過 FTS DELETE**：`ingest_one` 的兩個無條件 DELETE 只在 `existing is not None` 且 content 變更時執行（O(N²) → O(N)）
- **進度輸出**：`ingest_dir` 每 200 篇 `logger.info` 一行 `n/total (%) rate=x/s eta=y min`（logos `5f33c5b` 公式）
- **`--skip-bad` flag**（ingest dir / rebuild）：壞 frontmatter 收集後跳過、結尾列清單 + 非零退出；**預設維持 fail-fast**（fail-loud 哲學不動，營運彈性 opt-in）
- **Resume 揭露**：文件明寫「中斷後用 `wenji ingest dir`（非 rebuild）續跑 — content-hash fast path 已存在」；rebuild CLI help 加提示
- **`PRAGMA synchronous = NORMAL`**（WAL 下安全；12k 次 commit 的 fsync 稅）
- **查詢向量矩陣快取**（`search/vector.py`）：Searcher 常駐時 memoize (N,1024) 矩陣，以 `articles_meta` 的 MAX(indexed_at)+COUNT 做失效指紋

無 BREAKING。eval guard 適用（batch embed + 向量快取都碰檢索路徑：80q baseline before/after + 向量等價驗證，見 `.claude/skills/eval-regression-guard/`）。

## Capabilities

### New Capabilities

- `ingest-operability`: 大語料 ingest 的吞吐、進度可觀測性、故障韌性與續跑規格

### Modified Capabilities

（無）

## Impact

- **Code**: `src/wenji/ingest/__init__.py`、`ingest/embed.py`、`core/db.py`、`search/vector.py`、`cli/{ingest,rebuild}.py`、tests
- **量化預期**：batch embed 2-4x（歷史比例）+ DELETE O(N²) 消除（後段 2.9→~1.1s/篇）→ 全量 rebuild 從 ~9-12hr 壓向 ~2-4hr；查詢延遲去掉隨語料線性成長的最大項
- **驗證成本**：本機 parity db 一次 rebuild（M2 ~20-40 min）+ 80q baseline ×2
- **不在 scope**: C 級 API 減肥（backlog `api-slim-0-5`）；chunk-level vectors（roadmap 武器庫）

---

## G1 審查紀錄（2026-07-08）

**Round 1：FAIL（2 critical / 3 warning）→ 修正 → Round 2 見下**

- C1（D7 多軸快取指紋語意未進 spec）→ 已修：spec requirement 明文「per-axis key + 共用 corpus 指紋 + 接受跨軸過度失效」與理由
- C2（byte-identical 承諾 vs cosine 降級不一致）→ 已修：拆成兩層保證 — run-to-run byte-identity 由確定性打包無條件保證；batch-vs-single 等價是品質 gate，降級時 CHANGELOG 明載（design D1 + spec 同步改寫）
- W1（skip-bad JSON 輸出位置格式）→ 已修：spec scenario 明文 stdout 單行 JSON / stderr logger，沿用既有 CLI 慣例
- W3（打包確定性的檔案迭代基礎）→ 已修：spec 引用既有 `sorted(root.glob())`（`ingest/__init__.py:394` 實查）為明文前提
- W2（resume 例示 --config 不存在）→ **核實為誤報**：`cli/ingest.py:31-36` dir_command 實有 `--config` option，例示為 CLI 層命令，有效

**Round 2：PASS（0 critical / 0 warning）** — 五項逐一覆核 RESOLVED（C2 的兩層保證拆分獲 reviewer 認可為正解；W2 由 reviewer 核實撤回）。Info 一則：spec 引用的行號會隨 Phase 2 重構漂移，apply 時順手更新。

**G2 Coverage**：D1→2.1-2.4、D2→1.1-1.3、D3→3.1、D4→3.2-3.3、D5→3.4、D6→4.1、D7→4.2-4.3、G4 實驗→0.2+5.1-5.3，零缺口；孤立 task 皆為 pre-flight/commit boundary。
