# Design: Decouple logos branding and fix README

## Context

wenji 目前有 11 處對外暴露的 `your-deployment.example.com` URL（templates + app.py），CORS 預設 origin 也綁死該 domain。`wenji ingest from-logos-db` adapter 與 `wenji eval sanity-eyeball --logos-r13` 兩個 CLI surface 將「logos」當作功能識別字，前者僅一名使用者使用、後者通用功能但命名綁死。新 README 草稿三個視角審查（中立稽核 / 紅隊 / 邊界）共 17 項問題待修。

**約束**：
- wenji 尚未發行至 PyPI，BREAKING change 影響面極小
- 維護者自己的 logos 部署將受 CORS 預設改 empty 影響，需 .env 顯式設定
- 80q baseline snapshot 是 frozen 資產，內容不可改，僅 metadata key rename

**利害關係人**：
- 維護者（wenji 唯一 maintainer + logos 唯一使用者）
- 未來 fork / 部署 wenji 的開源使用者

## Goals / Non-Goals

**Goals:**
- 對外開源 surface（CLI、API、模板、文件）零 logos 識別字
- 預設行為對 fork 友善：unset 任何品牌 env → 模板省略 SEO，CORS 拒絕全部 origin（fail-safe）
- README 三行 quickstart 真的能跑、不誤導
- BREAKING change 控制在維護者自己的 logos 部署範圍內

**Non-Goals:**
- 不修補 code 層其他 P0/P1（path traversal / SSRF / XSS / cost-DoS）— 另案
- 不引入新部署模式（Docker、systemd 範本）
- 不通用化 `loader_logos_db.py` 的 schema（直接刪，私下另存）

## Decisions

### D1: env-driven branding config

**選項：**
1. env vars（`WENJI_SITE_URL` 等）—— 與既有 `WENJI_LLM_*`、`WENJI_AXES_YAML` 一致
2. `wenji.yaml` 新增 `branding:` section
3. CLI flags（`--site-url`）

**決策：採 1（env vars）**

**理由：**
- 與 wenji 既有 config 風格一致（既有所有 secrets/URL 走 env）
- 12-factor app 慣例，Docker / systemd 部署最自然
- CLI flag 對 SEO 這類部署期靜態值不合適（每次啟動重打）
- `wenji.yaml` 增加 schema 維護成本，且 SEO 是 deployment 層級不是 corpus 層級

**Trade-off：** env 數量會增加（+3）；可接受。

### D2: SEO meta omission when unset

**選項：**
1. 完全省略 SEO meta（純 HTML，不輸出 canonical/og/JSON-LD）
2. 輸出 fallback domain（如 `localhost:8000`）
3. 輸出 placeholder（`https://example.com`）

**決策：採 1（完全省略）**

**理由：**
- 最安全：不洩漏任何品牌、不引入 placeholder 流量
- SEO 對 search corpus 內部使用本就非必要（wenji 主要場景是私人語料，非公開 publishing）
- 模板用 Jinja2 `{% if site_url %}` 條件 block 即可，實作成本低

**Trade-off：** SEO 想開的使用者必須顯式設 env（acceptable，README 文件指路）。

### D3: robots and sitemap defaults

**選項：**
1. unset 時 endpoint 回 404
2. unset 時回 `User-agent: *\nDisallow: /` 的 conservative robots.txt（拒爬）
3. unset 時回 `User-agent: *\nAllow: /` 的 permissive

**決策：採 2（conservative robots，sitemap/llms 回 404）**

**理由：**
- robots.txt 404 會讓爬蟲 default permissive，反而比 disallow 危險
- conservative robots 對私人語料 default 安全：不主動引爬蟲
- sitemap 沒設 site_url 等於沒 URL 可列，404 合理
- llms.txt 同理，404

**Trade-off：** 想公開的使用者必須設 `WENJI_SITE_URL` 才會生效完整 SEO；這正是預期行為。

### D4: B1 deletion strategy

**選項：**
1. 直接 `git rm`，CHANGELOG 註明 BREAKING
2. 加 deprecation warning 一個版本後再刪
3. 移到 `scripts/private/` 但仍在 repo

