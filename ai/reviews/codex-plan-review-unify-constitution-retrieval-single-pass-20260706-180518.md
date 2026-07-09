# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- **Short no-contract prompts are eligible in the plan but cannot hit the raw-query cache.** Risk retrieval uses `query=prompt`, while current deliberation uses `_build_enriched_retrieval_query()` and passes `query=enriched_query`; the prefilter bypasses raw queries shorter than `MIN_QUERY_LEN_FOR_CLASSIFICATION` before computing any cache key, returning `[]`. For prompts like `51`, current deliberation can classify `REQUEST:\n51`, but the proposed raw deliberation query will bypass and drift. Evidence: `moralstack/models/risk/estimator.py:463-465`, `moralstack/orchestration/deliberation_runner.py:468-474`, `moralstack/orchestration/deliberation_runner.py:293`, `moralstack/constitution/retriever.py:430-445`. Must change: make short raw prompts ineligible or require a confirmed raw cache hit before switching query; the test should assert fallback to enriched, not merely "golden flags divergence."

- **The cache is not process-global or provably warm across all supported controller construction paths.** `DomainPrefilter._cache` is an instance field, and each `ConstitutionStore` constructs its own `ConstitutionRetriever`; the bundled factory shares one store with the risk estimator, but public orchestration APIs accept `risk_estimator` and `constitution_store` independently, and `_estimate_risk()` can also return a default estimate without any retrieval. Evidence: `moralstack/constitution/retriever.py:304`, `moralstack/constitution/store.py:576-588`, `moralstack/pipeline/deliberation_stack.py:106-112`, `moralstack/runtime/orchestrator.py:78-84`, `moralstack/runtime/orchestrator.py:145-153`, `moralstack/orchestration/controller.py:858-860`. This blocks because a cold/no-shared-cache eligible request would silently switch deliberation from enriched to raw without saving the prefilter call. Must change: either prove same-store cache hit before reuse and fall back to enriched on miss, or add an explicit retriever API for "reuse cached raw prefilter only if present."

- **The primary design is not prefilter-only; it can reuse or alter domain-agent retrieval and persisted audit prompts.** `get_relevant_principles()` passes the same `query` to both `filter_domains()` and `_run_enhanced_agents_parallel()`, enhanced agents embed that query in their `USER QUERY` prompt, and `persist_llm_call()` stores the prompt. Enhanced agents are reused on the retriever and have their own prompt-keyed cache, so a raw delib query can hit risk-warmed agent cache entries too. Evidence: `moralstack/constitution/retriever.py:1176-1179`, `moralstack/constitution/retriever.py:1233`, `moralstack/constitution/retriever.py:756`, `moralstack/constitution/retriever.py:90-98`, `moralstack/constitution/retriever.py:1114-1115`, `moralstack/constitution/retriever.py:797-804`, `moralstack/constitution/retriever.py:1458-1460`. This violates the conservative "dedupe the domain PREFILTER ONLY" framing and the plan's claim that the only observable delta is a prefilter `CACHE_HIT`. Must change: make the decoupled `prefilter_query` approach primary, keeping the enriched query for domain agents, or explicitly broaden the plan and golden gate to cover agent cache hits, agent prompts, raw responses, and `llm_calls` audit drift.

## Non-blocking issues
- The cache-key prose is slightly wrong: the code hashes `f"{query}_{','.join(sorted(available_domains))}"`, not the Python `sorted(...)` list representation. Evidence: `moralstack/constitution/retriever.py:445`.

- The deferred phase-label issue remains real: constitution LLM-call persistence defaults `retrieval_phase` to `risk_routing`, and enhanced-agent persistence does not pass a deliberation phase. Evidence: `moralstack/constitution/retriever.py:61`, `moralstack/constitution/retriever.py:906-916`.

## Missing tests
- Add controller-level tests where risk uses no store, a different store, and a store whose retrieval fails; all must preserve enriched deliberation behavior unless a same-store raw cache hit is confirmed. Evidence for the current independent wiring: `moralstack/runtime/orchestrator.py:145-153`, `moralstack/orchestration/controller.py:172-188`.

