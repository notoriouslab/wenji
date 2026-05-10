# deployment-branding Specification

## Purpose

TBD - created by archiving change 'decouple-logos-and-fix-readme'. Update Purpose after archive.

## Requirements

### Requirement: Site URL controls SEO meta output

The system SHALL only emit canonical, og:*, and JSON-LD schema metadata when the `WENJI_SITE_URL` environment variable is set to a non-empty HTTPS URL that passes host whitelist validation. When unset, empty, or whitespace-only, all such meta tags MUST be omitted from rendered templates. The validated URL MUST be normalised by stripping trailing slashes before being used as a canonical base.

#### Scenario: WENJI_SITE_URL unset
- **WHEN** a user requests `/` or `/article/<id>` with `WENJI_SITE_URL` unset
- **THEN** the response HTML MUST NOT contain `<link rel="canonical">`, `<meta property="og:*">`, or `<script type="application/ld+json">` blocks
- **AND** the page MUST still render search and article content normally

#### Scenario: WENJI_SITE_URL whitespace-only
- **WHEN** the server starts with `WENJI_SITE_URL="   "` (only whitespace)
- **THEN** the value MUST be treated as unset
- **AND** SEO meta MUST NOT render

#### Scenario: WENJI_SITE_URL set to valid HTTPS URL
- **WHEN** a user requests `/article/abc123` with `WENJI_SITE_URL=https://wenji.example.com`
- **THEN** the response MUST contain `<link rel="canonical" href="https://wenji.example.com/article/abc123">`
- **AND** og:url MUST resolve to `https://wenji.example.com/article/abc123`
- **AND** JSON-LD `url` field MUST equal the canonical URL

#### Scenario: WENJI_SITE_URL with trailing slash normalised
- **WHEN** the server starts with `WENJI_SITE_URL=https://wenji.example.com/`
- **THEN** the canonical URL for `/article/abc` MUST be `https://wenji.example.com/article/abc` (no double slash)

#### Scenario: WENJI_SITE_URL set to invalid scheme
- **WHEN** the server starts with `WENJI_SITE_URL=javascript:alert(1)` or `ftp://example.com`
- **THEN** startup MUST fail with a clear error message naming the variable and the rejection reason
- **AND** the server MUST NOT bind a port

---
### Requirement: Site URL host whitelist rejects unsafe forms

The system SHALL parse `WENJI_SITE_URL` via `urllib.parse.urlsplit` at startup and reject the following unsafe forms with hard startup failure: presence of userinfo (username/password), hostname inside RFC1918 / loopback / link-local private ranges (IPv4) and equivalent IPv6 ranges (`::1`, `fe80::/10`, `fc00::/7`) detected via `ipaddress` module, IDN homograph (Unicode hostname whose `idna.encode` round-trip differs from input), control characters (`\x00-\x1f\x7f`), percent-encoding in host or path (any `%` substring in raw input), hostname longer than 253 characters (RFC 1035), and non-default ports (anything other than absent / 80 / 443). An undocumented `WENJI_ALLOW_PRIVATE_HOST=1` env override SHALL bypass the private-IP check for internal deployments only. An undocumented `WENJI_ALLOW_NONSTANDARD_PORT=1` env override SHALL allow ports in the range 1024-65535 to support PaaS routing (Fly.io / Cloud Run / ngrok).

#### Scenario: Userinfo rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://attacker.com@example.com/`
- **THEN** startup MUST fail with an error mentioning userinfo
- **AND** the server MUST NOT bind a port

#### Scenario: Private IP rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://169.254.169.254/` (cloud metadata) or `https://127.0.0.1/` or `https://10.0.0.1/`
- **THEN** startup MUST fail with an error mentioning private/loopback IP
- **AND** the server MUST NOT bind a port

#### Scenario: IDN homograph rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://wenji.exampl.cοm/` (Greek omicron in `cοm`; the `wenji.example.com` ASCII domain looks identical)
- **THEN** startup MUST fail with an error mentioning IDN/punycode mismatch

#### Scenario: Control character rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://x.com/\nDisallow:` (CRLF)
- **THEN** startup MUST fail with an error mentioning control characters
- **AND** robots.txt MUST NOT have an opportunity to render the malicious value

#### Scenario: Non-default port rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://x.com:8080/`
- **THEN** startup MUST fail with an error mentioning unsupported port
- **AND** the server MUST NOT bind a port

#### Scenario: Private host override allows internal deployment
- **WHEN** the server starts with `WENJI_SITE_URL=https://10.0.0.1/ WENJI_ALLOW_PRIVATE_HOST=1`
- **THEN** startup MUST succeed
- **AND** SEO meta MUST render with that URL

#### Scenario: IPv6 loopback rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://[::1]/`
- **THEN** startup MUST fail (IPv6 loopback equivalent to 127.0.0.1)

#### Scenario: IPv6 link-local rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://[fe80::1]/`
- **THEN** startup MUST fail (IPv6 link-local)

