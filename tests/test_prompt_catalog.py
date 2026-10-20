"""Prompt catalog (``moralstack/prompts/catalog.py``): loader, validation, overrides, audit, identity.

Tier 1 (content independent) except the clearly marked duplication guard, which exists only while the
module constants still hold the prompt text: it is deleted in the A2x step where each module's constants
become re-exports of the catalog.

Synthetic trees go through the internal ``_load_from(defaults_dir, prompts_dir)`` hook; the tests on the
packaged set use ``default_prompt_set()`` / ``load_prompt_set()``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import string
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from moralstack.prompts import catalog
from moralstack.prompts.catalog import (
    NO_CONTEXT,
    PromptConfigError,
    PromptContext,
    PromptSet,
    default_prompt_set,
    load_prompt_set,
    prompt_run_meta,
    resolve_prompts_dir,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ALL_CONTEXTS = [PromptContext(c, h) for c in (False, True) for h in (False, True)]

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

SYNTH_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "fragments": {
        "demo.head": {"text": "HEAD"},
        "demo.dc": {"text": "DC"},
        "demo.tail": {"text": "TAIL"},
        "demo.shared": {"text": "SHARED"},
        "demo.tpl": {"kind": "template", "placeholders": ["a", "b"], "text": "x {a} y {b} {{lit}}"},
        "demo.items": {"kind": "lines", "text": "one\ntwo"},
        "demo.solo": {"text": "SOLO"},
        "safety_demo.x": {"text": "not under a safety prefix"},
        "risk.demo": {"text": "under a safety prefix"},
        "policy.canary_instruction": {"text": "explicit safety id"},
    },
    "compositions": {
        "demo.aware": {
            "context_aware": True,
            "end": "\n",
            "parts": ["demo.head", {"ref": "demo.dc", "sep": "\n\n"}, {"ref": "demo.shared", "sep": "\n\n"}],
        },
        "demo.aware2": {"context_aware": True, "parts": ["demo.shared", {"ref": "demo.tail", "sep": " "}]},
        "demo.plain": {"parts": ["demo.head", {"ref": "demo.tail", "sep": "\n"}]},
    },
}


def write_yaml(path: Path, obj: Any) -> Path:
    """JSON is valid YAML, so synthetic trees are written with ``json.dumps``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def make_defaults(tmp_path: Path, obj: dict[str, Any] | None = None, name: str = "demo.yaml") -> Path:
    d = tmp_path / "defaults"
    write_yaml(d / name, obj if obj is not None else SYNTH_DEFAULTS)
    return d


def make_override(tmp_path: Path, fragments: dict[str, Any], name: str = "o.yaml", dirname: str = "prompts") -> Path:
    d = tmp_path / dirname
    write_yaml(d / name, {"version": 1, "fragments": fragments})
    return d


def synth(tmp_path: Path, fragments: dict[str, Any] | None = None, defaults: dict[str, Any] | None = None) -> PromptSet:
    d = make_defaults(tmp_path, defaults)
    if fragments is None:
        return catalog._load_from(d, None)
    return catalog._load_from(d, make_override(tmp_path, fragments))


def packaged_with_override(tmp_path: Path, fragments: dict[str, Any]) -> PromptSet:
    return load_prompt_set(make_override(tmp_path, fragments))


def mutate(defaults: dict[str, Any], **changes: Any) -> dict[str, Any]:
    out = json.loads(json.dumps(defaults))
    for key, value in changes.items():
        out[key] = value
    return out


# --------------------------------------------------------------------------------------
# packaged set
# --------------------------------------------------------------------------------------

PACKAGED_FILES = [
    "common",
    "risk",
    "critic",
    "perspectives",
    "simulator",
    "hindsight",
    "context",
    "policy",
    "refusal",
    "compliance",
    "retrieval",
]


def test_packaged_defaults_load_and_ids_unique() -> None:
    ds = default_prompt_set()
    ids = list(ds.fragment_ids()) + list(ds.composition_ids())
    assert len(ids) == len(set(ids))
    regex = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    assert all(regex.match(i) for i in ids)
    assert ds.is_default
    assert ds.fingerprint == ds.default_fingerprint


def test_packaged_defaults_are_context_independent() -> None:
    ds = default_prompt_set()
    for cid in ds.composition_ids():
        renders = {ds.render(cid, ctx) for ctx in ALL_CONTEXTS}
        assert len(renders) == 1, cid


def test_packaged_fragments_per_file_are_in_the_expected_namespaces() -> None:
    """Each of the 11 files owns IDs; every fragment ID of the packaged set appears exactly once on disk."""
    data_dir = Path(catalog.__file__).parent / "data"
    on_disk: list[str] = []
    for name in PACKAGED_FILES:
        content = (data_dir / f"{name}.yaml").read_text(encoding="utf-8")
        ids = re.findall(r"^  ([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+):\s*$", content, flags=re.M)
        assert ids, name
        on_disk.extend(ids)
    ds = default_prompt_set()
    assert sorted(on_disk) == sorted(list(ds.fragment_ids()) + list(ds.composition_ids()))


def test_packaged_defaults_load_identically_from_a_crlf_checkout(tmp_path: Path) -> None:
    """core.autocrlf=true checkouts hold CRLF files: the loader must see the same bytes (assumption A-2)."""
    crlf = tmp_path / "crlf"
    crlf.mkdir()
    for f in (Path(catalog.__file__).parent / "data").glob("*.yaml"):
        (crlf / f.name).write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert catalog._load_from(crlf, None).fingerprint == default_prompt_set().fingerprint


def test_default_set_singleton_thread_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "_DEFAULT", None)
    results: list[PromptSet] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        results.append(default_prompt_set())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8
    assert all(r is results[0] for r in results)


# --------------------------------------------------------------------------------------
# T3.4 fail fast on a corrupt packaged default
# --------------------------------------------------------------------------------------


def _packaged_copy(tmp_path: Path) -> Path:
    src = Path(catalog.__file__).parent / "data"
    dst = tmp_path / "packaged"
    dst.mkdir()
    for f in src.glob("*.yaml"):
        (dst / f.name).write_bytes(f.read_bytes())
    return dst


