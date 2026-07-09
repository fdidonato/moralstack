# Implementer Handoff — FIX PASS (Codex diff-review BLOCK)

You are the **MoralStack Implementer** (`claude-implementer`, Claude Sonnet, isolated
context). This is a **narrow fix pass** on an already-implemented change (approved plan
`ai/plans/unify-constitution-retrieval-single-pass.md`). Do ONLY the fix below. No scope
creep. Honor `ai/prompts/claude-implementation-template.md` rules.

## The one blocker to fix (Codex diff review, verified)

The single `RELEVANT_PRINCIPLES_RETRIEVED` event is emitted from
`_emit_relevant_principles_retrieved`, called from `_build_request_analysis_from_risk`
(`moralstack/orchestration/controller.py:1797`), which is invoked **only** from
`_route_fast_path` (`controller.py:1853`) and `_route_deliberative` (`controller.py:1903`).
But the controller can return **without** those routes — COMPLIANCE_FAST_PATH
(`controller.py:2267,2306`), hard-signal REFUSE (`controller.py:2660`), benign
(`controller.py:2672`), SAFE_COMPLETE (`controller.py:2685`) — so on those routes the risk
retrieval succeeded and consumed principles but **no event is emitted**. This violates the
approved plan's observability contract ("emit the single `RELEVANT_PRINCIPLES_RETRIEVED` at
the controller/risk-carrier level on success; runner only on fallback") and the acceptance
criterion "Exactly one … per request … observable on non-deliberative routes."

## Required fix (smallest correct change)

1. In `process()`, **immediately after risk estimation succeeds** (right after the
   `_estimate_risk` call, ~`controller.py:2196-2200`, when
   `risk_estimation.retrieval_succeeded` is `True`): build the `RequestAnalysisContext`
   **once** and emit `RELEVANT_PRINCIPLES_RETRIEVED` **once**, BEFORE routing — so every
   route that can return after risk (deliberative, FAST_PATH, COMPLIANCE_FAST_PATH, REFUSE,
   benign, SAFE_COMPLETE) is covered. Hold the built context in a local (or request-scoped
   holder) and pass it to the route dispatchers.
2. `_route_fast_path` / `_route_deliberative` must **consume the already-built context**
   instead of rebuilding + re-emitting. Split "build context" from "emit event", OR make
   `_emit_relevant_principles_retrieved` idempotent per request (emit-once guard), so there
   is **exactly one** emit per request and **no double-emit** on fast/deliberative reuse.
3. Preserve **runner-only emission on the fallback path** (when the runner performs its own
   retrieval because no context was supplied) — unchanged.
4. Fail-safe unchanged: when `retrieval_succeeded` is `False` (no estimator / no store /
   retrieval raised), do NOT emit at the controller; the runner emits on its fallback
   retrieval as today. Never fail open, never double-emit, never drop principles.

Do not change any decision/routing logic, prompts, top_k, or the retrieval query policy —
observability wiring only.

## Files ALLOWED to modify
- `moralstack/orchestration/controller.py` (move build+emit to post-risk; route helpers
  consume the pre-built context).
- `moralstack/orchestration/deliberation_runner.py` — ONLY if a signature tweak is needed to
  accept the pre-built context on the fast/deliberative entry points (it already accepts
  `request_analysis`; likely no change beyond ensuring reuse doesn't re-emit).
- Tests: extend `tests/test_observability_relevant_principles_single_emit.py` and/or
  `tests/test_fast_path_single_retrieval.py` (or a new
  `tests/test_relevant_principles_event_all_routes.py`).
- `docs/TRACES/observability_db_to_ui.md` only if the emission-point description needs to match.

## Files NOT to modify
- Everything else from the original handoff's do-not-modify list: `POLICY_SYSTEM_PROMPT` /
  `_policy_helpers.py`, `final_revalidation.py`, the signals/operational mini prompts,
  `_normalize_runtime_domain`, `_LOCAL_LLM_CALL_PAYLOAD_KEYS` (carrier principles must not be
  persisted). Do NOT alter the retrieval unification / quick_check / top_k / query-policy
  logic — those passed review.

## Invariants (PROJECT_SPEC §5)
- §5.6 observability best-effort: the new/moved emit MUST stay in a swallowing try/except
  (as `_emit_relevant_principles_retrieved` already is). A telemetry failure must never break
  the request.
- No governance-decision change: this is audit wiring only; §5.1/§5.3/§5.4/§5.5 untouched.

## Required tests
- Exactly ONE `RELEVANT_PRINCIPLES_RETRIEVED` per request on: COMPLIANCE_FAST_PATH, benign
  fast path, SAFE_COMPLETE, and hard-signal REFUSE routes (drive `controller.process(...)`
  end-to-end with mocks; assert the event count == 1 per request on each route).
- No-double-emit on fast/deliberative reuse (event count == 1, not 2).
- Fallback still emits from the runner when `retrieval_succeeded` is False (unchanged).

## Verification (run and report REAL output)
```
python -m pytest tests/test_observability_relevant_principles_single_emit.py tests/test_fast_path_single_retrieval.py tests/test_single_retrieval_wave_e2e.py tests/test_relevant_principles_event_all_routes.py -v
python -m pytest         # full suite must stay green (was 2083 passed)
```
Do NOT run `pre-commit run -a`; scope it: `pre-commit run --files <changed files>` and make
mypy/black/ruff/whitespace pass on the changed files.

## Acceptance
- Exactly one `RELEVANT_PRINCIPLES_RETRIEVED` per request on EVERY route that returns after a
  successful risk retrieval (deliberative, FAST_PATH, COMPLIANCE_FAST_PATH, REFUSE, benign,
  SAFE_COMPLETE); no double-emit; runner still emits only on fallback.
- Full `python -m pytest` green; scoped `pre-commit` clean.

## Required output
files modified; tests added; commands run; results (REAL output); deviations; residual
problems. Do NOT git add/commit/push. If ambiguous or blocked, STOP and report.
