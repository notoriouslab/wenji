"""Tests for wenji.config.llm — LLMConfig + env loader."""

from __future__ import annotations

import dataclasses

import pytest

from wenji.config import LLMConfig, load_llm_config_from_env


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "WENJI_LLM_BASE_URL",
        "WENJI_LLM_API_KEY",
        "WENJI_LLM_MODEL",
        "WENJI_LLM_TIMEOUT",
        "WENJI_LLM_REWRITE_CACHE_TTL_DAYS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_disabled_when_all_unset():
    cfg = load_llm_config_from_env()
    assert cfg.enabled is False
    assert set(cfg.missing_fields()) == {
        "WENJI_LLM_BASE_URL",
        "WENJI_LLM_API_KEY",
        "WENJI_LLM_MODEL",
    }


def test_disabled_when_only_key_set(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    cfg = load_llm_config_from_env()
    assert cfg.enabled is False
    assert "WENJI_LLM_BASE_URL" in cfg.missing_fields()
    assert "WENJI_LLM_MODEL" in cfg.missing_fields()


def test_disabled_when_only_url_and_key_set(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "https://x.test")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    cfg = load_llm_config_from_env()
    assert cfg.enabled is False
    assert cfg.missing_fields() == ["WENJI_LLM_MODEL"]


def test_enabled_when_all_three_set(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "https://x.test/v1")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    cfg = load_llm_config_from_env()
    assert cfg.enabled is True
    assert cfg.missing_fields() == []
    assert cfg.timeout == 10.0
    assert cfg.rewrite_cache_ttl_days == 30


def test_custom_timeout(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "u")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    monkeypatch.setenv("WENJI_LLM_TIMEOUT", "5.0")
    cfg = load_llm_config_from_env()
    assert cfg.timeout == 5.0


def test_invalid_timeout_falls_back(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "u")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    monkeypatch.setenv("WENJI_LLM_TIMEOUT", "not-a-float")
    cfg = load_llm_config_from_env()
    assert cfg.timeout == 10.0


def test_custom_rewrite_cache_ttl(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "u")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    monkeypatch.setenv("WENJI_LLM_REWRITE_CACHE_TTL_DAYS", "7")
    cfg = load_llm_config_from_env()
    assert cfg.rewrite_cache_ttl_days == 7


def test_invalid_ttl_falls_back(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "u")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    monkeypatch.setenv("WENJI_LLM_REWRITE_CACHE_TTL_DAYS", "abc")
    cfg = load_llm_config_from_env()
    assert cfg.rewrite_cache_ttl_days == 30


def test_empty_string_treated_as_unset(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")
    cfg = load_llm_config_from_env()
    assert cfg.enabled is False
    assert "WENJI_LLM_BASE_URL" in cfg.missing_fields()


def test_dataclass_frozen():
    cfg = LLMConfig(base_url="u", api_key="k", model="m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.timeout = 5.0  # frozen dataclass; type: ignore
