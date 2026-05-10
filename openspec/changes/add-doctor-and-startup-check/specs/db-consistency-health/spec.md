# Capability: db-consistency-health

## ADDED Requirements

### Requirement: `wenji doctor` CLI reports db consistency health

The system SHALL provide a `wenji doctor` CLI subcommand that opens a wenji SQLite database (path supplied via `--db PATH`), runs three layers of consistency checks (counter vs row count, cross-table sanity, sample MATCH validation), prints a human-readable report to stdout, and exits with code 0 if the database is consistent or 1 if any issue is detected. The CLI MUST be read-only (no writes to the database). The CLI SHALL accept an optional `--sample-keywords` CSV flag to override the default Chinese keyword set for non-Chinese corpora.

#### Scenario: doctor on healthy db exits zero

- **WHEN** `wenji doctor --db <healthy.db>` is invoked against a database with consistent counters, populated FTS indices, and at least one sample keyword hit
- **THEN** the command MUST exit with code 0
- **AND** stdout MUST contain a summary indicating the health status (e.g. `"OK"`)
- **AND** the database MUST NOT be modified

#### Scenario: doctor on inconsistent db exits non-zero

- **WHEN** `wenji doctor --db <bad.db>` is invoked against a database where `wenji_meta.n_chunks` differs from `SELECT COUNT(*) FROM chunks_fts`
- **THEN** the command MUST exit with code 1
- **AND** stdout MUST list the specific inconsistency (counter name, expected value, actual value)
- **AND** the database MUST NOT be modified

#### Scenario: doctor accepts custom sample keywords

- **WHEN** `wenji doctor --db <db> --sample-keywords "term1,term2"` is invoked
- **THEN** the sample MATCH validation MUST use only the supplied keywords (not the default Chinese set)
- **AND** the report MUST reflect MATCH hits per supplied keyword

### Requirement: Retrieval entry points run consistency check at startup

The system SHALL run `check_consistency` against the configured wenji database at startup of every retrieval entry point: `wenji serve` (via FastAPI lifespan handler), each `wenji eval` subcommand (`run`, `run-benchmark`, `sanity-eyeball`, `migrate-jsonl`), and `wenji search` (in the in-process thin-client fallback path). On inconsistency the entry point MUST refuse to operate: `wenji serve` MUST raise `StartupError` from its lifespan handler so the FastAPI app does not bind a port; `wenji eval` and `wenji search` subcommands MUST print issues to stderr and exit with code 1.

#### Scenario: serve refuses to bind on inconsistent db

- **WHEN** `wenji serve` is invoked against a database where `chunks_fts` is empty but `articles_meta` has rows
- **THEN** the FastAPI lifespan handler MUST raise `wenji.core.errors.StartupError`
- **AND** the server MUST NOT bind a TCP port
- **AND** the error message MUST mention the database path and direct the user to run `wenji doctor`

#### Scenario: eval retrieval subcommands gate on consistency

- **WHEN** `wenji eval run-benchmark --db <bad.db> ...` is invoked against an inconsistent database
- **THEN** the subcommand MUST exit with code 1 before running any evaluation
- **AND** stderr MUST contain the specific issues and direct the user to run `wenji doctor`
- **WHEN** `wenji eval run --db <bad.db> ...` is invoked with a `db` argument that points to an inconsistent database
- **THEN** the subcommand MUST exit with code 1 before running

#### Scenario: eval non-retrieval subcommands are NOT gated

- **WHEN** `wenji eval sanity-eyeball <baseline.json> <comparison.json>` is invoked (no `--db` argument; subcommand operates on JSON files only)
- **THEN** the subcommand MUST proceed normally without running the consistency check
- **WHEN** `wenji eval migrate-jsonl <input.jsonl> <output.jsonl>` is invoked (no `--db` argument; pure format conversion)
- **THEN** the subcommand MUST proceed normally without running the consistency check

#### Scenario: ingest / rebuild / read-only commands are NOT gated

- **WHEN** `wenji ingest dir <path> --db <bad.db>` or `wenji rebuild --db <bad.db>` is invoked
- **THEN** the command MUST proceed normally without running the startup consistency check (these commands exist to fix inconsistent state and gating them creates a chicken-and-egg situation)
- **WHEN** `wenji stats --db <bad.db>` or `wenji inspect-chunks ...` is invoked
- **THEN** the command MUST proceed normally (read-only diagnostic; reflecting inconsistency in stats output is the desired behaviour)

### Requirement: Three-layer inconsistency detection

The system SHALL detect inconsistency via three layers (L2 has 4 sub-rules), with any single layer's failure causing the overall report to flag inconsistent. All layers MUST be evaluated even if an earlier layer fails (so the user sees the complete picture).

- **L1 (counter ↔ matching-table row count)**: For each `wenji_meta` counter, the integer value MUST equal `SELECT COUNT(*)` of its matching table — `n_articles` ↔ `articles_meta`, `n_chunks` ↔ `chunks_fts`, `n_doc_vectors` ↔ `doc_vectors`.
- **L2 (cross-table derived-from sanity)** — 4 sub-rules:
  - **L2.a**: If any counter value is greater than zero AND its matching table row count is zero, that is an inconsistency (counter not zeroed after manual truncate).
  - **L2.b**: If any matching table row count is greater than zero AND its counter is zero, that is an inconsistency (counter not updated after ingest).
  - **L2.c**: If `articles_meta` row count is greater than zero AND `chunks_fts` row count is zero, that is an inconsistency. This catches the prod-bug-style «假一致»: ingest wrote articles but not chunks, and the counter happened to be zero too — L1 sees `n_chunks=0` matching `chunks_fts=0` (consistent) but cross-table reveals chunks should have been derived from articles.
  - **L2.d**: If `articles_meta` row count is greater than zero AND `doc_vectors` row count is zero, that is an inconsistency (embedding step missing).
- **L3 (sample MATCH validation)**: At least one keyword from the supplied sample-keywords set MUST yield ≥ 1 hit when used as a `MATCH` query against `articles_fts` AND ≥ 1 hit against `chunks_fts`. If all keywords miss both indices, the issue MUST include the hint `"all sample keywords missed both FTS indices; if your corpus is non-Chinese, override with --sample-keywords"`.

#### Scenario: L2 catches the prod-bug-style 0/0 假一致

- **WHEN** `check_consistency` is called against a database where `articles_meta` has 12090 rows, `chunks_fts` has 0 rows, and `wenji_meta.n_chunks = '0'`
- **THEN** L1 MUST pass (counter 0 == row count 0 for chunks)
- **AND** L2 MUST fail (articles_meta has rows but chunks_fts does not, indicating incomplete ingest)
- **AND** the report MUST flag the database as inconsistent with a specific issue mentioning `chunks_fts` empty alongside populated `articles_meta`

#### Scenario: L3 hint mentions non-Chinese corpus override

- **WHEN** `check_consistency` is called against a database where row counts and counters all match, but no sample keyword (default Chinese set) returns any MATCH hit on either FTS index
- **THEN** the report MUST flag the database as inconsistent
- **AND** the issue MUST include the substring `"--sample-keywords"` to direct non-Chinese-corpus users to the override flag