**決策：採 1（直接刪）**

**理由：**
- wenji 未發行至 PyPI，沒有外部使用者需要 deprecation period
- 維護者本人是唯一使用者，自己拷貝到私 repo 即可
- 留在 repo 即是技術債，違反「對外開源無價值」原則

**Trade-off：** 維護者需在動手前先 `cp src/wenji/ingest/loader_logos_db.py ~/private-tools/`。

### D5: baseline flag naming

**選項：**
1. `--baseline-r13`（保留 r13 識別）
2. `--baseline-output`（更通用）
3. `--reference-run`（語意明確）

**決策：採 2（`--baseline-output`）**

**理由：**
- r13 是 logos 內部 release 編號，外部無意義
- 「baseline output」直白：上一次 run 的 output JSON 當基準
- `--reference-run` 也好但 wenji 既有 metric 用 `baseline_*` 字根（如 `baseline_top5`），保持一致

**Trade-off：** 比 `--baseline-r13` 多打字（acceptable）。

### D6: metadata key migration

**影響檔案（已實機 grep 確認）：**
- `tests/benchmark_80_v2_snapshot.json` — `logos_source_commit` 與 `snapshot_source_path` 內 "logos repo" 字串
- `src/wenji/eval/loader_logos_v2.py` — 整個檔名含 logos；`SnapshotMetadata` dataclass field `logos_source_commit`（line 36）；docstring（line 12）；validator（line 119, 124, 126）
- `src/wenji/cli/eval.py` — log/output 字串 `loaded {len(cands)} candidates from snapshot (commit={meta.logos_source_commit[:8]})`、metadata block `logos_source_commit`
- `src/wenji/eval/report.py` — 報表渲染欄位
- `tests/wenji/test_loader_logos_v2.py` — 整個檔名 + 測試 assertion
- 既有 `wenji_r0_*.json` 輸出 — 維護者私人輸出，逐檔 in-place migrate

**選項：**
1. **直接 rename + 無 backward-compat（in-place migrate）**：所有檔案一次改完，loader 只認新 key，舊 frozen JSON 同步改
2. **Dual-write + dual-read（一個 minor 版本）**：v0.3.7 兩 key 都讀都寫，emit deprecation warning，v0.3.8 砍舊
3. **Schema 版本號 metadata**：JSON 加 `schema_version: 2`，loader 依版本選 key 解碼

**決策：採 1（直接 rename + 無 backward-compat）**

**理由：**
- 維護者是 wenji 唯一使用者 + logos 是唯一既存 frozen JSON 來源 → 沒有外部 frozen 資料需要相容
- 違反 CLAUDE.md「不混搭新舊」全域規則：保留 fallback 等於同時維護兩條讀取路徑，是技術債
- 紅隊 G1 審查指出 backward-compat 是新增 attack surface（log injection via warning、`source_commit` vs `logos_source_commit` 衝突優先序未定義）
- 同時處理 `loader_logos_v2.py` 檔名（含 logos）、`SnapshotMetadata` dataclass field、`snapshot_source_path` 描述字串，避免分批殘留

**Trade-off：** 維護者需 一次性 in-place migrate 所有 frozen `wenji_r0_*.json`（grep 估計 ≤ 5 檔，逐檔 jq 改 key 即可）；無 deprecation period 但本來也沒有外部使用者。

### D8: URL host whitelist + IDN normalisation

紅隊 G1 審查（CR-1）指出單純 scheme 驗證會被 `https://attacker.com@your-deployment.example.com`、IDN homograph、cloud metadata IP（`169.254.169.254`）、CRLF（`\r\n`）注入繞過。

**選項：**
1. 白名單 scheme + 任意 host（原 design）
2. 白名單 scheme + host 黑名單（拒私有 IP）
3. 白名單 scheme + 完整 RFC3986 parse + 拒 userinfo / 私有 IP / IDN homograph / 控制字元 / 非預設 port

**決策：採 3（完整驗證）**

