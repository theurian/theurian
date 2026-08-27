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
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import get_type_hints

import pytest

from theurian.application.project_service import (
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    ProjectError,
    ProjectPaths,
)
from theurian.domain.state import StateHash
from theurian.domain.values import ContentHash
from theurian.security.project_config import PROJECT_CONFIG_FILE

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

#: A throwaway state hash for ``database_for``. Its value never reaches an
#: assertion: every escape test refuses on the ``state`` component before the
#: filename it derives is used.
_SAMPLE_STATE_HASH = StateHash(ContentHash("a" * 64))


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


# -- The descendant class: a committed symlink at any target, not just `.theurian` -----
#
# `.of`'s root-join check contains `.theurian` and its ancestors, but a clone can
# force-add a symlink at a *descendant* -- `.theurian/state -> ../../elsewhere`,
# past the ADR-0004 ignore -- and `.theurian` stays an honest directory that
# check waves through. `ProjectPaths._contained` closes that class for every path
# the class derives. The sweep below is exhaustive by reflection: a helper added
# later without containment is caught, not remembered.

#: How to call each path-returning helper, and which ``.theurian`` child a clone
#: would make an escaping symlink to reach it. `index_for` and `database_for` are
#: methods; the rest are properties. `_reflected_path_helpers` proves this map
#: names every helper on the class, so a new one that skips containment fails the
#: completeness test rather than passing unseen.
_HELPER_CALLS: dict[str, Callable[[ProjectPaths], Path]] = {
    "migrations": lambda p: p.migrations,
    "knowledge": lambda p: p.knowledge,
    "specifications": lambda p: p.specifications,
    "proposals": lambda p: p.proposals,
    "proposals_local": lambda p: p.proposals_local,
    "config": lambda p: p.config,
    "state": lambda p: p.state,
    "runtime": lambda p: p.runtime,
    "active_pointer": lambda p: p.active_pointer,
    "active_index_pointer": lambda p: p.active_index_pointer,
    "write_lock": lambda p: p.write_lock,
    "index_for": lambda p: p.index_for("01K1AAAAAA01234567890ABCDE"),
    "database_for": lambda p: p.database_for(_SAMPLE_STATE_HASH),
}

#: The ``.theurian`` child whose symlink escape reaches each helper. A leaf helper
#: (``active_pointer``) escapes through its parent directory (``state``); a
#: directory helper escapes through itself.
_ESCAPING_CHILD: dict[str, str] = {
    "migrations": "migrations",
    "knowledge": "knowledge",
    "specifications": "specifications",
    "proposals": "proposals",
    "proposals_local": "proposals-local",
    "config": PROJECT_CONFIG_FILE,
    "state": "state",
    "runtime": "runtime",
    "active_pointer": "state",
    "active_index_pointer": "state",
    "write_lock": "runtime",
    "index_for": "state",
    "database_for": "state",
}

#: The one helper deliberately contained by its *reader* rather than by
#: ``_contained``: ``migrations`` is consumed inside ``resolve_context``, where
#: the migration loader already refuses a directory that escapes the root, with a
#: culprit-naming remedy the CLI grades ``EXIT_STATE_ERROR`` (issue #233).
#: Routing it through ``_contained`` would pre-empt that richer refusal with a
#: coarser one and regrade a deliberate exit 4 to exit 1. It stays in the
#: reflection population (the property must hold for it) but out of the
#: ``_contained`` refusal sweep, and its own guard below pins the exclusion.
_READER_CONTAINED: set[str] = {"migrations"}


def _reflected_path_helpers() -> set[str]:
    """Every public member of :class:`ProjectPaths` that yields a ``Path``.

    Read off the class by reflection rather than remembered, so the sweep's
    coverage is a property of the shipped class and not of a hand-list that the
    next helper silently outgrows. ``of`` is excluded because it returns a
    :class:`ProjectPaths`, and ``_contained`` because it is the private
    chokepoint the sweep exists to prove every *other* member routes through --
    both fall out of the ``return is Path`` test rather than being named here.
    """
    found: set[str] = set()
    for name, member in vars(ProjectPaths).items():
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            function = member.fget
        elif isinstance(member, classmethod):
            function = member.__func__
        elif callable(member):
            function = member
        else:
            continue
        if function is None:
            continue
        if get_type_hints(function).get("return") is Path:
            found.add(name)
    return found


def test_the_containment_sweep_covers_every_path_returning_helper() -> None:
    """The reflection guard: a new path helper must join the sweep or fail here.

    Equality both ways -- a helper added to the class but not the map, or removed
    from the class but left in the map -- so the sweep cannot quietly stop being
    exhaustive. Every reflected helper is either swept through ``_contained`` or
    named in :data:`_READER_CONTAINED` with a reason; a new one is neither until a
    human classifies it, so it fails here rather than passing uncontained.
    """
    assert set(_HELPER_CALLS) == _reflected_path_helpers()
    assert set(_ESCAPING_CHILD) == set(_HELPER_CALLS)
    assert set(_HELPER_CALLS) >= _READER_CONTAINED


