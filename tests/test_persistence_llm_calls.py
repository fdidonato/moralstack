"""
Tests for persistence: create_run, upsert_request, llm_calls with long content.

Verifies no truncation and correct ordering.
"""

from __future__ import annotations

import pytest

from moralstack.observability import obs, router
from moralstack.observability import service as service_module
from moralstack.observability.context import (
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
)
from moralstack.observability.emit_helpers import persist_llm_call
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import (
    create_run,
    init_db,
    upsert_request,
)

_rs = SqliteReadStore()
get_llm_calls_for_request = _rs.get_llm_calls_for_request


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    yield
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


def test_persistence_llm_calls_long_content(tmp_path, monkeypatch):
    """Create run, upsert request, insert llm_calls with >10k chars; assert no truncation."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-test-llm"
    request_id = "req-test-llm"

    assert create_run(run_id, run_type="test", meta={"test": True})
    assert upsert_request(run_id, request_id, prompt="short prompt", domain="test")

    long_str = "x" * 15000
    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(0)

    assert persist_llm_call(
        phase="critic",
        module="critic",
        action="generate",
        prompt=long_str,
        system_prompt="sys_" + long_str[:100],
        raw_response="raw_" + long_str,
    )
    assert persist_llm_call(
        phase="simulator",
        module="simulator",
        action="generate",
        prompt="p2",
        system_prompt="s2",
        raw_response="r2",
        cycle=1,
    )

    obs.flush(timeout=10.0)
    calls = get_llm_calls_for_request(run_id, request_id)
    assert len(calls) == 2
    assert calls[0]["prompt"] == long_str
    assert len(calls[0]["prompt"]) == 15000
    assert calls[0]["system_prompt"] == "sys_" + long_str[:100]
    assert calls[0]["raw_response"] == "raw_" + long_str
    assert len(calls[0]["raw_response"]) == 15004  # "raw_" + 15000
    assert calls[0]["phase"] == "critic"
    assert calls[0]["cycle"] == 0

    assert calls[1]["phase"] == "simulator"
    assert calls[1]["cycle"] == 1
    assert calls[1]["prompt"] == "p2"


def test_get_llm_calls_ordered_by_sequence_in_cycle(tmp_path, monkeypatch):
    """get_llm_calls_for_request returns calls ordered by cycle, sequence_in_cycle, started_at."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-seq"
    request_id = "req-seq"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(1)

    # Persist in reverse logical order: hindsight (5), then policy (1).
    assert persist_llm_call(
        phase="hindsight",
        module="hindsight",
        action="evaluate",
        prompt="p",
        system_prompt="s",
        raw_response="r",
        cycle=1,
        sequence_in_cycle=5,
    )
    assert persist_llm_call(
        phase="policy_generate",
        module="policy",
        action="generate",
        prompt="p2",
        system_prompt="s2",
        raw_response="r2",
        cycle=1,
        sequence_in_cycle=1,
    )

    obs.flush(timeout=10.0)
    calls = get_llm_calls_for_request(run_id, request_id)
    assert len(calls) == 2
    # Logical order: policy (1) before hindsight (5).
    assert calls[0]["module"] == "policy"
    assert calls[0]["sequence_in_cycle"] == 1
    assert calls[1]["module"] == "hindsight"
    assert calls[1]["sequence_in_cycle"] == 5
