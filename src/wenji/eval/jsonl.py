"""JSONL eval-set loader with schema validation.

Each line of the candidates file SHALL be a JSON object containing at minimum
``id``, ``query``, and ``expected_keywords``. Missing required fields raise
:class:`IngestError` with the line number; the framework is intentionally
strict so users notice malformed data early.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from wenji.core.errors import IngestError


@dataclass(frozen=True)
class Candidate:
    """A single eval question."""

    id: int
    query: str
    expected_keywords: tuple[str, ...]
    expected_article_hints: tuple[str, ...] = ()
    category: str = ""
    source: str = ""
    extras: dict = field(default_factory=dict)


def _coerce_string_list(value, field_name: str, lineno: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    raise IngestError(
        f"line {lineno}: {field_name!r} must be a list or string, got {type(value).__name__}"
    )


def load_candidates(path: str | Path) -> list[Candidate]:
    """Read a JSONL file and return a list of :class:`Candidate`.

    Empty lines and lines starting with ``#`` (after optional whitespace) are
    skipped. Required fields are ``query`` and ``expected_keywords``; ``id`` is
    auto-assigned from the line number when absent.
    """
    p = Path(path)
    if not p.exists():
        raise IngestError(f"candidates file not found: {p}")

    candidates: list[Candidate] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise IngestError(f"line {lineno}: invalid JSON: {exc}") from exc
            if not isinstance(obj, dict):
                raise IngestError(
                    f"line {lineno}: top level must be object, got {type(obj).__name__}"
                )
            if "query" not in obj:
                raise IngestError(f"line {lineno}: missing required field 'query'")
            if "expected_keywords" not in obj:
                raise IngestError(f"line {lineno}: missing required field 'expected_keywords'")

            query = obj["query"]
            if not isinstance(query, str) or not query.strip():
                raise IngestError(f"line {lineno}: 'query' must be a non-empty string")

            kws = _coerce_string_list(obj["expected_keywords"], "expected_keywords", lineno)
            hints = _coerce_string_list(
                obj.get("expected_article_hints"), "expected_article_hints", lineno
            )

            cid_raw = obj.get("id", lineno)
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError) as exc:
                raise IngestError(f"line {lineno}: 'id' must be int, got {cid_raw!r}") from exc

            consumed = {
                "id",
                "query",
                "expected_keywords",
                "expected_article_hints",
                "category",
                "source",
            }
            extras = {k: v for k, v in obj.items() if k not in consumed}

            candidates.append(
                Candidate(
                    id=cid,
                    query=query,
                    expected_keywords=kws,
                    expected_article_hints=hints,
                    category=str(obj.get("category", "")),
                    source=str(obj.get("source", "")),
                    extras=extras,
                )
            )
    return candidates
