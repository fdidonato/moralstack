#!/usr/bin/env python3
"""Release driver for MoralStack — bump version, update CHANGELOG, commit, push, tag.

Run from anywhere inside the repo. Operates on the git toplevel.

    python .claude/skills/release-new-version/release.py 0.7.0
    python .claude/skills/release-new-version/release.py 0.7.0 --dry-run
    python .claude/skills/release-new-version/release.py 0.7.0 --no-push

The new version is written to pyproject.toml, a CHANGELOG section is produced
(either by promoting an existing "## Unreleased" block or by generating one from
the commits since the last tag), the two files are committed, the branch is
pushed, and a tag ``v<version>`` is created and pushed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
PYPROJECT_VERSION_RE = re.compile(r'^(version\s*=\s*")([^"]+)(")\s*$', re.MULTILINE)
CONVENTIONAL_RE = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*(?P<desc>.+)$")

# Conventional-commit type -> CHANGELOG section.
TYPE_TO_SECTION = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "build": "Changed",
    "ci": "Changed",
    "chore": "Changed",
    "style": "Changed",
    "docs": "Docs",
    "test": "Tests",
}
SECTION_ORDER = ["Added", "Changed", "Fixed", "Docs", "Tests", "Other"]


class ReleaseError(RuntimeError):
    """Recoverable, expected failure that aborts the release with a clear message."""


def run_git(args: list[str], cwd: Path, check: bool = True) -> str:
    """Run a git command and return stripped stdout. Raises ReleaseError on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise ReleaseError("not inside a git repository")
    return Path(out.stdout.strip())


def assert_clean_worktree(root: Path, editable: set[str]) -> None:
    """Abort if tracked files other than the ones we will edit are modified/staged."""
    status = run_git(["status", "--porcelain", "--untracked-files=no"], root)
    dirty = []
    for line in status.splitlines():
        # porcelain format: "XY path"
        path = line[3:].strip()
        if path and path not in editable:
            dirty.append(path)
    if dirty:
        raise ReleaseError(
            "working tree has uncommitted changes to tracked files:\n  "
            + "\n  ".join(dirty)
            + "\nCommit or stash them first (or pass --allow-dirty)."
        )


def bump_pyproject(pyproject: Path, new_version: str) -> str:
    """Replace the [project] version. Returns the old version."""
    text = pyproject.read_text(encoding="utf-8")
    match = PYPROJECT_VERSION_RE.search(text)
    if not match:
        raise ReleaseError(f'no `version = "..."` line found in {pyproject}')
    old_version = match.group(2)
    new_text = text[: match.start()] + f"{match.group(1)}{new_version}{match.group(3)}" + text[match.end() :]
    pyproject.write_text(new_text, encoding="utf-8")
    return old_version


def last_tag(root: Path) -> str | None:
    out = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or None if out.returncode == 0 else None


def commit_subjects(root: Path, since: str | None) -> list[str]:
    """Subjects of commits in `since..HEAD` (or all of HEAD if no tag), oldest last."""
    spec = f"{since}..HEAD" if since else "HEAD"
    out = run_git(["log", spec, "--no-merges", "--pretty=%s"], root)
    return [line for line in out.splitlines() if line.strip()]


