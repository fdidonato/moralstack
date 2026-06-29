#!/usr/bin/env bash
# Save the current working-tree diff to a markdown file under ai/reviews/.
# Read-only w.r.t. git: never commits, stages, pushes, or deletes.
# Usage: collect_git_diff.sh [--base <ref>] [--out <path>]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

BASE=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="$2"; shift 2;;
    --out)  OUT="$2"; shift 2;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

ROOT="$(repo_root)"; cd "$ROOT"
TS="$(timestamp)"
if [ -z "$OUT" ]; then ensure_dir "ai/reviews" >/dev/null; OUT="ai/reviews/diff-$TS.md"; fi

{
  echo "# Working-tree diff snapshot"
  echo
  echo "- Generated: $TS"
  echo "- Repo: $ROOT"
  [ -n "$BASE" ] && echo "- Base ref: $BASE"
  echo
  echo "## git status"; echo '```'; git status --short --branch || true; echo '```'; echo
  echo "## Untracked files"; echo '```'; git ls-files --others --exclude-standard || true; echo '```'; echo
  if [ -n "$BASE" ]; then
    echo "## git diff $BASE...HEAD"; echo '```diff'; git diff "$BASE...HEAD" || true; echo '```'; echo
  fi
  echo "## git diff (unstaged)"; echo '```diff'; git diff || true; echo '```'; echo
  echo "## git diff --cached (staged)"; echo '```diff'; git diff --cached || true; echo '```'
} > "$OUT"

echo "Wrote: $OUT"
printf '%s\n' "$OUT"
