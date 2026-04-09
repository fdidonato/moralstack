"""
Persistence context variables — re-export from moralstack.observability.context.

Deprecated: import from moralstack.observability.context directly.
"""

from __future__ import annotations

from moralstack.observability.context import (  # noqa: F401
    get_current_cycle,
    get_current_request_id,
    get_current_run_id,
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
)
