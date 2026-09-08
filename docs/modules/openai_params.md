# OpenAI API Parameters

> **Module**: `moralstack/utils/openai_params.py`

Single source of truth for OpenAI API parameter selection. Handles model-specific differences in the Chat Completions
API.

---

## Overview

OpenAI has deprecated `max_tokens` in favour of `max_completion_tokens` for newer models. Models that require the new
parameter reject requests that use `max_tokens` with a 400 error:

```
Unsupported parameter: 'max_tokens' is not supported with this model.
Use 'max_completion_tokens' instead.
```

MoralStack uses `moralstack.utils.openai_params` to select the correct parameter at every API call site, so you can use
any supported model without manual configuration.

---

## Why This Is Needed

| Parameter               | Models                                   | Description                                  |
|-------------------------|------------------------------------------|----------------------------------------------|
| `max_tokens`            | gpt-4o, gpt-4-turbo, gpt-3.5-turbo, etc. | Legacy parameter; limits generated tokens    |
| `max_completion_tokens` | gpt-5.x, o1, o3, o4                      | Required for reasoning and newer chat models |

The API does not provide a capability query; model compatibility is determined by model name prefix.

---

## API

### `MODELS_REQUIRING_MAX_COMPLETION_TOKENS`

Tuple of model name prefixes that require `max_completion_tokens`:

```python
MODELS_REQUIRING_MAX_COMPLETION_TOKENS = ("o1", "o3", "o4", "gpt-5")
```

### `uses_max_completion_tokens(model: str) -> bool`

Returns `True` if the model requires `max_completion_tokens` instead of `max_tokens`.

```python
from moralstack.utils.openai_params import uses_max_completion_tokens

uses_max_completion_tokens("gpt-4o")    # False
uses_max_completion_tokens("gpt-5.2")   # True
uses_max_completion_tokens("o3-mini")   # True
```

### `completion_tokens_param(model: str, max_tokens: int) -> dict[str, Any]`

Returns the correct parameter dict for `client.chat.completions.create()`:

```python
from moralstack.utils.openai_params import completion_tokens_param

# For gpt-4o, gpt-4-turbo, etc.
completion_tokens_param("gpt-4o", 1024)
# → {"max_tokens": 1024}

# For gpt-5.2, o3-mini, etc.
completion_tokens_param("gpt-5.2", 1024)
# → {"max_completion_tokens": 1024}
```

Usage at call site:

```python
response = client.chat.completions.create(
    model=model,
    messages=messages,
    temperature=0.7,
    **completion_tokens_param(model, max_tokens),
)
```

---

## Predicted Output Support

Some models support the `prediction` parameter, which enables speculative decoding for faster generation when the
expected output is largely similar to a known text (e.g. a draft revision in `rewrite()`).

### `MODELS_SUPPORTING_PREDICTED_OUTPUT`

Tuple of model name prefixes that support the `prediction` parameter:

```python
MODELS_SUPPORTING_PREDICTED_OUTPUT = (
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
)
```

### `supports_predicted_output(model: str) -> bool`

Returns `True` if the model supports speculative decoding via predicted outputs.

```python
from moralstack.utils.openai_params import supports_predicted_output

supports_predicted_output("gpt-4o")      # True
supports_predicted_output("gpt-4.1")     # True
supports_predicted_output("o3-mini")     # False (uses max_completion_tokens)
supports_predicted_output("gpt-5.2")    # False
```

**Constraints:** Predicted outputs are incompatible with `max_completion_tokens`, `logprobs`, and `n > 1`. The
`rewrite()` method in `OpenAIPolicy` uses this automatically — no caller changes needed.

