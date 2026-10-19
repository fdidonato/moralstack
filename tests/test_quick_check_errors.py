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


# --- D3: a failed self-retrieval is logged (class name only), the fallback is kept ---

_SECRET = "SECRET-retrieval-detail"


class _ListHandler:
    """Minimal logging handler collecting records (``level`` attr needed by Logger)."""

    def __init__(self) -> None:
        import logging

        self.level = logging.DEBUG
        self.records: list[Any] = []

    def handle(self, record: Any) -> None:
        self.records.append(record)


class _Store:
    def __init__(self, principles: list[Any] | None = None, exc: Exception | None = None) -> None:
        self.calls = 0
        self._principles = principles or []
        self._exc = exc

    def get_relevant_principles(self, *, query: str, top_k: int, domain: Any) -> list[Any]:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._principles


def _capture_critic_logger(monkeypatch: pytest.MonkeyPatch, name: str) -> _ListHandler:
    import logging

    lg = logging.getLogger(f"d3.{name}")
    lg.handlers.clear()
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    handler = _ListHandler()
    lg.addHandler(handler)  # type: ignore[arg-type]
    monkeypatch.setattr("moralstack.runtime.modules.critic_module.logger", lg)
    return handler


def test_r1_self_retrieval_failure_logs_one_warning_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    handler = _capture_critic_logger(monkeypatch, "r1")
    policy = _Policy()
    store = _Store(exc=RuntimeError(_SECRET))
    critic = LLMConstitutionalCritic(policy=policy, store=store)
    result = critic.quick_check("prompt", "response", _constitution(), None)

    warnings = [r for r in handler.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "quick_check self-retrieval failed" in msg
    assert "error_type=RuntimeError" in msg
    assert _SECRET not in msg
    assert warnings[0].exc_info is not None
    assert isinstance(warnings[0].exc_info[1], RuntimeError)
    assert store.calls == 1
    # Fallback kept: the constitution HARD principles are still checked.
    assert len(policy.calls) == 1
    assert result.passed is True


def test_r2_self_retrieval_success_logs_no_warning_and_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    handler = _capture_critic_logger(monkeypatch, "r2")
    policy = _Policy()
    store = _Store(principles=[_Principle("STORE.HARD.1")])
    critic = LLMConstitutionalCritic(policy=policy, store=store)
    result = critic.quick_check("prompt", "response", _constitution(level="soft"), None)

    assert [r for r in handler.records if r.levelno == logging.WARNING] == []
    assert store.calls == 1
    # Store HARD principle used: a check ran even though the constitution has no HARD.
    assert len(policy.calls) == 1
    assert "STORE.HARD.1" in policy.calls[0]["prompt"]
    assert result.passed is True
