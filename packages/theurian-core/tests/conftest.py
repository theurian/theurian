"""Test configuration.

Puts this directory on ``sys.path`` so ``fakes`` is importable as a top-level
package. The suite runs under ``--import-mode=importlib``, which does not add
test directories to the path automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_ROOT = Path(__file__).parent
if str(_TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TESTS_ROOT))
