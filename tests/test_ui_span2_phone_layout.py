"""UI test for iteration 15 remediation: `.meta-item.span-2` must not defeat the
phone single-column meta-grid collapse — and the override must WIN the cascade.

`.meta-item.span-2` declares `grid-column: span 2` unconditionally. Inside the
phone breakpoint's single-column `.meta-grid` (`@media (max-width: 480px)`), the
browser satisfies that span by auto-generating an implicit second, content-sized
track. That silently reverts the WHOLE grid to two columns, and under
`.card { overflow: hidden }` the right column is clipped rather than wrapped.
Measured in the browser at 390x844: every right-column item of the delivery
card overflowed the clipped card by 13-21px, truncating the "Causal reason"
sentence and the "Winning rule" value mid-word.

Two failure modes, both observed for real in this iteration, hence two guards:

1. the override is absent -> the implicit second track is created;
2. **the override is present but placed BEFORE the unconditional rule** -> a
   media query adds NO specificity, so at 390px both rules apply at 0-2-0 and
   the LATER one wins by source order. The first remediation attempt put the
   override at line ~1024 and the base rule at ~1598, so the override was dead
   at every viewport width while a presence-only test reported green. The
   verifier caught it in the browser; this test now catches it here.

CSS layout cannot be exercised by the Jinja/TestClient render path (no CSS
engine), so this asserts what ships in the served stylesheet. Presence alone is
not enough — cascade order is the actual load-bearing property, so it is what
gets asserted. The rendered result is verified in the browser by the UI-loop
verifier.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

# The unconditional base rule.
_BASE_RE = re.compile(r"\.meta-item\.span-2\s*\{[^}]*grid-column:\s*span\s+2\s*;[^}]*\}")
# A <=480px media block whose body makes .meta-item.span-2 span the full row.
_PHONE_OVERRIDE_RE = re.compile(
    r"@media\s*\(max-width:\s*(?P<width>\d+)px\s*\)\s*\{"
    r"(?P<body>(?:[^{}]|\{[^{}]*\})*?\.meta-item\.span-2\s*\{[^}]*grid-column:\s*1\s*/\s*-1\s*;[^}]*\}"
    r"(?:[^{}]|\{[^{}]*\})*?)\}",
    re.DOTALL,
)


def _served_css() -> str:
    from moralstack.ui.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    resp = client.get("/static/css/main.css")
    assert resp.status_code == 200, resp.status_code
    return resp.text


def _phone_override_match(css: str) -> re.Match[str] | None:
    for m in _PHONE_OVERRIDE_RE.finditer(css):
        if int(m.group("width")) <= 480:
            return m
    return None


def test_span_2_items_span_the_full_row_on_phones() -> None:
    """A <=480px rule must neutralise `span 2` so no implicit track appears."""
    assert _phone_override_match(_served_css()) is not None, (
        "no <=480px media query makes .meta-item.span-2 span the full row; "
        "`grid-column: span 2` forces an implicit second column that defeats "
        "the single-column collapse and clips content under overflow:hidden"
    )


def test_phone_override_wins_the_cascade_over_the_unconditional_rule() -> None:
    """The override must come AFTER the base rule, or it is silently dead.

    Media queries add no specificity. At 390px both `.meta-item.span-2` rules
    apply at identical specificity (0-2-0), so source order decides. This is the
    exact defect the first remediation attempt shipped while its presence-only
    test passed.
    """
    css = _served_css()

    override = _phone_override_match(css)
    assert override is not None, "phone override missing (see the previous test)"

    base_matches = list(_BASE_RE.finditer(css))
    assert base_matches, "the base `.meta-item.span-2 { grid-column: span 2 }` rule was removed"

    last_base_end = base_matches[-1].end()
    assert override.start() > last_base_end, (
        "the <=480px `.meta-item.span-2` override is declared BEFORE the "
        f"unconditional `grid-column: span 2` rule (override at char "
        f"{override.start()}, base rule ends at {last_base_end}). Same "
        "specificity + earlier source position means the unconditional rule "
        "wins at EVERY viewport width and the override does nothing."
    )


def test_base_span_2_rule_survives_for_wide_viewports() -> None:
    """Non-regression: the desktop two-column span must stay intact."""
    assert _BASE_RE.search(_served_css()), "the base `.meta-item.span-2 { grid-column: span 2 }` rule was removed"
