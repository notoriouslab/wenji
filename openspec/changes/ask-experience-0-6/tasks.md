# Tasks — ask-experience-0-6

七個 phase，每個 phase 結束即為 commit boundary，完成後停下確認再繼續。

## Phase 1 — FTS 查詢建構（D1、D9 的共用基礎）

- [x] 1.1 在 `src/wenji/search/bm25.py` 新增模組級 `INTERROGATIVE_STOPWORDS = {"多少","幾年","多久","幾天","怎麼","如何","什麼","可以","要","拿","話","領","去","一起","後","嗎","呢"}` 與 `DROP_POS = {"x","r","p","c","u","d","w","y","uj","ul","zg"}`
- [x] 1.2 新增 `build_fts_query_or(raw, *, column=None)`：`jieba_cut_pos` 分詞 → 濾 `DROP_POS` 與停用詞 → 濾純標點 token → 每詞轉字元片語 → `OR` 組合；`build_fts_query` 一行不動 ｜ Requirement: FTS query builder offers an OR-combined variant for natural-language input
- [x] 1.3 `tests/wenji/test_search_bm25.py` 新增：中文問句 OR 查詢非空且不含 `多少`/`可以`/`拿` 片語；`build_fts_query("因信稱義")` 仍回 `"因 信 稱 義"` 且無 `OR`；空字串回空字串
- [x] 1.4 Gate：`pytest tests/wenji/test_search_bm25.py -q` 全綠

**Phase 1 補充（apply 中的 self-review 發現）**：`build_fts_query_or` 加上 `MAX_OR_TERMS = 64` 上界。理由：OR 變體讓超長 `q` 變成數千項 FTS5 表達式（實測 4,000 項仍不報錯，但單請求成本高），而舊 AND 版退化成單一片語很便宜；Phase 7 會把此函式接到公開搜尋路徑，故先擋。spec 已同步。

## Phase 2 — 餵入層（D2、D3）

- [x] 2.1 `ask/__init__.py`：`Citation` dataclass 新增 `chunk_texts: list[str] = field(default_factory=list)`、`chunk_indexes: list[int] = field(default_factory=list)`（**必須有預設值**：`_answer_from_dict` 用 `Citation(**c)` 解舊快取列，無預設會 `TypeError` → 500）
- [x] 2.2 `_build_citations` 改用 `build_fts_query_or`，一次查 top-3（`ORDER BY bm25 ASC LIMIT 3`），取 `chunk_index` 與 `chunk_text_raw`；`chunk_index` 保留為 `chunk_indexes[0]`（無命中則 0、兩個 list 為空） ｜ Requirement: `/api/ask` citations carry the source text that grounded the answer
- [x] 2.3 把 `search/__init__.py:46` 的 `_strip_markdown_for_snippet` 公開為 `strip_markdown_for_snippet`（跨 subpackage 共用），更新其 2 處 src 呼叫端（`search/__init__.py:124`、`:340`）與 `tests/wenji/test_search_searcher.py` 的 import 與 5 處使用；不留舊別名
- [x] 2.3a chunk 原文經 `strip_markdown_for_snippet` 處理後存入 `chunk_texts`（R6）
- [x] 2.4 `_compose_prompt` 簽名改為吃 `citations: list[Citation]`（`SourceRef` 無 chunk 內文管道，見 design D2 簽名定案），每篇輸出 `[i] {title}\n<條文>{chunk_texts 串接}</條文>`；無 chunk 命中時退回文件級 snippet
- [x] 2.4a sanitize 順序：對 `title` 與每段 chunk 內文**個別**呼叫 `sanitize_prompt_input`，字面 `<條文>`／`</條文>` 標記在 sanitize 之後才組進最終字串（`core/safety.py:11` 會把 `<` `>` 轉 entity，整段 sanitize 會讓標記失效）
- [x] 2.4b 改寫既有測試 `tests/wenji/test_ask.py:246` `test_ask_compose_prompt_lists_sources`：改用新簽名與新格式斷言（舊斷言 `[1] 標題 — 摘要` 會失敗），並保留「query 出現在 prompt 中」的既有斷言
- [x] 2.4c `ask()` 內呼叫端改為先 `_build_citations`、再把 citations 傳給 `_compose_prompt`（現行兩者各自獨立由 retrieval 組出） ｜ Requirement: The LLM is grounded on clause text, not document summaries
- [x] 2.5 每 chunk 餵入字數上限 1,200 字（超出截斷並附 `…`），每篇最多 3 chunk（R4）
- [x] 2.6 `ask/prompts.py`：`ASK_PROMPT` 新增第 5 條「數字、金額、天數、百分比必須照抄條文原文，不得換算或概括」；四條既有規則不動 ｜ Requirement: Ask prompt requires verbatim numbers
- [x] 2.7 `tests/wenji/test_ask.py` 新增：citation 帶 `chunk_texts`/`chunk_indexes`；prompt 含 `<條文>` 與五條規則標記（形狀鎖：擴充既有 `tests/wenji/test_ask.py:238` `test_ask_prompt_template_has_required_clauses`）；無 chunk 命中時退回 snippet
- [x] 2.8 Gate：`pytest tests/wenji/test_ask.py tests/wenji/test_web_ask.py -q` 全綠
- [x] 2.9 **第一次品質驗收**：oracle 上以 PYTHONPATH 覆蓋跑 5 題考卷，逐題記錄 answer 與 verdict 進 `policy_qa_set.json` 的 `after_phase2` 區塊；baseline 是 0/5

