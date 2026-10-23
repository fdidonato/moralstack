"""
Pydantic schema for the YAML constitution.

Fail-fast structural validation: no Any, extra="forbid".
Exposed schemas: Principle, Overlay, Constitution.
Loading schemas (internal): PrincipleYAML, CoreYAML, OverlayYAML.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# -----------------------------------------------------------------------------
# Enums / literal types
# -----------------------------------------------------------------------------

SeverityLevel = Literal["hard", "soft"]


# -----------------------------------------------------------------------------
# Exposed models (public API)
# -----------------------------------------------------------------------------


class Principle(BaseModel):
    """Ethical principle of the constitution (exposed typed object)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    id: str
    level: SeverityLevel
    priority: int  # 1-100
    title: str
    rule: str
    examples_allow: list[str] = []
    examples_deny: list[str] = []
    remediation: str = ""
    domain: str | None = None

    @field_validator("priority")
    @classmethod
    def priority_in_range(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("priority deve essere tra 1 e 100")
        return v


class Overlay(BaseModel):
    """Domain overlay (constitution extension)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    domain: str
    additional_principles: list[Principle] = []
    priority_overrides: dict[str, int] = {}
    description: str = ""
    sensitive: bool = False
    excluded: bool = False
    refusal_redirection: str = ""
    simulator_domain_guidance: str = ""
    sensitive_risk_floor: float | None = None  # None = use global default (0.35)

    @field_validator("sensitive_risk_floor")
    @classmethod
    def floor_in_range(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError("sensitive_risk_floor deve essere tra 0.0 e 1.0")
        return v


class Constitution(BaseModel):
    """Complete constitution (core + optional overlay)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    core_principles: list[Principle] = []
    active_overlay: Overlay | None = None
    constitution_loaded_ok: bool = True
    constitution_corrupted: bool = False

    @property
    def principles(self) -> list[Principle]:
        """
        All ordered principles (core + overlay, with priority override).
        Order: hard > soft, descending priority, specificity, ID.
        """
        from moralstack.constitution.helpers import resolve_conflict

        all_principles = list(self.core_principles)
        if self.active_overlay:
            priority_map = dict(self.active_overlay.priority_overrides)
            for i, p in enumerate(all_principles):
                if p.id in priority_map:
                    all_principles[i] = p.model_copy(update={"priority": priority_map[p.id]})
            all_principles.extend(self.active_overlay.additional_principles)
        return resolve_conflict(all_principles)


# -----------------------------------------------------------------------------
# YAML loading models (internal, with optional YAML fields)
# -----------------------------------------------------------------------------


class PrincipleYAML(BaseModel):
    """
    Structure of a principle as it appears in the YAML.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    level: SeverityLevel
    priority: int
    title: str
    rule: str
    examples_allow: list[str] = []
    examples_deny: list[str] = []
    remediation: str = ""
    domain: str | None = None

    @field_validator("priority")
    @classmethod
    def priority_in_range(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("priority deve essere tra 1 e 100")
        return v

    def to_principle(self) -> Principle:
        """Converts to Principle.
        Limits examples to at most 2 per type to reduce tokens.
        Remediation is ignored (not used in prompts).
        """
        return Principle(
            id=self.id,
            level=self.level,
            priority=self.priority,
            title=self.title,
            rule=self.rule,
            examples_allow=list(self.examples_allow)[:2],
            examples_deny=list(self.examples_deny)[:2],
            remediation="",  # Not used in prompts; omitted from the YAMLs to save tokens
            domain=self.domain,
        )


class CoreYAML(BaseModel):
    """Root structure of the core.yaml file."""

    model_config = ConfigDict(extra="forbid")

    principles: list[PrincipleYAML]


class OverlayYAML(BaseModel):
    """Root structure of an overlay file (e.g. medical.yaml)."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    sensitive: bool = False
    excluded: bool = False
    priority_overrides: dict[str, int] = {}
    additional_principles: list[PrincipleYAML] = []
    refusal_redirection: str = ""
    simulator_domain_guidance: str = ""
    sensitive_risk_floor: float | None = None  # None = use global default (0.35)

    @field_validator("priority_overrides", mode="before")
    @classmethod
    def coerce_priority_values(cls, v: object) -> dict[str, int]:
        if not isinstance(v, dict):
            return {}
        return {str(k): int(val) for k, val in v.items()}

    @field_validator("sensitive_risk_floor")
    @classmethod
    def floor_in_range(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError("sensitive_risk_floor deve essere tra 0.0 e 1.0")
        return v
