"""
Persistence configuration — thin wrapper over moralstack.observability.config.

Deprecated: use moralstack.observability.config directly.
MORALSTACK_PERSIST_MODE and MORALSTACK_DB_PATH are still read as legacy aliases.
"""

# ruff: noqa: F401
from __future__ import annotations

from moralstack.observability.config import ObservabilityMode as PersistMode
from moralstack.observability.config import get_db_path as get_db_path
from moralstack.observability.config import get_observability_mode as get_persist_mode
from moralstack.observability.config import get_ui_credentials as get_ui_credentials
