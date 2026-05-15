"""
Tests for scripts/consolidate_jsonl_meta.py.

Verifies that the JSONL→consolidated merge produces the same result as
sqlite_sink.update_request_meta(merge=True), per the contract documented
in docs/modules/observability.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _import_module():
    """Load the script as a module via importlib."""
    import importlib.util
    import sys

    script_path = Path(__file__).parent.parent / "scripts" / "consolidate_jsonl_meta.py"
    spec = importlib.util.spec_from_file_location("consolidate_jsonl_meta", script_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["consolidate_jsonl_meta"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_jsonl(path: Path, envelopes: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for env in envelopes:
            fh.write(json.dumps(env) + "\n")


class TestConsolidateJsonlMeta:
    def test_single_envelope_passthrough(self, tmp_path):
        mod = _import_module()
        path = tmp_path / "input.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:00",
                    "payload": {"meta": {"final_action": "NORMAL_COMPLETE"}},
                }
            ],
        )
        envelopes = mod._load_envelopes([path])
        result = mod.consolidate(envelopes)
        assert result == {"r1:req1": {"final_action": "NORMAL_COMPLETE"}}

    def test_progressive_merge_last_write_wins(self, tmp_path):
        mod = _import_module()
        path = tmp_path / "input.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:00",
                    "payload": {"meta": {"final_action": "NORMAL_COMPLETE", "risk_score": 0.10}},
                },
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:01",
                    "payload": {"meta": {"risk_score": 0.15, "intent_clarity": "HIGH"}},
                },
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:02",
                    "payload": {"meta": {"was_cached": True}},
                },
            ],
        )
        envelopes = mod._load_envelopes([path])
        result = mod.consolidate(envelopes)
        assert result["r1:req1"] == {
            "final_action": "NORMAL_COMPLETE",
            "risk_score": 0.15,
            "intent_clarity": "HIGH",
            "was_cached": True,
        }

    def test_multiple_requests_isolated(self, tmp_path):
        mod = _import_module()
        path = tmp_path / "input.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:00",
                    "payload": {"meta": {"final_action": "NORMAL_COMPLETE"}},
                },
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req2",
                    "created_at": "2026-05-15T10:00:01",
                    "payload": {"meta": {"final_action": "REFUSE"}},
                },
            ],
        )
        envelopes = mod._load_envelopes([path])
        result = mod.consolidate(envelopes)
        assert result["r1:req1"] == {"final_action": "NORMAL_COMPLETE"}
        assert result["r1:req2"] == {"final_action": "REFUSE"}
        assert len(result) == 2

    def test_skip_envelopes_without_meta(self, tmp_path):
        """Envelopes without payload.meta (e.g. malformed or other event types) are skipped."""
        mod = _import_module()
        path = tmp_path / "input.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "event_type": "llm.call",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:00",
                    "payload": {"prompt": "hi"},
                },
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:01",
                    "payload": {"meta": {"final_action": "NORMAL_COMPLETE"}},
                },
            ],
        )
        envelopes = mod._load_envelopes([path])
        result = mod.consolidate(envelopes)
        assert result == {"r1:req1": {"final_action": "NORMAL_COMPLETE"}}

    def test_malformed_jsonl_line_skipped(self, tmp_path):
        mod = _import_module()
        path = tmp_path / "input.jsonl"
        path.write_text(
            "this is not json\n"
            + json.dumps({
                "event_type": "request.meta_updated",
                "run_id": "r1",
                "request_id": "req1",
                "created_at": "2026-05-15T10:00:00",
                "payload": {"meta": {"x": 1}},
            })
            + "\n",
            encoding="utf-8",
        )
        envelopes = mod._load_envelopes([path])
        result = mod.consolidate(envelopes)
        assert result == {"r1:req1": {"x": 1}}

    def test_end_to_end_via_main(self, tmp_path):
        """Verify the CLI entry point produces the expected output file."""
        mod = _import_module()
        input_path = tmp_path / "input.jsonl"
        output_path = tmp_path / "consolidated.json"
        _write_jsonl(
            input_path,
            [
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:00",
                    "payload": {"meta": {"a": 1}},
                },
                {
                    "event_type": "request.meta_updated",
                    "run_id": "r1",
                    "request_id": "req1",
                    "created_at": "2026-05-15T10:00:01",
                    "payload": {"meta": {"b": 2}},
                },
            ],
        )
        exit_code = mod.main(
            ["--input", str(input_path), "--output", str(output_path), "--pretty"]
        )
        assert exit_code == 0
        assert output_path.exists()
        loaded = json.loads(output_path.read_text(encoding="utf-8"))
        assert loaded == {"r1:req1": {"a": 1, "b": 2}}
