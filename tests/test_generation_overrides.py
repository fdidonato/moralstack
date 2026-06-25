"""Tests for client-supplied generation overrides and the env max_tokens default.

Covers:
- ``GenerationOverrides.from_mapping`` parsing (alias precedence, defensive casts).
- ``OPENAI_MAX_TOKENS`` env default applied by ``OpenAIPolicy.generate`` /
  ``generate_messages`` when no config and no override is given.
- Per-request overrides winning over the policy defaults on the delivered-answer
  generators (``generate`` / ``generate_messages`` / ``rewrite``).
- ``refuse`` ignoring per-request overrides (no transport, keeps the env default).
- Proxy passthrough mode (``passthrough_unset=True``): a field the client did
  not send is **omitted** from the OpenAI call (model default) instead of
  falling back to the env default; sent fields are still applied.

All offline: the OpenAI client is replaced with a capturing fake.
"""

from __future__ import annotations

from moralstack.models.base import GenerationConfig, GenerationOverrides
from moralstack.models.policy import OpenAIPolicy


# --------------------------------------------------------------------------- #
# Capturing fake OpenAI client
# --------------------------------------------------------------------------- #
class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeUsage:
    total_tokens = 12
    prompt_tokens = 6
    completion_tokens = 6


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse("ok")


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


def _make_policy(monkeypatch, *, max_tokens_env: str | None = None) -> tuple[OpenAIPolicy, _FakeCompletions]:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0.7")
    monkeypatch.setenv("OPENAI_TOP_P", "0.9")
    if max_tokens_env is None:
        monkeypatch.delenv("OPENAI_MAX_TOKENS", raising=False)
    else:
        monkeypatch.setenv("OPENAI_MAX_TOKENS", max_tokens_env)
    policy = OpenAIPolicy(api_key="sk-test", model="gpt-4o")
    fake = _FakeClient()
    policy.client = fake  # type: ignore[assignment]
    return policy, fake.chat.completions


# --------------------------------------------------------------------------- #
# GenerationOverrides.from_mapping
# --------------------------------------------------------------------------- #
class TestFromMapping:
    def test_empty_mapping_returns_none(self):
        assert GenerationOverrides.from_mapping({}) is None
        assert GenerationOverrides.from_mapping(None) is None

    def test_no_relevant_keys_returns_none(self):
        assert GenerationOverrides.from_mapping({"model": "x", "messages": []}) is None

    def test_max_tokens_parsed(self):
        ov = GenerationOverrides.from_mapping({"max_tokens": 200})
        assert ov is not None and ov.max_tokens == 200

    def test_max_completion_tokens_takes_precedence(self):
        ov = GenerationOverrides.from_mapping({"max_completion_tokens": 300, "max_tokens": 200})
        assert ov is not None and ov.max_tokens == 300

    def test_temperature_and_top_p_parsed(self):
        ov = GenerationOverrides.from_mapping({"temperature": 0.2, "top_p": 0.5})
        assert ov is not None
        assert ov.temperature == 0.2
        assert ov.top_p == 0.5

    def test_non_positive_max_tokens_ignored(self):
        assert GenerationOverrides.from_mapping({"max_tokens": 0}) is None
        assert GenerationOverrides.from_mapping({"max_tokens": -5}) is None

    def test_non_numeric_values_ignored(self):
        assert GenerationOverrides.from_mapping({"max_tokens": "abc"}) is None
        assert GenerationOverrides.from_mapping({"temperature": "warm"}) is None

    def test_string_numbers_coerced(self):
        ov = GenerationOverrides.from_mapping({"max_tokens": "256", "temperature": "0.3"})
        assert ov is not None
        assert ov.max_tokens == 256
        assert ov.temperature == 0.3


