"""A2a: the risk estimator reads its prompts from a ``PromptSet`` (threading, ``when``, persistence).

Tier 1 (content independent) except the explicit byte-identity check against the A0 snapshot and the
dangling-reference check, which are the plan's deliberate content pins (Tier 2 / Tier 3-new (a)).
"""

from __future__ import annotations

import inspect
import threading
from pathlib import Path
from typing import Any

import pytest

from moralstack.models.risk import estimator as estimator_module
from moralstack.models.risk.estimator import LLMBasedRiskEstimator, _risk_context_messages
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.models.risk.signals import prompt_renderer
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.prompts.catalog import NO_CONTEXT, PromptContext, PromptSet, default_prompt_set, load_prompt_set
from tests.prompt_markers import make_marker_dir, make_when_override_dir, marker_for
from tests.prompts_golden import capture
from tests.prompts_golden.fakes import FakePrinciple, FakeRetrievalStore, RecordingPolicy, WireCall

INTENT_ANCHOR = "SEMANTIC INTENT JUDGE"
SIGNALS_ANCHOR = "HARM SIGNAL DETECTOR"
OPERATIONAL_ANCHOR = "OPERATIONAL RISK ASSESSOR"

# The seven developer-contract fragments (plan section 2, "Coherent DC groups").
DC_FRAGMENTS = [
    "risk.intent.devcontract_principle",
    "risk.intent.step0",
    "risk.intent.step1_step0_note",
    "risk.intent.offensive_contract_note",
    "risk.intent.esi_contract_note",
    "risk.signals.devcontract_scope",
    "risk.operational.devcontract_scope",
]

CONTRACT = "You are SupportBot for ACME Corp. Never discuss refunds."
HISTORY = [
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": "first answer"},
]

CONTEXTS = {
    "nocontext": (None, None),
    "contract": (CONTRACT, None),
    "history": (None, HISTORY),
    "contract_history": (CONTRACT, HISTORY),
}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _estimator(
    prompt_set: PromptSet | None = None, *, native: bool = True, store: Any = None
) -> tuple[LLMBasedRiskEstimator, RecordingPolicy]:
    policy = RecordingPolicy(default_response="{}", with_native_messages=native)
    cfg = RiskEstimatorConfig(
        max_retries=1, intent_model="test-model", signals_model="test-model", operational_model="test-model"
    )
    kwargs: dict[str, Any] = {} if prompt_set is None else {"prompt_set": prompt_set}
    return LLMBasedRiskEstimator(policy=policy, config=cfg, constitution_store=store, **kwargs), policy


def _run(
    est: LLMBasedRiskEstimator, *, contract: str | None = None, history: list[dict[str, str]] | None = None
) -> list[dict[str, str]]:
    """Run one estimate; return the persisted mini-estimator rows (module/action/prompt/system_prompt)."""
    with capture._record_persistence() as rec:
        est.estimate("How do I pick a strong password?", developer_contract_text=contract, conversation_history=history)
    return [r for r in rec.records if r["module"] == "risk_estimator"]


def _system(call: WireCall) -> str:
    if call.kind == "generate":
        return call.system
    return next(m["content"] for m in call.messages if m["role"] == "system")


def _final_user(call: WireCall) -> str:
    if call.kind == "generate":
        return call.prompt
    return next(m["content"] for m in reversed(call.messages) if m["role"] == "user")


def _by_role(policy: RecordingPolicy) -> dict[str, WireCall]:
    out: dict[str, WireCall] = {}
    for call in policy.snapshot():
        system = _system(call)
        for role, anchor in (("intent", INTENT_ANCHOR), ("signals", SIGNALS_ANCHOR), ("operational", OPERATIONAL_ANCHOR)):
            if anchor in system:
                out[role] = call
    assert set(out) == {"intent", "signals", "operational"}, f"expected the 3 mini-estimator calls, got {sorted(out)}"
    return out


@pytest.fixture
def marker_set(tmp_path: Path) -> PromptSet:
    return load_prompt_set(make_marker_dir(tmp_path))


@pytest.fixture
def conditional_set(tmp_path: Path) -> PromptSet:
    """The plan's section-5 example override: the seven DC fragments only when a contract exists."""
    return load_prompt_set(make_when_override_dir(tmp_path, {fid: "contract_present" for fid in DC_FRAGMENTS}))


# --------------------------------------------------------------------------------------
# marker test: the estimator's own set reaches the wire of all three calls
# --------------------------------------------------------------------------------------

