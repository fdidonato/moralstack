# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- **Successful empty retrieval is indistinguishable from degraded retrieval.** The plan gates controller injection on `risk_estimation.relevant_principles` being non-empty and treats an empty payload as fallback-worthy (`ai/plans/unify-constitution-retrieval-single-pass.md:160`, `ai/plans/unify-constitution-retrieval-single-pass.md:206-210`). Current runner context can represent a successful zero-principle retrieval (`moralstack/orchestration/deliberation_runner.py:493`, `moralstack/orchestration/deliberation_runner.py:499`), but critic reuse currently requires `len(request_analysis.relevant_principles) > 0` (`moralstack/orchestration/deliberation_runner.py:2810`) and otherwise falls back to `critique_with_relevant_principles()` (`moralstack/orchestration/deliberation_runner.py:2856`). That can cause a second retrieval on a legitimate empty result, violating the plan's "exactly one" acceptance criterion (`ai/plans/unify-constitution-retrieval-single-pass.md:379`). Must change: carry an explicit retrieval status/attempted/error flag, build an authoritative context for successful empty results, and fall back only on unavailable/failed retrieval.

- **The plan claims fallback `retrieval_phase` labeling but omits the retriever changes needed to make it true.** The store accepts `retrieval_phase` (`moralstack/constitution/store.py:867`, `moralstack/constitution/store.py:873`) and the domain prefilter threads it (`moralstack/constitution/retriever.py:421`, `moralstack/constitution/retriever.py:557`), but enhanced agents call `_call_openai(prompt)` without a phase (`moralstack/constitution/retriever.py:807`, `moralstack/constitution/retriever.py:830`) and persist without passing `retrieval_phase`, falling back to the default (`moralstack/constitution/retriever.py:69`, `moralstack/constitution/retriever.py:906`). The plan's files-to-modify omit `moralstack/constitution/retriever.py`. Must change: thread `retrieval_phase` through enhanced and legacy domain-agent calls, and test persisted `llm_calls` phase labels for risk-owned and fallback retrieval.

- **The controller top-k plan depends on a private concrete estimator field.** The plan says controller computes `max(risk_estimator._top_k, runner_critic_top_k)` (`ai/plans/unify-constitution-retrieval-single-pass.md:138`), but the public protocol only exposes `estimate(prompt)` (`moralstack/core/types.py:97`, `moralstack/core/types.py:100`), and repo mocks implement that protocol without `_top_k` (`tests/test_orchestrator.py:186`, `tests/test_orchestrator.py:194`; `moralstack/cli/mocks.py:32`, `moralstack/cli/mocks.py:35`). Must change: use a public/defaulted accessor or guarded `getattr(..., 10)` and add a controller test with a protocol-only estimator.

## Non-blocking issues
- The top-k reconciliation is otherwise on the right track: current critic override uses supplied `principles` as-is (`moralstack/runtime/modules/critic_module.py:391`) while store-backed critic retrieval uses `top_k=self.config.top_k_principles` (`moralstack/runtime/modules/critic_module.py:757`, `moralstack/runtime/modules/critic_module.py:759`), so the planned critic slice is necessary.
- The plan says the `RiskEstimation` carrier is "primitives only" while also carrying `tuple[Any, ...]` principles (`ai/plans/unify-constitution-retrieval-single-pass.md:152-154`). That is not primitive. It may be acceptable, but the plan should say these are in-memory principle objects and must not be serialized into persisted payloads.
- Moving guidelines from user to system changes role priority when native messages are used: current message order is system, developer, history, user (`moralstack/models/risk/estimator.py:239`, `moralstack/models/risk/estimator.py:242`, `moralstack/models/risk/estimator.py:256`), and native messages are used at `moralstack/models/risk/estimator.py:803`. The statistical gate must include contract/history cases, not only no-context prompts.

## Missing tests
- Add a successful-empty retrieval test: store returns `[]`, risk records retrieval success, controller passes an empty `RequestAnalysisContext`, runner emits one retrieval event, and critic does not re-retrieve.
- Add retriever phase-label tests covering enhanced and legacy agents; current phase threading is incomplete (`moralstack/constitution/retriever.py:807`, `moralstack/constitution/retriever.py:906`).
- Add protocol-only estimator coverage for controller top-k computation (`moralstack/core/types.py:100`; `tests/test_orchestrator.py:194`).
- Expand hard-signal regression beyond Q17. The hard-signal set includes Q4/Q5/Q8/Q9/Q10/Q11/Q12/Q17 (`moralstack/orchestration/path_router.py:17-26`), but the plan only names Q17 (`ai/plans/unify-constitution-retrieval-single-pass.md:318-320`).

## Risky assumptions
- The statistical gate is acceptable only for `NORMAL_COMPLETE`/`SAFE_COMPLETE` drift. `route` and the REFUSE request set must remain exact; the plan does require route flips = 0 and unchanged REFUSE set (`ai/plans/unify-constitution-retrieval-single-pass.md:352-355`).
- "Empty payload means degraded" is unsafe because an empty retrieval can be a valid retriever result, not a failure (`moralstack/orchestration/deliberation_runner.py:493`, `moralstack/orchestration/deliberation_runner.py:499`).
- Enriched retrieval sends developer contract/history into retriever prompts; `_build_enriched_retrieval_query` includes contract and history text (`moralstack/orchestration/deliberation_runner.py:268-294`), and retriever persistence stores prompts via `persist_llm_call` (`moralstack/constitution/retriever.py:90`).

## Architecture concerns
- Adding retrieval payload to `RiskEstimation` is feasible because the dataclass is frozen and defaultable (`moralstack/models/risk/schema.py:21`, `moralstack/models/risk/schema.py:42-83`), but it crosses model/orchestration boundaries with constitution objects. Keep persisted payloads separate from this in-memory carrier.
- The current controller already handles optional estimator kwargs defensively (`moralstack/orchestration/controller.py:888-896`); the new query/top-k kwargs should preserve that compatibility pattern.

## Security/performance concerns
- Contract/history exposure to retrieval LLMs is a real security/audit expansion for any contract/history request (`moralstack/orchestration/deliberation_runner.py:268-294`; `moralstack/constitution/retriever.py:90`). The plan should explicitly document this as accepted and bounded by truncation.
- Retrieving at `max(risk_top_k, critic_top_k)` increases risk-wave retrieval size; consumer slicing protects prompts only if implemented at both risk formatting and critic reuse (`moralstack/orchestration/deliberation_runner.py:2828`; `moralstack/runtime/modules/critic_module.py:391`).

## Suggested plan changes
- Add `retrieval_succeeded` / `retrieval_error` or equivalent to the carrier; do not infer fallback from an empty principle tuple.
- Treat supplied `RequestAnalysisContext` as authoritative even when empty, while preserving fallback for no estimator, no store, or retrieval exception.
- Add `moralstack/constitution/retriever.py` to the files-to-modify list for phase propagation.
- Replace private `_top_k` access with a guarded/public accessor.
- Make the validation gate explicit: exact route equality, exact REFUSE set equality, no REFUSE transitions, and targeted hard-signal tests; statistical tolerance only for NORMAL/SAFE drift.

## Questions for Claude/User
- Should a successful zero-principle retrieval cause the critic to skip, use full constitution, or proceed with an empty principle set?
- Is the contract/history exposure to retrieval LLMs accepted for fast-path requests too, or should enriched retrieval be limited to requests that actually deliberate?
- Should hard-signal mismatch be tolerated at all when final_action is unchanged, or should hard-signal codes be exact for the targeted safety suite?
