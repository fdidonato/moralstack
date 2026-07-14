# Scoring rubric — 100 points

Score only a *verified* iteration. Every deduction cites observable evidence: a
URL, a template line, a screenshot, an accessibility-tree node. A deduction
without evidence is not a deduction, and a score without deductions is not a
score.

## 1. Decision comprehension — 25

- 8 — the authoritative delivered action is unmistakable
- 5 — the authoritative source of the delivered text is unmistakable
- 5 — a plain-language causal reason precedes the raw detail
- 4 — risk category, score, and path are visible and correctly scoped
- 3 — excluded alternative actions are understandable where relevant

**Automatic P0:** the UI materially misstates what was delivered, or why.

## 2. Deliberative-process readability — 20

- 5 — the causal sequence is understandable
- 4 — parallel execution is distinguished from sequence
- 4 — executed / skipped / deferred / synthetic / reused are visually distinct
- 4 — cycles, revisions, convergence and stop reason are understandable
- 3 — policy bounds, principles, calibration and reason codes have clear roles

## 3. Conversation continuity — 15

- 5 — turn order and current turn are immediately clear
- 4 — inherited / cached / refreshed state has visible provenance
- 3 — posture, risk and action changes across turns are scannable
- 3 — request-level detail is reachable without losing conversation context

## 4. Progressive disclosure and density — 15

- 5 — summary, explanation and raw evidence form three clear levels
- 4 — the default view has no duplicated or competing summaries
- 3 — expansion controls are predictable and labelled
- 3 — raw prompts, responses, JSON and trace codes remain reachable

## 5. Accessibility and responsiveness — 15

- 4 — semantic structure and accessible names are coherent
- 3 — keyboard navigation works and focus is visible
- 3 — status is never colour-only; contrast is credible
- 3 — 390×844 preserves meaning and access
- 2 — tables, long identifiers and dense traces degrade gracefully

## 6. Technical stability — 10

- 4 — existing and new UI tests pass
- 2 — no template or browser-console regression
- 2 — no governance, API or schema behaviour changed
- 1 — no file outside the loop's mandate was touched
- 1 — iteration evidence and state are complete

## Severity

- **P0** — materially false governance or delivery meaning
- **P1** — a primary reviewer task cannot be completed reliably
- **P2** — substantial friction, accessibility, responsive or density defect
- **P3** — polish or consistency

## Gates

`COMPLETE` requires all of: score ≥ 90; ≥ 90 for 3 consecutive verified
iterations; no unresolved P0/P1; every required scenario `PASS`; every
verification gate green; no regression. `state.py` computes this — do not assert
it yourself.

`PLATEAU`: three successive verified iterations gain less than one point in
total, and no evidence-backed P0/P1 hypothesis remains.