RISK_SYSTEM_PARTS = {
    "intent": "risk.intent.system",
    "signals": "risk.signals.system_head",
    "operational": "risk.operational.system",
}


def _parts(ps: PromptSet, composition: str) -> list[str]:
    return [p.ref for p in ps._compositions[composition].parts]


@pytest.mark.parametrize("native", [True, False], ids=["generate_messages", "legacy_generate"])
def test_marker_set_reaches_all_three_risk_calls(marker_set: PromptSet, native: bool) -> None:
    base = default_prompt_set()
    # the principles block needs a store; one hard principle is absent on purpose so the none-found text is used
    store = FakeRetrievalStore([FakePrinciple(id="SOFT.T.1", title="Soft", rule="Prefer balanced answers.", level="soft")])
    est_b, policy_b = _estimator(marker_set, native=native, store=store)
    est_a, policy_a = _estimator(None, native=native, store=store)
    # contract + history: on the native path this exercises the context preface too
    _run(est_b, contract=CONTRACT, history=HISTORY)
    _run(est_a, contract=CONTRACT, history=HISTORY)
    calls_b, calls_a = _by_role(policy_b), _by_role(policy_a)

    for role, composition in RISK_SYSTEM_PARTS.items():
        system = _system(calls_b[role])
        for fid in _parts(base, composition):
            assert marker_for(fid) in system, (role, fid)
        assert "<<M:" not in _system(calls_a[role]), f"default-set estimator leaked a marker ({role})"
        assert "<<M:" not in _final_user(calls_a[role]), f"default-set estimator leaked a marker in the user text ({role})"

    # registry-derived sections: fixed wording around them comes from the set as well
    signals_system = _system(calls_b["signals"])
    for fid in (
        "risk.signals.static_suffix",
        "risk.signals.evaluation_order_item",
        "risk.signals.output_schema_header",
        "risk.signals.output_schema_footer",
    ):
        assert marker_for(fid) in signals_system, fid
    # user templates + principles block (+ none-found text, + context preface on the native path)
    assert marker_for("risk.intent.user") in _final_user(calls_b["intent"])
    assert marker_for("risk.signals.user") in _final_user(calls_b["signals"])
    assert marker_for("risk.operational.user") in _final_user(calls_b["operational"])
    assert marker_for("risk.intent.principles_block") in _final_user(calls_b["intent"])
    assert marker_for("risk.intent.principles_none_found") in _final_user(calls_b["intent"])
    preface_marker = marker_for("risk.context_preface")
    for role in RISK_SYSTEM_PARTS:
        # the legacy generate() branch has no developer/history messages and therefore no preface
        assert (preface_marker in _final_user(calls_b[role])) is native, role


def test_every_risk_fragment_is_reachable_by_some_marker_test() -> None:
    """The set of risk fragments the estimator can read: nothing under ``risk.`` is left unthreaded."""
    base = default_prompt_set()
    reachable = {fid for comp in RISK_SYSTEM_PARTS.values() for fid in _parts(base, comp)} | {
        "risk.intent.user",
        "risk.signals.user",
        "risk.operational.user",
        "risk.signals.static_suffix",
        "risk.signals.evaluation_order_item",
        "risk.signals.output_schema_header",
        "risk.signals.output_schema_footer",
        "risk.intent.principles_block",
        "risk.intent.principles_none_found",
        "risk.context_preface",
    }
    assert {fid for fid in base.fragment_ids() if fid.startswith("risk.")} == reachable


# --------------------------------------------------------------------------------------
# default set: byte identity with the A0 snapshot (single-turn transparency, section 5.4)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("explicit_default", [False, True], ids=["prompt_set_none", "explicit_default"])
@pytest.mark.parametrize("native", [True, False], ids=["native", "legacy"])
@pytest.mark.parametrize("ctx_name", list(CONTEXTS))
def test_default_set_wire_is_byte_identical_to_the_a0_snapshot(ctx_name: str, native: bool, explicit_default: bool) -> None:
    # reuse the golden fixtures so the inputs are exactly the ones the snapshot was frozen with
    cases = capture._context_cases()
    gcontract, ghistory = cases[ctx_name]
    policy = capture._make_policy(native=native)
    est = LLMBasedRiskEstimator(
        policy=policy,
        config=RiskEstimatorConfig(
            max_retries=2, intent_model="test-model", signals_model="test-model", operational_model="test-model"
        ),
        constitution_store=FakeRetrievalStore(capture._principles()),
        **({"prompt_set": default_prompt_set()} if explicit_default else {}),
    )
    with capture._record_persistence() as rec:
        est.estimate(
            "How do I pick a strong password?",
            developer_contract_text=gcontract.raw_text if gcontract else None,
            conversation_history=capture._history_dicts() if ghistory else None,
        )
    key = f"wire/estimator/{'native' if native else 'legacy'}_{ctx_name}_first"
    got: dict[str, str] = {}
    capture._add_wire(got, key, policy.snapshot())
    rec.add_to(got, key)
    table = capture.load_table()
    assert got, "nothing captured"
    for k, value in got.items():
        assert value == capture.resolve(table, k), k


