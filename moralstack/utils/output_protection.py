"""
MoralStack Output Protection - Anti-Leakage and Language Consistency.

Robust protection against:
- System prompt leakage (via Canary Tokens)
- Delimiter injection (via Structural Delimiters)
- Language mismatch (via Echo-Back in the prompt)

Language-agnostic, low computational cost approach.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from moralstack.prompts.catalog import PromptSet, default_prompt_set


@dataclass
class ProtectionResult:
    """Output validation result."""

    original: str
    cleaned: str
    had_leakage: bool = False
    leakage_type: str | None = None
    confidence: float = 1.0


class CanaryProtection:
    """
    Leakage protection via canary tokens.

    Inserts unique "secret" tokens into the system prompt that should NEVER
    appear in the output. If they do → leakage detected.

    Advantages:
    - O(1) cost: just a string search
    - Language-agnostic: works with any language
    - No hardcoded patterns

    Usage:
        canary = CanaryProtection()
        system_prompt = canary.inject(original_prompt)
        # ... generate output ...
        is_safe, cleaned = canary.validate(output)
    """

    def __init__(self, rotate_per_request: bool = False, prompt_set: PromptSet | None = None):
        """
        Args:
            rotate_per_request: If True, generates a new canary for each request
            prompt_set: PromptSet to read the canary instruction text from (default: packaged set)
        """
        self._rotate = rotate_per_request
        self._prompt_set = prompt_set or default_prompt_set()
        self._canary = self._generate_canary()

    def _generate_canary(self) -> str:
        """Generates a unique, hard-to-guess canary token."""
        # Format that looks like an internal reference but is unique
        unique_id = hashlib.md5(uuid.uuid4().bytes).hexdigest()[:8].upper()
        return f"§REF_{unique_id}§"

    @property
    def current_canary(self) -> str:
        """Returns the current canary (for debugging)."""
        return self._canary

    def inject(self, system_prompt: str) -> str:
        """
        Injects the canary into the system prompt.

        The canary is added as an "internal reference" that the model
        should never repeat in the output.
        """
        if self._rotate:
            self._canary = self._generate_canary()

        # Instruction that includes the canary naturally
        canary_instruction = self._prompt_set.format("policy.canary_instruction", canary=self._canary)

        return system_prompt + canary_instruction

    def validate(self, output: str) -> tuple[bool, str]:
        """
        Checks whether the output contains the canary.

        Returns:
            (is_clean, cleaned_output)
            - is_clean: True if no leakage detected
            - cleaned_output: cleaned output (on leakage, removes the contaminated part)
        """
        if self._canary not in output:
            return True, output

        # Leakage detected! Find where the "contaminated" content ends
        lines = output.split("\n")
        clean_lines = []
        found_clean_content = False

        for line in lines:
            # If the line contains the canary, skip it
            if self._canary in line:
                continue
            # If the line contains instruction patterns, skip it
            if self._looks_like_instruction(line):
                continue
            # Otherwise it is clean content
            if line.strip():
                found_clean_content = True
            if found_clean_content:
                clean_lines.append(line)

        cleaned = "\n".join(clean_lines).strip()

        # If there is no clean content, return a placeholder
        if not cleaned:
            cleaned = "[Risposta non generata correttamente]"

        return False, cleaned

    def _looks_like_instruction(self, line: str) -> bool:
        """
        Lightweight heuristic to identify lines that look like instructions.

        Does NOT use language-specific keywords, but structural patterns.
        """
        stripped = line.strip()
        if not stripped:
            return False

        # Structural (non-linguistic) patterns:
        # 1. Starts with [ and ends with ] (bracket instruction)
        if stripped.startswith("[") and stripped.endswith("]"):
            return True

        # 2. All uppercase (probably an instruction header/title)
        if stripped.isupper() and len(stripped) > 3:
            return True

        # 3. Contains the "§" pattern (our marker)
        if "§" in stripped:
            return True

        return False


class DelimiterProtection:
    """
    Protection via structural delimiters.

    Uses special delimiters to separate the system prompt from user input.
    If these delimiters appear in the output → leakage.

    Advantages:
    - O(n) cost: string operations only
    - Language-agnostic
    - Clear separation of "roles"
    """

    # Pattern to detect any delimiter
    DELIMITER_PATTERN = re.compile(r"◆/?(?:SYS|USR)◆")

    def validate(self, output: str) -> tuple[bool, str]:
        """
        Checks whether the output contains delimiters (= leakage).

        Returns:
            (is_clean, cleaned_output)
        """
        # Look for any delimiter in the output
        if not self.DELIMITER_PATTERN.search(output):
            return True, output

        # Leakage detected! Remove everything before the last delimiter
        # and everything after "Response:"
        cleaned = output

        # Find the last occurrence of a delimiter
        matches = list(self.DELIMITER_PATTERN.finditer(output))
        if matches:
            last_match = matches[-1]
            # Take everything after the last delimiter
            after_delimiter = output[last_match.end() :]
            # Find the first newline after it
            newline_idx = after_delimiter.find("\n")
            if newline_idx >= 0:
                cleaned = after_delimiter[newline_idx:].strip()
            else:
                cleaned = after_delimiter.strip()

        # Also remove any leftover "Response:"
        if cleaned.startswith("Response:"):
            cleaned = cleaned[9:].strip()

        if not cleaned:
            cleaned = "[Risposta non generata correttamente]"

        return False, cleaned


class OutputProtector:
    """
    Complete protector combining Canary + Delimiters + Language Rules.

    Recommended usage:
        protector = OutputProtector()

        # Prepare the system prompt
        protected_prompt = protector.prepare_system_prompt(base_prompt)

        # After generation, validate the output
        result = protector.validate(output, user_input)
        final_output = result.cleaned

    Total cost: O(n) where n is the output length.
    """

    # Language and anti-leakage rules: the texts live in moralstack/prompts/data/policy.yaml
    # (``policy.language_rules`` / ``policy.anti_leak_rules``). These class attributes remain
    # as a re-export of the default set (values identical to the old literals); at runtime
    # ``prepare_system_prompt`` reads the instance's PromptSet.
    # NB (anti-leak): these rules protect the system prompt, they do not hide the model's
    # limits. A declared limit (no personal experience, no real-time
    # access, training date) is useful content for the user and
    # must remain sayable: forbidding it produced answers that invented memories
    # or omitted the knowledge cut-off date.
    LANGUAGE_RULES = default_prompt_set().text("policy.language_rules")
    ANTI_LEAK_RULES = default_prompt_set().text("policy.anti_leak_rules")

    def __init__(
        self,
        enable_canary: bool = True,
        enable_delimiters: bool = True,
        rotate_canary: bool = False,
        prompt_set: PromptSet | None = None,
    ):
        """
        Args:
            enable_canary: Enables canary token protection
            enable_delimiters: Enables delimiter protection
            rotate_canary: Generates a new canary for each request
            prompt_set: PromptSet to read language, anti-leak and canary rules from
        """
        self._prompt_set = prompt_set or default_prompt_set()
        self._canary = (
            CanaryProtection(rotate_per_request=rotate_canary, prompt_set=self._prompt_set) if enable_canary else None
        )
        self._delimiters = DelimiterProtection() if enable_delimiters else None

    def prepare_system_prompt(self, base_prompt: str) -> str:
        """
        Prepares the system prompt with all protections.

        Args:
            base_prompt: Original system prompt

        Returns:
            Protected system prompt with anti-leak and language rules
        """
        # Add language rules
        enhanced = (
            f"{base_prompt}\n{self._prompt_set.text('policy.language_rules')}"
            f"\n{self._prompt_set.text('policy.anti_leak_rules')}"
        )

        # Add canary if enabled
        if self._canary:
            enhanced = self._canary.inject(enhanced)

        return enhanced

    def validate(self, output: str) -> ProtectionResult:
        """
        Validates the output and removes any leakage.

        Args:
            output: Raw LLM output

        Returns:
            ProtectionResult with cleaned output and metadata
        """
        original = output
        cleaned = output
        had_leakage = False
        leakage_type = None

        # 1. Check canary
        if self._canary:
            is_clean, cleaned = self._canary.validate(cleaned)
            if not is_clean:
                had_leakage = True
                leakage_type = "canary"

        # 2. Check delimiters
        if self._delimiters:
            is_clean, cleaned = self._delimiters.validate(cleaned)
            if not is_clean:
                had_leakage = True
                leakage_type = "delimiter" if leakage_type is None else f"{leakage_type}+delimiter"

        # 3. Light final cleanup (remove excess whitespace)
        cleaned = cleaned.strip()

        return ProtectionResult(
            original=original,
            cleaned=cleaned,
            had_leakage=had_leakage,
            leakage_type=leakage_type,
            confidence=0.95 if had_leakage else 1.0,
        )


# Factory function for simple use
def create_protector(
    enable_canary: bool = True,
    enable_delimiters: bool = True,
    prompt_set: PromptSet | None = None,
) -> OutputProtector:
    """
    Creates an OutputProtector with standard configuration.

    Args:
        enable_canary: Enables canary protection (default True)
        enable_delimiters: Enables delimiter protection (default True)
        prompt_set: PromptSet to read the texts from (default: packaged set)

    Returns:
        Configured OutputProtector
    """
    return OutputProtector(
        enable_canary=enable_canary,
        enable_delimiters=enable_delimiters,
        rotate_canary=False,  # For efficiency, do not rotate
        prompt_set=prompt_set,
    )
