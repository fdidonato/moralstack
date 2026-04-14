# Creating Domain Overlays

> A step-by-step guide to extending MoralStack with custom domain governance.

Overlays let you tailor MoralStack's ethical governance to a specific domain — healthcare, legal, finance, or anything unique to your organization. This guide covers the full overlay schema, how each field affects the deliberation pipeline, and how to validate and test your overlay before deploying it.

For the architecture behind overlays, see [constitution.md](./constitution.md). For the full list of existing overlays, see `moralstack/constitution/data/overlays/`.

---

## Quick Start

Create a YAML file in `moralstack/constitution/data/overlays/`, validate it, and restart MoralStack:

```bash
# 1. Create your overlay
cat > moralstack/constitution/data/overlays/my_domain.yaml << 'EOF'
description: "Brief semantic description of your domain."
keywords:
  - keyword1
  - keyword2
sensitive: false
additional_principles: []
EOF

# 2. Validate it
moralstack-validate-overlay moralstack/constitution/data/overlays/my_domain.yaml

# 3. Run MoralStack — your overlay is now active for matching
moralstack
```

The filename (without `.yaml`) becomes the domain name. A file called `my_domain.yaml` creates the domain `my_domain`.

---

## Overlay Schema Reference

Every field in an overlay YAML file corresponds to a field in the `OverlayYAML` Pydantic model (`moralstack/constitution/schema.py`). The schema uses `extra="forbid"`, so any unrecognized field causes a validation error — this prevents typos from silently being ignored.

### Top-level fields

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `description` | `string` | `""` | Recommended | Semantic description of the domain. Used by the LLM-based domain detector to match incoming queries to this overlay. Write it as a comma-separated list of relevant topics and terms. |
| `keywords` | `list[string]` | `[]` | Recommended | Alternative keywords for domain matching. If empty, keywords are auto-extracted from `description` (lowercase, stopwords removed, words ≥ 4 chars, max 12). |
| `sensitive` | `bool` | `false` | No | When `true`, activates a risk score floor (default `0.35`) that forces the request into the deliberative path. Also triggers a `SAFE_COMPLETE` fallback if deliberation cycles are exhausted without convergence. |
| `excluded` | `bool` | `false` | No | When `true`, requests detected as this domain get an early exit — no deliberation, just a short polite refusal in the user's language. Useful for domains your deployment should not handle at all. |
| `priority_overrides` | `dict[string, int]` | `{}` | No | Alters the priority of existing core principles when this overlay is active. Keys are principle IDs (e.g., `SOFT.HONEST.1`), values are new priorities (1–100). |
| `refusal_redirection` | `string` | `""` | Recommended | Text shown to the user when a request is refused under this domain. Should suggest concrete alternative resources. |
| `simulator_domain_guidance` | `string` | `""` | No | Additional guidance injected into the consequence simulator prompt when this domain is active. Helps the simulator reason about domain-specific outcomes. |
| `sensitive_risk_floor` | `float \| null` | `null` | No | Override for the global sensitive risk floor (`0.35`). Only used when `sensitive: true`. Must be between `0.0` and `1.0`. Set this if your domain needs a higher or lower floor than the default. |
| `additional_principles` | `list[Principle]` | `[]` | No | Domain-specific ethical principles added on top of the core constitution. These are the heart of your overlay. |

### Principle fields (inside `additional_principles`)

