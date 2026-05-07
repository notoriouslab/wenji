# Wenji 資安與邊界測試審查報告

## Context
使用者要求對 wenji 專案（RAG 檢索 + LLM ask + Web API + CLI）進行資安與邊界條件審查，**不直接修改**，先檢視並報告。本次掃描範圍涵蓋 `src/wenji/{web,ingest,core,config,search,aggregate,ask,cli,eval,classify,examples,observability}`，共 64 個 .py 檔。當前分支 `claude/code-security-review-a6Jq7` 沒有 pending diff，因此採全域審查。

---

## 🔴 High（建議優先處理）

### H1. python-frontmatter 預設使用不安全 yaml.load — 任意程式碼執行
- **位置**: `src/wenji/ingest/frontmatter.py:40` `frontmatter.load(str(p))`
- **風險**: 預設走 `yaml.load`（非 safe_load），可被 `!!python/object/apply` 等 gadget 鏈在 ingest 階段執行任意程式碼。若語料來源為使用者上傳或第三方來源，等同 RCE。
- **建議**: 改用 `frontmatter.loads` + 自帶 `SafeLoader`，或在解析前以 `yaml.safe_load` 預處理 frontmatter 區塊。

### H2. Prompt Injection（多處）
- **位置**:
  - `src/wenji/ask/__init__.py:162` `ASK_PROMPT.format(query=query, sources=sources_block)`
  - `src/wenji/search/rewrite.py:78` `self.prompt_template.format(query=raw)`
  - `src/wenji/web/app.py:288, 394` 與 `src/wenji/aggregate/__init__.py:288` 將 `tag` / `concept` / `q` 直接 `.format()` 嵌入 LLM prompt
- **風險**: 使用者輸入、檢索到的文件內容（含 title / 摘錄）皆無分隔符或轉義，可越獄、改變回答內容、洩漏 system prompt。
- **建議**: 統一以結構化分隔（XML tag / JSON envelope）包裹使用者片段；對關鍵字段做最低限度的清理（去除控制字元、限制長度）。

### H3. Web API 數值參數無上界 — 資源耗盡 / 成本 DoS
- **位置**: `src/wenji/web/app.py:483, 546-548, 570-572, 597-602`
  - `/api/search?limit=` 無上限
  - `/api/ask` 的 `k`、`/api/aggregate/topic|concept` 的 `top_sources` / `per_source` 只檢查 `> 0`
- **風險**: `limit=999999` 或 `k=1000000` 可觸發大量 embedding、LLM 呼叫、DB 全掃，導致記憶體尖峰與 LLM 帳單暴漲。
- **建議**: 加上合理上限（建議 `limit ≤ 200`、`k ≤ 50`、`per_source ≤ 20`）並在 query string 進入處統一驗證。

### H4. 環境變數路徑注入 — 任意檔案讀取
- **位置**: `src/wenji/web/app.py:213-216, 229-233`
  - `WENJI_ENTITY_ALIAS_MAP`、`WENJI_INTENT_SOURCE_TYPES` 直接 `Path(...).read_text()`
- **風險**: 在多租戶或 container override 場景下，可被指向 `/etc/passwd`、Secrets 掛載點等，回應錯誤訊息中可能洩漏內容。
- **建議**: 限制這類路徑必須位於專案 `data/` 或顯式 allow-list 目錄；或一律於部署時固化、移除環境覆寫能力。

### H5. LLM Base URL 來自環境變數 — 潛在 SSRF / API key 外流
- **位置**: `src/wenji/config/llm.py:62`、`src/wenji/web/app.py:104-110`、被 `search/rewrite.py:79`、`aggregate/llm.py` 使用
- **風險**: `WENJI_LLM_BASE_URL` 不做白名單檢查；若部署環境 env 可被外部影響（K8s ConfigMap 注入、CI mis-config），請求會帶 `Authorization: Bearer <key>` 送往攻擊者站點。
- **建議**: 維護受信任 base URL 白名單，或在啟動時 log（脫敏）並要求顯式設定。

---

## 🟡 Medium

### M1. LLM 例外訊息可能洩漏 API Key
- **位置**: `src/wenji/aggregate/llm.py:43-44`、`src/wenji/search/rewrite.py:81`
- **風險**: 廣抓 `except Exception as e: raise LLMClientError(str(e))`；某些 httpx 例外（TLS 錯誤、proxy 錯誤）會在訊息中帶 request headers，將 `Authorization` 帶入 log。
- **建議**: 攔截後輸出固定字串；若需 debug，明確過濾 `Authorization` / `api-key`。

### M2. /healthz 洩漏內部路徑
- **位置**: `src/wenji/web/app.py:302-308`
- **風險**: 回傳 `db_path` 絕對路徑，便於攻擊者偵察。
- **建議**: 只回 `{"status":"ok"}`。

### M3. 無速率限制 / 無認證 / 無 CORS 設定
- **位置**: `src/wenji/web/app.py` 全域
- **風險**: 任意人皆可呼叫 `/api/ask`、`/api/search`，配合 H3 可放大成本攻擊。
- **建議**: 即使內部使用，加上簡易 API key middleware + 每 IP 速率限制（slowapi）；明確拒絕 cross-origin。

