# Proposal: API slim for 0.5

## Why

2026-07-08 健檢 C 級 findings 揭露 config 信任缺口（使用者在 wenji.yaml 寫 `search.alpha` 被靜默忽略，docstring 卻寫得像會生效），加上 2026-07-10 error analysis 判決兩條功能路徑死刑：reranker 無病可治（純 ranking miss ≈ 0）、LLM rewrite 實測傷害品質（73.8% vs 77.5%，prod 已永久 disabled）。同時兩件營運債有了根治方案：tgc 類 frontmatter 蓋掉 directory_map 造成 axes taxonomy 壓扁（07-10 維護者核准 A' 開關）、跨 onnxruntime 版本向量 cosine ~0.98 的靜默檢索劣化（無環境記錄可偵測）。0.5.0 一次收攏：刪掉判死的、接上該生效的、補上該記錄的。

## What Changes

### ① 搜尋 API 減肥（BREAKING）

- **rewrite 整路刪除**：`search/rewrite.py`、`Searcher(rewriter=)` 參數、`cli/search.py` 與 `cli/serve.py` 的 rewrite 接線含各自的 `--enable-rewrite`/`--no-rewrite` typer flags、`web/app.py` 接線與 `WENJI_REWRITE_OVERRIDE` env、`/api/search` response 的 `rewritten_query` 欄位（web API BREAKING，logos 前端若有讀取需同步）、`wenji segment` 的 rewrite trace 欄位與 `--enable-rewrite`/`--no-rewrite` flags（`observability/segment.py` + `cli/segment.py` + `cli/_format.py` human 輸出段）、**eval 側 rewrite 工具面**（`eval/__init__.py` 的 `clear_rewrite_cache` + `cli/eval.py` 的 `--clear-cache`/`--enable-rewrite`/`--no-rewrite` flags 與 run 檔 rewrite 標記 — v3 drop 表後 `--clear-cache` 是 crash 路徑，必須同刪）、`SearchConfig.rewrite` 子欄、`config/llm.py` 的 rewriter-only 部分（`rewrite_cache_ttl_days` + `WENJI_LLM_REWRITE_CACHE_TTL_DAYS`；`LLMClient` 本體保留 — ask/aggregate 仍用）
- **BREAKING（schema v2→v3）**：DROP `query_rewrite_cache` 表（schema.sql 表 7），`initialise_schema` 做冪等 in-place migration（先例：db.py:76 dead-keys 清理），既有 db 不需 rebuild
- **reranker 整路刪除**：`search/rerank.py`（144 行）、`Searcher(reranker=)` 參數、`core/model_download.py` rerank 模型分支（embedder 主模型分支保留）、`cli/download.py` rerank 選項、`SearchConfig.rerank` 子欄。判決依據：07-10 error analysis 純 ranking miss ≈ 0（memory `project_search_quality_roadmap`）
- **ranker_hooks 刪除**：`search/ranker.py` 全檔（`RankerHook` Protocol + `ChunkHitBooster`，零生產呼叫端）、`Searcher(ranker_hooks=)` 參數、pipeline step 9
- **classify_intent 方法刪除**：`search/intent.py:65-84`（僅測試驅動；`detect_intent`/`get_boost_types` 有生產用，保留）
- **BREAKING**：`Searcher.__init__` 9 參數 → 6（`conn, embedder, alpha, candidate_pool, entity_scorer, intent_classifier`）

### ② config 誠實化

- 三個 `Searcher()` 呼叫端（`web/app.py`、`ask/__init__.py`、`cli/search.py`）真讀 `load_config()` 的 `search.alpha`/`candidate_pool`/`default_limit`。預設值與現行 hardcoded 相同（0.25/50/10，已實測 `DEFAULT_SEARCH_CONFIG` ≡ `DEFAULT_ALPHA` 等），無 config 檔部署行為不變
- `cli/ingest.py:48-53` 與 `cli/rebuild.py:27-31` 的重複 yaml 讀取改走 `load_config()`（單一 config 入口）

### ③ source_type 決定權開關（A'，維護者 07-10 核准）

- `WenjiConfig` 新增 `directory_map_overrides_frontmatter: bool = False`；`derive_source_type`（`ingest/frontmatter.py:48`）在開關開啟時 directory_map 優先於 frontmatter。**預設 false = 現行為完全不變（opt-in）**。動機：tgc/christianitytoday 5,343 篇 frontmatter 自帶 `teaching` 壓扁 axes taxonomy

### ④ 建庫環境版本記錄

- ingest/rebuild 寫 `wenji_meta` 環境 keys（onnxruntime / numpy 版本兩鍵；key/value insert，不動 schema version）
- `wenji doctor` 比對當前 runtime 與建庫環境，漂移時告警（動機：跨 ort 版本 cosine ~0.98，memory `reference_embedder_env_sensitivity`）

### 去重小項（非 BREAKING）

- `from_sources` 重複 ~15 行（`entity.py:305` / `intent.py:114`）抽共用 helper
- `EmbedderProtocol` 雙份定義（`search/__init__.py:34` / `ingest/__init__.py:37`）合併為一份

