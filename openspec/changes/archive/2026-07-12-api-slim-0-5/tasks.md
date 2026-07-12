# Tasks: api-slim-0-5

> 每個 Phase 結尾是 commit boundary；apply 時每個 boundary 後停下向維護者回報（chunking 偏好）。

## Phase 0 — Pre-flight + eval before 基準

- [x] 0.1 `pwd` + `git remote -v` 確認 wenji repo、tree clean、main 最新；切 branch `api-slim-0-5`
- [x] 0.2 檢查本機 parity db 存活：`ls -la /tmp/parity_after.db`；不在 → 按 handoff memory 重建法重建（`rsync oracle:~/logos/articles/` + `wenji rebuild`，~6.5h，走 burn-guard 流程先跟維護者確認再點火）
- [x] 0.3 eval **before** 基準（0.4 code、main HEAD）：`unset WENJI_CONFIG` → serve parity db → `wenji eval run-benchmark --snapshot <logos>/tests/benchmark_80_v2_gold_r14.json --port 8765`；預期 75/80，run 檔存 `/tmp/eval_slim_before.json`（eval-regression-guard 之 before 半場；驗證 = run 檔存在且 pass 數記錄在案）

## Phase 1 — D1: 判死路徑移除深度 — 連根刪除 + schema v2→v3 in-place migration（前半：rewrite）

- [x] 1.1 刪 `src/wenji/search/rewrite.py`；`search/__init__.py` 移除 `QueryRewriter` import/export 與 pipeline step 1（`effective_query = query` 直通）、`Searcher(rewriter=)` 參數刪除；行為 = `from wenji.search import QueryRewriter` raises ImportError（部分滿足 spec requirement: Removed retrieval paths leave no runtime trace）
- [x] 1.2 呼叫端清理：`web/app.py`（rewriter 建構塊 + `WENJI_REWRITE_OVERRIDE` + `/api/search` 的 `rewritten_query` 計算塊與 response 欄位）、`cli/serve.py`（`--enable-rewrite`/`--no-rewrite` typer flags + 互斥檢查 + env 寫入 + state 顯示 + docstring — 勿只刪 env 行留死 flag）、`cli/search.py`（`--enable-rewrite`/`--no-rewrite` flags + `force_enable_rewrite`/`force_no_rewrite` 參數 + rewriter fallback 建構）；刪 `tests/wenji/test_search_rewrite_wiring.py`；行為 = serve/search 不再讀 `WENJI_REWRITE_OVERRIDE`、`wenji serve --no-rewrite` 與 `wenji search "q" --no-rewrite` 均 exit 2（unknown option，與 segment/eval 對稱）、`/api/search` payload 無 `rewritten_query` key（web API BREAKING，CHANGELOG 列）；驗證 = 更新後 web/search tests 綠 + response keys 斷言 + 兩命令 stale-flag exit 2 斷言
- [x] 1.3 segment 去 rewrite：`observability/segment.py`（`RewriteInfo` + trace 欄位）、`observability/__init__.py`（`RewriteInfo` re-export）、`cli/segment.py`（`--enable-rewrite`/`--no-rewrite` flags 與 rewriter 分支）、`cli/_format.py`（human 輸出的 Rewrite 段 — 漏刪會 `KeyError`）；行為 = `wenji segment "馬丁路德的神學"` JSON 與 human 兩種輸出皆無 rewrite 內容、`--no-rewrite` exit 2（滿足 spec requirement: segment trace drops rewrite instrumentation）；驗證 = 更新後的 segment tests + 手動跑一次斷言兩種輸出
- [x] 1.3b eval 側 rewrite 工具面刪除：`eval/__init__.py` 刪 `clear_rewrite_cache`（v3 drop 表後的 crash 路徑）、`cli/eval.py` 刪 `--clear-cache`/`--enable-rewrite`/`--no-rewrite` flags 與 run 檔 rewrite 標記（`rewrite_enabled` 欄位、run_id 後綴）；刪 `tests/wenji/test_eval_baseline_rewrite.py`；行為 = `wenji eval run-benchmark --clear-cache` exit 2（unknown option）、run 檔 pass/miss 對比欄位不變（eval guard before/after 口徑不受影響）；驗證 = eval tests 更新後綠 + `--help` 無 rewrite/cache 字樣
- [x] 1.4 `config/llm.py` 刪 rewriter-only 部分（`LLMConfig.rewrite_cache_ttl_days` 欄位 + `WENJI_LLM_REWRITE_CACHE_TTL_DAYS` 解析與文件行）；`LLMConfig`/`LLMClient` 本體保留；同步刪 `tests/wenji/test_llm_config.py` 的 ttl 斷言；行為 = ask/aggregate 路徑不受影響；驗證 = 既有 aggregate/ask tests 綠 + llm config tests 更新後綠
- [x] 1.5 config 欄位：`loader.py`/`defaults.py` 刪 `RewriteConfig` + `SearchConfig.rewrite`；驗證 = config tests 更新後綠
- [x] 1.6 Schema v3（design D1）：`schema.sql` 刪 `query_rewrite_cache` 表定義；`db.py` `SCHEMA_VERSION = "3"` + `initialise_schema` v2 檢測 → `DROP TABLE IF EXISTS query_rewrite_cache` + UPDATE version（冪等）；行為 = 新建 db 無該表且 version=3；v2 db 走寫入口自動升、資料完好；v2 db 走讀入口（connect only）不遷移照常服務（滿足 spec requirement: Schema v3 removes the rewrite cache with in-place migration）
- [x] 1.7 tests：v3 三情境（fresh / v2-upgrade 含資料保全斷言 / 非 2、3 版本 SchemaError）+ rewrite 專項殘留掃描（精確 symbol 子集）：`rg "QueryRewriter|WENJI_REWRITE_OVERRIDE|WENJI_LLM_REWRITE_CACHE_TTL_DAYS|rewrite_cache_ttl_days|RewriteConfig|RewriteInfo|clear_rewrite_cache|rewritten_query" src/ tests/` = 0 hits（`score_and_rerank`/branding URL-rewrite 註解為刻意保留；`query_rewrite_cache` 表名在 db.py migration 與 schema.sql 歷史註解為必要引用，均不在清單）
- [x] 1.8 `ruff check` + `pytest` 全綠 → **commit boundary**（訊息含 BREAKING 標記）

