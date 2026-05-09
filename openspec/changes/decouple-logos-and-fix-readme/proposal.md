# Proposal: Decouple logos branding and fix README quickstart

## Why

wenji 即將以開源形式釋出，但目前 repo 仍綁有原 production app（logos）的痕跡：對外 URL 寫死 `logos.jacobmei.com`、CORS 預設值僅服務 logos、CLI 提供僅一名使用者用得到的 `from-logos-db` adapter。同時新 README 草稿（`20260509_README.md`）的 quickstart 命令在 v0.3.1 已被移除（legacy `wenji ingest <path>` 形式），照貼會立即報錯。三份專家審查（中立稽核 / 紅隊資安 / 邊界測試）共指出 12 項 README 必修 + 5 項 logos hardcoded 風險，必須在開源公告前修補。

## What Changes

### A 類 — 對外品牌/SEO/CORS（env-driven 重構 + 安全驗證）

- **新增 env vars**：`WENJI_SITE_URL`、`WENJI_SITE_NAME`、`WENJI_OG_IMAGE_URL` —— 控制 JSON-LD、canonical、og:* meta 是否輸出
- **預設行為改變**：unset 時模板省略所有 SEO meta（不再洩漏品牌）；set 時用該值
- **URL host 白名單驗證**：`WENJI_SITE_URL` 與 `WENJI_OG_IMAGE_URL` 啟動時 hard reject userinfo、私有/loopback/link-local IP、IDN homograph、控制字元（CRLF）、非預設 port —— 防 SSRF / 開放重定向 / robots.txt CRLF injection
- **Context-aware output escape**：模板渲染品牌值時依輸出脈絡 escape：JSON-LD 用 `\uXXXX`、HTML attribute 用 `|e`、`WENJI_SITE_NAME` 字元集限制（拒 `< > " ' \r \n \x00`，長度 ≤ 256）—— 防 stored XSS
- **CORS 預設改 empty + 多層驗證**：`WENJI_CORS_ORIGINS` 預設 `[]`（從 `https://logos.jacobmei.com` 改）；拒 `*`、`null`、空元素、wildcard subdomain、非 https origin（除 `WENJI_ALLOW_HTTP_CORS=1` dev override）
- **robots.txt / llms.txt / sitemap.xml**：unset `WENJI_SITE_URL` 時 robots 回 conservative deny、sitemap/llms 回 404；不再寫死 logos URL
- **BREAKING**：既有依賴預設 CORS 連 `logos.jacobmei.com` 的部署需顯式設 `WENJI_CORS_ORIGINS`（僅主公自己的 logos 部署受影響）

### B1 類 — 刪除 logos.db adapter

- **BREAKING（移除）**：`wenji ingest from-logos-db` CLI subcommand
- **移除檔案**：`src/wenji/ingest/loader_logos_db.py`、`tests/wenji/test_loader_logos_db.py`
- 理由：僅一名使用者（主公），對外開源無價值；私下另存即可

### B2 類 — eval sanity-eyeball 通用化

- **重命名 CLI flag**：`wenji eval sanity-eyeball --logos-r13` → `--baseline-output`
- **重命名欄位**：`logos_top5` → `baseline_top5`、`logos_data` → `baseline_data` 等
- **新增 baseline JSON 安全驗證**：schema 驗證、檔案大小上限 10MB、單 string 上限 64KB、stdout 印出前 strip 控制字元（防 OOM、log injection）
- **保留功能**：A/B 比對機制、客觀 overlap、eyeball sampling 邏輯不動
- **BREAKING**：CLI flag 改名（公開未發行 → 影響可控）

### B3 類 — eval metadata key + 檔名 in-place 重命名

- **重命名檔案**：`src/wenji/eval/loader_logos_v2.py` → `src/wenji/eval/loader_benchmark_v2.py`（含對應 test 檔名）
- **重命名 dataclass field**：`SnapshotMetadata.logos_source_commit` → `SnapshotMetadata.source_commit`
- **重命名 JSON key**：`tests/benchmark_80_v2_snapshot.json` 中的 `logos_source_commit` → `source_commit`
- **重命名描述字串**：`snapshot_source_path: "tests/benchmark_80.json (logos repo)"` → 通用描述（如 `"upstream benchmark v2 80q"`）
- **不保留 backward-compat**：直接 in-place migrate，loader 只讀新 key；違反「不混搭新舊」原則 + 主公是唯一使用者，無外部 frozen JSON 需要相容
- 保留 snapshot 內容本身（80 題 gold set 是 wenji 資產）

### README 修補（12 項 P0/P1）

