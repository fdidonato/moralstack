# Implementer Handoff — Unify constitution retrieval into a single upstream wave (+ intent-prompt caching refactor)

You are the **MoralStack Implementer** (`claude-implementer`, Claude Sonnet, isolated
context). Implement ONLY the approved plan. No scope creep. Honor every rule in
`ai/prompts/claude-implementation-template.md`.

## Context

MoralStack is a governance engine whose decisions gate whether an LLM may answer. Today the
constitution retrieval (`get_relevant_principles` = domain prefilter + principle agents) runs
**multiple times per request**: once inside risk estimation (feeds the intent mini), again
inside the deliberation runner (feeds the critic), and a third time inside the fast-path
`critic.quick_check`. This change makes it run **exactly once per request, owned at
risk-estimation time, and reused by every consumer**, and moves 5 fixed guidelines from the
intent user message into the intent system prompt for OpenAI prompt-caching.

## Objective

Implement the approved, Codex-reviewed plan verbatim. Read it IN FULL first:
**`ai/plans/unify-constitution-retrieval-single-pass.md`** (status: APPROVED,
`APPROVE_WITH_CHANGES`, no blocking issues). The plan's "Design", "Files to modify",
"Tests to add / modify", "Acceptance criteria", and "Implementation checklist" sections are
authoritative. This handoff summarizes the guardrails; the plan holds the detail.

## Files ALLOWED to modify

Source (smallest-diff, per plan §Files to modify):
- `moralstack/models/risk/prompts.py` — append the 5 SEMANTIC ANALYSIS GUIDELINES to
  `INTENT_CONTEXT_SYSTEM_PROMPT`; leave `INTENT_CONTEXT_PROMPT_TEMPLATE` structurally intact.
- `moralstack/models/risk/estimator.py` — remove the guidelines block from
  `_get_principles_context` (`:504-511`); add optional `retrieval_query`/`retrieval_top_k`
  params threaded through `estimate`/`_semantic_analysis`/`_parallel_mini_analysis`; return
  the full retrieved list + debug snapshot; populate new `RiskEstimation` carrier fields.
- `moralstack/models/risk/schema.py` — add defaulted carrier fields to `RiskEstimation`
  incl. `retrieval_attempted`/`retrieval_succeeded`/`retrieval_error`.
- `moralstack/orchestration/controller.py` — query policy (RAW vs ENRICHED) + guarded/public
  `risk_top_k` + unified top_k in `_estimate_risk`; build+pass `request_analysis` (gated on
  `retrieval_succeeded`) into `run_deliberative_path`, `_route_fast_path`/FAST_PATH dispatch,
  and COMPLIANCE_FAST_PATH; emit the single `RELEVANT_PRINCIPLES_RETRIEVED` on success.
- `moralstack/orchestration/deliberation_runner.py` — accept `request_analysis` in
  `run_deliberative_path`/`_deliberative_path`/`run_fast_path`; fall back to
  `_try_build_request_analysis_context` only when `None` (emit event only on fallback);
  forward context to `quick_check` and the quick-check-failed `run_deliberative_path`; promote
  `_retrieval_top_k_for_request` to a public accessor; change the critic reuse gate
  (`:2808-2810`) to not require `len > 0`; slice critic reuse to critic top_k.
- `moralstack/runtime/modules/critic_module.py` — `quick_check` gains an optional
  pre-retrieved-principles param; filter to `level == "hard"` when supplied; **preserve** the
  constitution HARD fallback (`:647-649`) when the filtered list is empty; self-retrieve when
  no context supplied.
- `moralstack/constitution/retriever.py` — thread `retrieval_phase` through enhanced
  (`:807,830,906`), legacy (`:996,1006,1056`), and parallel-executor submit (`:1459,1484`)
  sites.

New test files (per plan §Tests): `tests/test_intent_guidelines_placement.py`,
`tests/test_signals_mini_principle_free.py`, `tests/test_risk_estimation_carrier_fields.py`,
`tests/test_retrieval_top_k_reconciliation.py`, `tests/test_retrieval_query_policy.py`,
`tests/test_single_retrieval_wave_e2e.py`, `tests/test_retriever_phase_labeling.py`,
`tests/test_controller_topk_protocol_only_estimator.py`,
`tests/test_fast_path_single_retrieval.py`,
`tests/test_fallback_paths_no_risk_estimator.py`,
`tests/test_fallback_paths_no_constitution_store.py`,
`tests/test_fallback_paths_retrieval_raises.py`,
`tests/test_observability_relevant_principles_single_emit.py`; plus extend
`tests/test_risk_estimator_runtime_domain.py`, `tests/test_request_analysis_reuse.py`,
`tests/governance_invariants/test_q17_hard_signal_invariant.py` (+ sibling
`..._hard_signal_not_overridable_by_retrieval_wave.py`).

