# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- Fast REFUSE token usage is still omitted. The plan fixes `response_assembler.py` and `final_revalidation.py`, but `_route_refuse` delegates to `RefusalHandler.handle()` (`moralstack/orchestration/controller.py:1563`), which calls `generate_llm_safe_refusal_detailed()` (`moralstack/orchestration/refusal_handler.py:94`) and emits an `llm.call` without `token_usage_json` (`moralstack/orchestration/refusal_handler.py:147`). The underlying LLM call currently discards all `GenerationResult` token fields and returns only text (`moralstack/orchestration/safe_refusal_generator.py:548`). This blocks “complete” refusal accounting. Add `refusal_handler.py` to scope and tests.

- The `billable_provider_call=False` list misses an existing non-provider `llm.call`. Fast-path output protection persists a diagnostic row with `module="output_protection"` / `action="leakage_detected (fast_path)"` (`moralstack/orchestration/deliberation_runner.py:909`). It is not a provider call, but the plan’s default/`COALESCE(...,1)` would count it as billable. This breaks accumulator/SQL parity and inflates call/missing counts. Audit all persisted diagnostic `llm.call` sites, not only the three listed.

- The speculative-discard design still violates the product decision that proxy `usage` includes all internal LLM calls. `abandon()` is explicitly non-blocking and emits discarded-call telemetry later in a daemon thread (`moralstack/orchestration/speculative_overlap.py:127`, `moralstack/orchestration/speculative_overlap.py:170`). The late routing paths call `abandon()` and then return through the normal funnel (`moralstack/orchestration/controller.py:2528`, `moralstack/orchestration/controller.py:2553`, `moralstack/orchestration/controller.py:2594`). The proposed `peek_request_token_usage()` snapshot would populate the HTTP response before the discarded call is counted. Either wait boundedly before proxy usage is finalized, or explicitly change the requirement to partial synchronous usage plus later durable reconciliation.

- Runtime module retries remain undercounted. Critic, simulator, hindsight, and perspective modules loop over LLM attempts but return/persist only the successful result’s token counts: critic retry loop (`moralstack/runtime/modules/critic_module.py:435`), simulator retry loop (`moralstack/runtime/modules/simulator_module.py:419`), hindsight retry loop (`moralstack/runtime/modules/hindsight_module.py:723`), perspective retry loop (`moralstack/runtime/modules/perspective_module.py:707`). Failed parse/validation attempts still consume provider tokens. The plan only propagates `token_usage_source` on final dataclasses, so it cannot satisfy “all internal LLM calls” accounting.

- The plan is internally inconsistent on zero-token usage semantics. The product decision says a present provider `usage` object with zero values is not missing, but Decisione 1 says `total_tokens==0` without split becomes `missing` (`ai/plans/token-accounting-p0-2b-p11.md:237`), and the proposed test name locks that opposite behavior (`ai/plans/token-accounting-p0-2b-p11.md:1209`). Current legacy behavior collapses zero/no-prompt to `None` (`moralstack/models/base.py:123`). The plan must choose one rule and align implementation/tests before it is implementable.

## Non-blocking issues
- `GovernanceMetadata` additions do not address the SDK `GovernedResponse.usage` property, which still returns only `openai_response.usage` and otherwise `None` (`moralstack/sdk/response.py:269`). Governed responses are constructed with `openai_response=None` (`moralstack/sdk/response.py:295`). Clarify whether SDK users should read metadata only.

- UI/reporting surfaces that already read `llm_calls` may not expose the new token columns unless updated. For example, the UI reads request LLM calls through `get_llm_calls_for_request` (`moralstack/ui/app.py:2613`), while the plan focuses on read-store methods and docs.

## Missing tests
- Fast REFUSE path via `RefusalHandler.handle()` must assert `token_usage_json` is persisted.

- Fast-path `output_protection/leakage_detected (fast_path)` must be non-billable.

- Proxy response `usage` must be tested when a discarded speculative call is still pending, not only after DB reconciliation.

- Failed-then-success retry cases must be tested for critic, simulator, hindsight, and perspective modules.

- Zero-token provider usage tests must match the final product decision.

- Additional `abandon()` callers should be covered, including compliance/domain-excluded paths (`moralstack/orchestration/controller.py:1378`, `moralstack/orchestration/controller.py:2213`, `moralstack/orchestration/controller.py:2258`).

## Risky assumptions
- The plan assumes a synchronous in-process snapshot is adequate for proxy usage, but current speculative discard behavior makes that snapshot incomplete.

- Defaulting missing `billable_provider_call` to billable is fragile because `llm.call` is also used for diagnostics.

- The run/request context is carried by `ContextVar`s (`moralstack/observability/context.py:10`). Proxy setup sets run id separately from per-request request id (`moralstack/server/proxy.py:650`, `moralstack/server/proxy.py:315`), so context propagation should be tested under the actual server execution model.

## Architecture concerns
- `llm.call` is overloaded as both provider-call telemetry and orchestration diagnostics. `billable_provider_call` helps, but only if every producer is audited and future producers use a shared helper.

- For precise accounting, provider-call token capture should happen at each actual LLM attempt boundary. Aggregating only final module dataclass fields hides failed retries and makes “complete” accounting depend on module-specific control flow.

- The accumulator, SQLite schema, runtime token-source propagation, refusal accounting, proxy behavior, and speculative-finalization timer are tightly coupled. The PR sequence should make each intermediate state internally consistent, especially around `billable_provider_call`.

## Security/performance concerns
- No direct authz/secret-handling regression found.

- Observability must remain best-effort: current service emission swallows exceptions (`moralstack/observability/service.py:43`, `moralstack/observability/service.py:50`), and SQLite sink writes are guarded (`moralstack/observability/sinks/sqlite_sink.py:1425`). The new accumulator, JSON parsing, and timers must preserve that invariant.

- A daemon `Timer` per pending speculative discard can accumulate under load. The plan needs cleanup/idempotency tests and a cap/backpressure story.

## Suggested plan changes
- Add `moralstack/orchestration/refusal_handler.py` to the refusal-token plumbing and tests.

- Audit all persisted diagnostic `llm.call` rows and mark every non-provider row `billable_provider_call=False`, including fast-path output protection.

- Resolve speculative proxy semantics: either boundedly wait for pending discarded calls before returning usage, or document and expose partial usage explicitly.

- Add per-attempt token accumulation for runtime module retries, or explicitly reduce the goal from “all internal LLM calls” to “successful module result calls.”

- Fix the zero-token `usage` rule and rename/remove contradictory tests.

## Questions for Claude/User
- Should OpenAI-compatible proxy `usage` be allowed to be partial when speculative discard accounting finishes after the HTTP response?

- Should failed parse/validation retry attempts in critic/simulator/perspective/hindsight count toward request totals?

- Should `GovernedResponse.usage` mirror the new metadata totals, or is `GovernanceMetadata` the only supported SDK token surface?

- Are diagnostic `llm.call` rows expected to remain in `llm_calls`, or should future diagnostics move to a separate event type?
