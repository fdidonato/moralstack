#!/usr/bin/env python3
"""
MoralStack Runtime CLI - Interactive shell for MoralStack.

Loads the full runtime stack and exposes an interactive CLI:
- Policy LLM for generation
- Risk Estimator for risk classification
- Constitutional Critic for validation
- Consequence Simulator for scenario simulation
- Hindsight Evaluator for retrospective evaluation
- Perspective Ensemble for multi-perspective evaluation

Usage:
    python scripts/mstack_run.py                    # Start interactive CLI
    python scripts/mstack_run.py --mock             # Use mock modules (no API key)
    python scripts/mstack_run.py --minimal          # Policy + risk only
    python scripts/mstack_run.py --help             # Show help
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from typing import Any, Optional

from moralstack.utils.clean_start import clean_start_artifacts
from moralstack.utils.env_loader import load_env

from .loader import HELP_TEXT, ModuleLoader, print_banner, print_colored, print_separator
from .models import (
    CLIConfig,
    DecisionReason,
    DeliberationTrace,
    PhaseResult,
    PhaseType,
    TraceParseResult,
    _parse_critic_trace,
    _parse_hindsight_trace,
    _parse_perspectives_trace,
    _parse_policy_trace,
    _parse_risk_trace,
    _parse_simulator_trace,
    path_reason_from_risk_and_action,
)
from .report import CallLogger, MarkdownReportGenerator
from .visualizer import DeliberationVisualizer


class MoralStackCLI:
    """
    Interactive CLI for MoralStack.

    Exposes an input() loop that calls orchestrator.process().
    """

    def __init__(self, config: CLIConfig):
        self.config = config
        self.orchestrator: Any = None  # Orchestrator after setup()
        self.orch_config: Any = None
        self.loader: Any = None  # ModuleLoader after setup()
        self.verbose = config.verbose
        self.running = True
        self.call_logger = CallLogger(verbose=config.verbose)
        self.constitution_store = None  # Will be set in setup()
        self.visualizer = DeliberationVisualizer(use_colors=True)
        self.current_trace: Optional[DeliberationTrace] = None
        self.report_generator = MarkdownReportGenerator(output_dir="reports")
        self.last_prompt: str = ""  # Save prompt for report
        self.cost_tracker: Any = None  # Cost tracking (only with --verbose)

    def setup(self) -> bool:
        """
        Initializes the runtime stack.

        Returns:
            True if setup succeeded
        """
        print_banner()

        self._show_system_info()

        # Load modules
        self.loader = ModuleLoader(self.config)
        modules = self.loader.load_all()

        # Create orchestrator
        print_colored("\n🔧 Initializing Orchestrator...", "yellow")

        try:
            # Orchestrator: single source of config is .env
            # (MORALSTACK_ORCHESTRATOR_*); no CLI override.
            from moralstack.orchestration.config_loader import load_orchestrator_config_from_env
            from moralstack.runtime.orchestrator import Orchestrator

            orch_config = load_orchestrator_config_from_env()

            constitution_store = modules.get("_constitution_store")
            if not constitution_store:
                from moralstack.constitution.openai_config import OpenAIClientConfig
                from moralstack.constitution.store import (
                    ConstitutionStore,
                    ConstitutionStoreConfig,
                )

                api_key = self.config.openai_api_key or os.getenv("OPENAI_API_KEY")
                constitution_store = ConstitutionStore(
                    config=ConstitutionStoreConfig(
                        policy_llm=modules.get("policy"),
                        use_llm_matching=True,
                        openai_config=OpenAIClientConfig.with_env_fallback(
                            api_key=api_key,
                            model=self.config.openai_model,
                        ),
                        max_parallel_agents=self.config.max_parallel_agents,
                    )
                )

            self.orch_config = orch_config
            self.orchestrator = Orchestrator(
                config=orch_config,
                policy=modules.get("policy"),
                risk_estimator=modules.get("risk_estimator"),
                critic=modules.get("critic"),
                simulator=modules.get("simulator"),
                hindsight=modules.get("hindsight"),
                perspectives=modules.get("perspectives"),
                constitution_store=constitution_store,
            )

            # Save constitution_store for access to relevant principles
            self.constitution_store = constitution_store

            # Verify critic has store
            if hasattr(self.orchestrator, "critic") and self.orchestrator.critic:
                if hasattr(self.orchestrator.critic, "store"):
                    if self.orchestrator.critic.store is None:
                        print_colored(
                            "  ⚠️  Critic has no constitution_store - will use all principles",
                            "yellow",
                        )
                        # Assign store to critic if missing
                        self.orchestrator.critic.store = constitution_store
                    else:
                        if self.verbose:
                            print_colored("  ✓ Critic has constitution_store configured", "green")

            # Pass logger to orchestrator if supported
            if hasattr(self.orchestrator, "set_logger"):
                self.orchestrator.set_logger(self.call_logger)

            # Cost tracking (only with --verbose)
            if self.config.verbose:
                from moralstack.utils.cost_tracker import TokenCostTracker

                self.cost_tracker = TokenCostTracker()
                policy = modules.get("policy")
                if policy is not None and hasattr(policy, "set_cost_tracker"):
                    policy.set_cost_tracker(self.cost_tracker)
                if constitution_store is not None and hasattr(constitution_store, "set_cost_tracker"):
                    constitution_store.set_cost_tracker(self.cost_tracker)
            else:
                self.cost_tracker = None

            print_colored("  ✓ Orchestrator ready", "green")

        except Exception as e:
            print_colored(f"\n❌ Initialization error: {e}", "red")
            return False

        print_colored("\n✅ MoralStack Runtime ready!", "green")
        print_colored(HELP_TEXT, "cyan")
        print_separator()

        return True

    def run(self) -> None:
        """Starts the interactive loop."""
        if self.orchestrator is None:
            print_colored("❌ Orchestrator not initialized. Run setup() first.", "red")
            return

        while self.running:
            try:
                # Prompt
                user_input = input("\n🧠 MoralStack> ").strip()

                if not user_input:
                    continue

                # Special commands
                if user_input.startswith("/"):
                    self._handle_command(user_input)
                    continue

                # Process prompt
                self._process_prompt(user_input)

            except KeyboardInterrupt:
                print_colored("\n\n👋 Interrupted. Use /quit to exit.", "yellow")
            except EOFError:
                self.running = False

        print_colored("\n👋 Bye!", "cyan")

    def _handle_command(self, command: str) -> None:
        """Special commands handler."""
        cmd = command.lower()

        if cmd in ["/quit", "/exit", "/q"]:
            self.running = False

        elif cmd == "/help":
            print_colored(HELP_TEXT, "cyan")

        elif cmd == "/status":
            self._show_status()

        elif cmd == "/config":
            self._show_config()

        elif cmd == "/verbose":
            self.verbose = not self.verbose
            print_colored(f"Verbose: {'ON' if self.verbose else 'OFF'}", "yellow")

        elif cmd == "/report":
            if self.current_trace and self.current_trace.phases:
                self._show_deliberation_report(None)
            else:
                print_colored("No previous processing available.", "yellow")
                print_colored("Run a query first to generate a report.", "yellow")

        elif cmd == "/clear":
            os.system("cls" if os.name == "nt" else "clear")
            print_banner()

        else:
            print_colored(f"Unknown command: {command}", "red")
            print_colored("Use /help for command list", "yellow")

    def _show_status(self) -> None:
        """Shows module status."""
        print_colored("\n📊 Module Status:", "cyan")
        print_separator()

        if self.loader:
            for name, status in self.loader.get_status().items():
                print(f"  {name:18} {status}")

        print_separator()

    def _show_system_info(self) -> None:
        """Shows system information."""
        print_colored("\n💻 System Information:", "cyan")
        print_separator()
        print_colored("  Provider:           OpenAI API", "green")
        model = (self.config.openai_model if hasattr(self, "config") and self.config else None) or "gpt-4o"
        print(f"  Model:              {model}")
        print_separator()

    def _show_config(self) -> None:
        """Shows configuration."""
        print_colored("\n⚙️  Configuration:", "cyan")
        print_separator()

        print(f"  Mock mode:          {self.config.use_mock}")
        print(f"  Minimal mode:       {self.config.minimal}")
        print(f"  OpenAI model:       {self.config.openai_model}")
        if self.orch_config:
            print(f"  Max cycles:         {self.orch_config.max_deliberation_cycles}")
            print(f"  Timeout:            {self.orch_config.timeout_ms}ms")
            print(f"  Perspectives:       {self.orch_config.enable_perspectives}")
            print(f"  Simulation:         {self.orch_config.enable_simulation}")
            print(f"  Hindsight:          {self.orch_config.enable_hindsight}")
        print(f"  Verbose:            {self.verbose}")

        print_separator()

    def _setup_run_context(self, prompt: str) -> str | None:
        """Reset logger/cost, init persistence and trace. Returns run_id for finally."""
        self.call_logger.calls = []
        self.call_logger.call_counter = 0
        if self.cost_tracker is not None:
            self.cost_tracker.reset()

        run_id = None
        try:
            import uuid

            from moralstack.persistence import create_run, init_db
            from moralstack.persistence.config import get_db_path
            from moralstack.persistence.context import set_current_run_id

            db_path = get_db_path()
            if db_path:
                run_id = str(uuid.uuid4())
                init_db(db_path)
                create_run(run_id, "single", {"prompt_preview": prompt[:100]})
                set_current_run_id(run_id)
        except ImportError:
            pass

        self.current_trace = DeliberationTrace(
            prompt=prompt,
            start_time=time.time(),
        )
        return run_id

    def _display_relevant_principles(self, prompt: str) -> None:
        """Stampa la domanda dell'utente, recupera/mostra i principi rilevanti se verbose, poi 'Processing...'."""
        self._print_user_question(prompt)

        relevant_principles: list[Any] = []
        if self.verbose and hasattr(self, "constitution_store") and self.constitution_store:
            try:
                # 1. Rilevamento Dominio
                detected_domain = self._get_detected_domain(prompt)

                # 2. Caricamento Costituzione e Debug relativo
                constitution = self.constitution_store.get_constitution(domain=detected_domain)
                self._display_constitution_debug_info(detected_domain, constitution)

                # 3. Recupero Principi Rilevanti
                relevant_principles = self.constitution_store.get_relevant_principles(query=prompt, top_k=10, domain=None)

                # 4. Debug dettagliato degli agenti
                debug_info = self.constitution_store.get_debug_info()
                if debug_info:
                    self._display_agent_debug_info(debug_info, relevant_principles, constitution)

            except Exception as e:
                print_colored(f"⚠️  Error retrieving relevant principles: {e}", "yellow")
                if self.verbose:
                    import traceback

                    traceback.print_exc()
        elif self.verbose and not getattr(self, "constitution_store", None):
            print_colored("⚠️  Constitution store not available for principle retrieval", "yellow")

        # 5. Visualizzazione finale dei risultati
        if self.verbose:
            self._display_final_principles(relevant_principles)
            print_colored("\n🔄 Processing...\n", "yellow")

    def _print_user_question(self, prompt: str) -> None:
        print_separator()
        print_colored("\n💬 USER QUESTION:", "cyan")
        print_colored(f"{'─' * 80}", "cyan")
        print(f"{prompt}\n")
        print_colored(f"{'─' * 80}", "cyan")

    def _get_detected_domain(self, prompt: str) -> Optional[str]:
        try:
            from moralstack.constitution.store import _detect_domain

            available_domains = self.constitution_store._get_available_domains()
            domain_descriptions = self.constitution_store.get_domain_descriptions()
            return _detect_domain(
                prompt,
                self.constitution_store.policy_llm,
                domain_descriptions,
                available_domains,
            )
        except Exception:
            return None

    def _display_constitution_debug_info(self, domain, constitution) -> None:
        if not self.verbose:
            return

        total_principles = len(constitution.principles)
        core_count = len(constitution.core_principles)
        overlay_count = 0
        if constitution.active_overlay:
            overlay_count = len(constitution.active_overlay.additional_principles)

        if domain:
            print_colored(f"  [DEBUG] Domain detected: {domain}", "blue")
            print_colored(
                f"  [DEBUG] Constitution loaded: {total_principles} total "
                f"principles ({core_count} core + {overlay_count} overlay)",
                "blue",
            )
        else:
            print_colored(
                f"  [DEBUG] Constitution loaded: {total_principles} total " "principles (core only, no overlay detected)",
                "blue",
            )

    def _display_agent_debug_info(self, debug_info, relevant_principles, constitution) -> None:
        if not self.verbose:
            return

        # 1. Informazioni sulla creazione degli agenti
        self._display_agent_creation_info(debug_info)

        # 2. Conteggio principi per agente
        self._display_agent_principle_counts(debug_info)

        # 3. Risultati dettagliati degli agenti
        self._display_agent_results_details(debug_info)

        # 4. Domini Accettati/Rifiutati
        self._display_domain_status(debug_info)

        # 5. Conteggio finale per dominio
        self._display_final_per_domain_counts(debug_info)

        # 6. Risultato finale e fallback
        self._display_final_summary_and_fallback(relevant_principles, constitution)

    def _display_agent_creation_info(self, debug_info: dict) -> None:
        n_agents = debug_info.get("agents_created", 0)
        print_colored(f"  [DEBUG] Created parallel agents: {n_agents}", "blue")

        agent_domains = debug_info.get("agent_domains", [])
        if agent_domains:
            domains_str = ", ".join(agent_domains)
            print_colored(f"  [DEBUG] Agent domains: {domains_str}", "blue")

    def _display_agent_principle_counts(self, debug_info: dict) -> None:
        agent_principles = debug_info.get("agent_principles_count", {})
        if agent_principles:
            for domain, count in agent_principles.items():
                print_colored(f"    - {domain}: {count} principles", "blue")

    def _display_agent_results_details(self, debug_info: dict) -> None:
        agent_results = debug_info.get("agent_results", {})
        if not agent_results:
            return

        print_colored("  [DEBUG] Agent results:", "blue")
        total_found = 0
        for domain, result in agent_results.items():
            if isinstance(result, dict):
                count = result.get("principles_count", 0)
                confidence = result.get("confidence", 0.0)
                domain_match = result.get("domain_match", False)
                if count > 0 and domain_match:
                    msg = f"    - {domain}: {count} principles (conf={confidence:.2f})"
                    print_colored(msg, "green")
                    total_found += count
                elif count > 0:
                    msg = f"    - {domain}: {count} principles (rejected, conf={confidence:.2f})"
                    print_colored(msg, "yellow")
                else:
                    print_colored(f"    - {domain}: no principles", "yellow")
            else:
                count = result
                if count > 0:
                    print_colored(f"    - {domain}: {count} relevant principles", "green")
                    total_found += count
                else:
                    print_colored(f"    - {domain}: no relevant principles", "yellow")

        print_colored(f"  [DEBUG] Total principles found by agents: {total_found}", "blue")

    def _display_domain_status(self, debug_info: dict) -> None:
        accepted = debug_info.get("accepted_domains", [])
        rejected = debug_info.get("rejected_domains", {})
        if accepted:
            print_colored(f"  [DEBUG] Accepted domains: {', '.join(accepted)}", "green")
        if rejected:
            rejected_names = list(rejected.keys())
            print_colored(f"  [DEBUG] Rejected domains: {', '.join(rejected_names)}", "yellow")

    def _display_final_per_domain_counts(self, debug_info: dict) -> None:
        principles_by_domain = debug_info.get("principles_by_domain", {})
        if principles_by_domain:
            print_colored("  [DEBUG] Final principles per domain:", "blue")
            for domain, count in principles_by_domain.items():
                print_colored(f"    - {domain}: {count} principles", "cyan")

    def _display_final_summary_and_fallback(self, relevant_principles, constitution) -> None:
        print_colored(f"  [DEBUG] Final relevant principles: {len(relevant_principles)}", "blue")

        if len(relevant_principles) == 0:
            print_colored("  [DEBUG] First 5 available principles:", "blue")
            for i, p in enumerate(constitution.principles[:5], 1):
                print(f"    {i}. {p.id} - {p.title}")

    def _display_final_principles(self, principles: list[Any]) -> None:
        if principles:
            print_colored("\n📜 RELEVANT CONSTITUTIONAL PRINCIPLES:", "cyan")
            print_colored(f"{'─' * 80}", "cyan")
            for i, p in enumerate(principles[:10], 1):
                level_marker = "🔴 [HARD]" if p.level == "hard" else "🟡 [SOFT]"
                print(f"  {i}. {p.id} {level_marker}")
                print(f"     {p.title}")
                if p.domain:
                    print(f"     Domain: {p.domain}")
            print_colored(f"{'─' * 80}", "cyan")
        else:
            print_colored("\n⚠️  No relevant principles found", "yellow")

    def _call_orchestrator(self, prompt: str):
        """Call orchestrator and return the result (no side effects)."""
        assert self.orchestrator is not None, "Orchestrator not initialized"
        return self.orchestrator.process(prompt)

    def _update_trace(self, result) -> None:
        """Update current_trace from orchestrator result and build phases from call_logger."""
        if not self.current_trace:
            return
        self.current_trace.request_id = result.request_id or ""
        self.current_trace.end_time = time.time()
        self.current_trace.path = result.path_taken
        self.current_trace.total_cycles = result.total_cycles
        self.current_trace.converged = result.converged
        self.current_trace.response_type = result.response.response_type.value
        self.current_trace.risk_score = result.response.metadata.risk_score
        self.current_trace.triggered_principles = result.response.metadata.triggered_principles

        final_action = getattr(result.response.metadata, "final_action", "") or ""
        risk_score = result.response.metadata.risk_score
        self.current_trace.path_reason = path_reason_from_risk_and_action(
            risk_score,
            final_action,
        )
        if self.current_trace.path_reason == DecisionReason.LOW_RISK.value and risk_score >= 0.3:
            warnings.warn(
                f"path_reason=LOW_RISK but risk_score={risk_score} >= 0.3: inconsistency",
                UserWarning,
                stacklevel=2,
            )

        self._build_trace_from_calls()

    def _display_result(self, result, elapsed: float) -> None:
        """Show final response, metadata (if verbose), and optional cost/deliberation report."""
        print_colored("\n" + "=" * 80, "green")
        print_colored("📝 FINAL RESPONSE:", "green")
        print_colored("=" * 80, "green")
        print(f"\n{result.response.content}\n")
        print_colored("=" * 80, "green")

        if self.verbose:
            self._show_metadata(result, elapsed)
            if self.call_logger.calls:
                print_colored(self.call_logger.get_summary(), "cyan")
            if self.cost_tracker is not None:
                print_colored("\n💰 " + self.cost_tracker.get_summary_eur(), "yellow")
            self._show_deliberation_report(result)
            print_colored("\n" + "=" * 80, "green")
            print_colored("📝 FINAL RESPONSE:", "green")
            print_colored("=" * 80, "green")
            print(f"\n{result.response.content}\n")
            print_colored("=" * 80, "green")
        else:
            path_icon = "⚡" if result.path_taken == "fast" else ("💥" if result.path_taken == "error" else "🧠")
            print_colored(
                f"\n[{result.response.response_type.value} | "
                f"risk: {result.response.metadata.risk_score:.2f} | "
                f"{path_icon} {result.path_taken} | "
                f"cycles: {result.total_cycles} | "
                f"{elapsed:.0f}ms]",
                "blue",
            )
            if result.error:
                print_colored(f"⚠️  Error: {result.error}", "yellow")
                print_colored("    Use --verbose for details", "yellow")

    def _process_prompt(self, prompt: str) -> None:
        """Processes a user prompt."""
        run_id = self._setup_run_context(prompt)
        self._display_relevant_principles(prompt)

        start_time = time.time()
        _process_error = False

        try:
            result = self._call_orchestrator(prompt)
            elapsed = (time.time() - start_time) * 1000
            self._update_trace(result)
            self._display_result(result, elapsed)
            try:
                from moralstack.persistence.context import get_current_request_id
                from moralstack.persistence.db import update_request_response

                req_id = get_current_request_id()
                if run_id and req_id:
                    update_request_response(
                        run_id=run_id,
                        request_id=req_id,
                        final_response=getattr(result.response, "content", "") or "",
                    )
            except Exception:
                pass

        except Exception as e:
            _process_error = True
            print_colored(f"\n❌ Error: {e}", "red")
            if self.verbose:
                import traceback

                traceback.print_exc()

            if self.current_trace:
                self.current_trace.end_time = time.time()
                self.current_trace.errors.append(str(e))
                if self.verbose:
                    self._show_deliberation_report(None)

        finally:
            try:
                from moralstack.persistence.write_queue import get_write_queue

                get_write_queue().flush(timeout=10.0)
            except Exception:
                pass

            try:
                if run_id is not None:
                    from moralstack.persistence import end_run

                    end_run(run_id, status="error" if _process_error else "ok")
            except (ImportError, NameError):
                pass

        print_separator()

    def _show_metadata(self, result: Any, elapsed_ms: float) -> None:
        """Shows detailed response metadata."""
        print_colored("\n📊 Metadata:", "cyan")
        print_colored(f"{'─' * 80}", "cyan")

        print(f"  Response Type:      {result.response.response_type.value}")
        print(f"  Risk Score:         {result.response.metadata.risk_score:.3f}")
        print(f"  Deliberation Cycles:{result.total_cycles}")
        print(f"  Hindsight Score:    {result.response.metadata.hindsight_score:.3f}")

        # Show path with clear indication
        if result.path_taken == "error":
            path_icon = "💥 ERROR"
            path_color = "red"
        elif result.path_taken == "fast":
            path_icon = "⚡ FAST"
            path_color = "green"
        else:
            path_icon = "🧠 DELIBERATIVE"
            path_color = "yellow"

        print_colored(f"  Path:               {path_icon} ({result.path_taken})", path_color)
        print(f"  Elapsed:            {elapsed_ms:.0f}ms")
        if result.response.metadata.triggered_principles:
            print(f"  Triggered:          {', '.join(result.response.metadata.triggered_principles)}")

        # Show error if present
        if result.error:
            print_colored(f"  Error:              {result.error}", "red")

        print_colored(f"{'─' * 80}", "cyan")

    def _build_trace_from_calls(self) -> None:
        """Builds the deliberative trace from logged calls (dispatcher)."""
        if not self.current_trace or not self.call_logger.calls:
            return

        current_cycle = 0

        for call in self.call_logger.calls:
            module = call.get("module", "")
            action = call.get("action", "")
            prompt = call.get("full_prompt", call.get("prompt", ""))
            response = call.get("full_response", call.get("response", ""))
            duration = call.get("duration_ms", 0.0)

            None

            if module == "risk_estimator":
                parse_result = _parse_risk_trace(call)
            elif module == "policy":
                parse_result = _parse_policy_trace(call, current_cycle)
            elif module == "critic":
                parse_result = _parse_critic_trace(call)
            elif module == "simulator":
                parse_result = _parse_simulator_trace(call)
            elif module == "hindsight":
                parse_result = _parse_hindsight_trace(call)
            elif module == "perspectives":
                parse_result = _parse_perspectives_trace(call)
            elif module == "orchestrator":
                if "deliberation_cycle" in action and "start" in action:
                    try:
                        cycle_num = int(action.split("_")[2])
                        current_cycle = cycle_num
                    except Exception:
                        pass
                    continue  # Skip logging this meta-event

                if "deliberation_cycle" in action and "complete" in action:
                    orch_details: dict = {}
                    orch_decision = None
                    orch_decision_reason = None
                    if "Decision:" in prompt:
                        try:
                            dec = prompt.split("Decision:")[1].split("\n")[0].strip()
                            orch_decision = dec.upper()
                            if dec == "CONVERGED":
                                orch_decision_reason = DecisionReason.CONVERGED.value
                            elif dec == "REVISE":
                                orch_decision_reason = "Revision required, continuing to next cycle"
                            elif dec == "REFUSE":
                                orch_decision_reason = "Refusal: unrecoverable violations"
                        except Exception:
                            pass
                    if "Hindsight score:" in prompt:
                        try:
                            hs = prompt.split("Hindsight score:")[1].split("\n")[0].strip()
                            orch_details["hindsight_score"] = hs
                        except Exception:
                            pass
                    parse_result = TraceParseResult(
                        phase_type=PhaseType.CONVERGENCE_CHECK,
                        decision=orch_decision,
                        decision_reason=orch_decision_reason,
                        details=orch_details,
                    )
                elif "timeout" in action.lower():
                    continue  # Skip timeout (warnings handled elsewhere)
                elif "pre_cycle_check" in action:
                    continue  # Skip meta-event
                else:
                    continue  # Skip other orchestrator events

            else:
                continue  # Skip unknown modules

            if parse_result is None:
                continue

            # Apply trace updates from parse result
            if parse_result.risk_score is not None:
                self.current_trace.risk_score = parse_result.risk_score
            if parse_result.risk_category is not None:
                self.current_trace.risk_category = parse_result.risk_category
            for dr in parse_result.draft_revisions:
                self.current_trace.draft_history.append(dr)

            phase_result = PhaseResult(
                phase=parse_result.phase_type,
                cycle=current_cycle,
                duration_ms=duration,
                success="ERROR" not in action and "ERROR" not in response,
                input_summary=prompt[:200] if prompt else "",
                output_summary=response[:200] if response else "",
                decision=parse_result.decision,
                decision_reason=parse_result.decision_reason,
                details=parse_result.details,
                errors=parse_result.errors,
                warnings=parse_result.warnings,
            )
            self.current_trace.add_phase(phase_result)

    def _show_deliberation_report(self, result: Any) -> None:
        """Shows the full report of the deliberative process."""
        if not self.current_trace:
            return

        print_colored("\n\n", "white")
        print_colored("╔" + "═" * 78 + "╗", "magenta")
        print_colored("║" + " DELIBERATIVE PROCESS REPORT ".center(78) + "║", "magenta")
        print_colored("╚" + "═" * 78 + "╝", "magenta")

        # Show the flow diagram
        print(self.visualizer.render_flow_diagram(self.current_trace))

        # Show detailed analysis
        print(self.visualizer.render_detailed_analysis(self.current_trace))

        # Show summary
        print(self.visualizer.render_summary(self.current_trace))

        # Show revision history
        self._show_revision_history()

        # Errors/omissions section
        self._show_error_analysis()

        # Generate full Markdown report (if verbose or if there were deliberative cycles)
        if self.verbose or (self.current_trace.total_cycles > 0):
            self._generate_markdown_report(result)

    def _show_revision_history(self) -> None:
        """Shows the draft revision history."""
        if not self.current_trace or not self.current_trace.draft_history:
            return

        # Only if there is more than one version (i.e. there were revisions)
        if len(self.current_trace.draft_history) < 2:
            return

        print_colored("\n" + "═" * 80, "blue")
        print_colored(" REVISION HISTORY ", "blue")
        print_colored("═" * 80, "blue")

        print_colored("\nThis section shows how the response evolved through deliberative cycles.", "dim")
        print_colored(
            "Each revision is guided by feedback from modules " "(Critic, Perspectives, Hindsight, Simulator).\n",
            "dim",
        )

        for i, revision in enumerate(self.current_trace.draft_history):
            # Version header
            if revision.is_initial:
                version_label = "INITIAL DRAFT"
                color = "yellow"
            else:
                version_label = f"REVISION #{i}"
                color = "green"

            print_colored(f"\n┌{'─' * 76}┐", color)
            print_colored(f"│ {version_label} (Cycle {revision.cycle})".ljust(77) + "│", color)
            print_colored(f"└{'─' * 76}┘", color)

            # Show guidance used (only for revisions, not for initial draft)
            if not revision.is_initial and revision.guidance_used:
                print_colored("\n📋 GUIDANCE USED FOR THIS REVISION:", "cyan")
                # Format guidance for readability - COMPLETE without truncation
                guidance_lines = revision.guidance_used.split("\n")
                for line in guidance_lines:  # Show ALL lines
                    if line.strip():
                        # Highlight module tags
                        if line.startswith("[CRITIC]"):
                            print_colored(f"   ⚖️  {line}", "yellow")
                        elif line.startswith("[PERSPECTIVES"):
                            print_colored(f"   👥 {line}", "magenta")
                        elif line.startswith("[HINDSIGHT]"):
                            print_colored(f"   🔍 {line}", "blue")
                        elif line.startswith("[SIMULATOR"):
                            print_colored(f"   🔮 {line}", "red")
                        else:
                            print_colored(f"   {line}", "dim")

            # Show the response text
            print_colored("\n📝 RESPONSE:", "white")

            # Format text with wrap
            draft_text = revision.draft_text.strip()
            max_line_length = 76

            # Split into paragraphs and then into lines
            paragraphs = draft_text.split("\n")
            for para in paragraphs:
                if not para.strip():
                    print("   ")
                    continue

                # Word wrap
                words = para.split()
                current_line = "   "
                for word in words:
                    if len(current_line) + len(word) + 1 <= max_line_length:
                        current_line += word + " "
                    else:
                        print(current_line.rstrip())
                        current_line = "   " + word + " "
                if current_line.strip():
                    print(current_line.rstrip())

            # Separator between versions
            if i < len(self.current_trace.draft_history) - 1:
                print_colored("\n" + "─" * 40 + " ↓ " + "─" * 36, "dim")

        # Summary of changes
        if len(self.current_trace.draft_history) > 1:
            print_colored("\n📊 EVOLUTION SUMMARY:", "cyan")
            initial = self.current_trace.draft_history[0]
            final = self.current_trace.draft_history[-1]

            print_colored(f"   • Total versions: {len(self.current_trace.draft_history)}", "white")
            print_colored(f"   • Initial length: {len(initial.draft_text)} characters", "white")
            print_colored(f"   • Final length: {len(final.draft_text)} characters", "white")

            diff = len(final.draft_text) - len(initial.draft_text)
            if diff > 0:
                print_colored(f"   • Change: +{diff} characters (response enriched)", "green")
            elif diff < 0:
                print_colored(f"   • Change: {diff} characters (response condensed)", "yellow")
            else:
                print_colored("   • Variation: 0 characters (same length)", "dim")

    def _show_error_analysis(self) -> None:
        """Shows analysis of errors and omissions in the process."""
        if not self.current_trace:
            return

        print_colored("\n" + "═" * 80, "red")
        print_colored(" ERRORS AND OMISSIONS ANALYSIS ", "red")
        print_colored("═" * 80, "red")

        issues_found = False
        early_refusal = self.current_trace.response_type == "full_refusal"

        # 1. Verifica fasi mancanti
        expected_phases = self._get_expected_phases()
        executed_phases = {p.phase for p in self.current_trace.phases}
        missing_phases = set(expected_phases.keys()) - executed_phases

        if missing_phases:
            if self._display_missing_phases(expected_phases, missing_phases, early_refusal):
                issues_found = True

        if early_refusal:
            self._display_early_stop_info()

        # 2. Verifica errori nelle fasi
        phases_with_errors = [p for p in self.current_trace.phases if p.errors]
        if phases_with_errors:
            self._display_phase_errors(phases_with_errors)
            issues_found = True

        # 3. Verifica avvisi
        phases_with_warnings = [p for p in self.current_trace.phases if p.warnings]
        if phases_with_warnings:
            self._display_phase_warnings(phases_with_warnings)
            issues_found = True

        # 4. Verifica convergenza
        if not early_refusal and (not self.current_trace.converged) and self.current_trace.path == "deliberative":
            self._display_convergence_issues()
            issues_found = True

        # 5. Verifica principi violati
        if self.current_trace.triggered_principles:
            self._display_violated_principles()
            issues_found = True

        # 6. Verifica tempo di elaborazione
        if self._display_processing_time_issues():
            issues_found = True

        if not issues_found:
            print_colored("\n✅ NO ERRORS OR OMISSIONS DETECTED", "green")
            print_colored("   The deliberative process completed successfully.", "green")

        print_colored("\n" + "═" * 80, "red")

    def _get_expected_phases(self) -> dict[PhaseType, str]:
        expected_phases = {
            PhaseType.RISK_ESTIMATION: "Risk Estimation",
            PhaseType.GENERATION: "Response generation",
        }

        if self.current_trace.path == "deliberative":
            expected_phases.update({PhaseType.CRITIQUE: "Constitutional critique"})
            if self.orch_config:
                if self.orch_config.enable_simulation:
                    expected_phases[PhaseType.SIMULATION] = "Consequence simulation"
                if self.orch_config.enable_hindsight:
                    expected_phases[PhaseType.HINDSIGHT] = "Hindsight evaluation"
                if self.orch_config.enable_perspectives:
                    expected_phases[PhaseType.PERSPECTIVES] = "Perspective evaluation"
        return expected_phases

    def _display_missing_phases(self, expected_dict: dict, missing_set: set, early_refusal: bool) -> bool:
        if early_refusal:
            missing_set = {
                p for p in missing_set if p in {PhaseType.RISK_ESTIMATION, PhaseType.GENERATION, PhaseType.CRITIQUE}
            }

        if not missing_set:
            return False

        print_colored("\n⚠️  MISSING PHASES:", "yellow")
        for phase in missing_set:
            print_colored(f"   • {expected_dict[phase]} ({phase.value})", "yellow")
            print_colored("     → Possible cause: timeout, error, or disabled module", "dim")
        return True

    def _display_early_stop_info(self) -> None:
        print_colored("\nℹ️  EARLY-STOP:", "dim")
        print_colored(
            "   Process terminated early with refusal; subsequent phases may have been intentionally skipped.",
            "dim",
        )

    def _display_phase_errors(self, phases_with_errors: list) -> None:
        print_colored("\n❌ PHASES WITH ERRORS:", "red")
        for phase in phases_with_errors:
            print_colored(f"   • {phase.phase.value.upper()} (Cycle {phase.cycle}):", "red")
            for error in phase.errors:
                print_colored(f"     - {error}", "red")

    def _display_phase_warnings(self, phases_with_warnings: list) -> None:
        print_colored("\n⚠️  WARNINGS:", "yellow")
        for phase in phases_with_warnings:
            print_colored(f"   • {phase.phase.value.upper()} (Cycle {phase.cycle}):", "yellow")
            for warning in phase.warnings:
                print_colored(f"     - {warning}", "yellow")

    def _display_convergence_issues(self) -> None:
        print_colored("\n⚠️  CONVERGENCE NOT REACHED:", "yellow")
        print_colored("   The deliberative process did not reach convergence.", "yellow")

        # Analyze possible causes
        max_cyc = self.orch_config.max_deliberation_cycles if self.orch_config else 2
        if self.current_trace.total_cycles >= max_cyc:
            print_colored(f"   → Maximum cycle limit reached ({max_cyc})", "dim")

        # Search for unresolved violations
        last_critique = None
        for phase in reversed(self.current_trace.phases):
            if phase.phase == PhaseType.CRITIQUE:
                last_critique = phase
                break

        if last_critique and last_critique.decision == "CRITICAL VIOLATION":
            print_colored("   → Critical violations not resolved", "dim")

    def _display_violated_principles(self) -> None:
        print_colored("\n⚖️  CONSTITUTIONAL PRINCIPLES VIOLATED:", "cyan")
        for principle_id in self.current_trace.triggered_principles:
            constraint_type = "UNKNOWN"
            if self.constitution_store is not None:
                try:
                    constitution = self.constitution_store.get_constitution(None)
                    for p in constitution.principles:
                        if p.id == principle_id:
                            constraint_type = p.level.upper()
                            break
                except Exception:
                    pass

            if constraint_type == "UNKNOWN":
                constraint_type = "HARD"

            color = "red" if constraint_type == "HARD" else "yellow"
            print_colored(f"   • [{constraint_type}] {principle_id}", color)

    def _display_processing_time_issues(self) -> bool:
        total_time = self.current_trace.total_duration_ms()
        timeout = self.orch_config.timeout_ms if self.orch_config else 600000
        if total_time > timeout * 0.8:
            print_colored("\n⏱️  CRITICAL PROCESSING TIME:", "yellow")
            msg = f"   Total time: {total_time:.0f}ms ({total_time / 1000:.1f}s)"
            print_colored(msg, "yellow")
            print_colored(f"   Timeout configured: {timeout}ms", "dim")
            print_colored(f"   → Processing used {total_time / timeout * 100:.1f}% of timeout", "dim")
            return True
        return False

    def _generate_markdown_report(self, result: Any) -> None:
        """No-op: reports are generated on-demand via UI export."""
        pass


