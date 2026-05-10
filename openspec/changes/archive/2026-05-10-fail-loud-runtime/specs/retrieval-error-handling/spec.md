# Capability: retrieval-error-handling

## ADDED Requirements

### Requirement: Chunk-level FTS5 OperationalError MUST propagate

The system SHALL raise `wenji.core.errors.SearchError` (chained via `from exc`) when `wenji.search.rrf.chunk_bm25_search` encounters a `sqlite3.OperationalError` while executing the `chunks_fts MATCH` query. The function MUST NOT silently return an empty dict on `OperationalError`. The original `OperationalError` MUST be preserved as the chained `__cause__` so callers can inspect SQLite-level details.

#### Scenario: chunks_fts query raises OperationalError

- **WHEN** `chunk_bm25_search(conn, "query")` is called and `conn.execute(...)` raises `sqlite3.OperationalError("database is locked")`
- **THEN** the function MUST raise `wenji.core.errors.SearchError`
- **AND** the raised `SearchError`'s message MUST contain `"chunks_fts query failed"` followed by the underlying error message
- **AND** `excinfo.value.__cause__` MUST be the original `sqlite3.OperationalError` instance

#### Scenario: Empty query string returns empty dict (existing behaviour preserved)

- **WHEN** `chunk_bm25_search(conn, "")` or `chunk_bm25_search(conn, "   ")` is called
- **THEN** the function MUST return `{}` (early return at the top of the function, before any SQLite execution)
- **AND** no SearchError MUST be raised

#### Scenario: Successful chunks_fts query returns score dict (existing behaviour preserved)

- **WHEN** `chunk_bm25_search(conn, "query")` is called against a populated `chunks_fts` table that yields rows
- **THEN** the function MUST return a `dict[str, float]` mapping article_id to best (most negative) bm25 score
- **AND** no warning MUST be emitted

### Requirement: Both BM25 retrieval paths emit warning logs on OperationalError

The system SHALL emit a `WARNING`-level log record via the standard library `logging` module when either `wenji.search.bm25.bm25_search` or `wenji.search.rrf.chunk_bm25_search` catches a `sqlite3.OperationalError`. The log message MUST include the table name (`articles_fts` or `chunks_fts`), the raw exception message, and the full stack trace (via `exc_info=True`). The warning MUST be emitted **before** the `SearchError` is raised so the trace survives in the logging pipeline regardless of the caller's exception handling.

#### Scenario: chunk-level BM25 OperationalError logs warning before raising

- **WHEN** `chunk_bm25_search` catches an `OperationalError` from a SQLite `MATCH` query
- **THEN** a single log record at level `WARNING` MUST be emitted on logger `wenji.search.rrf`
- **AND** the record's message MUST contain `"chunks_fts query failed"` and the raw exception message
- **AND** `LogRecord.exc_info` MUST be populated (stack trace available)
- **AND** the warning MUST be emitted before the `SearchError` propagates to the caller

#### Scenario: article-level BM25 OperationalError logs warning before raising

- **WHEN** `bm25_search` catches an `OperationalError` from the `articles_fts` query
- **THEN** a single log record at level `WARNING` MUST be emitted on logger `wenji.search.bm25`
- **AND** the record's message MUST contain `"articles_fts query failed"` and the raw exception message
- **AND** the existing `SearchError("FTS5 query failed: ...")` raise behaviour MUST be preserved unchanged
