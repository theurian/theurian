"""The environment a spawned ``gh`` receives, pinned by equality (ADR-0030 clause 4(i)).

**The expected mapping is written out here and never imported**, and that is the
correction of an inverted claim rather than a style preference. A test that reads
the production constant *moves with it*: mutate the constant and both sides
change, so the test SURVIVES -- measured by ADR-0030's round-three adversarial
review. A test-side expectation is what makes it killable, and
:func:`test_the_expected_mapping_can_fail` is the companion that shows it.

Membership is by **key and value**, and an **empty string is a present key, not
an absent one**: ``gh`` treats an empty config-locating variable as absent and
falls through its precedence chain, so the two are the same thing *to gh* and
different *mappings*. This file pins which one the constant carries.

The parent below is deliberately hostile. It carries every input ADR-0030
measured as able to move the destination or the identity -- ``GH_HOST`` (run B),
``HTTPS_PROXY`` (run C), the four token variables -- plus ``GH_DEBUG``, whose
``api`` value makes ``gh`` print request detail. None of them reaches the child,
and the equality is what says so rather than five separate absence assertions
that would each have to be remembered.

**A parent that does not set a variable cannot observe it crossing**, and that is
the direction the equality used to be blind in. It fails for a member *removed*
from ``FORWARDED_BY_VALUE`` and for a value changed, because the parent sets
those three. It could not fail for a member **added** that the parent happened
not to set: the constant would forward a variable, the parent would have none to
forward, and the child would come out identical. That is exactly the direction
``environment.py``'s recorded Linux gap points in -- ``DBUS_SESSION_BUS_ADDRESS``
and ``XDG_RUNTIME_DIR``, the two the Secret Service credential store is reached
through, whose admission the constant refuses on measurement grounds. So the
parent carries both as decoys, and
:func:`test_the_hostile_parent_sets_every_variable_the_child_may_forward` closes
the same direction for a name nobody has thought of: a member added to the
forwarded set that the parent does not set fails there before it can pass here.
"""

from __future__ import annotations

from typing import Final

import pytest

from theurian.infrastructure.github import environment

pytestmark = pytest.mark.unit

#: A parent environment carrying every measured destination- and identity-moving
#: input, so the equality below is asserted against something that would be a
#: leak if any of it crossed.
#:
#: ``DBUS_SESSION_BUS_ADDRESS`` and ``XDG_RUNTIME_DIR`` are here for the other
#: direction, and they are not destination-movers: they are the two variables
#: ``environment.py`` records *declining* to forward, because ``gh``'s Linux
#: credential store reaches the Secret Service through them and no measurement
#: exists. A parent that did not set them could not tell an unforwarded variable
#: from a forwarded one, so admitting either to ``FORWARDED_BY_VALUE`` would
#: leave the equality below green. With them set, it reddens.
PARENT: Final[dict[str, str]] = {
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/parent/run/bus",
    "GH_CONFIG_DIR": "/parent/gh-config",
    "GH_DEBUG": "api",
    "GH_ENTERPRISE_TOKEN": "enterprise-parent-value",
    "GH_HOST": "evil.test",
    "GH_TOKEN": "gh-parent-value",
    "GITHUB_ENTERPRISE_TOKEN": "github-enterprise-parent-value",
    "GITHUB_TOKEN": "github-parent-value",
    "HOME": "/parent/home",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "LANG": "en_US.UTF-8",
    "PATH": "/parent/bin:/somewhere/else",
    "XDG_CONFIG_HOME": "/parent/xdg",
    "XDG_RUNTIME_DIR": "/parent/run/1000",
}

#: The whole environment the child must receive, written out here.
#:
#: Eight entries: three forwarded by value, four literals, and the fixed
#: ``PATH``. Nothing else -- and in particular no token, because identity comes
#: from the operator's persisted ``gh`` login and never from a caller's
#: environment.
EXPECTED: Final[dict[str, str]] = {
    "GH_CONFIG_DIR": "/parent/gh-config",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_PROMPT_DISABLED": "1",
    "HOME": "/parent/home",
    "NO_COLOR": "1",
    "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "XDG_CONFIG_HOME": "/parent/xdg",
}

#: The four variables that would hand a spawned ``gh`` an identity the caller
#: chose. Listed for the message, not for the assertion: the equality above
#: already excludes them, and a second list that could drift from it would be the
#: thing that goes stale.
TOKEN_VARIABLES: Final[tuple[str, ...]] = (
    "GH_ENTERPRISE_TOKEN",
    "GH_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_TOKEN",
)


