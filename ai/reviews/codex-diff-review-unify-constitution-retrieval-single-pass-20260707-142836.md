# Codex Diff Review

## Verdict
BLOCK

## Deviations from approved plan
- Acceptable: `_get_principles_context` returns `_PrinciplesContextResult` instead of a wider tuple.
- Acceptable: `CriticProtocol.quick_check` was updated for the new optional pre-retrieved-principles argument.
- Not acceptable: `RequestAnalysisContext` / `RELEVANT_PRINCIPLES_RETRIEVED` emission is route-local to fast/deliberative paths only, not controller-owned on every successful risk retrieval.

## Blocking issues
- Missing `RELEVANT_PRINCIPLES_RETRIEVED` on successful risk retrieval for non-fast/non-deliberative routes. Risk estimation runs before routing (`moralstack/orchestration/controller.py:2196`, `moralstack/orchestration/controller.py:2200`) and the helper emits only when `_build_request_analysis_from_risk` is called (`moralstack/orchestration/controller.py:1797`). That helper is called only from `_route_fast_path` and `_route_deliberative` (`moralstack/orchestration/controller.py:1853`, `moralstack/orchestration/controller.py:1903`). The controller can return through COMPLIANCE_FAST_PATH (`moralstack/orchestration/controller.py:2267`, `moralstack/orchestration/controller.py:2306`), REFUSE (`moralstack/orchestration/controller.py:2660`), benign (`moralstack/orchestration/controller.py:2672`), or SAFE_COMPLETE (`moralstack/orchestration/controller.py:2685`) without emitting the event. This violates the approved plan's "controller on risk-success, runner only on fallback" observability contract and drops audit evidence on non-deliberative routes. Required fix: build/emit the risk-owned request analysis once immediately after risk estimation succeeds, carry it through routing, and ensure fast/deliberative reuse does not emit again.

## Non-blocking issues
None found.

## Missing/weak tests
- No controller-level test covers `RELEVANT_PRINCIPLES_RETRIEVED` on COMPLIANCE_FAST_PATH, benign, SAFE_COMPLETE, or hard-signal REFUSE routes. Existing coverage asserts fast-path emission (`tests/test_fast_path_single_retrieval.py:290`) and a generic happy path (`tests/test_observability_relevant_principles_single_emit.py:188`), while the compliance test only calls `run_benign_fast_path` directly and asserts no extra retrieval (`tests/test_fast_path_single_retrieval.py:365`).

## Security issues
None found in the supported code paths. The changed hard-signal path remains structural: signals mini receives only the request text (`moralstack/models/risk/estimator.py:843`, `moralstack/models/risk/estimator.py:844`), and hard-signal routing still uses structured decision/risk signals, not retrieved principles.

## Performance issues
None found.

## Maintainability issues
None found.

## Required fixes
- Move request-analysis construction / `RELEVANT_PRINCIPLES_RETRIEVED` emission to a single controller-owned point after risk estimation succeeds, or make the helper idempotent and call it for every route that can return after risk estimation. Preserve runner-only emission for fallback retrieval.

## Suggested fixes
- Add tests for exactly one `RELEVANT_PRINCIPLES_RETRIEVED` on COMPLIANCE_FAST_PATH, benign, SAFE_COMPLETE, and hard-signal REFUSE routes, plus a no-double-emit assertion for fast/deliberative reuse.
