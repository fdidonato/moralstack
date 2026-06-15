# Decision Policy: Formal SAFE_COMPLETE

This document describes the formalization of **SAFE_COMPLETE** as a first-class, rule-based action, with **single
source of truth** in `moralstack.runtime.decision.safe_complete_policy`.

## Principles

- **SAFE_COMPLETE is a policy action**, not an inference from text: it does not rely on presence of disclaimer or caveat
  in the response (language-agnostic).
- **BENIGN remains NORMAL_COMPLETE** except for epistemic escalation: `actionability_risk == HIGH` (user asks what to
  DO,
  provides resources/constraints/personal goals, or output directly influences a real decision) promotes
  any
  category — including BENIGN — to SAFE_COMPLETE. This is the only exception, and it is domain-agnostic.
- **Reason codes** are first-class: every decision includes deterministic codes (e.g. `hard_violations`, `risk_benign`,
  `safe_complete_required`, `safe_complete_required_high_actionability`, `domain_regulated`) for explainability and
  auditing.

## Single source of truth

- **Module**: `moralstack.runtime.decision.safe_complete_policy`
- **API**: `compute_action_bounds(context) -> PolicyBounds`,
  `decide_final_action(context) -> (final_action, bounds, reason_codes)`
- **Action order**: NORMAL_COMPLETE < SAFE_COMPLETE < REFUSE

## Policy inputs

- **domain** (optional): from `request.user_context.domain_overlay`
- **risk_category**: BENIGN | SENSITIVE | MORALLY_NUANCED | POTENTIALLY_HARMFUL | CLEARLY_HARMFUL
- **op_risk**: operational risk (actionable harmful action)
- **hard_violations_count**: number of constitutional hard violations
- **intent_type** (optional): factual | advice | support | explanation — derived from `request_type` and operational
  signals (`intent_operational`, `risk_score`). Used to differentiate SENSITIVE factual vs advisory.
- **Structured signals**: misuse_plausibility, actionability_risk, intent_clarity; optional
  `has_ambiguity_or_dual_use` (or harm_type ≠ none).
    - **actionability_risk** is the primary epistemic escalation signal:
      HIGH = user asks what to DO, provides resources/personal constraints, or output influences a real decision;
      MEDIUM = directional but generic guidance; LOW = purely informative/descriptive/historical.

## Overlay sensitivity (constitution-driven)

The policy no longer relies on a hardcoded regulated-domain set in the policy module.
Sensitive handling is driven by constitution overlays (`overlay_sensitive`) resolved by the controller.

## Rules (bounds)

1. **REFUSE mandatory**
    - `hard_violations_count > 0` → min = max = REFUSE
   - `risk_category == CLEARLY_HARMFUL` or op_risk indicates actionable harm → min = max = REFUSE

1.5. **SAFE_COMPLETE mandatory (epistemic escalation)**

- `actionability_risk == HIGH` **and** `hard_violations_count == 0` **and** op_risk not HIGH
- → min = max = SAFE_COMPLETE
- Reason code: `safe_complete_required_high_actionability`
- Definition of HIGH actionability_risk: user asks what to DO, provides resources/constraints/personal goals,
  or output could directly influence a real decision.
- This rule is domain-agnostic and precedes the rules for SENSITIVE/MORALLY_NUANCED.
- Does NOT override REFUSE (hard violations, op_risk HIGH, clearly_harmful already handled in rule 1).

2. **SAFE_COMPLETE mandatory**
    - `risk_category ∈ {SENSITIVE, MORALLY_NUANCED}` **and** (`intent_type ≠ factual` **or** ambiguity/dual-use
      signals)
    - → min = max = SAFE_COMPLETE (no downgrade to NORMAL)
    - **Factual exception**: if `intent_type == factual` **and** no ambiguity/dual-use signals **and** `overlay_sensitive == false` →
      min = NORMAL_COMPLETE, max = SAFE_COMPLETE (reason code: `risk_sensitive_allowed`).
      This reduces over-governance on informative questions in health, finance, etc.

3. **SAFE_COMPLETE allowed (gray zone)**
    - `risk_category == POTENTIALLY_HARMFUL` and not REFUSE:
      - if `overlay_sensitive == true` and not operational intent, SAFE_COMPLETE is required
        (factual + no ambiguity keeps `[NORMAL_COMPLETE, SAFE_COMPLETE]`)
      - otherwise SAFE_COMPLETE is allowed and **default final_action is NORMAL_COMPLETE** to reduce FP.

4. **NORMAL_COMPLETE mandatory (benign)**
    - `risk_category == BENIGN` and no hard_violation → min = max = NORMAL_COMPLETE.

## final_action derivation

