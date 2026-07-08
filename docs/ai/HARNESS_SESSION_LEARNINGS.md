# Harness session learnings — recurring bottlenecks of the agentic cycles

**Evidence-based** reconstruction (git + working tree) of past agentic cycles, to
evolve the `.claude/` harness and the memory system. Every bottleneck is cited with
`path:line` or a commit hash, and classified by remediability: **deterministic
hook/rule** vs **guidance only**.

Real sources used: `git log`/`git tag`; `ai/plans/*`, `ai/reviews/*`, `ai/handoffs/*`
(+ `*-implementation-report.md`); `.claude/.session-edits.json`;
`.claude/hooks/*`; `docs/refactoring_diary.md`; the "Hypotheses/Future work" section of
`docs/CODEBASE_FACTS.md`; `docs/ai/AGENTIC_WORKFLOW.md`, `docs/ai/REVIEW_POLICY.md`.

Method note: `.claude/.instructions-loaded.log` and `.claude/.session-edits.json` are
runtime-local (potentially gitignored) — verified at runtime before use.
`.session-edits.json` reflects **only the last session** (keyed by `session_id`); it is
not a history.

---

## Release picture (actual cadence)

From `git tag` + `git log --simplify-by-decoration`: 13 tags from `v0.1.0-alpha`
(2026-03-27) to `v0.7.0` (2026-07-06). Tight cadence (v0.6.0→v0.6.1 same day,
2026-06-25; v0.7.0 ~11 days later). The last 4 features (token-accounting,
remove-persistence, prompt-caching, unify-retrieval) all went through the
plan→codex-review→implement→diff-review→fixpass cycle. It is from **these 4** that the
clearest patterns emerge.

---

## Recurring bottlenecks

### B1 — Codex plan-review loop: 3–7 iterations before APPROVE
**Evidence.** File count of `ai/reviews/codex-plan-review-<task>-*.md`:
- `token-accounting-p0-2b-p11`: **7** reviews (2026-07-01 14:48 → 07-02 12:00, over 2 days).
- `unify-constitution-retrieval-single-pass`: **5** (07-06 17:24 → 07-07 11:55).
- `prompt-caching-strict-json`: **4** (07-06 10:17 → 11:32).
- `remove-deprecated-persistence-package`: **3** (07-03 17:17 → 18:00).

Correlation with plan size: `ai/plans/token-accounting-p0-2b-p11.md` = **247 KB**
(the largest) required the most rounds. Huge plans = less convergent review.

**Classification: guidance + light structural check (partially deterministic).**
The *content* of Codex's findings is semantic (not gate-able), but the plan's
**structure** is: a deterministic plan-lint can check that the plan contains the
sections Codex systematically asks for (invariant mapping §5, per-route observability
impact, byte-equality impact, test assertion-strength) before spending a Codex round.
It reduces the rounds, it does not zero them.

### B2 — Recurring diff-review fixpass (2 of 4 tasks required a corrective pass)
**Evidence.**
- `unify`: diff-review **BLOCK** →
  `ai/reviews/codex-diff-review-unify-constitution-retrieval-single-pass-20260707-142836.md:4`.
  Cause: `RELEVANT_PRINCIPLES_RETRIEVED` emitted only on fast/deliberative, **dropped** on
  COMPLIANCE_FAST_PATH / REFUSE / benign / SAFE_COMPLETE (`:12`). Fixpass:
  `ai/handoffs/unify-constitution-retrieval-single-pass-fixpass-implementation-report.md`.
- `prompt-caching`: **APPROVE_WITH_CHANGES** with required fix →
  `ai/reviews/codex-diff-review-prompt-caching-strict-json-20260706-125903.md:31-34`
  (hindsight batch loses the static base-framing; quick-check test not snapshotted
  byte-for-byte). Fixpass:
  `ai/handoffs/prompt-caching-strict-json-fixpass-implementation-report.md`.
- `remove-persistence`: **APPROVE_WITH_CHANGES**, missing `call_count` on the relocated
  patch targets (`codex-diff-review-remove-deprecated-persistence-package-20260704-102408.md:27`).

