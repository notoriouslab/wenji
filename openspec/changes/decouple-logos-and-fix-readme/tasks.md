# Tasks: Decouple logos branding and fix README

## 0. Coverage map (G2 reference)

Maps the original 17 README review findings + 11 G1 security findings to task IDs:

| Source finding | Task ID(s) |
|----------------|-----------|
| 中立稽核 E1 (quickstart `dir`) | 6.1 |
| 中立稽核 E2 (`download-model`) | 6.3 |
| 中立稽核 E3 (test counts) | 6.4 |
| 中立稽核 E4 (axes.yaml syntax) | 6.5 |
| 中立稽核 E5 (rule types) | 6.6 |
| 紅隊 C1 (production checklist) | 6.8 |
| 紅隊 C2 (`/docs` warning) | 6.9 |
| 紅隊 H1 (entity-source path warning) | 6.12 |
| 紅隊 H3 (API key shell rc) | 6.10 |
| 紅隊 H4 (rate-limit warning) | 6.8 |
| 邊界 B1 (ingest dir) | 6.1 |
| 邊界 B2 (PyPI vs disclaimer) | 6.2 |
| 邊界 B3 (examples wheel) | 6.2 |
| 邊界 B4 (eval prereq) | 6.11 |
| 邊界 B5 (Python 3.13) | 6.7 |
| 邊界 S1 (platform matrix) | 6.17 |
| 邊界 S2 (HF mirror) | 6.17 |
| 邊界 S3 (LLM fallback) | 6.13 |
| 邊界 S4 (axes missing) | 6.13 |
| 邊界 S5 (schema migration) | 6.14 |
| 邊界 S6 (eval jitter) | 6.13 |
| 邊界 S7 (deploy/ops) | 6.8 |
| 邊界 S8 (entity-source order) | 6.12 |
| **G1 紅隊 CR-1 (URL host whitelist)** | 2.1, 2.1.1, 2.1.2 |
| **G1 紅隊 CR-2 (context-aware escape)** | 2.2.1, 2.2.2 |
| **G1 紅隊 H-1 (CORS strict)** | 2.4, 2.4.1 |
| **G1 紅隊 H-2 (baseline JSON validation)** | 4.7, 4.8 |
| **G1 紅隊 H-3 (robots CRLF)** | 2.1, 2.5 |
| **G1 紅隊 M-1 (grep scope)** | 8.3 |
| **G1 紅隊 M-3 (.env.example)** | 6.10, 7.4 |
| **G1 紅隊 M-4 (og privacy warning)** | 6.17 |
| **G1 邊界 T1 (loader_logos_v2 path)** | 5.0, 5.1, 5.2 |
| **G1 邊界 H-4 (snapshot_source_path)** | 5.3 |
| **G1 邊界 H-5 (pre-flight verify)** | 1.1 (manual backup), 1.5 (gitignore) — production deploy steps removed after 2026-05-09 实機調查 |
| **G1 邊界 H-6 (commit boundaries)** | end-of-phase commit tasks |

## 1. Pre-flight (simplified after 2026-05-09 production reality check)

Production reality (verified via ssh oracle): nohup uvicorn :8001 with inline command-line env vars (no `.env` file), no auto-deploy hook (no cron/webhook/Actions), `WENJI_CORS_ORIGINS=https://your-deployment.example.com` already explicitly set. Therefore the original 1.2-1.4 production deploy preparation steps are not applicable.

- [x] 1.1 Backup `src/wenji/ingest/loader_logos_db.py` to maintainer's private repo (D4: B1 deletion strategy precondition) — completed 2026-05-09
- [x] 1.5 Verify `.gitignore` in wenji repo contains `.env`, `.env.*`, `.envrc`, and whitelists `.env.example`; add if missing — completed 2026-05-09 (commit 4f23cfc)

## 2. Phase 1 — env-driven branding config (D1: env-driven branding config, D2: SEO meta omission when unset, D3: robots and sitemap defaults, D8: URL host whitelist + IDN normalisation, D9: Context-aware output escape for branding values)

