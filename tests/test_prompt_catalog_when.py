"""``when`` conditions of the prompt catalog: context building, token semantics, separators, terminal newline.

Tier 1 (content independent): synthetic trees plus values computed from the live packaged set.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from moralstack.prompts import catalog
from moralstack.prompts.catalog import NO_CONTEXT, PromptConfigError, PromptContext, default_prompt_set, load_prompt_set

ALL_CONTEXTS = [PromptContext(c, h) for c in (False, True) for h in (False, True)]
TOKENS = ["always", "never", "contract_present", "contract_absent", "history_present", "history_absent"]


def _holds(token: str, ctx: PromptContext) -> bool:
    return {
        "always": True,
        "never": False,
        "contract_present": ctx.contract_present,
        "contract_absent": not ctx.contract_present,
        "history_present": ctx.history_present,
        "history_absent": not ctx.history_present,
    }[token]


def _write(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _defaults(tmp_path: Path, compositions: dict[str, Any], fragments: dict[str, Any] | None = None) -> Path:
    base: dict[str, Any] = {
        "version": 1,
        "fragments": fragments
        or {"a.one": {"text": "ONE"}, "a.two": {"text": "TWO"}, "a.three": {"text": "THREE"}, "a.four": {"text": "FOUR"}},
        "compositions": compositions,
    }
    _write(tmp_path / "defaults" / "d.yaml", base)
    return tmp_path / "defaults"


def _override(tmp_path: Path, fragments: dict[str, Any]) -> Path:
    _write(tmp_path / "prompts" / "o.yaml", {"version": 1, "fragments": fragments})
    return tmp_path / "prompts"


# --------------------------------------------------------------------------------------
# PromptContext construction
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"), [(None, False), ("", False), ("  ", False), ("\n\t", False), ("x", True), (" x ", True)]
)
def test_from_contract_text(text: str | None, expected: bool) -> None:
    assert PromptContext.from_contract_text(text, None).contract_present is expected


@pytest.mark.parametrize(("history", "expected"), [(None, False), ([], False), ([{"role": "user"}], True), ("truthy", True)])
def test_history_present_is_plain_truthiness(history: Any, expected: bool) -> None:
    assert PromptContext.from_contract_text("x", history).history_present is expected
    assert PromptContext.from_contract(None, history).history_present is expected


@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        (None, False),
        (SimpleNamespace(raw_text="x"), True),
        (SimpleNamespace(raw_text="  \n"), False),  # documented edge case: whitespace-only contract
        (SimpleNamespace(raw_text=""), False),
        (SimpleNamespace(), False),
        (SimpleNamespace(raw_text=None), False),
    ],
)
def test_from_contract(contract: Any, expected: bool) -> None:
    assert PromptContext.from_contract(contract, None).contract_present is expected


def test_from_contract_agrees_with_the_real_developer_contract() -> None:
    from moralstack.orchestration.contract import DeveloperContract

    for text in ("rules", "  rules ", "   ", "\n\t"):
        contract = DeveloperContract.from_text(text)
        assert PromptContext.from_contract(contract, None).contract_present is (not contract.is_empty())
        assert PromptContext.from_contract_text(text, None).contract_present is (not contract.is_empty())


def test_no_context_is_all_false() -> None:
    assert NO_CONTEXT == PromptContext(False, False)


# --------------------------------------------------------------------------------------
# token semantics
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("token", TOKENS)
def test_when_token_cross_context_include_and_drop(tmp_path: Path, token: str) -> None:
    d = _defaults(
        tmp_path,
        {
            "a.comp": {
                "context_aware": True,
                "end": "\n",
                "parts": ["a.one", {"ref": "a.two", "sep": "\t"}, {"ref": "a.three", "sep": "\t"}],
            }
        },
    )
    ds = catalog._load_from(d, _override(tmp_path, {"a.two": {"when": token}}))
    for ctx in ALL_CONTEXTS:
        expected = "ONE\tTWO\tTHREE\n" if _holds(token, ctx) else "ONE\tTHREE\n"
        assert ds.render("a.comp", ctx) == expected, (token, ctx)


# --------------------------------------------------------------------------------------
# default set
# --------------------------------------------------------------------------------------


def test_default_set_renders_identically_in_all_four_contexts() -> None:
    ds = default_prompt_set()
    aware = [cid for cid in ds.composition_ids() if ds.is_context_aware(cid)]
    assert {"risk.intent.system", "risk.signals.system_head", "risk.operational.system", "critic.full.system"} <= set(aware)
    for cid in aware:
        assert len({ds.render(cid, ctx) for ctx in ALL_CONTEXTS}) == 1, cid


def test_packaged_defaults_never_carry_a_conditional_when() -> None:
    ds = default_prompt_set()
    assert {ds._fragments[f].when for f in ds.fragment_ids()} == {"always"}


def test_load_time_self_check_raises_when_a_default_renders_differently(tmp_path: Path) -> None:
    d = _defaults(
        tmp_path,
        {"a.comp": {"context_aware": True, "parts": ["a.one", {"ref": "a.two", "sep": " "}]}},
        fragments={"a.one": {"text": "ONE"}, "a.two": {"text": "TWO", "when": "contract_present"}},
    )
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(d, None)
    assert info.value.fragment_id == "a.comp"
    # the same tree is fine once an override (not the packaged defaults) carries the condition
    d2 = _defaults(
        tmp_path / "ok",
        {"a.comp": {"context_aware": True, "parts": ["a.one", {"ref": "a.two", "sep": " "}]}},
        fragments={"a.one": {"text": "ONE"}, "a.two": {"text": "TWO"}},
    )
    ds = catalog._load_from(d2, _override(tmp_path / "ok", {"a.two": {"when": "contract_present"}}))
    assert ds.render("a.comp", NO_CONTEXT) == "ONE"
    assert ds.render("a.comp", PromptContext(contract_present=True)) == "ONE TWO"


# --------------------------------------------------------------------------------------
# separators
# --------------------------------------------------------------------------------------

FOUR_PARTS = {
    "a.comp": {
        "context_aware": True,
        "end": " ",
        "parts": ["a.one", {"ref": "a.two", "sep": "\t"}, {"ref": "a.three", "sep": "\n"}, {"ref": "a.four", "sep": "  "}],
    }
}


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [
        (["a.one"], "TWO\nTHREE  FOUR "),  # first part dropped: nothing is emitted before the first emitted part
        (["a.two"], "ONE\nTHREE  FOUR "),  # a dropped part takes its own separator with it
        (["a.three"], "ONE\tTWO  FOUR "),
        (["a.four"], "ONE\tTWO\nTHREE "),  # last part dropped: end is still emitted exactly once
        (["a.one", "a.two"], "THREE  FOUR "),
        (["a.two", "a.three"], "ONE  FOUR "),
        (["a.one", "a.four"], "TWO\nTHREE "),
        ([], "ONE\tTWO\nTHREE  FOUR "),
    ],
)
def test_separator_semantics(tmp_path: Path, dropped: list[str], expected: str) -> None:
    d = _defaults(tmp_path, FOUR_PARTS)
    ds = catalog._load_from(d, _override(tmp_path, {fid: {"when": "never"} for fid in dropped}) if dropped else None)
    assert ds.render("a.comp", NO_CONTEXT) == expected


def test_empty_text_override_keeps_its_separator_unlike_when_never(tmp_path: Path) -> None:
    """Pins today's behaviour (documented): ``text: ""`` still emits the part's separator, ``when: never`` drops it."""
    d = _defaults(tmp_path, FOUR_PARTS)
    empty = catalog._load_from(d, _override(tmp_path, {"a.two": {"text": ""}}))
    assert empty.render("a.comp", NO_CONTEXT) == "ONE\t\nTHREE  FOUR "
    dropped = catalog._load_from(d, _override(tmp_path, {"a.two": {"when": "never"}}))
    assert dropped.render("a.comp", NO_CONTEXT) == "ONE\nTHREE  FOUR "


def test_all_parts_dropped_renders_empty_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    d = _defaults(tmp_path, FOUR_PARTS)
    with caplog.at_level(logging.WARNING, logger=catalog.logger.name):
        ds = catalog._load_from(
            d, _override(tmp_path, {f"a.{n}": {"when": "never"} for n in ("one", "two", "three", "four")})
        )
    assert ds.render("a.comp", NO_CONTEXT) == ""  # not even the end
    assert any("a.comp" in r.getMessage() and "empty" in r.getMessage() for r in caplog.records)


def test_end_follows_the_last_emitted_part_and_non_aware_ignores_context(tmp_path: Path) -> None:
    d = _defaults(
        tmp_path,
        {"a.plain": {"end": "\n", "parts": ["a.one", {"ref": "a.two", "sep": "\n"}]}},
    )
    ds = catalog._load_from(d, None)
    assert ds.text("a.plain") == "ONE\nTWO\n"
    assert ds.render("a.plain", PromptContext(True, True)) == "ONE\nTWO\n"
    assert not ds.is_context_aware("a.plain")


# --------------------------------------------------------------------------------------
# terminal newline (MT3)
# --------------------------------------------------------------------------------------


def test_terminal_newline_survives_dropping_the_last_part(tmp_path: Path) -> None:
    d = _defaults(
        tmp_path, {"a.comp": {"context_aware": True, "end": "\n", "parts": ["a.one", {"ref": "a.two", "sep": "\n\n"}]}}
    )
    ds = catalog._load_from(d, _override(tmp_path, {"a.two": {"when": "contract_present"}}))
    assert ds.render("a.comp", NO_CONTEXT) == "ONE\n"
    assert ds.render("a.comp", PromptContext(contract_present=True)) == "ONE\n\nTWO\n"


def test_packaged_compositions_keep_terminal_whitespace_in_end_not_in_the_last_fragment() -> None:
    ds = default_prompt_set()
    for cid in ds.composition_ids():
        comp = ds._compositions[cid]
        last = ds._fragments[comp.parts[-1].ref].text
        assert last == last.rstrip(" \t\n"), f"{cid}: terminal whitespace belongs in the composition end"
    assert ds._compositions["risk.signals.system_head"].end == "\n"
    assert ds._compositions["hindsight.batch.system"].end == "\n"


DC_FRAGMENTS = [
    "risk.intent.devcontract_principle",
    "risk.intent.step0",
    "risk.intent.step1_step0_note",
    "risk.intent.offensive_contract_note",
    "risk.intent.esi_contract_note",
    "risk.signals.devcontract_scope",
    "risk.operational.devcontract_scope",
]
RISK_COMPOSITIONS = ["risk.intent.system", "risk.signals.system_head", "risk.operational.system"]


@pytest.fixture(scope="module")
def contract_conditional_set(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The plan's section-5 example override: the seven DC fragments conditional on a contract."""
    d = tmp_path_factory.mktemp("conditional") / "prompts"
    _write(
        d / "risk-contract-conditional.yaml",
        {"version": 1, "fragments": {f: {"when": "contract_present"} for f in DC_FRAGMENTS}},
    )
    return load_prompt_set(d)