**理由：**
- 黑名單必有繞過（CLAUDE.md 5.6 條），白名單一定要對 host 結構也做白名單
- IDN homograph 必須 `idna.encode` round-trip 才能偵測
- 控制字元（`\r\n`）若進 robots.txt `Sitemap:` 行 = CRLF injection（H3）

**實作要點：**
- `urllib.parse.urlsplit` → 檢查 `username/password is None`、`hostname` 經 `idna.encode` 後 decode 一致
- hostname 不在 RFC1918（`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`）、loopback（`127.0.0.0/8`）、link-local（`169.254.0.0/16`）、IPv6 等價範圍
- port `None`、`80`、`443` 才接受（其他 port 預設拒，但提供 `WENJI_ALLOW_NONSTANDARD_PORT=1` env override 接受 1024-65535 範圍，方便 Fly.io / Cloud Run / ngrok 等動態 port 部署）
- 字串內無 `[\x00-\x1f\x7f]` 控制字元
- hostname 長度 ≤ 253 字元（RFC 1035 上限，防 DOS）
- IPv6 形式 `[::1]`、`[fe80::1]` 同步走 ipaddress.ip_address 判斷是否在 loopback / link-local / private 範圍
- 不接受 percent-encoding 的 host 或 path（如 `%65%78ample.com`、`%0d%0a` CRLF），urlsplit 後對 raw input 直接 grep `%`

**Trade-offs：**
- 拒絕了「我就要部署在內網」的場景；釋放閥：`WENJI_ALLOW_PRIVATE_HOST=1` env override
- 拒絕非標準 port 的硬規則對 Fly.io / Cloud Run（常用 8080 內部 routing）會失敗；釋放閥：`WENJI_ALLOW_NONSTANDARD_PORT=1`
- 兩個 override 仍**不文檔化**（OPEN-4 已 ESTABLISHED：source code 註解標明，避免部署者誤用降低安全性）

### D9: Context-aware output escape for branding values

紅隊 G1 審查（CR-2）指出 Jinja2 預設 HTML autoescape **對 `<script>` 標籤內的 JSON 內容無效**。`WENJI_SITE_NAME='</script><script>alert(1)//'` 直接 break out 為 stored XSS。

**選項：**
1. 對所有品牌值做最嚴格 escape（HTML attribute 規則）
2. Context-aware escape：JSON-LD 用 JSON unicode escape，HTML 屬性用 `|e`，純文字用 `|e`
3. 啟動時拒絕含特殊字元的品牌值（`<`、`>`、`"`、`'`、`\r`、`\n`、`\x00`）

**決策：採 2 + 3 雙層防護**

**理由：**
- 啟動驗證（3）是 fail-fast，部署者立刻知道值有問題
- 模板 escape（2）是 defense-in-depth，即使啟動驗證有漏洞也能擋
- JSON-LD `<` 編碼是 JSON spec 合法的 `<` 表示，不會被瀏覽器解析為 tag
- 字串長度上限 256 字元防 OOM / DOS

**實作要點：**
- 啟動驗證 `WENJI_SITE_NAME` 字元集 + 長度
- 模板 JSON-LD 用 `{{ ld_json | tojson }}` 並驗證 Jinja2 環境的 `tojson` filter 套用 `\uXXXX` 轉碼（safe in script context per OWASP）
- HTML attribute（canonical/og:url）用標準 `|e`

### D10: Baseline JSON validation for sanity-eyeball

紅隊 G1 審查（H-2）指出 `--baseline-output <path>` 接受任意 JSON，攻擊者可投遞含 ANSI escape / 巨大字串 / 異常元素數的 JSON 觸發 OOM 或 console log injection。

**選項：**
1. 只做 `json.load`，仰賴 caller trust
2. Pydantic schema validation
3. 手寫 dataclass + 大小上限 + 控制字元 strip

**決策：採 3（手寫 + 上限）**

**理由：**
- wenji 全域不用 Pydantic（CLAUDE.md「用 plain YAML」原則延伸：用 plain dataclass）
- baseline JSON schema 簡單（top5 array、metadata），手寫成本低
- 大小上限（10MB 檔案、64KB 單字串）+ 控制字元 strip（stdout 印前過 `re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', s)`）即可阻擋紅隊列舉的 OOM / log injection

