"""Tests for wenji.core.chunk."""

from __future__ import annotations

import pytest

from wenji.core.chunk import (
    chunk,
    chunk_bible_chapter,
    chunk_bible_verses,
    chunk_fixed_window,
    chunk_numbered_entries,
    chunk_paragraph,
)


def test_paragraph_basic_split():
    text = "Para 1.\n\nPara 2.\n\nPara 3."
    chunks = chunk_paragraph(text, min_chars=1, max_chars=100)
    assert len(chunks) == 3


def test_paragraph_merges_until_min_chars():
    text = "p1\n\np2\n\np3\n\np4\n\np5"
    chunks = chunk_paragraph(text, min_chars=100, max_chars=500)
    assert len(chunks) == 1


def test_paragraph_splits_at_max_chars():
    long_para = "x" * 1500
    text = f"{long_para}\n\nshort tail"
    chunks = chunk_paragraph(text, min_chars=200, max_chars=1500)
    assert len(chunks) >= 1


def test_paragraph_empty_text():
    assert chunk_paragraph("", min_chars=1, max_chars=10) == []


def test_paragraph_tail_merges_into_previous_when_tiny():
    text = "x" * 250 + "\n\n" + "tail"
    chunks = chunk_paragraph(text, min_chars=200, max_chars=1500)
    assert len(chunks) == 1
    assert chunks[0].endswith("tail")


def test_fixed_window_size_and_overlap():
    text = "a" * 1000
    chunks = chunk_fixed_window(text, size=300, overlap=50)
    assert all(len(c) <= 300 for c in chunks)
    assert len(chunks) > 1


def test_fixed_window_validates_size():
    with pytest.raises(ValueError):
        chunk_fixed_window("text", size=0)


def test_fixed_window_validates_overlap():
    with pytest.raises(ValueError):
        chunk_fixed_window("text", size=10, overlap=10)
    with pytest.raises(ValueError):
        chunk_fixed_window("text", size=10, overlap=-1)


def test_fixed_window_empty():
    assert chunk_fixed_window("", size=100, overlap=10) == []


def test_bible_chapter_splits_on_marker():
    text = "前言段落。\n\n第一章\n第1節 起初。\n第2節 神創造。\n\n第二章\n第1節 然後。"
    chunks = chunk_bible_chapter(text)
    assert len(chunks) == 2
    assert chunks[0].startswith("第一章")
    assert chunks[1].startswith("第二章")


def test_bible_chapter_fallback_when_no_marker():
    text = "abc def" * 200
    chunks = chunk_bible_chapter(text, fallback_size=300, fallback_overlap=50)
    assert len(chunks) > 1


def test_chunk_dispatcher_paragraph():
    chunks = chunk("a\n\nb\n\nc", strategy="paragraph", min_chars=1, max_chars=100)
    assert isinstance(chunks, list)


def test_chunk_dispatcher_fixed_window():
    chunks = chunk("x" * 500, strategy="fixed-window", size=200, overlap=20)
    assert all(len(c) <= 200 for c in chunks)


def test_chunk_dispatcher_unknown_strategy_raises():
    with pytest.raises(ValueError, match="unknown strategy"):
        chunk("text", strategy="nonexistent")


def test_bible_verses_basic():
    text = "1:1 起初神創造天地。\n1:2 地是空虛混沌。\n1:3 神說要有光。\n"
    chunks = chunk_bible_verses(text)
    assert len(chunks) == 3
    assert chunks[0].startswith("1:1")
    assert chunks[2].startswith("1:3")


def test_bible_verses_fallback_when_no_marker():
    text = "abc def" * 200
    chunks = chunk_bible_verses(text, fallback_size=300, fallback_overlap=50)
    assert len(chunks) > 1


def test_numbered_entries_arabic():
    text = "1. 第一條內容\n2. 第二條內容\n3、 第三條內容\n"
    chunks = chunk_numbered_entries(text)
    assert len(chunks) == 3


def test_numbered_entries_chinese():
    text = "第一條 應當愛主你的神\n第二條 要愛人如己\n第三條 不可妄稱神的名"
    chunks = chunk_numbered_entries(text)
    assert len(chunks) == 3


def test_numbered_entries_fallback_when_no_marker():
    text = "no numbers here, just prose " * 50
    chunks = chunk_numbered_entries(text, fallback_size=200, fallback_overlap=20)
    assert len(chunks) >= 1


def test_dispatcher_routes_new_strategies():
    chunks_v = chunk("1:1 first.\n1:2 second.", strategy="bible-verses")
    assert len(chunks_v) == 2
    chunks_n = chunk("1. one\n2. two", strategy="numbered-entries")
    assert len(chunks_n) == 2