def test_signals_system_head_ends_with_exactly_one_newline_in_both_contexts(contract_conditional_set: Any) -> None:
    base = default_prompt_set()
    without = contract_conditional_set.render("risk.signals.system_head", NO_CONTEXT)
    with_contract = contract_conditional_set.render("risk.signals.system_head", PromptContext(contract_present=True))
    assert without == base.text("risk.signals.head") + "\n"
    assert with_contract == base.render("risk.signals.system_head", NO_CONTEXT)
    for rendered in (without, with_contract):
        assert rendered.endswith("\n") and not rendered.endswith("\n\n")
    # the renderer then joins this head to the static suffix with "\n\n": the join shape is the default one
    assert without + "\n\n" == base.text("risk.signals.head") + "\n\n\n"
    assert with_contract + "\n\n" == base.render("risk.signals.system_head", NO_CONTEXT) + "\n\n"


def test_section5_override_drops_and_keeps_the_seven_dc_fragments(contract_conditional_set: Any) -> None:
    base = default_prompt_set()
    groups = {
        "risk.intent.system": [f for f in DC_FRAGMENTS if f.startswith("risk.intent.")],
        "risk.signals.system_head": ["risk.signals.devcontract_scope"],
        "risk.operational.system": ["risk.operational.devcontract_scope"],
    }
    assert sum(len(v) for v in groups.values()) == 7
    for cid, fragments in groups.items():
        for ctx in ALL_CONTEXTS:
            rendered = contract_conditional_set.render(cid, ctx)
            for fid in fragments:
                text = base.text(fid)
                assert (text in rendered) is ctx.contract_present, (cid, fid, ctx)
            if ctx.contract_present:
                assert rendered == base.render(cid, NO_CONTEXT), (cid, ctx)
        # every other fragment stays in all contexts
        for part in base._compositions[cid].parts:
            if part.ref not in fragments:
                assert base.text(part.ref) in contract_conditional_set.render(cid, NO_CONTEXT)


def test_dc_when_groups_leave_no_dangling_references(contract_conditional_set: Any) -> None:
    """Without a contract no remaining fragment may refer to invariant 6, STEP 0 or the (6.c) override."""
    for cid in RISK_COMPOSITIONS:
        rendered = contract_conditional_set.render(cid, NO_CONTEXT)
        for needle in ("invariant 6", "STEP 0", "(6.c)", "DEVELOPER CONTRACT"):
            assert needle not in rendered, (cid, needle)
    # and the reverse: with a contract the references are present
    assert "STEP 0" in contract_conditional_set.render("risk.intent.system", PromptContext(contract_present=True))
