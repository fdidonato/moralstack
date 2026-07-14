---
name: improve-moralstack-ui
description: Run exactly one bounded, evidence-driven iteration on the MoralStack FastAPI/Jinja observability UI, improving how accurately and quickly a reviewer can read the governance decision and delivery for single requests and multi-turn conversations. Use when asked to improve, audit, or iterate on the MoralStack dashboard UI, its readability, or its auditability.
---

# MoralStack UI improvement — one iteration

Execute **exactly one** iteration and stop. Repetition is the caller's job
(`/ui-loop`), never this skill's.

## Contract

Editable surface:

- `moralstack/ui/**`
- `tests/test_ui_*.py`
- `CHANGELOG.md` (`[Unreleased]` section only — required, see *Commit*)
- `.claude/ui-loop/**` (loop state and evidence)

Everything else is out of bounds — in particular `moralstack/runtime/**`,
`moralstack/orchestration/**`, `moralstack/constitution/**`,
`moralstack/observability/**`, `moralstack/server/**`, `moralstack/prompts/**`.
Governance semantics, thresholds, trace meaning, persistence schema, proxy
behaviour and public API do not change to make the UI easier to build. If the UI
lacks evidence it needs, that is a `DATA GAP` finding in the report, not a
backend edit.

Also: never read `.env`; never push, merge, rebase, force-reset, or delete
branches; never bypass pre-commit hooks (`--no-verify` is blocked by a repo hook
and is not to be attempted); never ask the user a question mid-iteration —
record a genuine blocker instead.

**Aesthetic improvement without improved comprehension is a failed iteration.**

## Read first

1. `CLAUDE.md` → `PROJECT_SPEC.md`, and `.claude/rules/observability.md`,
   `.claude/rules/governed-delivery.md`
