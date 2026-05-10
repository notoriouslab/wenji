"""DB consistency health check + retrieval-entry startup gate.

Detects two layers of inconsistency:

- **L2 (cross-table derived-from sanity, 2 sub-rules)**:
    - L2.c: ``articles_meta`` > 0 but ``chunks_fts`` empty (prod bug 範式)
    - L2.d: ``articles_meta`` > 0 but ``doc_vectors`` empty
- **L3 (sample MATCH validation)**: at least one keyword in the supplied
  set MUST yield ≥1 hit on ``articles_fts`` AND ≥1 hit on ``chunks_fts``.

Used by ``wenji doctor`` (CLI wrapper) and retrieval-entry startup gates
(``wenji serve`` lifespan, ``wenji eval run*``, ``wenji search``).

Note on ``wenji_meta`` counters: ``n_articles`` / ``n_chunks`` /
``n_doc_vectors`` are NOT consulted. They were specced as build telemetry
in v0.1.0 but no ingest path has ever maintained them (see schema.sql
DEPRECATED notes). The ``cleanup-build-telemetry`` followup change will
decide whether to drop the columns or wire up maintenance; until then
this module relies purely on cross-table row counts and sample MATCH.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from wenji.search.bm25 import build_fts_query

DEFAULT_SAMPLE_KEYWORDS: tuple[str, ...] = ("神", "人", "心", "天", "之")

_TABLES: tuple[str, ...] = ("articles_meta", "articles_fts", "chunks_fts", "doc_vectors")


@dataclass
class ConsistencyReport:
    """Structured result of :func:`check_consistency`. Read-only."""

    schema_version: int
    row_counts: dict[str, int]
    sample_match_hits: dict[str, dict[str, int]]
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def format(self) -> str:
        lines = [
            f"schema_version = {self.schema_version}",
            f"row_counts     = {self.row_counts}",
            f"sample MATCH   = {self.sample_match_hits}",
        ]
        if self.issues:
            lines.append("")
            lines.append("Issues:")
            for issue in self.issues:
                lines.append(f"  - {issue}")
            lines.append("")
            lines.append(f"Status: FAIL ({len(self.issues)} issue(s))")
        else:
            lines.append("")
            lines.append("Status: OK")
        return "\n".join(lines)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _sample_match_count(conn: sqlite3.Connection, table: str, keyword: str) -> int:
    """Return number of rows matching keyword via FTS5 MATCH; 0 on parse failure."""
    fts_query = build_fts_query(keyword)
    if not fts_query:
        return 0
    return _scalar(
        conn,
        f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?",
        (fts_query,),
    )


def check_consistency(
    conn: sqlite3.Connection,
    sample_keywords: tuple[str, ...] = DEFAULT_SAMPLE_KEYWORDS,
) -> ConsistencyReport:
    """Run L2 cross-table sanity + L3 sample MATCH checks.

    Read-only. Returns a structured report regardless of OK/FAIL so callers
    (CLI doctor, startup gate) can present the full picture.
    """
    schema_version = _scalar(
        conn,
        "SELECT CAST(value AS INTEGER) FROM wenji_meta WHERE key = 'schema_version'",
    )
    row_counts = {table: _scalar(conn, f"SELECT COUNT(*) FROM {table}") for table in _TABLES}

    sample_hits: dict[str, dict[str, int]] = {}
    for kw in sample_keywords:
        sample_hits[kw] = {
            "articles_fts": _sample_match_count(conn, "articles_fts", kw),
            "chunks_fts": _sample_match_count(conn, "chunks_fts", kw),
        }

    issues: list[str] = []

    # L2.c: articles_meta > 0 but chunks_fts empty (prod bug 假一致 範式)
    if row_counts["articles_meta"] > 0 and row_counts["chunks_fts"] == 0:
        issues.append(
            f"articles_meta has {row_counts['articles_meta']} rows "
            "but chunks_fts is empty (chunks should be derived from articles)"
        )

    # L2.d: articles_meta > 0 but doc_vectors empty
    if row_counts["articles_meta"] > 0 and row_counts["doc_vectors"] == 0:
        issues.append(
            f"articles_meta has {row_counts['articles_meta']} rows "
            "but doc_vectors is empty (embeddings missing)"
        )

    # L3: sample MATCH all miss on either index
    if sample_keywords:
        any_articles_hit = any(h["articles_fts"] > 0 for h in sample_hits.values())
        any_chunks_hit = any(h["chunks_fts"] > 0 for h in sample_hits.values())
        if not any_articles_hit or not any_chunks_hit:
            issues.append(
                "all sample keywords missed both FTS indices; "
                "if your corpus is non-Chinese, override with --sample-keywords"
            )

    return ConsistencyReport(
        schema_version=schema_version,
        row_counts=row_counts,
        sample_match_hits=sample_hits,
        issues=issues,
    )


def _ensure_consistency(
    db_path: Path,
    sample_keywords: tuple[str, ...] = DEFAULT_SAMPLE_KEYWORDS,
) -> None:
    """Open db, run check_consistency, exit non-zero with hint on FAIL.

    Used by retrieval-entry CLI subcommands as a startup gate (eval run* /
    search). Side-effect: ``sys.exit(1)`` on inconsistency. Caller should
    invoke this before any retrieval work.

    Honours ``WENJI_DISABLE_STARTUP_CHECK`` env to skip the check (test
    fixtures only; production deploys MUST NOT set this).
    """
    if os.environ.get("WENJI_DISABLE_STARTUP_CHECK"):
        return
    from wenji.core.db import connect

    conn = connect(db_path)
    try:
        report = check_consistency(conn, sample_keywords)
    finally:
        conn.close()

    if not report.ok:
        issue_lines = "\n".join(f"  - {issue}" for issue in report.issues)
        msg = (
            f"wenji db consistency check FAILED at {db_path}\n"
            f"\n"
            f"Issues:\n"
            f"{issue_lines}\n"
            f"\n"
            f"Run `wenji doctor --db {db_path}` for full diagnostic, or\n"
            f"`wenji ingest dir <path> --db {db_path} --rebuild` to rebuild."
        )
        print(msg, file=sys.stderr)
        sys.exit(1)
