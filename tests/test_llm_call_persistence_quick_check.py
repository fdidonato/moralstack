"""NEW-2: the fast-path critic ``quick_check`` call is a persisted ``llm_calls`` row.

Goes through the REAL ``async_persist_llm_call`` (see ``test_llm_call_persistence_gaps.py`` for why).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import moralstack.observability.emit_helpers as emit_helpers
from moralstack.constitution.schema import Constitution, Principle
from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic
from moralstack.utils.json_utils import JSONParseError
from tests.test_llm_call_persistence_gaps import (
    REQUEST_ID,
    RUN_ID,
    _Gen,
    _Recorder,
    _request_scope,
    _tokens,
    assert_accounting_parity,
    recorder_fixture,  # noqa: F401  (a fixture: registered under the name "recorder")
)

# --------------------------------------------------------------------------------------
# Fix B (NEW-2): the fast-path quick_check is a persisted call
# --------------------------------------------------------------------------------------


def _hard_constitution() -> Constitution:
    return Constitution(
        core_principles=[Principle(id="H1", level="hard", priority=100, title="No harm", rule="Never help harm people.")]
    )


class _QuickPolicy:
    model = "gpt-crit"

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs: Any) -> _Gen:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return _Gen(text=self.reply, tokens_used=40, prompt_tokens=30, completion_tokens=10)


def _critic(policy: _QuickPolicy) -> LLMConstitutionalCritic:
    return LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=2))


def test_quick_check_pass_persists_a_billable_row_with_what_was_sent(recorder: _Recorder) -> None:
    policy = _QuickPolicy('{"violated": false}')
    with _request_scope():
        verdict = _critic(policy).quick_check("the request", "the draft", _hard_constitution())

    assert verdict.passed is True
    (row,) = recorder.payloads(module="critic", action="quick_check")
    sent = policy.calls[0]
    assert row["prompt"] == sent["prompt"] and row["system_prompt"] == sent["system"]
    assert row["raw_response"] == '{"violated": false}'
    assert row["billable_provider_call"] is True and _tokens(row) == 40
    assert row["model"] == "gpt-crit"
    assert json.loads(row["parsed_summary_json"])["passed"] is True
    assert (row["_cycle"], row["sequence_in_cycle"]) == (0, 2)
    assert (row["_run_id"], row["_request_id"]) == (RUN_ID, REQUEST_ID)


def test_quick_check_violation_row_names_the_principle_and_the_verdict_is_unchanged(recorder: _Recorder) -> None:
    policy = _QuickPolicy('{"violated": true, "principle_id": "H1", "evidence": "x"}')
    with _request_scope():
        verdict = _critic(policy).quick_check("the request", "the draft", _hard_constitution())

    assert verdict.passed is False and verdict.critical_violation.principle_id == "H1"
    (row,) = recorder.payloads(module="critic", action="quick_check")
    summary = json.loads(row["parsed_summary_json"])
    assert summary["passed"] is False and summary["principle_id"] == "H1"


@pytest.mark.parametrize("reply", ["not json", '{"no_verdict": 1}', '["violated"]'])
def test_quick_check_without_a_verdict_is_persisted_and_still_raises(recorder: _Recorder, reply: str) -> None:
    """The call was paid for: it leaves a row. It is still "no verdict" (the fast path fails closed)."""
    with _request_scope(), pytest.raises(JSONParseError):
        _critic(_QuickPolicy(reply)).quick_check("the request", "the draft", _hard_constitution())

    (row,) = recorder.payloads(module="critic", action="quick_check")
    assert row["raw_response"] == reply and row["error"] and _tokens(row) == 40
    assert json.loads(row["parsed_summary_json"])["passed"] is None


def test_quick_check_provider_error_bills_nothing_and_records_nothing(recorder: _Recorder) -> None:
    with _request_scope(), pytest.raises(ConnectionError):
        _critic(_QuickPolicy(ConnectionError("down"))).quick_check("r", "d", _hard_constitution())
    assert recorder.rows == []


def test_quick_check_persist_failure_never_changes_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observability never breaks the request (PROJECT_SPEC invariant 6)."""

    def boom() -> Any:
        raise RuntimeError("observability down")

    monkeypatch.setattr(emit_helpers, "get_obs", boom)
    with _request_scope():
        verdict = _critic(_QuickPolicy('{"violated": false}')).quick_check("r", "d", _hard_constitution())
        assert verdict.passed is True
        with pytest.raises(JSONParseError):
            _critic(_QuickPolicy("not json")).quick_check("r", "d", _hard_constitution())


def test_quick_check_row_keeps_accumulator_sql_and_read_store_in_parity(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One more billable call (40 tokens) per fast-path request, counted once and alike by every reading."""

    def drive() -> None:
        _critic(_QuickPolicy('{"violated": false}')).quick_check("r", "d", _hard_constitution())

    assert_accounting_parity(tmp_path, monkeypatch, drive, calls=1, total_tokens=40)