- [x] 2.10 `ask()` 對 LLM 失敗不寫入快取（`llm_failed` 時跳過 `cache_put`）+ 單元測試 ｜ Requirement: Transient LLM failures are not cached

**Phase 2 補充（驗收中發現的既有缺陷，已同案修）**：`ask()` 原本對 LLM 失敗仍執行 `cache_put`，把 `answer=None` 寫進 30 天 TTL 快取，一次 Groq 429 就讓該題整月答不出來（驗收跑 n5 時實際踩到）。修法：`llm_failed` 時跳過 `cache_put`；空 retrieval（未嘗試呼叫 LLM）仍照舊快取。spec 已加 requirement 與測試。

**Phase 2 驗收結果**：查數值題 **0/5 → 4 pass + 1 partial**（n3 數字正確但未說明年齡欄位與總上限）。citation 的 `chunk_indexes` 由全 0 變為真實位置（如 `[6,5,0]`）。詳見 breadoflife-knowledge `tests/policy_qa_set.json` 的 `after_phase2` 區塊。

## Phase 3 — 追問（D4）

- [x] 3.1 `ask/prompts.py` 新增 `FOLLOWUP_REWRITE_PROMPT`：輸入歷史與新問題，輸出單句自含檢索查詢（要求只輸出查詢字串、不加解釋）
- [x] 3.2 `Asker.ask` 新增 `history: list[dict] | None = None` 參數；非空時先呼叫改寫，取得 `retrieval_query`
- [x] 3.3 改寫失敗（`LLMClientError`）時退回「歷史末輪 user 內容 + 空白 + 新問題」，並 `logger.warning`；不得中斷回答
- [x] 3.4 `_cache_key` 納入對話 turns（**非**改寫後查詢，見 design D4 cache key 修正段：keying on rewrite 會讓每次查快取都先付一次 LLM）
- [x] 3.5 `web/app.py` 的 `POST /api/ask` 接收並驗證 `history`（list of `{role,content}`，role 限 `user`/`assistant`，長度上限 10 turn，超出取最後 10） ｜ Requirement: `/api/ask` accepts caller-held conversation history
- [x] 3.6 測試：省略主語追問會用改寫後查詢檢索；改寫失敗仍回 200；無 history 時不呼叫改寫（mock 斷言呼叫次數）；history 格式錯誤回 400（對齊 `api_ask` 既有以 `HTTPException(400)` 驗證 body 的風格）
- [x] 3.7 Gate：`pytest tests/wenji/test_ask.py tests/wenji/test_web_ask.py -q` 全綠