def test_the_hostile_parent_sets_every_variable_the_child_may_forward() -> None:
    """The fixture guard that makes the equality below able to fail in *both* directions.

    Forwarding is observable only for a variable the parent sets. A member added
    to ``FORWARDED_BY_VALUE`` that :data:`PARENT` does not carry crosses nothing,
    so the child comes out byte-identical and the equality stays green while the
    constant has grown -- the direction ``environment.py``'s Linux gap points in,
    and the direction a reviewer found the pin blind in.

    The two decoys close it for the two names that gap is about. This closes it
    for the ones nobody has named: whatever is added to the forwarded set, the
    parent has to gain a value for it in the same change, and only then does the
    equality below get to judge whether it crossed.
    """
    forwarded = set(environment.FORWARDED_BY_VALUE)

    assert forwarded <= set(PARENT), (
        f"`FORWARDED_BY_VALUE` carries {sorted(forwarded - set(PARENT))}, which the "
        f"hostile parent does not set. A variable the parent has no value for cannot "
        f"be seen crossing, so the equality below would pass with the constant "
        f"forwarding it. Give `PARENT` a decoy value for each new member -- and if "
        f"the member is meant to reach the child, `EXPECTED` gains a row and "
        f"ADR-0030 decision 1's table gains its admission ground."
    )


def test_the_child_environment_is_exactly_the_recorded_mapping() -> None:
    """RED for every wrong mapping: a passed-through variable, a missing member, a wrong value.

    An equality rather than a set of absence checks, because a blocklist has to
    be right about every variable ``gh`` and its transport stack read, and a
    constructed environment only has to be right about the few this project
    deliberately passes. The burden is inverted on purpose.
    """
    child = environment.child_environment(PARENT)

    assert child == EXPECTED, (
        f"the child environment is not the recorded one.\n\n"
        f"  built   : {dict(sorted(child.items()))!r}\n\n"
        f"  recorded: {dict(sorted(EXPECTED.items()))!r}\n\n"
        f"Extra keys are the direction that matters most: a variable crossing "
        f"from the parent is either a destination ADR-0030 measured moving "
        f"(GH_HOST, HTTPS_PROXY, the gh config locators) or an identity the "
        f"caller chose ({', '.join(TOKEN_VARIABLES)}). If this mapping genuinely "
        f"changed, ADR-0030 decision 1's table changed with it and the admission "
        f"ground for the new row has to be written down."
    )


@pytest.mark.parametrize(
    ("label", "attribute", "value"),
    (
        ("a wrong PATH", "FIXED_PATH", "/parent/bin:/somewhere/else"),
        (
            "a missing literal member",
            "SET_TO_ONE",
            ("GH_NO_EXTENSION_UPDATE_NOTIFIER", "GH_NO_UPDATE_NOTIFIER", "NO_COLOR"),
        ),
        (
            "a token forwarded from the parent",
            "FORWARDED_BY_VALUE",
            ("GH_CONFIG_DIR", "GH_TOKEN", "HOME", "XDG_CONFIG_HOME"),
        ),
        (
            "a platform variable added to the forwarded set",
            "FORWARDED_BY_VALUE",
            (
                "DBUS_SESSION_BUS_ADDRESS",
                "GH_CONFIG_DIR",
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_RUNTIME_DIR",
            ),
        ),
    ),
    ids=(
        "a wrong PATH",
        "a missing literal member",
        "a token forwarded from the parent",
        "a platform variable added to the forwarded set",
    ),
)
def test_the_expected_mapping_can_fail(
    monkeypatch: pytest.MonkeyPatch, label: str, attribute: str, value: object
) -> None:
    """The companion: mutate the production constant and the pin above goes RED.

    This is what a test that *imported* the expected mapping could not do.
    Reading the constant makes both sides move together, so the same three
    mutations would leave it green -- which is how a test-side restatement stops
    being a stylistic preference and becomes the thing that makes clause 4(i) a
    control.
    """
    monkeypatch.setattr(environment, attribute, value)

    assert environment.child_environment(PARENT) != EXPECTED, (
        f"{label}: the production constant was mutated and the recorded mapping "
        f"still matched. The pin above cannot fail, so it proves nothing."
    )


def test_an_empty_config_locator_crosses_as_an_empty_value_not_as_an_absence() -> None:
    """``gh`` treats the two the same; the mapping does not, and this pins which.

    Forwarded *by value* means the parent's value crosses unchanged, and an empty
    string is a value. The transport guard resolves the same chain with the same
    empty-is-absent rule ``gh`` uses, so the check and the child still read one
    directory -- what would break is a constant that quietly dropped the key and
    a reader who then could not tell which of the two the child saw.
    """
    child = environment.child_environment({**PARENT, "GH_CONFIG_DIR": ""})

    assert "GH_CONFIG_DIR" in child
    assert child["GH_CONFIG_DIR"] == ""


def test_a_config_locator_the_parent_does_not_set_is_absent_from_the_child() -> None:
    """The other half of the same rule: nothing is invented for an unset variable."""
    parent = {name: value for name, value in PARENT.items() if name != "HOME"}

    assert "HOME" not in environment.child_environment(parent)


def test_the_child_environment_shares_no_object_with_the_parent() -> None:
    """A fresh mapping each call, so no caller can mutate what a later spawn sees."""
    parent = dict(PARENT)
    child = environment.child_environment(parent)
    child["PATH"] = "/mutated"

    assert parent["PATH"] == PARENT["PATH"]
    assert environment.child_environment(parent)["PATH"] == environment.FIXED_PATH
