# Codex Plan Review

## Verdict

APPROVE_WITH_CHANGES

## Blocking issues

None.

## Non-blocking issues

- The docs update list misses at least one broken link created by deleting `docs/modules/persistence.md`: `docs/modules/benchmark.md:110` links to `./persistence.md`, while the plan deletes that file at `ai/plans/remove-deprecated-persistence-package.md:486-488`. The plan's docs list at `ai/plans/remove-deprecated-persistence-package.md:522-570` does not name `docs/modules/benchmark.md`.

- The public removal blast radius is broader than the plan's explicit changelog bullets. `moralstack/persistence/db.py` exposes read aliases beyond the list called out in the plan, including token usage and UI/report helpers at `moralstack/persistence/db.py:80-118`; `moralstack/persistence/config.py:11` also exposes `PersistMode`. The plan's read-helper list stops short at `ai/plans/remove-deprecated-persistence-package.md:232-239`, and the changelog instruction only explicitly names `PersistenceWriteQueue` / `get_write_queue` at `ai/plans/remove-deprecated-persistence-package.md:562-570`.

- The observability rule-scope fix is too narrow. `.claude/rules/observability.md:1-5` currently auto-loads only for `moralstack/observability/**` and `moralstack/persistence/**`; the plan adds the three new DI files at `ai/plans/remove-deprecated-persistence-package.md:490-495`, but it also edits `moralstack/orchestration/persistence_helpers.py`, which contains telemetry exception-swallowing behavior at `moralstack/orchestration/persistence_helpers.py:40-47` and `moralstack/orchestration/persistence_helpers.py:58-68`.

## Missing tests

- Add a static docs/import check that catches both dotted and path/link references, not only `moralstack.persistence`. The current acceptance grep at `ai/plans/remove-deprecated-persistence-package.md:607-609` would miss `docs/modules/benchmark.md:110` because it references `./persistence.md`, not `moralstack.persistence`.

- If the removal of all `persistence.db` read aliases is intentional, add either a targeted migration/changelog assertion or expand the planned benchmark/static import test to cover removed submodule symbols. Current public aliases include `get_token_usage_totals`, `get_token_usage_breakdown`, `get_runs_page`, `get_request_domains`, and `get_models_used_for_run` at `moralstack/persistence/db.py:80-118`.

## Risky assumptions

- The external-consumer assumption is correctly marked unverifiable at `ai/plans/remove-deprecated-persistence-package.md:116-119`, but the plan under-documents what external consumers may lose. Packaging includes all `moralstack*` packages at `pyproject.toml:74-76`, and `moralstack/persistence/__init__.py:80-127` plus `moralstack/persistence/db.py:80-118` expose more than the internally used helpers.

- The plan correctly treats the `core` domain persistence issue as pre-existing and out of scope. Current code passes `request.get_domain()` into the initial upsert at `moralstack/orchestration/controller.py:2078-2086`, `DefaultPersistence` writes that `domain` at `moralstack/persistence/default.py:68-75`, and normalization only happens later at `moralstack/orchestration/controller.py:2267-2274`.

## Architecture concerns

- The proposed `emit_helpers.py` replacement is a submodule-only API unless `moralstack/observability/__init__.py` is also updated. Current `__all__` exports do not include `persist_*` or `async_persist_*` helpers at `moralstack/observability/__init__.py:92-155`. That is fine, but the migration docs and changelog should say the supported replacement path is `moralstack.observability.emit_helpers`.

- Moving `DefaultPersistence` / `PersistencePort` into orchestration is consistent with current controller DI usage at `moralstack/orchestration/controller.py:158-165` and runtime wiring at `moralstack/runtime/orchestrator.py:181`.

## Security/performance concerns

No new security or performance concern found if the move is strictly import-path relocation. The key safety invariant is observability best-effort behavior: sync helpers currently catch `get_obs().emit()` failures at `moralstack/persistence/sink.py:113-120`, while async helpers currently rely on `ObservabilityService.emit()` swallowing queue failures at `moralstack/observability/service.py:61-64`.

## Suggested plan changes

- Add `docs/modules/benchmark.md` to §9 and broaden the acceptance search to cover `moralstack.persistence`, `moralstack/persistence`, and `persistence.md`.

- Expand the CHANGELOG/migration note to explicitly cover `PersistMode` and the full `persistence.db` read-helper removal, or state that the whole deprecated package and all submodules are removed without per-symbol compatibility.

- Add `moralstack/orchestration/persistence_helpers.py` to `.claude/rules/observability.md` frontmatter; consider `moralstack/orchestration/default_event_emitter.py` too, since it delegates orchestration event telemetry at `moralstack/orchestration/default_event_emitter.py:14-15`.

- State explicitly whether `moralstack.observability.emit_helpers` is the only new helper import path, or whether `moralstack/observability/__init__.py` should re-export the helpers.

## Questions for Claude/User

- Should historical docs such as `docs/refactoring_diary.md:115-118` keep old `moralstack/persistence/*` paths as historical record, or be annotated during this cleanup?

- Is the intended migration guidance "use `moralstack.observability.emit_helpers`" for helper functions, or should top-level `moralstack.observability` also expose those helpers for external users?
