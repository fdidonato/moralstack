"""
Call logger for MoralStack CLI.
"""

from typing import Any

from .loader import print_colored

CallEntry = dict[str, Any]


class CallLogger:
    """Logger to track all LLM calls."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.calls: list[CallEntry] = []
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
    ) -> None:
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

    def _print_call(self, call: CallEntry) -> None:
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
