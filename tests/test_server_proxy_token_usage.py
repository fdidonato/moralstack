"""Tests for proxy synthetic completion usage field."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.orchestration.types import (  # noqa: E402
    FinalResponse,
    OrchestratorResult,
    ResponseMetadata,
    ResponseType,
)
from moralstack.server.proxy import _build_synthetic_chat_completion, create_app  # noqa: E402


def _make_result_with_tokens(
    final_action: str = "NORMAL_COMPLETE",
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
) -> OrchestratorResult:
    metadata = ResponseMetadata()
    metadata.final_action = final_action
    metadata.risk_score = 0.2
    metadata.input_tokens = input_tokens
    metadata.output_tokens = output_tokens
    metadata.total_tokens = total_tokens
    rtype = ResponseType.FULL_REFUSAL if final_action == "REFUSE" else ResponseType.WITH_CAVEAT
    response = FinalResponse(content="Answer.", response_type=rtype, metadata=metadata)
    return OrchestratorResult(
        response=response,
        request_id="req-1",
        path_taken="deliberative",
        path="DELIBERATIVE_PATH",
        total_cycles=1,
        converged=True,
    )


def test_synthetic_completion_usage_reflects_result_metadata():
    payload = _build_synthetic_chat_completion(
        "hi",
        model="gpt-4",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    assert payload["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_synthetic_completion_usage_defaults_to_zero_without_metadata():
    payload = _build_synthetic_chat_completion("hi", model="gpt-4")
    assert payload["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_pipeline_failure_usage_is_zero_no_result_available():
    from moralstack.sdk.config import GovernanceConfig

    mock_orchestrator = MagicMock()
    mock_orchestrator.process = MagicMock(side_effect=RuntimeError("pipeline failed"))
    mock_openai = MagicMock()
    app = create_app(openai_client=mock_openai, orchestrator=mock_orchestrator, config=GovernanceConfig())
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_refuse_usage_zero_or_absent_no_generation_occurred():
    from moralstack.sdk.config import GovernanceConfig

    mock_orchestrator = MagicMock()
    mock_orchestrator.process = MagicMock(return_value=_make_result_with_tokens("REFUSE"))
    mock_openai = MagicMock()
    app = create_app(openai_client=mock_openai, orchestrator=mock_orchestrator, config=GovernanceConfig())
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["usage"]["total_tokens"] == 0
