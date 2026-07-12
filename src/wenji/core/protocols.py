"""Structural protocols shared across wenji subpackages.

``EmbedderProtocol`` is the single canonical definition — search and ingest
both consume it from here (previously each carried its own copy).
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class EmbedderProtocol(Protocol):
    DIM: int

    def encode_batch(self, texts: list[str]) -> np.ndarray: ...
