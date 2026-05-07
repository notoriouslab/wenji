"""OpenAI-compatible LLM client (zero abstraction layer, see design D3).

A thin :mod:`httpx` wrapper around any endpoint that conforms to the OpenAI
``chat/completions`` schema (Groq, OpenRouter, Together, Gemini OpenAI-compat,
vLLM, self-hosted llama.cpp, etc.). Failures are normalised to
:class:`LLMClientError`; the Aggregator catches that error and falls back to
``narrative=None`` (design D7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import re

import httpx


class LLMClientError(Exception):
    """Raised when an LLM call fails (timeout, 4xx, 5xx, or response-shape mismatch)."""


@dataclass
class LLMClient:
    base_url: str
    model: str
    api_key: str
    timeout: float = 10.0
    _transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.timeout = min(self.timeout, 30.0)

    def chat(self, messages: list[dict]) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            msg = str(exc)
            msg = re.sub(r"Bearer [A-Za-z0-9._-]+", "Bearer ***", msg)
            raise LLMClientError(f"LLM call failed: {msg}") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMClientError("LLM returned empty response")
        return content


if __name__ == "__main__":
    print("wenji.aggregate.llm — module loaded; no network call performed.")
