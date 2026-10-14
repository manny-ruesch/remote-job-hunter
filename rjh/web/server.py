"""Point d'entrée ASGI standard (`rjh.web.server:app`) pour `rjh serve` et Docker."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DASHBOARD_DIR = ROOT_DIR / "dashboard"

for p in (ROOT_DIR, DASHBOARD_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from dashboard.dashboard_server import app  # noqa: E402

__all__ = ["app"]
