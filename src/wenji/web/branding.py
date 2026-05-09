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

This is the v0.3.7 minimal validator. The full host whitelist (IDN,
IPv6, percent-encoding, length DoS, port restriction, RFC1918 /
loopback / link-local rejection) lives in task 2.1 and lands in a
follow-up commit.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SITE_NAME_MAX_LEN = 256
_SITE_NAME_FORBIDDEN_CHARS = ("<", ">", '"', "'", "\r", "\n", "\x00")


@dataclass(frozen=True)
class Branding:
    site_url: str | None = None
    site_name: str | None = None
    og_image_url: str | None = None


def _load_https_url(env_name: str) -> str | None:
    raw = os.environ.get(env_name)
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if not stripped.startswith("https://"):
        raise RuntimeError(
            f"{env_name} must start with https:// (got prefix={stripped[:10]!r}); "
            "refusing to start"
        )
    return stripped.rstrip("/")


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
            continue  # silently strip empty elements
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
            origins.append(origin)
            continue
        if origin.startswith("http://") and allow_http:
            origins.append(origin)
            continue
        raise RuntimeError(
            f"WENJI_CORS_ORIGINS rejects {origin!r}: only https:// origins "
            "accepted by default; for local dev set WENJI_ALLOW_HTTP_CORS=1 "
            "to permit http:// origins"
        )

    return origins
