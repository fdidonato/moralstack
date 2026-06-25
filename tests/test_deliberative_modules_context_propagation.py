from unittest.mock import MagicMock

from moralstack.core.types import Turn
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.conversation_context import build_conversation_context
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import DeliberationState, OrchestratorConfig, ProcessedRequest


def test_critic_critique_receives_context(monkeypatch):
    # Mock global variables and functions that might cause failures
    persist_mock = MagicMock()
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.persist_orchestration_event", persist_mock)
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", MagicMock())

    config = OrchestratorConfig()

    # Manual deps bag (not a full DeliberationDependencies instance).
    class MockDeps:
        pass

    deps = MockDeps()
    deps.policy = MagicMock()
    deps.critic = MagicMock()
    # MagicMock auto-attributes are truthy; force the plain critique() branch.
    deps.critic.critique_with_relevant_principles = None
    deps.simulator = MagicMock()
    deps.hindsight = MagicMock()
    deps.perspectives = MagicMock()
    deps.constitution_store = MagicMock()
    deps.output_protector = MagicMock()

    mock_critique = MagicMock()
    mock_critique.violations = []
    mock_critique.revision_guidance = ""
    deps.critic.critique.return_value = mock_critique

    runner = DeliberationRunner(config, deps, "protected", None, MagicMock())

    contract = DeveloperContract.from_text("SECRET CONTRACT", mode="opaque")
    history = [Turn(role="user", content="ping"), Turn(role="assistant", content="pong")]
    conversation_context = build_conversation_context(
        [
            {"role": "system", "content": "SECRET CONTRACT"},
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
            {"role": "user", "content": "test prompt"},
        ]
    )
    request = ProcessedRequest(
        prompt="test prompt",
        developer_contract=contract,
        conversation_history=history,
        conversation_context=conversation_context,
        request_id="test-id",
    )
    state = DeliberationState(draft_response="draft")

    runner._critique(state, request, constitution=MagicMock())

    deps.critic.critique.assert_called()
    _, kwargs = deps.critic.critique.call_args
    assert kwargs["developer_contract"] == contract
    assert kwargs["conversation_history"] == history
    assert kwargs["request_id"] == "test-id"
    persist_mock.assert_called()
    context_event = persist_mock.call_args.kwargs
    assert context_event["event_type"] == "CONTEXT_SHAPE_RECORDED"
    assert context_event["component"] == "critic"
    assert context_event["payload"]["context_mode"] == "role_serialized_full"
    assert context_event["payload"]["developer_contract_included"] is True


def test_simulator_simulate_receives_context(monkeypatch):
    persist_mock = MagicMock()
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.persist_orchestration_event", persist_mock)
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", MagicMock())

    config = OrchestratorConfig()

    class MockDeps:
        pass

    deps = MockDeps()
    deps.simulator = MagicMock()
    deps.policy = MagicMock()
    deps.critic = MagicMock()
    deps.hindsight = MagicMock()
    deps.perspectives = MagicMock()
    deps.constitution_store = MagicMock()
    deps.output_protector = MagicMock()

    mock_sim = MagicMock()
    mock_sim.consequences = []
    mock_sim.expected_valence = 0.0
    mock_sim.semantic_expected_harm = 0.0
    mock_sim.dominant_harm_types = []
    mock_sim.worst_harm = ""
    deps.simulator.simulate.return_value = mock_sim

    runner = DeliberationRunner(config, deps, "protected", None, MagicMock())

    contract = DeveloperContract.from_text("SECRET CONTRACT", mode="opaque")
    history = [Turn(role="user", content="ping"), Turn(role="assistant", content="pong")]
    request = ProcessedRequest(
        prompt="test prompt",
        developer_contract=contract,
        conversation_history=history,
        conversation_context=build_conversation_context(
            [
                {"role": "developer", "content": "SECRET CONTRACT"},
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
                {"role": "user", "content": "test prompt"},
            ]
        ),
    )
    state = DeliberationState(draft_response="draft")

    runner._simulate(state, request)

    deps.simulator.simulate.assert_called()
    _, kwargs = deps.simulator.simulate.call_args
    assert kwargs["developer_contract"] == contract
    assert kwargs["conversation_history"] == history
    assert persist_mock.call_args.kwargs["event_type"] == "CONTEXT_SHAPE_RECORDED"
    assert persist_mock.call_args.kwargs["component"] == "simulator"


