"""
Single entry point for parsing and validating structured LLM outputs.

All modules that interpret LLM responses must use this module (and not
json_utils or duplicated logic) for:
- JSON extraction from raw text
- schema/content validation
- construction of typed values

Principles:
- Parsing (JSON extraction) and validation (schema/fields) are separate.
- A single shared layer for JSON extraction; validation is pluggable.
- Pydantic models are strict: unexpected fields cause ValidationError.
- No decision fallback: if parsing fails an explicit error is raised.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, field_validator

from moralstack.core.schema import StructuredDecision
from moralstack.utils.json_utils import JSONParseError, extract_json

logger = logging.getLogger(__name__)

# Only OK or ERROR: no FALLBACK for decisions.
ParserStatus = Literal["OK", "ERROR"]


# -----------------------------------------------------------------------------
# Canonical schema (deliberative decisions)
# -----------------------------------------------------------------------------


def parse_structured_decision(
    text: str,
    request_id: str = "",
) -> "StructuredDecision":
    """
    Parses and validates LLM output as StructuredDecision (canonical schema).

    - Validates the schema (keys, enums, types).
    - Normalizes ONLY allowed values (e.g. final_action uppercase).
    - Does NOT invent missing values, does NOT correct semantics, does NOT apply fallbacks.

    Raises:
        JSONParseError: Unable to extract JSON from the text.
        ValidationError: JSON extracted but validation failed.

    On failure it logs request_id, raw_output (truncated), error.
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

    # Normalize only allowed values (enum)
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
# Critic (structured output: decision, violated_hard, violations, revision_guidance)
# -----------------------------------------------------------------------------


class CriticViolationOutput(BaseModel):
    """Single violation in the Critic output. Strict: no extra fields."""

    model_config = ConfigDict(extra="forbid")

    principle_id: str
    severity: float
    rationale: str
    evidence: str


class CriticOutput(BaseModel):
    """Expected output of the Critic module. Strict: no extra fields."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["PROCEED", "REVISE", "REFUSE"]
    violated_hard: bool
    revision_guidance: str
    violations: list[CriticViolationOutput]


# -----------------------------------------------------------------------------
# Simulator (output: list of consequences)
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
    """Single consequence in the Simulator output. Strict: no extra fields."""

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
    """Expected output of the Simulator module. Strict: no extra fields."""

    model_config = ConfigDict(extra="forbid")

    consequences: list[SimulatorConsequenceOutput]


def _enum_schema(literal: Any) -> dict[str, Any]:
    """JSON schema of a string Literal, to avoid duplicating its values."""
    return {"type": "string", "enum": list(get_args(literal))}


# Schema for OpenAI strict Structured Outputs: the provider enforces
# the schema, instead of validating it here after the call has already been paid for.
# Derived from the same Literal aliases of the Pydantic model above, so the two cannot
# diverge. Strict mode requires `additionalProperties: false` and
# every property in `required`: the Pydantic model defaults remain the
# safety net for plain JSON mode, where the field can still be missing.
SIMULATOR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["consequences"],
    "properties": {
        "consequences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "text",
                    "likelihood",
                    "scenario_type",
                    "outcome_valence",
                    "affected_stakeholders",
                    "harm_type",
                    "harm_severity",
                    "harm_scope",
                    "reversibility",
                ],
                "properties": {
                    "text": {"type": "string"},
                    "likelihood": {"type": "number"},
                    "scenario_type": _enum_schema(_SCENARIO_TYPE),
                    "outcome_valence": {"type": "number"},
                    "affected_stakeholders": {"type": "array", "items": {"type": "string"}},
                    "harm_type": _enum_schema(_HARM_TYPE),
                    "harm_severity": {"type": "number"},
                    "harm_scope": _enum_schema(_HARM_SCOPE),
                    "reversibility": {"type": "number"},
                },
            },
        }
    },
}


def simulator_response_format() -> dict[str, Any]:
    """`response_format` that makes the provider enforce SimulatorOutput."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "simulator_output",
            "strict": True,
            "schema": SIMULATOR_JSON_SCHEMA,
        },
    }


# -----------------------------------------------------------------------------
# Hindsight (single evaluation and batch)
# -----------------------------------------------------------------------------


