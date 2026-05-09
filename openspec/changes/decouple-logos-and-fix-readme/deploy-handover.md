# Deploy Handover — `decouple-logos-and-fix-readme` (v0.3.7)

> Internal maintainer note. Not part of the public CHANGELOG. Covers Phase 8
> task 8.9: what to add to the next manual deploy of `logos.jacobmei.com` so
> the v0.3.7 BREAKING changes do not regress production.

## 1. Update the inline `nohup uvicorn` command

The current production deploy bash (per memory `reference_wenji_logos_topology.md`)
runs uvicorn on Oracle VPS with inline env vars. After pulling v0.3.7, the
following **three new env vars** MUST be added so the SEO meta keeps the
existing `Logos` brand text and og:image; otherwise the templates will
fall back to neutral "wenji" branding and `/sitemap.xml` / `/llms.txt` will
return 404.

Append to the inline env block:

```bash
WENJI_SITE_URL=https://logos.jacobmei.com \
WENJI_SITE_NAME=Logos \
WENJI_OG_IMAGE_URL=https://logos.jacobmei.com/static/og-image.png \
```

`WENJI_CORS_ORIGINS=https://logos.jacobmei.com` is already set in the existing
deploy command — no change needed; the v0.3.7 default-empty CORS will not
affect this deployment.

## 2. Post-deploy curl verification checklist

Run these from any host that can reach the public origin. All MUST pass
before considering the deploy successful.

```bash
SITE=https://logos.jacobmei.com

# (a) canonical + og:* meta on the home page
curl -sf "$SITE/" | rg -o 'rel="canonical" href="[^"]+"|property="og:(url|site_name|image)" content="[^"]+"' | sort -u

# Expected (4 lines):
#   property="og:image" content="https://logos.jacobmei.com/static/og-image.png"
#   property="og:site_name" content="Logos"
#   property="og:url" content="https://logos.jacobmei.com/"
#   rel="canonical" href="https://logos.jacobmei.com/"

# (b) robots.txt — permissive policy with sitemap
curl -sf "$SITE/robots.txt"
# Expected exactly:
#   User-agent: *
#   Allow: /
#   Sitemap: https://logos.jacobmei.com/sitemap.xml

# (c) sitemap.xml returns 200 (not 404)
curl -sI "$SITE/sitemap.xml" | head -1
# Expected: HTTP/2 200

# (d) llms.txt uses Logos brand
curl -sf "$SITE/llms.txt" | head -3
# Expected first line contains: Logos Knowledge Engine

# (e) CORS preflight — allowed origin
curl -sI -X OPTIONS "$SITE/api/search" \
  -H "Origin: https://logos.jacobmei.com" \
  -H "Access-Control-Request-Method: GET" | rg -i 'access-control-allow'
# Expected: access-control-allow-origin: https://logos.jacobmei.com

# (f) CORS preflight — disallowed origin (regression check)
curl -sI -X OPTIONS "$SITE/api/search" \
  -H "Origin: https://attacker.example.com" \
  -H "Access-Control-Request-Method: GET" | rg -i 'access-control-allow' || echo "no allow header (correct)"
```

## 3. Adversarial startup gates (one-time, can run on any host)

These verify task 8.8 — that the v0.3.7 host whitelist actually rejects
malicious env values. Run locally with `wenji serve` against a throwaway DB;
each MUST exit non-zero at startup with a clear rejection message.

```bash
DB=/tmp/wenji-smoke.db
wenji ingest dir examples/articles/ --db "$DB"

# Each of these MUST hard-fail before serving (D8 host whitelist):
WENJI_SITE_URL='https://attacker.com@evil.com'           wenji serve --db "$DB" --port 9999  # userinfo
WENJI_SITE_URL='https://169.254.169.254/'                 wenji serve --db "$DB" --port 9999  # cloud-metadata IP
WENJI_SITE_URL='https://127.0.0.1/'                       wenji serve --db "$DB" --port 9999  # IPv4 loopback
WENJI_SITE_URL='https://10.0.0.1/'                        wenji serve --db "$DB" --port 9999  # RFC1918 private
WENJI_SITE_URL='https://[::1]/'                           wenji serve --db "$DB" --port 9999  # IPv6 loopback
WENJI_SITE_URL='https://[fe80::1]/'                       wenji serve --db "$DB" --port 9999  # IPv6 link-local
WENJI_SITE_URL='https://x.com:8080/'                      wenji serve --db "$DB" --port 9999  # non-default port
WENJI_SITE_URL='https://%6c%6fgos.example.com/'           wenji serve --db "$DB" --port 9999  # percent-encoded host
WENJI_SITE_URL='https://x.com/%0d%0aDisallow:'            wenji serve --db "$DB" --port 9999  # percent-encoded CRLF
$'WENJI_SITE_URL=https://x.com/\nDisallow:'               wenji serve --db "$DB" --port 9999  # raw control char
WENJI_SITE_URL='https://logos.jacobmei.cοm/'              wenji serve --db "$DB" --port 9999  # non-ASCII host (Greek-ο)
WENJI_SITE_NAME='</script>'                                wenji serve --db "$DB" --port 9999  # HTML metachar
WENJI_OG_IMAGE_URL='https://attacker.com@trusted.com/x.png' wenji serve --db "$DB" --port 9999  # og userinfo
WENJI_CORS_ORIGINS='*'                                     wenji serve --db "$DB" --port 9999  # wildcard
WENJI_CORS_ORIGINS='null'                                  wenji serve --db "$DB" --port 9999  # null
WENJI_CORS_ORIGINS='https://*.example.com'                 wenji serve --db "$DB" --port 9999  # wildcard subdomain

# Override probes that MUST succeed:
WENJI_SITE_URL='https://10.0.0.1/' WENJI_ALLOW_PRIVATE_HOST=1   wenji serve --db "$DB" --port 9999  # internal deploy
WENJI_SITE_URL='https://x.fly.dev:8080/' WENJI_ALLOW_NONSTANDARD_PORT=1 wenji serve --db "$DB" --port 9999  # PaaS port
```

For a faster sanity check without the full `wenji serve` boot, run the
loader directly:

```bash
uv run python -c '
import os
os.environ["WENJI_SITE_URL"] = "https://attacker.com@evil.com"
from wenji.web.branding import load_branding_from_env
load_branding_from_env()
'
# Expected: RuntimeError: WENJI_SITE_URL contains userinfo (...)
```

## 4. Rollback if any of the above fails

The Phase 1–4 commits land in dependency order; revert in reverse:

1. Revert Phase 5 docs (`8fddcdf`, `06d1600`, `69732ec`, `177e8ed`) — safe, no
   runtime impact.
2. Revert Phase 4 (`3ff3e0b`) — restores `loader_logos_v2.py`.
3. Revert Phase 3 (`3795bb7`) — restores `--logos-r13`.
4. Revert Phase 2 (`e606c7d`) — restores `from-logos-db`.
5. Revert Phase 1 (`b378e26`, `eebc1fb`) — restores hardcoded brand strings
   and the previous CORS default.

Mini-baseline (`wenji eval run-benchmark --no-rewrite`) after each step
confirms retrieval has not regressed.

## 5. Cleanup tasks AFTER successful deploy

- Delete `20260509_README.md` from the repo root (task 7.5).
- Run `spectra:archive` on this change to move it under
  `openspec/changes/archive/`.
