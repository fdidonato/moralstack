"""
Safety Override system for the DCCL.

Defines the immutable, framework-fixed list of safety-restricted output categories
and the classification logic used to detect them.

Reference: dccl_specification_v0.3.md section 5.

Classification strategy:
- A single LLM classifier decides whether an authorized ``action_payload`` falls into
  one of the framework-fixed restricted categories. The classifier is **language
  agnostic**: it classifies by meaning, not by matching a fixed keyword list, so an
  operational-harm payload written in any language is caught.
- The previous English keyword pre-filter (Layer 1) has been removed: it gave a false
  sense of coverage (it only matched a handful of English phrases and silently missed
  every paraphrase and every other language). Multilingual request-side coverage is
  provided independently by the risk-estimator hard-signal gate on the compliance
  fast-path (``path_router.has_hard_signal_evidence``); this classifier is the
  output-side, contract-payload check.

The classifier is deliberately conservative on its POSITIVE decision (when in doubt,
flag as restricted), but fails **open** on infrastructure error (returns ``None``),
matching the pre-existing philosophy of not blocking legitimate uses on an LLM outage.
The deterministic request-side gate remains as the non-LLM backstop.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Final

from moralstack.prompts.catalog import PromptSet, default_prompt_set

_LOG = logging.getLogger(__name__)

# Callback invoked with (generation_result, system_prompt, user_prompt) right after the
# classifier LLM call returns, so the caller can persist the call to observability
# identically to the other compliance/mini-estimator LLM calls (tokens, cached, model).
LLMResultCallback = Callable[[Any, str, str], None]

# =============================================================================
# Safety Override Categories (framework-fixed, NOT deployer-configurable)
# =============================================================================

WEAPONS_SYNTHESIS: Final[str] = "weapons_synthesis"
CBRN_OPERATIONAL: Final[str] = "cbrn_operational"
CSAM: Final[str] = "csam"
SELF_HARM_OPERATIONAL: Final[str] = "self_harm_operational"
FRAUD_MALWARE: Final[str] = "fraud_malware"
DOXXING_STALKING: Final[str] = "doxxing_stalking"
ILLEGAL_PHARMA: Final[str] = "illegal_pharma"

SAFETY_OVERRIDE_CATEGORIES: Final[tuple[str, ...]] = (
    WEAPONS_SYNTHESIS,
    CBRN_OPERATIONAL,
    CSAM,
    SELF_HARM_OPERATIONAL,
    FRAUD_MALWARE,
    DOXXING_STALKING,
    ILLEGAL_PHARMA,
)


def safety_override_response_format() -> dict[str, Any]:
    """`response_format` that makes the provider enforce the classifier's output.

    The enum is derived from SAFETY_OVERRIDE_CATEGORIES, so the categories the
    provider accepts and the ones `_classify_with_llm` recognises cannot drift.
    Without it an out-of-enum category is not an error: the classifier logs a
    warning and returns None, i.e. the safety override silently does not fire.
    `category` is nullable because "no restricted category" is the normal answer.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "safety_override_classification",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "rationale"],
                "properties": {
                    "category": {
                        "type": ["string", "null"],
                        "enum": [*SAFETY_OVERRIDE_CATEGORIES, None],
                    },
                    "rationale": {"type": "string"},
                },
            },
        },
    }


# Default re-export of the packaged fragment (``compliance.safety_classifier.system``). Runtime code reads the instance
# ``PromptSet`` instead; this name stays importable with the identical value.
_LLM_CLASSIFIER_SYSTEM_PROMPT = default_prompt_set().text("compliance.safety_classifier.system")


def _llm_classify(
    action_payload: str,
    policy: Any | None = None,
    timeout_ms: int = 5000,
    model: str | None = None,
    on_llm_result: LLMResultCallback | None = None,
    prompt_set: PromptSet | None = None,
) -> str | None:
    """
    LLM-based classification (language agnostic).

    Args:
        action_payload: the text to classify.
        policy: an LLM policy with a ``.generate()`` method. Required for an actual call.
        timeout_ms: reserved (the policy owns its own timeout).
        model: optional model override; when set, the classifier runs on this model
            instead of the primary policy model (used to route the check to a small,
            cheap model without slowing the compliance fast-path).
        on_llm_result: optional callback invoked with
            ``(generation_result, system_prompt, user_prompt)`` right after the LLM call,
            so the caller can persist the call to observability. Best-effort: a callback
            error never changes the classification.
        prompt_set: source of the classifier system prompt; defaults to the packaged set.

    Returns:
        Matching category name or None.
        Returns None if policy is None (offline mode) or on any error
        (conservative fallback: don't block legitimate uses on infrastructure failure).
    """
    _ = timeout_ms
    if policy is None:
        return None

    try:
        from moralstack.models.base import GenerationConfig
        from moralstack.utils.json_utils import extract_json

        config = GenerationConfig(
            max_tokens=128,
            temperature=0.0,
            top_p=1.0,
            response_format=safety_override_response_format(),
        )

        truncated = action_payload[:2000]
        system_prompt = (prompt_set or default_prompt_set()).text("compliance.safety_classifier.system")

        result = policy.generate(
            prompt=truncated,
            system=system_prompt,
            config=config,
            model_override=model,
        )
        if on_llm_result is not None:
            try:
                on_llm_result(result, system_prompt, truncated)
            except Exception:
                _LOG.debug("safety-override on_llm_result callback failed", exc_info=True)
        parsed = extract_json(result.text)
        category = parsed.get("category")
        if category is None or category == "null":
            return None
        if isinstance(category, str) and category in SAFETY_OVERRIDE_CATEGORIES:
            _LOG.debug("safety classifier LLM matched: %s", category)
            return category
        _LOG.warning("safety LLM returned unknown category: %r", category)
        return None
    except Exception as e:
        _LOG.debug("safety classifier LLM failed: %s", e, exc_info=True)
        return None


def classify_safety_override(
    action_payload: str,
    policy: Any | None = None,
    use_llm: bool = True,
    model: str | None = None,
    on_llm_result: LLMResultCallback | None = None,
    prompt_set: PromptSet | None = None,
) -> str | None:
    """
    Classify whether the given action_payload falls into a safety-restricted category.

    Language-agnostic LLM classification. When ``use_llm`` is False or no ``policy`` is
    supplied there is no classification (returns None): there is no deterministic keyword
    fallback anymore. Request-side hard-signal coverage is provided independently by the
    risk-estimator gate (``path_router.has_hard_signal_evidence``).

    Args:
        action_payload: the text the deployer wants to emit as response.
        policy: LLM policy used for classification (required for a non-None result).
        use_llm: whether to invoke the LLM classifier. Defaults True.
        model: optional model override for the classifier call.
        on_llm_result: optional callback invoked with
            ``(generation_result, system_prompt, user_prompt)`` when an LLM call is made,
            so the caller can persist it to observability (tokens, cached, model).
        prompt_set: source of the classifier system prompt; defaults to the packaged set.

    Returns:
        A category string from SAFETY_OVERRIDE_CATEGORIES, or None if not restricted
        (or if it could not be classified: no policy / use_llm False / LLM error).
    """
    if not action_payload or not action_payload.strip():
        return None

    if use_llm and policy is not None:
        return _llm_classify(action_payload, policy, model=model, on_llm_result=on_llm_result, prompt_set=prompt_set)

    return None


def is_category_valid(category: str) -> bool:
    """True if the given string is a known safety override category."""
    return category in SAFETY_OVERRIDE_CATEGORIES
