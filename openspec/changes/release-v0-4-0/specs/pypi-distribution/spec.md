# Capability: pypi-distribution

## ADDED Requirements

### Requirement: Tag push triggers automated PyPI publish via trusted publishing

The system SHALL provide a GitHub Actions workflow (`.github/workflows/release.yml`) that, on push of a tag matching `v*`, builds the sdist and wheel (`python -m build`), validates them (`twine check`), and publishes to PyPI using OIDC trusted publishing (`pypa/gh-action-pypi-publish`). The workflow MUST NOT reference any long-lived PyPI API token (no repository secret, no environment secret containing a token); authentication MUST rely solely on the OIDC `id-token: write` permission. The publish job MUST run in a GitHub environment named `pypi`.

#### Scenario: tag push publishes to PyPI

- **WHEN** a tag `v0.4.0` is pushed to the repository and the PyPI trusted publisher is configured for `release.yml`
- **THEN** the workflow MUST build sdist + wheel, pass `twine check`, and publish both artifacts to PyPI as version 0.4.0
- **AND** the workflow logs MUST NOT contain any API token

#### Scenario: non-tag push does not trigger publish

- **WHEN** a commit is pushed to `main` without a tag
- **THEN** the `release.yml` workflow MUST NOT run

### Requirement: CI validates package build and wheel completeness on every PR

The CI pipeline (`.github/workflows/ci.yml`) SHALL include a `build` job that runs on every push and pull request: it MUST build the package (`python -m build`), run `twine check dist/*`, and assert that the built wheel contains the declared package-data files — at minimum `wenji/core/schema.sql`, at least one file under `wenji/web/templates/`, and at least one file under the shipped examples corpus. A missing asserted file MUST fail the job.

#### Scenario: wheel missing package-data fails CI

- **WHEN** a change removes `core/schema.sql` from the built wheel (e.g. a `[tool.setuptools.package-data]` regression)
- **THEN** the `build` job MUST fail with an error naming the missing file

#### Scenario: healthy build passes

- **WHEN** the package builds cleanly and the wheel contains all asserted package-data files
- **THEN** the `build` job MUST exit successfully and upload no release artifacts (publishing is exclusively `release.yml`'s responsibility)

### Requirement: Released version metadata is consistent across pyproject, tag, and CHANGELOG

For every published release, the version in `pyproject.toml`, the git tag (`v<version>`), and a `## [<version>] — YYYY-MM-DD` section in `CHANGELOG.md` MUST agree. `CHANGELOG.md` MUST follow Keep a Changelog structure: one `## [<version>]` section per released version and an `[Unreleased]` section for pending work; released content MUST NOT remain under `[Unreleased]`.

#### Scenario: v0.4.0 release metadata agrees

- **WHEN** tag `v0.4.0` is pushed
- **THEN** `pyproject.toml` at that tag MUST declare `version = "0.4.0"`
- **AND** `CHANGELOG.md` at that tag MUST contain a `## [0.4.0]` section with a release date and MUST NOT carry `(vNext)` or `(v0.3.7)` interim markers under `[Unreleased]`

#### Scenario: historical versions have their own sections

- **WHEN** `CHANGELOG.md` is read at tag `v0.4.0`
- **THEN** every previously tagged version (`0.2.x` through `0.3.6.1`) MUST appear as its own `## [<version>] — YYYY-MM-DD` section with the date taken from its git tag

### Requirement: Release gate precedes tagging

Before a release tag is pushed, the release process MUST verify, in order: (1) `scripts/audit_release.sh` exits 0 (zero internal-reference hits), (2) the full unit test suite passes, (3) the integration test suite passes. A failure at any step MUST abort the release (no tag pushed).

#### Scenario: audit failure blocks release

- **WHEN** `scripts/audit_release.sh` reports one or more internal-reference hits
- **THEN** the release process MUST stop before tagging and the hits MUST be remediated first

#### Scenario: post-publish smoke verification

- **WHEN** the publish workflow completes successfully
- **THEN** the release process MUST verify `pip install wenji==<version>` succeeds in a clean environment and `wenji --help` executes without error
