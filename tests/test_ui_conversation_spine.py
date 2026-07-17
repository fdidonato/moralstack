"""
UI tests for the conversation-level spine on ``/conversations/{id}`` (Part B
of the conversation-spine plan): a first-turn node (contract/history chips),
one node per turn (decisional input -> decision -> outcome), a terminal node
folding the failure-aware aggregates, and the connectors between them
(cache-reuse label, posture-transition label, and the non-causal unordered
divider for colliding turn_index).

Reuses the house fixtures from ``tests/test_ui_conversation_views.py``. Real
tmp_path SQLite, offline, deterministic. Does not mock
``get_orchestration_events_for_request`` for the *normal* input-anchor tests
(see ``test_ui_input_anchor_provenance.py`` — that is the ground truth for
what "truthful reuse" means); the one exception is the best-effort §5 #6 test,
which forces a raise deliberately to prove the try/except contract — that
cannot be produced through malformed-but-parseable JSON alone, since
``_parse_json_field`` already swallows JSON errors.
"""

from __future__ import annotations

import html
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.conversation_events import emit_conversation_state_updated  # noqa: E402
from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_decision_trace,
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
    dbp = str(tmp_path / "ui_conversation_spine.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _seed_turn(
    run_id: str,
    request_id: str,
    conv_id: str,
    turn_index: int,
    *,
    final_action: str = "NORMAL_COMPLETE",
    risk_score: float = 0.1,
    posture: str | None = None,
    was_cached: bool = False,
    cached_from_turn: int | None = None,
    prior_turn_count: int | None = None,
    conversation_state_provided: bool | None = None,
    has_contract: bool | None = None,
    prompt: str = "turn prompt",
    response: str = "turn response",
    pipeline_failure: bool = False,
    parent_request_id: str | None = None,
    winning_rule: str = "rule_x",
    hard_violation_codes: list[str] | None = None,
) -> None:
    """Seed one conversation turn: request row, meta, response, optional
    per-turn orchestration events (COMPLIANCE_LAYER_STARTED /
    CONVERSATION_CONTEXT_ATTACHED / CONTEXT_SHAPE_RECORDED), a conversation
    state snapshot when posture/cache fields are set, and a decision trace
    (FINAL, unless ``pipeline_failure``, which persists RISK_ASSESSMENT only
    and no FINAL row — matching ``_detect_pipeline_failure``'s contract)."""
    kwargs: dict = {"conversation_id": conv_id, "turn_index": turn_index}
    if parent_request_id is not None:
        kwargs["parent_request_id"] = parent_request_id
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt=prompt, domain="general", **kwargs)

    meta: dict = {"final_action": final_action, "risk_score": risk_score}
    if posture is not None:
        meta["governance_posture"] = posture
    if was_cached:
        meta["was_cached"] = True
    if cached_from_turn is not None:
        meta["cached_from_turn"] = cached_from_turn
    if pipeline_failure:
        meta["triggered_principles"] = ["SYSTEM.ERROR"]
    update_request_meta(run_id, request_id, meta)
    update_request_response(run_id, request_id, response)

    if has_contract is not None:
        persist_orchestration_event(
            run_id=run_id,
            request_id=request_id,
            stage="orchestration",
            component="compliance_layer",
            event_type=COMPLIANCE_LAYER_STARTED,
            decision="started",
            payload={"has_contract": has_contract},
        )
    if conversation_state_provided is not None:
        persist_orchestration_event(
            run_id=run_id,
            request_id=request_id,
            stage="orchestration",
            component="conversation",
            event_type=CONVERSATION_CONTEXT_ATTACHED,
            decision="attached",
            payload={
                "conversation_id": conv_id,
                "turn_index": turn_index,
                "conversation_state_provided": conversation_state_provided,
            },
        )
    if prior_turn_count is not None:
        persist_orchestration_event(
            run_id=run_id,
            request_id=request_id,
            stage="context",
            component="policy",
            event_type=CONTEXT_SHAPE_RECORDED,
            decision="recorded",
            payload={"module": "policy", "prior_turn_count": prior_turn_count},
        )

    if posture is not None or was_cached or cached_from_turn is not None:
        emit_conversation_state_updated(
            run_id=run_id,
            request_id=request_id,
            conversation_id=conv_id,
            turn_index=turn_index,
            state_in=None,
            state_out=None,
            final_action=final_action,
            risk_score=risk_score,
            posture=posture,
            was_cached=was_cached,
            cached_from_turn=cached_from_turn,
            refresh_required=False,
        )

    if pipeline_failure:
        persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="RISK_ASSESSMENT",
            sequence=1,
            trace_json=json.dumps({"risk_score": risk_score}),
        )
    else:
        trace_json = {
            "final_action": final_action,
            "path": "DELIBERATIVE_PATH",
            "risk_score": risk_score,
            "winning_rule": winning_rule,
            f"why_not_{final_action.lower()}": f"{final_action} chosen: reason.",
        }
        if hard_violation_codes is not None:
            trace_json["hard_violation_codes"] = hard_violation_codes
        persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps(trace_json),
        )


