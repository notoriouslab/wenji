# Deployment Guide

Everything you need to run `wenji serve` beyond localhost.

## Production checklist

`wenji serve` ships with no auth and no rate limiting by default:

- Set `WENJI_API_KEY=<random-32-bytes>` to enable API-key auth. This also
  gates `/docs` and `/openapi.json` (public when no key is set).
- Set `WENJI_CORS_ORIGINS=https://your-frontend.example.com` — the default
  (empty) rejects all cross-origin requests.
- Bind `127.0.0.1` behind a reverse proxy (nginx / Caddy) and rate-limit
  there (`/api/ask` is one LLM billing event per call).
- Docker / systemd: load `WENJI_*` via `EnvironmentFile=/etc/wenji.env` —
  inline `Environment=` or docker `-e` leaks through `systemctl show` / `ps`.
- `axes.yaml` is optional; without it ingest/search work fine, the sidebar
  just has no axis filter.

## Site URL / SEO / CORS

For public deployments, SEO meta and CORS are controlled by env vars. With
everything unset the default is the safest zero-config: no branding exposed,
no cross-origin allowed.

```bash
# .env
WENJI_SITE_URL=https://wenji.example.com             # enables canonical / og:* / JSON-LD
WENJI_SITE_NAME=My Wenji                             # optional, max 256 chars
WENJI_OG_IMAGE_URL=https://wenji.example.com/og.png  # optional; that host sees every visitor IP/UA
WENJI_CORS_ORIGINS=https://my-frontend.example.com,https://api.example.com
```

URL hosts are whitelist-validated at startup — userinfo (`https://a@b.com`),
private-range IPs, IDN homographs, control characters, non-default ports, and
percent-encoded hosts all fail fast. CORS rejects `*` / `null` / wildcard
subdomains / non-https.

Local dev SPA: `localhost:5173` hitting `/api/*` is blocked by the default
CORS; set `WENJI_CORS_ORIGINS=http://localhost:5173 WENJI_ALLOW_HTTP_CORS=1`
during development.

## Secrets hygiene (`WENJI_LLM_*`)

Load LLM credentials via `.env` + `direnv` (copy `.env.example`; never
commit). Do **not** `export WENJI_LLM_API_KEY=...` in `~/.zshrc` or pass
`-e WENJI_LLM_API_KEY=...` to docker — both are visible in process listings.
`.gitignore` already covers `.env` and `.env.*`.

## Pre-flight: `wenji doctor`

```bash
wenji doctor --db wenji.db
```

Validates db consistency (cross-table sanity + sample FTS MATCH) and reports
the recorded build environment (onnxruntime / numpy versions — cross-version
vectors degrade retrieval silently). Exit 1 means an inconsistent db;
`wenji serve` will refuse to bind. For non-Chinese corpora override the
sample keywords: `--sample-keywords k1,k2,k3`.

## Search tuning: `WENJI_CONFIG`

`search.alpha` / `search.candidate_pool` / `search.default_limit` resolve
from a `wenji.yaml` at every entry point (CLI `--config` flag beats the
`WENJI_CONFIG` env var; both unset = built-in defaults):

```yaml
search:
  alpha: 0.25          # BM25/vector fusion weight
  candidate_pool: 50   # top-K per retriever before RRF
  default_limit: 10    # applies when no explicit limit is passed
```

A broken config file fails app startup loudly rather than silently falling
back to defaults.

## Bulk-ingest operations

- **Resume an interrupted run** by re-running the same `wenji ingest dir`
  command — unchanged articles take the content-hash fast path, no
  re-embedding. `rebuild` always starts from a wipe; don't use it to resume.
- **Broken frontmatter**: add `--skip-bad` to skip unparseable files (listed
  at the end, exit 1). The default remains fail-fast.
- **Upgrades**: markdown is the source of truth — no migration scripts.
  Minor schema bumps migrate in place on the next ingest; when in doubt,
  `rm wenji.db && wenji ingest dir <markdown-dir> --db wenji.db`.

## Supported platforms

| Platform | Status | Notes |
|----------|--------|-------|
| macOS arm64 (M1+) | ✅ supported | bundled libsimple binary |
| Linux x86_64 | ✅ supported | bundled libsimple binary |
| macOS x86_64 (Intel) | ⚠️ experimental | compile libsimple yourself |
| Linux ARM | ⚠️ experimental | compile libsimple yourself |
| Windows | ❌ unsupported | no libsimple .dll |

First run of `wenji ingest` / `wenji search` auto-downloads the ONNX BGE-M3
INT8 model (~600 MB) to the user cache. Restricted networks can set
`HF_ENDPOINT=https://hf-mirror.com`.
