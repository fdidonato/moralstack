# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- Simulator batch and seeded mode cannot safely share one expanded `SIMULATOR_SYSTEM_PROMPT`. The plan says that single constant will gain both the batch static prefix and seeded rubric/schema (`ai/plans/prompt-caching-strict-json.md:228`), but current code uses the same system prompt for both batch and seeded paths (`moralstack/runtime/modules/simulator_module.py:428`, `moralstack/runtime/modules/simulator_module.py:547`). Batch instructions require "exactly N" consequences (`moralstack/prompts/simulator_prompt.py:105`) and carry `num_scenarios` in the user prompt (`moralstack/prompts/simulator_prompt.py:164`), while seeded calls format only seed/request/response into `SEEDED_PROMPT_TEMPLATE` (`moralstack/runtime/modules/simulator_module.py:531`). Mixing those contracts would give seeded calls batch-only instructions without the corresponding dynamic fields. The plan must split batch and seeded system prompts, or otherwise prove each path receives only its own static contract.

- Hindsight single-scenario and batch evaluation cannot safely share one expanded `HINDSIGHT_SYSTEM_PROMPT`. The plan says that one constant will gain the rubric and output skeleton (`ai/plans/prompt-caching-strict-json.md:232`) and later claims both single and batch are covered (`ai/plans/prompt-caching-strict-json.md:360`). Current single-scenario output is a root object with fields like `safety`, `helpfulness`, `honesty`, and probabilities (`moralstack/runtime/modules/hindsight_module.py:344`), while batch output is rooted at `"evaluations"` (`moralstack/prompts/hindsight_prompt.py:23`). They are parsed by different validators (`moralstack/runtime/modules/hindsight_module.py:387`, `moralstack/runtime/modules/hindsight_module.py:441`) but both paths use the same system prompt (`moralstack/runtime/modules/hindsight_module.py:570`, `moralstack/runtime/modules/hindsight_module.py:720`). The plan must use path-specific system prompts or builders so the single and batch schemas never collide in the same LLM call.

- Perspective A5a misses the public `evaluate_single` path. The current `evaluate_single` builds `shared_system` by appending `build_perspectives_system_prompt(ctx)` (`moralstack/runtime/modules/perspective_module.py:793`, `moralstack/runtime/modules/perspective_module.py:818`), and that builder currently injects `REQUEST`, `RESPONSE`, and `RISK CONTEXT` into the system prompt (`moralstack/prompts/perspectives_prompt.py:77`, `moralstack/prompts/perspectives_prompt.py:91`). The per-perspective user prompt currently contains only perspective name/instructions, not request/response/risk (`moralstack/prompts/perspectives_prompt.py:117`). If A5a makes the shared system static but only updates the main multi-perspective path, `evaluate_single` loses the actual request/response/risk context. The plan must explicitly update `evaluate_single` and add a capture test proving its system is static and its user prompt still contains the dynamic context.

## Non-blocking issues
- The risk mini-estimator inventory is overly cautious about domain sensitivity. The current signal prompt renderer fills sections from the registry (`moralstack/models/risk/signals/prompt_renderer.py:119`) and leaves only `{request}` as the per-request placeholder (`moralstack/models/risk/signals/prompt_renderer.py:139`). That means "detected domain" is not currently injected into those rendered sections.

- If DCCL or constitution retriever prompts are touched, the plan needs explicit regression coverage. DCCL already has a static system prompt and dynamic later messages (`moralstack/compliance/dccl.py:481`, `moralstack/compliance/dccl.py:568`). The retriever hashes exact message payloads into `_domain_agent_cache_key` (`moralstack/constitution/retriever.py:169`), so changing system/user splits changes internal cache identity.

- The plan's prompt-caching API assumptions need tightening. It says `prompt_cache_key` is unnecessary (`ai/plans/prompt-caching-strict-json.md:216`), but current OpenAI prompt-caching guidance recommends using it consistently for shared prefixes. This is not a safety blocker, but it weakens the performance case.

## Missing tests
- Add simulator tests that separately capture batch and seeded messages. The seeded test must assert the system prompt does not contain batch-only "exactly N" or `num_scenarios` instructions, while the seeded user prompt still contains the selected perspective, request, and response (`moralstack/runtime/modules/simulator_module.py:167`, `moralstack/runtime/modules/simulator_module.py:531`).

