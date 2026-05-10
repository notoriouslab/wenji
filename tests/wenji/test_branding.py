"""Tests for branding env loader (v0.3.7).

Covers WENJI_SITE_URL / WENJI_SITE_NAME / WENJI_OG_IMAGE_URL / WENJI_CORS_ORIGINS
handling, including the spec D8 host whitelist (userinfo / private IP / IPv6 /
non-ASCII host / percent-encoding / control chars / hostname length / port).
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
        "<script>alert(1)</script>",
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


# ---- D8 URL host whitelist (task 2.1 follow-up) ----


def _clear_overrides(monkeypatch):
    for key in (
        "WENJI_SITE_URL",
        "WENJI_SITE_NAME",
        "WENJI_OG_IMAGE_URL",
        "WENJI_CORS_ORIGINS",
        "WENJI_ALLOW_HTTP_CORS",
        "WENJI_ALLOW_PRIVATE_HOST",
        "WENJI_ALLOW_NONSTANDARD_PORT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_d8_userinfo_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://attacker.com@example.com/")
    with pytest.raises(RuntimeError, match="userinfo"):
        load_branding_from_env()


def test_d8_ipv4_private_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://10.0.0.1/")
    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_branding_from_env()


def test_d8_ipv4_link_local_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://169.254.169.254/")
    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_branding_from_env()


def test_d8_ipv4_loopback_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://127.0.0.1/")
    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_branding_from_env()


def test_d8_ipv6_loopback_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://[::1]/")
    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_branding_from_env()


def test_d8_ipv6_link_local_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://[fe80::1]/")
    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_branding_from_env()


def test_d8_non_ascii_hostname_rejected_homograph(monkeypatch):
    """Greek-omicron in `cοm` (U+03BF). Non-ASCII hostname rule rejects all IDN
    forms; callers must pre-punycode their domain to xn--... before setting env."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://wenji.exampl.cοm/")
    with pytest.raises(RuntimeError, match="non-ASCII"):
        load_branding_from_env()


def test_d8_punycode_hostname_accepted(monkeypatch):
    """Pre-punycoded IDN (xn--...) is plain ASCII and accepted."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://xn--fiqs8s.example/")
    b = load_branding_from_env()
    assert b.site_url == "https://xn--fiqs8s.example"


def test_d8_percent_encoded_host_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://%6c%6fgos.example.com/")
    with pytest.raises(RuntimeError, match="percent-encoding"):
        load_branding_from_env()


def test_d8_percent_encoded_path_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://x.com/%0d%0aDisallow:")
    with pytest.raises(RuntimeError, match="percent-encoding"):
        load_branding_from_env()


def test_d8_control_char_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://x.com/\nDisallow:")
    with pytest.raises(RuntimeError, match="control characters"):
        load_branding_from_env()


def test_d8_hostname_too_long_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://" + ("a" * 254) + ".com/")
    with pytest.raises(RuntimeError, match="RFC 1035"):
        load_branding_from_env()


def test_d8_nonstandard_port_rejected(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://x.com:8080/")
    with pytest.raises(RuntimeError, match="port 8080"):
        load_branding_from_env()


def test_d8_default_port_443_accepted(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://x.example:443/")
    b = load_branding_from_env()
    assert b.site_url == "https://x.example:443"


def test_d8_private_host_override_accepts(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://10.0.0.1/")
    monkeypatch.setenv("WENJI_ALLOW_PRIVATE_HOST", "1")
    b = load_branding_from_env()
    assert b.site_url == "https://10.0.0.1"


def test_d8_private_host_override_only_literal_one(monkeypatch):
    """Override env var must be exactly '1'; 'true' / 'yes' do NOT bypass."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://10.0.0.1/")
    monkeypatch.setenv("WENJI_ALLOW_PRIVATE_HOST", "true")
    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_branding_from_env()


def test_d8_nonstandard_port_override_accepts_paas(monkeypatch):
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://x.fly.dev:8080/")
    monkeypatch.setenv("WENJI_ALLOW_NONSTANDARD_PORT", "1")
    b = load_branding_from_env()
    assert b.site_url == "https://x.fly.dev:8080"


def test_d8_nonstandard_port_override_rejects_below_1024(monkeypatch):
    """Even with override, ports < 1024 (privileged) are rejected."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://x.com:22/")
    monkeypatch.setenv("WENJI_ALLOW_NONSTANDARD_PORT", "1")
    with pytest.raises(RuntimeError, match="port 22"):
        load_branding_from_env()


def test_d8_og_image_subject_to_same_whitelist(monkeypatch):
    """og:image URL must pass the same whitelist (userinfo, private IP, etc.)."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://wenji.example.com/")
    monkeypatch.setenv("WENJI_OG_IMAGE_URL", "https://attacker.com@trusted.com/x.png")
    with pytest.raises(RuntimeError, match="WENJI_OG_IMAGE_URL.*userinfo"):
        load_branding_from_env()


def test_d8_cors_origin_subject_to_whitelist(monkeypatch):
    """Each CORS origin must pass the same whitelist."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "https://attacker@evil.com")
    from wenji.web.branding import load_cors_origins_from_env

    with pytest.raises(RuntimeError, match="WENJI_CORS_ORIGINS.*userinfo"):
        load_cors_origins_from_env()


def test_d8_http_cors_dev_override_relaxes_port(monkeypatch):
    """WENJI_ALLOW_HTTP_CORS=1 implies non-default ports for http origins
    (Vite dev server runs on 5173, etc.); developers should not need to set
    WENJI_ALLOW_NONSTANDARD_PORT separately."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("WENJI_ALLOW_HTTP_CORS", "1")
    from wenji.web.branding import load_cors_origins_from_env

    assert load_cors_origins_from_env() == ["http://localhost:5173"]


def test_d8_http_cors_dev_override_does_not_relax_other_rules(monkeypatch):
    """allow_http relaxing port does NOT extend to private IP / userinfo / etc."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "http://10.0.0.1:5173")
    monkeypatch.setenv("WENJI_ALLOW_HTTP_CORS", "1")
    from wenji.web.branding import load_cors_origins_from_env

    with pytest.raises(RuntimeError, match="private/loopback/link-local"):
        load_cors_origins_from_env()


def test_d8_typical_production_deployment_happy_path(monkeypatch):
    """Typical production deployment env values MUST pass the whitelist; this
    is a regression guard against accidental over-tightening that would break
    real deploys."""
    _clear_overrides(monkeypatch)
    monkeypatch.setenv("WENJI_SITE_URL", "https://wenji.example.com")
    monkeypatch.setenv("WENJI_SITE_NAME", "My Wenji")
    monkeypatch.setenv("WENJI_OG_IMAGE_URL", "https://wenji.example.com/static/og.png")
    monkeypatch.setenv("WENJI_CORS_ORIGINS", "https://wenji.example.com")
    b = load_branding_from_env()
    assert b.site_url == "https://wenji.example.com"
    assert b.site_name == "My Wenji"
    assert b.og_image_url == "https://wenji.example.com/static/og.png"
    from wenji.web.branding import load_cors_origins_from_env

    assert load_cors_origins_from_env() == ["https://wenji.example.com"]
