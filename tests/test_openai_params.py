"""Tests for moralstack.utils.openai_params."""

from moralstack.utils.openai_params import (
    completion_tokens_param,
    uses_max_completion_tokens,
)


class TestUsesMaxCompletionTokens:
    """Tests for uses_max_completion_tokens."""

    def test_gpt4o_returns_false(self):
        assert uses_max_completion_tokens("gpt-4o") is False

    def test_gpt4_turbo_returns_false(self):
        assert uses_max_completion_tokens("gpt-4-turbo") is False

    def test_gpt35_returns_false(self):
        assert uses_max_completion_tokens("gpt-3.5-turbo") is False

    def test_gpt52_returns_true(self):
        assert uses_max_completion_tokens("gpt-5.2") is True

    def test_gpt51_returns_true(self):
        assert uses_max_completion_tokens("gpt-5.1") is True

    def test_gpt5_returns_true(self):
        assert uses_max_completion_tokens("gpt-5") is True

    def test_o3_mini_returns_true(self):
        assert uses_max_completion_tokens("o3-mini") is True

    def test_o1_returns_true(self):
        assert uses_max_completion_tokens("o1") is True

    def test_o4_mini_returns_true(self):
        assert uses_max_completion_tokens("o4-mini") is True

    def test_empty_returns_false(self):
        assert uses_max_completion_tokens("") is False

    def test_none_returns_false(self):
        assert uses_max_completion_tokens(None) is False

    def test_case_insensitive(self):
        assert uses_max_completion_tokens("GPT-5.2") is True
        assert uses_max_completion_tokens("O3-mini") is True


class TestCompletionTokensParam:
    """Tests for completion_tokens_param."""

    def test_gpt4o_returns_max_tokens(self):
        result = completion_tokens_param("gpt-4o", 1024)
        assert result == {"max_tokens": 1024}

    def test_gpt52_returns_max_completion_tokens(self):
        result = completion_tokens_param("gpt-5.2", 1024)
        assert result == {"max_completion_tokens": 1024}

    def test_o3_mini_returns_max_completion_tokens(self):
        result = completion_tokens_param("o3-mini", 600)
        assert result == {"max_completion_tokens": 600}

    def test_value_preserved(self):
        result = completion_tokens_param("gpt-4o", 512)
        assert result["max_tokens"] == 512
        result2 = completion_tokens_param("gpt-5.2", 512)
        assert result2["max_completion_tokens"] == 512
