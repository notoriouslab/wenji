"""Markdown frontmatter parsing + source_type derivation.

Uses python-frontmatter to split YAML frontmatter from body. Derives
``source_type`` from frontmatter (preferred) or directory mapping (fallback).
Raises :class:`IngestError` for unmapped directories.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter

from wenji.core.errors import IngestError


@dataclass(frozen=True)
class ParsedArticle:
    """Result of parsing a markdown file."""

    metadata: dict[str, Any]
    body: str
    source_type: str
    path: Path


def parse_markdown(path: str | Path) -> tuple[dict[str, Any], str]:
    """Read ``.md`` file and return (frontmatter dict, body string).

    Empty frontmatter → empty dict. Body is the raw text *after* frontmatter
    (not yet normalised — call :func:`wenji.core.normalize.normalize` separately).
    """
    p = Path(path)
    if not p.exists():
        raise IngestError(f"markdown file not found: {p}")
    try:
        post = frontmatter.load(str(p))
    except Exception as exc:  # python-frontmatter raises generic YAMLError etc.
        raise IngestError(f"failed to parse frontmatter for {p}: {exc}") from exc
    return dict(post.metadata), post.content


def derive_source_type(
    metadata: Mapping[str, Any],
    path: str | Path,
    directory_map: Mapping[str, str] | None = None,
) -> str:
    """Determine ``source_type`` for an article.

    Resolution order:

    1. ``metadata['source_type']`` if present and non-empty
    2. ``directory_map[parent_dir_name]`` if mapping provided and key matches
    3. raise :class:`IngestError`

    ``directory_map`` is typically loaded from ``chunk_policy.yaml`` at the
    config layer (Group 9); ``ingest_one`` accepts the resolved mapping as a
    pure dict.
    """
    if "source_type" in metadata and metadata["source_type"]:
        return str(metadata["source_type"])
    if directory_map is None:
        raise IngestError(f"no source_type in frontmatter for {path} and no directory_map provided")
    parent = Path(path).parent.name
    if parent in directory_map:
        return directory_map[parent]
    raise IngestError(
        f"unmapped directory {parent!r} for {path}; add to chunk_policy directory map"
    )


def load_article(
    path: str | Path,
    directory_map: Mapping[str, str] | None = None,
) -> ParsedArticle:
    """One-shot: parse markdown and derive source_type."""
    p = Path(path)
    metadata, body = parse_markdown(p)
    source_type = derive_source_type(metadata, p, directory_map)
    return ParsedArticle(metadata=metadata, body=body, source_type=source_type, path=p)
