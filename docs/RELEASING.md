# Releasing MoralStack

## Prerequisites (one-time)

1. Register the project on PyPI: https://pypi.org/account/register/
2. Configure trusted publishing (see comment header in `.github/workflows/publish.yml`).
3. Ensure you have admin access to the GitHub repo to push tags.

## Release checklist

Before every release:

- [ ] All tests pass on `main` (check CI badge).
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

5. The `publish.yml` workflow builds, validates, and uploads to PyPI automatically.

6. Verify: `pip install moralstack==0.2.0` in a fresh virtualenv.

7. Create a GitHub Release from the tag with the CHANGELOG section as the release notes.

## Test release (optional, recommended for first publish)

Use TestPyPI before the real PyPI:

1. Configure trusted publisher for TestPyPI separately.
2. Push a pre-release tag like `v0.2.0rc1`.
3. Adapt the workflow with a `repository-url` parameter pointing to TestPyPI.
4. Install from TestPyPI to validate:
   ```bash
   pip install -i https://test.pypi.org/simple/ moralstack==0.2.0rc1
   ```
