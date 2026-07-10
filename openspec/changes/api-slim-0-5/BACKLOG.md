# Backlog scaffold: `api-slim-0-5`（健檢三包之三）

> Status: Backlog，非 propose-ready。2026-07-08 健檢 C 級 findings 的決策暫存，等 change 1（web-concurrency-hardening）、change 2（ingest-throughput-and-operability）完成後開案。

## 已驗證的 findings（2026-07-08 三鏡片健檢，簡化組報告）

1. **config.search 信任缺口（最優先）**：`WenjiConfig.search`（alpha/candidate_pool/rerank/rewrite）無任何生產讀取端 — 使用者在 wenji.yaml 寫 `search.alpha` 被靜默忽略，docstring 卻寫得像會生效。決策：接上（真讀 config）vs 刪除欄位 + docstring 改寫。同案：`cli/ingest.py:48-53` 與 `cli/rebuild.py:27-31` 繞過 `load_config()` 重複實作 yaml 讀取。
2. **ranker_hooks 抽象**（`search/ranker.py` 62 行）：唯一實作 `ChunkHitBooster`、無生產呼叫端。決策：合併進 RRF 步驟、砍 Protocol。
3. **classify_intent**（`search/intent.py:65-84`）：無人消費回傳值，僅單元測試驅動。
4. **from_sources 重複**（entity.py:304-332 / intent.py:118-145 逐行相同 ~15 行）→ 抽共用 helper。
5. **EmbedderProtocol 雙份定義**（search/__init__.py:34-37 / ingest/__init__.py:37-40）→ 留一份於 core/。

## 緩議（有明確解鎖條件）

- **reranker 整路刪除**（`search/rerank.py` 144 行 + model_download 分支 + CLI）：與 search-quality roadmap 衝突 — reranker 是 ranking-miss 的首選武器。**解鎖條件：18 題 error analysis 判決**（見 memory `project_search_quality_roadmap`）。ranking-miss 主導 → 插真模型（bge-reranker-v2-m3 INT8，先實測 2-core ARM latency）；否則 → 刪整路含 extras 依賴。

## 開放決策

- D1：config.search 接上 vs 刪（傾向：刪 rerank/rewrite 子欄、接上 alpha/candidate_pool — 但等 reranker 判決一起定）
- D2：`Searcher.__init__` 目標參數數（現 11 → 簡化組估 7-8）；哪些進 0.5.0 BREAKING 清單
- D3：0.5.0 版本邊界：API 減肥是否與 chunk-vectors（roadmap 武器 2，若選中）同版

## 觸發

`spectra new change api-slim-0-5` 時以本檔 + 健檢報告（session 2026-07-08）為輸入。
- **記錄建庫環境版本**（2026-07-10 新增）：wenji_meta 記 onnxruntime/numpy 版本 + doctor 比對告警 — 起因：實測跨 ort 版本向量 cosine 僅 ~0.98（見 memory reference_embedder_env_sensitivity），「升級 runtime 不重建 db」是靜默檢索劣化源


- **A' — directory_map 優先權開關（2026-07-10 排入，維護者核准）**：wenji config 加 `directory_map_overrides_frontmatter` 選項（deployment 得以宣告「目錄結構是 source_type 的真理」）。起因：tgc/christianitytoday 5,343 篇 frontmatter 自帶 `teaching` 蓋掉 directory_map 細分 → axes taxonomy 壓扁（theology 巨桶 59%、practice/public-discourse 空殼）。配套：prod 三表 source_type UPDATE（articles_meta / articles_fts / chunks_fts 皆 denormalized）或重 rebuild，之後重跑 classify。C 快修（logos axes.yaml fallback 規則，commit 7f8a5ab）已同日先行，unclassified 2,132 → 預期 ~100。
