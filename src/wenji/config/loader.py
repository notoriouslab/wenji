"""YAML config loader (pyyaml + dataclass, no pydantic).

A single ``wenji.yaml`` (or split files merged at higher layer) carries:

```yaml
directory_map:
  sermons: sermon
  articles: article

chunk_strategies:
  sermon: {strategy: paragraph, min_chars: 200, max_chars: 1500}

search:
  alpha: 0.25
  candidate_pool: 50
```

Missing keys fall back to :mod:`wenji.config.defaults`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from wenji.config.defaults import (
    DEFAULT_CHUNK_STRATEGIES,
    DEFAULT_DIRECTORY_MAP,
    DEFAULT_SEARCH_CONFIG,
    DEFAULT_WEB_CONFIG,
)
from wenji.core.errors import ConfigError


@dataclass(frozen=True)
class SearchConfig:
    alpha: float = 0.25
    candidate_pool: int = 50
    default_limit: int = 10


@dataclass(frozen=True)
class WebConfig:
    hero_title: str
    hero_subtitle: str | None
    search_placeholder: str
    topic_shortcuts: tuple[dict, ...]


@dataclass(frozen=True)
class WenjiConfig:
    directory_map: dict[str, str]
    chunk_strategies: dict[str, dict]
    search: SearchConfig
    web: WebConfig
    directory_map_overrides_frontmatter: bool = False


def _merge_dicts(base: dict, override: dict | None) -> dict:
    if override is None:
        return dict(base)
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dicts(out[k], v)
        else:
            out[k] = v
    return out


def _build_search(raw: dict | None) -> SearchConfig:
    merged = _merge_dicts(DEFAULT_SEARCH_CONFIG, raw or {})
    if not 0.0 <= float(merged["alpha"]) <= 1.0:
        raise ConfigError(f"search.alpha must be in [0, 1]; got {merged['alpha']}")
    return SearchConfig(
        alpha=float(merged["alpha"]),
        candidate_pool=int(merged["candidate_pool"]),
        default_limit=int(merged["default_limit"]),
    )


def _build_web(raw: dict | None) -> WebConfig:
    if raw is not None and not isinstance(raw, dict):
        raise ConfigError("'web' must be a mapping")
    raw = raw or {}
    # `topic_shortcuts: []` is a deliberate "hide the section" override, so an
    # explicit key must win over the default even when falsy.
    shortcuts_raw = raw.get("topic_shortcuts", DEFAULT_WEB_CONFIG["topic_shortcuts"])
    if not isinstance(shortcuts_raw, list):
        raise ConfigError("'web.topic_shortcuts' must be a list")
    shortcuts: list[dict] = []
    for i, group in enumerate(shortcuts_raw):
        if not isinstance(group, dict) or not group.get("category"):
            raise ConfigError(f"web.topic_shortcuts[{i}] needs a 'category' string")
        topics = group.get("topics")
        if not isinstance(topics, list) or not all(isinstance(t, str) for t in topics):
            raise ConfigError(f"web.topic_shortcuts[{i}].topics must be a list of strings")
        shortcuts.append(
            {
                "category": str(group["category"]),
                "icon": str(group["icon"]) if group.get("icon") else None,
                "topics": [str(t) for t in topics],
            }
        )
    hero_subtitle = raw.get("hero_subtitle", DEFAULT_WEB_CONFIG["hero_subtitle"])
    return WebConfig(
        hero_title=str(raw.get("hero_title") or DEFAULT_WEB_CONFIG["hero_title"]),
        hero_subtitle=str(hero_subtitle) if hero_subtitle else None,
        search_placeholder=str(
            raw.get("search_placeholder") or DEFAULT_WEB_CONFIG["search_placeholder"]
        ),
        topic_shortcuts=tuple(shortcuts),
    )


def resolve_config_path(cli_path: str | Path | None = None) -> str | Path | None:
    """Resolution order for the config file: CLI ``--config`` flag >
    ``WENJI_CONFIG`` environment variable > ``None`` (built-in defaults).

    Centralised here so every Searcher entry point (web factory, ``wenji
    search`` fallback, ``Asker``) resolves identically.
    """
    if cli_path is not None:
        return cli_path
    env = os.environ.get("WENJI_CONFIG", "").strip()
    return env or None


def load_config(path: str | Path | None = None) -> WenjiConfig:
    """Load a wenji.yaml from ``path`` (or return all-defaults when None)."""
    if path is None:
        return WenjiConfig(
            directory_map=dict(DEFAULT_DIRECTORY_MAP),
            chunk_strategies=dict(DEFAULT_CHUNK_STRATEGIES),
            search=_build_search(None),
            web=_build_web(None),
            directory_map_overrides_frontmatter=False,
        )

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config not found: {p}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config top level must be mapping, got {type(raw).__name__}")

    directory_map_raw = raw.get("directory_map") or {}
    if not isinstance(directory_map_raw, dict):
        raise ConfigError("'directory_map' must be a mapping")
    chunk_strategies_raw = raw.get("chunk_strategies") or {}
    if not isinstance(chunk_strategies_raw, dict):
        raise ConfigError("'chunk_strategies' must be a mapping")

    return WenjiConfig(
        directory_map={str(k): str(v) for k, v in directory_map_raw.items()},
        chunk_strategies=dict(chunk_strategies_raw),
        search=_build_search(raw.get("search")),
        web=_build_web(raw.get("web")),
        directory_map_overrides_frontmatter=bool(
            raw.get("directory_map_overrides_frontmatter", False)
        ),
    )
