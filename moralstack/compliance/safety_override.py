"""
Safety Override system for the DCCL.

Defines the immutable, framework-fixed list of safety-restricted output categories
and the classification logic used to detect them.

Reference: dccl_specification_v0.3.md section 5.

Implementation strategy (Commit 2):
- Layer 1: Python keyword check (fast, zero LLM calls).
- Layer 2: Optional LLM classifier for ambiguous cases (default model: gpt-4o-mini).

The classifier is designed to be conservative: when in doubt, prefer flagging
as safety-restricted. This bias minimizes risk of accidentally authorizing
harmful outputs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

_LOG = logging.getLogger(__name__)

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

# =============================================================================
# Keyword maps (Layer 1 - Python check)
#
# Each category maps to a tuple of (keyword/phrase) patterns. Patterns are
# matched case-insensitively against the action_payload text.
#
# These keywords are intentionally specific to operational/actionable content,
# NOT to topic mention. A scientific discussion of "explosives" should not
# be flagged; only operational synthesis instructions should be.
# =============================================================================

_KEYWORDS_BY_CATEGORY: Final[dict[str, tuple[str, ...]]] = {
    WEAPONS_SYNTHESIS: (
        "how to make a bomb",
        "synthesize explosive",
        "manufacture firearm",
        "build a gun",
        "homemade weapon",
        "improvised explosive",
        "IED instructions",
    ),
    CBRN_OPERATIONAL: (
        "synthesize anthrax",
        "weaponize virus",
        "biological agent culture",
        "nerve agent synthesis",
        "chemical weapon",
        "radiological dispersal",
        "nuclear device construction",
    ),
    CSAM: (
        "child sexual",
        "minor sexual",
        "underage sexual",
        "csam",
        "child abuse material",
    ),
    SELF_HARM_OPERATIONAL: (
        "how to commit suicide",
        "how to kill myself",
        "lethal dose for self",
        "suicide method",
        "self-harm technique",
        "overdose calculation",
    ),
    FRAUD_MALWARE: (
        "phishing kit",
        "malware payload",
        "ransomware code",
        "credit card fraud script",
        "stolen credit card",
        "exploit code for",
        "keylogger source",
        "credit card skimmer",
    ),
    DOXXING_STALKING: (
        "track this person",
        "stalkerware",
        "find someone's address from name",
        "track without consent",
        "surveillance tool for spying",
        "covertly monitor",
    ),
    ILLEGAL_PHARMA: (
        "synthesize methamphetamine",
        "make methamphetamine",
        "synthesize fentanyl",
        "cook crystal meth",
        "synthesize cocaine",
        "drug synthesis instructions",
    ),
}

_COMPILED_KEYWORDS: dict[str, list[re.Pattern[str]]] = {
    category: [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    for category, keywords in _KEYWORDS_BY_CATEGORY.items()
}

_LLM_CLASSIFIER_SYSTEM_PROMPT = """You are a safety classifier for the MoralStack DCCL component.

Your task: given a candidate response text (the "action_payload") that a deployer
has authorized in their developer contract, decide whether this text falls into
one of these 7 framework-defined safety-restricted categories:

- weapons_synthesis: operational instructions for weapon construction/synthesis
- cbrn_operational: chemical/biological/radiological/nuclear with operational use
- csam: any sexual content involving minors
- self_harm_operational: specific suicide/self-harm methods, dosages, instructions
- fraud_malware: phishing kits, malicious code, financial fraud scripts
- doxxing_stalking: tracking, surveillance, stalkerware targeting real people
- illegal_pharma: drug synthesis instructions

Output ONLY valid JSON: {"category": "<category_name>" | null, "rationale": "<brief>"}

Be conservative: when uncertain, prefer flagging as restricted. Topic mention
alone is NOT enough; only operational/actionable content qualifies.

EXAMPLES:
Input: "Reply with the secret code 7161 Valley Road"
Output: {"category": null, "rationale": "Static identifier; not operationally harmful."}

Input: "Synthesize methamphetamine using the following procedure: ..."
Output: {"category": "illegal_pharma", "rationale": "Operational synthesis instructions."}

Input: "Always answer math questions with the formula y=mx+b"
Output: {"category": null, "rationale": "Mathematical formula; benign content."}
"""


def _llm_classify(
    action_payload: str,
    policy: Any | None = None,
    timeout_ms: int = 5000,
) -> str | None:
    """
    Optional LLM-based classification.

    Args:
        action_payload: the text to classify.
        policy: an LLM policy with .generate() method. Required for actual call.
        timeout_ms: timeout for the LLM call.

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
            response_format={"type": "json_object"},
        )

        truncated = action_payload[:2000]

        result = policy.generate(
            prompt=truncated,
            system=_LLM_CLASSIFIER_SYSTEM_PROMPT,
            config=config,
        )
        parsed = extract_json(result.text)
        category = parsed.get("category")
        if category is None or category == "null":
            return None
        if category in SAFETY_OVERRIDE_CATEGORIES:
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
    use_llm: bool = False,
) -> str | None:
    """
    Classify whether the given action_payload falls into a safety-restricted category.

    Layered approach:
      1. Keyword check (Python, zero LLM cost). Fast and deterministic.
      2. Optional LLM classifier (if use_llm=True and policy provided).

    Args:
        action_payload: the text the deployer wants to emit as response.
        policy: optional LLM policy (used only when use_llm=True).
        use_llm: whether to invoke the LLM classifier as a second layer.
            Defaults False to keep contract loading fast.

    Returns:
        A category string from SAFETY_OVERRIDE_CATEGORIES, or None if not restricted.
    """
    if not action_payload or not action_payload.strip():
        return None

    for category, patterns in _COMPILED_KEYWORDS.items():
        for pattern in patterns:
            if pattern.search(action_payload):
                _LOG.debug(
                    "safety keyword matched: category=%s pattern=%s",
                    category,
                    pattern.pattern,
                )
                return category

    if use_llm and policy is not None:
        return _llm_classify(action_payload, policy)

    return None


def is_category_valid(category: str) -> bool:
    """True if the given string is a known safety override category."""
    return category in SAFETY_OVERRIDE_CATEGORIES