- Add a short-prompt regression that asserts raw prompts below the prefilter threshold stay on the current enriched deliberation path. Existing tests already lock the short-query bypass behavior at the prefilter level. Evidence: `tests/test_domain_prefilter_cache.py:241-253`.

- Add persisted-audit assertions for eligible requests: `llm_calls.prompt`, domain-agent call counts, and domain-agent cache-hit behavior must either remain byte-identical or be explicitly accepted as drift. Evidence that prompts are persisted: `moralstack/constitution/retriever.py:90-98`.

- Existing request-analysis reuse coverage only asserts one store retrieval and does not assert the retrieval query, cache warmth, or controller risk-before-delib cache ownership. Evidence: `tests/test_request_analysis_reuse.py:109-125`.

## Risky assumptions
- "Risk always warms the cache before deliberation" is true for the bundled factory path but not for custom/public construction or `risk_estimator is None`. Evidence: `moralstack/pipeline/deliberation_stack.py:106-112`, `moralstack/runtime/orchestrator.py:78-84`, `moralstack/orchestration/controller.py:858-860`.

- "Only `final_action` can move" undercounts audit and retrieval-side drift: agent prompts, agent cache hits, `raw_response`, and `llm_calls` rows can change even when final policy fields do not. Evidence: `moralstack/constitution/retriever.py:756`, `moralstack/constitution/retriever.py:797-804`, `moralstack/constitution/retriever.py:906-916`.

- The eligibility proxy is correct for non-empty contract/history text, but it is incomplete for cache reuse because it ignores short-query bypass and cache ownership. Evidence: `moralstack/orchestration/deliberation_runner.py:271-293`, `moralstack/constitution/retriever.py:430-445`.

## Architecture concerns
- The safer architecture is to separate "prefilter input" from "agent semantic query." That preserves current deliberation agent prompts while allowing a cached raw prefilter domain set to be reused when actually present. Current code couples those inputs through one `query` parameter. Evidence: `moralstack/constitution/retriever.py:1176-1179`, `moralstack/constitution/retriever.py:1233`.

- `RequestAnalysisContext` has retrieval metadata but no explicit query-source/cache-provenance field, so the plan's observability story would rely on indirect `prefilter_cache_status` only. Evidence: `moralstack/orchestration/types.py:891-901`.

## Security/performance concerns
- No new developer-contract/history exposure is introduced by the narrowed no-contract/no-history branch, but the primary design changes persisted domain-agent prompt bodies for eligible requests, which is audit-relevant. Evidence: `moralstack/orchestration/deliberation_runner.py:271-293`, `moralstack/constitution/retriever.py:90-98`.

- Performance savings may exceed the stated "prefilter only" scope because raw delib agent calls can hit risk-warmed enhanced-agent caches. Evidence: `moralstack/constitution/retriever.py:797-804`, `moralstack/constitution/retriever.py:1458-1460`.

## Suggested plan changes
- Make Alternative 3 the primary design: add a separate prefilter/cache query while keeping `query=enriched_query` for domain agents.

- Add a cache-hit guard: if the raw prefilter cache is absent, unavailable, short-query-bypassed, or on a different store instance, fall back to the current enriched deliberation retrieval.

- Change the short-prompt test from "golden detects divergence" to "short prompts are ineligible and preserve current behavior."

- Expand the golden gate to include `llm_calls` action counts, domain-agent prompts, cache-hit metadata, critic violation fields, and principle IDs, not only final governance fields.

## Questions for Claude/User
- Is the intent strictly "remove only the second domain_prefilter LLM call," or is implicit reuse of risk-warmed domain-agent cache acceptable?

- Should custom orchestrator wiring with separate risk/store instances be supported by this optimization, or explicitly excluded?

- If the golden detects a divergent class, should the implementation contain a runtime fallback for that class, or should the entire optimization be abandoned?
