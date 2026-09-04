"""A doctored path under ``.theurian/`` reaches a ``--json`` caller as one envelope.

Issue #525, closing the CP-2 envelope class that #483/#484/#518 opened one face at
a time. ``ProjectPaths._contained`` refuses a path that resolves outside the
project root -- a symbolic link a clone force-added past ADR-0004's ignore -- by
raising ``ProjectPathEscapeError``. Whether that refusal reached the caller as a
machine document or as a Rich traceback with an empty machine channel was decided
*per call site*, and the call sites disagreed. Measured on ``491bded6`` over the
thirteen plants below, of which eight reach a swept command through
``_contained``:

- ``.theurian/state/active-index.json`` escaping: **five** swept commands
  (``index build``, ``index gc``, ``index status``, ``migrate apply``,
  ``project status``) exited 1 with **zero bytes on stdout and zero on stderr**,
  publishing an uncaught refusal as a boxed traceback carrying absolute
  source paths;
- ``.theurian/state/index-secret-scan.json`` escaping: ``index build``, the same
  way -- a *sixth* face nobody had named, and the reason this file sweeps the
  population rather than the two paths the issue happened to list;
- the same root cause answered **exit 0, exit 1 and exit 4** depending on which
  helper resolved first: ``project status`` degraded to a full payload at exit 0
  over an escaping ``active.json``, ``_read_active`` graded six commands exit 1,
  and ``_require_project``'s ``database_for`` (#518) graded the identical
  condition exit 4.

**The population is derived from the source, never listed here.** Every member of
the class is a call to ``self._contained(...)`` inside ``ProjectPaths``, and
:func:`contained_derived_helpers` reads them out of the module's own AST -- so a
helper added in a later milestone joins the sweep, or fails
:func:`test_every_contained_derived_helper_is_planted_or_excluded_with_a_reason`
until someone classifies it. That is what makes "the population is closed" a
checkable claim rather than an assertion -- and the key is demonstrably able to
hit: adding a fourteenth ``_contained``-derived property to ``ProjectPaths``
fails that test by name. Run 2026-09-04 against the fix, with a
``fourteenth_helper`` property returning
``self._contained(self.knowledge_dir / "state" / "fourteenth.json")``: *"the
planted set and the ``_contained`` call sites in ProjectPaths have moved apart;
unplanted helpers: ['fourteenth_helper']"*. It is a second, independent
derivation beside the reflection over member *shapes* in
``tests/unit/test_project_paths_containment.py``: that one asks the class what it
exposes, this one asks the source what routes through the chokepoint.

**The reach this sweep does not have, stated so nobody reads it as closed.**
Four of the thirteen plants reach no swept command, and their exclusions are
recorded coverage gaps rather than non-membership. Re-run the key rather than
trusting the sentences below::

    git grep -n '\\.specifications\\b' -- packages/theurian-core/src
    git grep -n '_paths\\.proposals\\b\\|_paths\\.proposals_local\\b' -- packages/theurian-core/src
    git grep -n '_paths\\.knowledge\\b' -- packages/theurian-core/src
    git grep -n '\\.findings_for(' -- packages/theurian-core/src

Run 2026-09-04, with ``project_service.py``'s own definitions discounted:

- ``specifications`` returned **nothing**, so it is contained and unread, and a
  future reader joins this sweep by failing the exact-set guard;
- ``proposals``, ``proposals_local`` and ``knowledge`` returned lines in
  ``application/proposal_service.py`` and nowhere else, reached through
  ``propose`` and ``propose accept`` -- which ``CLI_SWEEP`` excludes for the
  reasons ``test_canonical_store_corruption.py``'s ``CLI_NOT_SWEPT`` records:
  each writes a fresh proposal directory or moves a migration file, so the
  corpus stops being the corpus the next plant is measured against;
- ``findings_for`` returned two consumers, ``cli/findings_commands.py``
  (``findings build``, outside ``CLI_SWEEP`` -- it needs a fetched
  ``refs/remotes/origin/main``) and ``mcp/tools.py`` (``review.findings``). The
  second is a *different envelope contract* -- an MCP transport error, not a
  ``--json`` document on stderr -- so it is not merely unswept here, it is
  outside what this file can assert about at all.

**The lock open is the class's other member, and it lives next door.** The write
lock's own ``_open`` is not a ``_contained`` call site, so the AST key above does
not reach it; ``test_migrate_apply_lock_confinement.py`` sweeps it over its own
three artefacts (#520). **The two are not equally strong, and saying so is the
point.** This file's population is derived, so a fourteenth member arrives as a
named failure (run above). That file's is a hand-written parametrisation of three
artefacts, so a fourth artefact at the lock path arrives as nothing at all -- what
it guards is vacuity rather than completeness, through
``_the_open_really_refuses``, which skips a plant the filesystem happens to
accept instead of asserting over a successful apply. The completeness claim
there rests on the ``except`` being unfiltered, which is a property of the one
``try``/``except`` pair in that ``_open`` rather than of a sweep.

The lock is named "the write lock's own ``_open``" throughout, and never by its
bare class name: that name is the key ``test_connection_claims.py`` uses to find
test files that *construct* a lock, and this one does not. Written here because
the mistake was made twice while writing this file, and the key caught it both
times.

**Two artefact shapes, two different classes, kept apart on purpose.** An
escaping symbolic link is refused by containment, and every such refusal owes one
exit code and the remedy keyed on the refused path. A *directory* at a path whose
leaf is a file is not a containment failure at all -- nothing escapes -- but it
reaches the same ``--json`` surface, so it owes the envelope and nothing else.
Folding the two together would demand one exit code from ``index build``'s
"nothing was published" contract, which is not this class's to regrade.

**What is deliberately not swept here.** A FIFO at the write-lock path blocks in
the ``open`` rather than failing (recorded on #526, the lock face); nothing in
this file plants one, and nothing here needs that fix to pass. An escaping
``.theurian`` *itself* is refused a level earlier, by the join check in
``ProjectPaths.of`` rather than by ``_contained``, and keeps each command's own
resolve-time grading -- the bound recorded on ``ProjectPathEscapeError``.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest
import typer.main
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application import project_service
from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    ProjectPaths,
    derived_escape_remedy,
)
from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app
from theurian.domain.state import StateHash
from theurian.domain.values import ContentHash
from theurian.security.project_config import PROJECT_CONFIG_FILE

pytestmark = pytest.mark.integration

runner = CliRunner()

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

#: The command paths swept over every plant, mirroring
#: ``test_canonical_store_corruption.py``'s ``CLI_SWEEP`` -- chosen by what is
#: safe to run against one corpus many times over, and held against the shipped
#: Typer app by :func:`test_every_swept_command_is_one_the_app_still_ships`.
#:
#: The exclusions there apply here for the same reasons and are not restated: a
#: command that rewrites the registry, writes a migration or registers an OS
#: service cannot be run repeatedly against one planted corpus. ``findings
#: build`` is the one whose absence costs coverage -- it is ``findings_for``'s
#: only CLI consumer -- and that cost is recorded on that plant rather than
#: hidden.
CLI_SWEEP: Final = (
    ("index", "build"),
    ("index", "gc"),
    ("index", "status"),
    ("migrate", "status"),
    ("migrate", "validate"),
    ("migrate", "apply"),
    ("project", "list"),
    ("project", "status"),
    ("version",),
)


# -- The population, read out of the source ---------------------------------


def contained_derived_helpers() -> frozenset[str]:
    """Every ``ProjectPaths`` member that routes a path through ``_contained``.

    Parsed from the module's own source rather than remembered, so the class this
    file sweeps is defined by the code and not by whoever last edited the list. A
    helper added in a later milestone appears here the moment it is written, and
    fails the partition below until it is planted or excluded with a reason.

    Keyed on the *call*, not on the member's shape: ``migrations`` returns a path
    and is deliberately contained by its reader instead (the migration loader's
    richer, culprit-naming refusal, issue #233), and ``index_for`` makes its own
    state-scoped check. Neither calls ``_contained``, so neither is a member of
    this class -- which is the distinction a reflection over return annotations
    cannot draw, and why the unit-level sweep in
    ``tests/unit/test_project_paths_containment.py`` and this one are two
    derivations rather than one repeated.
    """
    source_file = inspect.getsourcefile(project_service)
    assert source_file is not None, "project_service must be importable from source"
    tree = ast.parse(Path(source_file).read_text(encoding="utf-8"))
    (class_def,) = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == ProjectPaths.__name__
    ]
    return frozenset(
        member.name
        for member in class_def.body
        if isinstance(member, ast.FunctionDef)
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_contained"
            for node in ast.walk(member)
        )
    )


# -- One plant per helper ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Plant:
    """One doctored artefact, and every swept command it is measured to reach.

    ``relative`` is written by hand rather than taken from the helper, and
    :func:`test_each_plant_sits_at_the_path_its_helper_derives` compares the two.
    Planting at whatever the helper returns would make every assertion below a
    question asked of production and answered by production: a helper that
    started deriving the wrong path would move the plant with it and stay green.

    ``refuses`` and ``directory_refuses`` are measurements, stated exactly so the
    sweep cannot go vacuous. A plant no command reaches asserts nothing, and four
    of the thirteen helpers are in exactly that position -- so "no swept command
    published a traceback" is a property a sweep over unreached plants satisfies
    perfectly.

    ``outside_the_class_because`` is the other way a plant can look like coverage
    and be none: ``knowledge`` makes six commands exit 4 with a clean envelope,
    and every one of those refusals belongs to the migration loader rather than to
    ``_contained``. Counting an exit code as membership is exactly the mistake
    this field exists to refuse.
    """

    helper: str
    relative: str
    #: Whether the honest artefact at this path is a directory. A directory plant
    #: is only distinct where the honest artefact is a file.
    is_directory: bool
    #: The remedy a containment refusal of this path must publish, hand-classified
    #: the way ``test_project_paths_containment.py``'s ``_NAMES_A_DERIVED_ARTIFACT``
    #: is: recomputing it from ``DERIVED_SUBDIRECTORIES`` would be this test asking
    #: production the question production is being tested on.
    remedy: str
    #: Swept commands that refuse over the escaping-symlink plant.
    refuses: frozenset[str] = field(default_factory=frozenset)
    #: Swept commands that refuse over the directory plant, where one is distinct.
    directory_refuses: frozenset[str] = field(default_factory=frozenset)
    #: Empty exactly when this plant's refusals are ``_contained``'s own, which is
    #: what makes it a member of the class #525 closes. Non-empty carries the
    #: measured reason it is not.
    outside_the_class_because: str = ""

    @property
    def has_directory_shape(self) -> bool:
        return not self.is_directory

    @property
    def in_the_containment_class(self) -> bool:
        return not self.outside_the_class_because


_DERIVED_STATE: Final = derived_escape_remedy(".theurian", "state")
_DERIVED_RUNTIME: Final = derived_escape_remedy(".theurian", "runtime")

_EVERY_STATE_READER: Final = frozenset(
    {
        "index build",
        "index gc",
        "index status",
        "migrate status",
        "migrate validate",
        "migrate apply",
        "project status",
    }
)

#: The thirteen ``_contained``-derived helpers, each with the artefact a clone can
#: deliver at its path and the commands that artefact is measured to reach.
#:
#: Every number here was measured on ``491bded6`` (macOS 26.6, CPython 3.13.3) by
#: running the whole matrix; none was inferred from reading a call graph, which is
#: how ``index_secret_scan``'s traceback face came to be missing from the issue.
PLANTS: Final = (
    Plant(
        helper="knowledge",
        relative="knowledge",
        is_directory=True,
        remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
        refuses=_EVERY_STATE_READER - {"project status"},
        outside_the_class_because=(
            "the migration loader refuses first. Every swept command that resolves a "
            "project exits 4 over this plant, but the refusal is `PathEscapeError` "
            "over the migration's `contentFile`, naming `.theurian/migrations` -- "
            "`ProjectPaths.knowledge` is never asked. Its only consumer is "
            "`proposal_service`, reached through `propose` and `propose accept`, "
            "both outside this sweep. Attributed rather than asserted by "
            "`test_the_knowledge_plant_is_refused_by_the_migration_loader_not_by_containment`."
        ),
    ),
    Plant(
        helper="specifications",
        relative="specifications",
        is_directory=True,
        remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
        outside_the_class_because=(
            "no swept command reaches it, and no consumer exists at all. "
            "`git grep 'paths.specifications'` over `packages/theurian-core/src` "
            "returns nothing outside `project_service` "
            "itself; `initialize_project` creates the directory from the literal "
            "name. The helper is contained and unread, and a future reader joins "
            "this sweep by failing the exact-set guard."
        ),
    ),
    Plant(
        helper="proposals",
        relative="proposals",
        is_directory=True,
        remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
        outside_the_class_because=(
            "no swept command reaches it: read only by `proposal_service`, reached "
            "through `propose` and `propose accept` -- both excluded from this "
            "sweep because each writes a "
            "fresh proposal directory or moves a migration file, which changes the "
            "corpus the next plant would be measured against."
        ),
    ),
    Plant(
        helper="proposals_local",
        relative="proposals-local",
        is_directory=True,
        remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
        outside_the_class_because=(
            "no swept command reaches it: the `--local` half of `proposals`, with "
            "the same two consumers."
        ),
    ),
    Plant(
        helper="config",
        relative=PROJECT_CONFIG_FILE,
        is_directory=False,
        remedy=KNOWLEDGE_DIR_ESCAPE_REMEDY,
        refuses=frozenset({"index build"}),
        directory_refuses=frozenset({"index build"}),
    ),
    Plant(
        helper="state",
        relative="state",
        is_directory=True,
        remedy=_DERIVED_STATE,
        refuses=_EVERY_STATE_READER,
    ),
    Plant(
        helper="runtime",
        relative="runtime",
        is_directory=True,
        remedy=_DERIVED_RUNTIME,
        refuses=frozenset({"migrate status", "migrate apply"}),
    ),
    Plant(
        helper="active_pointer",
        relative="state/active.json",
        is_directory=False,
        remedy=_DERIVED_STATE,
        refuses=_EVERY_STATE_READER,
        directory_refuses=_EVERY_STATE_READER - {"project status"},
    ),
    Plant(
        helper="active_index_pointer",
        relative="state/active-index.json",
        is_directory=False,
        remedy=_DERIVED_STATE,
        refuses=frozenset(
            {"index build", "index gc", "index status", "migrate apply", "project status"}
        ),
        directory_refuses=frozenset({"index build"}),
    ),
    Plant(
        helper="index_secret_scan",
        relative="state/index-secret-scan.json",
        is_directory=False,
        remedy=_DERIVED_STATE,
        refuses=frozenset({"index build"}),
    ),
    Plant(
        helper="findings_for",
        relative=f"state/theurian-findings-{FINDINGS_STORE_ID}.sqlite",
        is_directory=False,
        remedy=_DERIVED_STATE,
        outside_the_class_because=(
            "no swept command reaches it. `findings build` is its only CLI consumer "
            "and is outside this sweep: it "
            "reads `refs/remotes/origin/main`, which this corpus has no reason to "
            "carry, and writes a separate derived store. The MCP `review.findings` "
            "tool reads it too, on a surface this file does not drive. A real "
            "coverage gap, recorded rather than papered over."
        ),
    ),
    Plant(
        helper="write_lock",
        relative="runtime/write.lock",
        is_directory=False,
        remedy=_DERIVED_RUNTIME,
        refuses=frozenset({"migrate status", "migrate apply"}),
        directory_refuses=frozenset({"migrate status", "migrate apply"}),
    ),
    Plant(
        helper="database_for",
        relative="state/theurian-state-*.sqlite",
        is_directory=False,
        remedy=_DERIVED_STATE,
        refuses=_EVERY_STATE_READER,
        directory_refuses=frozenset({"index build", "migrate status", "migrate apply"}),
    ),
)

PLANT_BY_HELPER: Final = {plant.helper: plant for plant in PLANTS}

#: The plants no swept command reaches at all, so the sweep asserts nothing about
#: them.
#:
#: Held as an exact set by :func:`test_exactly_these_plants_reach_a_swept_command`
#: in both directions: a plant that starts being reached is a new surface for this
#: class and must arrive as a failure, and a plant that stops being reached has
#: quietly hollowed out every property below.
REACHES_NO_SWEPT_COMMAND: Final = frozenset(
    {"specifications", "proposals", "proposals_local", "findings_for"}
)

#: The plants whose refusals are ``_contained``'s own -- the class #525 closes,
#: and the population its closure argument has to range over.
#:
#: ``knowledge`` is measured out of it rather than reasoned out: it refuses six
#: commands, and every refusal is the migration loader's.
CONTAINMENT_PLANTS: Final = tuple(plant for plant in PLANTS if plant.in_the_containment_class)


# -- The corpus and the sweep -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """What one command printed over one plant, as an operator would receive it."""

    exit_code: int
    stdout: str
    #: The parsed ``{error, remedy}`` document, or ``None`` when stderr held no
    #: JSON at all -- which is what an escaped exception leaves behind.
    envelope: dict[str, Any] | None
    #: The type name of an exception that escaped to the terminal, or ``None``.
    escaped: str | None

    @property
    def refused(self) -> bool:
        return self.exit_code != 0


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _observe(*args: str) -> Observation:
    """Run one command and record what reached the caller.

    ``CliRunner`` keeps an uncaught exception on ``result.exception`` rather than
    letting Typer's Rich handler render it, so the escape is invisible in the
    captured streams. Reading it off the result is what keeps this sweep looking
    at what an operator sees -- a boxed traceback with absolute source paths and
    an empty machine channel -- rather than at what the runner happened to keep.
    """
    result = runner.invoke(app, [*args, "--json"])
    escaped = result.exception
    if isinstance(escaped, SystemExit):
        escaped = None
    stderr = (result.stderr or "").strip()
    envelope: dict[str, Any] | None = None
    if stderr:
        try:
            parsed = json.loads(stderr)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            envelope = parsed
    return Observation(
        exit_code=result.exit_code,
        stdout=result.stdout,
        envelope=envelope,
        escaped=None if escaped is None else type(escaped).__name__,
    )


def _build_corpus(tmp_path: Path, patch: pytest.MonkeyPatch) -> Path:
    """A registered, migrated, indexed project with `HOME` and the data dir moved.

    Both redirections go through ``monkeypatch`` and never through ``os.environ``,
    so a corpus built inside a module-scoped fixture leaves nothing behind. The
    ``chdir`` is here too: the CLI resolves a project from the working directory,
    and a sweep that forgot it would resolve the developer's own checkout.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    patch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    patch.setenv("HOME", str(tmp_path / "home"))
    patch.chdir(root)

    _run("init")
    _run("project", "register")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("migrate", "apply")
    _run("index", "build")
    return root


def _planted_path(root: Path, plant: Plant) -> Path:
    """Where ``plant``'s artefact goes, resolving the one globbed name.

    ``database_for``'s leaf carries the state hash, so its relative spelling is a
    pattern; every other plant names its file outright. The glob is required to
    match exactly one file, so a corpus that stopped building the database -- or
    started building two -- fails here rather than planting nothing and reporting
    the resulting silence as a clean sweep.
    """
    if "*" not in plant.relative:
        return root / ".theurian" / plant.relative
    parent, _, pattern = plant.relative.rpartition("/")
    (found,) = sorted((root / ".theurian" / parent).glob(pattern))
    return found


def _clear(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path)


def _plant_escaping_symlink(root: Path, plant: Plant) -> None:
    """Replace the artefact with a symbolic link that leaves the working tree.

    Models what a clone hands the victim: the real content is moved beside the
    tree and a *relative* link is committed in its place, force-added past
    ADR-0004's ignore. Relative because a clone carries a relative one, and the
    target is genuinely outside the clone's real tree -- the property every
    assertion below turns on, so it is checked rather than assumed.
    """
    path = _planted_path(root, plant)
    outside = root.parent / f"outside-{plant.helper}"
    if path.exists() and not path.is_symlink():
        shutil.move(str(path), str(outside))
    elif plant.is_directory:
        outside.mkdir(exist_ok=True)
    else:
        outside.write_text("{}")
    _clear(path)
    depth = len(path.relative_to(root).parts)
    path.symlink_to(Path(*[".."] * depth) / outside.name)
    assert not outside.resolve().is_relative_to(root.resolve()), (
        "the plant must sit genuinely outside the clone's real tree"
    )


def _plant_directory(root: Path, plant: Plant) -> None:
    """Put a directory where a file belongs -- an artefact containment allows."""
    path = _planted_path(root, plant)
    _clear(path)
    path.mkdir(parents=True)


Matrix = dict[tuple[str, str], Observation]


def _sweep(
    tmp_path_factory: pytest.TempPathFactory,
    plant_it: Callable[[Path, Plant], None],
    plants: Iterator[Plant],
) -> Matrix:
    """One fresh corpus per plant, every swept command run against it.

    A corpus per plant rather than one shared corpus: several plants move a whole
    directory out of the tree, and a command run after that cannot be restored to
    the state the next plant needs. Sharing the *result* is what makes this cheap
    enough to read from several properties.
    """
    observed: Matrix = {}
    for plant in plants:
        with pytest.MonkeyPatch.context() as patch:
            root = _build_corpus(tmp_path_factory.mktemp(plant.helper.replace("_", "-")), patch)
            plant_it(root, plant)
            for command in CLI_SWEEP:
                observed[plant.helper, " ".join(command)] = _observe(*command)
    return observed


@pytest.fixture(scope="module")
def escaping_symlinks(tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    """Thirteen plants by nine commands, the containment class's whole surface."""
    return _sweep(tmp_path_factory, _plant_escaping_symlink, iter(PLANTS))


@pytest.fixture(scope="module")
def planted_directories(tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    """The same sweep for the artefact containment cannot refuse: a directory."""
    return _sweep(
        tmp_path_factory,
        _plant_directory,
        (plant for plant in PLANTS if plant.has_directory_shape),
    )


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An undoctored corpus, for the controls and the path-identity check."""
    return _build_corpus(tmp_path, monkeypatch)


# -- The population is the class, not a list --------------------------------


def test_every_contained_derived_helper_is_planted_or_excluded_with_a_reason() -> None:
    """A helper added later joins this sweep or fails here. The closure argument.

    The claim #525 has to make is not "these two faces are fixed" but "no further
    member of this class is findable by the key that found them", and that is only
    checkable if the population is the code's. Set equality both ways: a helper
    added to ``ProjectPaths`` that routes through ``_contained`` has no plant and
    fails here; a plant naming a helper that no longer exists fails here too.

    Every plant kept out of the class carries the measured reason it is out, so an
    exclusion is a recorded coverage gap rather than a silent one -- and the two
    kinds of exclusion are told apart, because they fail differently. Four helpers
    no swept command reaches (a gap in this sweep's reach); ``knowledge`` is
    reached and refused by a different guard (a gap in attribution, which an exit
    code alone reads as coverage).
    """
    planted = frozenset(PLANT_BY_HELPER)

    assert planted == contained_derived_helpers(), (
        "the planted set and the `_contained` call sites in ProjectPaths have "
        f"moved apart; unplanted helpers: {sorted(contained_derived_helpers() - planted)}, "
        f"planted but no longer derived: {sorted(planted - contained_derived_helpers())}"
    )
    assert planted >= REACHES_NO_SWEPT_COMMAND
    outside = {plant.helper for plant in PLANTS if not plant.in_the_containment_class}
    assert outside == REACHES_NO_SWEPT_COMMAND | {"knowledge"}, (
        f"the plants held outside the containment class have moved: {sorted(outside)}"
    )
    assert all(PLANT_BY_HELPER[helper].outside_the_class_because for helper in outside), (
        "a plant held outside the class without a reason is one someone forgot"
    )
    assert not any(PLANT_BY_HELPER[helper].refuses for helper in REACHES_NO_SWEPT_COMMAND), (
        "a plant classified as reaching nothing is measured to refuse something"
    )
    assert CONTAINMENT_PLANTS, "the class is empty, so every property over it is vacuous"


def test_every_swept_command_is_one_the_app_still_ships() -> None:
    """A renamed command in the sweep would assert nothing and report green.

    ``CLI_SWEEP`` is a list, and a list cannot fail. Held against the real Typer
    app so a command path that was renamed or removed stops counting as swept
    coverage. The complementary guard -- that the *unswept* half is classified
    rather than forgotten -- is
    ``test_canonical_store_corruption.py::test_every_shipped_command_is_swept_or_excluded_with_a_reason``,
    over the same population.
    """

    def walk(command: Any, prefix: tuple[str, ...] = ()) -> Iterator[str]:
        children = getattr(command, "commands", None)
        if prefix and (not children or getattr(command, "invoke_without_command", False)):
            yield " ".join(prefix)
        if children:
            for name, child in children.items():
                yield from walk(child, (*prefix, name))

    shipped = frozenset(walk(typer.main.get_command(app)))
    swept = frozenset(" ".join(command) for command in CLI_SWEEP)

    assert swept <= shipped, f"swept commands the app no longer ships: {sorted(swept - shipped)}"


@_NEEDS_SYMLINKS
def test_each_plant_sits_at_the_path_its_helper_derives(corpus: Path) -> None:
    """The plants are aimed by hand and checked against production, not taken from it.

    A plant at the wrong path is the quietest way for a sweep to assert nothing:
    every command answers normally, every property passes, and the helper it was
    supposed to cover was never touched. So the relative spellings above are
    written out, and this drives each helper on a real ``ProjectPaths`` and
    compares. A helper that starts deriving a different path fails here rather
    than dragging its plant along and staying green.
    """
    paths = ProjectPaths.of(corpus)
    validated = runner.invoke(app, ["migrate", "validate", "--json"], catch_exceptions=False)
    assert validated.exit_code == 0, validated.stderr
    state_hash = StateHash(ContentHash(json.loads(validated.stdout)["stateHash"]))

    def derived(plant: Plant) -> Path:
        if plant.helper == "database_for":
            return paths.database_for(state_hash)
        if plant.helper == "findings_for":
            return paths.findings_for(FINDINGS_STORE_ID)
        found = getattr(paths, plant.helper)
        assert isinstance(found, Path)
        return found

    mismatched = {
        plant.helper: (_planted_path(corpus, plant), derived(plant))
        for plant in PLANTS
        if _planted_path(corpus, plant) != derived(plant)
    }

    assert not mismatched, f"plants aimed somewhere the helper does not derive: {mismatched}"


# -- The controls ------------------------------------------------------------


def test_an_undoctored_corpus_answers_every_swept_command(corpus: Path) -> None:
    """Without this, no refusal below is attributable to the plant.

    Asserted over the whole swept population rather than over the commands that
    happen to refuse: a command that already fails on a healthy corpus would
    contribute a refusal to every plant's measured set and make the exact-set
    guard describe the fixture instead of the defect.
    """
    codes = {" ".join(command): _observe(*command) for command in CLI_SWEEP}

    assert [name for name, seen in codes.items() if seen.refused] == [], (
        f"a command failed against an undoctored corpus: "
        f"{ {name: seen for name, seen in codes.items() if seen.refused} }"
    )


@_NEEDS_SYMLINKS
def test_exactly_these_plants_reach_a_swept_command(escaping_symlinks: Matrix) -> None:
    """The vacuity guard. Four of the thirteen plants reach nothing at all.

    Every property below is quantified over the matrix, and every one of them is
    satisfied perfectly by a sweep whose plants no command ever touches. Stating
    which (plant, command) pairs actually refuse is what stops that: a fix that
    made a command stop noticing its doctored path would pass "no traceback" and
    "one exit code" while removing the refusal entirely, and fails here instead.

    **One pair in this set was RED at ``491bded6``, and it is the exit-0 face
    #525 names.** ``project status`` over an escaping
    ``.theurian/state/active.json`` answered a full payload at exit 0 with
    ``statePointerCorrupt: true`` -- the degradation it correctly performs for a
    pointer it cannot *parse*, applied to a pointer that resolves outside the
    working tree. Those are different conditions with different cures, and the
    second is the one the command's own ``database_for`` comment already refused
    to answer partially about: "no partial answer about a project in that
    condition is worth publishing". It now refuses at the pointer read too,
    keyed on the exception's type rather than on which helper reached it. The
    bound is held from the other side by
    :func:`test_a_pointer_that_will_not_parse_still_degrades_rather_than_refusing`,
    so closing this did not turn an unreadable pointer into a refusal.
    """
    measured = {
        plant.helper: frozenset(
            command
            for command in (" ".join(path) for path in CLI_SWEEP)
            if escaping_symlinks[plant.helper, command].refused
        )
        for plant in PLANTS
    }
    expected = {plant.helper: plant.refuses for plant in PLANTS}

    reaches_nothing = frozenset(helper for helper, seen in measured.items() if not seen)

    assert reaches_nothing == REACHES_NO_SWEPT_COMMAND, (
        "which plants reach a swept command has moved; newly reaching one: "
        f"{sorted(REACHES_NO_SWEPT_COMMAND - reaches_nothing)}, "
        f"newly reaching none: {sorted(reaches_nothing - REACHES_NO_SWEPT_COMMAND)}"
    )
    assert measured == expected, {
        helper: {
            "newly refusing": sorted(measured[helper] - expected[helper]),
            "no longer refusing": sorted(expected[helper] - measured[helper]),
        }
        for helper in measured
        if measured[helper] != expected[helper]
    }


# -- CP-2: a refusal is a document, never a traceback -----------------------


@_NEEDS_SYMLINKS
def test_a_containment_refusal_never_reaches_a_json_caller_as_a_traceback(
    escaping_symlinks: Matrix,
) -> None:
    """CP-2, issue #525. RED at ``491bded6``, GREEN since the refusal is handled.

    Six positions escaped, all of them raised inside ``ProjectPaths._contained``
    and caught by nobody:
    ``active-index.json`` through ``index build``, ``index gc``, ``index status``,
    ``migrate apply`` and ``project status``, and ``index-secret-scan.json``
    through ``index build``. Each exited 1 with **zero bytes on stdout and zero on
    stderr**, and published a boxed Rich traceback carrying absolute source paths
    to whoever was reading the terminal -- a caller parsing ``--json`` received
    nothing at all and could not tell a refusal from a crash.

    Reported as the whole set rather than at the first failure, because this is a
    class: #483 closed ``database_for``, #518 closed it at ``project status``, and
    both left the two pointer leaves standing behind them. A test that stopped at
    the first escape would send someone to fix a third face one at a time.
    """
    escaped = {
        position: observation.escaped
        for position, observation in escaping_symlinks.items()
        if observation.escaped is not None
    }

    assert not escaped, (
        f"{len(escaped)} commands published an exception instead of an envelope: "
        f"{dict(sorted(escaped.items()))}"
    )


@_NEEDS_SYMLINKS
def test_a_containment_refusal_publishes_one_document_and_an_empty_stdout(
    escaping_symlinks: Matrix,
) -> None:
    """The other half of CP-2: the machine channel stays clean and the error parses.

    Separate from the traceback property because the two fail separately. A
    refusal that printed its message to stdout, or one that wrote a JSON document
    *and* a human line onto stderr, escapes neither ``result.exception`` nor an
    exit-code check -- and either one breaks a caller that parses stderr as one
    document.
    """
    wrong = {
        position: {
            "stdout": observation.stdout,
            "envelope": observation.envelope,
            "exit": observation.exit_code,
        }
        for position, observation in escaping_symlinks.items()
        if observation.refused
        and (
            observation.stdout != ""
            or observation.envelope is None
            or not observation.envelope.get("error")
            or not observation.envelope.get("remedy")
        )
    }

    assert not wrong, f"refusals that are not one clean envelope: {wrong}"


@_NEEDS_SYMLINKS
def test_a_directory_where_a_file_belongs_is_also_answered_as_a_document(
    planted_directories: Matrix,
) -> None:
    """The second artefact shape, swept for the envelope and nothing else.

    A directory at a path whose leaf is a file is not a containment failure --
    nothing escapes the tree, and ``_contained`` correctly waves it through -- but
    it reaches the same ``--json`` surface, and the surface owes the same
    contract. Two positions escaped at ``491bded6``:

    - ``.theurian/state/active-index.json`` as a directory, through ``index
      build``, raising ``IsADirectoryError`` out of ``write_active_index_pointer``
      -- now graded by ``index build``'s own ``except OSError`` around the
      publish, with a remedy naming the pointer rather than the corpus;
    - ``.theurian/runtime/write.lock`` as a directory, through ``migrate apply``,
      raising ``IsADirectoryError`` out of the write lock's own ``_open`` --
      issue #520, whose arms live in ``test_migrate_apply_lock_confinement.py``.

    Only the envelope is asserted here, never the exit code: these are not one
    class with one cure. ``index build`` answering "nothing was published" over a
    directory at the database path is its own contract, and regrading it would be
    this file reaching outside the class it exists to close.
    """
    escaped = {
        position: observation.escaped
        for position, observation in planted_directories.items()
        if observation.escaped is not None
    }
    silent = {
        position: observation.exit_code
        for position, observation in planted_directories.items()
        if observation.refused and observation.escaped is None and observation.envelope is None
    }

    assert not escaped, f"a planted directory published an exception: {escaped}"
    assert not silent, f"a planted directory failed with no document at all: {silent}"


@_NEEDS_SYMLINKS
def test_exactly_these_planted_directories_reach_a_swept_command(
    planted_directories: Matrix,
) -> None:
    """The vacuity guard for the directory sweep, measured and stated exactly."""
    measured = {
        plant.helper: frozenset(
            command
            for command in (" ".join(path) for path in CLI_SWEEP)
            if planted_directories[plant.helper, command].refused
        )
        for plant in PLANTS
        if plant.has_directory_shape
    }
    expected = {
        plant.helper: plant.directory_refuses for plant in PLANTS if plant.has_directory_shape
    }

    assert measured == expected, {
        helper: {
            "newly refusing": sorted(measured[helper] - expected[helper]),
            "no longer refusing": sorted(expected[helper] - measured[helper]),
        }
        for helper in measured
        if measured[helper] != expected[helper]
    }


# -- One grading for one root cause -----------------------------------------


@_NEEDS_SYMLINKS
def test_every_containment_refusal_carries_the_state_error_exit_code(
    escaping_symlinks: Matrix,
) -> None:
    """The grading unification. RED at ``491bded6``, GREEN since #525 landed.

    One root cause -- a path under ``.theurian/`` that resolves outside the
    working tree -- answered three different exit codes, decided by which helper
    happened to resolve first rather than by what was wrong:

    - **0** from ``project status`` over an escaping ``active.json``, which
      degraded to a full payload;
    - **1** from ``_read_active``'s handler, over the ``state``, ``active.json``
      and ``config.yaml`` plants, on six commands each;
    - **4** from ``_require_project`` and ``project status``'s own
      ``database_for`` handlers (#518), and from ``migrate apply``'s lock section.

    ``EXIT_STATE_ERROR`` is the one to keep, and not by majority: it is the code
    ``_require_project`` already assigns to ``PathEscapeError`` -- a migrations
    directory symlinked out of the tree, the same doctored-clone condition
    reached through a different guard (#233) -- and the code #518 chose for this
    exact exception. Exit 1 is this CLI's "the command could not run here"; a
    working tree carrying a force-added symlink past ADR-0004's ignore is a
    knowledge-state problem the user must repair, which is what 4 means.

    A caller scripting against these commands is the reason it matters: it used
    to have to special-case which file was doctored to learn that anything was.
    The move is a **breaking change** to a published exit code, so it is named as
    one in the changelog rather than carried as a detail of adding a refusal --
    the discipline ``EXIT_STATE_ERROR``'s own note in ``cli/commands.py``
    records, and the reason the SEC-8 input caps beside it were left alone.

    Imported rather than written as ``4`` so a change to the constant moves the
    product and this assertion together; the value itself is pinned as a
    published contract by the plugin compatibility tests.
    """
    graded = {
        (plant.helper, command): escaping_symlinks[plant.helper, command].exit_code
        for plant in CONTAINMENT_PLANTS
        for command in (" ".join(path) for path in CLI_SWEEP)
        if escaping_symlinks[plant.helper, command].refused
        and escaping_symlinks[plant.helper, command].exit_code != EXIT_STATE_ERROR
    }
    degraded = sorted(
        (plant.helper, command)
        for plant in CONTAINMENT_PLANTS
        for command in plant.refuses
        if not escaping_symlinks[plant.helper, command].refused
    )

    assert not graded, (
        f"containment refusals graded something other than {EXIT_STATE_ERROR}: "
        f"{dict(sorted(graded.items()))}"
    )
    assert not degraded, (
        f"a containment refusal answered successfully instead of refusing: {degraded}"
    )


@_NEEDS_SYMLINKS
def test_every_containment_refusal_publishes_the_remedy_for_the_path_it_refused(
    escaping_symlinks: Matrix,
) -> None:
    """One cure per doctored artefact, whichever helper noticed it.

    ``_escape_remedy`` keys the cure on the refused path: a leaf under a derived
    subdirectory gets ``derived_escape_remedy``, which names ``.theurian/state``
    or ``.theurian/runtime`` and the commands that rebuild them, and everything
    else gets ``KNOWLEDGE_DIR_ESCAPE_REMEDY``, which is about the operator's
    authored knowledge directory. Publishing the second for a doctored
    ``.theurian/state/`` was #483's H-1: it named the wrong artefact and sent the
    reader to ``theurian init``, which meets the identical refusal.

    **The expectation is written per plant, not recomputed.** Deriving it from
    ``DERIVED_SUBDIRECTORIES`` would be this test asking production the question
    production is being tested on.

    RED at ``491bded6`` for the six escaping positions, which published no remedy
    at all because they published no document at all.

    **The face this pins is the CLI's, and that is deliberate.** Asking
    ``ProjectPaths`` for ``state`` or ``runtime`` *itself* still returns the older
    text, and no command reaches it: a helper resolving something *under* the
    directory raises first, and its refusal is what gets published. Measured
    again here -- every refusal over the ``state`` plant carries
    ``derived_escape_remedy(".theurian", "state")`` -- so the helper-level text is
    unreachable rather than absent, and pinning it at the helper would pin a
    string no user can meet.
    """
    assert KNOWLEDGE_DIR_ESCAPE_REMEDY not in {_DERIVED_STATE, _DERIVED_RUNTIME}, (
        "the two remedy texts are equal, so this test cannot tell them apart"
    )

    wrong = {
        (plant.helper, command): (escaping_symlinks[plant.helper, command].envelope or {}).get(
            "remedy"
        )
        for plant in CONTAINMENT_PLANTS
        for command in (" ".join(path) for path in CLI_SWEEP)
        if escaping_symlinks[plant.helper, command].refused
        and (escaping_symlinks[plant.helper, command].envelope or {}).get("remedy") != plant.remedy
    }

    assert not wrong, (
        "a containment refusal published a cure for a different artefact "
        f"(or none at all): {sorted(wrong)}"
    )


@_NEEDS_SYMLINKS
def test_the_knowledge_plant_is_refused_by_the_migration_loader_not_by_containment(
    escaping_symlinks: Matrix,
) -> None:
    """The one exclusion an exit code alone would misread as coverage.

    An escaping ``.theurian/knowledge`` makes six swept commands exit 4 with a
    clean envelope, which looks exactly like the containment class doing its job.
    It is not: the refusal is the migration loader's ``PathEscapeError`` over the
    migration's ``contentFile``, and ``ProjectPaths.knowledge`` -- whose only
    consumer is ``proposal_service``, reached through ``propose`` -- is never
    asked. Counting it as a covered member would put a helper no swept command
    touches inside the closure argument.

    Attributed by the published cure rather than by reading the call graph: the
    remedy names ``.theurian/migrations`` and is neither of the two texts
    ``_escape_remedy`` can return.
    """
    refusals = [
        observation
        for (helper, _command), observation in escaping_symlinks.items()
        if helper == "knowledge" and observation.refused
    ]

    assert refusals, "the knowledge plant reached nothing at all, so this proves nothing"
    for observation in refusals:
        remedy = str((observation.envelope or {}).get("remedy", ""))
        assert remedy not in {KNOWLEDGE_DIR_ESCAPE_REMEDY, _DERIVED_STATE, _DERIVED_RUNTIME}, (
            "the knowledge plant is now refused by containment; it belongs in the "
            "swept population rather than in the exclusions"
        )
        assert ".theurian/migrations" in remedy, (
            f"the knowledge plant's refusal no longer names the migration set: {remedy!r}"
        )


# -- The bound: what must NOT become a refusal ------------------------------


def test_a_pointer_that_will_not_parse_still_degrades_rather_than_refusing(
    corpus: Path,
) -> None:
    """The other side of the unification, so closing #525 cannot overshoot.

    An ``active.json`` holding four bytes of text is a *different root cause* from
    one that resolves outside the tree: derived state to delete, not a working
    tree someone doctored. Its grading is not this issue's to change, and the two
    conditions arrive at the same ``read_active_state`` call -- so a fix that
    regraded every failure there would take this one with it.

    Measured at ``491bded6`` and required to stay: the six state readers exit 1
    naming the pointer to delete, and ``project status`` answers its full payload
    at exit 0 with ``statePointerCorrupt: true``, which is the field that exists
    to say so. GREEN before the fix and after.
    """
    (corpus / ".theurian/state/active.json").write_text("nope")

    status = _observe("project", "status")
    apply_result = _observe("migrate", "apply")

    assert status.exit_code == 0, (
        "an unreadable pointer is a degradation this command reports in a field, "
        "not a refusal: `statePointerCorrupt` exists precisely to answer it"
    )
    assert json.loads(status.stdout)["statePointerCorrupt"] is True
    assert apply_result.exit_code == 1, (
        "an unreadable pointer keeps its own grading; only a path that resolves "
        "outside the working tree is the state-integrity class #525 unifies"
    )
    assert (apply_result.envelope or {}).get("remedy", "").startswith("Delete ")
