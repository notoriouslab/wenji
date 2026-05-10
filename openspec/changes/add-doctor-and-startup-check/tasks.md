# Tasks: Doctor CLI + startup consistency check

## Phase 0 — Pre-flight

- [x] 0.1 從 origin/main 切出新 branch `add-doctor-and-startup-check`
- [x] 0.2 確認 main HEAD 含 PR #5（`356b158`）archive commit

## Phase 1 — Core: `observability.health`

- [x] 1.1 新增 `src/wenji/core/errors.py` 加 `class StartupError(WenjiError)` （或在 errors module 既有位置 append）
- [x] 1.2 新增 `src/wenji/observability/health.py`：
  - `DEFAULT_SAMPLE_KEYWORDS = ("神", "人", "心", "天", "之")`
  - `@dataclass ConsistencyReport`（schema_version / counters / row_counts / sample_match_hits / issues / ok property / format method）
  - `def check_consistency(conn, sample_keywords=DEFAULT_SAMPLE_KEYWORDS) -> ConsistencyReport`
    - L1 query wenji_meta 三 counter + COUNT(*) 三 table，diff 入 issues
    - L2 cross-table sanity check 入 issues
    - L3 sample MATCH 全 miss 入 issues（含 hint）
  - `def _ensure_consistency(db_path)` helper：open conn → check → FAIL print + `sys.exit(1)`
  - import path：reuse `wenji.search.bm25.build_fts_query` 生 MATCH query
- [x] 1.3 `ruff check src/wenji/observability/health.py` + `ruff format` 通過

## Phase 2 — CLI: `wenji doctor`

- [x] 2.1 新增 `src/wenji/cli/doctor.py`：
  - typer command function
  - `--db PATH`（required，沿襲 `stats` / `inspect-chunks` 慣例；參考它們的 typer signature）
  - `--sample-keywords TEXT`（optional CSV，default = None；解析時 split + strip + filter empty；None 則用 DEFAULT_SAMPLE_KEYWORDS）
  - body：open conn → `check_consistency(conn, keywords)` → print `report.format()` → `sys.exit(0 if report.ok else 1)`
- [x] 2.2 `src/wenji/cli/__init__.py`：import `doctor as _doctor` + `app.command(name="doctor", help="Check db consistency (row count vs counter, sample MATCH).")(_doctor.command)`
- [x] 2.3 `ruff check + format` 通過

## Phase 3 — Startup gate integration

- [x] 3.1 `src/wenji/web/app.py`：
  - import `from contextlib import asynccontextmanager`
  - import `from wenji.observability.health import check_consistency`
  - import `from wenji.core.errors import StartupError`
  - 新 `@asynccontextmanager async def lifespan(app: FastAPI):` 開 conn → check → FAIL raise StartupError → close conn → yield → finally close
  - 改 `FastAPI(title="wenji", docs_url="/docs", redoc_url=None)` → `FastAPI(title="wenji", docs_url="/docs", redoc_url=None, lifespan=lifespan)`
  - db path 從既有 module-level env / config 讀（grep web/app.py 找出 path 來源）
- [x] 3.2 `src/wenji/cli/eval.py`：
  - import `from wenji.observability.health import _ensure_consistency`
  - 只 gate 涉 retrieval 的 subcommand：
    - `run`：`if db is not None: _ensure_consistency(db)`（當有 db 時 gate）
    - `run-benchmark`：subcommand body 開頭 `_ensure_consistency(db)`（db required）
    - `sanity-eyeball`：**不**動（純 JSON 比對無 db）
    - `migrate-jsonl`：**不**動（純 JSONL 轉換無 db）
- [x] 3.3 `src/wenji/cli/search.py`：thin-client fallback in-process Searcher 之前（line ~78 「from wenji.search import Searcher」附近）加 `_ensure_consistency(db_path)`
- [x] 3.4 確認**未動**: `cli/ingest.py` / `cli/rebuild.py` / `cli/inspect.py` / `cli/stats.py` / `cli/corpus.py` / `cli/classify.py` / `cli/aggregate.py` / `cli/segment.py` / `cli/set_chunk_strategy.py` / `cli/download.py`

## Phase 4 — Tests

- [x] 4.1 `tests/wenji/test_observability_health.py`：
  - `test_check_consistency_ok_state`：populated_db fixture → assert `report.ok is True`、`issues == []`
  - `test_check_consistency_counter_mismatch`：populated_db → 故意 `UPDATE wenji_meta SET value='999' WHERE key='n_articles'` → assert `not report.ok`、issue 字串含 `"n_articles"` 與 mismatch 數字
  - `test_check_consistency_chunks_table_empty_counter_zero`：populated_db → 故意 `DELETE FROM chunks_fts; UPDATE wenji_meta SET value='0' WHERE key='n_chunks'` → assert `not report.ok`、issue 含 `"chunks_fts empty"` 或類似 phrase（articles_meta > 0 但 chunks_fts = 0 且 counter 0 的「假一致」）
  - `test_check_consistency_sample_match_all_miss`：populated_db with 中文 → 故意傳 `sample_keywords=("zzzzz",)`（保證 0 hits）→ assert `not report.ok`、issue 含 `"sample keywords missed"` + hint
