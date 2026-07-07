# Design: Release v0.4.0 — first PyPI publish

## Decision context

三個前置決策已由主公於 2026-07-07 拍板（common-ground R5/R6/R7，全選推薦方案）：全包成 0.4.0、C-3 走 drop 路線納入、GitHub Actions trusted publishing。本 design 記錄各決策的完整 trade-offs（含已拍板者，供 audit trail），並補齊 propose 階段新浮現的執行層決策（D3 清理位置、D5 驗證策略、D6 回填深度、D7 packaging 細節）。

關鍵事實基礎（2026-07-07 實測）：

- `wenji_meta` 是 key/value 表（`key TEXT PRIMARY KEY, value TEXT`，`schema.sql:26-29`），dead 的是 5 個 **rows** 不是 columns — cleanup-build-telemetry BACKLOG.md 寫的「ALTER TABLE DROP COLUMN + schema bump v3」是 drift，實際成本低一個量級
- dead counter keys 有唯一一個 writer：`ingest/__init__.py:439-441` `rebuild_from_disk` 的 reset UPDATE（設回 `'0'`）；無任何 reader（`health.py` L1 已於 add-doctor-and-startup-check 移除）
- `core/db.py` 的 `connect()` 不執行 schema.sql；`initialise_schema(conn)` 只在寫路徑（db 建立 / rebuild）被呼叫 — read path（doctor / search / serve）永不觸碰 seed
- PyPI `wenji` 名稱可用（`pypi.org/pypi/wenji/json` 回 404）
- CI 現有 2 jobs：`test`（3.10/3.11/3.12 matrix）+ `integration`
- wenji 無外部使用者（主公 2026-07-07 stated，common-ground R11）

## Goals / Non-Goals

**Goals**

- cut v0.4.0 並完成 PyPI 首發，建立 tag-push 觸發的可重複 publish pipeline
- 首發前清掉 `wenji_meta` 5 個 dead keys（cleanup-build-telemetry D1 = drop，backlog 收案）
- CHANGELOG 從「全塞 [Unreleased]」整理成 per-version sections，之後每版增量維護
- README 安裝指引切到 `pip install wenji`

**Non-Goals**

- logos Mode 3 Phase B（logos 改 pip 消費 wenji）— 本 change 完成後解鎖的獨立後續工作
- logos prod migration / chunks_fts rebuild — 綁 Phase B，不在此
- counter wire-up / L1 health check 重引 — D2 拍板 drop 後永久關閉（將來如需 row counts，`SELECT COUNT(*)` on demand）
- `requires-python` 放寬到 3.13 — 需驗 onnxruntime / tokenizers 相容性，無使用者需求，留待有需求時獨立處理
- TestPyPI dry-run — 見 D5
- 新功能開發 — 本 change 純 release engineering

## D1 — 版號策略【主公已拍板】

選 **全包成 0.4.0**。

| 方案 | + | − |
|---|---|---|
| **全包 0.4.0（pick）** | PyPI 首發即完整版（doctor + branding + snippet fix）；無 phantom version；一次 release 工序 | CHANGELOG 內文 `(v0.3.7)` 標註需改掛 0.4.0（一次性編輯成本） |
| 補 tag 0.3.7 再出 0.4.0 | 歷史精確（0.3.7 對齊 prod 現跑 code） | 兩次 release 工序；0.3.7 tag 點要在 60 commits 裡人工考古切分；PyPI 上多一個無人消費的版本 |

## D2 — `wenji_meta` dead keys 處置【主公已拍板 drop；schema bump 為本 design 補充決策】

選 **drop（不 wire up）+ 不 bump SCHEMA_VERSION**。

處置路線（cleanup-build-telemetry BACKLOG D1，主公拍板）：

| 方案 | + | − |
|---|---|---|
| **Drop（pick）** | PyPI 首發乾淨（無殭屍 keys、無 DEPRECATED 註解出門）；成本 ~30-45 min；未來需要 row counts 用 `SELECT COUNT(*)` on demand | 放棄 build telemetry（backlog 自評價值有限，L2/L3 已涵蓋主要偵測場景） |
| Wire up counters + 重引 L1 | partial-crash 偵測（SIGKILL 在最後 commit 與 counter 更新之間） | 觸碰 ingest 寫路徑 + 既有 db backfill 遷移問題；偵測增益邊際（L2 cross-table sanity 已抓主要範式） |
| 留 backlog 不動 | 0.4 scope 更小 | PyPI 首發帶著 5 個殭屍 keys + 「DO NOT add readers」註解出門；之後清理需再走一次 release |

