"""
Shared canonical schema for deliberative decisions.

SINGLE source of truth: all deliberative modules that produce a decision
must conform to this schema. No ad-hoc extensions, no optional fields.
If a module cannot produce this schema → ERROR.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# -----------------------------------------------------------------------------
# Enum values (lowercase in JSON, normalized to allowed values)
# -----------------------------------------------------------------------------

FINAL_ACTION_VALUES = Literal["REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE"]
RISK_LEVEL_VALUES = Literal[
    "benign",
    "sensitive",
    "morally_nuanced",
    "potentially_harmful",
    "clearly_harmful",
]
AXIS_VALUES = Literal["low", "medium", "high"]


class RiskAssessmentSchema(BaseModel):
    """Mandatory sub-schema for risk_assessment."""

    model_config = ConfigDict(extra="forbid")

    risk_level: RISK_LEVEL_VALUES
    intent_clarity: AXIS_VALUES
    misuse_plausibility: AXIS_VALUES
    actionability_risk: AXIS_VALUES


class JustificationSchema(BaseModel):
    """Mandatory sub-schema for justification."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    triggered_principles: list[str]
    hard_violations: list[str]


class StructuredDecision(BaseModel):
    """
    Canonical JSON schema for all deliberative decisions.

    All fields are mandatory. No optional fields.
    Used as the single source of truth for final_action and risk/justification.
    """

    model_config = ConfigDict(extra="forbid")

    final_action: FINAL_ACTION_VALUES
    risk_assessment: RiskAssessmentSchema
    justification: JustificationSchema
    confidence: float  # 0.0 – 1.0


__all__ = [
    "AXIS_VALUES",
    "FINAL_ACTION_VALUES",
    "JustificationSchema",
    "RiskAssessmentSchema",
    "RISK_LEVEL_VALUES",
    "StructuredDecision",
]
