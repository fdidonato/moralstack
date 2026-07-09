# Codex Diff Review

## Verdict
`APPROVE_WITH_CHANGES`

## Deviations from approved plan
The flagged domain-resolution change in `_build_request_analysis_from_risk` is a narrow deviation from "observability wiring only," but it appears behavior-preserving: it mirrors the later domain persistence formula (`moralstack/orchestration/controller.py:1800`, `moralstack/orchestration/controller.py:1801`, `moralstack/orchestration/controller.py:2409`, `moralstack/orchestration/controller.py:2411`) and still excludes `core` via `_normalize_runtime_domain` (`moralstack/orchestration/controller.py:130`).

## Blocking issues
None.

## Non-blocking issues
None.

## Missing/weak tests
Missing direct regression coverage for the flagged effective-domain preservation. The moved builder now chooses the effective domain before the later `request.user_context.domain_overlay` assignment (`moralstack/orchestration/controller.py:1801`, `moralstack/orchestration/controller.py:2415`), and deliberation can consume `request_analysis.constitution` for simulator domain guidance (`moralstack/orchestration/deliberation_runner.py:1633`, `moralstack/orchestration/deliberation_runner.py:1641`). Existing all-route tests cover exactly-one event emission (`tests/test_relevant_principles_event_all_routes.py:185`, `tests/test_relevant_principles_event_all_routes.py:211`, `tests/test_relevant_principles_event_all_routes.py:236`, `tests/test_relevant_principles_event_all_routes.py:270`) but do not lock this prompt-adjacent domain equivalence.

## Security issues
None.

## Performance issues
None.

## Maintainability issues
None.

## Required fixes
None.

## Suggested fixes
Add a focused test for `_build_request_analysis_from_risk`/deliberative consumption with no request domain and `risk_estimation.detected_domain` set to a non-`core` overlay, asserting the prebuilt `RequestAnalysisContext` carries the same constitution/domain guidance the post-persist path would have used, plus a `core` case that normalizes to `None`.
