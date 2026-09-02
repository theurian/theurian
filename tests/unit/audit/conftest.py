"""Puts ``tools/audit/`` on ``sys.path`` so tests can import the census modules.

``tools/audit/`` is a flat script directory, not an installed package, and its
modules import each other by bare name (``import tracker_state``,
``from claim_surfaces import ...``) because that is what a script's own
directory on ``sys.path[0]`` gives them when run as
``uv run python tools/audit/<name>.py``. Reproducing that here is what lets a
test import the same module the audit runs, rather than a copy of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AUDIT_DIR = Path(__file__).resolve().parents[3] / "tools" / "audit"

if str(_AUDIT_DIR) not in sys.path:
    sys.path.insert(0, str(_AUDIT_DIR))
