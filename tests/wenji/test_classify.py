"""Integration tests for wenji.classify.AxesClassifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wenji.classify import UNCLASSIFIED, AxesClassifier, load_axes_config
from wenji.core.db import connect, initialise_schema
from wenji.core.errors import ClassifyError


@pytest.fixture
def db():
    conn = connect(":memory:")
    initialise_schema(conn)
    yield conn
    conn.close()


def _yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "axes.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _seed(conn, article_id: str, **kwargs):
    fields = {
        "article_id": article_id,
        "path": kwargs.get("path", f"fixtures/{article_id}.md"),
        "title": kwargs.get("title", "T"),
        "source_type": kwargs.get("source_type", ""),
        "pub_date": "",
        "pub_year": None,
        "content_length": 0,
        "chunk_count": 0,
        "content_hash": "",
        "indexed_at": "",
        "category": "",
        "author": "",
        "source_url": "",
        "source_urls_json": "",
        "subtype": kwargs.get("subtype", ""),
        "tags": kwargs.get("tags_json", ""),
        "description": "",
    }
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    conn.execute(
        f"INSERT INTO articles_meta ({cols}) VALUES ({placeholders})",
        list(fields.values()),
    )
    conn.commit()


def test_classify_one_writes_axes(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: theology
    name: 神學
    order: 1
    rules:
      - {source_type: tgc-theology, primary: true}
""",
        )
    )
    _seed(db, "a1", source_type="tgc-theology", title="保羅")
    cls = AxesClassifier(db, cfg)
    result = cls.classify_one("a1")
    assert result.matched_axes == [("theology", True)]
    rows = db.execute(
        "SELECT axis_id, is_primary FROM article_axes WHERE article_id='a1'"
    ).fetchall()
    assert rows == [("theology", 1)]


def test_classify_one_unclassified_when_no_match(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: theology
    name: 神學
    order: 1
    rules: [{source_type: tgc-theology, primary: true}]
""",
        )
    )
    _seed(db, "a1", source_type="random-source", title="X")
    cls = AxesClassifier(db, cfg)
    result = cls.classify_one("a1")
    assert result.matched_axes == [(UNCLASSIFIED, True)]


def test_classify_one_missing_article_raises(db, tmp_path):
    cfg = load_axes_config(_yaml(tmp_path, "axes: []\n"))
    cls = AxesClassifier(db, cfg)
    with pytest.raises(ClassifyError, match="not found"):
        cls.classify_one("missing")


def test_multi_axis_union(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: theology
    name: 神學
    order: 1
    rules: [{source_type: shared, primary: true}]
  - id: practice
    name: 實踐
    order: 2
    rules: [{source_type: shared, primary: false}]
""",
        )
    )
    _seed(db, "a1", source_type="shared", title="X")
    cls = AxesClassifier(db, cfg)
    result = cls.classify_one("a1")
    matched = dict(result.matched_axes)
    assert matched == {"theology": True, "practice": False}


def test_first_match_wins_per_axis(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules:
      - {source_type: t, primary: true}
      - {source_type: t, primary: false}
""",
        )
    )
    _seed(db, "a1", source_type="t", title="X")
    cls = AxesClassifier(db, cfg)
    result = cls.classify_one("a1")
    assert result.matched_axes == [("x", True)]


def test_retag_side_effect(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules:
      - source_type: original
        primary: true
        retag_source_type_to: renamed
""",
        )
    )
    _seed(db, "a1", source_type="original", title="X")
    cls = AxesClassifier(db, cfg)
    cls.classify_one("a1")
    new_st = db.execute("SELECT source_type FROM articles_meta WHERE article_id='a1'").fetchone()[0]
    assert new_st == "renamed"


def test_tag_rule_matches(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: prayer-axis
    name: 禱告
    order: 1
    rules:
      - {source_type: sermon, primary: true, tag: 禱告}
""",
        )
    )
    _seed(db, "a1", source_type="sermon", title="X", tags_json=json.dumps(["禱告", "信心"]))
    _seed(db, "a2", source_type="sermon", title="X", tags_json=json.dumps(["其他"]))
    cls = AxesClassifier(db, cfg)
    cls.classify_all()
    rows = db.execute(
        "SELECT article_id FROM article_axes WHERE axis_id='prayer-axis' ORDER BY article_id"
    ).fetchall()
    assert rows == [("a1",)]


def test_classify_all_processes_each_article(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules: [{source_type: t, primary: true}]
""",
        )
    )
    _seed(db, "a1", source_type="t", title="X")
    _seed(db, "a2", source_type="t", title="X")
    _seed(db, "a3", source_type="other", title="X")
    cls = AxesClassifier(db, cfg)
    results = cls.classify_all()
    assert len(results) == 3
    by_axis = {
        row[0]: row[1]
        for row in db.execute("SELECT axis_id, COUNT(*) FROM article_axes GROUP BY axis_id")
    }
    assert by_axis == {"x": 2, UNCLASSIFIED: 1}


def test_classify_all_idempotent(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules: [{source_type: t, primary: true}]
""",
        )
    )
    _seed(db, "a1", source_type="t", title="X")
    cls = AxesClassifier(db, cfg)
    cls.classify_all()
    n1 = db.execute("SELECT COUNT(*) FROM article_axes").fetchone()[0]
    cls.classify_all()
    n2 = db.execute("SELECT COUNT(*) FROM article_axes").fetchone()[0]
    assert n1 == n2 == 1


def test_validate_pass(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules: [{source_type: t, primary: true}]
validation:
  unclassified_max: 0
  per_axis_min: 1
""",
        )
    )
    _seed(db, "a1", source_type="t", title="X")
    cls = AxesClassifier(db, cfg)
    cls.classify_all()
    report = cls.validate()
    assert report.passed is True
    assert report.failures == []
    assert report.metrics["total_rows"] == 1


def test_validate_fail_on_unclassified(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules: [{source_type: t, primary: true}]
validation:
  unclassified_max: 0
""",
        )
    )
    _seed(db, "a1", source_type="random", title="X")
    cls = AxesClassifier(db, cfg)
    cls.classify_all()
    report = cls.validate()
    assert report.passed is False
    assert any("unclassified" in f for f in report.failures)


def test_validate_fail_on_empty_axis(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules: [{source_type: never_matches, primary: true}]
""",
        )
    )
    _seed(db, "a1", source_type="other", title="X")
    cls = AxesClassifier(db, cfg)
    cls.classify_all()
    report = cls.validate()
    assert any("'x'" in f and "no articles" in f for f in report.failures)


def test_validate_fail_on_per_axis_max(db, tmp_path):
    cfg = load_axes_config(
        _yaml(
            tmp_path,
            """
axes:
  - id: x
    name: X
    order: 1
    rules: [{source_type: t, primary: true}]
validation:
  per_axis_max: 1
""",
        )
    )
    _seed(db, "a1", source_type="t", title="X")
    _seed(db, "a2", source_type="t", title="X")
    cls = AxesClassifier(db, cfg)
    cls.classify_all()
    report = cls.validate()
    assert any("per_axis" in f or "count" in f for f in report.failures)
