"""Tests for branding env loader (v0.3.7 minimal validator).

Covers WENJI_SITE_URL / WENJI_SITE_NAME / WENJI_OG_IMAGE_URL handling.
Full host whitelist (IDN / IPv6 / percent-encoding / length DoS / port)
is task 2.1 in decouple-logos-and-fix-readme; this file covers the
minimal HTTPS-scheme + name char-class validator only.
"""

from __future__ import annotations

import pytest

from wenji.web.branding import Branding, load_branding_from_env


def test_all_unset_returns_none(monkeypatch):
    monkeypatch.delenv("WENJI_SITE_URL", raising=False)
    monkeypatch.delenv("WENJI_SITE_NAME", raising=False)
    monkeypatch.delenv("WENJI_OG_IMAGE_URL", raising=False)
    b = load_branding_from_env()
    assert b == Branding(site_url=None, site_name=None, og_image_url=None)


def test_whitespace_only_treated_as_unset(monkeypatch):
    monkeypatch.setenv("WENJI_SITE_URL", "   ")
    monkeypatch.setenv("WENJI_SITE_NAME", "\t\n")
    monkeypatch.delenv("WENJI_OG_IMAGE_URL", raising=False)
    b = load_branding_from_env()
    assert b.site_url is None
    assert b.site_name is None


def test_https_url_accepted_and_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("WENJI_SITE_URL", "https://wenji.example.com/")
    monkeypatch.delenv("WENJI_SITE_NAME", raising=False)
    monkeypatch.delenv("WENJI_OG_IMAGE_URL", raising=False)
    b = load_branding_from_env()
    assert b.site_url == "https://wenji.example.com"


def test_non_https_scheme_rejected(monkeypatch):
    monkeypatch.setenv("WENJI_SITE_URL", "javascript:alert(1)")
    with pytest.raises(RuntimeError, match="https://"):
        load_branding_from_env()


def test_http_scheme_rejected(monkeypatch):
    monkeypatch.setenv("WENJI_SITE_URL", "http://example.com")
    with pytest.raises(RuntimeError, match="https://"):
        load_branding_from_env()


def test_og_image_url_same_validation(monkeypatch):
    monkeypatch.delenv("WENJI_SITE_URL", raising=False)
    monkeypatch.delenv("WENJI_SITE_NAME", raising=False)
    monkeypatch.setenv("WENJI_OG_IMAGE_URL", "ftp://example.com/og.png")
    with pytest.raises(RuntimeError, match="https://"):
        load_branding_from_env()


def test_site_name_accepted(monkeypatch):
    monkeypatch.delenv("WENJI_SITE_URL", raising=False)
    monkeypatch.setenv("WENJI_SITE_NAME", "My Wenji")
    monkeypatch.delenv("WENJI_OG_IMAGE_URL", raising=False)
    b = load_branding_from_env()
    assert b.site_name == "My Wenji"


@pytest.mark.parametrize(
    "bad_value",
    [
        "</script>",
        '<script>alert(1)</script>',
        'name with " quote',
        "name with ' apostrophe",
        "with\rcarriage",
        "with\nnewline",
    ],
)
def test_site_name_forbidden_chars_rejected(monkeypatch, bad_value):
    monkeypatch.delenv("WENJI_SITE_URL", raising=False)
    monkeypatch.setenv("WENJI_SITE_NAME", bad_value)
    monkeypatch.delenv("WENJI_OG_IMAGE_URL", raising=False)
    with pytest.raises(RuntimeError, match="forbidden character"):
        load_branding_from_env()


def test_site_name_oversized_rejected(monkeypatch):
    monkeypatch.delenv("WENJI_SITE_URL", raising=False)
    monkeypatch.setenv("WENJI_SITE_NAME", "a" * 257)
    monkeypatch.delenv("WENJI_OG_IMAGE_URL", raising=False)
    with pytest.raises(RuntimeError, match="exceeds"):
        load_branding_from_env()


def test_full_set(monkeypatch):
    monkeypatch.setenv("WENJI_SITE_URL", "https://wenji.example.com")
    monkeypatch.setenv("WENJI_SITE_NAME", "My Wenji")
    monkeypatch.setenv("WENJI_OG_IMAGE_URL", "https://wenji.example.com/og.png")
    b = load_branding_from_env()
    assert b.site_url == "https://wenji.example.com"
    assert b.site_name == "My Wenji"
    assert b.og_image_url == "https://wenji.example.com/og.png"


# --- CORS validator (task 2.4) ---


def test_cors_unset_returns_empty(monkeypatch):
    monkeypatch.delenv("WENJI_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == []


def test_cors_empty_string_returns_empty(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == []


def test_cors_whitespace_only_returns_empty(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "   ")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == []


def test_cors_single_https_origin(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "https://app.example.com")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == ["https://app.example.com"]


def test_cors_multiple_origins_comma_separated(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "https://a.com,https://b.com")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == ["https://a.com", "https://b.com"]


def test_cors_strips_empty_elements(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", ",,https://x.com,")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == ["https://x.com"]


def test_cors_rejects_wildcard(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "*")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    with pytest.raises(RuntimeError, match="wildcard"):
        load_cors_origins_from_env()


def test_cors_rejects_null_origin(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "null")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    with pytest.raises(RuntimeError, match="null"):
        load_cors_origins_from_env()


def test_cors_rejects_wildcard_subdomain(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "https://*.example.com")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    with pytest.raises(RuntimeError, match="wildcard"):
        load_cors_origins_from_env()


def test_cors_rejects_http_without_override(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    with pytest.raises(RuntimeError, match="WENJI_ALLOW_HTTP_CORS"):
        load_cors_origins_from_env()


def test_cors_accepts_http_with_dev_override(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WENJI_ALLOW_HTTP_CORS", "1")
    from wenji.web.branding import load_cors_origins_from_env
    assert load_cors_origins_from_env() == ["http://localhost:5173"]


def test_cors_dev_override_only_accepts_literal_one(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WENJI_ALLOW_HTTP_CORS", "true")  # not "1"
    from wenji.web.branding import load_cors_origins_from_env
    with pytest.raises(RuntimeError, match="WENJI_ALLOW_HTTP_CORS"):
        load_cors_origins_from_env()


def test_cors_rejects_ftp_scheme(monkeypatch):
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "ftp://example.com")
    monkeypatch.delenv("WENJI_ALLOW_HTTP_CORS", raising=False)
    from wenji.web.branding import load_cors_origins_from_env
    with pytest.raises(RuntimeError, match="https://"):
        load_cors_origins_from_env()