- If REFUSE mandatory (rule 1) → final_action = REFUSE
- Else if actionability_risk HIGH (rule 1.5) → final_action = SAFE_COMPLETE
- Else if SAFE_COMPLETE mandatory (rule 2) → final_action = SAFE_COMPLETE
- Else final_action = NORMAL_COMPLETE (including POTENTIALLY_HARMFUL gray zone).

## Decision Trace and Audit Logging

The Decision Trace is a structured audit log that records decisions made by the system during request evaluation.
It is an observational audit mechanism: **it does not affect behavior nor final output**.

### PRE_POLICY vs FINAL

For a single request there can be multiple trace entries. `PRE_POLICY` represents the decision after risk and policy
bounds (without hard-violations). `FINAL` represents the decision after any hard-violations and enforcement. The
decision exposed to the user is always **FINAL**.

### Main fields

| Field                  | Meaning                                                     |
|------------------------|-------------------------------------------------------------|
| `request_id`           | Request identifier                                          |
| `stage`                | `PRE_POLICY` \| `FINAL`                                     |
| `sequence`             | Temporal order (1 = PRE, 2 = FINAL)                         |
| `final_action`         | Decided action (REFUSE \| SAFE_COMPLETE \| NORMAL_COMPLETE) |
| `decision_reason`      | Textual rationale                                           |
| `policy_reason_codes`  | Policy codes (e.g. `risk_benign`, `safe_complete_required`) |
| `hard_violation_codes` | Hard violation codes (if present)                           |

The presence of multiple entries for the same `request_id` is intentional; any downgrade or override is always explicit
in the fields.

### Contract-hard supremacy

`CORE.DEVCONTRACT.1` is a non-promotable hard violation. `decision_service._handle_hard_violations` refuses it even in
the two low-risk branches that can otherwise downgrade hard violations to `SAFE_COMPLETE`: regulated informational
requests and pre-policy `SAFE_COMPLETE` cases. This preserves the invariant that developer-contract content constraints
cannot be bypassed by domain-sensitive caveat routing.

### Determinism and side effects

The decision is deterministic given input and policy. The trace is a logging side effect: presence or absence of the
trace
does not change the decision.

### Intended uses

- Debugging
- Benchmark analysis
- Audit / compliance
- Policy tuning

## Reason codes (diagnostics)

Included at least:
`hard_violations`, `risk_clearly_harmful`, `risk_potentially_harmful`, `risk_sensitive`, `risk_morally_nuanced`,
`risk_benign`, `domain_regulated`, `safe_complete_required`, `safe_complete_required_high_actionability`,
`safe_complete_allowed`, `normal_complete_required`, `risk_sensitive_allowed`, `sim_negative_valence_safe_complete`,
`cycles_exhausted_sensitive_fallback`, `deliberation_override_refuse_to_safe_complete`.

Written in metadata (and in the per-question benchmark table) together with min_required, max_allowed and
correctness_verdict.

## Path (orchestrator)

- **REFUSE** can be FAST_PATH.
- **NORMAL_COMPLETE** (benign) → FAST_PATH when appropriate.
- **SAFE_COMPLETE** (required for SENSITIVE/MORALLY_NUANCED or `actionability_risk == HIGH`) → **DELIBERATIVE_PATH**:
  at least one deliberative cycle
  is executed (no "DELIBERATIVE_PATH with 0 modules").

## Overlay Sensitivity: Risk Score Floor

When a constitutional overlay is marked `sensitive: true`, the Controller applies a **floor** to the local `risk_score`:

- **Constant**: `OVERLAY_SENSITIVE_RISK_FLOOR = 0.35` (defined in `moralstack/orchestration/overlay_policy.py`, imported by controller)
- **Condition**: if `overlay.sensitive == True` and `risk_score < 0.35`, then `risk_score = 0.35`
- **Effect**: forces routing toward the deliberative path (`risk_score >= threshold_low = 0.3`)
- **Invariant**: the floor is a local boost to the `risk_score` variable in the controller; it does not mutate the
  `risk_estimation` object and does not affect risk estimator trace/diagnostics

## SAFE_COMPLETE fallback on CYCLES_EXHAUSTED

When deliberation exhausts cycles without converging, the Controller applies a conservative fallback:

- **Conditions** (all must hold):
    1. `outcome.stop_reason == "CYCLES_EXHAUSTED"`
  2. `decision.final_action == "NORMAL_COMPLETE"` (does not touch REFUSE)
  3. `risk_category in {SENSITIVE, MORALLY_NUANCED}` **or** `overlay_sensitive == True`

- **Action**: forces `final_action = "SAFE_COMPLETE"` by creating a new `Decision` with all fields preserved,
  adding the reason code `cycles_exhausted_sensitive_fallback`.

- **Rationale**: a CYCLES_EXHAUSTED outcome in a sensitive context signals uncertainty; the system adopts the
  precautionary
  principle preferring explicit governance (SAFE_COMPLETE) over an uncontrolled response (NORMAL_COMPLETE).

