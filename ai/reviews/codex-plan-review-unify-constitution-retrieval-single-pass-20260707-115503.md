# Codex Plan Review

## Verdict
APPROVE_WITH_CHANGES

## Blocking issues
None.

## Non-blocking issues
- FAST_PATH single-retrieval is now addressed in the plan and feasible against current code: current FAST_PATH dispatch reaches `_route_fast_path` at `moralstack/orchestration/controller.py:2605`, `_route_fast_path` calls `run_fast_path` at `moralstack/orchestration/controller.py:1752`, `run_fast_path` calls `critic.quick_check` at `moralstack/orchestration/deliberation_runner.py:970`, current `quick_check` self-retrieves at `moralstack/runtime/modules/critic_module.py:641` and filters HARD at `moralstack/runtime/modules/critic_module.py:643`, and quick-check failure escalates to `run_deliberative_path` at `moralstack/orchestration/deliberation_runner.py:972`. The plan now threads `RequestAnalysisContext` through that chain and into the failed quick-check fallback at `ai/plans/unify-constitution-retrieval-single-pass.md:247` and `ai/plans/unify-constitution-retrieval-single-pass.md:256`.
- COMPLIANCE_FAST_PATH is coherent: risk runs before DCCL/routing at `moralstack/orchestration/controller.py:2092` and `moralstack/orchestration/controller.py:2096`; compliance can return before normal routing at `moralstack/orchestration/controller.py:2163`; `_route_compliance_match` skips deliberation/critic/simulator/perspectives by design at `moralstack/orchestration/controller.py:1378` and `moralstack/orchestration/controller.py:1382`; it returns via `run_benign_fast_path` at `moralstack/orchestration/controller.py:1432`. No compliance-branch principle retrieval found.
- Observability has stale contradictory plan text. The v4.2 design correctly says controller on success / runner on fallback at `ai/plans/unify-constitution-retrieval-single-pass.md:298`, `:353`, and `:551`, but older text still says `_record_retrieval_start_and_event` runs "with whichever context resulted" at `ai/plans/unify-constitution-retrieval-single-pass.md:231`, and R3 still says the single emit site is in the runner at `ai/plans/unify-constitution-retrieval-single-pass.md:520`. Current runner emission is real at `moralstack/orchestration/deliberation_runner.py:542,546`, so leaving both instructions invites double-emission.

## Missing tests
- Add an assertion that a controller-supplied successful risk context does not call runner `_record_retrieval_start_and_event`; current runner emits after building request analysis at `moralstack/orchestration/deliberation_runner.py:1381,1384`.
- Add FAST_PATH quick-check coverage where the shared list has zero HARD principles; current `quick_check` falls back to constitution HARD constraints at `moralstack/runtime/modules/critic_module.py:647,649`, and the plan should preserve that behavior.
- Keep the planned COMPLIANCE_FAST_PATH no-extra-retrieval test; the route returns from compliance before normal routing at `moralstack/orchestration/controller.py:2163`.

## Risky assumptions
- "Exactly one per request" appears scoped to active controller routes, not standalone legacy/upstream final revalidation. `revalidate_final_output` can retrieve principles at `moralstack/orchestration/final_revalidation.py:272`, but active proxy governed delivery uses `finalize_delivery` at `moralstack/server/proxy.py:384`, and tests assert no final-revalidation event on governed delivery paths at `tests/test_server_proxy.py:1211`.
- Protocol-only risk estimators remain a real compatibility constraint: `RiskEstimatorProtocol` exposes `estimate` at `moralstack/core/types.py:100` and no `_top_k`; the guarded accessor in the plan is required.

## Architecture concerns
- Importing `_build_enriched_retrieval_query` into controller is feasible but couples controller to a private runner helper; the helper is currently module-level at `moralstack/orchestration/deliberation_runner.py:255`.
- Carrying in-memory `Principle` objects on `RiskEstimation` is acceptable only if they never enter persisted LLM payloads. Current local risk payload shape is fixed by `_LOCAL_LLM_CALL_PAYLOAD_KEYS` at `moralstack/models/risk/estimator.py:77` and validated at `moralstack/models/risk/estimator.py:224`.

## Security/performance concerns
- The R6 exposure note is correctly flagged: enriched retrieval includes contract text and recent history at `moralstack/orchestration/deliberation_runner.py:271,279`, while retriever LLM calls persist prompts via `persist_llm_call` at `moralstack/constitution/retriever.py:90`.
- The "same provider already sees it" citation is now correct: risk mini messages add developer contract/history at `moralstack/models/risk/estimator.py:240,242,247`, then call `generate_messages` at `moralstack/models/risk/estimator.py:803`.

## Suggested plan changes
- Remove or rewrite the stale observability lines at `ai/plans/unify-constitution-retrieval-single-pass.md:231` and `:520` so every observability instruction says: controller emits on risk-success, runner emits only when it performed fallback retrieval.
- Explicitly state that `quick_check` must retain the constitution HARD fallback when the supplied shared list has no HARD principles.
- Add one sentence scoping "exactly one retrieval per request" to active controller routes, or explicitly decide whether standalone `final_revalidation.py` is out of scope.

## Questions for Claude/User
- Is standalone `revalidate_final_output` intentionally out of scope for the GLOBAL "one retrieval per request" rule?
- Should the controller-level `RELEVANT_PRINCIPLES_RETRIEVED` event reuse the existing runner payload shape exactly, including `principles_count`, `principle_ids`, and retrieval metadata?
