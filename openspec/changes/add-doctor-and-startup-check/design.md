# Design: Doctor CLI + startup consistency check

## Decision context

Prod logos chunks_fts 0-rows 的 silent failure 範式無法被 fail-loud-runtime 擋下（沒進 OperationalError）。需要**主動驗證** db 狀態：(a) 主動診斷 CLI（doctor）；(b) startup 自動 gate（retrieval 入口）。兩件事 share 核心 `check_consistency` function。

## D1 — Module location

選 **`wenji.observability.health`**（與 `observability/stats.py` 並排）。

| 方案 | + | − |
|---|---|---|
| **`observability.health`（pick）** | conceptual home（monitoring 性質）；與 stats 共享 row count 概念，可 cross-reference 但不相互依賴 | — |
| 新 `wenji.doctor/` package | 單一 doctor focus | doctor 只是入口、core 是 health check；放 doctor 名下會讓 startup gate 引用 `wenji.doctor.*` 語義錯（gate 不是 doctor） |
| `wenji.core.health` | 與 db.py 並排（schema 也是 core） | core 是 db / errors / 嚴格基礎；health monitoring 屬 observability |

## D2 — FastAPI startup integration

選 **`lifespan=async_context_manager`**（modern FastAPI 0.93+）。

| 方案 | + | − |
|---|---|---|
| **`lifespan` (pick)** | 官方非 deprecated；clean async context；shutdown 階段也可加（將來） | 須改 `FastAPI(...)` constructor |
| `@app.on_event("startup")` | minimal change | FastAPI 0.93+ deprecated；warning |
| Middleware first-request gate | 不需要 startup hook | 第一個 request 跑 check 是 latency-spike + race window；server 已 bind port 不算「拒絕啟動」 |

## D3 — Sample MATCH default keywords

選 **`("神", "人", "心", "天", "之") + --sample-keywords flag override`**。

| 方案 | + | − |
|---|---|---|
| **中文 default + flag override (pick)** | 對 wenji 主場景（繁中 corpus）安全；非中文 user 有 escape hatch | 5 個 keyword 全 miss 才 FAIL，要選夠通用的字 |
| Hardcoded only | 最簡 | 純英文 corpus → 永遠 false FAIL，無法用 |
| Auto-detect from corpus | 智慧 | 複雜（要先 sample articles_meta 抽 token），bootstrap 問題（FTS 壞了就抽不到） |

選 5 個常見中文字（覆蓋宗教、人文、自然），降低全 miss 機率。覆寫旗標讓非中文用戶 doctor 時自己選。Startup gate 用同 default（不 expose flag）—— 純非中文 OSS user 起 server 會 fail，看到 hint 後可以 `WENJI_HEALTH_KEYWORDS` env 覆寫（spec scope 暫不加 env，留 follow-up）。

## D4 — Inconsistency definition (3 layers, L2 has 4 sub-rules)

選 **L1 + L2(a-d) + L3 同時檢**，issue 累積到 list。

| 方案 | 範圍 | 抓 prod bug? |
|---|---|---|
| 只 L1 | counter ≠ row count | ❌ prod 是 counter=0 + chunks_fts=0「假一致」 |
| 只 L1+L3 | + sample MATCH 全 miss | ⚠ 如果 articles_fts 還在但 chunks_fts 0，L3 用 articles 路徑可能仍 hit→ 漏抓 |
| **L1+L2(a-d)+L3 (pick)** | + cross-table derived sanity | ✅ L2.c 明確抓 articles_meta>0 + chunks_fts=0 場景 |
| L1+L2+L3+L4 schema migration check | + schema 版本 | over-eng（已由 db.py:71 SchemaError 守住） |

L2 的 4 個 sub-rule 都列：
- L2.a: counter > 0 但 table empty
- L2.b: table > 0 但 counter = 0
- L2.c: `articles_meta` rows > 0 但 `chunks_fts` rows = 0（**prod bug 範式**）
- L2.d: `articles_meta` rows > 0 但 `doc_vectors` rows = 0（embedding 漏）

L1 + L2.a + L2.b 是 self-consistency；L2.c + L2.d 是 cross-table derivation 規則（articles 是 source、chunks/vectors 是 derived）。

## D5 — CLI integration for retrieval entry points

選 **`_ensure_consistency(db_path)` helper + 在每個目標 subcommand body 開頭 call**。

| 方案 | + | − |
|---|---|---|
| **Helper + per-subcommand call (pick)** | explicit；每個 subcommand 自己 own startup gate；不影響 ingest / read-only diagnose 等 skip 場景 | tasks.md 列每個 entry 點，apply 時逐一加 |
| `typer.Typer(callback=...)` 集中 | 一個 callback 攔截全 sub | callback 不一定知道 db_path（不同 subcommand 取 db path 方式不同） |
| Decorator wrap | DRY-er | 與既有 typer command 風格不一致（既有 subcommand 都直接 def + register） |

## D6 — Report representation

選 **`@dataclass class ConsistencyReport`**（with `issues: list[str]` + `ok` property）。

| 方案 | + | − |
|---|---|---|
| **dataclass + issues list (pick)** | 結構化 + 可序列化；issues 自帶 hint 訊息；`ok` derived | — |
| 純 dict | 最簡 | static type loose；caller 要 know key |
| `Result[Report, list[Issue]]` | 顯式 OK/FAIL | over-engineered；Issue class 不需要結構化欄位 |

## D7 — Sample MATCH SQL pattern

`SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH ?` per keyword × 2 tables = 10 queries (5 keyword × 2 table)。每 query 對 12k corpus < 5ms；總 ~50ms。可接受。

`build_fts_query` (from `wenji.search.bm25`) 已 sanitise input；reuse 它生 MATCH query 一致。

## D8 — Counter source

選 **`SELECT value FROM wenji_meta WHERE key = ?`**（既有 schema）。Counter 維護由 `ingest/__init__.py` 寫入；本 spec 只讀。

不在本 spec 範圍：修補 ingest 寫 counter 的 logic（如果 prod 是寫 counter 邏輯壞掉造成的 0/0，那是 ingest spec）。

## Out-of-scope decisions

- doctor `--repair` mode（auto-rebuild on inconsistency）：留 follow-up
- `WENJI_HEALTH_KEYWORDS` env override for startup gate：留 follow-up（純非中文 OSS user 反饋後再加）
- doctor 跑 schema migration check：已由 `core/db.py:69-71` 守住，不重複
- `wenji.observability.stats` 引用 health：留 future（stats 與 health 互不依賴比較乾淨）

## Migration risk

- **Logos prod**：startup gate 上線後，prod 下次 deploy 會 fail 啟動（chunks_fts 0 rows）。主公要先 `wenji ingest dir articles/ --rebuild` 修狀態。這是 fail-loud 設計目標。
- **OSS user**：build 不完整 db → startup 拒絕；error 指向 `wenji doctor` 拿 detail。可接受 + 對 user 友善。
- **CHANGELOG** 標 Added，不算 BREAKING（新功能 + 新 gate；既有正常 db 不受影響）。
