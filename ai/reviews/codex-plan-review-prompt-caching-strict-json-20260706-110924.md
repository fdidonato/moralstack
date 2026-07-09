# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- A2 would merge the full critic schema into `CRITIC_SYSTEM_PROMPT` and then reuse that prompt for `quick_check`. That blocks because quick-check has a different JSON contract: its user prompt asks for `{"violated": ...}` while the full critic rules/schema require `decision`, `violated_hard`, `violations`, and `revision_guidance`. If the system schema wins, `quick_check` will parse no `violated` field, treat it as false, and pass; the fast path only escalates when `quick_result.passed` is false. Evidence: plan applies the moved schema to quick-check at `ai/plans/prompt-caching-strict-json.md:182-187`; full critic schema is in `moralstack/prompts/critic_prompt.py:70-86`; quick-check schema is different at `moralstack/runtime/modules/critic_module.py:175-190`; quick-check uses the shared `CRITIC_SYSTEM_PROMPT` at `moralstack/runtime/modules/critic_module.py:673-676`; parser defaults missing `violated` to false at `moralstack/runtime/modules/critic_module.py:679-685` and returns passed at `moralstack/runtime/modules/critic_module.py:706-709`; fast path only enters deliberation on `not quick_result.passed` at `moralstack/orchestration/deliberation_runner.py:968-972`. This risks hard-signal supremacy (`PROJECT_SPEC.md:70`, `.claude/rules/hard-signal-safety.md:11-12`). Must change A2 to use a path-specific full-critic system prompt and a separate quick-check system prompt, or leave quick-check's system contract unchanged.

## Non-blocking issues
- A4 should explicitly name the non-batch aggregate path. `evaluate()` falls into `_evaluate_individual` when batch mode is disabled or there is only one consequence, and that aggregate `HindsightResult` currently stores `system_prompt=HINDSIGHT_SYSTEM_PROMPT`. Evidence: branch at `moralstack/runtime/modules/hindsight_module.py:672-682`, individual prompt/result path at `moralstack/runtime/modules/hindsight_module.py:843-867`, and runner persistence reads `hindsight_result.system_prompt` at `moralstack/orchestration/deliberation_runner.py:3131-3149`.

## Missing tests
- Add a critic quick-check schema-isolation test: after A2, quick-check's captured system prompt must not contain the full critic `decision`/`violations` schema, and a hard-violation JSON with `violated=true` must still route to `passed=False`.
- Add a hindsight non-batch aggregate observability test for `len(consequences) == 1` and `use_batch_evaluation=False`, asserting the returned `HindsightResult.system_prompt` is the single-path prompt actually sent.

## Risky assumptions
- The plan assumes "critic x3 call sites" can share one constant. Current code disproves that because quick-check has a separate output shape and parser from full critique.
- `evaluate_single` cannot carry risk context through its public signature today; it builds `DelibContext(user_prompt=request, draft_text_full=response)` only. Evidence: `moralstack/runtime/modules/perspective_module.py:793-818`. Tests should assert request/response are preserved there, and risk defaults are explicit.

## Architecture concerns
- Prompt constants need to be path-contract constants, not module constants, when parser roots differ. Revision 3 applies that correctly to simulator and hindsight, but critic still violates it for quick-check.

## Security/performance concerns
- The critic quick-check issue is safety-relevant: a schema collision can silently turn a hard-constraint violation into `passed=True`, allowing fast-path delivery unless later gates catch it. That conflicts with hard-signal supremacy and governed delivery expectations (`PROJECT_SPEC.md:70-82`).

## Suggested plan changes
- Replace A2's single `CRITIC_SYSTEM_PROMPT` move with `CRITIC_FULL_SYSTEM_PROMPT` for full critique and `CRITIC_QUICK_SYSTEM_PROMPT` for quick-check.
- Add negative assertions that quick-check's system prompt does not include the full critique output schema.
- Explicitly update/test `HindsightEvaluator._evaluate_individual` result metadata along with `evaluate_scenario` and batch.

## Questions for Claude/User
- Should quick-check be optimized for prompt caching in Part A at all, or should it be left unchanged while only full critique gets the larger static prefix?