#### Scenario: Percent-encoded host rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://%6c%6fgos.example.com/` (percent-encoded "logos")
- **THEN** startup MUST fail with an error mentioning percent-encoding

#### Scenario: Percent-encoded CRLF rejected
- **WHEN** the server starts with `WENJI_SITE_URL=https://x.com/%0d%0aDisallow:`
- **THEN** startup MUST fail with an error mentioning percent-encoding

#### Scenario: Hostname length DoS rejected
- **WHEN** the server starts with `WENJI_SITE_URL` containing a hostname > 253 characters
- **THEN** startup MUST fail with an error mentioning the RFC 1035 length limit

#### Scenario: Nonstandard port allowed with override
- **WHEN** the server starts with `WENJI_SITE_URL=https://x.fly.dev:8080/ WENJI_ALLOW_NONSTANDARD_PORT=1`
- **THEN** startup MUST succeed
- **AND** SEO meta MUST render with `https://x.fly.dev:8080` as base

#### Scenario: Nonstandard port rejected without override
- **WHEN** the server starts with `WENJI_SITE_URL=https://x.com:9000/` (no override)
- **THEN** startup MUST fail with an error mentioning the port restriction and the override env var name

---
### Requirement: Site name and OG image URL are independently configurable

The system SHALL accept `WENJI_SITE_NAME` (string, max 256 characters, rejected if it contains any of `< > " ' \r \n \x00`) and `WENJI_OG_IMAGE_URL` (HTTPS URL, validated by the same host whitelist as `WENJI_SITE_URL`) as independent environment variables. Both MUST be optional and MUST only affect template output when both `WENJI_SITE_URL` and the respective variable are set.

#### Scenario: Site name set, image unset
- **WHEN** `WENJI_SITE_URL=https://x.com WENJI_SITE_NAME="My Wenji"` and `WENJI_OG_IMAGE_URL` unset
- **THEN** og:site_name MUST equal "My Wenji"
- **AND** og:image MUST be omitted from the rendered HTML

#### Scenario: Image set, site name unset
- **WHEN** `WENJI_SITE_URL=https://x.com WENJI_OG_IMAGE_URL=https://x.com/og.png` and `WENJI_SITE_NAME` unset
- **THEN** og:image MUST equal `https://x.com/og.png`
- **AND** og:site_name MUST be omitted from the rendered HTML

#### Scenario: Image set without site URL
- **WHEN** `WENJI_OG_IMAGE_URL=https://x.com/og.png` is set but `WENJI_SITE_URL` is unset
- **THEN** og:image MUST be omitted (because no SEO meta is rendered without site URL)

#### Scenario: Site name with HTML metacharacter rejected
- **WHEN** the server starts with `WENJI_SITE_NAME='</script><script>alert(1)//'`
- **THEN** startup MUST fail with an error mentioning the rejected character class
- **AND** the server MUST NOT bind a port

#### Scenario: Site name oversized rejected
- **WHEN** the server starts with `WENJI_SITE_NAME` whose length exceeds 256 characters
- **THEN** startup MUST fail with an error mentioning the length limit

#### Scenario: OG image URL applies same whitelist as site URL
- **WHEN** the server starts with `WENJI_OG_IMAGE_URL=https://1.2.3.4@evil.com/x.png`
- **THEN** startup MUST fail (userinfo rejected, identical to site-url validation)

---
### Requirement: Branding values are escaped per output context

The system SHALL escape `WENJI_SITE_URL`, `WENJI_SITE_NAME`, and `WENJI_OG_IMAGE_URL` per output context when rendering templates. JSON-LD `<script type="application/ld+json">` blocks MUST escape using JSON unicode escape (e.g., `<` → `<`) so that attempting to break out of `<script>` is impossible. HTML attributes MUST escape via standard HTML attribute encoding (`|e` Jinja2 filter or equivalent).

#### Scenario: JSON-LD encodes script-breakout attempt
- **WHEN** the server has passed startup validation (so `WENJI_SITE_NAME` cannot contain `<`); a hypothetical bypass injects `<` via a downstream code path
- **THEN** the JSON-LD block MUST contain `<` rather than literal `<`
- **AND** the rendered page MUST NOT allow the value to terminate the surrounding `<script>` tag

#### Scenario: HTML attribute encodes quote characters
- **WHEN** rendering canonical URL into `<link rel="canonical" href="...">` attribute
- **THEN** any `"` in the URL value (which the startup validator excludes; this scenario tests defense-in-depth) MUST be encoded as `&quot;`

---
### Requirement: CORS defaults deny all origins with strict validation

The system SHALL default `WENJI_CORS_ORIGINS` to an empty list. When unset or empty, the FastAPI app MUST NOT include `CORSMiddleware`, equivalent to denying all cross-origin requests. When set, the value MUST be split on commas, stripped of whitespace, with empty elements removed; each remaining origin MUST be HTTPS scheme and pass the same host whitelist as `WENJI_SITE_URL` (no userinfo, no private IP, no IDN homograph, no control characters). The literal values `*`, `null`, and any element containing `*` MUST be rejected at startup. An undocumented `WENJI_ALLOW_HTTP_CORS=1` env override SHALL allow `http://` origins for local development only.

