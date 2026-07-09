# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- **The plan treats the enriched risk retrieval query as non-decision-affecting, but it is a structured decision input.** The risk estimator injects retrieved principles into the intent mini prompt via `_get_principles_context(prompt)` and `INTENT_CONTEXT_PROMPT_TEMPLATE`, while signals/operational prompts do not receive that context (`moralstack/models/risk/estimator.py:737-745`). The merge then makes intent mini fields authoritative for `intent_to_harm`, `requested_instructions`, `intent_operational`, `request_type`, `harm_type`, and `intent_clarity` (`moralstack/models/risk/calibration.py:821-837`). `decide_action()` consumes those risk fields to build the policy context and final action (`moralstack/orchestration/decision_service.py:850-899`), and hard-signal bypass checks consume structured decision/risk signals (`moralstack/orchestration/path_router.py:39-64`). This blocks because the plan's "no final_action / hard-signal change" claim is not established. The plan must either preserve raw risk retrieval or add query-sensitive regression/shadow validation that fails on any structured risk/final_action/hard-signal delta.

- **`RequestAnalysisContext.detected_domain` is not equivalent to the risk estimator's current runtime domain.** Current risk behavior derives `runtime_domain` from `constitution_store.get_debug_info()["prefiltered_domains"]` and excludes `core` (`moralstack/models/risk/estimator.py:467-478`). The runner currently builds `RequestAnalysisContext.detected_domain` from `request.get_domain()`, not from prefiltered domains (`moralstack/orchestration/deliberation_runner.py:489-497`). The controller later uses `risk_estimation.detected_domain` to persist/apply a domain overlay after risk (`moralstack/orchestration/controller.py:2267-2279`). This blocks because using `request_analysis.detected_domain` as planned can drop risk-detected domains or mishandle `core`. The plan must derive injected risk runtime domain from `retrieval_metadata["prefiltered_domains"]` with the same `core` exclusion, or add an explicit `runtime_domain` field.

- **The top-k reconciliation is wrong for the critic path.** The runner passes the full `request_analysis.relevant_principles` list into `critic.critique(..., principles=list(...))` (`moralstack/orchestration/deliberation_runner.py:2808-2835`). The critic uses override `principles` as-is, and only slices to `config.top_k_principles` when no override is provided (`moralstack/runtime/modules/critic_module.py:365-370`). The existing store-backed critic retrieval uses `top_k=self.config.top_k_principles` (`moralstack/runtime/modules/critic_module.py:753-760`). This blocks because `max(risk_top_k, delib_top_k)` can widen critic inputs when critic top-k is smaller. The plan must explicitly slice per consumer before calling the critic and before formatting risk context.

- **The retrieval phase plan is internally inconsistent with current routing order.** The proposed shared pass must run before risk so risk can consume it, but the controller only knows the route after risk, DCCL, overlay normalization/flooring, decision policy, safe-complete gating, and `get_route()` (`moralstack/orchestration/controller.py:2089-2099`, `moralstack/orchestration/controller.py:2267-2351`). Current deliberation retrieval is built inside `run_deliberative_path()` after routing and is hard-coded as `retrieval_phase="deliberation_retrieval"` (`moralstack/orchestration/deliberation_runner.py:468-474`, `moralstack/orchestration/deliberation_runner.py:1377-1388`). This blocks because the plan cannot both choose fast-path vs deliberation phase and build before risk. The plan must choose a deterministic phase policy or a neutral phase and update observability expectations accordingly.

- **The plan broadens developer-contract/history exposure to retrieval LLM calls.** The enriched query includes raw developer contract text and recent history (`moralstack/orchestration/deliberation_runner.py:269-293`). Retriever prefilter and domain-agent prompts send that query onward (`moralstack/constitution/retriever.py:484-494`, `moralstack/constitution/retriever.py:749-760`). Today, risk retrieval uses raw `prompt` with `domain=None` (`moralstack/models/risk/estimator.py:462-465`), while enriched retrieval happens only inside the deliberative runner (`moralstack/orchestration/deliberation_runner.py:1377-1388`). This blocks until the plan explicitly accepts, limits, or mitigates the extra secret-handling surface.

## Non-blocking issues
- The files-to-modify list omits the public `RiskEstimatorProtocol`, whose `estimate()` signature still documents `estimate(prompt)` only (`moralstack/core/types.py:82-86`). Existing controller calls already use `# type: ignore[call-arg]` for optional kwargs (`moralstack/orchestration/controller.py:884-891`), so this may be intentional, but the plan should say so.

- The single-owner emission plan needs payload ownership cleanup. Current `RELEVANT_PRINCIPLES_RETRIEVED` emission is runner-private and stamps `"source": "deliberation_runner"` (`moralstack/orchestration/deliberation_runner.py:512-560`). A controller/shared-helper owner should update that source or make the emitter source-parameterized.

- The phase-threading fix only names enhanced agents. Legacy `DomainAgent.evaluate()` and `_call_openai()` also lack `retrieval_phase`, and legacy persistence falls back to the default phase (`moralstack/constitution/retriever.py:952-1008`, `moralstack/constitution/retriever.py:1060-1070`). If `use_enhanced_retrieval=False`, observability remains inconsistent.

## Missing tests
- Add a controller-level regression where the fake store returns different principles for raw vs enriched queries, then assert equality of structured risk fields and `final_action`; a fixed-principle fake will not test the actual proposed behavior change.

- Add injected-runtime-domain tests that drive `process()` and prove `prefiltered_domains=["core", "legal"]` yields the same overlay behavior as current `_get_principles_context()` (`moralstack/models/risk/estimator.py:467-478`, `moralstack/orchestration/controller.py:2267-2279`).

- Add top-k tests with `risk_top_k > critic.config.top_k_principles` to prove the critic receives only its configured number of principles (`moralstack/orchestration/deliberation_runner.py:2808-2835`, `moralstack/runtime/modules/critic_module.py:365-370`).

- Add phase tests for fast, deliberative, enhanced-agent, and legacy-agent paths; current prefilter persistence accepts a phase, while enhanced/legacy agent persistence defaults to `risk_routing` unless threaded (`moralstack/constitution/retriever.py:38-69`, `moralstack/constitution/retriever.py:906-917`, `moralstack/constitution/retriever.py:1060-1070`).

- Add speculative-overlap tests that assert the speculative draft future is submitted before or concurrently with retrieval if the plan claims no dispatch regression; current speculative risk and draft futures are submitted back-to-back (`moralstack/orchestration/controller.py:1063-1073`).

## Risky assumptions
- "Enriched query is a semantic superset" is not a safety proof. The prefilter cache key is based on the full query text and available domains, not semantic equivalence (`moralstack/constitution/retriever.py:443`), and domain agents embed the full query into their prompts (`moralstack/constitution/retriever.py:749-760`).

- "Risk-detected domain is always in the same pass's prefiltered domains" only helps if the injected risk path reads `prefiltered_domains`; current `RequestAnalysisContext.detected_domain` does not carry that value (`moralstack/orchestration/deliberation_runner.py:489-497`).

- The fallback claim is only true if controller injection failure still lets risk call its existing internal retrieval. Current estimator fallback catches retrieval failures inside `_get_principles_context()` (`moralstack/models/risk/estimator.py:514-516`) and `_semantic_analysis()` fails closed to `RiskEstimation.from_error()` on broader mini-analysis errors (`moralstack/models/risk/estimator.py:1029-1033`).

## Architecture concerns
- The controller-owned helper would move a runner-private behavior into shared orchestration. That is reasonable, but the current helper also emits runner-specific trace semantics and source labels (`moralstack/orchestration/deliberation_runner.py:512-560`), so the plan needs a clean boundary between "build context" and "emit observability."

- The carrier currently has one `relevant_principles` tuple and one `retrieval_top_k` (`moralstack/orchestration/types.py:893-901`). Sharing it across consumers with different top-k requirements needs explicit consumer views or slicing rules, not just `max(...)`.

- The retrieval debug state is read through `get_debug_info()` immediately after retrieval and copied into the context (`moralstack/orchestration/deliberation_runner.py:481-496`). Any shared helper must preserve that snapshot behavior.

## Security/performance concerns
- Secret exposure expands because contract/history text moves into retrieval for requests that may never deliberate (`moralstack/orchestration/deliberation_runner.py:269-293`, `moralstack/constitution/retriever.py:484-494`).

- The proposed speculative path computes shared retrieval before submitting futures, but current speculative draft generation is submitted in parallel with risk (`moralstack/orchestration/controller.py:1063-1073`). That can increase latency for paths that use or wait on the speculative draft.

- Retrieving `max(risk_top_k, delib_top_k)` and passing unsliced principles can increase prompt size and cost for the critic (`moralstack/runtime/modules/critic_module.py:365-381`).

## Suggested plan changes
- Reclassify the query change as safety-relevant. Gate it with a query-sensitive golden/shadow test, and fall back to domain-prefilter-only dedupe if any structured risk, hard-signal, route, or `final_action` field changes.

- Add an explicit runtime-domain derivation helper over `retrieval_metadata["prefiltered_domains"]` that excludes `core`, and use it for injected risk context.

- Add per-consumer principle slicing: risk formats `risk_top_k`; critic receives `critic.config.top_k_principles`; deliberation trace records the full retrieved count separately.

- Decide retrieval phase before implementation. Options include a neutral shared phase, always `risk_routing` with a separate reuse event for deliberation, or accepting `deliberation_retrieval` for all shared retrieval and documenting fast-path observability changes.

- Preserve speculative overlap by dispatching retrieval as a future alongside speculative generation and joining only before the intent mini needs context, or explicitly accept the latency regression.

## Questions for Claude/User
- Is it acceptable for developer contracts and recent conversation history to be sent to constitution retrieval LLM calls on fast-path/benign requests?

- Is `final_action` byte-for-byte/field-for-field equality required for this optimization, or are reviewed decision shifts allowed?

- What should the single retrieval phase be when the route is not knowable until after risk?

- Should the first implementation use the safer Alternative 1, deduping only domain prefiltering, before attempting full principle-sharing?
