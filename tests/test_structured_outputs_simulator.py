"""Strict Structured Outputs for the Simulator.

The simulator used to run in plain JSON mode, so the provider guaranteed only
that the reply parsed. Enum violations reached the client, which rejected the
whole reply and retried — on the COMPL-AI campaigns 2,231 calls were paid for
and discarded that way, ~32% of every simulator call, and 239 invocations
exhausted all retries and produced no consequence at all. Almost all of them
put a `harm_type` value (`misinformation` above all) into `scenario_type`.
A schema-enforced enum makes that impossible at the source.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_args
from unittest.mock import MagicMock

from moralstack.models.policy import OpenAIPolicy
from moralstack.utils.structured_output import (
    _HARM_SCOPE,
    _HARM_TYPE,
    _SCENARIO_TYPE,
    SIMULATOR_JSON_SCHEMA,
    SimulatorConsequenceOutput,
    simulator_response_format,
)

_ITEM = SIMULATOR_JSON_SCHEMA["properties"]["consequences"]["items"]


class TestSimulatorSchema:
    def test_enums_match_the_pydantic_literals(self):
        # Single source of truth: the schema is derived from the same aliases,
        # so a new enum value cannot reach the model without the validator.
        props = _ITEM["properties"]
        assert props["scenario_type"]["enum"] == list(get_args(_SCENARIO_TYPE))
        assert props["harm_type"]["enum"] == list(get_args(_HARM_TYPE))
        assert props["harm_scope"]["enum"] == list(get_args(_HARM_SCOPE))

    def test_misinformation_is_a_harm_type_and_not_a_scenario_type(self):
        # The exact confusion that caused the discarded calls.
        assert "misinformation" in _ITEM["properties"]["harm_type"]["enum"]
        assert "misinformation" not in _ITEM["properties"]["scenario_type"]["enum"]

    def test_schema_covers_every_model_field(self):
        assert set(_ITEM["properties"]) == set(SimulatorConsequenceOutput.model_fields)

    def test_strict_mode_contract(self):
        # OpenAI strict mode: every property required, no extra properties.
        assert SIMULATOR_JSON_SCHEMA["additionalProperties"] is False
        assert _ITEM["additionalProperties"] is False
        assert set(_ITEM["required"]) == set(_ITEM["properties"])
        fmt = simulator_response_format()
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["strict"] is True


def _policy(model: str) -> OpenAIPolicy:
    policy = OpenAIPolicy(api_key="sk-test", model=model)
    choice = SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    policy.client = MagicMock()
    policy.client.chat.completions.create.return_value = SimpleNamespace(choices=[choice], usage=usage)
    return policy


def _sent_response_format(policy: OpenAIPolicy) -> object:
    return policy.client.chat.completions.create.call_args.kwargs["response_format"]


class TestResponseFormatDegradation:
    def test_json_schema_reaches_a_supporting_model(self):
        policy = _policy("gpt-4o")
        policy._complete([{"role": "user", "content": "hi"}], response_format=simulator_response_format())
        assert _sent_response_format(policy)["type"] == "json_schema"

    def test_json_schema_degrades_to_json_object_on_an_unsupported_model(self):
        policy = _policy("my-local-llama")
        policy._complete([{"role": "user", "content": "hi"}], response_format=simulator_response_format())
        assert _sent_response_format(policy) == {"type": "json_object"}

    def test_plain_json_object_is_passed_through_untouched(self):
        policy = _policy("my-local-llama")
        policy._complete([{"role": "user", "content": "hi"}], response_format={"type": "json_object"})
        assert _sent_response_format(policy) == {"type": "json_object"}


def test_simulator_requests_the_strict_schema():
    from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator

    simulator = LLMConsequenceSimulator(policy=MagicMock())
    fmt = simulator._generation_config.response_format
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "simulator_output"