- [x] 2.1 Implement `WENJI_SITE_URL` / `WENJI_OG_IMAGE_URL` host-whitelist validator per D8: URL host whitelist + IDN normalisation: `urllib.parse.urlsplit` + reject userinfo + RFC1918/loopback/link-local/IPv6-equivalent private IP + IDN homograph (`idna.encode` round-trip mismatch) + control characters `[\x00-\x1f\x7f]` + non-default port (only None/80/443) + URL normalisation (strip whitespace, trailing slash) — implements "Site URL controls SEO meta output" + "Site URL host whitelist rejects unsafe forms" requirements
- [x] 2.1.1 Implement `WENJI_ALLOW_PRIVATE_HOST=1` undocumented dev override that bypasses RFC1918/loopback/link-local check
- [x] 2.1.2 Implement `WENJI_SITE_NAME` validator: max 256 chars, reject any of `< > " ' \r \n \x00` — implements "Site name and OG image URL are independently configurable" requirement
- [x] 2.2 Refactor `src/wenji/web/templates/index.html` and `article.html` to wrap canonical, og:*, JSON-LD blocks in `{% if site_url %}` conditional — D2: SEO meta omission when unset
- [x] 2.2.1 Replace JSON-LD inline literal with `{{ ld_json | tojson }}` and verify Jinja2 `tojson` filter encodes `<` as `<` (per D9: Context-aware output escape for branding values)
- [x] 2.2.2 Apply `|e` filter to canonical `href`, og:url `content`, og:image `content`, og:site_name `content` HTML attributes; defense-in-depth even though startup validator already excludes dangerous chars — implements "Branding values are escaped per output context" requirement
- [x] 2.3 Refactor `src/wenji/web/templates/base.html` (if shared) to consume new template variables — D2: SEO meta omission when unset; verify Jinja2 environment uses `Undefined` (not `StrictUndefined`) or apply `default('')` to all branding vars
- [x] 2.4 Replace CORS default in `src/wenji/web/app.py:200` from `https://your-deployment.example.com` to empty list; reject `*` and `null` and elements containing `*` and non-https origins (unless `WENJI_ALLOW_HTTP_CORS=1`) at startup — implements "CORS defaults deny all origins with strict validation" requirement
- [x] 2.4.1 Add `WENJI_ALLOW_HTTP_CORS=1` undocumented dev override and the `WENJI_ALLOW_PRIVATE_HOST` rule covered in 2.1.1
- [x] 2.5 Refactor `/robots.txt` route to emit `User-agent: *\nDisallow: /\n` when site URL unset, and exactly `User-agent: *\nAllow: /\nSitemap: <site_url>/sitemap.xml\n` when set — D3: robots and sitemap defaults; implements "robots.txt enforces conservative defaults"
- [x] 2.6 Refactor `/sitemap.xml` and `/llms.txt` routes to return 404 when site URL unset — D3: robots and sitemap defaults; implements "sitemap.xml and llms.txt require site URL"
- [x] 2.7 Update `tests/wenji/test_web.py` to cover all new scenarios: unset/whitespace/valid/trailing-slash/private-IP/userinfo/IDN/control-char/non-default-port for site URL; site_name HTML metacharacter and length; OG image URL same whitelist; CORS default deny / `*` reject / `null` reject / wildcard subdomain reject / http reject / dev override / multiple comma-separated / empty elements stripped; robots.txt body exact match; sitemap/llms 404
- [x] 2.8 Run `rg "logos\.jacobmei\.com" src/ tests/` to confirm zero matches in code paths — implements "No your-deployment.example.com hardcoded references remain in source" requirement
- [x] 2.9 **Commit boundary**: `feat(branding): env-driven SEO + URL whitelist + escape + strict CORS`

## 3. Phase 2 — D4: B1 deletion strategy

- [x] 3.1 Confirm 1.1 backup completed; then `git rm src/wenji/ingest/loader_logos_db.py` per D4: B1 deletion strategy direct-delete decision — implements "from-logos-db ingest subcommand" REMOVED requirement
- [x] 3.2 Remove `from-logos-db` subcommand from `src/wenji/cli/ingest.py` (typer.command block + imports)
- [x] 3.3 Delete `tests/wenji/test_loader_logos_db.py`
- [x] 3.4 Grep for any remaining import or reference: `loader_logos_db`, `from_logos_db`, `LogosDB`, `dump_logos_db` — case-insensitive — and remove
- [x] 3.5 Update `pyproject.toml` and `src/wenji.egg-info/SOURCES.txt` if either references deleted module (egg-info regenerates on build, manual sync optional)
- [x] 3.6 **Commit boundary**: `chore: remove from-logos-db adapter`

## 4. Phase 3 — D5: baseline flag naming + D10: Baseline JSON validation for sanity-eyeball

