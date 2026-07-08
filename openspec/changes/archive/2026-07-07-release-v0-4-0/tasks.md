# Tasks: Release v0.4.0 — first PyPI publish

## Phase 0 — Pre-flight

- [x] 0.1 確認 `pwd` + `git remote -v` 是 wenji repo、working tree clean、main HEAD 含 PR #8（`5c6a4bc`）
- [x] 0.2 從 origin/main 切出 branch `release-v0-4-0`

## Phase 1 — C-3：`wenji_meta` dead keys 清理（D2/D3）

- [x] 1.1 `src/wenji/core/schema.sql`：seed INSERT 移除 5 個 keys（`build_started_at` / `build_completed_at` / `n_articles` / `n_chunks` / `n_doc_vectors`）；刪除 header 的 DEPRECATED 註解區塊（現行約 line 18-24）與 INSERT 內的 DEPRECATED inline 註解；Live keys 註解保留 `schema_version` + `embedder` 兩行
- [x] 1.2 `src/wenji/core/db.py` `initialise_schema`：緊接 `executescript` 的下一個 statement（即 schema_version 的 SELECT 驗證之前），加 stale-key DELETE：
  `conn.execute("DELETE FROM wenji_meta WHERE key IN ('build_started_at','build_completed_at','n_articles','n_chunks','n_doc_vectors')")`
  docstring 補一句說明（清除 v0.4.0 前 schema 的殘留 keys，idempotent）
- [x] 1.3 `src/wenji/ingest/__init__.py` `rebuild_from_disk`：移除 counter reset UPDATE 整段（現行 line 439-441）；確認 `rg -n "n_articles|n_chunks|n_doc_vectors|build_started_at|build_completed_at" src/` 僅剩 `db.py` 的 DELETE 一處
- [x] 1.4 `src/wenji/observability/health.py` module docstring：counter 說明段改寫為歷史註記（keys 已於 v0.4.0 移除），刪除指向 `cleanup-build-telemetry` backlog 的句子
- [x] 1.5 tests：
  - `tests/wenji/test_core_db.py` 新增：fresh db init 後 `wenji_meta` 恰好 2 keys（`schema_version` / `embedder`）
  - 新增：先手動 INSERT 5 個舊 keys 模擬 pre-0.4.0 db，跑 `initialise_schema` 後斷言舊 keys 已被 DELETE、`schema_version` / `embedder` 保留
  - 掃既有 fixture：`rg -n "n_articles|n_chunks|n_doc_vectors|build_" tests/wenji/` 逐一確認無 test 依賴 dead keys（test_ingest_pipeline.py 的 `n_chunks` 是 local 變數名、非 db key，不動）
- [x] 1.6 `ruff check` + `ruff format --check` + `pytest tests/wenji/`（全綠；G3 驗證證據附輸出）
- [x] 1.7 `git rm -r openspec/changes/cleanup-build-telemetry/`（backlog 決策已在本 change 落地；commit message 註明 D1 拍板 = drop、落地於 release-v0-4-0）
- [x] 1.8 commit boundary：`feat(schema): drop dead wenji_meta build-telemetry keys`（G3 通過後）

## Phase 2 — CHANGELOG 全量回填（D6）

- [x] 2.1 取 tag 日期：`git tag -l 'v*' --format='%(refname:short) %(creatordate:short)'`，列出 0.2.x–0.3.6.1 全部對照表
- [x] 2.2 重排 `CHANGELOG.md`：
  - `(vNext)` + `(v0.3.7)` 標註的所有內容併入新 `## [0.4.0] — <本 phase commit 當日日期>`（P5 tag 時若跨日，5.4 前更新此 date 為 tag 日）
  - Phase 1 的 wenji_meta 清理補入 0.4.0 section（Changed 一條，1-2 句，公開 CHANGELOG 精簡原則）
  - `(v0.3.6.1)` 以下各 h3 群組升為 `## [x.y.z] — <tag date>`；h3 標註若出現無對應 tag 的版本，內容併入其後最近的有 tag 版本 section 並以一行註記標明
  - `[Unreleased]` 清空、heading 保留
- [x] 2.3 零遺漏驗證：重排前先 `git show HEAD:CHANGELOG.md > "$SCRATCHPAD/changelog_before.md"`（session scratchpad 目錄）；重排後以 script 比對（正規化 heading 行後 diff 內容行集合），輸出「遺漏行 = 0」證據
- [x] 2.4 commit boundary：`docs(changelog): backfill per-version sections + collect 0.4.0`

## Phase 3 — Packaging + CI build 驗證（D5/D7）

