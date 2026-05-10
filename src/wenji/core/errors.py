"""wenji exception hierarchy."""

from __future__ import annotations


class WenjiError(Exception):
    """Base for all wenji errors."""


class IngestError(WenjiError):
    """Ingest pipeline failure (frontmatter parse, encoding, missing fields)."""


class SchemaError(WenjiError):
    """DB schema mismatch or migration required."""


class ConfigError(WenjiError):
    """Invalid YAML config or missing required keys."""


class ClassifyError(WenjiError):
    """Axis classification failure (rule conflict, validation gate fail)."""


class SearchError(WenjiError):
    """Search engine failure (FTS query parse error, missing index)."""


class StartupError(WenjiError):
    """Retrieval entry point startup gate failure (db consistency check failed)."""
