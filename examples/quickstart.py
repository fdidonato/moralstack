"""Minimal example: wrap an OpenAI client with MoralStack governance and send one query.
Runs in ~70s for a deliberative query. Requires OPENAI_API_KEY.
"""

from __future__ import annotations

import os

from openai import OpenAI

from moralstack import govern
from moralstack.utils.env_loader import load_env


def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set. Copy examples/.env.example and export the variable.")

    client = govern(OpenAI())
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "What are the main causes of the French Revolution?"}],
    )

    meta = response.governance_metadata
    print("\n=== Response ===")
    print(response.content)
    print("\n=== Governance metadata ===")
    print("Final action: {}".format(meta.final_action))
    print("Risk category: {}".format(meta.risk_category))
    print("Path: {}".format(meta.path))
    print("Overlay: {}".format(meta.domain_overlay or "-"))


if __name__ == "__main__":
    try:
        main()
    except (EnvironmentError, KeyError) as exc:
        print("Configuration error: {}".format(exc))
