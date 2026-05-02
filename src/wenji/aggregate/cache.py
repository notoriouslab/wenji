"""Aggregate result cache: sha256 key + application-level TTL (default 30 days).

All Aggregator methods share a single ``aggregate_cache`` table; the cache key
is ``sha256(function_name + ":" + canonical_args_json)``. ``llm_client`` is
configuration rather than query semantics and is never part of the key.

Per spec, expired entries are treated as cache misses on read but are NOT
deleted on read; deletion happens only via :func:`cache_clear` or on next
write to the same key (UPSERT).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone


def cache_key(function_name: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{function_name}:{canonical}".encode()).hexdigest()
    return digest


def cache_get(
    conn: sqlite3.Connection,
    key: str,
    ttl_days: int = 30,
) -> dict | None:
    row = conn.execute(
        "SELECT value, created_at FROM aggregate_cache WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    value_json, created_at_iso = row
    try:
        created_at = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created_at > timedelta(days=ttl_days):
        return None
    return json.loads(value_json)


def cache_put(conn: sqlite3.Connection, key: str, value: dict) -> None:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    value_json = json.dumps(value, ensure_ascii=False)
    conn.execute(
        "INSERT INTO aggregate_cache (key, value, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "value = excluded.value, created_at = excluded.created_at",
        (key, value_json, now_iso),
    )
    conn.commit()


def cache_clear(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("DELETE FROM aggregate_cache")
    conn.commit()
    return cursor.rowcount
