"""Load + validate ``axes.yaml`` for axis classification.

Corpus-agnostic: empty ``axes: []`` is valid (every article ends up in
``unclassified``). Validation bounds default to permissive values so users
without a tuned corpus model can still run; tighten via the YAML
``validation:`` block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from wenji.core.errors import ConfigError

UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Rule:
    """A single classification rule.

    All match fields are AND-combined. ``primary`` controls whether the rule
    can mark its parent axis as the article's primary axis when matched.
    """

    source_type: str
    primary: bool
    title_regex: str | None = None
    subtype: str | None = None
    tag: str | None = None
    retag_source_type_to: str | None = None
    _compiled_regex: re.Pattern[str] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Axis:
    id: str
    name: str
    order: int
    rules: tuple[Rule, ...]
    short: str = ""
    description: str = ""
    icon: str = ""
    parent: str | None = None


@dataclass(frozen=True)
class ValidationBounds:
    """Soft / hard bounds enforced by :meth:`AxesClassifier.validate`.

    Defaults are permissive (no bounds applied). Tighten via the YAML
    ``validation:`` block to match the user's corpus reality.
    """

    total_rows_min: int | None = None
    total_rows_max: int | None = None
    avg_axes_per_article_min: float | None = None
    avg_axes_per_article_max: float | None = None
    primary_uniq_required: bool = True
    unclassified_max: int | None = None
    per_axis_min: int | None = None
    per_axis_max: int | None = None


@dataclass(frozen=True)
class AxesConfig:
    version: int
    axes: tuple[Axis, ...]
    validation: ValidationBounds

    def find_axis(self, axis_id: str) -> Axis | None:
        return next((a for a in self.axes if a.id == axis_id), None)

    def ancestors(self, axis_id: str) -> list[str]:
        """Return parent chain of ``axis_id`` nearest-first.

        Returns ``[]`` for root axes, unknown axes, or flat (no-``parent``)
        configurations. Cycles are rejected at load time so the walk cannot
        loop forever.
        """
        chain: list[str] = []
        current = self.find_axis(axis_id)
        if current is None:
            return chain
        seen: set[str] = {current.id}
        while current is not None and current.parent is not None:
            if current.parent in seen:
                break
            chain.append(current.parent)
            seen.add(current.parent)
            current = self.find_axis(current.parent)
        return chain


def _build_rule(raw: dict, axis_id: str, idx: int) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"axis {axis_id!r} rule[{idx}] must be a mapping, got {type(raw).__name__}"
        )
    src = raw.get("source_type")
    if not isinstance(src, str) or not src:
        raise ConfigError(f"axis {axis_id!r} rule[{idx}] missing or empty 'source_type'")
    primary = raw.get("primary")
    if not isinstance(primary, bool):
        raise ConfigError(f"axis {axis_id!r} rule[{idx}] 'primary' must be bool")

    title_regex = raw.get("title_regex")
    compiled: re.Pattern[str] | None = None
    if title_regex is not None:
        if not isinstance(title_regex, str):
            raise ConfigError(f"axis {axis_id!r} rule[{idx}] 'title_regex' must be string")
        try:
            compiled = re.compile(title_regex)
        except re.error as exc:
            raise ConfigError(f"axis {axis_id!r} rule[{idx}] invalid regex: {exc}") from exc

    for opt in ("subtype", "tag", "retag_source_type_to"):
        v = raw.get(opt)
        if v is not None and not isinstance(v, str):
            raise ConfigError(f"axis {axis_id!r} rule[{idx}] {opt!r} must be string")

    return Rule(
        source_type=src,
        primary=primary,
        title_regex=title_regex,
        subtype=raw.get("subtype"),
        tag=raw.get("tag"),
        retag_source_type_to=raw.get("retag_source_type_to"),
        _compiled_regex=compiled,
    )


def _build_axis(raw: dict, idx: int) -> Axis:
    if not isinstance(raw, dict):
        raise ConfigError(f"axes[{idx}] must be a mapping")
    required = {"id", "name", "order", "rules"}
    missing = required - set(raw)
    if missing:
        raise ConfigError(f"axes[{idx}] missing fields: {sorted(missing)}")
    if not isinstance(raw["id"], str) or not raw["id"]:
        raise ConfigError(f"axes[{idx}] 'id' must be non-empty string")
    if raw["id"] == UNCLASSIFIED:
        raise ConfigError(f"axes[{idx}] id 'unclassified' is reserved")
    if not isinstance(raw["order"], int):
        raise ConfigError(f"axis {raw['id']!r} 'order' must be int")
    if not isinstance(raw["rules"], list):
        raise ConfigError(f"axis {raw['id']!r} 'rules' must be a list")

    parent = raw.get("parent")
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise ConfigError(f"axis {raw['id']!r} 'parent' must be a non-empty string or null")

    rules = tuple(_build_rule(r, raw["id"], i) for i, r in enumerate(raw["rules"]))
    return Axis(
        id=raw["id"],
        name=str(raw["name"]),
        short=str(raw.get("short", "")),
        order=int(raw["order"]),
        description=str(raw.get("description", "")),
        rules=rules,
        icon=str(raw.get("icon", "")),
        parent=parent,
    )


def _build_validation(raw: dict | None) -> ValidationBounds:
    if raw is None:
        return ValidationBounds()
    if not isinstance(raw, dict):
        raise ConfigError("'validation' must be a mapping if present")

    def _opt_int(key: str) -> int | None:
        v = raw.get(key)
        return int(v) if v is not None else None

    def _opt_float(key: str) -> float | None:
        v = raw.get(key)
        return float(v) if v is not None else None

    return ValidationBounds(
        total_rows_min=_opt_int("total_rows_min"),
        total_rows_max=_opt_int("total_rows_max"),
        avg_axes_per_article_min=_opt_float("avg_axes_per_article_min"),
        avg_axes_per_article_max=_opt_float("avg_axes_per_article_max"),
        primary_uniq_required=bool(raw.get("primary_uniq_required", True)),
        unclassified_max=_opt_int("unclassified_max"),
        per_axis_min=_opt_int("per_axis_min"),
        per_axis_max=_opt_int("per_axis_max"),
    )


def _validate_parent_chains(axes: tuple[Axis, ...]) -> None:
    """Reject unknown parent references and cycles in the parent chain."""
    by_id = {a.id: a for a in axes}
    for axis in axes:
        if axis.parent is None:
            continue
        if axis.parent not in by_id:
            raise ConfigError(
                f"axis {axis.id!r} parent {axis.parent!r} does not refer to a known axis"
            )
        seen = [axis.id]
        cur: str | None = axis.parent
        while cur is not None:
            if cur in seen:
                seen.append(cur)
                raise ConfigError(f"axis cycle detected: {' -> '.join(seen)}")
            seen.append(cur)
            cur = by_id[cur].parent


def load_axes_config(path: str | Path) -> AxesConfig:
    """Parse + validate axes.yaml from ``path``."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigError(f"axes config not found: {cfg_path}")

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {cfg_path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"axes.yaml top level must be a mapping, got {type(raw).__name__}")

    raw_axes = raw.get("axes", [])
    if not isinstance(raw_axes, list):
        raise ConfigError("'axes' must be a list (may be empty)")

    axes = tuple(_build_axis(a, i) for i, a in enumerate(raw_axes))
    axes = tuple(sorted(axes, key=lambda a: a.order))

    ids = [a.id for a in axes]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"duplicate axis ids: {ids}")
    orders = [a.order for a in axes]
    if len(set(orders)) != len(orders):
        raise ConfigError(f"duplicate axis order values: {orders}")

    _validate_parent_chains(axes)

    return AxesConfig(
        version=int(raw.get("version", 1)),
        axes=axes,
        validation=_build_validation(raw.get("validation")),
    )
