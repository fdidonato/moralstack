# Decision Explainability Layer

## Overview

The Decision Explainability Layer provides structured, machine-readable explanations for every MoralStack decision. It
guarantees that `decision_reason` is always populated, reason codes are structured, activated risk signals are logged,
overlay applied is logged, and the winning rule is logged.

## Components

| Module              | File                                        | Description                                       |
|---------------------|---------------------------------------------|---------------------------------------------------|
| DecisionExplanation | `moralstack/models/decision_explanation.py` | Structured dataclass for decision explainability  |
| ReasonCode          | `moralstack/models/reason_codes.py`         | Centralized enum of machine-readable reason codes |

## DecisionExplanation

```python
@dataclass
class DecisionExplanation:
    final_action: str
    risk_score: float
    risk_category: str
    activated_signals: list[str]
    overlay_applied: str | None
    winning_rule: str
    reason_codes: list[str]
    why_not_refuse: str
    why_not_safe_complete: str
    why_not_normal_complete: str   # Why NORMAL_COMPLETE was not chosen (e.g. for SAFE_COMPLETE/REFUSE)
    timestamp: float
```

All fields are serializable via `to_dict()`; never `None` in output. For SAFE_COMPLETE, `why_not_normal_complete`
explains why full NORMAL_COMPLETE was not allowed; `why_not_safe_complete` is N/A.

## ReasonCode Enum

Structured codes include: `HIGH_RISK_OPERATIONAL`, `SENSITIVE_DOMAIN`, `OVERLAY_HEALTHCARE`, `OVERLAY_LEGAL`,
`FAST_PATH_LOW_RISK`, `CYCLES_EXHAUSTED_FALLBACK`, `RISK_BELOW_REFUSE_THRESHOLD`, `NO_OPERATIONAL_INTENT`,
`COMPLIANCE_LAYER_MATCH`, `DEFAULT_NORMAL_COMPLETE`, and others. Policy reason strings are mapped via `policy_reason_codes_to_reason_codes()`.

## Winning Rules

| Rule                        | When applied                                 |
|-----------------------------|----------------------------------------------|
| `hard_violations`           | Hard constraint violations present           |
| `hindsight_refuse`          | Hindsight recommends refuse                  |
| `normative_refuse_guards`   | op_risk HIGH or misuse+actionability HIGH    |
| `epistemic_escalation`      | Simulator negativity with high actionability |
| `informational_recovery`    | Informational recovery path                  |
| `fast_path`                 | PRE policy bounds indicate FAST_PATH         |
| `compliance_layer_match`    | DCCL MATCH with validated speculative draft  |
| `policy_bounds_fallback`    | Fallback to policy bounds                    |
| `cycles_exhausted_fallback` | CYCLES_EXHAUSTED sensitive fallback          |

## Decision trace (JSONL) semantics

- **At most one PRE_POLICY and one FINAL per request_id.** The controller calls `decide_action` once at entry (writes
  PRE_POLICY; FINAL only if path is not DELIBERATIVE_PATH) and, when taking the deliberative path, once after
  deliberation with `append_pre_policy_trace=False` (writes only FINAL). This avoids duplicate stage/sequence records
  for analytics.
- **PRE_POLICY** — Populated with `reason_codes`, `winning_rule`, and `overlay_applied` (same as `domain_overlay`) for
  consistent reasoning in every record.

## Output Locations

- **decision_trace.jsonl** — One PRE_POLICY and at most one FINAL per request; full explanation fields including
  `why_not_normal_complete`.
- **.debug/debug.log** — NDJSON lines with `event: "DECISION_EXPLANATION"` (via `orch_debug_log`) for auditing.
- **benchmark_results JSONL** — `moralstack_decision_reason`, `moralstack_reason_codes`, `moralstack_overlay`,
  `moralstack_winning_rule`, `moralstack_why_not_refuse`, `moralstack_why_not_safe_complete`,
  `moralstack_why_not_normal_complete`
- **ResponseMetadata** — Same fields in final response metadata

## See Also

- [README.md](../../README.md) — Decision Explainability Layer section
- [Decision Policy](../decision_policy.md) — Formal policy rules
