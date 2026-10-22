# MoralStack Development

Minimal guide for contributing to and developing MoralStack.

## Setup

```bash
pip install -e .[dev]
```

## Tools

- **pytest** — Run tests: `pytest` (parallel by default via **pytest-xdist**: `addopts` in `pyproject.toml`
  sets `-n auto --maxprocesses=8 --dist worksteal`; use `pytest -n 0` for a serial run, e.g. with `--pdb`; targeted runs — files, directories,
  node ids or `-k` — are serial automatically, and an explicit `-n auto` forces parallel)
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

## Verification (run locally)

There is no CI workflow: `.github/workflows/` was removed and the checks it used to run are the
contributor's responsibility. Run them locally on Python 3.11 and 3.12 after `pip install -e .[dev,ui]`:

```bash
ruff check .
black --check .
mypy moralstack
pytest --cov=moralstack --cov-report=term --maxfail=3
pytest -n 0 -m slow --maxfail=1 # perf benchmarks, excluded from the default run by addopts; run them serially
```

The first three also run as pre-commit hooks, so a normal commit already covers them.

## Generated Artifacts

The `logs/`, `reports/` and `.pytest_cache/` folders are generated at runtime and are in `.gitignore`. **They should not
be committed.**
