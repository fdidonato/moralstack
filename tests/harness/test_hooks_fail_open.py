"""Every hook must be fail-open: malformed or empty stdin never raises, exits 0."""

from __future__ import annotations

import pytest

HOOKS = [
    "stop_gate",
    "precompact_snapshot",
    "session_end",
    "session_start",
    "user_prompt_submit",
    "format_on_edit",
    "log_instructions",
]


@pytest.mark.parametrize("name", HOOKS)
@pytest.mark.parametrize("blob", ["", "   ", "not json {{{", "[1,2,3]", "null", '{"unexpected": true}'])
def test_hook_never_raises_and_exits_zero(load_hook, run_hook, project, name, blob):
    module = load_hook(name)
    code, _ = run_hook(module, blob, project, raw=True)
    assert code == 0
