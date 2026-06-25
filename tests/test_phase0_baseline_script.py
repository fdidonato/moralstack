from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).parent.parent / "scripts" / "phase0_token_latency_baseline.py"
    spec = importlib.util.spec_from_file_location("phase0_token_latency_baseline", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["phase0_token_latency_baseline"] = module
    spec.loader.exec_module(module)
    return module


def test_build_report_reads_sqlite_tokens_and_dccl(tmp_path):
    mod = _load_script_module()
    db_path = tmp_path / "obs.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE requests (run_id TEXT, request_id TEXT);
            CREATE TABLE llm_calls (
                run_id TEXT,
                request_id TEXT,
                phase TEXT,
                module TEXT,
                action TEXT,
                model TEXT,
                duration_ms REAL,
                token_usage_json TEXT,
                call_kind TEXT
            );
            """)
        conn.execute("INSERT INTO requests VALUES ('r1', 'q1')")
        conn.execute(
            "INSERT INTO llm_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "r1",
                "q1",
                "risk_estimation",
                "risk_estimator",
                "estimate_intent",
                "gpt-test",
                10.0,
                json.dumps({"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}),
                None,
            ),
        )
        conn.execute(
            "INSERT INTO llm_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("r1", "q1", "compliance_layer", "compliance_layer", "evaluate", "gpt-test", 5.0, None, "compliance_layer"),
        )

    report = mod.build_report(db_path=db_path, jsonl_dir=tmp_path / "missing", root=Path("."))

    assert "LLM calls/request: 2.00" in report
    assert "Total tokens: 10" in report
    assert "DCCL/compliance LLM rows: 1" in report


def test_jsonl_consumer_map_reports_hits(tmp_path):
    mod = _load_script_module()
    root = tmp_path
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "sample.py").write_text("path = 'llm.call.jsonl'\n", encoding="utf-8")

    report = mod.build_report(db_path=None, jsonl_dir=tmp_path / "missing", root=root)

    assert "JSONL Consumer Map" in report
    assert "sample.py" in report


def test_report_is_ascii_when_source_contains_unicode(tmp_path):
    mod = _load_script_module()
    root = tmp_path
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "sample.py").write_text("# jsonl -> \u2192 consumer\n", encoding="utf-8")

    report = mod.build_report(db_path=None, jsonl_dir=tmp_path / "missing", root=root)

    report.encode("ascii")
    assert "\\u2192" in report
