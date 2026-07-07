"""
[Gap 5] top_k reconciliation (problem C): a single retrieval at
`max(risk_top_k, critic_top_k)`; each consumer slices down to its own top_k —
never widened beyond its configured bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import DeliberationDependencies, OrchestratorConfig, ProcessedRequest


@dataclass
class _P:
    id: str
    title: str = "t"
    rule: str = "rule"
    level: str = "hard"


class _Store:
    def __init__(self, principles: list[_P]) -> None:
        self._principles = principles
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[_P]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain})
        return list(self._principles)[:top_k]

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": ["core"]}


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


def test_unified_topk_is_max_of_risk_and_critic():
    """A single retrieval call happens at top_k=20 when the caller requests 20."""
    principles = [_P(f"P.{i}") for i in range(25)]
    store = _Store(principles)
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _CapturingPolicy(default_response=_VALID_INTENT)
    est = LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)

    est.estimate("some request", retrieval_query="some request", retrieval_top_k=20)

    assert len(store.calls) == 1
    assert store.calls[0]["top_k"] == 20


def test_risk_intent_formatting_slices_to_risk_topk():
    """Intent-mini formatting slices to self._top_k (10) even when retrieval fetched 20."""
    principles = [_P(f"HARD.{i}", level="hard") for i in range(15)]
    store = _Store(principles)
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _CapturingPolicy(default_response=_VALID_INTENT)
    est = LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)
    assert est._top_k == 10  # default env value

    est.estimate("some request", retrieval_query="some request", retrieval_top_k=20)

    intent_calls = [c for c in policy.calls if "SEMANTIC INTENT JUDGE" in c[0]]
    assert intent_calls
    _, intent_user = intent_calls[-1]
    # Only the first 10 HARD ids should appear formatted in the intent user prompt.
    present = [f"HARD.{i}" in intent_user for i in range(15)]
    assert present[:10] == [True] * 10
    assert present[10:] == [False] * 5


def test_critic_reuse_slices_to_critic_topk_when_smaller_than_unified():
    """Critic top_k=5 -> critic sees 5 principles, not the full unified 20 — no widening."""

    class _CriticCfg:
        top_k_principles = 5

    critic = MagicMock()
    critic.config = _CriticCfg()
    seen: list[list[str]] = []

    def _critique(request, response, constitution, principles=None, **_kw: Any) -> MagicMock:
        seen.append([p.id for p in (principles or [])])
        return MagicMock(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            violated_hard=False,
            decision="PROCEED",
            revision_guidance="",
            raw_response="{}",
            parse_attempts=1,
            prompt="",
            system_prompt="",
        )

    critic.critique.side_effect = _critique

    deps = DeliberationDependencies(
        policy=None,
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=MagicMock(),
    )
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1,
        timeout_ms=60_000,
        parallel_module_calls=False,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
    )
    runner = DeliberationRunner(cfg, deps, protected_system_prompt="sys", logger=None, assembler=MagicMock())

    from moralstack.orchestration.types import RequestAnalysisContext

    unified_principles = tuple(_P(f"P.{i}") for i in range(20))
    request_analysis = RequestAnalysisContext(
        relevant_principles=unified_principles,
        constitution=object(),
        detected_domain=None,
        retrieval_top_k=20,
    )
    req = ProcessedRequest(request_id="req-topk", prompt="hello")

    class _RiskProto:
        score = 0.5
        risk_category = MagicMock(value="benign")
        detected_language = "en"
        intent_type = ""
        actionability_risk = MagicMock(value="LOW")
        detected_domain = None
        rationale = ""
        operational_risk = MagicMock(value="NONE")
        raw_response = ""
        used_fallback_parse = False
        risk_policy_action = MagicMock(value="ALLOW")
        harm_type = ""

    runner.run_deliberative_path(
        req,
        _RiskProto(),
        __import__("time").time(),
        constitution=object(),
        request_analysis=request_analysis,
    )

    assert seen, "critic.critique was never called"
    assert len(seen[0]) == 5
