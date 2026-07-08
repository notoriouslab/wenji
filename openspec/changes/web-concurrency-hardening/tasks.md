# Tasks: Web concurrency hardening

## Phase 0 — Pre-flight

- [ ] 0.1 `pwd` + `git remote -v` 確認 wenji repo、tree clean、main 含健檢時的 HEAD；切 branch `web-concurrency-hardening`

## Phase 1 — 單例鎖（D1）

- [ ] 1.1 `web/app.py`：module 級 `_init_lock = threading.Lock()`；`_get_searcher` 與 `_get_tag_browser` 改 double-checked（快路徑無鎖 re-check → 進鎖 → re-check → 建構 → 賦值）（滿足 spec requirement: Lazy singletons initialize exactly once under concurrency）
- [ ] 1.2 test：monkeypatch 慢建構（sleep 0.2）+ `ThreadPoolExecutor(8)` 併發呼叫，斷言建構次數 == 1 且回傳同一物件

## Phase 2 — Query lock（D2）

- [ ] 2.1 `web/app.py`：module 級 `_query_lock = threading.Lock()`；`s.search(...)` 兩個呼叫點（`app.py:764,986` 附近）以 `with _query_lock:` 包住（滿足 spec requirement: Shared SQLite connection calls are serialized）
- [ ] 2.2 `_get_conn` 註解改寫：刪除 file-lock 錯誤辯護，改述真實模型（同 Connection 併發由 query lock 序列化；per-request conn 即開即關）
- [ ] 2.3 test：TestClient + threads 併發打 `/api/search`（rewrite 開啟、假 LLM client），斷言全部 200 無 OperationalError

## Phase 3 — TagBrowser TTL + 原子替換（D3）

- [ ] 3.1 `browse/tag.py`：`_refresh_if_needed` 改 `time.monotonic() - self._last_load > 300` 判斷；load 全程用 local 變數，完成後鎖內（`self._lock = threading.Lock()`）一次替換 `_tag_to_articles` / `_article_to_meta` / `_tag_counts` + 更新 `_last_load`（滿足 spec requirement: Tag browser data refreshes within a bounded interval）
- [ ] 3.2 tests：TTL（monkeypatch monotonic，過期後 ingest 新列可見）；併發 list_tags/get_tag_detail 無 KeyError 冒煙
- [ ] 3.3 刪除 `tag.py:23-24` 過時註解（「transient instance」與實際 singleton 不符 — 健檢 finding）

## Phase 4 — year 防呆 + finally（D4）

- [ ] 4.1 `web/app.py`：新增 `_parse_year(value: str | None) -> int | None`（strip → isdigit → int，否則 None）；三處呼叫點（454、461、946 附近）改用之（滿足 spec requirement: Invalid year filter degrades gracefully）
- [ ] 4.2 `app.py:1002-1032` axes sidebar：`conn.close()` 移入 `finally`
- [ ] 4.3 test：`GET /?year=abc` → 200；`GET /?year=2023` 結果與現行一致

## Phase 5 — 驗證 + PR

- [ ] 5.1 `ruff check` + `ruff format --check` + `pytest tests/wenji/` 全綠（G3 證據附輸出）
- [ ] 5.2 CHANGELOG `[Unreleased]` Fixed 條目（1-2 句，公開精簡原則）
- [ ] 5.3 commit + PR + CI 全綠（**等 checks 完成才 merge** — 記取 logos PR #3 搶跑教訓）+ merge
- [ ] 5.4 spectra archive + memory 更新（健檢三包進度：1/3 done）
