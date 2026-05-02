"""Tests for wenji.ingest.jieba_setup."""

from __future__ import annotations

import pytest

from wenji.ingest import jieba_setup


@pytest.fixture(autouse=True)
def _reset_jieba():
    """Reset module state between tests so configure_jieba is reproducible."""
    jieba_setup.reset_for_test()
    yield
    jieba_setup.reset_for_test()


def test_tokenize_for_fts_char_level():
    out = jieba_setup.tokenize_for_fts("因信稱義是")
    assert out == "因 信 稱 義 是"


def test_tokenize_for_fts_strips_whitespace():
    out = jieba_setup.tokenize_for_fts("a  b\nc")
    assert out == "a b c"


def test_tokenize_for_fts_empty():
    assert jieba_setup.tokenize_for_fts("") == ""


def test_tokenize_for_fts_deterministic():
    text = "因信稱義是宗教改革的核心教義"
    a = jieba_setup.tokenize_for_fts(text)
    b = jieba_setup.tokenize_for_fts(text)
    assert a == b


def test_jieba_cut_basic():
    tokens = jieba_setup.jieba_cut("因信稱義是核心")
    assert isinstance(tokens, list)
    assert len(tokens) > 0


def test_jieba_cut_empty():
    assert jieba_setup.jieba_cut("") == []


def test_jieba_cut_lazy_initialises():
    # configure_jieba never called explicitly; jieba_cut must still work
    out = jieba_setup.jieba_cut("這是一個測試")
    assert len(out) > 0


def test_configure_jieba_idempotent():
    jieba_setup.configure_jieba()
    jieba_setup.configure_jieba()  # no crash
