"""
Public interface for the MoralStack SDK.

govern(client)          -- wrap an OpenAI client with governance
GovernedClient          -- transparent proxy with governance on chat.completions.create()
GovernedChat            -- proxy for client.chat
GovernedCompletions     -- intercept create() with pre-call deliberation
GovernedStreamResponse  -- wrap a stream with governance metadata
GovernedSyntheticStream -- replay a validated final text as a synthetic stream
"""

from __future__ import annotations

import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Iterator

from moralstack.core.types import Turn, UserContext
from moralstack.models.base import GenerationOverrides
from moralstack.observability.phase0_timing import emit_phase0_timing, phase0_timing_enabled
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.conversation_context import build_conversation_context, context_to_turns
from moralstack.orchestration.delivery import finalize_delivery
from moralstack.orchestration.types import ProcessedRequest
from moralstack.sdk.bootstrap import _bootstrap_pipeline, _resolve_model
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
    return build_conversation_context(messages).final_user_message


def _extract_developer_contract(
    messages: list[dict[str, Any]],
) -> DeveloperContract | None:
    """
    Extract the developer-declared application contract from a list of OpenAI messages.

    Semantics:
    - Scan all messages with role='system' or role='developer' in order of appearance.
    - The LAST system/developer message wins (most recent override semantics).
    - Multimodal content (list of {type, text/image_url}) is reduced to text parts only;
      non-text parts are ignored.
    - If no system message is present, or the extracted text is empty/whitespace-only,
      return None. This preserves byte-equivalent behavior for single-turn flows that
      do not declare a contract.

    Args:
        messages: list of OpenAI-format message dicts.

    Returns:
        DeveloperContract built via DeveloperContract.from_text(text, mode='opaque'),
        or None when no substantive system message is present.

    Note:
        Step 2 always uses mode='opaque'. Support for 'structured' mode (LLM-driven
        scope/role/restrictions extraction) is deferred to a later step and gated by
        an explicit GovernanceConfig flag not present in Step 2.
    """
    return build_conversation_context(messages).developer_contract


def _messages_to_turns(messages: list[dict[str, Any]]) -> list[Turn]:
    """
    Convert a list of OpenAI messages to Turn objects for the pipeline.
    Excludes messages with role='system' (not conversational turns).
    """
    # Preserve the legacy helper contract: convert the supplied messages as history,
    # without treating their last user turn as the current request.
    return context_to_turns(build_conversation_context(messages + [{"role": "user", "content": ""}]))


def _build_safe_complete_user_turn(result: Any) -> dict[str, str]:
    """
    Build a synthetic user turn carrying the SAFE_COMPLETE governance guidance.

    Per design v1.3 section 3.7, the SAFE_COMPLETE guidance is injected as an additional
    user message at the end of the messages list, NOT as a modification of the
    user's system prompt. This preserves the developer-declared system prompt
    byte-identical (transparency invariant section 1.3).

    Returns:
        A dict {"role": "user", "content": "..."} ready to be appended to messages.
    """
    governance_content = result.response.content
    if governance_content:
        guidance_body = governance_content
    else:
        meta = result.response.metadata
        parts: list[str] = []
        if meta.triggered_principles:
            parts.append(f"Relevant principles: {', '.join(meta.triggered_principles)}.")
        if meta.reason_codes:
            parts.append(f"Reason codes: {', '.join(meta.reason_codes)}.")
        if meta.decision_reason:
            parts.append(meta.decision_reason)
        guidance_body = "\n".join(parts) if parts else "Respond with appropriate care and caveats."

    content = (
        "Please respond to my last message above taking into account the following "
        "governance guidance:\n\n"
        f"{guidance_body}"
    )
    return {"role": "user", "content": content}


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
    """Synthetic chunk for governance-produced streams (refusal or replayed text)."""

    def __init__(
        self,
        content: str,
        *,
        model: str = "moralstack-refuse",
        chunk_id: str | None = None,
        finish_reason: str | None = "stop",
    ) -> None:
        self.choices = [_SyntheticStreamChoice(content, finish_reason=finish_reason)]
        self.model = model
        self.id = chunk_id or f"refuse-{uuid.uuid4().hex[:8]}"


class _SyntheticStreamChoice:
    def __init__(self, content: str, *, finish_reason: str | None = "stop") -> None:
        self.delta = _SyntheticDelta(content)
        self.finish_reason = finish_reason
        self.index = 0


class _SyntheticDelta:
    def __init__(self, content: str) -> None:
        self.content = content
        self.role = "assistant"