- Add hindsight tests that separately capture single and batch messages. The single path must not receive the batch `"evaluations"` schema, and the batch path must not receive the single root-object schema (`moralstack/runtime/modules/hindsight_module.py:344`, `moralstack/prompts/hindsight_prompt.py:23`).

- Add a Perspective `evaluate_single` capture test. Existing tests only assert a result exists (`tests/test_perspective_module.py:387`, `tests/test_perspective_standalone.py:343`), not that request/response/risk remain present after A5a.

- Add persistence/observability assertions for any split prompt fields. The runner persists module `prompt` and `system_prompt` from critic, simulator, hindsight, and perspectives (`moralstack/orchestration/deliberation_runner.py:2904`, `moralstack/orchestration/deliberation_runner.py:3017`, `moralstack/orchestration/deliberation_runner.py:3131`, `moralstack/orchestration/deliberation_runner.py:3239`).

## Risky assumptions
- The plan assumes moving static instructions from user to system is behavior-preserving. That is not guaranteed because system messages have higher priority than user messages. This matters for safety-relevant module outputs even if final policy decisions remain structured.

- The static-prefix tests described as "system message equals module constant" (`ai/plans/prompt-caching-strict-json.md:280`) are too coarse for modules with multiple prompt contracts. A single constant is precisely the risky coupling for simulator and hindsight.

- Part B deferral is acceptable as scope because current configs continue to use `response_format={"type":"json_object"}` (`moralstack/runtime/modules/critic_module.py:351`, `moralstack/runtime/modules/hindsight_module.py:524`, `moralstack/models/risk/estimator.py:437`) and existing parsers remain in place. It should not be treated as a blocker for Part A.

## Architecture concerns
- Modules with multiple execution paths and output schemas need path-specific prompt contracts. Simulator has batch and seeded paths (`moralstack/runtime/modules/simulator_module.py:363`), and hindsight switches between batch and individual evaluation (`moralstack/runtime/modules/hindsight_module.py:672`). A single "module system prompt" is the wrong abstraction for those cases.

- Perspective prompt plumbing currently couples dynamic context to the shared system prompt (`moralstack/runtime/modules/perspective_module.py:511`, `moralstack/prompts/perspectives_prompt.py:77`). A5a should make dynamic-context propagation explicit across `evaluate`, `_evaluate_single_perspective`, and `evaluate_single`, not just move text between existing builders.

- The core invariants are not directly violated by the goal of Part A, but they depend on preserving prompt content and module outputs. Final action is still computed from structured bounds (`moralstack/runtime/decision/safe_complete_policy.py:264`), hard-signal bypass exists (`moralstack/orchestration/path_router.py:88`), developer/history messages remain separate in `build_module_messages` (`moralstack/runtime/modules/message_context.py:27`), and observability helpers swallow persistence failures (`moralstack/orchestration/persistence_helpers.py:25`). The proposed shared-prompt merges risk breaking those guarantees indirectly through malformed module outputs.

## Security/performance concerns
- Prompt-contract collisions can create malformed or misparsed module output. In this engine that can change deliberation evidence or trigger fail-closed behavior, which is safer than unsafe answering but still a production regression.

- A5a may resend request/response/risk once per perspective instead of once in the shared system prompt. That may be acceptable for cacheability, but the plan should measure `cached_tokens`, latency, and total input tokens rather than assume a net win.

## Suggested plan changes
- Replace the shared simulator prompt change with explicit batch and seeded system prompts, each paired with its own user template and tests.

- Replace the shared hindsight prompt change with explicit single and batch system prompts, each paired with its own expected JSON root schema and parser tests.

- Make Perspective A5a update every caller, including `evaluate_single`, and add tests proving dynamic request/response/risk moved out of the shared system without disappearing.

- Treat DCCL and retriever as audit/no-op unless the plan adds targeted tests for native message shape, cache-key impact, and persistence payloads.

- Correct the OpenAI prompt-caching assumptions and make `prompt_cache_key` either an explicit non-goal with rationale or a small measured follow-up.

## Questions for Claude/User
- Should simulator and hindsight use separate named constants per path, or should their builders return explicit `(system_prompt, user_prompt)` pairs?

- Is `PerspectiveModule.evaluate_single` considered a supported public API that must retain full request/response/risk context? The current tests suggest it is exercised but under-asserted.

- Are DCCL and constitution retriever intended to be modified in Part A, or only verified as already compatible with the static-prefix pattern?
