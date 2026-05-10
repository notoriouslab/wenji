# Capability: db-consistency-health

## ADDED Requirements

### Requirement: `wenji doctor` CLI reports db consistency health

The system SHALL provide a `wenji doctor` CLI subcommand that opens a wenji SQLite database (path supplied via `--db PATH`), runs two layers of consistency checks (cross-table sanity, sample MATCH validation), prints a human-readable report to stdout, and exits with code 0 if the database is consistent or 1 if any issue is detected. The CLI MUST be read-only (no writes to the database). The CLI SHALL accept an optional `--sample-keywords` CSV flag to override the default Chinese keyword set for non-Chinese corpora.

#### Scenario: doctor on healthy db exits zero

- **WHEN** `wenji doctor --db <healthy.db>` is invoked against a database with populated FTS indices and at least one sample keyword hit on each FTS index
- **THEN** the command MUST exit with code 0
- **AND** stdout MUST contain a summary indicating the health status (e.g. `"OK"`)
- **AND** the database MUST NOT be modified

#### Scenario: doctor on inconsistent db exits non-zero

- **WHEN** `wenji doctor --db <bad.db>` is invoked against a database where `articles_meta` has rows but `chunks_fts` is empty
- **THEN** the command MUST exit with code 1
- **AND** stdout MUST list the specific cross-table inconsistency (with row counts)
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

#### Scenario: WENJI_DISABLE_STARTUP_CHECK env bypasses gate (test escape hatch)

- **WHEN** `WENJI_DISABLE_STARTUP_CHECK=1` is set in the process environment
- **THEN** both the FastAPI `lifespan` handler and the `_ensure_consistency` CLI helper MUST skip `check_consistency` entirely and proceed as if the database were healthy
- **AND** this env MUST NOT be set in production deployments (it is an escape hatch for test fixtures that build partial databases for endpoint behaviour testing; production deploy SOP MUST NOT include it)
- **AND** `tests/wenji/conftest.py` MUST set this env via an `autouse=True` fixture so the default test path skips the gate; tests that explicitly verify the gate's behaviour MUST `monkeypatch.delenv("WENJI_DISABLE_STARTUP_CHECK", raising=False)` to re-enable it

#### Scenario: ingest / rebuild / read-only commands are NOT gated

- **WHEN** `wenji ingest dir <path> --db <bad.db>` or `wenji rebuild --db <bad.db>` is invoked
- **THEN** the command MUST proceed normally without running the startup consistency check (these commands exist to fix inconsistent state and gating them creates a chicken-and-egg situation)
- **WHEN** `wenji stats --db <bad.db>` or `wenji inspect-chunks ...` is invoked
- **THEN** the command MUST proceed normally (read-only diagnostic; reflecting inconsistency in stats output is the desired behaviour)

### Requirement: Two-layer inconsistency detection

The system SHALL detect inconsistency via two layers (L2 has 3 sub-rules), with any single layer's failure causing the overall report to flag inconsistent. All layers MUST be evaluated even if an earlier layer fails (so the user sees the complete picture).

- **L2 (cross-table derived-from sanity)** — 3 sub-rules:
  - **L2.c**: If `articles_meta` row count is greater than zero AND `chunks_fts` row count is zero, that is an inconsistency. This catches the prod-bug-style «假一致»: ingest wrote articles but not chunks. Cross-table reveals chunks should have been derived from articles but are missing.
  - **L2.d**: If `articles_meta` row count is greater than zero AND `doc_vectors` row count is zero, that is an inconsistency (embedding step missing).
  - **L2.e**: If `chunks_fts` row count is greater than zero AND `articles_meta` row count is zero, that is an inconsistency (reverse broken state — chunks should be derived from articles, but the source has been wiped while derived rows linger).
- **L3 (sample MATCH validation)**: At least one keyword from the supplied sample-keywords set MUST yield ≥ 1 hit when used as a `MATCH` query against `articles_fts` AND ≥ 1 hit against `chunks_fts`. L3 is gated on **both** FTS indices being non-empty: a freshly-initialised database with zero rows MUST NOT fail L3 just because there is no content to MATCH against (the healthy `wenji ingest && wenji serve` workflow MUST not be blocked at startup before any data exists). When L3 does fire (both indices populated, no keyword hits), the issue MUST include the hint `"all sample keywords missed both FTS indices; if your corpus is non-Chinese, override with --sample-keywords"`.

