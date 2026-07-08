# Proposal: Release v0.4.0 — first PyPI publish

## Why

wenji 距上一個 tag `v0.3.6.1` 已累積 60 commits（含 5 個 BREAKING、doctor + startup gate、env-driven branding、snippet AST strip），但 `pyproject.toml` 版號仍是 0.3.6.1、CHANGELOG 內文標註的 v0.3.7 從未 tag。同時 Mode 3 migration 的 Phase B（logos 從 sibling editable install 改為 pip 消費 wenji）明確以「wenji 0.4 上 PyPI」為前置條件。本 change 一次收斂：cut v0.4.0、建立可重複的 PyPI publish pipeline（GitHub Actions trusted publishing）、並在首發前清掉 `wenji_meta` 的 5 個 dead keys（cleanup-build-telemetry backlog 的 D1 決策 = drop），讓 PyPI 上的第一個版本乾淨出門。

PyPI package name `wenji` 已確認可用（2026-07-07 實測 `pypi.org/pypi/wenji/json` 回 404）。wenji 目前沒有任何外部使用者（唯一 consumer 是 logos），release 風險容忍度高：首發失敗 delete tag 重來即可。

## What Changes

- **`wenji_meta` dead keys 清理（cleanup-build-telemetry 收尾，D1 = drop）**：
  - `schema.sql` seed 移除 `build_started_at` / `build_completed_at` / `n_articles` / `n_chunks` / `n_doc_vectors` 5 個 keys 與 DEPRECATED 註解區塊
  - `ingest/__init__.py` `rebuild_from_disk` 移除 counter reset UPDATE（該 3 keys 的唯一 writer）
  - `initialise_schema` 加 stale-key DELETE（既有 db 在下次任何寫路徑 schema init 時自動清掉舊 keys）
  - `observability/health.py` module docstring 的 counter 說明段更新（不再指向 backlog）
  - **不 bump `SCHEMA_VERSION`**（表結構不變，僅 seed data；既有 db 完全相容，詳 design.md D2）
- **CHANGELOG 全量回填**：`[Unreleased]` 下的 `(v0.3.x)` h3 群組升級為正式 `## [x.y.z] — YYYY-MM-DD` sections（date 取自 git tag）；`(vNext)` 與 `(v0.3.7)` 標註的內容併入新的 `## [0.4.0]` section（0.3.7 為 phantom version，從未 tag，不回填）
- **Packaging**：`pyproject.toml` 版號 bump `0.3.6.1 → 0.4.0`；Development Status classifier `3 - Alpha → 4 - Beta`；`requires-python = ">=3.10,<3.13"` 維持不變
- **CI 前移 build 驗證**：`ci.yml` 新增 `build` job（`python -m build` + `twine check dist/*` + wheel 內容驗證 script），每個 PR 驗 sdist/wheel 可建且 package-data 完整
- **Publish pipeline**：新增 `.github/workflows/release.yml` — tag `v*` push 觸發 build → PyPI trusted publishing（OIDC，`pypa/gh-action-pypi-publish`，無 API token 落地）
- **README**：安裝指引從「git clone + pip install -e .」改為 `pip install wenji`（4 處：pre-1.0 disclaimer、quickstart、dev setup 保留 editable 說明）
- **Release 執行**：`audit_release.sh` 0 hits + full tests + integration tests 通過後 tag `v0.4.0` push → 自動 publish → `pip install wenji` 冒煙驗證
- 歸檔 `cleanup-build-telemetry` backlog scaffold（決策已在本 change 落地）

無新增 BREAKING（`wenji_meta` dead keys 無任何 reader；rebuild 的 counter reset 移除不影響可觀察行為）。

## Capabilities

### New Capabilities

- `pypi-distribution`: wenji 以 PyPI package 形式發行的規格 — 版本 tag 觸發的自動 publish pipeline、wheel 內容完整性、CHANGELOG 版本紀律

### Modified Capabilities

（無 — dead metadata keys 從未被任何既有 capability spec 引用為 requirement；db-consistency-health 的 L1 counter check 已在 add-doctor-and-startup-check apply 階段移除，本次清理不改變任何 spec-level 行為，詳 design.md D2）

## Impact

- **Code**: `src/wenji/core/schema.sql`、`src/wenji/core/db.py`（initialise_schema）、`src/wenji/ingest/__init__.py`（rebuild_from_disk）、`src/wenji/observability/health.py`（docstring only）
- **Packaging / CI**: `pyproject.toml`、`.github/workflows/ci.yml`、新增 `.github/workflows/release.yml`
- **Docs**: `CHANGELOG.md`（全量重排）、`README.md`（安裝指引）
- **External（維護者 manual step）**: PyPI 帳號網頁設定 pending trusted publisher（project=`wenji`, owner=`notoriouslab`, repo=`wenji`, workflow=`release.yml`, environment=`pypi`）— agent無法代辦
- **下游**: logos Mode 3 Phase B（pip 消費）在本 change 完成後解鎖，屬後續獨立工作，不在本 scope

---

## G1 審查紀錄（2026-07-07）

- **Self-Review（5 項）**：S1 placeholder 掃描 0 hits；S2 抓到 Phase 3 依賴倒置（CI 斷言路徑引用尚未執行的本機 build 實測）→ 重排為 3.2 本機 build 先行、3.3 CI job 後行；S3 scope 單一 release train；S4 歧義由 3.2 前置消除；S5 D1-D7 各列 2-3 方案。
- **spectra analyze**：初跑 1 critical（analyzer 把 Modified Capabilities 說明文字的 backtick token 誤認 capability 名）+ 4 warning（tasks 未引用 requirement 名稱）→ 移除 backtick、tasks 補 requirement 引用 → 重跑 4 維度全 Clean。
- **Sub-Agent Review（haiku + sequential-thinking，獨立驗證 6 項程式碼事實全數確認）**：PASS，0 critical / 0 warning / 2 info → 兩項 info 均採納（1.2 DELETE 位置精確化為「緊接 executescript 的下一個 statement」；3.3 wheel 斷言路徑註明溯源自 3.2）。
