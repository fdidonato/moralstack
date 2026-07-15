"""
UI tests for iteration 09: progressive disclosure of raw JSON on the
request-detail page (Decision Traces + Debug Events).

``request.html`` used to render the Decision Traces and Debug Events sections'
raw JSON as a bare ``<pre>`` directly under the ``<h2>``, always fully
expanded — the only two raw blocks on the page not wrapped in
``<details><summary>...</summary>`` like every sibling (Raw Response, Parsed
Summary, Original System/Developer Messages, Conversation History). Debug
Events additionally carried no scannable label, just a timestamp + raw JSON.

Covers:
  * the new ``event_label`` Jinja filter (``moralstack/ui/app.py``), exercised
    through rendered output since it is a closure registered on the template
    environment, not a module-level function — component+message,
    component-only, message-only, event_type-only (no message), and the
    "debug event" fallback for empty/malformed/non-dict payloads.
  * the rendered request-detail page: both sections are now wrapped in
    ``<details>``, the decision-trace ``<summary>`` still carries
    ``stage (seq N)``, the raw ``trace_json`` / ``payload_json`` bytes are
    still present (reachability preserved), and neither section renders as a
    bare top-level ``<pre>`` anymore.
"""

from __future__ import annotations

import html
import json
import sqlite3
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_debug_event,
    persist_decision_trace,
)
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    update_request_response,
    upsert_request,
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
    dbp = str(tmp_path / "ui_progressive_disclosure.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    return client, dbp


def _seed_minimal_request(run_id: str, request_id: str) -> None:
    """A request with just enough persisted state to render request_detail."""
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05})
    update_request_response(run_id, request_id, "Hi there!")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "total_cycles": 0}),
    )


def _insert_raw_debug_event(dbp: str, run_id: str, request_id: str, payload_json: str) -> None:
    """Insert a debug_events row with an arbitrary raw payload_json string.

    Bypasses ``persist_debug_event`` (which only accepts a dict and always
    json-dumps it) so malformed/non-dict payloads reachable only via direct
    DB corruption or pre-migration data can be exercised too.
    """
    conn = sqlite3.connect(dbp)
    try:
        conn.execute(
            "INSERT INTO debug_events (run_id, request_id, created_at, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, request_id, int(time.time() * 1000), payload_json),
        )
        conn.commit()
    finally:
        conn.close()


def _get(ui_client_tuple, run_id: str, request_id: str):
    client, _dbp = ui_client_tuple
    token = _make_session_token(client)
    resp = client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    return resp.text


# ---------------------------------------------------------------------------
# (a) event_label — component + message
# ---------------------------------------------------------------------------


def test_event_label_component_and_message(ui_client):
    run_id, request_id = "run-el-1", "req-el-1"
    _seed_minimal_request(run_id, request_id)
    persist_debug_event(
        run_id=run_id,
        request_id=request_id,
        payload={"component": "orchestrator", "message": "branch risk_policy vs deliberative"},
    )
    get_obs().flush()

    body = _get(ui_client, run_id, request_id)
    assert '<summary>orchestrator · branch risk_policy vs deliberative <span class="muted ts">' in body


# ---------------------------------------------------------------------------
# (b) event_label — component only
# ---------------------------------------------------------------------------


def test_event_label_component_only(ui_client):
    run_id, request_id = "run-el-2", "req-el-2"
    _seed_minimal_request(run_id, request_id)
    persist_debug_event(run_id=run_id, request_id=request_id, payload={"component": "orchestrator"})
    get_obs().flush()

    body = _get(ui_client, run_id, request_id)
    assert '<summary>orchestrator <span class="muted ts">' in body


# ---------------------------------------------------------------------------
# (c) event_label — message only (no component)
# ---------------------------------------------------------------------------


def test_event_label_message_only(ui_client):
    run_id, request_id = "run-el-3", "req-el-3"
    _seed_minimal_request(run_id, request_id)
    persist_debug_event(run_id=run_id, request_id=request_id, payload={"message": "custom message text"})
    get_obs().flush()

    body = _get(ui_client, run_id, request_id)
    assert '<summary>custom message text <span class="muted ts">' in body


