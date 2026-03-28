"""
Modular Markdown renderer for MoralStack reports.

Section functions take RequestReport (or parts) and return markdown strings.
Single entry point: render_request_report(report) for request/deliberation reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moralstack.reports.model import RequestReport


def _risk_indicator(score: float) -> str:
    if score < 0.3:
        return "🟢 Low"
    if score < 0.5:
        return "🟡 Moderate"
    if score < 0.7:
        return "🟠 Elevated"
    return "🔴 High"


def _cycles_indicator(cycles: int) -> str:
    if cycles <= 1:
        return "⚡ Minimal"
    if cycles <= 2:
        return "🔄 Standard"
    return "🔁 Extended"


def _phase_icon(phase_type: str) -> str:
    """Returns icon for phase type (string)."""
    key = (phase_type or "").lower().replace(" ", "_").split("/")[0].strip()
    icons = {
        "risk_estimation": "🎯",
        "path_decision": "🔀",
        "generation": "✍️",
        "revision": "📝",
        "critic": "⚖️",
        "simulation": "🔮",
        "hindsight": "🔍",
        "perspectives": "👥",
        "convergence_check": "🎯",
        "assembly": "📦",
    }
    return icons.get(key, "●")


def render_request_header(report: "RequestReport") -> str:
    converged_str = "✅ Yes" if report.converged else "❌ No"
    rationale_block = ""
    if getattr(report, "risk_rationale", ""):
        rationale_block = f"""
### 🎯 Risk Rationale

> {report.risk_rationale}
"""
    calibration_guard_block = ""
    if getattr(report, "calibration_guard_info", ""):
        calibration_guard_block = f"""
### ⚡ Calibration Guard Triggered

> **The automatic calibration guard intercepted and capped the risk estimator output.**
> This happens when the intent estimator confirms a benign/non-operational request type
> (factual_query, sensitive_topic, ethical_dilemma, support_request, or crisis_support)
> with no harm intent and no requested instructions, but the operational estimator
> returned over-elevated risk metrics based on topic signals alone.

| Guard Detail | Value |
|--------------|-------|
| **Guard Active** | ✅ Yes |
| **Action** | Risk metrics capped to prevent over-refusal |
| **Details** | {report.calibration_guard_info} |
"""
    return f"""# 🧠 MoralStack Deliberation Report

> **Request ID**: `{report.request_id}`
> **Generated**: {report.generated_at}
> **Framework Version**: 0.1.0

---

## 📋 Request Information

| Property | Value |
|----------|-------|
| **Request ID** | `{report.request_id}` |
| **Domain** | {report.domain or '—'} |
| **Processing Path** | {report.path_badge} |
| **Risk Category** | `{report.risk_category}` |
| **Risk Score** | {report.risk_score:.3f} |
| **Total Cycles** | {report.total_cycles} |
| **Converged** | {converged_str} |
| **Response Type** | `{report.response_type}` |
| **Total Duration** | `{report.total_duration_ms:.0f}ms` |

### 💬 Original Prompt

```
{report.prompt}
```
{rationale_block}{calibration_guard_block}"""


def render_orchestrator_observability(report: "RequestReport") -> str:
    """Path routing / governance transparency (from debug events + FINAL trace)."""
    obs = getattr(report, "orchestrator_observability", None)
    if not obs or not obs.get("has_routing_data"):
        return ""
    from moralstack.reports.orchestrator_observability import render_orchestrator_observability_markdown

    return render_orchestrator_observability_markdown(obs)


def render_executive_summary(report: "RequestReport") -> str:
    decision_block = ""
    if report.decision_reason:
        decision_block = f"### Decision reason (from trace)\n{report.decision_reason}"
    risk_ind = _risk_indicator(report.risk_score)
    cycles_ind = _cycles_indicator(report.total_cycles)
    conv_cell = "✅" if report.converged else "⚠️"
    return f"""---

## 📊 Executive Summary

### Status
{report.status}
{decision_block}

### Final Response

> **Note**: This is the COMPLETE, untruncated response.

```
{report.response_content}
```

### Key Metrics

