---
description: Synthesize plan + reviews + diff into a final auditable status (READY / NEEDS_FIXES / BLOCKED)
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Bash, Read, Grep, Glob, Agent
---

Plan under finalization: **$ARGUMENTS**.

Steps:
1. **Lean lane** (plan's `Lane: lean`): skip the sub-agent and write the
   synthesis yourself, with the same inputs, the same required output structure
   of `.claude/agents/final-integrator.md` and the same evidence rules (cite the
   `suite_record.py check` outcome, never a green you did not observe).
   **Full lane** (or no `Lane:` line): launch the **final-integrator** agent. It reads: the plan
   (`$ARGUMENTS`), the adversarial-reviewer plan review and diff review
   (`ai/reviews/plan-review-*.md`, `ai/reviews/diff-review-*.md`), the handoff
   and implementation report
   (`ai/handoffs/*`), and the collected diff
   (`ai/reviews/diff-after-*.md`).
2. Before declaring READY, ensure verification actually happened, in both
   lanes: run (or have the user run) the **pre-commit-verifier**
   agent — full suite via `python scripts/ai/suite_record.py check` (reuses a PASSED
   record of the same tree state), `run` on a mismatch with the Bash tool
   `timeout: 600000`, plus `pre-commit run -a` — and cite the real outcome. Do not claim green you did not observe (PROJECT_SPEC §10).
3. Produce the final synthesis: what was planned, what the reviewer contested,
   what was corrected, what the implementer produced, what the diff shows, what
   the reviewer found in the diff review, the verification result, and the final
   status:
   - **READY** — criteria met, no BLOCKING items, verification green;
   - **NEEDS_FIXES** — enumerate the remaining fixes;
   - **BLOCKED** — state the blocker and who must decide.
4. List residual risks and next actions.

Do **not** commit or push — that is the user's decision. State exactly what
remains to be done.
