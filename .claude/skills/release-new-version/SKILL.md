---
name: release-new-version
description: Release a new MoralStack version. Bump, release, tag, publish a version of the project — updates pyproject.toml, adds a CHANGELOG section, commits, pushes, and creates+pushes a vX.Y.Z git tag. Use when asked to "release", "cut a release", "bump the version", "tag a new version", or "rilascia una nuova versione".
---

# Release a new MoralStack version

Drives a single release end to end via `.claude/skills/release-new-version/release.py`
(Python 3, stdlib + `git` only — no extra deps). Given a target version like
`0.7.0` it: bumps `version` in `pyproject.toml`, writes a new `## 0.7.0 — <date>`
section in `CHANGELOG.md`, commits, pushes the branch, then creates and pushes
the tag `v0.7.0`.

All paths below are relative to the repo root. The driver itself locates the git
toplevel, so you can run it from anywhere in the repo.

## Always confirm the version first

This skill must never guess the version. If the user did not give an explicit
number, **ask** which version to release (e.g. `0.7.0`) before doing anything.
The driver takes the number **without** the leading `v`; the tag gets the `v`
prefix automatically.

## Run (agent path)

1. **Preview** — never commit blind. Dry-run prints the pyproject bump and the
   exact CHANGELOG section that will be written, and writes nothing:

   ```bash
   python .claude/skills/release-new-version/release.py 0.7.0 --dry-run
   ```

2. **Review the generated CHANGELOG section.** The section is built from the
   commits since the last tag:
   - If a `## Unreleased` block already exists in `CHANGELOG.md`, it is **promoted**
     (its heading becomes `## 0.7.0 — <date>`) and its hand-curated content is kept
     verbatim.
   - Otherwise a section is **generated** from commit subjects, grouped by
     Conventional-Commit type: `feat` → Added, `fix` → Fixed,
     `perf`/`refactor`/`build`/`ci`/`chore`/`style` → Changed, `docs` → Docs,
     `test` → Tests, anything else → Other. Release/version-bump chores are skipped.

   If the auto-generated grouping is too coarse for a notable release, edit
   `CHANGELOG.md` by hand first (or add a `## Unreleased` block), then run the
   release — the promotion path preserves your curation.

3. **Release for real:**

   ```bash
   python .claude/skills/release-new-version/release.py 0.7.0
   ```

   This bumps pyproject, updates the CHANGELOG, commits
   `chore(release): rilascia v0.7.0`, runs `git push`, then `git tag v0.7.0` and
   `git push origin v0.7.0`.

   The driver stops at the tag. Pushing the tag no longer publishes to PyPI — the
   `publish.yml` workflow was removed; upload manually as described in
   `docs/RELEASING.md`.

### Useful flags

- `--no-push` — bump, commit, and create the tag locally; push nothing. Verify,
  then `git push && git push origin v0.7.0` yourself.
- `--allow-dirty` — skip the clean-worktree check (by default the release aborts
  if tracked files other than `pyproject.toml`/`CHANGELOG.md` are modified;
  untracked files are ignored).
- `--remote NAME` — push the tag to a remote other than `origin`.
- `--date YYYY-MM-DD` — override the release date (defaults to today).

## Preconditions enforced by the driver (it aborts otherwise)

- Version must match `X.Y.Z` (optionally `X.Y.Z-suffix`). `1.2` is rejected.
- The tag `vX.Y.Z` must not already exist.
- The worktree must be clean except for the two files it edits (unless `--allow-dirty`).

## Gotchas

- **Run the dry-run every time.** The generated section is only as good as the
  commit messages since the last tag. Commits that don't follow Conventional
  Commits all land under `### Other` — that's the signal to curate by hand.
- **The last tag is whatever `git describe --tags --abbrev=0` returns.** This repo
  has a few legacy non-`v` tags (`0.1.0`, `0.2.0`); the range is computed from the
  most recent reachable tag, so confirm the dry-run's `last tag:` line is the one
  you expect before releasing.
- **Push happens before tagging.** If `git push` fails (e.g. branch protection or
  needing a PR), the commit is already made but the tag is not pushed; fix the
  push, then re-run with `--allow-dirty` is **not** needed — instead push the
  branch and create/push the tag manually, or `git reset --soft HEAD~1` to redo.
- **Commit message is Italian** (`chore(release): rilascia vX.Y.Z`) to match the
  repo's commit conventions.
- The repo's `guard_dangerous_git.py` pre-commit hook blocks `--no-verify`,
  force-push, and `reset --hard`; the driver uses none of these.

## Verification done for this skill

Verified in this container against throwaway git repos with a bare remote (so the
real `origin` was never touched):

- `--dry-run 0.7.0` on the real repo: showed `0.6.0 -> 0.7.0`, `last tag: v0.6.0`,
  and the generated section.
- Full `release.py 0.2.0` against a sandbox repo: pyproject bumped, CHANGELOG
  section grouped (Added/Fixed/Docs/Other), `chore(release): rilascia v0.2.0`
  committed, branch pushed, tag `v0.2.0` created and pushed to the bare remote.
- `## Unreleased` promotion preserved a hand-curated note and the heading became
  `## X.Y.Z — <date>`.
- Guards exercised: bad version `1.2` (exit 2), existing tag (exit 1), dirty
  worktree (exit 1), and `--no-push` (local tag only).
