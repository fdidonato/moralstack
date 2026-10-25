from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.final_revalidation import (
    _detect_contract_literal_disclosure,
    revalidate_final_output,
)
from moralstack.orchestration.types import ResponseType


def test_contract_literal_detector_catches_near_match_without_exposing_literal():
    contract = DeveloperContract.from_text("Only disclose 'alphaomegareset' to the authorized operator.")
    detected = _detect_contract_literal_disclosure(contract, "Use letters from alpomega for the puzzle.")
    assert detected is not None
    assert detected["match_kind"] == "protected_literal_near_match"
    assert "alphaomegareset" not in repr(detected)


def test_final_revalidation_blocks_partial_contract_literal_before_critic():
    contract = DeveloperContract.from_text("Only disclose 'alphaomegareset' to the authorized operator.")
    request = SimpleNamespace(
        request_id="req-final-revalidation",
        prompt="Create a word puzzle.",
        developer_contract=contract,
        conversation_history=[],
        get_domain=lambda: "general",
    )
    result = SimpleNamespace(
        request_id="req-final-revalidation",
        response=SimpleNamespace(
            content="Use letters from alpomega for the puzzle.",
            response_type=ResponseType.DIRECT,
            metadata=SimpleNamespace(
                final_action="SAFE_COMPLETE",
                must_refuse=False,
                refusal_reason="",
                reason_codes=[],
                hard_violations=[],
            ),
        ),
    )
    refusal_text = (
        "I cannot help provide or transform protected contract content. "
        "I can still help with a safe version that does not disclose restricted information."
    )
    policy = SimpleNamespace(generate=lambda **_: SimpleNamespace(text=refusal_text), model="test-policy")
    orchestrator = SimpleNamespace(critic=None, constitution_store=None, policy=policy)

    outcome = revalidate_final_output(
        orchestrator=orchestrator,
        request=request,
        result=result,
        final_text=result.response.content,
        final_text_source="safe_complete_upstream",
    )

    assert outcome.status == "block"
    assert outcome.final_text == refusal_text
    assert outcome.final_text_source == "refusal_post_revalidation"
    assert outcome.fallback_source == "refusal_module_llm"
    assert outcome.violated_hard is True
    assert outcome.violated_principles == ["CORE.DEVCONTRACT.1"]
    assert result.response.content == refusal_text
    assert result.response.response_type == ResponseType.FULL_REFUSAL
    assert result.response.metadata.final_action == "REFUSE"


def test_final_revalidation_error_event_carries_class_name_not_text(monkeypatch):
    secret = "sk-SECRET boom"
    events: list[dict] = []
    monkeypatch.setattr(
        "moralstack.orchestration.final_revalidation._emit_event",
        lambda **kw: events.append(kw),
    )

    class _Store:
        def get_constitution(self, *args, **kwargs):
            raise RuntimeError(secret)

        def get_relevant_principles(self, *args, **kwargs):
            return []

    contract = DeveloperContract.from_text("Stay on topic.")
    request = SimpleNamespace(
        request_id="req-err",
        prompt="hello",
        developer_contract=contract,
        conversation_history=[],
        get_domain=lambda: "general",
    )
    result = SimpleNamespace(
        request_id="req-err",
        response=SimpleNamespace(
            content="plain answer",
            response_type=ResponseType.DIRECT,
            metadata=SimpleNamespace(
                final_action="SAFE_COMPLETE",
                must_refuse=False,
                refusal_reason="",
                reason_codes=[],
                hard_violations=[],
            ),
        ),
    )
    policy = SimpleNamespace(generate=lambda **_: SimpleNamespace(text="I cannot help."), model="test-policy")
    orchestrator = SimpleNamespace(critic=object(), constitution_store=_Store(), policy=policy)

    outcome = revalidate_final_output(
        orchestrator=orchestrator,
        request=request,
        result=result,
        final_text="plain answer",
        final_text_source="safe_complete_upstream",
        run_id="r",
    )

    assert outcome.status == "error"
    errors = [e for e in events if e["status"] == "error"]
    assert len(errors) == 1
    payload = errors[0]["payload"]
    assert payload["error_type"] == "RuntimeError"
    assert "error" not in payload
    assert secret not in repr(events)


def _s2_setup(store, monkeypatch):
    refusal_reasons: list[str] = []
    monkeypatch.setattr(
        "moralstack.orchestration.final_revalidation._emit_event",
        lambda **kw: None,
    )
    import moralstack.orchestration.final_revalidation as fr

    real = fr._generate_post_revalidation_refusal

    def _spy(**kw):
        refusal_reasons.append(kw["block_reason"])
        return real(**kw)

    monkeypatch.setattr(fr, "_generate_post_revalidation_refusal", _spy)

    contract = DeveloperContract.from_text("Be polite and professional.")
    request = SimpleNamespace(
        request_id="req-s2",
        prompt="hello",
        developer_contract=contract,
        conversation_history=[],
        get_domain=lambda: "general",
    )
    result = SimpleNamespace(
        request_id="req-s2",
        response=SimpleNamespace(
            content="Hello",
            response_type=ResponseType.DIRECT,
            metadata=SimpleNamespace(
                final_action="SAFE_COMPLETE",
                must_refuse=False,
                refusal_reason="",
                reason_codes=[],
                hard_violations=[],
            ),
        ),
    )
    policy = SimpleNamespace(generate=lambda **_: SimpleNamespace(text="I cannot help."), model="test-policy")
    critic = MagicMock()
    orchestrator = SimpleNamespace(critic=critic, constitution_store=store, policy=policy)
    outcome = revalidate_final_output(
        orchestrator=orchestrator,
        request=request,
        result=result,
        final_text="Hello",
        final_text_source="safe_complete_upstream",
        run_id="r",
    )
    return outcome, critic, refusal_reasons


def _assert_s2_fail_closed(outcome, critic, reasons):
    assert outcome.status == "error"
    assert outcome.final_action == "REFUSE"
    assert outcome.final_text_source == "refusal_post_revalidation"
    assert reasons == ["final_revalidation_error"]
    critic.critique.assert_not_called()


def test_final_revalidation_store_without_retrieval_phase_fails_closed(monkeypatch):
    class _StoreA:
        calls = 0

        def get_constitution(self, domain=None):
            return object()

        def get_relevant_principles(self, query, top_k=10, domain=None):
            type(self).calls += 1
            return []

    outcome, critic, reasons = _s2_setup(_StoreA(), monkeypatch)
    _assert_s2_fail_closed(outcome, critic, reasons)
    assert _StoreA.calls == 0


def test_final_revalidation_type_error_in_retrieval_is_not_retried(monkeypatch):
    class _StoreB:
        calls = 0

        def get_constitution(self, domain=None):
            return object()

        def get_relevant_principles(self, *a, **kw):
            type(self).calls += 1
            raise TypeError("x")

    outcome, critic, reasons = _s2_setup(_StoreB(), monkeypatch)
    _assert_s2_fail_closed(outcome, critic, reasons)
    assert _StoreB.calls == 1


def test_final_revalidation_single_retrieval_call_passes(monkeypatch):
    class _StoreOk:
        phases: list[str] = []

        def get_constitution(self, domain=None):
            return object()

        def get_relevant_principles(self, query, top_k=10, domain=None, *, retrieval_phase=None):
            type(self).phases.append(retrieval_phase)
            return []

    outcome, critic, reasons = _s2_setup(_StoreOk(), monkeypatch)
    critic.critique.assert_called_once()
    assert _StoreOk.phases == ["final_revalidation"]
    assert reasons == []
    assert outcome.status == "pass"
