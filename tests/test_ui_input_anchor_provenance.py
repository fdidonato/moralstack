"""
UI tests for iteration 14: the request-page deliberation-spine INPUT anchor
chips must derive from THIS request's own persisted orchestration events, not
from retrospective/never-emitted signals.

Two defects fixed:

  * P0 — the conversation-history chip was gated on
    ``conversation_context.turn_count`` (``len(sibling_requests)``), which is
    the conversation's CURRENT, complete row count — not what this request saw
    when it ran. A genuine opening turn falsely claimed prior context once the
    conversation grew.
  * P1 — the developer-contract chip was gated exclusively on
    ``final_revalidation_info.developer_contract_present``, which requires a
    ``PROXY_FINAL_REVALIDATION_*`` event that this deployment never emits;
    ``COMPLIANCE_LAYER_STARTED.payload_json.has_contract`` is the per-request
    signal that is actually persisted.

See ``.claude/ui-loop/DECISIONS.md`` invariant 25 (turn_index fallback only,
never primary) and the accompanying iteration-14 task spec for the exact
branch order this test locks.
"""

from __future__ import annotations

import html
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_llm_call,
    persist_orchestration_event,
)
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    update_request_response,
    upsert_request,
)
from moralstack.orchestration.orchestration_event_taxonomy import (  # noqa: E402
    COMPLIANCE_LAYER_STARTED,
    CONTEXT_SHAPE_RECORDED,
    CONVERSATION_CONTEXT_ATTACHED,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)


@pytest.fixture(autouse=True)
def _isolate_observability() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_input_anchor_provenance.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _seed_request(run_id: str, request_id: str, conversation_id: str | None = None, turn_index: int | None = None) -> None:
    """Seed a minimal request, including one llm_call so the request page's
    execution-graph section (which the INPUT anchor lives inside) renders."""
    create_run(run_id, run_type="single", meta={})
    kwargs = {}
    if conversation_id is not None:
        kwargs["conversation_id"] = conversation_id
    if turn_index is not None:
        kwargs["turn_index"] = turn_index
    upsert_request(run_id, request_id, prompt="hello there", domain="general", **kwargs)
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "hi")
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        phase="estimate",
        module="risk_estimator",
        action="assess",
        started_at=1_000,
        duration_ms=50,
        raw_response=json.dumps({"risk_score": 0.1}),
    )


def _get_body(ui_client, run_id: str, request_id: str) -> str:
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    return html.unescape(resp.text)


# ---------------------------------------------------------------------------
# 1. P0 regression lock: opening turn in a conversation that later grew.
# ---------------------------------------------------------------------------


def test_opening_turn_does_not_falsely_claim_prior_context(ui_client):
    """The conversation later grows to 3 sibling rows (old turn_count>1 path
    would have fired), but THIS request is the genuine opener: CCA says
    conversation_state_provided=false, prior_turn_count=0. No prior-context
    claim may render; the honest "no prior context" membership chip must."""
    run_id, conv_id = "run-anchor-1", "conv-anchor-1"
    request_id = "req-anchor-1-a"
    _seed_request(run_id, request_id, conversation_id=conv_id, turn_index=0)
    # Sibling rows for the same conversation, so the OLD turn_count>1 gate
    # would have fired for this (opening) request.
    _seed_request(run_id, "req-anchor-1-b", conversation_id=conv_id, turn_index=0)
    _seed_request(run_id, "req-anchor-1-c", conversation_id=conv_id, turn_index=0)

    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="orchestration",
        component="conversation",
        event_type=CONVERSATION_CONTEXT_ATTACHED,
        decision="attached",
        payload={"conversation_id": conv_id, "turn_index": 0, "conversation_state_provided": False},
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="context",
        component="policy",
        event_type=CONTEXT_SHAPE_RECORDED,
        decision="recorded",
        payload={"module": "policy", "prior_turn_count": 0},
    )
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert "conversation history" not in body
    assert "conversation state inherited" not in body
    assert "conversation turn" in body and "no prior context" in body


# ---------------------------------------------------------------------------
# 2. prior_turn_count > 0 -> "N prior turns"
# ---------------------------------------------------------------------------