Schema version 處置（本 design 補充）：

| 方案 | + | − |
|---|---|---|
| **不 bump，維持 `"2"`（pick）** | 表結構未變（CREATE TABLE 不動，僅 seed INSERT 縮減）；既有 db 完全相容，殘留 keys 由 D3 機制清除；logos prod db 不被迫 rebuild | 「schema_version 相同但 seed 不同」的兩種 db 短暫並存（無 reader，無可觀察差異） |
| Bump `"2" → "3"` | 版本號嚴格對應 schema.sql 每個 byte | 強制所有既有 db rebuild（`SchemaError` hard fail），為 5 個無 reader 的 rows 付出整庫重建代價，比例失衡 |

`SCHEMA_VERSION` 的既有語意是 connect 時 hard-fail gate（`db.py:72-76`），bump 的實際效果是強制 rebuild — 對本次無行為差異的清理是過度武器。

## D3 — 既有 db 的 stale keys 清理位置

選 **`initialise_schema` 內 DELETE**。

| 方案 | + | − |
|---|---|---|
| **`initialise_schema` DELETE（pick）** | 單一入口涵蓋所有寫路徑（db 建立、rebuild 都經過它）；read path（doctor / serve / search）保持零寫入，不違反 doctor read-only 的 ESTABLISHED assumption；idempotent（DELETE 不存在的 keys 是 no-op） | 既有 db 若永不再走寫路徑則殘留（無 reader，無害；下次 ingest/rebuild 自然清掉） |
| `rebuild_from_disk` DELETE（取代現有 UPDATE） | 改動點最少（同一行位置換語句） | `ingest_dir` 直接呼叫（不經 rebuild）的既有 db 清不到；清理語意綁在 rebuild 而非 schema 管理，位置不對 |
| 完全不清理 | 零改動 | 殭屍 rows 永久殘留；schema.sql 已無這些 keys 的定義，db 內容與 schema 文件長期不一致，未來 debug 時是誤導源 |

實作：`initialise_schema` 在 `executescript` 後加

```python
conn.execute(
    "DELETE FROM wenji_meta WHERE key IN ("
    "'build_started_at','build_completed_at','n_articles','n_chunks','n_doc_vectors')"
)
```

同時 `rebuild_from_disk` 的 counter reset UPDATE（`ingest/__init__.py:439-441`）整段移除（rebuild 本就呼叫 `initialise_schema`，清理由 D3 機制接手）。

## D4 — Publish 機制【主公已拍板】

選 **GitHub Actions trusted publishing（OIDC）**。

| 方案 | + | − |
|---|---|---|
| **Trusted publishing（pick）** | 無 API token 落地（符合機密隔離硬標準）；tag push 全自動、可重複；PyPA 官方推薦；publish 有 CI 紀錄 | 需主公在 PyPI 網頁做一次 pending publisher 設定（首發前置，趙雲無法代辦） |
| 本機 `twine upload` + token | 工序直觀、無 GA 依賴 | token 需長期保管於 `~/.paiop_secrets.json`；每次手動；無 CI 紀錄；token 洩漏面 |
| GA + token secret | 自動化程度同 pick | token 存 GitHub secrets 仍是長期機密資產；trusted publishing 存在時無理由選它 |

`release.yml` 結構：`on: push: tags: ['v*']` → job `build`（checkout → `python -m build` → `twine check dist/*` → upload-artifact）→ job `publish`（`environment: pypi`、`permissions: id-token: write`、download-artifact → `pypa/gh-action-pypi-publish@release/v1`）。

## D5 — 首發驗證策略

選 **build 驗證前移到 CI + 直接首發（不走 TestPyPI）**。

