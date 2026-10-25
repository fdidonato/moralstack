"""record_llm_call is best-effort on both branches (PROJECT_SPEC §5.6)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from moralstack.orchestration import persistence_helpers
from moralstack.orchestration.persistence_helpers import record_llm_call


class _RaisingLogger:
    def __init__(self) -> None:
        self.calls = 0

    def log_call(self, **_kw: Any) -> None:
        self.calls += 1
        raise RuntimeError("diagnostics logger down")


def test_raising_log_call_is_swallowed_and_persist_still_runs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    persisted: list[dict[str, Any]] = []
    monkeypatch.setattr(persistence_helpers, "async_persist_llm_call", lambda **kw: persisted.append(kw))
    logger = _RaisingLogger()

    with caplog.at_level(logging.WARNING, logger=persistence_helpers.__name__):
        record_llm_call(logger, {"module": "simulator", "action": "simulate"}, {"module": "simulator"})

    assert logger.calls == 1
    assert persisted == [{"module": "simulator"}]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "RuntimeError" in warnings[0].getMessage()


def test_log_call_receives_the_payload_when_it_works() -> None:
    seen: list[dict[str, Any]] = []

    class _Logger:
        def log_call(self, **kw: Any) -> None:
            seen.append(kw)

    record_llm_call(_Logger(), {"module": "critic", "action": "critique"}, None)
    assert seen == [{"module": "critic", "action": "critique"}]
