"""
Punto di ingresso unico per il parsing e la validazione degli output strutturati degli LLM.

Tutti i moduli che interpretano risposte LLM devono usare questo modulo (e non
json_utils o logiche duplicate) per:
- estrazione JSON da testo grezzo
- validazione dello schema/contenuto
- costruzione di tipi tipizzati

Principi:
- Parsing (estrazione JSON) e validazione (schema/campi) sono separati.
- Un solo strato condiviso per l'estrazione JSON; la validazione è pluggabile.
- I modelli Pydantic sono strict: campi inattesi causano ValidationError.
- Nessun fallback decisionale: se il parsing fallisce si solleva errore esplicito.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

from moralstack.core.schema import StructuredDecision
from moralstack.utils.json_utils import JSONParseError, extract_json

logger = logging.getLogger(__name__)

# Solo OK o ERROR: nessun FALLBACK per decisioni.
ParserStatus = Literal["OK", "ERROR"]


# -----------------------------------------------------------------------------
# Schema canonico (decisioni deliberative)
# -----------------------------------------------------------------------------


def parse_structured_decision(
    text: str,
    request_id: str = "",
) -> "StructuredDecision":
    """
    Parsa e valida output LLM come StructuredDecision (schema canonico).

    - Valida schema (chiavi, enum, tipi).
    - Normalizza SOLO valori ammessi (es. final_action uppercase).
    - NON inventa valori mancanti, NON corregge semantica, NON applica fallback.

    Raises:
        JSONParseError: Impossibile estrarre JSON dal testo.
        ValidationError: JSON estratto ma validazione fallita.

    In caso di fallimento logga request_id, raw_output (truncated), errore.
    """
    try:
        data = extract_json(text)
    except JSONParseError as e:
        logger.error(
            "parse_structured_decision failed request_id=%s error=%s raw_preview=%s",
            request_id or "unknown",
            str(e),
            (text[:500] + "..." if len(text) > 500 else text),
            extra={
                "moralstack_parser_diagnostic": True,
                "request_id": request_id or "unknown",
                "parser_status": "ERROR",
                "error_type": "JSONParseError",
            },
        )
        raise

    # Normalizza solo valori ammessi (enum)
    def _norm_final_action(v: Any) -> str:
        s = str(v).strip().upper() if v is not None else ""
        if s in ("REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE"):
            return s
        raise ValidationError(f"final_action non ammesso: {v!r}", data=data)

    def _norm_axis(v: Any) -> str:
        s = str(v).strip().lower() if v is not None else ""
        if s in ("low", "medium", "high"):
            return s
        raise ValidationError(f"axis value non ammesso: {v!r}", data=data)

    def _norm_risk_level(v: Any) -> str:
        s = str(v).strip().lower() if v is not None else ""
        allowed = (
            "benign",
            "sensitive",
            "morally_nuanced",
            "potentially_harmful",
            "clearly_harmful",
        )
        if s in allowed:
            return s
        raise ValidationError(f"risk_level non ammesso: {v!r}", data=data)

    try:
        ra = data.get("risk_assessment")
        if not isinstance(ra, dict):
            raise ValidationError("risk_assessment obbligatorio e deve essere un oggetto", data=data)
        just = data.get("justification")
        if not isinstance(just, dict):
            raise ValidationError("justification obbligatorio e deve essere un oggetto", data=data)
        data_norm = {
            "final_action": _norm_final_action(data.get("final_action")),
            "risk_assessment": {
                "risk_level": _norm_risk_level(ra.get("risk_level")),
                "intent_clarity": _norm_axis(ra.get("intent_clarity")),
                "misuse_plausibility": _norm_axis(ra.get("misuse_plausibility")),
                "actionability_risk": _norm_axis(ra.get("actionability_risk")),
            },
            "justification": {
                "summary": str(just.get("summary", "")),
                "triggered_principles": list(just.get("triggered_principles") or []),
                "hard_violations": list(just.get("hard_violations") or []),
            },
            "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
        }
        return StructuredDecision.model_validate(data_norm)
    except ValidationError:
        raise
    except Exception as e:
        logger.error(
            "parse_structured_decision validation failed request_id=%s error=%s raw_keys=%s",
            request_id or "unknown",
            str(e),
            list(data.keys()) if isinstance(data, dict) else [],
            extra={
                "moralstack_parser_diagnostic": True,
                "request_id": request_id or "unknown",
                "parser_status": "ERROR",
                "raw_output": str(data)[:500] if isinstance(data, dict) else str(data),
            },
        )
        raise ValidationError(str(e), data=data)


__all__ = [
    "CriticOutput",
    "CriticViolationOutput",
    "HindsightBatchOutput",
    "HindsightSingleEvaluationOutput",
    "JSONParseError",
    "ParserStatus",
    "SimulatorConsequenceOutput",
    "SimulatorOutput",
    "StructuredDecision",
    "ValidationError",
    "extract_json",
    "log_parser_diagnostic",
    "parse_and_validate",
    "parse_and_validate_critic_output",
    "parse_and_validate_simulator_output",
    "parse_and_validate_hindsight_batch_output",
    "parse_and_validate_hindsight_single_output",
    "parse_structured_decision",
]


# -----------------------------------------------------------------------------
# Critic (output strutturato: decision, violated_hard, violations, revision_guidance)
# -----------------------------------------------------------------------------


class CriticViolationOutput(BaseModel):
    """Singola violazione nell'output del Critic. Strict: nessun campo extra."""

    model_config = ConfigDict(extra="forbid")

    principle_id: str
    severity: float
    rationale: str
    evidence: str