def test_hindsight_evaluate_receives_context(monkeypatch):
    persist_mock = MagicMock()
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.persist_orchestration_event", persist_mock)
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", MagicMock())

    config = OrchestratorConfig()

    class MockDeps:
        pass

    deps = MockDeps()
    deps.hindsight = MagicMock()
    deps.policy = MagicMock()
    deps.critic = MagicMock()
    deps.simulator = MagicMock()
    deps.perspectives = MagicMock()
    deps.constitution_store = MagicMock()
    deps.output_protector = MagicMock()
    deps.hindsight.evaluate.return_value = MagicMock()

    runner = DeliberationRunner(config, deps, "protected", None, MagicMock())

    contract = DeveloperContract.from_text("SECRET CONTRACT", mode="opaque")
    history = [Turn(role="user", content="ping"), Turn(role="assistant", content="pong")]
    request = ProcessedRequest(
        prompt="test prompt",
        developer_contract=contract,
        conversation_history=history,
        conversation_context=build_conversation_context(
            [
                {"role": "developer", "content": "SECRET CONTRACT"},
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
                {"role": "user", "content": "test prompt"},
            ]
        ),
    )
    state = DeliberationState(draft_response="draft")

    runner._evaluate_hindsight(state, request)

    deps.hindsight.evaluate.assert_called()
    _, kwargs = deps.hindsight.evaluate.call_args
    assert kwargs["developer_contract"] == contract
    assert kwargs["conversation_history"] == history
    assert persist_mock.call_args.kwargs["event_type"] == "CONTEXT_SHAPE_RECORDED"
    assert persist_mock.call_args.kwargs["component"] == "hindsight"


def test_perspectives_evaluate_receives_context(monkeypatch):
    persist_mock = MagicMock()
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.persist_orchestration_event", persist_mock)
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", MagicMock())

    config = OrchestratorConfig()

    class MockDeps:
        pass

    deps = MockDeps()
    deps.perspectives = MagicMock()
    deps.policy = MagicMock()
    deps.critic = MagicMock()
    deps.simulator = MagicMock()
    deps.hindsight = MagicMock()
    deps.constitution_store = MagicMock()
    deps.output_protector = MagicMock()
    mock_res = MagicMock()
    mock_res.results = []
    deps.perspectives.evaluate.return_value = mock_res

    runner = DeliberationRunner(config, deps, "protected", None, MagicMock())

    contract = DeveloperContract.from_text("SECRET CONTRACT", mode="opaque")
    history = [Turn(role="user", content="ping"), Turn(role="assistant", content="pong")]
    request = ProcessedRequest(
        prompt="test prompt",
        developer_contract=contract,
        conversation_history=history,
        conversation_context=build_conversation_context(
            [
                {"role": "developer", "content": "SECRET CONTRACT"},
                {"role": "user", "content": "ping"},
                {"role": "assistant", "content": "pong"},
                {"role": "user", "content": "test prompt"},
            ]
        ),
    )
    state = DeliberationState(draft_response="draft")

    runner._evaluate_perspectives(state, request)

    deps.perspectives.evaluate.assert_called()
    _, kwargs = deps.perspectives.evaluate.call_args
    assert kwargs["developer_contract"] == contract
    assert kwargs["conversation_history"] == history
    assert persist_mock.call_args.kwargs["event_type"] == "CONTEXT_SHAPE_RECORDED"
    assert persist_mock.call_args.kwargs["component"] == "perspectives"


def test_propagation_none_when_no_context(monkeypatch):
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.persist_orchestration_event", MagicMock())
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", MagicMock())

    config = OrchestratorConfig()

    class MockDeps:
        pass

    deps = MockDeps()
    deps.critic = MagicMock()
    deps.critic.critique_with_relevant_principles = None
    deps.policy = MagicMock()
    deps.simulator = MagicMock()
    deps.hindsight = MagicMock()
    deps.perspectives = MagicMock()
    deps.constitution_store = MagicMock()
    deps.output_protector = MagicMock()
    mock_critique = MagicMock()
    mock_critique.violations = []
    mock_critique.revision_guidance = ""
    deps.critic.critique.return_value = mock_critique

    runner = DeliberationRunner(config, deps, "protected", None, MagicMock())

    request = ProcessedRequest(prompt="test prompt", developer_contract=None, conversation_history=[])
    state = DeliberationState(draft_response="draft")

    runner._critique(state, request, constitution=MagicMock())

    deps.critic.critique.assert_called()
    _, kwargs = deps.critic.critique.call_args
    assert kwargs["developer_contract"] is None
    assert kwargs["conversation_history"] == []