**Reference:** [OpenAI Predicted Outputs](https://platform.openai.com/docs/guides/predicted-outputs)

---

## Updating the Model List

When OpenAI releases new models that require `max_completion_tokens`, update the tuple in
`moralstack/utils/openai_params.py`:

```python
MODELS_REQUIRING_MAX_COMPLETION_TOKENS = ("o1", "o3", "o4", "gpt-5", "gpt-6")  # add new prefix
```

When OpenAI adds predicted output support to new models, update:

```python
MODELS_SUPPORTING_PREDICTED_OUTPUT = ("gpt-4o", "gpt-4o-mini", "gpt-4.1", ...)  # add new prefix
```

**Rules:**

- Use the **shortest unique prefix** that identifies the model family (e.g. `gpt-5` matches `gpt-5.2`, `gpt-5.1`,
  `gpt-5-mini`).
- Matching is case-insensitive and uses `str.startswith()`.
- If a new model returns the `unsupported_parameter` error for `max_tokens`, add its prefix to the tuple.

**Reference:** [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat) — `max_tokens` is
deprecated and not compatible with o-series models.

---

## Structured Outputs Support

### `MODELS_SUPPORTING_JSON_SCHEMA` / `MODELS_WITHOUT_JSON_SCHEMA`

Models that accept a strict `response_format={"type": "json_schema", ..., "strict": true}`,
where the provider enforces the schema instead of the client validating the reply
after paying for it: the `gpt-4o`, `gpt-4.1`, `gpt-5` families and the o-series.
`MODELS_WITHOUT_JSON_SCHEMA` carves out `gpt-4o-2024-05-13`, a snapshot that predates
Structured Outputs but matches the `gpt-4o` prefix.

### `supports_json_schema(model: str | None) -> bool`

Deliberately an **allowlist**: an unrecognised model (a custom deployment behind
`OPENAI_BASE_URL`) returns `False` and keeps plain JSON mode, so the predicate can
never turn a working call into a failing one.

`PolicyLLM._complete` applies it centrally — a `json_schema` request is degraded to
`{"type": "json_object"}` on a model that does not support it, mirroring how
`supports_predicted_output` gates `prediction`. Callers therefore declare the schema
they want and never have to branch on the model.

**Who declares a schema today** (the critic deliberately does not — see below):

| Module | Schema factory | Contract source |
|---|---|---|
| Simulator | `simulator_response_format()` | derived from the `Literal` aliases of `SimulatorOutput` |
| Risk — harm signals | `harm_signal_response_format(registry)` | derived from the same `SignalRegistry` that renders the prompt |
| Risk — intent | `intent_response_format()` | mirrors the OUTPUT block in `models/risk/prompts.py` |
| Risk — operational | `operational_response_format()` | mirrors the OUTPUT block in `models/risk/prompts.py` |
| Hindsight (single / batch) | `hindsight_single_response_format()` / `hindsight_batch_response_format()` | fields of `HindsightSingleEvaluationOutput` |
| Perspectives | `perspective_response_format()` | the JSON block in `prompts/perspectives_prompt.py` |
| Safety override | `safety_override_response_format()` | enum from `SAFETY_OVERRIDE_CATEGORIES` |
| DCCL draft match | `draft_match_response_format()` | `DCCL_DRAFT_MATCH_SYSTEM_PROMPT` |

**The critic is intentionally excluded.** It records zero parse failures over 5,000
campaign calls, so there is no measured defect to fix, and it is the P0 module whose
verdict decides `final_action` — a change there is validated on its own, never bundled
with seven others.

**What a strict schema does and does not guarantee.** It fixes shape, key names and
enums. It does **not** enforce value ranges: the supported JSON Schema subset has no
`minimum`/`maximum`, so 0.0-1.0 bounds on scores stay the caller's job, and
client-side validation stays load-bearing anyway because a model outside the allowlist
silently falls back to plain JSON mode. `tests/test_structured_outputs_modules.py`
pins every schema against strict mode's requirements and against the contract its
prompt declares.

---

## Integration

All modules that call the OpenAI Chat Completions API use this utility:

- **Policy LLM** (`moralstack/models/policy.py`) — `_complete()` uses `completion_tokens_param` and
  `supports_predicted_output` (the latter for `rewrite()` speculative decoding)
- **Benchmark** (`scripts/benchmark_moralstack.py`) — `OpenAIClient.generate()`, `_generate_with_model()`
- **Constitution Retriever** (`moralstack/constitution/retriever.py`) — direct `client.chat.completions.create` calls (used by store)
- **Runtime modules** (critic, perspective, hindsight, simulator, risk estimator) — via policy

Config objects (e.g. `GenerationConfig.max_tokens`) keep the semantic value; only the API parameter name is chosen at
call time.

---

## See Also

- [Policy LLM](./policy.md) — main generation path
- [Risk Estimator](./risk_estimator.md) — configuration and max_tokens
- [INSTALL.md](../../INSTALL.md) — model compatibility and setup
