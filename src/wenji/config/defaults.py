"""Built-in defaults applied when YAML config keys are missing.

Each mapping is a dict literal so user can pull the canonical default and
diff against their override.
"""

from __future__ import annotations

DEFAULT_DIRECTORY_MAP: dict[str, str] = {}
"""Default directory_map is empty — user MUST set if relying on path-based source_type."""

DEFAULT_CHUNK_STRATEGIES: dict[str, dict] = {}
"""Default chunk_strategies is empty — articles not chunked unless source_type is configured."""

DEFAULT_SEARCH_CONFIG: dict = {
    "alpha": 0.25,
    "candidate_pool": 50,
    "default_limit": 10,
    "rerank": {
        "enabled": False,
        "model_dir": None,
    },
}
