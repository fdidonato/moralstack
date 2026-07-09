# Codex Plan Review

## Verdict
APPROVE_WITH_CHANGES

## Blocking issues
None.

## Non-blocking issues
- DCCL is misdescribed as a "direct SDK path" in the plan. The code uses the injected policy via `generate_messages` or `generate`, with `GenerationConfig(response_format={"type": "json_object"})`, not a direct OpenAI SDK client call. This is documentation precision only because A6 is verify-only. Evidence: `moralstack/compliance/dccl.py:468-493`.
- `evaluate_single` risk context is not empty under current defaults: it builds `DelibContext(user_prompt=request, draft_text_full=response)`, `risk_score` defaults to `0.5`, and `get_risk_signals_str()` emits `risk_score=0.50` for nonnegative scores. Evidence: `moralstack/runtime/modules/perspective_module.py:816-818`, `moralstack/models/delib_context.py:38`, `moralstack/models/delib_context.py:65-70`.

## Missing tests
- The quick-check "byte-for-byte unchanged" test should capture both `system` and `prompt` passed to `policy.generate`. The `{"violated": ...}` contract lives in `QUICK_CHECK_PROMPT_TEMPLATE`, not in the system prompt, and the parser reads `data.get("violated", False)`. Evidence: `moralstack/runtime/modules/critic_module.py:175-190`, `moralstack/runtime/modules/critic_module.py:673-680`.
- Add a specific `evaluate_single` assertion for the default risk rendering expected after A5a. Current code defaults to `risk_score=0.50`; if the plan expects "none", that is a behavior change and should be explicit. Evidence: `moralstack/runtime/modules/perspective_module.py:816-818`, `moralstack/models/delib_context.py:65-70`.

## Risky assumptions
- Content-preservation tests will not prove full behavioral equivalence because Part A intentionally reorders dynamic context. For example, intent `constitution_context` currently appears before the Step 3 coherence check, while the plan moves dynamic context to the user suffix. Evidence: `moralstack/models/risk/prompts.py:265-268`.
- Performance gains should be measured after rollout. OpenAI docs confirm prompt caching is automatic and can reduce latency/cost, but hit rate still depends on exact prefixes and model routing: https://platform.openai.com/docs/guides/prompt-caching

## Architecture concerns
- Revision 4 correctly fixes the critic path-contract collision: full critique expects `decision`/`violated_hard`/`violations`/`revision_guidance`, while quick-check parses only `violated`. Evidence: `moralstack/prompts/critic_prompt.py:70-86`, `moralstack/runtime/modules/critic_module.py:679-680`.
- The Hindsight third path is now covered: `evaluate()` falls to `_evaluate_individual` when batch is disabled or there is one consequence, and that aggregate result persists `system_prompt` read by the runner. Evidence: `moralstack/runtime/modules/hindsight_module.py:672-682`, `moralstack/runtime/modules/hindsight_module.py:843-867`, `moralstack/orchestration/deliberation_runner.py:3148-3149`.
- No invariant blocker found. Final action remains structured-policy driven, not response-text driven. Evidence: `moralstack/runtime/decision/safe_complete_policy.py:158-170`, `moralstack/runtime/decision/safe_complete_policy.py:264-280`, `moralstack/orchestration/decision_service.py:844-846`.

## Security/performance concerns
- No new security blocker found. The main safety-sensitive risk was the critic quick-check collision; the plan now keeps quick-check separate from full critique. Evidence: `moralstack/runtime/modules/critic_module.py:673-680`, `moralstack/orchestration/deliberation_runner.py:970-978`.
- The retriever verify-only deferral is justified because its cache key hashes exact message payloads, so a split would change internal cache identity. Evidence: `moralstack/constitution/retriever.py:169-189`.

## Suggested plan changes
- Strengthen the quick-check test to assert the captured user prompt still contains the `{"violated": true/false}` contract and contains no full `decision`/`violations` schema.
- Align the `evaluate_single` test wording with the actual default risk rendering, or explicitly document any intended change.
- Fix the DCCL "direct SDK" wording and the lingering "opportunistic" wording for retriever/DCCL; A6 is verify-only.

## Questions for Claude/User
- Should `evaluate_single` render default risk as `risk_score=0.50` to preserve current `DelibContext` behavior, or should the new dynamic builder render `none` when no live risk estimate is available?
- Do you want quick-check byte equality pinned as a literal full prompt snapshot, or is schema-contract preservation sufficient?