#### Scenario: WENJI_CORS_ORIGINS unset
- **WHEN** the server starts with no CORS env var
- **THEN** the OPTIONS preflight response MUST NOT include `Access-Control-Allow-Origin`
- **AND** browser-originated requests from any third-party domain MUST be blocked by browser CORS enforcement

#### Scenario: WENJI_CORS_ORIGINS set to specific origin
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=https://my-frontend.example.com`
- **THEN** OPTIONS preflight from that exact origin MUST succeed
- **AND** OPTIONS preflight from any other origin MUST be denied

#### Scenario: WENJI_CORS_ORIGINS set to multiple origins comma-separated
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=https://a.com,https://b.com`
- **THEN** OPTIONS preflight from both `https://a.com` and `https://b.com` MUST succeed
- **AND** OPTIONS preflight from `https://c.com` MUST be denied

#### Scenario: Empty elements stripped
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=,,https://x.com,`
- **THEN** the parsed list MUST equal `["https://x.com"]`
- **AND** the server MUST start successfully

#### Scenario: WENJI_CORS_ORIGINS set to wildcard
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=*`
- **THEN** the server MUST refuse to start and emit an error explaining wildcard CORS is rejected for safety

#### Scenario: null origin rejected
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=null`
- **THEN** the server MUST refuse to start (sandboxed iframe / file:// origins must not be allowed by default)

#### Scenario: Wildcard subdomain rejected
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=https://*.example.com`
- **THEN** the server MUST refuse to start (wildcard subdomain CORS not supported by default; use `allow_origin_regex` configuration not exposed via this env var)

#### Scenario: HTTP origin rejected without override
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=http://localhost:5173`
- **THEN** the server MUST refuse to start
- **AND** the error MUST mention `WENJI_ALLOW_HTTP_CORS` for dev override

#### Scenario: HTTP origin allowed with dev override
- **WHEN** the server starts with `WENJI_CORS_ORIGINS=http://localhost:5173 WENJI_ALLOW_HTTP_CORS=1`
- **THEN** the server MUST start successfully
- **AND** OPTIONS preflight from `http://localhost:5173` MUST succeed

---
### Requirement: robots.txt enforces conservative defaults

The `/robots.txt` endpoint SHALL emit `User-agent: *\nDisallow: /` when `WENJI_SITE_URL` is unset, and SHALL emit a permissive policy with `Sitemap: <WENJI_SITE_URL>/sitemap.xml` when set.

#### Scenario: robots.txt without site URL
- **WHEN** GET `/robots.txt` and `WENJI_SITE_URL` is unset
- **THEN** the response body MUST equal `User-agent: *\nDisallow: /\n`
- **AND** the response MUST NOT contain any external URL

#### Scenario: robots.txt with site URL
- **WHEN** GET `/robots.txt` and `WENJI_SITE_URL=https://wenji.example.com`
- **THEN** the response body MUST equal exactly `User-agent: *\nAllow: /\nSitemap: https://wenji.example.com/sitemap.xml\n`
- **AND** the response MUST NOT contain any private deployment hostname

---
### Requirement: sitemap.xml and llms.txt require site URL

The `/sitemap.xml` and `/llms.txt` endpoints SHALL return 404 when `WENJI_SITE_URL` is unset. When set, both endpoints SHALL emit content using only that URL as the base.

#### Scenario: sitemap.xml without site URL
- **WHEN** GET `/sitemap.xml` and `WENJI_SITE_URL` is unset
- **THEN** the response status MUST be 404
- **AND** the response body MUST NOT contain any URL

#### Scenario: llms.txt without site URL
- **WHEN** GET `/llms.txt` and `WENJI_SITE_URL` is unset
- **THEN** the response status MUST be 404

#### Scenario: sitemap.xml with site URL
- **WHEN** GET `/sitemap.xml` and `WENJI_SITE_URL=https://x.com`
- **THEN** the XML body MUST contain `<loc>https://x.com/</loc>`
- **AND** MUST NOT contain any private deployment hostname

---
### Requirement: No private deployment URLs hardcoded in source

The repository SHALL contain zero hardcoded private deployment URLs (the maintainer's production hostname or any forker-specific brand URL) in `src/`, `tests/`, `docs/`, `README.md`, `CHANGELOG.md`, or any template after this change is applied. All deployment URLs MUST be supplied via env vars (`WENJI_SITE_URL` etc.); test fixtures MUST use `wenji.example.com` or `example.com` placeholders.

#### Scenario: grep returns zero matches for the maintainer's deploy host
- **WHEN** the maintainer runs their pre-release brand-leak audit against `src/`, `tests/`, `docs/`, `README.md`, and `CHANGELOG.md`
- **THEN** the audit MUST find zero hardcoded references to their production hostname
