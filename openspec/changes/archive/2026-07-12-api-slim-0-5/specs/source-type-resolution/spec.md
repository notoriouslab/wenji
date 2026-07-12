# Capability: source-type-resolution

## ADDED Requirements

### Requirement: Default resolution order is frontmatter first

With `directory_map_overrides_frontmatter` unset or `false`, `derive_source_type` SHALL resolve in the order: non-empty `metadata['source_type']` → `directory_map[parent_dir_name]` → raise `IngestError`. This codifies the pre-0.5 behavior; existing corpora MUST ingest identically.

#### Scenario: frontmatter wins by default

- **WHEN** an article carries frontmatter `source_type: teaching` and its parent directory maps to `tgc-theology`
- **THEN** the derived source_type is `teaching`

#### Scenario: directory fallback when frontmatter is silent

- **WHEN** an article has no `source_type` in frontmatter and its parent directory maps to `sermon`
- **THEN** the derived source_type is `sermon`

### Requirement: Deployment can declare directory structure as source of truth

`WenjiConfig` SHALL expose `directory_map_overrides_frontmatter: bool` defaulting to `false`, loaded from the yaml top level. When `true`, resolution order SHALL be: `directory_map[parent_dir_name]` → non-empty `metadata['source_type']` → raise `IngestError`. A directory-map miss with the flag on MUST fall back to frontmatter, not error.

#### Scenario: flag inverts precedence on map hit

- **WHEN** the flag is `true`, the parent directory maps to `tgc-theology`, and frontmatter says `teaching`
- **THEN** the derived source_type is `tgc-theology`

#### Scenario: flag on with map miss falls back to frontmatter

- **WHEN** the flag is `true`, the parent directory is absent from `directory_map`, and frontmatter says `teaching`
- **THEN** the derived source_type is `teaching` (no error)

#### Scenario: flag on with neither source errors

- **WHEN** the flag is `true`, the parent directory is unmapped, and frontmatter has no `source_type`
- **THEN** `IngestError` is raised naming the file path

##### Example: tgc taxonomy un-flattening

- **GIVEN** `directory_map: {tgc: tgc-theology}` and `directory_map_overrides_frontmatter: true`
- **WHEN** `articles/tgc/foo.md` with frontmatter `source_type: teaching` is ingested
- **THEN** the stored source_type is `tgc-theology`, enabling axes rules keyed on `tgc-*` subtypes
