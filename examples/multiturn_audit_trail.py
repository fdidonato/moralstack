"""
MoralStack v0.4 — Multi-turn audit trail export.

Demonstrates how to export a conversation's full audit trail as markdown,
for AI Act art. 12 compliance and downstream review.

Run with: OPENAI_API_KEY=sk-... python examples/multiturn_audit_trail.py
"""

from __future__ import annotations

from openai import OpenAI

from moralstack import govern
from moralstack.reports.conversation_export import export_conversation_to_markdown


def main() -> None:
    client = govern(OpenAI())
    messages: list[dict[str, str]] = []

    # Run a small conversation.
    for user_msg in [
        "What's the best way to manage a household budget?",
        "What about investments for beginners?",
        "Can you compare index funds and individual stocks?",
    ]:
        messages.append({"role": "user", "content": user_msg})
        response = client.chat.completions.create(model="gpt-4o", messages=messages)
        messages.append({"role": "assistant", "content": response.choices[0].message.content})

    conversation_id = response.governance_metadata.conversation_id
    print(f"Conversation ID: {conversation_id}\n")

    # Export the audit trail.
    markdown = export_conversation_to_markdown(conversation_id or "")
    print("=" * 60)
    print("AUDIT TRAIL EXPORT")
    print("=" * 60)
    print(markdown)


if __name__ == "__main__":
    main()
