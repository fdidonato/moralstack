"""Tests for LocalEmbedder, HashingEmbedder, and embedder factory helpers."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from moralstack.orchestration.embedder import (
    DEFAULT_HASHING_DIM,
    HashingEmbedder,
    LocalEmbedder,
    OpenAIEmbedder,
    cosine_similarity,
)
from moralstack.sdk.bootstrap import _build_embedder, _resolve_embedder_provider
from moralstack.sdk.config import GovernanceConfig


class _CountingStubEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.call_count = 0
        self._vector = vector or [1.0, 0.0, 0.0]

    def embed(self, text: str) -> list[float]:
        self.call_count += 1
        return list(self._vector)


class TestHashingEmbedderProtocolConformance:
    def test_hashing_embedder_returns_list_of_floats(self) -> None:
        result = HashingEmbedder().embed("hello")
        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)

    def test_hashing_embedder_is_deterministic(self) -> None:
        a = HashingEmbedder().embed("hello world")
        b = HashingEmbedder().embed("hello world")
        assert a == b

    def test_hashing_embedder_output_dim_is_constant(self) -> None:
        h = HashingEmbedder()
        assert len(h.embed("abc")) == DEFAULT_HASHING_DIM == 512
        assert len(h.embed("xyz")) == DEFAULT_HASHING_DIM

    def test_hashing_embedder_empty_string_does_not_crash(self) -> None:
        result = HashingEmbedder().embed("")
        assert isinstance(result, list)
        assert len(result) == DEFAULT_HASHING_DIM
        assert all(x == 0.0 for x in result)

    def test_hashing_embedder_different_inputs_differ(self) -> None:
        h = HashingEmbedder()
        assert h.embed("apple") != h.embed("orange")

    def test_hashing_embedder_satisfies_protocol(self) -> None:
        assert hasattr(HashingEmbedder, "embed")
        assert callable(HashingEmbedder.embed)

    def test_custom_dim(self) -> None:
        assert len(HashingEmbedder(dim=64).embed("x")) == 64

    def test_invalid_dim_raises(self) -> None:
        with pytest.raises(ValueError, match="dim must be >= 1"):
            HashingEmbedder(dim=0)

    def test_identical_input_cosine_one(self) -> None:
        h = HashingEmbedder()
        text = "same input"
        assert cosine_similarity(h.embed(text), h.embed(text)) == pytest.approx(1.0)

    def test_different_inputs_cosine_below_one(self) -> None:
        h = HashingEmbedder()
        assert cosine_similarity(h.embed("foo"), h.embed("bar")) < 1.0


class TestLocalEmbedderWithFastembedMocked:
    def test_local_embedder_uses_fastembed_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_vector = [float(i) for i in range(384)]
        mock_model = MagicMock()
        mock_model.embed.return_value = iter([fake_vector])

        class FakeTextEmbedding:
            def __init__(self, model_name: str) -> None:
                self.model_name = model_name

            def embed(self, texts: list[str]):
                return mock_model.embed(texts)

        fake_module = types.ModuleType("fastembed")
        fake_module.TextEmbedding = FakeTextEmbedding
        monkeypatch.setitem(sys.modules, "fastembed", fake_module)

        result = LocalEmbedder().embed("test")
        assert isinstance(result, list)
        assert len(result) == 384
        mock_model.embed.assert_called_once()

    def test_local_embedder_output_dim_consistent_across_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_vector = [0.1] * 384
        mock_model = MagicMock()
        mock_model.embed.side_effect = lambda texts: iter([fake_vector])

        class FakeTextEmbedding:
            def __init__(self, model_name: str) -> None:
                pass

            def embed(self, texts: list[str]):
                return mock_model.embed(texts)

        fake_module = types.ModuleType("fastembed")
        fake_module.TextEmbedding = FakeTextEmbedding
        monkeypatch.setitem(sys.modules, "fastembed", fake_module)

        emb = LocalEmbedder()
        assert len(emb.embed("a")) == len(emb.embed("b"))

    def test_local_embedder_returns_list_not_ndarray(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_vector = [0.5, 0.5]
        mock_model = MagicMock()
        mock_model.embed.return_value = iter([fake_vector])

        class FakeTextEmbedding:
            def __init__(self, model_name: str) -> None:
                pass

            def embed(self, texts: list[str]):
                return mock_model.embed(texts)

        fake_module = types.ModuleType("fastembed")
        fake_module.TextEmbedding = FakeTextEmbedding
        monkeypatch.setitem(sys.modules, "fastembed", fake_module)

        result = LocalEmbedder().embed("x")
        assert isinstance(result, list)
        assert isinstance(result[0], float)


class TestLocalEmbedderFallback:
    def test_local_embedder_falls_back_to_hashing_when_fastembed_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "fastembed", None)
        emb = LocalEmbedder()
        result = emb.embed("test")
        assert isinstance(result, list)
        assert len(result) == DEFAULT_HASHING_DIM

    def test_local_embedder_fallback_dim_matches_hashing_embedder_constant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "fastembed", None)
        result = LocalEmbedder().embed("hello")
        assert len(result) == DEFAULT_HASHING_DIM


class TestBuildEmbedderFactory:
    def test_build_embedder_returns_local_by_default(self) -> None:
        with patch(
            "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
            side_effect=ImportError("fastembed not installed"),
        ):
            result = _build_embedder(GovernanceConfig(), api_key="sk-test", base_url=None)
        assert isinstance(result, LocalEmbedder)
        assert not isinstance(result, OpenAIEmbedder)

    def test_build_embedder_returns_openai_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.OpenAI"):
            result = _build_embedder(
                GovernanceConfig(embedder_provider="openai"),
                api_key="sk-test",
                base_url=None,
            )
        assert isinstance(result, OpenAIEmbedder)

    def test_build_embedder_openai_without_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
            _build_embedder(GovernanceConfig(embedder_provider="openai"), api_key="", base_url=None)

    def test_build_embedder_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MORALSTACK_EMBEDDER_PROVIDER", raising=False)
        cfg = GovernanceConfig()
        object.__setattr__(cfg, "embedder_provider", "anthropic")
        with pytest.raises(ValueError, match="anthropic"):
            _build_embedder(cfg, api_key="sk-test", base_url=None)

    def test_resolve_embedder_provider_invalid_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_EMBEDDER_PROVIDER", "sagemaker")
        with pytest.raises(ValueError, match="sagemaker"):
            _resolve_embedder_provider(GovernanceConfig())

    def test_resolve_embedder_provider_invalid_env_empty_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_EMBEDDER_PROVIDER", "")
        assert _resolve_embedder_provider(GovernanceConfig()) == "local"
