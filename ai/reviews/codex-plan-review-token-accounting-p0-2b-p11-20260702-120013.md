# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- **v7 would erase core module model attribution.** The plan’s replacement for `_module_model()` reads only `module.model` (`ai/plans/token-accounting-p0-2b-p11.md:2209`), but current code intentionally reads `module.policy.model` first (`moralstack/orchestration/deliberation_runner.py:212`, `moralstack/orchestration/deliberation_runner.py:220`, `moralstack/orchestration/deliberation_runner.py:222`). The runtime modules store the LLM policy as `self.policy` (`moralstack/runtime/modules/critic_module.py:340`, `moralstack/runtime/modules/simulator_module.py:295`, `moralstack/runtime/modules/hindsight_module.py:511`, `moralstack/runtime/modules/perspective_module.py:439`), and the persisted core module rows depend on `_module_model()` (`moralstack/orchestration/deliberation_runner.py:2906`, `moralstack/orchestration/deliberation_runner.py:3017`, `moralstack/orchestration/deliberation_runner.py:3128`, `moralstack/orchestration/deliberation_runner.py:3241`). This blocks because the plan’s stated per-effective-model accounting would become blank for critic/simulator/hindsight/perspectives. Must preserve the inner-policy lookup and only normalize the final missing result to `""`.

- **v7 would misattribute rewrite calls to the primary generation model.** The plan’s replacement for `_policy_llm_model_for_action()` removes the `action` parameter (`ai/plans/token-accounting-p0-2b-p11.md:2206`), but current code uses `rewrite_model` when `action == "rewrite"` (`moralstack/orchestration/deliberation_runner.py:200`, `moralstack/orchestration/deliberation_runner.py:204`, `moralstack/orchestration/deliberation_runner.py:205`). `OpenAIPolicy.rewrite_model` can differ from `model` (`moralstack/models/policy.py:55`, `moralstack/models/policy.py:143`, `moralstack/models/policy.py:162`), and `rewrite()` actually calls `generate(..., model_override=self._rewrite_model)` (`moralstack/models/policy.py:489`). Rewrite audit rows use this helper (`moralstack/orchestration/deliberation_runner.py:2571`, `moralstack/orchestration/deliberation_runner.py:2749`). Must keep the `action` argument and rewrite-model branch, returning `""` only when no effective model exists.

## Non-blocking issues
- The A4 wording is imprecise: with no DB path and no explicit mode, observability defaults to `file_only`, not disabled (`moralstack/observability/config.py:64`, `moralstack/observability/config.py:77`), and proxy still sets a run id in `file_only` (`moralstack/server/proxy.py:638`, `moralstack/server/proxy.py:650`). The “empty run id” case applies to explicit non-`file_only` mode without DB path (`moralstack/server/proxy.py:641`, `moralstack/server/proxy.py:649`).

## Missing tests
- Add focused tests that `_module_model()` preserves `module.policy.model` for critic/simulator/hindsight/perspectives (`moralstack/orchestration/deliberation_runner.py:220`, `moralstack/orchestration/deliberation_runner.py:222`).
- Add a test that `_policy_llm_model_for_action(policy, "rewrite")` returns `rewrite_model`, not `model` (`moralstack/orchestration/deliberation_runner.py:204`, `moralstack/models/policy.py:489`).
- Add proxy e2e setup coverage that `db_only` tests explicitly set a DB path; otherwise `_initialize_observability_run()` can return `""` (`moralstack/server/proxy.py:641`, `moralstack/server/proxy.py:649`).

## Risky assumptions
- The plan assumes the `db_only` e2e path will have active run/request persistence, but `DefaultPersistence.ensure_run_and_upsert_request()` returns without writing if no run id or DB path exists (`moralstack/persistence/default.py:58`, `moralstack/persistence/default.py:61`).
- The cache-hit plan mutates cached result objects with `from_cache`; the cache returns the exact stored object (`moralstack/utils/cache.py:101`) and stores the exact value object (`moralstack/utils/cache.py:122`).

## Architecture concerns
- Preserve the existing helper semantics instead of replacing helper bodies: the current helpers encode effective-model behavior, including rewrite override and module-inner-policy lookup (`moralstack/orchestration/deliberation_runner.py:200`, `moralstack/orchestration/deliberation_runner.py:212`).
- For cache-hit billing, prefer copying cached dataclass results or carrying cache status out-of-band; the affected result types are mutable dataclasses (`moralstack/runtime/modules/simulator_module.py:95`, `moralstack/runtime/modules/hindsight_module.py:267`, `moralstack/runtime/modules/perspective_module.py:154`).

## Security/performance concerns
- No direct regression found against decision/generation separation, hard-signal supremacy, or governed delivery; those invariants are defined in `PROJECT_SPEC.md:65`, `PROJECT_SPEC.md:70`, and `PROJECT_SPEC.md:80`.
- Keep all new accounting hooks non-propagating: observability must remain best-effort (`PROJECT_SPEC.md:78`, `.claude/rules/observability.md:11`). Current `ObservabilityService.emit()` already swallows queue errors (`moralstack/observability/service.py:43`, `moralstack/observability/service.py:46`), and the write queue remains lossy on `queue.Full` (`moralstack/observability/write_queue.py:179`, `moralstack/observability/write_queue.py:180`).

## Suggested plan changes
- Replace the v7 helper snippets with semantic-preserving normalizations: keep `_policy_llm_model_for_action(policy, action)` and its rewrite branch; keep `_module_model(module)` checking `module.policy.model`, then `module.model`; return `""` only at the final missing-model boundary.
- Add the two helper-specific tests before implementation, because they protect the plan’s core “per effective model” goal.
- Clarify that `MORALSTACK_OBSERVABILITY_MODE=db_only` requires `MORALSTACK_OBSERVABILITY_DB_PATH`; no-DB default behavior is `file_only`.

## Questions for Claude/User
- Should cache-hit marking copy cached result objects instead of mutating cached instances in place?
- Is explicit `db_only` without a DB path a supported disabled-observability configuration, or should tests and implementation treat it as misconfiguration?
