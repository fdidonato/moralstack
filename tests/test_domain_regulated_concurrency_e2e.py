"""T5/T7 — end-to-end concurrency, ai/plans/retrieval-request-scoped-state.md.

Real `LLMBasedRiskEstimator.estimate()` -> real `OrchestrationController.process()`
-> persisted domain (`request.user_context.domain_overlay`, controller.py:2496-2503).
Offline/deterministic: `_MiniPolicy` routes the 3 mini-estimator calls by
system-prompt substring (pattern from tests/test_single_retrieval_wave_e2e.py),
no network.

Why beyond T1-T4: the only test that would still fail if the fix closes both
known channels but leaves a third leak elsewhere in the chain (risk estimator
-> controller -> persisted request domain).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from moralstack.constitution.retrieval_result import PrincipleRetrievalResult
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest, RiskThresholds
from moralstack.utils.output_protection import create_protector

_DOMAIN_MARKER = "DOMAIN:"


class _GatedDomainStore:
    """E2E constitution-store double: derives a domain from a `DOMAIN:<name>`
    marker embedded in the query (both risk-routing and deliberation-retrieval
    queries carry the user prompt verbatim, so the marker survives), gates on
    one designated domain the same way GatedSharedDebugInfoStore does (T1/T2),
    and answers get_constitution/has_excluded_domains so the full
    controller.process() path — including the domain-exclusion refusal route
    (T7) — runs offline."""

    def __init__(self, *, gate_domain: str | None = None, excluded_domains: frozenset[str] = frozenset()) -> None:
        self._entered = threading.Event()
        self._release = threading.Event()
        self._gate_domain = gate_domain
        self._excluded_domains = excluded_domains

    def _domain_for(self, query: str) -> str:
        idx = query.find(_DOMAIN_MARKER)
        if idx == -1:
            return "medical"
        return query[idx + len(_DOMAIN_MARKER) :].split()[0]

    def retrieve(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> PrincipleRetrievalResult:
        own = self._domain_for(query)
        if self._gate_domain is not None and own == self._gate_domain:
            self._entered.set()
            assert self._release.wait(timeout=5.0), "release not signaled: broken test setup"
        return PrincipleRetrievalResult(
            principles=(),
            prefiltered_domains=("core", own),
            debug_info={"prefiltered_domains": ["core", own]},
        )

    def get_constitution(self, domain: str | None = None) -> Any:
        overlay = None
        if domain in self._excluded_domains:
            overlay = SimpleNamespace(excluded=True, sensitive=False, sensitive_risk_floor=None)
        return SimpleNamespace(principles=[], active_overlay=overlay, constitution_corrupted=False)

    def has_excluded_domains(self) -> bool:
        return bool(self._excluded_domains)


@dataclass
class _GenResult:
    text: str
    tokens_used: int = 10
    prompt_tokens: int = 5
    completion_tokens: int = 5

    def token_usage_json(self) -> str | None:
        return None


class _MiniPolicy:
    """Policy double for the 3 risk mini-estimators: routes by system-prompt substring."""

    def __init__(self, intent_json: str, signals_json: str, operational_json: str) -> None:
        self.model = "test-model"
        self._responses = {
            "SEMANTIC INTENT JUDGE": intent_json,
            "HARM SIGNAL DETECTOR": signals_json,
            "OPERATIONAL RISK ASSESSOR": operational_json,
        }

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> _GenResult:
        for marker, text in self._responses.items():
            if marker in system:
                return _GenResult(text=text)
        return _GenResult(text="{}")


def _signal_json() -> str:
    import json

    out = {sig.key: "no" for sig in signal_registry.signals.values()}
    out["domain_sensitivity"] = "HIGH"
    return json.dumps(out)


_INTENT_SENSITIVE_JSON = """{
  "rationale": "sensitive discussion, no operational intent",
  "detected_language": "en",
  "intent_to_harm": "no",
  "requested_instructions": "no",
  "intent_operational": false,
  "request_type": "sensitive_topic",
  "harm_type": "none",
  "intent_clarity": "HIGH",
  "stated_personal_bias": "no",
  "seeks_norm_circumvention": "no"
}"""

_OPERATIONAL_MODERATE_JSON = """{
  "rationale": "sensitive but not actionable",
  "operational_risk": "LOW",
  "risk_score": 0.5,
  "confidence": 0.8,
  "misuse_plausibility": "LOW",
  "actionability_risk": "LOW",
  "risk_policy_action": "DELIBERATE"
}"""


def _make_risk_estimator(store: _GatedDomainStore) -> LLMBasedRiskEstimator:
    config = RiskEstimatorConfig(
        intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
    )
    policy = _MiniPolicy(
        intent_json=_INTENT_SENSITIVE_JSON,
        signals_json=_signal_json(),
        operational_json=_OPERATIONAL_MODERATE_JSON,
    )
    return LLMBasedRiskEstimator(policy=policy, config=config, constitution_store=store)


def _build_critic() -> MagicMock:
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)

    def _critique(request, response, constitution, principles=None, **_kw: Any) -> MagicMock:
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
            tokens_used=0,
            prompt_tokens=None,
            completion_tokens=None,
            token_usage_source=None,
            enumerated_output_gate_applied=False,
        )

    critic.critique.side_effect = _critique
    critic.critique_with_relevant_principles.side_effect = AssertionError("must not re-retrieve")
    return critic


def _make_controller(store: _GatedDomainStore) -> OrchestrationController:
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
    return OrchestrationController(
        config=config,
        policy=policy,
        risk_estimator=_make_risk_estimator(store),
        critic=_build_critic(),
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
    )


def test_process_pairs_query_to_domain_under_deterministic_concurrent_interleave() -> None:
    """T5 — thread A ("legal") gates inside its own retrieval; thread B completes
    an entire controller.process() for another domain in between; A resumes.
    Each request's persisted domain (request.user_context.domain_overlay) must be
    its own — never the other's."""
    store = _GatedDomainStore(gate_domain="legal")
    controller = _make_controller(store)

    result_a: dict[str, Any] = {}

    def thread_a() -> None:
        req_a = ProcessedRequest(prompt="DOMAIN:legal please discuss a sensitive but safe topic in detail.")
        controller.process(req_a)
        result_a["req"] = req_a

    t = threading.Thread(target=thread_a, name="thread-a")
    t.start()
    assert store._entered.wait(timeout=5.0), "thread A did not reach the gate"

    req_b = ProcessedRequest(prompt="DOMAIN:medical please discuss a sensitive but safe topic in detail.")
    controller.process(req_b)

    store._release.set()
    t.join(timeout=15.0)
    assert not t.is_alive()

    assert req_b.user_context.domain_overlay == "medical"
    assert result_a["req"].user_context.domain_overlay == "legal"


