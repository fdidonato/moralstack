"""``LLMConstitutionalCritic.quick_check`` never turns "no verdict" into a pass.

Before the fix, ``quick_check`` wrapped the LLM call and the JSON parsing in
``except (JSONParseError, Exception)`` and returned ``QuickCheckResult(passed=True)``
(fail OPEN, F2). It also read ``data.get("violated", False)``, so a parsed JSON
object without the ``violated`` key was a pass.

Contract pinned here:

* provider / transport errors, unparseable text, non-dict replies and a reply
  without the ``violated`` key all RAISE (the caller treats that as "no verdict");
* ``passed=True`` only after a parsed, falsy ``violated`` or on the unchanged
  zero-HARD-principles early pass.

Offline: the policy is a double, no network. The prompt bytes are pinned elsewhere
(``tests/test_static_prefix_stability.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic
from moralstack.utils.json_utils import JSONParseError


@dataclass
class _Principle:
    id: str
    title: str = "t"
    rule: str = "rule text"
    level: str = "hard"


class _Policy:
    """Records every generate call; returns ``text`` or raises ``exc``."""

    model = "test-model"

    def __init__(self, text: Any = '{"violated": false}', exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._text = text
        self._exc = exc

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> Any:
        self.calls.append({"prompt": prompt, "system": system})
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(text=self._text)


def _constitution(level: str = "hard") -> Any:
    return SimpleNamespace(principles=[_Principle("HARD.1", level=level)])


def _check(policy: _Policy, constitution: Any | None = None) -> Any:
    critic = LLMConstitutionalCritic(policy=policy, store=None)
    return critic.quick_check("prompt", "response", constitution or _constitution(), None)


def test_c1_generate_raises_propagates() -> None:
    policy = _Policy(exc=RuntimeError("provider down"))
    with pytest.raises(RuntimeError, match="provider down"):
        _check(policy)
    assert len(policy.calls) == 1


@pytest.mark.parametrize("text", ["", "not json", "{}{", "{}", '{"violation": false}'])
def test_c2_unparseable_or_missing_verdict_key_raises_json_parse_error(text: str) -> None:
    with pytest.raises(JSONParseError):
        _check(_Policy(text=text))


def test_c2_non_dict_reply_raises_json_parse_error() -> None:
    with pytest.raises(JSONParseError):
        _check(_Policy(text="[]"))


def test_c2_none_text_raises_attribute_error() -> None:
    # Pinned as observed: ``extract_json(None)`` fails on ``None.strip()``.
    with pytest.raises(AttributeError):
        _check(_Policy(text=None))


def test_c2b_string_false_is_truthy_and_fails_the_check() -> None:
    # Pins a FALSE ESCALATION (never a false pass): the string "false" is truthy.
    result = _check(_Policy(text='{"violated": "false"}'))
    assert result.passed is False


def test_c3_violated_true_fails_the_check() -> None:
    result = _check(_Policy(text='{"violated": true, "principle_id": "HARD.1", "evidence": "e"}'))
    assert result.passed is False
    assert result.critical_violation is not None
    assert result.critical_violation.principle_id == "HARD.1"


def test_c4_parsed_falsy_violated_passes() -> None:
    result = _check(_Policy(text='{"violated": false}'))
    assert result.passed is True


def test_c5_no_hard_principles_is_unchanged_pass_without_llm_call() -> None:
    policy = _Policy(exc=RuntimeError("must not be called"))
    result = _check(policy, constitution=_constitution(level="soft"))
    assert result.passed is True
    assert policy.calls == []
