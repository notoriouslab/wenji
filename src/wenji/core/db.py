"""SQLite connection helpers for wenji.

Single-file schema in :mod:`wenji.core.schema_sql`. ``schema_version`` lives in
``wenji_meta`` and is verified on initialisation; mismatch raises
:class:`SchemaError` (no silent migration).

libsimple extension is *optional* in v0.1.0 (FTS uses jieba pre-tokenize +
unicode61). v0.2+ may add a char-unigram fallback path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wenji.core.errors import SchemaError, WenjiError

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = "2"


def connect(
    db_path: str | Path,
    *,
    libsimple_path: str | Path | None = None,
) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults.

    Args:
        db_path: Path to SQLite file. ``":memory:"`` for ephemeral DB.
        libsimple_path: Optional path to libsimple shared library. If supplied,
            the extension is loaded; failure raises :class:`WenjiError`.

    Returns:
        Open ``sqlite3.Connection``.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")

    if libsimple_path is not None:
        try:
            conn.enable_load_extension(True)
            conn.load_extension(str(libsimple_path))
            conn.enable_load_extension(False)
        except (sqlite3.OperationalError, AttributeError) as exc:
            raise WenjiError(f"failed to load libsimple at {libsimple_path}: {exc}") from exc

    return conn


def initialise_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql to the connection. Idempotent (CREATE IF NOT EXISTS).

    Verifies ``wenji_meta.schema_version`` matches code ``SCHEMA_VERSION`` after
    application; mismatch raises :class:`SchemaError`.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()

    row = conn.execute("SELECT value FROM wenji_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        raise SchemaError("schema_version missing from wenji_meta after initialise")
    if row[0] != SCHEMA_VERSION:
        raise SchemaError(
            f"DB schema_version={row[0]!r} mismatches code SCHEMA_VERSION={SCHEMA_VERSION!r};"
            " migration required"
        )
