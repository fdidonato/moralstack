---
description: Hand the approved plan to Cursor CLI for headless implementation; collect log and diff
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Bash, Read, Edit, Grep, Glob, Agent
---

Approved plan: **$ARGUMENTS**.

Preconditions: the plan has been reviewed by Codex and has **no unresolved
BLOCKING items** (`/ai-review-plan-with-codex` already run). If not, stop and run
the review first.

Steps:
1. Launch the **cursor-cli-implementation-coordinator** agent. It reads the plan
   and the matching Codex plan review (`ai/reviews/codex-plan-review-*.md`) and
   writes a complete handoff to
   `ai/handoffs/<task>-cursor-cli-handoff.md` (context, objective, approved
   plan, files allowed to modify, files NOT to modify, invariants, checklist,
   required tests, acceptance criteria, risks, ready prompt, required output).
2. The coordinator runs
   `pwsh scripts/ai/run_cursor_implementation.ps1 -HandoffPath "ai/handoffs/<task>-cursor-cli-handoff.md"`.
   Cursor CLI (`cursor-agent`) is the implementer, headless
   (`-p --force --trust --model auto`). The script tees the run log to
   `ai/handoffs/` and saves the post-run diff to `ai/reviews/`. It never commits
   or pushes. If `cursor-agent` is unavailable it saves the bootstrap prompt and
   prints the manual fallback — relay it.
3. After the run, verify only **allowed** files changed (use `git status` and the
   saved diff). Flag loudly any do-not-modify file touched, any HEAD move
   (commit), or any out-of-scope change.
4. Report: files changed, deviations from plan, tests run + real results,
   and the diff path.

Next step to tell the user: `/ai-review-diff-with-codex $ARGUMENTS`.

Claude does **not** implement the feature code here — Cursor CLI does. Do not commit.
