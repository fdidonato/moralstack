# Codex Plan Review

## Verdict

BLOCK

## Blocking issues

- The plan relocates P0 observability persistence code outside the rule path that currently protects it. The plan moves `DefaultPersistence` to `moralstack/orchestration/default_persistence.py` (`ai/plans/remove-deprecated-persistence-package.md:147-157`) and deletes `moralstack/persistence/` (`ai/plans/remove-deprecated-persistence-package.md:413-415`), but `.claude/rules/observability.md` only applies to `moralstack/observability/**` and `moralstack/persistence/**` (`.claude/rules/observability.md:1-5`). That matters because `DefaultPersistence` is exactly where request DB writes are swallowed/logged (`moralstack/persistence/default.py:63-89`), and PROJECT_SPEC says the full invariant text is path-scoped under `.claude/rules/` (`PROJECT_SPEC.md:58-83`). The plan must add the relocated persistence files, or an appropriate `moralstack/orchestration/*persistence*.py` glob, to `.claude/rules/observability.md`.

- The proposed `core` domain characterization is ambiguous and may not pass against current code before the refactor. The plan requires new tests before moving files (`ai/plans/remove-deprecated-persistence-package.md:268-283`) and says `domain == "core"` must not be persisted (`ai/plans/remove-deprecated-persistence-package.md:273-277`). Current controller initially persists `request.get_domain()` directly (`moralstack/orchestration/controller.py:2078-2083`), and `DefaultPersistence` writes that `domain` argument into `upsert_request` (`moralstack/persistence/default.py:68-75`). The later normalization only runs after risk assessment and skips updates when `_normalize_runtime_domain("core")` returns `None` (`moralstack/orchestration/controller.py:123-136`, `moralstack/orchestration/controller.py:2267-2274`). The plan must either scope the new test to risk-detected `core` only, or explicitly add a separate pre-refactor bugfix for request-supplied `core`.

## Non-blocking issues

- The docs file list is incomplete. `README.md` still lists `moralstack/persistence/` as a main package (`README.md:112-120`), and `docs/modules/README.md` links to `docs/modules/persistence.md` (`docs/modules/README.md:47-50`) even though the plan deletes that file (`ai/plans/remove-deprecated-persistence-package.md:413-415`). Add both files to §9.

- `docs/modules/observability.md` needs more than migration-table cleanup: its module tree has no `emit_helpers.py` entry (`docs/modules/observability.md:11-25`) and its responsibilities table has no row for the moved helper layer (`docs/modules/observability.md:31-40`).

- The plan says no invariant except observability is touched (`ai/plans/remove-deprecated-persistence-package.md:132-135`), but it edits `server/proxy.py`, which is under governed-delivery rules (`.claude/rules/governed-delivery.md:1-19`) and emits governed-delivery audit markers through `persist_orchestration_event` (`moralstack/server/proxy.py:48`, `moralstack/server/proxy.py:427-462`). The plan later adds coverage, but §5 should acknowledge the governed-delivery audit surface.

## Missing tests

- Add a test or assertion that `.claude/rules/observability.md` covers the new `moralstack/orchestration/default_persistence.py` location, or make this an explicit checklist item. The invariant is path-scoped today (`.claude/rules/observability.md:1-5`).

- The existing proxy test already verifies `PROXY_OUTPUT_FINALIZED` payload fields (`tests/test_server_proxy.py:1216-1269`). Prefer updating/retaining that behavior-locking test rather than adding a redundant new one.

## Risky assumptions

- The plan assumes "byte-for-byte" observable behavior while moving modules that use `logging.getLogger(__name__)`. Current logger names come from `moralstack.persistence.default` and `moralstack.persistence.sink` (`moralstack/persistence/default.py:16`, `moralstack/persistence/sink.py:31`); moving the code verbatim changes logger names. Decide whether logger identity is intentionally changing or preserve old logger names.

- The plan treats `PersistenceWriteQueue` / `get_write_queue` as dead internally, but they are public exports today (`moralstack/persistence/__init__.py:49`, `moralstack/persistence/__init__.py:73`, `moralstack/persistence/__init__.py:122-123`). The plan acknowledges the external break; the changelog needs to be explicit.

## Architecture concerns

- Moving `PersistencePort` into orchestration is coherent with current controller dependency injection: the controller accepts `PersistencePort` and defaults to `NullPersistence` (`moralstack/orchestration/controller.py:158-166`). The missing architecture piece is the rule-path update, not the package placement.

## Security/performance concerns

- No new direct input-validation or authz issue found in the plan. Performance risk is low; the main runtime risk is import breakage across module-level and lazy imports already listed by the plan.

## Suggested plan changes

- Add `.claude/rules/observability.md` to files/docs to update.
- Clarify the `core` domain test scope before implementation.
- Add `README.md` and `docs/modules/README.md` to docs cleanup.
- Update `docs/modules/observability.md` architecture/responsibilities for `emit_helpers.py`.
- Amend §5 to acknowledge governed-delivery audit and constitution-domain surfaces.

## Questions for Claude/User

- Should request-supplied `domain_overlay="core"` be fixed in this same change, or tracked as a separate P0 bug outside this package-removal refactor?
- Should relocated helpers preserve old logger names for compatibility with existing log filters, or is the logger-name change acceptable with changelog coverage?
