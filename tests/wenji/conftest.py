"""Shared pytest fixtures for wenji tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from wenji.core.db import connect, initialise_schema
from wenji.ingest import ingest_dir


class DeterministicMockEmbedder:
    """Hash-based deterministic embedder shared across tests."""

    DIM = 1024

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            buf = (digest * ((self.DIM * 4) // len(digest) + 1))[: self.DIM * 4]
            vec = np.frombuffer(buf, dtype=np.float32).copy()
            norm = float(np.linalg.norm(vec)) or 1.0
            out[i] = vec / norm
        return out


@pytest.fixture(autouse=True)
def _disable_startup_check(monkeypatch):
    """Default-skip the FastAPI lifespan consistency gate for test fixtures.

    Test dbs are partial by design (e.g. only articles_meta + doc_vectors,
    no chunks_fts) to exercise specific endpoints. Tests that explicitly
    verify the startup gate (test_web_startup.py) MUST
    `monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)` to
    re-enable it.
    """
    monkeypatch.setenv("WENJI_DISABLE_STARTUP_CHECK", "1")


@pytest.fixture
def mock_embedder():
    return DeterministicMockEmbedder()


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    """Three sermon-like articles in tmp_path/sermons/."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    (sermons / "grace.md").write_text(
        "---\ntitle: 因信稱義論恩典\ntags: [恩典, 救恩]\n"
        "pubDate: 2024-01-15\n---\n"
        "因信稱義是宗教改革的核心。我們因信靠基督而被神稱為義，"
        "這完全是出於恩典，不是出於行為。\n",
        encoding="utf-8",
    )
    (sermons / "prayer.md").write_text(
        "---\ntitle: 禱告與屬靈生命\ntags: [禱告]\n"
        "pubDate: 2024-02-20\n---\n"
        "禱告是與神親近的方式，是基督徒屬靈生命的呼吸。"
        "持續恆切的禱告會帶來生命的轉化。\n",
        encoding="utf-8",
    )
    (sermons / "mission.md").write_text(
        "---\ntitle: 普世宣教使命\ntags: [宣教]\n"
        "category: excluded\n---\n"
        "教會被呼召去到地極傳福音。宣教是回應大使命的具體行動。\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def populated_db(tiny_corpus: Path, mock_embedder):
    conn = connect(":memory:")
    initialise_schema(conn)
    ingest_dir(
        tiny_corpus,
        conn,
        mock_embedder,
        directory_map={"sermons": "sermon"},
    )
    # Add an axis assignment for one article so axis-filter tests work.
    aid = conn.execute("SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'").fetchone()[
        0
    ]
    conn.execute(
        "INSERT INTO article_axes (article_id, axis_id, is_primary) VALUES (?, ?, 1)",
        (aid, "theology"),
    )
    conn.commit()
    yield conn
    conn.close()
