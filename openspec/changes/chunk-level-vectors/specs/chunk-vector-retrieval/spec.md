# Capability: chunk-vector-retrieval

## ADDED Requirements

### Requirement: Chunk vectors are produced alongside chunk FTS rows

`ingest_one` SHALL encode each chunk individually (one text per `encode_batch` call — batching is forbidden per the measured INT8 drift) and store one `chunk_vectors` row per `chunks_fts` row within the same per-article transaction. The encoded text MUST be the raw chunk string (the `chunk_text_raw` column content), never the jieba-tokenized `chunk_text` form. A content-unchanged re-ingest MUST NOT re-encode chunk vectors (the unchanged fast path returns before any FTS or vector write, leaving existing rows untouched). A content-changed re-ingest MUST delete the article's old vector rows and encode the new chunk set in full.

#### Scenario: fresh article gets full vector coverage

- **WHEN** a new article producing 6 chunks is ingested
- **THEN** `chunk_vectors` contains exactly 6 rows for that article_id, each `vec` being 1024 float32 values

#### Scenario: unchanged fast path skips chunk encoding

- **WHEN** the same file is re-ingested with identical content
- **THEN** no chunk encode calls occur and the existing `chunk_vectors` rows remain (asserted by call counting on the embedder)

#### Scenario: changed content replaces the vector set without orphans

- **WHEN** an article's body changes (content hash → new article_id) such that it now produces 4 chunks instead of 6
- **THEN** the table's total row count for this file's lineage is exactly 4 — the old article_id's 6 rows are deleted in the old-id cleanup block (content change mints a new article_id; asserting only the new id would miss orphans)

### Requirement: Existing databases backfill without a rebuild

A `wenji ingest backfill-chunk-vectors --db <path>` subcommand SHALL scan for `chunks_fts` rows lacking a `chunk_vectors` row and encode only those — reading `chunk_text_raw` so a backfilled vector is bytewise identical to what a fresh ingest of the same chunk produces — committing per article so an interrupted run resumes by re-running (already-covered chunks are never re-encoded). Progress logging SHALL follow the ingest format (count/total, rate, ETA). On successful completion the command SHALL stamp the build environment keys (same mechanism as bulk ingest).

#### Scenario: backfill covers a vectorless database

- **WHEN** the subcommand runs against a v4 database with 100 chunks and zero vectors
- **THEN** coverage reaches 100/100 and `wenji_meta` gains the environment keys

#### Scenario: backfilled vectors are bytewise identical to ingest-produced vectors

- **WHEN** the same chunk is vectorized once via fresh ingest and once via backfill (vectors wiped in between)
- **THEN** the two stored `vec` BLOBs are byte-for-byte equal

#### Scenario: interrupted backfill resumes without repeat work

- **WHEN** a backfill is killed after 60 of 100 chunks and the command re-runs
- **THEN** only the remaining 40 chunks are encoded (call-count assertion) and the run completes

### Requirement: Schema v4 with chained in-place migration

`SCHEMA_VERSION` SHALL be `"4"`; `schema.sql` SHALL define `chunk_vectors (chunk_id TEXT PRIMARY KEY, article_id TEXT NOT NULL, vec BLOB NOT NULL)` with an index on `article_id`. `initialise_schema` on a v3 database MUST create the table and stamp `4` with all other data preserved; a v2 database MUST migrate through both steps (drop `query_rewrite_cache`, then create `chunk_vectors`) in one call. Versions other than 2/3/4 MUST raise `SchemaError`. Read-only entry points MUST NOT migrate and MUST operate normally on v3 databases.

#### Scenario: v3 database upgrades on next write entry

- **WHEN** any ingest command runs against a 0.5.0-built database (v3, corpus populated)
- **THEN** afterwards `chunk_vectors` exists (empty), `schema_version` is `4`, and all row counts are otherwise unchanged

#### Scenario: v2 database chains to v4 in one pass

- **WHEN** `initialise_schema` runs on a v2 database containing `query_rewrite_cache`
- **THEN** the cache table is gone, `chunk_vectors` exists, and `schema_version` is `4`

### Requirement: Chunk-vector channel joins RRF as an independent third ranking

`Searcher.search` SHALL rank the query vector against the chunk-vector matrix, take the top `candidate_pool` chunks, roll up to articles by max cosine, and feed the resulting article ranking into `rrf_merge` as a third independent channel contributing `1/(k+rank)` alongside the existing main and chunk-BM25 channels. Axis filtering SHALL apply after roll-up (article-level join), not by slicing the matrix; `category='excluded'` articles are filtered at the same point. The `rrf_merge` signature extension MUST default to the two-channel behavior when the third ranking is absent.

#### Scenario: rewrite-style query recovers gold via the chunk-vector channel