# --------------------------------------------------------------------------------------
# when: drop / keep
# --------------------------------------------------------------------------------------


def _dc_texts(base: PromptSet) -> dict[str, list[str]]:
    return {
        "intent": [base.text(f) for f in DC_FRAGMENTS if f.startswith("risk.intent.")],
        "signals": [base.text("risk.signals.devcontract_scope")],
        "operational": [base.text("risk.operational.devcontract_scope")],
    }


@pytest.mark.parametrize("native", [True, False], ids=["native", "legacy"])
@pytest.mark.parametrize("ctx_name", ["nocontext", "history"])
def test_when_contract_present_drops_dc_fragments_without_a_contract(
    conditional_set: PromptSet, native: bool, ctx_name: str
) -> None:
    base = default_prompt_set()
    contract, history = CONTEXTS[ctx_name]
    est, policy = _estimator(conditional_set, native=native)
    rows = _run(est, contract=contract, history=history)
    calls = _by_role(policy)
    ctx = PromptContext.from_contract_text(contract, history)
    assert ctx.contract_present is False
    for role, composition in RISK_SYSTEM_PARTS.items():
        system = _system(calls[role])
        for text in _dc_texts(base)[role]:
            assert text not in system, (role, text[:40])
        assert conditional_set.render(composition, ctx) in system
    # what was persisted is what was sent
    sent = {role: _system(call) for role, call in calls.items()}
    assert sorted(r["system_prompt"] for r in rows) == sorted(sent.values())


@pytest.mark.parametrize("native", [True, False], ids=["native", "legacy"])
@pytest.mark.parametrize("ctx_name", ["contract", "contract_history"])
def test_when_contract_present_keeps_dc_fragments_with_a_contract(
    conditional_set: PromptSet, native: bool, ctx_name: str
) -> None:
    base = default_prompt_set()
    contract, history = CONTEXTS[ctx_name]
    est, policy = _estimator(conditional_set, native=native)
    _run(est, contract=contract, history=history)
    calls = _by_role(policy)
    for role, composition in RISK_SYSTEM_PARTS.items():
        system = _system(calls[role])
        for text in _dc_texts(base)[role]:
            assert text in system, (role, text[:40])
        # with the contract present the conditional set renders exactly the default prompts
        assert system.startswith(base.render(composition, NO_CONTEXT)), role


def test_whitespace_only_contract_counts_as_no_contract(conditional_set: PromptSet) -> None:
    """Documented edge case: ``contract_present`` is text based (same strip() as the estimator's messages)."""
    base = default_prompt_set()
    est, policy = _estimator(conditional_set)
    _run(est, contract="  \n\t ", history=None)
    calls = _by_role(policy)
    for role in RISK_SYSTEM_PARTS:
        for text in _dc_texts(base)[role]:
            assert text not in _system(calls[role]), role


def test_signals_head_keeps_its_terminal_newline_when_the_scope_fragment_is_dropped(conditional_set: PromptSet) -> None:
    base = default_prompt_set()
    est, policy = _estimator(conditional_set)
    _run(est)
    system = _system(_by_role(policy)["signals"])
    head = base.text("risk.signals.head")
    assert system.startswith(head + "\n" + "\n\n")  # head + composition end + the renderer's "\n\n"
    assert not system.startswith(head + "\n\n\n\n")


def test_dc_when_groups_leave_no_dangling_references_on_the_real_wire(conditional_set: PromptSet) -> None:
    """Tier 3-new (a), on real estimator wires: without a contract no prompt refers to the dropped rules."""
    est, policy = _estimator(conditional_set)
    _run(est)
    for role, call in _by_role(policy).items():
        system = _system(call)
        for needle in ("invariant 6", "STEP 0", "(6.c)", "DEVELOPER CONTRACT"):
            assert needle not in system, (role, needle)
    est2, policy2 = _estimator(conditional_set)
    _run(est2, contract=CONTRACT)
    assert "STEP 0" in _system(_by_role(policy2)["intent"])


