"""Tests for wenji.classify.axes_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from wenji.classify.axes_loader import (
    UNCLASSIFIED,
    AxesConfig,
    ValidationBounds,
    load_axes_config,
)
from wenji.core.errors import ConfigError


def write_yaml(p: Path, content: str) -> Path:
    p.write_text(content, encoding="utf-8")
    return p


def test_load_minimal_config(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - id: theology
    name: 神學
    order: 1
    rules:
      - source_type: tgc-theology
        primary: true
""",
    )
    cfg = load_axes_config(cfg_path)
    assert isinstance(cfg, AxesConfig)
    assert len(cfg.axes) == 1
    assert cfg.axes[0].id == "theology"
    assert cfg.axes[0].rules[0].source_type == "tgc-theology"


def test_empty_axes_valid(tmp_path):
    cfg_path = write_yaml(tmp_path / "axes.yaml", "axes: []\n")
    cfg = load_axes_config(cfg_path)
    assert cfg.axes == ()


def test_user_defined_axis_names_accepted(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - id: recipes
    name: 食譜
    order: 1
    rules: [{source_type: cooking, primary: true}]
  - id: travel
    name: 旅遊
    order: 2
    rules: [{source_type: trips, primary: true}]
""",
    )
    cfg = load_axes_config(cfg_path)
    assert {a.id for a in cfg.axes} == {"recipes", "travel"}


def test_axes_sorted_by_order(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: b, name: B, order: 2, rules: [{source_type: t, primary: true}]}
  - {id: a, name: A, order: 1, rules: [{source_type: t, primary: true}]}
""",
    )
    cfg = load_axes_config(cfg_path)
    assert [a.id for a in cfg.axes] == ["a", "b"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_axes_config(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("axes: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML parse"):
        load_axes_config(cfg_path)


def test_unclassified_id_reserved(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - id: unclassified
    name: X
    order: 1
    rules: [{source_type: t, primary: true}]
""",
    )
    with pytest.raises(ConfigError, match="reserved"):
        load_axes_config(cfg_path)


def test_duplicate_id_raises(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: a, name: A, order: 1, rules: [{source_type: t, primary: true}]}
  - {id: a, name: A2, order: 2, rules: [{source_type: u, primary: true}]}
""",
    )
    with pytest.raises(ConfigError, match="duplicate axis ids"):
        load_axes_config(cfg_path)


def test_rule_missing_primary_raises(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - id: a
    name: A
    order: 1
    rules:
      - source_type: t
""",
    )
    with pytest.raises(ConfigError, match="primary"):
        load_axes_config(cfg_path)


def test_rule_invalid_regex_raises(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - id: a
    name: A
    order: 1
    rules:
      - {source_type: t, primary: true, title_regex: '['}
""",
    )
    with pytest.raises(ConfigError, match="invalid regex"):
        load_axes_config(cfg_path)


def test_validation_block_parsed(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: a, name: A, order: 1, rules: [{source_type: t, primary: true}]}
validation:
  unclassified_max: 4
  per_axis_min: 100
  per_axis_max: 6000
""",
    )
    cfg = load_axes_config(cfg_path)
    assert cfg.validation.unclassified_max == 4
    assert cfg.validation.per_axis_min == 100
    assert cfg.validation.per_axis_max == 6000


def test_validation_defaults_permissive(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: a, name: A, order: 1, rules: [{source_type: t, primary: true}]}
""",
    )
    cfg = load_axes_config(cfg_path)
    assert cfg.validation.unclassified_max is None
    assert cfg.validation.per_axis_min is None
    assert cfg.validation.per_axis_max is None


def test_unclassified_constant_exposed():
    assert UNCLASSIFIED == "unclassified"


def test_validation_bounds_defaults():
    b = ValidationBounds()
    assert b.unclassified_max is None
    assert b.primary_uniq_required is True


def test_three_level_hierarchy_loads_with_ancestors(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: theology, name: 神學, order: 1, rules: [{source_type: t, primary: true}]}
  - id: soteriology
    name: 救恩論
    order: 2
    parent: theology
    rules: [{source_type: t, primary: true}]
  - id: justification
    name: 因信稱義
    order: 3
    parent: soteriology
    rules: [{source_type: t, primary: true}]
""",
    )
    cfg = load_axes_config(cfg_path)
    assert cfg.find_axis("justification").parent == "soteriology"
    assert cfg.ancestors("justification") == ["soteriology", "theology"]
    assert cfg.ancestors("soteriology") == ["theology"]
    assert cfg.ancestors("theology") == []


def test_parent_cycle_raises(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: a, name: A, order: 1, parent: b, rules: [{source_type: t, primary: true}]}
  - {id: b, name: B, order: 2, parent: a, rules: [{source_type: t, primary: true}]}
""",
    )
    with pytest.raises(ConfigError, match="cycle"):
        load_axes_config(cfg_path)


def test_unknown_parent_raises(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - id: child
    name: 子
    order: 1
    parent: nonexistent
    rules: [{source_type: t, primary: true}]
""",
    )
    with pytest.raises(ConfigError, match="does not refer to a known axis"):
        load_axes_config(cfg_path)


def test_flat_axes_have_empty_ancestors(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "axes.yaml",
        """
axes:
  - {id: a, name: A, order: 1, rules: [{source_type: t, primary: true}]}
  - {id: b, name: B, order: 2, rules: [{source_type: u, primary: true}]}
""",
    )
    cfg = load_axes_config(cfg_path)
    assert cfg.ancestors("a") == []
    assert cfg.ancestors("b") == []
    assert all(axis.parent is None for axis in cfg.axes)
