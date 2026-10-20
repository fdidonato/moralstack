"""R3 brace collapse of the harm-signal static suffix, catalog side (step A1).

``risk.signals.static_suffix`` is a ``template`` fragment: the renderer formats it with the five
registry-derived sections and then collapses ``{{``/``}}`` to single braces (the sections already contain
the JSON skeleton written with ``{{``). The renderer is not rewired to the catalog until A2a, so these tests
reproduce the renderer's two steps on top of the catalog fragments and compare them with the live
``get_harm_signal_prompts``. The cases that need the renderer itself to consume a ``PromptSet`` (a
``prompt_set=`` keyword and an override reaching ``get_harm_signal_prompts``) are deferred to A2a.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from moralstack.models.risk.signals import prompt_renderer
from moralstack.models.risk.signals.registry import registry
from moralstack.prompts.catalog import NO_CONTEXT, PromptSet, default_prompt_set, load_prompt_set

SECTION_NAMES = (
    "evaluation_order_section",
    "signal_definitions_section",
    "domain_sensitivity_section",
    "coherence_rules_section",
    "output_schema_section",
)


def _sections() -> dict[str, str]:
    return {
        "evaluation_order_section": prompt_renderer.render_evaluation_order(registry),
        "signal_definitions_section": prompt_renderer.render_signal_definitions(registry),
        "domain_sensitivity_section": prompt_renderer.render_domain_sensitivity(registry),
        "coherence_rules_section": prompt_renderer.render_coherence_rules(registry),
        "output_schema_section": prompt_renderer.render_output_schema(registry),
    }


def _static_suffix(ds: PromptSet) -> str:
    """The renderer's two steps (format, then collapse), applied to the catalog fragment."""
    return ds.format("risk.signals.static_suffix", **_sections()).replace("{{", "{").replace("}}", "}")


def _override(tmp_path: Path, fragments: dict[str, Any]) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "o.yaml").write_text(json.dumps({"version": 1, "fragments": fragments}), encoding="utf-8")
    return d


def test_static_suffix_is_formatted_then_collapsed_like_the_live_renderer() -> None:
    ds = default_prompt_set()
    live_system, live_user = prompt_renderer.get_harm_signal_prompts(registry)
    system = ds.render("risk.signals.system_head", NO_CONTEXT) + "\n\n" + _static_suffix(ds)
    assert system == live_system
    assert ds.template("risk.signals.user") == live_user
    assert "{{" not in system and "}}" not in system  # the skeleton braces were collapsed exactly once


def test_collapse_is_applied_after_format_so_section_values_are_collapsed_too() -> None:
    ds = default_prompt_set()
    sections = _sections()
    assert "{{" in sections["output_schema_section"]  # the registry-derived section carries the escaped skeleton
    formatted = ds.format("risk.signals.static_suffix", **sections)
    assert "{{" in formatted  # format() does not collapse values
    assert "{{" not in formatted.replace("{{", "{").replace("}}", "}")


def test_template_override_with_double_braces_reaches_the_wire_as_single_braces(tmp_path: Path) -> None:
    body = "\n".join("{" + name + "}" for name in SECTION_NAMES)
    ds = load_prompt_set(_override(tmp_path, {"risk.signals.static_suffix": {"text": body + '\n{{"a": 1}}\n'}}))
    suffix = _static_suffix(ds)
    assert suffix.endswith('{"a": 1}\n')
    assert '{{"a": 1}}' not in suffix


def test_text_kind_override_with_double_braces_is_not_collapsed(tmp_path: Path) -> None:
    ds = load_prompt_set(_override(tmp_path, {"critic.base": {"text": 'Reply {{"k": 1}} only.'}}))
    assert ds.text("critic.base") == 'Reply {{"k": 1}} only.'
    assert 'Reply {{"k": 1}} only.' in ds.render("critic.full.system", NO_CONTEXT)
    assert ds.render("critic.quick.system", NO_CONTEXT) == 'Reply {{"k": 1}} only.'


def test_override_omitting_a_section_placeholder_drops_that_section(tmp_path: Path) -> None:
    ds = load_prompt_set(_override(tmp_path, {"risk.signals.static_suffix": {"text": "{signal_definitions_section}\n"}}))
    suffix = _static_suffix(ds)
    assert suffix == _sections()["signal_definitions_section"] + "\n"
    assert ds.audit_summary()["fragments"]["risk.signals.static_suffix"]["missing_placeholders"] == [
        "evaluation_order_section",
        "domain_sensitivity_section",
        "coherence_rules_section",
        "output_schema_section",
    ]
