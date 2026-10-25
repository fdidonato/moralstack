"""
SignalRegistry: loads and validates signals.yaml, exposes signals, prompt_config and domain_sensitivity.

Loaded once at module import (lazy singleton). Consumers import the
module-level `registry` object rather than constructing a new instance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .schema import (
    DomainSensitivityConfig,
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


class SignalRegistry:
    """
    Parsed, validated registry of all harm-detection signals from signals.yaml.

    Public API:
        signals               — ordered dict of SignalDef by yaml key
        prompt_config         — PromptConfig
        domain_sensitivity    — DomainSensitivityConfig
    """

    def __init__(self, path: Path = _YAML_PATH) -> None:
        with open(path, encoding="utf-8") as f:
            raw = YAML(typ="safe").load(f)

        version = raw.get("version")
        if version not in _SUPPORTED_VERSIONS:
            raise ValueError(
                f"signals.yaml version {version!r} is not supported. " f"Supported: {sorted(_SUPPORTED_VERSIONS)}"
            )

        self._signals: dict[str, SignalDef] = {key: _parse_signal(key, val) for key, val in raw["signals"].items()}
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


# ── Module-level singleton ──────────────────────────────────────────────────
# Loaded once at first import. Tests can replace this with a custom instance.
try:
    registry: SignalRegistry = SignalRegistry()
except Exception as _exc:  # noqa: BLE001
    _LOG.error("Failed to load SignalRegistry: %s", _exc)
    raise
