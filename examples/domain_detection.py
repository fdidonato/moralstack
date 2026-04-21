"""No overlay is specified; MoralStack's domain detector chooses the most relevant
one based on query content.
"""

from __future__ import annotations

import os

from openai import OpenAI

from moralstack import govern
from moralstack.utils.env_loader import load_env

QUERIES = [
    "Can a landlord increase rent mid-contract in Italy?",
    "What are the warning signs of dehydration in older adults?",
    "How can I optimize this Python function for readability and speed?",
    "Write a short fantasy opening with a mysterious city in the clouds.",
    "What are the first steps to secure a public API against credential stuffing?",
]


def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set. Copy examples/.env.example and export the variable.")

    client = govern(OpenAI())
    rows = []
    print("{:<3} {:<58} {:<18} {:<16} {:<16}".format("#", "Query", "Detected overlay", "Final action", "Risk category"))
    print("{:<3} {:<58} {:<18} {:<16} {:<16}".format("-" * 3, "-" * 58, "-" * 18, "-" * 16, "-" * 16))

    for idx, query in enumerate(QUERIES, start=1):
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": query}],
        )
        meta = response.governance_metadata
        short_query = (query[:55] + "...") if len(query) > 58 else query
        rows.append((idx, short_query, meta.domain_overlay or "-", meta.final_action, meta.risk_category))

    for row in rows:
        print("{:<3} {:<58} {:<18} {:<16} {:<16}".format(*row))


if __name__ == "__main__":
    try:
        main()
    except (EnvironmentError, KeyError) as exc:
        print("Configuration error: {}".format(exc))
