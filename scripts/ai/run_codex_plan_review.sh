#!/usr/bin/env bash
# Independent Codex review of a technical PLAN (before implementation).
# Runs: codex exec -s read-only [-m model] -o <review.md> -   (prompt on stdin)
# Read-only sandbox: Codex cannot modify, commit, or push.
# Usage: run_codex_plan_review.sh --plan <path> [--out-dir dir] [--model m] [--dry-run]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

PLAN=""; OUTDIR=""; MODEL="${CODEX_MODEL:-}"; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --plan) PLAN="$2"; shift 2;;
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
TEMPLATE="ai/prompts/codex-plan-review-template.md"
[ -f "$TEMPLATE" ] || { echo "Missing template: $TEMPLATE" >&2; exit 1; }

GEN="ai/prompts/generated-codex-plan-review-$NAME-$TS.md"
REVIEW="$OUTDIR/codex-plan-review-$NAME-$TS.md"
{
  cat "$TEMPLATE"
  echo; echo "---"; echo
  echo "## Repository context"
  echo "- Repo root: $ROOT"
  echo "- MoralStack governance engine. Flag plan steps that risk PROJECT_SPEC section 5"
  echo "  invariants (.claude/rules/). You may read any file read-only to verify claims."
  echo; echo "---"; echo
  echo "## PLAN UNDER REVIEW (file: $PLAN)"; echo
  cat "$PLAN"
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

echo "Running Codex plan review (read-only sandbox) -> $REVIEW"
cat "$GEN" | "$CODEX" exec -s read-only "${MODEL_ARGS[@]}" -o "$REVIEW" -
[ -f "$REVIEW" ] && { echo "Review written: $REVIEW"; printf '%s\n' "$REVIEW"; } || { echo "No review produced; see $GEN" >&2; exit 3; }