**Common theme of the Codex blockers** (recurs in 3 of 3 reviews): the implementer covers
the **happy path** but misses (a) **coverage of all terminal routes** for the emitted
events, and (b) the **assertion strength** the plan required (literal snapshots,
`call_count`, per-route tests).

**Classification: guidance (dominant) + weak test-presence hook.** "Emit the event on
every route" is semantic → not deterministically gate-able. The lever is a **handoff with
explicit per-route/per-assertion acceptance criteria** that `claude-implementer` must
self-verify, plus optionally a check that a new observability event has a dedicated test.

### B3 — Defect class "observability: complete, non-duplicated emission"
**Evidence (recurs across git history, not just the last 4 features).**
- `fe0328c` / `ffeb33e` "DCCL Commit Fix L: Duplicate constitution retrieval ... shown at
  same temporal row on UI" (duplicated event in the UI).
- `b7f8088` "DCCL Commit Fix A ... persist PROXY_OUTPUT_FINALIZED event" (event not
  persisted).
- `unify` diff-review B2 (event dropped on 4 routes).
- Known latent-wiring in `docs/CODEBASE_FACTS.md` (Future work): `X-Moralstack-Cached-From`
  header never emitted (`cached_from_decision_id` never set); `core` domain not
  normalized at the initial upsert (`controller.py:2078-2086`).

This is a **recurring product defect** (emit-once, on-every-route, no-duplicates), not pure
harness friction. `.claude/rules/observability.md` already exists but covers "best-effort /
swallow", not the "emit once on every terminal route" contract.

**Classification: guidance (extend the rule) + possible test-presence check.**

### B4 — Flaky test that muddies the "green" signal: SQLite "database is locked"
**Evidence.** `test_persistence_load.py::test_concurrency_emitted_equals_persisted` fails
under full-suite concurrency ("database is locked"); passes standalone and on rerun →
`ai/handoffs/unify-constitution-retrieval-single-pass-fixpass-implementation-report.md:37-44`
(run 1: 1 failed / 2086 passed; run 2: 2087 passed). It costs **double runs** and ambiguity
over green. Root cause: SQLite write contention, not a regression.

**Classification: real test fix (outside harness) OR a deterministic allowlist in the
verifier.** The correct fix is DB isolation / `busy_timeout` / test serialization — but §7
forbids weakening tests. In harness: the `pre-commit-verifier` (or `stop_gate`) could
recognize this **known, specific** flake and not treat a single occurrence as red, with a
targeted re-run. Risk: masking real regressions → it must be kept tight (exact test name +
"database is locked" message + rerun-passes).

