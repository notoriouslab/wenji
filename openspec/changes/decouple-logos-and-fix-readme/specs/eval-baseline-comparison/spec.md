# Capability: eval-baseline-comparison

## ADDED Requirements

### Requirement: sanity-eyeball CLI accepts a generic baseline-output flag

The `wenji eval sanity-eyeball` CLI SHALL accept a baseline run output JSON via `--baseline-output <path>`. The flag MUST replace the legacy `--logos-r13` option entirely; the legacy name MUST NOT be accepted.

#### Scenario: New flag accepted
- **WHEN** `wenji eval sanity-eyeball --wenji-run wenji.json --baseline-output ref.json --n 8 --seed 42`
- **THEN** the command MUST execute and load both JSON files
- **AND** the output MUST present side-by-side top-5 comparison

#### Scenario: Legacy flag rejected
- **WHEN** `wenji eval sanity-eyeball --logos-r13 ref.json` is invoked
- **THEN** the command MUST exit with non-zero status
- **AND** the error MUST mention the new `--baseline-output` flag

### Requirement: Comparison output uses neutral baseline_top5 field name

The sanity-eyeball comparison output SHALL use `baseline_top5` (not `logos_top5`) as the field name for the reference run's top-5 results. All log messages and report sections SHALL refer to "baseline" rather than any product name.

#### Scenario: Output JSON field naming
- **WHEN** sanity-eyeball produces a comparison sample
- **THEN** each sample object MUST contain `wenji_top5` and `baseline_top5` arrays
- **AND** the object MUST NOT contain `logos_top5`

#### Scenario: Console output wording
- **WHEN** sanity-eyeball prints sample comparisons to stdout
- **THEN** section labels MUST read "baseline top-5:" rather than "logos top-5:"
- **AND** no console line MUST contain the word "logos"

### Requirement: Benchmark snapshot metadata uses source_commit key without backward compat

The frozen benchmark snapshot at `tests/benchmark_80_v2_snapshot.json` SHALL store the upstream provenance commit hash under the key `source_commit`. The eval loader and report generator SHALL read `source_commit` only; legacy `logos_source_commit` MUST NOT be accepted as a fallback. All previously-frozen `wenji_r0_*.json` outputs SHALL be in-place migrated as part of this change (not at load time). The Python module `src/wenji/eval/loader_logos_v2.py` SHALL be renamed to `src/wenji/eval/loader_benchmark_v2.py`, and the dataclass field `SnapshotMetadata.logos_source_commit` SHALL be renamed to `SnapshotMetadata.source_commit`. The `snapshot_source_path` description string SHALL be migrated to a generic phrase such as `upstream benchmark v2 80q` (no `logos` substring).

#### Scenario: Loader reads new key
- **WHEN** the snapshot loader parses a JSON file containing `source_commit: "413642af"`
- **THEN** the parsed metadata MUST expose `source_commit == "413642af"`

#### Scenario: Loader rejects legacy-only key
- **WHEN** the snapshot loader parses a JSON file containing only `logos_source_commit: "413642af"` and not `source_commit`
- **THEN** the loader MUST raise an error with a message naming the missing required key `source_commit`
- **AND** the loader MUST NOT silently accept the legacy key

#### Scenario: Report generator emits new key
- **WHEN** `wenji eval run-benchmark` writes its summary JSON
- **THEN** the metadata block MUST contain `source_commit`
- **AND** the metadata block MUST NOT contain `logos_source_commit`

#### Scenario: Module import path renamed
- **WHEN** code imports `from wenji.eval.loader_benchmark_v2 import SnapshotMetadata`
- **THEN** the import MUST succeed
- **AND** `from wenji.eval.loader_logos_v2 import SnapshotMetadata` MUST raise `ModuleNotFoundError`

#### Scenario: snapshot_source_path generic
- **WHEN** the eval loader inspects `tests/benchmark_80_v2_snapshot.json`
- **THEN** the `snapshot_source_path` field MUST NOT contain the substring `logos`

### Requirement: baseline-output JSON undergoes schema and size validation

The `wenji eval sanity-eyeball --baseline-output <path>` CLI SHALL validate the loaded JSON against a defined schema (top-level dict with `results` array of objects each containing `q`, `top5`), enforce a file size limit of 10 megabytes before parsing, and reject any string field exceeding 64 kilobytes. Before printing any baseline value to stdout, the system SHALL strip control characters matching `[\x00-\x08\x0b-\x1f\x7f]` to prevent log injection. Path MUST exist and MUST be a regular file.

#### Scenario: Schema mismatch rejected
- **WHEN** the loaded JSON lacks a top-level `results` array
- **THEN** the command MUST exit non-zero with an error message naming the missing field

#### Scenario: File size limit enforced
- **WHEN** the baseline JSON file exceeds 10 megabytes
- **THEN** the command MUST exit non-zero before parsing JSON
- **AND** the error MUST mention the size limit

#### Scenario: Control characters stripped from console output
- **WHEN** a baseline `top5` element contains the string `"\x1b[2J<fake success>"`
- **THEN** the printed comparison line MUST NOT contain `\x1b` (ANSI escape) or other control bytes
- **AND** the printed text MUST equal `<fake success>`

#### Scenario: Path must be regular file
- **WHEN** `--baseline-output` is given a directory path or symlink to /dev/null
- **THEN** the command MUST exit non-zero with an error mentioning regular-file requirement

## REMOVED Requirements

### Requirement: from-logos-db ingest subcommand

**Reason**: The adapter served exactly one user (the maintainer's private logos production database) and provided no value to public open-source consumers. Keeping it in the public repository is technical debt and brand leakage.

**Migration**: Maintainers needing the adapter SHALL keep a private copy of `src/wenji/ingest/loader_logos_db.py` outside the public repository. Public users wishing to import from a SQLite source SHALL convert their data to wenji's markdown corpus format manually or write a domain-specific adapter.

#### Scenario: CLI no longer registers the subcommand
- **WHEN** the user runs `wenji ingest --help`
- **THEN** the listed subcommands MUST NOT include `from-logos-db`
- **AND** running `wenji ingest from-logos-db --src x --out y` MUST exit with non-zero status

#### Scenario: Module import fails
- **WHEN** any code attempts `from wenji.ingest.loader_logos_db import dump_logos_db`
- **THEN** the import MUST raise `ModuleNotFoundError`