def build_changelog_section(version: str, subjects: list[str], release_date: str) -> str:
    """Group commit subjects into a Keep-a-Changelog section."""
    buckets: dict[str, list[str]] = {name: [] for name in SECTION_ORDER}
    for subject in subjects:
        # Skip release/version-bump chores so the section is not self-referential.
        if re.match(r"^chore\(release\):", subject) or subject.lower().startswith("changed version"):
            continue
        match = CONVENTIONAL_RE.match(subject)
        if match:
            section = TYPE_TO_SECTION.get(match.group("type"), "Other")
            scope = match.group("scope")
            desc = match.group("desc").strip()
            entry = f"**{scope}**: {desc}" if scope else desc
        else:
            section = "Other"
            entry = subject.strip()
        buckets[section].append(entry)

    lines = [f"## {version} — {release_date}", ""]
    any_entry = False
    for name in SECTION_ORDER:
        entries = buckets[name]
        if not entries:
            continue
        any_entry = True
        lines.append(f"### {name}")
        lines.append("")
        for entry in entries:
            lines.append(f"- {entry}")
        lines.append("")
    if not any_entry:
        lines.append("- No notable changes recorded since the previous tag.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def update_changelog(changelog: Path, version: str, subjects: list[str], release_date: str) -> str:
    """Promote an existing `## Unreleased` block or insert a generated section.

    Returns the text of the section that now represents this version (for preview).
    """
    text = changelog.read_text(encoding="utf-8")
    unreleased_re = re.compile(r"^##[ \t]+\[?Unreleased\]?[ \t]*$", re.MULTILINE | re.IGNORECASE)
    unreleased = unreleased_re.search(text)
    if unreleased:
        new_heading = f"## {version} — {release_date}"
        new_text = text[: unreleased.start()] + new_heading + text[unreleased.end() :]
        changelog.write_text(new_text, encoding="utf-8")
        return new_heading

    section = build_changelog_section(version, subjects, release_date)
    # Insert before the first existing version heading; otherwise append after the preamble.
    first_version = re.search(r"^## ", text, re.MULTILINE)
    if first_version:
        insert_at = first_version.start()
        new_text = text[:insert_at] + section + "\n" + text[insert_at:]
    else:
        new_text = text.rstrip() + "\n\n" + section
    changelog.write_text(new_text, encoding="utf-8")
    return section


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump version, update CHANGELOG, commit, push, tag.")
    parser.add_argument("version", help="target version, e.g. 0.7.0 (without the leading 'v')")
    parser.add_argument("--dry-run", action="store_true", help="show planned changes, write/commit nothing")
    parser.add_argument("--no-push", action="store_true", help="commit and tag locally, do not push")
    parser.add_argument("--allow-dirty", action="store_true", help="skip the clean-worktree check")
    parser.add_argument("--remote", default="origin", help="remote to push to (default: origin)")
    parser.add_argument("--date", default=date.today().isoformat(), help="release date (default: today)")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    if not SEMVER_RE.match(version):
        print(f"error: '{args.version}' is not a valid version (expected X.Y.Z, e.g. 0.7.0)", file=sys.stderr)
        return 2
    tag = f"v{version}"

    try:
        root = repo_root()
        pyproject = root / "pyproject.toml"
        changelog = root / "CHANGELOG.md"
        if not pyproject.exists():
            raise ReleaseError(f"{pyproject} not found")
        if not changelog.exists():
            raise ReleaseError(f"{changelog} not found")

        if not args.allow_dirty:
            assert_clean_worktree(root, editable={"pyproject.toml", "CHANGELOG.md"})

        if tag in run_git(["tag", "--list", tag], root).splitlines():
            raise ReleaseError(f"tag {tag} already exists")

        since = last_tag(root)
        subjects = commit_subjects(root, since)

        if args.dry_run:
            current = PYPROJECT_VERSION_RE.search(pyproject.read_text(encoding="utf-8"))
            old = current.group(2) if current else "?"
            print(f"[dry-run] pyproject version: {old} -> {version}")
            print(f"[dry-run] last tag: {since or '(none)'}  | commits in range: {len(subjects)}")
            print(f"[dry-run] tag to create: {tag} (remote: {args.remote})")
            print("[dry-run] CHANGELOG section that would be written:\n")
            print(build_changelog_section(version, subjects, args.date))
            return 0

        old_version = bump_pyproject(pyproject, version)
        update_changelog(changelog, version, subjects, args.date)
        print(f"version {old_version} -> {version}")

        run_git(["add", "pyproject.toml", "CHANGELOG.md"], root)
        run_git(["commit", "-m", f"chore(release): rilascia {tag}"], root)
        print(f"committed release {tag}")

        if not args.no_push:
            run_git(["push"], root)
            print("pushed branch")

        run_git(["tag", tag], root)
        print(f"created tag {tag}")

        if not args.no_push:
            run_git(["push", args.remote, tag], root)
            print(f"pushed tag {tag} to {args.remote}")
        else:
            print(f"--no-push: tag {tag} created locally; push it with `git push {args.remote} {tag}`")

        print(f"\nrelease {tag} complete")
        return 0

    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