## Deliberation override: REFUSE → SAFE_COMPLETE when modules unanimous

When a request is routed to the deliberative path due to **borderline REFUSE** (risk_score within the borderline
range), the Controller may downgrade a post-deliberation REFUSE to SAFE_COMPLETE if the deliberative modules
**unanimously** support a safe response.

- **Conditions** (all must hold):
  1. Post-deliberation `decide_action` still returns REFUSE.
  2. Critic said PROCEED with zero violations and no hard violations.
  3. Perspectives have mean approval_score ≥ 0.7.
  4. Simulator has expected_valence ≥ 0 and semantic_expected_harm < 0.3.
  5. Hindsight (if present) does not recommend refuse.
  6. No critical violations in state.

- **Action**: the Controller replaces the decision with SAFE_COMPLETE and adds the reason code
  `deliberation_override_refuse_to_safe_complete`.

- **Rationale**: avoids wasting deliberative cycles when all modules agree the response is safe; the override
  is applied only after full deliberation and only when every module concurs.

## Enumerated-output gate (Tier-1, SOFT-only revision suppression)

When the requested output is a single enumerated token (e.g. `answer exactly
'TRUE' or 'FALSE'`, multiple-choice single letter), a SOFT revision can only
flip the selected option, which corrupts the factual answer. The critic
(`runtime/modules/critic_module.py`) detects this case deterministically via
`pipeline/output_contract.py:detect_enumerated_output` (declared constraints +
draft cross-check) and, when `decision == REVISE`, `violated_hard == False`, and
the draft is an enumerated member, downgrades the verdict to `PROCEED` and
**clears the soft `violations`/`guidance`** (required because
`ConvergenceEvaluator` votes `revise` on violation presence, not on `decision`).

- **Trigger**: explicit enumerated declaration **and** draft is a single short
  token member of the option set. Either alone does not trigger.
- **Safety**: guarded by `not violated_hard` — HARD violations are never
  suppressed (hard-signal supremacy, PROJECT_SPEC §5.3). No-op for free-form
  outputs.
- **Reason / audit**: each activation emits a best-effort
  `governance.enumerated_output_gate` diagnostic
  (`CriticReport.enumerated_output_gate_applied = True`) to
  `debug.event.jsonl`, the `debug_events` table, and the UI "Debug Events"
  panel. Observability only — never alters the decision (PROJECT_SPEC §5.6).
- **Motivation**: removes a `boolq_contrast` regression where the revision loop
  flipped a correct binary answer to the wrong value.

## REFUSE generation prompt handling

Current behavior in `response_assembler._make_refusal()` passes the original user prompt to `policy.refuse(...)`
to enable context-aware refusals and domain-appropriate redirection.

The safety contract is enforced by policy decisions and structured signals upstream (final_action, reason_codes,
hard violations), not by stripping prompt content at refusal assembly time.

## Implementation references

- **Policy**: `moralstack.runtime.decision.safe_complete_policy` (`compute_action_bounds`, `decide_final_action`,
  `PolicyBounds`, `PolicyContext`)
- **Decisions**: `moralstack.orchestration.decision_service.decide_action` calls the policy as the sole source for
  bounds
  and final_action
- **DCF**: `moralstack.runtime.decision_correctness.compute_interval` uses the same policy for min/max and reason_codes
- **Response contract**: `ResponseMetadata.caveat_present`, `safe_alternative_present`, `no_prescriptive_language`
  set in assembler when final_action == SAFE_COMPLETE

## Refusal focus classification (7 priorities, design v1.3 §3.8)

The `classify_refusal_focus` function maps risk signals + intent + developer
context to a refusal focus + redirection guidance. The 7-priority hierarchy:

| Priority | Source | Overridable? |
|---|---|---|
| **P0** | Hard topical signals (Q8 self-harm, Q17 child, Q10 weapons, Q5 physical) | NO |
| **P1** | Developer contract refusal redirection (`mode='structured'`) | NO (only P0 wins above) |
| **P2** | Domain overlay refusal_redirection | downstream guidance fallback |
| **P3** | harm_type-driven (reputational, financial) | yes |
| **P4** | Reputational cluster signals (Q14, Q15, Q16) | yes |
| **P5** | Residual harm-signal focuses (Q9 cyber, Q11 privacy, Q12 medical, Q4 fraud) | yes |
| **P6** | Coarse fallbacks (request_type, operational_risk) | always last |

**P0 invariant (§1.7)**: P0 returns short-circuit the function. No P1+ logic
can override a P0 signal. This invariant is non-negotiable.

**P1 conditions**: triggers only when the developer contract has `mode='structured'`
AND its `raw_text` contains a redirection keyword (`"redirect"`, `"refer to"`,
`"consult"`). Opaque contracts (the default) are not consulted at this stage.
