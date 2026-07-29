# Design — ask-experience-0-6

## Context

`/api/ask` 的答案品質瓶頸經 2026-07-29 實測定位在餵入層，非檢索層（proposal.md 有 0/5 對照表）。沿路挖出兩個既存缺陷共用同一個根因：`build_fts_query` 對無空白的中文問句產生單一「字元連續片語」，導致 chunk 級與文件級 BM25 對口語問句全滅。

本設計的所有數字皆為當日實測，資料源為規章語料鏡像（45 篇 / 296 chunks / 45 doc vectors，`breadoflife-knowledge/data/wenji_policy.db`），LLM baseline 取自 oracle 規章站 :8802（wenji 0.5.2 + Groq llama-3.3-70b）。

## Goals

1. 查數值題從 0/5 提升到可量測的通過率（考卷：`breadoflife-knowledge/tests/policy_qa_set.json`）
2. 引用連結指向真正含答案的段落，而非一律 chunk 0
3. 問答介面從「側欄窄欄 + 缺 CSS」升級為可讀、可追問、可分享的獨立頁面
4. 修掉口語問句下 BM25 雙通道全滅的缺陷，且 80q/v3 不退

## Non-Goals

- 人資規章拆章（語料側；該篇 13,419 字使 doc 向量攤平，是 45 篇中唯一檢索黑洞，另案處理）
- 問題橋接層（檢索側 P2，等考卷 ≥15 題）
- chunk 級向量（2026-07-15 已 G4 判死，不重開）
- 規章領域詞典（`在職進修` 被切成 `職`+`進修`、`短宣` 被切成 `短`+`宣`）：屬語料端配置，不放進通用 OSS 套件；列為 D2 的已知殘留

## Decisions

### D1 — chunk 選取演算法

**問題**：現行 `build_fts_query(原始問句)` 對中文問句永不命中（實測：文件級 0 篇、chunk 級 0 個）。

- **方案 A（採用）**：`jieba_cut_pos` 取詞 → 濾掉標點/代詞/介詞/連詞/助詞/副詞詞性（`x r p c u d w y uj ul zg`）與疑問詞停用表 → 每詞展開為字元片語 → **OR 組合** → `bm25()` 排序。
- 方案 B：維持 AND 但改成整句字元 AND（不要求連續）。否決：實測 jieba 分詞後 AND 組合仍 NONE，AND 對長問句過嚴。
- 方案 C：改用 SQLite libsimple tokenizer 重建 FTS。否決：tokenizer 寫死在 `schema.sql` 的 `tokenize='unicode61'`，改動等於全庫重建（prod 3.5-4.5h），且 ingest 端已做 char-level 預切，收益不明。

**實測（5 題，chunk 定位準確度）**：

| 變體 | rank-1 命中 | top-3 命中 |
|------|-----------|-----------|
| 方案 A，不濾疑問詞 | 2/5 | 4/5 |
| 方案 A，濾疑問詞 | 2/5 | **5/5** |

疑問詞停用表（首批）：`多少 幾年 多久 幾天 怎麼 如何 什麼 可以 要 拿 話 領 去 一起 後 嗎 呢`。

### D2 — 餵入範圍：每篇 top-3 chunk，非 best chunk

**premise 更正**：common-ground K8 原記錄「每篇最佳 chunk」，該選擇建立在「best chunk 選取可用」的前提上。D1 實測顯示 rank-1 只有 2/5、top-3 才 5/5，故前提不成立。

- **方案 A（採用）**：每篇取 top-3 chunk 的 `chunk_text_raw`，依 chunk_index 排序後串接餵入。
- 方案 B：只餵 rank-1 chunk。否決：實測 2/5，等於改完仍有三題答不出。
- 方案 C：餵整篇全文。否決：示範站 12,100 篇長文會爆 token 且拉高延遲；規章站可行但一體適用原則下不可分岔。

**token 估算**：規章語料平均約 450 字/chunk（13,419 字 / 30 chunks）。top-5 篇 × 3 chunks × 450 字 ≈ 6,750 字，約 10k tokens，Groq 128K 上限下有充裕餘裕。

**已知殘留**：`在職進修`、`短宣` 等領域詞被 jieba 切碎，使 rank-1 準確度受限。屬語料端詞典議題（見 Non-Goals），不在本案修。

