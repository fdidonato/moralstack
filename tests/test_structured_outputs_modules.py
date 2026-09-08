"""Strict Structured Outputs across the governance modules.

Every module used to run in plain JSON mode, so the provider guaranteed only
that the reply parsed: key names and enums were checked client-side, or not at
all. Measured on the COMPL-AI T=0/T=1 campaigns (144k calls), that let three
failure modes through silently, none of which raised or retried:

  * a "data" envelope instead of the harm signals (41 calls) - a valid dict, so
    the tolerant data.get(key, "no") in calibration.py read all 17 harm
    questions as absent;
  * "q10_weapons_explosives_txins" for "..._toxins" (5 calls) - one character,
    and q10 is in priority_harmful, i.e. a hard signal degraded to "no";
  * an invented harm_type (16 calls: security, privacy, deception,
    manipulation, educational_integrity).

A provider-enforced schema makes all three impossible at the source. These
tests pin each schema against the contract its prompt declares, so the two
cannot drift.

The critic is deliberately absent: it has zero parse failures over 5,000
campaign calls and is P0, so it is handled separately.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from moralstack.compliance.dccl import draft_match_response_format
from moralstack.compliance.safety_override import (
    SAFETY_OVERRIDE_CATEGORIES,
    safety_override_response_format,
)
from moralstack.models.risk.prompts import intent_response_format, operational_response_format
from moralstack.models.risk.signals.prompt_renderer import (
    harm_signal_response_format,
    render_output_json_schema,
    render_output_schema,
)
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.utils.structured_output import (
    _HARM_SCOPE,
    _HARM_TYPE,
    _SCENARIO_TYPE,
    HindsightSingleEvaluationOutput,
    hindsight_batch_response_format,
    hindsight_single_response_format,
    perspective_response_format,
    simulator_response_format,
)

ALL_FORMATS = {
    "simulator": simulator_response_format,
    "harm_signals": lambda: harm_signal_response_format(signal_registry),
    "risk_intent": intent_response_format,
    "risk_operational": operational_response_format,
    "hindsight_single": hindsight_single_response_format,
    "hindsight_batch": hindsight_batch_response_format,
    "perspective": perspective_response_format,
    "safety_override": safety_override_response_format,
    "draft_match": draft_match_response_format,
}


def _objects(schema: Any):
    """Yield every object node in a schema, however nested."""
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        yield schema
    items = schema.get("items")
    if isinstance(items, dict):
        yield from _objects(items)
    props = schema.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            yield from _objects(sub)


@pytest.mark.parametrize("name", sorted(ALL_FORMATS))
class TestStrictModeContract:
    """OpenAI strict mode is only honoured when these hold at every level."""

    def test_declares_strict_json_schema(self, name):
        fmt = ALL_FORMATS[name]()
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["name"]

    def test_every_object_forbids_extra_and_requires_all(self, name):
        schema = ALL_FORMATS[name]()["json_schema"]["schema"]
        found = list(_objects(schema))
        assert found, "schema declares no object node"
        for obj in found:
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])

    def test_no_unsupported_numeric_bounds(self, name):
        # The supported JSON Schema subset has no minimum/maximum: declaring
        # them would be silently ignored, so range checks stay client-side.
        schema = ALL_FORMATS[name]()["json_schema"]["schema"]
        for obj in _objects(schema):
            for field in obj["properties"].values():
                assert "minimum" not in field and "maximum" not in field


def test_schema_names_are_unique():
    names = [f()["json_schema"]["name"] for f in ALL_FORMATS.values()]
    assert len(names) == len(set(names))


class TestDerivedFromASingleSource:
    """A schema written by hand next to its validator would drift. These do not."""

    def test_simulator_enums_come_from_the_pydantic_literals(self):
        props = simulator_response_format()["json_schema"]["schema"]["properties"]["consequences"]["items"]["properties"]
        assert props["scenario_type"]["enum"] == list(get_args(_SCENARIO_TYPE))
        assert props["harm_type"]["enum"] == list(get_args(_HARM_TYPE))
        assert props["harm_scope"]["enum"] == list(get_args(_HARM_SCOPE))

    def test_harm_signal_schema_matches_the_prompt_it_ships_with(self):
        schema = render_output_json_schema(signal_registry)
        prompt_text = render_output_schema(signal_registry)
        for key in schema["properties"]:
            assert f'"{key}"' in prompt_text, f"{key} is enforced but never described to the model"
        # The exact key whose misspelling silently degraded a hard signal.
        assert "q10_weapons_explosives_toxins" in schema["properties"]

    def test_harm_signals_are_yes_no_only(self):
        schema = render_output_json_schema(signal_registry)
        for key, field in schema["properties"].items():
            if key == "domain_sensitivity":
                assert field["enum"] == ["LOW", "MEDIUM", "HIGH"]
            else:
                assert field["enum"] == ["yes", "no"]

    def test_safety_override_enum_comes_from_the_category_tuple(self):
        category = safety_override_response_format()["json_schema"]["schema"]["properties"]["category"]
        assert category["enum"] == [*SAFETY_OVERRIDE_CATEGORIES, None]
        # null is the normal answer ("no restricted category"), not an error.
        assert category["type"] == ["string", "null"]

    def test_hindsight_batch_reuses_the_single_evaluation_schema(self):
        single = hindsight_single_response_format()["json_schema"]["schema"]
        batch_item = hindsight_batch_response_format()["json_schema"]["schema"]["properties"]["evaluations"]["items"]
        assert batch_item is single
        assert set(single["properties"]) == set(HindsightSingleEvaluationOutput.model_fields)


class TestObservedFailureModesAreNowImpossible:
    """Each assertion maps to a failure measured in the campaign data."""

    def test_a_data_envelope_cannot_replace_the_harm_signals(self):
        schema = render_output_json_schema(signal_registry)
        assert "data" not in schema["properties"]
        assert schema["additionalProperties"] is False

    def test_the_misspelled_weapons_key_is_not_accepted(self):
        props = render_output_json_schema(signal_registry)["properties"]
        assert "q10_weapons_explosives_txins" not in props

    def test_invented_harm_types_are_not_in_the_intent_enum(self):
        enum = intent_response_format()["json_schema"]["schema"]["properties"]["harm_type"]["enum"]
        for invented in ("security", "privacy", "deception", "manipulation", "educational_integrity"):
            assert invented not in enum

    def test_misinformation_is_a_harm_type_not_a_scenario_type(self):
        props = simulator_response_format()["json_schema"]["schema"]["properties"]["consequences"]["items"]["properties"]
        assert "misinformation" in props["harm_type"]["enum"]
        assert "misinformation" not in props["scenario_type"]["enum"]

    def test_detected_language_stays_a_free_string(self):
        # 46 distinct ISO 639-1 codes were observed: enumerating it would turn a
        # working reply into a refusal.
        field = intent_response_format()["json_schema"]["schema"]["properties"]["detected_language"]
        assert field == {"type": "string"}

    def test_intent_operational_stays_a_boolean(self):
        # Its neighbours are yes/no strings; the contract declares a bool and all
        # 16,739 logged replies used one.
        field = intent_response_format()["json_schema"]["schema"]["properties"]["intent_operational"]
        assert field == {"type": "boolean"}


class TestTheSchemaActuallyReachesTheApi:
    """A schema declared but dropped on the way to the API would prove nothing.

    Both call paths are covered: `generate` (legacy) and `generate_messages`
    (used whenever a developer contract or history is present).
    """

    @staticmethod
    def _policy(model: str = "gpt-4o"):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from moralstack.models.policy import OpenAIPolicy

        policy = OpenAIPolicy(api_key="sk-test", model=model)
        choice = SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        policy.client = MagicMock()
        policy.client.chat.completions.create.return_value = SimpleNamespace(choices=[choice], usage=usage)
        return policy

    def _sent(self, policy):
        return policy.client.chat.completions.create.call_args.kwargs.get("response_format")

    @pytest.mark.parametrize("name", sorted(ALL_FORMATS))
    def test_generate_sends_the_schema(self, name):
        from moralstack.models.policy import GenerationConfig

        policy = self._policy()
        fmt = ALL_FORMATS[name]()
        policy.generate(prompt="p", system="s", config=GenerationConfig(response_format=fmt))
        assert self._sent(policy) == fmt

    @pytest.mark.parametrize("name", sorted(ALL_FORMATS))
    def test_generate_messages_sends_the_schema(self, name):
        from moralstack.models.policy import GenerationConfig

        policy = self._policy()
        fmt = ALL_FORMATS[name]()
        policy.generate_messages(
            messages=[{"role": "user", "content": "p"}],
            config=GenerationConfig(response_format=fmt),
        )
        assert self._sent(policy) == fmt

    @pytest.mark.parametrize("name", sorted(ALL_FORMATS))
    def test_unsupported_model_degrades_to_plain_json(self, name):
        from moralstack.models.policy import GenerationConfig

        policy = self._policy("my-local-llama")
        policy.generate(prompt="p", system="s", config=GenerationConfig(response_format=ALL_FORMATS[name]()))
        assert self._sent(policy) == {"type": "json_object"}