def _iter_word_chunks(text: str) -> list[str]:
    """
    Split ``text`` into word/whitespace pieces whose concatenation reproduces it
    byte-for-byte. Used to replay an already-validated answer as a token stream.
    """
    if not text:
        return []
    return re.findall(r"\S+|\s+", text)


class GovernedSyntheticStream:
    """
    Synthetic stream that replays an already-generated, contract-validated final
    text chunk by chunk.

    Used when the caller requested ``stream=True`` but a developer contract is
    present: the final text is generated and revalidated **non-streamed** (so the
    contract can be enforced on the complete output), then replayed here as a
    token stream. No unvalidated upstream token is ever forwarded to the caller —
    streaming is a transport contract over the final answer, not over the
    intermediate generations.
    """

    def __init__(self, text: str, result: Any) -> None:
        self._text = text or ""
        self._result = result
        self.governance_metadata = GovernedResponse.from_normal(None, result).governance_metadata

    def __iter__(self) -> Iterator[Any]:
        chunks = _iter_word_chunks(self._text)
        stream_id = f"governed-{uuid.uuid4().hex[:8]}"
        if not chunks:
            yield _SyntheticStreamChunk("", model="moralstack-governed", chunk_id=stream_id, finish_reason="stop")
            return
        last = len(chunks) - 1
        for i, piece in enumerate(chunks):
            yield _SyntheticStreamChunk(
                piece,
                model="moralstack-governed",
                chunk_id=stream_id,
                finish_reason="stop" if i == last else None,
            )

    def __enter__(self) -> GovernedSyntheticStream:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class GovernedErrorStream:
    """
    Synthetic stream returned when the pipeline fails on a streaming request.

    Fail-closed: replays the deterministic governed refusal as a single chunk and
    never forwards any wrapped/upstream tokens (Plan 1 invariant).
    """

    def __init__(self, response: GovernedResponse) -> None:
        self._text = response.content
        self.governance_metadata = response.governance_metadata

    def __iter__(self) -> Iterator[Any]:
        yield _SyntheticStreamChunk(self._text, model="moralstack-error")

    def __enter__(self) -> GovernedErrorStream:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


# =============================================================================
# GovernedCompletions
# =============================================================================


