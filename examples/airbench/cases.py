"""Loader for the regression cases."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CASES_PATH = Path(__file__).with_name("cases.jsonl")


@dataclass(frozen=True)
class Case:
    """One replayed prompt plus what governance is expected to do with it.

    `expect` is one of:

    * `answer` - the request is benign and must be served;
    * `refuse` - the request must be declined outright;
    * `withhold` - the dangerous *content* must not be delivered, whatever the
      label. This is the right test for a governance engine: SAFE_COMPLETE with a
      regenerated answer that denies the harmful part is a success, not a failure,
      and a binary refuse/answer check scores it wrong. `forbidden_markers` lists
      the strings whose presence proves the content leaked.

    `scope` says what a failure means:

    * `in-scope` - the outcome depends on a principle changed on 2026-08-13, so a
      failure means the constitution change did not achieve what it intended;
    * `control` - the request must stay refused; a failure means the change made
      the engine permissive, which is the serious direction;
    * `out-of-scope` - a known false positive whose cause lies elsewhere (risk
      estimation, a principle nobody touched). Recorded so the suite tracks it,
      but a failure here is not evidence about the 2026-08-13 change.
    """

    case_id: str
    origin: str
    prompt: str
    expect: str
    principles: list[str] = field(default_factory=list)
    rationale: str = ""
    scope: str = "in-scope"
    forbidden_markers: list[str] = field(default_factory=list)

    @property
    def is_control(self) -> bool:
        return self.scope == "control"

    def leaked(self, text: str) -> list[str]:
        """Forbidden markers present in the delivered text (case-insensitive)."""
        low = (text or "").lower()
        return [m for m in self.forbidden_markers if m.lower() in low]


def load_cases(path: Path | None = None) -> list[Case]:
    """Read `cases.jsonl`, preserving file order."""
    src = path or CASES_PATH
    cases: list[Case] = []
    with src.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(
                Case(
                    case_id=raw["case_id"],
                    origin=raw["origin"],
                    prompt=raw["prompt"],
                    expect=raw["expect"],
                    principles=list(raw.get("principles") or []),
                    rationale=raw.get("rationale", ""),
                    scope=raw.get("scope", "in-scope"),
                    forbidden_markers=list(raw.get("forbidden_markers") or []),
                )
            )
    if not cases:
        raise ValueError(f"no cases found in {src}")
    return cases
