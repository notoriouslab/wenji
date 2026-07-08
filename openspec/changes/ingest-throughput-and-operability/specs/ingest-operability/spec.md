# Capability: ingest-operability

## ADDED Requirements

### Requirement: Batched embedding preserves vector equivalence

`ingest_dir` SHALL embed articles in character-budget batches (oversized articles run alone; a failed batch retries its members individually). Two distinct guarantees apply:

1. **Run-to-run byte-identity (unconditional)**: batch composition MUST be a pure function of the sorted file iteration order (the existing `sorted(root.glob(...))` at `ingest/__init__.py:394`), so two rebuilds of the same corpus remain byte-identical — the `rebuild_from_disk` docstring promise is preserved regardless of item 2's outcome.
2. **Batch-vs-single equivalence (quality gate)**: batched vectors are expected element-wise equal to singly-computed vectors; if onnxruntime batching introduces float variance, the gate falls back to cosine similarity > 0.99999 AND the CHANGELOG MUST note that vectors differ from v0.4.0 single-call vectors (a one-time documented shift, not a broken promise — run-to-run identity still holds).

#### Scenario: batch and single vectors agree

- **WHEN** the same 10-article sample (including the longest article in the corpus) is embedded via the batched path and via one-at-a-time calls
- **THEN** the resulting vectors MUST be element-wise equal (or meet the documented cosine fallback)

#### Scenario: oversized article does not blow the batch

- **WHEN** an article's text alone exceeds the batch character budget
- **THEN** it MUST be embedded in its own single-item call and ingest MUST proceed

##### Example: 40k-char commentary chapter

- **GIVEN** budget 32,000 chars and a queue of [3k, 5k, 40k, 2k] char articles
- **WHEN** the packer runs
- **THEN** batches are [3k, 5k] + [40k alone] + [2k...] — the 40k article never shares a batch

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
