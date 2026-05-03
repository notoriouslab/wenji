"""JSONL eval-set loader with multi-path schema validation.

Each line of the candidates file SHALL be a JSON object containing at minimum
``id``, ``query``, and a non-empty ``gold_paths`` list. Each gold path is one
independently-valid answer trajectory; a question passes when ANY one of its
gold paths is fully matched by retrieval (OR semantics). Missing required
fields raise :class:`IngestError` with the line number; the loader is strict
so users notice malformed data early.

Legacy single-path schema (``expected_keywords`` + ``expected_article_hints``
at top-level) is REJECTED with a migration hint pointing to
``wenji eval migrate-jsonl``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wenji.core.errors import IngestError

_LEGACY_HINT = (
    "legacy single-path schema detected ('expected_keywords' / "
    "'expected_article_hints' at top level). Run "
    "`wenji eval migrate-jsonl <old> <new>` to wrap legacy entries as "
    "single-path gold_paths."
)


@dataclass(frozen=True)
class GoldPath:
    """One independently-valid answer trajectory for a question."""

    path_tag: str
    keywords: tuple[str, ...]
    article_hints: tuple[str, ...] = ()
    expected_direction: str = ""


@dataclass(frozen=True)
class Candidate:
    """A single eval question with one or more gold paths."""

    id: int
    query: str
    gold_paths: tuple[GoldPath, ...]
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


def _parse_gold_path(raw: Any, *, lineno: int, idx: int) -> GoldPath:
    if not isinstance(raw, dict):
        raise IngestError(
            f"line {lineno}: gold_paths[{idx}] must be an object, got {type(raw).__name__}"
        )
    if "keywords" not in raw:
        raise IngestError(
            f"line {lineno}: gold_paths[{idx}] missing required field 'keywords'"
        )
    keywords = _coerce_string_list(raw["keywords"], f"gold_paths[{idx}].keywords", lineno)
    if not keywords:
        raise IngestError(
            f"line {lineno}: gold_paths[{idx}].keywords must be non-empty"
        )
    article_hints = _coerce_string_list(
        raw.get("article_hints"), f"gold_paths[{idx}].article_hints", lineno
    )
    path_tag = str(raw.get("path_tag", f"path_{idx}"))
    expected_direction = str(raw.get("expected_direction", ""))
    return GoldPath(
        path_tag=path_tag,
        keywords=keywords,
        article_hints=article_hints,
        expected_direction=expected_direction,
    )


def wrap_legacy_candidate(old: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy single-path JSONL entry to multi-path schema.

    Wraps ``expected_keywords`` + ``expected_article_hints`` as a single-element
    ``gold_paths`` list with ``path_tag="default"``. Returns a new dict; does
    not mutate the input.

    Used by ``wenji eval migrate-jsonl`` and for backward compatibility of
    user-supplied JSONL files. Entries that already have ``gold_paths`` pass
    through unchanged.
    """
    if "gold_paths" in old:
        return dict(old)
    if "expected_keywords" not in old:
        raise IngestError("legacy entry missing 'expected_keywords' for migration")
    new = {k: v for k, v in old.items() if k not in {"expected_keywords", "expected_article_hints"}}
    keywords = old["expected_keywords"]
    if isinstance(keywords, str):
        keywords = [keywords]
    article_hints = old.get("expected_article_hints", [])
    if isinstance(article_hints, str):
        article_hints = [article_hints]
    new["gold_paths"] = [
        {
            "path_tag": "default",
            "keywords": list(keywords) if keywords is not None else [],
            "article_hints": list(article_hints) if article_hints is not None else [],
        }
    ]
    return new


def load_candidates(path: str | Path) -> list[Candidate]:
    """Read a JSONL file and return a list of :class:`Candidate`.

    Empty lines and lines starting with ``#`` (after optional whitespace) are
    skipped. Required fields per line: ``query`` + non-empty ``gold_paths``;
    ``id`` is auto-assigned from the line number when absent.

    Legacy single-path schema (``expected_keywords`` / ``expected_article_hints``
    at top level without ``gold_paths``) is rejected with a migration hint.
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

            query = obj["query"]
            if not isinstance(query, str) or not query.strip():
                raise IngestError(f"line {lineno}: 'query' must be a non-empty string")

            if "gold_paths" not in obj:
                if "expected_keywords" in obj:
                    raise IngestError(f"line {lineno}: {_LEGACY_HINT}")
                raise IngestError(
                    f"line {lineno}: missing required field 'gold_paths'"
                )

            gold_paths_raw = obj["gold_paths"]
            if not isinstance(gold_paths_raw, list):
                raise IngestError(
                    f"line {lineno}: 'gold_paths' must be a list, got {type(gold_paths_raw).__name__}"
                )
            if not gold_paths_raw:
                raise IngestError(
                    f"line {lineno}: 'gold_paths' must have at least one entry"
                )
            gold_paths = tuple(
                _parse_gold_path(raw_path, lineno=lineno, idx=idx)
                for idx, raw_path in enumerate(gold_paths_raw)
            )

            cid_raw = obj.get("id", lineno)
            try:
                cid = int(cid_raw)
            except (TypeError, ValueError) as exc:
                raise IngestError(f"line {lineno}: 'id' must be int, got {cid_raw!r}") from exc

            consumed = {"id", "query", "gold_paths", "category", "source"}
            extras = {k: v for k, v in obj.items() if k not in consumed}

            candidates.append(
                Candidate(
                    id=cid,
                    query=query,
                    gold_paths=gold_paths,
                    category=str(obj.get("category", "")),
                    source=str(obj.get("source", "")),
                    extras=extras,
                )
            )
    return candidates
