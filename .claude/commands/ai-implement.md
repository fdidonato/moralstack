---
description: Hand the approved plan to a Claude Sonnet sub-agent for implementation; collect the diff
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Read, Edit, Write, Grep, Glob, Bash, Agent
---

Approved plan: **$ARGUMENTS**.

Preconditions: the plan has been reviewed by Codex and has **no unresolved
BLOCKING items** (`/ai-review-plan-with-codex` already run). If not, stop and run
the review first.

You are the **orchestrator** here — you prepare the handoff and verify the
result, but you do **not** write the feature code yourself. A separate Claude
**Sonnet** sub-agent (`claude-implementer`) is the implementer, running in an
isolated context so the plan→implement split stays honest (PROJECT_SPEC).

Steps:
1. Read the plan (`$ARGUMENTS`) and the matching Codex plan review
   (`ai/reviews/codex-plan-review-*.md`). If the review verdict is `BLOCK` or has
   unresolved BLOCKING items, stop and send it back to planning.
2. Write a complete handoff to `ai/handoffs/<slug>-handoff.md` (slug =
   `$ARGUMENTS` basename without extension), building on
   `ai/prompts/claude-implementation-template.md`. Fill in: context, objective,
   approved plan (inline or referenced), **files allowed to modify**, **files NOT
   to modify**, invariants (PROJECT_SPEC §5 in play + how to keep them),
   checklist, required tests, acceptance criteria, risks, and the required output
   format.
3. Snapshot HEAD (`git rev-parse HEAD`) so you can detect any commit afterwards.
   Then launch the implementer sub-agent:
   `Agent(subagent_type: "claude-implementer", model: "sonnet", prompt: "Implement the handoff at ai/handoffs/<slug>-handoff.md. Read it in full first, implement only what it allows, and return your required-output report.")`.
   Save the sub-agent's returned report to
   `ai/handoffs/<slug>-implementation-report.md` for the audit trail.
4. Collect the post-implementation diff to `ai/reviews/diff-after-<slug>-<ts>.md`
   via `scripts/ai/collect_git_diff.ps1 -OutPath <path>` (bash:
   `collect_git_diff.sh`; ts = `date +%Y%m%d-%H%M%S`). It never commits.
5. Verify only **allowed** files changed (`git status --short` + the saved diff).
   Flag loudly any do-not-modify file touched, any HEAD move (a commit — the
   sub-agent must not commit), or any out-of-scope change.
6. Report: files changed, deviations from plan, tests run + real results (from
   the sub-agent report), the handoff path, and the diff path.

Next step to tell the user: `/ai-review-diff-with-codex $ARGUMENTS`.

You do **not** implement the feature code here — the `claude-implementer`
sub-agent does. Do not commit.
