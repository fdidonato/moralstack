# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
1. No-provider refusal rows would still be counted as billable provider calls. The plan defaults `billable_provider_call=True` and only skips accumulator rows when the payload is explicitly `False` (`ai/plans/token-accounting-p0-2b-p11.md:885-897`). But the fast refusal path can run with `policy=None`: `OrchestrationController` accepts `policy: PolicyLLMProtocol | None` and passes it into `RefusalHandler` (`moralstack/orchestration/controller.py:142-146`, `moralstack/orchestration/controller.py:201-205`). `RefusalHandler.handle()` passes that value as `llm_client` and always emits an `llm_call` (`moralstack/orchestration/refusal_handler.py:93-104`, `moralstack/orchestration/refusal_handler.py:147-158`). When `llm_client is None`, the generator returns `attempts=0` (`moralstack/orchestration/safe_refusal_generator.py:599-605`). Final revalidation has the same shape: it sets `llm_client=None` when no callable policy exists, then still records a refusal `llm_call` (`moralstack/orchestration/final_revalidation.py:538-540`, `moralstack/orchestration/final_revalidation.py:557-567`, `moralstack/orchestration/final_revalidation.py:576-597`). This blocks because non-provider fallback diagnostics would inflate billable call counts and missing-usage counts. The plan must set `billable_provider_call=False` when `refusal_result.attempts == 0` for `refusal_handler.py` and `final_revalidation.py`, and add tests proving those rows are excluded from synchronous totals and SQL totals.

2. The plan still overclaims complete offline audit/billing reconstruction from a lossy queue. It labels the `llm_calls` SUM query as the canonical source when “garanzia di completezza (offline, billing, compliance)” is needed (`ai/plans/token-accounting-p0-2b-p11.md:1905-1912`), while also acknowledging that `usage_may_be_incomplete` does not cover `queue.Full` drops (`ai/plans/token-accounting-p0-2b-p11.md:2313-2319`). The current queue drops submissions on `queue.Full` (`moralstack/observability/write_queue.py:175-181`), and the persisted drop marker contains only a count plus run/request context, not the lost event identities or token amounts (`moralstack/observability/write_queue.py:190-203`, `moralstack/observability/write_queue.py:246-260`). This blocks because the plan presents an audit/cost surface as complete when the persistence layer is explicitly best-effort, and PROJECT_SPEC requires observability to remain best-effort rather than silently pretending to be stronger (`PROJECT_SPEC.md:78-79`, `.claude/rules/observability.md:11-13`). The plan must either bring P2 durable accounting into scope for token-bearing `llm.call` events, or downgrade all “canonical/complete/billing/compliance guarantee” language to “complete among persisted rows” and expose/test a caveat for queue-loss uncertainty.

## Non-blocking issues
- `moralstack/observability/__init__.py` is missing from the files-to-modify list. It currently re-exports event constants and SQLite helpers (`moralstack/observability/__init__.py:34-54`, `moralstack/observability/__init__.py:92-155`), so the new request-token event/writer may need to be exported for API consistency.
- `moralstack/persistence/db.py` currently mirrors read-store helpers such as `get_llm_calls_for_request` (`moralstack/persistence/db.py:72-77`). The plan treats token-total wrappers there as optional, but compatibility callers may expect the same mirror.
- The `GenerationResult` zero-token legacy behavior needs tighter wording. Today `token_usage_json()` returns `None` when `tokens_used == 0` and `prompt_tokens` is absent/falsy (`moralstack/models/base.py:123-127`); existing constructors omit any new source field (`tests/test_llm_parse_contract.py:134-138`). The plan should explicitly preserve or intentionally change the zero-token/no-source case.

## Missing tests
- Add no-client fast refusal and no-client final-revalidation tests asserting `attempts=0`, `billable_provider_call=0`, and exclusion from accumulator plus SQL totals.
- Add a queue-drop test or documented negative test showing that `SUM(...) FROM llm_calls` is not complete when an `EVENT_LLM_CALL` is dropped by the queue (`moralstack/observability/write_queue.py:175-181`).
- Add a `GenerationResult(tokens_used=0, prompt_tokens=None, token_usage_source omitted)` test to lock whether the legacy `None` JSON behavior remains.
- Add tests that any new `EVENT_REQUEST_TOKEN_USAGE_FINALIZED` dispatch remains swallowed by `ObservabilityService.emit()`/`emit_batch()` on failure, matching current non-raising behavior (`moralstack/observability/service.py:43-57`).

## Risky assumptions
- The plan assumes “provider LLM available” is the only refusal case needing token accounting, but current refusal persistence also records fallback/no-client paths (`moralstack/orchestration/refusal_handler.py:147-158`, `moralstack/orchestration/final_revalidation.py:576-597`).
- It assumes persisted `llm_calls` can serve billing/compliance completeness despite the current lossy write queue (`moralstack/observability/write_queue.py:175-181`).
- It relies on a source default for new token fields; current `GenerationResult` has no source and serializes by numeric presence only (`moralstack/models/base.py:106-127`).

## Architecture concerns
- I did not find a plan step that inherently changes decision/generation separation, hard-signal supremacy, prompt transparency, `core` overlay behavior, or governed delivery. Those invariants are defined in PROJECT_SPEC and rules (`PROJECT_SPEC.md:65-83`, `.claude/rules/decision-policy.md:12-20`, `.claude/rules/hard-signal-safety.md:11-14`, `.claude/rules/prompt-transparency.md:12-31`, `.claude/rules/constitution-domains.md:11-12`).
- The proxy currently delivers governed text via `finalize_delivery`, and streaming replays synthetic governed SSE rather than live upstream tokens (`moralstack/server/proxy.py:375-400`, `.claude/rules/governed-delivery.md:11-24`). Changing only the `usage` field is architecturally acceptable if it remains telemetry-only.
- The main architectural risk is semantic drift between synchronous in-process totals and durable totals under queue loss; the code’s queue semantics make that a data-quality boundary, not a minor documentation detail.

## Security/performance concerns
- No authz, secret-handling, or prompt-injection security issue was found in the plan scope.
- The planned JSON parsing in `ObservabilityService.emit_batch()` is on a hot path; current `emit_batch()` is intentionally a small enqueue wrapper (`moralstack/observability/service.py:50-57`). The plan’s batch-load tests are necessary.
- The queue-loss issue is both performance and audit-related: under load, dropped token events are possible by design (`moralstack/observability/write_queue.py:175-181`).

## Suggested plan changes
- Mark refusal rows with `billable_provider_call=False` when no LLM call was issued (`attempts == 0`) in both fast refusal and final revalidation.
- Replace “canonical complete billing/compliance” claims with “canonical over persisted `llm_calls` only”, unless P2 durable token persistence is brought into scope.
- Add `moralstack/observability/__init__.py` to the scope if the new event/writer are part of the public observability surface.
- Specify the exact default `token_usage_source` behavior for legacy `GenerationResult` objects, especially zero-token objects.

## Questions for Claude/User
- Should a refusal attempt where `policy.generate()` is called but fails before returning usage be counted as billable `missing`, or non-billable because no provider usage object was observed?
- Is billing/compliance allowed to rely on best-effort observability, or must token-bearing `llm.call` events get a non-lossy persistence path in this same work?
- Should no-client refusal diagnostics remain in `llm_calls` as `billable_provider_call=0`, or should they move to orchestration events instead?
