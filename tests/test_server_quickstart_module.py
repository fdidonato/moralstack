"""ASGI export checks for ``examples.server_quickstart``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def test_server_quickstart_warns_single_worker_for_multiturn():
    text = Path("examples/server_quickstart.py").read_text(encoding="utf-8")
    assert "uvicorn" in text
    lower = text.lower()
    assert "worker" in lower
    assert "multi-turn" in lower


def test_server_quickstart_exports_app_without_running_uvicorn(monkeypatch):
    monkeypatch.setattr("examples.server_quickstart._bootstrap_pipeline", lambda _cfg: MagicMock())
    monkeypatch.setattr("examples.server_quickstart.OpenAI", MagicMock)
    import importlib

    import examples.server_quickstart as sq

    importlib.reload(sq)
    assert hasattr(sq, "build_app")
    assert hasattr(sq, "app")
    built = sq.build_app()
    assert callable(built)
    assert built is not None
    assert sq.app is not None
    src = Path("examples/server_quickstart.py").read_text(encoding="utf-8")
    assert "uvicorn.run" in src
    assert "__name__" in src
