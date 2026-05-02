# Contributing to wenji

Thanks for your interest in **wenji** — a Chinese-first markdown RAG framework.
This guide covers local development setup, the test suite, and the PR flow.

## Development setup

```bash
# 1. Clone
git clone https://github.com/notoriouslab/wenji.git
cd wenji

# 2. Create a virtualenv (Python 3.10 / 3.11 / 3.12)
python3 -m venv .venv
source .venv/bin/activate

# 3. Editable install with dev extras
pip install -e ".[dev]"
```

This pulls in `pytest`, `pytest-cov`, `ruff`, and `build` alongside the runtime
dependencies declared in `pyproject.toml`.

## Running tests

wenji has two test tiers:

```bash
# Unit tests (default — fast, no network, no model download)
pytest

# Integration tests (~600 MB ONNX model download, real inference)
pytest -m integration
```

The default `addopts` excludes the `integration` marker so `pytest` stays fast.
CI runs both tiers across Python 3.10 / 3.11 / 3.12.

## Code style

```bash
# Lint
ruff check src/wenji tests/wenji

# Format check (CI gate)
ruff format --check src/wenji tests/wenji

# Auto-format
ruff format src/wenji tests/wenji
```

CI fails on any `ruff check` error or formatting drift, so run both locally
before pushing.

## Pull request flow

1. **Fork** and create a topic branch off `main`:
   `feat/<short-slug>` or `fix/<short-slug>`.
2. **Write tests first** when fixing a bug or adding a feature. The bar is
   "if it isn't tested, it isn't done." Pure-logic modules (no network /
   no filesystem dependency) MUST have unit tests.
3. **Run the full unit suite** locally before opening the PR:
   `ruff check && ruff format --check && pytest`.
4. **Update CHANGELOG.md** under the `## [Unreleased]` heading (add one if
   missing) describing the user-facing change.
5. **Open the PR** — keep the description focused on *what* and *why*, not
   *how*; the diff already shows *how*.

CI will run the lint + unit + cleanup-audit gates on every push.

## Design philosophy (read before proposing big changes)

wenji follows **LLM-essential, not LLM-default**:

- The indexing pipeline performs **zero** LLM calls.
- LLM use is allowed only at query time, must be query-level cached, and must
  ship a deterministic structured fallback path that works without any LLM.
- No LLM-extracted graphs, no pre-built community reports, no LLM-driven
  entity merging. Entity dictionaries are user-supplied, not LLM-derived.

Proposals that violate these constraints (e.g. "let's call an LLM during
ingest to generate summaries") will be politely declined unless the
justification is overwhelming. See `openspec/specs/` for the full design.

## Reporting issues

Before filing, please:

- Check the [existing issues](https://github.com/notoriouslab/wenji/issues).
- Include your Python version, OS, and a minimal reproduction (a markdown
  file plus the `wenji` command that fails is ideal).
- For search-quality regressions, include the query, expected top result,
  and actual top result.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
