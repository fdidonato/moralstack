---
paths:
  - "moralstack/runtime/decision/**"
  - "moralstack/orchestration/decision_service.py"
  - "moralstack/orchestration/safe_complete_gating.py"
---
# Invariant — Decision/generation separation (P0)

Load-bearing. If a change appears to require breaking this, stop and surface it to
the user rather than working around it.

**Decision/generation separation.** The policy layer decides
`final_action ∈ {NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE}`; generation produces text
*within* that decision. `final_action` is computed from structured signals,
**never inferred from response text or disclaimers**. Action bounds are defined in
`moralstack/runtime/decision/safe_complete_policy.py` (`compute_action_bounds`,
`decide_final_action`). The runtime final action is assembled by
`orchestration/decision_service.py` (which adds narrow exception handling in
`_handle_hard_violations` for crisis-support and regulated-info cases) and may be
post-gated by `orchestration/safe_complete_gating.py`.
