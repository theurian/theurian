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

import functools
import inspect
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, get_args, get_type_hints

import pytest

from theurian.application.project_service import (
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    BuildProvenance,
    ProjectError,
    ProjectPathEscapeError,
    ProjectPaths,
    derived_escape_remedy,
    review_escape_remedy,
)
from theurian.cli.commands import _STATE_DATABASE_GLOB
from theurian.cli.index_commands import INDEX_FILENAME_PREFIX
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

    This is the driving test the arm's comment names, and the door it comes
    through is the embedded NUL: it makes ``Path.resolve`` raise ``ValueError``
    from the syscall layer (measured, ``lstat: embedded null character``). The
    ``OSError`` half of the arm is a contract guarantee, not a POSIX branch --
    ``resolve`` here is non-strict, and a symlink cycle, a dangling link and an
    over-length name were each measured 2026-09-07 to resolve without raising
    (Darwin), so only a stricter platform reaches it that way. Neither exception
    is a ``TheurianError``, and callers of ``ProjectPaths.of`` only narrow to that
    -- so a join that will not resolve is refused as the escape it is (the
    ``ProjectPathEscapeError`` the CLI grades ``EXIT_STATE_ERROR`` since #550),
    with the same remedy as one that resolves outside, rather than escaping as a
    raw exception.
    """
    with pytest.raises(ProjectPathEscapeError) as excinfo:
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
# check waves through. `_contain` closes that class.
#
# **The population this completeness argument ranges over is every WRITER (and
# reader) under the project tree, not the convenient set of enumerable
# `ProjectPaths` members.** That distinction is the whole point: a reflection
# test that reads GREEN while `theurian init` mkdir's the knowledge subtree at a
# symlink's out-of-tree target is a false completeness claim. So the population is:
#
#   1. Every `ProjectPaths` path helper -- swept below by reflection over member
#      *shapes* (property, cached_property, method; `Path`, `Path | None`, or
#      unannotated), so no shape escapes enumeration.
#   2. `initialize_project` -- a writer that is not a member, routed through the
#      same `_contain` chokepoint and pinned by its own integration test
#      (`test_cli_commands.py::test_init_refuses_an_escaping_knowledge_symlink_*`).
#   3. The `ingest` manifest's `.theurian/cache/ingestion.json`, which used to be
#      named here as out of scope: a *different* root cause -- derived,
#      git-ignored state a repository should not carry (the GHSA-266v class),
#      filed #394 and closed by giving it a helper. It joins (1) as
#      `ingestion_manifest`, which is why this entry now reads as a member rather
#      than as an exclusion.
#   4. The three `.json.tmp` leaves the atomic publishers build. A derivation
#      (`pointer.with_suffix(".json.tmp")`) produces a leaf the published name's
#      containment never covered, and the write through it followed a planted
#      link out of the tree at exit 0 (#523). They join (1) as the
#      `*_temporary` helpers.
#
# **Containment is half of (3) and (4), and the sweep below proves only that
# half.** A link whose target is inside the tree resolves inside it and passes
# `_contain`, correctly -- what refuses that one is `O_NOFOLLOW` inside the write
# (`theurian.security.no_follow`), driven by
# `tests/unit/test_no_follow_writes.py` and by the CLI plants in
# `tests/integration/test_derived_path_symlink_writes.py`.
#
# A new writer joins (1) by reflection, or gets its own named containment plus a
# test as (2) does -- never a silent exclusion, the treatment `migrations` gets.

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
    "review": lambda p: p.review,
    "config": lambda p: p.config,
    "state": lambda p: p.state,
    "runtime": lambda p: p.runtime,
    "active_pointer": lambda p: p.active_pointer,
    "active_index_pointer": lambda p: p.active_index_pointer,
    "index_secret_scan": lambda p: p.index_secret_scan,
    "active_pointer_temporary": lambda p: p.active_pointer_temporary,
    "active_index_pointer_temporary": lambda p: p.active_index_pointer_temporary,
    "index_secret_scan_temporary": lambda p: p.index_secret_scan_temporary,
    "ingestion_manifest": lambda p: p.ingestion_manifest,
    "write_lock": lambda p: p.write_lock,
    "index_for": lambda p: p.index_for("01K1AAAAAA01234567890ABCDE"),
    "state_database_named": lambda p: p.state_database_named("theurian-state-abc123.sqlite"),
    "database_for": lambda p: p.database_for(_SAMPLE_STATE_HASH),
    "findings_for": lambda p: p.findings_for("01K1AAAAAA01234567890ABCDE"),
    "review_search_for": lambda p: p.review_search_for("01K1AAAAAA01234567890ABCDE"),
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
    "review": "review",
    "config": PROJECT_CONFIG_FILE,
    "state": "state",
    "runtime": "runtime",
    "active_pointer": "state",
    "active_index_pointer": "state",
    "index_secret_scan": "state",
    "active_pointer_temporary": "state",
    "active_index_pointer_temporary": "state",
    "index_secret_scan_temporary": "state",
    "ingestion_manifest": "cache",
    "write_lock": "runtime",
    "index_for": "state",
    "state_database_named": "state",
    "database_for": "state",
    "findings_for": "state",
    "review_search_for": "state",
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


#: The helpers whose refused path is a *leaf under* a derived subdirectory, and
#: which therefore publish :func:`derived_escape_remedy` rather than
#: :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY` (#483 round one, H-1).
#:
#: Written out as a judgement rather than recomputed from
#: ``DERIVED_SUBDIRECTORIES``, which would be this test asking production the
#: question production is being tested on. Read off the helper list by hand: each
#: of these asks for something *inside* ``state``, ``runtime`` or ``cache``, while
#: ``state`` and ``runtime`` themselves ask for the directory and keep the older
#: text -- the same wrong-artifact shape one level up, which belongs to #525's
#: population rather than to this fix's.
#:
#: ``index_for`` is deliberately absent even though it reads under ``state``: its
#: own escape check raises before ``_contained`` sees the leaf, with
#: ``INDEX_POINTER_REMEDY``, and the ``self.state`` access it makes first refuses
#: as the *directory* case. Its presence here would have passed for the wrong
#: reason, which is why the assertion below is an equality in both directions.
#:
#: ``state_database_named`` is absent for that same reason and arrived at it the
#: hard way (round two, security H-1): its first cut called ``_contained`` and so
#: belonged here, and root-scoped containment served a decoy *inside* the
#: checkout at exit 0. It now carries ``index_for``'s state-scoped check, so its
#: refusal is its own with ``ACTIVE_POINTER_REMEDY``, and the ``self.state``
#: access it makes first is what this sweep sees.
#:
#: ``review_search_for`` is absent for the same reason as those two, and it is
#: the third member of that shape rather than a new case: ADR-0030 slice 3 gave it
#: ``index_for``'s state-scoped check from the start, so an escaping ``state`` is
#: refused by the ``self.state`` access it makes first -- which is the *directory*
#: case, and therefore ``KNOWLEDGE_DIR_ESCAPE_REMEDY`` -- and an escaping store id
#: is refused by its own check with ``REVIEW_SEARCH_STORE_REMEDY``. Only the first
#: of the two is what this sweep drives.
#:
#: ``index_secret_scan`` joined on #329's merge, and the seam is worth naming: it
#: is a hand-written classification of the helper list *as it stood*, so a helper
#: landing from another branch is classified by whichever assertion runs rather
#: than by a judgement. It is the same shape as ``active_index_pointer`` -- a leaf
#: under ``state``, refused by ``_contained`` after ``self.knowledge_dir`` has
#: been composed -- so it publishes :func:`derived_escape_remedy` and belongs
#: here. Neither side changed production: #483's fix already answered this way for
#: it, and only this set had not been told.
#:
#: The three ``*_temporary`` leaves and ``ingestion_manifest`` joined on #523 and
#: #394, classified the same way and for the same reason: each asks for a leaf
#: inside a derived subdirectory. ``ingestion_manifest`` is the first member under
#: ``cache`` rather than ``state`` or ``runtime``, which is why the sentence above
#: names three subdirectories now and why ``_REBUILD_AFTER_REMOVING`` grew a
#: ``cache`` tail in the same change -- without it the remedy would have named a
#: subdirectory to remove and then said nothing about rebuilding it.
_NAMES_A_DERIVED_ARTIFACT: set[str] = {
    "active_pointer",
    "active_index_pointer",
    "index_secret_scan",
    "active_pointer_temporary",
    "active_index_pointer_temporary",
    "index_secret_scan_temporary",
    "ingestion_manifest",
    "write_lock",
    "database_for",
    "findings_for",
}

#: The helpers whose refused path names the review-evidence directory, and which
#: therefore publish :func:`review_escape_remedy` rather than either of the two
#: texts above (#602).
#:
#: Written out as a judgement for the reason :data:`_NAMES_A_DERIVED_ARTIFACT`
#: records, and a third class rather than a member of that one: ``review`` is not
#: in ``DERIVED_SUBDIRECTORIES`` and never may be -- ADR-0030 decision 3 makes
#: the evidence canonical with no replayable source -- so the rebuild-shaped cure
#: would tell an operator that ``rm -rf`` costs them nothing. It was in neither
#: class until now, which is what made it fall to
#: :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`: that text sends the reader to ``theurian
#: init``, and ``review`` is absent from ``INITIAL_DIRECTORIES``, so the command
#: creates nothing at this path and reports nothing either.
#:
#: One member today. Production keys the carve-out on the first path component at
#: any depth, so a helper resolving something *beneath* the evidence directory
#: would publish this cure too and belongs here -- and until one exists, saying
#: so is the whole of what this set can claim.
_NAMES_THE_REVIEW_EVIDENCE: set[str] = {"review"}


#: Sentinel for "no return annotation": distinct from a member annotated
#: ``-> None``, which is genuinely not a path helper.
_UNANNOTATED = object()


def _underlying_function(member: object) -> Callable[..., Any] | None:
    """The function behind a class member, across every descriptor shape.

    ``property.fget``, ``cached_property.func``, ``classmethod``/``staticmethod``'s
    ``__func__``, or a plain method. The earlier sweep keyed on ``callable(member)``,
    which is ``False`` for a ``functools.cached_property`` (it defines ``__get__``
    but not ``__call__``), so a ``cached_property`` path helper fell through the
    ``else`` and was never enumerated -- a real hole a reviewer added a shadow to
    prove. Returning ``None`` means "not a member that carries a function".
    """
    if isinstance(member, property):
        return member.fget
    if isinstance(member, functools.cached_property):
        return member.func
    if isinstance(member, classmethod | staticmethod):
        return member.__func__
    if inspect.isfunction(member):
        return member
    return None


def _could_return_a_path(function: Callable[..., Any]) -> bool:
    """Whether ``function``'s return annotation does not *rule out* a ``Path``.

    Not ``== Path``: that missed ``Path | None`` (a ``UnionType``, not ``Path``)
    and an unannotated helper (no ``return`` key), both mypy-legal shapes a path
    helper can wear. A member is included unless its annotation is present and
    provably not a path -- so a union *containing* ``Path`` counts, and an
    unannotated member counts (it cannot be proved harmless), while a member
    annotated ``-> str`` or ``-> ProjectPaths`` (``of``) is excluded. Over-
    inclusion is the safe direction: it forces a human to classify, which is the
    completeness guarantee, rather than letting a shape slip past unseen.
    """
    try:
        annotation = get_type_hints(function).get("return", _UNANNOTATED)
    except Exception:
        # An annotation that will not resolve cannot be proved harmless.
        return True
    if annotation is _UNANNOTATED:
        return True
    members = set(get_args(annotation)) or {annotation}
    return Path in members


def _reflected_path_helpers(cls: type = ProjectPaths) -> set[str]:
    """Every public member of ``cls`` that could yield a ``Path``.

    Read off the class by reflection over member *shapes* rather than remembered,
    so the sweep's coverage is a property of the shipped class and not of a
    hand-list the next helper silently outgrows. ``of`` falls out because it
    returns a :class:`ProjectPaths`; ``_contain``/``_contained`` fall out on the
    leading underscore -- they are the chokepoint the sweep proves every *other*
    member routes through.

    ``cls`` is a parameter so a test can point the same reflection at a subclass
    carrying a deliberately-uncontained shadow member and prove the enumeration
    catches it.
    """
    found: set[str] = set()
    for name, member in vars(cls).items():
        if name.startswith("_"):
            continue
        function = _underlying_function(member)
        if function is None:
            continue
        if _could_return_a_path(function):
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
    # The remedy classes partition the swept population the same way, so a
    # helper named in neither -- or named in one that no longer exists -- fails
    # here rather than being silently classified by whichever assertion runs.
    assert set(_HELPER_CALLS) >= _NAMES_A_DERIVED_ARTIFACT
    assert not (_NAMES_A_DERIVED_ARTIFACT & _READER_CONTAINED)
    assert set(_HELPER_CALLS) >= _NAMES_THE_REVIEW_EVIDENCE
    assert not (_NAMES_THE_REVIEW_EVIDENCE & (_NAMES_A_DERIVED_ARTIFACT | _READER_CONTAINED))


@pytest.mark.parametrize(
    "shadow",
    [
        pytest.param(functools.cached_property(lambda self: self.knowledge_dir / "x"), id="cached"),
        pytest.param(property(lambda self: self.knowledge_dir / "x"), id="path-or-none"),
        pytest.param(property(lambda self: self.knowledge_dir / "x"), id="unannotated"),
    ],
)
def test_the_reflection_catches_the_member_shapes_the_return_is_path_test_missed(
    shadow: object,
) -> None:
    """M-1: the earlier ``return is Path`` + ``callable()`` sweep missed shapes.

    A ``cached_property`` (``callable()`` is ``False``), a ``Path | None`` return
    (a ``UnionType``, not ``Path``), and an unannotated helper are all mypy-legal
    and were all invisible to the old reflection -- so adding one to
    ``ProjectPaths`` left the completeness assertion GREEN while the member wrote
    uncontained. The subclass carries the shadow under a name absent from
    ``_HELPER_CALLS``; the reflection must surface it, which is exactly what makes
    the completeness equality above go RED for such an addition.

    The union and unannotated shapes are asserted through the annotation helper
    directly, because a ``lambda`` cannot carry either annotation.
    """
    shadowed = type("_Shadowed", (ProjectPaths,), {"shadow_member": shadow})
    assert "shadow_member" in _reflected_path_helpers(shadowed)

    # The two annotation shapes a lambda cannot express, checked at the predicate.
    def path_or_none(self: ProjectPaths) -> Path | None:  # pragma: no cover - reflected only
        return None

    def unannotated(self):  # type: ignore[no-untyped-def]  # pragma: no cover - reflected only
        return self.knowledge_dir

    assert _could_return_a_path(path_or_none)
    assert _could_return_a_path(unannotated)
    of_function = _underlying_function(inspect.getattr_static(ProjectPaths, "of"))
    assert of_function is not None
    assert not _could_return_a_path(of_function), "`of` returns ProjectPaths, not a Path"


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

    **The remedy is asserted per class, not as one constant** (#483 round one,
    H-1). ``KNOWLEDGE_DIR_ESCAPE_REMEDY`` was published for every helper here,
    and for the ``len(_NAMES_A_DERIVED_ARTIFACT)`` in that set it named the
    operator's authored knowledge directory for a refusal about
    ``.theurian/state/`` or ``.theurian/runtime/`` -- then sent them to
    ``theurian init``, which meets the identical refusal. The count is spelled as
    the expression rather than as a number because the number here read *five*
    while the set held ten.

    **Three classes now, and the third failed the same clause a second way**
    (#602). ``review`` publishes :func:`review_escape_remedy`: it is not a
    derived artefact, so the rebuild-shaped cure is data loss rather than a
    rebuild, and the knowledge-directory text it fell back to sends the reader to
    ``theurian init`` -- which for this path does not refuse, it creates nothing
    and says nothing, because ``review`` is absent from ``INITIAL_DIRECTORIES``.
    Each class asserts ``theurian init`` is *not* named, for the two different
    reasons that command is wrong here.

    The expectation is a set written out in this module, so a helper that changes
    class fails here rather than being re-derived into agreement with whatever
    production now returns.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (root / ".theurian" / _ESCAPING_CHILD[helper]).symlink_to(outside)
    paths = ProjectPaths.of(root)

    with pytest.raises(ProjectError) as excinfo:
        _HELPER_CALLS[helper](paths)

    if helper in _NAMES_THE_REVIEW_EVIDENCE:
        assert excinfo.value.remedy == review_escape_remedy(".theurian")
        assert _ESCAPING_CHILD[helper] in excinfo.value.remedy
        assert "theurian init" not in excinfo.value.remedy, (
            "the remedy sends the reader to a command that creates nothing at this path"
        )
        return

    if helper not in _NAMES_A_DERIVED_ARTIFACT:
        assert excinfo.value.remedy == KNOWLEDGE_DIR_ESCAPE_REMEDY
        return

    # The subdirectory the escape happened under, which is the artefact the
    # remedy must name -- never the leaf, which for `write_lock` sits *inside*
    # the link's target and removing it would cure nothing.
    expected = derived_escape_remedy(".theurian", _ESCAPING_CHILD[helper])
    assert excinfo.value.remedy == expected
    assert _ESCAPING_CHILD[helper] in excinfo.value.remedy
    assert "theurian init" not in excinfo.value.remedy, (
        "the remedy sends the reader to the command that meets this same refusal"
    )


@_NEEDS_SYMLINKS
def test_a_not_yet_created_state_db_under_an_escaping_state_symlink_is_refused(
    tmp_path: Path,
) -> None:
    """Refinement: the creation case.

    The state database does not exist yet -- a first ``migrate apply`` is about to
    create it -- so there is no inode of its own to resolve. The escape is via its
    parent, the ``state`` symlink. ``path.resolve()`` (non-strict) follows that
    *existing* symlink and normalises only the missing leaf, so the target still
    resolves outside and is refused before the write creates it -- closing the gap
    between "the file does not exist" and "its parent is a link that leaves the
    tree".
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
    ``False``, so a check that keyed on existence alone would read it as "no state
    yet" and pass. ``path.resolve()`` (non-strict) still follows the dangling link
    to its normalised target location -- outside the root -- and refuses. This is
    the crash-masked-as-absent shape the first descendant probe hit before the
    target dir was created.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    (root / ".theurian" / "state").symlink_to(Path("..") / ".." / "gone")
    paths = ProjectPaths.of(root)

    with pytest.raises(ProjectError):
        _ = paths.active_pointer


@_NEEDS_SYMLINKS
def test_containment_resolves_an_unresolved_root_before_comparing(tmp_path: Path) -> None:
    """Pin the ``root.resolve()`` an adversarial mutation to ``self.root`` survived.

    ``ProjectPaths.of`` always pre-resolves the root, so within ``.of``-built
    instances the resolve is a no-op and dropping it changes nothing measurable --
    which is why the mutation survived the suite. But a caller holding an
    *unresolved* root (a symlinked directory) would then compare a resolved leaf
    against an unresolved root and refuse every honest access. Built directly, not
    via ``.of``, so the root stays the unresolved symlink path; without the
    resolve, ``paths.state`` raises instead of resolving inside the real tree.
    """
    real = tmp_path / "real"
    (real / ".theurian" / "state").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    paths = ProjectPaths(root=linked, knowledge_dir=linked / ".theurian")

    assert paths.state.resolve() == (real / ".theurian" / "state").resolve()


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


def test_the_findings_index_and_state_filename_prefixes_are_pairwise_disjoint(
    tmp_path: Path,
) -> None:
    """The four artifacts sharing ``.theurian/state/`` are told apart by prefix (#396 T-4).

    Every reader in ``.theurian/state/`` tells its own artifact apart from its
    neighbours by filename prefix alone, never by opening the file first: ``index
    gc`` globs ``theurian-index-*`` and *must* skip the canonical
    ``theurian-state-*`` database beside it (``test_index_gc_cli.py``'s whole
    reason for existing), and ``_applied_migration_ids`` globs
    ``theurian-state-*`` and tries to open whatever matches as a canonical state
    database, silently treating an open failure as "nothing recorded there". A
    store retargeted onto a neighbour's prefix would be picked up by the wrong
    reader -- reclaimed as a stale index build, or opened and silently ignored as
    an unreadable state database -- without either reader raising.

    **Four since ADR-0030 slice 3**, which added the review search store. The
    number moves with the population on purpose: a fifth artifact that reused one
    of these prefixes would be reclaimed or misread exactly as a third would have
    been, so the assertion is over the whole set rather than over the pair somebody
    remembered.

    ``findings_for``'s and ``review_search_for``'s filenames are pinned by exact
    value, driven through the real methods rather than hand-built strings, so a
    retargeted f-string moves this assertion regardless of which neighbour's prefix
    it was pointed at. The other two prefixes are read from their owning modules
    (``INDEX_FILENAME_PREFIX``, ``_STATE_DATABASE_GLOB``) rather than restated as
    literals here, so a prefix change made *only* in its owning module still fails
    this test if it collides.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    paths = ProjectPaths.of(root)

    findings_name = paths.findings_for("local").name
    review_name = paths.review_search_for("local").name

    assert findings_name == "theurian-findings-local.sqlite"
    assert review_name == "theurian-review-local.sqlite"

    findings_prefix = "theurian-findings-"
    review_prefix = "theurian-review-"
    assert findings_name == f"{findings_prefix}local.sqlite"
    assert review_name == f"{review_prefix}local.sqlite"
    index_prefix = INDEX_FILENAME_PREFIX
    state_prefix = _STATE_DATABASE_GLOB.removesuffix("*.sqlite")
    assert state_prefix == "theurian-state-", (
        f"_STATE_DATABASE_GLOB no longer has the shape 'theurian-state-*.sqlite'; "
        f"got {_STATE_DATABASE_GLOB!r}, so the derived prefix is {state_prefix!r}"
    )

    prefixes = (findings_prefix, review_prefix, index_prefix, state_prefix)
    assert len(set(prefixes)) == 4, f"the four artifact prefixes are not distinct: {prefixes}"
    assert not any(
        a != b and (a.startswith(b) or b.startswith(a)) for a in prefixes for b in prefixes
    ), (
        f"one artifact prefix is a prefix of another ({prefixes}), so a glob on "
        f"the shorter one would also match the other artifact's files"
    )


def test_the_state_rebuild_tail_names_a_command_for_every_artifact_family() -> None:
    """RED means removing `.theurian/state` leaves one artifact with no cure named.

    ``derived_escape_remedy`` tells a reader to remove a whole subdirectory, and
    the tail is the only place the response says what to run afterwards. Keying it
    on the subdirectory cannot say *which* artifact the refused leaf was -- four
    families resolve under ``state`` -- so the tail has to name the union, and a
    union is only correct while it grows with the family list. It did not: the
    review search store landed as the fourth family with the tail still naming
    three, so a reader who removed ``state/`` rebuilt everything except their
    review evidence's projection.

    The population is read off :class:`BuildProvenance` -- one ``record_<family>``
    and one ``has_<family>`` per family -- rather than listed here, so a fifth
    family reddens this at the moment it lands. The command per family is derived
    from the same token by the CLI's own convention (``theurian <family> build``);
    ``state`` is the one exception, rebuilt by ``migrate apply`` and named
    unconditionally because every escape under this subdirectory costs it.
    """
    recorders = {
        name.removeprefix("record_") for name in vars(BuildProvenance) if name.startswith("record_")
    }
    checkers = {
        name.removeprefix("has_") for name in vars(BuildProvenance) if name.startswith("has_")
    }
    assert recorders == checkers, (
        f"`BuildProvenance` records {sorted(recorders)} and checks {sorted(checkers)}; "
        f"until they agree there is no single family set for this tail to cover"
    )
    assert "state" in recorders, "the canonical state is not among the recorded families"

    tail = derived_escape_remedy(".theurian", "state")

    assert "`theurian migrate apply`" in tail, (
        "the tail does not name the unconditional rebuild for the canonical state"
    )
    for family in sorted(recorders - {"state"}):
        assert f"`theurian {family} build`" in tail, (
            f"the `state` rebuild tail names no command for the `{family}` family. "
            f"Add it -- `theurian {family} build` if that is the verb, and correct "
            f"this derivation if it is not -- so a reader who removes "
            f"`.theurian/state` is told how to rebuild every artifact it held.\n"
            f"{tail}"
        )
