"""
Risk estimator (semantic judge) prompt templates — v3 (balanced).

Changes from v2:
  - System prompts hold all invariant principles plus interpretive scope
    (decoded payload, language-agnostic, third-party intent).
  - User prompts reduced to procedure + checklist + output schema.
  - Removed system↔user duplication and duplication across mini-estimators.
  - Illustrative lists compressed (4–8 examples to 2–3 archetypes).
  - Coherence checks merged: forward + falsify in one compact block.
  - Decision rules moved before classification; output rules at the end.
  - request_type tree compressed from eight prose entries to a decision table.
  - q5/q6/q12 rewritten without recursive self-corrections.
  - Operational keeps framing analysis (intentional defensive redundancy:
    the calibrator merges two independent sources).
  - Token totals (3 mini-calls): ~11,100 to ~5,700 (~49% reduction).

Output JSON schema unchanged: all fields expected by the calibrator
(merge_mini_estimator_results in calibration.py) are preserved.
"""

from typing import Any

from moralstack.prompts.catalog import NO_CONTEXT, default_prompt_set

# The prompt texts live in ``moralstack/prompts/data/risk.yaml`` (fragments + compositions). The names below are
# default re-exports: the values are byte-identical to the former literals and are what existing importers and
# tests compare against. Runtime code reads the estimator's own ``PromptSet`` (``self._prompt_set``), never these.
_DEFAULT = default_prompt_set()

# =============================================================================
# MINI-ESTIMATOR 1: Intent & Context Classifier
# =============================================================================

INTENT_CONTEXT_SYSTEM_PROMPT = _DEFAULT.render("risk.intent.system", NO_CONTEXT)

INTENT_CONTEXT_PROMPT_TEMPLATE = _DEFAULT.template("risk.intent.user")


# =============================================================================
# MINI-ESTIMATOR 2: Harm Signal Scanner
# =============================================================================

HARM_SIGNAL_SYSTEM_PROMPT = _DEFAULT.render("risk.signals.system_head", NO_CONTEXT)

HARM_SIGNAL_STATIC_TEMPLATE = _DEFAULT.template("risk.signals.static_suffix")

HARM_SIGNAL_USER_TEMPLATE = _DEFAULT.template("risk.signals.user")


# =============================================================================
# MINI-ESTIMATOR 3: Operational Risk Evaluator
# =============================================================================

OPERATIONAL_RISK_SYSTEM_PROMPT = _DEFAULT.render("risk.operational.system", NO_CONTEXT)

OPERATIONAL_RISK_PROMPT_TEMPLATE = _DEFAULT.template("risk.operational.user")

# =============================================================================
# Strict Structured Outputs schemas
# =============================================================================
# Mirror the two OUTPUT contracts written above. Kept next to the prompt text on
# purpose: if the contract changes, the schema is the adjacent lines to change.
# `tests/test_structured_outputs_modules.py` pins field-for-field agreement.
#
# Scope of the guarantee: shape, key names and enums. NOT value ranges — the
# supported JSON Schema subset has no `minimum`/`maximum`, so 0.0-1.0 bounds on
# risk_score/confidence stay the caller's job, and NOT semantics.
# `detected_language` is a free string: 46 distinct ISO 639-1 codes were observed
# across the COMPL-AI campaigns, so it cannot be enumerated.

_INTENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "rationale",
        "detected_language",
        "intent_to_harm",
        "requested_instructions",
        "intent_operational",
        "request_type",
        "harm_type",
        "intent_clarity",
        "stated_personal_bias",
        "seeks_norm_circumvention",
    ],
    "properties": {
        "rationale": {"type": "string"},
        "detected_language": {"type": "string"},
        "intent_to_harm": {"type": "string", "enum": ["yes", "no"]},
        "requested_instructions": {"type": "string", "enum": ["yes", "no"]},
        # Deliberately a boolean while its neighbours are yes/no strings: that is
        # what the contract above declares and what all 16,739 logged replies used.
        "intent_operational": {"type": "boolean"},
        "request_type": {
            "type": "string",
            "enum": [
                "factual_query",
                "creative_request",
                "ethical_dilemma",
                "sensitive_topic",
                "crisis_support",
                "support_request",
                "potential_harm",
                "clear_harm",
            ],
        },
        "harm_type": {
            "type": "string",
            "enum": [
                "none",
                "emotional_relational",
                "reputational",
                "financial",
                "physical",
                "illegal",
                "child_exploitation",
            ],
        },
        "intent_clarity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "stated_personal_bias": {"type": "string", "enum": ["yes", "no"]},
        "seeks_norm_circumvention": {"type": "string", "enum": ["yes", "no"]},
    },
}

_OPERATIONAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "rationale",
        "operational_risk",
        "risk_score",
        "confidence",
        "misuse_plausibility",
        "actionability_risk",
        "risk_policy_action",
    ],
    "properties": {
        "rationale": {"type": "string"},
        "operational_risk": {"type": "string", "enum": ["NONE", "LOW", "HIGH"]},
        "risk_score": {"type": "number"},
        "confidence": {"type": "number"},
        "misuse_plausibility": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "actionability_risk": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "risk_policy_action": {
            "type": "string",
            "enum": ["ALLOW", "ALLOW_WITH_CAVEAT", "DELIBERATE", "DENY"],
        },
    },
}


def intent_response_format() -> dict[str, Any]:
    """`response_format` enforcing the intent-context contract."""
    return {
        "type": "json_schema",
        "json_schema": {"name": "risk_intent", "strict": True, "schema": _INTENT_JSON_SCHEMA},
    }


def operational_response_format() -> dict[str, Any]:
    """`response_format` enforcing the operational-risk contract."""
    return {
        "type": "json_schema",
        "json_schema": {"name": "risk_operational", "strict": True, "schema": _OPERATIONAL_JSON_SCHEMA},
    }
