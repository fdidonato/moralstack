---
description: Analyze the codebase and produce a reviewed-ready technical plan under ai/plans/
argument-hint: <feature | bug | refactor description>
---

You are the **orchestrator**. The user request is: **$ARGUMENTS**

Goal: produce a detailed technical plan under `ai/plans/`. Do **not** modify
application code in this command (PROJECT_SPEC §6; Claude does not implement
features unless explicitly asked).

Steps:
1. Launch the **codebase-cartographer** agent to map the area the request
   touches (modules, entry points, data flows, tests, risks, dependencies).
   Read-only, evidence-cited.
2. Launch the **architect-planner** agent with that map to design the change.
3. Launch the **test-strategist** agent to design the test strategy for it.
4. Merge their outputs into a single plan file
   `ai/plans/<short-task-slug>.md` using the architect-planner structure (goal,
   current/target behavior, assumptions, constraints, design, alternatives,
   files to modify, tests, risks, acceptance criteria, checklist, rollback),
   with the test strategy folded into the "Tests to add / modify" section.
5. Print the plan path and a 5-line summary. Note any PROJECT_SPEC §5 invariant
   the plan touches.

Next step to tell the user: review the plan with Codex via
`/ai-review-plan-with-codex ai/plans/<slug>.md`.

Do not run the implementation. Do not commit.
