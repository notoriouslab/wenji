"""Idempotent text normalisation for ingest / hash / FTS.

Pipeline: NFC → HTML entity decode → strip HTML tags → CRLF→LF →
trailing-whitespace remove → collapse horizontal whitespace runs →
collapse 3+ newlines to 2 → trim.

Idempotent: ``normalize(normalize(x)) == normalize(x)`` for all inputs.
"""

from __future__ import annotations

import html
import re
import unicodedata

_HTML_TAG_RE = re.compile(r"<[a-zA-Z/!][^>]*>")
_HORIZ_WS_RUN_RE = re.compile(r"[ \t　]+")
_NEWLINE_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t　]+\n")


def normalize(text: str | None) -> str:
    """Return canonical normalised form of ``text``."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS_RE.sub("\n", text)
    text = _HORIZ_WS_RUN_RE.sub(" ", text)
    text = _NEWLINE_RUN_RE.sub("\n\n", text)
    return text.strip()