def parse_args() -> CLIConfig:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="MoralStack Runtime CLI - Interactive shell for MoralStack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  export OPENAI_API_KEY=sk-...
  python scripts/mstack_run.py               # Start (requires OPENAI_API_KEY)
  python scripts/mstack_run.py --mock        # Use mock modules (no API key)
  python scripts/mstack_run.py --minimal     # Policy + risk estimator only
  python scripts/mstack_run.py --verbose     # Detailed output
  python scripts/mstack_run.py --clean-start # Clean reports/trace/debug before starting
        """,
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock modules instead of real models (no API key required)",
    )

    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Load only policy and risk estimator (disable other modules)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output",
    )

    parser.add_argument(
        "--openai-key",
        type=str,
        default=None,
        help="OpenAI API key (default: OPENAI_API_KEY env var, required)",
    )

    parser.add_argument(
        "--openai-model",
        type=str,
        default=None,
        help="OpenAI model (default: gpt-4o or OPENAI_MODEL env)",
    )

    parser.add_argument(
        "--max-parallel-agents",
        type=int,
        default=2,
        help="Maximum number of parallel agents (default: 2)",
    )

    parser.add_argument(
        "--clean-start",
        action="store_true",
        help="Delete .md reports, decision_trace.jsonl and debug.log before starting",
    )
    parser.add_argument(
        "--clean-db",
        action="store_true",
        dest="clean_db",
        help="When using db_only mode, also delete the database file",
    )
    parser.add_argument(
        "--no-perspectives",
        action="store_true",
        dest="no_perspectives",
        help="Disable perspectives module",
    )
    parser.add_argument(
        "--no-simulation",
        action="store_true",
        dest="no_simulation",
        help="Disable simulation module",
    )
    parser.add_argument(
        "--no-hindsight",
        action="store_true",
        dest="no_hindsight",
        help="Disable hindsight module",
    )

    args = parser.parse_args()
    openai_model = args.openai_model or os.getenv("OPENAI_MODEL", "gpt-4o")
    return CLIConfig(
        use_mock=args.mock,
        minimal=args.minimal,
        clean_start=args.clean_start,
        clean_db=getattr(args, "clean_db", False),
        verbose=args.verbose,
        openai_api_key=args.openai_key,
        openai_model=openai_model,
        max_parallel_agents=args.max_parallel_agents,
        enable_perspectives=not getattr(args, "no_perspectives", False),
        enable_simulation=not getattr(args, "no_simulation", False),
        enable_hindsight=not getattr(args, "no_hindsight", False),
    )


def main() -> int:
    """Main entry point."""
    load_env()
    config = parse_args()

    if config.clean_start:
        clean_start_artifacts(clean_db=config.clean_db)

    if not config.use_mock:
        api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not (api_key or "").strip():
            print_colored("ERROR: OPENAI_API_KEY required to start MoralStack.", "red")
            print_colored("  Set the variable: export OPENAI_API_KEY=sk-...", "yellow")
            print_colored("  Or: moralstack --openai-key sk-...", "yellow")
            return 1

    cli = MoralStackCLI(config)

    if not cli.setup():
        return 1

    cli.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
