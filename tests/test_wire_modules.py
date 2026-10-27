"""A4b-1..3: the modules attach the exact wire of their calls to their results (Tier 1, offline).

Every record is checked against what the recording fake policy actually received, for the native and the legacy branch,
the first attempt and the retry, with and without context. Building a record is telemetry: a failure must never reach the
module call (invariant 6), and a cache hit (no call) must carry no wire.
"""

from __future__ import annotations

import json
from collections import Counter
from types import SimpleNamespace
from unittest import mock

import pytest

from moralstack.observability.wire import decode_wire
from tests.prompts_golden import capture as cap
from tests.test_persistence_fidelity import PROMPT, _decode_row_wire, _make_policy, _sent_messages

CONTEXTS = ("nocontext", "contract_history")
BRANCHES = (True, False)  # native generate_messages / legacy generate
ATTEMPTS = (False, True)  # first attempt / parse retry
ANSWER = "Use a long passphrase and a password manager."


def _context(name: str):
    contract, history = cap._context_cases()[name]
    return {"developer_contract": contract, "conversation_history": history}


def _decode(wire, row_prompt: str, row_system: str):
    return decode_wire(wire, prompt_col=row_prompt, system_prompt_col=row_system)


def _canonical(messages: list[dict[str, str]]) -> str:
    return json.dumps(messages, sort_keys=True, ensure_ascii=False)


def _last_ok_call(policy, label_prefix: str):
    calls = [c for c in policy.snapshot() if c.label.startswith(label_prefix)]
    return _sent_messages(calls[-1])


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("retry", ATTEMPTS)
@pytest.mark.parametrize("native", BRANCHES)
def test_critic_report_wire_is_what_the_policy_received(native, retry, context):
    from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic

    policy = _make_policy(native=native, retry=retry)
    critic = LLMConstitutionalCritic(policy, store=None, config=CriticConfig(max_retries=2))
    principles = cap._principles()
    report = critic.critique(
        request=PROMPT,
        response=ANSWER,
        constitution=SimpleNamespace(principles=principles),  # type: ignore[arg-type]
        principles=principles,
        request_id="req-wire",
        delib_context=cap._delib_context(),
        **_context(context),
    )
    assert report.wire is not None
    assert report.wire["branch"] == ("messages" if native else "generate")
    assert _decode(report.wire, report.prompt, report.system_prompt) == _last_ok_call(policy, "critic.full")


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("retry", ATTEMPTS)
@pytest.mark.parametrize("native", BRANCHES)
def test_simulator_batch_wire_is_what_the_policy_received(native, retry, context):
    from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator, SimulatorConfig

    policy = _make_policy(native=native, retry=retry)
    sim = LLMConsequenceSimulator(policy, SimulatorConfig(max_retries=2, enable_caching=False))
    result = sim.simulate(PROMPT, ANSWER, num_scenarios=3, delib_context=cap._delib_context(), **_context(context))
    assert result.wire is not None and result.wire_calls is None
    assert _decode(result.wire, result.prompt, result.system_prompt) == _last_ok_call(policy, "simulator.batch")


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("retry", ATTEMPTS)
@pytest.mark.parametrize("native", BRANCHES)
def test_hindsight_wires_are_what_the_policy_received(native, retry, context):
    from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator

    policy = _make_policy(native=native, retry=retry)
    evaluator = LLMHindsightEvaluator(policy, HindsightConfig(max_retries=2, enable_caching=False))
    batch = evaluator.evaluate(PROMPT, ANSWER, cap._consequences(), delib_context=cap._delib_context(), **_context(context))
    assert batch.wire is not None
    assert _decode(batch.wire, batch.prompt, batch.system_prompt) == _last_ok_call(policy, "hindsight.batch")

    policy = _make_policy(native=native, retry=retry)
    evaluator = LLMHindsightEvaluator(policy, HindsightConfig(max_retries=2, enable_caching=False))
    single = evaluator.evaluate(PROMPT, ANSWER, cap._consequences()[:1], delib_context=cap._delib_context())
    assert single.wire is not None
    assert single.wire["branch"] == "generate"  # the single path is generate() only
    assert _decode(single.wire, single.prompt, single.system_prompt) == _last_ok_call(policy, "hindsight.single")


