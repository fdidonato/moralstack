"""
Test suite for moralstack/orchestration/embedder.py.

Tests are pure unit tests: the OpenAI client is fully mocked. No network calls.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from moralstack.orchestration.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    EmbedderProtocol,
    OpenAIEmbedder,
    cosine_similarity,
)

# =============================================================================
# cosine_similarity (pure function)
# =============================================================================


class TestCosineSimilarity:
    """Tests for the cosine_similarity pure function."""

    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_opposite_vectors_return_minus_one(self):
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_known_value(self):
        """sanity check: cos(45deg) between [1,0] and [1,1] is sqrt(2)/2."""
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = math.sqrt(2.0) / 2.0
        assert cosine_similarity(a, b) == pytest.approx(expected)

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0

    def test_both_zero_vectors_return_zero(self):
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_empty_vectors_return_zero(self):
        assert cosine_similarity([], []) == 0.0

    def test_different_length_raises(self):
        with pytest.raises(ValueError, match="equally-sized"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_scale_invariant(self):
        """Cosine similarity is invariant under positive scaling."""
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]
        assert cosine_similarity(a, b) == pytest.approx(1.0)


# =============================================================================
# EmbedderProtocol (structural conformance)
# =============================================================================


class TestEmbedderProtocolConformance:
    """Verifies that production and mock implementations satisfy EmbedderProtocol."""

    def test_openai_embedder_is_protocol_compliant(self):
        """OpenAIEmbedder exposes the embed(str) -> list[float] method."""
        # Structural check: the method exists and is callable.
        # We do not instantiate the class here (needs API key); we check the class itself.
        assert hasattr(OpenAIEmbedder, "embed")
        assert callable(OpenAIEmbedder.embed)

    def test_simple_mock_satisfies_protocol(self):
        """A minimal class with embed() should be acceptable as EmbedderProtocol."""

        class StubEmbedder:
            def embed(self, text: str) -> list[float]:
                return [0.0]

        stub: EmbedderProtocol = StubEmbedder()  # Static type-check accepts this.
        assert stub.embed("anything") == [0.0]


# =============================================================================
# OpenAIEmbedder — initialization
# =============================================================================


class TestOpenAIEmbedderInit:
    """Tests for OpenAIEmbedder.__init__ configuration resolution."""

    def test_uses_constructor_api_key(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            emb = OpenAIEmbedder(api_key="sk-test-explicit")
            assert emb._api_key == "sk-test-explicit"
            mock_openai_cls.assert_called_once()
            call_kwargs = mock_openai_cls.call_args.kwargs
            assert call_kwargs["api_key"] == "sk-test-explicit"

    def test_uses_env_api_key_when_not_provided(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-env")
        with patch("openai.OpenAI") as mock_openai_cls:
            emb = OpenAIEmbedder()
            assert emb._api_key == "sk-test-env"
            mock_openai_cls.assert_called_once()

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
            OpenAIEmbedder()

    def test_constructor_api_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        with patch("openai.OpenAI"):
            emb = OpenAIEmbedder(api_key="sk-explicit-override")
            assert emb._api_key == "sk-explicit-override"

    def test_default_model_is_small(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
        with patch("openai.OpenAI"):
            emb = OpenAIEmbedder()
            assert emb.model == DEFAULT_EMBEDDING_MODEL
            assert emb.model == "text-embedding-3-small"

    def test_constructor_model_overrides_default(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        with patch("openai.OpenAI"):
            emb = OpenAIEmbedder(model="text-embedding-3-large")
            assert emb.model == "text-embedding-3-large"

    def test_env_model_used_when_no_constructor_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")
        with patch("openai.OpenAI"):
            emb = OpenAIEmbedder()
            assert emb.model == "text-embedding-ada-002"

    def test_base_url_passed_to_client_when_provided(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        with patch("openai.OpenAI") as mock_openai_cls:
            OpenAIEmbedder(base_url="https://custom.example.com")
            call_kwargs = mock_openai_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "https://custom.example.com"

    def test_base_url_not_passed_when_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        with patch("openai.OpenAI") as mock_openai_cls:
            OpenAIEmbedder()
            call_kwargs = mock_openai_cls.call_args.kwargs
            assert "base_url" not in call_kwargs


# =============================================================================
# OpenAIEmbedder.embed — happy path and error handling
# =============================================================================


def _build_embedder_with_mock_response(embedding: list[float], monkeypatch) -> tuple[OpenAIEmbedder, MagicMock]:
    """Helper: build an OpenAIEmbedder whose underlying client returns a preset embedding."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_embedding_item = MagicMock()
        mock_embedding_item.embedding = embedding
        mock_response = MagicMock()
        mock_response.data = [mock_embedding_item]
        mock_client.embeddings.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client
        emb = OpenAIEmbedder()
    return emb, mock_client


class TestOpenAIEmbedderEmbedHappyPath:
    """Tests OpenAIEmbedder.embed() with mocked responses."""

    def test_returns_list_of_floats(self, monkeypatch):
        emb, _ = _build_embedder_with_mock_response([0.1, 0.2, 0.3], monkeypatch)
        result = emb.embed("hello")
        assert isinstance(result, list)
        assert result == [0.1, 0.2, 0.3]

    def test_passes_text_to_client(self, monkeypatch):
        emb, client = _build_embedder_with_mock_response([0.0], monkeypatch)
        emb.embed("test prompt")
        client.embeddings.create.assert_called_once()
        call_kwargs = client.embeddings.create.call_args.kwargs
        assert call_kwargs["input"] == "test prompt"

    def test_passes_model_to_client(self, monkeypatch):
        emb, client = _build_embedder_with_mock_response([0.0], monkeypatch)
        emb.embed("x")
        call_kwargs = client.embeddings.create.call_args.kwargs
        assert call_kwargs["model"] == DEFAULT_EMBEDDING_MODEL

    def test_empty_input_text_still_calls_client(self, monkeypatch):
        """Empty input is passed through; caller decides how to handle empty embeddings."""
        emb, client = _build_embedder_with_mock_response([0.0], monkeypatch)
        emb.embed("")
        client.embeddings.create.assert_called_once()


class TestOpenAIEmbedderEmbedErrorHandling:
    """Tests OpenAIEmbedder.embed() error paths."""

    def test_raises_when_response_has_no_data(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.data = []
            mock_client.embeddings.create.return_value = mock_response
            mock_openai_cls.return_value = mock_client
            emb = OpenAIEmbedder()

        with pytest.raises(RuntimeError, match="returned no data"):
            emb.embed("anything")

    def test_raises_when_data_entry_has_no_embedding(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_embedding_item = MagicMock(spec=[])  # Empty spec -> no attributes
            mock_response = MagicMock()
            mock_response.data = [mock_embedding_item]
            mock_client.embeddings.create.return_value = mock_response
            mock_openai_cls.return_value = mock_client
            emb = OpenAIEmbedder()

        with pytest.raises(RuntimeError, match="without 'embedding' attribute"):
            emb.embed("anything")

    def test_propagates_api_errors(self, monkeypatch):
        """Network or API errors from the openai client are propagated unchanged."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_client.embeddings.create.side_effect = RuntimeError("simulated API failure")
            mock_openai_cls.return_value = mock_client
            emb = OpenAIEmbedder()

        with pytest.raises(RuntimeError, match="simulated API failure"):
            emb.embed("anything")
