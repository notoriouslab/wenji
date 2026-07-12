"""Tests for the 0.5.0 source-type precedence switch (A') and build-environment
provenance (wenji_meta env keys + doctor drift reporting)."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from wenji.core.db import connect, initialise_schema
from wenji.core.errors import IngestError
from wenji.ingest import ingest_dir
from wenji.ingest.frontmatter import derive_source_type
from wenji.observability.health import check_consistency, check_environment


class DeterministicMockEmbedder:
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


# ----- Requirement: Default resolution order is frontmatter first -----


def test_default_frontmatter_wins_over_directory_map(tmp_path):
    md = tmp_path / "tgc" / "foo.md"
    st = derive_source_type(
        {"source_type": "teaching"}, md, {"tgc": "tgc-theology"}
    )
    assert st == "teaching"


def test_default_directory_fallback_when_frontmatter_silent(tmp_path):
    md = tmp_path / "sermons" / "s.md"
    st = derive_source_type({}, md, {"sermons": "sermon"})
    assert st == "sermon"


# ----- Requirement: Deployment can declare directory structure as source of truth -----


def test_flag_on_map_hit_overrides_frontmatter(tmp_path):
    """tgc taxonomy un-flattening: the map value beats frontmatter 'teaching'."""
    md = tmp_path / "tgc" / "foo.md"
    st = derive_source_type(
        {"source_type": "teaching"},
        md,
        {"tgc": "tgc-theology"},
        directory_map_overrides_frontmatter=True,
    )
    assert st == "tgc-theology"


def test_flag_on_map_miss_falls_back_to_frontmatter(tmp_path):
    md = tmp_path / "elsewhere" / "foo.md"
    st = derive_source_type(
        {"source_type": "teaching"},
        md,
        {"tgc": "tgc-theology"},
        directory_map_overrides_frontmatter=True,
    )
    assert st == "teaching"


def test_flag_on_both_absent_raises_naming_path(tmp_path):
    md = tmp_path / "elsewhere" / "foo.md"
    with pytest.raises(IngestError, match="elsewhere"):
        derive_source_type(
            {},
            md,
            {"tgc": "tgc-theology"},
            directory_map_overrides_frontmatter=True,
        )


def test_flag_flows_through_ingest_dir(tmp_path, fresh_conn):
    corpus = tmp_path / "corpus"
    (corpus / "tgc").mkdir(parents=True)
    (corpus / "tgc" / "a.md").write_text(
        "---\ntitle: T\nsource_type: teaching\n---\n本文內容足夠長,可以進入索引。",
        encoding="utf-8",
    )
    ingest_dir(
        corpus,
        fresh_conn,
        DeterministicMockEmbedder(),
        directory_map={"tgc": "tgc-theology"},
        directory_map_overrides_frontmatter=True,
    )
    row = fresh_conn.execute("SELECT source_type FROM articles_meta").fetchone()
    assert row[0] == "tgc-theology"


# ----- Requirement: Bulk ingest records the build environment -----


def _corpus(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "sermons").mkdir(parents=True, exist_ok=True)
    (corpus / "sermons" / "s.md").write_text(
        "---\ntitle: S\nsource_type: sermon\n---\n講道內容,足夠長的一段文字。",
        encoding="utf-8",
    )
    return corpus


def test_successful_ingest_stamps_environment(tmp_path, fresh_conn):
    import numpy as np_mod
    import onnxruntime

    ingest_dir(_corpus(tmp_path), fresh_conn, DeterministicMockEmbedder())
    rows = dict(
        fresh_conn.execute(
            "SELECT key, value FROM wenji_meta WHERE key LIKE 'env_%'"
        ).fetchall()
    )
    assert rows["env_onnxruntime_version"] == onnxruntime.__version__
    assert rows["env_numpy_version"] == np_mod.__version__


def test_crashed_ingest_leaves_no_stamp(tmp_path, fresh_conn, monkeypatch):
    import wenji.ingest as ingest_mod

    def boom(*a, **kw):
        raise RuntimeError("mid-corpus crash")

    monkeypatch.setattr(ingest_mod, "ingest_one", boom)
    with pytest.raises(RuntimeError):
        ingest_dir(_corpus(tmp_path), fresh_conn, DeterministicMockEmbedder())
    rows = fresh_conn.execute(
        "SELECT key FROM wenji_meta WHERE key LIKE 'env_%'"
    ).fetchall()
    assert rows == []


def test_incremental_ingest_overwrites_stamp(tmp_path, fresh_conn):
    ingest_dir(_corpus(tmp_path), fresh_conn, DeterministicMockEmbedder())
    fresh_conn.execute(
        "UPDATE wenji_meta SET value = '0.0.0' WHERE key = 'env_onnxruntime_version'"
    )
    fresh_conn.commit()
    ingest_dir(_corpus(tmp_path), fresh_conn, DeterministicMockEmbedder())
    row = fresh_conn.execute(
        "SELECT value FROM wenji_meta WHERE key = 'env_onnxruntime_version'"
    ).fetchone()
    assert row[0] != "0.0.0"  # last successful bulk write wins


# ----- Requirement: Doctor reports environment drift without failing -----


def test_environment_not_recorded_on_pre_0_5_db(fresh_conn):
    assert check_environment(fresh_conn) == "not recorded (pre-0.5 db)"


def test_environment_ok_when_matching(tmp_path, fresh_conn):
    ingest_dir(_corpus(tmp_path), fresh_conn, DeterministicMockEmbedder())
    assert check_environment(fresh_conn).startswith("ok (onnxruntime ")


def test_environment_drift_reported_but_exit_semantics_unchanged(tmp_path, fresh_conn):
    ingest_dir(_corpus(tmp_path), fresh_conn, DeterministicMockEmbedder())
    fresh_conn.execute(
        "UPDATE wenji_meta SET value = '9.9.9' WHERE key = 'env_onnxruntime_version'"
    )
    fresh_conn.commit()
    env = check_environment(fresh_conn)
    assert env.startswith("DRIFT")
    assert "db=9.9.9" in env
    # exit semantics: report.ok is governed solely by consistency issues —
    # a drift adds zero issues (compare against the same db pre-drift shape).
    report = check_consistency(fresh_conn)
    assert report.environment.startswith("DRIFT")
    fresh_conn.execute(
        "UPDATE wenji_meta SET value = ? WHERE key = 'env_onnxruntime_version'",
        (__import__("onnxruntime").__version__,),
    )
    fresh_conn.commit()
    report_no_drift = check_consistency(fresh_conn)
    assert report.issues == report_no_drift.issues  # drift alone changes nothing
    assert "environment" in report.format()
