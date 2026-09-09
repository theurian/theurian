"""``ProjectRegistry.load``'s refusals, and what the cure beside them may claim.

**The translation (issue #205).** ``ProjectRegistry._raw_entries`` -- the shared
path behind ``load``, and so behind ``project.list``, every project-scoped MCP
tool, and ``project status`` -- translates a bare ``OSError`` into
``ProjectError`` at two separate ``try`` blocks: the ``.exists()`` probe (added
when a *data directory* at mode ``000`` was found to escape it, one level above
where the read-side translation already covered a *registry file* at mode
``000``; issue #205's Class 1c) and the read itself. Both transformations
survived a full-suite run with either one reverted -- no existing test drives a
`chmod`-unreadable registry through `ProjectRegistry.load` at all. The
parametrized test is that drive, one case per branch, and they are the same
class for a shared reason: both convert the identical raw `OSError`, at the
identical two-line `except OSError as exc: raise ProjectError(...)` shape, to
the identical `_registry_reset_remedy`, and are proven here by the identical
assertion.

**The cure (issue #381).** That shared remedy used to be pinned here by exact
text, which could notice the sentence changing but never that it was false --
and it was: it promised that deleting the registry "holds nothing that is not
also recoverable from each project's own .theurian/", a costless-removal claim
over the one file holding every project's registration. The pin is now the
shape in ``registry_deletion_cure_claims``, and the third test drives it through
a malformed body so the claim is asserted even where a ``chmod`` cannot refuse.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from registry_deletion_cure_claims import assert_registry_deletion_cure_shape

from theurian.application.project_service import ProjectError, ProjectRegistry

pytestmark = pytest.mark.unit

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_cli_commands.py` uses before a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: The invocation five other pins already assert on -- `test_cli_commands.py`
#: (three), `test_unreadable_registry_surface.py` and `test_setup_service.py`.
#: Named here because it is the half of this remedy that must *survive* the
#: rewrite issue #381 asks for: a cure that drops it leaves the reader with a
#: file they have been told to delete and no way back.
RE_REGISTER_INVOCATION = "re-register each project with `theurian project register`"


def _assert_the_reset_remedy_shape(text: str, *, where: str) -> None:
    """What `_registry_reset_remedy` must say, pinned as a shape not as bytes.

    This was an exact-text comparison until issue #381, and the text it pinned
    was the defect: "it is derived and holds nothing that is not also
    recoverable from each project's own .theurian/" is a costless-removal claim
    over the one file that holds every project's registration. An equality pin
    could only notice that the sentence changed, never that it was false, and it
    would have to be rewritten byte for byte alongside any correction -- which is
    how a wrong claim survives its own fix.

    The three properties are shared with every other surface that offers to
    delete this file (``registry_deletion_cure_claims``); the invocation below is
    this remedy's own, because the ``_context_remedy`` defaults spell the
    recovery without naming the command.
    """
    assert_registry_deletion_cure_shape(text, where=where)
    assert RE_REGISTER_INVOCATION in text, (
        f"{where} must keep the recovery typeable -- five other pins read this exact "
        f"invocation, and a cure that only says 'delete it' has no way out: {text!r}"
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


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
@pytest.mark.parametrize(
    "make_unreadable",
    [_make_data_directory_unreadable, _make_registry_file_unreadable],
    ids=["data-directory-unreadable", "registry-file-unreadable"],
)
def test_load_raises_project_error_with_the_reset_remedy_when_unreadable(
    tmp_path: Path,
    make_unreadable: Callable[[ProjectRegistry], Path],
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    registry = ProjectRegistry(path=data_dir / "projects.json")
    registry.path.write_text("{}", encoding="utf-8")

    unreadable = make_unreadable(registry)
    try:
        with pytest.raises(ProjectError) as excinfo:
            registry.load()
    finally:
        unreadable.chmod(0o700)

    assert f"{registry.path} cannot be opened" in str(excinfo.value)
    assert str(registry.path) in excinfo.value.remedy, "the file to inspect is named"
    _assert_the_reset_remedy_shape(
        excinfo.value.remedy, where=f"the reset remedy for {make_unreadable.__name__}"
    )


def test_the_reset_remedy_does_not_promise_a_costless_deletion_of_the_registry(
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
    refuse, met again at a different seam. RED before the fix on that claim;
    GREEN after, over whatever prose replaces it.

    **Driven through a body that is not JSON rather than through a ``chmod``**,
    and that is what makes this the load-bearing pin rather than a duplicate of
    the parametrized test above. That one is skipped wherever a mode cannot
    refuse -- which includes a CI job running as root -- so on those runs it
    would leave the whole claim unasserted. A malformed body reaches the same
    ``_registry_reset_remedy`` on every platform and as every user.
    """
    registry = ProjectRegistry(path=tmp_path / "projects.json")
    registry.path.write_bytes(b'{"demo": {"rootPath"')

    with pytest.raises(ProjectError) as excinfo:
        registry.load()

    assert "cannot be read as JSON" in str(excinfo.value), (
        "the fixture must reach the whole-file refusal, not some narrower one"
    )
    _assert_the_reset_remedy_shape(excinfo.value.remedy, where="_registry_reset_remedy")