def test_process_pairs_query_to_domain_under_5way_concurrent_stress() -> None:
    """T5 3+-way variant (mandatory per plan) — ThreadPoolExecutor(5) + Barrier(5)
    so the fix cannot pass 'by accident' with exactly two contenders."""
    store = _GatedDomainStore(gate_domain=None)
    controller = _make_controller(store)
    domains = ["legal", "medical", "financial", "coding", "creative"]
    barrier = threading.Barrier(len(domains))

    def run_one(domain: str) -> tuple[str, str | None]:
        barrier.wait(timeout=10.0)
        req = ProcessedRequest(prompt=f"DOMAIN:{domain} please discuss a sensitive but safe topic in detail.")
        controller.process(req)
        return domain, req.user_context.domain_overlay

    with ThreadPoolExecutor(max_workers=len(domains)) as ex:
        results = list(ex.map(run_one, domains))

    for expected, actual in results:
        assert actual == expected, f"expected own domain {expected!r}, got {actual!r} (cross-request leak): {results}"


def test_excluded_domain_still_refuses_under_interleave() -> None:
    """T7 — the highest-stakes consumer (controller.py's domain-exclusion refusal
    route) exercised under interleave. No shipped overlay declares `excluded`, so
    this fixture is the only coverage of that route. Thread A's domain is
    excluded and gates; thread B's domain is not excluded and runs to
    completion concurrently; both must resolve to their OWN routing."""
    store = _GatedDomainStore(gate_domain="excluded_domain", excluded_domains=frozenset({"excluded_domain"}))
    controller = _make_controller(store)

    result_a: dict[str, Any] = {}

    def thread_a() -> None:
        req_a = ProcessedRequest(prompt="DOMAIN:excluded_domain please discuss a sensitive but safe topic.")
        result_a["r"] = controller.process(req_a)

    t = threading.Thread(target=thread_a, name="thread-a")
    t.start()
    assert store._entered.wait(timeout=5.0), "thread A did not reach the gate"

    req_b = ProcessedRequest(prompt="DOMAIN:medical please discuss a sensitive but safe topic.")
    result_b = controller.process(req_b)

    store._release.set()
    t.join(timeout=15.0)
    assert not t.is_alive()

    assert result_b.path != "DOMAIN_EXCLUDED", "non-excluded concurrent request must not be routed as excluded"
    assert result_a["r"].path == "DOMAIN_EXCLUDED", "excluded-domain request must still refuse under interleave"
