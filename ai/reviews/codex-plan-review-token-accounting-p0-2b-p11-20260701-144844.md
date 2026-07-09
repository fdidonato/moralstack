# Codex Plan Review

## Verdict
BLOCK

## Blocking issues

- Request totals would not be authoritative because speculative LLM calls can be emitted after the proposed finalization point. `_attach_trace_and_return()` currently runs before `process()` reaches its `finally` cleanup (`moralstack/orchestration/controller.py:312`, `moralstack/orchestration/controller.py:318`, `moralstack/orchestration/controller.py:320`, `moralstack/orchestration/controller.py:2675`, `moralstack/orchestration/controller.py:2677`). `SpeculativeOverlapHandle.abandon()` emits the speculative `llm_call` from a daemon thread after waiting on the future (`moralstack/orchestration/speculative_overlap.py:156`, `moralstack/orchestration/speculative_overlap.py:163`, `moralstack/orchestration/speculative_overlap.py:170`), and several routes call `abandon()` before returning (`moralstack/orchestration/controller.py:2528`, `moralstack/orchestration/controller.py:2553`, `moralstack/orchestration/controller.py:2594`). This blocks the plan because proxy `usage` can exclude real token spend. The plan must define deterministic accounting for used and discarded speculative calls before request finalization, without adding a blocking DB flush that would violate observability best-effort.

- The proposed `ObservabilityService.emit()` accumulator assumes `EVENT_LLM_CALL` means “one provider LLM call”, but current code uses `llm.call` for synthetic/reuse/diagnostic rows too. Output-protection leakage is persisted as an `llm.call` without a provider call or token usage (`moralstack/orchestration/deliberation_runner.py:2716`, `moralstack/orchestration/deliberation_runner.py:2718`, `moralstack/orchestration/deliberation_runner.py:2728`, `moralstack/orchestration/deliberation_runner.py:2732`). Speculative reuse is explicitly “no second policy LLM call” but is persisted as an `llm.call` (`moralstack/orchestration/deliberation_runner.py:2627`, `moralstack/orchestration/deliberation_runner.py:2634`, `moralstack/orchestration/deliberation_runner.py:2638`). This blocks the plan because `llm_call_count` and `missing_usage_count` would be inflated. The plan must add an explicit billable/provider-call discriminator, or exclude non-provider rows from token accumulation.

- Token provenance is not propagated through the runtime module result types the plan relies on. `GenerationResult` currently has token counts but no source (`moralstack/models/base.py:107`, `moralstack/models/base.py:120`, `moralstack/models/base.py:123`), and modules copy only counts into their own result objects: critic (`moralstack/runtime/modules/critic_module.py:80`, `moralstack/runtime/modules/critic_module.py:511`), simulator (`moralstack/runtime/modules/simulator_module.py:122`, `moralstack/runtime/modules/simulator_module.py:449`), hindsight (`moralstack/runtime/modules/hindsight_module.py:282`, `moralstack/runtime/modules/hindsight_module.py:786`), and perspectives (`moralstack/runtime/modules/perspective_module.py:86`, `moralstack/runtime/modules/perspective_module.py:173`, `moralstack/runtime/modules/perspective_module.py:734`). This blocks the stated target of exact/estimated/missing per module. The plan must add and aggregate source/count metadata through these dataclasses, not only through `GenerationResult` and `_token_usage_json_from_result()`.

- The plan omits token accounting for refusal-generation calls that consume the policy LLM. `RefusalGenerationResult` carries text, prompts, attempts, and leak metadata, but no token fields (`moralstack/orchestration/safe_refusal_generator.py:19`, `moralstack/orchestration/safe_refusal_generator.py:38`, `moralstack/orchestration/safe_refusal_generator.py:43`). `_llm_refusal_call()` discards the `GenerationResult` after extracting text (`moralstack/orchestration/safe_refusal_generator.py:540`, `moralstack/orchestration/safe_refusal_generator.py:548`, `moralstack/orchestration/safe_refusal_generator.py:550`), and the call sites persist refusal rows without `token_usage_json` (`moralstack/orchestration/response_assembler.py:304`, `moralstack/orchestration/response_assembler.py:315`, `moralstack/orchestration/response_assembler.py:321`; `moralstack/orchestration/final_revalidation.py:577`, `moralstack/orchestration/final_revalidation.py:590`, `moralstack/orchestration/final_revalidation.py:596`). The plan must include `safe_refusal_generator.py`, `response_assembler.py`, and `final_revalidation.py`.

- The accumulator key assumption is unsafe. `ProcessedRequest.request_id` has a UUID default, but it is a mutable caller-supplied dataclass field (`moralstack/orchestration/types.py:194`, `moralstack/orchestration/types.py:198`). SQLite uses `(run_id, request_id)` as the request identity (`moralstack/observability/sinks/sqlite_sink.py:99`, `moralstack/observability/sinks/sqlite_sink.py:123`, `moralstack/observability/sinks/sqlite_sink.py:128`). A dict keyed only by `request_id` can mix totals across runs or repeated caller-provided IDs. The accumulator must key by `(run_id, request_id)`.

