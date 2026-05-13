"""
Tests for moralstack.reports.conversation_export.
"""

from __future__ import annotations

import json
from typing import Any

from moralstack.reports.conversation_export import export_conversation_to_markdown


class _StubReadStore:
    """ReadStore stub returning fixed conversation data."""

    def __init__(self, requests: list[dict[str, Any]]):
        self._requests = requests

    def get_run(self, run_id: str): ...

    def get_all_runs(self, limit: int = 100):
        return []

    def get_runs_page(self, *args, **kwargs):
        return ([], 0)

    def get_request_domains(self):
        return []

    def get_request(self, run_id: str, request_id: str):
        return None

    def get_requests_for_run(self, run_id: str):
        return []

    def get_requests_for_conversation(self, conversation_id: str):
        return self._requests if conversation_id else []


def _make_request(
    *,
    request_id: str,
    turn_index: int,
    prompt: str,
    final_response: str,
    final_action: str = "NORMAL_COMPLETE",
    risk_score: float = 0.2,
) -> dict[str, Any]:
    meta = {
        "final_action": final_action,
        "risk_score": risk_score,
        "path": "DELIBERATIVE_PATH",
        "reason_codes": ["TEST"],
        "triggered_principles": ["safety"],
        "decision_reason": "test rationale",
    }
    return {
        "request_id": request_id,
        "turn_index": turn_index,
        "prompt": prompt,
        "final_response": final_response,
        "domain": "general",
        "created_at": 1715000000000,
        "meta_json": json.dumps(meta),
    }


class TestEmptyAndMissingData:
    def test_empty_conversation_id(self):
        md = export_conversation_to_markdown("")
        assert "Error" in md
        assert "empty conversation_id" in md

    def test_no_requests_found(self):
        store = _StubReadStore(requests=[])
        md = export_conversation_to_markdown("missing-conv", read_store=store)
        assert "missing-conv" in md
        assert "No requests found" in md
        assert "Total turns**: 0" in md


class TestFiveTurnConversation:
    def test_exports_all_turns_in_order(self):
        requests = [
            _make_request(
                request_id=f"req-{i}",
                turn_index=i,
                prompt=f"Turn {i} prompt",
                final_response=f"Reply {i}",
            )
            for i in range(5)
        ]
        store = _StubReadStore(requests=requests)
        md = export_conversation_to_markdown("conv-5turns", read_store=store)
        # All 5 turn headers present.
        for i in range(5):
            assert f"## Turn {i}" in md
            assert f"Turn {i} prompt" in md
            assert f"Reply {i}" in md
        # Order preserved (turn 0 appears before turn 4).
        idx_0 = md.index("## Turn 0")
        idx_4 = md.index("## Turn 4")
        assert idx_0 < idx_4

    def test_includes_governance_metadata(self):
        requests = [
            _make_request(
                request_id="req-1",
                turn_index=0,
                prompt="Q",
                final_response="A",
                final_action="SAFE_COMPLETE",
                risk_score=0.65,
            )
        ]
        store = _StubReadStore(requests=requests)
        md = export_conversation_to_markdown("conv-1", read_store=store)
        assert "SAFE_COMPLETE" in md
        assert "0.6500" in md
        assert "DELIBERATIVE_PATH" in md
        assert "test rationale" in md

    def test_includes_framework_version_and_compliance_note(self):
        from moralstack import __version__

        store = _StubReadStore(requests=[_make_request(request_id="r", turn_index=0, prompt="Q", final_response="A")])
        md = export_conversation_to_markdown("conv-1", read_store=store)
        assert f"MoralStack v{__version__}" in md
        assert "AI Act art. 12" in md


class TestRobustness:
    def test_missing_meta_json_handled(self):
        req = _make_request(request_id="r", turn_index=0, prompt="Q", final_response="A")
        req["meta_json"] = None
        store = _StubReadStore(requests=[req])
        md = export_conversation_to_markdown("conv-1", read_store=store)
        # Must not crash; turn header still present.
        assert "## Turn 0" in md

    def test_malformed_meta_json_handled(self):
        req = _make_request(request_id="r", turn_index=0, prompt="Q", final_response="A")
        req["meta_json"] = "{not valid json"
        store = _StubReadStore(requests=[req])
        md = export_conversation_to_markdown("conv-1", read_store=store)
        assert "## Turn 0" in md
