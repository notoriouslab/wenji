"""Corpus / index statistics aggregation for /api/stats and ``wenji stats``.

All counts are computed by direct SQL aggregation against the live wenji DB
on every call (no cache layer — see Decision 1 in change
``wenji-stats-segment-v0-3-3``). At 12k corpus on local SSD each query is
sub-100ms; if scale grows beyond that, a stats snapshot table can be added
in a future change.
"""

from __future__ import annotations

import sqlite3
from typing import TypedDict

from wenji.classify.axes_loader import UNCLASSIFIED, AxesConfig


class IndicesInfo(TypedDict):
    fts5_articles: int
    fts5_chunks: int
    vector_dims: int
    vector_count: int


class StatsResult(TypedDict):
    articles: int
    chunks: int
    indices: IndicesInfo
    source_types: dict[str, int]
    axes: dict[str, int]
    last_ingest_at: str | None


_BYTES_PER_FLOAT32 = 4


def _scalar_count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _vector_dims(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT LENGTH(vec) FROM doc_vectors LIMIT 1").fetchone()
    if not row or row[0] is None:
        return 0
    return int(row[0]) // _BYTES_PER_FLOAT32


def _source_type_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT source_type, COUNT(*) FROM articles_meta "
        "WHERE source_type IS NOT NULL AND source_type != '' "
        "GROUP BY source_type"
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def _axes_counts(conn: sqlite3.Connection, axes_config: AxesConfig | None) -> dict[str, int]:
    """Count articles per axis, mapping axis_id → human label via axes.yaml.

    When ``axes_config`` is None (no ``WENJI_AXES_YAML`` configured), returns
    ``{}`` rather than raw axis_ids — observability output should not leak
    internal identifiers when the corpus owner has not curated labels.
    """
    if axes_config is None:
        return {}
    rows = conn.execute(
        "SELECT axis_id, COUNT(*) FROM article_axes WHERE axis_id != ? GROUP BY axis_id",
        (UNCLASSIFIED,),
    ).fetchall()
    id_to_name = {a.id: a.name for a in axes_config.axes}
    out: dict[str, int] = {}
    for axis_id, count in rows:
        label = id_to_name.get(str(axis_id))
        if label is None:
            continue
        out[label] = int(count)
    return out


def _last_ingest_at(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(indexed_at) FROM articles_meta").fetchone()
    if not row or row[0] in (None, ""):
        return None
    return str(row[0])


def compute_stats(conn: sqlite3.Connection, axes_config: AxesConfig | None) -> StatsResult:
    """Compute corpus / index stats from a live wenji DB connection.

    Caller owns the connection lifecycle. Schema mismatch (missing tables)
    propagates as ``sqlite3.OperationalError``.
    """
    return {
        "articles": _scalar_count(conn, "SELECT COUNT(*) FROM articles_meta"),
        "chunks": _scalar_count(conn, "SELECT COUNT(*) FROM chunks_fts"),
        "indices": {
            "fts5_articles": _scalar_count(conn, "SELECT COUNT(*) FROM articles_fts"),
            "fts5_chunks": _scalar_count(conn, "SELECT COUNT(*) FROM chunks_fts"),
            "vector_dims": _vector_dims(conn),
            "vector_count": _scalar_count(conn, "SELECT COUNT(*) FROM doc_vectors"),
        },
        "source_types": _source_type_counts(conn),
        "axes": _axes_counts(conn, axes_config),
        "last_ingest_at": _last_ingest_at(conn),
    }