@pytest.mark.parametrize("breakage", ["truncated", "duplicate_id", "bad_when_on_standalone", "unknown_ref", "bad_version"])
def test_corrupt_packaged_default_fails_fast_at_import(
    breakage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = default_prompt_set()
    broken = _packaged_copy(tmp_path)
    target = broken / "context.yaml"
    text = target.read_text(encoding="utf-8")
    if breakage == "truncated":
        target.write_text(text[: len(text) // 2] + "\n  : [", encoding="utf-8")
    elif breakage == "duplicate_id":
        (broken / "context_copy.yaml").write_text(text, encoding="utf-8")
    elif breakage == "bad_when_on_standalone":
        target.write_text(text.replace("    text: |+", "    when: contract_present\n    text: |+", 1), encoding="utf-8")
    elif breakage == "unknown_ref":
        crit = broken / "critic.yaml"
        crit.write_text(
            crit.read_text(encoding="utf-8").replace("      - critic.base\n", "      - critic.nope\n", 1), encoding="utf-8"
        )
    else:
        target.write_text(text.replace("version: 1", "version: 2", 1), encoding="utf-8")
    monkeypatch.setattr(catalog, "_DATA_DIR", broken)
    monkeypatch.setattr(catalog, "_DEFAULT", None)
    with pytest.raises(PromptConfigError):
        default_prompt_set()
    assert catalog._DEFAULT is None, "a failed load must not leave a partial singleton"
    # a later good load works: a real fresh load from the good packaged dir, not the old singleton
    monkeypatch.undo()
    monkeypatch.setattr(catalog, "_DEFAULT", None)
    fresh = default_prompt_set()
    assert fresh is not good
    assert fresh.fingerprint == good.fingerprint


def test_precomputed_renders_never_raise_at_request_time() -> None:
    ds = default_prompt_set()
    for cid in ds.composition_ids():
        for ctx in ALL_CONTEXTS:
            assert isinstance(ds.render(cid, ctx), str)


# --------------------------------------------------------------------------------------
# override errors (every one carries the fragment id and never echoes fragment text)
# --------------------------------------------------------------------------------------

SECRET = "TOPSECRET-TEXT-123"


def _expect_error(tmp_path: Path, fragments: dict[str, Any], *, fragment_id: str | None) -> PromptConfigError:
    with pytest.raises(PromptConfigError) as info:
        synth(tmp_path, fragments)
    err = info.value
    assert SECRET not in str(err)
    if fragment_id is not None:
        assert err.fragment_id == fragment_id
        assert fragment_id in str(err)
    return err


def test_unknown_fragment_id_in_override_raises(tmp_path: Path) -> None:
    _expect_error(tmp_path, {"demo.nope": {"text": SECRET}}, fragment_id="demo.nope")


def test_override_of_composition_id_raises(tmp_path: Path) -> None:
    _expect_error(tmp_path, {"demo.aware": {"text": SECRET}}, fragment_id="demo.aware")


def test_duplicate_id_across_override_files_raises(tmp_path: Path) -> None:
    d = make_defaults(tmp_path)
    p = tmp_path / "prompts"
    write_yaml(p / "a.yaml", {"version": 1, "fragments": {"demo.head": {"text": SECRET}}})
    write_yaml(p / "b.yaml", {"version": 1, "fragments": {"demo.head": {"text": SECRET}}})
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(d, p)
    assert info.value.fragment_id == "demo.head"
    assert SECRET not in str(info.value)


@pytest.mark.parametrize("key", ["kind", "placeholders", "parts", "end"])
def test_override_unknown_key_raises(tmp_path: Path, key: str) -> None:
    _expect_error(tmp_path, {"demo.head": {"text": SECRET, key: "x"}}, fragment_id="demo.head")


@pytest.mark.parametrize("entry", [{}, None, "just a string", ["list"]])
def test_override_entry_needs_text_or_when(tmp_path: Path, entry: Any) -> None:
    _expect_error(tmp_path, {"demo.head": entry}, fragment_id="demo.head")


@pytest.mark.parametrize("token", ["contract-present", "Always", "contract_present and history_present", "", 3, None])
def test_bad_when_token_raises(tmp_path: Path, token: Any) -> None:
    _expect_error(tmp_path, {"demo.dc": {"when": token}}, fragment_id="demo.dc")


def test_override_text_must_be_a_string(tmp_path: Path) -> None:
    _expect_error(tmp_path, {"demo.head": {"text": 5}}, fragment_id="demo.head")


def test_conditional_when_on_non_aware_composition_raises(tmp_path: Path) -> None:
    # demo.tail sits in the aware demo.aware2 AND the non-aware demo.plain
    err = _expect_error(tmp_path, {"demo.tail": {"when": "contract_present"}}, fragment_id="demo.tail")
    assert "demo.plain" in str(err)
    assert "demo.aware2" not in str(err)


def test_conditional_when_on_standalone_fragment_raises(tmp_path: Path) -> None:
    _expect_error(tmp_path, {"demo.solo": {"when": "never"}}, fragment_id="demo.solo")


# --- shared-fragment rule on the packaged set -------------------------------------------


@pytest.mark.parametrize("when", ["contract_present", "never"])
@pytest.mark.parametrize(
    ("fragment_id", "composition"),
    [
        ("critic.base", "critic.quick.system"),
        ("simulator.enums", "simulator.retry"),
        ("simulator.schema_skeleton", "simulator.retry"),
        ("retry.prefix", "critic.retry"),
        ("hindsight.base", "hindsight.single.system"),
        ("critic.retry.body", "critic.retry"),
    ],
)
def test_when_on_fragment_in_non_aware_composition_raises(
    tmp_path: Path, fragment_id: str, composition: str, when: str
) -> None:
    with pytest.raises(PromptConfigError) as info:
        packaged_with_override(tmp_path, {fragment_id: {"when": when}})
    assert info.value.fragment_id == fragment_id
    assert composition in str(info.value)


@pytest.mark.parametrize("when", ["contract_present", "never"])
def test_when_on_standalone_packaged_fragment_raises(tmp_path: Path, when: str) -> None:
    with pytest.raises(PromptConfigError) as info:
        packaged_with_override(tmp_path, {"policy.system": {"when": when}})
    assert info.value.fragment_id == "policy.system"


def test_when_on_fragment_only_in_aware_compositions_accepted(tmp_path: Path) -> None:
    base = default_prompt_set()
    ds = packaged_with_override(
        tmp_path,
        {"critic.rules.devcontract": {"when": "contract_present"}, "common.output_json_only": {"when": "contract_present"}},
    )
    # untouched non-aware compositions render unchanged
    assert ds.render("critic.quick.system", NO_CONTEXT) == base.render("critic.quick.system", NO_CONTEXT)
    assert ds.render("critic.retry", NO_CONTEXT) == base.render("critic.retry", NO_CONTEXT)
    # the aware one reacts to the context
    with_contract = PromptContext(contract_present=True)
    assert base.text("critic.rules.devcontract") not in ds.render("critic.full.system", NO_CONTEXT)
    assert base.text("critic.rules.devcontract") in ds.render("critic.full.system", with_contract)
    assert ds.render("critic.full.system", with_contract) == base.render("critic.full.system", NO_CONTEXT)


# --- templates ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["{}", "{0}", "{a.b}", "{a[0]}", "{a!r}", "{a:>5}", "{a:{b}}", "{ a }", "stray } brace"])
def test_template_rejects_positional_attribute_index_conversion_spec(tmp_path: Path, bad: str) -> None:
    # In an override ...
    err = _expect_error(tmp_path, {"demo.tpl": {"text": bad}}, fragment_id="demo.tpl")
    assert "template" in str(err).lower()
    # ... and in the packaged defaults.
    broken = mutate(SYNTH_DEFAULTS)
    broken["fragments"]["demo.tpl"] = {"kind": "template", "placeholders": ["a"], "text": bad}
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(make_defaults(tmp_path / "second", broken), None)
    assert info.value.fragment_id == "demo.tpl"


def test_template_undeclared_placeholder_in_override_raises(tmp_path: Path) -> None:
    err = _expect_error(tmp_path, {"demo.tpl": {"text": "{a} {zzz}"}}, fragment_id="demo.tpl")
    assert "zzz" in str(err)


def test_template_missing_declared_placeholder_in_override_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=catalog.logger.name):
        ds = synth(tmp_path, {"demo.tpl": {"text": "only {a}"}})
    assert any("demo.tpl" in r.getMessage() and "b" in r.getMessage() for r in caplog.records)
    entry = ds.audit_summary()["fragments"]["demo.tpl"]
    assert entry["missing_placeholders"] == ["b"]
    assert ds.format("demo.tpl", a=1, b=2) == "only 1"


def test_default_template_fields_equal_declared_placeholders_exactly(tmp_path: Path) -> None:
    ds = default_prompt_set()
    checked = 0
    for fid in ds.fragment_ids():
        frag = ds._fragments[fid]
        if frag.kind != "template":
            continue
        fields = {name for _lit, name, _spec, _conv in string.Formatter().parse(frag.text) if name is not None}
        assert fields == set(frag.placeholders), fid
        checked += 1
    assert checked > 20
    # a mismatch in a default tree is a load error
    for placeholders in (["a"], ["a", "b", "c"]):
        broken = mutate(SYNTH_DEFAULTS)
        broken["fragments"]["demo.tpl"]["placeholders"] = placeholders
        with pytest.raises(PromptConfigError):
            catalog._load_from(make_defaults(tmp_path / "x".join(placeholders), broken), None)


def test_template_without_placeholders_declaration_in_defaults_raises(tmp_path: Path) -> None:
    broken = mutate(SYNTH_DEFAULTS)
    del broken["fragments"]["demo.tpl"]["placeholders"]
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path, broken), None)


def test_double_brace_literal_in_template_renders_single_brace(tmp_path: Path) -> None:
    ds = synth(tmp_path)
    assert ds.format("demo.tpl", a=1, b=2) == "x 1 y 2 {lit}"
    assert ds.template("demo.tpl") == "x {a} y {b} {{lit}}"


def test_values_are_not_formatted_a_second_time(tmp_path: Path) -> None:
    ds = synth(tmp_path)
    assert ds.format("demo.tpl", a="{b}", b="{{x}}") == "x {b} y {{x}} {lit}"


# --- file handling -----------------------------------------------------------------------


def _write_raw(tmp_path: Path, content: bytes | str, name: str = "o.yaml", dirname: str = "prompts") -> Path:
    d = tmp_path / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return d


def test_malformed_yaml_raises_prompt_config_error(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "version: 1\nfragments: [oops\n")
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(make_defaults(tmp_path), d)
    assert "invalid YAML" in str(info.value)


@pytest.mark.parametrize("content", [b"", b"# only a comment\n", b"   \n"])
def test_empty_file_raises(tmp_path: Path, content: bytes) -> None:
    d = _write_raw(tmp_path, content)
    with pytest.raises(PromptConfigError, match="empty"):
        catalog._load_from(make_defaults(tmp_path), d)


@pytest.mark.parametrize(
    "header", ["", "version: 0\n", "version: 2\n", 'version: "1"\n', "version: true\n", "version: 1.0\n"]
)
def test_missing_or_wrong_version_raises(tmp_path: Path, header: str) -> None:
    d = _write_raw(tmp_path, header + "fragments:\n  demo.head: {text: x}\n")
    with pytest.raises(PromptConfigError, match="version"):
        catalog._load_from(make_defaults(tmp_path), d)


def test_non_mapping_top_level_raises(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "- a\n- b\n")
    with pytest.raises(PromptConfigError, match="mapping"):
        catalog._load_from(make_defaults(tmp_path), d)


def test_non_utf8_file_raises_prompt_config_error(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, b"version: 1\nfragments:\n  demo.head: {text: \xff\xfe}\n")
    with pytest.raises(PromptConfigError, match="UTF-8"):
        catalog._load_from(make_defaults(tmp_path), d)


YAML_HEAD_OVERRIDE = "version: 1\nfragments:\n  demo.head:\n    text: "


def test_utf8_bom_file_loads(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, b"\xef\xbb\xbf" + (YAML_HEAD_OVERRIDE + "|-\n      caf\xc3\xa9\n").encode("latin-1"))
    ds = catalog._load_from(make_defaults(tmp_path), d)
    assert ds.text("demo.head") == "café"


def test_crlf_file_loads_same_content_as_lf(tmp_path: Path) -> None:
    body = YAML_HEAD_OVERRIDE + "|\n      line one\n\n      line two\n"
    lf = catalog._load_from(make_defaults(tmp_path), _write_raw(tmp_path, body, dirname="lf"))
    crlf = catalog._load_from(make_defaults(tmp_path), _write_raw(tmp_path, body.replace("\n", "\r\n"), dirname="crlf"))
    assert lf.text("demo.head") == crlf.text("demo.head") == "line one\n\nline two\n"
    assert lf.fingerprint == crlf.fingerprint
    assert "\r" not in crlf.text("demo.head")
    # the override-file sha256 is computed on the LF-normalized text, so it is line-ending independent
    assert lf.audit_summary()["override_files"] == crlf.audit_summary()["override_files"]


def test_trailing_space_on_content_line_preserved(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, YAML_HEAD_OVERRIDE + "|-\n      keeps the trailing space \n      next\n")
    ds = catalog._load_from(make_defaults(tmp_path), d)
    assert ds.text("demo.head") == "keeps the trailing space \nnext"


def test_pipe2_leading_space_preserved(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, YAML_HEAD_OVERRIDE + "|2-\n        two leading spaces\n      none here\n")
    ds = catalog._load_from(make_defaults(tmp_path), d)
    assert ds.text("demo.head") == "  two leading spaces\nnone here"


def test_leading_empty_line_block_scalar_preserved(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, YAML_HEAD_OVERRIDE + "|\n\n      body\n")
    ds = catalog._load_from(make_defaults(tmp_path), d)
    assert ds.text("demo.head") == "\nbody\n"


def test_packaged_fragments_with_a_leading_newline_in_source_keep_it() -> None:
    from moralstack.orchestration import _policy_helpers
    from moralstack.prompts import hindsight_prompt, perspectives_prompt, simulator_prompt

    ds = default_prompt_set()
    expected = {
        "simulator.enums": simulator_prompt.SIMULATOR_ENUMS,
        "simulator.devcontract_evaluation": simulator_prompt.DEVELOPER_CONTRACT_EVALUATION,
        "simulator.batch.contract_intro": simulator_prompt.SIMULATOR_FULL_STATIC_PREFIX,
        "hindsight.devcontract_evaluation": hindsight_prompt.DEVELOPER_CONTRACT_EVALUATION,
        "perspectives.risk_context_interpretation": perspectives_prompt.RISK_CONTEXT_INTERPRETATION,
        "perspectives.devcontract_evaluation": perspectives_prompt.DEVELOPER_CONTRACT_EVALUATION,
        "policy.safe_complete_instruction": _policy_helpers.SAFE_COMPLETE_GENERATION_INSTRUCTION,
    }
    for fid, source in expected.items():
        assert source.startswith("\n"), fid
        assert ds.text(fid).startswith("\n"), fid


def test_chomping_indicators_keep_strip_clip(tmp_path: Path) -> None:
    cases = {"|-": "a\nb", "|": "a\nb\n", "|+": "a\nb\n\n\n"}
    for i, (indicator, expected) in enumerate(cases.items()):
        d = _write_raw(tmp_path, YAML_HEAD_OVERRIDE + f"{indicator}\n      a\n      b\n\n\n", dirname=f"c{i}")
        assert catalog._load_from(make_defaults(tmp_path), d).text("demo.head") == expected, indicator


