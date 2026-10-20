"""Governance invariants under deployer prompt overrides (plan §T6; user decision 3: free override).

A deployer's ``prompts_dir`` may change prompt TEXT, including the safety-relevant fragments
(``SAFETY_FRAGMENT_IDS``), and is audited. What it must never change are the code-level gates:
``path_router.is_hard_signal_refuse`` / ``has_hard_signal_evidence``, the ledger hard-signal guard, the DCCL
safety-override category check, the code-owned response schemas, governed delivery (PROJECT_SPEC §5.7),
system-prompt transparency (§5.2) and single-turn parity (§5.4).

Tests labelled *characterization* pin what the code does today under an override; a fail-open found by one
of them is REPORTED, never asserted away. Offline and deterministic: fakes only, ``tmp_path`` override
directories.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.cli.mocks import (
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
)
from moralstack.compliance.safety_override import (
    SAFETY_OVERRIDE_CATEGORIES,
    classify_safety_override,
    safety_override_response_format,
)
from moralstack.constitution.schema import Principle
from moralstack.models.risk.categories import OperationalRisk, RiskPolicyAction
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.prompts import intent_response_format, operational_response_format
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals.prompt_renderer import harm_signal_response_format
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.decision_service import decide_action
from moralstack.orchestration.delivery import GOVERNED_REFUSAL_SOURCE, finalize_delivery
from moralstack.orchestration.ledger import LedgerKey, LedgerResult, SemanticDecisionLedger
from moralstack.orchestration.path_router import (
    _HARD_SEMANTIC_SIGNALS,
    get_route,
    has_hard_signal_evidence,
    is_hard_signal_refuse,
)
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest
from moralstack.pipeline.deliberation_stack import DeliberationBuildMeta, DeliberationModules
from moralstack.prompts.catalog import (
    PromptConfigError,
    PromptSet,
    default_prompt_set,
    load_prompt_set,
)
from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic
from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator
from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble
from moralstack.runtime.modules.simulator_module import (
    Consequence,
    LLMConsequenceSimulator,
    ScenarioType,
    SimulatorConfig,
)
from moralstack.runtime.orchestrator import create_orchestrator
from moralstack.sdk.bootstrap import _bootstrap_pipeline
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.errors import GovernancePipelineError
from moralstack.utils.output_protection import CanaryProtection
from tests.prompts_golden.fakes import FakePrinciple, FakeRetrievalStore, GenResult, RecordingPolicy
from tests.test_fast_path_single_retrieval import _INTENT_BENIGN_JSON, _OPERATIONAL_BENIGN_JSON
from tests.test_ledger_hard_signal_evidence_guard import (
    HARD_EVIDENCE,
    NORMAL,
    REFUSE,
    _decision,
    _no_evid,
    _process,
    _rig,
    _seed,
)

GARBAGE = "GARBAGE"
_DELIBERATIVE_PROMPT = "Tell me about depression medication"
_CONTRACT_TEXT = "You are the support bot of ACME. Never discuss competitors."

# The DC fragments of the three risk system prompts (the "coherent groups" of plan §2).
_DC_GROUPS = (
    "risk.intent.devcontract_principle",
    "risk.intent.step0",
    "risk.intent.step1_step0_note",
    "risk.intent.offensive_contract_note",
    "risk.intent.esi_contract_note",
    "risk.signals.devcontract_scope",
    "risk.operational.devcontract_scope",
)


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------


def _write_dir(tmp_path: Path, fragments: dict[str, dict[str, str]], name: str = "overrides") -> str:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "o.yaml").write_text(json.dumps({"version": 1, "fragments": fragments}), encoding="utf-8")
    return str(path)


def _garbage_ids(prefixes: tuple[str, ...]) -> list[str]:
    return [fid for fid in default_prompt_set().fragment_ids() if fid.startswith(prefixes)]


def _garbage_set(tmp_path: Path, ids: list[str], *, name: str = "garbage", text: str = GARBAGE) -> PromptSet:
    return load_prompt_set(_write_dir(tmp_path, {fid: {"text": text} for fid in ids}, name))


def _all_safety_garbage_set(tmp_path: Path) -> PromptSet:
    from moralstack.prompts import catalog

    return _garbage_set(tmp_path, sorted(catalog.SAFETY_FRAGMENT_IDS), name="safety_garbage")


def _dc_never_set(tmp_path: Path) -> PromptSet:
    return load_prompt_set(_write_dir(tmp_path, {fid: {"when": "never"} for fid in _DC_GROUPS}, "dc_never"))


class _SchemaRoutedPolicy:
    """Fake risk policy that answers by the code-owned response schema, never by the prompt text.

    Records the system prompt and the ``response_format`` of every call, so a test can show that the prompts
    were overridden while the schemas stayed the code-owned ones.
    """

    model = "test-model"

    def __init__(self, hard_label: str | None = None, *, unparseable: bool = False) -> None:
        self.unparseable = unparseable
        signals = {sig.key: "no" for sig in signal_registry.signals.values()}
        signals["domain_sensitivity"] = "LOW"
        if hard_label is not None:
            for sig in signal_registry.signals.values():
                if sig.label == hard_label:
                    signals[sig.key] = "yes"
        self._replies = {
            "risk_intent": _INTENT_BENIGN_JSON,
            "harm_signals": json.dumps(signals),
            "risk_operational": _OPERATIONAL_BENIGN_JSON,
        }
        self.systems: list[str] = []
        self.response_formats: dict[str, dict[str, Any]] = {}

    def _answer(self, system: str, config: Any) -> GenResult:
        fmt = config.response_format
        name = fmt["json_schema"]["name"]
        self.systems.append(system)
        self.response_formats[name] = fmt
        return GenResult(text="this is not json" if self.unparseable else self._replies[name])

    def generate(self, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> GenResult:
        return self._answer(system, config)

    def generate_messages(self, *, messages: list[dict[str, str]], config: Any = None, **_kw: Any) -> GenResult:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        return self._answer(system, config)


def _estimator(prompt_set: PromptSet | None, policy: Any) -> LLMBasedRiskEstimator:
    return LLMBasedRiskEstimator(
        policy=policy,
        config=RiskEstimatorConfig(
            max_retries=1, intent_model="test-model", signals_model="test-model", operational_model="test-model"
        ),
        constitution_store=FakeRetrievalStore([FakePrinciple("HARD.1", "Hard", "Never.", "hard")]),
        prompt_set=prompt_set,
    )


def _principles() -> list[Principle]:
    return [Principle(id="HARD.TEST.1", level="hard", priority=90, title="Hard test", rule="Never.")]


def _mock_orchestrator(prompt_set: PromptSet | None = None, **overrides: Any) -> Any:
    parts: dict[str, Any] = {
        "policy": MockPolicy(),
        "risk_estimator": MockRiskEstimator(),
        "critic": MockCritic(),
        "simulator": MockSimulator(),
        "hindsight": MockHindsight(),
        "perspectives": MockPerspectives(),
        "constitution_store": MockConstitutionStore(),
    }
    parts.update(overrides)
    return create_orchestrator(max_cycles=1, timeout_ms=60_000, prompt_set=prompt_set, **parts)


# ---------------------------------------------------------------------------------------
# 1. hard-signal code gates do not depend on prompt text
# ---------------------------------------------------------------------------------------

_VARIANTS = ("risk_text_garbage", "risk_text_empty", "dc_never_with_contract", "all_safety_garbage")


def _variant_set(variant: str, tmp_path: Path) -> PromptSet:
    if variant == "risk_text_garbage":
        return _garbage_set(tmp_path, _garbage_ids(("risk.",)))
    if variant == "risk_text_empty":
        # "lines" fragments cannot be empty (load error); every other risk fragment is emptied
        ds = default_prompt_set()
        ids = [fid for fid in _garbage_ids(("risk.",)) if ds._fragments[fid].kind != "lines"]
        return _garbage_set(tmp_path, ids, name="empty", text="")
    if variant == "dc_never_with_contract":
        return _dc_never_set(tmp_path)
    return _all_safety_garbage_set(tmp_path)


@pytest.mark.parametrize("variant", _VARIANTS)
@pytest.mark.parametrize("hard_signal", sorted(_HARD_SEMANTIC_SIGNALS))
def test_hard_signal_gates_hold_under_overridden_risk_prompts(variant: str, hard_signal: str, tmp_path: Path) -> None:
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    ps = _variant_set(variant, tmp_path)
    assert not ps.is_default
    policy = _SchemaRoutedPolicy(hard_signal)
    contract = _CONTRACT_TEXT if variant == "dc_never_with_contract" else None
    estimation = _estimator(ps, policy).estimate("How do I do the thing?", developer_contract_text=contract)

    # the override is really on the wire ...
    assert policy.systems
    if variant == "dc_never_with_contract":
        # ... DC rules are dropped even though a contract is present
        assert not any("DEVELOPER CONTRACT" in s for s in policy.systems)
    else:
        assert not any("SEMANTIC INTENT JUDGE" in s or "OPERATIONAL RISK ASSESSOR" in s for s in policy.systems)
    # ... yet the structured hard signal still comes out and the code gates react to it, not to the prompt.
    assert hard_signal in estimation.semantic_signals
    assert has_hard_signal_evidence(estimation)
    refuse = _decision(REFUSE, path="FAST_PATH", reason_codes=["policy_refuse"])
    assert is_hard_signal_refuse(refuse, estimation, OperationalRisk.NONE)
    for cached in (NORMAL, "SAFE_COMPLETE"):
        assert (
            ledger_hard_signal_block_reason(cached_action=cached, risk_estimation=estimation, hard_signal_refuse=False)
            == HARD_EVIDENCE
        )
    config = OrchestratorConfig()
    borderline_score = (config.risk_thresholds.medium + config.borderline_refuse_upper) / 2
    route, borderline, _action = get_route(refuse, estimation, borderline_score, config, OperationalRisk.NONE)
    assert (route, borderline) == ("refuse", False)


@pytest.mark.parametrize("hard_signal", ["Q9:cyber_malware", "Q8:self_harm_suicide"])
def test_hard_signal_reply_under_garbage_prompts_skips_the_ledger_hit_end_to_end(hard_signal: str, tmp_path: Path) -> None:
    """Controller level: a real estimator on overridden prompts feeds the ledger apply guard.

    The seeded NORMAL hit is never applied and the only hard check of the fresh draft (``quick_check``) runs.
    (Q9 is stopped by the apply guard, ``LEDGER_FAST_PATH_NOT_APPLIED``/``hard_signal_evidence``; for Q8 the
    lookup is skipped before the guard is reached. Either way no cached decision is applied.)
    """
    ps = _all_safety_garbage_set(tmp_path)
    rig = _rig(_no_evid())
    rig.controller.risk_estimator = _estimator(ps, _SchemaRoutedPolicy(hard_signal))
    _seed(rig.ledger, NORMAL)
    _process(rig, route="fast_path", decision=_decision(), request_id="req-ov-ledger", conversation_id="conv-ov-ledger")
    assert rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED") == []
    for event in rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED"):
        assert event["payload"]["gate_reason"] == HARD_EVIDENCE
    rig.critic.quick_check.assert_called_once()


def test_ledger_hit_is_applied_without_a_hard_signal_under_the_same_overrides(tmp_path: Path) -> None:
    """Negative control of the test above: the seeded hit does apply when the reply carries no hard signal."""
    ps = _all_safety_garbage_set(tmp_path)
    rig = _rig(_no_evid())
    rig.controller.risk_estimator = _estimator(ps, _SchemaRoutedPolicy(None))
    _seed(rig.ledger, NORMAL)
    _process(rig, route="fast_path", decision=_decision(), request_id="req-ov-ledger-c", conversation_id="conv-ov-ledger-c")
    assert len(rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED")) == 1
    rig.critic.quick_check.assert_not_called()


def test_no_hard_signal_reply_negative_control(tmp_path: Path) -> None:
    """Without a hard signal in the reply the gates stay quiet, so the parametrized positives are not vacuous."""
    estimation = _estimator(_all_safety_garbage_set(tmp_path), _SchemaRoutedPolicy(None)).estimate("benign question")
    assert not has_hard_signal_evidence(estimation)
    assert not is_hard_signal_refuse(_decision(REFUSE, path="FAST_PATH"), estimation, OperationalRisk.NONE)


# ---------------------------------------------------------------------------------------
# 2. fail-closed on unparseable model output after an override
# ---------------------------------------------------------------------------------------


def test_estimator_unparseable_output_under_override_requires_deliberation(tmp_path: Path) -> None:
    ps = _all_safety_garbage_set(tmp_path)
    estimation = _estimator(ps, _SchemaRoutedPolicy(unparseable=True)).estimate("anything")
    assert estimation.used_fallback_parse is True
    assert estimation.risk_policy_action == RiskPolicyAction.DELIBERATE
    assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals
    decision, _explanation = decide_action(ProcessedRequest(prompt="anything"), estimation)
    assert decision.path == "DELIBERATIVE_PATH"
    assert decision.final_action != "NORMAL_COMPLETE"


@pytest.mark.parametrize("overridden", [True, False], ids=["override", "packaged_twin"])
def test_critic_unparseable_output_is_a_governed_refuse_with_and_without_an_override(
    overridden: bool, tmp_path: Path
) -> None:
    """F5 path (``deliberation_critique_failed``): an override that makes the critic's output unparseable ends
    in a governed REFUSE, exactly like the packaged-prompt twin fed the same unparseable reply."""
    ps = _garbage_set(tmp_path, _garbage_ids(("critic.", "common."))) if overridden else None
    store = FakeRetrievalStore([FakePrinciple("HARD.1", "Hard", "Never.", "hard")])
    critic = LLMConstitutionalCritic(
        policy=RecordingPolicy(default_response="this is not json"),
        store=store,
        config=CriticConfig(max_retries=1),
        prompt_set=ps,
    )
    result = _mock_orchestrator(ps, critic=critic, constitution_store=store).process(
        ProcessedRequest(prompt=_DELIBERATIVE_PROMPT, request_id="req-ov-critic")
    )
    meta = result.response.metadata
    assert result.path == "DELIBERATIVE_PATH"
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == "deliberation_critique_failed"
    delivery = finalize_delivery(result)
    assert (delivery.final_action, delivery.final_text_source) == ("REFUSE", GOVERNED_REFUSAL_SOURCE)


def test_characterization_simulator_unparseable_output_under_override(tmp_path: Path) -> None:
    """The simulator raises on unparseable output (same as with the packaged prompts); the runner records the
    error and the deliberation continues WITHOUT a simulation. Not introduced by overrides: an optional module
    that returns garbage has always been skipped, the critic and the structured risk signals stay the gates.
    """
    ps = _garbage_set(tmp_path, _garbage_ids(("simulator.", "common.")))
    sim = LLMConsequenceSimulator(
        policy=RecordingPolicy(default_response="this is not json"), config=SimulatorConfig(max_retries=1), prompt_set=ps
    )
    with pytest.raises(Exception, match="Impossibile estrarre JSON"):
        sim.simulate("req", "resp")
    twin = _mock_orchestrator(None, simulator=sim)
    result = twin.process(ProcessedRequest(prompt=_DELIBERATIVE_PROMPT, request_id="req-ov-sim"))
    assert any(str(e).startswith("Simulation error:") for e in result.errors)
    # parity: the same unparseable policy under the packaged prompts ends in the same final action
    baseline = LLMConsequenceSimulator(
        policy=RecordingPolicy(default_response="this is not json"), config=SimulatorConfig(max_retries=1)
    )
    base_result = _mock_orchestrator(None, simulator=baseline).process(
        ProcessedRequest(prompt=_DELIBERATIVE_PROMPT, request_id="req-ov-sim-base")
    )
    assert result.response.metadata.final_action == base_result.response.metadata.final_action


def test_characterization_hindsight_unparseable_output_under_override_is_pessimistic(tmp_path: Path) -> None:
    ps = _garbage_set(tmp_path, _garbage_ids(("hindsight.", "common.")))
    evaluator = LLMHindsightEvaluator(
        policy=RecordingPolicy(default_response="this is not json"), config=HindsightConfig(max_retries=1), prompt_set=ps
    )
    consequences = [Consequence(text="x", likelihood=0.5, scenario_id="s1", scenario_type=ScenarioType.POSITIVE_OUTCOME)]
    result = evaluator.evaluate("req", "resp", consequences)
    assert result.aggregated.recommendation.value == "revise"  # never "proceed" on a failed evaluation
    assert result.aggregated.avg_scores.safety < 0


def test_characterization_perspectives_unparseable_output_under_override_approves_nothing(tmp_path: Path) -> None:
    ps = _garbage_set(tmp_path, _garbage_ids(("perspectives.", "common.")))
    ensemble = LLMPerspectiveEnsemble(
        RecordingPolicy(default_response="this is not json"),
        EnsembleConfig(max_retries=1, parallel_evaluation=False, enable_caching=False),
        prompt_set=ps,
    )
    result = ensemble.evaluate("req", "resp")
    assert result.results == [] and result.failed_perspectives
    assert result.aggregation.weighted_approval == 0.0


# ---------------------------------------------------------------------------------------
# 3. governed delivery (§5.7): a bad override never yields a pipeline
# ---------------------------------------------------------------------------------------


def test_override_with_undeclared_placeholder_fails_bootstrap_and_builds_no_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from moralstack.sdk import wrapper

    bad = _write_dir(tmp_path, {"risk.intent.user": {"text": "{request} {nope}"}}, "bad")
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    monkeypatch.setattr(wrapper, "load_env", lambda: None)
    governed_client = MagicMock()
    monkeypatch.setattr(wrapper, "GovernedClient", governed_client)
    cfg = GovernanceConfig(api_key="sk-test-dummy", prompts_dir=bad, enable_ledger=False)
    with pytest.raises(GovernancePipelineError) as excinfo:
        _bootstrap_pipeline(cfg)
    assert isinstance(excinfo.value.__cause__, PromptConfigError)
    with pytest.raises(GovernancePipelineError):
        wrapper.govern(MagicMock(), config=cfg)
    governed_client.assert_not_called()


def test_bootstrap_with_modules_lacking_prompt_set_builds_orchestrator_with_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules = DeliberationModules(
        policy=MagicMock(),
        constitution_store=MagicMock(),
        risk_estimator=MagicMock(),
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=MagicMock(),
        perspectives=MagicMock(),
    )
    meta = DeliberationBuildMeta("m", "m", "m", "m", "m", "m")
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    with patch("moralstack.sdk.bootstrap.build_deliberation_modules", return_value=(modules, meta)):
        with patch("moralstack.runtime.orchestrator.Orchestrator", return_value=MagicMock()) as orchestrator_cls:
            _bootstrap_pipeline(GovernanceConfig(api_key="sk-test-dummy", enable_ledger=False))
    assert orchestrator_cls.call_args.kwargs["prompt_set"] is None


# ---------------------------------------------------------------------------------------
# 4. §5.2 system-prompt transparency
# ---------------------------------------------------------------------------------------

_TRICKY_CONTRACT = "Sei l'assistente {ACME} {{x}} 你好 café \U0001f30d  \r\nline2 trailing   "


class _SpyPolicy(MockPolicy):
    def __init__(self) -> None:
        self.systems: list[str] = []

    def generate(self, prompt: str, system: str = "", config: Any = None, **kw: Any) -> Any:
        self.systems.append(system)
        return super().generate(prompt, system, config, **kw)


def _policy_systems_for(prompt_set: PromptSet | None, contract: DeveloperContract | None) -> tuple[Any, list[str]]:
    spy = _SpyPolicy()
    orch = _mock_orchestrator(prompt_set, policy=spy)
    orch.process(ProcessedRequest(prompt="Tell me about the weather", request_id="req-ov-52", developer_contract=contract))
    return orch, spy.systems


def test_developer_system_prompt_reaches_the_wire_verbatim_under_an_overridden_policy_system(tmp_path: Path) -> None:
    ps = load_prompt_set(_write_dir(tmp_path, {"policy.system": {"text": "OVERRIDDEN POLICY BASE {braces} {{x}}"}}))
    contract = DeveloperContract.from_text(_TRICKY_CONTRACT)
    orch, systems = _policy_systems_for(ps, contract)
    base = orch._controller._protected_system_prompt
    assert base.startswith("OVERRIDDEN POLICY BASE {braces} {{x}}")  # the base is MoralStack's, overridable
    expected = _TRICKY_CONTRACT + "\n\n" + base  # ... and the contract is a verbatim prefix, nothing added
    assert expected in systems, "the governed generator never received <contract verbatim>\\n\\n<base>"
    for system in systems:
        if _TRICKY_CONTRACT in system:
            assert system.startswith(_TRICKY_CONTRACT + "\n\n")


def test_override_cannot_reference_contract_text(tmp_path: Path) -> None:
    """No generator-facing fragment can interpolate the developer contract (§5.2).

    FINDING (plan wording was too broad): the plan says "no override field can reference the contract text".
    One packaged template does, by design: ``compliance.dccl.fallback_user`` has a ``{contract}`` placeholder,
    because the DCCL evaluator must read the contract to judge compliance. That is the compliance layer's
    user message, not the governed generator's system prompt (which §5.2 protects). Pinned as an allow-list.
    """
    ds = default_prompt_set()
    contract_names = {"developer_contract", "contract", "contract_text", "raw_text", "system_prompt", "developer_prompt"}
    holders = sorted(
        fid
        for fid in ds.fragment_ids()
        if ds._fragments[fid].kind == "template" and set(ds._fragments[fid].placeholders) & contract_names
    )
    assert holders == ["compliance.dccl.fallback_user"]
    # a text-kind override that spells such a placeholder is sent literally, never substituted
    ps = load_prompt_set(
        _write_dir(tmp_path, {"policy.system": {"text": "BASE {developer_contract} {raw_text}"}}, "literal")
    )
    contract = DeveloperContract.from_text("CONTRACT-BODY-XYZ")
    orch, systems = _policy_systems_for(ps, contract)
    assert orch._controller._protected_system_prompt.startswith("BASE {developer_contract} {raw_text}")
    for system in systems:
        assert system.count("CONTRACT-BODY-XYZ") <= 1
    # and a template override cannot introduce an undeclared contract placeholder
    with pytest.raises(PromptConfigError):
        load_prompt_set(_write_dir(tmp_path, {"risk.intent.user": {"text": "{request} {developer_contract}"}}, "tpl"))


# ---------------------------------------------------------------------------------------
# 5. §5.4 single-turn parity
# ---------------------------------------------------------------------------------------


def test_default_set_single_turn_wire_equals_baseline() -> None:
    """``prompt_set=default_prompt_set()`` and ``prompt_set=None`` put the same bytes on the policy wire.

    The byte baseline itself is the A0 golden snapshot's job; this pins that passing the default set
    explicitly (as every composition root now does) changes nothing.
    """
    token = "§REF_00000000§"
    with patch.object(CanaryProtection, "_generate_canary", lambda self: token):
        _o1, systems_none = _policy_systems_for(None, None)
        _o2, systems_default = _policy_systems_for(default_prompt_set(), None)
    assert systems_none and systems_none == systems_default
    assert systems_none[0].startswith(default_prompt_set().text("policy.system"))


# ---------------------------------------------------------------------------------------
# 6. / 7. code-owned schemas and the signal registry are not fragments
# ---------------------------------------------------------------------------------------

_SCHEMA_LIKE_IDS = (
    "risk.signals.response_format",
    "risk.intent.response_format",
    "risk.operational.response_format",
    "risk.signals.output_json_schema",
    "compliance.safety_override.response_format",
    "compliance.safety_classifier.response_format",
    "compliance.safety_classifier.categories",
)


@pytest.mark.parametrize("fragment_id", _SCHEMA_LIKE_IDS)
def test_response_format_schemas_are_not_overridable_fragment_ids(fragment_id: str, tmp_path: Path) -> None:
    assert not default_prompt_set().has(fragment_id)
    with pytest.raises(PromptConfigError, match="unknown fragment ID"):
        load_prompt_set(_write_dir(tmp_path, {fragment_id: {"text": "{}"}}))


def test_no_fragment_id_names_a_schema_or_response_format() -> None:
    for fid in default_prompt_set().fragment_ids() + default_prompt_set().composition_ids():
        assert "response_format" not in fid and "json_schema" not in fid, fid


def test_code_owned_schemas_reach_the_provider_unchanged_under_garbage_prompts(tmp_path: Path) -> None:
    policy = _SchemaRoutedPolicy(None)
    _estimator(_all_safety_garbage_set(tmp_path), policy).estimate("benign question")
    assert policy.response_formats["risk_intent"] == intent_response_format()
    assert policy.response_formats["risk_operational"] == operational_response_format()
    assert policy.response_formats["harm_signals"] == harm_signal_response_format(signal_registry)

    sent: list[Any] = []

    class _Classifier:
        def generate(self, *, prompt: str, system: str, config: Any, **_kw: Any) -> GenResult:
            sent.append((system, config.response_format))
            return GenResult(text='{"category": "csam", "rationale": "r"}')

    ps = _all_safety_garbage_set(tmp_path)
    assert classify_safety_override("payload", _Classifier(), prompt_set=ps) == "csam"
    assert sent[0][0] == GARBAGE  # the classifier prompt is the overridden one ...
    assert sent[0][1] == safety_override_response_format()  # ... the schema is the code-owned one


def test_signals_registry_is_not_externalized_and_unchanged_by_a_load(tmp_path: Path) -> None:
    def registry_hash() -> str:
        blob = json.dumps(harm_signal_response_format(signal_registry), sort_keys=True)
        keys = json.dumps(sorted((s.key, s.label) for s in signal_registry.signals.values()))
        return hashlib.sha256((blob + keys).encode("utf-8")).hexdigest()

    before = registry_hash()
    _all_safety_garbage_set(tmp_path)
    assert registry_hash() == before
    ds = default_prompt_set()
    assert not any(fid.startswith("signals.") for fid in ds.fragment_ids())
    data_dir = Path(inspect.getfile(PromptSet)).parent / "data"
    assert not (data_dir / "signals.yaml").exists()
    labels = {s.label for s in signal_registry.signals.values()}
    assert not (labels & set(ds.fragment_ids()))


# ---------------------------------------------------------------------------------------
# 8. R8: the output sanitizer is coupled to one phrase of the language prefix
# ---------------------------------------------------------------------------------------


def test_sanitizer_markers_still_match_default_language_prefix() -> None:
    """Tier 3-new (b). ``sanitize_policy_output`` strips lines containing "do not add translations": the
    default ``policy.language_prefix.*`` must keep that phrase, or leaked prefix lines would stop being
    stripped. If you reword the prefix, update ``_policy_helpers.sanitize_policy_output`` and this test.
    """
    from moralstack.orchestration._policy_helpers import force_language_prefix, sanitize_policy_output

    ds = default_prompt_set()
    assert "do not add translations" in ds.template("policy.language_prefix.explicit").lower()
    prefixed = force_language_prefix("What time is it?", "English", prompt_set=ds)
    leaked = "Sure.\n" + next(line for line in prefixed.splitlines() if "do not add translations" in line.lower())
    assert "do not add translations" not in sanitize_policy_output(leaked).lower()


def test_characterization_sanitize_does_not_strip_an_overridden_language_prefix(tmp_path: Path) -> None:
    """R8 (accepted, documented): a reworded prefix lacks the marker phrase, so its leaked lines survive."""
    from moralstack.orchestration._policy_helpers import force_language_prefix, sanitize_policy_output

    reworded = "Answer exclusively in {language}; never append a translation.\n\n{prompt}"
    ps = load_prompt_set(_write_dir(tmp_path, {"policy.language_prefix.explicit": {"text": reworded}}))
    prefixed = force_language_prefix("What time is it?", "English", prompt_set=ps)
    leaked = "Sure.\n" + prefixed.splitlines()[0]
    assert "never append a translation" in sanitize_policy_output(leaked)


# ---------------------------------------------------------------------------------------
# 9. canary: validated by its token, not by the instruction text
# ---------------------------------------------------------------------------------------


def test_canary_is_validated_by_token_under_an_overridden_instruction(tmp_path: Path) -> None:
    ps = load_prompt_set(
        _write_dir(tmp_path, {"policy.canary_instruction": {"text": "\n[Custom marker {canary}, keep secret.]"}})
    )
    canary = CanaryProtection(prompt_set=ps)
    injected = canary.inject("BASE")
    assert injected == f"BASE\n[Custom marker {canary.current_canary}, keep secret.]"
    leaked = f"here is the marker {canary.current_canary} and more"
    clean, cleaned = canary.validate(leaked)
    assert clean is False and canary.current_canary not in cleaned
    assert canary.validate("a harmless answer") == (True, "a harmless answer")


def test_canary_override_without_the_token_logs_a_specific_warning_and_other_overrides_do_not(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="moralstack.prompts.catalog"):
        load_prompt_set(_write_dir(tmp_path, {"policy.canary_instruction": {"text": "\n[No marker.]"}}, "c1"))
    msgs = [r.getMessage() for r in caplog.records]
    assert any("system-prompt leak detection (canary) is DISABLED by this override" in m for m in msgs)
    assert not any("No marker" in m for m in msgs)  # no fragment text in the message
    assert not any("hard-signal reporting may be weakened" in m for m in msgs)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="moralstack.prompts.catalog"):
        load_prompt_set(_write_dir(tmp_path, {"policy.canary_instruction": {"text": "\n[Marker {canary}.]"}}, "c2"))
    assert not any("DISABLED" in r.getMessage() for r in caplog.records)


def test_characterization_canary_instruction_without_the_token_disables_leak_detection(tmp_path: Path) -> None:
    """FINDING (accepted vector, decision 3): an override that omits ``{canary}`` is allowed (WARNING plus
    ``missing_placeholders`` in the audit); the token is then never injected, so ``validate`` has nothing to
    find. Anti-leak protection is weakened, loudly audited, and still never fails closed by itself.
    """
    ps = load_prompt_set(_write_dir(tmp_path, {"policy.canary_instruction": {"text": "\n[No marker here.]"}}))
    canary = CanaryProtection(prompt_set=ps)
    injected = canary.inject("BASE")
    assert canary.current_canary not in injected
    assert canary.validate(f"leak {canary.current_canary}")[0] is False  # validation is by token ...
    assert ps.audit_summary()["fragments"]["policy.canary_instruction"]["missing_placeholders"] == ["canary"]
    assert "policy.canary_instruction" in ps.audit_summary()["safety_fragments_overridden"]


# ---------------------------------------------------------------------------------------
# 10. ledger: no prompt text in the key
# ---------------------------------------------------------------------------------------


class _SpyLedger:
    def __init__(self) -> None:
        self.lookups: list[dict[str, Any]] = []
        self.stores: list[dict[str, Any]] = []

    def lookup(self, **kw: Any) -> LedgerResult:
        self.lookups.append(kw)
        return LedgerResult(
            is_hit=False, cached_decision=None, similarity=0.0, from_turn=0, reason="miss", query_embedding=None
        )

    def store(self, **kw: Any) -> bool:
        self.stores.append(kw)
        return False


def test_ledger_key_has_no_prompt_text_in_it(tmp_path: Path) -> None:
    assert [f.name for f in dataclasses.fields(LedgerKey)] == ["contract_hash", "posture", "domain"]
    for fn in (SemanticDecisionLedger.lookup, SemanticDecisionLedger.store):
        assert "prompt_set" not in inspect.signature(fn).parameters

    keys: list[tuple[Any, ...]] = []
    for ps in (None, _garbage_set(tmp_path, sorted(default_prompt_set().fragment_ids()), name="all_garbage")):
        ledger = _SpyLedger()
        orch = _mock_orchestrator(ps, ledger=ledger)
        orch.process(
            ProcessedRequest(prompt=_DELIBERATIVE_PROMPT, request_id="req-ov-ledger-key"),
            conversation_id="conv-ov-key",
            turn_index=2,
        )
        assert ledger.lookups, "the ledger was not consulted"
        keys.append(tuple((k["contract_hash"], k["posture"], k["domain"]) for k in ledger.lookups))
    assert keys[0] == keys[1]


# ---------------------------------------------------------------------------------------
# 11. every safety fragment overridden with garbage: gates intact, audit complete
# ---------------------------------------------------------------------------------------


def test_all_safety_fragments_garbage_gates_intact_and_audit_lists_every_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from moralstack.prompts import catalog

    safety_ids = sorted(catalog.SAFETY_FRAGMENT_IDS)
    assert safety_ids, "SAFETY_FRAGMENT_IDS is empty"
    with caplog.at_level(logging.WARNING, logger="moralstack.prompts.catalog"):
        ps = _garbage_set(tmp_path, safety_ids, name="all_safety")
    summary = ps.audit_summary()
    assert summary["safety_fragments_overridden"] == safety_ids
    per_fragment_warnings = [r for r in caplog.records if "touches safety-relevant fragment" in r.getMessage()]
    assert len(per_fragment_warnings) == len(safety_ids)
    assert not summary["is_default"]

    # code gates: structured hard signals still refuse / block, with every reporting prompt garbage
    estimation = _estimator(ps, _SchemaRoutedPolicy("Q17:minor_exploitation")).estimate("anything")
    assert has_hard_signal_evidence(estimation)
    assert is_hard_signal_refuse(_decision(REFUSE, path="FAST_PATH"), estimation, OperationalRisk.NONE)

    # DCCL safety-override category check: the code decides what a category is, not the prompt
    class _Classifier:
        def __init__(self, category: str) -> None:
            self.category = category

        def generate(self, *, prompt: str, system: str, config: Any, **_kw: Any) -> GenResult:
            return GenResult(text=json.dumps({"category": self.category, "rationale": "r"}))

    for category in SAFETY_OVERRIDE_CATEGORIES:
        assert classify_safety_override("payload", _Classifier(category), prompt_set=ps) == category
    assert classify_safety_override("payload", _Classifier("not_a_category"), prompt_set=ps) is None
    assert classify_safety_override("payload", _Classifier("null"), prompt_set=ps) is None


def test_default_set_has_no_safety_override_and_a_quiet_audit() -> None:
    summary = default_prompt_set().audit_summary()
    assert summary["safety_fragments_overridden"] == [] and summary["is_default"]
