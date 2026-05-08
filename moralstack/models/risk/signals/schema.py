"""
Dataclasses for the signal configuration system.

These are the internal typed representations of signals.yaml entries.
All fields map 1-to-1 to YAML keys — see signals.yaml for documentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SignalContributes:
    harmful_count: bool = False
    priority_harm_count: bool = False


@dataclass(frozen=True)
class SignalHarmType:
    label: str | None = None  # e.g. "weapons", "physical", "illegal", None
    priority: int | None = None  # lower = higher priority; None = does not assign harm_type


@dataclass(frozen=True)
class SignalPrompt:
    yes_when: str | None = None
    no_when: str | None = None


@dataclass(frozen=True)
class SignalEffects:
    """Named behavior flags consumed by calibration and rules engine."""

    escalates_to_clearly_harmful: bool = False
    forces_risk_policy_action: str | None = None  # "DENY" | "ALLOW" | ... | None
    blocks_defensive_override: bool = False
    blocks_defensive_override_when_intent_present: bool = False
    blocks_calibration_guard: bool = False
    excludes_from_intent_contradiction_guard: bool = False
    excludes_from_non_operational_clamp: bool = False
    maps_to_self_harm_language: bool = False
    priority_resolution_in_prompt: bool = False


@dataclass(frozen=True)
class SignalDef:
    """Complete definition of a single harm-detection signal."""

    key: str  # YAML dict key, e.g. "q5_physical"
    label: str  # downstream label, e.g. "Q5:physical_harm"
    short_description: str
    prompt: SignalPrompt
    contributes_to: SignalContributes
    harm_type: SignalHarmType
    refusal_domain: str | None
    effects: SignalEffects


@dataclass(frozen=True)
class HarmTypeDefault:
    label: str
    triggers_fallback_signal_append: bool = False


@dataclass(frozen=True)
class DefensiveOverrideApply:
    category: str
    score_cap: float
    harm_type_realign_from: list[str] = field(default_factory=list)
    harm_type_realign_to: str = "none"
    harm_type_realign_when: str = ""


@dataclass(frozen=True)
class CalibrationRules:
    """Parsed calibration section of signals.yaml."""

    signal_assignment_order: list[str]
    fallback_signal_order: list[str]
    harm_type_default: HarmTypeDefault
    # Raw YAML sub-sections kept as dicts for rules engine consumption.
    # The rules engine parses them on first use.
    defensive_override: dict
    escalation_clearly_harmful: dict
    default_potentially_harmful: dict
    default_action_from_category_score: list[dict]
    op_risk_action_mapping: dict
    intent_contradiction_guard: dict
    non_operational_clamp: dict
    force_actions: list[dict]
    calibration_guard: dict


@dataclass(frozen=True)
class PromptConfig:
    coherence_rules: list[str]


@dataclass(frozen=True)
class DomainSensitivityConfig:
    description: str
    values: dict[str, str]  # HIGH/MEDIUM/LOW → description string
