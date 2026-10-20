"""A2d: the DCCL and the safety-override classifier read their prompt texts from the injected PromptSet.

Marker sets (``tests.prompt_markers``) append a unique marker to the fragments, so each assertion proves which
fragment reached the wire (or the persisted ``system_prompt``). With ``prompt_set=None`` the default set is used
and the wire is the pre-externalization text (also pinned by ``tests/prompts_golden``).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from moralstack.compliance import dccl as dccl_module
from moralstack.compliance import safety_override as so_module
from moralstack.compliance.dccl import DeveloperContractComplianceLayer
from moralstack.compliance.safety_override import classify_safety_override
from moralstack.compliance.types import ActionType, ComplianceDecision, StructuredRule, TriggerType
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.catalog import default_prompt_set, load_prompt_set
from tests.prompt_markers import make_marker_dir, marker_for

_CLASSIFIER_ID = "compliance.safety_classifier.system"
_SYSTEM_ID = "compliance.dccl.system"
_TRAILER_ID = "compliance.dccl.native_trailer"
_FALLBACK_ID = "compliance.dccl.fallback_user"

_MATCH_JSON = (
    '{"verdict": "MATCH", "rule_excerpt": "if X reply Y", "action_excerpt": "Y", '
    '"safety_override_reason": null, "rationale": "r", "confidence": 0.95, '
    '"draft_matches_action": true, "draft_match_confidence": 0.9}'
)


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


class _GeneratePolicy:
    """``generate`` only: the DCCL takes the fallback single-prompt branch."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, system: str = "", config: Any = None, **kwargs: Any) -> _Result:
        self.calls.append({"prompt": prompt, "system": system})
        if "safety classifier for the MoralStack DCCL" in system:
            return _Result('{"category": null, "rationale": "t"}')
        return _Result(_MATCH_JSON)


class _MessagesPolicy(_GeneratePolicy):
    """Has ``generate_messages``: the DCCL takes the native-messages branch (classifier still uses ``generate``)."""

    def __init__(self) -> None:
        super().__init__()
        self.messages_calls: list[list[dict[str, str]]] = []

    def generate_messages(self, messages: list[dict[str, str]], config: Any = None, **kwargs: Any) -> _Result:
        self.messages_calls.append(messages)
        return _Result(_MATCH_JSON)


class _Request:
    def __init__(self, prompt: str, contract: Any) -> None:
        self.prompt = prompt
        self.developer_contract = contract


@pytest.fixture
def marker_set(tmp_path):
    return load_prompt_set(make_marker_dir(tmp_path))


@pytest.fixture
def llm_path(monkeypatch):
    monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "llm")


@pytest.fixture
def recorded(monkeypatch):
    rows: list[dict[str, Any]] = []

    def _fake_record(logger, diagnostics_payload, persist_kwargs):
        if persist_kwargs is not None:
            rows.append(persist_kwargs)

    monkeypatch.setattr("moralstack.orchestration.persistence_helpers.record_llm_call", _fake_record)
    return rows


def _request() -> _Request:
    return _Request("X", DeveloperContract.from_text("if user says X, reply Y"))


# -- wire: marker reaches the system prompt, the trailer / fallback user and the classifier ----------


def test_dccl_fallback_wire_carries_markers(marker_set, llm_path) -> None:
    policy = _GeneratePolicy()
    layer = DeveloperContractComplianceLayer(policy=policy, prompt_set=marker_set)
    verdict = layer.evaluate(_request(), speculative_draft="Y")

    assert verdict.decision == ComplianceDecision.MATCH
    dccl_call = next(c for c in policy.calls if "safety classifier" not in c["system"])
    assert marker_for(_SYSTEM_ID) in dccl_call["system"]
    assert marker_for(_FALLBACK_ID) in dccl_call["prompt"]
    classifier_call = next(c for c in policy.calls if "safety classifier" in c["system"])
    assert marker_for(_CLASSIFIER_ID) in classifier_call["system"]


def test_dccl_native_messages_wire_carries_markers(marker_set, llm_path) -> None:
    policy = _MessagesPolicy()
    layer = DeveloperContractComplianceLayer(policy=policy, prompt_set=marker_set)
    layer.evaluate(_request(), speculative_draft="Y")

    (messages,) = policy.messages_calls
    assert messages[0]["role"] == "system"
    assert marker_for(_SYSTEM_ID) in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert marker_for(_TRAILER_ID) in messages[-1]["content"]
    # the classifier of the MATCH verdict is reached with the same set
    assert [c for c in policy.calls if marker_for(_CLASSIFIER_ID) in c["system"]]


def test_no_draft_text_comes_from_the_set(tmp_path) -> None:
    ps_dir = make_marker_dir(tmp_path, ids=["compliance.dccl.no_draft"], marker="NODRAFT")
    layer = DeveloperContractComplianceLayer(policy=None, prompt_set=load_prompt_set(ps_dir))
    messages = layer._build_llm_messages("contract", "final request", "")
    assert "NODRAFT" in messages[-1]["content"]
    assert "NODRAFT" in layer._build_llm_user_prompt("contract", "final request", "")


class _Transcript:
    def __init__(self, text: str, truncated: bool) -> None:
        self._result = (text, truncated)

    def role_serialized_transcript(self, budget: int) -> tuple[str, bool]:
        return self._result


