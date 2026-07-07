"""
Intent-prompt caching refactor: the 5 SEMANTIC ANALYSIS GUIDELINES move from the
intent mini's per-request USER message into its static SYSTEM prompt
(`INTENT_CONTEXT_SYSTEM_PROMPT`), so they join the cacheable static prefix
(OpenAI prompt caching). `INTENT_CONTEXT_PROMPT_TEMPLATE` stays `REQUEST` +
`{constitution_context}`; `constitution_context` now carries only the variable
principles list — no guidelines, no duplication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import INTENT_CONTEXT_SYSTEM_PROMPT
from moralstack.models.risk.schema import RiskEstimatorConfig

_GUIDELINE_LINES = [
    "1. These principles are RELEVANT to the topic, but the request may NOT violate them.",
    "2. Analyze the INTENT and MEANING, not the presence of specific words.",
    "3. A question ABOUT a sensitive topic is NOT the same as REQUESTING harmful content.",
    "4. Academic/educational discussions RESPECT principles by promoting understanding.",
    "5. Only classify as harmful if the request ACTIVELY SEEKS TO CAUSE HARM:",
]


def test_guidelines_present_in_intent_system_prompt():
    for line in _GUIDELINE_LINES:
        assert line in INTENT_CONTEXT_SYSTEM_PROMPT, f"missing guideline line in system prompt: {line!r}"


@dataclass
class _P:
    id: str
    title: str = "t"
    rule: str = "rule text"
    level: str = "soft"


class _Store:
    """Constitution store double returning HARD + SOFT principles."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[_P]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain})
        return [_P("HARD.ID.1", title="Hard One", level="hard"), _P("SOFT.ID.1", title="Soft One", level="soft")]

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": ["core"]}


@dataclass
class _GenResult:
    text: str
    tokens_used: int = 10
    prompt_tokens: int = 5
    completion_tokens: int = 5

    def token_usage_json(self) -> str | None:
        return None


class _CapturingPolicy:
    def __init__(self, default_response: str) -> None:
        self.calls: list[tuple[str, str]] = []
        self.default_response = default_response
        self.model = "test-model"

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> _GenResult:
        self.calls.append((system, prompt))
        return _GenResult(text=self.default_response)


_VALID_INTENT = """{
  "rationale": "r",
  "detected_language": "en",
  "intent_to_harm": "no",
  "requested_instructions": "no",
  "intent_operational": false,
  "request_type": "factual_query",
  "harm_type": "none",
  "intent_clarity": "HIGH",
  "stated_personal_bias": "no",
  "seeks_norm_circumvention": "no"
}"""


def test_guidelines_absent_from_intent_user_prompt():
    """
    [Gap 6] Drive the real intent-mini user message (via `_parallel_mini_analysis`)
    and assert none of the 5 guideline lines leaked into the USER prompt, while the
    retrieved HARD/SOFT principle IDs are still present there (principles were moved
    to the user tail only; guidelines to the system prompt only — no duplication,
    no partial move).
    """
    store = _Store()
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _CapturingPolicy(default_response=_VALID_INTENT)
    est = LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)

    est._parallel_mini_analysis("does this violate the hard constraint")

    intent_calls = [c for c in policy.calls if "SEMANTIC INTENT JUDGE" in c[0]]
    assert intent_calls, "no intent-mini call captured"
    intent_system, intent_user = intent_calls[-1]

    assert intent_system == INTENT_CONTEXT_SYSTEM_PROMPT
    for line in _GUIDELINE_LINES:
        assert line not in intent_user, f"guideline line leaked into intent user prompt: {line!r}"

    assert "HARD.ID.1" in intent_user
    assert "SOFT.ID.1" in intent_user
