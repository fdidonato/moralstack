# Codex Diff Review

## Verdict
`APPROVE_WITH_CHANGES`

## Deviations from approved plan
- Hindsight batch does not preserve all pre-change static prompt text. `HINDSIGHT_SYSTEM_PROMPT` still contains the base hindsight framing at `moralstack/runtime/modules/hindsight_module.py:310`, and the single path includes it via `HINDSIGHT_SINGLE_SYSTEM_PROMPT` at `moralstack/runtime/modules/hindsight_module.py:323`. The batch constant starts directly with the batch rubric at `moralstack/prompts/hindsight_prompt.py:27` and is sent at `moralstack/runtime/modules/hindsight_module.py:748`, so the batch path lost the old base framing despite the handoff requiring `concat(system,user)` content-equivalence at `ai/handoffs/prompt-caching-strict-json-handoff.md:162`.
- Perspective `context: DelibContext | None = None` is not a production-path defect. `evaluate()` constructs or threads `ctx` before `_evaluate_parallel` / `_evaluate_sequential` at `moralstack/runtime/modules/perspective_module.py:517` and passes it at `moralstack/runtime/modules/perspective_module.py:539`; `evaluate_single()` constructs `ctx` and passes it at `moralstack/runtime/modules/perspective_module.py:840`. Repository callers of the private methods outside this file are tests only.
- The three extra rewritten tests do not use `skip`/`xfail` and generally assert content in the new location. See `tests/test_perspective_contract_injection.py:6`, `tests/test_prompt8_contract_priority.py:33`, and `tests/test_estimator_developer_contract_interpretation.py:372`.

## Blocking issues
None identified.

## Non-blocking issues
- Hindsight batch prompt content loss is a real behavior drift. The dropped base text includes "Be rigorous and objective" and the core hindsight role framing at `moralstack/runtime/modules/hindsight_module.py:310`, while batch now sends only `HINDSIGHT_BATCH_SYSTEM_PROMPT` from `moralstack/prompts/hindsight_prompt.py:27`. This does not merge schemas or change parsers, but it violates the approved content-preservation requirement.

## Missing/weak tests
- Critic quick-check is not pinned as a literal byte snapshot. The handoff requires literal snapshots of both `system` and `prompt` at `ai/handoffs/prompt-caching-strict-json-handoff.md:133`, but the test imports the live `CRITIC_SYSTEM_PROMPT` at `tests/test_static_prefix_stability.py:43` and compares against it at `tests/test_static_prefix_stability.py:313`; the user prompt is only checked for containing `"violated"` at `tests/test_static_prefix_stability.py:317`.
- Content-preservation coverage is too weak to catch the Hindsight batch regression. The Hindsight tests check stability/schema separation at `tests/test_static_prefix_stability.py:480`, but do not assert the old base `HINDSIGHT_SYSTEM_PROMPT` text remains in the batch path.
- Plan-required integration/observability assertions are incomplete. The handoff asks for full-cycle final-action and observability split coverage at `ai/handoffs/prompt-caching-strict-json-handoff.md:140`, while the new test file is scoped mainly to static prefix/path collision checks at `tests/test_static_prefix_stability.py:4`.

## Security issues
None identified.

## Performance issues
None identified. Perspective system prompts are ctx-independent at `moralstack/prompts/perspectives_prompt.py:99`, and production perspective paths pass dynamic context in the user prompt.

## Maintainability issues
- The perspectives prompt module docstring still describes the old OPT-2 split with REQUEST/RESPONSE in the shared system prompt at `moralstack/prompts/perspectives_prompt.py:4`, which now contradicts the implementation.

## Required fixes
- Preserve the old Hindsight base framing in the batch path while keeping the batch `"evaluations"` schema separate from the single root-object schema.
- Add a Hindsight batch content-preservation test that would fail if `HINDSIGHT_SYSTEM_PROMPT` base text is absent from `concat(system,user)`.
- Replace the quick-check "unchanged" assertions with literal snapshots for both the quick-check system prompt and user prompt.

## Suggested fixes
- Update the stale perspectives module docstring to reflect the new static system / dynamic user split.
- Add the missing observability split assertions for runner-persisted `prompt` / `system_prompt` fields.