**`_compose_prompt` 簽名定案（G1 審查補）**：`SourceRef`（`aggregate/__init__.py:47-51`）只有 `article_id/title/snippet/bm25_score`，沒有管道帶 chunk 內文；`ask()` 目前是 citations 與 prompt 各自獨立由 `retrieval` 組出。三個選項：(a) `_compose_prompt` 改吃 `citations: list[Citation]`；(b) 幫 `SourceRef` 加 `chunk_texts` 欄位；(c) `_compose_prompt` 內部重查一次 DB。**採用 (a)**：`Citation` 已是「檢索結果 + chunk 定位」的完整載體，改吃它可零額外查詢且不污染 `aggregate` 共用的 `SourceRef`；(b) 會讓 aggregate 模組帶上只有 ask 用得到的欄位，(c) 重複查詢且與 `_build_citations` 的結果可能不一致。既有測試 `tests/wenji/test_ask.py:246` `test_ask_compose_prompt_lists_sources` 斷言舊簽名與舊格式 `[1] 標題 — 摘要`，必須同案改寫（見 tasks 2.4b）。

**sanitize 順序（G1 審查補）**：`core/safety.py:11` 的 `sanitize_prompt_input` 會把 `<` `>` XML-escape。現行 `_compose_prompt` 是「先組整段 sources_block、再整段 sanitize」，若照此順序嵌入 `<條文>` 標記，標記會被逃逸成 `&lt;條文&gt;`，結構失效且 spec 的形狀鎖測試會失敗。因此改為 **對 `title` 與每段 chunk 內文個別 sanitize，字面標記在 sanitize 之後才組進最終字串**。

**Citation 欄位相容（G1 審查補）**：`_answer_from_dict`（`ask/__init__.py:254`）用 `Citation(**c)` 原樣解包快取列，而 `aggregate/cache.py` 的快取只有 TTL、無 schema 版本。新增欄位若無預設值，升級後 30 天內任何舊快取列被讀到就會 `TypeError` → `/api/ask` 500。因此兩個新欄位一律 `field(default_factory=list)`，且 0.6.0 部署時清一次 `aggregate_cache`（見 Rollout）。

### D3 — ASK_PROMPT 修訂

- **方案 A（採用）**：保留現行四條規則骨架（已驗證的形狀，傷疤一教訓：prompt 是行為不是文案），僅新增一條：引用數字、金額、天數時必須照抄條文原文，不得換算或概括；並把來源區塊格式從 `[i] 標題 — 摘要` 改為 `[i] 標題\n<條文>…</條文>`。
- 方案 B：整段重寫成「條文問答專用」prompt。否決：改動面過大且違反傷疤一（形狀變更等同改演算法），無對照數據支撐。

**鎖形狀**：擴充既有的 `tests/wenji/test_ask.py:238` `test_ask_prompt_template_has_required_clauses`，斷言五條規則標記與 `<條文>` 區塊標記（斷言結構而非全文比對）。註：guard 技能提到的 `tests/wenji/test_search_rewrite.py` 已隨 0.5.0 移除 rewrite 一併刪除，該檔不存在，形狀鎖改沿用上述 ask 測試。

### D4 — 追問改寫

- **方案 A（採用）**：`/api/ask` 新增選填 `history: [{role, content}]`（前端持有）。當 `history` 非空時，先以獨立的改寫 prompt 呼叫一次 LLM，把歷史與新問題壓成一句自含檢索查詢；**改寫結果只用於檢索，不進答案、不顯示給使用者**。改寫失敗（LLM error）時退回「歷史末輪問題 + 新問題」字串串接，不阻斷回答。
- 方案 B：前端字串串接（零 LLM 成本）。否決：追問三四輪後查詢被舊詞彙污染。
- 方案 C：追問不重新檢索。否決：跨題追問（特休 → 借場地）直接答不出。

**cache key**：改寫後的查詢字串納入 cache key，避免同一句追問在不同歷史下共用快取。

### D5 — streaming 傳輸形狀

- **方案 A（採用）**：新增 `GET /api/ask/stream`（SSE，query 走 querystring，含 `history` 的 base64 JSON 選填參數），`LLMClient` 新增 `chat_stream()` 產生 token 迭代器。事件序：`meta`（citations，檢索完成即送）→ 多個 `delta`（答案片段）→ `done`。串流完成後才 `cache_put`；cache 命中則直接送完整 `meta` + 單一 `delta` + `done`。
- 方案 B：POST + `StreamingResponse`。否決：EventSource 只支援 GET，改用 fetch reader 會讓前端重連與錯誤處理自己重寫一遍。
- 方案 C：不做 streaming。否決：使用者明示要做；且餵入量增加後延遲必然上升，空等體驗更差。

