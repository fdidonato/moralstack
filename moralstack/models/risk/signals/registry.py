"""
SignalRegistry: loads and validates signals.yaml, exposes lookup API.

Loaded once at module import (lazy singleton). Consumers import the
module-level `registry` object rather than constructing a new instance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .schema import (
    CalibrationRules,
    DomainSensitivityConfig,
    HarmTypeDefault,
    PromptConfig,
    SignalContributes,
    SignalDef,
    SignalEffects,
    SignalHarmType,
    SignalPrompt,
)

_LOG = logging.getLogger(__name__)

_SUPPORTED_VERSIONS = {1}

# Resolved path to signals.yaml, relative to this package
_YAML_PATH = Path(__file__).parent.parent / "config" / "signals.yaml"


def _parse_signal_effects(raw: dict[str, Any]) -> SignalEffects:
    return SignalEffects(
        escalates_to_clearly_harmful=bool(raw.get("escalates_to_clearly_harmful", False)),
        forces_risk_policy_action=raw.get("forces_risk_policy_action") or None,
        blocks_defensive_override=bool(raw.get("blocks_defensive_override", False)),
        blocks_defensive_override_when_intent_present=bool(raw.get("blocks_defensive_override_when_intent_present", False)),
        blocks_calibration_guard=bool(raw.get("blocks_calibration_guard", False)),
        excludes_from_intent_contradiction_guard=bool(raw.get("excludes_from_intent_contradiction_guard", False)),
        excludes_from_non_operational_clamp=bool(raw.get("excludes_from_non_operational_clamp", False)),
        maps_to_self_harm_language=bool(raw.get("maps_to_self_harm_language", False)),
        priority_resolution_in_prompt=bool(raw.get("priority_resolution_in_prompt", False)),
    )


def _parse_signal(key: str, raw: dict[str, Any]) -> SignalDef:
    prompt_raw = raw.get("prompt") or {}
    harm_type_raw = raw.get("harm_type") or {}
    contrib_raw = raw.get("contributes_to") or {}
    effects_raw = raw.get("effects") or {}

    return SignalDef(
        key=key,
        label=str(raw["label"]),
        short_description=str(raw["short_description"]),
        prompt=SignalPrompt(
            yes_when=prompt_raw.get("yes_when") or None,
            no_when=prompt_raw.get("no_when") or None,
        ),
        contributes_to=SignalContributes(
            harmful_count=bool(contrib_raw.get("harmful_count", False)),
            priority_harm_count=bool(contrib_raw.get("priority_harm_count", False)),
        ),
        harm_type=SignalHarmType(
            label=harm_type_raw.get("label") or None,
            priority=harm_type_raw.get("priority"),
        ),
        refusal_domain=raw.get("refusal_domain") or None,
        effects=_parse_signal_effects(effects_raw),
    )


def _parse_calibration(raw: dict[str, Any]) -> CalibrationRules:
    htm_raw = raw.get("harm_type_default", {})
    return CalibrationRules(
        signal_assignment_order=list(raw.get("signal_assignment_order", [])),
        fallback_signal_order=list(raw.get("fallback_signal_order", [])),
        harm_type_default=HarmTypeDefault(
            label=str(htm_raw.get("label", "illegal")),
            triggers_fallback_signal_append=bool(htm_raw.get("triggers_fallback_signal_append", False)),
        ),
        defensive_override=dict(raw.get("defensive_override", {})),
        escalation_clearly_harmful=dict(raw.get("escalation_clearly_harmful", {})),
        default_potentially_harmful=dict(raw.get("default_potentially_harmful", {})),
        default_action_from_category_score=list(raw.get("default_action_from_category_score", [])),
        op_risk_action_mapping=dict(raw.get("op_risk_action_mapping", {})),
        intent_contradiction_guard=dict(raw.get("intent_contradiction_guard", {})),
        non_operational_clamp=dict(raw.get("non_operational_clamp", {})),
        force_actions=list(raw.get("force_actions", [])),
        calibration_guard=dict(raw.get("calibration_guard", {})),
    )


class SignalRegistry:
    """
    Parsed, validated registry of all harm-detection signals from signals.yaml.

    Public API:
        signals               — ordered dict of SignalDef by yaml key
        calibration           — CalibrationRules
        prompt_config         — PromptConfig
        domain_sensitivity    — DomainSensitivityConfig

        signal_by_label(label)                 — lookup by downstream label string
        signal_by_key(key)                     — lookup by yaml key
        signals_in_order(key_list)             — iterate SignalDefs in given key order
        any_signal_with_effect(effect, active) — check any active signal has effect
        maps_to_self_harm_language(active)     — derive legacy self_harm_language flag
        compute_counts(active)                 — return (harmful_count, priority_harm_count)
    """

    def __init__(self, path: Path = _YAML_PATH) -> None:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        version = raw.get("version")
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(
                f"signals.yaml version {version!r} is not supported. " f"Supported: {sorted(_SUPPORTED_VERSIONS)}"
            )

        self._signals: dict[str, SignalDef] = {key: _parse_signal(key, val) for key, val in raw["signals"].items()}
        self._label_index: dict[str, SignalDef] = {sig.label: sig for sig in self._signals.values()}
        self.calibration: CalibrationRules = _parse_calibration(raw.get("calibration", {}))
        ds_raw = raw.get("domain_sensitivity", {})
        self.domain_sensitivity = DomainSensitivityConfig(
            description=str(ds_raw.get("description", "")),
            values=dict(ds_raw.get("values", {})),
        )
        prompt_raw = raw.get("prompt", {})
        self.prompt_config = PromptConfig(
            coherence_rules=list(prompt_raw.get("coherence_rules", [])),
        )
        _LOG.debug("SignalRegistry loaded %d signals from %s", len(self._signals), path)

    @property
    def signals(self) -> dict[str, SignalDef]:
        return self._signals

    def signal_by_key(self, key: str) -> SignalDef:
        """Return SignalDef for a yaml key like 'q5_physical'. Raises KeyError if missing."""
        return self._signals[key]

    def signal_by_label(self, label: str) -> SignalDef | None:
        """Return SignalDef for a downstream label like 'Q5:physical_harm'. Returns None if not found."""
        return self._label_index.get(label)

    def signals_in_order(self, key_list: list[str]) -> list[SignalDef]:
        """Return list of SignalDefs in the given key order, skipping unknown keys."""
        return [self._signals[k] for k in key_list if k in self._signals]

    def any_signal_with_effect(self, effect_name: str, active_signals: dict[str, bool]) -> bool:
        """
        Return True if any signal that is active (True in active_signals) has
        the named effect set to a truthy value.
        """
        for key, sig in self._signals.items():
            if active_signals.get(key, False):
                val = getattr(sig.effects, effect_name, None)
                if val:
                    return True
        return False

    def any_signal_with_effect_when_intent_present(
        self,
        effect_name: str,
        active_signals: dict[str, bool],
        has_any_intent: bool,
    ) -> bool:
        """
        Return True if any active signal has the named effect AND has_any_intent is True.
        Used for blocks_defensive_override_when_intent_present (q10).
        """
        if not has_any_intent:
            return False
        return self.any_signal_with_effect(effect_name, active_signals)

    def maps_to_self_harm_language(self, active_signals: dict[str, bool]) -> bool:
        """
        Derive legacy self_harm_language flag: True if any active signal has
        effects.maps_to_self_harm_language=True.
        """
        return self.any_signal_with_effect("maps_to_self_harm_language", active_signals)

    def compute_counts(self, active_signals: dict[str, bool]) -> tuple[int, int]:
        """Return (harmful_count, priority_harm_count) based on active signals."""
        harmful = 0
        priority = 0
        for key, sig in self._signals.items():
            if active_signals.get(key, False):
                if sig.contributes_to.harmful_count:
                    harmful += 1
                if sig.contributes_to.priority_harm_count:
                    priority += 1
        return harmful, priority

    def get_signal_with_forced_action(self, active_signals: dict[str, bool]) -> str | None:
        """
        Return the forced risk_policy_action value from the highest-priority
        signal that is active and has forces_risk_policy_action set.
        Currently all signals with this effect should agree (only q17 uses it).
        Returns None if no active signal forces an action.
        """
        for key, sig in self._signals.items():
            if active_signals.get(key, False) and sig.effects.forces_risk_policy_action:
                return sig.effects.forces_risk_policy_action
        return None

    def resolve_harm_type(self, active_signals: dict[str, bool]) -> str | None:
        """
        Return harm_type label for the highest-priority active signal
        (lowest priority integer). Returns None if no active signal has a harm_type.
        """
        best_priority: int | None = None
        best_label: str | None = None
        for key, sig in self._signals.items():
            if not active_signals.get(key, False):
                continue
            ht = sig.harm_type
            if ht.label is None or ht.priority is None:
                continue
            if best_priority is None or ht.priority < best_priority:
                best_priority = ht.priority
                best_label = ht.label
        return best_label


# ── Module-level singleton ──────────────────────────────────────────────────
# Loaded once at first import. Tests can replace this with a custom instance.
try:
    registry: SignalRegistry = SignalRegistry()
except Exception as _exc:  # noqa: BLE001
    _LOG.error("Failed to load SignalRegistry: %s", _exc)
    raise
