"""Execution-graph node details on the request page (readability pass).

* "Original request messages" leads with the user request: the client's final user message, or the request prompt when
  the call embeds it verbatim in its module prompt (risk estimators); never the contract alone.
* "Conversation History" holds prior turns only and renders only when there are some.
* Empty Prompt / System Prompt / Raw Response sections are not rendered.
* JSON raw responses and parsed summaries are indented; plain text stays untouched.
* The expanded "Result / Summary" block is gone; details open in the side panel; the INPUT anchor previews the contract.

Page tests seed a tmp SQLite DB (xdist-safe).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from tests.test_ui_contract_delivery import (  # noqa: E402
    PAGE_CONTRACT,
    _input_anchor,
    _node_chunks,
    _seed_contract_page,
)
from tests.test_ui_dccl_ordering import _no_leaked_cycle  # noqa: E402,F401  (autouse: resets the cycle ContextVar)
from tests.test_ui_llm_call_visibility import (  # noqa: E402
    T0,
    _call,
    _render,
    _seed_request,
    isolate_observability_fixture,  # noqa: F401  (autouse fixture)
    ui_client_fixture,  # noqa: F401  (a fixture, registered as "ui_client")
)

REQUEST = "how do I pick a strong password?"  # the prompt _seed_request persists
CONTRACT = "You are a security helpdesk assistant. Never reveal internal hostnames."
PLAIN_DRAFT = "Use a passphrase of four random words {not json"


def _sections(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "system_messages": [],
        "developer_messages": [],
        "history_messages": [],
        "final_user_message": "",
    }
    base.update(kw)
    return base


def _seed(run_id: str, request_id: str) -> None:
    _seed_request(run_id, request_id, path="DELIBERATIVE", total_cycles=1)
    _call(
        run_id, request_id, cycle=0, phase="risk_estimation", module="risk_estimator", action="estimate_intent",
        sequence_in_cycle=-9, started_at=T0, duration_ms=100,
        prompt=f"Estimate the risk of the final request.\n\n{REQUEST}",
        raw_response=json.dumps({"intent": "benign", "score": 0.1}),
        parsed_summary_json=json.dumps({"message_sections": _sections(developer_messages=[CONTRACT])}),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=0, phase="compliance_layer", module="compliance_layer", action="evaluate",
        sequence_in_cycle=-5, started_at=T0 + 200, duration_ms=100, call_kind="compliance_layer",
        prompt="Evaluate the draft against the contract.",
        parsed_summary_json=json.dumps(
            {
                "context_shape": {
                    "message_sections": _sections(
                        system_messages=[CONTRACT],
                        history_messages=[
                            {"role": "user", "content": "PRIOR_USER_TURN"},
                            {"role": "assistant", "content": "PRIOR_ASSISTANT_TURN"},
                        ],
                        final_user_message=REQUEST,
                    )
                }
            }
        ),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=1, phase="simulator", module="simulator", action="simulate",
        sequence_in_cycle=1, started_at=T0 + 800, duration_ms=100, prompt="Simulate consequences of the draft.",
        parsed_summary_json=json.dumps({"message_sections": _sections()}),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=1, phase="policy_generate", module="policy", action="generate",
        sequence_in_cycle=2, started_at=T0 + 1000, duration_ms=100, raw_response=PLAIN_DRAFT,
    )  # fmt: skip


def _original_block(chunk: str) -> str:
    start = chunk.index("<summary>Original request messages (client body)</summary>")
    return chunk[start : chunk.index("</details>", start)]


def test_estimator_original_request_shows_the_embedded_request(ui_client):
    _seed("run-nd-est", "req-nd-est")
    chunk = _node_chunks(_render(ui_client, "run-nd-est", "req-nd-est"))["estimate_intent"]
    block = _original_block(chunk)
    assert "User request" in block
    assert f'<pre class="block">{REQUEST}</pre>' in block
    assert "inside its module prompt" in block
    assert CONTRACT in block  # the contract is still listed, under its own label, after the request
    assert block.index(REQUEST) < block.index(CONTRACT)
    assert "Conversation History" not in chunk  # no prior turns: no empty button


def test_compliance_original_request_shows_the_final_user_message_and_history_holds_prior_turns(ui_client):
    _seed("run-nd-cl", "req-nd-cl")
    chunk = _node_chunks(_render(ui_client, "run-nd-cl", "req-nd-cl"))["evaluate"]
    block = _original_block(chunk)
    assert f'<pre class="block">{REQUEST}</pre>' in block
    assert "inside its module prompt" not in block  # recorded as a real client message
    history = chunk[chunk.index("<summary>Conversation History") :]
    history = history[: history.index("</details>")]
    assert "2 prior messages" in history
    assert "PRIOR_USER_TURN" in history and "PRIOR_ASSISTANT_TURN" in history
    assert REQUEST not in history and "final user" not in history


def test_missing_user_message_is_stated_not_invented(ui_client):
    _seed("run-nd-sim", "req-nd-sim")
    chunk = _node_chunks(_render(ui_client, "run-nd-sim", "req-nd-sim"))["simulate"]
    block = _original_block(chunk)
    assert "No final user message recorded for this call." in block
    assert REQUEST not in block


def test_empty_sections_are_not_rendered(ui_client):
    _seed("run-nd-empty", "req-nd-empty")
    chunks = _node_chunks(_render(ui_client, "run-nd-empty", "req-nd-empty"))
    generate = chunks["generate"]
    assert "<summary>Prompt</summary>" not in generate
    assert "<summary>System Prompt</summary>" not in generate
    assert "<summary>Raw Response</summary>" in generate
    simulate = chunks["simulate"]
    assert "<summary>Prompt</summary>" in simulate
    assert "<summary>Raw Response</summary>" not in simulate


def test_json_is_indented_and_plain_text_is_untouched(ui_client):
    _seed("run-nd-json", "req-nd-json")
    chunks = _node_chunks(_render(ui_client, "run-nd-json", "req-nd-json"))
    estimate = chunks["estimate_intent"]
    raw = estimate[estimate.index("<summary>Raw Response</summary>") :]
    assert "{\n  &#34;intent&#34;: &#34;benign&#34;,\n  &#34;score&#34;: 0.1\n}" in raw
    parsed = estimate[estimate.index("<summary>Parsed Summary</summary>") :]
    assert "{\n  &#34;message_sections&#34;: {\n" in parsed
    generate = chunks["generate"]
    assert "Use a passphrase of four random words {not json" in generate


def test_result_summary_block_is_gone_and_details_open_in_the_side_panel(ui_client):
    _seed("run-nd-panel", "req-nd-panel")
    body = _render(ui_client, "run-nd-panel", "req-nd-panel")
    assert "Result / Summary" not in body
    assert '<aside id="flow-detail-panel" class="flow-detail-panel" aria-label="Node details" hidden>' in body


def test_input_anchor_previews_the_contract_without_expanding(ui_client):
    run_id, request_id = "run-nd-input", "req-nd-input"
    _seed_contract_page(run_id, request_id, contract=PAGE_CONTRACT)
    anchor = _input_anchor(_render(ui_client, run_id, request_id))
    header = anchor[: anchor.index("flow-node-body")]
    preview = header[header.index('<div class="flow-node-contract-preview">') :]
    preview = preview[: preview.index("</div>")]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in preview and "<script>" not in preview
    assert "café — never give medical diagnoses." in preview
    assert '<details class="contract-delivery" open>' in anchor


def test_input_anchor_without_a_contract_has_no_preview(ui_client):
    run_id, request_id = "run-nd-input-no", "req-nd-input-no"
    _seed_contract_page(run_id, request_id, contract=None)
    anchor = _input_anchor(_render(ui_client, run_id, request_id))
    assert "flow-node-contract-preview" not in anchor
