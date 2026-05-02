"""Content hashing for incremental ingest detection.

Short SHA256[:16] is the canonical wenji content_hash — used in articles_meta
for change detection (skip re-ingest when disk hash matches DB hash).
"""

from __future__ import annotations

import hashlib

HASH_LENGTH = 16


def content_hash(text: str) -> str:
    """Return SHA256(utf-8 text) hex truncated to ``HASH_LENGTH`` chars."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LENGTH]