- [x] 4.1 Rename CLI flag `--logos-r13` to `--baseline-output` in `src/wenji/cli/eval.py:219` per D5: baseline flag naming decision — implements "sanity-eyeball CLI accepts a generic baseline-output flag" requirement
- [x] 4.2 Replace `logos_data` variable with `baseline_data` in `src/wenji/cli/eval.py:246-268`
- [x] 4.3 Rename `logos_top5` → `baseline_top5` in sample objects, dataclass fields, and any downstream consumers under `src/wenji/eval/` — implements "Comparison output uses neutral baseline_top5 field name" requirement
- [x] 4.4 Update console output strings: "logos top-5:" → "baseline top-5:" and any other "logos" wording in eval module
- [x] 4.5 Update `tests/wenji/test_eval_*` to assert new flag, new field names, rejection of legacy `--logos-r13`
- [x] 4.6 Verify CLI rejects legacy `--logos-r13` flag with clear error pointing to `--baseline-output`
- [x] 4.7 Implement baseline JSON validator per D10: Baseline JSON validation for sanity-eyeball: file size cap 10 MB pre-parse + schema check (top-level dict with `results` array, each element has `q` and `top5`) + reject any string > 64 KB + path must be regular file — implements "baseline-output JSON undergoes schema and size validation" requirement
- [x] 4.8 Implement control-character strip (`re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', s)`) on every baseline value before printing to stdout — D10: Baseline JSON validation for sanity-eyeball
- [x] 4.9 Add unit tests for 4.7/4.8: oversized file rejected, missing `results` rejected, control-char-bearing string sanitised on print, directory path rejected
- [ ] 4.10 Run mini-baseline (10q smoke from snapshot) per D11: Apply commit boundary + retreat protocol — pass@3 partial+ within 5pp of pre-phase baseline; abort phase if regression — **deferred to 8.5** (Phase 3 is CLI/validator only, no retrieval impact)
- [x] 4.11 **Commit boundary** per D11: Apply commit boundary + retreat protocol: `refactor(eval): rename --logos-r13 to --baseline-output + JSON validation`

## 5. Phase 4 — D6: metadata key migration (loader_logos_v2 file rename + dataclass field rename + JSON in-place migrate, no backward-compat)

- [x] 5.0 `git mv src/wenji/eval/loader_logos_v2.py src/wenji/eval/loader_benchmark_v2.py` and `git mv tests/wenji/test_loader_logos_v2.py tests/wenji/test_loader_benchmark_v2.py` — D6: metadata key migration
- [x] 5.1 Rename `SnapshotMetadata.logos_source_commit` → `SnapshotMetadata.source_commit` in renamed loader file (line 36 originally); update docstring (line 12) and validator messages (line 119, 124, 126) — D6: metadata key migration; implements "Benchmark snapshot metadata uses source_commit key without backward compat" requirement; loader MUST raise error if legacy key present without new key
- [x] 5.2 Update all imports `from wenji.eval.loader_logos_v2` → `from wenji.eval.loader_benchmark_v2` across `src/wenji/cli/eval.py`, tests, and any `__init__.py` re-exports — D6: metadata key migration
- [x] 5.3 In-place migrate `tests/benchmark_80_v2_snapshot.json`: rename `logos_source_commit` key → `source_commit`; rename `snapshot_source_path` value from `"tests/benchmark_80.json (logos repo)"` → `"upstream benchmark v2 80q"` — implements scenario "snapshot_source_path generic"
- [ ] 5.4 In-place migrate every existing frozen `wenji_r0_*.json` and any other private baseline output files (run `find . -name 'wenji_r*.json' -not -path './.git/*'` and apply jq script `jq '.metadata.source_commit = .metadata.logos_source_commit | del(.metadata.logos_source_commit)'`) — these files are private artefacts outside git tracking; **retreat note: `git revert` of phase 4 commit will NOT restore these files; the maintainer MUST keep a pre-migration backup (`cp -r baselines baselines.pre-phase4`) before running 5.4 and apply reverse jq if retreat is triggered** — **deferred / maintainer-driven** (private artefacts outside git tracking)
- [x] 5.5 Update `src/wenji/eval/report.py` to emit `source_commit` only — D6: metadata key migration
- [x] 5.6 Update `src/wenji/cli/eval.py:153,178` log/output strings using `logos_source_commit` → `source_commit` — D6: metadata key migration
- [x] 5.7 Add unit test: loader given JSON with only `logos_source_commit` (no `source_commit`) MUST raise error naming the missing key — D6: metadata key migration
- [x] 5.8 Run mini-baseline (10q smoke) — pass@3 partial+ within 5pp of pre-phase baseline; abort phase + start retreat protocol if regression
- [x] 5.9 **Commit boundary**: `refactor(eval): rename loader_logos_v2 to loader_benchmark_v2 + metadata key migration`

