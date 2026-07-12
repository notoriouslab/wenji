"""wenji.config — YAML config loader using pyyaml + dataclasses (no pydantic).

Public API:
- :func:`load_config` — read YAML + apply defaults
- :class:`WenjiConfig` — root config dataclass
"""

from __future__ import annotations

from wenji.config.defaults import (
    DEFAULT_CHUNK_STRATEGIES,
    DEFAULT_DIRECTORY_MAP,
    DEFAULT_SEARCH_CONFIG,
)
from wenji.config.llm import LLMConfig, load_llm_config_from_env
from wenji.config.loader import (
    SearchConfig,
    WenjiConfig,
    load_config,
    resolve_config_path,
)

__all__ = [
    "load_config",
    "resolve_config_path",
    "WenjiConfig",
    "SearchConfig",
    "DEFAULT_DIRECTORY_MAP",
    "DEFAULT_SEARCH_CONFIG",
    "DEFAULT_CHUNK_STRATEGIES",
    "LLMConfig",
    "load_llm_config_from_env",
]
