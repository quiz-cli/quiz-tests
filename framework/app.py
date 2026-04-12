"""Provide the quiz-server app with per-test state isolation."""

import contextlib
import sys
from pathlib import Path

from fastapi import FastAPI

# Ensure quiz-server src is importable
_server_src = Path(__file__).resolve().parent.parent.parent / "quiz-server" / "src"
if str(_server_src) not in sys.path:
    sys.path.insert(0, str(_server_src))

from main import app  # noqa: E402
from models import Players, Results  # noqa: E402


def reset_app() -> FastAPI:
    """Clear all mutable state so each test starts clean."""
    Players._players.clear()  # noqa: SLF001
    Results._results.clear()  # noqa: SLF001
    for attr in ("quiz", "admin"):
        with contextlib.suppress(AttributeError, KeyError):
            delattr(app.state, attr)
    return app
