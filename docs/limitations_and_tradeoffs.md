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
computational overhead compared to a direct LLM call (~36s mean,
~26s median on benchmark run 12 vs ~6s for raw GPT-4o).

The system prioritizes safety, decision correctness, and auditability
over pure latency. Cumulative optimizations (dynamic parallel scheduling,
speculative overlap, parallel risk estimation, early convergence on
cycle 1, lighter models for simulator and policy rewrite, guidance
filtering on later cycles) reduced **mean** wall-clock from ~73s in an
earlier benchmark configuration to **~36s** on benchmark run 12 (~51%
reduction), with **98.8%** compliance unchanged.

Latency profile by path (84-question benchmark, run 12):

- **Fast path** (benign + clearly harmful queries, ~37% of traffic): ~12s
- **Deliberative path**: SAFE_COMPLETE
  converging in **one** cycle averages ~23s mean; two-cycle SAFE_COMPLETE
  averages ~61s mean
- **Deliberative sensitive** (regulated domains): often at the higher end
  of the deliberative range

For this reason, MoralStack is not suitable for:

- high-frequency creative chat
- real-time systems with strict sub-second latency constraints

Planned further optimizations include early-exit on low-risk deliberative
queries and context-mode switching for reduced token overhead.

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
