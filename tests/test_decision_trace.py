"""
Test per decision_trace: path configurabile, I/O robusto, formato invariato.
"""

from __future__ import annotations

import json
import os
import threading

from moralstack.runtime.trace import (
    DecisionTrace,
    append_decision_trace,
    normalize_trace_fields,
)


def test_append_decision_trace_default_path(tmp_path, monkeypatch):
    """Default path: logs/decision_trace.jsonl."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DECISION_TRACE_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
    trace = DecisionTrace(request_id="req-1")
    trace.stage = "FINAL"
    trace.final_action = "NORMAL_COMPLETE"
    normalize_trace_fields(trace)
    append_decision_trace(trace)
    expected = tmp_path / "logs" / "decision_trace.jsonl"
    assert expected.exists()
    lines = expected.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["request_id"] == "req-1"
    assert obj["stage"] == "FINAL"
    assert obj["final_action"] == "NORMAL_COMPLETE"


def test_append_decision_trace_custom_path_param(tmp_path, monkeypatch):
    """Path custom via parametro opzionale."""
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
    custom = tmp_path / "custom" / "trace.jsonl"
    trace = DecisionTrace(request_id="req-2")
    trace.stage = "PRE_POLICY"
    normalize_trace_fields(trace)
    append_decision_trace(trace, path=str(custom))
    assert custom.exists()
    lines = custom.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["request_id"] == "req-2"


def test_append_decision_trace_env_var(tmp_path, monkeypatch):
    """Path configurabile via MORALSTACK_DECISION_TRACE_PATH."""
    custom = tmp_path / "env_trace.jsonl"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
    monkeypatch.setenv("MORALSTACK_DECISION_TRACE_PATH", str(custom))
    trace = DecisionTrace(request_id="req-3")
    normalize_trace_fields(trace)
    append_decision_trace(trace)
    assert custom.exists()
    lines = custom.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["request_id"] == "req-3"


def test_append_decision_trace_format_unchanged(tmp_path, monkeypatch):
    """Formato JSONL invariato: una riga per trace, campi identici."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DECISION_TRACE_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
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
    path = tmp_path / "logs" / "decision_trace.jsonl"
    line = path.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert obj["request_id"] == "req-format"
    assert obj["stage"] == "FINAL"
    assert obj["sequence"] == 2
    assert obj["risk_category"] == "BENIGN"
    assert obj["risk_score"] == 0.1
    assert obj["final_action"] == "NORMAL_COMPLETE"
    assert obj["path"] == "FAST_PATH"
    assert obj["decision_reason"] == "test"


def test_append_decision_trace_thread_safe(tmp_path, monkeypatch):
    """Scritture concorrenti: nessuna riga persa o duplicata."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DECISION_TRACE_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")

    def write_n(thread_id: int, n: int) -> None:
        for i in range(n):
            trace = DecisionTrace(request_id=f"req-{thread_id}-{i}")
            normalize_trace_fields(trace)
            append_decision_trace(trace)

    threads = [threading.Thread(target=write_n, args=(tid, 10)) for tid in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    path = tmp_path / "logs" / "decision_trace.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 50
    ids = [json.loads(line)["request_id"] for line in lines]
    assert len(set(ids)) == 50


def test_append_decision_trace_directory_created(tmp_path, monkeypatch):
    """Directory di output creata se non esiste."""
    custom = tmp_path / "a" / "b" / "c" / "trace.jsonl"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
    assert not custom.parent.exists()
    trace = DecisionTrace(request_id="req-dir")
    normalize_trace_fields(trace)
    append_decision_trace(trace, path=str(custom))
    assert custom.exists()


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


def test_append_decision_trace_no_block_on_error(tmp_path, monkeypatch):
    """Errore di scrittura: non blocca, sistema continua."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MORALSTACK_DECISION_TRACE_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
    # Path invalido (es. dispositivo)
    bad_path = "Z:\\nonexistent\\trace.jsonl" if os.name == "nt" else "/dev/full"
    trace = DecisionTrace(request_id="req-err")
    normalize_trace_fields(trace)
    # Non deve sollevare
    append_decision_trace(trace, path=bad_path)
