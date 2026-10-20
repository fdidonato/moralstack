"""Recording fakes for the prompt golden oracle (A0). Offline: no network, no real API keys.

``RecordingPolicy`` is lifted from ``tests/test_static_prefix_stability.py:76-134``
(``_GenResult``, ``_Call``, ``_CapturingPolicy``) and extended:

* it records every call in order, as the exact wire the model would receive
  (``generate`` -> system + prompt, ``generate_messages`` -> the messages list);
* every call carries a deterministic *label*, so calls issued from worker threads
  (the three risk mini-estimators, the perspective ensemble) are keyed by what
  they are, not by thread arrival order;
* ``fail_first_per_label`` makes the first call of every label return unparseable
  text, which forces each module onto its retry composition (attempt 1).

The source test module is NOT imported or modified: the golden oracle must not
depend on a file that Phase B is allowed to migrate.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

UNPARSEABLE = "NOT-JSON: the golden fake returns this to force the retry composition."


@dataclass
class GenResult:
    """Minimal stand-in for GenerationResult (lifted from the static-prefix test)."""

    text: str
    tokens_used: int = 10
    prompt_tokens: int = 5
    completion_tokens: int = 5
    finish_reason: str = "stop"

    def token_usage_json(self) -> str | None:
        return None


@dataclass
class WireCall:
    """One call as received by the (fake) model."""

    label: str
    kind: str  # "generate" | "messages"
    system: str = ""
    prompt: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)


ReplyFor = Callable[[str], str]
ReplyForUser = Callable[[str, str], "str | None"]
Labeler = Callable[[str, str], str]


def _default_labeler(system: str, user: str) -> str:
    return "call"


class RecordingPolicy:
    """Fake PolicyLLMProtocol recording the exact wire of every call.

    ``reply_for`` maps the system prompt of a call to the text returned (``default_response`` when
    absent). ``reply_for_user(system, user)`` is consulted first and may return ``None`` to defer to
    ``reply_for`` (used where a reply must echo ids printed in the prompt). ``generate_messages`` is
    exposed only when ``with_native_messages`` (the modules branch on its presence,
    which is how the legacy ``generate()`` branches are reached).
    """

    def __init__(
        self,
        default_response: str = "{}",
        reply_for: ReplyFor | None = None,
        with_native_messages: bool = True,
        fail_first_per_label: bool = False,
        labeler: Labeler | None = None,
        model: str = "test-model",
        reply_for_user: ReplyForUser | None = None,
    ) -> None:
        self.default_response = default_response
        self.reply_for = reply_for
        self.reply_for_user = reply_for_user
        self.fail_first_per_label = fail_first_per_label
        self.labeler = labeler or _default_labeler
        self.model = model
        self._rewrite_model = model
        self.wire: list[WireCall] = []
        self._seen_labels: set[str] = set()
        self._lock = threading.Lock()
        if with_native_messages:
            self.generate_messages = self._generate_messages

    def _respond(self, label: str, system: str, user: str = "") -> str:
        with self._lock:
            first = label not in self._seen_labels
            self._seen_labels.add(label)
        if self.fail_first_per_label and first:
            return UNPARSEABLE
        if self.reply_for_user is not None:
            echoed = self.reply_for_user(system, user)
            if echoed is not None:
                return echoed
        if self.reply_for is not None:
            return self.reply_for(system)
        return self.default_response

    def generate(self, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> GenResult:
        label = self.labeler(system, prompt)
        with self._lock:
            self.wire.append(WireCall(label=label, kind="generate", system=system, prompt=prompt))
        return GenResult(text=self._respond(label, system, prompt))

    def _generate_messages(self, *, messages: list[dict[str, str]], config: Any = None, **_kw: Any) -> GenResult:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        label = self.labeler(system, user)
        with self._lock:
            self.wire.append(WireCall(label=label, kind="messages", messages=[dict(m) for m in messages]))
        return GenResult(text=self._respond(label, system, user))

    def rewrite(self, *args: Any, **kwargs: Any) -> GenResult:
        """Run the REAL ``OpenAIPolicy.rewrite`` composition on top of the recorded ``generate``."""
        from moralstack.models.policy import OpenAIPolicy

        return OpenAIPolicy.rewrite(self, *args, **kwargs)  # type: ignore[arg-type]

    def refuse(self, *args: Any, **kwargs: Any) -> GenResult:
        """Run the REAL ``OpenAIPolicy.refuse`` composition on top of the recorded ``generate``."""
        from moralstack.models.policy import OpenAIPolicy

        return OpenAIPolicy.refuse(self, *args, **kwargs)  # type: ignore[arg-type]

    def snapshot(self) -> list[WireCall]:
        with self._lock:
            return list(self.wire)


class FakeOpenAIFactory:
    """Stand-in for ``openai.OpenAI`` used by the constitution retriever (prefilter and domain agents).

    Records the exact ``messages`` of every ``chat.completions.create`` call and returns a fixed
    JSON reply. Patched in with ``unittest.mock.patch("openai.OpenAI", factory)``.
    """

    def __init__(self, reply: str = "{}") -> None:
        self.reply = reply
        self.wire: list[WireCall] = []
        self._lock = threading.Lock()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        completions = SimpleNamespace(create=self._create)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def _create(self, **kwargs: Any) -> Any:
        messages = [dict(m) for m in kwargs.get("messages", [])]
        with self._lock:
            self.wire.append(WireCall(label="call", kind="messages", messages=messages))
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        choice = SimpleNamespace(message=SimpleNamespace(content=self.reply), finish_reason="stop")
        return SimpleNamespace(usage=usage, choices=[choice])


@dataclass
class FakePrinciple:
    """Duck-typed principle for the retrieval store fake (``id``/``title``/``rule``/``level``)."""

    id: str
    title: str
    rule: str
    level: str


class FakeRetrievalStore:
    """Constitution store double exposing ``retrieve()`` (the typed channel the estimator prefers)."""

    def __init__(self, principles: list[Any]) -> None:
        self._principles = principles

    def retrieve(self, query: str, top_k: int = 10, domain: str | None = None, **_kw: Any) -> Any:
        from moralstack.constitution.retrieval_result import PrincipleRetrievalResult

        return PrincipleRetrievalResult(
            principles=tuple(self._principles),
            prefiltered_domains=("core",),
            debug_info={"prefiltered_domains": ["core"]},
        )

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None, **_kw: Any) -> list[Any]:
        return list(self._principles)

    def get_constitution(self, domain: str | None = None) -> Any:
        return SimpleNamespace(principles=list(self._principles), active_overlay=None, constitution_corrupted=False)

    def has_excluded_domains(self) -> bool:
        return False
