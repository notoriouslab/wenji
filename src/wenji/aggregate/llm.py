"""OpenAI-compatible LLM client (zero abstraction layer, see design D3).

A thin :mod:`httpx` wrapper around any endpoint that conforms to the OpenAI
``chat/completions`` schema (Groq, OpenRouter, Together, Gemini OpenAI-compat,
vLLM, self-hosted llama.cpp, etc.). Failures are normalised to
:class:`LLMClientError`; the Aggregator catches that error and falls back to
``narrative=None`` (design D7).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx


class LLMClientError(Exception):
    """Raised when an LLM call fails (timeout, 4xx, 5xx, or response-shape mismatch)."""


@dataclass
class LLMClient:
    base_url: str
    model: str
    api_key: str
    timeout: float = 10.0
    #: Optional post-processor applied to every returned/yielded text piece.
    #: Kept generic (a plain callable) so the client stays domain-neutral; the
    #: web layer wires Traditional-Chinese conversion here when configured.
    output_transform: Callable[[str], str] | None = field(default=None, repr=False)
    _transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.timeout = min(self.timeout, 30.0)

    def _transform(self, text: str) -> str:
        return self.output_transform(text) if self.output_transform else text

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
        return self._transform(content)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """Yield answer fragments as the model produces them.

        Same request shape as :meth:`chat` plus ``stream: true``, parsing the
        OpenAI-compatible SSE frames (``data: {...}`` lines terminated by
        ``data: [DONE]``). ``timeout`` applies per read, not to the whole
        stream, so a long answer is not cut off.

        Raises :class:`LLMClientError` on transport failure or if the stream
        ends without producing any text; malformed individual frames are
        skipped rather than aborting a stream that is otherwise fine.
        """
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        produced = False
        try:
            with httpx.Client(transport=self._transport, timeout=self.timeout) as client:
                with client.stream("POST", url, headers=headers, json=body) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:") :].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            delta = json.loads(payload)["choices"][0]["delta"]
                        except (KeyError, IndexError, ValueError, TypeError):
                            continue
                        piece = delta.get("content")
                        if piece:
                            produced = True
                            yield self._transform(piece)
        except httpx.HTTPError as exc:
            msg = re.sub(r"Bearer [A-Za-z0-9._-]+", "Bearer ***", str(exc))
            raise LLMClientError(f"LLM stream failed: {msg}") from exc

        if not produced:
            raise LLMClientError("LLM stream produced no content")


if __name__ == "__main__":
    print("wenji.aggregate.llm — module loaded; no network call performed.")
