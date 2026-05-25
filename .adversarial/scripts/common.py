from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class AdversarialError(RuntimeError):
    pass


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists() or (candidate / ".adversarial").exists():
            return candidate
    return current


def adversarial_root(repo_root: Path) -> Path:
    root = repo_root / ".adversarial"
    if not root.exists():
        raise AdversarialError(f"Missing .adversarial directory under {repo_root}")
    return root


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise AdversarialError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: str, fallback: str = "task") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or fallback


def now_run_id(name: str | None = None) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(name)}" if name else stamp


def resolve_executable(binary: str) -> str | None:
    """Resolve a CLI binary robustly across POSIX and Windows.

    On Windows, Node/npm CLIs are commonly exposed as .cmd launchers.
    PowerShell can resolve them interactively, while subprocess(shell=False) may
    fail when only the bare command name is passed. This function resolves the
    concrete launcher path before subprocess is called.
    """
    if not binary:
        return None

    candidate = Path(binary)
    if candidate.is_absolute() or any(sep in binary for sep in ("/", "\\")):
        if candidate.exists():
            return str(candidate)
        found = shutil.which(binary)
        return found

    found = shutil.which(binary)
    if found:
        return found

    if os.name == "nt":
        pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PS1").split(";")
        names = [binary]
        if not Path(binary).suffix:
            names.extend(binary + ext.lower() for ext in pathext)
            names.extend(binary + ext.upper() for ext in pathext)
        for name in names:
            found = shutil.which(name)
            if found:
                return found

        # Last resort: ask cmd.exe to search PATH the same way many Windows shells do.
        try:
            proc = subprocess.run(
                ["cmd.exe", "/d", "/c", "where", binary],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            if proc.returncode == 0:
                first = proc.stdout.splitlines()[0].strip()
                if first:
                    return first
        except Exception:
            pass

    return None


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
    stdin_text: str | None = None,
    check: bool = False,
) -> CommandResult:
    original_command = list(command)
    if command:
        resolved = resolve_executable(command[0])
        if resolved:
            command = [resolved, *command[1:]]

    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            input=stdin_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        result = CommandResult(command=command, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except FileNotFoundError as exc:
        binary = original_command[0] if original_command else "unknown"
        result = CommandResult(
            command=original_command,
            returncode=127,
            stdout="",
            stderr=f"Executable not found: {binary}. Ensure it is installed and in PATH. {exc}",
        )
    if check and result.returncode != 0:
        raise AdversarialError(
            "Command failed:\n" + " ".join(result.command) + f"\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )
    return result


def safe_command_text(command: list[str]) -> str:
    return " ".join(command)


def command_exists(binary: str) -> bool:
    return resolve_executable(binary) is not None


def git_output(repo_root: Path, args: list[str], timeout: int = 30) -> str:
    result = run_command(["git", *args], cwd=repo_root, timeout=timeout, check=False)
    if result.returncode != 0:
        return f"[git {' '.join(args)} failed]\n{result.stderr.strip()}"
    return result.stdout.strip()


def copy_file_preserving_relative(repo_root: Path, src_relative: str, dst_root: Path) -> Path:
    src = repo_root / src_relative
    dst = dst_root / src_relative
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def iter_markdown_files(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.md") if p.is_file())


def extract_keywords(text: str, limit: int = 18) -> list[str]:
    stop = {
        "questo",
        "questa",
        "quello",
        "quella",
        "della",
        "delle",
        "degli",
        "dagli",
        "nella",
        "nelle",
        "vorrei",
        "voglio",
        "creare",
        "realizzare",
        "analizza",
        "analizzare",
        "piano",
        "lavoro",
        "codice",
        "codebase",
        "setup",
        "adversarial",
        "claude",
        "codex",
        "implementazione",
        "automatico",
        "automaticamente",
        "with",
        "that",
        "this",
        "from",
        "into",
        "about",
        "there",
        "their",
        "would",
        "should",
        "could",
        "must",
        "implementation",
        "planning",
        "investigation",
        "workflow",
        "project",
        "system",
    }
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{3,}|[A-Za-z0-9_\-]{5,}", text)
    seen: list[str] = []
    for token in tokens:
        lowered = token.lower().strip("-_")
        if len(lowered) < 4 or lowered in stop:
            continue
        if lowered not in seen:
            seen.append(lowered)
        if len(seen) >= limit:
            break
    return seen


def trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[... trimmed ...]\n\n" + text[-half:]


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped[7:]
    elif stripped.startswith("```"):
        stripped = stripped[3:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


def load_model_json(path: Path) -> dict[str, Any]:
    return json.loads(strip_code_fences(read_text(path)))


def write_run_report(path: Path, title: str, lines: list[str]) -> None:
    body = [f"# {title}", "", *lines, ""]
    write_text(path, "\n".join(body))