**Phase 3 補充**：改寫輸出採自然問句而非傷疤一的 keyword 形狀，理由見 design D4 輸出形狀段（舊 rewriter 的對照條件已不存在），形狀鎖在 `test_followup_rewrite_prompt_shape_is_locked`。另外 `MAX_HISTORY_TURNS = 10` 同時做 web 層拒絕（400）與 library 層防禦性切片，讓直接用套件的呼叫者也受保護。

## Phase 4 — streaming（D5）

- [x] 4.1 `aggregate/llm.py` 新增 `LLMClient.chat_stream(messages) -> Iterator[str]`：`httpx.Client.stream("POST", ...)` + `stream=True` body，逐行解析 `data:`、遇 `[DONE]` 結束，沿用 `chat()` 的 `temperature=0.1` 與 `Bearer` 遮蔽邏輯
- [x] 4.2 `Asker` 新增 `ask_stream(...)`：檢索與 citation 先算完 yield 一次 meta，再逐段 yield 答案；結束後 `cache_put`
- [x] 4.2a 連線生命週期：`asker.db.close()` 放在 generator 內部的 `try/finally`，涵蓋客戶端中途斷線；不可留在 route function（會在串流中途關掉連線）
- [x] 4.2b 持鎖範圍：`_query_lock` 只包住檢索與 citation 查詢，取得 citations 後釋放；LLM 串流階段不持鎖；`cache_put` 於串流結束後重新取鎖執行（避免整段生成期間擋住所有搜尋，見 design D5 並行安全段）
- [x] 4.3 `web/app.py` 新增 `GET /api/ask/stream`（`StreamingResponse`、`media_type="text/event-stream"`、`X-Accel-Buffering: no`、`Cache-Control: no-cache`），參數 `q`/`k`/`axis`/`history_b64` ｜ Requirement: Streaming ask endpoint
- [x] 4.4 cache 命中路徑：emit `meta` → 單一 `delta`（全文）→ `done`，不呼叫 LLM
- [x] 4.5 未配置 LLM 回 503
- [x] 4.6 測試：事件順序為 meta→delta+→done；cache 命中只有一個 delta 且無 LLM 呼叫；無 LLM 配置回 503；`history_b64` 非法 base64 回 400
- [x] 4.7 Gate：`pytest tests/wenji/test_web_ask.py -q` 全綠

**Phase 4 補充（修掉既有測試污染）**：`tests/wenji/test_aggregate.py` 的 `test_module_import_does_not_hit_network` 用 `importlib.reload` 重載 llm 模組，會把 `LLMClientError` 換成新類別物件，導致該檔之後定義的任何測試用檔頭 import 的舊類別做 `pytest.raises` 時接不到（新增 chat_stream 測試時實際踩到：例外正確丟出卻判 fail）。改為在 throwaway namespace 執行模組本體（需先註冊 `sys.modules`，dataclass 建立時會回查），同樣證明 import 不觸網但不污染，並加斷言確認 live module 未被動過。

**鎖與連線邊界的實作方式**：`ask_stream` 接受 `db_lock` 參數（預設 `nullcontext()`），只在 cache 讀、檢索+citation、cache 寫三段進鎖，LLM 串流段不持鎖；連線關閉放在 route 的 generator `finally`。持鎖範圍有專測 `test_ask_stream_holds_db_lock_only_around_db_work`（用探針鎖斷言串流期間 `held is False`），不靠人工紀律。

## Phase 5 — 前端（D6、D7、D10）

