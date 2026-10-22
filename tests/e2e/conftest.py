"""Puts the core test tree and this directory on ``sys.path``.

That makes ``migration_fixtures`` and ``daemon_session`` importable here.

The same mechanism ``tests/unit/tools/conftest.py`` uses for ``tools/``. The
migration fixtures in this directory declare a ``contentSha256`` the schema
requires (ADR-0027), and the digest helper lives with the rest of the suite's
shared builders under ``packages/theurian-core/tests/``. That directory is
already put on the path by its own ``conftest.py``, but only once pytest has
collected it -- which a run of ``tests/e2e`` alone never does.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CORE_TESTS = Path(__file__).resolve().parents[2] / "packages" / "theurian-core" / "tests"

for _path in (_CORE_TESTS, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
