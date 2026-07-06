---
description: Have Codex independently review a plan; integrate blocking feedback into a revised plan
argument-hint: <path to ai/plans/plan.md>
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Skill
---

Plan to review: **$ARGUMENTS** (a file under `ai/plans/`).

This uses the official OpenAI Codex Claude Code plugin's own `codex:rescue`
entry point (`github.com/openai/codex-plugin-cc`) — not a hand-rolled script.
If it is not installed, tell the user to run `/plugin install codex@openai-codex`
and stop; do not fabricate a review.

Steps:
1. Read `ai/prompts/codex-plan-review-template.md` (the review rubric and
   required output structure) and `$ARGUMENTS` (the plan itself).
2. Compose the review request: the template's content verbatim, followed by:
   - "This is a READ-ONLY review. Do not modify, create, or delete any file."
   - "Read the plan yourself at `$ARGUMENTS` before judging it — do not rely on
     any summary of it given here."
   - Repo-root framing: this is the MoralStack governance engine; check the
     plan against the invariants in `PROJECT_SPEC.md` section 5 and
     `.claude/rules/` (decision/generation separation, hard-signal supremacy,
     prompt transparency, governed delivery, observability best-effort).
3. Invoke `Skill(skill: "codex:rescue", args: "--wait --fresh <composed request>")`.
   - `--wait` runs it synchronously; `--fresh` skips the resumable-thread
     prompt since this is a one-shot review.
   - Do not add `--write` — the read-only phrasing above is what keeps the
     Codex sandbox read-only per the plugin's own `codex-cli-runtime` skill.
   - Return exactly what that Skill call produces; do not paraphrase it away.
4. Save Codex's verbatim response to
   `ai/reviews/codex-plan-review-<slug>-<timestamp>.md` and the exact composed
   request to `ai/prompts/generated-codex-plan-review-<slug>-<timestamp>.md`
   (slug = `$ARGUMENTS` basename without extension; timestamp = `date +%Y%m%d-%H%M%S`)
   so the review stays reproducible and auditable.
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