- [x] 5.1 `config/defaults.py` 新增 `ask_hint`、`ask_placeholder`、`ask_examples` 三鍵，預設值逐字沿用 `base.html` 現行文案（`直接輸入問題，由 AI 從語料中檢索並總結回答。`／`例如：靈命成長的關鍵是什麼？`／`[]`）
- [x] 5.2 `config/loader.py` 的 `WebConfig` 加三個欄位與型別驗證（`ask_examples` 非 list 時 raise `ConfigError`） ｜ Requirement: Ask copy is configurable
- [x] 5.3 新增 `templates/ask.html`：兩欄版面、`?q=` 預填、noindex meta、範例題區（`ask_examples` 為空則不渲染） ｜ Requirement: Dedicated ask page
- [x] 5.4 `web/app.py` 新增 `GET /ask` route；`robots.txt` 加 `Disallow: /ask`
**Phase 5 完成（段 A 5.1-5.4/5.8、段 B 5.5-5.7/5.9）**。實測數據（本機 :8803 規章鏡像）：

| 項目 | 段 A 前 | 完成後 |
|------|--------|--------|
| textarea | 182×36px | 1126×121px（頁面）／567×99px（側欄）|
| 提問鈕對比 | 黑字配深藍 1.52:1 | 白字配深藍 **13.85:1** |
| 面板寬度 | 500px | 640px（<768px 全螢幕）|
| 空的維度下拉 | 常駐佔位 | 無 axes 時自動隱藏 |
| 條文截斷 | 無 | line-clamp 3，clientH 87 < scrollH 261，短條文不出現死按鈕 |

段 B 另外處理：文案改走 `templates.env.globals`（側欄在每頁都要用，per-route context 會漏）、彙整面板的 inline style 收進 CSS、`style.css?v=2.6` 與 `ask.js?v=0.6` bump 破快取。

**範圍外但一併修的 production 缺陷**：`/tags` 在 Starlette 1.0 下回 500（route 用舊版 `TemplateResponse(name, context)` 簽名），而該連結在每一頁的導覽列上。已修 `tags_index` 與 `tag_detail` 兩處並加回歸測試。

**已知未修（待維護者決定）**：375px 下 `body.scrollWidth` 515 > 375 橫向溢出，來源是既有 header 的 `.topbar-right`（字級控制 + 書籤計數），非問答頁；修它會動到全站 header。

- [x] 5.5 `static/ask.js` 改寫：SSE 優先、503 時降級為 `POST /api/ask`；維護 client-side turn 陣列；引用區渲染 `chunk_texts` 並 clamp 3 行 + 展開；複製答案按鈕
- [x] 5.6 `static/style.css` 新增 `.chat-input-area`、`.chat-input-area textarea`、`.chat-submit`、`.chat-select`、`.ask-two-col`、`.ask-citation-clamp` 規則；submit 對比 ≥4.5:1；textarea 撐滿容器寬 ｜ Requirement: Ask input controls have explicit styling
- [x] 5.7 `base.html` 移除問答區 inline `style` 與 `space-between` 佈局，文案改讀 config；側欄面板寬度 500→640px（<900px 全螢幕）
- [x] 5.8 測試：`GET /ask` 回 200 且含 noindex；`robots.txt` 含 `Disallow: /ask`；`ask_examples: []` 時 HTML 無範例區；未配置 `web:` 時文案與 0.5.2 逐字相同
- [x] 5.9 Dogfood：本機 :8803 規章鏡像用 browse 截圖（空狀態／串流中／有答案／375px 手機），確認 submit 對比與 textarea 寬度符合 spec；**同步截圖文章彙整報告面板（`#chat-panel`／`aggregate-form`）確認按鈕與下拉外觀未回歸**（三個 class 為兩區共用，見 design D10 共用範圍）

## Phase 6 — over-fetch 修復（D8）

- [x] 6.1 `web/app.py` 的 `api_search`：demo 模式先取 `max(limit * 5, 50)` 再 post-filter，最後 `[:limit]` ｜ Requirement: Demo-mode search over-fetches before post-filtering
- [x] 6.2 測試：demo 模式 `limit=3` 回 3 筆（不再是 0）；`len(results) <= limit`；非 demo 模式行為不變
- [x] 6.3 Gate：`pytest tests/wenji/test_web.py -q` 全綠