New tooling: `scripts/ai/noise_floor_compare.py` (offline comparator — see Verification).

Docs (§8, same change): `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`,
`docs/TRACES/governance_decision_flow.md`, `docs/TRACES/observability_db_to_ui.md`,
`docs/modules/risk_estimator.md` (+ the deliberation module doc if present).

## Files NOT to modify (do-not-touch)

- `moralstack/orchestration/_policy_helpers.py` `POLICY_SYSTEM_PROMPT` / any delivered-answer
  composition — must stay byte-identical (§5.4).
- The **signals** and **operational** mini prompts/user templates — they must NEVER receive
  constitution principles (§5.3). Only the intent mini gets principles.
- `_normalize_runtime_domain` core-exclusion logic in `controller.py` (§5.5).
- `_LOCAL_LLM_CALL_PAYLOAD_KEYS` shape (`estimator.py:77,224`) — the new in-memory
  `Principle` carrier objects must NEVER enter the persisted `llm_calls` payload.
- `moralstack/orchestration/final_revalidation.py` — explicitly out of scope.
- Do NOT weaken/skip/delete any existing test. `tests/test_static_prefix_stability.py` intent
  assertions may be **re-anchored** to the new `INTENT_CONTEXT_SYSTEM_PROMPT` constant (it
  compares to the live constant, so it should stay green automatically) — do not change its
  logic, only confirm green and add the guideline present/absent assertions in the NEW test.

## Invariants (PROJECT_SPEC §5) — keep intact

- **§5.1** decision/generation separation — `final_action` from structured signals only.
- **§5.3** hard-signal supremacy — signals mini stays principle-free; hard-signal detection
  structural. A governance change that fails **open** is a defect.
- **§5.4** single-turn byte-equality — delivered `POLICY_SYSTEM_PROMPT` untouched; on the
  no-contract/no-history path the retrieval query stays RAW so risk behavior is unchanged.
- **§5.5** `core` is retrieval-only — unified retrieval passes `domain=None`; runtime_domain
  keeps excluding `core`.
- **§5.6** observability best-effort — all event emission in swallowing try/except.
- **§7** governed delivery / fail-safe — every degraded path (no risk estimator, no store,
  retrieval raises, no context supplied to quick_check) must FALL BACK to self-retrieval,
  never drop principles, never crash, never fail open.

## Checklist (ordered — from plan §Implementation checklist)

1. `prompts.py`: append guidelines to `INTENT_CONTEXT_SYSTEM_PROMPT`.
2. `estimator.py`: remove guidelines from `_get_principles_context`; add
   `retrieval_query`/`retrieval_top_k`; return full list + snapshot; slice to `self._top_k`
   for intent formatting.
3. Thread params through `estimate` → `_semantic_analysis` → `_parallel_mini_analysis`.
4. `schema.py`: add defaulted carrier fields (incl. status flags); populate at construction.
5. `retriever.py`: thread `retrieval_phase` through all agent + executor sites.
6. `critic_module.py`: `quick_check` optional pre-retrieved-principles param (filter to HARD;
   preserve HARD fallback; self-retrieve otherwise).
7. `deliberation_runner.py`: promote `_retrieval_top_k_for_request`; add `request_analysis`
   (authoritative even when empty; emit event only on fallback); forward to `run_fast_path`/
   `quick_check`/failed-quick-check `run_deliberative_path`; change critic reuse gate; slice
   critic reuse to critic top_k.
8. `controller.py`: query policy + guarded `risk_top_k` + unified top_k; build+pass
   `request_analysis` across deliberative/FAST_PATH/COMPLIANCE_FAST_PATH; emit single event
   on success.
9. Build `scripts/ai/noise_floor_compare.py`; add/extend tests; update docs (§8).
10. Run scoped tests → full suite. (Noise-floor gate = release-time, see below.)

## Required tests

