"""
Deliberation trace visualizer for MoralStack CLI.
"""

from .models import DeliberationTrace, PhaseResult, PhaseType


def _phase_icon(phase: PhaseType) -> str:
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


class DeliberationVisualizer:
    """Generates visualizations of the deliberative process."""

    # ANSI colors
    COLORS = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "bg_red": "\033[41m",
        "bg_green": "\033[42m",
        "bg_yellow": "\033[43m",
        "bg_blue": "\033[44m",
    }

    # Symbols for the diagram
    SYMBOLS = {
        "start": "◉",
        "end": "◎",
        "phase": "●",
        "decision": "◆",
        "success": "✓",
        "failure": "✗",
        "warning": "⚠",
        "arrow_down": "↓",
        "arrow_right": "→",
        "corner": "└",
        "tee": "├",
        "line_v": "│",
        "line_h": "─",
        "box_tl": "┌",
        "box_tr": "┐",
        "box_bl": "└",
        "box_br": "┘",
    }

    def __init__(self, use_colors: bool = True):
        self.use_colors = use_colors

    def _c(self, text: str, color: str) -> str:
        """Applies color to text."""
        if not self.use_colors:
            return text
        return f"{self.COLORS.get(color, '')}{text}{self.COLORS['reset']}"

    def _box(self, title: str, content: str, width: int = 78, color: str = "cyan") -> str:
        """Creates a box with borders."""
        lines = [
            self._c(f"╔{'═' * (width - 2)}╗", color),
            self._c(f"║ {title.center(width - 4)} ║", color),
            self._c(f"╠{'═' * (width - 2)}╣", color),
        ]

        for line in content.split("\n"):
            # Truncate if too long
            if len(line) > width - 4:
                line = line[: width - 7] + "..."
            lines.append(self._c(f"║ {line.ljust(width - 4)} ║", color))

        lines.append(self._c(f"╚{'═' * (width - 2)}╝", color))
        return "\n".join(lines)

    def _header(self, title: str, char: str = "═", width: int = 80) -> str:
        """Creates a header."""
        padding = (width - len(title) - 4) // 2
        return self._c(f"\n{char * padding} {title} {char * padding}\n", "cyan")

    def _status_icon(self, success: bool, has_warnings: bool = False) -> str:
        """Returns the status icon."""
        if not success:
            return self._c("✗", "red")
        if has_warnings:
            return self._c("⚠", "yellow")
        return self._c("✓", "green")

    def render_flow_diagram(self, trace: DeliberationTrace) -> str:
        """Generates the process flow diagram."""
        # Header
        lines = [
            self._header("DELIBERATIVE PROCESS MAP", "═"),
            f"\n{self._c('📋 REQUEST ID:', 'bold')} {trace.request_id}",
            f"{self._c('⏱️  TOTAL DURATION:', 'bold')} {trace.total_duration_ms():.0f}ms",
        ]
        # General info
        path_color = "green" if trace.path == "fast" else "yellow"
        lines.append(f"{self._c('🛤️  PATH:', 'bold')} {self._c(trace.path.upper(), path_color)}")
        lines.append(f"{self._c('🔄 CYCLES:', 'bold')} {trace.total_cycles}")
        conv_text = "YES" if trace.converged else "NO"
        conv_color = "green" if trace.converged else "yellow"
        lines.append(f"{self._c('✅ CONVERGENCE:', 'bold')} {self._c(conv_text, conv_color)}")

        # Vertical diagram
        lines.append("\n" + self._c("┌" + "─" * 76 + "┐", "blue"))
        lines.append(self._c("│", "blue") + " " + self._c("PROCESSING FLOW", "bold").center(84) + self._c("│", "blue"))
        lines.append(self._c("└" + "─" * 76 + "┘", "blue"))

        # START
        lines.append(f"\n    {self._c('◉', 'green')} {self._c('START', 'bold')}: Request received")
        lines.append(f"    {self._c('│', 'blue')}")

        # Group by cycle
        phases_by_cycle = trace.get_phases_by_cycle()

        for cycle_num in sorted(phases_by_cycle.keys()):
            cycle_phases = phases_by_cycle[cycle_num]

            if cycle_num > 0:
                lines.append(f"    {self._c('│', 'blue')}")
                box_top = "╔" + "═" * 68 + "╗"
                lines.append(f"    {self._c(box_top, 'yellow')}")
                cycle_title = self._c(f"DELIBERATIVE CYCLE #{cycle_num}", "bold").center(76)
                row = f"{self._c('║', 'yellow')} {cycle_title} {self._c('║', 'yellow')}"
                lines.append(f"    {row}")
                box_bottom = "╚" + "═" * 68 + "╝"
                lines.append(f"    {self._c(box_bottom, 'yellow')}")

            for phase in cycle_phases:
                lines.append(f"    {self._c('│', 'blue')}")
                lines.extend(self._render_phase_node(phase))

        # END
        lines.append(f"    {self._c('│', 'blue')}")
        end_label = f"{self._c('◎', 'green')} {self._c('END', 'bold')}: {trace.response_type}"
        lines.append(f"    {end_label}")

        return "\n".join(lines)

    def _render_phase_node(self, phase: PhaseResult) -> list[str]:
        """Renders a phase node."""
        lines = []

        icon = _phase_icon(phase.phase)
        status = self._status_icon(phase.success, len(phase.warnings) > 0)

        # Phase name
        phase_name = phase.phase.value.replace("_", " ").upper()
        duration = f"{phase.duration_ms:.0f}ms"

        # Phase box
        lines.append(f"    {self._c('├', 'blue')}{'─' * 3}{self._c('┬', 'blue')}{'─' * 68}")
        phase_line = (
            f"    {self._c('│', 'blue')}   {self._c('│', 'blue')} "
            f"{icon} {self._c(phase_name, 'bold')} {status} [{duration}]"
        )
        lines.append(phase_line)

        # Input summary (truncated)
        prefix = f"    {self._c('│', 'blue')}   {self._c('│', 'blue')}   "
        if phase.input_summary:
            input_text = phase.input_summary[:60] + "..." if len(phase.input_summary) > 60 else phase.input_summary
            lines.append(prefix + f"{self._c('INPUT:', 'dim')} {input_text}")

        # Output summary (truncated)
        if phase.output_summary:
            output_text = phase.output_summary[:60] + "..." if len(phase.output_summary) > 60 else phase.output_summary
            lines.append(prefix + f"{self._c('OUTPUT:', 'dim')} {output_text}")

        # Decision and reason
        if phase.decision:
            decision_color = (
                "green"
                if phase.decision in ["PROCEED", "CONVERGED", "APPROVED"]
                else "yellow" if phase.decision == "REVISE" else "red"
            )
            dec_part = self._c("DECISION:", "bold") + " " + self._c(phase.decision, decision_color)
            lines.append(prefix + dec_part)

        if phase.decision_reason:
            lines.append(prefix + f"{self._c('REASON:', 'dim')} {phase.decision_reason}")

        # Errors
        for error in phase.errors:
            lines.append(prefix + f"{self._c('❌ ERROR:', 'red')} {error}")

        # Warning
        for warning in phase.warnings:
            lines.append(prefix + f"{self._c('⚠️  WARNING:', 'yellow')} {warning}")

        lines.append(f"    {self._c('│', 'blue')}   {self._c('└', 'blue')}{'─' * 68}")

        return lines

    def render_detailed_analysis(self, trace: DeliberationTrace) -> str:
        """Generates a detailed analysis of each phase."""
        lines = [self._header("DETAILED PHASE ANALYSIS", "═")]

        for i, phase in enumerate(trace.phases, 1):
            lines.append(self._render_phase_details(phase, i))

        return "\n".join(lines)

    def _render_phase_details(self, phase: PhaseResult, index: int) -> str:
        """Renders the details of a phase."""
        lines = []

        icon = _phase_icon(phase.phase)
        status = self._status_icon(phase.success, len(phase.warnings) > 0)
        phase_name = phase.phase.value.replace("_", " ").upper()

        # Phase header
        color = "green" if phase.success else "red"
        lines.append(f"\n{self._c('▓' * 80, color)}")
        lines.append(f"{self._c(f'▓ PHASE {index}: {icon} {phase_name}', 'bold')} {status}")
        lines.append(f"{self._c(f'▓ Cycle: {phase.cycle} | Duration: {phase.duration_ms:.0f}ms', 'dim')}")
        lines.append(f"{self._c('▓' * 80, color)}")

        # Input
        lines.append(f"\n{self._c('📥 INPUT:', 'cyan')}")
        lines.append(f"   {phase.input_summary}")

        # Output
        lines.append(f"\n{self._c('📤 OUTPUT:', 'cyan')}")
        lines.append(f"   {phase.output_summary}")

        # Specific details
        if phase.details:
            lines.append(f"\n{self._c('📊 DETAILS:', 'cyan')}")
            for key, value in phase.details.items():
                if isinstance(value, list):
                    lines.append(f"   {self._c(key + ':', 'bold')}")
                    for item in value[:5]:  # Max 5 items
                        lines.append(f"     • {item}")
                    if len(value) > 5:
                        lines.append(f"     ... and {len(value) - 5} more")
                elif isinstance(value, dict):
                    lines.append(f"   {self._c(key + ':', 'bold')}")
                    for k, v in list(value.items())[:5]:
                        lines.append(f"     {k}: {v}")
                else:
                    lines.append(f"   {self._c(key + ':', 'bold')} {value}")

        # Decision
        if phase.decision:
            lines.append(f"\n{self._c('🎯 DECISION:', 'cyan')}")
            decision_color = (
                "green"
                if phase.decision in ["PROCEED", "CONVERGED", "APPROVED"]
                else "yellow" if phase.decision == "REVISE" else "red"
            )
            lines.append(f"   {self._c(phase.decision, decision_color)}")

        if phase.decision_reason:
            lines.append(f"\n{self._c('💡 REASONING:', 'cyan')}")
            lines.append(f"   {phase.decision_reason}")

        # Errors and warnings
        if phase.errors:
            lines.append(f"\n{self._c('❌ ERRORS:', 'red')}")
            for error in phase.errors:
                lines.append(f"   • {error}")

        if phase.warnings:
            lines.append(f"\n{self._c('⚠️  WARNINGS:', 'yellow')}")
            for warning in phase.warnings:
                lines.append(f"   • {warning}")

        return "\n".join(lines)

    def render_summary(self, trace: DeliberationTrace) -> str:
        """Generates a process summary."""
        lines = [self._header("DELIBERATIVE PROCESS SUMMARY", "═")]

        # General statistics
        total_phases = len(trace.phases)
        successful_phases = sum(1 for p in trace.phases if p.success)
        failed_phases = total_phases - successful_phases
        total_errors = sum(len(p.errors) for p in trace.phases)
        total_warnings = sum(len(p.warnings) for p in trace.phases)

        lines.append(f"\n{self._c('📈 STATISTICS:', 'bold')}")
        lines.append(f"   Total phases:       {total_phases}")
        lines.append(f"   Completed phases:  {self._c(str(successful_phases), 'green')}")
        failed_str = self._c(str(failed_phases), "red") if failed_phases > 0 else "0"
        lines.append(f"   Failed phases:     {failed_str}")
        errors_str = self._c(str(total_errors), "red") if total_errors > 0 else "0"
        lines.append(f"   Total errors:      {errors_str}")
        warnings_str = self._c(str(total_warnings), "yellow") if total_warnings > 0 else "0"
        lines.append(f"   Warnings:          {warnings_str}")
        lines.append(f"   Deliberative cycles: {trace.total_cycles}")
        lines.append(f"   Total time:         {trace.total_duration_ms():.0f}ms")

        # Time breakdown per phase
        lines.append(f"\n{self._c('⏱️  TIME BREAKDOWN:', 'bold')}")
        phase_times = {}
        for phase in trace.phases:
            phase_name = phase.phase.value
            if phase_name not in phase_times:
                phase_times[phase_name] = 0.0
            phase_times[phase_name] += phase.duration_ms

        for phase_name, duration in sorted(phase_times.items(), key=lambda x: -x[1]):
            bar_len = int((duration / max(trace.total_duration_ms(), 1)) * 40)
            bar = "█" * bar_len
            lines.append(f"   {phase_name:20} {bar} {duration:.0f}ms")

        # Violated principles
        if trace.triggered_principles:
            lines.append(f"\n{self._c('⚖️  PRINCIPLES VIOLATED:', 'bold')}")
            for principle in trace.triggered_principles[:10]:
                lines.append(f"   • {principle}")

        # Global errors
        if trace.errors:
            lines.append(f"\n{self._c('❌ GLOBAL ERRORS:', 'red')}")
            for error in trace.errors:
                lines.append(f"   • {error}")

        # Final evaluation
        lines.append(f"\n{self._c('📋 FINAL ASSESSMENT:', 'bold')}")
        lines.append(f"   Response type:  {trace.response_type}")
        conv_str = self._c("Yes", "green") if trace.converged else self._c("No", "yellow")
        lines.append(f"   Convergence:    {conv_str}")
        lines.append(f"   Path:           {trace.path}")

        if trace.path_reason:
            lines.append(f"   Reason:         {trace.path_reason}")

        return "\n".join(lines)