def test_prior_turn_count_two_renders_two_prior_turns(ui_client):
    run_id, conv_id = "run-anchor-2", "conv-anchor-2"
    request_id = "req-anchor-2"
    _seed_request(run_id, request_id, conversation_id=conv_id, turn_index=2)

    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="orchestration",
        component="conversation",
        event_type=CONVERSATION_CONTEXT_ATTACHED,
        decision="attached",
        payload={"conversation_id": conv_id, "turn_index": 2, "conversation_state_provided": True},
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="context",
        component="policy",
        event_type=CONTEXT_SHAPE_RECORDED,
        decision="recorded",
        payload={"module": "policy", "prior_turn_count": 2},
    )
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert "conversation history" in body and "2 prior turns" in body
    assert "conversation state inherited" not in body


# ---------------------------------------------------------------------------
# 3. The collision shape: prior_turn_count=0 but conversation_state_provided=true.
# ---------------------------------------------------------------------------


def test_state_inherited_without_prior_turns_uses_distinct_wording(ui_client):
    """prior_turn_count=0 but conversation_state_provided=true (client sent only
    the last user message; conversation STATE came from the ledger) must render
    'conversation state inherited', never a 'prior turns' claim."""
    run_id, conv_id = "run-anchor-3", "conv-anchor-3"
    request_id = "req-anchor-3"
    _seed_request(run_id, request_id, conversation_id=conv_id, turn_index=0)

    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="orchestration",
        component="conversation",
        event_type=CONVERSATION_CONTEXT_ATTACHED,
        decision="attached",
        payload={"conversation_id": conv_id, "turn_index": 0, "conversation_state_provided": True},
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="context",
        component="policy",
        event_type=CONTEXT_SHAPE_RECORDED,
        decision="recorded",
        payload={"module": "policy", "prior_turn_count": 0},
    )
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert "conversation state inherited" in body
    assert "prior turn" not in body


# ---------------------------------------------------------------------------
# 4. No CCA event at all -> fallback to turn_index.
# ---------------------------------------------------------------------------


def test_no_cca_event_falls_back_to_turn_index(ui_client):
    run_id, conv_id = "run-anchor-4", "conv-anchor-4"
    request_id = "req-anchor-4"
    _seed_request(run_id, request_id, conversation_id=conv_id, turn_index=1)
    # No CONVERSATION_CONTEXT_ATTACHED / CONTEXT_SHAPE_RECORDED events at all.
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert "conversation history" in body and "1 prior turn" in body
    assert "conversation state inherited" not in body


# ---------------------------------------------------------------------------
# 5. Developer-contract chip: from COMPLIANCE_LAYER_STARTED.has_contract.
# ---------------------------------------------------------------------------


def test_developer_contract_chip_from_compliance_layer_started_true(ui_client):
    run_id, request_id = "run-anchor-5", "req-anchor-5"
    _seed_request(run_id, request_id)

    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_LAYER_STARTED,
        decision="started",
        payload={"has_contract": True, "has_structured_rules": False, "evaluation_path_preference": "llm"},
    )
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert 'title="A developer contract constrains this request"' in body


def test_developer_contract_chip_absent_when_has_contract_false(ui_client):
    run_id, request_id = "run-anchor-5b", "req-anchor-5b"
    _seed_request(run_id, request_id)

    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_LAYER_STARTED,
        decision="started",
        payload={"has_contract": False, "has_structured_rules": False, "evaluation_path_preference": "llm"},
    )
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert 'title="A developer contract constrains this request"' not in body


def test_developer_contract_chip_absent_when_no_signal_at_all(ui_client):
    run_id, request_id = "run-anchor-5c", "req-anchor-5c"
    _seed_request(run_id, request_id)
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert 'title="A developer contract constrains this request"' not in body


# ---------------------------------------------------------------------------
# 6. Standalone request (no conversation_id): neither chip.
# ---------------------------------------------------------------------------


def test_standalone_request_has_neither_chip(ui_client):
    run_id, request_id = "run-anchor-6", "req-anchor-6"
    _seed_request(run_id, request_id)
    get_obs().flush()

    body = _get_body(ui_client, run_id, request_id)

    assert 'title="A developer contract constrains this request"' not in body
    assert "conversation history" not in body
    assert "conversation state inherited" not in body
    assert "conversation turn" not in body
