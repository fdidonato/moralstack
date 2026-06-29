You are running headless as the implementer for MoralStack plan point P2
(audit-grade observability persistence). Read the handoff file at:

  ai/handoffs/p2-persistence-audit-grade-cursor-cli-handoff.md

Then implement EXACTLY and ONLY what that handoff approves. Hard rules:
- Modify ONLY files listed under the handoff's allowed-files section.
- Do NOT touch files listed as do-not-modify (persistence/default.py, cli/shell.py),
  and do NOT refactor opportunistically.
- Do NOT weaken, skip, or delete tests. test_risk_persist_batch.py is REWRITTEN to
  the inverse contract WITH justification (PROJECT_SPEC §7), not deleted.
- Honor every invariant the handoff cites (PROJECT_SPEC §5 — especially #6:
  observability never breaks the request; telemetry best-effort, swallowing
  try/except, never raises into the caller).
- The four v5 blocker-closures are PENDING diff-review validation — implement them
  precisely: (1) route_audit_sync resultful + counted + never-raising; (2) per-mode
  "persisted" semantics (dual=SQLite, file_only=JSONL, not crash-durable);
  (3) exactly-once proxy.request_finalized (UNIQUE index + idempotent upsert);
  (4) single-owner finalize_audit_sync used by BOTH proxy and SDK.
- Run the verification commands the handoff lists; report their REAL results
  (python -m pytest scoped then full; pre-commit). Do not claim green you did not see.
- If the plan is ambiguous or you hit a blocking architectural problem, STOP and
  report the blocker instead of working around it.
- Do NOT git add, commit, push, or delete files outside your own edits.

At the end, output: files modified, tests added, commands run + results,
deviations from the plan, and residual problems.
