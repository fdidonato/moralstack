"""
MoralStack v0.4 — Multi-turn governance quickstart.

Demonstrates a 3-turn conversation governed by MoralStack. The same
GovernedClient handles all turns; the conversation_id is auto-managed.

Run with: OPENAI_API_KEY=sk-... python examples/multiturn_quickstart.py
"""

from __future__ import annotations

from openai import OpenAI

from moralstack import govern


def main() -> None:
    client = govern(OpenAI())

    messages: list[dict[str, str]] = []

    # Turn 1
    messages.append({"role": "user", "content": "What's the capital of France?"})
    response = client.chat.completions.create(model="gpt-4o", messages=messages)
    assistant_reply = response.choices[0].message.content
    print(f"[Turn 1] Decision: {response.governance_metadata.final_action}")
    print(f"[Turn 1] Reply: {assistant_reply}\n")
    messages.append({"role": "assistant", "content": assistant_reply})

    # Turn 2
    messages.append({"role": "user", "content": "What's its population?"})
    response = client.chat.completions.create(model="gpt-4o", messages=messages)
    assistant_reply = response.choices[0].message.content
    print(f"[Turn 2] Decision: {response.governance_metadata.final_action}")
    print(f"[Turn 2] Reply: {assistant_reply}\n")
    messages.append({"role": "assistant", "content": assistant_reply})

    # Turn 3
    messages.append({"role": "user", "content": "What major museums are there?"})
    response = client.chat.completions.create(model="gpt-4o", messages=messages)
    assistant_reply = response.choices[0].message.content
    print(f"[Turn 3] Decision: {response.governance_metadata.final_action}")
    print(f"[Turn 3] Reply: {assistant_reply}\n")

    print("Conversation ID:", response.governance_metadata.conversation_id)


if __name__ == "__main__":
    main()
