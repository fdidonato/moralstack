"""Helpers for native chat-message context in deliberative modules."""

from __future__ import annotations

from typing import Any

from moralstack.core.types import Turn
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.catalog import PromptSet, default_prompt_set
from moralstack.utils.history_window import recent_turns, truncate_turn_content

# Default re-export of the packaged ``context.module_preface`` fragment (identical bytes).
_CONTEXT_REFERENCE_INSTRUCTION = default_prompt_set().text("context.module_preface")


def build_module_messages(
    *,
    system_prompt: str,
    user_prompt: str,
    developer_contract: DeveloperContract | None = None,
    conversation_history: list[Turn] | None = None,
    retry_prompt: str = "",
    prompt_set: PromptSet | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if developer_contract is not None and developer_contract.raw_text:
        messages.append({"role": "developer", "content": developer_contract.raw_text})
    for turn in recent_turns(conversation_history):
        role = getattr(turn, "role", "") or ""
        if role not in {"user", "assistant"}:
            continue
        messages.append({"role": role, "content": truncate_turn_content(getattr(turn, "content", ""))})
    content = user_prompt if not retry_prompt else f"{user_prompt}\n\n{retry_prompt}"
    if developer_contract is not None or conversation_history:
        content = (prompt_set or default_prompt_set()).text("context.module_preface") + content
    messages.append({"role": "user", "content": content})
    return messages


def message_sections(
    *,
    developer_contract: DeveloperContract | None = None,
    conversation_history: list[Turn] | None = None,
) -> dict[str, Any]:
    return {
        "system_messages": [],
        "developer_messages": (
            [developer_contract.raw_text] if developer_contract is not None and developer_contract.raw_text else []
        ),
        # Content is stored in full on purpose: this is the audit record, and the
        # only source from which a faithful replay can rebuild what the module saw.
        "history_messages": [
            {"role": getattr(turn, "role", "") or "unknown", "content": getattr(turn, "content", "") or ""}
            for turn in recent_turns(conversation_history)
        ],
        "final_user_message": "",
    }
