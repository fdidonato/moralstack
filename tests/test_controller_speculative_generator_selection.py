"""Unit tests for `_speculative_generate` draft-generator selection.

Covers: uses `request.upstream_draft_generator` when present (and never
`ctrl.policy.generate`); falls back to `self.policy` when the field is `None`;
selection affects ONLY the speculative call site; multi-turn ->
`generate_messages` on the selected generator; `module`/`model` in
`persist_kwargs` reflect the generator; and the `except TypeError` fallback
landmine (`controller.py`) uses the selected generator, not `self.policy`.

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest
from moralstack.utils.output_protection import OutputProtector


class _FakeGenerationResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens_used = 10
        self.prompt_tokens = 5
        self.completion_tokens = 5
        self.token_usage_source = "exact"
        self.prompt_used = None
        self.system_used = None


class _FakeGenerator:
    """Fake generator recording `.generate`/`.generate_messages` calls."""

    def __init__(self, model: str, text: str) -> None:
        self.model = model
        self._text = text
        self.calls: list[str] = []
        self.generate = MagicMock(side_effect=self._do_generate)
        self.generate_messages = MagicMock(side_effect=self._do_generate_messages)

    def _do_generate(self, *args, **kwargs):
        self.calls.append("generate")
        return _FakeGenerationResult(self._text)

    def _do_generate_messages(self, *args, **kwargs):
        self.calls.append("generate_messages")
        return _FakeGenerationResult(self._text)


def _make_controller(policy) -> OrchestrationController:
    return OrchestrationController(
        config=OrchestratorConfig(),
        policy=policy,
        risk_estimator=MagicMock(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )


class TestGeneratorSelection:
    def test_uses_upstream_generator_when_present(self):
        governance_gen = _FakeGenerator("governance-model-G", "governance text")
        ctrl = _make_controller(governance_gen)
        upstream_gen = _FakeGenerator("client-model-C", "client draft text")

        req = ProcessedRequest(prompt="hello", request_id="r1")
        req.upstream_draft_generator = upstream_gen

        draft, persist_kwargs = ctrl._speculative_generate(req)

        assert draft == "client draft text"
        upstream_gen.generate.assert_called_once()
        governance_gen.generate.assert_not_called()
        assert persist_kwargs is not None
        assert persist_kwargs["model"] == "client-model-C"
        assert persist_kwargs["module"] == "upstream_speculative"

    def test_falls_back_to_policy_when_field_is_none(self):
        governance_gen = _FakeGenerator("governance-model-G", "governance text")
        ctrl = _make_controller(governance_gen)

        req = ProcessedRequest(prompt="hello", request_id="r2")
        assert req.upstream_draft_generator is None

        draft, persist_kwargs = ctrl._speculative_generate(req)

        assert draft == "governance text"
        governance_gen.generate.assert_called_once()
        assert persist_kwargs is not None
        assert persist_kwargs["model"] == "governance-model-G"
        assert persist_kwargs["module"] == "policy"
        # Internal mode: no draft_origin key leaks into parsed_summary_json.
        assert "draft_origin" not in persist_kwargs.get("parsed_summary_json", "")

    def test_selection_affects_only_speculative_call_site(self):
        """`ctrl.policy.rewrite`/`refuse` are untouched by generator selection."""
        governance_gen = _FakeGenerator("governance-model-G", "governance text")
        governance_gen.rewrite = MagicMock(return_value=_FakeGenerationResult("rewritten"))
        governance_gen.refuse = MagicMock(return_value=_FakeGenerationResult("refusal"))
        ctrl = _make_controller(governance_gen)
        upstream_gen = _FakeGenerator("client-model-C", "client draft text")

        req = ProcessedRequest(prompt="hello", request_id="r3")
        req.upstream_draft_generator = upstream_gen

        ctrl._speculative_generate(req)

        governance_gen.rewrite.assert_not_called()
        governance_gen.refuse.assert_not_called()
        assert ctrl.policy is governance_gen

    def test_multi_turn_uses_generate_messages_on_selected_generator(self):
        from moralstack.orchestration.conversation_context import ConversationContext

        governance_gen = _FakeGenerator("governance-model-G", "governance text")
        ctrl = _make_controller(governance_gen)
        upstream_gen = _FakeGenerator("client-model-C", "client multi-turn draft")

        req = ProcessedRequest(prompt="second turn", request_id="r4")
        req.upstream_draft_generator = upstream_gen
        conv_ctx = MagicMock(spec=ConversationContext)
        conv_ctx.prior_turn_count = 1
        conv_ctx.native_context_messages = MagicMock(return_value=[{"role": "user", "content": "first turn"}])
        conv_ctx.observability_message_sections = MagicMock(return_value={})
        conv_ctx.context_shape_metadata = MagicMock(return_value={})
        req.conversation_context = conv_ctx

        draft, persist_kwargs = ctrl._speculative_generate(req)

        assert draft == "client multi-turn draft"
        assert upstream_gen.calls == ["generate_messages"]
        upstream_gen.generate.assert_not_called()
        governance_gen.generate.assert_not_called()
        governance_gen.generate_messages.assert_not_called()

    def test_typeerror_fallback_still_uses_selected_generator(self):
        """Landmine: the `except TypeError` fallback (controller.py) MUST use
        the selected `gen`, not hard-code `self.policy`."""
        governance_gen = _FakeGenerator("governance-model-G", "governance text")
        ctrl = _make_controller(governance_gen)

        upstream_gen = _FakeGenerator("client-model-C", "client fallback draft")
        # Simulate a generator whose `.generate(prompt=..., system=..., overrides=...)`
        # signature does not accept keyword args (raises TypeError), forcing the
        # `except TypeError: result = gen.generate(prompt_text)` fallback branch.
        call_log: list[tuple] = []

        def _generate(*args, **kwargs):
            if kwargs:
                raise TypeError("unexpected keyword argument")
            call_log.append(args)
            return _FakeGenerationResult("client fallback draft")

        upstream_gen.generate = MagicMock(side_effect=_generate)

        req = ProcessedRequest(prompt="hello", request_id="r5")
        req.upstream_draft_generator = upstream_gen

        draft, persist_kwargs = ctrl._speculative_generate(req)

        assert draft == "client fallback draft"
        assert persist_kwargs is not None
        assert persist_kwargs["model"] == "client-model-C"
        assert persist_kwargs["module"] == "upstream_speculative"
        # The fallback call must have gone to the upstream generator, never
        # silently substituting the governance policy.
        governance_gen.generate.assert_not_called()
        assert upstream_gen.generate.call_count == 2  # first raises, fallback succeeds
