"""Tests for OpenAIEmbedder token accounting instrumentation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from moralstack.orchestration.embedder import LocalEmbedder, OpenAIEmbedder

_DEFAULT_USAGE = object()


def _mock_embed_response(*, usage: object | None = _DEFAULT_USAGE) -> SimpleNamespace:
    embedding = [0.1, 0.2, 0.3]
    entry = SimpleNamespace(embedding=embedding)
    if usage is _DEFAULT_USAGE:
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=0, total_tokens=10)
    return SimpleNamespace(data=[entry], usage=usage)


def test_embed_records_llm_call_with_usage_on_success():
    embedder = OpenAIEmbedder(api_key="sk-test")
    response = _mock_embed_response()
    embedder._client = MagicMock()
    embedder._client.embeddings.create.return_value = response
    captured: dict[str, object] = {}

    def _record(_logger, _diag, persist_kwargs):
        captured.update(persist_kwargs or {})

    with patch("moralstack.orchestration.persistence_helpers.record_llm_call", side_effect=_record):
        result = embedder.embed("hello")

    assert result == [0.1, 0.2, 0.3]
    assert captured["module"] == "embedder"
    assert captured["action"] == "embed"
    assert captured["call_kind"] == "embedding"
    usage_json = json.loads(str(captured["token_usage_json"]))
    assert usage_json["source"] == "exact"
    assert usage_json["total_tokens"] == 10


def test_embed_records_missing_usage_when_response_has_no_usage_attr():
    embedder = OpenAIEmbedder(api_key="sk-test")
    response = _mock_embed_response(usage=None)
    delattr(response, "usage")
    embedder._client = MagicMock()
    embedder._client.embeddings.create.return_value = response
    captured: dict[str, object] = {}

    def _record(_logger, _diag, persist_kwargs):
        captured.update(persist_kwargs or {})

    with patch("moralstack.orchestration.persistence_helpers.record_llm_call", side_effect=_record):
        embedder.embed("hello")

    assert captured.get("token_usage_json") is None


def test_embed_instrumentation_failure_does_not_break_embedding():
    embedder = OpenAIEmbedder(api_key="sk-test")
    response = _mock_embed_response()
    embedder._client = MagicMock()
    embedder._client.embeddings.create.return_value = response

    with patch("moralstack.orchestration.persistence_helpers.record_llm_call", side_effect=RuntimeError("boom")):
        result = embedder.embed("hello")

    assert result == [0.1, 0.2, 0.3]


def test_embed_still_calls_client_exactly_once_with_instrumentation():
    embedder = OpenAIEmbedder(api_key="sk-test")
    response = _mock_embed_response()
    embedder._client = MagicMock()
    embedder._client.embeddings.create.return_value = response

    with patch("moralstack.orchestration.persistence_helpers.record_llm_call"):
        embedder.embed("hello")

    embedder._client.embeddings.create.assert_called_once()


def test_local_embedder_embed_does_not_emit_llm_call():
    with patch("moralstack.orchestration.persistence_helpers.record_llm_call") as record_mock:
        embedder = LocalEmbedder()
        embedder.embed("hello world")
    record_mock.assert_not_called()