def _get_conversation_page(ui_client, conv_id: str) -> str:
    token = _make_session_token(ui_client)
    resp = ui_client.get(f"/conversations/{conv_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    return html.unescape(resp.text)


# ---------------------------------------------------------------------------
# Node count / order
# ---------------------------------------------------------------------------


def test_spine_renders_one_node_per_turn(ui_client):
    run_id, conv_id = "run-spine-1", "conv-spine-1"
    _seed_turn(run_id, "req-s1-0", conv_id, 0, final_action="NORMAL_COMPLETE")
    _seed_turn(run_id, "req-s1-1", conv_id, 1, final_action="SAFE_COMPLETE")
    _seed_turn(run_id, "req-s1-2", conv_id, 2, final_action="REFUSE")
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert body.count("conv-spine-slot--input") == 3
    # seq_pos order (turn_index, created_at): Turn 0 appears before Turn 1
    # appears before Turn 2 in the rendered document.
    pos0 = body.index("Turn 0")
    pos1 = body.index("Turn 1")
    pos2 = body.index("Turn 2")
    assert pos0 < pos1 < pos2


def test_spine_scales_beyond_three_turns(ui_client):
    run_id, conv_id = "run-spine-5turn", "conv-spine-5turn"
    for i in range(5):
        _seed_turn(run_id, f"req-5t-{i}", conv_id, i, final_action="NORMAL_COMPLETE")
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert body.count("conv-spine-slot--input") == 5
    for i in range(5):
        assert f"Turn {i}" in body


# ---------------------------------------------------------------------------
# First-turn node: developer contract / history chips
# ---------------------------------------------------------------------------


def test_first_node_shows_developer_contract_chip(ui_client):
    run_id, conv_id = "run-spine-contract", "conv-spine-contract"
    _seed_turn(run_id, "req-contract-1", conv_id, 0, has_contract=True)
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "developer contract present" in body


def test_first_node_omits_developer_contract_chip_when_absent(ui_client):
    run_id, conv_id = "run-spine-no-contract", "conv-spine-no-contract"
    _seed_turn(run_id, "req-no-contract-1", conv_id, 0, has_contract=False)
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "developer contract present" not in body


def test_first_node_shows_prior_turns_from_context_shape(ui_client):
    run_id, conv_id = "run-spine-prior", "conv-spine-prior"
    _seed_turn(run_id, "req-prior-1", conv_id, 0, prior_turn_count=2)
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "2 prior turns" in body


def test_node_state_inherited_wins_over_turn_index_fallback(ui_client):
    """invariant 32: CCA conversation_state_provided=True (no prior_turn_count
    event) must render 'conversation state inherited', never the turn_index
    fallback ('2 prior turns') even though turn_index=2 alone would trigger it
    if the CCA event were absent."""
    run_id, conv_id = "run-spine-inherited", "conv-spine-inherited"
    _seed_turn(run_id, "req-inherited-1", conv_id, 2, conversation_state_provided=True)
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "conversation state inherited" in body
    assert "prior turn" not in body


def test_first_node_chip_reflects_what_turn_1_actually_saw_not_row_count(ui_client):
    """P0 regression at conversation scope: 3 sibling rows exist ('Total
    turns' = 3), but turn 1 was seeded as a genuine opener
    (conversation_state_provided=False, prior_turn_count=0). The first node
    must say 'no prior context', never '3 prior turns' — invariant 31."""
    run_id, conv_id = "run-spine-opener", "conv-spine-opener"
    _seed_turn(
        run_id,
        "req-opener-0",
        conv_id,
        0,
        conversation_state_provided=False,
        prior_turn_count=0,
    )
    _seed_turn(run_id, "req-opener-1", conv_id, 1, final_action="NORMAL_COMPLETE")
    _seed_turn(run_id, "req-opener-2", conv_id, 2, final_action="NORMAL_COMPLETE")
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "Total turns" in body and ">3<" in body
    assert "3 prior turns" not in body
    assert "no prior context" in body


# ---------------------------------------------------------------------------
# Pipeline failure: neutral, verbatim text, no causal slot
# ---------------------------------------------------------------------------


def test_pipeline_failure_node_is_not_a_governed_success(ui_client):
    run_id, conv_id = "run-spine-failure", "conv-spine-failure"
    _seed_turn(
        run_id,
        "req-failure-1",
        conv_id,
        0,
        final_action="NORMAL_COMPLETE",
        risk_score=1.0,
        pipeline_failure=True,
    )
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "not a governed outcome" in body
    # Neutral badge: the failure never renders the green NORMAL_COMPLETE class.
    assert 'badge-ok">NORMAL_COMPLETE' not in body
    # No causal slot: no risk claim, no winning rule for the failed turn.
    assert "rule_x" not in body
    assert "no risk claim" not in body  # not even the "no FINAL row" note


# ---------------------------------------------------------------------------
# §5 #1 — action/colour derive from structured signals, never response text
# ---------------------------------------------------------------------------


def test_decisional_output_reads_from_traces_not_response_text__apologetic_body_on_normal_complete(
    ui_client,
):
    run_id, conv_id = "run-spine-dir-a", "conv-spine-dir-a"
    _seed_turn(
        run_id,
        "req-dir-a",
        conv_id,
        0,
        final_action="NORMAL_COMPLETE",
        response="I'm sorry, I can't help with that request.",
    )
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert 'badge-ok">NORMAL_COMPLETE' in body
    assert 'badge-fail">REFUSE' not in body


def test_decisional_output_reads_from_traces_not_response_text__compliant_body_on_refuse(
    ui_client,
):
    run_id, conv_id = "run-spine-dir-b", "conv-spine-dir-b"
    _seed_turn(
        run_id,
        "req-dir-b",
        conv_id,
        0,
        final_action="REFUSE",
        risk_score=0.95,
        response="Sure! Here's exactly how to do that.",
    )
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert 'badge-fail">REFUSE' in body
    assert 'badge-ok">NORMAL_COMPLETE' not in body


# ---------------------------------------------------------------------------
# Connectors: collision divider, cache-reuse label
# ---------------------------------------------------------------------------


def test_collision_connector_is_unordered_not_causal(ui_client):
    run_id, conv_id = "run-spine-collision", "conv-spine-collision"
    _seed_turn(run_id, "req-coll-a", conv_id, 0, final_action="NORMAL_COMPLETE")
    _seed_turn(run_id, "req-coll-b", conv_id, 0, final_action="SAFE_COMPLETE")
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "conv-spine-pipe--unordered" in body
    assert "order not established" in body
    assert "escalation" not in body.lower()
    assert "sequence" not in body.lower()
    # Canonical turn_index and #pos/size disambiguator stay visible.
    assert "Turn 0" in body
    assert "#1/2" in body and "#2/2" in body


def test_cached_from_edge_label(ui_client):
    run_id, conv_id = "run-spine-cache", "conv-spine-cache"
    _seed_turn(run_id, "req-cache-0", conv_id, 0, final_action="NORMAL_COMPLETE", posture="STABLE")
    _seed_turn(
        run_id,
        "req-cache-1",
        conv_id,
        1,
        final_action="NORMAL_COMPLETE",
        was_cached=True,
        cached_from_turn=0,
        posture="STABLE",
    )
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "reused decision from turn 0" in body


# ---------------------------------------------------------------------------
# Terminal node — reuses failure-aware aggregates
# ---------------------------------------------------------------------------


def test_terminal_node_reuses_failure_aware_aggregates(ui_client):
    run_id, conv_id = "run-spine-terminal", "conv-spine-terminal"
    _seed_turn(
        run_id,
        "req-terminal-0",
        conv_id,
        0,
        final_action="NORMAL_COMPLETE",
        risk_score=1.0,
        posture="ESCALATED",
        pipeline_failure=True,
    )
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "conv-spine-node--terminal" in body
    assert "Conversation end" in body
    assert "from a turn that ended in a pipeline failure, not a governed outcome" in body
    assert "includes 1 pipeline failure, not a governed outcome" in body


# ---------------------------------------------------------------------------
# §5 #3 — hard_violation_codes visible on the conversation spine turn node
# ---------------------------------------------------------------------------


def test_hard_violation_codes_visible_on_conversation_spine_turn(ui_client):
    run_id, conv_id = "run-spine-hard", "conv-spine-hard"
    _seed_turn(
        run_id,
        "req-hard-1",
        conv_id,
        0,
        final_action="REFUSE",
        risk_score=0.95,
        hard_violation_codes=["H_WEAPON_1"],
    )
    get_obs().flush()

    body = _get_conversation_page(ui_client, conv_id)
    assert "H_WEAPON_1" in body


# ---------------------------------------------------------------------------
# §5 #6 — one malformed turn cannot break the page
# ---------------------------------------------------------------------------


def test_malformed_turn_events_do_not_break_page(ui_client, monkeypatch):
    """Forces a raise from the per-turn orchestration-events fetch for one
    turn only. This is the one place this file monkeypatches
    ``get_orchestration_events_for_request`` — proving the try/except
    contract requires an actual exception, which malformed-but-parseable JSON
    cannot produce (``_parse_json_field`` already swallows JSON errors). The
    "do not mock" guidance in the plan is about not diverging from the real
    read path when testing *derivation* logic; this test targets the error
    boundary itself."""
    run_id, conv_id = "run-spine-malformed", "conv-spine-malformed"
    _seed_turn(run_id, "req-mal-0", conv_id, 0, final_action="NORMAL_COMPLETE")
    _seed_turn(run_id, "req-mal-1", conv_id, 1, final_action="SAFE_COMPLETE")
    get_obs().flush()

    from moralstack.ui import app as app_module

    real_fn = app_module.get_orchestration_events_for_request

    def _raising(run_id_arg, request_id_arg):
        if request_id_arg == "req-mal-1":
            raise RuntimeError("simulated malformed-event read failure")
        return real_fn(run_id_arg, request_id_arg)

    monkeypatch.setattr(app_module, "get_orchestration_events_for_request", _raising)

    body = _get_conversation_page(ui_client, conv_id)
    assert "Turn 0" in body
    assert "Turn 1" in body


# ---------------------------------------------------------------------------
# Ordering is independent of parent_request_id (the differential test)
# ---------------------------------------------------------------------------


def _seed_ordering_conversation(run_id: str, conv_id: str, parent_ids: dict[int, str | None]) -> list[str]:
    """Seed 3 turns (turn_index 0, 1, 2) in real chronological order, each
    with the given parent_request_id (may be self-referential, garbage, or a
    plausible-but-wrong non-self chain). Returns the request_ids in insertion
    (= turn_index/created_at) order."""
    req_ids = [f"req-order-{conv_id}-{i}" for i in range(3)]
    create_run(run_id, run_type="single", meta={})
    for i, rid in enumerate(req_ids):
        upsert_request(
            run_id,
            rid,
            prompt=f"turn {i}",
            domain="general",
            conversation_id=conv_id,
            turn_index=i,
            parent_request_id=parent_ids.get(i),
        )
        update_request_meta(run_id, rid, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1 * i})
        update_request_response(run_id, rid, f"response {i}")
        persist_decision_trace(
            run_id=run_id,
            request_id=rid,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "DELIBERATIVE_PATH", "risk_score": 0.1 * i}),
        )
    return req_ids


