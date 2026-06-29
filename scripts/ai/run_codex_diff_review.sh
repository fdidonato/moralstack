#!/usr/bin/env bash
# Independent Codex review of the current DIFF against an approved plan.
# Runs: codex exec -s read-only [-m model] -o <review.md> -   (prompt on stdin)
# Read-only sandbox: Codex cannot modify, commit, or push.
# Usage: run_codex_diff_review.sh --plan <path> [--diff <path>] [--handoff <path>]
#                                 [--base <ref>] [--out-dir dir] [--model m] [--dry-run]
set -euo pipefail
HERE="$(dirname "${BASH_SOURCE[0]}")"
. "$HERE/_common.sh"

PLAN=""; DIFF=""; HANDOFF=""; BASE=""; OUTDIR=""; MODEL="${CODEX_MODEL:-}"; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --plan) PLAN="$2"; shift 2;;
    --diff) DIFF="$2"; shift 2;;
    --handoff) HANDOFF="$2"; shift 2;;
    --base) BASE="$2"; shift 2;;
    --out-dir) OUTDIR="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    *) echo "Unknown arg: $1" >&2; exit 1;;
  esac
done
[ -n "$PLAN" ] && [ -f "$PLAN" ] || { echo "Plan not found: $PLAN" >&2; exit 1; }

ROOT="$(repo_root)"; cd "$ROOT"
TS="$(timestamp)"
NAME="$(basename "${PLAN%.*}")"
[ -n "$OUTDIR" ] || OUTDIR="ai/reviews"
ensure_dir "$OUTDIR" >/dev/null
ensure_dir "ai/prompts" >/dev/null

if [ -z "$DIFF" ]; then
  if [ -n "$BASE" ]; then DIFF="$(bash "$HERE/collect_git_diff.sh" --base "$BASE" | tail -n1)";
  else DIFF="$(bash "$HERE/collect_git_diff.sh" | tail -n1)"; fi
fi
[ -f "$DIFF" ] || { echo "Diff not found: $DIFF" >&2; exit 1; }

TEMPLATE="ai/prompts/codex-diff-review-template.md"
[ -f "$TEMPLATE" ] || { echo "Missing template: $TEMPLATE" >&2; exit 1; }

GEN="ai/prompts/generated-codex-diff-review-$NAME-$TS.md"
REVIEW="$OUTDIR/codex-diff-review-$NAME-$TS.md"
{
  cat "$TEMPLATE"
  echo; echo "---"; echo
  echo "## Repository context"
  echo "- Repo root: $ROOT"
  echo "- MoralStack governance engine. Verify the diff does not break PROJECT_SPEC"
  echo "  section 5 invariants. You may read any file read-only."
  echo; echo "---"; echo
  echo "## APPROVED PLAN (file: $PLAN)"; echo; cat "$PLAN"
  echo; echo "---"; echo
  echo "## CURSOR HANDOFF (file: ${HANDOFF:-none})"; echo
  if [ -n "$HANDOFF" ] && [ -f "$HANDOFF" ]; then cat "$HANDOFF"; else echo "(no handoff provided)"; fi
  echo; echo "---"; echo
  echo "## DIFF UNDER REVIEW (file: $DIFF)"; echo; cat "$DIFF"
} > "$GEN"

CODEX="$(resolve_codex || true)"
MODEL_ARGS=(); [ -n "$MODEL" ] && MODEL_ARGS+=(-m "$MODEL")
[ -n "${CODEX_REASONING_EFFORT:-}" ] && MODEL_ARGS+=(-c "model_reasoning_effort=\"$CODEX_REASONING_EFFORT\"")

if [ -z "$CODEX" ] || [ "$DRY" = "1" ]; then
  echo "Codex CLI not invoked (manual mode). Prompt saved: $GEN"
  echo "Run manually:"
  echo "  cat '$GEN' | codex exec -s read-only ${MODEL_ARGS[*]:-} -o '$REVIEW' -"
  exit 2
fi

echo "Running Codex diff review (read-only sandbox) -> $REVIEW"
cat "$GEN" | "$CODEX" exec -s read-only "${MODEL_ARGS[@]}" -o "$REVIEW" -
[ -f "$REVIEW" ] && { echo "Review written: $REVIEW"; printf '%s\n' "$REVIEW"; } || { echo "No review produced; see $GEN" >&2; exit 3; }
