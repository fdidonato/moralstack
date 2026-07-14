# Representative scenarios

URLs are **resolved by** `scripts/scenarios.py` from the observability DB and
written to `.claude/ui-loop/runtime/scenarios.json`. Never hand-write a URL and
never claim a scenario the database does not prove: a fabricated PASS is worse
than an honest `NOT_AVAILABLE`, because it silently invalidates every later score.

Each available scenario is checked at **1440×900** and **390×844**.

## Single request

| ID | Scenario | Evidence the UI must convey |
|---|---|---|
| S1 | Normal complete, fast path | benign risk, no deliberation cycles, delivered output |
| S2 | Safe complete | the applied bounds/safeguards, and the delivered safe answer |
| S3 | Refuse | refusal reason and redirection; no implication that an answer was generated |
| S4 | Multi-cycle deliberation | at least one revision, and an explicit stop reason |
| S5 | Parallel modules | a verified parallel tier, not rendered as a sequence |
| S6 | DCCL match reused as delivery | compliance match **plus** an authoritative reuse event |
| S7 | DCCL match without reuse | internal/speculative consumption, clearly distinguished from S6 |
| S8 | Degraded / fail-closed delivery | that the pipeline did NOT produce a governed answer — never rendered as a normal completion |
| S9 | Skipped or deferred module | the reason, and no implication of execution |
| S10 | Calibration guard applied | raw estimator evidence and the guard-capped result distinguishable (llm_calls: module=risk, action=calibration_guard) |

## Conversation

| ID | Scenario | Evidence the UI must convey |
|---|---|---|
| C1 | Stable benign conversation | ordered turns, inherited context, stable posture |
| C2 | Escalation across turns | the change in risk/posture/action, with provenance |
| C3 | Cached state reuse | cached-from-turn (or equivalent) provenance |
| C4 | Turn re-deliberated despite prior state | why the ledger fast path was *not* applied, and what was recomputed |
| C5 | Mixed final actions | at least two different delivered actions in one conversation |
| C6 | Turn navigation | the current turn stays obvious while detail is open |

## Tasks to attempt on every scenario

1. State the authoritative delivered action **and its source**.
2. Explain the primary causal reason in plain language.
3. Locate the canonical codes and the raw trace evidence.
4. Identify executed / parallel / skipped / deferred / synthetic / reused work.
5. Separate pre-delivery governance from final proxy delivery.
6. For conversations: say what changed since the previous turn, and why.

A task is `PASS` only if a competent reviewer who has never seen the trace can do
it from the rendered page alone.

## Evidence rules

- record the page URL and the request/conversation id;
- capture desktop and mobile screenshots under `.claude/ui-loop/artifacts/`
  (never committed);
- record browser-console errors and accessibility-tree observations;
- never store credentials or sensitive prompt content in a report.
