---
description: Run bounded MoralStack UI improvement iterations until the loop reaches a terminal state.
argument-hint: "[iterations to run in this invocation, default 1]"
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Task
---

Run MoralStack UI improvement iterations.

Iterations to run in this invocation: **$1** (default: 1).

Procedure:

1. Check the gate:
   `python .claude/skills/improve-moralstack-ui/scripts/state.py gate`
   Exit code 3 → the loop is terminal (COMPLETE / BLOCKED / PLATEAU /
   MAX_ITERATIONS). Report the status in one sentence, change nothing, stop.

2. Otherwise invoke the `improve-moralstack-ui` skill **once** and let it complete
   a full iteration, including its own state update.

3. If more iterations were requested, return to step 1. Never start a new
   iteration before the previous one has recorded its outcome, and never continue
   past a terminal state.

Report, per iteration: number, status, selected issue, score delta, and either the
commit sha or the rollback/blocker reason.
