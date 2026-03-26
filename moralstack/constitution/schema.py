"""
Schema Pydantic per la costituzione YAML.

Validazione strutturale fail-fast: nessun Any, extra="forbid".
Schema esposti: Principle, Overlay, Constitution.
Schema di caricamento (interni): PrincipleYAML, CoreYAML, OverlayYAML.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# -----------------------------------------------------------------------------
# Enums / literal types
# -----------------------------------------------------------------------------

SeverityLevel = Literal["hard", "soft"]


# -----------------------------------------------------------------------------
# Modelli esposti (API pubblica)
# -----------------------------------------------------------------------------


class Principle(BaseModel):
    """Principio etico della costituzione (oggetto tipizzato esposto)."""

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
    keywords: list[str] = []

    @field_validator("priority")
    @classmethod
    def priority_in_range(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("priority deve essere tra 1 e 100")
        return v


class Overlay(BaseModel):
    """Overlay di dominio (estensione costituzione)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    domain: str
    additional_principles: list[Principle] = []
    priority_overrides: dict[str, int] = {}
    description: str = ""
    keywords: list[str] = []
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
    """Costituzione completa (core + eventuale overlay)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    core_principles: list[Principle] = []
    active_overlay: Overlay | None = None
    constitution_loaded_ok: bool = True
    constitution_corrupted: bool = False

    @property
    def principles(self) -> list[Principle]:
        """
        Tutti i principi ordinati (core + overlay, con override priorità).
        Ordine: hard > soft, priority decrescente, specificità, ID.
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
# Modelli di caricamento YAML (interni, con campi opzionali YAML)
# -----------------------------------------------------------------------------


class PrincipleYAML(BaseModel):
    """
    Struttura di un principio come appare nel YAML.
    Accetta keywords, keywords_block, keywords_style; vengono uniti in Principle.keywords.
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
    keywords: list[str] = []
    keywords_block: list[str] = []
    keywords_style: list[str] = []

    @field_validator("priority")
    @classmethod
    def priority_in_range(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("priority deve essere tra 1 e 100")
        return v

    def to_principle(self) -> Principle:
        """Converte in Principle unendo keywords, keywords_block, keywords_style.
        Limita examples a max 2 per tipo per ridurre token.
        Remediation ignorato (non usato nei prompt).
        """
        keywords = list(self.keywords)
        keywords.extend(self.keywords_block)
        keywords.extend(self.keywords_style)
        # Deduplica mantenendo ordine
        seen: set[str] = set()
        merged: list[str] = []
        for k in keywords:
            s = str(k).strip()
            if s and s not in seen:
                seen.add(s)
                merged.append(s)
        return Principle(
            id=self.id,
            level=self.level,
            priority=self.priority,
            title=self.title,
            rule=self.rule,
            examples_allow=list(self.examples_allow)[:2],
            examples_deny=list(self.examples_deny)[:2],
            remediation="",  # Non usato nei prompt; omesso dai YAML per risparmio token
            domain=self.domain,
            keywords=merged,
        )


class CoreYAML(BaseModel):
    """Struttura radice del file core.yaml."""

    model_config = ConfigDict(extra="forbid")

    principles: list[PrincipleYAML]


class OverlayYAML(BaseModel):
    """Struttura radice di un file overlay (es. medical.yaml)."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    keywords: list[str] = []
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
