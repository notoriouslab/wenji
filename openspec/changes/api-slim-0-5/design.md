# Design: api-slim-0-5

## Context

wenji 0.4.0 的搜尋 API 帶著三條「存在但判死」的路徑：LLM rewrite（實測傷品質 -3.7pp，prod 永久 disabled）、cross-encoder reranker hook（07-10 error analysis 判決純 ranking miss ≈ 0，無病可治）、ranker_hooks 抽象（零生產呼叫端）。同時 `WenjiConfig.search` 從 loader 建構後無任何生產讀取端 — 使用者寫 `search.alpha` 被靜默忽略。兩件營運債待根治：frontmatter 蓋 directory_map 壓扁 taxonomy（A'）、跨 onnxruntime 版本 cosine ~0.98 靜默劣化無從偵測。

關鍵現況事實（2026-07-11 實測）：
- `connect()` 不驗 schema_version；只有 `initialise_schema()`（ingest/rebuild 入口）驗，mismatch raise `SchemaError`
- web/app.py 全 env 驅動（`WENJI_DB_PATH`/`WENJI_AXES_YAML`/`WENJI_LLM_*` 等 12+ 個），prod 以 `uvicorn` 直起 app factory，不經 wenji CLI
- `config/llm.py` 為 ask/aggregate 與 rewrite 共用；`LLMClient` 必須保留
- `wenji segment`（observability trace）消費 rewrite（`RewriteInfo` 欄位 + CLI flags）
- db.py:75-78 有 in-place 清理先例（pre-v0.4 dead keys DELETE，no version bump）
- 唯一 consumer = logos（無外部 user）；0.5.0 BREAKING 安全

## Goals / Non-Goals

**Goals:**
- 刪除三條判死路徑至 git 歷史可考、工作樹零殘留（含 config 欄位、env 開關、CLI flags、trace 欄位、db 表）
- `search.alpha`/`candidate_pool`/`default_limit` 在三個 Searcher 入口真實生效；預設行為 bit-for-bit 不變
- A' 開關落地（預設 off = 行為不變）；建庫環境版本可記錄、可比對
- 全程過 eval-regression-guard（80q+r14 持平）與 audit_release.sh

**Non-Goals:**
- logos prod 三表 source_type UPDATE 與 classify 重跑（consumer 側配套，logos repo 排程）
- 檢索品質提升（改寫魯棒性是獨立戰場，v3 題集驗證）
- `api_search` demo over-fetch 修復（獨立雜項）
- 任何新檢索功能或參數語意變更

## Decisions

### D1: 判死路徑移除深度 — 連根刪除 + schema v2→v3 in-place migration

| 方案 | 內容 | 取捨 |
|------|------|------|
| A（採用） | rewrite/rerank/hooks 整路刪；schema.sql 移除 `query_rewrite_cache` 表定義；`SCHEMA_VERSION = "3"`；`initialise_schema()` 檢測 v2 時執行 `DROP TABLE IF EXISTS query_rewrite_cache` + UPDATE version（冪等，~5 行） | 誠實記錄 shape 變更；0.4 code 開 v3 db 會 fail-loud（SchemaError）；既有 db 下次 ingest 自動升，**不需 rebuild** |
| B | 刪 code 但留殭屍表、不 bump version（沿 dead-keys 先例） | 零 migration 成本，但先例理由是「same table shape」— 本次整表消失，shape 變了；殭屍表違反刪乾淨精神 |
| C | 0.5 deprecate（警告）、0.6 才刪 | 為不存在的外部 user 付兩版成本；R11 證實無人需要緩衝 |

read 入口（serve/search/doctor）走 `connect()` 不驗 version：0.5 code 開未升級的 v2 db 一切正常（該表已無讀者），db 保持 v2 直到下次 ingest/rebuild 觸發升級 — 升級視窗無服務中斷。

