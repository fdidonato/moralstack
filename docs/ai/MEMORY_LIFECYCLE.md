# Memory lifecycle — how MoralStack's project memory maintains itself

This document exists so a future session understands the memory mechanism
**without it being re-explained**. The contract is enforced by
`.claude/rules/memory-maintenance.md`; this is the narrative.

## Why two tiers

MoralStack is a governance engine — its facts drive refusal behavior and audit
trails. A wrong "fact" is a safety bug. So the memory is split, with a
verification gate between the tiers (PROJECT_SPEC §4):

- **Staging (Tier 1)** — cheap, automatic, always UNVERIFIED. Hooks capture it
  as a side effect of normal work. It can be wrong; it is only ever an *input*.
- **Verified ledgers (Tier 2)** — `docs/CODEBASE_FACTS.md` Verified facts table
  and `docs/MORALSTACK_CODEBASE_INDEX.md`. A claim enters only after someone
  re-reads the code and cites `path:line`. Entry is **never** automatic.

## The cycle

```
        WORK HAPPENS
             │
   hooks capture (automatic, fail-open)
             │
             ▼
   ┌────────────────────────── STAGING (Tier 1, UNVERIFIED) ──────────────────────────┐
   │ session_end.py     (SessionEnd) → .claude/session-diary.md    (per-session digest)│
   │ precompact_snapshot.py (PreCompact) → .claude/.context-snapshot.md (context tail) │
   │ stop_gate.py       (Stop docs-gate) → .claude/.docs-stub.md   (symbols → docs)    │
   └───────────────────────────────────────────────────────────────────────────────────┘
             │
   memory-curator agent  (on-demand, END of cycle — NEVER a hook)
   verify each candidate against the code NOW, cite path:line
             │
      ┌──────┴───────────────────────────────┐
      ▼                                       ▼
  code-verifiable                        not verifiable now
      │                                  / external-only / contradicted
      ▼                                       ▼
  promote → CODEBASE_FACTS                 keep in Hypotheses
  Verified facts (+ INDEX if a            (or drop if code
  module/flow changed)                     contradicts it, §9)
      │
      ▼
  prune the promoted/stale staging lines
```

## Who does what

| Step | Actor | Trigger | Output |
| --- | --- | --- | --- |
| Capture | hooks (`session_end`, `precompact_snapshot`, `stop_gate`) | automatic, on the event | staging files under `.claude/` |
| Re-inject | `session_start.py`, `user_prompt_submit.py` | resume/compact, plan keywords | snapshot back into context |
| Promote + prune | **`memory-curator` agent** | on-demand, end of cycle | Verified facts rows + trimmed staging |

The re-injection step is what lets a later session *use* staging; the promotion
step is what makes a claim *trustworthy*. They are deliberately different actors:
hooks may only stage (fail-open, no judgement); promotion is a judgement act that
reads code.

## The one rule that must not bend

**No hook, and no staging line on its own, may add a row to the Verified facts
table.** Promotion always requires reading the supporting code in the current
session and citing `path:line`. This is why the curator is an on-demand agent and
not a hook: automatic promotion would let an UNVERIFIED digest masquerade as a
verified fact, which is exactly the failure PROJECT_SPEC §4 exists to prevent.

## Running it

Invoke the `memory-curator` agent at the end of a work cycle (e.g. before
declaring a task done, alongside the `pre-commit-verifier`). It reads staging,
promotes what it can verify, prunes what it promoted, and reports what it left as
a hypothesis. It never commits or pushes — the user reviews the resulting doc
diff.

## Related

- `.claude/rules/memory-maintenance.md` — the enforced contract (tiers + gate).
- `.claude/agents/memory-curator.md` — the promotion/prune agent.
- `.claude/hooks/README.md` — the capture hooks and their marker files.
- `docs/ai/HARNESS_SESSION_LEARNINGS.md` — the bottlenecks this loop addresses.
- PROJECT_SPEC §4 (facts vs hypotheses), §8 (docs in the same change), §9
  (code wins on contradiction).