def test_transcript_texts_come_from_the_set(marker_set) -> None:
    layer = DeveloperContractComplianceLayer(policy=None, prompt_set=marker_set)
    no_context = layer._build_llm_user_prompt("contract", "final request", "draft")
    truncated = layer._build_llm_user_prompt("contract", "final request", "draft", _Transcript("USER: hi", True))
    complete = layer._build_llm_user_prompt("contract", "final request", "draft", _Transcript("USER: hi", False))

    assert marker_for("compliance.dccl.transcript_fallback") in no_context
    assert marker_for("compliance.dccl.transcript_complete_note") in no_context
    assert marker_for("compliance.dccl.transcript_truncated_note") in truncated
    assert marker_for("compliance.dccl.transcript_complete_note") in complete
    assert marker_for("compliance.dccl.transcript_fallback") not in complete


# -- persisted system_prompt is the rendered string ------------------------------------------------


def test_persisted_system_prompts_equal_the_rendered_set_text(marker_set, llm_path, recorded) -> None:
    policy = _MessagesPolicy()
    layer = DeveloperContractComplianceLayer(policy=policy, prompt_set=marker_set)
    layer.evaluate(_request(), speculative_draft="Y")

    by_action = {row["action"]: row for row in recorded}
    assert by_action["evaluate"]["system_prompt"] == marker_set.text(_SYSTEM_ID)
    assert by_action["evaluate"]["system_prompt"] != default_prompt_set().text(_SYSTEM_ID)
    assert by_action["safety_override"]["system_prompt"] == marker_set.text(_CLASSIFIER_ID)
    # the persisted system prompt is what the model received
    assert by_action["evaluate"]["system_prompt"] == policy.messages_calls[0][0]["content"]


# -- _classify_safety_override forwards the instance set ----------------------------------------------


def test_classify_safety_override_forwards_the_instance_set(marker_set, monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    def _fake(action_payload: str, **kwargs: Any) -> None:
        seen.append(kwargs)
        return None

    monkeypatch.setattr(dccl_module, "classify_safety_override", _fake)
    layer = DeveloperContractComplianceLayer(policy=_GeneratePolicy(), prompt_set=marker_set)
    assert layer._classify_safety_override("payload") is None
    assert len(seen) == 1
    assert seen[0]["prompt_set"] is marker_set


def test_validate_contract_forwards_the_instance_set(marker_set, monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    def _fake(action_payload: str, **kwargs: Any) -> None:
        seen.append(kwargs)
        return None

    monkeypatch.setattr(dccl_module, "classify_safety_override", _fake)
    rule = StructuredRule(
        rule_id="r1",
        trigger_pattern="PING",
        trigger_type=TriggerType.LITERAL,
        action_type=ActionType.EMIT,
        action_payload="PONG",
        description="d",
    )
    contract = replace(DeveloperContract.from_text("t"), structured_rules=(rule,))
    layer = DeveloperContractComplianceLayer(policy=_GeneratePolicy(), prompt_set=marker_set)
    accepted, rejected = layer.validate_contract(contract)
    assert [r.rule_id for r in accepted] == ["r1"] and rejected == []
    assert len(seen) == 1 and seen[0]["prompt_set"] is marker_set


def test_structured_path_classifier_uses_the_set(marker_set, monkeypatch) -> None:
    monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
    policy = _GeneratePolicy()
    rule = StructuredRule(
        rule_id="r1",
        trigger_pattern="PING",
        trigger_type=TriggerType.LITERAL,
        action_type=ActionType.EMIT,
        action_payload="PONG",
        description="d",
    )
    contract = replace(DeveloperContract.from_text("t"), structured_rules=(rule,))
    layer = DeveloperContractComplianceLayer(policy=policy, prompt_set=marker_set)
    layer.evaluate(_Request("PING", contract), speculative_draft="PONG")
    assert [c for c in policy.calls if marker_for(_CLASSIFIER_ID) in c["system"]]


# -- the classifier on its own -----------------------------------------------------------------------


def test_classify_safety_override_direct_api_uses_the_given_set(marker_set) -> None:
    policy = _GeneratePolicy()
    assert classify_safety_override("some payload", policy=policy, use_llm=True, prompt_set=marker_set) is None
    assert policy.calls[0]["system"] == marker_set.text(_CLASSIFIER_ID)
    assert marker_for(_CLASSIFIER_ID) in policy.calls[0]["system"]


def test_classifier_callback_receives_the_rendered_system_prompt(marker_set) -> None:
    got: list[str] = []
    classify_safety_override(
        "some payload",
        policy=_GeneratePolicy(),
        use_llm=True,
        on_llm_result=lambda result, system_prompt, user_prompt: got.append(system_prompt),
        prompt_set=marker_set,
    )
    assert got == [marker_set.text(_CLASSIFIER_ID)]


# -- prompt_set=None keeps today's text ----------------------------------------------------------------


def test_prompt_set_none_uses_the_default_set_and_the_legacy_text(llm_path) -> None:
    policy = _MessagesPolicy()
    layer = DeveloperContractComplianceLayer(policy=policy)
    assert layer._prompt_set is default_prompt_set()
    layer.evaluate(_request(), speculative_draft="Y")

    assert policy.messages_calls[0][0]["content"] == dccl_module._DCCL_LLM_SYSTEM_PROMPT
    assert policy.messages_calls[0][-1]["content"].startswith("Evaluate whether the preceding final user request")
    classifier_call = next(c for c in policy.calls if "safety classifier" in c["system"])
    assert classifier_call["system"] == so_module._LLM_CLASSIFIER_SYSTEM_PROMPT


def test_module_constants_are_default_reexports() -> None:
    ds = default_prompt_set()
    assert dccl_module._DCCL_LLM_SYSTEM_PROMPT == ds.text(_SYSTEM_ID)
    assert dccl_module.DCCL_DRAFT_MATCH_SYSTEM_PROMPT == ds.text("compliance.draft_match.system")
    assert so_module._LLM_CLASSIFIER_SYSTEM_PROMPT == ds.text(_CLASSIFIER_ID)
