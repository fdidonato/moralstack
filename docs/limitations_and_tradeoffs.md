## Limitations & Trade-offs

MoralStack introduces intentional choices that entail some trade-offs.
These limitations are not accidental defects, but deliberate consequences
of the governance stance adopted by the system.

### 1. Residual Over-Governance

In some sensitive contexts (emotional support, vulnerability, "care" topics),
MoralStack may produce SAFE_COMPLETE even when a direct response
might be acceptable to a human evaluator.

This behavior is intentional:
moderate over-governance is considered preferable
to the risk of underestimating vulnerability situations.

### 2. Latency and Computational Cost

When it activates the deliberative path, MoralStack introduces
significant computational overhead compared to a direct LLM call.

The system prioritizes:

- safety
- decision correctness
- auditability

over pure latency.

For this reason, MoralStack is not suitable for:

- high-frequency creative chat
- real-time systems with strict latency constraints

### 3. SAFE_COMPLETE as Decision, not Error

In the policy-aware benchmark, SAFE_COMPLETE is not treated as
an automatic penalty.

This implies that comparisons based solely on
"helpfulness" metrics or aggregate scores may be misleading
if the underlying governance decision is not considered.

### 4. Not Optimized for All Domains

MoralStack is designed primarily for:

- public or regulated contexts
- sensitive informational support
- safety-critical applications

It is not intended as a universal replacement for a standard LLM
in purely creative or technical domains.

### 5. Dependence on Signal Quality

Decision correctness depends on the quality of extracted signals
(risk estimation, intent classification).

Although deterministic guardrails are present,
classification errors can influence
the decision path, especially in ambiguous cases.
