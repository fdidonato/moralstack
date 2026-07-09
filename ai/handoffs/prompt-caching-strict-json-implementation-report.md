# Implementation report — prompt-caching-strict-json (Part A)

_Returned verbatim by the `claude-implementer` (Claude Sonnet) sub-agent for the audit trail._

## Summary

**Goal:** Implement the approved Part A (prompt reordering for OpenAI prompt caching) plan across risk minis, critic, simulator, hindsight, perspectives, plus verify-only checks for DCCL/retriever, per `ai/handoffs/prompt-caching-strict-json-handoff.md` and `ai/plans/prompt-caching-strict-json.md` (Revision 5, APPROVED).

### Files modified

- `moralstack/models/risk/prompts.py` — moved STEP0-3/schema blocks from `INTENT_CONTEXT_PROMPT_TEMPLATE`, `OPERATIONAL_RISK_PROMPT_TEMPLATE` into `INTENT_CONTEXT_SYSTEM_PROMPT`/`OPERATIONAL_RISK_SYSTEM_PROMPT`; new `HARM_SIGNAL_STATIC_TEMPLATE`/`HARM_SIGNAL_USER_TEMPLATE`; user templates now dynamic-only.
- `moralstack/models/risk/signals/prompt_renderer.py` — `get_harm_signal_prompts()` composes the full static system (all 5 registry sections + base invariants) and returns a dynamic-only user template.
- `moralstack/models/risk/estimator.py` — persistence/call-site now uses the composed signals system prompt.
- `moralstack/prompts/critic_prompt.py` — new `CRITIC_FULL_SYSTEM_PROMPT` (base + `CRITIC_SHARED_RULES` + `OUTPUT_JSON_ONLY`); `CRITIC_FULL_TEMPLATE` dynamic-only.
- `moralstack/runtime/modules/critic_module.py` — full-critique call sites (both `generate_messages` and legacy branches) + `CriticReport.system_prompt` use `CRITIC_FULL_SYSTEM_PROMPT`; quick-check untouched (`CRITIC_SYSTEM_PROMPT`).
- `moralstack/prompts/simulator_prompt.py` — new `SIMULATOR_BATCH_SYSTEM_PROMPT`, `SIMULATOR_SEEDED_SYSTEM_PROMPT`, `_SIMULATOR_BASE_SYSTEM_PROMPT`, `SIMULATOR_SEEDED_STATIC_RUBRIC`; `SIMULATOR_FULL_TEMPLATE` dynamic-only.
- `moralstack/runtime/modules/simulator_module.py` — batch/seeded call sites + persistence use path-specific constants; `SEEDED_PROMPT_TEMPLATE` dynamic-only.
- `moralstack/prompts/hindsight_prompt.py` — new `HINDSIGHT_BATCH_SYSTEM_PROMPT`; `HINDSIGHT_BATCH_FULL_TEMPLATE` dynamic-only.
- `moralstack/runtime/modules/hindsight_module.py` — new `HINDSIGHT_SINGLE_SYSTEM_PROMPT`; single/batch/individual-aggregate call sites + result `system_prompt` fields use path-specific constants.
- `moralstack/prompts/perspectives_prompt.py` — `build_perspectives_system_prompt()` now static/ctx-independent (no args); `build_perspectives_user_prompt(name, instructions, context=None)` carries dynamic TURN CONTEXT.
- `moralstack/runtime/modules/perspective_module.py` — `evaluate`, `_evaluate_parallel`, `_evaluate_sequential`, `_evaluate_single_perspective`, `evaluate_single` thread `context` (keyword-passed, optional for backward compat).
- Docs: `docs/MORALSTACK_CODEBASE_INDEX.md` (§5.1 new), `docs/CODEBASE_FACTS.md` (6 new verified rows), `docs/traces/observability_db_to_ui.md`, `docs/modules/{critic,simulator,hindsight,perspectives,risk_estimator}.md`.

### Tests added / modified

- **New**: `tests/test_static_prefix_stability.py` (26 tests — risk x3 paths, critic full x2 + quick-check unchanged, simulator batch+seeded, hindsight single/batch/individual-aggregate, perspective evaluate+evaluate_single, A6 DCCL/retriever verify-only, edge cases).
- **Rewritten** (necessary consequence of the design, not scope creep): `tests/test_critic_prompt.py`, `tests/test_prompt_audit_fixes.py::TestPerspectivesFullModeRiskContext` (plan-named), plus `tests/test_perspective_contract_injection.py`, `tests/test_prompt8_contract_priority.py`, `tests/test_estimator_developer_contract_interpretation.py` (not explicitly named in the plan but broke as a direct, mechanical consequence of moving STEP0/examples system-ward — fixed to assert presence in the new location rather than deleted/weakened).

### Commands run (real output)

```
pytest tests/test_llm_parse_contract.py -v                                          → 9 passed
pytest tests/test_critic_prompt.py tests/test_prompt_audit_fixes.py tests/test_perspective_contract_injection.py -v → 16 passed
pytest tests/test_runtime_modules_retry_token_accounting.py tests/test_perspective_module.py -v → 54 passed
pytest tests/test_system_prompt_byte_equality.py -v                                  → 6 passed
pytest tests/test_static_prefix_stability.py -v                                      → 26 passed
pytest -q (full suite)                                                                → 2024 passed, 0 failed (213-251s)
pre_commit run --files <all 24 changed files>                                        → trim/EOF/ruff/black/mypy all Passed
```
(First full-suite run caught 2 regressions in `tests/test_runtime_modules_token_usage_source.py` — missed by earlier scoped `-k perspective` runs since the test names don't contain "perspective" — fixed by passing `context` as keyword instead of positional in `_evaluate_parallel`/`_evaluate_sequential`→`_evaluate_single_perspective`; re-ran full suite clean afterward.)

### Deviations from the plan

1. **Perspective `context` param made optional** (`context: DelibContext | None = None`) on `_evaluate_parallel`, `_evaluate_sequential`, `_evaluate_single_perspective`, `build_perspectives_user_prompt` — needed so the protected `tests/test_runtime_modules_retry_token_accounting.py::test_perspective_retry_failed_attempt_persists_billable_row_with_tokens` (calls `_evaluate_single_perspective("sys", perspective)` with 2 positional args) stays green **unchanged**, per the handoff's explicit instruction not to touch that file. When `context` is `None`, the user prompt omits the TURN CONTEXT block (old pre-A5a shape) — production call sites (`evaluate`, `evaluate_single`) always pass a real context.
2. **Test files rewritten beyond the plan's named list**: `test_perspective_contract_injection.py`, `test_prompt8_contract_priority.py`, `test_estimator_developer_contract_interpretation.py` — these pinned STEP0/examples-in-user-template or ctx-dependent-system-prompt-signature behavior that the approved design intentionally changes. Fixed with presence assertions in the new location rather than deleted.
3. Everything else matches the plan/handoff exactly (path-specific constants, quick-check untouched, DCCL/retriever verify-only with no code edits, `response_format` untouched everywhere).

### Residual problems / blockers

None. Full suite green (2024 passed), byte-equality and parse-contract regression guards green unchanged, `policy.py`/`base.py`/`structured_output.py`/`llm_parse_contract.py`/`retriever.py`/`dccl.py` untouched, pre-commit clean on all changed files.
