# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- The proposed characterization test for `write_queue.py` is false for current behavior. The plan says the new sink contract test applies "per ciascuna funzione in `sink.py`/`write_queue.py`" and that `get_obs().emit()` raising should return `False`/`None` without propagation (`ai/plans/remove-deprecated-persistence-package.md:273`, `ai/plans/remove-deprecated-persistence-package.md:280`). But the async helpers directly call `get_obs().emit(envelope)` after only missing-context early returns (`moralstack/persistence/write_queue.py:36`, `moralstack/persistence/write_queue.py:41`, `moralstack/persistence/write_queue.py:74`, `moralstack/persistence/write_queue.py:77`, `moralstack/persistence/write_queue.py:82`, `moralstack/persistence/write_queue.py:96`, `moralstack/persistence/write_queue.py:99`, `moralstack/persistence/write_queue.py:103`, `moralstack/persistence/write_queue.py:113`). `ObservabilityService.emit()` itself catches queue failures (`moralstack/observability/service.py:44`, `moralstack/observability/service.py:61`), but the helper-level contract in the plan is still wrong if `get_obs()` or a monkeypatched `emit` raises. This blocks because the plan requires characterization tests before refactoring; those tests either fail against current code or force an unacknowledged behavior change in a P0 observability area (`PROJECT_SPEC.md:78`, `.claude/rules/observability.md:11`). The plan must split sync `sink.py` contracts from async `write_queue.py` contracts, or explicitly propose and test a behavior change.

## Non-blocking issues
- The docs cleanup list is incomplete relative to the plan's own acceptance grep. `docs/architecture_spec.md` still names `moralstack.persistence.context` (`docs/architecture_spec.md:1250`), and `moralstack/observability/context.py` still says it was migrated from `moralstack.persistence.context` (`moralstack/observability/context.py:5`). The acceptance criterion says `grep -r "moralstack.persistence" moralstack/ scripts/ tests/ docs/` should be clean except changelog notes (`ai/plans/remove-deprecated-persistence-package.md:429`).
- `persistence/db.py` is not just direct import aliasing: it creates a module-level `SqliteReadStore` and wrapper functions (`moralstack/persistence/db.py:61`, `moralstack/persistence/db.py:64`, `moralstack/persistence/db.py:104`). That does not invalidate removal, but the plan should describe it accurately.
- The plan says external-consumer breakage is mitigated by documenting the removal in the changelog (`ai/plans/remove-deprecated-persistence-package.md:107`), but the files/docs section does not list `CHANGELOG.md`.

## Missing tests
- `test_benchmark_moralstack_imports.py` as described would not exercise the benchmark's lazy imports. The persistence imports are inside runtime branches (`scripts/benchmark_moralstack.py:2456`, `scripts/benchmark_moralstack.py:2615`, `scripts/benchmark_moralstack.py:2634`, `scripts/benchmark_moralstack.py:2751`), so `import scripts.benchmark_moralstack` only catches top-level import errors.
- Add explicit async-helper characterization after correcting the contract: missing context returns before emit (`moralstack/persistence/write_queue.py:41`, `moralstack/persistence/write_queue.py:82`, `moralstack/persistence/write_queue.py:103`), and current call sites swallow telemetry failures where they wrap helpers (`moralstack/orchestration/persistence_helpers.py:40`, `moralstack/orchestration/persistence_helpers.py:58`).
- Keep or add a proxy audit assertion after the import relocation. Proxy emits governed-delivery audit markers via `persist_orchestration_event` (`moralstack/server/proxy.py:429`, `moralstack/server/proxy.py:443`, `moralstack/server/proxy.py:444`), which is coupled to the governed-delivery invariant (`.claude/rules/governed-delivery.md:11`).

## Risky assumptions
- "Dead code" only holds inside this repository. `PersistenceWriteQueue` and `get_write_queue` are public exports today (`moralstack/persistence/__init__.py:49`, `moralstack/persistence/__init__.py:73`, `moralstack/persistence/__init__.py:122`, `moralstack/persistence/__init__.py:123`), and package discovery includes all `moralstack*` packages (`pyproject.toml:76`).
- Removing `moralstack/persistence/` is a public package break despite the deprecation warning (`moralstack/persistence/__init__.py:13`). The plan should make the release/version decision explicit.

## Architecture concerns
- Moving `PersistencePort`/`DefaultPersistence`/`NullPersistence` into orchestration fits current ownership: controller accepts `PersistencePort` (`moralstack/orchestration/controller.py:158`), defaults to `NullPersistence` (`moralstack/orchestration/controller.py:165`), and runtime injects `DefaultPersistence` (`moralstack/runtime/orchestrator.py:181`).
- Keep the compliance-layer import lazy unless tests are deliberately rewritten around the new binding model. Current controller imports `persist_orchestration_event` inside the compliance path (`moralstack/orchestration/controller.py:1136`), and tests patch the old lazy target (`tests/test_compliance_fast_path.py:129`, `tests/test_observability_contract.py:167`).

## Security/performance concerns
- No direct authz or secret-handling issue found in the plan.
- Performance risk is low if `emit_helpers.py` preserves `ObservabilityService.emit()` / `emit_batch()` fire-and-forget behavior (`moralstack/observability/service.py:44`, `moralstack/observability/service.py:66`). Replacing helpers with synchronous DB writes would be a regression; the plan should keep that out of scope.

## Suggested plan changes
- Fix §7.2 before implementation: characterize `sink.py` and `write_queue.py` separately, and decide explicitly whether async helper failures remain call-site-swallowed or become helper-swallowed.
- Add `docs/architecture_spec.md`, `moralstack/observability/context.py`, and `CHANGELOG.md` to the explicit file list.
- Replace the benchmark "module import" test with a test that executes the lazy persistence-import branches or an AST/static check plus a focused branch test.
- Keep the controller compliance import lazy or update the patching tests with explicit call/capture assertions after rebinding.

## Questions for Claude/User
- Is removal of the deprecated public `moralstack.persistence` package acceptable in this release, or should it survive one more release with stronger warnings?
- Should `async_persist_*` preserve today's helper behavior, or should this PR intentionally fix helper-level swallowing under the observability invariant?
- Should historical `ai/` artifacts be excluded from cleanup permanently, while `docs/`, `moralstack/`, `scripts/`, and `tests/` must be clean?
