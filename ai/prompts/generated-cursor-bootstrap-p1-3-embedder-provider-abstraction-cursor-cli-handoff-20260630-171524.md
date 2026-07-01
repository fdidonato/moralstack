You are running headless as the implementer. Read the handoff file at:
  C:\Users\fdidonato\Documents\progetti\moralstack\ai\handoffs\p1-3-embedder-provider-abstraction-cursor-cli-handoff.md
Then implement EXACTLY and ONLY what that handoff approves. Hard rules:
- Modify ONLY files listed under the handoff's allowed-files section.
- Do NOT touch files listed as do-not-modify, and do NOT refactor opportunistically.
- Do NOT weaken, skip, or delete tests. Add/adjust tests as the handoff requires.
- Honor every invariant the handoff cites (MoralStack PROJECT_SPEC section 5).
- Run the verification commands the handoff lists; report their real results.
- If the plan is ambiguous or you hit a blocking architectural problem, STOP and
  report the blocker instead of working around it.
- Do NOT git add, commit, push, or delete files outside your own edits.
At the end, output: files modified, tests added, commands run + results,
deviations from the plan, and residual problems.
