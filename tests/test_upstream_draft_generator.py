"""Unit tests for `UpstreamDraftGenerator` (opt-in `generation="upstream_then_verify"`).

Covers: forwards prompt/system/overrides using the constructor model (never the
governance model); `generate_messages` multi-turn branch; `GenerationResult` shape;
`.model == constructor arg`; propagates client exceptions (never swallowed); empty
content -> `.text == ""` (never an exception); honors a real `GenerationOverrides`
object (both passthrough and non-passthrough semantics).
"""

from __future__ import annotations

from moralstack.models.base import GenerationOverrides
from moralstack.orchestration.upstream_draft import UpstreamDraftGenerator


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeUsage:
    total_tokens = 12
    prompt_tokens = 6
    completion_tokens = 6


class _FakeResponse:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, content: str | None = "client-model-C says hi", raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._content = content
        self._raises = raises

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, content: str | None = "client-model-C says hi", raises: Exception | None = None) -> None:
        self.chat = _FakeChat(_FakeCompletions(content=content, raises=raises))


class TestConstructorModel:
    def test_model_attribute_is_constructor_arg(self):
        gen = UpstreamDraftGenerator(client=_FakeClient(), model="client-model-C")
        assert gen.model == "client-model-C"

    def test_generate_uses_constructor_model_not_governance(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "governance-model-G")
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        gen.generate(prompt="hello", system="sys")
        assert client.chat.completions.calls[0]["model"] == "client-model-C"


