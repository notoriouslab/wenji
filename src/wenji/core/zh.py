"""Optional Traditional-Chinese normalisation for model output.

wenji is domain-neutral, so Simplified→Traditional conversion is opt-in
(``WENJI_LLM_OUTPUT_S2TWP``) and depends on the optional ``opencc`` extra.
A model asked for Traditional Chinese usually complies, but some models
occasionally slip into Simplified; this is the belt-and-suspenders guarantee
for Traditional-Chinese deployments.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def _converter():
    try:
        from opencc import OpenCC
    except ImportError as exc:  # pragma: no cover - exercised via error message test
        raise RuntimeError(
            "Traditional-Chinese output conversion is enabled "
            "(WENJI_LLM_OUTPUT_S2TWP) but opencc is not installed. "
            "Install the optional dependency: pip install 'wenji[s2twp]'."
        ) from exc
    return OpenCC("s2twp")


def to_traditional(text: str) -> str:
    """Convert Simplified Chinese to Taiwan-standard Traditional (``s2twp``).

    No-op for empty text; idempotent on text that is already Traditional.
    Raises :class:`RuntimeError` if opencc is not installed.
    """
    if not text:
        return text
    return _converter().convert(text)