def test_spine_order_is_independent_of_parent_request_id(ui_client):
    """Three variants of parent_request_id must all render turn 0 -> 1 -> 2,
    the seq_pos (turn_index, created_at) order. Self-referential and garbage
    links cannot produce a visibly wrong order by themselves — only a
    plausible, acyclic non-self chain implying the reverse order (variant C)
    would expose an implementer who wired ordering through
    parent_request_id. A cyclic chain would not: an implementation could
    detect the cycle and fall back to row order."""
    run_id_a, conv_a = "run-order-a", "conv-order-a"
    req_ids_a = [f"req-order-{conv_a}-{i}" for i in range(3)]
    # Variant A: self-referential (the real production shape, 131/131).
    parents_a = {0: req_ids_a[0], 1: req_ids_a[1], 2: req_ids_a[2]}
    _seed_ordering_conversation(run_id_a, conv_a, parents_a)

    run_id_b, conv_b = "run-order-b", "conv-order-b"
    # Variant B: None / garbage.
    parents_b = {0: None, 1: "does-not-exist", 2: None}
    _seed_ordering_conversation(run_id_b, conv_b, parents_b)

    run_id_c, conv_c = "run-order-c", "conv-order-c"
    req_ids_c = [f"req-order-{conv_c}-{i}" for i in range(3)]
    # Variant C: plausible, ACYCLIC non-self chain implying the reverse order
    # (req2 is the root, then req1, then req0). If ordering were derived from
    # parent_request_id, the page would render Turn 2 -> Turn 1 -> Turn 0 and the
    # seq_pos assertion below would fail. A cycle would not prove this: an
    # implementation could detect it and fall back to row order.
    parents_c = {2: None, 1: req_ids_c[2], 0: req_ids_c[1]}
    _seed_ordering_conversation(run_id_c, conv_c, parents_c)

    get_obs().flush()

    for conv_id in (conv_a, conv_b, conv_c):
        body = _get_conversation_page(ui_client, conv_id)
        pos0 = body.index("Turn 0")
        pos1 = body.index("Turn 1")
        pos2 = body.index("Turn 2")
        assert pos0 < pos1 < pos2, f"{conv_id}: expected Turn 0 < Turn 1 < Turn 2, got positions {pos0},{pos1},{pos2}"


