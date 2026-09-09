"""``ProjectRegistry.load``'s refusals, and which cure each one arrives with.

**The translation (issue #205).** ``ProjectRegistry._raw_entries`` -- the shared
path behind ``load``, and so behind ``project.list``, every project-scoped MCP
tool, and ``project status`` -- translates a bare ``OSError`` into
``ProjectError`` at two separate ``try`` blocks: the ``.exists()`` probe (added
when a *data directory* at mode ``000`` was found to escape it, one level above
where the read-side translation already covered a *registry file* at mode
``000``; issue #205's Class 1c) and the read itself. Both transformations
survived a full-suite run with either one reverted -- no existing test drives a
`chmod`-unreadable registry through `ProjectRegistry.load` at all. The
parametrized test is that drive, one case per branch. They are one class for the
translation -- the identical raw `OSError`, at the identical two-line
`except OSError as exc: raise ProjectError(...)` shape -- and two cases for the
cure, which is the half that is *not* identical: an unreadable file and an
unreadable data directory leave the reader able to do different things, so each
branch passes its own ``RegistryFailureArm`` and this test pins which.

**The cure (issue #381).** That remedy used to be pinned here by exact text,
which could notice the sentence changing but never that it was false -- and it
was: it promised that deleting the registry "holds nothing that is not also
recoverable from each project's own .theurian/", a costless-removal claim over
the one file holding every project's registration. The pin is now the shapes and
the byte pins in ``registry_deletion_cure_claims``, whose own tests are
``tests/unit/test_registry_deletion_cure_claims.py``. What this file adds is the
*wiring*: that each raise reaches for the arm written for the condition it
raises in. The third test drives the same wiring through a malformed body, so
the claim is asserted even where a ``chmod`` cannot refuse.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from registry_deletion_cure_claims import (
    RE_REGISTER_INVOCATION,
    assert_a_registry_cure_is_the_pinned_text,
    assert_registry_deletion_cure_shape,
)

from theurian.application.project_service import (
    ProjectError,
    ProjectRegistry,
    RegistryFailureArm,
)

pytestmark = pytest.mark.unit

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_cli_commands.py` uses before a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


def _assert_the_registry_cure(text: str, *, arm: RegistryFailureArm, path: Path) -> None:
    """The cure a refusal carries: the right shape, and the right arm's bytes.

    Both layers, in the order ``registry_deletion_cure_claims`` argues for. The
    shapes say what any cure for this file must hold -- no costless claim, the
    cost beside the deletion, an invitation before the destruction, and the
    ``theurian project register`` invocation that makes the recovery typeable.
    The byte pin then says *which* of the four texts arrived, which is the only
    way to catch a raise that reaches for the wrong arm: swapping the two
    ``OSError`` branches would leave every shape satisfied and tell the reader to
    ``chmod`` the wrong thing.
    """
    assert_registry_deletion_cure_shape(text, where=f"the {arm.value} cure at the raise")
    assert_a_registry_cure_is_the_pinned_text(
        text, arm=arm.value, path=path, where=f"the {arm.value} cure at the raise"
    )


def _make_data_directory_unreadable(registry: ProjectRegistry) -> Path:
    """`chmod 000` the registry's *parent* directory.

    `.exists()` must traverse the parent to stat the file inside it, so this
    drives the `.exists()` probe's own `except OSError`, not the read below
    it -- the probe never gets far enough to attempt a read.
    """
    registry.path.parent.chmod(0o000)
    return registry.path.parent


def _make_registry_file_unreadable(registry: ProjectRegistry) -> Path:
    """`chmod 000` the registry file itself, leaving its parent traversable.

    `.exists()` succeeds here -- stat needs no permission on the target
    itself -- so this drives the *read*-side `except OSError`, the other of
    the two branches this file pins.
    """
    registry.path.chmod(0o000)
    return registry.path


@dataclass(frozen=True)
class _AnUnreadableRegistry:
    """One ``OSError`` branch, and the arm whose cure it must raise with."""

    make_unreadable: Callable[[ProjectRegistry], Path]
    arm: RegistryFailureArm


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
@pytest.mark.parametrize(
    "case",
    [
        _AnUnreadableRegistry(
            _make_data_directory_unreadable, RegistryFailureArm.DIRECTORY_UNREADABLE
        ),
        _AnUnreadableRegistry(_make_registry_file_unreadable, RegistryFailureArm.FILE_UNREADABLE),
    ],
    ids=["data-directory-unreadable", "registry-file-unreadable"],
)
def test_load_refuses_an_unreadable_registry_with_the_cure_for_that_condition(
    tmp_path: Path,
    case: _AnUnreadableRegistry,
) -> None:
    """Both branches translate, and each cures the condition its reader is in.

    The translation is one behaviour: neither branch may let a bare ``OSError``
    escape ``load``. The cure is not -- a registry file at mode ``000`` can still
    be *removed* through a traversable parent, while a data directory at mode
    ``000`` blocks the deletion the cure goes on to offer. A single text served
    both until ``30e460b9`` and told the reader of an unopenable file to inspect
    it, which is the payload contradicting itself.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    registry = ProjectRegistry(path=data_dir / "projects.json")
    registry.path.write_text("{}", encoding="utf-8")

    unreadable = case.make_unreadable(registry)
    try:
        with pytest.raises(ProjectError) as excinfo:
            registry.load()
    finally:
        unreadable.chmod(0o700)

    assert f"{registry.path} cannot be opened" in str(excinfo.value)
    assert str(registry.path) in excinfo.value.remedy, "the file to inspect is named"
    _assert_the_registry_cure(excinfo.value.remedy, arm=case.arm, path=registry.path)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_mode_000_registry_is_still_removable_through_a_traversable_parent(
    tmp_path: Path,
) -> None:
    """The measurement the two unreadable arms are split on.

    ``test_only_the_directory_arm_says_the_deletion_itself_is_blocked`` pins that
    only the data-directory arm tells the reader the deletion cannot be done. That
    is a claim about POSIX, not about prose: unlinking needs write and search on
    the *directory*, and read on the file is irrelevant to it. Measured here
    rather than assumed, because if it were false the file-unreadable arm would be
    offering a deletion its own reader cannot perform -- and the fix would be a
    production change, not a wording one.
    """
    traversable = tmp_path / "traversable"
    traversable.mkdir(mode=0o700)
    unreadable_file = traversable / "projects.json"
    unreadable_file.write_text("{}", encoding="utf-8")
    unreadable_file.chmod(0o000)

    with pytest.raises(PermissionError):
        unreadable_file.read_text(encoding="utf-8")
    unreadable_file.unlink()

    assert not unreadable_file.exists(), (
        "a mode-000 registry is removable through a traversable parent, which is why the "
        "file-unreadable cure offers the deletion without qualifying it"
    )