## Phase 2 — D1 後半：reranker + ranker_hooks + classify_intent 刪除，Searcher 簽名收斂

- [x] 2.1 刪 `src/wenji/search/rerank.py`；`search/__init__.py` 移除 `CrossEncoderReranker` import/export、`Searcher(reranker=)` 參數與 pipeline 分支；`core/model_download.py` 刪 `download_reranker_model`/`RERANKER_MODEL_DEFAULT`（embedder 分支保留）；`cli/download.py` 刪 reranker 選項；config 側對齊 1.5：`loader.py` 刪 `RerankConfig` + `SearchConfig.rerank` + `_build_search` rerank 區塊 + docstring yaml 範例、`defaults.py` 刪 `"rerank"` 鍵；行為 = `wenji download --help` 無 reranker 字樣
- [x] 2.2 刪 `src/wenji/search/ranker.py`；`search/__init__.py` 移除 `RankerHook`/`apply_ranker_hooks` export、`Searcher(ranker_hooks=)` 參數與 pipeline step 9
- [x] 2.3 刪 `IntentClassifier.classify_intent`（`detect_intent`/`get_boost_types` 保留）+ 對應 tests 刪除；行為 = 生產 intent boost 路徑不變
- [x] 2.4 Searcher 簽名終態 = 6 參數（`conn, embedder, *, alpha, candidate_pool, entity_scorer, intent_classifier`），docstring pipeline 步驟收斂重編號；tests 斷言 `rewriter=`/`reranker=`/`ranker_hooks=` → TypeError（滿足 spec requirement: Searcher construction contract is six parameters）
- [x] 2.5 殘留總掃描（精確 symbol 全清單，對齊 spec scenario）：`rg "QueryRewriter|CrossEncoderReranker|RankerHook|apply_ranker_hooks|ranker_hooks|ChunkHitBooster|WENJI_REWRITE_OVERRIDE|WENJI_RERANKER_DIR|WENJI_LLM_REWRITE_CACHE_TTL_DAYS|rewrite_cache_ttl_days|download_reranker_model|RERANKER_MODEL_DEFAULT|RewriteConfig|RerankConfig|RewriteInfo|clear_rewrite_cache|rewritten_query" src/ tests/` = 唯一命中為 test_search_searcher.py 的 removed-keyword 合約測試（其字面量點名被拒參數屬必要；滿足 spec requirement: Removed retrieval paths leave no runtime trace）
- [x] 2.6 `ruff check` + `pytest` 全綠 → **commit boundary**（BREAKING 標記）

## Phase 3 — D2 config 接上 + D5 去重歸位

