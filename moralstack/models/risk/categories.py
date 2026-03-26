"""
Enums, categorie e costanti per il sistema di classificazione del rischio.
"""

from __future__ import annotations

from enum import Enum


class RiskCategory(Enum):
    """Categorie di rischio etico."""

    BENIGN = "benign"  # Richieste informative standard
    MORALLY_NUANCED = "morally_nuanced"  # Dilemmi etici interpersonali (richiede deliberazione)
    SENSITIVE = "sensitive"  # Argomenti delicati ma legittimi
    POTENTIALLY_HARMFUL = "potentially_harmful"  # Richiede scrutinio
    CLEARLY_HARMFUL = "clearly_harmful"  # Violazione evidente


class DomainSensitivity(Enum):
    """Dimensione per domini regolati o etici."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OperationalRisk(Enum):
    """Dimensione: capacità di ricostruire o eseguire azioni dannose."""

    NONE = "NONE"
    LOW = "LOW"
    HIGH = "HIGH"


class IntentClarity(Enum):
    """Segnale language-agnostic: chiarezza dell'intento utente (semantico). LOW|MEDIUM|HIGH."""

    LOW = "LOW"  # Ambiguous intent (legacy AMBIGUOUS -> LOW)
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"  # Clear intent (legacy CLEAR -> HIGH)


class MisusePlausibility(Enum):
    """Segnale language-agnostic: plausibilità di uso malevolo (semantico)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActionabilityRisk(Enum):
    """Segnale: rischio che le informazioni possano essere usate in modo dannoso."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskPolicyAction(Enum):
    """Azione di gating basata su risk (usata dall'orchestrator)."""

    ALLOW = "ALLOW"
    ALLOW_WITH_CAVEAT = "ALLOW_WITH_CAVEAT"
    DELIBERATE = "DELIBERATE"
    DENY = "DENY"
