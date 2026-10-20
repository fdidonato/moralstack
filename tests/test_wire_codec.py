"""Wire codec (A4b, plan §13.2 / §T12): round-trip, sha256 and client-boundary parity. Tier 1, offline."""

from __future__ import annotations

import hashlib
import itertools
import json
from unittest import mock

import pytest

from moralstack.models.policy import OpenAIPolicy
from moralstack.observability.wire import (
    decode_wire,
    encode_wire,
    encode_wire_calls,
    generate_messages_for,
    wire_sha256,
)
from tests.prompts_golden.fakes import FakeOpenAIFactory

# Strings that stress every place a template engine, a line-ending normaliser or a JSON encoder could bite.
NASTY = (
    "",
    "plain",
    "{",
    "}",
    "{{",
    "}}",
    "{x} and {{y}}",
    "100% of the time $x %s",
    "crlf\r\nline",
    "nbsp here",
    " leading",
    "trailing ",
    "\n",
    "emoji \U0001f600 dash —",
    'quote " and \\ backslash',
)


def _roundtrip(messages: list[dict[str, str]], prompt_col: str, system_col: str) -> dict:
    record = encode_wire(messages, prompt_col=prompt_col, system_prompt_col=system_col, branch="messages")
    # The record is persisted as JSON: the property must hold after a real serialization round trip.
    record = json.loads(json.dumps(record, ensure_ascii=False))
    assert decode_wire(record, prompt_col=prompt_col, system_prompt_col=system_col) == messages
    assert wire_sha256(decode_wire(record, prompt_col=prompt_col, system_prompt_col=system_col)) == record["sha256"]
    assert record["n"] == len(messages)
    return record


def test_roundtrip_and_sha_over_synthetic_messages():
    checked = 0
    for system, prompt, middle in itertools.product(NASTY, NASTY, NASTY):
        messages = [
            {"role": "system", "content": system},
            {"role": "developer", "content": middle},
            {"role": "user", "content": middle},
            {"role": "assistant", "content": system},
            {"role": "user", "content": f"{middle}{prompt}{middle}"},
        ]
        # Columns as persisted today (system + prompt) and as an unrelated pair: decoding never depends on luck.
        _roundtrip(messages, prompt, system)
        _roundtrip(messages, "unrelated-prompt", "unrelated-system")
        _roundtrip(messages, "", "")
        checked += 1
    assert checked == len(NASTY) ** 3


def test_ref_when_content_equals_the_column():
    messages = [{"role": "system", "content": "SYS {x}"}, {"role": "user", "content": "PROMPT"}]
    record = _roundtrip(messages, "PROMPT", "SYS {x}")
    assert record["messages"] == [
        {"role": "system", "ref": "system_prompt"},
        {"role": "user", "ref": "prompt"},
    ]
    assert record["strings"] == []


def test_ref_with_prefix_and_suffix_when_the_column_occurs_once():
    preface = "Use the conversation above as context.\n\n"
    suffix = "\n\nRetry: return valid JSON only."
    messages = [{"role": "user", "content": f"{preface}THE PROMPT{suffix}"}]
    record = _roundtrip(messages, "THE PROMPT", "")
    assert record["messages"] == [{"role": "user", "ref": "prompt", "prefix": preface, "suffix": suffix}]
    assert record["strings"] == []


def test_column_occurring_twice_falls_back_to_the_string_table():
    content = "AB-prompt-CD-prompt-EF"
    record = _roundtrip([{"role": "user", "content": content}], "prompt", "")
    assert record["messages"] == [{"role": "user", "s": 0}]
    assert record["strings"] == [content]


def test_empty_column_is_never_a_reference():
    record = _roundtrip([{"role": "user", "content": ""}, {"role": "user", "content": "x"}], "", "")
    assert all("ref" not in m for m in record["messages"])


def test_identical_contents_share_one_string():
    messages = [
        {"role": "developer", "content": "same contract"},
        {"role": "user", "content": "other"},
        {"role": "user", "content": "same contract"},
    ]
    record = _roundtrip(messages, "unrelated", "unrelated")
    assert record["strings"] == ["same contract", "other"]
    assert [m["s"] for m in record["messages"]] == [0, 1, 0]


def test_sha256_is_the_documented_compact_json_digest():
    messages = [{"role": "system", "content": "é—"}, {"role": "user", "content": "u"}]
    canonical = '[{"role":"system","content":"é—"},{"role":"user","content":"u"}]'
    assert wire_sha256(messages) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # Extra keys on a message do not move the digest: only role and content are on the wire digest.
    assert wire_sha256([{**messages[0], "name": "ignored"}, messages[1]]) == wire_sha256(messages)
    # Order and content are significant.
    assert wire_sha256(list(reversed(messages))) != wire_sha256(messages)


