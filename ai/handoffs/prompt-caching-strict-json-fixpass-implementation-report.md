# Fix-pass implementation report — prompt-caching-strict-json

_Returned verbatim by the `claude-implementer` (Claude Sonnet) sub-agent. Follow-up pass closing the Codex
diff-review required/suggested fixes (`ai/reviews/codex-diff-review-prompt-caching-strict-json-20260706-125903.md`)._

## Files modified
- `moralstack/prompts/hindsight_prompt.py` — REQUIRED 1. Added `_HINDSIGHT_BASE_FRAMING` (private literal
  duplicate of `HINDSIGHT_SYSTEM_PROMPT`, `hindsight_module.py:310`) prepended to `HINDSIGHT_BATCH_SYSTEM_PROMPT`,
  mirroring `HINDSIGHT_SINGLE_SYSTEM_PROMPT`.
- `moralstack/prompts/perspectives_prompt.py` — SUGGESTED 4. Rewrote the stale OPT-2 module docstring to the
  A5a static-system/dynamic-user split.
- `tests/test_static_prefix_stability.py` — REQUIRED 2, 3, SUGGESTED 5.

## Tests added / modified
- NEW `test_batch_content_preservation_base_framing_present` (REQUIRED 2): asserts batch `concat(system,prompt)`
  contains the three base-framing substrings + exact drift-guard `HINDSIGHT_SYSTEM_PROMPT in HINDSIGHT_BATCH_SYSTEM_PROMPT`
  + batch/single schema-separation.
- MODIFIED `test_quick_check_system_and_user_unchanged_and_no_full_schema` (REQUIRED 3): class-level literal
  snapshots `_EXPECTED_QUICK_CHECK_SYSTEM`/`_EXPECTED_QUICK_CHECK_PROMPT`, exact equality asserts, kept prior
  no-schema-collision assertions.
- `test_quick_check_hard_violation_still_fails` — untouched.
- NEW `TestObservabilityPersistedFieldsReflectStaticDynamicSplit` (5 tests, SUGGESTED 5): critic, simulator batch,
  hindsight batch, hindsight single/individual, perspectives — module-result `system_prompt`/`prompt` equal the
  path-specific static constant / stay dynamic-only (the fields the runner persists via getattr at
  `deliberation_runner.py:2904/3017/3131/3239`, read-only).

## Commands run (real output)
```
pytest tests/test_static_prefix_stability.py -v                                                      → 32 passed
pytest tests/test_llm_parse_contract.py tests/test_system_prompt_byte_equality.py tests/test_runtime_modules_retry_token_accounting.py -v → 21 passed
pytest (full)                                                                                          → 2030 passed, 102 warnings in 217.89s
pre_commit run --files hindsight_prompt.py perspectives_prompt.py test_static_prefix_stability.py     → all hooks Passed
```
Ad-hoc: confirmed pre-fix `HINDSIGHT_BATCH_SYSTEM_PROMPT` (reconstructed) does NOT contain the base-framing
substrings → REQUIRED 2 test would have failed before REQUIRED 1.

## Deviations from the plan
- REQUIRED 1 could NOT import `HINDSIGHT_SYSTEM_PROMPT` from `hindsight_module.py` into `hindsight_prompt.py`:
  `hindsight_module.py:26` imports `HINDSIGHT_BATCH_SYSTEM_PROMPT` from `hindsight_prompt.py` at load time, before
  its own `HINDSIGHT_SYSTEM_PROMPT` (`:310`) is defined → a back-import is a genuine circular import. Resolved by
  a private literal duplicate `_HINDSIGHT_BASE_FRAMING` in `hindsight_prompt.py`, documented in-code, guarded by
  the exact-substring drift test. Did not touch `hindsight_module.py`.
- No other deviations.

## Residual problems / blockers
None blocking. One documented DRY drift-risk: `_HINDSIGHT_BASE_FRAMING` and `HINDSIGHT_SYSTEM_PROMPT` are two
literal copies kept in sync only by the new test; a durable single-source-of-truth (shared low-level module)
was out of the fix-pass allowed-files scope. Worth a follow-up decision.

HEAD still `f02e540`, no commit; only the three allowed files changed.
