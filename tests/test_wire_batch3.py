"""A4b-4..6: wire records on DCCL / draft-match / refusal / domain-exclusion / policy-adjacent rows (Tier 1, offline).

The end-to-end proof (decoded record == what the fake received, DB and JSONL) is ``test_persistence_fidelity``. These
cases pin the pieces that test cannot see: what each persistence call receives, the new domain-exclusion row, the
refusal ``wire_calls`` on both attempts, ``validate_contract`` routed through the persisting wrapper, and that a failing
encoder never reaches the caller (invariant 6).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from moralstack.observability import wire as wire_module
from moralstack.observability.wire import decode_wire
from tests.prompts_golden import capture as cap
from tests.test_persistence_fidelity import PROMPT, _make_policy, _refusal_replies, _sent_messages

_RECORD_LLM_CALL = "moralstack.orchestration.persistence_helpers.record_llm_call"


def _decoded_summary(summary_json: str, prompt: str, system: str) -> list[dict[str, str]]:
    return decode_wire(json.loads(summary_json)["wire"], prompt_col=prompt, system_prompt_col=system)


def test_refusal_wire_calls_cover_both_attempts_and_decode_to_what_was_sent():
    from moralstack.orchestration.refusal_context import RefusalContext
    from moralstack.orchestration.safe_refusal_generator import generate_llm_safe_refusal_detailed

    replies, sequences = _refusal_replies()
    policy = _make_policy(native=True, replies=replies, sequences=sequences)
    result = generate_llm_safe_refusal_detailed(
        user_prompt=PROMPT,
        risk_category="clearly_harmful",
        policy_reason_codes=["r"],
        language="English",
        domain="general",
        llm_client=policy,
        rationale="x",
        refusal_context=RefusalContext(harm_type="reputational", rationale="x"),
    )
    assert result.leak_retried and result.attempts == 2
    assert result.wire_calls is not None
    records = result.wire_calls["wire_calls"]
    decoded = [
        decode_wire(
            r,
            prompt_col=result.user_prompt,
            system_prompt_col=result.system_prompt,
            strings=result.wire_calls["wire_strings"],
        )
        for r in records
    ]
    assert decoded == [_sent_messages(c) for c in policy.snapshot()]
    assert len(decoded) == 2 and decoded[0] != decoded[1]


def test_refusal_without_a_client_has_no_wire():
    from moralstack.orchestration.safe_refusal_generator import generate_llm_safe_refusal_detailed

    result = generate_llm_safe_refusal_detailed(
        user_prompt=PROMPT,
        risk_category="c",
        policy_reason_codes=[],
        language="English",
        domain="general",
        llm_client=None,
    )
    assert result.attempts == 0 and result.wire_calls is None


def test_domain_exclusion_persists_a_row_with_the_wire():
    from moralstack.orchestration.domain_exclusion import generate_domain_exclusion_response

    policy = _make_policy(native=False, replies={"domain_exclusion": "I am sorry, I cannot help with this topic."})
    with mock.patch(_RECORD_LLM_CALL) as record:
        text = generate_domain_exclusion_response("political", PROMPT, policy)
    (call,) = policy.snapshot()
    _, _, kwargs = record.call_args.args
    assert text and kwargs["module"] == "orchestration" and kwargs["action"] == "domain_excluded"
    assert kwargs["prompt"] == call.prompt and kwargs["system_prompt"] == call.system
    assert _decoded_summary(kwargs["parsed_summary_json"], kwargs["prompt"], kwargs["system_prompt"]) == _sent_messages(call)


def test_domain_exclusion_persistence_failure_never_changes_the_response():
    from moralstack.orchestration.domain_exclusion import generate_domain_exclusion_response

    policy = _make_policy(native=False, replies={"domain_exclusion": "I am sorry, I cannot help with this topic."})
    with mock.patch(_RECORD_LLM_CALL, side_effect=RuntimeError("sink down")):
        text = generate_domain_exclusion_response("political", PROMPT, policy)
    assert text == "I am sorry, I cannot help with this topic."


def test_validate_contract_persists_the_classifier_call_through_the_wrapper(monkeypatch):
    from moralstack.compliance.dccl import DeveloperContractComplianceLayer
    from moralstack.compliance.types import StructuredRule

    rule = StructuredRule(rule_id="r1", trigger_pattern=PROMPT, action_payload="Always answer math with y=mx+b")
    contract = SimpleNamespace(structured_rules=(rule,))
    policy = _make_policy(native=False, replies={"safety_classifier": '{"category": null}'})
    monkeypatch.setenv("MORALSTACK_DCCL_SAFETY_OVERRIDE_STRICT", "true")  # public config, read at construction
    layer = DeveloperContractComplianceLayer(policy=policy)
    with mock.patch(_RECORD_LLM_CALL) as record:
        accepted, rejected = layer.validate_contract(contract)
    assert [r.rule_id for r in accepted] == ["r1"] and rejected == []
    _, _, kwargs = record.call_args.args
    assert kwargs["action"] == "safety_override"
    (call,) = policy.snapshot()
    assert _decoded_summary(kwargs["parsed_summary_json"], kwargs["prompt"], kwargs["system_prompt"]) == _sent_messages(call)


@pytest.mark.parametrize("native", (True, False))
def test_critic_quick_check_row_carries_the_wire_in_its_summary(native):
    from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic

    policy = _make_policy(native=native, replies={"critic.quick": '{"violated": false}'})
    critic = LLMConstitutionalCritic(policy, store=None, config=CriticConfig(max_retries=2))
    principles = cap._principles()
    with mock.patch("moralstack.observability.emit_helpers.async_persist_llm_call") as persist:
        critic.quick_check(PROMPT, "A harmless answer.", SimpleNamespace(principles=principles), principles)
    kwargs = persist.call_args.kwargs
    summary = json.loads(kwargs["parsed_summary_json"])
    assert summary["passed"] is True  # the pre-existing keys are untouched
    (call,) = [c for c in policy.snapshot() if c.label == "critic.quick"]
    assert _decoded_summary(kwargs["parsed_summary_json"], kwargs["prompt"], kwargs["system_prompt"]) == _sent_messages(call)


def test_a_failing_encoder_never_reaches_the_callers():
    with mock.patch.object(wire_module, "encode_wire", side_effect=RuntimeError("boom")):
        assert wire_module.generate_wire_summary("p", "s", prompt_col="p", system_prompt_col="s") == {}
        assert wire_module.wire_summary_json({"wire": object()}) is None
    assert wire_module.wire_summary_json({}) is None
    assert wire_module.wire_summary_json(mock.MagicMock()) is None


def test_domain_exclusion_encoder_failure_keeps_the_row_without_a_wire():
    from moralstack.orchestration.domain_exclusion import generate_domain_exclusion_response

    policy = _make_policy(native=False, replies={"domain_exclusion": "I am sorry, I cannot help with this topic."})
    with (
        mock.patch.object(wire_module, "encode_wire", side_effect=RuntimeError("boom")),
        mock.patch(_RECORD_LLM_CALL) as record,
    ):
        text = generate_domain_exclusion_response("political", PROMPT, policy)
    _, _, kwargs = record.call_args.args
    assert text and kwargs["parsed_summary_json"] is None


@pytest.mark.parametrize("native", (True, False))
def test_dccl_evaluate_row_wire_decodes_to_what_the_policy_received(native, monkeypatch):
    from moralstack.compliance.dccl import DeveloperContractComplianceLayer

    monkeypatch.setenv("MORALSTACK_DCCL_ENABLED", "true")
    monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "llm")
    policy = _make_policy(native=native)
    layer = DeveloperContractComplianceLayer(policy=policy)
    request = SimpleNamespace(developer_contract=cap._contract(), prompt=PROMPT, conversation_context=None)
    with mock.patch(_RECORD_LLM_CALL) as record:
        layer.evaluate(request, speculative_draft="A speculative draft.")
    (call,) = [c for c in policy.snapshot() if c.label == "dccl"]
    (kwargs,) = [c.args[2] for c in record.call_args_list if c.args[2]["action"] == "evaluate"]
    summary = json.loads(kwargs["parsed_summary_json"])
    assert summary["wire"]["branch"] == ("messages" if native else "generate")
    assert _decoded_summary(kwargs["parsed_summary_json"], kwargs["prompt"], kwargs["system_prompt"]) == _sent_messages(call)


def test_draft_revalidate_row_wire_decodes_to_what_the_policy_received():
    from moralstack.orchestration.controller import OrchestrationController

    policy = _make_policy(native=False)
    rule = SimpleNamespace(action_payload_summary="The authorized action payload text.")
    verdict = SimpleNamespace(matched_rule=rule)
    fake_self = SimpleNamespace(policy=policy, logger=None)
    with mock.patch(_RECORD_LLM_CALL) as record:
        # the draft does not contain the action text, so the semantic LLM check runs
        OrchestrationController._revalidate_draft(fake_self, verdict, "A different draft.")  # type: ignore[arg-type]
    (call,) = [c for c in policy.snapshot() if c.label == "draft_match"]
    _, _, kwargs = record.call_args.args
    assert kwargs["action"] == "draft_revalidate"
    assert _decoded_summary(kwargs["parsed_summary_json"], kwargs["prompt"], kwargs["system_prompt"]) == _sent_messages(call)


@pytest.mark.parametrize(
    ("native", "context", "branch"),
    [(True, "history", "messages"), (True, "nocontext", "generate"), (False, "history", "generate")],
)
def test_upstream_speculative_row_wire_decodes_to_what_the_generator_received(native, context, branch):
    from moralstack.orchestration._policy_helpers import POLICY_SYSTEM_PROMPT
    from moralstack.orchestration.controller import OrchestrationController
    from moralstack.orchestration.types import ProcessedRequest
    from moralstack.utils.output_protection import create_protector
    from tests.test_persistence_fidelity import _context_requests

    protector = create_protector(enable_canary=True, enable_delimiters=True)
    protected = protector.prepare_system_prompt(POLICY_SYSTEM_PROMPT)
    internal = _make_policy(native=True)
    upstream = _make_policy(native=native)
    upstream.model = "upstream-model"
    request = ProcessedRequest(request_id="req-up", upstream_draft_generator=upstream, **_context_requests()[context])
    fake_self = SimpleNamespace(
        policy=internal,
        _protected_system_prompt=protected,
        _output_protector=protector,
        _emit_context_shape=lambda **_: None,
    )
    text, kwargs = OrchestrationController._speculative_generate(fake_self, request)  # type: ignore[arg-type]
    assert text is not None and kwargs is not None
    assert internal.snapshot() == []  # only the upstream generator was called
    (call,) = upstream.snapshot()
    summary = json.loads(kwargs["parsed_summary_json"])
    assert kwargs["module"] == "upstream_speculative" and summary["draft_origin"] == "upstream"
    assert kwargs["model"] == "upstream-model"
    assert summary["wire"]["branch"] == branch
    assert _decoded_summary(kwargs["parsed_summary_json"], kwargs["prompt"], kwargs["system_prompt"]) == _sent_messages(call)
