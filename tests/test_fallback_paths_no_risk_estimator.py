"""
[§5.6/§7 fail-safe] `risk_estimator=None` (`controller.py` `_estimate_risk` default
path): the risk-owned retrieval never runs (`retrieval_succeeded` stays False on the
default `RiskEstimation`), so the controller passes `request_analysis=None`;
deliberation still retrieves (today's wave-2 fallback) and reaches the critic with
non-empty principles — no crash, no silently-dropped principles.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds
from moralstack.utils.output_protection import create_protector


@dataclass
class _Principle:
    id: str
    title: str = "t"
    rule: str = "rule text"
    level: str = "hard"


class _CountingStore:
    def __init__(self, principles: list[_Principle]) -> None:
        self._principles = principles
        self.calls: list[dict[str, Any]] = []

    def get_relevant_principles(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> list[_Principle]:
        self.calls.append({"query": query, "top_k": top_k, "domain": domain, "retrieval_phase": retrieval_phase})
        return list(self._principles)

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": ["core"]}

    def get_constitution(self, domain: str | None = None) -> Any:
        return SimpleNamespace(principles=list(self._principles), active_overlay=None, constitution_corrupted=False)

    def has_excluded_domains(self) -> bool:
        return False


@dataclass
class _GenResult:
    text: str

    def token_usage_json(self) -> str | None:
        return None


def test_no_risk_estimator_deliberation_still_retrieves_and_reaches_critic() -> None:
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    seen: list[list[str]] = []

    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)

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

    config = OrchestratorConfig(
        risk_thresholds=RiskThresholds(),
        max_deliberation_cycles=1,
        timeout_ms=60_000,
        parallel_module_calls=False,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
    )
    policy = MagicMock()
    policy.generate.return_value = _GenResult(text="A safe response.")
    controller = OrchestrationController(
        config=config,
        policy=policy,
        risk_estimator=None,  # <-- degraded path under test
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
    )

    request = ProcessedRequest(prompt="Please discuss a sensitive but safe topic in detail.")
    result = controller.process(request)  # must not raise

    assert result is not None
    assert len(store.calls) >= 1, "deliberation must still retrieve when no risk estimator is configured"
    assert seen, "critic.critique was never called"
    assert seen[0] == ["HARD.1"], "non-empty principles must reach the critic"