**降級**：`WENJI_LLM_*` 未設時 `/api/ask/stream` 回 503，前端自動退回既有 `POST /api/ask`。

**連線生命週期（G1 審查補）**：現行 `POST /api/ask`（`web/app.py:812-843`）在 route function 的 `try/finally` 裡 `asker.db.close()`。SSE 的 generator 橫跨整段串流才被消費完，close 若留在 route function 會在 LLM 還在串流時就關掉連線。因此 `ask_stream` 的連線關閉必須放在 **generator 內部的 `try/finally`**，並涵蓋客戶端中途斷線的情形。

**並行安全（自審補強）**：`web/app.py:300` 的 `_query_lock` 序列化所有碰到 Searcher 共用 SQLite 連線的呼叫，而現行 `POST /api/ask`（`app.py:837`）把 LLM 延遲整段包在鎖內。串流端點若照抄這個形狀，會在整段生成期間（數秒到數十秒）擋住所有搜尋請求。因此 SSE 端點 **只在檢索與 citation 查詢期間持鎖**，取得 citations 後即釋放，LLM 串流階段不持鎖。`cache_put` 需要連線，於串流結束後重新取鎖執行。

**httpx 串流形狀**：`LLMClient.chat_stream` 使用 `httpx.Client.stream("POST", ...)` 逐行解析 `data:` SSE 行、`[DONE]` 結束；沿用 `chat()` 的 `temperature=0.1` 與 header 遮蔽邏輯（錯誤訊息中的 `Bearer` 必須續遮）。現行 `__post_init__` 把 timeout 上限壓在 30s，該值對 httpx 是單次讀取逾時而非總時長，串流長答案不受影響。

### D6 — `/ask` 頁面架構

- **方案 A（採用）**：伺服器渲染頁面骨架（`templates/ask.html`，含 `?q=` 預填與 noindex meta），答案與引用由 `static/ask.js` 經 SSE 填入。兩欄版面（左答案、右引用），視窗 <900px 時單欄堆疊。側欄 panel 保留為快捷入口，共用同一支 JS 與 CSS。
- 方案 B：純前端 SPA。否決：與現有 Jinja 模板體系不一致，且 `?q=` 分享連結需要伺服器端預填才能在無 JS 環境給出有意義內容。

**SEO**：`ask.html` 輸出 `<meta name="robots" content="noindex, nofollow">`，並在 `robots.txt` 加 `Disallow: /ask`。

### D7 — `web:` config 新增鍵

- **方案 A（採用）**：新增 `ask_hint`（問答區說明）、`ask_placeholder`（輸入框範例）、`ask_examples`（範例題清單，空陣列＝隱藏該區）。預設值沿用現行寫死文案，未配置時行為與 0.5.2 逐字相同。
- 方案 B：合併成單一 `ask` 巢狀物件。否決：與現有四個扁平鍵（`hero_title` 等）風格不一致。

### D8 — `/api/search` over-fetch 修復

- **方案 A（採用）**：demo 模式下先取 `max(limit * 5, 50)` 筆再 post-filter，最後截斷回 `limit`。與 index route 既有的 `fetch_limit = 50`（`web/app.py:970`）對齊。
- 方案 B：改成 SQL 層預過濾。否決：`Searcher.search` 不接受 source_type 前置條件，改動面遠大於本案需求。

### D9 — 檢索側同病修復（觸發 eval guard）

**範圍（G1 審查更正）**：影響排名的 chunk 通道入口是 **`search/rrf.py:128`** 的 `chunk_bm25_search`（由 `search/__init__.py:283` 呼叫，產出的 `chunk_signals` 餵進 Step 5 的 `rrf_merge`）。初版誤把範圍寫成 `search/__init__.py:100`，該處位於 `_hydrate_chunk_hits`（`search/__init__.py:81`），只餵排序後的 UI 展示欄位 `chunk_hits`／`matched_chunks`，不影響排名。

- 排名相關（本案必修）：`search/rrf.py:128`、`search/bm25.py:77`
- UI 展示準確度（順手修，不影響排名）：`search/__init__.py:100`

