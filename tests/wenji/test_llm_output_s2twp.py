"""Traditional-Chinese output conversion (WENJI_LLM_OUTPUT_S2TWP)."""

from __future__ import annotations

import pytest

from wenji.core.zh import to_traditional
from wenji.web.app import _llm_client_from_env

opencc = pytest.importorskip("opencc", reason="s2twp extra not installed")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "WENJI_LLM_BASE_URL",
        "WENJI_LLM_API_KEY",
        "WENJI_LLM_MODEL",
        "WENJI_LLM_OUTPUT_S2TWP",
    ):
        monkeypatch.delenv(var, raising=False)


def test_to_traditional_converts_simplified():
    assert to_traditional("这些来源规范了出差报支与灵粮堂") == "這些來源規範了出差報支與靈糧堂"


def test_to_traditional_is_idempotent_on_traditional():
    trad = "這些來源規範了出差報支與靈糧堂"
    assert to_traditional(trad) == trad


def test_to_traditional_empty_is_noop():
    assert to_traditional("") == ""


def _set_llm_env(monkeypatch):
    monkeypatch.setenv("WENJI_LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("WENJI_LLM_API_KEY", "k")
    monkeypatch.setenv("WENJI_LLM_MODEL", "m")


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_wires_traditional_transform(monkeypatch, val):
    _set_llm_env(monkeypatch)
    monkeypatch.setenv("WENJI_LLM_OUTPUT_S2TWP", val)
    client = _llm_client_from_env()
    assert client is not None
    assert client.output_transform is to_traditional


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "maybe"])
def test_env_flag_off_leaves_transform_unset(monkeypatch, val):
    _set_llm_env(monkeypatch)
    if val:
        monkeypatch.setenv("WENJI_LLM_OUTPUT_S2TWP", val)
    client = _llm_client_from_env()
    assert client is not None
    assert client.output_transform is None
