# Capability: ingest-operability

## ADDED Requirements

### Requirement: Fresh inserts skip derived-table deletes

`ingest_one` MUST NOT execute `DELETE FROM articles_fts` / `DELETE FROM chunks_fts` when no prior row exists for the article's path; the deletes run only on the content-changed path.

#### Scenario: full rebuild pays no delete scans

- **WHEN** `wenji rebuild` ingests a corpus into freshly-wiped tables
- **THEN** no per-article FTS DELETE statements execute during the run

### Requirement: Long-running ingest reports progress

`ingest_dir` SHALL emit a progress log line at least every 200 articles containing processed count, total, percentage, rate, and ETA.

#### Scenario: operator can estimate completion from logs

- **WHEN** a 12,000-article ingest is running under nohup
- **THEN** the log file MUST contain periodic lines of the form `ingest: <n>/<total> (<pct>%) rate=<x>/s eta=<y>min`

### Requirement: Bad-file resilience is explicit opt-in

With `--skip-bad`, a file whose frontmatter fails to parse MUST be recorded and skipped; at completion the command MUST list every skipped file with its error and exit non-zero. Without the flag, behavior remains fail-fast (first bad file aborts).

#### Scenario: corpus with two bad files completes under --skip-bad

- **WHEN** `wenji ingest dir corpus/ --skip-bad` runs over a corpus containing 2 unparseable files
- **THEN** all other articles MUST be ingested, the 2 files MUST be listed with their errors, and the exit code MUST be 1
- **AND** the machine-readable summary goes to stdout as one compact JSON line (`{"ingested": N, "skipped_bad": [{"path": ..., "error": ...}]}`), while the human-readable per-file list goes to stderr via `logger.error` — matching the CLI's existing stdout-JSON / stderr-log convention

#### Scenario: default remains fail-fast

- **WHEN** the same corpus is ingested without `--skip-bad`
- **THEN** the command MUST abort on the first unparseable file with a non-zero exit

### Requirement: Interrupted ingest resumes via content-hash fast path

Documentation (rebuild CLI help and README operations section) MUST state that an interrupted bulk ingest is resumed by re-running `wenji ingest dir` with the same arguments — unchanged articles take the content-hash fast path without re-embedding.

#### Scenario: crash-resume skips completed articles

- **WHEN** an ingest of 100 articles is killed after 60 and `wenji ingest dir` re-runs with identical arguments
- **THEN** the 60 completed articles MUST NOT be re-embedded and the run MUST complete the remaining 40

### Requirement: Query-time vector matrix is cached with ingest-aware invalidation

The vector search candidate matrix SHALL be memoized for a live Searcher, keyed by the search's `axis` parameter (the no-axis query is its own key). All cache entries share one corpus-level fingerprint (`COUNT(*)` + `MAX(indexed_at)` of `articles_meta`); any fingerprint change invalidates every entry — over-invalidation across axes is accepted (a corpus change is rare relative to queries, and per-axis fingerprints would require per-axis bookkeeping for negligible gain).

#### Scenario: repeated queries reuse the matrix

- **WHEN** two consecutive searches run with no intervening ingest
- **THEN** the candidate matrix MUST be constructed at most once

##### Example: back-to-back queries

- **GIVEN** a 12,090-article db and a live Searcher
- **WHEN** `search("禱告")` then `search("信心")` execute
- **THEN** `doc_vectors` rows are loaded and stacked exactly once; the second query reuses the (12090, 1024) matrix

#### Scenario: external ingest invalidates the cache

- **WHEN** an article is ingested by another process after the matrix was cached
- **THEN** the next search MUST detect the fingerprint change and rebuild the matrix

##### Example: serve running during ingest

- **GIVEN** a cached matrix built when `COUNT=12090, MAX(indexed_at)=T1`
- **WHEN** `wenji ingest dir new/` adds one article (fingerprint becomes `12091, T2`) and a search runs
- **THEN** the search rebuilds the matrix and the new article is retrievable by vector
