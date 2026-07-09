# Implementation Report — unify-constitution-retrieval-single-pass

Implementer: `claude-implementer` (Claude Sonnet, isolated context). HEAD at handoff:
`cc62c6bdb4dfebca9cc6edf04cb233efa15599e4`.

**Goal:** Implement approved plan v4.2 — unify constitution-principle retrieval into exactly
one `get_relevant_principles` call per request (owned in the risk thread, reused by
deliberation/critic/fast-path/quick-check fallback) + move the 5 SEMANTIC ANALYSIS GUIDELINES
from the intent mini's user message into its system prompt for OpenAI prompt-caching.

## Files modified (all within allowed list)
- `moralstack/models/risk/prompts.py` — appended 5 guidelines to `INTENT_CONTEXT_SYSTEM_PROMPT`; `INTENT_CONTEXT_PROMPT_TEMPLATE` untouched.
- `moralstack/models/risk/estimator.py` — new `_PrinciplesContextResult` dataclass; `_get_principles_context` takes `retrieval_query`/`retrieval_top_k`, removes guidelines block, returns full list + debug snapshot + status flags; threaded through `estimate`/`_semantic_analysis`/`_parallel_mini_analysis`/`_to_risk_estimation`.
- `moralstack/models/risk/schema.py` — `RiskEstimation` carrier fields: `relevant_principles`, `retrieval_metadata`, `retrieval_count`, `retrieval_duration_ms`, `retrieval_started_at_ms`, `retrieval_top_k`, `retrieval_attempted`, `retrieval_succeeded`, `retrieval_error`.
- `moralstack/orchestration/controller.py` — `DEFAULT_RISK_TOP_K`; `_estimate_risk` query policy + unified top_k; `_build_request_analysis_from_risk`/`_emit_relevant_principles_retrieved` helpers; `_route_fast_path`/`_route_deliberative` build+pass `request_analysis`.
- `moralstack/orchestration/deliberation_runner.py` — `retrieval_top_k_for_request` public; `run_deliberative_path`/`run_fast_path` accept `request_analysis` (authoritative-even-empty, fallback+emit only when None); critic reuse gate no longer requires `len>0` + slices to critic top_k; `run_fast_path` forwards shared principles to `quick_check` + failed-quick-check fallback.
- `moralstack/runtime/modules/critic_module.py` — `quick_check` gained `pre_retrieved_principles` (filter to HARD when supplied; HARD fallback preserved; self-retrieve otherwise).
- `moralstack/constitution/retriever.py` — `retrieval_phase` threaded through enhanced/legacy `evaluate`/`_call_openai`/persistence + parallel-executor submits (`functools.partial`).
- Docs: INDEX, CODEBASE_FACTS, TRACES/governance_decision_flow, TRACES/observability_db_to_ui, modules/risk_estimator, modules/critic, modules/constitution_store, modules/orchestrator.
- New tool: `scripts/ai/noise_floor_compare.py`.
- 7 pre-existing tests adapted (mechanical unpacking → attribute access + agent `retrieval_phase` kwarg); `tests/test_request_analysis_reuse.py` extended.

## Tests added (13 new files, offline/deterministic)
test_intent_guidelines_placement, test_signals_mini_principle_free, test_risk_estimation_carrier_fields, test_retrieval_top_k_reconciliation, test_retrieval_query_policy, test_single_retrieval_wave_e2e, test_fast_path_single_retrieval (7 cases), test_retriever_phase_labeling, test_controller_topk_protocol_only_estimator, test_fallback_paths_no_risk_estimator, test_fallback_paths_no_constitution_store, test_fallback_paths_retrieval_raises, test_observability_relevant_principles_single_emit, governance_invariants/test_hard_signal_not_overridable_by_retrieval_wave.

## Commands run / real results
- Scoped new+changed (13 files): `42 passed`.
- Existing behavior-locking (risk_estimator_runtime_domain, request_analysis_reuse, static_prefix_stability, system_prompt_byte_equality, risk_persist_batch, governance_invariants/): `74 passed`.
- Full suite `python -m pytest -q`: **2083 passed, 0 failed** (345s).
- `ruff check` on touched files: clean.
- Noise-floor tool validated HEAD-vs-HEAD (4 real runs in `moralstack.db`, 6 pairs, n=83): reproduces the band — final_action mean 4.22%/max 8.43%, all NORMAL↔SAFE, route 0%, hard-signal-code divergence 0%. **Post-change (branch-vs-HEAD) gate NOT run** — needs live OpenAI benchmark; outstanding release-time step, not claimed passed.

## Deviations from plan
1. `_get_principles_context` returns a frozen `_PrinciplesContextResult` dataclass (attribute access) rather than a wider tuple — avoids fragile positional unpacking; required adapting 8 pre-existing tests' unpacking (assertions unchanged).
2. `RequestAnalysisContext` build + single `RELEVANT_PRINCIPLES_RETRIEVED` emit live in a shared controller helper invoked inside `_route_fast_path`/`_route_deliberative` (not immediately after `_estimate_risk`); COMPLIANCE_FAST_PATH never calls it (consumes no principles) — matches plan intent.
3. Some plan line citations drifted vs live source (`_route_deliberative` at 1880 not 1806; critic gate at 2825 not 2808) — same logic, verified against live source before editing.

## Residual / blockers
None from implementation. Outstanding: release-time noise-floor gate against a live post-change benchmark run (explicitly out of scope per handoff).
