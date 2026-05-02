"""Tokenisation helpers for FTS pre-processing + jieba bootstrap.

v0.1.0 indexes content in **char-level + space-joined** form so
``tokenize='unicode61'`` treats each Chinese character as an independent token.
Phrase MATCH (``"因信稱義"``) then locates a sequence of 4 char-level tokens
in the indexed string — robust to jieba segmentation drift between query and
ingest time, which broke earlier ``jieba.cut(...) + space-join`` attempts.

Logos production (``articles_fts_v2``) follows the same approach.

jieba is still bootstrapped here for v0.2+ query understanding (multi-term
expansion / alias matching) but does NOT participate in FTS tokenisation in
v0.1.0.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

_LOCK = threading.Lock()
_INITIALISED = False


def configure_jieba(custom_dicts: Iterable[str | Path] = ()) -> None:
    """Initialise jieba (idempotent, thread-safe). Reserved for query understanding (v0.2+)."""
    global _INITIALISED
    import jieba

    with _LOCK:
        if _INITIALISED:
            return
        jieba.initialize()
        for d in custom_dicts:
            jieba.load_userdict(str(d))
        _INITIALISED = True


def reset_for_test() -> None:
    """Reset module state so tests can re-initialise. Test-only."""
    global _INITIALISED
    with _LOCK:
        _INITIALISED = False


def tokenize_for_fts(text: str) -> str:
    """Char-level + space-join: each non-whitespace char becomes its own token.

    Example: ``"因信稱義是"`` → ``"因 信 稱 義 是"``.
    """
    if not text:
        return ""
    return " ".join(c for c in text if not c.isspace())


def jieba_cut(text: str) -> list[str]:
    """Run jieba.cut for query-understanding callers (alias / synonym expansion).

    Filtering of empty / whitespace tokens; returns list. Loads jieba lazily.
    """
    if not text:
        return []
    import jieba

    if not _INITIALISED:
        configure_jieba()
    return [t for t in jieba.cut(text) if t.strip()]
