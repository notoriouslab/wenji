# Followup change scaffold: `cleanup-build-telemetry`

> **Status**: Backlog. NOT yet a propose-ready spectra change. This file captures the open decisions handed off from `add-doctor-and-startup-check` so the followup is not forgotten and the next session has full context.

## Origin

Discovered during `add-doctor-and-startup-check` Phase 4 apply (2026-05-10).

`wenji_meta` contains 5 dead schema columns introduced in v0.1.0 but never maintained by any code path:

- `build_started_at` — timestamp stub, no writer
- `build_completed_at` — timestamp stub, no writer
- `n_articles` — counter, only ever set to `'0'` (schema init + rebuild reset)
- `n_chunks` — same
- `n_doc_vectors` — same

Live keys (kept): `schema_version` (verified at connect), `embedder` (init-only constant).

## Why a separate change

The original `add-doctor-and-startup-check` propose specced an L1 layer reading `n_articles` / `n_chunks` / `n_doc_vectors` against matching table row counts. Apply discovered the counters were dead — L1 was removed (see `add-doctor-and-startup-check/proposal.md` G1 drift correction #2). Wiring up counter maintenance OR dropping the columns is an independent design decision that:

1. Touches `ingest/__init__.py` (write path) — outside the read-only health-check change
2. May require schema bump (v2 → v3) — large blast radius
3. Has prerequisite questions about whether build telemetry is worth implementing at all

Forcing the decision into the doctor change would have:
- Mixed retrieval-health (the actual scope) with ingest-pipeline maintenance
- Either left a half-fix (counter alive but no migration for existing dbs) or expanded scope to ~120+ min including self-heal logic

## Open decisions for the followup change

### D1 — Drop or wire up?

| Path | Outcome | Effort |
|---|---|---|
| **Drop all 5 columns** | Schema bump v2 → v3; existing dbs auto-migrate (SQLite ALTER TABLE DROP COLUMN). `wenji_meta` shrinks to `schema_version` + `embedder`. No build telemetry. | medium (schema migration + test coverage) |
| **Wire up counters only** | `ingest_dir` / `rebuild_from_disk` end → recompute + write `n_articles` / `n_chunks` / `n_doc_vectors`. Re-introduce L1 in `health.py` with meaningful detection (partial-crash). Drop timestamp columns. | medium |
| **Wire up everything** | Counters + `build_started_at` (set at ingest start) + `build_completed_at` (set at end). Full build telemetry. | high |
| **Hybrid: lazy compute counters** | Drop counter columns; if any future need, compute `SELECT COUNT(*)` on demand. Drop timestamps too. | low |

### D2 — Existing dbs migration path

If columns are dropped, SQLite supports `ALTER TABLE wenji_meta DROP COLUMN ...` from 3.35+ (2021). wenji's required SQLite version needs verifying.

If columns stay but counters become alive, existing prod dbs (counter `'0'` + row count > 0) need either:
- Doctor `--recompute-counters` flag (manual)
- Startup self-heal (auto-backfill when counter `'0'` + row count > 0, before L1 verifies)
- Schema bump forcing rebuild

### D3 — L1 re-introduction (only if wiring up)

If counters become alive, `health.py` should re-add L1 (counter ↔ row count). Detection power is limited but real for partial-crash scenarios (ingest_dir is per-article commit per `src/wenji/ingest/__init__.py:411` — counter update at ingest end; SIGKILL between last article commit and counter update leaves detectable mismatch).

## Non-goals

- Not in scope for this followup: redesigning `wenji.observability.stats` (it does not read these columns)
- Not in scope: changing `embedder` column semantics (init-only constant, fine as-is)

## Triggering this change

Open via `spectra:propose cleanup-build-telemetry` with this BACKLOG.md as input; include current `wenji_meta` reader inventory + decision answers from above.

## References

- `add-doctor-and-startup-check/proposal.md` G1 drift correction #2 — original discovery
- `add-doctor-and-startup-check/design.md` D4.original — archived 3-layer design that depended on alive counters
- `src/wenji/core/schema.sql` — DEPRECATED-annotated columns
- `src/wenji/observability/health.py` module docstring — note pointing here
