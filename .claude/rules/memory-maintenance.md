---
paths:
  - "docs/CODEBASE_FACTS.md"
  - ".claude/session-diary.md"
  - ".claude/.docs-stub.md"
  - ".claude/.context-snapshot.md"
---
# Memory maintenance contract

MoralStack's memory has two tiers with a **one-way gate** between them. The gate
is a verification act, never an automatic copy. This rule makes the split
explicit so the memory maintains itself with few prompts (see
`docs/ai/MEMORY_LIFECYCLE.md`).

## Tier 1 — staging (auto-captured, always UNVERIFIED)

Written by hooks, best-effort, gitignored, never authoritative:

| Artifact | Written by | Holds |
| --- | --- | --- |
| `.claude/session-diary.md` | `session_end.py` (SessionEnd) | Append-only per-session digest: files edited, verify outcome, reason. |
| `.claude/.context-snapshot.md` | `precompact_snapshot.py` (PreCompact) | Pre-compaction context tail, re-injected on resume/compact. |
| `.claude/.docs-stub.md` | `stop_gate.py` (Stop docs-gate) | Touched symbols → likely docs targets. |

These are **inputs to verification**, not conclusions. A hook may only *stage*;
it must stay fail-open and must never write into a verified ledger. This is the
persistence-side expression of PROJECT_SPEC **§4** (facts vs hypotheses).

## Tier 2 — verified ledgers (promotion is a human/agent act)

| Ledger | Gate to enter |
| --- | --- |
| `docs/CODEBASE_FACTS.md` → **Verified facts** table | Re-read the cited code **now**; cite `path:line`. Only code-verifiable claims, including defects: a verified defect is a fact, marked **NOT fixed** until it is. |
| `docs/CODEBASE_FACTS.md` → **Conditionally verified / deployment assumptions** | A code-verified component plus a part only an external system, deployment config or a test run can confirm (benchmark measurements, pass counts). |
| `docs/CODEBASE_FACTS.md` → **Future work / known gaps** | `**Hypothesis — …**` rows for anything plausible-but-unverified or verifiable only against external systems, plus open work items such as the fix of a verified defect. |

Promotion rules (mirror PROJECT_SPEC §4 and §8):

- **Never auto-promote.** No hook, and no unverified staging line, may land a row
  in the Verified facts table. Promotion requires reading the supporting code in
  the current session and citing `path:line`.
- **Code wins (§9).** If a staged claim contradicts the code, the code is right —
  drop or correct the claim, do not promote it.
- **External-only claims never enter the Verified facts table.** Anything
  verifiable only against a live LLM, an external runner, or deployment config
  (e.g. the 84-question governance benchmark, COMPL-AI runs) stays a
  `**Hypothesis — …**` row, or goes to Conditionally verified once measured and
  tied to a code-verified component.
- **A verified defect is a fact.** Record it in the Verified facts table with its
  `path:line`, marked **NOT fixed** until the fix lands; an unverified one is a
  `**Hypothesis — …**` row (PROJECT_SPEC §9).
- **Docs move in the same change (§8).** Promoting a fact updates
  `docs/CODEBASE_FACTS.md` (and `docs/MORALSTACK_CODEBASE_INDEX.md` if a module or
  flow changed) in the same edit.

## The prune step

Staging is append-only and grows unbounded. At the end of a work cycle, the
`memory-curator` agent (`.claude/agents/memory-curator.md`) reads staging,
verifies each candidate against the code, promotes what qualifies, and **prunes**
the staged lines that were promoted or proven stale. Running it is on-demand —
promotion must never be triggered by a hook.
