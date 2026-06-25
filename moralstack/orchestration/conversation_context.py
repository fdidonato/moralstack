"""Shared OpenAI-message conversation context for SDK and proxy entry points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from moralstack.core.types import Turn
from moralstack.orchestration.contract import DeveloperContract

ContextMode = Literal["none", "system_last_user_only", "role_serialized_full", "role_serialized_truncated", "full_native"]


def _content_to_text(content: Any) -> str:
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return " ".join(str(p) for p in parts)
    return str(content or "")


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class DeliveryContextGuardResult:
    delivery_context_broader_than_governance: bool
    mismatch_guard_action: str = "none"
    governance_context_mode: str = "none"
    candidate_context_mode: str = "none"
    prior_turn_count: int = 0

    def to_metadata(self) -> dict[str, Any]:
        return {
            "delivery_context_broader_than_governance": self.delivery_context_broader_than_governance,
            "mismatch_guard_action": self.mismatch_guard_action,
            "governance_context_mode": self.governance_context_mode,
            "candidate_context_mode": self.candidate_context_mode,
            "prior_turn_count": self.prior_turn_count,
        }


@dataclass(frozen=True)
class ConversationContext:
    """Additive transcript view; absent/empty contexts keep legacy single-turn behavior."""

    messages: tuple[ChatMessage, ...] = field(default_factory=tuple)
    final_user_message: str = ""
    developer_contract: DeveloperContract | None = None
    history_source: str = "none"
    contains_full_native_messages: bool = False

    @property
    def prior_messages(self) -> tuple[ChatMessage, ...]:
        final_idx = self._final_user_index()
        if final_idx is None:
            return tuple(m for m in self.messages if m.role in {"user", "assistant"})
        return tuple(m for i, m in enumerate(self.messages[:final_idx]) if m.role in {"user", "assistant"})

    @property
    def prior_user_messages(self) -> tuple[str, ...]:
        return tuple(m.content for m in self.prior_messages if m.role == "user")

    @property
    def prior_assistant_messages(self) -> tuple[str, ...]:
        return tuple(m.content for m in self.prior_messages if m.role == "assistant")

    @property
    def prior_turn_count(self) -> int:
        return len(self.prior_user_messages) + len(self.prior_assistant_messages)

    @property
    def system_message_count(self) -> int:
        return sum(1 for m in self.messages if m.role == "system")

    @property
    def developer_message_count(self) -> int:
        return sum(1 for m in self.messages if m.role == "developer")

    def _final_user_index(self) -> int | None:
        for idx in range(len(self.messages) - 1, -1, -1):
            if self.messages[idx].role == "user":
                return idx
        return None

    @property
    def system_messages(self) -> tuple[ChatMessage, ...]:
        return tuple(m for m in self.messages if m.role == "system")

    @property
    def developer_messages(self) -> tuple[ChatMessage, ...]:
        return tuple(m for m in self.messages if m.role == "developer")

    def native_context_messages(self, *, include_final_user: bool = True) -> list[dict[str, str]]:
        """Return original system/developer/user/assistant messages in native role order."""
        final_idx = self._final_user_index()
        out: list[dict[str, str]] = []
        for idx, msg in enumerate(self.messages):
            if msg.role not in {"system", "developer", "user", "assistant"}:
                continue
            if not include_final_user and final_idx is not None and idx == final_idx:
                continue
            out.append({"role": msg.role, "content": msg.content})
        if include_final_user and not any(m["role"] == "user" for m in out) and self.final_user_message:
            out.append({"role": "user", "content": self.final_user_message})
        return out

    def observability_message_sections(self) -> dict[str, Any]:
        return {
            "system_messages": [m.content for m in self.system_messages],
            "developer_messages": [m.content for m in self.developer_messages],
            "history_messages": [
                {"role": m.role, "content": m.content} for m in self.prior_messages if m.role in {"user", "assistant"}
            ],
            "final_user_message": self.final_user_message,
        }

    def role_serialized_transcript(self, budget: int = 6000) -> tuple[str, bool]:
        """Return a role-ordered transcript including the final user turn."""
        conversational: list[ChatMessage] = []
        final_idx = self._final_user_index()
        for idx, msg in enumerate(self.messages):
            if msg.role not in {"user", "assistant"}:
                continue
            if final_idx is None or idx <= final_idx:
                conversational.append(msg)
        if not conversational and self.final_user_message:
            conversational.append(ChatMessage("user", self.final_user_message))

        lines = [f"{msg.role.upper()}: {msg.content}" for msg in conversational]
        if not lines:
            return "", False
        out: list[str] = []
        used = 0
        truncated = False
        for line in reversed(lines):
            line_len = len(line) + (1 if out else 0)
            if out and used + line_len > budget:
                truncated = True
                break
            if not out and line_len > budget:
                out.append(line[:budget])
                truncated = True
                break
            out.append(line)
            used += line_len
        out.reverse()
        return "\n".join(out), truncated

    def context_shape_metadata(
        self,
        *,
        module: str,
        context_mode: str,
        prior_used: int | None = None,
        history_truncation: str = "none",
        history_truncated_count: int = 0,
    ) -> dict[str, Any]:
        used = self.prior_turn_count if prior_used is None else prior_used
        return {
            "module": module,
            "context_mode": context_mode,
            "raw_message_count": len(self.messages),
            "system_message_count": self.system_message_count,
            "developer_message_count": self.developer_message_count,
            "prior_user_available": len(self.prior_user_messages),
            "prior_assistant_available": len(self.prior_assistant_messages),
            "prior_turn_count": self.prior_turn_count,
            "prior_turns_used": used,
            "history_truncation": history_truncation,
            "history_truncated_count": history_truncated_count,
            "contains_full_native_messages": self.contains_full_native_messages,
            "developer_contract_included": self.developer_contract is not None,
            "final_user_included": bool(self.final_user_message),
            "history_source": self.history_source,
        }


def build_conversation_context(messages: list[dict[str, Any]] | None) -> ConversationContext:
    raw = messages or []
    chat_messages = tuple(
        ChatMessage(role=str(msg.get("role", "")), content=_content_to_text(msg.get("content", "")))
        for msg in raw
        if isinstance(msg, dict)
    )
    final_user = ""
    for msg in reversed(chat_messages):
        if msg.role == "user":
            final_user = msg.content
            break

    last_system_text = ""
    for msg in chat_messages:
        if msg.role in {"system", "developer"} and msg.content.strip():
            last_system_text = msg.content
    contract = DeveloperContract.from_text(last_system_text, mode="opaque") if last_system_text.strip() else None
    return ConversationContext(
        messages=chat_messages,
        final_user_message=final_user,
        developer_contract=contract,
        history_source="request_body" if raw else "none",
        contains_full_native_messages=bool(raw),
    )


def context_to_turns(ctx: ConversationContext) -> list[Turn]:
    turns: list[Turn] = []
    for m in ctx.prior_messages:
        if m.role == "user":
            turns.append(Turn(role="user", content=m.content))
        elif m.role == "assistant":
            turns.append(Turn(role="assistant", content=m.content))
    return turns


def evaluate_delivery_context_guard(
    ctx: ConversationContext | None,
    *,
    governance_context_mode: str,
    candidate_context_mode: str,
    is_draft_reused_as_final: bool,
) -> DeliveryContextGuardResult:
    prior_turn_count = ctx.prior_turn_count if ctx is not None else 0
    broader = (
        prior_turn_count > 0
        and is_draft_reused_as_final
        and candidate_context_mode == "system_last_user_only"
        and governance_context_mode in {"role_serialized_full", "full_native"}
    )
    return DeliveryContextGuardResult(
        delivery_context_broader_than_governance=broader,
        governance_context_mode=governance_context_mode,
        candidate_context_mode=candidate_context_mode,
        prior_turn_count=prior_turn_count,
    )
