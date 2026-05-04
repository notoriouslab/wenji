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
_USER_TERMS: set[str] = set()


def _read_dict_terms(path: str | Path) -> list[str]:
    """Extract the first whitespace-separated column (word) from a jieba user_dict file."""
    terms: list[str] = []
    p = Path(path)
    for line in p.read_text(encoding="utf-8").splitlines():
        word = line.strip().split(None, 1)
        if word and word[0]:
            terms.append(word[0])
    return terms


def configure_jieba(custom_dicts: Iterable[str | Path] = ()) -> None:
    """Initialise jieba (idempotent, thread-safe). Reserved for query understanding (v0.2+).

    Loaded user-dict terms are also recorded in :func:`loaded_user_terms` —
    wenji maintains this independently because ``jieba.posseg.cut`` resets
    ``jieba.dt.user_word_tag_tab`` on first call, making it unreliable as
    the source of truth for observability.
    """
    global _INITIALISED
    import jieba

    with _LOCK:
        if _INITIALISED:
            return
        jieba.initialize()
        for d in custom_dicts:
            jieba.load_userdict(str(d))
            _USER_TERMS.update(_read_dict_terms(d))
        _INITIALISED = True


def loaded_user_terms() -> frozenset[str]:
    """Return the set of terms loaded into jieba via :func:`configure_jieba`.

    Used by ``wenji.observability.segment`` to report ``dict_hits``. Stable
    against ``jieba.posseg.cut``'s side effect on ``user_word_tag_tab``.
    """
    return frozenset(_USER_TERMS)


def reset_for_test() -> None:
    """Reset module state so tests can re-initialise. Test-only."""
    global _INITIALISED
    with _LOCK:
        _INITIALISED = False
        _USER_TERMS.clear()


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


def jieba_cut_pos(text: str) -> list[tuple[str, str]]:
    """Run jieba.posseg.cut for query-understanding callers (segment trace,
    future alias / synonym expansion).

    Returns ``[(token_text, pos_tag), ...]`` with empty/whitespace tokens
    filtered out. Lazy-initialises jieba (idempotent, thread-safe via
    :func:`configure_jieba`).

    NOTE: v0.3.x Searcher does NOT call this — query-time FTS uses char-level
    expansion via :func:`wenji.search.bm25.build_fts_query`. This helper is
    the canonical entry-point for any code path that needs jieba's word-level
    view of a query (observability ``/api/segment``, future synonym lookup).
    Duplicating this logic in another module is forbidden; import from here.
    """
    if not text:
        return []
    import jieba.posseg as pseg

    if not _INITIALISED:
        configure_jieba()
    return [(p.word, p.flag) for p in pseg.cut(text) if p.word.strip()]