| Metric | Value | Status |
|--------|-------|--------|
| Risk Score | {report.risk_score:.3f} | {risk_ind} |
| Deliberation Cycles | {report.total_cycles} | {cycles_ind} |
| Convergence | {"Yes" if report.converged else "No"} | {conv_cell} |
"""


def render_journey_map(report: "RequestReport") -> str:
    lines = [
        "---",
        "",
        "## 🚂 Deliberation Journey",
        "",
        "> This section visualizes the deliberation process as a train journey through stations.",
        "> Each station represents a processing phase.",
        "",
        "```",
        "╔══════════════════════════════════════════════════════════════════════════════╗",
        "║                         🚂 MORALSTACK EXPRESS 🚂                              ║",
        "║                    Deliberation Journey Timeline                              ║",
        "╚══════════════════════════════════════════════════════════════════════════════╝",
        "",
    ]
    prompt_preview = (report.prompt[:50] + "...") if len(report.prompt) > 50 else report.prompt
    lines.append("    🚉 DEPARTURE")
    lines.append("    ║")
    lines.append(f'    ║  📋 Request: "{prompt_preview}"')
    lines.append("    ║")
    box_line = "    ╠" + "═" * 68
    for cycle_num, phases in sorted(report.phases_by_cycle):
        if cycle_num == 0:
            lines.append(box_line)
            lines.append("    ║  🎯 RISK ASSESSMENT STATION")
            lines.append(box_line)
        else:
            lines.append("    ║")
            lines.append(box_line)
            lines.append(f"    ║  🔄 DELIBERATION CYCLE #{cycle_num}")
            lines.append(box_line)
        for pi in phases:
            icon = _phase_icon(pi.phase_type)
            status = "✅" if pi.success else "❌"
            lines.append("    ║")
            lines.append(f"    ╠──🚏 {icon} {pi.phase_type.upper()} {status}")
            lines.append(f"    ║     └─ Duration: {pi.duration_ms:.0f}ms")
            if pi.decision:
                lines.append(f"    ║     └─ Decision: {pi.decision}")
            for key, value in list(pi.details.items())[:3]:
                lines.append(f"    ║     └─ {key}: {value}")
    lines.append("    ║")
    lines.append("    ╠══════════════════════════════════════════════════════════════════════════")
    lines.append(f"    ║  🏁 ARRIVAL: {report.response_type.upper()}")
    lines.append("    ╠══════════════════════════════════════════════════════════════════════════")
    lines.append("    ║")
    lines.append("    🚉 DESTINATION REACHED")
    lines.append("")
    total_phases = sum(len(p) for _, p in report.phases_by_cycle)
    lines.append(f"    Total Journey Time: {report.total_duration_ms:.0f}ms")
    lines.append(f"    Stations Visited: {total_phases}")
    lines.append(f"    Cycles Completed: {report.total_cycles}")
    lines.append("```")
    return "\n".join(lines)


def render_detailed_phases(report: "RequestReport") -> str:
    lines = [
        "---",
        "",
        "## 📑 Detailed Phase Analysis",
        "",
        "> **Note**: All prompts and responses are shown in FULL, without truncation.",
        "",
    ]
    retry_count = sum(
        1
        for c in (report.call_log or [])
        if (getattr(c, "module", "") or "").lower() == "simulator" and "retry" in (getattr(c, "action", "") or "").lower()
    )
    if retry_count > 0:
        lines.append(f"⚠️ **Note**: The simulator required {retry_count} retry(ies) due to JSON parse failures.")
        lines.append("")
    for cycle_num, phases in sorted(report.phases_by_cycle):
        if cycle_num == 0:
            lines.append("### 🎯 Initial Assessment (Cycle 0)")
        else:
            lines.append(f"### 🔄 Deliberation Cycle {cycle_num}")
        lines.append("")
        for pi in phases:
            icon = _phase_icon(pi.phase_type)
            status_icon = "✅" if pi.success else "❌"
            title = pi.phase_type.replace("_", " ").title()
            lines.append(f"#### {icon} {title} {status_icon}")
            lines.append("")
            lines.append("| Property | Value |")
            lines.append("|----------|-------|")
            lines.append(f"| **Duration** | `{pi.duration_ms:.0f}ms` |")
            lines.append(f"| **Success** | {status_icon} |")
            if pi.decision:
                lines.append(f"| **Decision** | `{pi.decision}` |")
            if pi.decision_reason:
                lines.append(f"| **Reason** | {pi.decision_reason} |")
            lines.append("")
            if pi.details:
                lines.append("**Details:**")
                lines.append("")
                for key, value in pi.details.items():
                    lines.append(f"- **{key}**: `{value}`")
                lines.append("")
            for label, content in [
                ("System Prompt (Complete)", getattr(pi, "system_prompt", "") or ""),
                ("Input (Complete)", pi.full_input or pi.input_summary),
                ("Output (Complete)", pi.full_output or pi.output_summary),
            ]:
                if content:
                    lines.append(f"**{label}:**")
                    lines.append("")
                    lines.append("```")
                    lines.append(content)
                    lines.append("```")
                    lines.append("")
            if pi.errors:
                lines.append("**⚠️ Errors:**")
                lines.append("")
                for err in pi.errors:
                    lines.append(f"- {err}")
                lines.append("")
            if pi.warnings:
                lines.append("**⚡ Warnings:**")
                lines.append("")
                for w in pi.warnings:
                    lines.append(f"- {w}")
                lines.append("")
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


def render_metrics_dashboard(report: "RequestReport") -> str:
    width = 50
    filled = max(0, min(width, int((report.risk_score - 0) / 1 * width)))
    gauge = "█" * filled + "░" * (width - filled)
    if report.risk_score < 0.3:
        marker = "🟢"
    elif report.risk_score < 0.5:
        marker = "🟡"
    elif report.risk_score < 0.7:
        marker = "🟠"
    else:
        marker = "🔴"
    lines = [
        "## 📈 Metrics Dashboard",
        "",
        "### Risk Score Distribution",
        "",
        "```",
        f"┌{'─' * (width + 2)}┐",
        f"│ {gauge} │ {marker}",
        f"└{'─' * (width + 2)}┘",
        f"  Risk Score: {report.risk_score:.3f}",
        f"  0.0 {'─' * 20} 0.5 {'─' * 20} 1.0",
        "```",
        "",
    ]
    if report.hindsight_score is not None:
        h_filled = max(0, min(width, int((report.hindsight_score - 0) / 1 * width)))
        h_gauge = "█" * h_filled + "░" * (width - h_filled)
        lines.append("### Hindsight Score")
        lines.append("")
        lines.append("```")
        lines.append(f"┌{'─' * (width + 2)}┐")
        lines.append(f"│ {h_gauge} │")
        lines.append(f"└{'─' * (width + 2)}┘")
        lines.append(f"  Hindsight: {report.hindsight_score:.3f}")
        lines.append("```")
        lines.append("")
    if report.phase_durations:
        lines.append("### Phase Duration Breakdown")
        lines.append("")
        lines.append("```")
        lines.append("Phase Duration Chart (ms)")
        lines.append("─" * 60)
        max_d = max(report.phase_durations.values()) or 1
        for name, dur in sorted(report.phase_durations.items(), key=lambda x: -x[1]):
            bar_w = int(dur / max_d * 40) if max_d > 0 else 0
            bar = "█" * bar_w
            lines.append(f"{name:20} │{bar} {dur:.0f}ms")
        lines.append("─" * 60)
        lines.append("```")
        lines.append("")
    lines.append("### Module Activity Summary")
    lines.append("")
    lines.append("| Module | Calls | Total Time | Avg Time |")
    lines.append("|--------|-------|------------|----------|")
    for mod, stats in report.module_stats.items():
        c = stats.get("calls", 0)
        t = stats.get("total_ms", 0)
        a = stats.get("avg_ms", 0)
        lines.append(f"| {mod} | {c} | {t:.0f}ms | {a:.0f}ms |")
    lines.append("")
    return "\n".join(lines)


def render_policy_overlay(report: "RequestReport") -> str:
    if not report.policy_overlay:
        return ""
    o = report.policy_overlay
    lines = ["---", "", "## 🔧 Policy Overlay & Meta Analysis (Debug)", ""]
    if o.get("caveat_type") is not None or o.get("principle_ids") is not None:
        lines.append(f"- **caveat_type**: `{o.get('caveat_type', '')}`")
        lines.append(f"- **principle_ids**: {o.get('principle_ids', [])}")
        lines.append("")
    if o.get("stop_reason") is not None or o.get("hindsight_score") is not None:
        lines.append(f"- **stop_reason**: `{o.get('stop_reason', '')}`")
        lines.append(f"- **hindsight_score**: {o.get('hindsight_score', 0):.3f}")
        lines.append("")
    return "\n".join(lines)


def render_revision_history(report: "RequestReport") -> str:
    lines = [
        "---",
        "",
        "## 📝 Complete Revision History",
        "",
        "> **Note**: All draft texts are shown in FULL, without any truncation.",
        "",
    ]
    if not report.revision_history:
        lines.append("*No revision history available.*")
        return "\n".join(lines)
    for i, rev in enumerate(report.revision_history):
        if rev.is_initial:
            lines.append(f"### 📄 Initial Draft (Cycle {rev.cycle})")
        else:
            lines.append(f"### 📝 Revision #{i} (Cycle {rev.cycle})")
        lines.append("")
        if not rev.is_initial and rev.guidance_used:
            lines.append("#### 📋 Guidance Used for This Revision")
            lines.append("")
            lines.append("```")
            lines.append(rev.guidance_used)
            lines.append("```")
            lines.append("")
        lines.append("#### 📄 Draft Text (Complete)")
        lines.append("")
        lines.append("```")
        lines.append(rev.draft_text)
        lines.append("```")
        lines.append("")
        lines.append(f"**Character Count**: {len(rev.draft_text)}")
        lines.append("")
        if i < len(report.revision_history) - 1:
            lines.append("```")
            lines.append("                              ⬇️")
            lines.append("                         REVISION")
            lines.append("                              ⬇️")
            lines.append("```")
            lines.append("")
    if len(report.revision_history) > 1:
        initial = report.revision_history[0]
        final = report.revision_history[-1]
        diff = len(final.draft_text) - len(initial.draft_text)
        lines.append("### 📊 Evolution Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Versions | {len(report.revision_history)} |")
        lines.append(f"| Initial Length | {len(initial.draft_text)} chars |")
        lines.append(f"| Final Length | {len(final.draft_text)} chars |")
        lines.append(f"| Change | {'+' if diff >= 0 else ''}{diff} chars |")
        lines.append("")
    if getattr(report, "soft_revision_applied", False):
        lines.append("### 🔄 Soft Revision Applied")
        lines.append("")
        lines.append("A single rewrite pass was applied after convergence to incorporate soft suggestions.")
        lines.append("")
        if getattr(report, "soft_revision_guidance_used", "").strip():
            lines.append("**Guidance used:**")
            lines.append("")
            lines.append("```")
            lines.append(report.soft_revision_guidance_used.strip())
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def render_full_call_log(report: "RequestReport") -> str:
    lines = [
        "---",
        "",
        "## 📞 Complete LLM Call Log",
        "",
        "> **Note**: All prompts and responses are shown in FULL, without truncation.",
        "> This section provides complete transparency into all LLM interactions.",
        "",
    ]
    if not report.call_log:
        lines.append("*No calls logged.*")
        return "\n".join(lines)
    for c in report.call_log:
        lines.append(f"### 📞 Call #{c.call_id}: {c.module} → {c.action}")
        lines.append("")
        lines.append(f"**Duration**: `{c.duration_ms:.0f}ms`")
        lines.append("")
        lines.append("#### 📤 Prompt (Complete)")
        lines.append("")
        lines.append("```")
        lines.append(c.full_prompt)
        lines.append("```")
        lines.append("")
        lines.append("#### 📥 Response (Complete)")
        lines.append("")
        lines.append("```")
        lines.append(c.full_response)
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def render_request_footer(report: "RequestReport") -> str:
    total_phases = sum(len(p) for _, p in report.phases_by_cycle)
    return f"""---

