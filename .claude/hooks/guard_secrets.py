#!/usr/bin/env python3
"""PreToolUse guard: keep secrets out of files, content and git staging.

Enforces the global security rule ("mai segreti/.env nel codice, nei log o nei
commit") deterministically rather than as guidance. Registered for both
``Edit|Write`` and ``Bash``; it inspects ``tool_name`` and applies the relevant
checks:

* **Edit / Write / MultiEdit**
  - block writing to a secret-bearing path (``.env`` and variants, ``*.pem``,
    ``*.key``, ``id_rsa``, ``credentials.json`` …); templates/examples are allowed.
  - block writing *content* that contains a real-looking credential
    (``sk-…`` keys, AWS ``AKIA…``, PEM private-key blocks). Documentation files
    (``.md``/``docs/``/``.claude/``) are exempt — they legitimately show patterns.

* **Bash**
  - block ``git add`` / ``git commit`` that stages a ``.env`` / secret file.
  - block a command that embeds a real-looking credential inline.

Blocks with exit code 2 (stderr is shown to Claude). Fails **open** on any
malformed input or unexpected error, so a hook bug can never wedge the session.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath

# --- secret-bearing file paths -------------------------------------------------

_SECRET_BASENAMES = {
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    "id_ecdsa",
    "credentials.json",
    ".netrc",
    ".pgpass",
    ".htpasswd",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks")
# Suffixes that mark a non-secret template/example, even on a secret-looking name.
_TEMPLATE_MARKERS = (".template", ".example", ".sample", ".dist")

# --- real-looking credentials in content/commands ------------------------------

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenAI/Anthropic-style key", re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{24,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("PEM private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
)
# Obvious placeholders that must never trip the content scanner.
_PLACEHOLDER = re.compile(r"\$\{|<your|your-?key|xxxx|placeholder|example|changeme|\.\.\.", re.IGNORECASE)


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


def _is_template(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(m) or m + "." in lower for m in _TEMPLATE_MARKERS)


def _is_secret_path(path: str) -> bool:
    name = _basename(path)
    lower = name.lower()
    if _is_template(name):
        return False
    if lower == ".env" or lower.startswith(".env."):
        return True
    if name in _SECRET_BASENAMES:
        return True
    return any(lower.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _is_doc_path(path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    return (
        lower.endswith((".md", ".rst", ".txt"))
        or "/docs/" in lower
        or lower.startswith("docs/")
        or "/.claude/" in lower
        or lower.startswith(".claude/")
    )


def _scan_content(text: str) -> list[str]:
    found: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 40) : m.end() + 40]
            if _PLACEHOLDER.search(window):
                continue
            found.append(label)
            break
    return found


def _check_edit(tool_input: dict) -> list[str]:
    msgs: list[str] = []
    path = tool_input.get("file_path")
    if isinstance(path, str) and path and _is_secret_path(path):
        msgs.append(
            f"writing to a secret-bearing file ({_basename(path)}) — secrets must live in env vars, never in tracked files."
        )

    if not (isinstance(path, str) and _is_doc_path(path)):
        chunks: list[str] = []
        for key in ("content", "new_string"):
            value = tool_input.get(key)
            if isinstance(value, str):
                chunks.append(value)
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                    chunks.append(edit["new_string"])
        for label in _scan_content("\n".join(chunks)):
            msgs.append(f"content contains a real-looking credential ({label}) — use a placeholder / env var instead.")
    return msgs


def _check_bash(cmd: str) -> list[str]:
    msgs: list[str] = []
    stages = re.search(r"git\s+(?:add|commit)\b", cmd) is not None
    if stages and re.search(r"(?:^|\s)\.env(?:\.\w+)?(?:\s|$)", cmd):
        if not re.search(r"\.env\.(?:template|example|sample|dist)", cmd):
            msgs.append("staging a `.env` file for commit — never commit secrets.")
    for label in _scan_content(cmd):
        msgs.append(f"command embeds a real-looking credential ({label}) — pass it via an env var, not inline.")
    return msgs


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0

    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    if tool in ("Edit", "Write", "MultiEdit"):
        violations = _check_edit(tool_input)
    elif tool == "Bash":
        cmd = tool_input.get("command")
        violations = _check_bash(cmd) if isinstance(cmd, str) and cmd.strip() else []
    else:
        return 0

    if violations:
        sys.stderr.write(
            "Blocked by guard_secrets (global security rule — no secrets in code/logs/commits):\n  - "
            + "\n  - ".join(violations)
            + "\nIf this is a false positive (e.g. a documented placeholder), tell the user; do not work around the guard.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