| 方案 | + | − |
|---|---|---|
| **CI build job + 直接首發（pick）** | 每個 PR 驗 `python -m build` + `twine check` + wheel 內容清單，翻車點（package-data 缺檔）在 merge 前暴露；首發失敗 delete tag + yank 重來，無使用者、無 SLA（common-ground R11） | publish 步驟本身首發前未真跑過（風險由 pypa action 的成熟度 + 失敗可重來緩解） |
| TestPyPI 先行 dry-run | publish 全鏈路先驗一次 | 多一組 pending publisher 設定 + TestPyPI 帳號工序；對無外部使用者的單人專案，防的是「可重來的失敗」，成本 > 收益 |
| 不加 CI build job，直接 tag | 最少改動 | wheel 內容問題（`core/*.sql`、templates、examples 沒進 wheel）要到 publish 後 `pip install` 才發現，是 PyPI 首發最常見翻車模式 |

wheel 內容驗證實作：CI build job 內 `unzip -l dist/*.whl` 後 grep 斷言關鍵 package-data 存在（`core/schema.sql`、`web/templates/`、`examples/corpus-christian/` 內至少各一檔），對照 `pyproject.toml` `[tool.setuptools.package-data]` 清單。

## D6 — CHANGELOG 回填深度

選 **全量回填**。

| 方案 | + | − |
|---|---|---|
| **全量回填（pick）** | 符合檔頭已宣告的 Keep a Changelog 格式；PyPI 首發後新讀者看到的是正常結構；工作機械性高（搬 h3 → h2，date 取自 `git tag -l --format`） | 一次性編輯量較大（0.2.x–0.3.6.1 約 6-8 個版本群組） |
| 最小改（只收斂 0.4.0） | 編輯量最小 | 宣告 KaC 卻把 0.2–0.3.6.1 全留在 [Unreleased]，格式自相矛盾持續存在；之後補救還要再動一次 |

細節：`(v0.3.7)` 與 `(vNext)` 標註的內容併入 `## [0.4.0] — <release date>`；`(v0.3.6.1)` 以下各群組升為 `## [x.y.z] — <tag date>`；比對 `git tag -l 'v*' --format='%(refname:short) %(creatordate:short)'` 取日期；缺 tag 的早期版本（若 h3 有標但無 tag）併入其後最近的有 tag 版本並註記。`[Unreleased]` 清空保留 heading。

## D7 — Packaging 細節

一組小決策，合併記錄：

- **Development Status `3 - Alpha → 4 - Beta`（pick）**：693 tests + 3 版本 CI matrix + 即將 PyPI 發行，Alpha 標籤低估成熟度。替代：維持 Alpha（過度保守，與「請人用 pip install」的訊號矛盾）；直接 `5 - Production/Stable`（言過其實，API 仍可能在 minor 版本間變更）。
- **`requires-python = ">=3.10,<3.13"` 維持（pick）**：無使用者被上限擋住（R11），放寬需驗證 onnxruntime / tokenizers 的 3.13 wheel 支援，屬獨立工作。替代：現在放寬（引入未驗證的相容性面，首發風險無謂增加）。
- **README pre-1.0 disclaimer 保留但改寫**：移除「尚未上 PyPI；請從 source 安裝」句（4 處 git-clone 安裝指引改 `pip install wenji`），保留「API 可能在 minor 版本間變更」警語 — Beta + 0.x 的 SemVer 語意本就如此。

## 執行順序與風險

Phase 順序：C-3 清理 → CHANGELOG → packaging + CI build job → release.yml → release 執行。前四個 phase 各自可獨立 PR / commit boundary（遵循主公 chunking 偏好），release 執行 phase 依賴前四者全部 merged 至 main。

主要風險與緩解：

- **wheel 缺檔**：D5 的 CI 內容斷言在 merge 前抓
- **pending publisher 設定錯誤**（owner/repo/workflow 名不符）：publish job 會 401，錯誤訊息明確，修 PyPI 設定後 re-run job 即可，不需重 tag
- **audit_release.sh 抓到內部詞**：release 執行 phase 的第一步就跑，早於 tag
- **CHANGELOG 回填搬錯內容**：純文字搬移，G3 驗證用「回填前後 `(vX.Y)` 標註內容 diff 為零遺漏」檢查（詳 tasks）
