"""
Public interface for the MoralStack SDK.

govern(client)          -- wrap an OpenAI client with governance
GovernedClient          -- transparent proxy with governance on chat.completions.create()
GovernedChat            -- proxy for client.chat
GovernedCompletions     -- intercept create() with pre-call deliberation
GovernedStreamResponse  -- wrap a stream with governance metadata
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Iterator

from moralstack.core.types import Turn, UserContext
from moralstack.orchestration.types import ProcessedRequest
from moralstack.sdk.bootstrap import _bootstrap_pipeline
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.errors import GovernancePipelineError
from moralstack.sdk.response import GovernedResponse
from moralstack.sdk.session import SessionState
from moralstack.utils.env_loader import load_env

if TYPE_CHECKING:
    from moralstack.runtime.orchestrator import Orchestrator


# =============================================================================
# Helpers
# =============================================================================


def _extract_last_user_message(messages: list[dict[str, Any]]) -> str:
    """
    Return the content of the last message with role='user'.
    Returns empty string if no user message is present.
    """
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # Multimodal format: list of {type, text} or {type, image_url}
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return " ".join(parts)
            return str(content)
    return ""


def _messages_to_turns(messages: list[dict[str, Any]]) -> list[Turn]:
    """
    Convert a list of OpenAI messages to Turn objects for the pipeline.
    Excludes messages with role='system' (not conversational turns).
    """
    turns: list[Turn] = []
    for msg in messages:
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(parts)
        turns.append(Turn(role=role, content=str(content)))
    return turns


def _build_safe_system_injection(result: Any) -> str:
    """
    Build governance guidance to inject into the system prompt
    for SAFE_COMPLETE cases. Uses content assembled from ResponseAssembler.
    """
    governance_content = result.response.content
    if governance_content:
        return "You must follow these governance constraints for this response:\n" f"{governance_content}"
    # Fallback: use reason_codes and triggered_principles
    meta = result.response.metadata
    parts: list[str] = []
    if meta.triggered_principles:
        parts.append(f"Relevant principles: {', '.join(meta.triggered_principles)}.")
    if meta.reason_codes:
        parts.append(f"Reason codes: {', '.join(meta.reason_codes)}.")
    if meta.decision_reason:
        parts.append(meta.decision_reason)
    return "\n".join(parts) if parts else "Respond with appropriate care and caveats."


def _inject_safe_guidance(kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
    """
    Clone kwargs and inject governance guidance into the system prompt.
    If no system message exists, prepends one.
    """
    guidance = _build_safe_system_injection(result)
    messages = list(kwargs.get("messages", []))

    # Look for an existing system message
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            original_content = msg.get("content", "")
            messages[i] = {
                **msg,
                "content": f"{original_content}\n\n{guidance}".strip(),
            }
            break
    else:
        # No system message: insert at the beginning
        messages.insert(0, {"role": "system", "content": guidance})

    return {**kwargs, "messages": messages}


# =============================================================================
# GovernedStreamResponse
# =============================================================================


class GovernedStreamResponse:
    """
    Wrap an OpenAI stream with governance metadata.

    Same iterator interface as OpenAI: ``for chunk in response`` works.
    Governance metadata is available via ``response.governance_metadata``
    before iteration (deliberation runs before the stream).
    """

    def __init__(self, stream: Any, result: Any) -> None:
        self._stream = stream
        self.governance_metadata = GovernedResponse.from_normal(None, result).governance_metadata

    def __iter__(self) -> Iterator[Any]:
        return iter(self._stream)

    def __enter__(self) -> GovernedStreamResponse:
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        if hasattr(self._stream, "__exit__"):
            self._stream.__exit__(*args)


class GovernedRefusalStream:
    """Synthetic stream for REFUSE when stream=True."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.governance_metadata = GovernedResponse.from_refusal(result).governance_metadata

    def __iter__(self) -> Iterator[Any]:
        # Yield a single synthetic chunk with refusal text
        yield _SyntheticStreamChunk(self._result.response.content)

    def __enter__(self) -> GovernedRefusalStream:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _SyntheticStreamChunk:
    """Synthetic chunk for refusal streams."""

    def __init__(self, content: str) -> None:
        self.choices = [_SyntheticStreamChoice(content)]
        self.model = "moralstack-refuse"
        self.id = f"refuse-{uuid.uuid4().hex[:8]}"


class _SyntheticStreamChoice:
    def __init__(self, content: str) -> None:
        self.delta = _SyntheticDelta(content)
        self.finish_reason = "stop"
        self.index = 0


class _SyntheticDelta:
    def __init__(self, content: str) -> None:
        self.content = content
        self.role = "assistant"


# =============================================================================
# GovernedCompletions
# =============================================================================