# --------------------------------------------------------------------------- #
# Env default (OPENAI_MAX_TOKENS)
# --------------------------------------------------------------------------- #
class TestEnvMaxTokensDefault:
    def test_env_value_used_by_generate(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        policy.generate("hello")
        assert completions.calls[-1]["max_tokens"] == 4096

    def test_default_when_env_absent(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env=None)
        policy.generate("hello")
        assert completions.calls[-1]["max_tokens"] == 4096

    def test_custom_env_value(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="1024")
        policy.generate_messages(messages=[{"role": "user", "content": "hi"}])
        assert completions.calls[-1]["max_tokens"] == 1024


# --------------------------------------------------------------------------- #
# Per-request overrides win on the delivered-answer generators
# --------------------------------------------------------------------------- #
class TestOverridesApplied:
    def test_generate_override_wins_over_default(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides(max_tokens=128, temperature=0.0)
        policy.generate("hello", overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 128
        assert call["temperature"] == 0.0
        # top_p untouched -> policy default
        assert call["top_p"] == 0.9

    def test_generate_messages_override(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides(max_tokens=64, top_p=0.5)
        policy.generate_messages(messages=[{"role": "user", "content": "hi"}], overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 64
        assert call["top_p"] == 0.5

    def test_override_beats_config(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        cfg = GenerationConfig(max_tokens=2048, temperature=0.5)
        ov = GenerationOverrides(max_tokens=100)
        policy.generate("hello", config=cfg, overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 100  # override > config
        assert call["temperature"] == 0.5  # config kept (no override field)

    def test_rewrite_forwards_overrides(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides(max_tokens=77)
        policy.rewrite("prompt", "draft", "guidance", overrides=ov)
        assert completions.calls[-1]["max_tokens"] == 77

    def test_no_override_keeps_default(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        policy.generate("hello")
        call = completions.calls[-1]
        assert call["max_tokens"] == 4096
        assert call["temperature"] == 0.7
        assert call["top_p"] == 0.9


# --------------------------------------------------------------------------- #
# Rewrite guards the enumerated single-token answer against soft-feedback flips
# --------------------------------------------------------------------------- #
class TestRewriteEnumeratedInvariance:
    def test_rewrite_prompt_instructs_unchanged_for_enumerated_draft(self, monkeypatch):
        """The rewrite prompt tells the model to keep a single enumerated draft
        unchanged when feedback carries no hard safety requirement.

        Regression lock for boolq_contrast: soft/balance/disclaimer feedback must
        never flip which option (e.g. TRUE/FALSE) an enumerated answer selects.
        """
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        policy.rewrite(
            "Question: Is X true?",
            "FALSE",
            "[BALANCE] present both sides; acknowledge variability",
        )
        user_msg = next(m["content"] for m in completions.calls[-1]["messages"] if m["role"] == "user")
        assert "single enumerated answer" in user_msg
        assert "return the CURRENT DRAFT UNCHANGED" in user_msg
        assert "no hard safety requirement" in user_msg


# --------------------------------------------------------------------------- #
# REFUSE ignores per-request overrides (no transport into refuse)
# --------------------------------------------------------------------------- #
class TestRefuseExcluded:
    def test_refuse_uses_env_default_not_override(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        # refuse() has no ``overrides`` parameter; a client max_tokens cannot
        # reach it, so the safety message keeps the operator-set default.
        policy.refuse("prompt", "guidance")
        assert completions.calls[-1]["max_tokens"] == 4096

    def test_refuse_has_no_overrides_param(self):
        import inspect

        sig = inspect.signature(OpenAIPolicy.refuse)
        assert "overrides" not in sig.parameters


# --------------------------------------------------------------------------- #
# Proxy passthrough mode (omit unset -> model default)
# --------------------------------------------------------------------------- #
class TestProxyPassthroughFromMapping:
    def test_empty_mapping_returns_instance_when_passthrough(self):
        ov = GenerationOverrides.from_mapping({}, passthrough_unset=True)
        assert ov is not None
        assert ov.passthrough_unset is True
        assert ov.is_empty()

    def test_none_mapping_returns_instance_when_passthrough(self):
        ov = GenerationOverrides.from_mapping(None, passthrough_unset=True)
        assert ov is not None
        assert ov.passthrough_unset is True
        assert ov.is_empty()

    def test_partial_mapping_keeps_passthrough_flag(self):
        ov = GenerationOverrides.from_mapping({"max_tokens": 200}, passthrough_unset=True)
        assert ov is not None
        assert ov.passthrough_unset is True
        assert ov.max_tokens == 200
        assert ov.temperature is None
        assert ov.top_p is None

    def test_default_is_not_passthrough(self):
        ov = GenerationOverrides.from_mapping({"max_tokens": 200})
        assert ov is not None
        assert ov.passthrough_unset is False


class TestProxyPassthroughGeneration:
    def test_empty_passthrough_omits_all_sampling(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping({}, passthrough_unset=True)
        policy.generate("hello", overrides=ov)
        call = completions.calls[-1]
        # The env default (4096 / 0.7 / 0.9) must NOT leak into the call.
        assert "max_tokens" not in call
        assert "max_completion_tokens" not in call
        assert "temperature" not in call
        assert "top_p" not in call

    def test_partial_passthrough_sends_only_max_tokens(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping({"max_tokens": 200}, passthrough_unset=True)
        policy.generate("hello", overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 200
        assert "temperature" not in call
        assert "top_p" not in call

    def test_partial_passthrough_sends_only_temperature(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping({"temperature": 0.0}, passthrough_unset=True)
        policy.generate("hello", overrides=ov)
        call = completions.calls[-1]
        assert call["temperature"] == 0.0
        assert "max_tokens" not in call
        assert "max_completion_tokens" not in call
        assert "top_p" not in call

    def test_full_passthrough_sends_all(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping(
            {"max_tokens": 128, "temperature": 0.2, "top_p": 0.5},
            passthrough_unset=True,
        )
        policy.generate("hello", overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 128
        assert call["temperature"] == 0.2
        assert call["top_p"] == 0.5

    def test_passthrough_applies_to_generate_messages(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping({"temperature": 0.1}, passthrough_unset=True)
        policy.generate_messages(messages=[{"role": "user", "content": "hi"}], overrides=ov)
        call = completions.calls[-1]
        assert call["temperature"] == 0.1
        assert "max_tokens" not in call
        assert "top_p" not in call

    def test_passthrough_applies_to_rewrite(self, monkeypatch):
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping({"max_tokens": 99}, passthrough_unset=True)
        policy.rewrite("prompt", "draft", "guidance", overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 99
        assert "temperature" not in call
        assert "top_p" not in call

    def test_non_passthrough_empty_override_keeps_env_default(self, monkeypatch):
        # SDK/CLI path: an all-empty mapping yields None (not passthrough), so the
        # env defaults still apply -- the legacy behavior is preserved.
        policy, completions = _make_policy(monkeypatch, max_tokens_env="4096")
        ov = GenerationOverrides.from_mapping({})  # passthrough_unset defaults False
        assert ov is None
        policy.generate("hello", overrides=ov)
        call = completions.calls[-1]
        assert call["max_tokens"] == 4096
        assert call["temperature"] == 0.7
        assert call["top_p"] == 0.9
