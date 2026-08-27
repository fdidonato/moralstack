# Releasing MoralStack

## Prerequisites (one-time)

1. Register the project on PyPI: https://pypi.org/account/register/
2. Create a PyPI API token for the `moralstack` project and keep it in your local
   `~/.pypirc` or in the `TWINE_PASSWORD` environment variable. Never commit it.
3. Ensure you have admin access to the GitHub repo to push tags.

> **Publishing is manual.** The `.github/workflows/publish.yml` workflow that used to
> publish on tag was removed; releasing now runs from your machine (step 5 below).
> The PyPI *trusted publisher* configured for this project still exists on the PyPI
> side — removing the workflow does not delete it. If the workflow is ever restored it
> must be named `publish.yml` again, because the pending publisher is bound to that
> exact file name.

## Release checklist

Before every release:

- [ ] All tests pass on `main` (`python -m pytest`; see `docs/DEVELOPMENT.md`).
- [ ] `CHANGELOG.md` has an `[Unreleased]` section with real entries.
- [ ] `examples/` still run with the new code (at minimum `quickstart.py`).
- [ ] Benchmark compliance has not regressed (spot-check with `scripts/benchmark_moralstack.py`).

## Release steps

1. Decide the new version following SemVer:
   - `0.1.x` -> patch (bug fixes, doc changes).
   - `0.x.0` -> minor (new features, backward-compatible).
   - `1.0.0` -> major (stable API, breaking changes acknowledged).

2. Update `version` in `pyproject.toml`.

3. Move `[Unreleased]` content in `CHANGELOG.md` under a new dated heading:
   ```markdown
   ## [0.2.0] - 2026-05-XX
   ```
   Leave a new empty `[Unreleased]` section on top.

4. Commit and tag:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "Release v0.2.0"
   git tag v0.2.0
   git push origin main
   git push origin v0.2.0
   ```

5. Build, validate, and upload to PyPI from a clean checkout of the tag:
   ```bash
   rm -rf dist/
   python -m build
   twine check dist/*
   twine upload dist/*
   ```
   `build` and `twine` come with `pip install -e ".[dev]"`.

6. Verify: `pip install moralstack==0.2.0` in a fresh virtualenv.

7. Create a GitHub Release from the tag with the CHANGELOG section as the release notes.

## Test release (optional, recommended for first publish)

Use TestPyPI before the real PyPI:

1. Create a separate API token on TestPyPI.
2. Tag a pre-release like `v0.2.0rc1` and build it as in step 5 above.
3. Upload with an explicit repository:
   ```bash
   twine upload --repository-url https://test.pypi.org/legacy/ dist/*
   ```
4. Install from TestPyPI to validate:
   ```bash
   pip install -i https://test.pypi.org/simple/ moralstack==0.2.0rc1
   ```