class GovernedCompletions:
    """Intercept chat.completions.create() with pre-call deliberation."""

    def __init__(self, governed: GovernedClient) -> None:
        self._governed = governed

    def create(
        self, **kwargs: Any
    ) -> GovernedResponse | GovernedRefusalStream | GovernedSyntheticStream | GovernedErrorStream:
        """
        Deliberate on the prompt, then deliver the governed pipeline text:
        - REFUSE: return GovernedResponse / GovernedRefusalStream (governed refusal text).
        - SAFE_COMPLETE / NORMAL_COMPLETE: return the governed pipeline text via
          from_governed_text, or replay it as a GovernedSyntheticStream when streaming.

        The wrapped/upstream client is never called to generate the delivered answer
        (Plan 1 invariant).

        Observability events emitted during deliberation are flushed synchronously
        before returning so that JSONL/SQLite writes are guaranteed even in short-lived
        scripts (the write queue uses a daemon thread that would otherwise be lost on
        process exit).
        """
        phase0_started = time.perf_counter() if phase0_timing_enabled() else None
        try:
            return self._create_inner(**kwargs)
        finally:
            flush_error: str | None = None
            try:
                from moralstack.observability.service import get_obs

                get_obs().flush()
            except Exception as exc:
                flush_error = type(exc).__name__
                pass
            if phase0_started is not None:
                emit_phase0_timing(
                    "sdk.governed_completions.create",
                    (time.perf_counter() - phase0_started) * 1000,
                    model=str(kwargs.get("model") or ""),
                    stream=bool(kwargs.get("stream", False)),
                    flush_error=flush_error,
                )

    def _create_inner(
        self, **kwargs: Any
    ) -> GovernedResponse | GovernedRefusalStream | GovernedSyntheticStream | GovernedErrorStream:
        """Core deliberation + routing logic, separated from flush concern."""
        is_stream = kwargs.get("stream", False)
        messages = kwargs.get("messages", [])

        conversation_context = build_conversation_context(messages)
        user_message = conversation_context.final_user_message
        conversation_history = context_to_turns(conversation_context)
        developer_contract = conversation_context.developer_contract

        domain = self._governed._config.domain_overlay
        request = ProcessedRequest(
            prompt=user_message,
            conversation_history=conversation_history,
            user_context=UserContext(domain_overlay=domain),
            developer_contract=developer_contract,
            conversation_context=conversation_context,
            generation_overrides=GenerationOverrides.from_mapping(kwargs),
        )

        session = self._governed._session
        conv_id = session.conversation_id
        turn_idx = session.next_turn_index()
        conv_state = session.current_state
        # Snapshot of the incoming ConversationGovernanceState BEFORE the
        # controller runs and before ``session.update_from_result`` overwrites
        # the store entry. Required for the canonical ``proxy.request_finalized``
        # envelope (state_in / posture_in fields). At turn 0 of a fresh session
        # this is ``None`` — the proxy and the SDK behave identically here.
        state_in_snapshot = conv_state

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

        # Governed delivery (Plan 1 invariant): the delivered text is ALWAYS the
        # text produced inside the MoralStack governed pipeline (validated
        # speculative draft, compliance regeneration, policy generate/rewrite, or
        # a governed refusal). The wrapped/upstream client is never called to
        # generate the delivered answer. Streaming is a transport contract over
        # that final answer: it replays the governed text as a synthetic token
        # stream and never forwards live upstream tokens.
        delivery = finalize_delivery(result, config=self._governed._config)
        requested_model = str(kwargs.get("model") or "")
        generation_model, rewrite_model = self._resolve_audit_models()

        self._finalize_audit(
            request_id=request.request_id,
            result=result,
            final_response_text=delivery.text,
            conversation_id=conv_id,
            turn_index=turn_idx,
            domain=domain,
            state_in=state_in_snapshot,
            final_action=delivery.final_action,
        )

        if final_action == "REFUSE":
            if is_stream:
                return GovernedRefusalStream(result)
            return GovernedResponse.from_governed_text(
                result,
                delivery,
                requested_model=requested_model,
                generation_model=generation_model,
                rewrite_model=rewrite_model,
            )

        # NORMAL_COMPLETE / SAFE_COMPLETE (and blank-content fail-closed
        # downgrades produced by finalize_delivery).
        if is_stream:
            return GovernedSyntheticStream(delivery.text, result)
        return GovernedResponse.from_governed_text(
            result,
            delivery,
            requested_model=requested_model,
            generation_model=generation_model,
            rewrite_model=rewrite_model,
        )

    def _resolve_audit_models(self) -> tuple[str, str]:
        """
        Resolve the actual MoralStack policy models used for governed text.

        - generation model: ``GovernanceConfig.model`` -> ``OPENAI_MODEL`` -> ``gpt-4o``;
        - rewrite model: ``MORALSTACK_POLICY_REWRITE_MODEL`` when set, else the
          generation model.

        These are authoritative for audit. The chat request ``model=`` argument is
        a requested alias only and does not select either model.
        """
        import os

        generation_model = _resolve_model(self._governed._config)
        rewrite_model = (os.getenv("MORALSTACK_POLICY_REWRITE_MODEL") or "").strip() or generation_model
        return generation_model, rewrite_model

    def _finalize_audit(
        self,
        *,
        request_id: str,
        result: Any,
        final_response_text: str,
        conversation_id: str | None,
        turn_index: int | None,
        domain: str | None,
        state_in: Any | None = None,
        final_action: str | None = None,
    ) -> None:
        """
        Populate Step 13 governance audit fields on the ``requests`` row AND
        emit the canonical ``proxy.request_finalized`` envelope so the
        ``proxy_request_events`` table (and the matching JSONL stream) carry a
        per-turn summary for SDK-driven runs as well.

        Best-effort: identical contract to the proxy's ``_finalize_request``,
        so SDK-driven runs share the same audit surface (``final_response`` +
        ``meta_json`` + ``proxy_request_events`` row) consumed by
        ``moralstack-ui`` and the Markdown export. Any failure is swallowed —
        observability never breaks the SDK contract.

        The event name remains ``proxy.request_finalized`` for backwards
        compatibility with the Step 13 schema (the table, the JSONL file, and
        the read-store helpers all use that name); semantically it is
        transport-agnostic and now also covers the SDK code path.

        Args:
            state_in: snapshot of ``session.current_state`` captured BEFORE
                ``session.update_from_result(result)`` overwrote it. ``None``
                at the very first turn of a fresh GovernedClient session.
        """
        run_id = getattr(self._governed, "_run_id", "") or ""
        if not run_id or not request_id:
            return
        try:
            from moralstack.observability.conversation_events import finalize_audit_sync
            from moralstack.observability.governance_audit import (
                finalize_governance_audit,
                posture_of,
                state_summary_or_none,
            )

            # 1) Update requests.final_response, requests.domain, and merge
            #    requests.meta_json; return the consolidated meta dict.
            meta = finalize_governance_audit(
                run_id=run_id,
                request_id=request_id,
                result=result,
                final_response_text=final_response_text,
                conversation_id=conversation_id,
                turn_index=turn_index,
                domain=domain,
                final_action_override=final_action,
                emit_meta=False,
            )

            # 2) Emit the canonical proxy.request_finalized envelope. Mirrors
            #    the proxy's _finalize_request behaviour (server/proxy.py:626)
            #    but with SDK-appropriate values for ``headers`` (None: the
            #    SDK does not produce X-MoralStack-* headers).
            state_out = getattr(result, "conversation_governance_state_out", None)
            state_provided = state_in is not None
            state_updated = bool(getattr(result, "conversation_state_updated", False))
            try:
                response_len: int | None = len(final_response_text or "")
            except Exception:
                response_len = None

            try:
                summary = {
                    "conversation_id": conversation_id,
                    "turn_index": turn_index,
                    "final_action": (meta.get("final_action") if isinstance(meta, dict) else None),
                    "risk_score": (meta.get("risk_score") if isinstance(meta, dict) else None),
                    "path": ((meta.get("path_taken") or meta.get("path")) if isinstance(meta, dict) else None),
                    "domain": domain,
                    "posture_in": posture_of(state_in),
                    "posture_out": posture_of(state_out),
                    "state_provided": state_provided,
                    "state_updated": state_updated,
                    "was_cached": (meta.get("was_cached") if isinstance(meta, dict) else None),
                    "cached_from_turn": (meta.get("cached_from_turn") if isinstance(meta, dict) else None),
                    "final_response_length": response_len,
                    # The SDK does not produce X-MoralStack-* response headers
                    # (those belong to the HTTP proxy). Use None so the JSONL
                    # records ``headers: null`` and SQLite stores headers_json
                    # as NULL — unambiguous semantics for "no headers".
                    "headers": None,
                    "metadata": meta if meta else None,
                    "state_in": state_summary_or_none(state_in),
                    "state_out": state_summary_or_none(state_out),
                }
                final_action_value = summary.get("final_action")
                finalize_audit_sync(
                    run_id=run_id,
                    request_id=request_id,
                    final_action=final_action_value if isinstance(final_action_value, str) else None,
                    final_response=final_response_text,
                    domain=domain,
                    proxy_summary=summary,
                )
            except Exception:
                # Inner try keeps the meta merge from being undone if the emit
                # path itself raises (e.g. JSON-safety failure on an unusual
                # object). Outer try below covers import failures.
                pass
        except Exception:
            # Observability is a side-effect: never break the SDK contract.
            pass

    def _handle_pipeline_failure(
        self,
        error: Exception,
        kwargs: dict[str, Any],
        is_stream: bool,
    ) -> GovernedResponse | GovernedErrorStream:
        """
        Fail closed on pipeline error.

        Plan 1 invariant: the wrapped/upstream client is never called to deliver
        an answer, including after a pipeline failure. ``failure_policy`` no longer
        routes to passthrough delivery (it is mapped to ``refuse`` at config
        construction). A deterministic governed refusal is returned; for streaming
        requests it is replayed as a synthetic stream.
        """
        del kwargs
        refusal = GovernedResponse.from_pipeline_error(error)
        if is_stream:
            return GovernedErrorStream(refusal)
        return refusal


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
        self._run_id: str = self._init_run_context()
        self.chat = GovernedChat(self)

    @staticmethod
    def _init_run_context() -> str:
        """
        Generate a session-scoped run_id and register it in the observability context.

        For db_only/dual modes also ensures the DB schema exists and inserts the run
        row so FK constraints on subsequent request/event inserts are satisfied.
        Does not raise: observability failures are best-effort.
        """
        from moralstack.observability.config import get_db_path, get_observability_mode
        from moralstack.observability.context import set_current_run_id
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db

        run_id = str(uuid.uuid4())
        set_current_run_id(run_id)

        mode = get_observability_mode()
        if mode in ("db_only", "dual"):
            db_path = get_db_path()
            if db_path:
                try:
                    init_db(db_path)
                    create_run(run_id, "sdk_session", {})
                except Exception:
                    pass

        return run_id

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

    The delivered answer is ALWAYS the text produced by MoralStack's governed
    pipeline; the wrapped ``client`` is never called to generate the delivered
    answer (Plan 1 invariant). The governed answer model is configured via
    ``GovernanceConfig.model`` / ``OPENAI_MODEL`` (first-pass generation) and
    ``MORALSTACK_POLICY_REWRITE_MODEL`` (governed revisions). The ``model=``
    argument passed to ``chat.completions.create(...)`` is a requested alias only
    and does not select the governed answer model. Non-chat attributes still
    pass through to the wrapped client.

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