`chunk_signals` 為空字典時 `rrf_merge` 會退化成 main-only + intent boost，這正是 proposal 描述「混合檢索只剩向量單通道」的實際發生位置。

- **方案 A（採用）**：`build_fts_query` 保持原簽名與行為不動（既有呼叫端與測試不受影響），新增 `build_fts_query_or(raw, *, column=None)` 走 D1 演算法；兩個檢索呼叫端改用新函式。
- 方案 B：直接改 `build_fts_query` 的語義。否決：該函式的 AND 語義被既有測試與 docstring 明確約定，就地改語義會讓「哪些呼叫端預期 AND」變得不可辨識。

**Keep/Discard 判定（G4）**：
- Keep 條件：80q v2 gold r14 的 pass@3 partial+ **不低於** before，且 v3 holdout **不低於** before
- after < before → Discard 該項（D9 可獨立回退，D1-D8 不依賴 D9）
- 總分持平但 miss 題換人 → 逐題 diff 記錄後再判
- **禁止**「分數掉一點但邏輯更乾淨所以保留」（傷疤一）

**eval 執行環境（已實測可行，2026-07-29）**：
- db：oracle `~/logos/data/wenji.db`（12,100 篇 / 123,929 chunks，就是歷史 baseline 那顆，ort 環境原生匹配）
- 掛改動：`PYTHONPATH=~/wenji_eval/src ~/logos/.venv/bin/python`（實測覆蓋成功，不動 prod venv、可瞬間還原）
- 跑法：起 scratch port `wenji serve` → `wenji eval run-benchmark`
- 本機 parity db 已被 /tmp 清除；重建需數小時，故不走本機路線

### D10 — 缺失 CSS 的補齊方式

**問題**：`.chat-input-area`、`.chat-submit`、`.chat-select` 在 `style.css` 無任何規則（實測：提問按鈕 `rgb(0,0,0)` 黑字配 `rgb(2,48,71)` 深藍底、padding `1px 6px`、字級 13.3px 為瀏覽器預設；textarea 182×36px）。

- **方案 A（採用）**：新增這三個 class 的規則，並移除 `base.html` 內的 inline `style` 與 `justify-content: space-between` 佈局，改由 CSS 控制。側欄與 `/ask` 頁面共用同一組規則。

  **共用範圍（G1 審查補）**：這三個 class 同時被問答區（`base.html:57`）與文章彙整報告面板（`base.html:80`）使用，故新規則會一併改變彙整面板的按鈕與下拉外觀（方向一致，皆為由不可見變可見）。`.chat-input-area textarea` 用後代選擇器，彙整面板內是 `<input>` 不是 `<textarea>`，無結構衝突。彙整面板需納入 5.9 的 dogfood 截圖確認。
- 方案 B：只改 inline style 補顏色。否決：治不了 textarea 尺寸與按鈕大小，且把樣式繼續留在 template 內與 `web:` 文案可配置的方向背道而馳。

## Risks

- **R1 [SSE 被 Cloudflare tunnel 緩衝，逐字輸出變一次吐出]** → apply 階段在規章站實機驗證（common-ground K14）；若被緩衝則保留端點但前端降級為非串流顯示，不阻斷交付
- **R2 [D9 使 80q/v3 退步]** → G4 判定表；D9 設計為可獨立回退（新函式，呼叫端兩行）
- **R3 [追問改寫多一次 LLM 往返拉高延遲]** → 改寫用短 prompt；量測改寫耗時並記錄；失敗自動降級為字串串接
- **R4 [餵入量增加使 Groq rate limit 或 token 成本上升]** → top-3 上限 + 每 chunk 截斷上限（見 tasks）；規章站使用者為白名單同工，量小
- **R5 [prompt 形狀改動造成品質回歸]** → 傷疤一：形狀鎖進 regression test + 5 題考卷 before/after 對照
- **R6 [`chunk_text` 進 prompt 帶入 markdown 雜訊]** → 沿用既有 `_strip_markdown_for_snippet` 處理後再餵

## Rollout

0.6.0 發佈後：**先清 `aggregate_cache`**（`Citation` 欄位變更，見 D2 相容段）→ 規章站 `pip install -U wenji` + 重啟（runbook A）→ 煙霧 4 題 rank-1 逐字比對 → 5 題考卷 after 對照；示範站下次重啟自然升級。