- [x] 4.2 `tests/wenji/test_cli_doctor.py`：
  - 用 typer.testing.CliRunner
  - `test_doctor_ok_exits_zero`：populated_db → exit_code 0、stdout 含 `"OK"`
  - `test_doctor_inconsistent_exits_one`：corrupted db → exit_code 1
  - `test_doctor_sample_keywords_override`：傳 `--sample-keywords "foo,bar"` → 走自定 keyword path
- [x] 4.3 `tests/wenji/test_web_startup.py`：
  - `test_lifespan_passes_on_healthy_db`：用 TestClient + populated_db → app starts、`GET /health` or `GET /` 200
  - `test_lifespan_raises_startup_error_on_bad_db`：corrupted db → TestClient 開 app 時 raise StartupError（pytest.raises 攔截）
- [x] 4.4 `tests/wenji/test_cli_search_startup_gate.py`：
  - `test_search_cli_exits_one_on_inconsistent_db`：corrupted db + `wenji search` invocation → exit_code 1
- [x] 4.5 `pytest tests/wenji/ -q` 全綠

## Phase 5 — Docs

- [ ] 5.1 `CHANGELOG.md` `[Unreleased]` 新 `### Added (vNext)` section：
  ```markdown
  - `wenji doctor` CLI: db consistency health check (row count vs
    `wenji_meta` counter + sample MATCH validation against
    `articles_fts` / `chunks_fts`). Exit 0 if OK, 1 if inconsistent.
    Optional `--sample-keywords k1,k2,k3` override for non-Chinese
    corpora.
  - Startup consistency gate on retrieval entry points
    (`wenji serve` via FastAPI lifespan; `wenji eval *` and
    `wenji search` via per-command helper). Inconsistent db → server
    refuses to bind / CLI exits non-zero with hint to run
    `wenji doctor`.
  ```
- [ ] 5.2 README.md：考慮在 production checklist section 加 `wenji doctor` 一行（grep README 看是否有 deployment ops section）

## Phase 6 — G3 Verify + Ship + Archive

- [ ] 6.1 G3 三要素：行為改變 ✓ / test 覆蓋 ✓ / docs 同步 ✓
- [ ] 6.2 `pytest tests/wenji/ -q` 最終 green
- [ ] 6.3 `ruff check + ruff format --check` 全通過
- [ ] 6.4 `bash scripts/audit_release.sh` clean
- [ ] 6.5 `wenji doctor --db data/wenji.db` 本機 smoke（如果 db 存在）
- [ ] 6.6 `wenji serve` smoke：startup 不一致 db 時 exit non-zero（用測試 db 模擬）
- [ ] 6.7 Atomic commits（建議分：health module / doctor CLI / startup gate / tests / docs / total ~5 commits）
- [ ] 6.8 Push branch + 開 PR base=main
- [ ] 6.9 PR title: `feat: add wenji doctor + startup consistency gate (Phase C-2)`
- [ ] 6.10 主公 review + merge
- [ ] 6.11 開 archive PR `chore(spectra): archive add-doctor-and-startup-check`，mv 到 `openspec/changes/archive/2026-XX-XX-add-doctor-and-startup-check/`
- [ ] 6.12 更新 `~/.claude/projects/.../memory/project_mode3_migration.md`：標 C-2 shipped、Phase C 整體完成（C-3 留將來）

## Apply notes (2026-05-10)

- **B 精緻 supersedes parts of Phase 1 / 4**: Phase 4 寫 test 時發現 `wenji_meta` build counters 是 v0.1.0 dead column (從未被任何 ingest path 維護) → L1 + L2.a + L2.b 三條 rule 整層移除。`health.py` 重寫為 2-layer (L2.c / L2.d / L3)；`ConsistencyReport.counters` field 移除；4 個 test 對應改寫；spec.md / proposal.md / design.md 補 drift correction #2。schema.sql 給 5 個 dead column 加 DEPRECATED 註解。Followup change scaffold `cleanup-build-telemetry/BACKLOG.md` 留接續決策。詳見 `proposal.md` G1 record 末條。
- 4.1 / 4.2 task descriptions 在 commit history 跟 spec 中記錄為 propose 階段版本；apply 後實際 test 名與內容以 `tests/wenji/test_observability_health.py` 等檔案為準。
- Phase 5 + 6 (docs + verify + ship + archive) 尚未啟動。

## Effort estimate

~60-90 min apply（比 fail-loud-runtime 大 3x）。建議分 chunk：
- Chunk 1: Phase 1 + 2（core module + doctor CLI），~25 min，commit boundary
- Chunk 2: Phase 3（startup gate × 3 entry），~20 min，commit boundary
- Chunk 3: Phase 4（tests × 4 file），~25 min，commit boundary
- Chunk 4: Phase 5 + 6（docs + verify + ship），~15 min
