"""Developer-contract delivery on the request page.

Every node that actually received the developer contract shows it (channel, coverage, identity check); a node that did
not receive it shows nothing and its io annotations do not claim it. The resolver (``moralstack/ui/contract_delivery.py``)
is data-driven: it decodes the wire record persisted with each ``llm_calls`` row (``observability/wire.py``) and
verifies the contract identity with the verdict event's ``contract_hash``.

Unit tests build wire records with the real encoder; hand-written records are used only for malformed cases.
Page tests seed a tmp SQLite DB (xdist-safe). No literal tier numbers are asserted.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

pytest.importorskip("fastapi")

from moralstack.observability.emit_helpers import persist_orchestration_event  # noqa: E402
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.wire import encode_wire, encode_wire_calls  # noqa: E402
from moralstack.orchestration.contract import DeveloperContract  # noqa: E402
from moralstack.orchestration.orchestration_event_taxonomy import (  # noqa: E402
    COMPLIANCE_LAYER_STARTED,
    COMPLIANCE_LAYER_VERDICT_MATCH,
)
from moralstack.ui.app import _attach_contract_delivery, _build_module_io_annotations  # noqa: E402
from moralstack.ui.contract_delivery import (  # noqa: E402
    MIN_PARTIAL_CONTRACT_CHARS,
    contract_delivery_for_call,
    resolve_request_contract,
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

CONTRACT = "You are a kids education assistant. Never give medical diagnoses. Stay on topic.  "  # 83 chars
LONG_CONTRACT = ("0123456789abcdefghij" * 200) + "END"  # 4003 chars, > every truncation limit


def _hash(text: str, mode: str = "opaque") -> str:
    return DeveloperContract.from_text(text, mode).contract_hash  # type: ignore[arg-type]


def _verdict_event(contract_text: str, mode: str = "opaque") -> dict[str, Any]:
    return {
        "event_type": COMPLIANCE_LAYER_VERDICT_MATCH,
        "payload_json": json.dumps({"contract_hash": _hash(contract_text, mode)}),
    }


def _wire(messages: list[dict[str, str]], *, prompt: str = "", system: str = "", branch: str = "messages") -> dict:
    record = encode_wire(messages, prompt_col=prompt, system_prompt_col=system, branch=branch)
    return json.loads(json.dumps(record, ensure_ascii=False))  # as persisted


def _row(
    module: str = "risk_estimator",
    action: str = "estimate_intent",
    *,
    messages: list[dict[str, str]] | None = None,
    prompt: str = "",
    system_prompt: str = "",
    sections: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    parsed_json: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = dict(summary or {})
    if messages is not None:
        body["wire"] = _wire(messages, prompt=prompt, system=system_prompt)
    if sections is not None:
        body["message_sections"] = sections
    call: dict[str, Any] = {
        "id": 1,
        "module": module,
        "action": action,
        "phase": "x",
        "cycle": 0,
        "prompt": prompt,
        "system_prompt": system_prompt,
        "parsed_summary_json": json.dumps(body) if body else None,
        "parsed_json": parsed_json if isinstance(parsed_json, (str, type(None))) else json.dumps(parsed_json),
    }
    call.update(extra)
    return call


def _canonical(text: str = CONTRACT, *, system_sections: bool = True) -> dict[str, Any]:
    """The request contract as the resolver reports it (verified by hash)."""
    sections = {"system_messages": [text], "developer_messages": []} if system_sections else {"developer_messages": [text]}
    rows = [_row("compliance_layer", "evaluate", sections=sections)]
    contract = resolve_request_contract(rows, [_verdict_event(text)])
    assert contract is not None
    return contract


# --------------------------------------------------------------------------------------
# resolve_request_contract
# --------------------------------------------------------------------------------------


def test_contract_is_verified_by_hash_and_stripped_for_display():
    # The hash covers the raw (unstripped) text: CONTRACT carries trailing spaces.
    rows = [_row("compliance_layer", "evaluate", sections={"system_messages": [CONTRACT], "developer_messages": []})]
    contract = resolve_request_contract(rows, [_verdict_event(CONTRACT)])
    assert contract is not None
    assert contract["text"] == CONTRACT.strip()
    assert contract["verified"] is True
    assert contract["client_role_system"] is True


@pytest.mark.parametrize("mode", ["opaque", "structured"])
def test_hash_match_works_for_stripped_text_and_both_modes(mode):
    padded = "\n  " + CONTRACT.strip() + "\n"
    rows = [_row("risk_estimator", sections={"system_messages": [], "developer_messages": [padded]})]
    for hashed in (padded, padded.strip()):
        contract = resolve_request_contract(rows, [_verdict_event(hashed, mode)])
        assert contract is not None and contract["verified"] is True
        assert contract["text"] == CONTRACT.strip()
        assert contract["client_role_system"] is False


@pytest.mark.parametrize("contract_last", [True, False])
def test_hash_selects_the_contract_message_regardless_of_position(contract_last):
    old = "Old rules from an earlier turn."
    messages = [old, CONTRACT] if contract_last else [CONTRACT, old]
    rows = [_row("compliance_layer", "evaluate", sections={"system_messages": messages})]
    contract = resolve_request_contract(rows, [_verdict_event(CONTRACT)])
    assert contract is not None and contract["text"] == CONTRACT.strip() and contract["verified"] is True


def test_context_shape_message_sections_are_candidates_too():
    rows = [
        _row(
            "compliance_layer", "evaluate", summary={"context_shape": {"message_sections": {"system_messages": [CONTRACT]}}}
        )
    ]
    contract = resolve_request_contract(rows, [_verdict_event(CONTRACT)])
    assert contract is not None and contract["verified"] is True


def test_unverified_fallback_needs_exactly_one_distinct_risk_developer_message():
    one = [_row("risk_estimator", sections={"developer_messages": [CONTRACT, CONTRACT]})]
    contract = resolve_request_contract(one, [])
    assert contract is not None and contract["verified"] is False and contract["text"] == CONTRACT.strip()
    two = [_row("risk_estimator", sections={"developer_messages": [CONTRACT, "another contract entirely"]})]
    assert resolve_request_contract(two, []) is None


def test_hash_without_matching_text_and_no_fallback_claims_nothing():
    rows = [_row("compliance_layer", "evaluate", sections={"system_messages": ["not the contract"]})]
    assert resolve_request_contract(rows, [_verdict_event(CONTRACT)]) is None


def test_no_contract_anywhere_resolves_to_none():
    rows = [_row("risk_estimator", sections={"system_messages": [], "developer_messages": []}), _row("policy")]
    assert resolve_request_contract(rows, []) is None
    assert resolve_request_contract([], []) is None


def test_no_message_sections_means_no_claim_even_with_a_wire():
    contract_less = [_row("critic", "critique", messages=[{"role": "developer", "content": CONTRACT}])]
    assert resolve_request_contract(contract_less, [_verdict_event(CONTRACT)]) is None


# --------------------------------------------------------------------------------------
# contract_delivery_for_call: channels
# --------------------------------------------------------------------------------------


def test_native_developer_message():
    call = _row(messages=[{"role": "developer", "content": CONTRACT}, {"role": "user", "content": "hello"}])
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None
    assert delivery["channel"] == "native_developer"
    assert delivery["label"] == "native developer message (role=developer)"
    assert delivery["coverage"] == f"full ({len(CONTRACT.strip())} chars)"
    assert delivery["truncated"] is False
    assert delivery["excerpt"] == CONTRACT.strip()
    assert delivery["verified"] is True


def test_native_system_message_notes_the_client_role():
    call = _row(
        "compliance_layer", "evaluate", messages=[{"role": "system", "content": CONTRACT}, {"role": "user", "content": "q"}]
    )
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None
    assert delivery["channel"] == "native_system"
    assert delivery["label"] == "native message (role=system) — sent by the client as role=system"
    # Same wire, but the client did not send the contract as role=system: no client note.
    delivery = contract_delivery_for_call(call, _canonical(system_sections=False))
    assert delivery is not None and delivery["label"] == "native message (role=system)"


def test_embedded_in_the_system_prompt():
    system = f"You are a drafting assistant.\n\nCONTRACT:\n{CONTRACT.strip()}\n\nBe brief."
    call = _row(
        "upstream_speculative",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": "q"}],
        system_prompt=system,
    )
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None and delivery["channel"] == "embedded_system"
    assert delivery["label"] == "embedded in the system prompt"


def test_embedded_in_the_user_prompt():
    prompt = f"Pick the domains.\nCONTRACT:\n{CONTRACT.strip()}\nREQUEST: how do I pick a password?"
    call = _row("constitution_retriever", "domain_prefilter", messages=[{"role": "user", "content": prompt}], prompt=prompt)
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None and delivery["channel"] == "embedded_user"
    assert delivery["label"] == "embedded in the user prompt"
    assert delivery["coverage"].startswith("full (")


def test_native_wins_over_embedded_in_the_same_call():
    system = f"prefix {CONTRACT.strip()} suffix"
    call = _row(
        messages=[
            {"role": "system", "content": system},
            {"role": "developer", "content": CONTRACT},
            {"role": "user", "content": f"quote: {CONTRACT.strip()}"},
        ]
    )
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None and delivery["channel"] == "native_developer"


@pytest.mark.parametrize("received", [1499, 1500, 1501, 2999, 3000, 3001])
def test_truncated_embedded_contract_reports_first_n_of_total(received):
    prompt = f"CONTRACT:\n{LONG_CONTRACT[:received]}\nREQUEST: q"
    call = _row(
        "constitution_retriever", "enhanced_domain_agent", messages=[{"role": "user", "content": prompt}], prompt=prompt
    )
    delivery = contract_delivery_for_call(call, _canonical(LONG_CONTRACT))
    assert delivery is not None
    assert delivery["chars_received"] == received
    assert delivery["chars_total"] == len(LONG_CONTRACT)
    assert delivery["truncated"] is True
    assert delivery["coverage"] == f"truncated: first {received} of {len(LONG_CONTRACT)} chars"
    assert delivery["excerpt"] == LONG_CONTRACT[:received]


def test_native_truncated_prefix_is_recognised_above_the_minimum():
    body = LONG_CONTRACT[:1500]
    call = _row("critic", "critique", messages=[{"role": "developer", "content": body}])
    delivery = contract_delivery_for_call(call, _canonical(LONG_CONTRACT))
    assert delivery is not None and delivery["channel"] == "native_developer" and delivery["truncated"] is True


def test_short_fragment_of_the_contract_is_not_a_delivery():
    # A 500-char excerpt (smaller than MIN) of a long contract is e.g. a rule-action quote, not the contract.
    assert MIN_PARTIAL_CONTRACT_CHARS > 500
    fragment = LONG_CONTRACT[:500]
    for role in ("developer", "system", "user"):
        call = _row(messages=[{"role": role, "content": f"see: {fragment}"}])
        assert contract_delivery_for_call(call, _canonical(LONG_CONTRACT)) is None
    call = _row(messages=[{"role": "developer", "content": fragment}])
    assert contract_delivery_for_call(call, _canonical(LONG_CONTRACT)) is None


def test_contract_shorter_than_the_minimum_needs_full_containment():
    assert len(CONTRACT.strip()) < MIN_PARTIAL_CONTRACT_CHARS
    partial = CONTRACT.strip()[:60]
    call = _row(messages=[{"role": "user", "content": f"quote: {partial}"}])
    assert contract_delivery_for_call(call, _canonical()) is None
    call = _row(messages=[{"role": "user", "content": f"quote: {CONTRACT.strip()}"}])
    assert contract_delivery_for_call(call, _canonical()) is not None


def test_residual_short_contract_quoted_in_a_draft_reads_as_embedded_in_the_user_prompt():
    """Documented residual (plan R3): a short contract quoted in full inside the user text is literally embedded."""
    draft = f"Sure. As my instructions say: {CONTRACT.strip()} Anyway, here is the answer."
    call = _row("critic", "critique", messages=[{"role": "user", "content": draft}], prompt=draft)
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None and delivery["label"] == "embedded in the user prompt"


def test_rows_without_a_wire_fall_back_to_the_columns_then_to_recorded_sections():
    contract = _canonical()
    system = f"Be helpful. {CONTRACT.strip()}"
    by_columns = _row("policy", system_prompt=system, prompt="q")
    delivery = contract_delivery_for_call(by_columns, contract)
    assert delivery is not None and delivery["channel"] == "embedded_system" and delivery["source"] == "columns"

    by_sections = _row("simulator", prompt="q", sections={"developer_messages": [CONTRACT]})
    delivery = contract_delivery_for_call(by_sections, contract)
    assert delivery is not None and delivery["channel"] == "recorded_sections"
    assert delivery["label"] == "recorded in this call's message sections (role sent not recorded)"

    nothing = _row("policy", system_prompt="Be helpful.", prompt="q", sections={"developer_messages": []})
    assert contract_delivery_for_call(nothing, contract) is None


def test_a_wire_without_the_contract_does_not_fall_back_to_sections():
    # The wire is the exact message list: a row whose wire carries no contract did not receive it, whatever the
    # recorded request sections say.
    call = _row(
        "compliance_layer", "evaluate", messages=[{"role": "user", "content": "q"}], sections={"system_messages": [CONTRACT]}
    )
    assert contract_delivery_for_call(call, _canonical()) is None


# --------------------------------------------------------------------------------------
# contract_delivery_for_call: who must not claim the contract
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["safety_override", "draft_revalidate"])
def test_deny_listed_compliance_rows_never_claim_the_contract_even_when_the_text_matches(action):
    call = _row("compliance_layer", action, messages=[{"role": "user", "content": f"excerpt: {CONTRACT.strip()}"}])
    assert contract_delivery_for_call(call, _canonical()) is None


def test_hydrated_speculative_reuse_and_synthetic_rows_are_skipped():
    wire = [{"role": "developer", "content": CONTRACT}]
    contract = _canonical()
    assert contract_delivery_for_call(_row(messages=wire, _hydrated_from_speculative_generate=True), contract) is None
    assert contract_delivery_for_call(_row(messages=wire, is_synthetic=True), contract) is None
    assert contract_delivery_for_call(_row(messages=wire), contract) is not None  # control


def test_no_request_contract_means_no_delivery():
    assert contract_delivery_for_call(_row(messages=[{"role": "developer", "content": CONTRACT}]), None) is None


# --------------------------------------------------------------------------------------
# critic, retry_failed, aggregated rows
# --------------------------------------------------------------------------------------


def test_critic_contract_is_read_from_parsed_json_and_the_summary_stays_untouched():
    summary_text = "Violations: 0, Guidance: N/A"
    wire = {"wire": _wire([{"role": "developer", "content": CONTRACT}, {"role": "user", "content": "draft"}])}
    call = _row("critic", "critique", parsed_json=wire)
    call["parsed_summary_json"] = summary_text
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None and delivery["channel"] == "native_developer"
    assert call["parsed_summary_json"] == summary_text


def test_each_critic_row_is_resolved_from_its_own_wire():
    with_contract = _row("critic", "critique", parsed_json={"wire": _wire([{"role": "developer", "content": CONTRACT}])})
    without = _row("critic", "critique", parsed_json={"wire": _wire([{"role": "developer", "content": "no contract here"}])})
    contract = _canonical()
    assert contract_delivery_for_call(with_contract, contract) is not None
    assert contract_delivery_for_call(without, contract) is None


def test_retry_failed_row_inline_wire_resolves():
    call = _row(
        "critic",
        "critique",
        summary={"wire": _wire([{"role": "developer", "content": CONTRACT}], branch="messages")},
        call_outcome="retry_failed",
    )
    assert contract_delivery_for_call(call, _canonical()) is not None


def test_aggregated_wire_calls_report_how_many_calls_received_the_contract():
    calls = [
        ([{"role": "developer", "content": CONTRACT}, {"role": "user", "content": f"p{i}"}], "messages") for i in range(2)
    ] + [([{"role": "developer", "content": "different"}, {"role": "user", "content": "p2"}], "messages")]
    summary = encode_wire_calls(calls, prompt_col="", system_prompt_col="")
    call = _row("perspectives", "evaluate")
    call["parsed_summary_json"] = json.dumps(json.loads(json.dumps(summary)))
    delivery = contract_delivery_for_call(call, _canonical())
    assert delivery is not None
    assert (delivery["calls_with_contract"], delivery["calls_total"]) == (2, 3)
    assert delivery["channel"] == "native_developer"


def test_single_call_rows_report_one_of_one():
    delivery = contract_delivery_for_call(_row(messages=[{"role": "developer", "content": CONTRACT}]), _canonical())
    assert delivery is not None and (delivery["calls_with_contract"], delivery["calls_total"]) == (1, 1)


# --------------------------------------------------------------------------------------
# robustness: malformed data never raises and never claims
# --------------------------------------------------------------------------------------

_BAD_WIRES: list[Any] = [
    {"v": 1, "messages": [{"role": "developer", "s": 7}], "strings": ["x"]},  # index out of range
    {"v": 1},  # no messages
    {"v": 1, "messages": [{"role": "developer", "ref": "nope"}]},  # unknown ref
    {"v": 1, "messages": [{"role": "developer", "ref": "prompt"}]},  # ref-only on an empty column: decodes to ""
    {"v": 2, "messages": []},  # unsupported version
    "not a dict",
    [],
    None,
    {"wire_calls": "oops", "wire_strings": 3},
]


@pytest.mark.parametrize("bad", _BAD_WIRES)
def test_malformed_wire_claims_nothing(bad):
    call = _row("critic", "critique", summary={"wire": bad}, sections={"developer_messages": [CONTRACT]})
    assert contract_delivery_for_call(call, _canonical()) is None
    aggregated = _row("perspectives", summary={"wire_calls": bad, "wire_strings": bad})
    assert contract_delivery_for_call(aggregated, _canonical()) is None


@pytest.mark.parametrize(
    "call",
    [
        {"module": "critic", "action": "critique", "parsed_summary_json": "{truncated json", "parsed_json": "{also"},
        {"module": "critic", "action": "critique", "parsed_summary_json": None, "parsed_json": json.dumps(["a list"])},
        {"module": "critic", "action": "critique", "parsed_summary_json": json.dumps({"message_sections": "a string"})},
        {"module": "risk_estimator", "cycle": "one", "prompt": None, "system_prompt": 5, "parsed_summary_json": 12},
        {"module": None, "action": None},
        {},
    ],
)
def test_garbage_rows_neither_raise_nor_claim(call):
    contract = _canonical()
    assert contract_delivery_for_call(call, contract) is None
    assert (
        resolve_request_contract([call], [{"event_type": COMPLIANCE_LAYER_VERDICT_MATCH, "payload_json": "{not json"}])
        is None
    )
    assert resolve_request_contract([call], [{}, {"payload_json": 3}]) is None


def test_attach_never_raises_into_the_page():
    rows: list[Any] = [None, "garbage", _row("risk_estimator", messages=[{"role": "developer", "content": CONTRACT}])]
    rows[2]["io_annotations"] = {"inputs": [], "outputs": []}
    _attach_contract_delivery(rows, [])  # type: ignore[arg-type]  # no verdict hash, no sections: nothing to claim


# --------------------------------------------------------------------------------------
# io annotations (static, P2-h)
# --------------------------------------------------------------------------------------


def _labels(call: dict[str, Any]) -> list[str]:
    return [i["label"] for i in _build_module_io_annotations(call)["inputs"]]


def test_safety_override_io_describes_the_rule_action_not_the_contract():
    io = _build_module_io_annotations({"module": "compliance_layer", "action": "safety_override", "cycle": 0})
    assert [(i["label"], i["source"]) for i in io["inputs"]] == [
        ("action_payload", "matched rule action (speculative draft as LLM-path fallback)")
    ]


def test_draft_revalidate_io_is_draft_plus_rule_action():
    io = _build_module_io_annotations({"module": "compliance_layer", "action": "draft_revalidate", "cycle": 0})
    assert [(i["label"], i["source"]) for i in io["inputs"]] == [
        ("draft", "policy (compliance-regenerate)"),
        ("rule_action", "matched rule"),
    ]


def test_evaluate_io_keeps_contract_draft_prompt():
    assert _labels({"module": "compliance_layer", "action": "evaluate", "cycle": 0}) == [
        "developer_contract",
        "draft",
        "prompt",
    ]


def test_compliance_regenerate_policy_row_io_is_rule_action_plus_prompt():
    io = _build_module_io_annotations({"module": "policy", "action": "generate (compliance-regenerate)", "cycle": 0})
    assert [(i["label"], i["source"]) for i in io["inputs"]] == [("rule_action", "dccl matched rule"), ("prompt", "user")]


def test_no_contract_io_label_sets_are_unchanged_for_the_other_modules():
    """R5 snapshot of the label sets before this change: the resolver only ever adds a developer_contract row."""
    expected = {
        ("risk_estimator", "estimate_intent", 0): ["prompt"],
        ("policy", "generate (fast_path)", 0): ["risk", "principles"],
        ("policy", "generate", 1): ["revision_guidance", "risk"],
        ("critic", "critique", 1): ["draft", "principles", "risk_context"],
        ("simulator", "simulate", 1): ["draft", "risk_context"],
        ("perspectives", "evaluate", 1): ["draft", "risk_context"],
        ("hindsight", "evaluate", 1): ["draft", "consequences"],
        ("constitution_retriever", "domain_prefilter", 0): ["domain"],
    }
    for (module, action, cycle), labels in expected.items():
        assert _labels({"module": module, "action": action, "cycle": cycle}) == labels, (module, action)


# --------------------------------------------------------------------------------------
# integration helper
# --------------------------------------------------------------------------------------


def _annotated(call: dict[str, Any]) -> dict[str, Any]:
    call["io_annotations"] = _build_module_io_annotations(call)
    return call


def test_attach_sets_delivery_and_adds_or_updates_the_developer_contract_io_row():
    risk = _annotated(
        _row(
            "risk_estimator",
            messages=[{"role": "developer", "content": CONTRACT}],
            sections={"developer_messages": [CONTRACT]},
        )
    )
    evaluate = _annotated(
        _row(
            "compliance_layer",
            "evaluate",
            messages=[{"role": "system", "content": CONTRACT}],
            sections={"system_messages": [CONTRACT]},
        )
    )
    override = _annotated(
        _row("compliance_layer", "safety_override", messages=[{"role": "user", "content": CONTRACT.strip()}])
    )
    refusal = _annotated(_row("orchestration", "refuse (fast_path)", messages=[{"role": "user", "content": "q"}]))
    rows = [risk, evaluate, override, refusal]
    _attach_contract_delivery(rows, [_verdict_event(CONTRACT)])

    assert risk["contract_delivery"]["channel"] == "native_developer"
    assert risk["io_annotations"]["inputs"][0] == {
        "label": "developer_contract",
        "source": risk["contract_delivery"]["short_label"],
    }
    assert [i["label"] for i in risk["io_annotations"]["inputs"]].count("developer_contract") == 1
    # evaluate already had a developer_contract row: its source is refined, not duplicated.
    labels = [i["label"] for i in evaluate["io_annotations"]["inputs"]]
    assert labels.count("developer_contract") == 1
    assert evaluate["contract_delivery"]["channel"] == "native_system"
    assert (
        dict((i["label"], i["source"]) for i in evaluate["io_annotations"]["inputs"])["developer_contract"]
        == evaluate["contract_delivery"]["short_label"]
    )
    assert override["contract_delivery"] is None and "developer_contract" not in [
        i["label"] for i in override["io_annotations"]["inputs"]
    ]
    assert refusal["contract_delivery"] is None


def test_attach_without_a_contract_changes_nothing():
    risk = _annotated(
        _row("risk_estimator", messages=[{"role": "user", "content": "q"}], sections={"developer_messages": []})
    )
    before = json.dumps(risk["io_annotations"], sort_keys=True)
    assert _attach_contract_delivery([risk], []) is None
    assert json.dumps(risk["io_annotations"], sort_keys=True) == before
    assert not risk.get("contract_delivery")


def test_attach_returns_the_resolved_request_contract():
    risk = _annotated(
        _row(
            "risk_estimator",
            messages=[{"role": "developer", "content": CONTRACT}],
            sections={"developer_messages": [CONTRACT]},
        )
    )
    contract = _attach_contract_delivery([risk], [_verdict_event(CONTRACT)])
    assert contract == {"text": CONTRACT.strip(), "verified": True, "client_role_system": False}


# --------------------------------------------------------------------------------------
# page level
# --------------------------------------------------------------------------------------

PAGE_CONTRACT = "Kids app contract <script>alert(1)</script> {{ 7 * 7 }} café — never give medical diagnoses."


def _node_chunks(body: str) -> dict[str, str]:
    """Graph node HTML by action (the ``badge-type`` text), from the execution graph only."""
    start = body.index("<h2>Execution graph")
    end = body.index("flow-node--output", start)
    graph = body[start:end]
    parts = re.split(r'(?=<div class="flow-node")', graph)[1:]
    chunks: dict[str, str] = {}
    for part in parts:
        m = re.search(r'<span class="badge badge-type">(.*?)</span>', part, re.S)
        if m:
            chunks[" ".join(m.group(1).split())] = part
    return chunks


def _seed_contract_page(run_id: str, request_id: str, *, contract: str | None) -> None:
    _seed_request(run_id, request_id, path="FAST_PATH", total_cycles=1)
    marker = contract or ""

    def msgs_dev(extra_user: str = "q") -> str:
        out = [{"role": "user", "content": extra_user}]
        if marker:
            out.insert(0, {"role": "developer", "content": marker})
        return json.dumps(
            {
                "wire": _wire(out),
                "message_sections": {"system_messages": [], "developer_messages": [marker] if marker else []},
            }
        )

    _call(
        run_id, request_id, cycle=0, phase="risk_estimation", module="risk_estimator", action="estimate_intent",
        sequence_in_cycle=-9, started_at=T0, duration_ms=100, parsed_summary_json=msgs_dev(),
    )  # fmt: skip
    prefilter_prompt = (f"CONTRACT:\n{marker}\n" if marker else "") + "REQUEST: q"
    _call(
        run_id, request_id, cycle=0, phase="constitution_retrieval", module="constitution_retriever",
        action="domain_prefilter", sequence_in_cycle=-10, started_at=T0 - 600, duration_ms=100, prompt=prefilter_prompt,
        parsed_summary_json=json.dumps(
            {"wire": _wire([{"role": "user", "content": prefilter_prompt}], prompt=prefilter_prompt)}
        ),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=0, phase="compliance_layer", module="compliance_layer", action="evaluate",
        sequence_in_cycle=-5, started_at=T0 + 200, duration_ms=100, call_kind="compliance_layer",
        parsed_summary_json=json.dumps(
            {
                "wire": _wire(
                    ([{"role": "system", "content": marker}] if marker else []) + [{"role": "user", "content": "q"}]
                ),
                "message_sections": {"system_messages": [marker] if marker else [], "developer_messages": []},
            }
        ),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=0, phase="compliance_layer", module="compliance_layer", action="safety_override",
        sequence_in_cycle=-6, started_at=T0 + 400, duration_ms=100, call_kind="compliance_layer",
        parsed_summary_json=json.dumps({"wire": _wire([{"role": "user", "content": f"excerpt: {marker[:200]}"}])}),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=1, phase="critic", module="critic", action="critique",
        sequence_in_cycle=2, started_at=T0 + 800, duration_ms=100,
        parsed_summary_json="Violations: 0, Guidance: N/A",
        parsed_json=json.dumps(
            {
                "wire": _wire(
                    ([{"role": "developer", "content": marker}] if marker else [])
                    + [{"role": "user", "content": "draft"}]
                )
            }
        ),
    )  # fmt: skip
    _call(
        run_id, request_id, cycle=0, phase="refusal", module="orchestration", action="refuse (fast_path)",
        sequence_in_cycle=6, started_at=T0 + 600, duration_ms=100,
        parsed_summary_json=json.dumps({"wire": _wire([{"role": "user", "content": "refuse q"}])}),
    )  # fmt: skip
    if marker:
        persist_orchestration_event(
            run_id=run_id, request_id=request_id, cycle=0, stage="COMPLIANCE", component="compliance_layer",
            event_type=COMPLIANCE_LAYER_VERDICT_MATCH, started_at=T0 + 300,
            payload={"contract_hash": _hash(marker), "decision": "MATCH"},
        )  # fmt: skip
    get_obs().flush()


def test_page_shows_the_contract_on_the_nodes_that_received_it(ui_client):
    run_id, request_id = "run-cd-yes", "req-cd-yes"
    _seed_contract_page(run_id, request_id, contract=PAGE_CONTRACT)
    chunks = _node_chunks(_render(ui_client, run_id, request_id))

    def section(action: str) -> str:
        chunk = chunks[action]
        assert '<details class="contract-delivery"' in chunk, action
        return chunk

    assert "native developer message" in section("estimate_intent")
    assert "embedded in the user prompt" in section("domain_prefilter")
    evaluate = section("evaluate")
    assert "native message (role=system)" in evaluate and "sent by the client as role=system" in evaluate
    assert "native developer message" in section("critique")  # critic: summary is plain text, wire in parsed_json
    for action in ("estimate_intent", "evaluate", "critique", "domain_prefilter"):
        assert "developer_contract" in chunks[action], action  # io-row, data-driven

    # Nodes that did not receive the contract show nothing and claim nothing.
    for action in ("safety_override", "refuse (fast_path)"):
        assert '<details class="contract-delivery"' not in chunks[action], action
        assert "developer_contract" not in chunks[action], action


def test_page_escapes_the_contract_and_keeps_unicode(ui_client):
    run_id, request_id = "run-cd-esc", "req-cd-esc"
    _seed_contract_page(run_id, request_id, contract=PAGE_CONTRACT)
    chunk = _node_chunks(_render(ui_client, run_id, request_id))["estimate_intent"]
    block = chunk[chunk.index('<details class="contract-delivery"') :]
    block = block[: block.index("</details>")]
    assert "<script>alert(1)</script>" not in block
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in block
    assert "{{ 7 * 7 }}" in block and "49" not in block.replace("{{ 7 * 7 }}", "")
    assert "café — never" in block


def test_page_without_a_contract_renders_no_contract_section(ui_client):
    run_id, request_id = "run-cd-no", "req-cd-no"
    _seed_contract_page(run_id, request_id, contract=None)
    body = _render(ui_client, run_id, request_id)
    assert 'class="contract-delivery"' not in body
    assert "embedded in the" not in body
    chunks = _node_chunks(body)
    for action in ("estimate_intent", "critique", "domain_prefilter"):
        assert "developer_contract" not in chunks[action], action


def test_page_renames_the_original_messages_summary(ui_client):
    run_id, request_id = "run-cd-rename", "req-cd-rename"
    _seed_contract_page(run_id, request_id, contract=PAGE_CONTRACT)
    body = _render(ui_client, run_id, request_id)
    assert "<summary>Original request messages (client body)</summary>" in body
    assert "Original System / Developer Messages" not in body


def test_page_survives_a_corrupt_wire_row(ui_client):
    run_id, request_id = "run-cd-bad", "req-cd-bad"
    _seed_contract_page(run_id, request_id, contract=PAGE_CONTRACT)
    _call(
        run_id, request_id, cycle=1, phase="critic", module="critic", action="critique",
        sequence_in_cycle=3, started_at=T0 + 900, duration_ms=10,
        parsed_summary_json="{broken",
        parsed_json=json.dumps({"wire": {"v": 1, "messages": [{"role": "developer", "s": 99}]}}),
    )  # fmt: skip
    body = _render(ui_client, run_id, request_id)
    # The healthy module rows still show it (the INPUT anchor emits the same marker, so check a module node).
    assert '<details class="contract-delivery"' in _node_chunks(body)["estimate_intent"]


def _input_anchor(body: str) -> str:
    """HTML of the INPUT anchor node of the execution graph (up to the first pipe after it)."""
    start = body.index("flow-node--input")
    return body[start : body.index('<div class="flow-pipe">', start)]


def test_input_anchor_shows_the_request_contract(ui_client):
    run_id, request_id = "run-cd-input", "req-cd-input"
    _seed_contract_page(run_id, request_id, contract=PAGE_CONTRACT)
    anchor = _input_anchor(_render(ui_client, run_id, request_id))
    block = anchor[anchor.index('<details class="contract-delivery"') :]
    block = block[: block.index("</details>")]
    assert f"{len(PAGE_CONTRACT.strip())} chars" in block
    assert "Identity verified by contract_hash." in block
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in block and "<script>alert(1)</script>" not in block
    assert "{{ 7 * 7 }}" in block and "49" not in block.replace("{{ 7 * 7 }}", "")
    assert "café — never give medical diagnoses." in block
    assert "sent by the client as role=system" in block  # evaluate recorded it among the system messages
    # The seed persists no COMPLIANCE_LAYER_STARTED event: the header chip follows the resolved contract anyway.
    header = anchor[: anchor.index("flow-node-body")]
    assert '<span class="badge badge-type" title="A developer contract constrains this request">' in header


def test_input_anchor_without_a_contract_shows_nothing(ui_client):
    run_id, request_id = "run-cd-input-no", "req-cd-input-no"
    _seed_contract_page(run_id, request_id, contract=None)
    anchor = _input_anchor(_render(ui_client, run_id, request_id))
    assert "Developer contract" not in anchor
    assert "developer contract" not in anchor  # no presence chip either


def test_input_anchor_flags_a_present_but_unidentified_contract(ui_client):
    run_id, request_id = "run-cd-input-unk", "req-cd-input-unk"
    _seed_contract_page(run_id, request_id, contract=None)
    persist_orchestration_event(
        run_id=run_id, request_id=request_id, cycle=0, stage="COMPLIANCE", component="compliance_layer",
        event_type=COMPLIANCE_LAYER_STARTED, started_at=T0 + 100, payload={"has_contract": True},
    )  # fmt: skip
    get_obs().flush()
    anchor = _input_anchor(_render(ui_client, run_id, request_id))
    assert "contract-delivery" not in anchor
    assert "Developer contract present, but its text could not be identified" in anchor