class TestGenerate:
    def test_forwards_prompt_and_system(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        result = gen.generate(prompt="What is 2+2?", system="You are helpful.")
        call = client.chat.completions.calls[0]
        assert call["messages"] == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
        ]
        assert result.text == "client-model-C says hi"
        assert result.prompt_used == "What is 2+2?"
        assert result.system_used == "You are helpful."

    def test_no_system_omits_system_message(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        gen.generate(prompt="hi")
        call = client.chat.completions.calls[0]
        assert call["messages"] == [{"role": "user", "content": "hi"}]

    def test_generation_result_shape(self):
        client = _FakeClient(content="draft text")
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        result = gen.generate(prompt="hi")
        assert result.text == "draft text"
        assert result.tokens_used == 12
        assert result.prompt_tokens == 6
        assert result.completion_tokens == 6
        assert result.token_usage_source == "exact"
        assert result.prompt_used == "hi"
        assert result.system_used is None
        assert result.finish_reason == "stop"

    def test_empty_content_returns_empty_text_not_exception(self):
        client = _FakeClient(content=None)
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        result = gen.generate(prompt="hi")
        assert result.text == ""

    def test_whitespace_content_normalized_to_empty(self):
        client = _FakeClient(content="   ")
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        result = gen.generate(prompt="hi")
        assert result.text == ""

    def test_client_exception_propagates(self):
        client = _FakeClient(raises=RuntimeError("upstream boom"))
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        try:
            gen.generate(prompt="hi")
        except RuntimeError as e:
            assert "upstream boom" in str(e)
        else:
            raise AssertionError("expected RuntimeError to propagate")

    def test_honors_generation_overrides_non_passthrough(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        overrides = GenerationOverrides(max_tokens=222, temperature=0.11, top_p=0.55, passthrough_unset=False)
        gen.generate(prompt="hi", overrides=overrides)
        call = client.chat.completions.calls[0]
        assert call["max_tokens"] == 222
        assert call["temperature"] == 0.11
        assert call["top_p"] == 0.55

    def test_passthrough_unset_omits_unsent_fields(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        overrides = GenerationOverrides(max_tokens=999, temperature=None, top_p=None, passthrough_unset=True)
        gen.generate(prompt="hi", overrides=overrides)
        call = client.chat.completions.calls[0]
        assert call["max_tokens"] == 999
        assert "temperature" not in call
        assert "top_p" not in call


class TestCompletionTokensParamPerModelFamily:
    """`UpstreamDraftGenerator` must route the token-limit param through
    `completion_tokens_param` (`moralstack/utils/openai_params.py`), exactly
    like `OpenAIPolicy` (`moralstack/models/policy.py:233-234`) -- otherwise
    a `max_tokens` payload is rejected by gpt-5/o-series models and the
    opt-in mode silently falls back to internal regeneration for those
    families (Codex non-blocking finding)."""

    def test_legacy_model_family_uses_max_tokens(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="gpt-4o")
        gen.generate(prompt="hi", overrides=GenerationOverrides(max_tokens=123, passthrough_unset=False))
        call = client.chat.completions.calls[0]
        assert call["max_tokens"] == 123
        assert "max_completion_tokens" not in call

    def test_gpt5_model_family_uses_max_completion_tokens(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="gpt-5")
        gen.generate(prompt="hi", overrides=GenerationOverrides(max_tokens=123, passthrough_unset=False))
        call = client.chat.completions.calls[0]
        assert call["max_completion_tokens"] == 123
        assert "max_tokens" not in call

    def test_o_series_model_family_uses_max_completion_tokens(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="o3-mini")
        gen.generate(prompt="hi", overrides=GenerationOverrides(max_tokens=123, passthrough_unset=False))
        call = client.chat.completions.calls[0]
        assert call["max_completion_tokens"] == 123
        assert "max_tokens" not in call


class TestTimeoutBound:
    """Bounding the call (Codex round-2 performance finding): the upstream
    draft call must pass an explicit ``timeout`` to
    ``client.chat.completions.create``, mirroring ``OpenAIPolicy``'s own
    resolution of ``OPENAI_TIMEOUT_MS`` (`moralstack/models/policy.py:49-50,
    221-224`) -- same env var, same 60000ms default. The speculative join in
    ``speculative_overlap.py`` (do-not-modify) waits on the future without a
    timeout, so a hanging upstream client can only be bounded here, at the
    call site."""

    def test_default_timeout_seconds_passed_to_client(self, monkeypatch):
        monkeypatch.delenv("OPENAI_TIMEOUT_MS", raising=False)
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        gen.generate(prompt="hi")
        call = client.chat.completions.calls[0]
        assert call["timeout"] == 60.0

    def test_env_timeout_ms_overrides_default(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TIMEOUT_MS", "5000")
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        gen.generate(prompt="hi")
        call = client.chat.completions.calls[0]
        assert call["timeout"] == 5.0

    def test_timeout_applies_to_generate_messages_too(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TIMEOUT_MS", "1500")
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        gen.generate_messages(messages=[{"role": "user", "content": "hi"}])
        call = client.chat.completions.calls[0]
        assert call["timeout"] == 1.5

    def test_timeout_resolved_once_at_construction_not_per_call(self, monkeypatch):
        """Mirrors `OpenAIPolicy`: the env var is read once at construction
        (`self._timeout`), not re-read on every call."""
        monkeypatch.setenv("OPENAI_TIMEOUT_MS", "9000")
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        monkeypatch.setenv("OPENAI_TIMEOUT_MS", "1")
        gen.generate(prompt="hi")
        call = client.chat.completions.calls[0]
        assert call["timeout"] == 9.0


class TestGenerateMessages:
    def test_multi_turn_forwards_messages_verbatim(self):
        client = _FakeClient()
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "reply 1"},
            {"role": "user", "content": "turn 2"},
        ]
        result = gen.generate_messages(messages=messages)
        call = client.chat.completions.calls[0]
        assert call["messages"] == messages
        assert call["model"] == "client-model-C"
        assert result.messages_used == messages
        assert result.prompt_used is None
        assert result.system_used is None

    def test_empty_content_in_messages_branch_returns_empty_text(self):
        client = _FakeClient(content="")
        gen = UpstreamDraftGenerator(client=client, model="client-model-C")
        result = gen.generate_messages(messages=[{"role": "user", "content": "hi"}])
        assert result.text == ""
