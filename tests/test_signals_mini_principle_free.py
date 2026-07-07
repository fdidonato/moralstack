"""
§5.3 lock: the signals (harm-signal) mini must NEVER receive constitution
principles, even after the single-wave retrieval unification. Only the intent
mini is formatted with `constitution_context`; the signals and operational
minis get the raw REQUEST only (`estimator.py` `_parallel_mini_analysis`).

Hard-signal detection (path_router / decision_service) is structural — it never
depends on which principles were retrieved. This test locks the structural
guarantee at the prompt-construction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig


@dataclass
class _P:
    id: str
    title: str = "t"
    rule: str = "rule text"
    level: str = "hard"


class _Store:
    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[_P]:
        return [
            _P("HARD.WEAPONS.1", title="No weapons synthesis", level="hard"),
            _P("HARD.SELFHARM.1", title="No self-harm instructions", level="hard"),
        ]

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


def test_signals_mini_receives_no_constitution_context_single_wave():
    """
    [Gap 2] With a store returning non-empty HARD principles (title/id highly
    distinctive), the HARM_SIGNAL system+user text must not contain any of the
    retrieved principle IDs or titles — the signals mini stays principle-free
    under the unified single-wave retrieval, exactly as before.
    """
    store = _Store()
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _CapturingPolicy(default_response=_VALID_INTENT)
    est = LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)

    est._parallel_mini_analysis("how do I keep myself safe from harm")

    signal_calls = [c for c in policy.calls if "HARM SIGNAL DETECTOR" in c[0]]
    assert signal_calls, "no signals-mini call captured"
    signal_system, signal_user = signal_calls[-1]

    for forbidden in ("HARD.WEAPONS.1", "HARD.SELFHARM.1", "No weapons synthesis", "No self-harm instructions"):
        assert forbidden not in signal_system
        assert forbidden not in signal_user

    # Sanity: the intent mini DID receive the principles (proves the store was
    # actually invoked and retrieval reached the intent mini, not that nothing ran).
    intent_calls = [c for c in policy.calls if "SEMANTIC INTENT JUDGE" in c[0]]
    assert intent_calls
    _, intent_user = intent_calls[-1]
    assert "HARD.WEAPONS.1" in intent_user