# ---------------------------------------------------------------------------
# Unit tests: call app.py helpers directly
# ---------------------------------------------------------------------------


def test_build_conversation_spine_node_input_reuses_anchor_info_verbatim(tmp_path, monkeypatch):
    dbp = str(tmp_path / "obs_unit1.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import (
        _build_conversation_spine_node,
        _build_final_revalidation_info,
        _build_input_anchor_info,
    )

    run_id, request_id, conv_id = "run-unit-1", "req-unit-1", "conv-unit-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hi", domain="general", conversation_id=conv_id, turn_index=1)
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE"})
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="orchestration",
        component="conversation",
        event_type=CONVERSATION_CONTEXT_ATTACHED,
        decision="attached",
        payload={"conversation_id": conv_id, "turn_index": 1, "conversation_state_provided": True},
    )
    get_obs().flush()

    from moralstack.ui.app import get_orchestration_events_for_request

    orchestration_events = get_orchestration_events_for_request(run_id, request_id)
    request_row = {"turn_index": 1, "prompt": "hi", "final_response": "", "pipeline_failure": False}
    node = _build_conversation_spine_node(
        request_row=request_row,
        meta={},
        traces=[],
        orchestration_events=orchestration_events,
        state=None,
        conversation_id=conv_id,
    )
    expected_anchor = _build_input_anchor_info(
        orchestration_events,
        conv_id,
        1,
        _build_final_revalidation_info(orchestration_events),
    )
    assert node["input"]["anchor"] == expected_anchor


