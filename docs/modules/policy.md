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

# From environment variables (OPENAI_API_KEY, OPENAI_MODEL, ...)
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
