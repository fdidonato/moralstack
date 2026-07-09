# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- **Fast-path retrieval is still outside the "exactly one retrieval per request" design.** The plan promises one `get_relevant_principles` call per request (`ai/plans/unify-constitution-retrieval-single-pass.md:51`, `ai/plans/unify-constitution-retrieval-single-pass.md:97`), but the reachable FAST_PATH route still performs a separate critic quick-check retrieval: controller dispatches FAST_PATH at `moralstack/orchestration/controller.py:2605`, `_route_fast_path` calls `run_fast_path` at `moralstack/orchestration/controller.py:1752`, `run_fast_path` calls `critic.quick_check` at `moralstack/orchestration/deliberation_runner.py:968`, and `quick_check` calls `self.store.get_relevant_principles(...)` at `moralstack/runtime/modules/critic_module.py:638` and `moralstack/runtime/modules/critic_module.py:641`. If quick-check fails, `run_fast_path` then calls `run_deliberative_path` at `moralstack/orchestration/deliberation_runner.py:971`, which today performs its own request-scoped retrieval at `moralstack/orchestration/deliberation_runner.py:1381`. This blocks the plan because the single-wave carrier is only threaded into controller deliberation dispatch (`ai/plans/unify-constitution-retrieval-single-pass.md:199`, `ai/plans/unify-constitution-retrieval-single-pass.md:206`) and not into FAST_PATH quick-check or its deliberative fallback. Must change: either explicitly narrow the acceptance criterion to deliberative-path-only, or pass the risk-owned `RequestAnalysisContext` through `_route_fast_path` / `run_fast_path` / `quick_check` and into the quick-check-failed `run_deliberative_path` call.

## Non-blocking issues
- The three prior blockers are materially addressed for the deliberative critic path: v4.1 adds explicit retrieval status flags (`ai/plans/unify-constitution-retrieval-single-pass.md:187`), gates controller reuse on `retrieval_succeeded` (`ai/plans/unify-constitution-retrieval-single-pass.md:199`), and removes the critic `len > 0` reuse requirement (`ai/plans/unify-constitution-retrieval-single-pass.md:211`). This matches the current empty-result ambiguity: `RequestAnalysisContext` can contain `tuple(relevant)` and `len(relevant)` even when empty (`moralstack/orchestration/deliberation_runner.py:492`, `moralstack/orchestration/deliberation_runner.py:498`), while current critic reuse rejects empty principles (`moralstack/orchestration/deliberation_runner.py:2808`) and falls back to `critique_with_relevant_principles` (`moralstack/orchestration/deliberation_runner.py:2855`).
- The retriever phase-label plan is directionally right, but the cited line list is incomplete. Enhanced agent sites are at `moralstack/constitution/retriever.py:807`, `moralstack/constitution/retriever.py:830`, and `moralstack/constitution/retriever.py:906`; legacy agent sites are also at `moralstack/constitution/retriever.py:996`, `moralstack/constitution/retriever.py:1006`, and `moralstack/constitution/retriever.py:1056`. The phase must also pass through the parallel executor calls at `moralstack/constitution/retriever.py:1459` and `moralstack/constitution/retriever.py:1484`.
- The acceptance summary weakens the detailed hard-signal gate: the detailed gate requires byte-identical hard-signal codes on Q4/Q5/Q8/Q9/Q10/Q11/Q12/Q17 (`ai/plans/unify-constitution-retrieval-single-pass.md:429`), matching the code set (`moralstack/orchestration/path_router.py:17`, `moralstack/orchestration/path_router.py:19`), but the acceptance checklist says only "hard-signal ≤ baseline" (`ai/plans/unify-constitution-retrieval-single-pass.md:487`).

## Missing tests
- Add FAST_PATH coverage proving total store retrieval count remains one when quick-check passes.
- Add FAST_PATH quick-check-failed coverage proving the fallback deliberative path reuses the risk-owned context and does not retrieve again.
- Add COMPLIANCE_FAST_PATH coverage for whether a risk-owned retrieval should emit `RELEVANT_PRINCIPLES_RETRIEVED`, because risk runs before DCCL (`moralstack/orchestration/controller.py:2092`, `moralstack/orchestration/controller.py:2099`) and compliance can return before deliberation (`moralstack/orchestration/controller.py:2120`, `moralstack/orchestration/controller.py:2163`).
- Add retriever phase-label tests for both enhanced and legacy agents, including cache-miss cases.

## Risky assumptions
- R6 says contract text already reaches the same provider via risk minis and cites `estimator.py:746-749` (`ai/plans/unify-constitution-retrieval-single-pass.md:455`), but those lines build message-section metadata; the provider call carrying contract/history is at `moralstack/models/risk/estimator.py:802` through `moralstack/models/risk/estimator.py:809`, with message construction at `moralstack/models/risk/estimator.py:240` through `moralstack/models/risk/estimator.py:247`.
- The plan assumes runner-owned retrieval event emission is sufficient, but non-deliberative paths can use risk retrieval without entering `run_deliberative_path` (`moralstack/orchestration/controller.py:2163`).

## Architecture concerns
- Importing `_build_enriched_retrieval_query` from `deliberation_runner` into controller is feasible because `deliberation_runner.py` does not import controller (`moralstack/orchestration/deliberation_runner.py:255`), but it makes controller depend on a private runner helper.
- Carrying in-memory `Principle` objects on `RiskEstimation` is acceptable only if kept out of persisted payloads as the plan says (`ai/plans/unify-constitution-retrieval-single-pass.md:187`); current risk LLM payload shape is guarded by `_LOCAL_LLM_CALL_PAYLOAD_KEYS` (`moralstack/models/risk/estimator.py:77`, `moralstack/models/risk/estimator.py:224`).

## Security/performance concerns
- Enriched retrieval sends truncated contract and history into retriever prompts (`moralstack/orchestration/deliberation_runner.py:271`, `moralstack/orchestration/deliberation_runner.py:279`) and retriever persistence stores prompts through `persist_llm_call` (`moralstack/constitution/retriever.py:90`); the plan accepts this at `ai/plans/unify-constitution-retrieval-single-pass.md:455`.
- Retrieving at `max(risk_top_k, critic_top_k)` increases risk-wave retrieval work; consumer slicing is necessary because critic override otherwise uses supplied principles as-is (`moralstack/runtime/modules/critic_module.py:388`), while store-backed critic retrieval uses `top_k_principles` (`moralstack/runtime/modules/critic_module.py:757`).

## Suggested plan changes
- Add `request_analysis` threading through `_route_fast_path`, `run_fast_path`, `critic.quick_check`, and the quick-check-failed `run_deliberative_path` call.
- Expand the "exactly one retrieval" tests to cover DELIBERATIVE_PATH, FAST_PATH, FAST_PATH-to-deliberative fallback, and COMPLIANCE_FAST_PATH.
- Fix retriever phase propagation instructions to include enhanced evaluate/call/persist, legacy evaluate/call/persist, and both parallel executor submit sites.
- Make the acceptance checklist match the detailed noise gate: exact route equality, exact REFUSE set equality, and exact targeted hard-signal codes.
- Correct R6's risk-mini exposure citation to the actual provider-call lines.

## Questions for Claude/User
- Is "exactly one retrieval per request" intended globally, including FAST_PATH and COMPLIANCE_FAST_PATH, or only for deliberative requests?
- Should FAST_PATH quick-check consume the risk-owned principles, or should quick-check be explicitly exempted from the single-retrieval goal?
- Should `RELEVANT_PRINCIPLES_RETRIEVED` be emitted by the controller/risk carrier path so non-deliberative requests are observable too?
