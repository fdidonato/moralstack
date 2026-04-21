"""Force a specific overlay via GovernanceConfig(domain_overlay=...).

Important: overlays are always loaded at startup. This example demonstrates forcing
one overlay explicitly, bypassing automatic domain detection.
"""

from __future__ import annotations

import os

from openai import OpenAI

from moralstack import GovernanceConfig, govern
from moralstack.utils.env_loader import load_env


def run_query(client: object, prompt: str) -> None:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
    )
    meta = response.governance_metadata
    print("\nQuery: {}".format(prompt))
    print("Final action: {}".format(meta.final_action))
    print("Domain overlay: {}".format(meta.domain_overlay or "-"))
    print("Risk score: {:.2f}".format(meta.risk_score))
    print("Triggered principles: {}".format(", ".join(meta.triggered_principles) if meta.triggered_principles else "-"))


# Even for the off-topic query, the healthcare overlay is forced, which may
# influence decision bounds. Compare with domain_detection.py where the detector
# picks the overlay automatically.
def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set. Copy examples/.env.example and export the variable.")

    client = govern(OpenAI(), config=GovernanceConfig(domain_overlay="healthcare"))
    run_query(client, "What are the common symptoms of vitamin D deficiency?")
    run_query(client, "What is the weather like in Rome this weekend?")


if __name__ == "__main__":
    try:
        main()
    except (EnvironmentError, KeyError) as exc:
        print("Configuration error: {}".format(exc))