def test_aggregated_calls_share_one_string_table():
    system = "SHARED SYSTEM"
    calls = [
        (
            [{"role": "system", "content": system}, {"role": "user", "content": f"perspective {i}"}],
            "messages",
        )
        for i in range(3)
    ]
    encoded = json.loads(json.dumps(encode_wire_calls(calls, prompt_col="", system_prompt_col="")))
    assert set(encoded) == {"wire_calls", "wire_strings"}
    assert encoded["wire_strings"].count(system) == 1
    assert all("strings" not in rec for rec in encoded["wire_calls"])
    for (messages, _), record in zip(calls, encoded["wire_calls"], strict=True):
        decoded = decode_wire(record, prompt_col="", system_prompt_col="", strings=encoded["wire_strings"])
        assert decoded == messages
        assert wire_sha256(decoded) == record["sha256"]


def test_decode_rejects_malformed_records():
    good = encode_wire([{"role": "user", "content": "x"}], prompt_col="", system_prompt_col="", branch="generate")
    with pytest.raises(ValueError):
        decode_wire({**good, "v": 2}, prompt_col="", system_prompt_col="")
    with pytest.raises(ValueError):
        decode_wire({**good, "messages": [{"role": "user", "ref": "nope"}]}, prompt_col="", system_prompt_col="")


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        {"v": 1},  # no messages
        {"v": 1, "messages": [{"role": "user"}]},  # neither ref nor s
        {"v": 1, "messages": [{"role": "user", "s": 3}], "strings": ["x"]},  # index out of range
        {"v": 1, "messages": [{"role": "user", "s": -1}], "strings": ["x"]},  # negative index
        {"v": 1, "messages": [{"s": 0}], "strings": ["x"]},  # no role
        {"v": 1, "messages": ["not a dict"], "strings": []},
        {"v": 1, "messages": [{"role": "user", "s": "0"}], "strings": ["x"]},
    ],
)
def test_decode_normalizes_every_malformed_record_to_value_error(record):
    with pytest.raises(ValueError):
        decode_wire(record, prompt_col="", system_prompt_col="")  # type: ignore[arg-type]


def test_generate_messages_for_omits_an_empty_system():
    assert generate_messages_for("U", "") == [{"role": "user", "content": "U"}]
    assert generate_messages_for("U", "S") == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]


# --------------------------------------------------------------------------------------
# Client-boundary parity (MT1): what the provider client receives is exactly the decoded wire.
# --------------------------------------------------------------------------------------


def _policy_on_fake_client() -> tuple[OpenAIPolicy, FakeOpenAIFactory]:
    factory = FakeOpenAIFactory("A reply long enough to be a plausible model answer.")
    with mock.patch("openai.OpenAI", factory):
        policy = OpenAIPolicy(api_key="offline-test-key", model="gpt-4o")
    return policy, factory


@pytest.mark.parametrize("system", ["", "SYSTEM {x} PROMPT", "sys\r\nnbsp "])
@pytest.mark.parametrize("prompt", ["USER PROMPT", "{braces} {{double}} 100%", ""])
def test_generate_client_boundary_parity(system: str, prompt: str):
    policy, factory = _policy_on_fake_client()
    policy.generate(prompt, system=system)
    (received,) = factory.wire
    sent = generate_messages_for(prompt, system)
    assert received.messages == sent
    record = encode_wire(sent, prompt_col=prompt, system_prompt_col=system, branch="generate")
    assert decode_wire(record, prompt_col=prompt, system_prompt_col=system) == received.messages
    assert wire_sha256(received.messages) == record["sha256"]


def test_generate_messages_client_boundary_parity():
    policy, factory = _policy_on_fake_client()
    messages = [
        {"role": "system", "content": "RENDERED SYSTEM {x}"},
        {"role": "developer", "content": "Contract 100% {tier}"},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "PREFACE\n\nTHE PROMPT\n\nRETRY"},
    ]
    policy.generate_messages(messages)
    (received,) = factory.wire
    record = encode_wire(messages, prompt_col="THE PROMPT", system_prompt_col="RENDERED SYSTEM {x}", branch="messages")
    decoded = decode_wire(record, prompt_col="THE PROMPT", system_prompt_col="RENDERED SYSTEM {x}")
    assert received.messages == decoded == messages
    assert wire_sha256(received.messages) == record["sha256"]
    # Provider-side serialization is part of the claim: the record survives JSON.
    assert json.loads(json.dumps(record))["sha256"] == wire_sha256(received.messages)