## 6. Phase 5 — D7: README finalisation flow (17 review findings + 5 G1 enrichments)

- [x] 6.1 Fix quickstart command: `wenji ingest examples/articles/` → `wenji ingest dir examples/articles/` (lines 49 and 305 per D7: README finalisation flow)
- [x] 6.2 Replace `pip install wenji` with `git clone https://github.com/notoriouslab/wenji && cd wenji && pip install -e .` (lines 48, 304) since PyPI not yet published; explicitly note `examples/` only available in source checkout
- [x] 6.3 Replace `wenji download` references with `wenji download-model` (lines 55, 222)
- [x] 6.4 Update test counts to actual `pytest --collect-only -q | tail -1` output (lines 263, 287) — currently believed 582 unit + 7 integration; re-verify at apply time
- [x] 6.5 Replace incorrect axes.yaml example syntax with content copied from `examples/axes.yaml` ground truth (lines 145-149, 369-373)
- [x] 6.6 Replace rule type list "tag-match / regex-match / all-of / any-of" with actual fields "source_type / tag / title_regex / subtype" AND-combined (lines 151, 375)
- [x] 6.7 Add Python 3.10–3.12 explicit range note to disclaimer (3.13 unsupported per `pyproject.toml requires-python = ">=3.10,<3.13"`)
- [x] 6.8 Add **production checklist** subsection to scenario 2: warn about default unauthenticated server, recommend `WENJI_API_KEY`, `WENJI_CORS_ORIGINS`, bind 127.0.0.1 + reverse proxy + rate limit + Docker/systemd minimal note
- [x] 6.9 Add note about FastAPI `/docs`/`/openapi.json` exposure when `WENJI_API_KEY` unset
- [x] 6.10 Replace `export WENJI_LLM_API_KEY=<your-key>` examples with `.env` file usage; provide `.env.example` reference and `direnv` recommendation; add explicit "DO NOT commit .env, DO NOT shell-rc, verify .gitignore" warning
- [x] 6.11 Add eval prerequisites: must run `wenji serve` in another terminal first, snapshot file path required, mini-baseline 10q smoke recommendation
- [x] 6.12 Correct entity-source security wording: clarify only http/https schemes are rejected, local paths are not sandboxed (file path traversal risk pending separate fix); document multi-source last-write-wins ordering with explicit "rightmost overrides leftmost" example
- [x] 6.13 Add **LLM failure fallback** section: rewrite skipped on timeout/5xx, retrieval still works, ask returns answer=null with citations populated; eval jitter ±1.5pp tolerance; axes.yaml missing → search/ingest unaffected, only sidebar omitted
- [x] 6.14 Add **schema migration** section: pre-v0.3.6 databases must be rebuilt via `rm wenji.db && wenji ingest dir`
- [x] 6.15 Remove `wenji ingest from-logos-db` row from CLI subcommand table (line 209)
- [x] 6.16 Remove all remaining occurrences of "logos" word in user-facing text (verify with grep — exclude code identifiers covered by 8.3)
- [x] 6.17 Add `WENJI_SITE_URL` / `WENJI_SITE_NAME` / `WENJI_OG_IMAGE_URL` documentation in 進階設定 section, including: HTTPS-only requirement, host whitelist behaviour, default omission of SEO meta, **og:image privacy warning** (visitor IP/UA leaked to image host — recommend self-host), platform support matrix (macOS arm64 / Linux x86_64 supported, Intel Mac / Linux ARM experimental, Windows unsupported), HF mirror env vars (`HF_ENDPOINT`)
- [x] 6.18 Update CORS default documentation: empty default, comma-separated origins, dev override `WENJI_ALLOW_HTTP_CORS=1` example for SPA dev (`http://localhost:5173`)
- [x] 6.19 Run final `rg -i "logos" 20260509_README.md` — must return zero matches outside CHANGELOG-quoted historical notes
- [x] 6.20 `cp 20260509_README.md README.md` (preserve draft for rollback) per D7: README finalisation flow; defer `rm 20260509_README.md` until 8.x verification gates pass
- [x] 6.21 **Commit boundary**: `docs: rewrite README and remove logos references`

## 7. Documentation, CHANGELOG, and ignore files

