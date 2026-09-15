---
description: Have the independent adversarial-reviewer sub-agent review a plan; integrate blocking feedback into a revised plan
argument-hint: <path to ai/plans/plan.md>
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Agent
---

Plan to review: **$ARGUMENTS** (a file under `ai/plans/`).

The reviewer is the **adversarial-reviewer** sub-agent
(`.claude/agents/adversarial-reviewer.md`): read-only tools, a model pinned in
its own frontmatter that differs from the implementer's, and a fresh isolated
context — it has not seen this plan being written, which is the point of the
review. Do not review the plan yourself and present it as the sub-agent's. If
the sub-agent cannot be launched (e.g. `Agent type 'adversarial-reviewer' not
found`), use the headless path in `docs/ai/REVIEW_GUIDE.md`
(`claude -p --agent adversarial-reviewer ...`) with the same saved request,
verbatim save and `Reviewer model:` check — otherwise say so and stop; never
fabricate a review.

Steps:
1. Read `ai/prompts/plan-review-template.md` (the review rubric and required
   output structure) and `$ARGUMENTS` (the plan itself).
2. Compose the review request: the template's content verbatim, followed by:
   - "This is a READ-ONLY review. Do not modify, create, or delete any file."
   - "Read the plan yourself at `$ARGUMENTS` before judging it — do not rely on
     any summary of it given here."
   - Repo-root framing: this is the MoralStack governance engine; check the
     plan against the invariants in `PROJECT_SPEC.md` section 5 and
     `.claude/rules/` (decision/generation separation, hard-signal supremacy,
     prompt transparency, governed delivery, observability best-effort).
3. Save the exact composed request to
   `ai/prompts/generated-plan-review-<slug>-<timestamp>.md` (slug =
   `$ARGUMENTS` basename without extension; timestamp = `date +%Y%m%d-%H%M%S`)
   **before** launching, so the prompt stays traceable even if the run fails.
   Then invoke the reviewer:
   `Agent(subagent_type: "adversarial-reviewer", description: "Review plan <slug>", prompt: "<composed request>")`.
   - Do **not** pass `model:` — the agent definition pins the reviewer model;
     overriding it here would collapse the model separation.
   - Wait for it to complete and return exactly what it produces; do not
     paraphrase it away.
4. Save the sub-agent's verbatim response to
   `ai/reviews/plan-review-<slug>-<timestamp>.md` (same slug and timestamp as
   the prompt file) so the review stays reproducible and auditable. Its first
   line must be `Reviewer model: <id>`; if it is missing, or names the same
   model as the implementer, say so loudly — the independence claim is void.
5. Present the verdict (`APPROVE` / `APPROVE_WITH_CHANGES` / `BLOCK`) with
   findings classified `BLOCKING` / `NON_BLOCKING` / `SUGGESTION` / `QUESTION`
   per `docs/ai/REVIEW_POLICY.md`. Never hide a blocker.
6. If the verdict is **BLOCK** or there are unresolved **BLOCKING** items:
   update the plan file in place to address them (you may re-engage
   architect-planner / test-strategist), and note in the plan what changed and
   why. Then say the plan should be re-reviewed.
7. If **APPROVE** / **APPROVE_WITH_CHANGES** with only non-blocking items: fold
   the agreed non-blocking fixes into the plan, mark it approved, and tell the
   user the next step:
   `/ai-implement ai/plans/<slug>.md`.

Do not implement the change. Do not commit.