def test_load_refuses_an_unparsable_registry_without_promising_a_costless_deletion(
    tmp_path: Path,
) -> None:
    """Issue #381's second half, and the PR #596 family it belongs to.

    ``projects.json`` is not derived. It is the enumeration of every project's
    registration, and an entry's ``registeredAt`` exists nowhere else -- no
    project's own ``.theurian/`` records that it was registered, let alone when.
    The remedy nevertheless told the operator that the file "holds nothing that
    is not also recoverable from each project's own .theurian/", which is a
    costless-removal claim over data-holding bytes: the shape PR #596's
    ``test_no_cure_claims_a_costless_removal_outside_the_shape_guard`` exists to
    refuse, met again at a different seam. RED on that claim at ``2d3c23bb``,
    GREEN at ``043f0c5e`` over the prose that replaced it.

    **Driven through a body that is not JSON rather than through a ``chmod``**,
    and that is what makes this the load-bearing pin rather than a duplicate of
    the parametrized test above. That one is skipped wherever a mode cannot
    refuse -- which includes a CI job running as root -- so on those runs it
    would leave the whole claim unasserted. A malformed body reaches
    ``registry_deletion_remedy`` on every platform and as every user, and reaches
    it on the arm for a file whose bytes the reader can still see.
    """
    registry = ProjectRegistry(path=tmp_path / "projects.json")
    registry.path.write_bytes(b'{"demo": {"rootPath"')

    with pytest.raises(ProjectError) as excinfo:
        registry.load()

    assert "cannot be read as JSON" in str(excinfo.value), (
        "the fixture must reach the whole-file refusal, not some narrower one"
    )
    assert RE_REGISTER_INVOCATION in excinfo.value.remedy, (
        "the recovery has to stay typeable -- the population reading this literal is "
        "`git grep -n 're-register each project with' packages/theurian-core/tests`"
    )
    _assert_the_registry_cure(
        excinfo.value.remedy, arm=RegistryFailureArm.UNPARSABLE, path=registry.path
    )