### D11: Apply commit boundary + retreat protocol

邊界 G1 審查（P1, P2）指出 28 個 task 沒分階段 commit boundary，phase N 失敗時不知 retreat 到哪。

**決策：每 phase 結束後 hard commit + 80q smoke baseline + 失敗 retreat 順序鎖定**

**Commit boundary：**
- Phase 1 完成 → commit `feat(branding): env-driven SEO + URL whitelist + escape`
- Phase 2 完成 → commit `chore: remove from-logos-db adapter`
- Phase 3 完成 → commit `refactor(eval): rename --logos-r13 to --baseline-output + JSON validation`
- Phase 4 完成 → commit `refactor(eval): rename loader_logos_v2 to loader_benchmark_v2 + metadata key migration`
- Phase 5 完成 → commit `docs: rewrite README and remove logos references`

**Retreat protocol（baseline 退步 > 1.5pp）：**
1. 先 revert phase 4（最可能影響 retrieval：metadata loader rename 可能讓 candidates loader fail-soft 後回 0 結果）
2. 重跑 mini-baseline（10q smoke）→ 若回升即停
3. 否則 revert phase 3（rename + validation 可能 reject 某些原本接受的 input）
4. 同上重跑
5. Phase 1/2 影響 retrieval 機率極低，最後才考慮

**Mini-baseline 設置：**
- Phase 3 結束 + Phase 4 結束各跑一次 10 題 smoke（`tests/benchmark_80_v2_snapshot.json` 取前 10）
- 退步 > 5pp（10q 容忍寬一點）即觸發 retreat

### D7: README finalisation flow

**選項：**
1. 直接覆蓋現有 `README.md`，修完所有 17 項
2. 在 `20260509_README.md` 修完後，在 apply 階段 rename 為 `README.md` 取代
3. 分兩個 PR：先合 logos decouple，再合 README

**決策：採 2**

**理由：**
- `20260509_README.md` 已是新結構，直接在上面修 17 項問題
- 完成後 G3 驗證通過再 `mv 20260509_README.md README.md`
- 避免半成品 README 暴露在主分支

## Risks / Trade-offs

- **[Risk] 維護者自己的 logos 部署 CORS 在 deploy 後拒絕請求** → Mitigation：在 logos 部署的 `.env` 顯式設 `WENJI_CORS_ORIGINS=https://your-deployment.example.com`。建議 apply 期間同步更新維護者 logos production 的 env（或寫一段「維護者部署 checklist」備註）
- **[Risk] B3 metadata key 改名後既有 r0 JSON load 失敗** → Mitigation：D6 的 backward-compat 讀取分支
- **[Risk] B2 `--logos-r13` flag 改名後既有 shell history / 文件失效** → Mitigation：CHANGELOG BREAKING 說明，維護者個人 workflow 同步更新
- **[Risk] 模板條件 block 寫錯導致 SEO meta 殘留** → Mitigation：在 G3 加上 grep 檢查（`rg "logos\.jacobmei" src/ tests/`）回 0 為通過條件
- **[Trade-off] 對外乾淨 vs 開發便利** → 維護者自己使用 wenji 時需多設 env，但這是符合 fork-friendly 的正確代價

## Migration Plan

**Production 實況（已實機驗證 2026-05-09）：**
- Oracle VPS，`<prod>/logos`，nohup uvicorn :8001
- 沒有 systemd 自啟（`rag-server.service` 存在但 disabled）、沒有 cron / webhook / GitHub Actions auto-deploy
- Deploy 觸發：維護者手動跑一段 bash command（`git pull origin main && sudo fuser -k 8001/tcp && nohup ... uvicorn ...`）
- Env 配置方式：**inline command line**（不是 `.env` 檔）——env vars 直接寫在 nohup 前
- 既有 env：`WENJI_CORS_ORIGINS=https://your-deployment.example.com` 已顯式設