def test_default_set_is_context_independent() -> None:
    """All defaults are ``always``: the wire does not depend on the call context (only the DC-free fields differ)."""
    systems = {}
    for name, (contract, history) in CONTEXTS.items():
        est, policy = _estimator(None)
        _run(est, contract=contract, history=history)
        systems[name] = {role: _system(call) for role, call in _by_role(policy).items()}
    assert all(systems[name] == systems["nocontext"] for name in CONTEXTS)


# --------------------------------------------------------------------------------------
# persistence: the rendered strings, not the module constants
# --------------------------------------------------------------------------------------


def test_persisted_system_prompt_equals_the_rendered_string_under_an_override(marker_set: PromptSet) -> None:
    est, policy = _estimator(marker_set)
    rows = _run(est, contract=CONTRACT, history=HISTORY)
    calls = _by_role(policy)
    ctx = PromptContext.from_contract_text(CONTRACT, HISTORY)
    by_action = {r["action"]: r for r in rows}
    assert set(by_action) == {"estimate_intent", "estimate_signals", "estimate_operational"}
    assert by_action["estimate_intent"]["system_prompt"] == marker_set.render("risk.intent.system", ctx)
    assert by_action["estimate_operational"]["system_prompt"] == marker_set.render("risk.operational.system", ctx)
    expected_signals, _user = prompt_renderer.get_harm_signal_prompts(signal_registry, prompt_set=marker_set, ctx=ctx)
    assert by_action["estimate_signals"]["system_prompt"] == expected_signals
    # persisted == sent, for every call
    assert by_action["estimate_intent"]["system_prompt"] == _system(calls["intent"])
    assert by_action["estimate_signals"]["system_prompt"] == _system(calls["signals"])
    assert by_action["estimate_operational"]["system_prompt"] == _system(calls["operational"])
    # and the persisted user text is the rendered user text (no module constant involved)
    assert marker_for("risk.intent.user") in by_action["estimate_intent"]["prompt"]
    assert marker_for("risk.signals.user") in by_action["estimate_signals"]["prompt"]
    assert marker_for("risk.operational.user") in by_action["estimate_operational"]["prompt"]


def test_persisted_system_prompt_follows_the_when_context(conditional_set: PromptSet) -> None:
    est, _policy = _estimator(conditional_set)
    no_contract = {r["action"]: r["system_prompt"] for r in _run(est)}
    est2, _policy2 = _estimator(conditional_set)
    with_contract = {r["action"]: r["system_prompt"] for r in _run(est2, contract=CONTRACT)}
    for action in no_contract:
        assert no_contract[action] != with_contract[action], action


# --------------------------------------------------------------------------------------
# prompt_set=None falls back to the default
# --------------------------------------------------------------------------------------


def test_estimator_prompt_set_none_falls_back_to_the_default_set() -> None:
    assert LLMBasedRiskEstimator(policy=None)._prompt_set is default_prompt_set()
    ps = default_prompt_set()
    assert LLMBasedRiskEstimator(policy=None, prompt_set=ps)._prompt_set is ps


def test_renderer_functions_prompt_set_none_equals_the_default_set() -> None:
    ps = default_prompt_set()
    assert prompt_renderer.render_evaluation_order(signal_registry) == prompt_renderer.render_evaluation_order(
        signal_registry, ps
    )
    assert prompt_renderer.render_output_schema(signal_registry) == prompt_renderer.render_output_schema(signal_registry, ps)
    assert prompt_renderer.get_harm_signal_prompts(signal_registry) == prompt_renderer.get_harm_signal_prompts(
        signal_registry, prompt_set=ps, ctx=NO_CONTEXT
    )


def test_context_messages_prompt_set_none_equals_the_default_set() -> None:
    kwargs: dict[str, Any] = {
        "system_prompt": "S",
        "user_prompt": "U",
        "developer_contract_text": CONTRACT,
        "conversation_history": HISTORY,
    }
    assert _risk_context_messages(**kwargs) == _risk_context_messages(**kwargs, prompt_set=default_prompt_set())


