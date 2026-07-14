# Objective

An observability UI in which a technical reviewer can reconstruct MoralStack's
decision and delivery — accurately, quickly, and at the right level of detail —
for both a single governed request and a multi-turn conversation.

Accuracy outranks speed; speed outranks beauty.

## Within the first viewport

1. What was actually delivered to the caller?
2. Which action did MoralStack apply (`NORMAL_COMPLETE` / `SAFE_COMPLETE` / `REFUSE`)?
3. What was the authoritative source of the delivered text?
4. Which risk category and execution path were selected?
5. What is the one-sentence causal reason?
6. Is this a standalone request or a turn in a conversation?
7. Was context inherited, cached, refreshed, or escalated?

## After one deliberate expansion

1. Which principles and signals mattered, and in what priority?
2. Which modules executed, ran in parallel, were skipped, deferred, synthetic, or reused?
3. How did deliberation cycles change the decision, and why did it stop?
4. Why were the alternative actions not selected?
5. Did the compliance fast-path (DCCL) merely *match*, or was a draft actually *reused*?
6. Was a final provider candidate generated?
7. Did final revalidation pass, block, fail closed, or get skipped?
8. How did conversation state evolve from the previous turn?
9. Where is the canonical raw trace evidence?

## Design direction

- answer-first, then cause, then evidence;
- causal, not merely chronological;
- progressive disclosure — never deletion — of raw detail;
- human-readable language *beside* canonical codes, never replacing them;
- explicit provenance and source labels;
- accessible and responsive;
- low cognitive load without hiding audit evidence.

## Out of scope

- changing governance behaviour, thresholds, or policy semantics;
- changing the observability schema for visual convenience;
- replacing the FastAPI/Jinja stack;
- decorative redesign unrelated to comprehension.

## The distinction the UI most often gets wrong

**Pre-delivery governance** (what the deliberation decided) is not
**authoritative delivery** (what the proxy finalised and returned). A speculative
draft is not a delivered answer. A DCCL match is not a reuse. When the two differ,
the delivered result wins the visual hierarchy and the difference must be visible.
