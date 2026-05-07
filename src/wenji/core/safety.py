"""Prompt safety utilities — sanitization + structural delimiters."""

from __future__ import annotations

import re

_MAX_PROMPT_INPUT_LENGTH = 10_000


def sanitize_prompt_input(value: str, max_length: int = _MAX_PROMPT_INPUT_LENGTH) -> str:
    """Strip control characters and truncate.

    Keeps ``\\n``, ``\\t``, and printable characters.  Limits length to
    *max_length* (default 10 000).  Never raises.
    """
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    if max_length > 0 and len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned
