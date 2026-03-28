"""
Call logger and Markdown report generator for MoralStack CLI.
"""

from pathlib import Path
from typing import Any

from .loader import print_colored
from .models import DeliberationTrace, PhaseResult, PhaseType


class CallLogger:
    """Logger to track all LLM calls."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.calls: list[dict] = []
        self.call_counter = 0

    def log_call(
        self,
        module: str,
        action: str,
        prompt: str,
        response: str = "",
        duration_ms: float = 0.0,
        *,
        model: str | None = None,
    ):
        """Logs an LLM call."""
        self.call_counter += 1

        # Limits for console display (not for storage)
        prompt_limit = 500 if module in ["simulator", "perspectives", "hindsight", "critic"] else 300
        response_limit = 800 if module in ["simulator", "perspectives", "hindsight", "critic"] else 500

        call_info = {
            "id": self.call_counter,
            "module": module,
            "action": action,
            "prompt": prompt[:prompt_limit] + "..." if len(prompt) > prompt_limit else prompt,
            "response": (response[:response_limit] + "..." if len(response) > response_limit else response),
            "duration_ms": duration_ms,
            "model": model,
            # Always save complete data for revision history
            "full_prompt": prompt,
            "full_response": response,
        }
        self.calls.append(call_info)

        # Immediate print only if verbose
        if self.verbose:
            self._print_call(call_info)

    def _print_call(self, call: dict):
        """Prints a formatted call."""
        # Different color for errors
        border_color = "red" if "(ERROR)" in call["action"] else "cyan"
        header_color = "red" if "(ERROR)" in call["action"] else "yellow"

        print_colored(f"\n{'=' * 80}", border_color)
        print_colored(f"📞 LLM CALL #{call['id']}: {call['module']} → {call['action']}", header_color)
        print_colored(f"{'=' * 80}", border_color)

        if call.get("model"):
            print_colored(f"🤖 Model: {call['model']}", "cyan")

        if call["prompt"]:
            print_colored("\n📤 PROMPT:", "blue")
            # Show full prompt for important modules
            if call["module"] in [
                "simulator",
                "perspectives",
                "hindsight",
                "critic",
                "orchestrator",
            ]:
                print(f"{call['prompt']}")
            else:
                print(f"{call['prompt']}")

        if call["response"]:
            print_colored("\n📥 RESPONSE:", "green")
            # For detailed responses, show better formatting
            if call["module"] in ["simulator", "perspectives", "hindsight", "critic"]:
                # Format with indentation for readability
                lines = call["response"].split("\n")
                for line in lines:
                    if line.strip():
                        print(f"  {line}")
            else:
                print(f"{call['response']}")

        if call["duration_ms"] > 0:
            duration_sec = call["duration_ms"] / 1000
            print_colored(f"\n⏱️  Duration: {call['duration_ms']:.0f}ms ({duration_sec:.1f}s)", "magenta")

        print_colored(f"{'=' * 80}\n", border_color)

    def get_summary(self) -> str:
        """Returns a summary of the calls."""
        if not self.calls:
            return ""

        total_time = sum(c["duration_ms"] for c in self.calls)
        by_module: dict[str, int] = {}
        for c in self.calls:
            by_module[c["module"]] = by_module.get(c["module"], 0) + 1

        summary = "\n📊 CALL SUMMARY:\n"
        summary += f"   Total Calls: {len(self.calls)}\n"
        summary += f"   Total time: {total_time:.0f}ms ({total_time / 1000:.1f}s)\n"
        summary += f"   By module: {', '.join(f'{k}: {v}' for k, v in by_module.items())}\n"
        return summary


class MarkdownReportGenerator:
    """
    Generates complete Markdown reports of the deliberative process.

    Features:
    - Full text never truncated
    - "Train journey" visualization with stations
    - ASCII charts for metrics
    - Complete revision history
    """

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(self, trace: DeliberationTrace, call_logger: CallLogger, result: Any, prompt: str) -> str:
        """
        Renders the complete Markdown report. Does NOT write to file.

        Uses shared model + renderer (RequestReport, render_request_report).
        Returns:
            Markdown content string
        """
        from moralstack.reports.model import request_report_from_cli
        from moralstack.reports.renderer_markdown import render_request_report

        report = request_report_from_cli(trace, call_logger, result, prompt)
        return render_request_report(report)

    def _build_report(self, trace: DeliberationTrace, call_logger: CallLogger, result: Any, prompt: str) -> str:
        """Builds the report content."""
        sections = []

        # Header
        sections.append(self._header(trace, prompt))

        # Executive Summary
        sections.append(self._executive_summary(trace, result))

        # Journey Map (train-style)
        sections.append(self._journey_map(trace))

        # Detailed Phases
        sections.append(self._detailed_phases(trace, call_logger))

        # Metrics Dashboard
        sections.append(self._metrics_dashboard(trace, result))

        # Policy Overlay / Meta Analysis (debug, when present)
        overlay_section = self._policy_overlay_section(result)
        if overlay_section:
            sections.append(overlay_section)

        # Revision History (complete)
        sections.append(self._revision_history(trace))

        # Full Call Log (complete)
        sections.append(self._full_call_log(call_logger))

        # Footer
        sections.append(self._footer(trace))

        return "\n\n".join(sections)

    def _header(self, trace: DeliberationTrace, prompt: str) -> str:
        """Generates the report header."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return f"""# 🧠 MoralStack Deliberation Report

> **Request ID**: `{trace.request_id}`
> **Generated**: {timestamp}
> **Framework Version**: 0.1.0

---

## 📋 Request Information

| Property | Value |
|----------|-------|
| **Request ID** | `{trace.request_id}` |
| **Processing Path** | {self._path_badge(trace.path)} |
| **Risk Category** | `{trace.risk_category}` |
| **Risk Score** | `{trace.risk_score:.3f}` |
| **Total Cycles** | {trace.total_cycles} |
| **Converged** | {"✅ Yes" if trace.converged else "❌ No"} |
| **Response Type** | `{trace.response_type}` |
| **Total Duration** | `{trace.total_duration_ms():.0f}ms` |

### 💬 Original Prompt

```
{prompt}
```
"""

    def _path_badge(self, path: str) -> str:
        """Generates a badge for the path."""
        if path == "fast":
            return "⚡ **FAST PATH**"
        else:
            return "🧠 **DELIBERATIVE PATH**"

    def _executive_summary(self, trace: DeliberationTrace, result: Any) -> str:
        """Generates the executive summary."""
        response_content = result.response.content if result else "N/A"
        orch_trace = getattr(result, "trace", None)
        decision_reason = ""
        if orch_trace is not None:
            fa = (getattr(orch_trace, "final_action", "") or "").strip()
            dp = (getattr(orch_trace, "decision_path", "") or "").strip()
            decision_reason = f"{fa} ({dp})" if fa else ""

        # Determine status
        if trace.response_type == "full_refusal":
            status = "🚫 **REFUSED** - Request was refused due to policy violations"
        elif trace.response_type == "with_caveat":
            status = "⚠️ **APPROVED WITH CAVEATS** - Response includes disclaimers"
        elif trace.converged:
            status = "✅ **APPROVED** - All modules satisfied"
        else:
            status = "🔶 **COMPLETED** - Max cycles reached without full convergence"

        decision_reason_block = ""
        if decision_reason:
            decision_reason_block = f"### Decision reason (from trace)\n{decision_reason}"

        return f"""---

## 📊 Executive Summary

### Status
{status}
{decision_reason_block}

### Final Response

> **Note**: This is the COMPLETE, untruncated response.

```
{response_content}
```

### Key Metrics

| Metric | Value | Status |
|--------|-------|--------|
| Risk Score | {trace.risk_score:.3f} | {self._risk_indicator(trace.risk_score)} |
| Deliberation Cycles | {trace.total_cycles} | {self._cycles_indicator(trace.total_cycles)} |
| Convergence | {"Yes" if trace.converged else "No"} | {"✅" if trace.converged else "⚠️"} |
"""

    def _risk_indicator(self, score: float) -> str:
        """Visual indicator for risk."""
        if score < 0.3:
            return "🟢 Low"
        elif score < 0.5:
            return "🟡 Moderate"
        elif score < 0.7:
            return "🟠 Elevated"
        else:
            return "🔴 High"

    def _cycles_indicator(self, cycles: int) -> str:
        """Visual indicator for cycles."""
        if cycles <= 1:
            return "⚡ Minimal"
        elif cycles <= 2:
            return "🔄 Standard"
        else:
            return "🔁 Extended"

    def _policy_overlay_section(self, result: Any) -> str:
        """Optional section Policy Overlay / Meta Analysis (debug)."""
        if not result or not hasattr(result, "response"):
            return ""
        resp = result.response
        overlay = getattr(resp, "policy_overlay", None)
        meta = getattr(resp, "meta_analysis", None)
        if not overlay and not meta:
            return ""
        lines = ["---", "", "## 🔧 Policy Overlay & Meta Analysis (Debug)", ""]
        if overlay:
            lines.append(f"- **caveat_type**: `{getattr(overlay, 'caveat_type', '')}`")
            lines.append(f"- **principle_ids**: {getattr(overlay, 'principle_ids', [])}")
            lines.append("")
        if meta:
            lines.append(f"- **stop_reason**: `{getattr(meta, 'stop_reason', '')}`")
            lines.append(f"- **hindsight_score**: {getattr(meta, 'hindsight_score', 0):.3f}")
            lines.append("")
        return "\n".join(lines)

    def _journey_map(self, trace: DeliberationTrace) -> str:
        """Generates the journey map (train-style with stations)."""
        lines = []
        lines.append("---")
        lines.append("")
        lines.append("## 🚂 Deliberation Journey")
        lines.append("")
        lines.append("> This section visualizes the deliberation process as a train " "journey through stations.")
        lines.append("> Each station represents a processing phase.")
        lines.append("")

        # Group phases by cycle
        phases_by_cycle = trace.get_phases_by_cycle()

        # Generate the timeline
        lines.append("```")
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                         🚂 MORALSTACK EXPRESS 🚂                              ║")
        lines.append("║                    Deliberation Journey Timeline                              ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Start station
        lines.append("    🚉 DEPARTURE")
        lines.append("    ║")
        lines.append(f'    ║  📋 Request: "{trace.prompt[:50]}..."')
        lines.append("    ║")

        # Process each cycle
        box_line = "    ╠" + "═" * 68
        for cycle_num in sorted(phases_by_cycle.keys()):
            phases = phases_by_cycle[cycle_num]

            if cycle_num == 0:
                lines.append(box_line)
                lines.append("    ║  🎯 RISK ASSESSMENT STATION")
                lines.append(box_line)
            else:
                lines.append("    ║")
                lines.append(box_line)
                lines.append(f"    ║  🔄 DELIBERATION CYCLE #{cycle_num}")
                lines.append(box_line)

            for phase in phases:
                icon = self._get_phase_icon(phase.phase)
                status = "✅" if phase.success else "❌"
                duration = f"{phase.duration_ms:.0f}ms"

                lines.append("    ║")
                lines.append(f"    ╠──🚏 {icon} {phase.phase.value.upper()} {status}")
                lines.append(f"    ║     └─ Duration: {duration}")

                if phase.decision:
                    lines.append(f"    ║     └─ Decision: {phase.decision}")

                # Show key details
                if phase.details:
                    for key, value in list(phase.details.items())[:3]:
                        lines.append(f"    ║     └─ {key}: {value}")

        # End station
        lines.append("    ║")
        lines.append("    ╠══════════════════════════════════════════════════════════════════════════")
        lines.append(f"    ║  🏁 ARRIVAL: {trace.response_type.upper()}")
        lines.append("    ╠══════════════════════════════════════════════════════════════════════════")
        lines.append("    ║")
        lines.append("    🚉 DESTINATION REACHED")
        lines.append("")
        lines.append(f"    Total Journey Time: {trace.total_duration_ms():.0f}ms")
        lines.append(f"    Stations Visited: {len(trace.phases)}")
        lines.append(f"    Cycles Completed: {trace.total_cycles}")
        lines.append("```")

        return "\n".join(lines)

    def _get_phase_icon(self, phase: PhaseType) -> str:
        """Returns the icon for a phase."""
        icons = {
            PhaseType.RISK_ESTIMATION: "🎯",
            PhaseType.PATH_DECISION: "🔀",
            PhaseType.GENERATION: "✍️",
            PhaseType.REVISION: "📝",
            PhaseType.CRITIQUE: "⚖️",
            PhaseType.SIMULATION: "🔮",
            PhaseType.HINDSIGHT: "🔍",
            PhaseType.PERSPECTIVES: "👥",
            PhaseType.CONVERGENCE_CHECK: "🎯",
            PhaseType.ASSEMBLY: "📦",
        }
        return icons.get(phase, "●")

    def _detailed_phases(self, trace: DeliberationTrace, call_logger: CallLogger) -> str:
        """Generates full details for each phase using COMPLETE data from CallLogger."""
        lines = []
        lines.append("---")
        lines.append("")
        lines.append("## 📑 Detailed Phase Analysis")
        lines.append("")
        lines.append("> **Note**: All prompts and responses are shown in FULL, without truncation.")
        lines.append("")

        # Create a module -> calls map to retrieve complete data
        calls_by_module = self._build_calls_map(call_logger)

        phases_by_cycle = trace.get_phases_by_cycle()

        for cycle_num in sorted(phases_by_cycle.keys()):
            phases = phases_by_cycle[cycle_num]

            if cycle_num == 0:
                lines.append("### 🎯 Initial Assessment (Cycle 0)")
            else:
                lines.append(f"### 🔄 Deliberation Cycle {cycle_num}")

            lines.append("")

            for i, phase in enumerate(phases):
                icon = self._get_phase_icon(phase.phase)
                status_icon = "✅" if phase.success else "❌"

                lines.append(f"#### {icon} {phase.phase.value.replace('_', ' ').title()} {status_icon}")
                lines.append("")
                lines.append("| Property | Value |")
                lines.append("|----------|-------|")
                lines.append(f"| **Duration** | `{phase.duration_ms:.0f}ms` |")
                lines.append(f"| **Success** | {status_icon} |")

                policy_call = self._get_policy_call_dict_for_phase(phase, cycle_num, calls_by_module)
                if policy_call and policy_call.get("model"):
                    lines.append(f"| **Model** | `{policy_call['model']}` |")

                if phase.decision:
                    lines.append(f"| **Decision** | `{phase.decision}` |")
                if phase.decision_reason:
                    lines.append(f"| **Reason** | {phase.decision_reason} |")

                lines.append("")

                # Details
                if phase.details:
                    lines.append("**Details:**")
                    lines.append("")
                    for key, value in phase.details.items():
                        lines.append(f"- **{key}**: `{value}`")
                    lines.append("")

                # Retrieve COMPLETE data from CallLogger
                full_input, full_output = self._get_full_data_for_phase(phase, cycle_num, calls_by_module)

                # Input (COMPLETE from CallLogger)
                if full_input:
                    lines.append("**Input (Complete):**")
                    lines.append("")
                    lines.append("```")
                    lines.append(full_input)
                    lines.append("```")
                    lines.append("")
                elif phase.input_summary:
                    # Fallback to summary if complete data not found
                    lines.append("**Input:**")
                    lines.append("")
                    lines.append("```")
                    lines.append(phase.input_summary)
                    lines.append("```")
                    lines.append("")

                # Output (COMPLETE from CallLogger)
                if full_output:
                    lines.append("**Output (Complete):**")
                    lines.append("")
                    lines.append("```")
                    lines.append(full_output)
                    lines.append("```")
                    lines.append("")
                elif phase.output_summary:
                    # Fallback to summary if complete data not found
                    lines.append("**Output:**")
                    lines.append("")
                    lines.append("```")
                    lines.append(phase.output_summary)
                    lines.append("```")
                    lines.append("")

                # Errors
                if phase.errors:
                    lines.append("**⚠️ Errors:**")
                    lines.append("")
                    for error in phase.errors:
                        lines.append(f"- {error}")
                    lines.append("")

                # Warnings
                if phase.warnings:
                    lines.append("**⚡ Warnings:**")
                    lines.append("")
                    for warning in phase.warnings:
                        lines.append(f"- {warning}")
                    lines.append("")

                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    def _build_calls_map(self, call_logger: CallLogger) -> dict:
        """
        Builds a mapping of calls organized by module.

        Returns:
            Dict with key = module name, value = list of calls
        """
        calls_map: dict[str, list[dict[str, Any]]] = {}

        for call in call_logger.calls:
            module = call.get("module", "unknown")
            if module not in calls_map:
                calls_map[module] = []
            calls_map[module].append(call)

        return calls_map

    def _get_policy_call_dict_for_phase(
        self, phase: PhaseResult, cycle: int, calls_map: dict
    ) -> dict[str, Any] | None:
        """Returns the CallLogger entry for policy generation or revision, if any."""
        if phase.phase not in (PhaseType.GENERATION, PhaseType.REVISION):
            return None
        if "policy" not in calls_map:
            return None
        module_calls = calls_map["policy"]
        if phase.phase == PhaseType.GENERATION:
            for call in module_calls:
                if "generate" in call.get("action", "").lower():
                    return call
            return module_calls[0] if module_calls else None
        if phase.phase == PhaseType.REVISION:
            rewrite_calls = [c for c in module_calls if "rewrite" in c.get("action", "").lower()]
            if cycle >= 2 and rewrite_calls:
                idx = cycle - 2
                if 0 <= idx < len(rewrite_calls):
                    return rewrite_calls[idx]
            return None
        return None

    def _get_full_data_for_phase(self, phase: PhaseResult, cycle: int, calls_map: dict) -> tuple:
        """
        Retrieves COMPLETE (non-truncated) data for a phase from CallLogger.

        Returns:
            Tuple (full_input, full_output)
        """
        # Map phase type -> module name in CallLogger
        phase_to_module = {
            PhaseType.RISK_ESTIMATION: "risk_estimator",
            PhaseType.GENERATION: "policy",
            PhaseType.REVISION: "policy",
            PhaseType.CRITIQUE: "critic",
            PhaseType.SIMULATION: "simulator",
            PhaseType.HINDSIGHT: "hindsight",
            PhaseType.PERSPECTIVES: "perspectives",
            PhaseType.CONVERGENCE_CHECK: "orchestrator",
            PhaseType.PATH_DECISION: "orchestrator",
            PhaseType.ASSEMBLY: "orchestrator",
        }

        module_name = phase_to_module.get(phase.phase)
        if not module_name or module_name not in calls_map:
            return (None, None)

        module_calls = calls_map[module_name]

        # Find the corresponding call
        # For modules called multiple times (once per cycle),
        # we use the cycle index

        # Count how many calls were made before this cycle
        # to determine which call to use

        if phase.phase == PhaseType.RISK_ESTIMATION:
            # Risk estimation is always the first call (cycle 0)
            call_index = 0
        elif phase.phase == PhaseType.GENERATION:
            # Generation is only at cycle 1
            call_index = 0
            # Look specifically for a "generate" call
            for idx, call in enumerate(module_calls):
                if "generate" in call.get("action", "").lower():
                    call_index = idx
                    break
        elif phase.phase == PhaseType.REVISION:
            # Revision: cycle 2 = index 1, cycle 3 = index 2, etc.
            # But in CallLogger revisions come after generation
            rewrite_calls = [c for c in module_calls if "rewrite" in c.get("action", "").lower()]
            if cycle >= 2 and len(rewrite_calls) >= cycle - 1:
                # Cycle 2 -> rewrite_calls[0], Cycle 3 -> rewrite_calls[1]
                call = rewrite_calls[cycle - 2] if cycle - 2 < len(rewrite_calls) else None
                if call:
                    return (
                        call.get("full_prompt", call.get("prompt", "")),
                        call.get("full_response", call.get("response", "")),
                    )
            return (None, None)
        else:
            # For critic, simulator, hindsight, perspectives: one call per cycle
            # Cycle 1 -> index 0, Cycle 2 -> index 1, etc.
            call_index = max(0, cycle - 1)

        # Retrieve the call
        if call_index < len(module_calls):
            call = module_calls[call_index]
            full_input = call.get("full_prompt", call.get("prompt", ""))
            full_output = call.get("full_response", call.get("response", ""))
            return (full_input, full_output)

        return (None, None)

    def _metrics_dashboard(self, trace: DeliberationTrace, result: Any) -> str:
        """Generates the metrics dashboard with ASCII charts."""
        lines = []
        lines.append("## 📈 Metrics Dashboard")
        lines.append("")

        # Risk Score Gauge
        lines.append("### Risk Score Distribution")
        lines.append("")
        lines.append("```")
        lines.append(self._ascii_gauge("Risk Score", trace.risk_score, 0, 1))
        lines.append("```")
        lines.append("")

        # Hindsight Score (if available)
        if result and hasattr(result.response, "metadata"):
            hindsight = result.response.metadata.hindsight_score
            lines.append("### Hindsight Score")
            lines.append("")
            lines.append("```")
            lines.append(self._ascii_gauge("Hindsight", hindsight, 0, 1))
            lines.append("```")
            lines.append("")

        # Phase Duration Chart
        lines.append("### Phase Duration Breakdown")
        lines.append("")
        lines.append("```")
        lines.append(self._phase_duration_chart(trace))
        lines.append("```")
        lines.append("")

        # Module Activity
        lines.append("### Module Activity Summary")
        lines.append("")
        lines.append("| Module | Calls | Total Time | Avg Time |")
        lines.append("|--------|-------|------------|----------|")

        module_stats = self._calculate_module_stats(trace)
        for module, stats in module_stats.items():
            row = f"| {module} | {stats['calls']} | " f"{stats['total_ms']:.0f}ms | {stats['avg_ms']:.0f}ms |"
            lines.append(row)

        lines.append("")

        return "\n".join(lines)

    def _ascii_gauge(self, label: str, value: float, min_val: float, max_val: float) -> str:
        """Generates an ASCII gauge."""
        width = 50
        filled = int((value - min_val) / (max_val - min_val) * width)
        filled = max(0, min(width, filled))

        gauge = "█" * filled + "░" * (width - filled)

        # Determine color/marker based on thresholds
        if value < 0.3:
            marker = "🟢"
        elif value < 0.5:
            marker = "🟡"
        elif value < 0.7:
            marker = "🟠"
        else:
            marker = "🔴"

        lines = []
        lines.append(f"┌{'─' * (width + 2)}┐")
        lines.append(f"│ {gauge} │ {marker}")
        lines.append(f"└{'─' * (width + 2)}┘")
        lines.append(f"  {label}: {value:.3f}")
        lines.append(f"  0.0 {'─' * 20} 0.5 {'─' * 20} 1.0")

        return "\n".join(lines)

    def _phase_duration_chart(self, trace: DeliberationTrace) -> str:
        """Generates a duration chart per phase."""
        if not trace.phases:
            return "No phases recorded"

        # Calculate durations per phase type
        phase_durations = {}
        for phase in trace.phases:
            name = phase.phase.value
            if name not in phase_durations:
                phase_durations[name] = 0
            phase_durations[name] += phase.duration_ms

        if not phase_durations:
            return "No duration data"

        max_duration = max(phase_durations.values())
        max_bar_width = 40

        lines = []
        lines.append("Phase Duration Chart (ms)")
        lines.append("─" * 60)

        for phase_name, duration in sorted(phase_durations.items(), key=lambda x: -x[1]):
            bar_width = int(duration / max_duration * max_bar_width) if max_duration > 0 else 0
            bar = "█" * bar_width
            lines.append(f"{phase_name:20} │{bar} {duration:.0f}ms")

        lines.append("─" * 60)

        return "\n".join(lines)

    def _calculate_module_stats(self, trace: DeliberationTrace) -> dict:
        """Calculates statistics per module."""
        stats = {}

        for phase in trace.phases:
            module = phase.phase.value
            if module not in stats:
                stats[module] = {"calls": 0, "total_ms": 0, "avg_ms": 0.0}
            stats[module]["calls"] += 1
            stats[module]["total_ms"] += phase.duration_ms

        for module in stats:
            if stats[module]["calls"] > 0:
                stats[module]["avg_ms"] = stats[module]["total_ms"] / stats[module]["calls"]

        return stats

    def _revision_history(self, trace: DeliberationTrace) -> str:
        """Generates the COMPLETE revision history."""
        lines = []
        lines.append("---")
        lines.append("")
        lines.append("## 📝 Complete Revision History")
        lines.append("")
        lines.append("> **Note**: All draft texts are shown in FULL, without any truncation.")
        lines.append("")

        if not trace.draft_history:
            lines.append("*No revision history available.*")
            return "\n".join(lines)

        for i, revision in enumerate(trace.draft_history):
            if revision.is_initial:
                lines.append(f"### 📄 Initial Draft (Cycle {revision.cycle})")
            else:
                lines.append(f"### 📝 Revision #{i} (Cycle {revision.cycle})")

            lines.append("")

            # Guidance used (COMPLETE)
            if not revision.is_initial and revision.guidance_used:
                lines.append("#### 📋 Guidance Used for This Revision")
                lines.append("")
                lines.append("```")
                lines.append(revision.guidance_used)
                lines.append("```")
                lines.append("")

            # Draft text (COMPLETE)
            lines.append("#### 📄 Draft Text (Complete)")
            lines.append("")
            lines.append("```")
            lines.append(revision.draft_text)
            lines.append("```")
            lines.append("")

            # Stats
            lines.append(f"**Character Count**: {len(revision.draft_text)}")
            lines.append("")

            # Separator
            if i < len(trace.draft_history) - 1:
                lines.append("```")
                lines.append("                              ⬇️")
                lines.append("                         REVISION")
                lines.append("                              ⬇️")
                lines.append("```")
                lines.append("")

        # Evolution summary
        if len(trace.draft_history) > 1:
            initial = trace.draft_history[0]
            final = trace.draft_history[-1]
            diff = len(final.draft_text) - len(initial.draft_text)

            lines.append("### 📊 Evolution Summary")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Total Versions | {len(trace.draft_history)} |")
            lines.append(f"| Initial Length | {len(initial.draft_text)} chars |")
            lines.append(f"| Final Length | {len(final.draft_text)} chars |")
            lines.append(f"| Change | {'+' if diff >= 0 else ''}{diff} chars |")
            lines.append("")

        return "\n".join(lines)

    def _full_call_log(self, call_logger: CallLogger) -> str:
        """Generates the COMPLETE log of all calls."""
        lines = []
        lines.append("---")
        lines.append("")
        lines.append("## 📞 Complete LLM Call Log")
        lines.append("")
        lines.append("> **Note**: All prompts and responses are shown in FULL, without truncation.")
        lines.append("> This section provides complete transparency into all LLM interactions.")
        lines.append("")

        if not call_logger.calls:
            lines.append("*No calls logged.*")
            return "\n".join(lines)

        for call in call_logger.calls:
            call_id = call.get("id", "?")
            module = call.get("module", "unknown")
            action = call.get("action", "unknown")
            duration = call.get("duration_ms", 0)

            # Use COMPLETE data
            full_prompt = call.get("full_prompt", call.get("prompt", ""))
            full_response = call.get("full_response", call.get("response", ""))

            lines.append(f"### 📞 Call #{call_id}: {module} → {action}")
            lines.append("")
            lines.append(f"**Duration**: `{duration:.0f}ms`")
            lines.append("")

            # Prompt COMPLETE
            lines.append("#### 📤 Prompt (Complete)")
            lines.append("")
            lines.append("```")
            lines.append(full_prompt)
            lines.append("```")
            lines.append("")

            # Response COMPLETE
            lines.append("#### 📥 Response (Complete)")
            lines.append("")
            lines.append("```")
            lines.append(full_response)
            lines.append("```")
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _footer(self, trace: DeliberationTrace) -> str:
        """Generates the report footer."""
        from datetime import datetime

        return f"""---

## 📋 Report Metadata

| Property | Value |
|----------|-------|
| **Report Generated** | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
| **Request ID** | `{trace.request_id}` |
| **Total Phases** | {len(trace.phases)} |
| **Total Cycles** | {trace.total_cycles} |
| **Processing Time** | `{trace.total_duration_ms():.0f}ms` |

---

<div align="center">

**Generated by MoralStack v0.1.0**

*Deliberative Reasoning Runtime for LLMs*

</div>
"""
