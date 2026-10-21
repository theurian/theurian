"""The environment every deciding ``git`` spawn runs under, pinned by equality (#981).

The expected mapping is written out here and never imported: a test that reads the
production constant moves with it, so mutating the constant would leave the pin
green. The parent is hostile -- it sets every one of the fourteen inherited
overrides, so a member removed from the stripped set shows up as a key in the child.
"""

from __future__ import annotations

import os
from typing import Final

import pytest

from theurian.infrastructure.git import environment
from theurian.infrastructure.git.environment import child_environment

pytestmark = pytest.mark.unit

#: The inherited variables a child ``git`` must never see, written out by hand.
OVERRIDES: Final[tuple[str, ...]] = (
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
)

#: What the child is forced to carry. ``GIT_ALLOW_PROTOCOL`` is set *and empty*: an
#: empty list allows no transport, which is the below-2.45 fallback for a lazy fetch.
FORCED: Final[dict[str, str]] = {
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_GRAFT_FILE": os.devnull,
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_ALLOW_PROTOCOL": "",
}

#: Variables the child must keep: ``git`` still has to be found, and a trace or
#: identity the caller set is not an isolation concern.
KEPT: Final[dict[str, str]] = {"PATH": "/parent/bin", "HOME": "/parent/home", "GIT_TRACE": "/t"}


def _hostile_parent() -> dict[str, str]:
    return {
        **KEPT,
        **{name: f"/parent/{name}" for name in OVERRIDES},
        "GIT_GRAFT_FILE": "/parent/grafts",
        "GIT_ALLOW_PROTOCOL": "file:https",
    }


def test_the_child_environment_is_exactly_the_kept_variables_plus_the_forced_ones() -> None:
    """Equality, so a leaked override or a missing forced value are the same failure."""
    child = child_environment(_hostile_parent())

    assert child == {**KEPT, **FORCED}


@pytest.mark.parametrize("name", OVERRIDES)
def test_each_inherited_override_is_stripped(name: str) -> None:
    """One name per case, so a single member dropped from the set names itself."""
    child = child_environment({**KEPT, name: "/parent/value"})

    assert name not in child


@pytest.mark.parametrize(("name", "value"), sorted(FORCED.items()))
def test_each_forced_value_overrides_an_inherited_one(name: str, value: str) -> None:
    """A parent that sets the variable to something else does not win."""
    child = child_environment({**KEPT, name: "inherited"})

    assert child[name] == value


def test_unrelated_variables_are_kept() -> None:
    child = child_environment({**KEPT, "LANG": "C"})

    assert child["LANG"] == "C"
    assert child["PATH"] == "/parent/bin"


def test_the_parent_mapping_is_not_mutated() -> None:
    parent = _hostile_parent()
    before = dict(parent)

    child_environment(parent)

    assert parent == before


def test_the_expected_mapping_can_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The companion: a forced value changed in production no longer equals the pin."""
    monkeypatch.setattr(environment, "_FORCED", {**environment._FORCED, "GIT_NO_LAZY_FETCH": "0"})

    assert child_environment(_hostile_parent()) != {**KEPT, **FORCED}