- **WHEN** a query has zero BM25 hits (article and chunk level) but one article's chunk scores top cosine against the query
- **THEN** the 3-way RRF result includes that article (unit fixture: synthetic corpus where only the chunk-vector channel carries the signal)

##### Example: the diagnosis scenario in miniature

- **GIVEN** a corpus where article G's chunk 3 is semantically close to query Q while G's doc vector and all BM25 signals rank G outside the pool
- **WHEN** `search(Q)` runs with chunk vectors present
- **THEN** G appears in the merged results with an RRF contribution from the chunk-vector channel

#### Scenario: axis filter applies after roll-up

- **WHEN** a search with `axis=X` runs and the chunk-vector top-K contains articles outside axis X
- **THEN** only axis-X articles from that channel enter the merge

### Requirement: Missing vectors degrade gracefully to two-way behavior

When `chunk_vectors` is empty, **the table does not exist** (a v3 database served read-only — the production upgrade path), or the embedder is unavailable, the chunk-vector channel SHALL contribute nothing and retrieval MUST be identical to the 0.5.0 two-way pipeline. The missing-table case MUST be handled as equivalent to an empty table (catch the `sqlite3.OperationalError` at the channel loader and return no candidates — never propagate). Partial coverage (interrupted backfill) SHALL operate normally with the covered subset — no warning, no failure.

#### Scenario: v3 database without the table serves read-only without error

- **WHEN** a 0.6.0 `wenji serve` opens a 0.5.0-built database (v3 — `chunk_vectors` does not exist; read-only entry points never migrate) and a search runs
- **THEN** results return via the two-way pipeline with no error (the missing table is treated as zero coverage)

#### Scenario: un-backfilled database behaves exactly like 0.5.0

- **WHEN** the 80q+r14 benchmark runs against a v4 database with zero chunk vectors
- **THEN** per-question results are identical to the 0.5.0 baseline (75/80 with the same miss list)

#### Scenario: partial coverage serves without complaint

- **WHEN** a search runs against a database with 40% chunk-vector coverage
- **THEN** results return normally using the covered chunks; no error or warning is emitted

### Requirement: Chunk matrix is cached with backfill-aware invalidation

The chunk-vector matrix SHALL be memoized once per database (no per-axis copies) following the doc-matrix cache pattern. The corpus fingerprint SHALL extend to the triple (`COUNT(*)` of articles_meta, `MAX(indexed_at)`, `COUNT(*)` of chunk_vectors) so that a backfill — which writes only `chunk_vectors` — invalidates the cache. The chunk_vectors count in the fingerprint MUST tolerate a missing table (count as 0 — a v3 database read-only path hits this fingerprint on the **doc** channel before any chunk loader runs; an unguarded query there reintroduces the crash the degradation contract forbids).

#### Scenario: repeated queries build the matrix once

- **WHEN** two consecutive searches run with no intervening writes
- **THEN** the chunk matrix is constructed at most once

##### Example: back-to-back queries on the parity corpus

- **GIVEN** a 123,929-chunk database and a live Searcher
- **WHEN** `search("禱告")` then `search("信心")` execute
- **THEN** `chunk_vectors` rows are loaded and stacked exactly once; the second query reuses the (123929, 1024) matrix

#### Scenario: backfill invalidates the cached matrix

- **WHEN** a live Searcher has served queries, then a backfill adds vectors, then a new query arrives
- **THEN** the matrix is rebuilt and the new vectors participate in ranking

##### Example: the fingerprint triple catches a vectors-only write

- **GIVEN** a Searcher whose cached fingerprint is (12100 articles, max indexed_at T, 0 chunk vectors)
- **WHEN** a backfill writes 123,929 `chunk_vectors` rows (articles_meta untouched) and a query arrives
- **THEN** the fingerprint reads (12100, T, 123929) ≠ cached → both matrices reload and the chunk channel returns candidates

### Requirement: Doctor reports chunk-vector coverage

`wenji doctor` SHALL report `chunk_vectors` coverage as `<n_vectors>/<n_chunks> (<pct>%)`. On a v3 database where the table does not exist the line SHALL render as zero coverage (no error — the migration window between pip upgrade and first write entry is exactly when doctor is used for reconciliation). The line is informational: coverage below 100% (including zero or absent) MUST NOT affect the exit code.

#### Scenario: coverage three states render correctly

- **WHEN** doctor runs against databases with full, partial (40%), and zero coverage
- **THEN** each report shows the correct ratio and the exit code is governed solely by the existing consistency checks

#### Scenario: doctor survives the migration window

- **WHEN** a 0.6.0 `wenji doctor` runs against a v3 database (`chunk_vectors` absent)
- **THEN** the coverage line renders as zero/absent, no error is raised, and exit code follows consistency checks alone
