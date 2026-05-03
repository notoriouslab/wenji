"""LLM provider configuration loaded from ``WENJI_LLM_*`` environment variables.

This module is shared by:

- ``wenji.aggregate.llm.LLMClient`` (v0.3.0+, aggregate / ask functionality)
- ``wenji.search.rewrite.QueryRewriter`` (v0.3.2+, query rewrite wiring)

Recognised env vars:

| variable | scope | meaning | default |
|---|---|---|---|
| ``WENJI_LLM_BASE_URL`` | shared | OpenAI-compatible base URL | unset |
| ``WENJI_LLM_API_KEY`` | shared | API key | unset |
| ``WENJI_LLM_MODEL`` | shared | model name | unset |
| ``WENJI_LLM_TIMEOUT`` | shared | per-request timeout (seconds) | ``10.0`` |
| ``WENJI_LLM_REWRITE_CACHE_TTL_DAYS`` | rewriter-only | cache TTL | ``30`` |

When ``LLMConfig.enabled`` is ``False`` (any of base_url / api_key / model
unset), neither the aggregator nor the rewriter SHALL be instantiated. This
preserves the v0.3.1 default-disabled behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    """Shared LLM provider configuration."""

    base_url: str | None
    api_key: str | None
    model: str | None
    timeout: float = 10.0
    rewrite_cache_ttl_days: int = 30

    @property
    def enabled(self) -> bool:
        """True iff base_url, api_key, and model are all non-empty."""
        return bool(self.base_url and self.api_key and self.model)

    def missing_fields(self) -> list[str]:
        """Return env var names needed for ``enabled=True``."""
        missing: list[str] = []
        if not self.base_url:
            missing.append("WENJI_LLM_BASE_URL")
        if not self.api_key:
            missing.append("WENJI_LLM_API_KEY")
        if not self.model:
            missing.append("WENJI_LLM_MODEL")
        return missing


def load_llm_config_from_env() -> LLMConfig:
    """Load :class:`LLMConfig` from ``WENJI_LLM_*`` environment variables.

    Defaults: ``timeout=10.0``, ``rewrite_cache_ttl_days=30``.
    Missing core vars (base_url / api_key / model) result in ``enabled=False``.
    """
    base_url = os.environ.get("WENJI_LLM_BASE_URL") or None
    api_key = os.environ.get("WENJI_LLM_API_KEY") or None
    model = os.environ.get("WENJI_LLM_MODEL") or None

    timeout_raw = os.environ.get("WENJI_LLM_TIMEOUT", "10.0")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 10.0

    ttl_raw = os.environ.get("WENJI_LLM_REWRITE_CACHE_TTL_DAYS", "30")
    try:
        rewrite_cache_ttl_days = int(ttl_raw)
    except ValueError:
        rewrite_cache_ttl_days = 30

    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        rewrite_cache_ttl_days=rewrite_cache_ttl_days,
    )