rewrite 刪除邊界：`config/llm.py` 只刪 rewriter-only 部分（`LLMConfig.rewrite_cache_ttl_days` 欄位 + `WENJI_LLM_REWRITE_CACHE_TTL_DAYS` 解析與文件行），`LLMConfig`/`LLMClient` 本體保留（ask/aggregate 消費）。`core/model_download.py` 只刪 reranker 分支（`download_reranker_model`/`RERANKER_MODEL_DEFAULT`），embedder 主模型分支保留。eval 工具面同刪：`eval/__init__.py` 的 `clear_rewrite_cache`（v3 drop 表後是 crash 路徑）、`cli/eval.py` 的 `--clear-cache`/`--enable-rewrite`/`--no-rewrite` flags 與 run 檔 rewrite 標記（run 檔的 pass/miss 對比欄位不變，eval guard before/after 口徑不受影響）。segment 的 human formatter（`cli/_format.py` Rewrite 段）隨 trace 欄位一併刪。刻意保留的同字根識別符：`EntityScorer.score_and_rerank`（entity 層 re-rank，生產 pipeline step 8）與 `web/branding.py` 的 URL-rewrite 註解 — 與 LLM rewrite/cross-encoder 無關，不改名不刪除；殘留驗收因此採精確 symbol 清單而非字根掃描。

### D2: config 傳遞機制 — `WENJI_CONFIG` env + CLI `--config` 優先

| 方案 | 內容 | 取捨 |
|------|------|------|
| A | 只加 CLI `--config` flag | prod 以 uvicorn 直起 app factory，無 CLI 可傳 → web 入口死路 |
| B | 只 env `WENJI_CONFIG` | web 可用，但 CLI 臨時試 config 要 export env，體感差；且 ingest/rebuild 已有 `--config` 慣例 |
| C（採用） | env `WENJI_CONFIG` 為基底；CLI `--config` flag 存在時優先；兩者皆無 → `load_config(None)` = all-defaults | 與 app.py 全 env 慣例一致 + 保留 CLI 慣例；解析順序單一明確 |

落點：web/app.py app factory 讀 env 建 `SearchConfig` 注入 Searcher 構造（`alpha`/`candidate_pool`）與 `/api/search` 的 `limit` default；`cli/search.py` fallback 路徑同；`ask/__init__.py` 的 lazy `Searcher(db, Embedder())` 改接受可選 config（由 web 層傳入，獨立使用時 env）。`cli/ingest.py`/`cli/rebuild.py` 的手工 yaml 解析改呼叫 `load_config(config_path)`（錯誤型別統一為 `ConfigError`）。

行為不變保證：`DEFAULT_SEARCH_CONFIG` 的 0.25/50/10 與現行 hardcoded `DEFAULT_ALPHA`/50/10 逐值相等（已實測），無 config 部署 bit-for-bit 不變；由 eval guard + 單元斷言雙重鎖定。

### D3: doctor 環境漂移檢查 — 資訊層（warn-only，不影響 exit code）

| 方案 | 內容 | 取捨 |
|------|------|------|
| A（採用） | doctor 報告新增 environment 段：建庫記錄 vs 當前 runtime 逐項比對，漂移顯示 `DRIFT` 行；exit code 不受影響 | 漂移是「劣化警示」非「故障」（cosine ~0.98 仍可用）；doctor 的 exit 1 語意保留給資料不一致 |
| B | 漂移 exit 1 | 把可用系統判死太暴力；且會逼營運在升級 runtime 後立刻全量 rebuild |
| C | 漂移 exit 1 + `--allow-env-drift` escape | gate 複雜度不成比例；沒有自動化流程消費這個 fail |

缺 keys 容忍（關鍵邊界）：0.4 建的 db 無環境 keys → 顯示 `not recorded (pre-0.5 db)`，不告警不失敗。否則 0.5 doctor 會對所有既有 db 誤報。

### D4: A' 開關語意 — config bool 欄位 + `derive_source_type` 顯式參數

| 方案 | 內容 | 取捨 |
|------|------|------|
| A（採用） | `WenjiConfig.directory_map_overrides_frontmatter: bool = False`；`derive_source_type()` 加同名 keyword 參數（default False），ingest_one/ingest_dir 從 config 傳入 | 純函數可測；預設 false 時舊測試零改動即過 |
| B | `derive_source_type` 直接收整個 `WenjiConfig` | ingest 底層函數耦合 config 全型別，違反現有「resolved mapping as pure dict」設計註解 |
| C | env 全域開關 | 隱形狀態；config 檔才是 deployment 宣告「目錄結構是真理」的正確位置 |

