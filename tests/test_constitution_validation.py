"""
Test validazione costituzione: CORE.BALANCE.1 deve essere caricato correttamente.
Fail-fast: YAML invalido o validazione Pydantic fallita → ConstitutionLoadError.
Nessun mock di ruamel o Pydantic.
"""

import tempfile
from pathlib import Path

import pytest

from moralstack.constitution import (
    REQUIRED_CORE_PRINCIPLE_ID,
    ConstitutionLoadError,
    ConstitutionStore,
)


def test_core_balance_1_loaded():
    """YAML valido → caricamento corretto, CORE.BALANCE.1 presente."""
    config_dir = Path(__file__).parent.parent / "config" / "constitution"
    if not (config_dir / "core.yaml").exists():
        pytest.skip("config/constitution/core.yaml non presente")
    store = ConstitutionStore(config_dir=config_dir)
    core = store.load_core()
    ids = [p.id for p in core]
    assert (
        REQUIRED_CORE_PRINCIPLE_ID in ids
    ), f"Required principle {REQUIRED_CORE_PRINCIPLE_ID} must be loaded from core.yaml"
    balance = next(p for p in core if p.id == REQUIRED_CORE_PRINCIPLE_ID)
    assert balance.level in ("hard", "soft")
    assert balance.title
    assert balance.rule


def test_get_constitution_exposes_loaded_ok():
    """get_constitution() espone constitution_loaded_ok e constitution_corrupted
    (sempre True/False con fail-fast)."""
    config_dir = Path(__file__).parent.parent / "config" / "constitution"
    if not (config_dir / "core.yaml").exists():
        pytest.skip("config/constitution/core.yaml non presente")
    store = ConstitutionStore(config_dir=config_dir)
    constitution = store.get_constitution(domain=None)
    assert hasattr(constitution, "constitution_loaded_ok")
    assert hasattr(constitution, "constitution_corrupted")
    assert constitution.constitution_loaded_ok is True
    assert constitution.constitution_corrupted is False


def test_yaml_invalid_raises():
    """YAML sintatticamente invalido → ConstitutionLoadError."""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        core_path = config_dir / "core.yaml"
        core_path.write_text("principles:\n  - id: X\n    level: invalid\n  bad: indent", encoding="utf-8")
        store = ConstitutionStore(config_dir=config_dir)
        with pytest.raises(ConstitutionLoadError) as exc_info:
            store.load_core()
        msg = str(exc_info.value)
        assert str(core_path) in msg or "level" in msg.lower() or "invalid" in msg.lower()


def test_yaml_unknown_field_raises():
    """Campo sconosciuto nel YAML (extra='forbid') → ConstitutionLoadError."""
    valid_core = """
principles:
  - id: CORE.BALANCE.1
    level: soft
    priority: 78
    title: Balance
    rule: "Present limitations and counterarguments."
unknown_field: 42
"""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        core_path = config_dir / "core.yaml"
        core_path.write_text(valid_core, encoding="utf-8")
        store = ConstitutionStore(config_dir=config_dir)
        with pytest.raises(ConstitutionLoadError):
            store.load_core()


def test_yaml_wrong_type_raises():
    """Tipo errato (es. priority non intero) → ConstitutionLoadError."""
    invalid_core = """
principles:
  - id: CORE.BALANCE.1
    level: soft
    priority: "not_an_int"
    title: Balance
    rule: "Present limitations."
"""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        (config_dir / "core.yaml").write_text(invalid_core, encoding="utf-8")
        store = ConstitutionStore(config_dir=config_dir)
        with pytest.raises(ConstitutionLoadError):
            store.load_core()


def test_yaml_empty_raises():
    """File YAML vuoto → ConstitutionLoadError."""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        (config_dir / "core.yaml").write_text("", encoding="utf-8")
        store = ConstitutionStore(config_dir=config_dir)
        with pytest.raises(ConstitutionLoadError) as exc_info:
            store.load_core()
        assert "vuoto" in str(exc_info.value).lower() or "empty" in str(exc_info.value).lower()
