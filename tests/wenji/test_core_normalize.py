"""Tests for wenji.core.normalize."""

from __future__ import annotations

from wenji.core.normalize import normalize


def test_idempotent():
    text = "  Hello   <b>world</b>\r\n\r\n\r\n  Test  \n"
    once = normalize(text)
    twice = normalize(once)
    assert once == twice


def test_html_tag_strip():
    assert normalize("<p>hello</p>") == "hello"


def test_html_entity_decode():
    assert normalize("&amp; &lt; &gt;") == "& < >"


def test_horizontal_whitespace_collapse():
    assert normalize("a    b\tc　d") == "a b c d"


def test_newline_collapse():
    assert normalize("a\n\n\n\n\nb") == "a\n\nb"


def test_crlf_to_lf():
    assert normalize("a\r\nb") == "a\nb"


def test_trailing_whitespace_before_newline():
    assert normalize("a   \nb") == "a\nb"


def test_empty_input():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_nfc_normalisation():
    decomposed = "é"  # e + combining acute
    composed = "é"  # é precomposed
    assert normalize(decomposed) == normalize(composed)


def test_strip_outer_whitespace():
    assert normalize("   hello   ") == "hello"


def test_preserves_double_newline_paragraph_break():
    text = "para1\n\npara2"
    assert normalize(text) == "para1\n\npara2"
