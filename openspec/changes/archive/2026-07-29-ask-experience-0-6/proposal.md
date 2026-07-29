# Proposal — ask-experience-0-6

## Why

`/api/ask` 目前答不出「條文裡的數字」，而且原因不是檢索不到，是餵給 LLM 的內容太薄。2026-07-29 在規章語料（45 篇公文，296 chunks）實測 5 題查數值題：

| 題目 | 檢索命中 | 答案 |
|------|---------|------|
| 開公務車出車禍自己要付多少 | rank 1 | 資料中未提及。 |
| 在職進修補助上限與綁約年限 | rank 1 | 資料中未提及…… |
| 帶子女去亞洲短宣補助上限 | rank 1 | 資料中未提及。 |
| 霸凌申訴多久查完 | rank 1 | 資料中未提及…… |
| 短宣團費含機票補助上限 | rank 1 | 資料中未提及 |

**0/5，而檢索 5/5 命中正確文件。** `ask/__init__.py:168` 的 `_compose_prompt` 只組出 `[i] 標題 — 文件級摘要`，LLM 從沒看到條文本身。

實測同時暴露第二個缺陷：5 題共 15 筆 citation 的 `chunk_index` **全為 0**。`_build_citations`（`ask/__init__.py:184`）用 `build_fts_query(原始問句, column="chunk_text")` 去 MATCH，而該函式（`search/bm25.py:22`）按空白切詞後把每段展開成「字元連續片語」；中文問句沒有空白，整句成為單一 phrase，要求那十幾個字在內文連續出現，因此永不命中，靜默退回 chunk 0。連帶後果：兩個站台的引用連結 `#c{index}` 一律跳到文章開頭而非相關段落。

同一個查詢建構函式也是 `search/__init__.py:100`（chunk 訊號）與 `search/bm25.py:77`（文件級 BM25）的入口。實測同一句口語問句：

| 通道 | 命中 |
|------|------|
| 文件級 BM25 `articles_fts` | 0 篇 |
| chunk 訊號 `chunks_fts` | 0 個 |
| 向量 bge-m3 | 正常，rank 1 |

即口語問句下兩個 BM25 通道全滅，混合檢索實際上只剩向量單通道在工作。這與既有紀錄「BM25 系對改寫查詢 0/17 全滅」是同一病根。

介面側另有三個量測到的缺陷：`.chat-input-area`／`.chat-submit`／`.chat-select` 三個 class 在 `static/style.css` 完全沒有規則，導致問答輸入框只有 182×36px、提問按鈕是深藍底配純黑字（`rgb(2,48,71)` / `rgb(0,0,0)`）幾乎不可見；引用區把整段摘要無上限貼出；問答區文案寫死在 template，`web:` config 管不到。

## What Changes

一次改版把「答案品質」與「問答介面」一起做完，並修掉沿路挖出的兩個既存缺陷。

1. **餵入層**：citation 回傳 chunk 原文；prompt 改餵 chunk 原文（`ADDED` 能力）
2. **最佳 chunk 選取**：新增分詞 + OR 組合 + BM25 排序的選取邏輯，取代永不命中的整句 phrase match（`FIXED`）
3. **檢索側同病一併修**：`chunk_signals` 與文件級 BM25 的查詢建構（`FIXED`，**觸發 eval-regression-guard，必跑 80q + v3 before/after**）
4. **追問**：`/api/ask` 接受前端持有的對話歷史；伺服器端以 ask 專用改寫產生自含檢索查詢（改寫只用於檢索，不進答案）
5. **streaming**：新增 SSE 端點 + `LLMClient.chat_stream()`；串完才寫 cache，cache 命中一次吐完
6. **獨立問答頁**：`GET /ask` 兩欄版面、`?q=` 可分享、noindex
7. **文案可配置**：問答區 hint／placeholder／範例題納入 `web:` config
8. **`/api/search` over-fetch 修復**：demo 模式 post-filter 前先過量取回（`FIXED`）
9. **補齊缺失 CSS**：三個無主 class 補上規則，側欄與新頁面共用

## Impact

- **Affected specs**: `search-api-surface`（MODIFIED：`/api/ask` 契約擴充、新增 SSE 端點、over-fetch 修復）、`ask-experience`（ADDED：新 spec，涵蓋問答頁、追問、餵入層、文案配置）
- **Affected code**: `src/wenji/ask/__init__.py`、`src/wenji/ask/prompts.py`、`src/wenji/aggregate/llm.py`、`src/wenji/search/bm25.py`、`src/wenji/search/__init__.py`、`src/wenji/web/app.py`、`src/wenji/web/templates/`（`base.html` + 新 `ask.html`）、`src/wenji/web/static/`（`ask.js`、`style.css`）、`src/wenji/config/{defaults,loader}.py`
- **版本**: 0.6.0（新端點 + 新 config 鍵 + 新頁面，皆為 additive；citation 加欄位不破壞既有讀取）
- **兩站一體適用**：規章站與示範站共用同一套套件，改動同時生效（示範站需重啟才吃到新版）
- **不在 scope**：人資規章拆章（語料側，另案）、問題橋接層（檢索側 P2）、chunk 級向量（已 G4 判死）

## 已知界線

本改版**不會**修好「上班途中跌倒可申請補助嗎」這類題。2026-07-29 實測確認該題病灶是人資規章單篇 13,419 字使 doc-level 向量被攤平（45 篇中唯一檢索黑洞，三題全 MISS >top-20，而其他 44 篇七題全 rank 1-2），屬語料側拆章的工作範圍。