- [x] 3.1 落實 D2: config 傳遞機制 — `WENJI_CONFIG` env + CLI `--config` 優先：web/app.py factory 讀 `WENJI_CONFIG` env 建 config、Searcher 構造帶 `alpha`/`candidate_pool`、`/api/search` limit default 用 `default_limit`；`cli/search.py` 加 `--config`（flag > env > defaults）；`--limit`/web `limit` query param 改 sentinel（未顯式提供時 fallback config `default_limit`，顯式值永遠優先）；`ask/__init__.py` lazy Searcher 接受可選 config；行為 = yaml `search.alpha: 0.9` 時 Searcher.alpha == 0.9（滿足 spec requirement: search config takes effect at every Searcher entry point）
- [x] 3.2 tests：三入口注入斷言（unit 層 mock env/flag，斷言 Searcher 收到值）、flag 蓋 env、無 config = defaults 逐值等於 0.4 hardcoded（`DEFAULT_ALPHA`/50/10 斷言鎖定）
- [x] 3.3 `cli/ingest.py` + `cli/rebuild.py` 手工 yaml 解析改 `load_config(config_path)`；行為 = 三命令壞 yaml 同 `ConfigError` 訊息（滿足 spec requirement: CLI config parsing has a single entry point）；驗證 = 壞 yaml fixture test 跨命令斷言
- [x] 3.4 落實 D5: 共用型別與重複碼歸屬 — EmbedderProtocol 入 core、from_sources 入 search 私有模組：`EmbedderProtocol` 唯一定義移 `core/protocols.py`，search/ingest 改 import（re-export 保留）；`from_sources` 兩段先 `diff` 確認逐行相同（**有差異 → 停，spec-drift 流程呈報維護者**）再抽 `search/_sources.py`；驗證 = `rg "class EmbedderProtocol" src/` 恰 1 hit、既有 entity/intent tests 綠
- [x] 3.5 `ruff check` + `pytest` 全綠 → **commit boundary**

## Phase 4 — D4 A' 開關 + D6 環境記錄 + D3 doctor 漂移

- [x] 4.1 落實 D4: A' 開關語意 — config bool 欄位 + `derive_source_type` 顯式參數：`WenjiConfig.directory_map_overrides_frontmatter: bool = False`（loader 解析頂層 key）+ `derive_source_type` 同名參數，ingest_one/ingest_dir 傳入；行為 = 預設 false 現行為不變（既有 frontmatter-first tests 零改動全綠，滿足 spec requirement: Default resolution order is frontmatter first）
- [x] 4.2 A' tests：開關 on 三分支（map 命中蓋 frontmatter / map 未命中 fallback frontmatter / 兩無 IngestError）+ tgc example 情境（滿足 spec requirement: Deployment can declare directory structure as source of truth）
- [x] 4.3 落實 D6: 建庫環境版本記錄 — 最小兩鍵、批量完成時 upsert：`ingest_dir`/`rebuild_from_disk` 成功收尾 `INSERT OR REPLACE` `env_onnxruntime_version`/`env_numpy_version`；行為 = 成功跑完 meta 有兩鍵、中途 crash 不寫（滿足 spec requirement: Bulk ingest records the build environment）；tests = 成功寫入 + mock crash 不寫 + 增量覆蓋
- [x] 4.4 落實 D3: doctor 環境漂移檢查 — 資訊層（warn-only，不影響 exit code）：`observability/health.py` + `cli/doctor.py` 加 environment 比對，三態輸出（ok / DRIFT / not recorded (pre-0.5 db)），exit code 純由 consistency 決定；tests = 三態各一 + DRIFT 時 exit 0 斷言（滿足 spec requirement: Doctor reports environment drift without failing）
- [x] 4.5 `ruff check` + `pytest` 全套全綠 → **commit boundary**

## Phase 5 — eval guard after + 文件 + PR + 收尾

- [x] 5.1 eval **after**：同 Phase 0.3 環境（`unset WENJI_CONFIG`、同 parity db、branch code）重跑 80q+r14；預期 = 與 before 完全一致（75/80、miss 清單相同）；劣化 → 停下按 G3 Auto-Retry 排查；run 檔 `/tmp/eval_slim_after.json`，before/after 對比記錄進 PR（eval-regression-guard 完整履約；v3 題集不跑不調 — 鐵律）
- [x] 5.2 CHANGELOG 0.5.0 條目（公開 OSS 精簡風格 1-2 句/條，BREAKING 集中列：Searcher 簽名、schema v3、rewrite/rerank 移除、`WENJI_REWRITE_OVERRIDE` 除役、segment 與 eval 的 rewrite flags 移除）；README/docstring 同步（config 生效說明 + `WENJI_CONFIG`、A' 開關、doctor 環境段）
- [x] 5.3 Code Change Self-Review 6 點（重複定義 ruff F811、patch 副產物、重構殘留 rg、一致性、安全微清單、`git diff` 全讀）+ `scripts/audit_release.sh` **存 exit code 判斷**（不接 pipe）
- [x] 5.4 push + PR（body 含 before/after eval 對比）+ CI 全綠 → 維護者核可後 merge
- [x] 5.5 `spectra archive api-slim-0-5` + memory 更新（handoff：健檢三包 3/3 完結；topology：0.5.0 升級注意事項 — logos pip 升級 + env 清理 + A' 開關擇期）
