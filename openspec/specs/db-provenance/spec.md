# db-provenance Specification

## Purpose

TBD - created by archiving change 'api-slim-0-5'. Update Purpose after archive.

## Requirements

### Requirement: Bulk ingest records the build environment

On successful completion, `ingest_dir` and `rebuild_from_disk` SHALL upsert into `wenji_meta` the keys `env_onnxruntime_version` and `env_numpy_version`, valued from the running interpreter's `onnxruntime.__version__` and `numpy.__version__`. A run that aborts before completion MUST NOT write these keys. An incremental ingest overwrites prior values (last successful bulk write wins).

#### Scenario: successful rebuild stamps the environment

- **WHEN** `wenji rebuild corpus/ --db test.db` completes under onnxruntime 1.26.x
- **THEN** `wenji_meta` contains `env_onnxruntime_version` starting with `1.26` and a non-empty `env_numpy_version`

#### Scenario: crashed run leaves no stamp

- **WHEN** an `ingest dir` run is killed mid-corpus on a database that never had environment keys
- **THEN** `wenji_meta` still has no `env_onnxruntime_version` key

---
### Requirement: Doctor reports environment drift without failing

`wenji doctor` SHALL append an environment section comparing recorded build-environment keys against the current runtime, with three states: match (reported as ok), mismatch (reported as `DRIFT` naming both versions), and keys absent (reported as `not recorded (pre-0.5 db)`). The environment section MUST NOT affect the exit code; exit semantics remain governed solely by data-consistency checks.

#### Scenario: drift is visible but not fatal

- **WHEN** a database built under onnxruntime 1.26 is checked by a runtime running 1.27 and all consistency checks pass
- **THEN** the report contains a `DRIFT` line naming `1.26` and `1.27`, and the exit code is `0`

#### Scenario: pre-0.5 database is not a false positive

- **WHEN** doctor runs against a 0.4.0-built database lacking environment keys
- **THEN** the environment section reads `not recorded (pre-0.5 db)` and no drift warning is emitted

#### Scenario: matching environment reads clean

- **WHEN** doctor runs in the same environment that built the database
- **THEN** the environment section reports ok with the shared version values

##### Example: parity check on the local eval db

- **GIVEN** a database rebuilt under onnxruntime 1.26.0 / numpy 2.4.4 and a doctor run from the same venv
- **WHEN** `wenji doctor --db /tmp/parity_after.db` executes
- **THEN** the environment section prints `environment: ok (onnxruntime 1.26.0, numpy 2.4.4)` and exit code follows the consistency checks alone