2. `.claude/skills/mstack-run/SKILL.md` — the authoritative way to launch the app
3. `reference/GOAL.md`, `reference/RUBRIC.md`, `reference/SCENARIOS.md` (this skill's folder)
4. `.claude/ui-loop/DECISIONS.md` — durable invariants; do not rediscover them
5. the two most recent `.claude/ui-loop/ITERATIONS/iteration-*.md`

## Phase 0 — gate

```bash
python .claude/skills/improve-moralstack-ui/scripts/state.py gate
```

Exit code 3 means the loop is terminal (`COMPLETE`, `BLOCKED`, `PLATEAU`,
`MAX_ITERATIONS`). Report the status in one sentence, edit nothing, stop.

Otherwise claim the iteration:

```bash
python .claude/skills/improve-moralstack-ui/scripts/state.py begin
```

## Phase 1 — preflight, auth, scenarios

```bash
python .claude/skills/improve-moralstack-ui/scripts/preflight.py
python .claude/skills/improve-moralstack-ui/scripts/ui_login.py
python .claude/skills/improve-moralstack-ui/scripts/scenarios.py
```

- `preflight.py` failure → `state.py block --reason <cause>`, stop.
- `ui_login.py` mints a Playwright storage-state from `.env`. It re-mints on every
  iteration because the UI keeps sessions **in memory**, so any restart of
  `moralstack-ui` kills every previous cookie. After it runs, call
  `mcp__playwright browser_close` before the first navigation so the new state is
  loaded. If it exits non-zero the UI is down or misconfigured → block with
  `UI_UNAUTHENTICATED`; never ask for or handle credentials yourself.
- `scenarios.py` resolves real URLs from the observability DB into
  `.claude/ui-loop/runtime/scenarios.json`. Use only those URLs. A scenario marked
  `NOT_AVAILABLE` is recorded as such — it prevents `COMPLETE` but does not
  prevent a useful iteration on the scenarios that exist. Never invent a URL,
  never claim a scenario the DB does not prove.

## Phase 2 — two independent reviews

Launch both subagents and let them return **before** either sees the other's
output; a single biased review is worse than none.

- `moralstack-ui-auditor` — drives the real UI through Playwright at 1440×900 and
  390×844: first-viewport comprehension, hierarchy, affordances, keyboard/focus,
  accessibility tree, progressive disclosure, empty/error states, console errors,
  colour-only encoding.
- `moralstack-ui-semantics-reviewer` — reads `moralstack/ui/app.py` view models and
  the Jinja templates against the persisted trace: can a reviewer reconstruct the
  true causal chain, and does the rendering imply anything false?

Pass both the scenario URLs from `scenarios.json`.

## Phase 3 — select exactly one issue

Rank findings by `severity × user impact × evidence confidence ÷ effort`, then
break ties in this order:

1. misleading or semantically false presentation (P0)
2. the decision and its cause cannot be identified (P1)
3. conversation-state evolution cannot be followed (P1)
4. inaccessible or unusable interaction (P2)
5. density / poor progressive disclosure (P2)
6. visual polish (P3)

Pick one coherent issue (or one tightly coupled cluster). Write the problem, its
evidence, the expected reviewer outcome, and a **falsifiable** hypothesis into
the iteration report *before* any edit.

## Phase 4 — implement the smallest coherent slice

Delegate to `moralstack-ui-implementer`. Keep FastAPI/Jinja; prefer semantic HTML
and reusable classes in `static/css/main.css` over inline styles; add
human-readable labels *beside* canonical codes, never instead of them; keep raw
audit evidence reachable behind progressive disclosure; make causality explicit
rather than merely chronological; add focused `tests/test_ui_*.py` for behaviour
that can regress (follow the existing fixtures in `tests/test_ui_conversation_views.py`).

## Phase 5 — verify

```bash
python .claude/skills/improve-moralstack-ui/scripts/verify.py
```

Then delegate browser and semantic verification to `moralstack-ui-verifier`, which
re-runs the affected scenarios at both viewports and can veto a green script run.

At most **two** remediation passes, each starting from a root-cause diagnosis. If
it still fails:

1. `git restore` only the files this iteration touched;
2. keep the report and the failure evidence;
3. `state.py record --outcome rolled_back ...` (no score is recorded for code that
   no longer exists) or `state.py block --reason ...` for an environmental fault;
4. do not commit failed code.

Never revert a file this iteration did not touch.

## Phase 6 — persist

Write `.claude/ui-loop/ITERATIONS/iteration-NN.md` with these sections:

```markdown
# Iteration NN
## Scenarios used (URLs, availability)
## Baseline evidence
## Auditor findings
## Semantics-reviewer findings
## Selected issue and falsifiable hypothesis
## Implementation and files changed
## Verification (script gates, browser, a11y, semantics)
## Rubric before → after, with per-dimension deductions
## Regressions and remediation
## Decision: committed | rolled back | blocked
## Remaining P0/P1
## Recommended next issue
```

Add to `.claude/ui-loop/DECISIONS.md` only durable invariants or rejected
patterns — not a chronology.

Then record the outcome. The script, not you, decides whether the loop is
terminal:

```bash
python .claude/skills/improve-moralstack-ui/scripts/state.py record \
  --outcome committed --score 74 \
  --active-issue "..." --next-issue "..." \
  --p0 "..." --p1 "..." \
  --scenario S1=PASS --scenario S6=NOT_AVAILABLE \
  --report iteration-03.md --commit <sha>
```

## Commit

Only a verified, passing iteration is committed.

The repo's `changelog-guard` pre-commit hook **rejects any commit that stages a
file outside `.claude/`, `ai/`, `tests/` without `CHANGELOG.md`**. So a UI commit
must include a `CHANGELOG.md` bullet under `[Unreleased]`. Do not bypass the hook.

Stage exactly:

- the touched files under `moralstack/ui/**`
- focused `tests/test_ui_*.py`
- `CHANGELOG.md`

`.claude/ui-loop/**` is git-ignored by design (the repo keeps process artefacts
local) — do not try to add it, and do not commit screenshots, storage state,
prompts, or the observability DB.

```text
ux(ui): iteration NN — <concise verified improvement>
```

## Report back

Iteration number and status; the selected issue; files changed; script gates and
browser result; score delta; commit sha or rollback/blocker; next issue or
terminal reason. Then stop — do not begin another iteration.
