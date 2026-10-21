"""The environment every deciding ``git`` spawn runs under (ADR-0029 D7, ADR-0033).

One construction shared by the trailer read, the fixCommit verdict and the
migration committed check, so a repository-local or inherited input cannot
redirect what git reads or make it reach a promisor remote (#981).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

#: Inherited ``GIT_*`` environment variables the child ``git`` must not see, so no
#: ambient process state can redirect what the repository's history means (ADR-0029
#: Amendment 1, D7). Stripping a variable can only make ``git`` fall back to its
#: default (this repository, its real object store, no injected config) -- never to
#: a wrong answer -- so the list is a defensive superset. It covers three classes:
#: *which repository/objects* answer (``GIT_DIR``, ``GIT_WORK_TREE``,
#: ``GIT_OBJECT_DIRECTORY``, ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_COMMON_DIR``,
#: ``GIT_INDEX_FILE``, ``GIT_CEILING_DIRECTORIES``); *ref resolution and replacement*
#: (``GIT_NAMESPACE``, ``GIT_REPLACE_REF_BASE``); and *config injection*
#: (``GIT_CONFIG``, the ``GIT_CONFIG_{COUNT,GLOBAL,SYSTEM}`` trio, and
#: ``GIT_CONFIG_PARAMETERS`` -- the last injects config as if by ``-c``, so an
#: inherited ``i18n.logOutputEncoding=UTF-16`` there would make ``git log`` emit
#: non-UTF-8 and break the parse). Defined here rather than imported from ``tools/``
#: or the test tree (which keep their own copy for a different read) so the adapter
#: owns its own contract.
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_REPLACE_REF_BASE",
        "GIT_WORK_TREE",
    }
)

#: Variables set on top of the stripped parent. Each has its measured reason.
_FORCED: Final = {
    "GIT_NO_REPLACE_OBJECTS": "1",
    # Grafts are not stopped by GIT_NO_REPLACE_OBJECTS; this overrides an
    # inherited value too and leaves no repo-local graft file readable.
    "GIT_GRAFT_FILE": os.devnull,
    # Honoured from git 2.45.0: stops every lazy fetch from a promisor remote.
    "GIT_NO_LAZY_FETCH": "1",
    # Fallback below git 2.45. git reads this env list before any config, and an
    # empty list allows no transport; `-c protocol.allow=never` is defeated by a
    # repo-local `protocol.file.allow=always`, this is not.
    "GIT_ALLOW_PROTOCOL": "",
}


def child_environment(parent: Mapping[str, str]) -> dict[str, str]:
    """``parent`` minus the inherited ``GIT_*`` overrides, plus the forced isolation."""
    env = {name: value for name, value in parent.items() if name not in _INHERITED_GIT_OVERRIDES}
    env.update(_FORCED)
    return env