## Phase 7 — 檢索側修復與驗收（D9 + 全案 G3/G4）

- [~] 7.1 **已實作後回退（G4 DISCARD）** ：`search/rrf.py:128` 的 `chunk_bm25_search` 改用 `build_fts_query_or`（此處產出的 `chunk_signals` 由 `search/__init__.py:283` 餵進 `rrf_merge`；空字典時 RRF 退化成 main-only，即 proposal 描述的單通道現象） ｜ Requirement: Ranking-relevant chunk signals use the OR builder
- [~] 7.1a **一併回退，另立候選** ：`search/__init__.py:100`（`_hydrate_chunk_hits`）改用 `build_fts_query_or`，讓 `chunk_hits`／`matched_chunks` 對口語問句不再為空
- [~] 7.2 **已實作後回退（G4 DISCARD）** `search/bm25.py:145` 的 `bm25_search` 改用 `build_fts_query_or`
- [x] 7.3 **eval before**（改動前 HEAD）：oracle 上 `PYTHONPATH=~/wenji_eval/src`（指向未含 7.1/7.2 的樹）起 scratch port serve → `wenji eval run-benchmark`（80q v2 gold r14）+ v3 holdout；記下 pass@3 partial+ 與 miss 題清單
- [x] 7.4 **eval after**：同一顆 db、同一命令，樹含 7.1/7.2
- [x] 7.5 G4 判定：80q 與 v3 皆不低於 before → Keep；任一低於 → 回退 7.1/7.2（D1-D8 不依賴 D9）；總分持平但 miss 題換人則逐題 diff 後再判。判定與逐題 diff 寫進本檔
- [x] 7.6 5 題考卷 after 最終對照，結果寫進 `policy_qa_set.json` 的 `after_final` 區塊
- [x] 7.7 規章站煙霧 4 題 rank-1 標題逐字比對（handoff runbook B）
- [ ] 7.8 SSE 實機驗證（R1／common-ground K14）：規章站經 Cloudflare tunnel 確認逐字輸出未被緩衝；若被緩衝則前端降級並記錄
- [x] 7.9 `bash scripts/audit_release.sh`（檢查 exit code，不接 pipe）
- [x] 7.10 `/code-self-review` 六點自審全過
- [ ] 7.11 部署前置：清一次 `aggregate_cache`（`Citation` 欄位變更；預設值已防 `TypeError`，清快取確保引用立即帶 chunk 原文）
- [x] 7.12 CHANGELOG 0.6.0 條目（精簡風格，1-2 句帶過）+ `pyproject.toml` version bump + `pip install -e . --no-deps` 刷新本機 metadata（2026-07-29 完成，`wenji.__version__` 實測回報 0.6.0）

**Phase 7 驗收紀錄（2026-07-29，皆未碰 production）**

| 項目 | 結果 |
|------|------|
| 7.6 考卷 after_final（未拆語料，隔離 code 效果）| 5/5 有答案，與 after_phase2 一致 → D9 回退未影響 ask 品質 |
| 7.6 考卷（已拆語料探測 db）| 檢索不變 + **b1 婚假題首次答出**（引用 rank-1 =「人資規章｜第四章第三節 請假」，並正確補出建卷時漏抓的「一年內請畢、逾期視同放棄」，已查證原文第 235-236 行非捏造）|
| 7.7 煙霧 4 題 | 兩顆 db 皆 4/4 rank-1 標題逐字不變 |
| 7.9 `audit_release.sh` | exit 0（首跑抓到我在 tasks.md 誤用角色稱謂，已修）|
| 7.10 全案六點自審 | 全 PASS；26 檔 +3,056/-134 |

執行期間遭遇 Groq 429（同日累積呼叫過多），非 code 缺陷；帶退避重試後補齊。

## G2 Coverage Mapping

