---
description: Have Codex CLI independently review a plan; integrate blocking feedback into a revised plan
argument-hint: <path to ai/plans/plan.md>
allowed-tools: Bash, Read, Edit, Grep, Glob, Agent
---

Plan to review: **$ARGUMENTS** (a file under `ai/plans/`).

Steps:
1. Launch the **codex-review-coordinator** agent to run the Codex plan review:
   `pwsh scripts/ai/run_codex_plan_review.ps1 -PlanPath "$ARGUMENTS"`
   (the script builds the prompt from
   `ai/prompts/codex-plan-review-template.md`, runs `codex exec -s read-only`
   in a read-only sandbox, and saves the report under `ai/reviews/`). If Codex
   CLI is unavailable the script prints the manual command and the generated
   prompt path — relay that, do not fabricate a review.
2. Read the saved review and present the verdict
   (APPROVE / APPROVE_WITH_CHANGES / BLOCK) with findings classified
   BLOCKING / NON_BLOCKING / SUGGESTION / QUESTION. Never hide a blocker.
3. If the verdict is **BLOCK** or there are unresolved **BLOCKING** items:
   update the plan file in place to address them (you may re-engage
   architect-planner / test-strategist), and note in the plan what changed and
   why. Then say the plan should be re-reviewed.
4. If **APPROVE** / **APPROVE_WITH_CHANGES** with only non-blocking items: fold
   the agreed non-blocking fixes into the plan, mark it approved, and tell the
   user the next step:
   `/ai-implement-with-cursor ai/plans/<slug>.md`.

Do not implement the change. Do not commit.