Each principle in `additional_principles` follows the `PrincipleYAML` schema:

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `id` | `string` | — | **Yes** | Unique identifier. Convention: `DOMAIN.TOPIC.N` (e.g., `HC.HIPAA.1`, `LEGAL.DISCLAIMER.1`). Must not collide with core principle IDs or other overlay IDs. |
| `level` | `"hard" \| "soft"` | — | **Yes** | `hard` = non-negotiable (violation triggers refusal). `soft` = negotiable (violation triggers caveats or revision). |
| `priority` | `int` (1–100) | — | **Yes** | Higher = more important. Core hard constraints use 85–100. Soft norms typically use 30–80. Your domain principles should fit within these ranges. |
| `title` | `string` | — | **Yes** | Short, descriptive title shown in audit trails and decision explanations. |
| `rule` | `string` | — | **Yes** | The ethical rule in natural language. This is what the constitutional critic evaluates against. Be specific: vague rules lead to unpredictable enforcement. |
| `examples_allow` | `list[string]` | `[]` | Recommended | 1–2 examples of behaviors that comply with this principle. Used by the critic for calibration. More than 2 are silently truncated. |
| `examples_deny` | `list[string]` | `[]` | Recommended | 1–2 examples of behaviors that violate this principle. Same truncation rule. |
| `remediation` | `string` | `""` | No | Corrective action text (currently not used in prompts — reserved for future use). |
| `domain` | `string \| null` | `null` | No | Domain tag for the principle. Typically left `null` for overlay principles (the overlay itself provides domain context). |
| `keywords` | `list[string]` | `[]` | Recommended | Keywords associated with this specific principle. Used for principle-level matching within the domain. |

---

## How Each Field Affects the Pipeline

Understanding how MoralStack uses each field helps you write effective overlays.

### `description` and `keywords` → Domain Detection

When a user query arrives, MoralStack's domain detector (an LLM call) classifies the query against all available overlay descriptions. A good `description` is the single most important factor for accurate domain matching.

**Tips for writing `description`:**
- Write it as a comma-separated list of topics, not a sentence: `"Healthcare services, patient care, medical facilities, health insurance, HIPAA compliance."`
- Include both broad terms (`healthcare`) and specific terms (`HIPAA compliance`, `clinical care`).
- Think about how users phrase queries in your domain — include those phrasings.

**Tips for `keywords`:**
- Use 5–15 keywords that are central to your domain.
- Include both technical terms (`HIPAA`) and everyday language (`hospital`, `doctor`).
- If you leave `keywords` empty, they are auto-extracted from `description` using a simple tokenizer (lowercase, stopwords removed, words ≥ 4 chars, max 12 keywords). This is usually sufficient, but explicit keywords give you more control.

### `sensitive: true` → Deliberative Path Enforcement

When `sensitive` is `true`, two things happen:

1. **Risk score floor**: the risk score is clamped to at least `0.35` (or `sensitive_risk_floor` if set), which is above the low-risk threshold (`0.3`). This forces the request into the full deliberative path (critic → simulator → perspectives → hindsight) instead of the fast path.

2. **Cycles-exhausted fallback**: if deliberation runs out of cycles without converging and the tentative decision is `NORMAL_COMPLETE`, the system overrides it to `SAFE_COMPLETE` with reason code `cycles_exhausted_sensitive_fallback`. This is a safety net — in sensitive domains, uncertainty defaults to caution.

**When to use `sensitive: true`:**
- Domains where incorrect or unguarded responses can cause real-world harm (medical, legal, financial).
- Domains where regulatory compliance requires documented reasoning (HIPAA, GDPR).
- Domains with high reputational risk.

**When to leave `sensitive: false`:**
- General-purpose domains (coding, creative writing, education) where fast-path responses are acceptable for clearly benign queries.

### `excluded: true` → Domain Exclusion

When `excluded` is `true`, requests detected as this domain skip the entire pipeline. MoralStack generates a short, polite message in the user's language explaining that this domain is not available, and returns a `REFUSE` with path `DOMAIN_EXCLUDED`.

**Use case:** you deploy MoralStack for a customer service chatbot and want to exclude political or medical domains entirely. Set `excluded: true` in those overlays.

### `priority_overrides` → Tuning Core Principles

Priority overrides let you adjust how important a core principle is within your domain, without modifying the core constitution. The key is a principle ID from `moralstack/constitution/data/core.yaml`, and the value is the new priority (1–100).