意味著：
- **無 auto-deploy hook 需要暫停** — 維護者自己決定何時 pull + restart
- **無 `.env` 檔需要預先部署** — env 走 inline command line
- **CORS 預設改 empty 對 production 無影響** — 已顯式設 `WENJI_CORS_ORIGINS`
- **新 SEO env vars（`WENJI_SITE_URL` 等）** — 等 Phase 1 commit 後，維護者下次手動 deploy 時把新 env 加進 nohup 那段 bash command

**部署順序（簡化）：**

1. **Pre-flight：**
   1.1 維護者 backup `loader_logos_db.py` 到私人 repo（B1 刪除前置）— ✅ 維護者已完成 2026-05-09
   1.2 wenji repo `.gitignore` 加 `.env.*`、`.envrc`、`!.env.example` — ✅ 已完成（task 1.5）

2. **Apply：** 套用本 change（按 D11 phase commit boundary）

3. **Post-apply 驗證（在 dev 機器跑）：**
   3.1 跑完整 `pytest` — 全綠
   3.2 跑 80q baseline — 退步 ≤ 1.5pp
   3.3 smoke-test `wenji serve` 三組 env 組合（無 env / SITE_URL only / 完整 env）→ 模板輸出符合 spec
   3.4 Adversarial smoke：14 種惡意 env 輸入 hard-fail at startup

4. **維護者 logos production 切換（維護者自行決定時機，本 change 不阻擋）：**
   4.1 維護者更新自己慣用的 deploy bash command，加上新 SEO env vars：
       ```bash
       ... WENJI_CORS_ORIGINS=https://your-deployment.example.com \
           WENJI_SITE_URL=https://your-deployment.example.com \
           WENJI_SITE_NAME=<your-brand> \
           WENJI_OG_IMAGE_URL=https://your-deployment.example.com/static/og-image.png \
           uvicorn wenji.web.app:app --host 0.0.0.0 --port 8001
       ```
   4.2 維護者手動跑 deploy command（git pull + fuser -k + nohup uvicorn）
   4.3 curl 驗證：搜尋頁 canonical/og/JSON-LD 仍正確、CORS 仍允許 your-deployment.example.com、`/robots.txt` 含 sitemap line、`/sitemap.xml` 仍 200

**Rollback：**
- Phase 內失敗 → `git revert` 該 phase commit（D11 列出每 phase commit 訊息）
- 80q baseline 退步 > 1.5pp（jitter 容忍範圍外）→ 觸發 D11 retreat protocol（依 phase 4 → 3 → 2 順序 revert）
- 維護者 step 4.3 驗證失敗 → 維護者手動跑舊版 deploy command（git checkout 上一個 working commit + 重啟 nohup）

## Open Questions

- **OPEN-1**：~~B3 backward-compat 退場時程~~ —— **已關閉**：D6 改採 in-place migrate，無 backward-compat。
- **OPEN-2**：維護者 logos production 的 `.env` 範本是否寫進 `docs/private/`？建議 ESTABLISHED：**不放 repo**，僅在 Migration Plan step 1.3 與 commit message 提及；避免私人 production 細節污染 open-source repo。
- **OPEN-3**：BGE-M3 ONNX 模型 URL 是否算品牌洩漏？ESTABLISHED：**不處理**，HuggingFace 上是 public model，無 jacobmei 字眼，與 logos 解耦無關。
- **OPEN-4**：`WENJI_ALLOW_PRIVATE_HOST` / `WENJI_ALLOW_HTTP_CORS` / `WENJI_ALLOW_NONSTANDARD_PORT` 三個 dev override env var 是否文檔化？**ESTABLISHED：不文檔化**，僅在 source code（`web/app.py` 各 validator 函式 docstring）標明用途與安全 implications；防止部署者複製貼上 README 而誤用降低安全性。後續若有強烈需求再補 `docs/internals.md`，但本 change 不處理。
- **OPEN-5**：D11 mini-baseline 用前 10 題 smoke 是否代表性夠？**ESTABLISHED：採前 10 題 + 5pp 容忍**作為「快速明顯 regression 偵測」用途，不取代 8.5 的全 80q gate（jitter 容忍 1.5pp）。若 apply 期實機發現 10q 變異 >5pp 但 80q 符合 1.5pp，則僅 mini-baseline 取消、不阻擋 phase commit。
