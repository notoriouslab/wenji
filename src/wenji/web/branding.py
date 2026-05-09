"""Branding + CORS environment loaders for the web app.

All env vars are optional and validated at startup; misconfiguration
fails fast so a serving process is never reached with a bad value.

Branding (SEO meta + brand text):
  - WENJI_SITE_URL     : enables canonical / og:* / JSON-LD output
  - WENJI_SITE_NAME    : brand text in title / og:site_name / publisher
  - WENJI_OG_IMAGE_URL : og:image content
  All unset = no SEO meta rendered, templates fall back to neutral "wenji".

CORS:
  - WENJI_CORS_ORIGINS  : comma-separated list of allowed origins
  - WENJI_ALLOW_HTTP_CORS=1 : dev override allowing http:// origins
  Unset / empty = empty list = CORSMiddleware not installed (deny all
  cross-origin, safest default for forks).

URL host whitelist (per spec D8): every URL value (`WENJI_SITE_URL`,
`WENJI_OG_IMAGE_URL`, and each `WENJI_CORS_ORIGINS` element) is parsed
with ``urllib.parse.urlsplit`` at startup and rejected if it contains
userinfo / control characters / percent-encoding / non-ASCII hostname /
RFC1918+loopback+link-local IP / hostname > 253 chars / port other than
absent / 80 / 443. Two undocumented dev overrides:
  - WENJI_ALLOW_PRIVATE_HOST=1     : bypass private-IP check
  - WENJI_ALLOW_NONSTANDARD_PORT=1 : permit 1024-65535 ports for PaaS routing

The non-ASCII hostname rule is stricter than spec D8's literal "idna.encode
round-trip" mechanism. Empirically, Greek-omicron homographs round-trip
equal through ``idna``, so the literal mechanism would not catch the spec
scenario it cites; rejecting any non-ASCII host (callers should
pre-punycode their IDN domain to ``xn--...``) provides equivalent
security with zero ambiguity.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SITE_NAME_MAX_LEN = 256
_SITE_NAME_FORBIDDEN_CHARS = ("<", ">", '"', "'", "\r", "\n", "\x00")
_HOSTNAME_MAX_LEN = 253  # RFC 1035
_DEFAULT_HTTPS_PORTS = (None, 80, 443)


@dataclass(frozen=True)
class Branding:
    site_url: str | None = None
    site_name: str | None = None
    og_image_url: str | None = None


def _has_control_char(s: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in s)


def _validate_https_url_host(
    env_name: str, raw: str, *, relax_port_check: bool = False
) -> str:
    """Parse and whitelist-check an HTTPS URL env value; return normalised URL.

    Implements spec D8 "Site URL host whitelist rejects unsafe forms":
    HTTPS-only scheme, no userinfo, no control chars, no percent-encoding,
    ASCII hostname only (callers must pre-punycode IDN), hostname ≤ 253
    chars, no private/loopback/link-local IP (unless WENJI_ALLOW_PRIVATE_HOST=1),
    port absent or 80/443 (unless WENJI_ALLOW_NONSTANDARD_PORT=1 to allow
    1024-65535 for PaaS, or *relax_port_check* True for the
    WENJI_ALLOW_HTTP_CORS local-dev path).

    Returns the URL with trailing slash stripped on success; raises
    ``RuntimeError`` with a clear, actionable message on any rejection.
    """
    if _has_control_char(raw):
        raise RuntimeError(
            f"{env_name} contains control characters (\\x00-\\x1f or \\x7f); "
            "refusing to start"
        )
    if "%" in raw:
        raise RuntimeError(
            f"{env_name} contains percent-encoding; only plain ASCII URLs are accepted "
            "(decode the value yourself before setting the env var)"
        )
    if not raw.startswith("https://"):
        raise RuntimeError(
            f"{env_name} must start with https:// (got prefix={raw[:10]!r}); "
            "refusing to start"
        )

    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise RuntimeError(f"{env_name} is not a parseable URL: {exc}") from exc

    if parts.username is not None or parts.password is not None:
        raise RuntimeError(
            f"{env_name} contains userinfo (username/password before @); "
            "this is rejected to prevent display-name spoofing"
        )

    hostname = parts.hostname
    if not hostname:
        raise RuntimeError(f"{env_name} is missing a hostname")

    if not hostname.isascii():
        raise RuntimeError(
            f"{env_name} hostname {hostname!r} contains non-ASCII characters; "
            "use punycode form (xn--...) for IDN domains"
        )

    if len(hostname) > _HOSTNAME_MAX_LEN:
        raise RuntimeError(
            f"{env_name} hostname is {len(hostname)} chars; RFC 1035 caps at "
            f"{_HOSTNAME_MAX_LEN}"
        )

    allow_private = os.environ.get("WENJI_ALLOW_PRIVATE_HOST", "").strip() == "1"
    if not allow_private:
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise RuntimeError(
                    f"{env_name} hostname is a private/loopback/link-local IP "
                    f"({ip}); set WENJI_ALLOW_PRIVATE_HOST=1 to override for "
                    "internal deployments"
                )
        except ValueError:
            pass

    allow_nonstandard_port = (
        relax_port_check
        or os.environ.get("WENJI_ALLOW_NONSTANDARD_PORT", "").strip() == "1"
    )
    port = parts.port
    if port not in _DEFAULT_HTTPS_PORTS:
        if allow_nonstandard_port and 1024 <= port <= 65535:
            pass
        else:
            raise RuntimeError(
                f"{env_name} port {port} not in (80, 443); set "
                "WENJI_ALLOW_NONSTANDARD_PORT=1 to permit 1024-65535 ports "
                "for PaaS routing (Fly.io / Cloud Run / ngrok)"
            )

    return raw.rstrip("/")


def _load_https_url(env_name: str) -> str | None:
    raw = os.environ.get(env_name)
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    return _validate_https_url_host(env_name, stripped)


def _load_site_name(env_name: str) -> str | None:
    raw = os.environ.get(env_name)
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if len(stripped) > _SITE_NAME_MAX_LEN:
        raise RuntimeError(
            f"{env_name} exceeds {_SITE_NAME_MAX_LEN} chars (got {len(stripped)}); "
            "refusing to start"
        )
    for char in _SITE_NAME_FORBIDDEN_CHARS:
        if char in stripped:
            raise RuntimeError(
                f"{env_name} contains forbidden character {char!r}; refusing to start"
            )
    return stripped


def load_branding_from_env() -> Branding:
    """Load branding env vars at startup; raise RuntimeError on validation failure.

    Unset / whitespace-only env values produce None (no branding rendered).
    Invalid values fail fast at startup so misconfiguration cannot reach
    a serving process.
    """
    return Branding(
        site_url=_load_https_url("WENJI_SITE_URL"),
        site_name=_load_site_name("WENJI_SITE_NAME"),
        og_image_url=_load_https_url("WENJI_OG_IMAGE_URL"),
    )


def load_cors_origins_from_env() -> list[str]:
    """Parse and validate ``WENJI_CORS_ORIGINS`` into a list of allowed origins.

    Unset / empty / whitespace-only → empty list (CORSMiddleware should not
    be installed; deny all cross-origin requests).

    Each origin element MUST:
    - Not be the literal ``*`` (browser-equivalent of "any origin")
    - Not contain any ``*`` character anywhere (no wildcard subdomains)
    - Not be the literal ``null`` (sandboxed iframe / file:// origin)
    - Be ``https://`` scheme by default; ``http://`` is permitted only when
      ``WENJI_ALLOW_HTTP_CORS=1`` is also set (dev override, undocumented).
    - Pass the same host whitelist as ``WENJI_SITE_URL`` (no userinfo,
      no private IP, no IDN, no control chars, no percent-encoding, port
      restrictions). The ``http://`` dev override skips the HTTPS-only
      check but still applies all other host-whitelist rules.

    Empty elements (``,,``) are silently stripped.

    Any violation raises ``RuntimeError`` so the server fails to start.
    """
    raw = os.environ.get("WENJI_CORS_ORIGINS", "")
    if not raw or not raw.strip():
        return []

    allow_http = os.environ.get("WENJI_ALLOW_HTTP_CORS", "").strip() == "1"

    origins: list[str] = []
    for piece in raw.split(","):
        origin = piece.strip()
        if not origin:
            continue
        if origin == "*" or "*" in origin:
            raise RuntimeError(
                f"WENJI_CORS_ORIGINS rejects wildcard origin {origin!r}; "
                "set explicit https://host origins instead"
            )
        if origin == "null":
            raise RuntimeError(
                "WENJI_CORS_ORIGINS rejects 'null' origin; sandboxed / file:// "
                "callers cannot be safely allowed by default"
            )
        if origin.startswith("https://"):
            origins.append(_validate_https_url_host("WENJI_CORS_ORIGINS", origin))
            continue
        if origin.startswith("http://") and allow_http:
            # Apply the host whitelist with a temporary https:// rewrite so
            # the same validator can reject userinfo / private IP / IDN /
            # control chars / percent-encoding for http origins. The dev
            # override implies non-default ports (Vite 5173, etc.) are also
            # acceptable — relax_port_check=True skips the 80/443 gate.
            normalised = _validate_https_url_host(
                "WENJI_CORS_ORIGINS",
                "https://" + origin[len("http://") :],
                relax_port_check=True,
            )
            origins.append("http://" + normalised[len("https://") :])
            continue
        raise RuntimeError(
            f"WENJI_CORS_ORIGINS rejects {origin!r}: only https:// origins "
            "accepted by default; for local dev set WENJI_ALLOW_HTTP_CORS=1 "
            "to permit http:// origins"
        )

    return origins
