"""
Schema canonico condiviso per le decisioni deliberative.

UNICA fonte di verità: tutti i moduli deliberativi che producono una decisione
devono conformarsi a questo schema. Nessuna estensione ad-hoc, nessun campo opzionale.
Se un modulo non è in grado di produrre questo schema → ERRORE.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# -----------------------------------------------------------------------------
# Enum values (lowercase in JSON, normalizzati a valori ammessi)
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
    """Sotto-schema obbligatorio per risk_assessment."""

    model_config = ConfigDict(extra="forbid")

    risk_level: RISK_LEVEL_VALUES
    intent_clarity: AXIS_VALUES
    misuse_plausibility: AXIS_VALUES
    actionability_risk: AXIS_VALUES


class JustificationSchema(BaseModel):
    """Sotto-schema obbligatorio per justification."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    triggered_principles: list[str]
    hard_violations: list[str]


class StructuredDecision(BaseModel):
    """
    Schema JSON canonico per tutte le decisioni deliberative.

    Tutti i campi sono obbligatori. Nessun campo opzionale.
    Usato come unica fonte di verità per final_action e risk/justification.
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
