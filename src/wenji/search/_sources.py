"""Shared source-list merging for ``EntityScorer.from_sources`` /
``IntentClassifier.from_sources``.

Both classmethods accept the same source grammar and merge with
last-write-wins; only their constructor wiring differs, so the loop lives
here once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


def merge_sources(sources: list[str], load_example: Callable[[str], dict]) -> dict:
    """Merge JSON mappings from ``sources`` (last-write-wins).

    Each ``source`` SHALL be one of:

    - ``"example:<name>"`` — wheel-bundled example (resolved by ``load_example``)
    - absolute or relative filesystem path ending in ``.json``

    Network URLs (``http://``, ``https://``) are rejected to prevent
    accidental remote fetch.
    """
    merged: dict = {}
    for src in sources:
        if src.startswith(("http://", "https://")):
            raise ValueError(f"network sources not supported in v0.3.6: {src}")
        if src.startswith("example:"):
            merged.update(load_example(src[len("example:") :]))
            continue
        path = Path(src)
        if not path.exists():
            raise FileNotFoundError(f"source not found: {src}")
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged
