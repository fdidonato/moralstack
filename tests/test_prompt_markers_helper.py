"""The shared marker helper used by the A2 steps (``tests/prompt_markers.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moralstack.prompts.catalog import NO_CONTEXT, default_prompt_set, load_prompt_set
from tests.prompt_markers import make_marker_dir, make_when_override_dir, marker_for


def _all_ids() -> tuple[str, ...]:
    return default_prompt_set().fragment_ids()


def test_marker_for_formats_the_id() -> None:
    assert marker_for("risk.intent.head") == "<<M:risk.intent.head>>"
    assert marker_for("a.b", "[[{id}]]") == "[[a.b]]"


def test_marker_dir_loads_and_marks_every_packaged_fragment(tmp_path: Path) -> None:
    default = default_prompt_set()
    ds = load_prompt_set(make_marker_dir(tmp_path))
    assert ds.fragment_ids() == default.fragment_ids()
    assert ds.fingerprint != default.fingerprint and not ds.is_default
    audit = ds.audit_summary()["fragments"]
    for fid in _all_ids():
        frag = ds._fragments[fid]
        assert frag.text.endswith(marker_for(fid)), fid
        assert frag.text.startswith(default._fragments[fid].text), fid
        assert audit[fid]["origin"] == "override" and audit[fid]["overridden"] == ["text"], fid
        assert frag.missing_placeholders == (), fid
        assert frag.placeholders == default._fragments[fid].placeholders, fid
        assert frag.kind == default._fragments[fid].kind and frag.when == default._fragments[fid].when, fid


def test_marker_dir_templates_still_format(tmp_path: Path) -> None:
    ds = load_prompt_set(make_marker_dir(tmp_path))
    default = default_prompt_set()
    for fid in _all_ids():
        frag = default._fragments[fid]
        if frag.kind == "template":
            values = {p: f"<{p}>" for p in frag.placeholders}
            assert ds.format(fid, **values) == default.format(fid, **values) + "\n" + marker_for(fid), fid


def test_marker_dir_reaches_the_compositions(tmp_path: Path) -> None:
    ds = load_prompt_set(make_marker_dir(tmp_path))
    default = default_prompt_set()
    for cid in ds.composition_ids():
        rendered = ds.render(cid, NO_CONTEXT)
        assert rendered != default.render(cid, NO_CONTEXT), cid
        for part in ds._compositions[cid].parts:
            assert marker_for(part.ref) in rendered, (cid, part.ref)


def test_marker_dir_ids_subset_and_custom_marker(tmp_path: Path) -> None:
    ds = load_prompt_set(make_marker_dir(tmp_path, ids=["policy.system", "risk.intent.head"], marker="[[{id}]]"))
    overridden = sorted(fid for fid, e in ds.audit_summary()["fragments"].items() if e["origin"] == "override")
    assert overridden == ["policy.system", "risk.intent.head"]
    assert ds._fragments["policy.system"].text.endswith("[[policy.system]]")


def test_marker_dirs_from_one_tmp_path_are_distinct(tmp_path: Path) -> None:
    first = make_marker_dir(tmp_path, ids=["policy.system"])
    second = make_marker_dir(tmp_path, ids=["policy.system"])
    assert first != second and first.is_dir() and second.is_dir()


def test_marker_with_braces_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_marker_dir(tmp_path, ids=["policy.system"], marker="{{{id}}}")


def test_when_override_dir_changes_only_when(tmp_path: Path) -> None:
    default = default_prompt_set()
    aware_only = {
        part.ref
        for cid in default.composition_ids()
        if default.is_context_aware(cid)
        for part in default._compositions[cid].parts
    } - {
        part.ref
        for cid in default.composition_ids()
        if not default.is_context_aware(cid)
        for part in default._compositions[cid].parts
    }
    target = sorted(aware_only)[0]
    ds = load_prompt_set(make_when_override_dir(tmp_path, {target: "contract_present"}))
    assert not ds.is_default and ds.fingerprint != default.fingerprint
    entry = ds.audit_summary()["fragments"][target]
    assert entry["overridden"] == ["when"] and entry["when"] == "contract_present"
    assert ds._fragments[target].text == default._fragments[target].text
    others = [e for fid, e in ds.audit_summary()["fragments"].items() if fid != target]
    assert all(e["origin"] == "default" for e in others)