**Example:** in a healthcare overlay, honesty and accuracy matter more than in general conversation:

```yaml
priority_overrides:
  SOFT.HONEST.1: 95      # Accuracy is critical in healthcare
  CORE.NM.1: 100         # Patient safety is paramount
  CORE.PRIV.1: 100       # HIPAA privacy
```

**What this changes:** when the constitution's conflict resolution algorithm sorts principles, your overridden priorities are used instead of the defaults. A `SOFT.HONEST.1` that would normally be priority 70 becomes 95, making it rank higher than soft norms that would otherwise outrank it.

### `additional_principles` → Domain-Specific Rules

This is where you define rules unique to your domain. These principles are added to the core constitution when your overlay is active, and the constitutional critic evaluates the response against them.

**Hard vs. soft principles:**
- **Hard** (`level: hard`): a violation triggers consideration for `REFUSE`. Use for non-negotiable safety rules (e.g., "never diagnose medical conditions").
- **Soft** (`level: soft`): a violation triggers consideration for `SAFE_COMPLETE` with caveats. Use for best-practice rules (e.g., "include medical disclaimers").

**Writing effective `rule` text:**
- Be specific and actionable: ✅ "Never prescribe treatments, medications, or dosages" vs. ❌ "Be careful with medical advice".
- State both what to do and what not to do.
- The critic LLM evaluates the response against this text literally — ambiguity leads to inconsistent enforcement.

**Writing `examples_allow` and `examples_deny`:**
- Keep them short and concrete (1–2 each, more are truncated to 2).
- `examples_allow` shows the critic what compliance looks like.
- `examples_deny` shows the critic what violation looks like.
- Together, they calibrate the critic's judgment for borderline cases.

### `refusal_redirection` → User-Facing Guidance

When a request is refused under your domain, MoralStack includes this text as a suggestion for alternative resources. Write it as a helpful list of concrete alternatives:

```yaml
refusal_redirection: |
  Suggest the user consult qualified professionals, such as:
  - Licensed attorneys or legal aid services
  - Consumer protection agencies
  - Official governmental legal information portals
```

### `simulator_domain_guidance` → Consequence Simulation Context

The consequence simulator imagines potential outcomes of the response. This field injects domain-specific context into the simulator's reasoning. Use it when your domain has unique outcome dynamics:

```yaml
simulator_domain_guidance: |
  In healthcare contexts, consider:
  - Patient safety implications of the information provided
  - Risk of self-diagnosis or self-medication based on the response
  - Potential for misinterpretation of medical terminology by non-experts
```

---

## Step-by-Step Workflow

### 1. Create the YAML file

Create a new file in `moralstack/constitution/data/overlays/`. The filename (without `.yaml`) becomes the domain name:

```bash
touch moralstack/constitution/data/overlays/real_estate.yaml
```

### 2. Write the overlay

Start with the minimal structure and expand:

```yaml
# MoralStack Constitution - Real Estate Domain Overlay
# Principles for real estate advice, property transactions, and housing.

description: "Real estate, property transactions, housing market, home buying, selling, renting, mortgages, real estate agents, property valuation, landlord tenant law, housing regulations."

keywords:
  - real estate
  - property
  - mortgage
  - housing
  - rental
  - landlord
  - tenant
  - home buying

sensitive: true

refusal_redirection: |
  Suggest the user consult qualified professionals, such as:
  - Licensed real estate agents or brokers
  - Real estate attorneys for contract review
  - Licensed mortgage advisors for financing questions
  - Local housing authorities for regulations

priority_overrides:
  SOFT.HONEST.1: 90      # Accuracy matters in financial decisions
  SOFT.HELPFUL.1: 85     # High utility for practical questions

additional_principles:
  - id: "RE.NOFINANCIAL.1"
    level: hard
    priority: 95
    title: "No Specific Financial Advice"
    rule: "Never provide specific financial advice on property purchases, mortgage rates, or investment returns. Always recommend consulting a licensed financial advisor."
    examples_allow:
      - "Generally, buyers consider factors like location, condition, and market trends"
      - "A mortgage advisor can help you understand your options"
    examples_deny:
      - "You should buy this property, it's a great investment"
      - "Lock in this mortgage rate now before it goes up"
    keywords:
      - "invest"
      - "mortgage"
      - "buy"
      - "profit"

  - id: "RE.DISCLAIMER.1"
    level: soft
    priority: 80
    title: "Real Estate Disclaimer"
    rule: "Include appropriate disclaimers that information is general and not a substitute for professional advice. Local regulations vary significantly."
    examples_allow:
      - "Real estate laws vary by jurisdiction; consult a local expert"
      - "This is general information; your situation may differ"
    examples_deny:
      - "Presenting general info as applicable everywhere"
      - "Omitting professional consultation recommendation"
    keywords:
      - "law"
      - "regulation"
      - "jurisdiction"
      - "contract"
```

### 3. Validate the overlay

Use the CLI validator to check your overlay before deploying:

```bash
moralstack-validate-overlay moralstack/constitution/data/overlays/real_estate.yaml
```

On success:

```
✔ Overlay "real_estate" is valid.

  Domain name:           real_estate
  Description:           Real estate, property transactions, housing market, ...
  Keywords:              8 explicit
  Sensitive:             true (risk floor: 0.35)
  Excluded:              false
  Priority overrides:    2 (SOFT.HONEST.1 → 90, SOFT.HELPFUL.1 → 85)
  Additional principles: 2 (1 hard, 1 soft)
  Refusal redirection:   provided
```

On error:

```
✘ Validation failed for "real_estate.yaml":

  additional_principles → 0 → priority
    Value error, priority deve essere tra 1 e 100
```

You can also validate all overlays at once:

```bash
moralstack-validate-overlay moralstack/constitution/data/overlays/
```

### 4. Test the overlay

Start MoralStack and try queries in your domain:

```bash
moralstack
```

```
You: Should I buy a house in this market?
```

Check that:
- The domain is detected correctly (visible in verbose mode: `moralstack --verbose`).
- The `final_action` matches your expectations (`SAFE_COMPLETE` for sensitive domains with general questions, `REFUSE` for out-of-scope requests).
- The decision explanation references your overlay principles.

If you have the SDK installed, you can also test programmatically:

```python
from moralstack import govern, GovernanceConfig
from openai import OpenAI

client = govern(
    OpenAI(),
    config=GovernanceConfig(domain_overlay="real_estate"),
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Should I buy a house in this market?"}],
)

print(response.governance_metadata.final_action)
print(response.governance_metadata.domain_overlay)
print(response.governance_metadata.triggered_principles)
```

### 5. Iterate

Overlay development is iterative. Common adjustments:
- **Too many refusals?** Lower the priority of hard principles, or rephrase `rule` to be more specific about what constitutes a violation.
- **Not enough governance?** Add `sensitive: true`, increase priorities, or add more hard principles.
- **Wrong domain detection?** Expand `description` and `keywords` with terms users actually use.
- **Irrelevant principles triggering?** Narrow `keywords` and make `rule` more specific.

---

## Naming Conventions

### Domain names
- Use `snake_case`: `real_estate`, `customer_service`, `mental_health`.
- Keep them short and descriptive.
- The filename is the domain name: `real_estate.yaml` → domain `real_estate`.

### Principle IDs
- Convention: `DOMAIN_PREFIX.TOPIC.NUMBER`.
- Use a 2–4 character uppercase prefix unique to your domain.
- Examples from existing overlays:
    - Healthcare: `HC.HIPAA.1`, `HC.NODIAGNOSIS.1`
    - Legal: `LEGAL.DISCLAIMER.1`, `LEGAL.NOPRACTICE.1`
    - Coding: `CODE.SECURITY.1`, `CODE.MALWARE.1`
    - Medical: `MED.DISCLAIMER.1`

