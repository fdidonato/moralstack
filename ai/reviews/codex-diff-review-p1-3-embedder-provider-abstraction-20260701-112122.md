# Codex Diff Review

## Verdict
`BLOCK`

## Deviations from approved plan
- `.claude/agents/architect-planner.md` was modified even though the handoff explicitly listed `.claude/` under files not to modify.
- `moralstack/orchestration/controller.py` was changed outside `_maybe_store_in_ledger()` at `moralstack/orchestration/controller.py:1470`, despite the handoff allowing controller edits only in `_maybe_store_in_ledger()`.
- The implementation otherwise follows the main production plan: default local embedder, OpenAI opt-in, `LedgerResult.query_embedding`, keyword-only `prompt_embedding`, and provider config are present.

## Blocking issues
- Forbidden `.claude/` capability change grants the planner write access. This blocks because the approved handoff explicitly prohibited `.claude/` edits, and this change expands an agent that says it “Does NOT implement code” into one with `Write` capability. Evidence: `.claude/agents/architect-planner.md:7` states the planner does not implement code, while `.claude/agents/architect-planner.md:9` now includes `Write`. Required fix: revert `.claude/agents/architect-planner.md:9` to the pre-diff tool list, removing `Write`.

No runtime governance fail-open issue found in the reviewed code: ESCALATED ledger lookup/store guards remain first at `moralstack/orchestration/ledger.py:247` and `moralstack/orchestration/ledger.py:322`; cache application still goes through `is_safe_to_apply()` at `moralstack/orchestration/controller.py:2379`; ledger store failures remain non-fatal at `moralstack/orchestration/controller.py:701`; ledger construction failure still disables the fast path at `moralstack/sdk/bootstrap.py:152`.

## Non-blocking issues
- Out-of-scope formatting-only controller change at `moralstack/orchestration/controller.py:1470`. It is behavior-neutral, but violates the “controller.py except `_maybe_store_in_ledger()` only” handoff constraint.
- `LedgerResult` docstring does not document the new `query_embedding` field, even though the field is added at `moralstack/orchestration/ledger.py:150`.
- `ledger_similarity_threshold` docstring still lacks the required local-embedder recalibration warning at `moralstack/sdk/config.py:101`.

## Missing/weak tests
- Existing bootstrap test still calls `_bootstrap_pipeline()` without forcing `_FastEmbedWrapper.__init__` to raise `ImportError`, so a dev/CI environment with `fastembed` installed can still attempt model construction/download. Evidence: `tests/test_sdk_bootstrap.py:95` to `tests/test_sdk_bootstrap.py:97`.
- Acceptance criteria for `LedgerResult` hashability and `repr=False` are not covered. The added test only checks frozen assignment at `tests/test_ledger.py:557`.
- Invalid env provider is tested only at resolver level (`tests/test_local_embedder.py:191`), not through `_build_ledger()` returning `None` as specified for the graceful-disable path.

## Security issues
- Blocking: `.claude/agents/architect-planner.md:9` adds `Write` to a planning-only agent, expanding write capability outside the approved implementation scope.
- Non-blocking: `store(prompt_embedding=...)` stores the caller-supplied mutable list directly. Evidence: `moralstack/orchestration/ledger.py:351` to `moralstack/orchestration/ledger.py:359` assigns it to `embedding`, then `moralstack/orchestration/ledger.py:365` to `moralstack/orchestration/ledger.py:368` stores it. Consider copying with `list(prompt_embedding)` after validation.

## Performance issues
- Test performance/offline risk: `tests/test_sdk_bootstrap.py:95` to `tests/test_sdk_bootstrap.py:97` can instantiate `LocalEmbedder` without the required fastembed fallback patch.

## Maintainability issues
- Stale ledger store-flow docstring still says store always embeds the prompt at `moralstack/orchestration/ledger.py:175` to `moralstack/orchestration/ledger.py:179`.
- `ledger_embedding_model` documentation at `moralstack/sdk/config.py:114` to `moralstack/sdk/config.py:119` still reads as generic ledger model configuration, but the new local default ignores it.

## Required fixes
- Revert `.claude/agents/architect-planner.md` to remove `Write`.
- Revert the unrelated controller formatting change outside `_maybe_store_in_ledger()`.
- Patch `tests/test_sdk_bootstrap.py:95` to `tests/test_sdk_bootstrap.py:97` with the same `_FastEmbedWrapper.__init__` `ImportError` guard used by the other bootstrap tests.
- Add tests for `LedgerResult` hashability and `repr` exclusion of `query_embedding`.

## Suggested fixes
- Copy caller-supplied `prompt_embedding` before storing it.
- Update `LedgerResult`, store-flow, `ledger_similarity_threshold`, and `ledger_embedding_model` docstrings to reflect the new provider-neutral behavior.
- Add a `_build_ledger()` test for invalid `MORALSTACK_EMBEDDER_PROVIDER` returning `None` with a warning.