開關 on 的解析順序：`directory_map[parent]` 命中 → 用 map 值（**蓋掉 frontmatter**）；map 未命中 → fallback frontmatter；兩者皆無 → `IngestError`（不變）。off = 現行順序（frontmatter 優先）。on 且 map 未命中時 fallback 而非 error — 否則 tgc 目錄外的散檔（frontmatter-only）會被誤殺。

### D5: 共用型別與重複碼歸屬 — EmbedderProtocol 入 core、from_sources 入 search 私有模組

- `EmbedderProtocol` 唯一定義移至 `core/protocols.py`（新檔，~10 行）；search/ingest 兩處改 import。放 core 而非任一消費側：search 依賴 ingest（或反向）都是層次倒置，core 是既有共同底層。兩處 `__all__` re-export 保留（import 路徑向後相容不是目標，但 re-export 成本為零且減少 diff 面）。
- `from_sources` 重複 ~15 行（sources list 解析：`example:` prefix 發現 + 路徑載入）抽至 `search/_sources.py` 私有 helper；只有 entity/intent 兩個消費者、皆在 search/ 域內，提升到 core 是過度抽象。

### D6: 建庫環境版本記錄 — 最小兩鍵、批量完成時 upsert

| 方案 | 內容 | 取捨 |
|------|------|------|
| A | `connect()` 時寫 | 讀路徑不該寫；serve 唯讀場景會炸 |
| B | `ingest_one` 每篇寫 | 12k 篇 12k 次冗餘 upsert |
| C（採用） | `ingest_dir`/`rebuild_from_disk` 成功收尾時 `INSERT OR REPLACE` | 語意正確：環境敏感性屬於「這批向量是誰算的」；單次 IO |

Keys（wenji_meta，key/value insert，不動 schema shape）：
- `env_onnxruntime_version` = `onnxruntime.__version__`
- `env_numpy_version` = `numpy.__version__`

不記 embed model hash/名稱：模型由 `model_download` 固定單一來源，版本敏感性實測在 runtime 層（ort 1.26 vs 1.27 cosine ~0.98）而非模型檔。將來換模型是 schema 級事件，到時再加鍵。

部分寫入語意：增量 `ingest_dir` 完成也 upsert（最後一次批量寫入的環境勝出）— 混環境增量本身就是 drift 場景，doctor 會照 D3 顯示；不做 per-article 環境追蹤（YAGNI）。

## Implementation Contract

**可觀察行為（apply 驗收基準）：**