@pytest.mark.parametrize("parallel", (False, True))
@pytest.mark.parametrize("retry", ATTEMPTS)
@pytest.mark.parametrize("native", BRANCHES)
def test_perspectives_wire_calls_cover_every_result_of_the_row(native, retry, parallel):
    from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble

    policy = _make_policy(native=native, retry=retry)
    ensemble = LLMPerspectiveEnsemble(
        policy, EnsembleConfig(max_retries=2, parallel_evaluation=parallel, enable_caching=False, max_perspectives=2)
    )
    result = ensemble.evaluate(PROMPT, ANSWER, delib_context=cap._delib_context(), **_context("contract_history"))
    assert result.wire_calls is not None
    decoded = [
        decode_wire(r, prompt_col="", system_prompt_col="", strings=result.wire_calls["wire_strings"])
        for r in result.wire_calls["wire_calls"]
    ]
    assert len(decoded) == len(result.prompts) == 2
    # order is the order of the row's joined prompts; each call's user message ends with that result's prompt
    for messages, prompt in zip(decoded, result.prompts, strict=True):
        assert messages[-1]["content"].endswith(prompt)
    # the shared system prompt is stored once
    assert result.wire_calls["wire_strings"].count(result.system_prompts[0]) == 1
    # the records are the messages the policy actually received: all of them, or (parse retry) all but the failed ones
    received = Counter(_canonical(_sent_messages(c)) for c in policy.snapshot() if c.label.startswith("perspectives"))
    recorded = Counter(_canonical(messages) for messages in decoded)
    assert recorded - received == Counter()
    if not retry:
        assert recorded == received
    # captured messages are consumed by the row: the results (state.perspectives, the LRU cache) do not keep them
    assert all("_sent_wire" not in vars(r) for r in result.results)


@pytest.mark.parametrize("native", BRANCHES)
def test_estimator_mini_calls_carry_their_wire(native):
    from moralstack.models.risk.estimator import LLMBasedRiskEstimator
    from moralstack.models.risk.schema import RiskEstimatorConfig

    policy = _make_policy(native=native, retry=False)
    est = LLMBasedRiskEstimator(
        policy=policy,
        config=RiskEstimatorConfig(
            max_retries=2, intent_model="test-model", signals_model="test-model", operational_model="test-model"
        ),
        constitution_store=cap.FakeRetrievalStore(cap._principles()),
    )
    captured: list[dict] = []
    contract, history = cap._context_cases()["contract_history"]
    with mock.patch.object(
        LLMBasedRiskEstimator, "_persist_mini_llm_calls_batch", lambda self, calls: captured.extend(calls)
    ):
        est.estimate(
            PROMPT,
            developer_contract_text=contract.raw_text,
            conversation_history=cap._history_dicts(),
        )
    assert {c["action"] for c in captured} == {"estimate_intent", "estimate_signals", "estimate_operational"}
    label = {
        "estimate_intent": "risk.intent",
        "estimate_signals": "risk.signals",
        "estimate_operational": "risk.operational",
    }
    for call in captured:
        assert call["wire"]["branch"] == ("messages" if native else "generate")
        assert _decode(call["wire"], call["prompt"], call["system_prompt"]) == _last_ok_call(policy, label[call["action"]])


def test_a_failing_encoder_never_reaches_the_module_call():
    from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator, SimulatorConfig

    policy = _make_policy(native=True)
    sim = LLMConsequenceSimulator(policy, SimulatorConfig(max_retries=2, enable_caching=False))
    with mock.patch("moralstack.observability.wire.encode_wire", side_effect=RuntimeError("boom")):
        result = sim.simulate(PROMPT, ANSWER, num_scenarios=3, delib_context=cap._delib_context())
    assert result.consequences  # the call and its result are unaffected
    assert result.wire is None


