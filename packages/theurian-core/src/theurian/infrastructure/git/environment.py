"""The environment every deciding ``git`` spawn runs under (ADR-0029 D7, ADR-0033).

Shared by the trailer read, the fixCommit verdict and the migration committed check (#981).
With each spawn's own flags it neutralises every documented git feature that can make a deciding
read admit what this repository's own objects do not, except two that are tracked: an
attribute-driven conversion on the committed check (#1015) and, below git 2.45, the fetch child's
reading of repository config (#1016). Features that can only make a read refuse are outside this
bound. A direct write to the object store, a cache file or a ref can still forge any answer.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

#: Variables set on top of the stripped parent. Each reason was measured unless marked source read.
_FORCED: Final = {
    "GIT_NO_REPLACE_OBJECTS": "1",
    # Grafts are not stopped by GIT_NO_REPLACE_OBJECTS; this overrides an
    # inherited value too and leaves no repo-local graft file readable.
    "GIT_GRAFT_FILE": os.devnull,
    # Honoured from git 2.45.0 and the v2.44.1 and v2.39.4 maintenance releases (source
    # read): stops every lazy fetch from a promisor remote.
    "GIT_NO_LAZY_FETCH": "1",
    # Fallback below git 2.45 (source read). git reads this env list before any config, and an
    # empty list allows no transport; `-c protocol.allow=never` is defeated by a
    # repo-local `protocol.file.allow=always`, this is not.
    "GIT_ALLOW_PROTOCOL": "",
}


def child_environment(parent: Mapping[str, str]) -> dict[str, str]:
    """``parent`` minus inherited ``GIT_*`` variables (not tracing), plus the forced isolation."""
    env = {
        name: value
        for name, value in parent.items()
        # Tracing redirects no read; aimed at the captured stdout it only makes the parse refuse.
        if not name.startswith("GIT_") or name.startswith("GIT_TRACE")
    }
    env.update(_FORCED)
    return env