### B5 — docs-gate loophole: "tests touched" satisfies the gate without docs
**Evidence.** `stop_gate.py:176`:
`needs_docs = bool(behavior) and not (docs_touched or tests_touched)`. Touching a behavior
file **+ a test** disables the documentation gate, even without any doc update. It is *by
design* (the reason string says "or touch the behavior-locking tests if that is the right
place", `:184`), but it is a wide escape hatch: the vast majority of behavior changes also
touch tests.

**Classification: deterministic design tension (hook).** Remediable by tightening the gate
(e.g. requiring docs when behavior changes *and* tests alone are not enough), but with a
risk of false positives. To be decided with the user (precision/friction trade-off).

### B6 — DRY duplicates kept in sync only by a test (latent drift)
**Evidence.** `_HINDSIGHT_BASE_FRAMING` is a literal copy of `HINDSIGHT_SYSTEM_PROMPT`
introduced for a circular import, kept in sync only by a test →
`ai/handoffs/prompt-caching-strict-json-fixpass-implementation-report.md:46-48`. Recurring
theme: `4b2ab13` "single source of thruth for codex and claude".

**Classification: guidance only / follow-up decision.** Not gate-able; it is an
architectural debt to flag, not to hook.

### B7 — [RESOLVED] Cursor CLI dropped mid-run
**Evidence.** `ai/handoffs/cursor-run-token-accounting-...-20260702-161614.log` contains
**only** "Connection lost, reconnecting ... / Retry attempt" lines. Commit `dfe2460`
"chore(ai): sostituisci cursor con claude sonnet" replaced Cursor with the
`claude-implementer` sub-agent. **Already resolved by the harness evolution** — cited for
completeness and as a caution: the unreliable external implementer was internalized.

### B8 — Proliferation of unpruned ai/ artifacts + working-tree clutter
**Evidence.** SessionStart brief: "117 change(s): 1 staged, 23 modified, 93 untracked";
uncommitted root notes (`ANALISI_TECNICA_MORALSTACK.md`, `claude_upgrade_plan.md`,
`codex_upgrade_plan.md`). `ai/reviews/` holds ~30 files, several `diff-after-*.md` at
100–240 KB, many stale. No archiving/pruning convention. Noise that competes with the
signal in the `session_start` brief.

**Classification: guidance + optional .gitignore/archive convention.** Low risk, high
hygiene return; no blocking hook needed.

---

## Summary: where a hook/rule actually helps

| # | Bottleneck | Cost | Deterministic? | Harness lever |
|---|---|---|---|---|
| B1 | Plan-review loop (3–7 rounds) | High (days) | Partial | structural plan-lint + guidance on plan size |
| B2 | Diff-review fixpass (2/4) | High | No (semantic) | handoff with per-route/assertion AC; test-presence check |
| B3 | Observability emit-once/no-dup | Medium (recurs) | No | extend `rules/observability.md`; test-presence check |
| B4 | SQLite "db is locked" flake | Medium (double runs) | Yes (tight) | known-flake allowlist in the verifier + real test fix |
| B5 | docs-gate loophole (tests≡docs) | Medium (audit) | Yes | tighten `stop_gate.py:176` (trade-off to be decided) |
| B6 | DRY drift sync-by-test | Low/latent | No | guidance only / follow-up |
| B7 | Unstable Cursor CLI | — | — | already resolved (`dfe2460`) |
| B8 | ai/ + root artifact clutter | Low | Partial | pruning/archive convention; guidance |

**Suggested priorities for the next phases** (to confirm with the user): B4 and B5 are the
cleanest candidates for a **deterministic** intervention (low risk, measurable effect);
B1/B2/B3 pay off more as **structured guidance** (handoff/plan templates + rule extensions)
than as blocking hooks — consistent with the "fail-open hook, never wedge a turn" principle.

---

## Phase 2 — implemented harness interventions (2026-07-07)

A different work set from the B-items above (which remain follow-ups): focused on the
**hook mechanics**. All fail-open; no product `moralstack/`/`tests/` file touched →
84/84 benchmark unchanged by construction. Tests in `tests/harness/` (63).

| Gap | Intervention | File |
|---|---|---|
| Redundant Stop verify | skip on `stop_hook_active` + dedup by content fingerprint (`.last-verified.json`) | `.claude/hooks/stop_gate.py` |
| Docs nudge repeated across chains | per-session counter with cap (`.nudge-count.json`, `MSTACK_DOCS_NUDGE_CAP`) | `.claude/hooks/stop_gate.py` |
| Context lost at compaction | PreCompact snapshot (`async`) + reload in SessionStart on `source∈{compact,resume}` | `precompact_snapshot.py`, `session_start.py` |
| No session diary | SessionEnd → UNVERIFIED digest in `.claude/session-diary.md` (staging, §4) | `session_end.py` |
| Docs-gate with no help | touched-symbols stub → doc targets (`.docs-stub.md`) | `.claude/hooks/stop_gate.py` |
| Manual context prompts | UserPromptSubmit keyword-gated injects snapshot + active plans | `user_prompt_submit.py` |

Full hook + marker inventory: `.claude/hooks/README.md`. Follow-ups not yet done:
B4 (real SQLite flake fix), B5 (docs-gate loophole, left unchanged by choice), B1/B2/B3
(structured plan/handoff guidance).

---

## Phase 4 — self-maintaining memory (2026-07-08)

The capture→staging→promote→prune memory cycle was formalized so the memory maintains
itself with few prompts. Contract in `.claude/rules/memory-maintenance.md`, narrative in
`docs/ai/MEMORY_LIFECYCLE.md`, promotion/prune performed on-demand by the `memory-curator`
agent (`.claude/agents/memory-curator.md`). The load-bearing rule: no hook and no staging
line on its own may add a row to the Verified facts table — promotion always requires
reading the code and citing `path:line` (PROJECT_SPEC §4).
