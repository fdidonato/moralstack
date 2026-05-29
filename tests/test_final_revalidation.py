from __future__ import annotations

from types import SimpleNamespace

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
