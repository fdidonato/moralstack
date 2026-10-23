"""
Enums, categories and constants for the risk classification system.
"""

from __future__ import annotations

from enum import Enum


class RiskCategory(Enum):
    """Ethical risk categories."""

    BENIGN = "benign"  # Standard informational requests
    MORALLY_NUANCED = "morally_nuanced"  # Interpersonal ethical dilemmas (requires deliberation)
    SENSITIVE = "sensitive"  # Delicate but legitimate topics
    POTENTIALLY_HARMFUL = "potentially_harmful"  # Requires scrutiny
    CLEARLY_HARMFUL = "clearly_harmful"  # Obvious violation


class DomainSensitivity(Enum):
    """Dimension for regulated or ethical domains."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OperationalRisk(Enum):
    """Dimension: ability to reconstruct or carry out harmful actions."""

    NONE = "NONE"
    LOW = "LOW"
    HIGH = "HIGH"


class IntentClarity(Enum):
    """Language-agnostic signal: clarity of the user's intent (semantic). LOW|MEDIUM|HIGH."""

    LOW = "LOW"  # Ambiguous intent (legacy AMBIGUOUS -> LOW)
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"  # Clear intent (legacy CLEAR -> HIGH)


class MisusePlausibility(Enum):
    """Language-agnostic signal: plausibility of malicious use (semantic)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActionabilityRisk(Enum):
    """Signal: risk that the information could be used harmfully."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskPolicyAction(Enum):
    """Gating action based on risk (used by the orchestrator)."""

    ALLOW = "ALLOW"
    ALLOW_WITH_CAVEAT = "ALLOW_WITH_CAVEAT"
    DELIBERATE = "DELIBERATE"
    DENY = "DENY"