@pytest.mark.parametrize("text", ["a\n\nb", "a\n", "\na", ""])
def test_lines_kind_rejects_empty_items(tmp_path: Path, text: str) -> None:
    _expect_error(tmp_path, {"demo.items": {"text": text}}, fragment_id="demo.items")
    broken = mutate(SYNTH_DEFAULTS)
    broken["fragments"]["demo.items"]["text"] = text
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path / "again", broken), None)


def test_lines_accessor(tmp_path: Path) -> None:
    ds = synth(tmp_path, {"demo.items": {"text": "x\ny\nz"}})
    assert ds.lines("demo.items") == ("x", "y", "z")


@pytest.mark.parametrize("field", ["sep", "end"])
@pytest.mark.parametrize("value", ["x", " x ", "\n.", "-"])
def test_sep_and_end_whitespace_only_enforced(tmp_path: Path, field: str, value: str) -> None:
    broken = mutate(SYNTH_DEFAULTS)
    comp = broken["compositions"]["demo.plain"]
    if field == "end":
        comp["end"] = value
    else:
        comp["parts"][1]["sep"] = value
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(make_defaults(tmp_path, broken), None)
    assert info.value.fragment_id == "demo.plain"


def test_sep_and_end_accept_spaces_tabs_newlines(tmp_path: Path) -> None:
    broken = mutate(SYNTH_DEFAULTS)
    broken["compositions"]["demo.plain"]["end"] = " \t\n"
    broken["compositions"]["demo.plain"]["parts"][1]["sep"] = "\t\n\n "
    ds = catalog._load_from(make_defaults(tmp_path, broken), None)
    assert ds.text("demo.plain") == "HEAD\t\n\n TAIL \t\n"


def test_unknown_transform_raises(tmp_path: Path) -> None:
    broken = mutate(SYNTH_DEFAULTS)
    broken["compositions"]["demo.plain"]["parts"][1] = {"ref": "demo.tail", "transform": "shout"}
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(make_defaults(tmp_path, broken), None)
    assert info.value.fragment_id == "demo.plain"


def test_transform_double_braces_applied_once(tmp_path: Path) -> None:
    broken = mutate(SYNTH_DEFAULTS)
    broken["fragments"]["demo.tail"] = {"text": 'T {"k": 1}'}
    broken["compositions"]["demo.plain"]["parts"][1] = {"ref": "demo.tail", "sep": " ", "transform": "double_braces"}
    ds = catalog._load_from(make_defaults(tmp_path, broken), None)
    assert ds.text("demo.plain") == 'HEAD T {{"k": 1}}'  # only the transformed part is doubled
    assert ds.text("demo.head") == "HEAD"
    assert ds._fragments["demo.tail"].text == 'T {"k": 1}'  # the fragment itself is untouched


def test_composition_refs_must_exist_and_be_text_fragments(tmp_path: Path) -> None:
    for ref in ("demo.nope", "demo.tpl", "demo.items", "demo.plain"):
        broken = mutate(SYNTH_DEFAULTS)
        broken["compositions"]["demo.plain"]["parts"].append(ref)
        with pytest.raises(PromptConfigError) as info:
            catalog._load_from(make_defaults(tmp_path / ref, broken), None)
        assert info.value.fragment_id == "demo.plain"


def test_default_tree_structure_errors(tmp_path: Path) -> None:
    cases: dict[str, dict[str, Any]] = {}
    dup = mutate(SYNTH_DEFAULTS)
    dup["compositions"]["demo.head"] = {"parts": ["demo.tail"]}  # same ID as a fragment
    cases["id_in_both_namespaces"] = dup
    bad_id = mutate(SYNTH_DEFAULTS)
    bad_id["fragments"]["Demo.Bad"] = {"text": "x"}
    cases["bad_id"] = bad_id
    no_dot = mutate(SYNTH_DEFAULTS)
    no_dot["fragments"]["nodot"] = {"text": "x"}
    cases["no_dot"] = no_dot
    no_text = mutate(SYNTH_DEFAULTS)
    no_text["fragments"]["demo.head"] = {"kind": "text"}
    cases["no_text"] = no_text
    bad_kind = mutate(SYNTH_DEFAULTS)
    bad_kind["fragments"]["demo.head"] = {"kind": "yaml", "text": "x"}
    cases["bad_kind"] = bad_kind
    stray_key = mutate(SYNTH_DEFAULTS)
    stray_key["fragments"]["demo.head"] = {"text": "x", "extra": 1}
    cases["stray_key"] = stray_key
    ph_on_text = mutate(SYNTH_DEFAULTS)
    ph_on_text["fragments"]["demo.head"] = {"text": "x", "placeholders": ["a"]}
    cases["placeholders_on_text"] = ph_on_text
    empty_parts = mutate(SYNTH_DEFAULTS)
    empty_parts["compositions"]["demo.plain"]["parts"] = []
    cases["empty_parts"] = empty_parts
    for name, obj in cases.items():
        with pytest.raises(PromptConfigError):
            catalog._load_from(make_defaults(tmp_path / name, obj), None)


def test_duplicate_id_across_default_files_raises(tmp_path: Path) -> None:
    d = make_defaults(tmp_path)
    write_yaml(d / "other.yaml", {"version": 1, "fragments": {"demo.head": {"text": "again"}}})
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(d, None)
    assert info.value.fragment_id == "demo.head"


def test_prompts_dir_nonexistent_file_or_dir_raises(tmp_path: Path) -> None:
    d = make_defaults(tmp_path)
    with pytest.raises(PromptConfigError, match="does not exist"):
        catalog._load_from(d, tmp_path / "missing")
    a_file = tmp_path / "afile.yaml"
    a_file.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(PromptConfigError, match="not a directory"):
        catalog._load_from(d, a_file)


def test_prompts_dir_empty_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path), empty)
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path / "again"), "   ")


def test_empty_prompts_dir_error_message(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.md").write_text("not a prompt file", encoding="utf-8")
    with pytest.raises(PromptConfigError) as info:
        load_prompt_set(empty)
    message = str(info.value)
    assert str(empty) in message
    assert "contains no *.yaml override files" in message
    assert "unset" in message and "MORALSTACK_PROMPTS_DIR" in message


def test_prompts_dir_with_only_yml_raises(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "version: 1\nfragments:\n  demo.head: {text: x}\n", name="o.yml")
    with pytest.raises(PromptConfigError, match=r"\.yaml"):
        catalog._load_from(make_defaults(tmp_path), d)


@pytest.mark.parametrize(
    "name", ["o.YAML", "o.Yaml", ".#o.yaml", "o.yaml~", ".o.yaml", "o.yaml.bak", ".o.yaml.swp", "o.yml"]
)
def test_wrong_case_hidden_and_editor_files_are_load_errors(tmp_path: Path, name: str) -> None:
    """Decision (A1): a file that looks like an override but would be skipped is an error, never ignored."""
    d = _write_raw(tmp_path, "version: 1\nfragments:\n  demo.head: {text: x}\n", name="good.yaml")
    (d / name).write_text("version: 1\nfragments:\n  demo.head: {text: y}\n", encoding="utf-8")
    with pytest.raises(PromptConfigError) as info:
        catalog._load_from(make_defaults(tmp_path), d)
    assert info.value.path is not None and name in info.value.path


def test_unrelated_files_and_subdirectories_in_prompts_dir_are_ignored(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "version: 1\nfragments:\n  demo.head: {text: x}\n", name="good.yaml")
    (d / "README.md").write_text("docs", encoding="utf-8")
    (d / "notes.txt").write_text("notes", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "deep.yaml").write_text("not: loaded", encoding="utf-8")
    ds = catalog._load_from(make_defaults(tmp_path), d)
    assert ds.text("demo.head") == "x"


def test_override_files_load_in_sorted_order_and_different_ids_are_fine(tmp_path: Path) -> None:
    d = make_defaults(tmp_path)
    p = tmp_path / "prompts"
    write_yaml(p / "b.yaml", {"version": 1, "fragments": {"demo.tail": {"text": "B"}}})
    write_yaml(p / "a.yaml", {"version": 1, "fragments": {"demo.head": {"text": "A"}}})
    ds = catalog._load_from(d, p)
    assert [f["file"] for f in ds.audit_summary()["override_files"]] == ["a.yaml", "b.yaml"]
    assert ds.text("demo.head") == "A" and ds.text("demo.tail") == "B"


def test_override_file_without_fragments_raises(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "version: 1\n")
    with pytest.raises(PromptConfigError, match="fragments"):
        catalog._load_from(make_defaults(tmp_path), d)


def test_override_file_may_not_define_compositions(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "version: 1\nfragments:\n  demo.head: {text: x}\ncompositions: {}\n")
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path), d)


def test_duplicate_keys_in_one_mapping_are_not_silently_accepted(tmp_path: Path) -> None:
    d = _write_raw(tmp_path, "version: 1\nfragments:\n  demo.head: {text: x}\n  demo.head: {text: y}\n")
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path), d)


def test_unsafe_yaml_tags_are_rejected(tmp_path: Path) -> None:
    d = _write_raw(
        tmp_path,
        "version: 1\nfragments:\n  demo.head:\n    text: !!python/object/apply:os.system ['echo pwned']\n",
    )
    with pytest.raises(PromptConfigError):
        catalog._load_from(make_defaults(tmp_path), d)


def test_text_on_context_aware_composition_raises_typeerror(tmp_path: Path) -> None:
    ds = synth(tmp_path)
    with pytest.raises(TypeError):
        ds.text("demo.aware")
    with pytest.raises(TypeError):
        default_prompt_set().text("risk.intent.system")


def test_accessor_kind_mismatches_raise_typeerror_and_unknown_ids_keyerror(tmp_path: Path) -> None:
    ds = synth(tmp_path)
    with pytest.raises(TypeError):
        ds.text("demo.tpl")
    with pytest.raises(TypeError):
        ds.text("demo.items")
    with pytest.raises(TypeError):
        ds.template("demo.head")
    with pytest.raises(TypeError):
        ds.lines("demo.head")
    with pytest.raises(TypeError):
        ds.render("demo.head", NO_CONTEXT)
    with pytest.raises(TypeError):
        ds.template("demo.aware")
    for call in (ds.text, ds.template, ds.lines, ds.format):
        with pytest.raises(KeyError):
            call("demo.nope")
    with pytest.raises(KeyError):
        ds.render("demo.nope", NO_CONTEXT)
    assert ds.has("demo.head") and ds.has("demo.aware") and not ds.has("demo.nope")


# --------------------------------------------------------------------------------------
# safety audit
# --------------------------------------------------------------------------------------


def test_safety_fragment_ids_are_complete() -> None:
    ds = default_prompt_set()
    ids = set(ds.fragment_ids())
    assert catalog.SAFETY_FRAGMENT_IDS <= ids
    for fid in ids:
        if fid.startswith(("risk.", "critic.", "compliance.", "refusal.")):
            assert fid in catalog.SAFETY_FRAGMENT_IDS, fid
    explicit = {
        "policy.safe_complete_instruction",
        "policy.constrained_instruction",
        "policy.hard_violation_guidance",
        "policy.hard_violation_guidance.unspecified",
        "policy.canary_instruction",
        "policy.anti_leak_rules",
        "retrieval.prefilter.system",
    }
    assert explicit <= catalog.SAFETY_FRAGMENT_IDS
    # nothing else sneaks in
    assert all(
        f.startswith(("risk.", "critic.", "compliance.", "refusal.")) or f in explicit for f in catalog.SAFETY_FRAGMENT_IDS
    )
    assert "policy.system" not in catalog.SAFETY_FRAGMENT_IDS


def test_safety_fragment_ids_not_loaded_by_importing_the_catalog() -> None:
    """Importing the catalog is cheap: no ruamel, no default-set load; the name is served lazily (PEP 562)."""
    code = (
        "import sys\n"
        "from moralstack.prompts import catalog\n"
        "assert 'ruamel.yaml' not in sys.modules, 'ruamel imported at catalog import'\n"
        "assert catalog._DEFAULT is None, 'default set loaded at catalog import'\n"
        "ids = catalog.SAFETY_FRAGMENT_IDS\n"
        "assert catalog._DEFAULT is not None, 'accessing SAFETY_FRAGMENT_IDS must load the default set'\n"
        "assert 'risk.intent.head' in ids and 'policy.system' not in ids\n"
        "assert catalog.SAFETY_FRAGMENT_IDS is ids\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_unknown_module_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        catalog.NOT_A_NAME  # noqa: B018


def test_invalid_fragment_id_error_names_the_offending_key(tmp_path: Path) -> None:
    bad = mutate(SYNTH_DEFAULTS, fragments={"Bad ID": {"text": "x"}})
    with pytest.raises(PromptConfigError) as exc:
        catalog._load_from(make_defaults(tmp_path, bad), None)
    assert "'Bad ID'" in str(exc.value)


def test_safety_override_logs_one_warning_per_fragment(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=catalog.logger.name):
        packaged_with_override(
            tmp_path,
            {
                "risk.intent.step0": {"text": "weaker", "when": "contract_present"},
                "critic.base": {"text": "weaker critic"},
                "policy.system": {"text": "not a safety fragment"},
            },
        )
    safety = [r for r in caplog.records if "safety-relevant fragment" in r.getMessage()]
    assert sorted(re.search(r"fragment (\S+)", r.getMessage()).group(1) for r in safety) == [  # type: ignore[union-attr]
        "critic.base",
        "risk.intent.step0",
    ]
    assert all(r.levelno == logging.WARNING for r in safety)
    assert any("Prompt overrides active" in r.getMessage() for r in caplog.records)
    # a message never contains the override text
    assert not any("weaker" in r.getMessage() for r in caplog.records)


def test_safety_fragments_overridden_lists_text_and_when_overrides(tmp_path: Path) -> None:
    ds = packaged_with_override(
        tmp_path,
        {
            "risk.signals.devcontract_scope": {"when": "contract_present"},
            "compliance.dccl.system": {"text": "changed"},
            "policy.anti_leak_rules": {"text": "changed"},
            "policy.system": {"text": "changed"},
        },
    )
    audit = ds.audit_summary()
    assert audit["safety_fragments_overridden"] == [
        "compliance.dccl.system",
        "policy.anti_leak_rules",
        "risk.signals.devcontract_scope",
    ]


def test_default_set_has_empty_safety_fragments_overridden() -> None:
    assert default_prompt_set().audit_summary()["safety_fragments_overridden"] == []


# --------------------------------------------------------------------------------------
# resolution and identity
# --------------------------------------------------------------------------------------


def test_override_precedence_config_over_env_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_PROMPTS_DIR", raising=False)
    assert resolve_prompts_dir(None) is None
    monkeypatch.setenv("MORALSTACK_PROMPTS_DIR", "/from/env")
    assert resolve_prompts_dir(None) == "/from/env"
    assert resolve_prompts_dir("/explicit") == "/explicit"
    assert resolve_prompts_dir("   ") == "/from/env"
    assert resolve_prompts_dir("") == "/from/env"


@pytest.mark.parametrize("value", ["", "  ", "\t\n"])
def test_resolve_prompts_dir_blank_env_is_none(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MORALSTACK_PROMPTS_DIR", value)
    assert resolve_prompts_dir(None) is None


def test_prompt_set_immutable() -> None:
    ds = default_prompt_set()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ds.ps = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ds.is_default = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        ds._fragments["x"] = None  # type: ignore[index]
    with pytest.raises(TypeError):
        ds._renders["x"] = ""  # type: ignore[index]
    assert not any(hasattr(ds, name) for name in ("override", "set_text", "with_override", "update"))


def _counting_reader(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    real = catalog._read_yaml_file
    read: list[str] = []

    def counting(path: Path, *args: Any, **kwargs: Any) -> Any:
        read.append(Path(path).name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(catalog, "_read_yaml_file", counting)
    return read


def test_load_with_dir_reuses_parsed_packaged_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_prompt_set()  # make sure the singleton (and its parse result) exists
    override_dir = make_override(tmp_path, {"common.output_json_only": {"text": "X\n"}})
    # genuinely cold: no cached singleton or parse result, so every packaged file is read
    with monkeypatch.context() as cold_ctx:
        cold_ctx.setattr(catalog, "_DEFAULT", None)
        cold_ctx.setattr(catalog, "_DEFAULT_PARSED", None)
        cold_read = _counting_reader(cold_ctx)
        cold = catalog._load_from(catalog._DATA_DIR, override_dir)
    assert sorted(cold_read) == sorted([f"{n}.yaml" for n in PACKAGED_FILES] + ["o.yaml"]), cold_read
    assert len(cold_read) == 12
    read = _counting_reader(monkeypatch)
    warm = load_prompt_set(override_dir)
    assert read == ["o.yaml"], f"packaged files were re-read: {read}"
    assert warm.fingerprint == cold.fingerprint
    assert warm.default_fingerprint == cold.default_fingerprint == default_prompt_set().fingerprint
    assert warm.audit_summary() == cold.audit_summary()
    assert {(i, c): warm.render(i, c) for i in warm.composition_ids() for c in ALL_CONTEXTS} == {
        (i, c): cold.render(i, c) for i in cold.composition_ids() for c in ALL_CONTEXTS
    }
    # the cached parse is not mutated by an override load
    assert default_prompt_set().text("common.output_json_only") != "X\n"


def test_custom_defaults_dir_is_parsed_even_when_the_default_set_is_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_prompt_set()
    read = _counting_reader(monkeypatch)
    ds = synth(tmp_path, {"demo.head": {"text": "H2"}})
    assert read == ["demo.yaml", "o.yaml"]
    assert ds.text("demo.head") == "H2"


def test_tilde_in_prompts_dir_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_override(tmp_path, {"common.output_json_only": {"text": "T\n"}}, dirname="home_prompts")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ds = load_prompt_set("~/home_prompts")
    assert ds.text("common.output_json_only") == "T\n"
    assert ds.audit_summary()["prompts_dir"] == "~/home_prompts"


def test_relative_prompts_dir_round_trips_into_the_audit_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_override(tmp_path, {"common.output_json_only": {"text": "R\n"}}, dirname="rel")
    monkeypatch.chdir(tmp_path)
    ds = load_prompt_set("rel")
    assert ds.audit_summary()["prompts_dir"] == "rel"


def test_yaml_anchors_and_aliases_load_under_safe_mode(tmp_path: Path) -> None:
    body = (
        "version: 1\nfragments:\n"
        "  common.output_json_only:\n    text: &a 'shared'\n"
        "  hindsight.batch.instructions:\n    text: *a\n"
    )
    ds = load_prompt_set(_write_raw(tmp_path, body))
    assert ds.text("common.output_json_only") == ds.text("hindsight.batch.instructions") == "shared"


def test_fingerprint_stable_across_loads_and_processes(tmp_path: Path) -> None:
    fresh = catalog._load_from(catalog._DATA_DIR, None)
    assert fresh.fingerprint == default_prompt_set().fingerprint
    code = "from moralstack.prompts.catalog import default_prompt_set as d; print(d().fingerprint)"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=True
    ).stdout.strip()
    assert out == default_prompt_set().fingerprint


def test_fingerprint_changes_with_text_and_with_when(tmp_path: Path) -> None:
    base = synth(tmp_path / "a")
    changed_text = synth(tmp_path / "b", {"demo.head": {"text": "other"}})
    changed_when = synth(tmp_path / "c", {"demo.dc": {"when": "contract_present"}})
    assert len({base.fingerprint, changed_text.fingerprint, changed_when.fingerprint}) == 3
    assert not changed_text.is_default and not changed_when.is_default
    assert changed_text.default_fingerprint == base.fingerprint


def test_is_default_by_content_not_path(tmp_path: Path) -> None:
    base = default_prompt_set()
    same = packaged_with_override(tmp_path, {"common.output_json_only": {"text": base.text("common.output_json_only")}})
    assert same.is_default and same.fingerprint == base.fingerprint and same.ps == base.ps
    assert all(same.cache_tag_for(ctx) == "" for ctx in ALL_CONTEXTS)
    entry = same.audit_summary()["fragments"]["common.output_json_only"]
    assert entry["origin"] == "override"  # still reported as an override of identical content


def test_ps_is_fp16_for_default_and_override(tmp_path: Path) -> None:
    base = default_prompt_set()
    other = packaged_with_override(tmp_path, {"common.output_json_only": {"text": "Output ONLY JSON."}})
    for ds in (base, other):
        assert re.fullmatch(r"[0-9a-f]{16}", ds.ps)
        assert ds.ps == ds.fingerprint[:16]
    assert base.ps != other.ps


def test_request_link_shape(tmp_path: Path) -> None:
    base = default_prompt_set()
    assert base.request_link() == {"ps": base.ps, "is_default": True}
    other = synth(tmp_path, {"demo.head": {"text": "x"}})
    assert other.request_link() == {"ps": other.ps, "is_default": False}


def test_cache_tag_for_default_is_empty_for_all_contexts() -> None:
    ds = default_prompt_set()
    assert [ds.cache_tag_for(ctx) for ctx in ALL_CONTEXTS] == ["", "", "", ""]


def test_cache_tag_for_nondefault_includes_context_bits(tmp_path: Path) -> None:
    ds = synth(tmp_path, {"demo.head": {"text": "x"}})
    tags = {ctx: ds.cache_tag_for(ctx) for ctx in ALL_CONTEXTS}
    assert len(set(tags.values())) == 4
    for ctx, tag in tags.items():
        assert re.fullmatch(r"ps:[0-9a-f]{16}:[01]{2}", tag)
        assert tag == f"ps:{ds.ps}:{int(ctx.contract_present)}{int(ctx.history_present)}"


def test_audit_summary_shape_for_default_and_override(tmp_path: Path) -> None:
    base = default_prompt_set()
    audit = base.audit_summary()
    assert audit["schema"] == 1
    assert audit["set_fingerprint"] == audit["default_fingerprint"] == f"sha256:{base.fingerprint}"
    assert audit["is_default"] is True and audit["prompts_dir"] is None and audit["override_files"] == []
    assert set(audit["fragments"]) == set(base.fragment_ids())
    assert all(set(e) == {"sha256", "when", "origin"} and e["origin"] == "default" for e in audit["fragments"].values())

    d = make_override(
        tmp_path, {"demo.head": {"text": "NEW"}, "demo.dc": {"when": "never"}}, name="o.yaml", dirname="my dir"
    )
    ds = catalog._load_from(make_defaults(tmp_path), d)
    audit = ds.audit_summary()
    assert audit["is_default"] is False and audit["prompts_dir"] == str(d)
    assert [f["file"] for f in audit["override_files"]] == ["o.yaml"]
    assert re.fullmatch(r"[0-9a-f]{64}", audit["override_files"][0]["sha256"])
    head = audit["fragments"]["demo.head"]
    assert head["origin"] == "override" and head["override_file"] == "o.yaml"
    assert head["overridden"] == ["text"] and head["text"] == "NEW"
    dc = audit["fragments"]["demo.dc"]
    assert dc["overridden"] == ["when"] and dc["when"] == "never" and "text" not in dc
    assert audit["fragments"]["demo.tail"]["origin"] == "default"
    assert audit["fragments"]["demo.tail"]["sha256"] == base_sha("TAIL")
    assert prompt_run_meta(ds) == {"prompt_config": audit}


def base_sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_audit_summary_is_deep_copy() -> None:
    ds = default_prompt_set()
    first = ds.audit_summary()
    first["fragments"]["common.output_json_only"]["when"] = "tampered"
    first["safety_fragments_overridden"].append("x")
    second = ds.audit_summary()
    assert second["fragments"]["common.output_json_only"]["when"] == "always"
    assert second["safety_fragments_overridden"] == []


def test_prompt_run_meta_only_for_prompt_sets_and_never_raises() -> None:
    assert prompt_run_meta(None) == {}
    assert prompt_run_meta(object()) == {}
    assert prompt_run_meta("not a set") == {}
    from unittest.mock import MagicMock

    assert prompt_run_meta(MagicMock()) == {}
    assert list(prompt_run_meta(default_prompt_set())) == ["prompt_config"]


def test_loading_logs_one_info_line_without_text(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=catalog.logger.name):
        synth(tmp_path, {"demo.head": {"text": SECRET}})
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "fingerprint" in infos[0].getMessage()
    assert not any(SECRET in r.getMessage() for r in caplog.records)


def test_empty_render_logs_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    only = mutate(SYNTH_DEFAULTS)
    only["compositions"] = {"demo.only": {"context_aware": True, "parts": ["demo.dc"]}}
    d = make_defaults(tmp_path, only)
    with caplog.at_level(logging.WARNING, logger=catalog.logger.name):
        ds = catalog._load_from(d, make_override(tmp_path, {"demo.dc": {"when": "never"}}))
    assert ds.render("demo.only", NO_CONTEXT) == ""
    assert any("demo.only" in r.getMessage() and "empty" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------------------
# T3.3 wire-form braces (computed from the live set)
# --------------------------------------------------------------------------------------


def _ranges_of_text_parts(ds: PromptSet) -> list[tuple[str, str]]:
    out = []
    for cid in ds.composition_ids():
        comp = ds._compositions[cid]
        for part in comp.parts:
            if not part.transform:
                out.append((cid, part.ref))
    return out


def test_text_fragments_render_verbatim() -> None:
    ds = default_prompt_set()
    pairs = _ranges_of_text_parts(ds)
    assert len(pairs) > 40
    for cid, ref in pairs:
        assert ds.text(ref) in ds.render(cid, NO_CONTEXT), (cid, ref)


def test_double_braces_transform_applied_only_to_its_part() -> None:
    ds = default_prompt_set()
    skeleton = ds.text("simulator.schema_skeleton")
    doubled = skeleton.replace("{", "{{").replace("}", "}}")
    retry = ds.render("simulator.retry", NO_CONTEXT)
    expected = (
        ds.text("retry.prefix")
        + ds.text("simulator.retry.values_heading")
        + ds.text("simulator.enums")
        + "\n\n"
        + doubled
        + ds.text("simulator.retry.footer")
    )
    assert retry == expected
    assert doubled in retry and skeleton not in retry


def test_simulator_schema_skeleton_wire_form_has_single_braces() -> None:
    skeleton = default_prompt_set().text("simulator.schema_skeleton")
    assert skeleton == skeleton.replace("{{", "{").replace("}}", "}")
    assert skeleton.startswith("{") and not skeleton.startswith("{{")


def test_perspectives_common_instructions_wire_form() -> None:
    text = default_prompt_set().text("perspectives.common_instructions")
    assert text == text.replace("{{", "{").replace("}}", "}")
    assert "{" in text


def test_override_of_wire_form_fragment_flows_to_retry_with_transform(tmp_path: Path) -> None:
    ds = packaged_with_override(tmp_path, {"simulator.schema_skeleton": {"text": '{"only": [1]}'}})
    assert '{"only": [1]}' in ds.render("simulator.batch.system", NO_CONTEXT)
    assert '{{"only": [1]}}' in ds.render("simulator.retry", NO_CONTEXT)


# --------------------------------------------------------------------------------------
# DUPLICATION GUARD (temporary): packaged YAML == live Python constants == A0 snapshot.
# Deleted, module by module, in the A2x steps where the constants become re-exports.
# --------------------------------------------------------------------------------------


def _live_compositions() -> dict[str, tuple[str, str]]:
    """composition id -> (live value, A0 snapshot key)."""
    from moralstack.models.risk import prompts as rp
    from moralstack.prompts import critic_prompt, hindsight_prompt, perspectives_prompt, retry, simulator_prompt
    from moralstack.runtime.modules import critic_module, hindsight_module

    return {
        "risk.intent.system": (rp.INTENT_CONTEXT_SYSTEM_PROMPT, "const/models.risk.prompts.INTENT_CONTEXT_SYSTEM_PROMPT"),
        "risk.signals.system_head": (rp.HARM_SIGNAL_SYSTEM_PROMPT, "const/models.risk.prompts.HARM_SIGNAL_SYSTEM_PROMPT"),
        "risk.operational.system": (
            rp.OPERATIONAL_RISK_SYSTEM_PROMPT,
            "const/models.risk.prompts.OPERATIONAL_RISK_SYSTEM_PROMPT",
        ),
        "critic.full.system": (
            critic_prompt.CRITIC_FULL_SYSTEM_PROMPT,
            "const/prompts.critic_prompt.CRITIC_FULL_SYSTEM_PROMPT",
        ),
        "critic.quick.system": (
            critic_module.CRITIC_SYSTEM_PROMPT,
            "const/runtime.modules.critic_module.CRITIC_SYSTEM_PROMPT",
        ),
        "critic.retry": (retry.RETRY_CRITIC, "const/prompts.retry.RETRY_CRITIC"),
        "perspectives.system_body": (
            perspectives_prompt.build_perspectives_system_prompt(),
            "builder/perspectives_system_prompt/default",
        ),
        "simulator.batch.system": (
            simulator_prompt.SIMULATOR_BATCH_SYSTEM_PROMPT,
            "const/prompts.simulator_prompt.SIMULATOR_BATCH_SYSTEM_PROMPT",
        ),
        "simulator.seeded.system": (
            simulator_prompt.SIMULATOR_SEEDED_SYSTEM_PROMPT,
            "const/prompts.simulator_prompt.SIMULATOR_SEEDED_SYSTEM_PROMPT",
        ),
        "simulator.retry": (retry.RETRY_SIMULATOR, "const/prompts.retry.RETRY_SIMULATOR"),
        "hindsight.batch.system": (
            hindsight_prompt.HINDSIGHT_BATCH_SYSTEM_PROMPT,
            "const/prompts.hindsight_prompt.HINDSIGHT_BATCH_SYSTEM_PROMPT",
        ),
        "hindsight.single.system": (
            hindsight_module.HINDSIGHT_SINGLE_SYSTEM_PROMPT,
            "const/runtime.modules.hindsight_module.HINDSIGHT_SINGLE_SYSTEM_PROMPT",
        ),
        "hindsight.retry.single": (retry.RETRY_HINDSIGHT_SINGLE, "const/prompts.retry.RETRY_HINDSIGHT_SINGLE"),
        "hindsight.retry.batch": (retry.RETRY_HINDSIGHT_BATCH, "const/prompts.retry.RETRY_HINDSIGHT_BATCH"),
    }


@pytest.fixture(scope="module")
def snapshot_table() -> dict[str, Any]:
    from tests.prompts_golden import capture

    return capture.load_table()


def _snapshot_value(table: dict[str, Any], key: str) -> Any:
    from tests.prompts_golden import capture

    return capture.resolve(table, key)


def _first_diff(a: str, b: str) -> str:
    i = next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    return f"lengths {len(a)} vs {len(b)}, first difference at offset {i}"


def test_default_roundtrip_matches_live_constants(snapshot_table: dict[str, Any]) -> None:
    """Every composition rendered from the packaged YAML equals the live constant AND the A0 snapshot entry."""
    ds = default_prompt_set()
    live = _live_compositions()
    assert set(live) == set(ds.composition_ids()), "a composition is missing from the guard"
    problems: list[str] = []
    for cid, (value, key) in live.items():
        snap = _snapshot_value(snapshot_table, key)
        for ctx in ALL_CONTEXTS if ds.is_context_aware(cid) else [NO_CONTEXT]:
            rendered = ds.render(cid, ctx)
            if rendered != value:
                problems.append(f"{cid} {ctx}: differs from the live constant ({_first_diff(rendered, value)})")
            if rendered != snap:
                problems.append(f"{cid} {ctx}: differs from the A0 snapshot ({_first_diff(rendered, snap)})")
    assert not problems, "\n".join(problems)


def _live_standalone() -> dict[str, tuple[str, str]]:
    """fragment id -> (kind, live value) for fragments that equal an existing module constant."""
    from moralstack.compliance import dccl, safety_override
    from moralstack.constitution import retriever
    from moralstack.models.risk import prompts as rp
    from moralstack.orchestration import _policy_helpers as ph
    from moralstack.orchestration import domain_exclusion
    from moralstack.orchestration import safe_refusal_generator as srg
    from moralstack.prompts import _common, critic_prompt, hindsight_prompt, perspectives_prompt, retry, simulator_prompt
    from moralstack.runtime.modules import (
        critic_module,
        hindsight_module,
        message_context,
        perspective_module,
        simulator_module,
    )
    from moralstack.utils.output_protection import OutputProtector

    out: dict[str, tuple[str, str]] = {
        "common.output_json_only": ("text", _common.OUTPUT_JSON_ONLY),
        "retry.prefix": ("text", retry.RETRY_PREFIX),
        "risk.intent.user": ("template", rp.INTENT_CONTEXT_PROMPT_TEMPLATE),
        "risk.signals.user": ("template", rp.HARM_SIGNAL_USER_TEMPLATE),
        "risk.operational.user": ("template", rp.OPERATIONAL_RISK_PROMPT_TEMPLATE),
        "risk.signals.static_suffix": ("template", rp.HARM_SIGNAL_STATIC_TEMPLATE),
        "critic.base": ("text", critic_prompt._CRITIC_FULL_BASE_PROMPT),
        "critic.full.user": ("template", critic_prompt.CRITIC_FULL_TEMPLATE),
        "critic.quick.user": ("template", critic_module.QUICK_CHECK_PROMPT_TEMPLATE),
        "perspectives.base": ("text", perspective_module.PERSPECTIVE_SYSTEM_PROMPT),
        "perspectives.risk_context_interpretation": ("text", perspectives_prompt.RISK_CONTEXT_INTERPRETATION),
        "perspectives.devcontract_evaluation": ("text", perspectives_prompt.DEVELOPER_CONTRACT_EVALUATION),
        "perspectives.turn_context": ("template", perspectives_prompt._PERSPECTIVE_TURN_CONTEXT_TEMPLATE),
        "perspectives.retry": ("text", retry.RETRY_PERSPECTIVES),
        "simulator.base": ("text", simulator_prompt._SIMULATOR_BASE_SYSTEM_PROMPT),
        "simulator.enums": ("text", simulator_prompt.SIMULATOR_ENUMS),
        "simulator.harm_type_mapping_rule": ("text", simulator_prompt.HARM_TYPE_MAPPING_RULE),
        "simulator.devcontract_evaluation": ("text", simulator_prompt.DEVELOPER_CONTRACT_EVALUATION),
        "simulator.batch.user": ("template", simulator_prompt.SIMULATOR_FULL_TEMPLATE),
        "simulator.seeded.user": ("template", simulator_module.SEEDED_PROMPT_TEMPLATE),
        "simulator.seeded.seeds": ("lines", "\n".join(simulator_module.SCENARIO_SEEDS)),
        "simulator.domain_guidance.cybersecurity": ("text", simulator_prompt.DOMAIN_GUIDANCE["cybersecurity"]),
        "simulator.domain_guidance.default": ("text", simulator_prompt.DEFAULT_DOMAIN_GUIDANCE),
        "hindsight.base": ("text", _common.HINDSIGHT_BASE_FRAMING),
        "hindsight.batch.user": ("template", hindsight_prompt.HINDSIGHT_BATCH_FULL_TEMPLATE),
        "hindsight.single.user": ("template", hindsight_module.HINDSIGHT_PROMPT_TEMPLATE),
        "context.module_preface": ("text", message_context._CONTEXT_REFERENCE_INSTRUCTION),
        "policy.system": ("text", ph.POLICY_SYSTEM_PROMPT),
        "policy.language_rules": ("text", OutputProtector.LANGUAGE_RULES),
        "policy.anti_leak_rules": ("text", OutputProtector.ANTI_LEAK_RULES),
        "policy.safe_complete_instruction": ("text", ph.SAFE_COMPLETE_GENERATION_INSTRUCTION),
        "policy.constrained_instruction": ("text", ph.CONSTRAINED_GENERATION_INSTRUCTION),
        "compliance.dccl.system": ("text", dccl._DCCL_LLM_SYSTEM_PROMPT),
        "compliance.draft_match.system": ("text", dccl.DCCL_DRAFT_MATCH_SYSTEM_PROMPT),
        "compliance.safety_classifier.system": ("text", safety_override._LLM_CLASSIFIER_SYSTEM_PROMPT),
        "retrieval.agent.enhanced.system": ("text", retriever._ENHANCED_DOMAIN_AGENT_SYSTEM_PROMPT),
        "retrieval.agent.legacy.system": ("text", retriever._LEGACY_DOMAIN_AGENT_SYSTEM_PROMPT),
        "refusal.system": ("text", srg._build_system_message()),
        "exclusion.system": ("text", domain_exclusion._build_system_message()),
    }
    for p in perspective_module.DEFAULT_PERSPECTIVES:
        out[f"perspectives.identity.{p.id}"] = ("text", p.prompt_template)
    for harm_type, items in srg._HARM_TYPE_CATEGORIES.items():
        out[f"refusal.category_hints.{harm_type}"] = ("lines", "\n".join(items))
    for domain, items in retriever.EnhancedDomainAgent.DOMAIN_NEGATIVE_EXAMPLES.items():
        out[f"retrieval.agent.negative_examples.{domain}"] = ("lines", "\n".join(items))
    # wire-form fragments: the module constant is the source form with ``{{``/``}}``
    out["simulator.schema_skeleton"] = (
        "text",
        simulator_prompt.SIMULATOR_SCHEMA_SKELETON.replace("{{", "{").replace("}}", "}"),
    )
    common = perspectives_prompt.PERSPECTIVE_COMMON_INSTRUCTIONS
    assert common.endswith(_common.OUTPUT_JSON_ONLY)
    out["perspectives.common_instructions"] = (
        "text",
        common[: -len(_common.OUTPUT_JSON_ONLY)].replace("{{", "{").replace("}}", "}"),
    )
    # the hindsight batch composition carries the contract block's terminal newline in ``end``
    assert hindsight_prompt.DEVELOPER_CONTRACT_EVALUATION.endswith("\n")
    out["hindsight.devcontract_evaluation"] = ("text", hindsight_prompt.DEVELOPER_CONTRACT_EVALUATION[:-1])
    return out


def test_standalone_fragments_equal_live_constants() -> None:
    ds = default_prompt_set()
    problems: list[str] = []
    for fid, (kind, value) in _live_standalone().items():
        frag = ds._fragments.get(fid)
        if frag is None:
            problems.append(f"{fid}: missing from the packaged set")
            continue
        if frag.kind != kind:
            problems.append(f"{fid}: kind {frag.kind} != {kind}")
        if frag.text != value:
            problems.append(f"{fid}: {_first_diff(frag.text, value)}")
    assert not problems, "\n".join(problems)


def test_standalone_constants_equal_the_a0_snapshot(snapshot_table: dict[str, Any]) -> None:
    """The snapshot pins the constants; the YAML is checked against the live ones above and, here, against it."""
    ds = default_prompt_set()
    pinned = {
        "const/prompts._common.OUTPUT_JSON_ONLY": "common.output_json_only",
        "const/prompts.retry.RETRY_PREFIX": "retry.prefix",
        "const/models.risk.prompts.HARM_SIGNAL_STATIC_TEMPLATE": "risk.signals.static_suffix",
        "const/models.risk.prompts.INTENT_CONTEXT_PROMPT_TEMPLATE": "risk.intent.user",
        "const/models.risk.prompts.HARM_SIGNAL_USER_TEMPLATE": "risk.signals.user",
        "const/models.risk.prompts.OPERATIONAL_RISK_PROMPT_TEMPLATE": "risk.operational.user",
        "const/prompts.critic_prompt.CRITIC_FULL_TEMPLATE": "critic.full.user",
        "const/runtime.modules.critic_module.QUICK_CHECK_PROMPT_TEMPLATE": "critic.quick.user",
        "const/prompts._common.HINDSIGHT_BASE_FRAMING": "hindsight.base",
        "const/prompts.simulator_prompt.SIMULATOR_ENUMS": "simulator.enums",
        "const/prompts.simulator_prompt.HARM_TYPE_MAPPING_RULE": "simulator.harm_type_mapping_rule",
        "const/prompts.simulator_prompt.DEVELOPER_CONTRACT_EVALUATION": "simulator.devcontract_evaluation",
        "const/prompts.simulator_prompt.SIMULATOR_FULL_TEMPLATE": "simulator.batch.user",
        "const/runtime.modules.simulator_module.SEEDED_PROMPT_TEMPLATE": "simulator.seeded.user",
        "const/prompts.hindsight_prompt.HINDSIGHT_BATCH_FULL_TEMPLATE": "hindsight.batch.user",
        "const/runtime.modules.hindsight_module.HINDSIGHT_PROMPT_TEMPLATE": "hindsight.single.user",
        "const/prompts.perspectives_prompt.RISK_CONTEXT_INTERPRETATION": "perspectives.risk_context_interpretation",
        "const/prompts.perspectives_prompt.DEVELOPER_CONTRACT_EVALUATION": "perspectives.devcontract_evaluation",
        "const/prompts.perspectives_prompt._PERSPECTIVE_TURN_CONTEXT_TEMPLATE": "perspectives.turn_context",
        "const/runtime.modules.perspective_module.PERSPECTIVE_SYSTEM_PROMPT": "perspectives.base",
        "const/runtime.modules.message_context._CONTEXT_REFERENCE_INSTRUCTION": "context.module_preface",
        "const/orchestration._policy_helpers.POLICY_SYSTEM_PROMPT": "policy.system",
        "const/orchestration._policy_helpers.SAFE_COMPLETE_GENERATION_INSTRUCTION": "policy.safe_complete_instruction",
        "const/orchestration._policy_helpers.CONSTRAINED_GENERATION_INSTRUCTION": "policy.constrained_instruction",
        "const/utils.output_protection.OutputProtector.LANGUAGE_RULES": "policy.language_rules",
        "const/utils.output_protection.OutputProtector.ANTI_LEAK_RULES": "policy.anti_leak_rules",
        "const/compliance.dccl._DCCL_LLM_SYSTEM_PROMPT": "compliance.dccl.system",
        "const/compliance.dccl.DCCL_DRAFT_MATCH_SYSTEM_PROMPT": "compliance.draft_match.system",
        "const/compliance.safety_override._LLM_CLASSIFIER_SYSTEM_PROMPT": "compliance.safety_classifier.system",
        "const/constitution.retriever._ENHANCED_DOMAIN_AGENT_SYSTEM_PROMPT": "retrieval.agent.enhanced.system",
        "const/constitution.retriever._LEGACY_DOMAIN_AGENT_SYSTEM_PROMPT": "retrieval.agent.legacy.system",
    }
    for key, fid in pinned.items():
        snap = _snapshot_value(snapshot_table, key)
        assert ds._fragments[fid].text == snap, f"{fid}: {_first_diff(ds._fragments[fid].text, snap)}"
    # identity texts and keyed lists
    for pid in ("user", "vulnerable", "observer", "adversary", "compliance"):
        key = f"const/runtime.modules.perspective_module.DEFAULT_PERSPECTIVES.{pid}.prompt_template"
        assert ds.text(f"perspectives.identity.{pid}") == _snapshot_value(snapshot_table, key)
    for i, seed in enumerate(ds.lines("simulator.seeded.seeds")):
        assert seed == _snapshot_value(snapshot_table, f"const/runtime.modules.simulator_module.SCENARIO_SEEDS.{i}")
    for harm_type in ("child_exploitation", "financial", "physical", "reputational", "illegal", "emotional_relational"):
        for i, item in enumerate(ds.lines(f"refusal.category_hints.{harm_type}")):
            key = f"const/orchestration.safe_refusal_generator._HARM_TYPE_CATEGORIES.{harm_type}.{i}"
            assert item == _snapshot_value(snapshot_table, key)
    for domain in ("enterprise", "financial", "journalism", "medical", "mental_health", "research"):
        for i, item in enumerate(ds.lines(f"retrieval.agent.negative_examples.{domain}")):
            key = f"const/constitution.retriever.EnhancedDomainAgent.DOMAIN_NEGATIVE_EXAMPLES.{domain}.{i}"
            assert item == _snapshot_value(snapshot_table, key)


def test_dead_path_constants_equal_double_braces_of_default() -> None:
    from moralstack.prompts import perspectives_prompt, simulator_prompt

    ds = default_prompt_set()

    def double(s: str) -> str:
        return s.replace("{", "{{").replace("}", "}}")

    assert simulator_prompt.SIMULATOR_SCHEMA_SKELETON == double(ds.text("simulator.schema_skeleton"))
    assert perspectives_prompt.PERSPECTIVE_COMMON_INSTRUCTIONS == (
        double(ds.text("perspectives.common_instructions")) + ds.text("common.output_json_only")
    )


# -- inline templates: assembled exactly as the code does, compared with the live builders -----


def _delib(**kwargs: Any) -> Any:
    from tests.prompts_golden import capture

    return capture._delib_context(**kwargs)


def test_critic_user_prompt_fragments_reproduce_the_live_builder() -> None:
    from moralstack.prompts.critic_prompt import build_critic_prompt

    ds = default_prompt_set()
    principles = "HARD.TEST.1 [H]: rule one\nSOFT.TEST.1 [S]: rule two"
    cases = {
        "none": ("", ""),
        "violations": ("HARD.TEST.1: leaked {detail}", ""),
        "guidance": ("", "suggest: reframe at a conceptual level"),
        "both": ("  HARD.TEST.1: leaked detail \n", "\tsuggest: reframe  "),
        "blank": ("   ", "\n"),
    }
    for case, (violations, guidance) in cases.items():
        ctx = _delib()
        blocks = []
        if violations and violations.strip():
            blocks.append(ds.format("critic.full.previous_violations_block", previous_violations=violations.strip()))
        if guidance and guidance.strip():
            blocks.append(ds.format("critic.full.previous_guidance_block", previous_guidance=guidance.strip()))
        section = "\n" + "\n\n".join(blocks) if blocks else ""
        got = ds.format(
            "critic.full.user",
            principles=principles,
            risk_signals=ctx.get_risk_signals_str() or "none",
            request=ctx.user_prompt,
            response=ctx.draft_text_full,
            previous_guidance_section=section,
        )
        assert got == build_critic_prompt(ctx, principles, previous_violations=violations, previous_guidance=guidance), case


def test_simulator_hindsight_perspectives_user_prompts_reproduce_the_live_builders() -> None:
    from moralstack.prompts.hindsight_prompt import build_hindsight_prompt
    from moralstack.prompts.perspectives_prompt import build_perspectives_user_prompt
    from moralstack.prompts.simulator_prompt import build_simulator_prompt

    ds = default_prompt_set()
    ctx = _delib()
    risk = ctx.get_risk_signals_str() or "none"
    domain_guidance = ds.text("simulator.domain_guidance.default")
    got = ds.format(
        "simulator.batch.user",
        num_scenarios=3,
        request=ctx.user_prompt,
        response=ctx.draft_text_full,
        risk_signals=risk,
        domain="general",
        domain_guidance=domain_guidance,
    )
    live = build_simulator_prompt(_delib(domain_overlay="no_such_domain"))
    assert got.replace("domain_overlay", "x") == live.replace("no_such_domain", "general")
    cyber = build_simulator_prompt(_delib(domain_overlay="cybersecurity"))
    assert ds.text("simulator.domain_guidance.cybersecurity") in cyber

    assert ds.format(
        "hindsight.batch.user",
        consequences="CONSEQ",
        request=ctx.user_prompt,
        response=ctx.draft_text_full,
        risk_signals=risk,
    ) == build_hindsight_prompt(ctx, "CONSEQ")

    identity = ds.format("perspectives.identity_block", perspective_name="Direct User", perspective_instructions="INSTR")
    assert identity == build_perspectives_user_prompt("Direct User", "INSTR", None)
    with_turn = identity + ds.format(
        "perspectives.turn_context", request=ctx.user_prompt, response=ctx.draft_text_full, risk_signals=risk
    )
    assert with_turn == build_perspectives_user_prompt("Direct User", "INSTR", ctx)


def test_language_prefix_hard_violation_exclusion_and_refusal_fragments_reproduce_the_live_code() -> None:
    from moralstack.orchestration import _policy_helpers as ph
    from moralstack.orchestration import domain_exclusion
    from moralstack.orchestration import safe_refusal_generator as srg
    from moralstack.orchestration.deliberation_runner import _hard_violation_regeneration_guidance
    from moralstack.orchestration.refusal_context import RefusalContext

    ds = default_prompt_set()
    assert ds.format("policy.language_prefix.explicit", language="English", prompt="P {x}") == ph.force_language_prefix(
        "P {x}", "English"
    )
    assert ds.format("policy.language_prefix.implicit", prompt="P {x}") == ph.force_language_prefix("P {x}")
    assert ds.format("policy.hard_violation_guidance", ids="A, B") == _hard_violation_regeneration_guidance(["A", "B"])
    assert ds.format(
        "policy.hard_violation_guidance", ids=ds.text("policy.hard_violation_guidance.unspecified")
    ) == _hard_violation_regeneration_guidance([])
    assert ds.format("exclusion.user", domain="political", prompt_snippet="hi {x}") == domain_exclusion._build_user_message(
        "political", "hi {x}"
    )

    def hints(items: tuple[str, ...]) -> str:
        return "\n".join(f"    - {b}" for b in items)

    # evidence mode (refusal context present), with and without a redirection in the context
    for redirection in ("red it {x}", ""):
        ctx = RefusalContext(
            risk_category="x",
            harm_type="reputational",
            request_type="clear_harm",
            rationale="rat {braces} here",
            operational_risk="HIGH",
            requested_instructions=True,
            intent_operational=True,
            safe_refusal_focus="focus",
            refusal_redirection=redirection,
        )
        live = srg._build_user_message(
            "do {bad} thing", "RC", ["A", "B"], "Italian", "finance", "ignored", "", ctx, ("t1", "t2")
        )
        evidence = ds.format(
            "refusal.evidence_block",
            harm_type="reputational",
            request_type="clear_harm",
            rationale_text="rat {braces} here",
            safe_refusal_focus="focus",
            operational_risk="HIGH",
            requested_instructions=True,
            intent_operational=True,
            redirection_guidance=redirection or ds.text("refusal.default_redirection"),
        )
        got = ds.format(
            "refusal.user",
            request_context_block=ds.format("refusal.request_context_block", user_prompt="do {bad} thing"),
            risk_category="RC",
            reasons_str="A, B",
            domain_str="finance",
            context_block=evidence,
            leak_avoidance_block=ds.format("refusal.leak_avoidance_block", terms="t1, t2"),
            category_hints=hints(ds.lines("refusal.category_hints.reputational")),
            language="Italian",
        )
        assert got == live, _first_diff(got, live)
    # legacy mode (no refusal context): rationale and domain blocks, fallback hints
    live = srg._build_user_message("req", "RC", [], "English", "finance", "some rationale", "do X", None, ())
    context = (
        ds.format("refusal.legacy_rationale_block", rationale="some rationale")
        + ds.format("refusal.legacy_domain_block", domain="finance")
        + ds.format("refusal.legacy_domain_redirection", domain="finance", redirection="do X")
    )
    got = ds.format(
        "refusal.user",
        request_context_block=ds.format("refusal.request_context_block", user_prompt="req"),
        risk_category="RC",
        reasons_str="policy_decision",
        domain_str="finance",
        context_block=context,
        leak_avoidance_block="",
        category_hints=hints(ds.lines("refusal.category_hints.fallback")),
        language="English",
    )
    assert got == live, _first_diff(got, live)


def test_canary_and_estimator_fragments_reproduce_the_live_code() -> None:
    from moralstack.models.risk.estimator import _risk_context_messages
    from moralstack.utils.output_protection import CanaryProtection

    ds = default_prompt_set()
    canary = CanaryProtection(rotate_per_request=False)
    assert canary.inject("") == ds.format("policy.canary_instruction", canary=canary.current_canary)
    messages = _risk_context_messages(system_prompt="S", user_prompt="U {x}", developer_contract_text="contract")
    assert messages[-1]["content"] == ds.text("risk.context_preface") + "U {x}"


def test_risk_principles_block_and_signal_renderer_fragments_reproduce_the_live_code(snapshot_table: dict[str, Any]) -> None:
    from moralstack.models.risk.signals import prompt_renderer
    from moralstack.models.risk.signals.registry import registry

    ds = default_prompt_set()
    none = ds.text("risk.intent.principles_none_found")
    soft = "- SOFT.TEST.1 [SOFT]: Soft title\n  Rule: Short soft rule."
    block = ds.format("risk.intent.principles_block", hard_principles=none, soft_principles=soft)
    assert block == _snapshot_value(snapshot_table, "builder/risk_principles_block/soft_only_hard_none_found")

    # evaluation order items and the output schema header/footer
    items = [
        ds.format("risk.signals.evaluation_order_item", topic_hints=sig.short_description, key=sig.key)
        for sig in registry.signals.values()
        if sig.effects.priority_resolution_in_prompt
    ]
    assert "\n\n".join(items) == prompt_renderer.render_evaluation_order(registry)
    lines = [ds.format("risk.signals.output_schema_header", field_count=len(registry.signals) + 1), "", "{{"]
    lines += [f'  "{sig.key}": "yes" | "no",' for sig in registry.signals.values()]
    lines += ['  "domain_sensitivity": "LOW" | "MEDIUM" | "HIGH"', "}}", "", ds.text("risk.signals.output_schema_footer")]
    assert "\n".join(lines) == prompt_renderer.render_output_schema(registry)


def test_policy_rewrite_contract_regeneration_and_draft_match_fragments_match_the_a0_snapshot(
    snapshot_table: dict[str, Any],
) -> None:
    ds = default_prompt_set()
    # rewrite: default system + constraints appended with no separator (a pinned quirk), user prompt template
    prompt = ds.format(
        "policy.rewrite.user", prompt="ORIGINAL USER PROMPT", draft="The current draft.", guidance="make it more balanced"
    )
    system = ds.text("policy.rewrite.default_system") + ds.text("policy.rewrite.constraints")
    assert prompt == _snapshot_value(snapshot_table, "wire/policy_rewrite/default_system/call#0/prompt")
    assert system == _snapshot_value(snapshot_table, "wire/policy_rewrite/default_system/call#0/system")

    # contract regeneration, single turn (no transcript) and multi turn (transcript block)
    single = ds.format(
        "policy.contract_regeneration.user",
        action_ref="the authorized action summary",
        transcript_block="",
        request="final request",
    )
    assert single == _snapshot_value(snapshot_table, "wire/contract_regeneration/single_turn/call#0/prompt")
    multi = _snapshot_value(snapshot_table, "wire/contract_regeneration/multi_turn/call#0/prompt")
    marker = ds.format("policy.contract_regeneration.transcript_block", transcript="@@T@@")
    head, _sep, tail = marker.partition("@@T@@")
    assert tail == ""
    start = multi.index(head)
    transcript = multi[start + len(head) : multi.rindex("\n\nFinal user request: ")]
    assert transcript, "the multi-turn snapshot must contain a transcript"
    assert multi == ds.format(
        "policy.contract_regeneration.user",
        action_ref="the authorized action summary",
        transcript_block=ds.format("policy.contract_regeneration.transcript_block", transcript=transcript),
        request="final request",
    )

    # draft match: action[:500] / draft[:1000]
    for case, action, draft in (
        ("default", "authorized action", "A different draft that does not contain it."),
        ("long", "a" * 700, "d" * 1200),
    ):
        got = ds.format("compliance.draft_match.user", action=action[:500], draft=draft[:1000])
        assert got == _snapshot_value(snapshot_table, f"wire/draft_match/{case}/call#0/prompt"), case


def test_dccl_fragments_reproduce_the_live_builders() -> None:
    from moralstack.compliance.dccl import DeveloperContractComplianceLayer
    from moralstack.orchestration.conversation_context import build_conversation_context
    from tests.prompts_golden import capture

    ds = default_prompt_set()
    layer = DeveloperContractComplianceLayer(policy=None)
    raw = capture.CONTRACT_TEXT
    ctx_native = build_conversation_context(
        [
            {"role": "system", "content": raw},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "final request"},
        ]
    )
    ctx_big = build_conversation_context(
        [
            {"role": "system", "content": raw},
            {"role": "user", "content": "a" * 4000},
            {"role": "assistant", "content": "b" * 4000},
            {"role": "user", "content": "final request"},
        ]
    )

    def user_prompt(raw_text: str, draft: str, ctx: Any) -> str:
        truncated_draft = draft[:1000] if draft else ds.text("compliance.dccl.no_draft")
        transcript, truncated = ctx.role_serialized_transcript(budget=5000) if ctx is not None else ("", False)
        if not transcript:
            transcript = ds.format("compliance.dccl.transcript_fallback", user_prompt="final request")
        note = ds.text(
            "compliance.dccl.transcript_truncated_note" if truncated else "compliance.dccl.transcript_complete_note"
        )
        return ds.format(
            "compliance.dccl.fallback_user",
            contract=raw_text[:3000],
            transcript=transcript,
            truncation_note=note,
            user_prompt="final request",
            draft=truncated_draft,
        )

    cases = [
        (raw, "Draft text.", None),
        (raw, "", None),
        (raw, "Draft text.", ctx_native),
        (raw, "d" * 1500, ctx_big),
        ("c" * 3500, "Draft text.", None),
    ]
    for raw_text, draft, ctx in cases:
        live = layer._build_llm_user_prompt(raw_text, "final request", draft, ctx)
        assert user_prompt(raw_text, draft, ctx) == live
        trailer = layer._build_llm_messages(raw_text, "final request", draft, ctx)[-1]["content"]
        expected = ds.format(
            "compliance.dccl.native_trailer",
            draft=draft[:1000] if draft else ds.text("compliance.dccl.no_draft"),
        )
        assert expected == trailer


def test_retrieval_prefilter_and_agent_fragments_reproduce_the_live_code(snapshot_table: dict[str, Any]) -> None:
    from moralstack.constitution import retriever as rt

    ds = default_prompt_set()
    prefilter = rt.DomainPrefilter.__new__(rt.DomainPrefilter)
    prefilter.max_domains = 3
    live = prefilter._build_prefilter_system_prompt("- a: b.\n- c {x}")
    assert ds.format("retrieval.prefilter.system", domain_list="- a: b.\n- c {x}", max_domains=3) == live
    snap_user = _snapshot_value(snapshot_table, "wire/prefilter/real_catalog/call#0/m1/content")
    assert snap_user == ds.format("retrieval.prefilter.user", query="How do I synthesize a controlled substance at home?")

    from moralstack.constitution.prompt_formatter import format_principles_for_prompt
    from tests.prompts_golden import capture

    principles = capture._principles()
    summary = [{"id": p.id, "title": p.title, "rule": p.rule, "level": p.level} for p in principles]
    principles_text = format_principles_for_prompt(summary, include_level=True, style="compact", max_rule_len=233)
    query = 'Query with "quotes" and {braces}'
    negatives = "\n".join(f"- {ex}" for ex in ds.lines("retrieval.agent.negative_examples.financial"))
    enhanced = ds.format(
        "retrieval.agent.enhanced.user",
        domain_upper="FINANCIAL",
        domain_desc="Financial domain description.",
        query=query,
        principles_text=principles_text,
        domain_name="financial",
        negative_section=ds.format("retrieval.agent.negative_section", examples=negatives),
    )
    assert enhanced == _snapshot_value(snapshot_table, "wire/enhanced_domain_agent/with_negatives/call#0/m1/content")
    plain = ds.format(
        "retrieval.agent.enhanced.user",
        domain_upper="LEGAL",
        domain_desc="Principles specific to legal domain",
        query=query,
        principles_text=principles_text,
        domain_name="legal",
        negative_section="",
    )
    assert plain == _snapshot_value(snapshot_table, "wire/enhanced_domain_agent/without_negatives/call#0/m1/content")
    legacy = ds.format("retrieval.agent.legacy.user", domain_upper="LEGAL", query=query, principles_text=principles_text)
    assert legacy == _snapshot_value(snapshot_table, "wire/legacy_domain_agent/default/call#0/m1/content")


def test_risk_head_fragments_end_at_the_existing_partition_markers() -> None:
    """Independent oracle for the DC cut boundaries (deleted in A2a with the rest of the guard).

    The live constants are split on the partition markers that
    tests/test_estimator_developer_contract_interpretation.py already relies on.
    """
    from moralstack.models.risk import prompts as rp

    ds = default_prompt_set()
    cuts = {
        "risk.intent.head": (
            rp.INTENT_CONTEXT_SYSTEM_PROMPT,
            "\n\n6. DEVELOPER CONTRACT \u2014 PRIORITY OVER ALL OTHER FRAMINGS.",
        ),
        "risk.signals.head": (rp.HARM_SIGNAL_SYSTEM_PROMPT, "\n\n4. DEVELOPER CONTRACT SCOPE."),
        "risk.operational.head": (rp.OPERATIONAL_RISK_SYSTEM_PROMPT, "\n\n7. DEVELOPER CONTRACT SCOPE."),
    }
    for fid, (live, marker) in cuts.items():
        head, sep, tail = live.partition(marker)
        assert sep and tail, fid
        assert ds.text(fid) == head.rstrip("\n"), f"{fid}: {_first_diff(ds.text(fid), head)}"
        assert "DEVELOPER CONTRACT" not in ds.text(fid), fid


def test_every_packaged_fragment_is_covered_by_the_duplication_guard() -> None:
    """A fragment that no guard compares with the live code would be an unchecked copy."""
    ds = default_prompt_set()
    covered: set[str] = set(_live_standalone())
    for cid in ds.composition_ids():
        covered.update(part.ref for part in ds._compositions[cid].parts)
    covered.update(
        {
            # compared with the live builders / the A0 snapshot in the inline-template tests above
            "critic.full.previous_violations_block",
            "critic.full.previous_guidance_block",
            "perspectives.identity_block",
            "policy.language_prefix.explicit",
            "policy.language_prefix.implicit",
            "policy.hard_violation_guidance",
            "policy.hard_violation_guidance.unspecified",
            "policy.canary_instruction",
            "policy.rewrite.user",
            "policy.rewrite.default_system",
            "policy.rewrite.constraints",
            "policy.contract_regeneration.user",
            "policy.contract_regeneration.transcript_block",
            "exclusion.user",
            "refusal.user",
            "refusal.evidence_block",
            "refusal.default_redirection",
            "refusal.legacy_rationale_block",
            "refusal.legacy_domain_block",
            "refusal.legacy_domain_redirection",
            "refusal.request_context_block",
            "refusal.leak_avoidance_block",
            "refusal.category_hints.fallback",
            "compliance.dccl.native_trailer",
            "compliance.dccl.no_draft",
            "compliance.dccl.fallback_user",
            "compliance.dccl.transcript_truncated_note",
            "compliance.dccl.transcript_complete_note",
            "compliance.dccl.transcript_fallback",
            "compliance.draft_match.user",
            "retrieval.prefilter.system",
            "retrieval.prefilter.user",
            "retrieval.agent.enhanced.user",
            "retrieval.agent.legacy.user",
            "retrieval.agent.negative_section",
            "risk.context_preface",
            "risk.intent.principles_block",
            "risk.intent.principles_none_found",
            "risk.signals.evaluation_order_item",
            "risk.signals.output_schema_header",
            "risk.signals.output_schema_footer",
        }
    )
    missing = sorted(set(ds.fragment_ids()) - covered)
    assert not missing, f"fragments not covered by any duplication-guard check: {missing}"
    assert not sorted(covered - set(ds.fragment_ids())), "the guard names a fragment that no longer exists"