## 📋 Report Metadata

| Property | Value |
|----------|-------|
| **Report Generated** | {report.generated_at} |
| **Request ID** | `{report.request_id}` |
| **Total Phases** | {total_phases} |
| **Total Cycles** | {report.total_cycles} |
| **Processing Time** | `{report.total_duration_ms:.0f}ms` |

---

<div align="center">

**Generated by MoralStack v0.1.0**

*Auditable AI governance, by design*

</div>
"""


def render_request_report(report: "RequestReport", *, models_used_section: str = "") -> str:
    """Render full request/deliberation report markdown from RequestReport.

    Optional ``models_used_section``: markdown block (e.g. '### Models used' table) inserted
    after the request header and before the executive summary (used by UI export).
    """
    orch = render_orchestrator_observability(report)
    sections = [
        render_request_header(report),
    ]
    if models_used_section and models_used_section.strip():
        sections.append(models_used_section.strip())
    sections.append(render_executive_summary(report))
    if orch:
        sections.append(orch)
    sections.extend(
        [
            render_journey_map(report),
            render_detailed_phases(report),
            render_metrics_dashboard(report),
        ]
    )
    overlay = render_policy_overlay(report)
    if overlay:
        sections.append(overlay)
    sections.append(render_revision_history(report))
    sections.append(render_full_call_log(report))
    sections.append(render_request_footer(report))
    return "\n\n".join(sections)