- 修 quickstart 命令（`wenji ingest dir`）、`wenji download-model` 命名、Python 3.13 限制
- 改 `pip install wenji` → `git clone + pip install -e .`（PyPI 未上架）
- 修 axes.yaml 範例語法（用 `examples/axes.yaml` 真實格式）
- 補 production 安全 checklist（auth、CORS、bind 127.0.0.1、反代速率限制）
- 補 LLM 失敗 fallback 行為說明、schema migration、eval 前置條件
- 移除所有 logos 引用

## Non-Goals

- **不修補** code 層其他 P0/P1 安全問題（path traversal、SSRF、stored XSS、cost-DoS）—— 另案處理，避免本 change 範圍爆炸
- **不重命名** `loader_logos_db.py` 為 `loader_sqlite.py`（adapter 行為是 logos schema specific，rename 會誤導；直接刪比較乾淨）
- **不新增** Docker / systemd / reverse proxy 部署範本（README 補 production checklist 文字即可）
- **不改變** wenji.eval 的 A/B 比對核心邏輯，只重命名識別字
- **不刪除** `tests/benchmark_80_v2_snapshot.json` 的 80 題 gold set（已 frozen，是 wenji baseline 資產）

## Capabilities

### New Capabilities

- `deployment-branding`: env-driven 控制對外暴露的品牌、SEO meta、CORS 來源；定義 unset/set 兩態的行為差異與安全預設
- `eval-baseline-comparison`: 通用化的兩 RAG run 比對能力（客觀 overlap + 主觀 eyeball sampling），與特定 baseline 識別字解耦

### Modified Capabilities

無（wenji 尚無既有 specs，本 change 為 capabilities bootstrap 一部分）

## Impact

**程式碼**
- `src/wenji/web/app.py` — CORS 預設、robots/llms/sitemap routes、URL host 白名單 validator
- `src/wenji/web/templates/{index,article,base}.html` — JSON-LD/canonical/og meta 條件渲染 + context-aware escape
- `src/wenji/cli/ingest.py` — 刪除 `from-logos-db` 子命令
- `src/wenji/cli/eval.py` — `--logos-r13` → `--baseline-output`、log 訊息 rename、baseline JSON validator
- `src/wenji/ingest/loader_logos_db.py` — **刪除**
- `src/wenji/eval/loader_logos_v2.py` → **重命名**為 `loader_benchmark_v2.py`；`SnapshotMetadata.logos_source_commit` → `source_commit` field rename
- 任何 import `loader_logos_v2` 的呼叫端（`cli/eval.py`、test 等）— path update

**測試**
- `tests/wenji/test_loader_logos_db.py` — **刪除**
- `tests/wenji/test_loader_logos_v2.py` → **重命名**為 `test_loader_benchmark_v2.py`，內部 assertion 更新
- `tests/wenji/test_web.py` — CORS 預設、URL 白名單、escape、robots.txt、sitemap 預期更新
- `tests/wenji/test_eval_*` — flag rename + metadata key rename + JSON schema validation

**資料**
- `tests/benchmark_80_v2_snapshot.json` — `logos_source_commit` 改為 `source_commit`、`snapshot_source_path` 描述去 logos
- 既有 r0 baseline JSON（`wenji_r0_*.json` 等 frozen 輸出）— 同步 in-place migrate（主公唯一使用者，逐檔處理）

**文件**
- `README.md` — 全面替換為修補後的 `20260509_README.md`
- `CHANGELOG.md` — 新增 v0.3.7 (Unreleased) 段落記錄 BREAKING
- `CONTRIBUTING.md` — 若提及 `from-logos-db` 同步移除
- `.gitignore` — 確認含 `.env`、`.envrc`（README task 6.10 規範化前置）

**CI / 配置**
- `pyproject.toml` — 確認無 entry point / script 引用被刪檔案
- `.github/workflows/` — 若有引用 logos 名稱同步改

**對外 API / behaviour 變更**
- 預設 CORS 行為：empty（從 `https://logos.jacobmei.com` 改）
- 預設 robots.txt 行為：conservative deny；sitemap.xml / llms.txt：404
- CLI surface 縮減：`wenji ingest from-logos-db` 移除
- CLI flag rename：`wenji eval sanity-eyeball --logos-r13` → `--baseline-output`
- Python module rename：`wenji.eval.loader_logos_v2` → `wenji.eval.loader_benchmark_v2`
- Dataclass field rename：`SnapshotMetadata.logos_source_commit` → `SnapshotMetadata.source_commit`（無 backward-compat 讀取）