- [x] 3.1 `pyproject.toml`：`version = "0.4.0"`；classifier `Development Status :: 3 - Alpha` → `4 - Beta`；`requires-python` 不動
- [x] 3.2（發現+修復：wheel 缺 examples JSON，package-data 補 `examples/**/*.json`；wheel user 呼叫 load_example 原會 FileNotFoundError）本機 build 實測：`python -m build` + `twine check dist/*` + `unzip -l dist/*.whl`，人工核對 package-data 完整（`core/schema.sql`、web templates、examples corpus），記下 wheel 內的實際路徑清單供 3.3 斷言使用
- [x] 3.3 `.github/workflows/ci.yml` 新增 `build` job（滿足 spec requirement: CI validates package build and wheel completeness on every PR）：
  - `python -m build`
  - `twine check dist/*`
  - wheel 內容斷言：`unzip -l dist/*.whl` 後 grep 斷言 `wenji/core/schema.sql`、`wenji/web/templates/`（至少 1 檔）、examples corpus（至少 1 檔；路徑用 3.2 記下的實際清單，workflow 內以註解標明來源為本機 build 實測）皆存在，缺任一 → exit 1 並印出缺檔名
  - 不 upload artifact（publish 是 release.yml 專責）
- [x] 3.4 `README.md` 安裝指引：line 15 disclaimer 移除「尚未上 PyPI；請從 source 安裝」、保留 API 變動警語；line 47-51 與 375-376 的 git clone + `pip install -e .` 改 `pip install wenji`；line 330 / 469 的 dev setup（`pip install -e ".[dev]"`）保留並確認上下文標明「開發者從 source」
- [x] 3.5 `pytest` 全綠 + commit boundary：`chore(release): bump 0.4.0 + Beta classifier + CI build job + README pip install`

## Phase 4 — Publish pipeline（D4）

- [x] 4.1 新增 `.github/workflows/release.yml`（滿足 spec requirement: Tag push triggers automated PyPI publish via trusted publishing）：
  - `on: push: tags: ['v*']`
  - job `build`：checkout → setup-python 3.12 → `pip install build twine` → `python -m build` → `twine check dist/*` → `actions/upload-artifact`
  - job `publish`：`needs: build`、`environment: pypi`、`permissions: id-token: write`、`actions/download-artifact` → `pypa/gh-action-pypi-publish@release/v1`
  - 全 workflow 無任何 secret 引用
- [x] 4.2 驗證：`actionlint`（若本機無則 `brew install actionlint`）0 error；人工比對 4.1 結構逐項符合
- [x] 4.3 寫維護者 manual step 說明（本 tasks.md 此處即說明，不另開檔）：PyPI 登入 → Account → Publishing → Add pending publisher：PyPI project name=`wenji`、owner=`notoriouslab`、repository=`wenji`、workflow=`release.yml`、environment=`pypi`。完成後維護者回報，5.3 gate 才放行
- [x] 4.4 commit boundary：`ci(release): PyPI trusted publishing workflow` → 開 PR、CI 全綠、merge main

## Phase 5 — Release 執行（依賴 Phase 1-4 全部 merged）

- [x] 5.1 main 上跑 `scripts/audit_release.sh` → 0 hits（有 hit → 修完重跑，不 tag）（5.1-5.2 滿足 spec requirement: Release gate precedes tagging）
- [x] 5.2 `pytest tests/wenji/` 全綠 + `pytest -m integration` 全綠（G3 證據附輸出）
- [x] 5.3 Gate：維護者確認 4.3 pending publisher 已設定
- [x] 5.4 確認 CHANGELOG `## [0.4.0]` date = 今日（跨日則改）、`pyproject.toml` version 與 tag 一致 → `git tag v0.4.0` → `git push origin v0.4.0`（與 2.2 / 3.1 共同滿足 spec requirement: Released version metadata is consistent across pyproject, tag, and CHANGELOG）
- [x] 5.5 監看 release.yml run 至 publish 成功；失敗 → 依錯誤修正（publisher 設定錯 → 修 PyPI 後 re-run job；build 錯 → delete tag、修復、重 tag）
- [x] 5.6 冒煙驗證：新建乾淨 venv → `pip install wenji==0.4.0` → `wenji --help` 正常 → `python -c "import wenji"` 正常 → 附輸出證據
- [x] 5.7 收尾：更新 memory（`project_mode3_migration.md` Phase B 上半場完成、`reference_wenji_logos_topology.md` 若涉及）+ common-ground；`spectra archive release-v0-4-0`
