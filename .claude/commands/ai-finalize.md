---
description: Synthesize plan + reviews + diff into a final auditable status (READY / NEEDS_FIXES / BLOCKED)
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Bash, Read, Grep, Glob, Agent
---

Plan under finalization: **$ARGUMENTS**.

Steps:
1. Launch the **final-integrator** agent. It reads: the plan
   (`$ARGUMENTS`), the Codex plan review and diff review
   (`ai/reviews/codex-*-review-*.md`), the Cursor handoff and run log
   (`ai/handoffs/*`), and the collected diff
   (`ai/reviews/diff-after-cursor-*.md`).
2. Before declaring READY, ensure verification actually happened: if MoralStack
   code/tests changed, run (or have the user run) the **pre-commit-verifier**
   agent — full `python -m pytest` + `pre-commit run -a` — and cite the real
   outcome. Do not claim green you did not observe (PROJECT_SPEC §10).
3. Produce the final synthesis: what was planned, what Codex contested, what was
   corrected, what Cursor CLI implemented, what the diff shows, what Codex found
   in the diff review, the verification result, and the final status:
   - **READY** — criteria met, no BLOCKING items, verification green;
   - **NEEDS_FIXES** — enumerate the remaining fixes;
   - **BLOCKED** — state the blocker and who must decide.
4. List residual risks and next actions.

Do **not** commit or push — that is the user's decision. State exactly what
remains to be done.
