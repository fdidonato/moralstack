"""
Yaml constitution loader.

Uses EXCLUSIVELY ruamel.yaml (typ="safe").
Fail-fast: YAML empty or invalid raises ConstitutionLoadError.
"""

from __future__ import annotations

from pathlib import Path


def _get_yaml():
    """YAML parser lazy-initialized at first call (avoids side effects at import-time)."""
    from ruamel.yaml import YAML

    return YAML(typ="safe")


class ConstitutionLoadError(Exception):
    """
    Raised when constitution loading or validation fails.

    Attributes:
        path: Path of the file (if applicable).
        field: Invalid field (if applicable).
        reason: Error message.
    """

    def __init__(
        self,
        message: str,
        *,
        path: Path | str | None = None,
        field: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.field = field
        self.reason = reason or message
        parts = [str(self.reason)]
        if self.path:
            parts.append(f"file={self.path}")
        if self.field:
            parts.append(f"field={self.field}")
        super().__init__(" | ".join(parts))


def load_yaml_file(path: Path) -> dict:
    """
    Loads YAML file into a dictionary.

    Unico punto del codice che legge YAML; usa solo ruamel.yaml (safe).

    Args:
        path: Path del file YAML.

    Returns:
        Dizionario radice del YAML.

    Raises:
        ConstitutionLoadError: Se il file è vuoto o il parsing fallisce.
        FileNotFoundError: Se il file non esiste (non gestito qui).
    """
    with open(path, "r", encoding="utf-8") as f:
        data = _get_yaml().load(f)
    if data is None:
        raise ConstitutionLoadError(
            "YAML vuoto",
            path=path,
            field="(root)",
            reason="File YAML vuoto o senza contenuto.",
        )
    if not isinstance(data, dict):
        raise ConstitutionLoadError(
            "YAML root non è un mapping",
            path=path,
            field="(root)",
            reason="La radice del file deve essere un oggetto YAML (mapping).",
        )
    return data
