"""Puts ``tools/`` on ``sys.path`` so tests can import ``mutate`` and friends.

``tools/`` is a flat script directory, not an installed package: the CLI
itself relies on the same mechanism (a script's own directory lands on
``sys.path[0]`` when run directly via ``uv run python tools/mutate.py``).
This conftest runs before pytest imports any test module in this directory,
so a plain top-level ``import mutate`` in a test file resolves correctly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[3] / "tools"

if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