### M4. 動態 SQL 表名 f-string
- **位置**: `src/wenji/ingest/__init__.py:435` `conn.execute(f"DELETE FROM {tbl}")`
- **風險**: 目前 `tbl` 為硬編碼 tuple，**目前不可被注入**；但屬危險範式，未來改 refactor 容易引入真正的注入點。
- **建議**: 改成顯式 5 條 DELETE 或以白名單檢查。

### M5. LLM JSON 回應形狀未驗證
- **位置**: `src/wenji/aggregate/llm.py:42` `data["choices"][0]["message"]["content"]`
- **風險**: LLM/proxy 回傳格式變動會 KeyError/IndexError，被廣抓後吞掉，可能讓 cache 寫入空值並污染後續請求。
- **建議**: 用 pydantic / 顯式 isinstance 檢查；解析失敗不快取。

### M6. CLI 路徑未正規化
- **位置**: `src/wenji/cli/eval.py:29` 的 `--candidates / --db / --output`
- **風險**: 雖然 CLI 由本機使用者執行影響有限，但若被打包進服務 wrapper 會放大；`../../../...` 仍可通過 `exists=True`。
- **建議**: 對 output 路徑做 `Path.resolve()` + 限制根目錄。

### M7. 長字串 / Regex 高亮 ReDoS
- **位置**: `src/wenji/web/app.py:71-85` `_highlight_in_html`
- **風險**: 對使用者 query 拆 token 後組成 regex，沒有長度與 token 數上限，可造成回溯爆炸。
- **建議**: 限制 query 長度 ≤ 5KB、token 數 ≤ 32；高亮使用 `re.escape` 並避免巢狀 alternation。

### M8. LLM 呼叫無逾時上限
- **位置**: `src/wenji/search/rewrite.py:19`、`src/wenji/aggregate/llm.py:26`
- **風險**: `WENJI_LLM_TIMEOUT` 由 env 控制無 ceiling，可掛起 worker。
- **建議**: 強制 `min(env_value, 30s)`。

---

## 🟢 Low

- **L1. Markdown 渲染**：`_markdown_renderer({"html": False})` 已關閉 raw HTML，但 LLM 輸出若帶 `[xx](javascript:...)` 仍可能在某些 renderer 下生效；建議加 URL scheme 白名單。`web/app.py:58, 90`
- **L2. LLM 失敗靜默**：`ask/__init__.py:219-225` 在 LLM 失敗時 `answer_text=None` 但仍回 citations，前端無明確標示，使用者易誤判。
- **L3. Eval JSONL 錯誤訊息含結構**：`eval/jsonl.py:142-159` 錯誤訊息包含行號與欄位名稱，僅供本機 CLI 使用時影響低。
- **L4. /api/* 的錯誤回應原樣回拋 ValueError 訊息**：`web/app.py:540, 564`，建議統一錯誤格式。
- **L5. `np.frombuffer` 反序列化向量無形狀防呆**：`search/vector.py:52`，依賴 ingest 端檢查；建議加 defensive shape assert。
- **L6. Hash 用途**：`core/hash.py` 使用 SHA256（OK，僅作 dedup，非安全用途）。

---

## 沒問題的部分（正面確認）
- 主要 SQL 路徑（search、stats、segment、aggregate、eval）皆使用 parameterized query。
- `config/loader.py:123` 使用 `yaml.safe_load`。
- 無 `pickle.load`、`shell=True`、`eval/exec` on LLM 輸出。
- `aggregate/llm.py` 用 `with httpx.Client()` context manager。
- 向量正規化已用 `np.maximum(norms, 1e-12)` 防 div-by-zero。
- chunking（`core/chunk.py:73-88`）邊界條件 OK，無 off-by-one。
- 機密來自 env，無硬編碼 API key。

---

## 建議的修補優先順序
1. **H1（frontmatter unsafe yaml）** — 影響最直接的 RCE 面，先修。
2. **H3（API 數值上限）** — 一行一行加 cap，性價比最高。
3. **H2（prompt injection 統一收斂）** — 加 helper `wrap_user_input()`。
4. **H4 + H5（env path / base_url 白名單）** — 部署面 hardening。
5. **M1 / M5 / M8** — LLM client 一次重構，封裝 timeout cap、redacted error、shape validation。
6. **M3** — 加 middleware（auth + rate limit）。
7. 其餘 Low 視時間追補。

## 驗證方式
- 各項修補應對應新增 test：
  - `tests/wenji/test_ingest_frontmatter.py` 補 `!!python/object/apply` 攻擊向量
  - `tests/wenji/test_web.py` 補超大 limit / 負數 / 空 query 邊界
  - 新增 `test_search_rewrite_safety.py` 驗證 prompt injection 字串會被包進 envelope
- `pytest -q` 應全綠
- 手動：`curl /api/search?limit=999999` 應被拒；`curl /api/ask -d '{"q":"<10KB+ string>"}'` 應 400
