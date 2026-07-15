"""
UI test for iteration 11: the meta-grid collapses to a single column on phones.

At two columns (`@media (max-width: 768px)` → `1fr 1fr`) an unbreakable cell —
e.g. the `white-space: nowrap` "First / last turn" timestamp — pins a grid
track wider than the clipped `.card` (`overflow: hidden`), silently cutting off
content in that column (observed: the conversation "Final actions" REFUSE
count at 390px). A phone breakpoint (`max-width: 480px`) forcing a single
column gives every tile the full width so nothing is clipped.

CSS media-query layout cannot be exercised by the Jinja/TestClient render path
(no CSS engine), so this asserts the rule ships in the served stylesheet — a
regression guard against the breakpoint being removed. The visual behaviour is
verified in the browser by the UI-loop verifier.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _served_css() -> str:
    from moralstack.ui.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    resp = client.get("/static/css/main.css")
    assert resp.status_code == 200, resp.status_code
    return resp.text


def test_meta_grid_collapses_to_single_column_on_phones() -> None:
    css = _served_css()
    # Find every `@media (max-width: N…)` block and its body.
    blocks = re.findall(
        r"@media\s*\(max-width:\s*(\d+)px\s*\)\s*\{(.*?)\n\}",
        css,
        flags=re.DOTALL,
    )
    assert blocks, "no max-width media queries found in served CSS"

    # At least one phone-width (<=480px) block must set .meta-grid to a single
    # column. We match the .meta-grid rule inside the block body.
    phone_single_col = False
    for width_str, body in blocks:
        if int(width_str) > 480:
            continue
        rule = re.search(
            r"\.meta-grid\s*\{[^}]*grid-template-columns:\s*1fr\s*;[^}]*\}",
            body,
        )
        if rule:
            phone_single_col = True
            break
    assert phone_single_col, "no <=480px media query collapses .meta-grid to a single column"


def test_two_column_meta_grid_still_present_for_tablets() -> None:
    # Non-regression: the tablet breakpoint keeping two columns must survive.
    css = _served_css()
    assert re.search(
        r"@media\s*\(max-width:\s*768px\s*\).*?\.meta-grid\s*\{[^}]*grid-template-columns:\s*1fr\s+1fr\s*;",
        css,
        flags=re.DOTALL,
    ), "the 768px two-column meta-grid rule was removed"
