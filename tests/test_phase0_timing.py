from __future__ import annotations

import json
import logging

from moralstack.observability.phase0_timing import emit_phase0_timing, phase0_timing_enabled
from moralstack.sdk.wrapper import GovernedCompletions


def test_phase0_timing_disabled_by_default(monkeypatch, caplog):
    monkeypatch.delenv("MORALSTACK_PHASE0_TIMING", raising=False)
    monkeypatch.delenv("MORALSTACK_PHASE0_TIMING_JSONL", raising=False)
    caplog.set_level(logging.INFO)

    emit_phase0_timing("x", 1.2)

    assert phase0_timing_enabled() is False
    assert not [record for record in caplog.records if "phase0_timing" in record.message]


def test_phase0_timing_writes_jsonl_when_enabled(monkeypatch, tmp_path):
    output = tmp_path / "phase0" / "timing.jsonl"
    monkeypatch.setenv("MORALSTACK_PHASE0_TIMING", "1")
    monkeypatch.setenv("MORALSTACK_PHASE0_TIMING_JSONL", str(output))

    emit_phase0_timing("risk_estimator.mini_persist", 12.3456, row_count=3)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "risk_estimator.mini_persist"
    assert payload["duration_ms"] == 12.346
    assert payload["row_count"] == 3


def test_governed_create_emits_phase0_timing_when_enabled(monkeypatch):
    class _Obs:
        flushed = False

        def flush(self) -> None:
            self.flushed = True

    obs = _Obs()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    completions = GovernedCompletions(object())  # type: ignore[arg-type]

    monkeypatch.setenv("MORALSTACK_PHASE0_TIMING", "1")
    monkeypatch.setattr(completions, "_create_inner", lambda **_: "ok")
    monkeypatch.setattr("moralstack.observability.service.get_obs", lambda: obs)
    monkeypatch.setattr("moralstack.sdk.wrapper.emit_phase0_timing", lambda *args, **kwargs: calls.append((args, kwargs)))

    assert completions.create(model="gpt-test", stream=True) == "ok"

    assert obs.flushed is True
    assert calls
    assert calls[0][0][0] == "sdk.governed_completions.create"
    assert calls[0][1]["model"] == "gpt-test"
    assert calls[0][1]["stream"] is True