@_NEEDS_SYMLINKS
def test_migrations_is_contained_by_its_reader_not_the_chokepoint(tmp_path: Path) -> None:
    """Pin the one deliberate exclusion so it cannot be silently changed.

    ``migrations`` is not routed through ``_contained``: accessing it under an
    escaping-``migrations`` fixture returns the (uncontained) path rather than
    raising, because the migration loader contains it downstream with a
    deliberately richer, exit-4 refusal. If someone later routes it through
    ``_contained``, this goes RED and points at the reason in
    ``ProjectPaths.migrations`` -- the same regression the CLI validate tests
    (``test_validate_names_the_symlink_when_the_migrations_directory_escapes_the_project``)
    would show as an exit-code change.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".theurian" / "migrations").symlink_to(outside)
    paths = ProjectPaths.of(root)

    # No raise: the property hands back the path, and the loader refuses later.
    assert paths.migrations == root.resolve() / ".theurian" / "migrations"


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("helper", sorted(set(_HELPER_CALLS) - _READER_CONTAINED))
def test_every_path_helper_refuses_when_a_committed_symlink_escapes_under_it(
    tmp_path: Path, helper: str
) -> None:
    """The descendant class, swept over every helper the reflection guard found.

    For each helper, the ``.theurian`` child on its path is a symlink that leaves
    the tree -- the force-added-symlink shape a clone can carry past the ADR-0004
    ignore. Every helper must refuse rather than hand back a path a read or write
    would follow outside the working tree. A future helper that forgets
    ``_contained`` returns an uncontained path here and goes RED.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (root / ".theurian" / _ESCAPING_CHILD[helper]).symlink_to(outside)
    paths = ProjectPaths.of(root)

    with pytest.raises(ProjectError) as excinfo:
        _HELPER_CALLS[helper](paths)

    assert excinfo.value.remedy == KNOWLEDGE_DIR_ESCAPE_REMEDY


@_NEEDS_SYMLINKS
def test_a_not_yet_created_state_db_under_an_escaping_state_symlink_is_refused(
    tmp_path: Path,
) -> None:
    """Refinement: the creation case, where a naive ``path.resolve()`` slips.

    The state database does not exist yet -- a first ``migrate apply`` is about to
    create it -- so there is no inode to resolve. The escape is via its parent,
    the ``state`` symlink. Resolving the deepest *existing* directory entry
    (the ``state`` link itself) rather than the missing file is what refuses the
    write before it is created, closing the gap between "the file does not exist"
    and "its parent is a link that leaves the tree".
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".theurian" / "state").symlink_to(outside, target_is_directory=True)
    paths = ProjectPaths.of(root)

    database = root / ".theurian" / "state" / _SAMPLE_STATE_HASH.database_filename
    assert not database.exists(), "the DB must not exist, so the parent link is the only escape"
    with pytest.raises(ProjectError):
        paths.database_for(_SAMPLE_STATE_HASH)


@_NEEDS_SYMLINKS
def test_a_dangling_escaping_symlink_is_refused_not_read_as_absent(tmp_path: Path) -> None:
    """The dangling-target variant of the creation case.

    ``state -> ../../gone`` where the target does not exist: ``state.exists()`` is
    ``False``, so a walk that keyed on existence alone would step *past* the link
    to the honest ``.theurian`` and pass. The walk stops at the deepest directory
    *entry* -- a symlink counts even dangling -- and resolves the link itself,
    which lands outside the root. This is the crash-masked-as-absent shape the
    first descendant probe hit before the target dir was created.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    (root / ".theurian" / "state").symlink_to(Path("..") / ".." / "gone")
    paths = ProjectPaths.of(root)

    with pytest.raises(ProjectError):
        _ = paths.active_pointer


@_NEEDS_SYMLINKS
def test_a_symlinked_state_pointing_inside_the_root_writes_normally(tmp_path: Path) -> None:
    """Refinement (AC-2, no false rejection): escape-the-ROOT, not is-a-symlink.

    ``.theurian/state -> ../state_real`` where ``state_real`` sits inside the
    clone is a legitimate contained link, so it must resolve fine and a write
    through it must land inside the tree. The containment check refuses only a
    link whose resolved target leaves the root, never a link as such.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    (root / "state_real").mkdir()
    (root / ".theurian" / "state").symlink_to(Path("..") / "state_real")
    paths = ProjectPaths.of(root)

    assert paths.state.resolve().is_relative_to(root.resolve())
    paths.active_pointer.write_text("{}")

    assert (root / "state_real" / "active.json").read_text() == "{}"