Implement every test named in the plan's "Tests to add / modify" section (unit 1–6,
integration 7–7d, regression 12–16). All must be **offline, deterministic, mocked** — no
network, no real LLM. Key load-bearing ones: single-retrieval e2e (exactly one store call
across routes incl. FAST_PATH), authoritative-empty-context (no re-retrieval on zero
principles), each fail-safe fallback path, guidelines present-in-system/absent-from-user,
signals-mini-principle-free, top_k no-widening, query policy RAW/ENRICHED, protocol-only
estimator top_k, retriever phase labels (enhanced+legacy), controller-emits/runner-fallback
no-double-emit, quick_check HARD fallback on zero-HARD shared list, full hard-signal set not
overridable.

## Verification commands (run and report REAL output)

Use the project venv (`venv/Scripts/python.exe` on Windows; fall back to `python`):

```
# scoped new/changed unit + integration tests
python -m pytest tests/test_intent_guidelines_placement.py tests/test_signals_mini_principle_free.py tests/test_risk_estimation_carrier_fields.py tests/test_retrieval_top_k_reconciliation.py tests/test_retrieval_query_policy.py tests/test_single_retrieval_wave_e2e.py tests/test_fast_path_single_retrieval.py tests/test_retriever_phase_labeling.py tests/test_controller_topk_protocol_only_estimator.py tests/test_fallback_paths_no_risk_estimator.py tests/test_fallback_paths_no_constitution_store.py tests/test_fallback_paths_retrieval_raises.py tests/test_observability_relevant_principles_single_emit.py -v

# existing behavior-locking tests in this area (must stay green)
python -m pytest tests/test_risk_estimator_runtime_domain.py tests/test_request_analysis_reuse.py tests/test_static_prefix_stability.py tests/test_system_prompt_byte_equality.py tests/test_risk_persist_batch.py tests/governance_invariants/ -v

# full suite (declare-done gate)
python -m pytest
```

**Noise-floor gate is a RELEASE-TIME step, NOT part of your run.** It requires live benchmark
runs against the OpenAI API, which you do not have. Do this much only: build
`scripts/ai/noise_floor_compare.py` (offline; matches two runs' `decision_traces` by
prompt-hash, reads ONLY `final_action`/route/`hard_violation_codes`, never prompt text) and
**validate it against the 4 existing HEAD benchmark runs already in `moralstack.db`**
(`81319498`, `79830edc`, `dceb24f8`, `60124777`) to confirm it reproduces the known band
(final_action mean 4.2%/max 8.4%, all NORMAL↔SAFE; route 0%; hard 1.4%). Do NOT claim the
post-change gate passed — report it as an outstanding release-time step.

Do NOT run `pre-commit run -a` (HEAD is not pre-commit-clean; it churns unrelated files). If
you run pre-commit, scope it: `pre-commit run --files <the files you changed>`.

## Acceptance criteria (from plan)

- Exactly one `get_relevant_principles` per request on the happy path across ALL routes;
  reuse-vs-fallback decided by `retrieval_succeeded`, never by emptiness; authoritative empty
  context causes no re-retrieval.
- Guidelines in `INTENT_CONTEXT_SYSTEM_PROMPT`, absent from intent user prompt; principles
  list still in the user tail.
- Query RAW iff no contract and no history, ENRICHED otherwise; single retrieval at
  `max(risk_top_k, critic_top_k)`; each consumer sliced to its own top_k (no widening).
- Every degraded path falls back and still retrieves principles; no crash, no fail-open.
- Delivered `POLICY_SYSTEM_PROMPT` byte-identical; signals mini principle-free; `core` never
  a runtime overlay; unified retrieval passes `domain=None`.
- Exactly one `RELEVANT_PRINCIPLES_RETRIEVED` event per request (controller on success, runner
  on fallback — never both).
- Full `python -m pytest` green.

## Risks (be careful)

- Enriched query under contract changes intent-mini principles vs today — accepted, gated by
  the release-time noise floor; do not try to "fix" it.
- `RiskEstimation` new fields must all be defaulted (frozen dataclass; factories
  `benign`/`clearly_harmful`/`from_error` must still construct).
- Do not double-emit the retrieval event; do not let the carrier principle objects leak into
  persisted `llm_calls`.

## Required output (end of your run)

files modified; tests added; commands run; results (REAL output); deviations from the plan;
residual problems / blockers. If the plan is ambiguous or you hit a blocking architectural
problem, STOP and report — do not guess or work around it. Do NOT git add/commit/push.
