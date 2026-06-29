---
description: Collect the current diff and have Codex CLI review it against the approved plan
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Bash, Read, Grep, Glob, Agent
---

Approved plan the diff must satisfy: **$ARGUMENTS**.

Steps:
1. Launch the **codex-review-coordinator** agent to run the diff review:
   `pwsh scripts/ai/run_codex_diff_review.ps1 -PlanPath "$ARGUMENTS" -HandoffPath "ai/handoffs/<task>-cursor-cli-handoff.md"`
   The script collects the current diff via
   `scripts/ai/collect_git_diff.ps1` (saved under `ai/reviews/`), builds the
   prompt from `ai/prompts/codex-diff-review-template.md`, runs
   `codex exec -s read-only` (read-only sandbox), and saves the report to
   `ai/reviews/`. If a diff was already collected (e.g. by the Cursor run),
   pass it with `-DiffPath`. If Codex CLI is unavailable, relay the manual
   command the script prints.
2. Read the saved review. Present the verdict
   (APPROVE / APPROVE_WITH_CHANGES / BLOCK) and classify findings
   **BLOCKING / NON_BLOCKING / SUGGESTION**. Surface deviations from the plan.
3. If there are BLOCKING items: state that implementation must be fixed (back to
   `/ai-implement-with-cursor` with an updated handoff) before finalize.

Next step to tell the user: `/ai-finalize $ARGUMENTS`.

Do not modify code here. Do not commit.