# ---------------------------------------------------------------------------
# (d) event_label — event_type only (no message, no component)
# ---------------------------------------------------------------------------


def test_event_label_falls_back_to_event_type_when_no_message(ui_client):
    run_id, request_id = "run-el-4", "req-el-4"
    _seed_minimal_request(run_id, request_id)
    persist_debug_event(run_id=run_id, request_id=request_id, payload={"event_type": "lifecycle.start"})
    get_obs().flush()

    body = _get(ui_client, run_id, request_id)
    assert '<summary>lifecycle.start <span class="muted ts">' in body


# ---------------------------------------------------------------------------
# (e) event_label — "debug event" fallback for empty / malformed / non-dict payload
# ---------------------------------------------------------------------------


def test_event_label_falls_back_to_debug_event_for_empty_malformed_or_non_dict_payload(ui_client):
    client, dbp = ui_client
    run_id, request_id = "run-el-5", "req-el-5"
    _seed_minimal_request(run_id, request_id)
    get_obs().flush()

    # Empty dict, malformed non-JSON, and non-dict JSON (a list) — all fall back.
    _insert_raw_debug_event(dbp, run_id, request_id, "{}")
    _insert_raw_debug_event(dbp, run_id, request_id, "not valid json{")
    _insert_raw_debug_event(dbp, run_id, request_id, "[1, 2, 3]")

    body = _get((client, dbp), run_id, request_id)
    assert body.count('<summary>debug event <span class="muted ts">') == 3
    # Raw bytes are still reachable underneath, unchanged.
    assert '<pre class="block debug-block">{}</pre>' in body
    assert '<pre class="block debug-block">not valid json{</pre>' in body
    assert '<pre class="block debug-block">[1, 2, 3]</pre>' in body


# ---------------------------------------------------------------------------
# (f) structural: both sections wrapped in <details>, raw bytes reachable
# ---------------------------------------------------------------------------


def test_decision_traces_and_debug_events_collapsed_behind_details(ui_client):
    run_id, request_id = "run-struct-1", "req-struct-1"
    _seed_minimal_request(run_id, request_id)
    persist_debug_event(
        run_id=run_id,
        request_id=request_id,
        payload={"component": "orchestrator", "message": "branch risk_policy vs deliberative"},
    )
    get_obs().flush()

    body = _get(ui_client, run_id, request_id)
    # Jinja autoescapes the raw JSON inside <pre> (" -> &#34;); unescape to assert
    # the underlying trace/payload bytes are still reachable.
    body_text = html.unescape(body)

    # Decision Traces: <summary> carries stage + seq, raw trace_json still present.
    assert "<h2>Decision Traces" in body
    trace_summary_start = body.index('<summary>FINAL <span class="muted">(seq 1)</span></summary>')
    assert trace_summary_start > 0
    assert '"final_action": "NORMAL_COMPLETE"' in body_text
    assert '"path": "FAST_PATH"' in body_text

    # Debug Events: <summary> carries the component · message label, raw payload still present.
    assert "<h2>Debug Events" in body
    assert '<summary>orchestrator · branch risk_policy vs deliberative <span class="muted ts">' in body
    assert '"component": "orchestrator"' in body_text
    assert '"message": "branch risk_policy vs deliberative"' in body_text

    # Neither section renders its <pre> as a bare top-level block anymore: every
    # trace/debug <pre> is now nested inside a <details> element.
    traces_section = body[body.index("<h2>Decision Traces") : body.index("<h2>Debug Events")]
    assert "<details>" in traces_section
    assert traces_section.index("<details>") < traces_section.index('<pre class="block json-block">')

    debug_section = body[body.index("<h2>Debug Events") :]
    debug_section = debug_section[: debug_section.index("<h2>", 1)] if "<h2>" in debug_section[1:] else debug_section
    assert "<details>" in debug_section
    assert debug_section.index("<details>") < debug_section.index('<pre class="block debug-block">')