## Capabilities

### New Capabilities

- `search-api-surface`: Searcher 建構合約（6 參數）、config.search 生效行為、rewrite/rerank/ranker_hooks 移除後的 pipeline 形狀
- `source-type-resolution`: `derive_source_type` 的優先權規則與 `directory_map_overrides_frontmatter` 開關語意
- `db-provenance`: wenji_meta 建庫環境記錄 keys 與 doctor 漂移比對行為

### Modified Capabilities

（無 — 既有五個 specs 均不覆蓋上述行為域）

## Impact

- **Code**: `src/wenji/search/`（rewrite.py/rerank.py/ranker.py 刪除、__init__.py 收斂、intent.py/entity.py 去重）、`config/`（loader/defaults/llm）、`cli/`（search/serve/segment/_format/download/eval/ingest/rebuild）、`eval/__init__.py`、`web/app.py`、`ask/__init__.py`、`observability/segment.py`、`core/`（schema.sql/db.py/model_download.py）、對應 tests
- **版本**: 0.5.0（BREAKING 集中一版）；唯一 consumer = logos，一次升級（無外部 user，R11）
- **驗證**: 檢索行為改動必過 `eval-regression-guard`（80q+r14 before/after 於本機 parity db；v3 永不調參）；公開 repo，commit 前 `scripts/audit_release.sh` 檢查 exit code
- **不在 scope**: logos prod 三表 source_type UPDATE 與 classify 重跑（A' 的 consumer 側配套，logos repo 工作）；api_search demo over-fetch 小修（獨立雜項）
- **Backlog drift 記錄**: backlog 寫 Searcher「現 11 參數」實測 9；backlog 提「extras 依賴」實測 pyproject 無 rerank extras — 均按當日實測為準

---

## G1 審查紀錄（2026-07-11）

**Round 1：FAIL（1 critical / 2 warning）→ 修正**

- C1（config 側 rerank 刪除是孤兒任務，會撞 2.5 audit）→ 已修：task 2.1 補 `loader.py` `RerankConfig`+`SearchConfig.rerank`+`_build_search` 區塊+docstring、`defaults.py` `"rerank"` 鍵，與 1.5 對稱
- W1（proposal ④ 寫三鍵與 D6 兩鍵矛盾）→ 已修：統一為 onnxruntime/numpy 兩鍵
- W2（llm.py 欄位名不精確 + test_llm_config.py 未點名）→ 已修：精確為 `LLMConfig.rewrite_cache_ttl_days`，1.4 明列 test 更新

**Round 2：FAIL（新 2 critical / 1 warning，reviewer 全樹模擬 audit 揭露）→ 修正**

- C2（audit 字根掃描撞保留路徑 `score_and_rerank`/branding URL-rewrite 註解，驗收式不可滿足）→ 已修：改精確 symbol 清單（最終 18 個，含後補的 rewritten_query）（spec scenario + task 2.5 全集 + 1.7 子集三處一致），保留識別符 spec 明文列出
- C3（eval 側 rewrite 面完全未列舉，`--clear-cache` 在 v3 db 是 crash 路徑）→ 已修：proposal ①/Impact、design D1、spec requirement+scenario、新 task 1.3b 四層補齊；明文 eval guard 對比口徑不受影響
- W3（`cli/_format.py` Rewrite 段漏列，會 KeyError）→ 已修：task 1.3 補 _format.py 與 observability re-export
- 作者自查追加：`/api/search` response `rewritten_query` 欄位（web/app.py:808 實證）為未列舉 web API BREAKING → proposal/spec scenario/task 1.2/audit 清單四處補齊

**Round 3：PASS（fresh reviewer，Critical 0 / Warning 1）** — 五項修正全數獨立實證 RESOLVED（三份 symbol 清單逐字一致、audit 實跑 30 檔全在 Impact 面內、`score_and_rerank`/branding 零誤中）；判語「可進 apply」。
- W4（serve/search 的 `--enable-rewrite`/`--no-rewrite` typer flags 四層未明文列舉，半刪殘留風險）→ 已修：task 1.2 明列兩命令 flags+互斥檢查+docstring 刪除項與 stale-flag exit 2 驗證、spec 補「serve and search reject stale rewrite flags symmetrically」scenario、design contract#3 與 proposal ① 同步。`spectra analyze` 四維全綠。

**G2 Coverage**：D1→Phase 1(1.1-1.7)+Phase 2(2.1-2.5)、D2→3.1-3.3、D3→4.4、D4→4.1-4.2、D5→3.4、D6→4.3；六 Risk 全數有 mitigation task（WENJI_CONFIG 污染→0.3/5.1 unset、from_sources 差異→3.4 先 diff 停、logos env 殘留→5.2 點名）；孤立 task 皆為 pre-flight/commit boundary/收尾 infra。Type Check：開關名/env keys/協定路徑/版本字串/6 參數清單跨檔一致。零缺口。