## Non-blocking issues

- If new read methods are intended to mirror existing DB convenience APIs, `moralstack/persistence/db.py` is missing from the file list; it currently exposes `get_llm_calls_for_request()` by delegating to `SqliteReadStore` (`moralstack/persistence/db.py:76`, `moralstack/persistence/db.py:77`).

- The proxy delivery invariant is not directly threatened by changing `usage`: the current proxy finalizes governed text and builds synthetic responses without calling upstream (`moralstack/server/proxy.py:375`, `moralstack/server/proxy.py:378`, `moralstack/server/proxy.py:396`, `moralstack/server/proxy.py:401`). The risk is audit correctness, not delivered-answer provenance.

## Missing tests

- Add tests for source propagation through critic, simulator, hindsight, and perspectives result dataclasses and aggregation.

- Add tests for speculative generation accounting in both `join_for_consumer()` and `abandon()` paths, including discarded speculative calls and no double-counting of speculative-reuse rows.

- Add tests proving non-provider `llm.call` rows such as `output_protection` and speculative reuse do not increment provider call counts or missing-usage counts.

- Add tests for refusal-generation token usage, including anti-leak retry attempts.

- Add tests for accumulator isolation with the same `request_id` under different `run_id` values.

- Add proxy tests where `usage` includes or explicitly excludes discarded speculative calls, based on the product decision.

## Risky assumptions

- A2 is false as written: `request_id` is default-generated but not guaranteed unique by the type or DB schema (`moralstack/orchestration/types.py:198`, `moralstack/observability/sinks/sqlite_sink.py:123`).

- A1 is supported: `moralstack.orchestration.*` is under strict mypy (`pyproject.toml:140`, `pyproject.toml:141`).

- A4 is mostly true but best-effort, not absolute. Proxy run initialization can return an empty run id on initialization failure (`moralstack/server/proxy.py:617`, `moralstack/server/proxy.py:650`, `moralstack/server/proxy.py:654`), while request-row setup skips when `proxy_run_id` is empty (`moralstack/server/proxy.py:680`, `moralstack/server/proxy.py:681`).

## Architecture concerns

- The plan’s “authoritative request total” conflicts with the current observability model unless it separates provider-call accounting from persisted telemetry rows. Observability is explicitly best-effort in PROJECT_SPEC section 5 (`PROJECT_SPEC.md:78`) and `.claude/rules/observability.md` (`.claude/rules/observability.md:11`).

- Fixing the speculative timing problem by flushing or blocking on the write queue would risk the same invariant. The current service API is fire-and-forget and swallows emit failures (`moralstack/observability/service.py:43`, `moralstack/observability/service.py:47`, `moralstack/observability/service.py:50`, `moralstack/observability/service.py:57`).

- No direct issue found with decision/generation separation, hard-signal supremacy, prompt transparency, `core` overlay, or governed delivery, provided the implementation remains metadata-only (`PROJECT_SPEC.md:65`, `PROJECT_SPEC.md:68`, `PROJECT_SPEC.md:70`, `PROJECT_SPEC.md:76`, `PROJECT_SPEC.md:80`).

## Security/performance concerns

- Audit integrity risk: inaccurate `usage` in the OpenAI-compatible proxy can mislead downstream cost/audit systems even though it does not change the governed answer.

- Performance risk is moderate: a global accumulator with JSON parsing and locking on every `EVENT_LLM_CALL` needs load tests, especially because risk mini-estimators batch emit through `emit_batch()` (`moralstack/models/risk/estimator.py:71`, `moralstack/models/risk/estimator.py:75`, `moralstack/models/risk/estimator.py:704`, `moralstack/models/risk/estimator.py:708`).

## Suggested plan changes

- Key request totals by `(run_id, request_id)`.

- Add token source fields to all module result dataclasses that copy policy result token counts, and define aggregation rules for mixed exact/estimated/missing calls.

- Add token capture to `safe_refusal_generator.py`, `response_assembler.py`, `final_revalidation.py`, and `_speculative_generate()`.

- Introduce an explicit `billable_provider_call` or `token_usage_source="not_applicable"` concept so synthetic/reuse/diagnostic `llm.call` rows are not counted as missing provider usage.

- Rework finalization so it runs after all in-process provider-call accounting for the request is known, including speculative abandon outcomes, without relying on DB flush.

## Questions for Claude/User

- Should proxy `usage` include every internal provider call, including discarded speculative calls, embeddings, refusal retries, and compliance revalidation, or only calls that contributed to delivered text?

- Should non-provider audit rows remain in `llm_calls`, or should they move to `orchestration_events`?

- For request-level metadata, should mixed sources be exposed only as counts, or should there also be an overall source/status field?

- How should a real provider response with `total_tokens == 0` be distinguished from missing usage?