1. **Searcher 簽名**：`Searcher(conn, embedder, *, alpha, candidate_pool, entity_scorer, intent_classifier)` 共 6 參數；傳 `rewriter=`/`reranker=`/`ranker_hooks=` → `TypeError`。pipeline docstring 步驟同步收斂（原 step 1 rewrite、step 9 hooks 移除）。
2. **模組面**：`wenji.search` 不再 export `QueryRewriter`/`CrossEncoderReranker`/`RankerHook`/`apply_ranker_hooks`；`src/wenji/search/{rewrite,rerank,ranker}.py` 三檔不存在；殘留驗收 = 精確 symbol 清單掃描歸零（清單見 spec「repository-wide residue audit by exact symbols」scenario；`score_and_rerank`/branding URL-rewrite 註解為刻意保留，不在清單內）。
3. **CLI 面**：`wenji segment` 輸出 JSON 無 `rewrite` 欄位（human 格式同步，`cli/_format.py`）、`--enable-rewrite`/`--no-rewrite` flags 不存在（typer 未知參數 exit 2）；`wenji eval run-benchmark` 無 `--clear-cache`/`--enable-rewrite`/`--no-rewrite`；`wenji serve` 與 `wenji search` 的 `--enable-rewrite`/`--no-rewrite` flags（typer.Option 本體 + 互斥檢查 + docstring）同刪、stale flag 一律 exit 2 — 四個 CLI 行為對稱；`wenji download` 無 reranker 選項；`wenji search`/`serve` 不讀 `WENJI_REWRITE_OVERRIDE`。
4. **Schema**：新建 db `sqlite_master` 無 `query_rewrite_cache`、`schema_version=3`；對 v2 db 跑 `initialise_schema`（任一 ingest/rebuild 命令）→ 表被 drop、version 升 3、其餘資料完好；v3 之外的舊版本照舊 `SchemaError`。read 入口開 v2 db 不觸發遷移、功能正常。
5. **Config 生效**：`WENJI_CONFIG` 指向含 `search.alpha: 0.9` 的 yaml → serve/search/ask 的檢索行為實際改變（單元層斷言 Searcher 收到 0.9）；CLI `--config` 優先於 env；兩者皆無 → 行為與 0.4.0 逐位一致（eval guard 80q+r14 before/after 持平為證）。
6. **Config 入口統一**：`cli/ingest.py`/`cli/rebuild.py` 無手工 `yaml.safe_load`；壞 yaml 的錯誤訊息與 `load_config` 的 `ConfigError` 一致。
7. **A' 開關**：yaml `directory_map_overrides_frontmatter: true` + directory_map 命中 → source_type 取 map 值（frontmatter 被蓋）；map 未命中 → frontmatter；預設/未設 → 現行為（既有 frontmatter 優先測試全數不改而過）。
8. **環境記錄**：`ingest dir`/`rebuild` 成功結束 → `wenji_meta` 含 `env_onnxruntime_version`/`env_numpy_version` 且值 = 當時 runtime；中途 crash 不寫。
9. **Doctor**：報告含 environment 段；三態 — 一致（`ok`）/漂移（`DRIFT`，exit code 不變）/未記錄（`not recorded (pre-0.5 db)`，非告警）。
10. **Gate**：`scripts/audit_release.sh` exit 0；`pytest` 全綠；eval-regression-guard 流程完整執行並留檔。

**Scope 邊界**：in = 上述 1-10 + CHANGELOG 0.5.0 條目（精簡風格）+ README/docstring 同步；out = logos repo 一切（prod UPDATE、env 清理、pip 升級）、新檢索功能、demo over-fetch。

## Risks / Trade-offs

- [接上 config 後 eval 環境被本機殘留 `WENJI_CONFIG` 污染 → 分數漂移誤判回歸] → eval guard 跑法明文 `unset WENJI_CONFIG`（或顯式空值）；before/after 同 shell 環境
- [刪 rewrite 後 logos prod 殘留 `WENJI_REWRITE_OVERRIDE` env] → 無害（無讀者）；logos deploy 文件除役條目記在 consumer 側配套（out of scope，但 CHANGELOG BREAKING 條目點名）
- [v2→v3 migration 寫在 `initialise_schema`，read 入口不觸發 → db 長期停在 v2] → 可接受：該表無讀者，v2 停留無功能影響；下次任何 ingest 自然收斂
- [`segment` trace 欄位移除破壞外部 trace 消費者] → R11：無外部 user；logos 不消費 segment trace
- [A' 開關 on + map 未命中 fallback frontmatter 的語意被誤解為「map 永遠贏」] → spec scenario 明文三分支；config 欄位 docstring 寫解析順序
- [`from_sources` 抽共用時兩處有隱性行為差異（逐行相同是健檢聲稱）] → apply 時先 diff 兩段確認逐行相同再抽；有差異則停下按 spec-drift 流程呈報

## Migration Plan

1. wenji 0.5.0 tag push → trusted publishing 自動上 PyPI（0.4.0 已驗證的 pipeline）
2. logos 側（out of scope，僅記順序）：pip 升級 → prod db 下次 ingest 自動 v3 → 擇期 A' 開關 + 三表 UPDATE + classify 重跑
3. 回退策略：pip 釘回 0.4.0 即回退；若 db 已升 v3，0.4.0 的 read 入口不驗 version 照常可用（rewrite cache 表缺失無礙 — 0.4 prod 的 rewrite 本就 disabled），僅 0.4 `initialise_schema` 會 fail-loud 擋 ingest（預期行為）

## Open Questions

（無 — O1/O2/O3 已由主公 2026-07-11 拍板；D1-D6 本文件定案，G1 審查後如有翻案再回此節記錄）
