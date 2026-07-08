# Tasks: Fail-loud runtime

## Phase 0 — Pre-flight

- [ ] 0.1 確認 PR #2（v0.3.6.1 + v0.3.7 release）已 merge 到 main
- [ ] 0.2 從 origin/main 切出新 branch `fail-loud-runtime`

## Phase 1 — Code

- [ ] 1.1 `src/wenji/search/rrf.py`：新增 `import logging` + `logger = logging.getLogger(__name__)` + `from wenji.core.errors import SearchError`
- [ ] 1.2 `src/wenji/search/rrf.py:136-137`：替換 `return {}` 為：
  ```python
  logger.warning("chunks_fts query failed: %s", exc, exc_info=True)
  raise SearchError(f"chunks_fts query failed: {exc}") from exc
  ```
- [ ] 1.3 `src/wenji/search/bm25.py`：新增 `import logging` + `logger = logging.getLogger(__name__)`
- [ ] 1.4 `src/wenji/search/bm25.py:107`：在既有 `raise SearchError(f"FTS5 query failed: {exc}") from exc` **之前**插入：
  ```python
  logger.warning("articles_fts query failed: %s", exc, exc_info=True)
  ```
- [ ] 1.5 `ruff check src/wenji/search/` 通過

## Phase 2 — Tests

- [ ] 2.1 Grep `tests/wenji/test_search_rrf.py` + `test_search_bm25.py`，確認**無**既有「OperationalError → return `{}`」silent-return assertion（如有 → 列出後與維護者確認改 / 刪）
- [ ] 2.2 `tests/wenji/test_search_rrf.py`：新增 `test_chunk_bm25_search_raises_on_operational_error`
  - 用 `unittest.mock.MagicMock` 造 fake conn：`fake_conn.execute = MagicMock(side_effect=sqlite3.OperationalError("simulated lock"))`
  - `with pytest.raises(SearchError) as excinfo: chunk_bm25_search(fake_conn, "test", limit=10)`
  - assert `excinfo.value.__cause__` 是 `sqlite3.OperationalError` instance
  - assert `"chunks_fts query failed"` in `str(excinfo.value)`
- [ ] 2.3 同檔新增 `test_chunk_bm25_search_logs_warning_on_operational_error`
  - 同 fake conn 模式 + `caplog.set_level(logging.WARNING, logger="wenji.search.rrf")`
  - `with pytest.raises(SearchError): chunk_bm25_search(fake_conn, "test", limit=10)`
  - assert 至少 1 條 WARNING record on logger `wenji.search.rrf`
  - assert `"chunks_fts query failed"` 在 record.message 中
  - assert `record.exc_info is not None`（stack trace 帶上）
- [ ] 2.4 `tests/wenji/test_search_bm25.py`：新增 `test_bm25_search_logs_warning_on_operational_error`
  - 同 fake conn 模式 + `caplog.set_level(logging.WARNING, logger="wenji.search.bm25")`
  - **MUST** `with pytest.raises(SearchError) as excinfo: bm25_search(fake_conn, "test", limit=10)`（既有 raise 行為驗證）
  - assert `excinfo.value.__cause__` 是 `sqlite3.OperationalError` instance（既有 `from exc` 行為驗證）
  - assert 至少 1 條 WARNING record on logger `wenji.search.bm25`，message 含 `"articles_fts query failed"`
  - assert `record.exc_info is not None`
- [ ] 2.5 `pytest tests/wenji/ -q` 全綠

## Phase 3 — Docs

- [ ] 3.1 `CHANGELOG.md` `[Unreleased]` 新增（或合併到既存版本 entry）：
  ```markdown
  ### Changed / BREAKING

  - `wenji.search.rrf.chunk_bm25_search` 對 SQLite `OperationalError`
    改 raise `SearchError`（與 `wenji.search.bm25.bm25_search` 一致），
    不再 silent fallback 回 `{}`。Caller 收到 SearchError 行為由
    `Searcher.search()` 與 FastAPI exception handler 決定（預設 HTTP
    500）。
  - 兩條 retrieval path（`articles_fts` 與 `chunks_fts`）觸發
    `OperationalError` 時皆 emit `WARNING` log，含 stack trace。
  ```

## Phase 4 — G3 Verify + Ship

- [ ] 4.1 G3 三要素：行為改變 ✓ / test 覆蓋 ✓ / docs 同步 ✓
- [ ] 4.2 `pytest tests/wenji/ -q` 最終 green
- [ ] 4.3 `wenji serve` 起 server smoke：`/api/search?q=測試` 正常路徑回 200（沒觸發改動 path）
- [ ] 4.4 Atomic commits（建議分 Phase 1 / 2 / 3 三個 commit）
- [ ] 4.5 Push branch + 開 PR：title `fix(search): fail-loud OperationalError + warning logs`
- [ ] 4.6 PR description 引用 spec change directory `openspec/changes/fail-loud-runtime/`
- [ ] 4.7 維護者 review + merge

## Phase 5 — Archive

- [ ] 5.1 PR merge 後執行 `spectra archive fail-loud-runtime`（或手動 mv 到 `openspec/changes/archive/2026-XX-XX-fail-loud-runtime/`）
- [ ] 5.2 更新 `~/.claude/common-ground/wenji/assumptions.md`：標 fail-loud-runtime spec 為 archived，移到 history section

## Effort estimate

~30 分鐘 apply（依維護者 chunking 偏好「還可以一下」≈ 30 min 區間）。Phase 1 ~10 min、Phase 2 ~10 min、Phase 3-5 ~10 min。
