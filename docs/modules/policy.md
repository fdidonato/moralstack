# Policy LLM

> **Module**: `moralstack/models/policy.py`

The Policy LLM is the generative model responsible for text production in MoralStack.

**For testers and integrators**: The module exposes `generate`, `rewrite` and `refuse`. The Orchestrator uses `rewrite`
with aggregated guidance from Critic, Simulator, Hindsight and Perspectives. For isolated tests it is possible to mock
the Policy or use a lightweight model. In the current implementation, MoralStack uses an external LLM provider (OpenAI)
for policy reasoning. The policy abstraction is designed to be extensible in the future, but currently there is only one
concrete implementation (OpenAIPolicy).

---

## Overview

The Policy LLM handles:

- **Initial generation** of responses
- **Guided revision** based on feedback
- **Formulation of refusal** with reasoned and respectful explanations

---

## Implementation

### OpenAIPolicy

Cloud implementation using the OpenAI API. It is the only concrete implementation currently available.
Uses [OpenAI Params](./openai_params.md) to select `max_tokens` or `max_completion_tokens` based on the model (newer
models like gpt-5.x and o-series require the latter).

```python
from moralstack.models.policy import OpenAIPolicy, OpenAIPolicyConfig

# From environment variables (OPENAI_API_KEY, OPENAI_MODEL, optional MORALSTACK_POLICY_REWRITE_MODEL, ...)
policy = OpenAIPolicy()

# Or with explicit overrides
policy = OpenAIPolicy(api_key="sk-...", model="gpt-4o")
```

---

## Main Methods

### generate()

Generates a response from a prompt.

```python
result = policy.generate(
    prompt="Explain the concept of social justice",
    system="You are an educational assistant",
    config=GenerationConfig(
        max_new_tokens=512,
        temperature=0.7,
    )
)

print(result.text)
```

### rewrite()

Revises a response based on feedback.

When the configured model supports it (gpt-4o, gpt-4o-mini, gpt-4.1 family), `rewrite()` automatically leverages
**OpenAI Predicted Outputs** (speculative decoding): the existing draft is provided as a prediction hint so that
unchanged portions of the text are generated significantly faster. This is transparent to the caller and does not
alter the output quality.

**Rewrite model**: `OpenAIPolicy` may use a separate model for `rewrite()` only via `MORALSTACK_POLICY_REWRITE_MODEL`.
If unset or empty, `rewrite()` uses the same model as `generate()` (`OPENAI_MODEL`). This keeps the first-pass
draft on the primary model while allowing a lighter model for deliberative revisions (see `docs/architecture_spec.md`).

The rewrite prompt also includes explicit constraints that prevent lighter models from adding
new operational content not present in the original draft. This ensures that `gpt-4.1-nano`
rewrites maintain quality comparable to `gpt-4o` rewrites on the benchmark (9.39 avg score,
0 leakage).

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required API key |
| `OPENAI_MODEL` | Primary model for `generate()` and `refuse()` |
| `MORALSTACK_POLICY_REWRITE_MODEL` | Optional; model for `rewrite()` (deliberative cycle 2+). Defaults to `OPENAI_MODEL` when unset. `.env.template` sets `gpt-4.1-nano` when you copy that file. |

See also `.env.template` and `INSTALL.md`.

```python
result = policy.rewrite(
    prompt="Original user request",
    draft="Previous draft to improve...",
    guidance="Add medical disclaimer and acknowledge emotional impact",
    system="You are an assistant that improves responses",
)

print(result.text)  # Revised response
```

**Important note**: The `guidance` is used as *instructions* for the LLM, it is not included literally in the output.

### refuse()

Generates a reasoned and respectful refusal.

```python
result = policy.refuse(
    prompt="Problematic user request",
    guidance="Explain that we cannot provide instructions for illegal activities",
    language="English",  # Optional: explicit output language (e.g. when prompt is empty for regulated domains)
)

print(result.text)  # Natural refusal, not the literal guidance
```

---

## Output Structure

### GenerationResult

```python
@dataclass
class GenerationResult:
    text: str              # Generated text
    tokens_used: int       # Tokens consumed
    finish_reason: str     # Termination reason ("stop", "length", etc.)
```

### GenerationConfig

```python
@dataclass
class GenerationConfig:
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    stop_sequences: list[str] = []
    response_format: Any = None  # OpenAI response format constraint
```

The optional `response_format` field maps to OpenAI's response format (e.g. `{"type": "json_object"}`). Structured evaluation modules (Critic, Simulator, Hindsight, Perspectives) set it so the API returns guaranteed valid JSON.

---

## Output Sanitization

The Policy LLM includes protection against internal instruction leakage:

```python
def sanitize_policy_output(text: str) -> str:
    """Removes meta-instructions from generated text."""
    # Filters patterns like "RULE OF THUMB", "system prompt", etc.
```

---

## Language Handling

The system preserves the language of the request. When `explicit_language` is provided (e.g. from Risk Estimator's
`detected_language`), a stronger instruction is used to reduce LLM non-compliance:

```python
def force_language_prefix(
    user_prompt: str,
    explicit_language: str | None = None,
) -> str:
    """Adds prefix to respect user language. Use explicit_language when known to reduce output language drift."""
    # When explicit_language is set: "CRITICAL: The user's request is in {language}. You MUST respond entirely in {language}."
    # Otherwise: "Reply in the same language as the user's request below. Do not add translations."
```

---

## Protocol

### PolicyLLMProtocol

```python
class PolicyLLMProtocol(Protocol):
    def generate(
        self,
        prompt: str,
        system: str = "",
        config: Any = None,
    ) -> Any:
        """Generates response from prompt."""
        ...

    def rewrite(
        self,
        prompt: str,
        draft: str,
        guidance: str,
        system: str = "",
        config: Any = None,
    ) -> Any:
        """Revises response with guidance."""
        ...

    def refuse(
        self,
        prompt: str,
        guidance: str,
        config: Any = None,
        language: str | None = None,
    ) -> Any:
        """Generates reasoned refusal. language: explicit output language when prompt empty or to reduce drift."""
        ...
```

The `config` argument is typically a `GenerationConfig` (see [Output Structure](#output-structure)); it may include `response_format` for OpenAI structured outputs.

---

## Usage with OpenAI

MoralStack uses OpenAI as the default LLM provider:

```bash
export OPENAI_API_KEY=sk-...
moralstack
```

Or with CLI override:

```bash
moralstack --openai-key YOUR_KEY --openai-model gpt-4o
```

Alternatively: `python -m moralstack.cli.run`. All generation operations go through the OpenAI API.

---

## See Also

- [Orchestrator](./orchestrator.md) - Uses Policy for generation/revision
- [Constitutional Critic](./critic.md) - Uses Policy for evaluation
- [Hindsight Evaluator](./hindsight.md) - Uses Policy for retrospective evaluation
