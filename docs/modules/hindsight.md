# Hindsight Evaluator

> **Module**: `moralstack/runtime/modules/hindsight_module.py`

The Hindsight Evaluator implements retrospective evaluation of responses and simulated scenarios.

**For testers**: The score (`expected_value`) and `recommendation` (proceed/revise/refuse) are used by the Orchestrator
for convergence and the final decision. The `min_hindsight_score` threshold (e.g. 0.8) in config determines when the
deliberative cycle can converge; a `REFUSE` recommendation can lead to **REFUSE** in output.

---

## Overview

The Hindsight Evaluator:

- Evaluates responses as if consequences were already realized
- Applies the "hindsight" criterion to identify issues
- Produces multi-dimensional scores (safety, helpfulness, honesty)
- Provides recommendations and feedback for revisions

---

## Evaluation Dimensions

### HindsightScores

| Dimension       | Default Weight | Range   | Description                                |
|-----------------|----------------|---------|--------------------------------------------|
| **Safety**      | 0.5            | [-1, 1] | How safe and non-harmful the response is   |
| **Helpfulness** | 0.3            | [-1, 1] | How useful and informative the response is |
| **Honesty**     | 0.2            | [-1, 1] | How honest and transparent the response is |

### Total Score Calculation

```python
total = (safety × 0.5) + (helpfulness × 0.3) + (honesty × 0.2)
```

---

## Prompt-Caching Static/Dynamic Split (Part A)

Hindsight has **three LLM entry points** across two JSON contracts (root-object vs
`"evaluations"`-rooted), so path-specific system prompts prevent schema collisions:

- **Single-scenario** (`evaluate_scenario`) and the **non-batch aggregate**
  (`_evaluate_individual`, used when batch is disabled or there is a single
  consequence — it calls `evaluate_scenario` per consequence) both use
  `HINDSIGHT_SINGLE_SYSTEM_PROMPT` (`hindsight_module.py`): base framing + the
  3-dimension rubric + the ROOT-OBJECT JSON skeleton. `HINDSIGHT_PROMPT_TEMPLATE`
  is dynamic-only (REQUEST/RESPONSE/CONSEQUENCE). The aggregate `HindsightResult`
  returned by `_evaluate_individual` sets `system_prompt` to this same constant
  (reflecting what was actually sent, per the observability invariant).
- **Batch** (`_evaluate_batch`, `generate_messages`/legacy branches) uses
  `HINDSIGHT_BATCH_SYSTEM_PROMPT` (`moralstack/prompts/hindsight_prompt.py`): the
  batch evaluation instructions + the `"evaluations"`-rooted schema + the
  developer-contract-compliance block. `build_hindsight_prompt()` is dynamic-only
  (POTENTIAL CONSEQUENCES/TURN CONTEXT/RISK CONTEXT).

The two constants are never interchangeable (verified by
`tests/test_static_prefix_stability.py`); `response_format` stays `json_object`.

---

## Output Structure

### HindsightScores

```python
@dataclass
class HindsightScores:
    safety: float  # [-1, 1]
    helpfulness: float  # [-1, 1]
    honesty: float  # [-1, 1]
    total: float  # Weighted average
```

### HindsightEvaluation

```python
@dataclass
class HindsightEvaluation:
    scores: HindsightScores
    recommendation: HindsightRecommendation  # proceed/revise/refuse
    feedback: str  # Detailed feedback
    suggestions: list[str]  # Specific suggestions
    reasoning: str  # Reasoning
```

### AggregatedHindsight

```python
@dataclass
class AggregatedHindsight:
    expected_value: float  # E[score] over all scenarios
    worst_case: float  # min(score)
    best_case: float  # max(score)
    variance: float  # Var(score)
    recommendation: str  # Aggregated recommendation
    evaluations: list[...]  # Individual evaluations
```

---

## Recommendations

### HindsightRecommendation

| Value     | When                | Action              |
|-----------|---------------------|---------------------|
| `PROCEED` | Total score ≥ 0.7   | Acceptable response |
| `REVISE`  | Total score 0.3-0.7 | Needs improvement   |
| `REFUSE`  | Total score < 0.3   | Should be refused   |

---

## Usage

### Initialization

```python
from moralstack.runtime.modules.hindsight_module import LLMHindsightEvaluator

hindsight = LLMHindsightEvaluator(policy_llm=policy)
```

### Evaluation

```python
result = hindsight.evaluate(
    prompt="User request",
    response="Response to evaluate",
    consequences=simulation.consequences,  # Optional
)

print(f"Expected value: {result.expected_value}")
print(f"Recommendation: {result.recommendation}")
print(f"Feedback: {result.feedback}")

# Detailed scores
for eval in result.evaluations:
    print(f"Safety: {eval.scores.safety}")
    print(f"Helpfulness: {eval.scores.helpfulness}")
    print(f"Honesty: {eval.scores.honesty}")
```

---

## Example Output

```python
AggregatedHindsight(
    expected_value=0.65,
    worst_case=-0.05,
    best_case=1.00,
    variance=0.24,
    recommendation="proceed",
    evaluations=[
        HindsightEvaluation(
            scores=HindsightScores(
                safety=0.67,
                helpfulness=0.50,
                honesty=0.83,
                total=0.65
            ),
            recommendation=HindsightRecommendation.PROCEED,
            feedback="The response balances accuracy and caution",
            suggestions=["Add references for further reading"],
            reasoning="Overall positive evaluation..."
        )
    ]
)
```

---

## Orchestrator Integration

### Convergence Threshold

```python
if hindsight.expected_value >= config.min_hindsight_score:  # 0.8
    # Can converge
    decision = DecisionType.CONVERGED
```