- [x] 7.1 Add v0.3.7 (Unreleased) entry to `CHANGELOG.md`: BREAKING (CORS default empty, `from-logos-db` removed, `--logos-r13` renamed to `--baseline-output`, `logos_source_commit` key replaced with `source_commit`, `loader_logos_v2` module renamed to `loader_benchmark_v2`); Added (`WENJI_SITE_URL`/`SITE_NAME`/`OG_IMAGE_URL`, host whitelist validation, baseline JSON schema validation); Security (URL host whitelist, context-aware escape, strict CORS validation)
- [x] 7.2 Grep `CONTRIBUTING.md` for `from-logos-db`, `loader_logos`, `--logos-r13` mentions and remove or update
- [x] 7.3 Grep `docs/`, `scripts/`, `Makefile`, `.github/workflows/`, `pyproject.toml` for `your-deployment.example.com` or `logos_` identifiers and remove
- [x] 7.4 Create `.env.example` template file with placeholder values for all WENJI_* vars (no real keys); confirm `.gitignore` excludes `.env` and `.env.*` and `.envrc` (1.5 already handles this)
- [x] 7.5 Delete `20260509_README.md` after 8.x gates pass

## 8. Verification gates (G3 final)

- [x] 8.1 Run full `pytest` — all unit tests must pass; record actual collected count for 6.4 verification
- [x] 8.2 Run `ruff check src/wenji tests/wenji` — must report zero issues
- [x] 8.3 Run extended grep `rg "logos\.jacobmei\.com" .` excluding `.git/`, `.venv/`, `node_modules/`, `openspec/changes/decouple-logos-and-fix-readme/` (this change's own design references) — must return exit 1 (no matches)
- [x] 8.4 Run `rg -i 'logos|jacobmei' --glob '!CHANGELOG.md' --glob '!openspec/changes/**' .` — review remaining matches manually; only acceptable matches are within CHANGELOG historical notes
- [ ] 8.5 Run full 80q baseline `wenji eval run-benchmark --no-rewrite` and confirm pass@3 partial+ within 77.5% ± 1.5pp jitter band; if outside, follow D11 retreat protocol (revert phase 4 → 3 → 2 in order, re-running mini-baseline after each) — **deferred to maintainer** (needs live `wenji serve` + DB)
- [ ] 8.6 Smoke-test `wenji serve --db wenji.db` with NO env vars set: confirm `/`, `/tags`, `/article/<id>` render without canonical/og/JSON-LD; confirm `/robots.txt` returns `User-agent: *\nDisallow: /\n`; confirm `/sitemap.xml` and `/llms.txt` return 404 — **deferred to maintainer** (needs live `wenji serve`)
- [ ] 8.7 Smoke-test with `WENJI_SITE_URL=https://wenji.example.com` set: confirm meta tags appear with correct URL; confirm `/sitemap.xml` returns 200 with that URL; confirm canonical URL has no trailing-slash double slash — **deferred to maintainer** (needs live `wenji serve`)
- [ ] 8.8 Adversarial smoke: try starting server with each of `WENJI_SITE_URL=https://attacker.com@evil.com`, `https://169.254.169.254`, `https://[::1]/`, `https://[fe80::1]/`, `https://x.com:8080`, `WENJI_SITE_URL=https://x.com/\nDisallow:`, `WENJI_SITE_URL=https://%6c%6fgos.jacobmei.com/`, `WENJI_SITE_URL=https://x.com/%0d%0aDisallow:`, `WENJI_SITE_URL=https://$(python -c 'print("a"*254)').com/`, `WENJI_SITE_NAME='</script>'`, `WENJI_OG_IMAGE_URL=https://attacker.com@trusted.com/x.png`, `WENJI_OG_IMAGE_URL=https://[::1]/x.png`, `WENJI_CORS_ORIGINS=*`, `WENJI_CORS_ORIGINS=null`, `WENJI_CORS_ORIGINS=https://*.example.com` — each MUST hard-fail at startup with a clear error mentioning the rejection reason — **deferred to maintainer** (adversarial startup test, needs live serve)
- [x] 8.9 Production handover (maintainer-driven, not blocking apply): document in CHANGELOG and commit message that next manual deploy needs to add `WENJI_SITE_URL=https://your-deployment.example.com WENJI_SITE_NAME=<your-brand> WENJI_OG_IMAGE_URL=https://your-deployment.example.com/static/og-image.png` to the inline env vars in the deploy bash command. Provide a short verification curl checklist (canonical / og / robots.txt / sitemap.xml / CORS preflight) for the maintainer to run after their next deploy, but do not gate this change on it.
