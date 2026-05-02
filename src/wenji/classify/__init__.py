"""wenji.classify — multi-axis rule-based classifier.

Public API:

- :func:`load_axes_config` (re-export from :mod:`wenji.classify.axes_loader`)
- :class:`AxesClassifier` — main entry, supports ``classify_one`` / ``classify_all`` / ``validate``
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from wenji.classify.axes_loader import (
    UNCLASSIFIED,
    AxesConfig,
    Axis,
    Rule,
    ValidationBounds,
    load_axes_config,
)
from wenji.classify.rules import Article, rule_matches
from wenji.core.errors import ClassifyError

logger = logging.getLogger("wenji.classify")


@dataclass
class ClassifyResult:
    article_id: str
    matched_axes: list[tuple[str, bool]]  # (axis_id, is_primary)
    retag_to: str | None


@dataclass
class ValidationReport:
    passed: bool
    metrics: dict[str, object]
    failures: list[str]


def _classify_one(article: Article, config: AxesConfig) -> ClassifyResult:
    """Apply rules to a single article (pure — no DB writes here)."""
    matches: list[tuple[Axis, Rule]] = []
    retag_to: str | None = None

    for axis in config.axes:
        for rule in axis.rules:
            if rule_matches(rule, article):
                matches.append((axis, rule))
                if retag_to is None and rule.retag_source_type_to:
                    retag_to = rule.retag_source_type_to
                break  # first-match-wins per axis

    if not matches:
        return ClassifyResult(
            article_id=article.article_id,
            matched_axes=[(UNCLASSIFIED, True)],
            retag_to=None,
        )

    primary_candidates = [axis.id for axis, rule in matches if rule.primary]
    primary_axis = primary_candidates[0] if primary_candidates else None
    if primary_axis is None:
        logger.warning(
            "article %s matched only non-primary rules across %s",
            article.article_id,
            [axis.id for axis, _ in matches],
        )
    matched_axes = [(axis.id, axis.id == primary_axis) for axis, _ in matches]
    return ClassifyResult(
        article_id=article.article_id,
        matched_axes=matched_axes,
        retag_to=retag_to,
    )


def _write_result(conn: sqlite3.Connection, result: ClassifyResult) -> None:
    """DELETE old rows + INSERT new + retag if applicable. Idempotent."""
    conn.execute("DELETE FROM article_axes WHERE article_id = ?", (result.article_id,))
    conn.executemany(
        "INSERT INTO article_axes (article_id, axis_id, is_primary) VALUES (?, ?, ?)",
        [
            (result.article_id, axis_id, 1 if is_primary else 0)
            for axis_id, is_primary in result.matched_axes
        ],
    )
    if result.retag_to:
        conn.execute(
            "UPDATE articles_meta SET source_type = ? WHERE article_id = ?",
            (result.retag_to, result.article_id),
        )


def _iter_articles(conn: sqlite3.Connection) -> Iterator[Article]:
    for row in conn.execute(
        "SELECT article_id, source_type, subtype, title, tags FROM articles_meta"
    ):
        yield Article(
            article_id=row[0],
            source_type=row[1],
            subtype=row[2],
            title=row[3],
            tags_json=row[4],
        )


def _fetch_one(conn: sqlite3.Connection, article_id: str) -> Article | None:
    row = conn.execute(
        "SELECT article_id, source_type, subtype, title, tags FROM articles_meta "
        "WHERE article_id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return None
    return Article(
        article_id=row[0],
        source_type=row[1],
        subtype=row[2],
        title=row[3],
        tags_json=row[4],
    )


class AxesClassifier:
    """Multi-axis classifier driven by ``axes.yaml``.

    Args:
        conn: Open SQLite connection (schema initialised, articles ingested).
        config: Parsed :class:`AxesConfig` (load via :func:`load_axes_config`).
    """

    def __init__(self, conn: sqlite3.Connection, config: AxesConfig) -> None:
        self.conn = conn
        self.config = config

    def classify_one(self, article_id: str) -> ClassifyResult:
        article = _fetch_one(self.conn, article_id)
        if article is None:
            raise ClassifyError(f"article not found: {article_id!r}")
        result = _classify_one(article, self.config)
        _write_result(self.conn, result)
        self.conn.commit()
        return result

    def classify_all(self) -> list[ClassifyResult]:
        """Re-run classification across every article. Returns all results."""
        results: list[ClassifyResult] = []
        for article in list(_iter_articles(self.conn)):
            result = _classify_one(article, self.config)
            _write_result(self.conn, result)
            results.append(result)
        self.conn.commit()
        return results

    def validate(self) -> ValidationReport:
        """Verify post-classify metrics against ``axes.yaml validation:`` bounds."""
        bounds: ValidationBounds = self.config.validation
        cur = self.conn.cursor()

        total_rows = cur.execute("SELECT COUNT(*) FROM article_axes").fetchone()[0]
        total_articles = cur.execute("SELECT COUNT(*) FROM articles_meta").fetchone()[0]
        avg_axes = (total_rows / total_articles) if total_articles else 0.0

        # Multiple primaries per article: detect even though DDL has UNIQUE partial idx
        multi_primary = cur.execute(
            "SELECT article_id, COUNT(*) FROM article_axes WHERE is_primary = 1 "
            "GROUP BY article_id HAVING COUNT(*) > 1"
        ).fetchall()

        unclassified = cur.execute(
            "SELECT COUNT(DISTINCT article_id) FROM article_axes WHERE axis_id = ?",
            (UNCLASSIFIED,),
        ).fetchone()[0]

        per_axis_counts: dict[str, int] = {}
        for axis in self.config.axes:
            n = cur.execute(
                "SELECT COUNT(*) FROM article_axes WHERE axis_id = ?",
                (axis.id,),
            ).fetchone()[0]
            per_axis_counts[axis.id] = n

        failures: list[str] = []
        if bounds.total_rows_min is not None and total_rows < bounds.total_rows_min:
            failures.append(f"total_rows {total_rows} < min {bounds.total_rows_min}")
        if bounds.total_rows_max is not None and total_rows > bounds.total_rows_max:
            failures.append(f"total_rows {total_rows} > max {bounds.total_rows_max}")
        if (
            bounds.avg_axes_per_article_min is not None
            and avg_axes < bounds.avg_axes_per_article_min
        ):
            failures.append(f"avg_axes {avg_axes:.2f} < min {bounds.avg_axes_per_article_min}")
        if (
            bounds.avg_axes_per_article_max is not None
            and avg_axes > bounds.avg_axes_per_article_max
        ):
            failures.append(f"avg_axes {avg_axes:.2f} > max {bounds.avg_axes_per_article_max}")
        if bounds.primary_uniq_required and multi_primary:
            failures.append(f"{len(multi_primary)} articles have multiple primary axes")
        if bounds.unclassified_max is not None and unclassified > bounds.unclassified_max:
            failures.append(f"unclassified {unclassified} > max {bounds.unclassified_max}")
        for axis in self.config.axes:
            n = per_axis_counts[axis.id]
            if n == 0:
                failures.append(f"axis {axis.id!r} has no articles")
            if bounds.per_axis_min is not None and n < bounds.per_axis_min:
                failures.append(f"axis {axis.id!r} count {n} < min {bounds.per_axis_min}")
            if bounds.per_axis_max is not None and n > bounds.per_axis_max:
                failures.append(f"axis {axis.id!r} count {n} > max {bounds.per_axis_max}")

        metrics: dict[str, object] = {
            "total_rows": total_rows,
            "total_articles": total_articles,
            "avg_axes_per_article": round(avg_axes, 4),
            "primary_uniq_violations": len(multi_primary),
            "unclassified": unclassified,
            "per_axis": per_axis_counts,
        }
        return ValidationReport(passed=not failures, metrics=metrics, failures=failures)


__all__ = [
    "AxesClassifier",
    "ClassifyResult",
    "ValidationReport",
    "load_axes_config",
    "UNCLASSIFIED",
]
