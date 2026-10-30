"""The environment every deciding ``git`` spawn runs under, pinned by equality (#981).

The expected mapping is written out here and never imported: a test that reads the
production constant moves with it, so mutating the constant would leave the pin
green. The predicate: every inherited ``GIT_*`` variable goes, tracing included, and
then the forced set applies.
"""

from __future__ import annotations

import os
from typing import Final

import pytest
from git_isolation_fixtures import parse_git_version

from theurian.infrastructure.git.environment import child_environment

pytestmark = pytest.mark.unit

#: Inherited ``GIT_*`` variables written out by hand, one per way a spawn is redirected.
HOSTILE: Final[tuple[str, ...]] = (
    "GIT_DIR",
    "GIT_EXEC_PATH",
    "GIT_SHALLOW_FILE",
    "GIT_TEST_COMMIT_GRAPH",
    "GIT_ATTR_SOURCE",
    "GIT_CONFIG_PARAMETERS",
    "GIT_TRACE",
    "GIT_TRACE2_EVENT",
)

#: What the child is forced to carry. ``GIT_ALLOW_PROTOCOL`` is set *and empty*: an
#: empty list allows no transport, which is the below-2.45 fallback for a lazy fetch.
FORCED: Final[dict[str, str]] = {
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_GRAFT_FILE": os.devnull,
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_ALLOW_PROTOCOL": "",
    "GIT_TRACE2": "0",
    "GIT_TRACE2_EVENT": "0",
    "GIT_TRACE2_PERF": "0",
}

#: Variables the child must keep: ``git`` still has to be found, and ``GITHUB_TOKEN``
#: starts with ``GIT`` but not ``GIT_``.
KEPT: Final[dict[str, str]] = {
    "PATH": "/parent/bin",
    "HOME": "/parent/home",
    "GITHUB_TOKEN": "t",
}


def _hostile_parent() -> dict[str, str]:
    return {
        **KEPT,
        **{name: f"/parent/{name}" for name in HOSTILE},
        "GIT_NO_LAZY_FETCH": "0",
        "GIT_GRAFT_FILE": "/parent/grafts",
        "GIT_ALLOW_PROTOCOL": "file:https",
    }


def test_the_child_environment_is_exactly_the_kept_variables_plus_the_forced_ones() -> None:
    """Equality, so a leaked override or a missing forced value are the same failure."""
    child = child_environment(_hostile_parent())

    assert child == {**KEPT, **FORCED}


def test_the_parent_mapping_is_not_mutated() -> None:
    parent = _hostile_parent()
    before = dict(parent)

    child_environment(parent)

    assert parent == before


@pytest.mark.parametrize(
    ("text", "expected"),
    [("git version 2.44.1", (2, 44)), ("git version 2.54.0 (Apple Git-157)", (2, 54))],
)
def test_the_git_version_parser_reads_both_the_plain_and_the_apple_banner(
    text: str, expected: tuple[int, int]
) -> None:
    assert parse_git_version(text) == expected