### Priority ranges
- **100**: absolute safety rules (harm prevention, child protection).
- **90–99**: critical domain rules (no diagnosis, no legal advice).
- **80–89**: important best practices (disclaimers, evidence-based info).
- **70–79**: recommended practices (accessibility, timeliness).
- **30–69**: nice-to-have guidelines (tone, formatting).

---

## Common Patterns

### Sensitive domain with hard safety rails

For domains where incorrect responses can cause real harm (medical, legal, financial):

```yaml
sensitive: true
additional_principles:
  - id: "DOMAIN.SAFETY.1"
    level: hard
    priority: 100
    title: "Safety First"
    rule: "Never provide advice that could cause harm if followed without professional supervision."
    # ...
```

### Informational domain with soft guidance

For domains where MoralStack should be helpful but add appropriate caveats (education, science):

```yaml
sensitive: false
additional_principles:
  - id: "DOMAIN.ACCURACY.1"
    level: soft
    priority: 80
    title: "Accuracy and Sources"
    rule: "Encourage citing sources and acknowledging uncertainty."
    # ...
```

### Excluded domain

For domains your deployment should not handle:

```yaml
excluded: true
description: "Domain description for detection purposes."
keywords:
  - keyword1
```

When `excluded: true`, the `additional_principles`, `priority_overrides`, and other fields are still validated but not used at runtime — the request is refused before deliberation starts.

---

## Troubleshooting

**"My overlay is never detected"**
- Check that `description` contains terms similar to how users phrase queries.
- Add more `keywords` — both technical and colloquial terms.
- Use `moralstack --verbose` to see the domain detection reasoning.
- Try forcing the overlay via the SDK: `GovernanceConfig(domain_overlay="my_domain")`.

**"Validation fails with 'extra fields are not permitted'"**
- The schema uses `extra="forbid"`. Check for typos in field names — a field like `sensitve` (misspelled) will be rejected.
- Run `moralstack-validate-overlay` for a clear error message pointing to the offending field.

**"My hard principle never triggers refusal"**
- Check the `rule` text: the critic evaluates the *response* against the rule, not the *query*. If the response complies with the rule, no violation is detected.
- Check the `examples_deny`: are they similar to the response you're testing?
- Check `priority`: if it's too low, higher-priority soft principles (like helpfulness) might override it.

**"Everything is SAFE_COMPLETE when it should be NORMAL_COMPLETE"**
- If `sensitive: true`, the risk floor pushes all requests into deliberation. Consider whether your domain truly needs this.
- Check `priority_overrides`: a very high priority on a soft principle (like `SOFT.HONEST.1: 95`) can increase governance strictness.

---

## Existing Overlays as Examples

Browse the `moralstack/constitution/data/overlays/` directory for 19 production overlays. Good starting points:

| Overlay | Why it's useful as a reference |
|---|---|
| `coding.yaml` | Simple, non-sensitive domain with clear hard/soft principle separation. |
| `healthcare.yaml` | Comprehensive sensitive domain with many principles and priority overrides. |
| `legal.yaml` | Shows `LEGAL.NOPRACTICE.1` as a hard principle that prevents unauthorized practice. |
| `creative.yaml` | Non-sensitive domain showing how to balance safety with creative freedom. |
| `cybersecurity.yaml` | Sensitive domain handling dual-use information (defensive vs. offensive security). |

---

## References

- [Constitution design](./constitution.md) — Architecture, conflict resolution, overlay properties.
- [Constitution Store module docs](./modules/constitution_store.md) — API for loading and querying the constitution.
- [Decision policy](./decision_policy.md) — How `final_action` is determined.
- [Architecture spec](./architecture_spec.md) — Full technical specification.