### Guidance Generation

When the score is low (< 0.7), guidance is generated:

```python
if hindsight.expected_value < 0.7:
    guidance_parts.append(
        f"[HINDSIGHT] Low score ({score:.2f}). "
        "Improve the overall ethical value of the response."
    )
```

### Contribution to Aggregated Guidance

```
# Example of generated guidance
[HINDSIGHT] Low score (0.49). Improve the overall ethical value of the response, making it more balanced and responsible.
[HINDSIGHT - Feedback] The response does not adequately acknowledge emotional impact
[HINDSIGHT - Suggestions] Include emotional validation and support resources
```

---

## Caching

The module implements caching to avoid recomputation:

```python
# Cache based on hash(prompt + response + consequences)
cache_key = hashlib.md5(
    f"{prompt}|{response}|{str(consequences)}".encode()
).hexdigest()
```

---

## Factory Methods

### HindsightScores.compute_total()

```python
scores = HindsightScores.compute_total(
    safety=0.8,
    helpfulness=0.7,
    honesty=0.9,
    weights=(0.5, 0.3, 0.2)  # Optional
)
```

### AggregatedHindsight.from_error()

```python
# Fallback on error
result = AggregatedHindsight.from_error("Evaluation failed")
# Assumes neutral score (0.5)
```

---

## Environment Variables

All hindsight evaluator tuning can be overridden via `.env`. Variables are read at evaluator construction; empty or
missing values use the defaults below. See `.env.template` for the full list. **In application runs (CLI and benchmark),
hindsight configuration is the single source of configuration — no CLI or code path overrides these variables.**

### Model (hindsight LLM)

#### MORALSTACK_HINDSIGHT_MODEL

- **Default**: *(none — uses the same model as the rest of the stack, e.g. `OPENAI_MODEL` or `gpt-4o`)*
- **Type**: string (OpenAI model id)
- **Description**: OpenAI model used **only** for the hindsight evaluator. When set and non-empty, the CLI and benchmark
  create a dedicated `OpenAIPolicy` with this model for the hindsight evaluator; the rest of the stack keeps using
  `OPENAI_MODEL`.
- **Effect**:
    - **Set to a model id** (e.g. `gpt-4o-mini`): The evaluator uses that model.
    - **Unset or empty**: The evaluator uses the same policy (and model) as the rest of the pipeline.

### LLM and retry behaviour

#### MORALSTACK_HINDSIGHT_MAX_RETRIES

- **Default**: `3`
- **Type**: int (>= 1)
- **Description**: Number of parse attempts for the hindsight JSON response before raising an error.

Hindsight evaluation uses OpenAI's `json_object` response format (`response_format={"type": "json_object"}` on `GenerationConfig`), which guarantees valid JSON and greatly reduces retries caused by malformed JSON.

#### MORALSTACK_HINDSIGHT_MAX_TOKENS

- **Default**: `768`
- **Type**: int (>= 1)
- **Description**: Maximum tokens for the hindsight LLM response.

#### MORALSTACK_HINDSIGHT_TEMPERATURE

- **Default**: `0.3`
- **Type**: float (0–2)
- **Description**: LLM temperature for evaluation. Low values favour consistent, deterministic evaluations.

#### MORALSTACK_HINDSIGHT_TOP_P

- **Default**: `0.9`
- **Type**: float (0–1)
- **Description**: Nucleus sampling (top-p) for hindsight LLM generation. Controls diversity of token sampling.

### Score weights

#### MORALSTACK_HINDSIGHT_WEIGHT_SAFETY

- **Default**: `0.5`
- **Type**: float (0–1)
- **Description**: Weight of the safety dimension in the total score calculation.

#### MORALSTACK_HINDSIGHT_WEIGHT_HELPFULNESS

- **Default**: `0.3`
- **Type**: float (0–1)
- **Description**: Weight of the helpfulness dimension in the total score calculation.

#### MORALSTACK_HINDSIGHT_WEIGHT_HONESTY

- **Default**: `0.2`
- **Type**: float (0–1)
- **Description**: Weight of the honesty dimension in the total score calculation.

### Recommendation thresholds

#### MORALSTACK_HINDSIGHT_REFUSE_THRESHOLD

- **Default**: `-0.7`
- **Type**: float (-1 to 1)
- **Description**: Expected value below this threshold triggers a REFUSE recommendation.

#### MORALSTACK_HINDSIGHT_REVISE_THRESHOLD

- **Default**: `0.0`
- **Type**: float (-1 to 1)
- **Description**: Expected value below this threshold triggers a REVISE recommendation.

### Evaluation mode

#### MORALSTACK_HINDSIGHT_USE_BATCH_EVALUATION

- **Default**: `true`
- **Type**: bool (1/true/yes or 0/false/no)
- **Description**: When true, evaluates all scenarios in a single LLM call (more efficient). When false, evaluates each
  scenario individually (more robust).

### Caching

#### MORALSTACK_HINDSIGHT_ENABLE_CACHING

- **Default**: `true`
- **Type**: bool (1/true/yes or 0/false/no)
- **Description**: Enable caching of evaluation results to avoid recomputation on identical inputs.

---

## Conversation Context

For multi-turn requests, hindsight receives the developer contract plus recent conversation history from
`ProcessedRequest`. The prompt declares `context_mode=role_serialized_truncated; last 3 turns`, and the deliberation
runner records `CONTEXT_SHAPE_RECORDED` plus context-shape metadata on the hindsight LLM call.

---

## See Also

- [Consequence Simulator](./simulator.md) - Provides scenarios to evaluate
- [Orchestrator](./orchestrator.md) - Uses hindsight for convergence
- [Policy LLM](./policy.md) - Evaluation generation
