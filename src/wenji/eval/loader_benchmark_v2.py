"""Loader for the v2 80-question benchmark gold set snapshot.

Reads ``tests/benchmark_80_v2_snapshot.json`` (or any compatible
benchmark v2 schema file) and flattens ``categories[].questions[]``
into a list of multi-path :class:`Candidate` objects suitable for
:func:`wenji.eval.run_baseline`.

The snapshot SHALL be a frozen copy of an upstream benchmark file with
two added top-level fields recording provenance:

- ``source_commit``: git SHA of the upstream repository at snapshot time
- ``snapshot_taken_at``: ISO date the snapshot was captured

Live-linking the upstream file outside the wenji repository is permitted
but emits a warning since the schema may drift; the snapshot pattern is
recommended for reproducibility.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wenji.core.errors import IngestError
from wenji.eval.jsonl import Candidate, GoldPath


@dataclass(frozen=True)
class SnapshotMetadata:
    """Provenance metadata extracted from a v2 benchmark snapshot file."""

    source_commit: str
    snapshot_taken_at: str
    snapshot_source_path: str
    version: str


def _is_inside_wenji_repo(snapshot_path: Path) -> bool:
    """Heuristic: snapshot path is under a wenji-related repo if any ancestor
    contains ``pyproject.toml`` referencing wenji or contains ``src/wenji``."""
    p = snapshot_path.resolve()
    for ancestor in [p.parent, *p.parents]:
        if (ancestor / "src" / "wenji").is_dir():
            return True
        pyp = ancestor / "pyproject.toml"
        if pyp.is_file():
            try:
                if "wenji" in pyp.read_text(encoding="utf-8")[:500]:
                    return True
            except OSError:
                pass
    return False


def _parse_gold_path(raw: dict, *, qid: int, idx: int) -> GoldPath:
    if "path_tag" not in raw:
        raise IngestError(f"question {qid}: gold_paths[{idx}] missing 'path_tag'")
    if "keywords" not in raw:
        raise IngestError(f"question {qid}: gold_paths[{idx}] missing 'keywords'")
    keywords_raw = raw["keywords"]
    if not isinstance(keywords_raw, list) or not keywords_raw:
        raise IngestError(f"question {qid}: gold_paths[{idx}].keywords must be a non-empty list")

    # The upstream v2 schema has `representative_corpus_articles` — extract
    # titles as article_hints when present. Field may be a list of dicts
    # {title,path,...}
    # or a list of strings, or null.
    rca = raw.get("representative_corpus_articles")
    article_hints: list[str] = []
    if isinstance(rca, list):
        for item in rca:
            if isinstance(item, dict):
                title = item.get("title") or item.get("name") or ""
                if title:
                    article_hints.append(str(title))
            elif isinstance(item, str):
                article_hints.append(item)

    return GoldPath(
        path_tag=str(raw["path_tag"]),
        keywords=tuple(str(k) for k in keywords_raw),
        article_hints=tuple(article_hints),
        expected_direction=str(raw.get("expected_direction", "")),
    )


def load_benchmark_v2_snapshot(
    snapshot_path: str | Path,
) -> tuple[list[Candidate], SnapshotMetadata]:
    """Load a v2 benchmark snapshot and return (candidates, metadata).

    The categories' questions are flattened into a single list of multi-path
    Candidates. Path uniqueness within each question is verified.
    """
    p = Path(snapshot_path)
    if not p.exists():
        raise IngestError(f"snapshot file not found: {p}")

    if not _is_inside_wenji_repo(p):
        warnings.warn(
            f"snapshot path {p} is not inside a wenji repository — schema "
            "drift may be undetected. Recommend committing the snapshot to "
            "wenji's tests/ directory.",
            stacklevel=2,
        )

    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"snapshot is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise IngestError(f"snapshot top-level must be object, got {type(data).__name__}")

    metadata = SnapshotMetadata(
        source_commit=str(data.get("source_commit", "")),
        snapshot_taken_at=str(data.get("snapshot_taken_at", "")),
        snapshot_source_path=str(data.get("snapshot_source_path", "")),
        version=str(data.get("version", "")),
    )
    if not metadata.source_commit:
        raise IngestError(
            "snapshot missing required field 'source_commit' (provenance metadata)"
        )

    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        raise IngestError("snapshot 'categories' must be a non-empty list")

    candidates: list[Candidate] = []
    seen_ids: set[int] = set()
    for cat_idx, cat in enumerate(categories):
        if not isinstance(cat, dict):
            raise IngestError(f"categories[{cat_idx}] must be an object, got {type(cat).__name__}")
        cat_name = str(cat.get("name", f"cat_{cat_idx}"))
        questions = cat.get("questions") or []
        if not isinstance(questions, list):
            raise IngestError(f"categories[{cat_idx}].questions must be a list")
        for q in questions:
            if not isinstance(q, dict):
                raise IngestError(f"category {cat_name!r}: question must be an object")
            qid_raw = q.get("id")
            if qid_raw is None:
                raise IngestError(f"category {cat_name!r}: question missing 'id'")
            try:
                qid = int(qid_raw)
            except (TypeError, ValueError) as exc:
                raise IngestError(
                    f"category {cat_name!r}: question 'id' not int: {qid_raw!r}"
                ) from exc
            if qid in seen_ids:
                raise IngestError(f"duplicate question id {qid}")
            seen_ids.add(qid)

            query = q.get("query")
            if not isinstance(query, str) or not query.strip():
                raise IngestError(f"question {qid}: 'query' must be non-empty string")

            gp_raw = q.get("gold_paths") or []
            if not isinstance(gp_raw, list) or not gp_raw:
                raise IngestError(f"question {qid}: 'gold_paths' must be a non-empty list")
            gold_paths = tuple(
                _parse_gold_path(gp, qid=qid, idx=idx) for idx, gp in enumerate(gp_raw)
            )
            # path_tag uniqueness within question
            tags = [gp.path_tag for gp in gold_paths]
            if len(set(tags)) != len(tags):
                raise IngestError(f"question {qid}: gold_paths path_tag must be unique, got {tags}")

            candidates.append(
                Candidate(
                    id=qid,
                    query=query,
                    gold_paths=gold_paths,
                    category=cat_name,
                    source="benchmark_v2",
                )
            )

    return candidates, metadata