> **Note on `wenji_meta` build counters (L1 layer removed during apply)**:
> The original propose specced an L1 layer comparing `wenji_meta.n_articles` / `n_chunks` / `n_doc_vectors` against matching table row counts (plus L2.a / L2.b sub-rules dependent on the same counters). Apply-phase discovery (see `proposal.md` G1 drift correction): no ingest path has ever maintained these counters since v0.1.0 — they are dead schema columns initialised to `'0'` and never updated. An L1 layer reading them would either always fail (real production databases) or always pass with zero detection power (every reader is in-process and the counters are static). L1 / L2.a / L2.b were removed; L2.c / L2.d / L3 (which rely purely on row counts and FTS MATCH, not on `wenji_meta`) are sufficient to catch the prod bug範式 originally motivating this change. The dead columns are flagged DEPRECATED in `src/wenji/core/schema.sql`. A followup change `cleanup-build-telemetry` will decide whether to drop the columns (schema bump) or wire up maintenance (and re-introduce a meaningful L1).

#### Scenario: L2 catches the prod-bug-style chunks-empty 假一致

- **WHEN** `check_consistency` is called against a database where `articles_meta` has 12090 rows and `chunks_fts` has 0 rows
- **THEN** L2 MUST fail (articles_meta has rows but chunks_fts does not, indicating incomplete ingest)
- **AND** the report MUST flag the database as inconsistent with a specific issue mentioning `chunks_fts` empty alongside populated `articles_meta`

#### Scenario: L3 hint mentions non-Chinese corpus override

- **WHEN** `check_consistency` is called against a database where row counts pass L2 but no sample keyword (default Chinese set) returns any MATCH hit on either FTS index
- **THEN** the report MUST flag the database as inconsistent
- **AND** the issue MUST include the substring `"--sample-keywords"` to direct non-Chinese-corpus users to the override flag

#### Scenario: empty database passes (operator workflow)

- **WHEN** `check_consistency` is called against a freshly-initialised database (schema applied but no `wenji ingest` yet — all four tables `articles_meta` / `articles_fts` / `chunks_fts` / `doc_vectors` are empty)
- **THEN** L2.c / L2.d / L2.e MUST NOT fire (each requires its respective non-empty side)
- **AND** L3 MUST NOT fire (gated on both FTS indices being non-empty)
- **AND** the report MUST be `ok`, `issues == []`

### Requirement: `WENJI_DISABLE_STARTUP_CHECK` uses truthy-value whitelist

The system SHALL recognise `WENJI_DISABLE_STARTUP_CHECK` as disabling the gate **only** when set to one of the canonical truthy values: `1`, `true`, `yes`, `on` (case-insensitive, after `strip()`). Any other value (including the footgun cases `"0"`, `"false"`, `"no"`, `"off"`, blank, or arbitrary strings) MUST leave the gate **enabled**. When the env IS recognised as disabling, exactly one `WARNING`-level log entry MUST be emitted via the `wenji.observability.health` logger so incident response has an audit trail.

#### Scenario: footgun env values do not silently bypass the gate

- **WHEN** `WENJI_DISABLE_STARTUP_CHECK=0` (or `false`, `no`, blank, `anything-else`) is set in the environment
- **THEN** the gate MUST run normally as if the env were unset
- **AND** no warning log MUST be emitted (the env value is not recognised)

#### Scenario: recognised disable value emits audit warning

- **WHEN** `WENJI_DISABLE_STARTUP_CHECK=1` (or `true`, `yes`, `on`) is set
- **THEN** the gate MUST skip `check_consistency`
- **AND** exactly one `WARNING`-level log MUST be emitted on `wenji.observability.health` mentioning `"DISABLED"` and `"Production deploys MUST NOT"`

### Requirement: `wenji serve` lifespan gate resolves db_path from arg/env/default

The FastAPI `lifespan` handler SHALL resolve the database path from (1) the `db_path` argument to `create_app(...)` if supplied, otherwise (2) the `WENJI_DB_PATH` environment variable, otherwise (3) the default `data/wenji.db`. The resolved path MUST be the same path used by request-time state (so the gate cannot check a different db than the one queries hit). The gate MUST run unconditionally on this resolved path unless `WENJI_DISABLE_STARTUP_CHECK` is recognised as disabling.

> **Why this requirement was added during apply**: an earlier commit had `if db_path is not None and not env_disabled` as the gate condition. The module-level `app = create_app()` (the instance uvicorn loads) passes no argument → `db_path = None` → the gate never fired in production while still passing all tests (which call `create_app(db_path=...)` explicitly). Discovered by sub-agent code review post-Phase-4. See `proposal.md` G1 drift correction #3.

#### Scenario: production module-level app loads db_path from env

- **WHEN** `app = create_app()` is invoked with no `db_path` argument and `WENJI_DB_PATH` points to an inconsistent database
- **THEN** the lifespan handler MUST raise `StartupError` referencing the resolved path
- **AND** the server MUST NOT bind a TCP port
