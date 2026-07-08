# Tasks: Ingest throughput and operability

## Phase 0 — Pre-flight

- [ ] 0.1 `pwd` + `git remote -v` 確認 wenji repo、tree clean、main 含 change 1 merge；切 branch `ingest-throughput-and-operability`
- [ ] 0.2 準備本機 parity db（G4 對照組）：`cd ~/Projects/notoriouslab/logos && 暫移 breadoflife/2026BOL && wenji rebuild articles --db /tmp/parity_before.db --config config/wenji_ingest.yaml`，**計時記錄**（這顆同時是 before 基準）

## Phase 1 — D2 fresh-insert 跳過 DELETE（先做：最小改動、立即可測）

- [ ] 1.1 `ingest/__init__.py`：兩個 FTS DELETE（現 :313,:336 附近）移入 content-changed 分支（滿足 spec requirement: Fresh inserts skip derived-table deletes）
- [ ] 1.2 tests：三態（fresh insert 無 DELETE 執行——用 sqlite trace 或 mock 斷言、unchanged fast path 不動、changed 仍清舊列）
- [ ] 1.3 `ruff` + `pytest` 全綠 → commit boundary

## Phase 2 — D1 batch embedding

- [ ] 2.1 `ingest/__init__.py`：`ingest_dir` 重構為「收集 →字元預算打包（BUDGET=32000，單篇超限獨行）→ `encode_batch` → 逐篇寫入」；批次例外 → 該批逐篇重試；打包順序 = 檔案迭代順序（確定性）（滿足 spec requirement: Batched embedding preserves vector equivalence）
- [ ] 2.2 等價 gate test：10 篇樣本（含最長）batch vs single 逐元素相等；斷言失敗則改 cosine>0.99999 + CHANGELOG 記載後重跑
- [ ] 2.3 打包器單元測試：預算邊界、超限獨行、空 buffer、確定性
- [ ] 2.4 `ruff` + `pytest` → commit boundary

## Phase 3 — D3/D4/D5 可運維

- [ ] 3.1 `ingest_dir` 進度 log：每 200 篇 `logger.info("ingest: %d/%d (%.1f%%) rate=%.1f/s eta=%dmin", ...)`（滿足 spec requirement: Long-running ingest reports progress）
- [ ] 3.2 `--skip-bad`：`cli/ingest.py` + `cli/rebuild.py` 加 flag 傳入 `ingest_dir(skip_bad=False)`；skip 收集 `(path, err)`，結尾 `logger.error` 列清單、JSON 加 `skipped_bad`、exit 1（滿足 spec requirement: Bad-file resilience is explicit opt-in）
- [ ] 3.3 tests：兩壞檔 corpus with/without flag（清單、exit code、fail-fast 保持）
- [ ] 3.4 D5 文件：rebuild CLI help 加「中斷續跑用 ingest dir（hash fast path）」提示；README 運維段補同句（滿足 spec requirement: Interrupted ingest resumes via content-hash fast path）；crash-resume test（60/100 kill 模擬：以 hash fast path 計數斷言）
- [ ] 3.5 `ruff` + `pytest` → commit boundary

## Phase 4 — D6/D7 db 與查詢側

- [ ] 4.1 `core/db.py` `connect()`：WAL 分支加 `PRAGMA synchronous = NORMAL` + 註釋（WAL 下安全性說明）
- [ ] 4.2 `search/vector.py`：候選矩陣 memoize，指紋 `SELECT COUNT(*), MAX(indexed_at) FROM articles_meta`，按 axis 分 key（滿足 spec requirement: Query-time vector matrix is cached with ingest-aware invalidation）
- [ ] 4.3 tests：重複查詢單次建構（計數 mock）、外部 ingest 後指紋變 → 重建
- [ ] 4.4 `ruff` + `pytest` 全套全綠 → commit boundary

## Phase 5 — G4 實驗 + eval guard + PR

- [ ] 5.1 after 計時：branch 版全量 rebuild `/tmp/parity_after.db`，記 before/after 總時長與 rate 曲線（前 2k vs 後 2k 篇）
- [ ] 5.2 向量等價抽查：兩顆 db 隨機 20 篇 `doc_vectors` bytes 比對
- [ ] 5.3 eval guard：80q baseline 對兩顆 db 各跑一次（`wenji serve` + `eval run-benchmark`），分數與 miss 清單不得劣化（eval-regression-guard 流程）
- [ ] 5.4 CHANGELOG Fixed/Changed 條目（1-2 句）+ commit + PR + **audit_release.sh 存 exit code 判斷**（不接 pipe）+ CI 全綠才 merge
- [ ] 5.5 spectra archive + memory 更新（健檢三包 2/3；prod 下次 rebuild 可望 2-4hr）+ 建議主公：prod 重啟命令 THREADS 改 2
