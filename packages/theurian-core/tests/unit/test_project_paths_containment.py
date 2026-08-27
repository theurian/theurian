"""``ProjectPaths.of`` contains the ``.theurian`` join (#237, SEC-7, T-5).

Every path Theurian reads or writes for a project is derived from
``ProjectPaths.knowledge_dir``, which is ``<root>/.theurian``. The root is
resolved before the join, but the join itself was not -- so a working tree whose
``.theurian`` is a symbolic link pointing outside the tree turned every derived
read and write into one outside it. A clone can deliver exactly that: a committed
``.theurian -> ../elsewhere`` symlink (#237). These tests pin the containment at
the join, upstream of every helper, where a single refusal closes both the write
faces (state database, active pointer, write lock) and the read faces at once.

The end-to-end reproduction through the real CLI -- ``migrate apply`` writing
state outside the clone, then ``migrate status`` reading it back -- lives in
``tests/integration/test_cli_commands.py``; this file drives the fix locus
directly.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

from theurian.application.project_service import (
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    ProjectError,
    ProjectPaths,
)

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def test_an_honest_real_theurian_resolves_to_a_contained_knowledge_dir(tmp_path: Path) -> None:
    """AC-3: a real ``.theurian`` directory is derived exactly as before.

    The join is unchanged when nothing on its path is a link, so every helper
    keeps naming ``.theurian`` and the containment costs the honest path nothing.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)

    paths = ProjectPaths.of(root)

    assert paths.knowledge_dir == root.resolve() / ".theurian"
    assert paths.state == root.resolve() / ".theurian" / "state"


def test_a_theurian_that_does_not_exist_yet_is_not_refused(tmp_path: Path) -> None:
    """A fresh clone whose ``.theurian`` has not been created still resolves.

    ``init`` and the first ``migrate apply`` run against a root that has no
    ``.theurian`` yet; a containment check that rejected a not-yet-existing join
    would refuse every project on its first command.
    """
    root = tmp_path / "repo"
    root.mkdir()

    paths = ProjectPaths.of(root)

    assert paths.knowledge_dir == root.resolve() / ".theurian"


@_NEEDS_SYMLINKS
def test_a_symlinked_theurian_pointing_outside_the_tree_is_refused(tmp_path: Path) -> None:
    """AC-1 at the join: the reproduced #237 shape, refused before any helper.

    ``.theurian -> ../shared`` resolves outside the working tree, so the state
    database, active pointer and write lock every write helper derives would
    land in ``shared``. The refusal is raised here rather than at each helper,
    so it holds for reads too.
    """
    root = tmp_path / "repo"
    root.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (root / ".theurian").symlink_to(shared, target_is_directory=True)

    with pytest.raises(ProjectError) as excinfo:
        ProjectPaths.of(root)

    assert excinfo.value.remedy == KNOWLEDGE_DIR_ESCAPE_REMEDY
    assert str(root.resolve()) in str(excinfo.value)


@_NEEDS_SYMLINKS
def test_the_refusal_is_not_defeated_by_a_symlink_that_resolves_to_itself(tmp_path: Path) -> None:
    """The self-referential comparison ``index_for`` makes must not decide this.

    If containment compared the join to its *own* resolution rather than to the
    root, an escaped ``.theurian`` would resolve to the escaped location and
    compare equal to it -- trivially "contained". The check is anchored to the
    resolved root, so the escape is caught regardless of what the link targets.
    """
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / "state").mkdir(parents=True)
    (root / ".theurian").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectError):
        ProjectPaths.of(root)


@_NEEDS_SYMLINKS
def test_a_symlinked_theurian_pointing_inside_the_tree_is_allowed(tmp_path: Path) -> None:
    """Containment, not a blanket ban on links: a contained ``.theurian`` works.

    ``.theurian -> real-theurian`` where both sit inside the tree resolves back
    inside the root, so it is not an escape. The join keeps its ``.theurian``
    name for `initialize_project`'s reporting and the managed ``.gitignore``
    block; only the escaping case is refused.
    """
    root = tmp_path / "repo"
    (root / "real-theurian" / "state").mkdir(parents=True)
    (root / ".theurian").symlink_to(root / "real-theurian", target_is_directory=True)

    paths = ProjectPaths.of(root)

    assert paths.knowledge_dir == root.resolve() / ".theurian"
    # A helper that resolves (`index_for`) stays inside the root, because the
    # link the join follows lands inside it.
    assert paths.state.resolve().is_relative_to(root.resolve())


def test_a_knowledge_directory_that_will_not_resolve_is_a_project_error_not_a_valueerror(
    tmp_path: Path,
) -> None:
    """The ``except`` arm: ``resolve`` can raise instead of answering a location.

    An embedded NUL makes ``Path.resolve`` raise ``ValueError``; a name the
    platform rejects makes it raise ``OSError``. Neither is a ``TheurianError``,
    and callers of ``ProjectPaths.of`` only narrow to that -- so a join that will
    not resolve is refused with the same remedy as one that resolves outside,
    rather than escaping as a raw exception. Modelled on ``index_for``'s and
    ``entry_root``'s conversions of the identical pair.
    """
    with pytest.raises(ProjectError) as excinfo:
        ProjectPaths.of(tmp_path, PurePosixPath(".theurian\x00evil"))

    assert excinfo.value.remedy == KNOWLEDGE_DIR_ESCAPE_REMEDY


@_NEEDS_SYMLINKS
def test_a_symlinked_ancestor_of_the_knowledge_dir_that_escapes_is_refused(tmp_path: Path) -> None:
    """Family 1: the escape can be an *ancestor* of the join, not the join itself.

    A nested knowledge directory (``nested/.theurian``) under a symlinked
    ``nested`` that leaves the tree escapes exactly as a symlinked ``.theurian``
    does. Resolving the whole join rather than testing only its last component
    is what reaches the ancestor link.
    """
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside-nested"
    outside.mkdir()
    (root / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProjectError) as excinfo:
        ProjectPaths.of(root, PurePosixPath("nested/.theurian"))

    assert excinfo.value.remedy == KNOWLEDGE_DIR_ESCAPE_REMEDY
