"""Branding environment loader for SEO meta + brand text.

Reads three optional env vars at startup. All unset = no SEO meta is
rendered and templates fall back to a neutral "wenji" brand (safest
zero-config default for fork-friendly open-source distribution).

  - WENJI_SITE_URL     : enables canonical / og:* / JSON-LD output
  - WENJI_SITE_NAME    : brand text in title / og:site_name / publisher
  - WENJI_OG_IMAGE_URL : og:image content

This is the v0.3.7 minimal validator. Full host whitelist (IDN, IPv6,
percent-encoding, length DoS, port restriction, RFC1918 / loopback /
link-local rejection) lives in task 2.1 and lands in a follow-up commit.
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