class CriticOutput(BaseModel):
    """Output atteso del modulo Critic. Strict: nessun campo extra."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["PROCEED", "REVISE", "REFUSE"]
    violated_hard: bool
    revision_guidance: str
    violations: list[CriticViolationOutput]


# -----------------------------------------------------------------------------
# Simulator (output: lista di conseguenze)
# -----------------------------------------------------------------------------

_SCENARIO_TYPE = Literal[
    "immediate_harm",
    "downstream_misuse",
    "social_impact",
    "legal_consequence",
    "positive_outcome",
]

_HARM_TYPE = Literal[
    "financial_loss",
    "physical_harm",
    "psychological_harm",
    "privacy_breach",
    "legal_risk",
    "reputational_harm",
    "security_breach",
    "discrimination",
    "misinformation",
    "exploitation",
    "self_harm",
    "emotional_manipulation",  # CORE.MANIPULATION.1, CORE.AUTONOMY.1
    "financial_deception",  # FIN.SCAM.1, CORE.DECEPTION.1
    "none",
]

_HARM_SCOPE = Literal["individual", "group", "societal", "systemic"]


class SimulatorConsequenceOutput(BaseModel):
    """Singola conseguenza nell'output del Simulator. Strict: nessun campo extra."""

    model_config = ConfigDict(extra="forbid")

    text: str
    likelihood: float
    scenario_type: _SCENARIO_TYPE = "social_impact"
    outcome_valence: float = 0.0
    affected_stakeholders: list[str] = []
    harm_type: _HARM_TYPE
    harm_severity: float
    harm_scope: _HARM_SCOPE
    reversibility: float

    @field_validator("harm_severity", "reversibility")
    @classmethod
    def _clamp_0_1(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class SimulatorOutput(BaseModel):
    """Output atteso del modulo Simulator. Strict: nessun campo extra."""

    model_config = ConfigDict(extra="forbid")

    consequences: list[SimulatorConsequenceOutput]


# -----------------------------------------------------------------------------
# Hindsight (singola valutazione e batch)
# -----------------------------------------------------------------------------


class HindsightSingleEvaluationOutput(BaseModel):
    """Singola valutazione hindsight (usata sia in risposta singola che in batch).
    Strict: nessun campo extra."""

    model_config = ConfigDict(extra="forbid")

    safety: float = 0.0
    helpfulness: float = 0.0
    honesty: float = 0.0
    harm_probability: float = 0.0
    benefit_probability: float = 0.5
    confidence: float = 0.8
    rationale: str = ""
    scenario_id: str = "default"


class HindsightBatchOutput(BaseModel):
    """Output atteso del modulo Hindsight per valutazioni batch.
    Strict: nessun campo extra."""

    model_config = ConfigDict(extra="forbid")

    evaluations: list[HindsightSingleEvaluationOutput]


def _normalize_unicode_quotes(s: str) -> str:
    """Map common Unicode quote characters to ASCII quotes for key matching."""
    for u, a in (
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u201e", '"'),
        ("\u201f", '"'),
        ("\u2033", '"'),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201a", "'"),
        ("\uff02", '"'),
    ):
        s = s.replace(u, a)
    return s


def _strip_invisible_chars(s: str) -> str:
    """Remove BOM / zero-width characters that break naive key normalization."""
    return s.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\u2060", "")


def _key_aliases_evaluations(key: str) -> bool:
    """Return True if *key* is a malformed variant of the JSON field name ``evaluations``."""
    s = _strip_invisible_chars(_normalize_unicode_quotes(str(key)))
    collapsed = re.sub(r"\s+", "", s.strip()).strip('"`').lower()
    if collapsed == "evaluations":
        return True
    letters_only = re.sub(r"[^a-z]", "", collapsed)
    return letters_only == "evaluations"


def _normalize_hindsight_batch_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Rename malformed top-level keys to ``evaluations`` when LLM output breaks JSON keys.

    Production cases: newline/spaces/quotes inside the key string, Unicode "smart" quotes,
    invisible characters, or both a canonical ``evaluations`` key and a duplicate malformed key
    (the latter must be dropped because ``HindsightBatchOutput`` uses ``extra="forbid"``).
    """
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = dict(data)
    alias_payloads: list[Any] = []

    for k in list(out.keys()):
        if k == "evaluations":
            continue
        if _key_aliases_evaluations(k):
            alias_payloads.append(out.pop(k))

    if "evaluations" not in out and alias_payloads:
        out["evaluations"] = alias_payloads[0]
    # If ``evaluations`` is already present, duplicate alias keys were popped above.

    return out


# -----------------------------------------------------------------------------
# Parsing e validazione generica
# -----------------------------------------------------------------------------

T = TypeVar("T")


class ValidationError(Exception):
    """Errore di validazione: JSON estratto ma struttura/contenuto non validi."""

    def __init__(self, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


def log_parser_diagnostic(
    request_id: str,
    parser_status: ParserStatus,
    raw_output_keys: list[str],
    parsed_output_keys: list[str],
    final_action: str,
    path_decision: str,
    risk_level: str | None = None,
) -> None:
    """
    Log strutturato per diagnostica parser. Emesso sempre (anche quando OK).
    Il benchmark può intercettare tramite extra['moralstack_parser_diagnostic'].
    """
    extra: dict[str, Any] = {
        "moralstack_parser_diagnostic": True,
        "request_id": request_id,
        "parser_status": parser_status,
        "raw_output_keys": raw_output_keys,
        "parsed_output_keys": parsed_output_keys,
        "final_action": final_action,
        "path_decision": path_decision,
    }
    if risk_level is not None:
        extra["risk_level"] = risk_level
    logger.info(
        "parser_diagnostic request_id=%s parser_status=%s final_action=%s path_decision=%s",
        request_id,
        parser_status,
        final_action,
        path_decision,
        extra=extra,
    )


def parse_and_validate(
    text: str,
    validator: Callable[[dict[str, Any]], T],
) -> T:
    """
    Estrae JSON dal testo LLM e lo valida tramite un callable.

    È il flusso standard: extract_json (singolo strato condiviso) + validazione
    specifica per tipo di output.

    Args:
        text: Risposta grezza dell'LLM (può contenere markdown, testo prima/dopo).
        validator: Callable che riceve il dict e restituisce un valore tipizzato T.
        Può sollevare ValidationError se i dati non sono validi.

    Returns:
        Il valore restituito da validator (tipizzato come T).

    Raises:
        JSONParseError: Impossibile estrarre JSON valido dal testo.
        ValidationError: JSON estratto ma validazione fallita.
    """
    data = extract_json(text)
    return validator(data)


# -----------------------------------------------------------------------------
# Helper: parse_and_validate per ogni tipo di output
# -----------------------------------------------------------------------------


def parse_and_validate_critic_output(text: str) -> CriticOutput:
    """Estrae JSON dal testo e valida con CriticOutput (strict).
    Solleva JSONParseError o ValidationError."""
    return parse_and_validate(text, CriticOutput.model_validate)


def parse_and_validate_simulator_output(text: str) -> SimulatorOutput:
    """Estrae JSON dal testo e valida con SimulatorOutput (strict).
    Solleva JSONParseError o ValidationError."""
    return parse_and_validate(text, SimulatorOutput.model_validate)


def parse_and_validate_hindsight_batch_output(text: str) -> HindsightBatchOutput:
    """Estrae JSON e valida come batch hindsight (evaluations).
    Solleva JSONParseError o ValidationError."""
    raw = extract_json(text)
    if isinstance(raw, list):
        raw = {"evaluations": raw}
    elif not isinstance(raw, dict):
        raise ValidationError(
            "Hindsight batch root must be a JSON object or array",
            data=None,
        )
    normalized = _normalize_hindsight_batch_dict(raw)
    return HindsightBatchOutput.model_validate(normalized)


def parse_and_validate_hindsight_single_output(text: str) -> HindsightSingleEvaluationOutput:
    """Estrae JSON e valida come singola valutazione hindsight.
    Solleva JSONParseError o ValidationError."""
    return parse_and_validate(text, HindsightSingleEvaluationOutput.model_validate)
