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


def test_entity_encoded_tags_are_stripped_too():
    """Decoding has to happen before the tag strip, not after.

    With the passes in the wrong order an entity-encoded tag survives the
    strip and is then decoded into live markup — a poisoned markdown file
    would land real ``<script>`` in stored content.
    """
    assert normalize("&lt;script&gt;alert(1)&lt;/script&gt;") == "alert(1)"
    assert normalize("&lt;img src=x onerror=alert(1)&gt;") == ""
    for probe in ("&lt;b&gt;粗&lt;/b&gt;", "<p>hi</p>", "&amp; text"):
        once = normalize(probe)
        assert normalize(once) == once, f"not idempotent for {probe!r}"


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