def _simulate_twice():
    from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator, SimulatorConfig

    sim = LLMConsequenceSimulator(_make_policy(native=True), SimulatorConfig(max_retries=2, enable_caching=True))
    request = "wire cache probe request"
    return [sim.simulate(request, ANSWER, num_scenarios=3, delib_context=cap._delib_context()) for _ in range(2)]


def _hindsight_twice():
    from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator

    evaluator = LLMHindsightEvaluator(_make_policy(native=True), HindsightConfig(max_retries=2, enable_caching=True))
    request = "wire cache probe request"
    return [evaluator.evaluate(request, ANSWER, cap._consequences(), delib_context=cap._delib_context()) for _ in range(2)]


def _perspectives_twice():
    from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble

    ensemble = LLMPerspectiveEnsemble(
        _make_policy(native=True), EnsembleConfig(max_retries=2, enable_caching=True, max_perspectives=2)
    )
    request = "wire cache probe request"
    return [ensemble.evaluate(request, ANSWER, delib_context=cap._delib_context()) for _ in range(2)]


@pytest.mark.parametrize("twice", (_simulate_twice, _hindsight_twice, _perspectives_twice))
def test_a_cache_hit_carries_no_wire(twice):
    first, second = twice()
    assert not first.from_cache
    assert getattr(first, "wire", None) is not None or first.wire_calls is not None
    assert second.from_cache and getattr(second, "wire", None) is None and second.wire_calls is None


def test_hindsight_individual_path_with_several_consequences_records_every_call():
    from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator

    policy = _make_policy(native=True)
    evaluator = LLMHindsightEvaluator(
        policy, HindsightConfig(max_retries=2, enable_caching=False, use_batch_evaluation=False)
    )
    consequences = cap._consequences()
    assert len(consequences) > 1
    result = evaluator.evaluate(PROMPT, ANSWER, consequences, delib_context=cap._delib_context())
    assert result.wire is None and result.wire_calls is not None
    records = result.wire_calls["wire_calls"]
    assert len(records) == len(consequences)
    decoded = [
        decode_wire(r, prompt_col="", system_prompt_col=result.system_prompt, strings=result.wire_calls["wire_strings"])
        for r in records
    ]
    received = [_sent_messages(c) for c in policy.snapshot() if c.label.startswith("hindsight.single")]
    assert decoded == received


def test_estimator_retry_failed_row_inlines_the_failed_attempt_wire():
    from moralstack.models.risk.estimator import LLMBasedRiskEstimator
    from moralstack.models.risk.schema import RiskEstimatorConfig

    policy = _make_policy(native=True, retry=True)
    est = LLMBasedRiskEstimator(
        policy=policy,
        config=RiskEstimatorConfig(
            max_retries=2, intent_model="test-model", signals_model="test-model", operational_model="test-model"
        ),
        constitution_store=cap.FakeRetrievalStore(cap._principles()),
    )
    persisted: list[dict] = []
    contract, _ = cap._context_cases()["contract_history"]
    with (
        mock.patch.object(LLMBasedRiskEstimator, "_persist_mini_llm_calls_batch", lambda self, calls: None),
        mock.patch(
            "moralstack.observability.emit_helpers.async_persist_llm_call", side_effect=lambda **kw: persisted.append(kw)
        ),
    ):
        est.estimate(PROMPT, developer_contract_text=contract.raw_text, conversation_history=cap._history_dicts())
    failed = [kw for kw in persisted if kw.get("call_outcome") == "retry_failed"]
    assert failed, "the unparseable first attempts must produce retry-failed rows"
    received = Counter(_canonical(_sent_messages(c)) for c in policy.snapshot() if c.label.startswith("risk."))
    for kw in failed:
        summary = json.loads(kw["parsed_summary_json"])
        (messages,) = _decode_row_wire(summary, kw["prompt"], "")
        assert _canonical(messages) in received