class GovernedCompletions:
    """Intercept chat.completions.create() with pre-call deliberation."""

    def __init__(self, governed: GovernedClient) -> None:
        self._governed = governed

    def create(self, **kwargs: Any) -> GovernedResponse | GovernedStreamResponse | GovernedRefusalStream:
        """
        Deliberate on the prompt, then:
        - REFUSE: return GovernedResponse/GovernedRefusalStream without calling OpenAI
        - SAFE_COMPLETE: inject guidance into system prompt, call OpenAI
        - NORMAL_COMPLETE: call OpenAI directly
        """
        is_stream = kwargs.get("stream", False)
        messages = kwargs.get("messages", [])

        user_message = _extract_last_user_message(messages)
        # History excludes the last user message (the one being deliberated)
        history_messages = messages[:-1] if messages else []
        conversation_history = _messages_to_turns(history_messages)

        domain = self._governed._config.domain_overlay
        request = ProcessedRequest(
            prompt=user_message,
            conversation_history=conversation_history,
            user_context=UserContext(domain_overlay=domain),
        )

        session = self._governed._session
        conv_id = session.conversation_id
        turn_idx = session.next_turn_index()
        conv_state = session.current_state

        # --- Deliberation ---
        try:
            result = self._governed._orchestrator.process(
                request,
                conversation_id=conv_id,
                turn_index=turn_idx,
                parent_request_id=request.request_id,
                conversation_state=conv_state,
            )
        except Exception as e:
            return self._handle_pipeline_failure(e, kwargs, is_stream)

        session.update_from_result(result)

        final_action = result.response.metadata.final_action

        # --- Routing ---
        if final_action == "REFUSE":
            if is_stream:
                return GovernedRefusalStream(result)
            return GovernedResponse.from_refusal(result)

        if final_action == "SAFE_COMPLETE":
            modified_kwargs = _inject_safe_guidance(kwargs, result)
            if is_stream:
                stream = self._governed._client.chat.completions.create(**modified_kwargs)
                return GovernedStreamResponse(stream, result)
            openai_response = self._governed._client.chat.completions.create(**modified_kwargs)
            return GovernedResponse.from_safe(openai_response, result)

        # NORMAL_COMPLETE (or any other value)
        if is_stream:
            stream = self._governed._client.chat.completions.create(**kwargs)
            return GovernedStreamResponse(stream, result)
        openai_response = self._governed._client.chat.completions.create(**kwargs)
        return GovernedResponse.from_normal(openai_response, result)

    def _handle_pipeline_failure(
        self,
        error: Exception,
        kwargs: dict[str, Any],
        is_stream: bool,
    ) -> GovernedResponse | GovernedStreamResponse:
        """Handle pipeline error according to failure_policy."""
        policy = self._governed._config.failure_policy

        if policy == "passthrough":
            # Call original client without governance
            try:
                if is_stream:
                    stream = self._governed._client.chat.completions.create(**kwargs)

                    # Wrap stream with sentinel metadata
                    class _PassthroughStream:
                        def __init__(self, s: Any, err: Exception) -> None:
                            self._s = s
                            self.governance_metadata = GovernedResponse.from_passthrough(None, err).governance_metadata

                        def __iter__(self) -> Iterator[Any]:
                            return iter(self._s)

                    return _PassthroughStream(stream, error)  # type: ignore[return-value]
                openai_response = self._governed._client.chat.completions.create(**kwargs)
                return GovernedResponse.from_passthrough(openai_response, error)
            except Exception:
                # If passthrough also fails, fall back to refusal
                return GovernedResponse.from_pipeline_error(error)

        # failure_policy == "refuse" (default)
        return GovernedResponse.from_pipeline_error(error)


# =============================================================================
# GovernedChat / GovernedClient
# =============================================================================


class GovernedChat:
    """Proxy for client.chat."""

    def __init__(self, governed: GovernedClient) -> None:
        self._governed = governed
        self.completions = GovernedCompletions(governed)


class GovernedClient:
    """
    Transparent proxy over an OpenAI client with MoralStack governance.

    Everything except ``chat.completions.create()`` passes through to the original client.
    """

    def __init__(
        self,
        client: Any,
        orchestrator: Orchestrator,
        config: GovernanceConfig,
    ) -> None:
        self._client = client
        self._orchestrator = orchestrator
        self._config = config
        self._session = SessionState(config)
        self.chat = GovernedChat(self)

    def __getattr__(self, name: str) -> Any:
        """Passthrough for undefined attributes (e.g. client.models, client.files)."""
        return getattr(self._client, name)


# =============================================================================
# govern() — public entry point
# =============================================================================


def govern(
    client: Any,
    config: GovernanceConfig | None = None,
) -> GovernedClient:
    """
    Wrap an OpenAI client with MoralStack governance.

    The original client is used for text generation (NORMAL/SAFE_COMPLETE).
    The deliberative pipeline uses its own LLM client configured via GovernanceConfig
    or environment variables (OPENAI_API_KEY, OPENAI_MODEL).

    Args:
        client: OpenAI client (or duck-typed compatible with .chat.completions.create()).
        config: Optional configuration. If None, defaults come from the environment.

    Returns:
        GovernedClient: governed proxy.

    Raises:
        GovernanceConfigError: OPENAI_API_KEY missing or invalid configuration.
        GovernancePipelineError: error while initializing the pipeline.

    Example::

        from moralstack import govern
        from openai import OpenAI

        client = govern(OpenAI())
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Explain quantum computing."}],
        )
        print(response.content)
        print(response.governance_metadata.final_action)
    """
    # Duck-type check: client must expose the expected interface
    if not (hasattr(client, "chat") or callable(getattr(client, "__getattr__", None))):
        raise GovernancePipelineError("client must expose a .chat.completions.create() interface " "(e.g. openai.OpenAI())")

    load_env()

    if config is None:
        config = GovernanceConfig()

    orchestrator = _bootstrap_pipeline(config)
    return GovernedClient(client, orchestrator, config)
