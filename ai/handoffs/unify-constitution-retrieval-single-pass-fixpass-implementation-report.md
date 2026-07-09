# Fix-Pass Implementation Report — unify-constitution-retrieval-single-pass

Addresses the single Codex diff-review BLOCK: `RELEVANT_PRINCIPLES_RETRIEVED` was emitted only
on fast/deliberative routes, dropping the event on COMPLIANCE_FAST_PATH / REFUSE / benign /
SAFE_COMPLETE.

## Fix
- `controller.py` `process()` (~:2225): builds the request analysis + emits the single event
  **once, right after risk estimation succeeds, before routing** — covers every route (both
  speculative and non-speculative branches). Result stored in a local `request_analysis`,
  passed to `_route_fast_path`/`_route_deliberative`.
- `_build_request_analysis_from_risk` (~:1762): now the single build+emit site; route
  dispatchers no longer call it.
- `_route_fast_path`/`_route_deliberative`: gained keyword-only `request_analysis=` param;
  consume the pre-built context (no rebuild, no re-emit).
- `_route_compliance_match`/`_route_refuse`/`_route_benign`/`_route_safe_complete`/
  `_route_domain_excluded`: unchanged — moving the build earlier covers them automatically.

## Deviation flagged (needs re-review scrutiny)
- **Domain resolution changed inside `_build_request_analysis_from_risk`**: from
  `request.get_domain()` to `_normalize_runtime_domain(request.get_domain() or
  risk_estimation.detected_domain)`. Rationale (implementer): because the build now runs
  earlier (before the later domain-persist block), `request.get_domain()` would otherwise
  return the pre-overlay domain for the deliberative path's `simulator_domain_guidance`
  (prompt-adjacent). They replicated the persist block's domain formula to keep behavior
  identical for the pre-existing routes. Uses `_normalize_runtime_domain` (core-excluding, so
  §5.5 preserved). `_emit_relevant_principles_retrieved`'s `constitution_domain` payload now
  sourced from `request_analysis.detected_domain`.

## Tests added
- `tests/test_relevant_principles_event_all_routes.py` (4): exactly one
  `RELEVANT_PRINCIPLES_RETRIEVED` on COMPLIANCE_FAST_PATH, benign, SAFE_COMPLETE, hard-signal
  REFUSE.

## Results (real)
- Scoped (observability + fast-path + e2e + new all-routes): `18 passed`.
- Full suite: run 1 `1 failed, 2086 passed` (the failure = `test_persistence_load.py::
  test_concurrency_emitted_equals_persisted`, a pre-existing SQLite "database is locked"
  write-contention flake, unrelated — zero references to this change); run 2 `2087 passed, 0
  failed`; flaky test re-run standalone → passed.
- `ruff`/`mypy`/`pre-commit --files` on changed files: clean.

## Residual
- Pre-existing SQLite-lock flake in `test_persistence_load.py` under full-suite concurrency;
  passes standalone and on rerun; not touched by this change.
