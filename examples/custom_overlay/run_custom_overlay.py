"""Load a custom overlay without modifying package files.

This script copies core.yaml and all bundled overlays to a temporary constitution
folder, adds my_domain.yaml, and uses GovernanceConfig(constitution_dir=...)
for both forced and automatic detection runs.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from openai import OpenAI

from moralstack import GovernanceConfig, govern
from moralstack.utils.env_loader import load_env


def build_temp_constitution_dir() -> tempfile.TemporaryDirectory[str]:
    repo_root = Path(__file__).parent.parent.parent
    source_constitution_dir = repo_root / "moralstack" / "constitution" / "data"
    custom_overlay = repo_root / "examples" / "custom_overlay" / "my_domain.yaml"

    temp_dir = tempfile.TemporaryDirectory()
    temp_path = Path(temp_dir.name)
    temp_overlays = temp_path / "overlays"
    temp_overlays.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_constitution_dir / "core.yaml", temp_path / "core.yaml")
    for overlay_file in (source_constitution_dir / "overlays").glob("*.yaml"):
        shutil.copy2(overlay_file, temp_overlays / overlay_file.name)
    shutil.copy2(custom_overlay, temp_overlays / "my_domain.yaml")
    return temp_dir


def run_case(config: GovernanceConfig, query: str, label: str) -> None:
    client = govern(OpenAI(), config=config)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": query}],
    )
    meta = response.governance_metadata

    print("\n[{}] {}".format(label, query))
    print("Final action: {}".format(meta.final_action))
    print("Detected overlay: {}".format(meta.domain_overlay or "-"))
    print("Triggered principles: {}".format(", ".join(meta.triggered_principles) if meta.triggered_principles else "-"))


def main() -> None:
    load_env()
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set. Copy examples/.env.example and export the variable.")

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        temp_dir = build_temp_constitution_dir()
        constitution_dir = temp_dir.name

        # Bootstrap check: if constitution_dir is invalid, govern(...) will fail early.
        forced_config = GovernanceConfig(domain_overlay="my_domain", constitution_dir=constitution_dir)
        run_case(
            forced_config,
            "I'm thinking of buying my first apartment. What should I evaluate before making an offer?",
            "FORCED",
        )

        automatic_config = GovernanceConfig(constitution_dir=constitution_dir)
        run_case(
            automatic_config,
            "Before signing a lease, what should I review about deposits, maintenance, and local tenant rules?",
            "AUTOMATIC",
        )

        print("\nCompare forced vs automatic output to verify whether my_domain is selected by detection.")
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    try:
        main()
    except (EnvironmentError, KeyError) as exc:
        print("Configuration error: {}".format(exc))
