"""LLM provider configuration loaded from ``WENJI_LLM_*`` environment variables.

This module serves ``wenji.aggregate.llm.LLMClient`` (v0.3.0+, aggregate /
ask functionality).

Recognised env vars:

| variable | scope | meaning | default |
|---|---|---|---|
| ``WENJI_LLM_BASE_URL`` | shared | OpenAI-compatible base URL | unset |
| ``WENJI_LLM_API_KEY`` | shared | API key | unset |
| ``WENJI_LLM_MODEL`` | shared | model name | unset |
| ``WENJI_LLM_TIMEOUT`` | shared | per-request timeout (seconds) | ``10.0`` |

When ``LLMConfig.enabled`` is ``False`` (any of base_url / api_key / model
unset), the aggregator SHALL NOT be instantiated. This
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

    Defaults: ``timeout=10.0``.
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

    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
