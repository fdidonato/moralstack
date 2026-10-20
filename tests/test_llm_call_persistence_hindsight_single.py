"""NEW-3: the hindsight single-consequence path persists its retry-failed rows.

Goes through the REAL ``async_persist_llm_call`` (see ``test_llm_call_persistence_gaps.py`` for why).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import moralstack.observability.emit_helpers as emit_helpers
from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator
from moralstack.runtime.modules.simulator_module import Consequence
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
# Fix C (NEW-3): hindsight single-consequence path writes retry-failed rows
# --------------------------------------------------------------------------------------

_VALID_SINGLE = json.dumps(
    {
        "safety": 0.5,
        "helpfulness": 0.5,
        "honesty": 0.5,
        "harm_probability": 0.1,
        "benefit_probability": 0.6,
        "confidence": 0.8,
        "rationale": "ok",
    }
)


class _Sequence:
    model = "gpt-hind"

    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)

    def generate(self, **kwargs: Any) -> _Gen:
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Gen(text=reply, tokens_used=20, prompt_tokens=12, completion_tokens=8)


def _hindsight(policy: _Sequence) -> LLMHindsightEvaluator:
    return LLMHindsightEvaluator(policy, HindsightConfig(max_retries=2, enable_caching=False))


def _consequence() -> Consequence:
    return Consequence(text="a consequence", likelihood=0.5, scenario_id="s1")


def test_hindsight_single_retry_failed_attempt_persists_a_billable_row(recorder: _Recorder) -> None:
    with _request_scope(cycle=1):
        evaluation = _hindsight(_Sequence(["bad", _VALID_SINGLE])).evaluate_scenario("req", "resp", _consequence())

    assert evaluation.rationale == "ok"  # the second attempt was parsed, not the pessimistic fallback
    (row,) = recorder.payloads(call_outcome="retry_failed")
    assert row["module"] == "hindsight" and row["phase"] == "hindsight_retry"
    assert row["action"] == "retry_failed_attempt_1" and row["attempts"] == 1
    assert "s1" in row["prompt"] and row["raw_response"] == "bad"
    assert row["billable_provider_call"] is True and _tokens(row) == 20 and row["model"] == "gpt-hind"
    assert (row["_cycle"], row["_run_id"], row["_request_id"]) == (1, RUN_ID, REQUEST_ID)


def test_hindsight_single_all_attempts_failed_keeps_the_pessimistic_evaluation(recorder: _Recorder) -> None:
    with _request_scope(cycle=1):
        evaluation = _hindsight(_Sequence(["bad", "worse"])).evaluate_scenario("req", "resp", _consequence())

    # decision input unchanged: the pessimistic fallback of the single path
    assert evaluation.scores.safety == -1.0 and evaluation.harm_probability == 1.0
    rows = recorder.payloads(call_outcome="retry_failed")
    assert sorted(r["attempts"] for r in rows) == [1, 2]


def test_hindsight_single_provider_error_bills_nothing_and_records_nothing(recorder: _Recorder) -> None:
    with _request_scope(cycle=1):
        _hindsight(_Sequence([ConnectionError("down"), ConnectionError("down")])).evaluate_scenario(
            "req", "resp", _consequence()
        )
    assert recorder.rows == []


def test_hindsight_single_persist_failure_never_changes_the_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("observability down")

    monkeypatch.setattr(emit_helpers, "get_obs", boom)
    with _request_scope(cycle=1):
        evaluation = _hindsight(_Sequence(["bad", _VALID_SINGLE])).evaluate_scenario("req", "resp", _consequence())
    assert evaluation.rationale == "ok"


def test_hindsight_single_retry_row_keeps_accumulator_sql_and_read_store_in_parity(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failed single-path attempt (20 tokens): counted once, alike, by every reading."""

    def drive() -> None:
        _hindsight(_Sequence(["bad", _VALID_SINGLE])).evaluate_scenario("req", "resp", _consequence())

    assert_accounting_parity(tmp_path, monkeypatch, drive, calls=1, total_tokens=20, cycle=1)
