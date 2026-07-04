# Review policy — what blocks, what doesn't, when to proceed

This policy governs how Codex's findings (plan review and diff review) gate the
workflow. `/ai-review-plan-with-codex` and `/ai-review-diff-with-codex`
classify every finding into one of four buckets and **never hide a blocker**.

## Classification

- **BLOCKING** — must be fixed before advancing. Includes:
  - any finding that breaks a MoralStack invariant (PROJECT_SPEC §5 /
    `.claude/rules/`) — especially anything that makes governance fail **open**;
  - a correctness bug or regression in the changed behavior;
  - a missing test for new/changed safety-relevant behavior;
  - a security defect (input not validated, secret exposure, broken authz);
  - a change to a public API or persisted payload (DB row, JSONL envelope,
    emitted event) that the plan did not sanction;
  - the implementation deviating from the approved plan in scope or contract.
- **NON_BLOCKING** — should be addressed, but does not gate: a smaller bug with
  low blast radius, a weak (not missing) test, a non-safety performance issue.
- **SUGGESTION** — optional improvement: naming, minor readability, an
  alternative the author may decline with a one-line reason.
- **QUESTION** — needs a human/Claude answer before the item can be classified;
  treat as blocking until answered if it concerns an invariant.

## Codex verdicts → action

| Verdict | Plan stage | Diff stage |
| --- | --- | --- |
| `APPROVE` | Proceed to implementation | Proceed to finalize |
| `APPROVE_WITH_CHANGES` | Fold non-blocking changes into the plan, then proceed | Apply non-blocking fixes (new Cursor handoff) or accept with noted follow-ups, then finalize |
| `BLOCK` | **Stop.** Revise the plan, re-run plan review | **Stop.** Fix via a new Cursor handoff, re-run diff review |

## Gates

- **Back to the plan** when: a plan review is `BLOCK`; or a diff review reveals
  the plan itself was wrong (not just the implementation).
- **Block implementation** when: the plan has unresolved BLOCKING items, or a
  required invariant cannot be preserved — surface to the user (PROJECT_SPEC §5).
- **Block finalize/merge** when: the diff review has any BLOCKING item, or the
  pre-commit-verifier gate (`python -m pytest` + `pre-commit run -a`) is not
  green. `final-integrator` returns NEEDS_FIXES or BLOCKED, never a false READY.

## Non-negotiables

- No weakening, skipping, or deleting tests to pass review (PROJECT_SPEC §7).
- No `--no-verify`, no force-push, no auto-commit (PROJECT_SPEC §9; enforced by
  `guard_dangerous_git.py`).
- Claude integrates and reports faithfully; Codex is the reviewer of record.
