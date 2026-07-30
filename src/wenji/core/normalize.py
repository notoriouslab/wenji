"""Text normalisation for ingest / hash / FTS.

Pipeline: NFC → HTML entity decode (one layer) → strip HTML tags →
CRLF→LF → trailing-whitespace remove → collapse horizontal whitespace
runs → collapse 3+ newlines to 2 → trim.

Idempotent for text carrying at most one layer of entity encoding — which
is what both call sites feed it (one pass over raw source text). It is NOT
idempotent for all inputs: each call decodes exactly one entity layer, so
double-encoded input (``&amp;lt;``) changes again on a second pass.
Deliberate — decoding to a fixpoint would mangle text that legitimately
discusses entities, and the surviving ``&lt;script&gt;`` text is inert
downstream because every renderer escapes on output.
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
    # Decode before stripping, per the documented pipeline order. The reverse
    # lets an entity-encoded tag (``&lt;script&gt;``) slip through the strip
    # and come out the far side as live markup.
    text = html.unescape(text)
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS_RE.sub("\n", text)
    text = _HORIZ_WS_RUN_RE.sub(" ", text)
    text = _NEWLINE_RUN_RE.sub("\n\n", text)
    return text.strip()