| Decision | 內容 | Tasks |
|----------|------|-------|
| D1 | chunk 選取演算法（分詞 + OR） | 1.1, 1.2, 1.3, 1.4 |
| D2 | 餵入每篇 top-3 chunk | 2.1, 2.2, 2.3, 2.3a, 2.4, 2.4a, 2.4b, 2.4c, 2.5, 2.7 |
| D3 | ASK_PROMPT 修訂 + 形狀鎖 | 2.6, 2.7 |
| D4 | 追問改寫 | 3.1-3.7 |
| D5 | SSE streaming | 4.1-4.7（含 4.2a 連線生命週期、4.2b 持鎖範圍） |
| D6 | `/ask` 獨立頁面 | 5.3, 5.4, 5.5, 5.8, 5.9 |
| D7 | `web:` config 文案鍵 | 5.1, 5.2, 5.7, 5.8 |
| D8 | `/api/search` over-fetch | 6.1, 6.2, 6.3 |
| D9 | 檢索側同病修復 + eval 判定 | 7.1, 7.1a, 7.2, 7.3, 7.4, 7.5 |
| D10 | 缺失 CSS 補齊 | 5.6, 5.7, 5.9 |
| 全案驗收 | 考卷、煙霧、SSE 實機、audit、自審、清快取、發版 | 2.9, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12 |

## Requirement → Task 對照（逐字標題）

| Spec Requirement | Tasks |
|------------------|-------|
| FTS query builder offers an OR-combined variant for natural-language input | 1.1, 1.2, 1.3, 1.4 |
| `/api/ask` citations carry the source text that grounded the answer | 2.1, 2.2, 2.3, 2.3a, 2.7 |
| `/api/ask` accepts caller-held conversation history | 3.1, 3.2, 3.3, 3.4, 3.5, 3.6 |
| Streaming ask endpoint | 4.1, 4.2, 4.2a, 4.2b, 4.3, 4.4, 4.5, 4.6 |
| Ranking-relevant chunk signals use the OR builder | 7.1, 7.1a, 7.3, 7.4, 7.5 |
| Demo-mode search over-fetches before post-filtering | 6.1, 6.2, 6.3 |
| The LLM is grounded on clause text, not document summaries | 2.4, 2.4a, 2.4b, 2.4c, 2.5, 2.7, 2.9 |
| Ask prompt requires verbatim numbers | 2.6, 2.7 |
| Transient LLM failures are not cached | 2.10 |
| Dedicated ask page | 5.3, 5.4, 5.5, 5.8, 5.9 |
| Ask copy is configurable | 5.1, 5.2, 5.7, 5.8 |
| Ask input controls have explicit styling | 5.6, 5.7, 5.9 |

## Design topic → Task 對照（逐字標題）

| Design topic | Tasks |
|--------------|-------|
| D1 — chunk 選取演算法 | 1.1-1.4 |
| D2 — 餵入範圍：每篇 top-3 chunk，非 best chunk | 2.1-2.5, 2.7 |
| D3 — ASK_PROMPT 修訂 | 2.6, 2.7 |
| D4 — 追問改寫 | 3.1-3.7 |
| D5 — streaming 傳輸形狀 | 4.1-4.7 |
| D6 — `/ask` 頁面架構 | 5.3-5.5, 5.8, 5.9 |
| D7 — `web:` config 新增鍵 | 5.1, 5.2, 5.7, 5.8 |
| D8 — `/api/search` over-fetch 修復 | 6.1-6.3 |
| D9 — 檢索側同病修復（觸發 eval guard） | 7.1, 7.1a, 7.2-7.5 |
| D10 — 缺失 CSS 的補齊方式 | 5.6, 5.7, 5.9 |

跨 task 一致性檢查：`build_fts_query_or` 由 1.2 建立，被 2.2（ask citation）、7.1（RRF chunk 訊號）、7.1a（UI chunk hits）、7.2（文件級 BM25）四處消費，三者共用同一停用詞表與 POS 濾除規則，無第二份實作。`chunk_texts` 由 2.1 定義、2.2 填充、5.5 渲染。`ask_*` 三鍵由 5.1 定義、5.2 驗證、5.3/5.7 消費。