def test_changed_signatures_default_prompt_set_to_none() -> None:
    for fn in (
        LLMBasedRiskEstimator.__init__,
        _risk_context_messages,
        prompt_renderer.render_evaluation_order,
        prompt_renderer.render_output_schema,
        prompt_renderer.get_harm_signal_prompts,
    ):
        param = inspect.signature(fn).parameters["prompt_set"]
        assert param.default is None, fn.__qualname__
    assert (
        inspect.signature(prompt_renderer.get_harm_signal_prompts).parameters["prompt_set"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


# --------------------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------------------


def test_get_harm_signal_prompts_uses_the_given_set_and_context(conditional_set: PromptSet, marker_set: PromptSet) -> None:
    base = default_prompt_set()
    with_dc, user = prompt_renderer.get_harm_signal_prompts(
        signal_registry, prompt_set=conditional_set, ctx=PromptContext(contract_present=True)
    )
    without_dc, _ = prompt_renderer.get_harm_signal_prompts(signal_registry, prompt_set=conditional_set)
    default_system, default_user = prompt_renderer.get_harm_signal_prompts(signal_registry)
    assert with_dc == default_system and user == default_user
    assert base.text("risk.signals.devcontract_scope") in with_dc
    assert base.text("risk.signals.devcontract_scope") not in without_dc
    _marked, marked_user = prompt_renderer.get_harm_signal_prompts(signal_registry, prompt_set=marker_set)
    assert marker_for("risk.signals.user") in marked_user


def test_module_constants_are_default_reexports() -> None:
    from moralstack.models.risk import prompts as rp

    ps = default_prompt_set()
    assert rp.INTENT_CONTEXT_SYSTEM_PROMPT == ps.render("risk.intent.system", NO_CONTEXT)
    assert rp.HARM_SIGNAL_SYSTEM_PROMPT == ps.render("risk.signals.system_head", NO_CONTEXT)
    assert rp.OPERATIONAL_RISK_SYSTEM_PROMPT == ps.render("risk.operational.system", NO_CONTEXT)
    assert rp.INTENT_CONTEXT_PROMPT_TEMPLATE == ps.template("risk.intent.user")
    assert rp.HARM_SIGNAL_USER_TEMPLATE == ps.template("risk.signals.user")
    assert rp.OPERATIONAL_RISK_PROMPT_TEMPLATE == ps.template("risk.operational.user")
    assert rp.HARM_SIGNAL_STATIC_TEMPLATE == ps.template("risk.signals.static_suffix")


def test_estimator_runtime_code_does_not_read_the_module_constants() -> None:
    """Runtime code reads ``self._prompt_set``, never the module-level default re-exports."""
    source = inspect.getsource(estimator_module)
    for name in (
        "INTENT_CONTEXT_SYSTEM_PROMPT",
        "OPERATIONAL_RISK_SYSTEM_PROMPT",
        "INTENT_CONTEXT_PROMPT_TEMPLATE",
        "OPERATIONAL_RISK_PROMPT_TEMPLATE",
    ):
        assert name not in source, name


# --------------------------------------------------------------------------------------
# isolation between estimators with different sets
# --------------------------------------------------------------------------------------


def test_two_estimators_with_different_sets_do_not_cross_contaminate(marker_set: PromptSet) -> None:
    results: dict[str, dict[str, WireCall]] = {}
    errors: list[BaseException] = []

    def work(name: str, ps: PromptSet | None) -> None:
        try:
            est, policy = _estimator(ps)
            for _ in range(3):
                est.estimate("How do I pick a strong password?", developer_contract_text=CONTRACT)
            results[name] = _by_role(policy)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    # One recorder for both threads, entered and exited on the main thread: overlapping mock.patch
    # contexts from two threads restore in the wrong order and leave the persistence hooks patched.
    with capture._record_persistence():
        threads = [threading.Thread(target=work, args=(n, p)) for n, p in (("A", None), ("B", marker_set))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not errors, errors
    assert "<<M:" not in "".join(_system(c) + _final_user(c) for c in results["A"].values())
    assert all("<<M:" in _system(c) for c in results["B"].values())


def test_static_suffix_override_collapses_double_braces_through_the_real_renderer(tmp_path: Path) -> None:
    """The A1-deferred R3 case: an override reaches ``get_harm_signal_prompts`` and ``{{`` is collapsed once."""
    import json

    sections = (
        "evaluation_order_section",
        "signal_definitions_section",
        "domain_sensitivity_section",
        "coherence_rules_section",
        "output_schema_section",
    )
    body = "\n".join("{" + name + "}" for name in sections) + '\n{{"a": 1}}\n'
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "o.yaml").write_text(
        json.dumps({"version": 1, "fragments": {"risk.signals.static_suffix": {"text": body}}}), encoding="utf-8"
    )
    system, _user = prompt_renderer.get_harm_signal_prompts(signal_registry, prompt_set=load_prompt_set(d))
    assert system.endswith('{"a": 1}\n')
    assert '{{"a": 1}}' not in system
