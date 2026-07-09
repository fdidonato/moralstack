# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- Perspective strict-schema/prefix work is underspecified. The plan recommends strict schema for five modules but omits Perspective from schema sourcing, while current parsing only requires `approval_score` and defaults `concerns`, `suggestions`, and `rationale`. Also, A5a says move REQUEST/RESPONSE out of system, but `risk_signals` is also dynamic and currently interpolated into the shared system body, so the prefix would still vary by request. Evidence: `moralstack/prompts/perspectives_prompt.py:78-98`, `moralstack/runtime/modules/perspective_module.py:283-346`, `moralstack/runtime/modules/perspective_module.py:532-537`. Must explicitly design Perspective's schema and move or consciously retain all dynamic fields, including risk context.

- Hindsight scope is wrong. The plan claims Hindsight has no `generate_messages` branch, but batch mode does use `generate_messages`; single-scenario mode has a separate prompt template inside `hindsight_module.py`, not `prompts/hindsight_prompt.py`. Evidence: `moralstack/runtime/modules/hindsight_module.py:310-352`, `moralstack/runtime/modules/hindsight_module.py:734-745`, `moralstack/runtime/modules/hindsight_module.py:824-845`. Must cover both batch and individual paths, with separate schema/message-shape tests.

- Simulator seeded mode is missing. The plan targets `build_simulator_prompt`, but `use_seeded_generation=True` uses `SEEDED_PROMPT_TEMPLATE` inside `simulator_module.py` and its own retry loop. Evidence: `moralstack/runtime/modules/simulator_module.py:160-185`, `moralstack/runtime/modules/simulator_module.py:527-556`. Must either refactor/test seeded mode too or explicitly exclude it with a behavior-preserving reason.

- Strict-schema optional/null handling conflicts with current parsers. The plan proposes required fields with nullable optionals, but current Pydantic models mostly use non-optional fields/defaults; nulls would not preserve "missing key" behavior without parser changes. Evidence: `moralstack/utils/structured_output.py:197-210`, `moralstack/utils/structured_output.py:224-238`, `moralstack/utils/structured_output.py:255-267`. Must derive schemas from actual validators or update validators and tests explicitly.

- Risk hard-signal schema coverage cannot rely on the cited fixture. Current calibration/merge consumes actual `q1_confidential` through `q17_minor_exploitation`, but `tests/test_llm_parse_contract.py` fixture uses legacy names such as `q1_deception_manipulation` and does not cover q13-q17. Evidence: `moralstack/models/risk/calibration.py:120-161`, `moralstack/models/risk/calibration.py:780-807`, `tests/test_llm_parse_contract.py:91-120`. This risks violating hard-signal supremacy in `PROJECT_SPEC.md:70-72` and `.claude/rules/hard-signal-safety.md:8-10`. Must add schema fixtures/tests for the actual parser keys.

- `message.refusal` handling remains too vague for a shared policy boundary. `_complete` currently strips `choice.message.content` to text and has no refusal channel; `generate` and `generate_messages` both return `GenerationResult` from this shared path. Evidence: `moralstack/models/base.py:83-104`, `moralstack/models/policy.py:233-244`, `moralstack/models/policy.py:318-349`, `moralstack/models/policy.py:390-421`. Must define the exact fail-closed data/exception path and prove delivered-answer generation is unchanged except for the refusal branch.

## Non-blocking issues
- Observability metadata still names only `json_object` contracts; strict schema would mislabel parse/audit rows unless `llm_parse_contract` is updated. Evidence: `moralstack/utils/llm_parse_contract.py:14-18`, `moralstack/utils/llm_parse_contract.py:61-116`.

- DCCL and retriever use direct SDK calls and their own persistence paths, not only `GenerationConfig`. Evidence: `moralstack/compliance/dccl.py:468-493`, `moralstack/constitution/retriever.py:594-607`, `moralstack/constitution/retriever.py:852-858`, `moralstack/constitution/retriever.py:1028-1034`.

## Missing tests
- Static-prefix tests must include Perspective risk context, Hindsight batch and individual modes, and Simulator seeded mode.

- Strict schema tests must cover the actual risk merge/calibration fields, not the current `test_llm_parse_contract.py` fixture. Evidence: `moralstack/models/risk/calibration.py:780-807`, `tests/test_llm_parse_contract.py:91-120`.

- Existing retry tests are malformed-output retry tests and will not cover transient-only retries after strict schema. Evidence: `tests/test_runtime_modules_retry_token_accounting.py:73-178`, `tests/test_perspective_module.py:544-580`.

- Existing response_format coverage is risk-only. Evidence: `tests/test_llm_parse_contract.py:82-86`.

## Risky assumptions
- The byte-equality invariant is policy-generator scoped, but touching `_complete` still crosses the delivered-answer path. Evidence: `tests/test_system_prompt_byte_equality.py:36-100`, `moralstack/models/policy.py:318-421`.

- OpenAI SDK support is only declared by dependency metadata; the installed venv version still needs verification. Evidence: `pyproject.toml:28`.

- Risk mini model overrides can defeat per-model cache stability. Evidence: `moralstack/models/risk/estimator.py:772-797`.

## Architecture concerns
- There is already a structured-output model layer for critic/simulator/hindsight; hand-authored schemas can drift from it. Evidence: `moralstack/utils/structured_output.py:197-267`.

- Developer contract/history placement must stay out of module system prompts. Current builders put contract in developer messages and context guidance in the user message. Evidence: `moralstack/runtime/modules/message_context.py:24-39`, `moralstack/models/risk/estimator.py:233-258`.

## Security/performance concerns
- A5a may increase raw token volume by re-sending draft/request per perspective; caching benefits need measurement, especially when prefix length or model choice prevents cache hits.

- Any strict-schema omission of `q8_self_harm_suicide`, `q10_weapons_explosives_toxins`, `q17_minor_exploitation`, `operational_risk`, or `risk_policy_action` can change structured refusal routing. Evidence: `moralstack/models/risk/calibration.py:120-161`, `moralstack/models/risk/calibration.py:560-563`, `moralstack/runtime/decision/safe_complete_policy.py:159-173`.

## Suggested plan changes
- Split Part A prompt reordering from Part B strict schema/refusal plumbing.

- Fix the module inventory: include Perspective schema, Hindsight batch/single, Simulator seeded mode, `llm_parse_contract.py`, and any parser changes required for nullable fields.

- Generate schemas from existing Pydantic models where possible; do not add nullable fields unless the parser accepts `None`.

- Keep DCCL/retriever on `json_object` in this iteration unless a separate direct-SDK schema plan is written.

## Questions for Claude/User
- Is Perspective A5a approved, and if yes should `risk_signals` move out of the shared system prompt too?

- Is strict JSON intended for Perspective, or only risk/critic/simulator/hindsight?

- Are parser-default behavior changes acceptable, or must strict schemas preserve today's missing-field defaults?

- Should Simulator seeded mode and Hindsight individual mode be refactored now or explicitly out of scope?
