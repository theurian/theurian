"""``ProjectRegistry.load``'s raw-filesystem-failure translation (issue #205).

``ProjectRegistry._raw_entries`` -- the shared path behind ``load``, and so
behind ``project.list``, every project-scoped MCP tool, and ``project
status`` -- translates a bare ``OSError`` into ``ProjectError`` at two
separate ``try`` blocks: the ``.exists()`` probe (added when a *data
directory* at mode ``000`` was found to escape it, one level above where the
read-side translation already covered a *registry file* at mode ``000``;
issue #205's Class 1c) and the read itself. Both transformations survived a
full-suite run with either one reverted -- no existing test drives a
`chmod`-unreadable registry through `ProjectRegistry.load` at all. These two
tests are that drive, one per branch, and are the same class for a shared
reason: both convert the identical raw `OSError`, at the identical two-line
`except OSError as exc: raise ProjectError(...)` shape, to the identical
`_registry_reset_remedy`, and are proven here by the identical assertion.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from theurian.application.project_service import ProjectError, ProjectRegistry

pytestmark = pytest.mark.unit

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all --
#: the same guard `test_cli_commands.py` uses before a permission-refusal test.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


def _reset_remedy(path: Path) -> str:
    """The exact text `_registry_reset_remedy` produces, re-derived rather than
    imported: the function is private to `project_service.py`, and importing
    a private helper to build the expected value would make this test unable
    to notice a change to *what it returns*, only to whether it was called.
    """
    return (
        f"Delete {path} and re-register each project with `theurian project register`; "
        f"it is derived and holds nothing that is not also recoverable from each "
        f"project's own .theurian/."
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
    assert excinfo.value.remedy == _reset_remedy(registry.path)
