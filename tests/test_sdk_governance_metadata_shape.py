"""
SDK `GovernanceMetadata` additive-shape coverage for
`generation="upstream_then_verify"` (Codex round-4).

`GovernanceMetadata` gains two defaulted fields -- `draft_origin`/`draft_model`
-- as a deliberate, documented additive public-API change (the SINGLE scoped
exception to byte-identity, per the plan). This file pins:
  - internal mode: the new fields are present with their defaults
    (`"internal"`/`""`), and every prior field is populated exactly as before;
  - upstream clean mode: the new fields carry the client provenance.

Kept separate from the persisted/wire byte-identity suite
(`test_upstream_then_verify_observability.py`), per the plan's explicit
instruction: this object is an additive API change, not a byte-identity target.
"""

from __future__ import annotations

import dataclasses

from moralstack.orchestration.types import FinalResponse, OrchestratorResult, ResponseMetadata, ResponseType
from moralstack.sdk.response import GovernanceMetadata


def _make_metadata(**overrides: object) -> ResponseMetadata:
    meta = ResponseMetadata()
    meta.final_action = "NORMAL_COMPLETE"
    meta.risk_score = 0.12
    meta.risk_category = "clearly_benign"
    meta.path = "FAST_PATH"
    meta.domain_overlay = "healthcare"
    meta.reason_codes = ["SENSITIVE_DOMAIN"]
    meta.winning_rule = "fast_path_allow"
    meta.decision_reason = "benign request"
    meta.processing_time_ms = 42
    meta.deliberation_cycles = 0
    meta.triggered_principles = ["CORE.1"]
    meta.why_not_refuse = "risk below threshold"
    meta.why_not_safe_complete = "no caveat needed"
    meta.input_tokens = 10
    meta.output_tokens = 20
    meta.total_tokens = 30
    meta.llm_call_count = 1
    meta.token_usage_missing_count = 0
    meta.token_usage_estimated_count = 0
    meta.usage_may_be_incomplete = False
    meta.incomplete_reason = None
    for key, value in overrides.items():
        setattr(meta, key, value)
    return meta


def _make_result(
    metadata: ResponseMetadata, *, conversation_id: str | None = None, turn_index: int | None = None
) -> OrchestratorResult:
    response = FinalResponse(content="the answer", response_type=ResponseType.DIRECT, metadata=metadata)
    return OrchestratorResult(
        response=response,
        request_id="req-shape-1",
        path_taken="fast",
        path="FAST_PATH",
        total_cycles=0,
        converged=True,
        conversation_id=conversation_id,
        turn_index=turn_index,
    )


class TestInternalModeAdditiveShape:
    def test_new_fields_present_with_defaults_and_prior_fields_unchanged(self) -> None:
        metadata = _make_metadata()  # draft_origin/draft_model left at ResponseMetadata defaults
        result = _make_result(metadata, conversation_id="conv-1", turn_index=0)

        gm = GovernanceMetadata.from_result(result)

        # Additive fields: present, defaulted.
        field_names = {f.name for f in dataclasses.fields(GovernanceMetadata)}
        assert {"draft_origin", "draft_model"} <= field_names
        assert gm.draft_origin == "internal"
        assert gm.draft_model == ""

        # Every prior field is populated exactly as before -- unaffected by
        # the additive fields' presence.
        assert gm.final_action == "NORMAL_COMPLETE"
        assert gm.risk_score == 0.12
        assert gm.risk_category == "clearly_benign"
        assert gm.path == "FAST_PATH"
        assert gm.domain_overlay == "healthcare"
        assert gm.reason_codes == ["SENSITIVE_DOMAIN"]
        assert gm.winning_rule == "fast_path_allow"
        assert gm.decision_reason == "benign request"
        assert gm.processing_time_ms == 42
        assert gm.deliberation_cycles == 0
        assert gm.triggered_principles == ["CORE.1"]
        assert gm.why_not_refuse == "risk below threshold"
        assert gm.why_not_safe_complete == "no caveat needed"
        assert gm.conversation_id == "conv-1"
        assert gm.turn_index == 0
        assert gm.input_tokens == 10
        assert gm.output_tokens == 20
        assert gm.total_tokens == 30
        assert gm.llm_call_count == 1


class TestUpstreamCleanModeProvenance:
    def test_upstream_fields_carry_client_provenance(self) -> None:
        metadata = _make_metadata(
            draft_origin="upstream",
            draft_model="client-model-C",
            internal_draft_reused=True,
        )
        result = _make_result(metadata)

        gm = GovernanceMetadata.from_result(result)

        assert gm.draft_origin == "upstream"
        assert gm.draft_model == "client-model-C"
        # Prior fields are still populated normally alongside the new ones.
        assert gm.final_action == "NORMAL_COMPLETE"
        assert gm.risk_score == 0.12