def test_spine_anchor_honours_final_revalidation_info(tmp_path, monkeypatch):
    """Seeds a turn where final revalidation reports
    developer_contract_present=True while COMPLIANCE_LAYER_STARTED is missing —
    a shape that has 0 rows in production today (PROXY_FINAL_REVALIDATION_*
    is never emitted) but that the builder must still honour, since it passes
    the real _build_final_revalidation_info(orchestration_events) rather than
    None (the rejected shortcut)."""
    dbp = str(tmp_path / "obs_unit2.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.orchestration.orchestration_event_taxonomy import PROXY_FINAL_REVALIDATION_PASSED
    from moralstack.ui.app import _build_conversation_spine_node

    run_id, request_id, conv_id = "run-unit-2", "req-unit-2", "conv-unit-2"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hi", domain="general", conversation_id=conv_id, turn_index=0)
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE"})
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="PROXY",
        component="proxy",
        event_type=PROXY_FINAL_REVALIDATION_PASSED,
        decision="passed",
        payload={"developer_contract_present": True},
    )
    get_obs().flush()

    from moralstack.ui.app import get_orchestration_events_for_request

    orchestration_events = get_orchestration_events_for_request(run_id, request_id)
    request_row = {"turn_index": 0, "prompt": "hi", "final_response": "", "pipeline_failure": False}
    node = _build_conversation_spine_node(
        request_row=request_row,
        meta={},
        traces=[],
        orchestration_events=orchestration_events,
        state=None,
        conversation_id=conv_id,
    )
    assert node["input"]["anchor"]["developer_contract_present"] is True


