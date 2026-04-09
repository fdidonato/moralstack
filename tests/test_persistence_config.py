"""
Characterization tests for persistence config.

Documents current behavior of get_db_path, get_persist_mode, get_ui_credentials.
"""

from moralstack.persistence.config import (
    get_db_path,
    get_persist_mode,
    get_ui_credentials,
)


def test_get_db_path_default(monkeypatch):
    """get_db_path returns None when MORALSTACK_DB_PATH is not set."""
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    result = get_db_path()
    assert result is None


def test_get_db_path_set(monkeypatch):
    """get_db_path returns stripped path when env is set."""
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", "  /path/to/db.sqlite  ")
    result = get_db_path()
    assert result == "/path/to/db.sqlite"


def test_get_persist_mode_default_no_db(monkeypatch):
    """get_persist_mode returns file_only when no db path and no override."""
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_PERSIST_MODE", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_MODE", raising=False)
    result = get_persist_mode()
    assert result == "file_only"


def test_get_persist_mode_db_only_when_path_set(monkeypatch):
    """get_persist_mode returns db_only when MORALSTACK_DB_PATH is set."""
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_MODE", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", "/tmp/db.sqlite")
    monkeypatch.delenv("MORALSTACK_PERSIST_MODE", raising=False)
    result = get_persist_mode()
    assert result == "db_only"


def test_get_persist_mode_override(monkeypatch):
    """get_persist_mode respects MORALSTACK_PERSIST_MODE override."""
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_MODE", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "dual")
    result = get_persist_mode()
    assert result == "dual"


def test_get_ui_credentials_default(monkeypatch):
    """get_ui_credentials returns empty strings when not set."""
    monkeypatch.delenv("MORALSTACK_UI_USERNAME", raising=False)
    monkeypatch.delenv("MORALSTACK_UI_PASSWORD", raising=False)
    result = get_ui_credentials()
    assert result == ("", "")


def test_get_ui_credentials_set(monkeypatch):
    """get_ui_credentials returns (username, password) from env."""
    monkeypatch.setenv("MORALSTACK_UI_USERNAME", "admin")
    monkeypatch.setenv("MORALSTACK_UI_PASSWORD", "secret")
    result = get_ui_credentials()
    assert result == ("admin", "secret")