class HindsightSingleEvaluationOutput(BaseModel):
    """Single hindsight evaluation (used both in a single response and in batch).
    Strict: no extra fields."""

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
    """Expected output of the Hindsight module for batch evaluations.
    Strict: no extra fields."""

    model_config = ConfigDict(extra="forbid")

    evaluations: list[HindsightSingleEvaluationOutput]


# Strict Structured Outputs schemas for the modules whose reply shape is fully
# specified by their prompt. Strict mode requires every property in `required`
# and `additionalProperties: false`; the Pydantic defaults above stay the net
# for the plain-JSON path, where a field can still be missing.
#
# Limit worth remembering: the supported JSON Schema subset has no `minimum` /
# `maximum`, so numeric ranges (0.0-1.0 scores) remain the caller's job. These
# schemas guarantee shape, key names and enums — never value ranges or meaning.


def hindsight_batch_response_format() -> dict[str, Any]:
    """`response_format` enforcing HindsightBatchOutput."""
    evaluation = _HINDSIGHT_EVALUATION_SCHEMA
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "hindsight_batch",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["evaluations"],
                "properties": {"evaluations": {"type": "array", "items": evaluation}},
            },
        },
    }


_HINDSIGHT_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "scenario_id",
        "safety",
        "helpfulness",
        "honesty",
        "harm_probability",
        "benefit_probability",
        "confidence",
        "rationale",
    ],
    "properties": {
        "scenario_id": {"type": "string"},
        "safety": {"type": "number"},
        "helpfulness": {"type": "number"},
        "honesty": {"type": "number"},
        "harm_probability": {"type": "number"},
        "benefit_probability": {"type": "number"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
}


def hindsight_single_response_format() -> dict[str, Any]:
    """`response_format` enforcing a single hindsight evaluation.

    Separate from the batch schema on purpose: the module has two prompts and
    two parsers, and one shared schema would make the provider return the wrong
    shape for one of them.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "hindsight_single",
            "strict": True,
            "schema": _HINDSIGHT_EVALUATION_SCHEMA,
        },
    }


def perspective_response_format() -> dict[str, Any]:
    """`response_format` enforcing the perspective evaluation contract."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "perspective_evaluation",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["approval_score", "concerns", "suggestions", "rationale"],
                "properties": {
                    "approval_score": {"type": "number"},
                    "concerns": {"type": "array", "items": {"type": "string"}},
                    "suggestions": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
            },
        },
    }


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
# Generic parsing and validation
# -----------------------------------------------------------------------------

T = TypeVar("T")


class ValidationError(Exception):
    """Validation error: JSON extracted but structure/content invalid."""

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
    Structured log for parser diagnostics. Always emitted (even when OK).
    The benchmark can intercept it via extra['moralstack_parser_diagnostic'].
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
    Extracts JSON from the LLM text and validates it through a callable.

    This is the standard flow: extract_json (single shared layer) + validation
    specific to the output type.

    Args:
        text: Raw LLM response (may contain markdown, text before/after).
        validator: Callable that receives the dict and returns a typed value T.
        May raise ValidationError if the data is invalid.

    Returns:
        The value returned by validator (typed as T).

    Raises:
        JSONParseError: Unable to extract valid JSON from the text.
        ValidationError: JSON extracted but validation failed.
    """
    data = extract_json(text)
    return validator(data)


# -----------------------------------------------------------------------------
# Helpers: parse_and_validate for each output type
# -----------------------------------------------------------------------------


def parse_and_validate_critic_output(text: str) -> CriticOutput:
    """Extracts JSON from the text and validates it with CriticOutput (strict).
    Raises JSONParseError or ValidationError."""
    return parse_and_validate(text, CriticOutput.model_validate)


def parse_and_validate_simulator_output(text: str) -> SimulatorOutput:
    """Extracts JSON from the text and validates it with SimulatorOutput (strict).
    Raises JSONParseError or ValidationError."""
    return parse_and_validate(text, SimulatorOutput.model_validate)


def parse_and_validate_hindsight_batch_output(text: str) -> HindsightBatchOutput:
    """Extracts JSON and validates it as a hindsight batch (evaluations).
    Raises JSONParseError or ValidationError."""
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
    """Extracts JSON and validates it as a single hindsight evaluation.
    Raises JSONParseError or ValidationError."""
    return parse_and_validate(text, HindsightSingleEvaluationOutput.model_validate)
