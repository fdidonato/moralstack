"""F6 follow-up C1 - one normalization rule for the ``final_action`` label.

``normalize_final_action`` (``orchestration/types.py``) is the single rule used by
``finalize_delivery``, the ``ensure`` early return, the turn summary and posture, and
the ledger store check. The ``ensure`` backfill writes canonical upper-case names. The
ledger store is strict skip-only: exact membership in ``CANONICAL_FINAL_ACTIONS`` or no
store at all (the ledger can only skip, never apply something new).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest

from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.orchestration.conversational_fast_path import ConversationalFastPathRunner
from moralstack.orchestration.deliberation_runner import LEDGER_NON_CACHEABLE_FLIP_REASONS
from moralstack.orchestration.delivery import finalize_delivery
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.types import (
    CANONICAL_FINAL_ACTIONS,
    FinalResponse,
    OrchestratorResult,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    normalize_final_action,
)
from tests.test_conversational_fast_path import _make_decision, _make_hit
from tests.test_pipeline_error_fail_closed import (
    _capture_logger,
    _ctrl,
    _ledger_result,
    _new_ledger,
    _store_size,
)

_CANONICAL = {"NORMAL_COMPLETE", "SAFE_COMPLETE", "REFUSE"}


def _ensure(ctrl: Any, label: str, response_type: ResponseType) -> OrchestratorResult:
    result = OrchestratorResult(
        response=FinalResponse(content="ANSWER", response_type=response_type, metadata=ResponseMetadata(final_action=label)),
        request_id="req-c1",
    )
    return ctrl._diagnostics.ensure_final_action_and_return(
        result,
        ProcessedRequest(prompt="hello", request_id="req-c1"),
        time.time(),
        "c1",
        lambda r, _req: r,
    )


def _summary_state(ctrl: Any, label: Any, *, constraints: tuple[str, ...] = ()) -> ConversationGovernanceState:
    result = OrchestratorResult(
        response=FinalResponse(
            content="ANSWER", response_type=ResponseType.DIRECT, metadata=ResponseMetadata(final_action=label)
        ),
        request_id="req-c1",
        conversation_id="c1",
        turn_index=1,
    )
    return ctrl._extend_state_out_v04(
        state=ConversationGovernanceState(conversation_id="c1", turn_index=1, last_hard_constraints_triggered=constraints),
        request=ProcessedRequest(prompt="hello", request_id="req-c1"),
        result=result,
        call_ctx=ProcessCallContext(),
    )


# --------------------------------------------------------------------------- L1 / L2


@pytest.mark.parametrize(
    "response_type, expected",
    [
        (ResponseType.DIRECT, "NORMAL_COMPLETE"),
        (ResponseType.WITH_CAVEAT, "SAFE_COMPLETE"),
        (ResponseType.FULL_REFUSAL, "REFUSE"),
        (ResponseType.PARTIAL_REFUSAL, "REFUSE"),
        (ResponseType.REDIRECT, "REFUSE"),
        (ResponseType.DOMAIN_EXCLUDED, "REFUSE"),
    ],
)
def test_l1_ensure_backfill_writes_canonical_label(
    request: pytest.FixtureRequest, response_type: ResponseType, expected: str
) -> None:
    out = _ensure(_ctrl(request=request), "", response_type)
    assert out.response.metadata.final_action == expected
    assert finalize_delivery(out).final_action == expected


@pytest.mark.parametrize("label", ["REFUSE", "refuse", " Safe_Complete "])
def test_l2_ensure_keeps_present_valid_label(request: pytest.FixtureRequest, label: str) -> None:
    out = _ensure(_ctrl(request=request), label, ResponseType.DIRECT)
    assert out.response.metadata.final_action == label


def test_l2b_ensure_passes_nonempty_garbage_label_through(request: pytest.FixtureRequest) -> None:
    # CHAR: a non-empty unknown label with a mappable response_type is not rewritten here;
    # delivery fails it closed.
    out = _ensure(_ctrl(request=request), "garbage", ResponseType.DIRECT)
    assert out.response.metadata.final_action == "garbage"
    assert finalize_delivery(out).final_action == "REFUSE"


def test_l2c_ensure_lookalike_passes_through_and_delivery_refuses(request: pytest.FixtureRequest) -> None:
    out = _ensure(_ctrl(request=request), "ſafe_complete", ResponseType.DIRECT)
    assert out.response.metadata.final_action == "ſafe_complete"
    delivery = finalize_delivery(out)
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"


# --------------------------------------------------------------------------- L3


@pytest.mark.parametrize(
    "label, expected",
    [
        ("REFUSE", "REFUSE"),
        (" refuse\n", "REFUSE"),
        ("Safe_Complete", "SAFE_COMPLETE"),
        ("normal_complete", "NORMAL_COMPLETE"),
        ("ſafe_complete", None),
        ("refuſe", None),
        ("", None),
        ("ERROR", None),
        ("NORMAL COMPLETE", None),
        ("REFUSE\x00", None),
    ],
)
def test_l3_normalize_final_action_contract(label: str, expected: str | None) -> None:
    assert normalize_final_action(label) == expected


def test_l3_canonical_set_is_closed() -> None:
    assert CANONICAL_FINAL_ACTIONS == _CANONICAL


# --------------------------------------------------------------------------- L4 / L5


@pytest.mark.parametrize(
    "label, expected",
    [
        ("refuse", "REFUSE"),
        (" Safe_Complete ", "SAFE_COMPLETE"),
        ("garbage", "REFUSE"),
        ("ſafe_complete", "REFUSE"),
        ("", "REFUSE"),
    ],
)
def test_l4_turn_summary_normalizes_non_canonical_label(request: pytest.FixtureRequest, label: str, expected: str) -> None:
    state = _summary_state(_ctrl(request=request), label)
    assert state.turn_decisions_summary[-1].final_action == expected


def test_l4_turn_summary_non_string_label_is_refuse(request: pytest.FixtureRequest) -> None:
    from unittest.mock import MagicMock

    state = _summary_state(_ctrl(request=request), MagicMock())
    assert state.turn_decisions_summary[-1].final_action == "REFUSE"


@pytest.mark.parametrize("label", sorted(_CANONICAL))
def test_l4_turn_summary_canonical_label_unchanged(request: pytest.FixtureRequest, label: str) -> None:
    state = _summary_state(_ctrl(request=request), label)
    assert state.turn_decisions_summary[-1].final_action == label


def test_l5_posture_escalated_with_lowercase_refuse(request: pytest.FixtureRequest) -> None:
    state = _summary_state(_ctrl(request=request), "refuse", constraints=("self_harm_crisis",))
    assert state.last_governance_posture == "ESCALATED"


@pytest.mark.parametrize(
    "label, constraints",
    [
        ("SAFE_COMPLETE", ("self_harm_crisis",)),
        ("NORMAL_COMPLETE", ("self_harm_crisis",)),
        ("REFUSE", ()),
    ],
)
def test_l5n_posture_not_escalated_for_other_labels(
    request: pytest.FixtureRequest, label: str, constraints: tuple[str, ...]
) -> None:
    state = _summary_state(_ctrl(request=request), label, constraints=constraints)
    assert state.last_governance_posture != "ESCALATED"


# --------------------------------------------------------------------------- L6 / L7


def _skip_infos(handler: Any) -> list[logging.LogRecord]:
    return [
        r
        for r in handler.records
        if r.levelno == logging.INFO and r.getMessage().startswith("ledger store skipped for non-canonical")
    ]


@pytest.mark.parametrize("label", ["refuse", " REFUSE ", "safe_complete"])
def test_l6_ledger_skips_non_exact_label(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    handler = _capture_logger(monkeypatch, "moralstack.orchestration.controller._LOG", request.node.name)
    ctrl = _ctrl(request=request)
    assert _store_size(ctrl, _ledger_result("", final_action=label)) == 0
    assert len(_skip_infos(handler)) == 1


def test_l6b_ledger_stores_canonical_label_and_applies(request: pytest.FixtureRequest) -> None:
    ctrl = _ctrl(request=request)
    ledger = _new_ledger()
    ctrl._ledger = ledger
    stored: list[Any] = []
    real_put = ledger._storage.put

    def _spy(key: Any, entry: Any) -> None:
        stored.append(entry)
        real_put(key, entry)

    ledger._storage.put = _spy  # type: ignore[method-assign]
    ctrl._maybe_store_in_ledger(
        request=ProcessedRequest(prompt="p", request_id="req-c"),
        result=_ledger_result("", final_action="REFUSE"),
        call_ctx=ProcessCallContext(conversation_id="c1", turn_index=1),
    )
    assert len(stored) == 1
    cached = stored[0].cached_decision
    assert cached.final_action == "REFUSE"
    _, route = ConversationalFastPathRunner().apply_cached_decision(
        ledger_result=_make_hit(cached), current_decision=_make_decision()
    )
    assert route == "refuse"


@pytest.mark.parametrize("label", ["garbage", "ſafe_complete", "NORMAL COMPLETE"])
def test_l7_ledger_skips_unknown_label(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    handler = _capture_logger(monkeypatch, "moralstack.orchestration.controller._LOG", request.node.name)
    ctrl = _ctrl(request=request)
    assert _store_size(ctrl, _ledger_result("", final_action=label)) == 0
    assert len(_skip_infos(handler)) == 1


def test_l7b_ledger_flip_reason_skip_still_wins(request: pytest.FixtureRequest) -> None:
    ctrl = _ctrl(request=request)
    reason = sorted(LEDGER_NON_CACHEABLE_FLIP_REASONS)[0]
    assert _store_size(ctrl, _ledger_result(reason, final_action="REFUSE")) == 0