def test_build_conversation_spine_node_decision_is_none_on_crashed_turn():
    from moralstack.ui.app import _build_conversation_spine_node

    request_row = {"turn_index": 0, "prompt": "hi", "final_response": "", "pipeline_failure": True}
    node = _build_conversation_spine_node(
        request_row=request_row,
        meta={"final_action": "NORMAL_COMPLETE"},
        traces=[],
        orchestration_events=[],
        state=None,
        conversation_id="conv-x",
    )
    assert node["decision"] is None


def test_build_conversation_spine_node_outcome_never_reads_final_response():
    from moralstack.ui.app import _build_conversation_spine_node

    request_row = {
        "turn_index": 0,
        "prompt": "hi",
        "final_response": "Sure! Here's exactly how to do that.",
        "pipeline_failure": False,
    }
    node = _build_conversation_spine_node(
        request_row=request_row,
        meta={"final_action": "REFUSE"},
        traces=[],
        orchestration_events=[],
        state=None,
        conversation_id="conv-x",
    )
    # The outcome's final_action comes from meta (a structured signal), and the
    # response text is only ever exposed as a preview field, never inspected.
    assert node["outcome"]["final_action"] == "REFUSE"
    assert node["outcome"]["response_preview"] == "Sure! Here's exactly how to do that."


def test_activated_signals_default_empty_list_not_none():
    from moralstack.ui.app import _build_final_decision_card

    traces = [{"stage": "FINAL", "trace_json": json.dumps({"final_action": "NORMAL_COMPLETE"})}]
    card = _build_final_decision_card(traces)
    assert card["activated_signals"] == []


def test_hard_violation_codes_default_empty_list_not_none():
    from moralstack.ui.app import _build_final_decision_card

    traces = [{"stage": "FINAL", "trace_json": json.dumps({"final_action": "NORMAL_COMPLETE"})}]
    card = _build_final_decision_card(traces)
    assert card["hard_violation_codes"] == []
