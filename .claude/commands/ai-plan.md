---
description: Analyze the codebase and produce a reviewed-ready technical plan under ai/plans/
argument-hint: <feature | bug | refactor description>
allowed-tools: Read, Grep, Glob, Bash, Write, Agent
---

You are the **orchestrator**. The user request is: **$ARGUMENTS**

Goal: produce a detailed technical plan under `ai/plans/`. Do **not** modify
application code in this command (PROJECT_SPEC §6; Claude does not implement
features unless explicitly asked).

Steps:
0. **Pick the lane** (`docs/ai/AGENTIC_WORKFLOW.md` → "Lanes"). Both lanes keep
   the plan review, the diff review and the pre-commit-verifier gate; they differ
   only in how much mapping/synthesis is delegated.
   - **lean** — only if **every** file the change touches is on the lean
     allowlist in `docs/ai/AGENTIC_WORKFLOW.md` → "Lanes" (harness files,
     `tests/harness/**`, `docs/ai/**`) and the change has a single viable design.
   - **full** — everything else, and always when the change touches
     `moralstack/**`, any other `tests/**`, a persisted payload or public API, or
     a PROJECT_SPEC §5 invariant (including its text in `.claude/rules/**`,
     `PROJECT_SPEC.md`, `CLAUDE.md`). When in doubt, full.
   Collect every decision the user must take in **one** question round, before
   planning starts.
1. **full only:** launch the **codebase-cartographer** agent to map the area the
   request touches (modules, entry points, data flows, tests, risks,
   dependencies). Read-only, evidence-cited. In the lean lane the planner maps
   the area itself.
2. Launch the **architect-planner** agent (with the map, if any) to design the
   change.
3. **full only:** launch the **test-strategist** agent to design the test
   strategy for it. In the lean lane the planner writes the tests section.
4. Merge their outputs into a single plan file
   `ai/plans/<short-task-slug>.md` using the architect-planner structure (goal,
   current/target behavior, assumptions, constraints, design, alternatives,
   files to modify, tests, risks, acceptance criteria, checklist, rollback),
   with the test strategy folded into the "Tests to add / modify" section.
   The plan's first line after the title is `Lane: full` or `Lane: lean`.
   Keep the plan at or under ~20 KB: it is re-read by the reviewer and by every
   implementer slice. Nothing that affects correctness is cut to fit — move
   background and nice-to-haves to a "Follow-ups" section instead.
5. Print the plan path and a 5-line summary. Note any PROJECT_SPEC §5 invariant
   the plan touches.

Next step to tell the user: review the plan with the independent
`adversarial-reviewer` sub-agent via `/ai-review-plan ai/plans/<slug>.md`.

Do not run the implementation. Do not commit.
