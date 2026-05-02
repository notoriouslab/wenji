"""Chunking strategies — config-driven via chunk_policy.yaml at higher layer.

Three built-in strategies:

- ``paragraph``: split on blank-line, merge tiny paragraphs to reach min_chars
- ``bible-chapter``: split on Chinese 第N章 markers (fallback to fixed-window)
- ``fixed-window``: char-window with overlap

Higher layer (ingest pipeline) decides which strategy applies per source_type.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")
_BIBLE_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+章", re.MULTILINE)
_BIBLE_VERSE_RE = re.compile(r"^\d+:\d+\s", re.MULTILINE)
_NUMBERED_ENTRY_RE = re.compile(
    r"^(?:\d+[\.、)]\s|第\s?[一二三四五六七八九十百千零〇0-9]+\s?[條節點]\s?)", re.MULTILINE
)
_MARKDOWN_HEADING_RE = re.compile(r"^#{2,3}\s", re.MULTILINE)


def chunk_paragraph(text: str, min_chars: int = 200, max_chars: int = 1500) -> list[str]:
    """Paragraph-based chunking: split blank lines, merge until min_chars.

    Graceful degrade: if a single paragraph already exceeds ``max_chars`` (e.g.
    a transcript with no blank-line breaks), it is split via fixed-window so
    the chunk count reflects content size rather than collapsing to 1.
    """
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK_RE.split(text) if p.strip()]
    if not paragraphs:
        return []

    # Pre-split oversized paragraphs via fixed-window
    expanded: list[str] = []
    for p in paragraphs:
        if len(p) > max_chars:
            expanded.extend(chunk_fixed_window(p, size=max_chars, overlap=max(max_chars // 10, 50)))
        else:
            expanded.append(p)
    paragraphs = expanded

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        plen = len(p)
        if buf and buf_len + plen + 2 > max_chars:
            chunks.append("\n\n".join(buf))
            buf = [p]
            buf_len = plen
            continue
        buf.append(p)
        buf_len += plen + (2 if len(buf) > 1 else 0)
        if buf_len >= min_chars:
            chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0
    if buf:
        tail = "\n\n".join(buf)
        if chunks and len(tail) < min_chars:
            chunks[-1] = chunks[-1] + "\n\n" + tail
        else:
            chunks.append(tail)
    return chunks


def chunk_fixed_window(text: str, size: int = 1000, overlap: int = 100) -> list[str]:
    """Fixed character window with overlap. Returns at least one chunk for non-empty text."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be in [0, size)")
    if not text:
        return []
    chunks: list[str] = []
    step = size - overlap
    i = 0
    n = len(text)
    while i < n:
        chunks.append(text[i : i + size])
        if i + size >= n:
            break
        i += step
    return chunks


def _chunk_by_marker(
    text: str,
    pattern: re.Pattern[str],
    fallback_size: int,
    fallback_overlap: int,
) -> list[str]:
    """Split ``text`` so each ``pattern`` match starts a new chunk."""
    matches = list(pattern.finditer(text))
    if not matches:
        return chunk_fixed_window(text, size=fallback_size, overlap=fallback_overlap)
    chunks: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
    return chunks


def chunk_bible_chapter(
    text: str, fallback_size: int = 1000, fallback_overlap: int = 100
) -> list[str]:
    """Split on Chinese chapter markers (第N章). Falls back to fixed-window if no markers."""
    return _chunk_by_marker(text, _BIBLE_CHAPTER_RE, fallback_size, fallback_overlap)


def chunk_bible_verses(
    text: str, fallback_size: int = 1000, fallback_overlap: int = 100
) -> list[str]:
    """Split on bible-verse markers (e.g. ``1:1 ``, ``2:14 ``).

    Suitable for verse-by-verse exegesis where each annotated verse is a
    self-contained unit (拾穗解經 / 詩篇 / 律法書).
    """
    return _chunk_by_marker(text, _BIBLE_VERSE_RE, fallback_size, fallback_overlap)


def chunk_numbered_entries(
    text: str, fallback_size: int = 1000, fallback_overlap: int = 100
) -> list[str]:
    """Split on numbered-entry markers (``1. ``, ``2、``, ``3) ``, ``第一條``, ``第3節``).

    Suitable for clause-style content (條目式講章 / 教義條文 / 週報公告 / 法條 inline).
    """
    return _chunk_by_marker(text, _NUMBERED_ENTRY_RE, fallback_size, fallback_overlap)


def chunk_markdown_heading(
    text: str, fallback_size: int = 1000, fallback_overlap: int = 100
) -> list[str]:
    """Split on Markdown ``##`` / ``###`` heading lines.

    H1 is treated as the article title and not used as a chunk boundary.
    Suitable for structured articles (blog posts with section headings, laws
    with ``### 第 N 條`` heading, technical docs).
    """
    return _chunk_by_marker(text, _MARKDOWN_HEADING_RE, fallback_size, fallback_overlap)


STRATEGIES: dict[str, Callable[..., list[str]]] = {
    "paragraph": chunk_paragraph,
    "bible-chapter": chunk_bible_chapter,
    "bible-verses": chunk_bible_verses,
    "numbered-entries": chunk_numbered_entries,
    "markdown-heading": chunk_markdown_heading,
    "fixed-window": chunk_fixed_window,
}


def chunk(text: str, strategy: str = "paragraph", **kwargs) -> list[str]:
    """Dispatch to a chunking strategy by name. ``**kwargs`` forwarded to the strategy."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; choices: {sorted(STRATEGIES)}")
    return STRATEGIES[strategy](text, **kwargs)
