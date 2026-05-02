"""Tests for wenji.ingest pipeline (ingest_one / ingest_dir / rebuild_from_disk).

Uses a duck-typed deterministic mock embedder so tests exercise:
- idempotency (re-ingest same file → no row growth)
- byte-identical rebuild (run rebuild twice, verify FTS.content + vec match)
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from wenji.core.db import connect, initialise_schema
from wenji.ingest import ingest_dir, ingest_one, rebuild_from_disk


class DeterministicMockEmbedder:
    """Hash-based deterministic embedder for byte-identical rebuild tests."""

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


@pytest.fixture
def fresh_conn():
    conn = connect(":memory:")
    initialise_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def corpus(tmp_path):
    """Tiny fixture corpus: 2 files in a 'sermons' subdir."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    (sermons / "s1.md").write_text(
        "---\ntitle: 講道一\ntags: [禱告, 信心]\npubDate: 2024-01-15\n---\n第一段內容。\n\n第二段內容更長一些，足以分成段落。",
        encoding="utf-8",
    )
    (sermons / "s2.md").write_text(
        "---\ntitle: 講道二\nauthor: 張三\n---\n單一段落。",
        encoding="utf-8",
    )
    return tmp_path


def test_ingest_one_writes_meta_and_fts(fresh_conn, corpus):
    article_id = ingest_one(
        corpus / "sermons" / "s1.md",
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    row = fresh_conn.execute(
        "SELECT title, source_type, content_hash FROM articles_meta WHERE article_id=?",
        (article_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "講道一"
    assert row[1] == "sermon"
    assert len(row[2]) == 16
    fts_count = fresh_conn.execute(
        "SELECT COUNT(*) FROM articles_fts WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert fts_count == 1


def test_ingest_one_idempotent(fresh_conn, corpus):
    args = (
        corpus / "sermons" / "s1.md",
        fresh_conn,
        DeterministicMockEmbedder(),
    )
    kwargs = {"directory_map": {"sermons": "sermon"}}
    aid1 = ingest_one(*args, **kwargs)
    aid2 = ingest_one(*args, **kwargs)
    fresh_conn.commit()
    assert aid1 == aid2
    n_meta = fresh_conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
    n_fts = fresh_conn.execute("SELECT COUNT(*) FROM articles_fts").fetchone()[0]
    n_vec = fresh_conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]
    assert n_meta == 1
    assert n_fts == 1
    assert n_vec == 1


def test_ingest_one_chunks_when_strategy_configured(fresh_conn, corpus):
    article_id = ingest_one(
        corpus / "sermons" / "s1.md",
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
        chunk_strategies={"sermon": {"strategy": "paragraph", "min_chars": 1, "max_chars": 100}},
    )
    fresh_conn.commit()
    n_chunks = fresh_conn.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert n_chunks >= 1
    chunk_count_meta = fresh_conn.execute(
        "SELECT chunk_count FROM articles_meta WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert chunk_count_meta == n_chunks


def test_ingest_one_no_chunks_when_strategy_absent(fresh_conn, corpus):
    article_id = ingest_one(
        corpus / "sermons" / "s2.md",
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
        chunk_strategies={},  # no entry for sermon
    )
    fresh_conn.commit()
    n_chunks = fresh_conn.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert n_chunks == 0
    chunk_count_meta = fresh_conn.execute(
        "SELECT chunk_count FROM articles_meta WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert chunk_count_meta == 0


def test_ingest_dir_processes_all_files(fresh_conn, corpus):
    ids = ingest_dir(
        corpus,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    assert len(ids) == 2
    n = fresh_conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
    assert n == 2


def _snapshot(conn):
    fts_rows = conn.execute(
        "SELECT article_id, content FROM articles_fts ORDER BY article_id"
    ).fetchall()
    vec_rows = conn.execute(
        "SELECT article_id, vec FROM doc_vectors ORDER BY article_id"
    ).fetchall()
    return fts_rows, vec_rows


def test_rebuild_from_disk_byte_identical(corpus):
    db_path = ":memory:"
    conn1 = connect(db_path)
    initialise_schema(conn1)
    rebuild_from_disk(
        conn1,
        corpus,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    snap_1 = _snapshot(conn1)
    conn1.close()

    conn2 = connect(db_path)
    initialise_schema(conn2)
    rebuild_from_disk(
        conn2,
        corpus,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    snap_2 = _snapshot(conn2)
    conn2.close()

    assert snap_1 == snap_2


def test_rebuild_from_disk_clears_stale_data(fresh_conn, corpus):
    # First insert: 2 files
    rebuild_from_disk(
        fresh_conn,
        corpus,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    n1 = fresh_conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
    assert n1 == 2

    # Remove one file then rebuild
    (corpus / "sermons" / "s2.md").unlink()
    rebuild_from_disk(
        fresh_conn,
        corpus,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    n2 = fresh_conn.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
    assert n2 == 1


def test_frontmatter_chunk_strategy_overrides_source_type(fresh_conn, tmp_path):
    """Frontmatter chunk_strategy overrides the source_type default mapping."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "verses.md"
    md.write_text(
        "---\ntitle: T\nchunk_strategy: bible-verses\n---\n"
        "1:1 起初神創造天地。\n1:2 地是空虛混沌。\n1:3 神說要有光。\n",
        encoding="utf-8",
    )
    article_id = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
        chunk_strategies={"sermon": {"strategy": "paragraph", "min_chars": 1, "max_chars": 100}},
    )
    fresh_conn.commit()
    n_chunks = fresh_conn.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert n_chunks == 3  # bible-verses produced 3, not whatever paragraph would


def test_title_falls_back_to_first_h1_when_frontmatter_missing_title(fresh_conn, tmp_path):
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "no-title.md"
    md.write_text(
        "---\ntags: [a]\n---\n# Found H1 Title\n\nBody content here.",
        encoding="utf-8",
    )
    article_id = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    title = fresh_conn.execute(
        "SELECT title FROM articles_meta WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert title == "Found H1 Title"


def test_title_fallback_setext_h1(fresh_conn, tmp_path):
    """L4: Setext-style H1 (``Title\\n===``) fallback via Markdown AST."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "setext.md"
    md.write_text(
        "---\ntags: [a]\n---\nMy Setext Title\n=========\n\nBody.",
        encoding="utf-8",
    )
    article_id = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    title = fresh_conn.execute(
        "SELECT title FROM articles_meta WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert title == "My Setext Title"


def test_title_fallback_inline_formatting_stripped(fresh_conn, tmp_path):
    """L4: H1 with inline emphasis is reduced to plain text via AST."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "inline.md"
    md.write_text(
        "---\ntags: [a]\n---\n# **Bold** Title with `code_id`\n\nBody.",
        encoding="utf-8",
    )
    article_id = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    title = fresh_conn.execute(
        "SELECT title FROM articles_meta WHERE article_id=?", (article_id,)
    ).fetchone()[0]
    assert title == "Bold Title with code_id"


def test_source_url_list_yields_first_string(fresh_conn, tmp_path):
    """L3: frontmatter source_url as list → first non-empty entry stored."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "src-list.md"
    md.write_text(
        "---\ntitle: T\nsource_url:\n  - https://a.example\n  - https://b.example\n---\nBody.",
        encoding="utf-8",
    )
    ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    row = fresh_conn.execute(
        "SELECT source_url, source_urls_json FROM articles_meta WHERE path = ?",
        (str(md.resolve()),),
    ).fetchone()
    assert row[0] == "https://a.example"
    # source_urls plural was not provided, so json column is empty.
    assert row[1] == ""


def test_source_url_dict_uses_url_field(fresh_conn, tmp_path):
    """L3: frontmatter source_url as dict → ``url`` field used."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "src-dict.md"
    md.write_text(
        "---\ntitle: T\nsource_url:\n  url: https://primary.example\n  note: primary\n---\nBody.",
        encoding="utf-8",
    )
    ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    row = fresh_conn.execute(
        "SELECT source_url FROM articles_meta WHERE path = ?",
        (str(md.resolve()),),
    ).fetchone()
    assert row[0] == "https://primary.example"


def test_source_urls_plural_stored_as_json(fresh_conn, tmp_path):
    """L3: ``source_urls`` plural → JSON list in source_urls_json column."""
    import json as _json

    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "src-plural.md"
    md.write_text(
        "---\ntitle: T\nsource_urls:\n  - https://a.example\n  - https://b.example\n---\nBody.",
        encoding="utf-8",
    )
    ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    row = fresh_conn.execute(
        "SELECT source_urls_json FROM articles_meta WHERE path = ?",
        (str(md.resolve()),),
    ).fetchone()
    assert _json.loads(row[0]) == ["https://a.example", "https://b.example"]


def test_ingest_same_path_unchanged_content_keeps_one_row(fresh_conn, tmp_path):
    """L5: re-ingest same path with identical body → 1 row, indexed_at refreshed."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "stable.md"
    md.write_text("---\ntitle: T\n---\nFirst version body.", encoding="utf-8")
    aid1 = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    # Second ingest with identical content
    aid2 = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
    )
    fresh_conn.commit()
    assert aid1 == aid2
    n_rows = fresh_conn.execute(
        "SELECT COUNT(*) FROM articles_meta WHERE path = ?", (str(md.resolve()),)
    ).fetchone()[0]
    assert n_rows == 1


def test_ingest_same_path_changed_content_replaces_row_and_cleans_derived(fresh_conn, tmp_path):
    """L5: same path + different content → old article_id and derived rows gone, new row inserted."""
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    md = sermons / "shifty.md"
    md.write_text("---\ntitle: T\n---\nOriginal body content.", encoding="utf-8")
    aid_old = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
        chunk_strategies={"sermon": {"strategy": "paragraph", "min_chars": 1, "max_chars": 200}},
    )
    fresh_conn.commit()

    # Edit content
    md.write_text("---\ntitle: T\n---\nNew completely different body content.", encoding="utf-8")
    aid_new = ingest_one(
        md,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"sermons": "sermon"},
        chunk_strategies={"sermon": {"strategy": "paragraph", "min_chars": 1, "max_chars": 200}},
    )
    fresh_conn.commit()

    assert aid_old != aid_new

    # Exactly one articles_meta row for this path, the new one
    rows = fresh_conn.execute(
        "SELECT article_id FROM articles_meta WHERE path = ?", (str(md.resolve()),)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == aid_new

    # Old article_id is gone everywhere
    assert (
        fresh_conn.execute(
            "SELECT COUNT(*) FROM articles_meta WHERE article_id = ?", (aid_old,)
        ).fetchone()[0]
        == 0
    )
    assert (
        fresh_conn.execute(
            "SELECT COUNT(*) FROM articles_fts WHERE article_id = ?", (aid_old,)
        ).fetchone()[0]
        == 0
    )
    assert (
        fresh_conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE article_id = ?", (aid_old,)
        ).fetchone()[0]
        == 0
    )
    assert (
        fresh_conn.execute(
            "SELECT COUNT(*) FROM doc_vectors WHERE article_id = ?", (aid_old,)
        ).fetchone()[0]
        == 0
    )
