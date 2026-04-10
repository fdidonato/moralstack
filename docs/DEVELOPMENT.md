# MoralStack Development

Minimal guide for contributing to and developing MoralStack.

## Setup

```bash
pip install -e .[dev]
```

## Tools

- **pytest** — Run tests: `pytest`
- **pytest-cov** — Coverage reports: `pytest --cov=moralstack --cov-report=xml --cov-report=term`
- **ruff** — Linting and formatting: `ruff check .` / `ruff format .`
- **black** — Format check: `black --check .` (or `black .` to reformat)
- **mypy** — Type checking: `mypy moralstack`

## Pre-commit Hooks

Pre-commit hooks run automated checks (format, lint, whitespace, type checks) before every commit.

**Setup (one-time):**

```bash
pip install pre-commit   # included in .[dev]
pre-commit install
```

**Manual run on all files:**

```bash
pre-commit run --all-files
```

**Skip hooks (discouraged):**

```bash
git commit --no-verify
```

Active hooks: `trailing-whitespace`, `end-of-file-fixer`, `ruff check --fix`, `black`, `mypy moralstack`.

## CI

The workflow in `.github/workflows/ci.yml` runs on Python 3.11 and 3.12 with `pip install -e .[dev,ui]`, then executes:
`ruff check .`, `black --check .`, `mypy moralstack --ignore-missing-imports`, and `pytest --cov=moralstack
--cov-report=xml --cov-report=term --maxfail=3`.

## Generated Artifacts

The `logs/`, `reports/` and `.pytest_cache/` folders are generated at runtime and are in `.gitignore`. **They should not
be committed.**
