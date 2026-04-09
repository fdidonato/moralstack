"""
Test per decision_trace: routing via observability, formato invariato.

append_decision_trace() ora richiede run_id nel contesto e instrada
via observability router (file_only → logs/observability/decision.trace.jsonl).
Il parametro path= è accettato per compatibilità ma ignorato.
"""

from __future__ import annotations

import json
import uuid

from moralstack.observability.context import set_current_run_id
from moralstack.observability.service import get_obs
from moralstack.runtime.trace import (
    DecisionTrace,
    append_decision_trace,
    normalize_trace_fields,
)


def _flush():
    get_obs().flush(timeout=5.0)


def test_append_decision_trace_writes_to_jsonl(tmp_path, monkeypatch):
    """In file_only mode, traces are written to logs/observability/decision.trace.jsonl."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "logs" / "observability"))

    run_id = str(uuid.uuid4())
    set_current_run_id(run_id)
    try:
        trace = DecisionTrace(request_id="req-1")
        trace.stage = "FINAL"
        trace.final_action = "NORMAL_COMPLETE"
        normalize_trace_fields(trace)
        append_decision_trace(trace)
        _flush()

        expected = tmp_path / "logs" / "observability" / "decision.trace.jsonl"
        assert expected.exists(), f"Expected {expected} to exist"
        lines = [ln for ln in expected.read_text(encoding="utf-8").strip().split("\n") if ln]
        assert len(lines) >= 1
        obj = json.loads(lines[-1])
        payload = obj.get("payload", obj)
        trace_json_str = payload.get("trace_json", "{}")
        td = json.loads(trace_json_str) if isinstance(trace_json_str, str) else trace_json_str
        assert td["request_id"] == "req-1"
        assert td["stage"] == "FINAL"
    finally:
        set_current_run_id(None)


def test_append_decision_trace_path_param_ignored(tmp_path, monkeypatch):
    """The path= parameter is accepted for backwards compat but has no effect on routing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "logs" / "observability"))

    run_id = str(uuid.uuid4())
    set_current_run_id(run_id)
    try:
        custom = tmp_path / "custom" / "trace.jsonl"
        trace = DecisionTrace(request_id="req-2")
        trace.stage = "PRE_POLICY"
        normalize_trace_fields(trace)
        # path= is ignored in new implementation
        append_decision_trace(trace, path=str(custom))
        _flush()
        # custom path should NOT be created (routing is controlled by mode, not path param)
        assert not custom.exists()
    finally:
        set_current_run_id(None)


def test_append_decision_trace_no_run_id_noop(tmp_path, monkeypatch):
    """Without run_id in context, append_decision_trace is a no-op."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "logs" / "observability"))

    set_current_run_id(None)
    trace = DecisionTrace(request_id="req-noop")
    normalize_trace_fields(trace)
    append_decision_trace(trace)
    _flush()
    # No file should be created
    obs_dir = tmp_path / "logs" / "observability"
    if obs_dir.exists():
        files = list(obs_dir.glob("decision.trace.jsonl"))
        assert len(files) == 0


def test_append_decision_trace_format_fields(tmp_path, monkeypatch):
    """Fields written to JSONL match the DecisionTrace dataclass."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "logs" / "observability"))

    run_id = str(uuid.uuid4())
    set_current_run_id(run_id)
    try:
        trace = DecisionTrace(request_id="req-format")
        trace.stage = "FINAL"
        trace.sequence = 2
        trace.risk_category = "BENIGN"
        trace.risk_score = 0.1
        trace.final_action = "NORMAL_COMPLETE"
        trace.path = "FAST_PATH"
        trace.decision_reason = "test"
        normalize_trace_fields(trace)
        append_decision_trace(trace)
        _flush()

        path = tmp_path / "logs" / "observability" / "decision.trace.jsonl"
        assert path.exists()
        lines = [ln for ln in path.read_text(encoding="utf-8").strip().split("\n") if ln]
        assert len(lines) >= 1
        # The envelope payload contains trace_json (serialised DecisionTrace)
        obj = json.loads(lines[-1])
        payload = obj.get("payload", {})
        trace_json_str = payload.get("trace_json", "{}")
        td = json.loads(trace_json_str) if isinstance(trace_json_str, str) else trace_json_str
        assert td["request_id"] == "req-format"
        assert td["stage"] == "FINAL"
        assert td["sequence"] == 2
        assert td["risk_category"] == "BENIGN"
        assert td["risk_score"] == 0.1
        assert td["final_action"] == "NORMAL_COMPLETE"
        assert td["path"] == "FAST_PATH"
        assert td["decision_reason"] == "test"
    finally:
        set_current_run_id(None)


def test_append_decision_trace_no_block_on_missing_context():
    """Without run_id in context, append_decision_trace does not raise."""
    set_current_run_id(None)
    trace = DecisionTrace(request_id="req-err")
    normalize_trace_fields(trace)
    # Must not raise even with bad state
    append_decision_trace(trace)


def test_decision_trace_to_dict_includes_sim_fields():
    """to_dict() include sim_expected_valence, sim_semantic_expected_harm,
    sim_dominant_harm_types, sim_worst_harm."""
    trace = DecisionTrace(request_id="req-sim")
    trace.sim_expected_valence = 0.35
    trace.sim_semantic_expected_harm = 0.6
    trace.sim_dominant_harm_types = ["physical_harm", "psychological_harm"]
    trace.sim_worst_harm = {"harm_type": "physical_harm", "harm_scope": "individual", "risk": 0.72}
    d = trace.to_dict()
    assert d["sim_expected_valence"] == 0.35
    assert d["sim_semantic_expected_harm"] == 0.6
    assert d["sim_dominant_harm_types"] == ["physical_harm", "psychological_harm"]
    assert d["sim_worst_harm"] == {
        "harm_type": "physical_harm",
        "harm_scope": "individual",
        "risk": 0.72,
    }


def test_decision_trace_to_dict_includes_token_optimization_fields():
    """to_dict() includes context_mode_by_module and modules_skipped
    (optional token optimization)."""
    trace = DecisionTrace(request_id="req-token")
    trace.context_mode_by_module = {"critic": "thin", "simulator": "full"}
    trace.modules_skipped = {"simulator": "carried_forward"}
    d = trace.to_dict()
    assert d["context_mode_by_module"] == {"critic": "thin", "simulator": "full"}
    assert d["modules_skipped"] == {"simulator": "carried_forward"}
