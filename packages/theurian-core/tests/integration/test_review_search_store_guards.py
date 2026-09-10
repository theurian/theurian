"""Every guard the review search store inherited, with the test that drives it.

The fourth artifact family under ``.theurian/state/`` was built on the design the
first three already carried -- publish by rename under a ``.building`` sibling,
unlink that name before opening it, reap the publish name's write-ahead log
before the rename, refuse the shape at the path before the open, open ``mode=ro``
so a read cannot conjure a database, recompute containment for the leaf, refuse
two records under one key -- and it inherited the *design* without inheriting the
*pins*. That is the class this module closes, and it is a class rather than a
list: a guard nothing drives survives its own deletion, and the deletion is
invisible because the surrounding tests only ever ask whether the store answered.

The closure argument
--------------------
**guards inherited = the derived census**, computed here from the shipped source
in four families rather than remembered:

* :data:`WRITE_PATH_HYGIENE` -- the module's own private helpers whose whole
  purpose is their effect on a *name*, called as bare statements inside
  ``replace_all``. Derived from ``findings_store`` and intersected with this
  store, so a helper the parent carries and this one does not is not claimed.
* :data:`OPENER_SAFETY` -- everything the opener calls that comes from the shared
  ``infrastructure.sqlite.schema`` safety module, derived from
  ``findings_store._read`` and ``index_store._connect_to`` and intersected the
  same way.
* :data:`CONTAINMENT_RECOMPUTE` -- every public :class:`ProjectPaths` helper that
  returns a ``Path`` and recomputes containment for its own leaf rather than
  routing through the ``_contained`` chokepoint. ``review_search_for`` is the
  third member; the other two are its precedent.
* :data:`LOAD_REFUSALS` -- every dataclass in ``domain/review_search.py`` whose
  ``__post_init__`` raises, so an impossible load or query cannot be constructed
  at all.

**pin per guard = :data:`PINS`**, which maps every derived member to the test
that drives it -- six in this module, four already driven elsewhere and named by
file so the claim is checkable. :func:`test_every_inherited_guard_has_a_driving_
test` asserts the map and the census agree **in both directions**, so a seventh
inherited guard joins the census by the reflection and fails here until somebody
writes its pin. That is what makes this a census rather than the five guards a
mutation run happened to hit.

Each pin below was verified RED under the removal of the guard it drives; the
docstrings say what the neutered store does, because "this test would fail"
is a claim and the measured consequence is a fact.

Marked ``integration``: real SQLite files, real named pipes and sockets, and one
child process. Writes only under ``tmp_path``.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Final, get_type_hints

import pytest

from theurian.application import project_service
from theurian.application.project_service import (
    REVIEW_SEARCH_STORE_ID,
    REVIEW_SEARCH_STORE_REMEDY,
    ProjectError,
    ProjectPaths,
)
from theurian.domain import review_search as review_search_domain
from theurian.domain.errors import InvariantViolationError
from theurian.domain.review_search import (
    ReviewSearchLoad,
    ReviewSearchQuery,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
)
from theurian.infrastructure.sqlite import findings_store as findings_store_module
from theurian.infrastructure.sqlite import index_store as index_store_module
from theurian.infrastructure.sqlite import review_search_store as review_search_store_module
from theurian.infrastructure.sqlite.review_search_store import (
    ReviewSearchStoreError,
    SqliteReviewSearchStore,
)

pytestmark = pytest.mark.integration

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

#: ``os.mkfifo`` is POSIX-only, and a named pipe is the shape whose open is
#: *unbounded* rather than merely wrong -- the one the child harness exists for.
_CAN_PLANT_A_PIPE_AND_A_SOCKET: Final = hasattr(os, "mkfifo") and hasattr(socket, "AF_UNIX")

ONE_INSTANT: Final = "2026-09-07T09:00:00.000000+00:00"

#: Crockford base32 -- no ``I``, ``L``, ``O`` or ``U``.
RUN_ID: Final = "01K1GRD00000000000000ABCDE"

#: The module this store's own guards live in, named once so every derivation
#: below reads the same source.
CHILD_MODULE: Final = review_search_store_module

#: Each parent opener, with the function that performs the open. Two of them
#: because the guard arrived from both -- ``index_store`` is where the shape
#: refusal was written (#586) and ``findings_store`` is where this store's
#: connection handling was copied from -- and they spell the opener differently,
#: which is exactly what a hand-list of one would have missed.
PARENT_OPENERS: Final = ((findings_store_module, "_read"), (index_store_module, "_connect_to"))


# -- the census, derived ------------------------------------------------------


def _tree(module: ModuleType) -> ast.Module:
    return ast.parse(pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    """The named function anywhere in ``tree``, method or module level."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(f"{name} is gone from the module this census reads")


def _private_side_effect_calls(module: ModuleType, function_name: str) -> frozenset[str]:
    """Bare-statement calls to the module's own private helpers inside a function.

    The structural shape of a name-hygiene guard: a call whose value nobody uses,
    made to a helper this module defines, standing between the caller and a file.
    ``_finding_rows(load.accepted)`` is assigned and therefore excluded; a
    ``mkdir`` on ``self._path.parent`` is an attribute call rather than a call to
    a module-level helper, and is a precondition rather than a refusal.
    """
    tree = _tree(module)
    private = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return frozenset(
        node.value.func.id
        for node in ast.walk(_function(tree, function_name))
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in private
    )


def _shared_safety_calls(module: ModuleType, function_name: str) -> frozenset[str]:
    """Calls inside a function to names imported from the shared safety module.

    ``infrastructure/sqlite/schema.py`` is where this package keeps the helpers
    that stand in front of an ``open`` of derived state, so "what does the opener
    ask that module" is a structural reading of "what does the opener refuse".
    """
    tree = _tree(module)
    shared = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "theurian.infrastructure.sqlite.schema"
        for alias in node.names
    }
    return frozenset(
        node.func.id
        for node in ast.walk(_function(tree, function_name))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in shared
    )


def _containment_recomputing_helpers() -> frozenset[str]:
    """Public :class:`ProjectPaths` helpers that recompute containment themselves.

    A helper that routes through ``_contained`` inherits the chokepoint's proof
    and is swept by ``test_project_paths_containment.py``. These are the ones that
    ask ``is_relative_to`` in their own body, because their leaf is named by a
    value rather than by a constant -- so each carries its own refusal, its own
    remedy, and needs its own driving test. Filtered by the return annotation, so
    ``of`` (which returns a ``ProjectPaths``) falls out rather than being excluded
    by name.
    """
    tree = _tree(project_service)
    declaration = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == ProjectPaths.__name__
    )
    found: set[str] = set()
    for node in declaration.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        recomputes = any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "is_relative_to"
            for inner in ast.walk(node)
        )
        member = inspect.getattr_static(ProjectPaths, node.name)
        function = member.__func__ if isinstance(member, classmethod | staticmethod) else member
        if recomputes and get_type_hints(function).get("return") is Path:
            found.add(node.name)
    return frozenset(found)


def _refusing_load_types() -> frozenset[str]:
    """Every dataclass in the review-search domain whose ``__post_init__`` raises.

    The guard family that refuses at *construction*: a load, a record or a query
    that cannot be stored is not built, so no half-assembled file exists to clean
    up and the refusal names the value rather than a database constraint.
    """
    tree = _tree(review_search_domain)
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for member in node.body
        if isinstance(member, ast.FunctionDef)
        and member.name == "__post_init__"
        and any(isinstance(statement, ast.Raise) for statement in ast.walk(member))
    )


#: The write path's name hygiene, derived from the parent and kept only where this
#: store makes the same call. Two members today: the unlink that makes a planted
#: symlink at the working name unreachable, and the sidecar reap before the
#: publish.
WRITE_PATH_HYGIENE: Final = _private_side_effect_calls(
    findings_store_module, "replace_all"
) & _private_side_effect_calls(CHILD_MODULE, "replace_all")

#: What the opener asks before it opens. Two members today: the shape refusal and
#: the read-only URI.
OPENER_SAFETY: Final = frozenset[str]().union(
    *(_shared_safety_calls(module, opener) for module, opener in PARENT_OPENERS)
) & _shared_safety_calls(CHILD_MODULE, "_read")

#: The path helpers that recompute containment for their own leaf. Three members
#: today, of which ``review_search_for`` is this store's.
CONTAINMENT_RECOMPUTE: Final = _containment_recomputing_helpers()

#: The domain types that refuse at construction. Three members today.
LOAD_REFUSALS: Final = _refusing_load_types()

#: Every guard the derivation above found, in one set.
INHERITED_GUARDS: Final = WRITE_PATH_HYGIENE | OPENER_SAFETY | CONTAINMENT_RECOMPUTE | LOAD_REFUSALS

#: Guard -> the test that drives it. A value with no ``::`` names a test in this
#: module; one with a file prefix names the pin that already exists elsewhere, so
#: a reader can check the claim rather than take it.
PINS: Final[dict[str, str]] = {
    "_unlink_with_sidecars": (
        "test_a_symlink_at_the_building_path_is_unlinked_never_written_through"
    ),
    "_unlink_sidecars": "test_a_rebuild_publishes_over_the_sidecars_a_serve_left_behind",
    "irregular_shape_at": "test_an_artefact_at_the_store_path_is_refused_rather_than_opened",
    "read_only_uri": "test_a_serving_read_of_a_missing_store_conjures_no_database",
    "review_search_for": "test_a_store_id_that_escapes_the_state_directory_is_refused",
    "ReviewSearchLoad": "test_two_records_under_one_path_are_refused_before_a_file_exists",
    "index_for": (
        "test_derived_state_value_envelope.py::"
        "test_index_gc_refuses_an_index_build_id_that_escapes_the_state_directory"
    ),
    "state_database_named": (
        "test_derived_state_value_envelope.py::"
        "test_the_mcp_surface_refuses_a_state_database_outside_the_state_directory"
    ),
    "ReviewSearchQuery": (
        "test_review_search_store.py::test_a_query_without_a_positive_bound_cannot_be_constructed"
    ),
    "ReviewSearchRecord": (
        "test_review_search_builder.py::"
        "test_a_hand_edited_pull_request_record_number_is_refused_at_the_record"
    ),
}


def test_the_census_reads_a_source_that_still_carries_guards() -> None:
    """The premise: a walk that found nothing would report perfect coverage.

    Each family is asserted non-empty on the **parent** side before it is
    intersected, so a derivation that stopped seeing the parent's guards -- a
    renamed opener, a moved import, a helper turned into a method -- fails here
    rather than shrinking the census to nothing and passing.
    """
    assert _private_side_effect_calls(findings_store_module, "replace_all"), (
        "no name-hygiene helper was found on the parent store's write path, so the "
        "intersection below is empty for a reason that is not inheritance"
    )
    for module, opener in PARENT_OPENERS:
        assert _shared_safety_calls(module, opener), (
            f"`{module.__name__}.{opener}` asks the shared safety module nothing, so this "
            f"census has stopped reading a module it claims to derive from"
        )
    assert len(INHERITED_GUARDS) >= 6, (
        f"the census found only {sorted(INHERITED_GUARDS)}; the six guards this module "
        f"exists to pin are fewer than that"
    )


def test_every_inherited_guard_has_a_driving_test() -> None:
    """The census, both ways: a seventh inherited guard fails here until it is pinned.

    This is the assertion that makes the module a closure rather than a list. The
    left side is computed from the shipped source every run; the right side is
    written by whoever added a pin. A guard that appears -- a new safety helper on
    the opener, a fourth path helper recomputing containment, another dataclass
    refusing at construction -- joins the left side by reflection and has no pin,
    and a pin left behind by a guard that was removed has nothing to drive.

    Each named pin is checked to exist: a test in this module by attribute, a test
    elsewhere by reading the file it names. A map pointing at a test somebody
    renamed is a map that says a guard is covered when it is not.
    """
    assert set(PINS) == set(INHERITED_GUARDS), (
        f"guards with no driving test: {sorted(set(INHERITED_GUARDS) - set(PINS))}; "
        f"pins for guards that are gone: {sorted(set(PINS) - set(INHERITED_GUARDS))}"
    )

    here = set(globals())
    for guard, pin in PINS.items():
        if "::" not in pin:
            assert pin in here, f"`{guard}`'s pin `{pin}` is not a test in this module"
            continue
        file_name, test_name = pin.split("::")
        source = (
            pathlib.Path(__file__).parent.parent / _SUITE_DIRECTORY[file_name] / file_name
        ).read_text(encoding="utf-8")
        assert f"def {test_name}(" in source, (
            f"`{guard}` is recorded as driven by `{pin}`, and that file carries no such "
            f"test -- so the census claims a guard is covered when nothing covers it"
        )


#: Where each externally-pinned test lives, relative to ``tests/``.
_SUITE_DIRECTORY: Final = {
    "test_derived_state_value_envelope.py": "integration",
    "test_review_search_store.py": "integration",
    "test_review_search_builder.py": "integration",
}


# -- the corpus every pin below is driven against -----------------------------


def _record(
    *, relative_path: str, record_key: str = "T1", text: str = "a comment"
) -> ReviewSearchRecord:
    return ReviewSearchRecord(
        relative_path=relative_path,
        record_key=record_key,
        kind="review-thread",
        provider="github",
        repository="acme/order-service",
        pull_request=42,
        thread_state="resolved",
        file_path="src/order.py",
        source_uri="https://github.com/acme/order-service/pull/42",
        author_external_id="USER_A",
        author_display_name="Reviewer One",
        participant_ids=("USER_A",),
        texts=(ReviewTextFragment(channel=ReviewTextChannel.COMMENT, content=text),),
        last_seen_run_id=RUN_ID,
        last_seen_at=ONE_INSTANT,
    )


def _store_at(tmp_path: Path) -> SqliteReviewSearchStore:
    """A store under a state directory that does not exist yet, as a fresh one is."""
    return SqliteReviewSearchStore(
        tmp_path / "project" / ".theurian" / "state" / "theurian-review-local.sqlite"
    )


def _served(store: SqliteReviewSearchStore) -> tuple[str, ...]:
    return tuple(
        hit.relative_path for hit in store.search(ReviewSearchQuery(limit=10), text_chars=50)
    )


# -- guard: the unlink that makes a planted symlink unreachable ---------------


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", ["existing-empty-target", "dangling-target"])
def test_a_symlink_at_the_building_path_is_unlinked_never_written_through(
    tmp_path: Path, plant: str
) -> None:
    """#404 R1-8, inherited from ``findings_store`` and until now unpinned here.

    ``building_path`` is derived *lexically* -- the publish name plus a suffix --
    so it is contained exactly as far as the publish name is. What stops a rebuild
    writing *through* a symbolic link planted at that name is the
    ``_unlink_with_sidecars(building)`` that runs before ``sqlite3.connect`` opens
    it: the link is gone by the time the connection creates a file, so the file it
    creates is a regular one inside the tree.

    Both plants, because they fail differently and only one of them is what the
    findings twin drives. An **existing** target measures a write *through* the
    link -- neutered, the whole 32 KB database lands outside the project while the
    build reports success. A **dangling** target measures a *creation* outside it:
    ``sqlite3.connect`` follows a dangling link and creates the file at its target,
    so the neutered store leaves a database in a directory it was never pointed at.

    The publish is asserted too: with the unlink gone, ``os.replace`` renames the
    *symlink* onto the live name, so the served store is itself a link to a file
    outside the tree.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "grafted.sqlite"
    if plant == "existing-empty-target":
        target.write_bytes(b"")  # empty and writable, so a leaked write WOULD land here
    store = _store_at(tmp_path)
    store.building_path.parent.mkdir(parents=True)
    store.building_path.symlink_to(target)
    assert store.building_path.is_symlink(), "the premise: the working name is a link"

    store.replace_all(ReviewSearchLoad(records=(_record(relative_path="a/1.json"),)))

    expected = [target] if plant == "existing-empty-target" else []
    assert list(outside.iterdir()) == expected, (
        "the rebuild reached a path outside the project: the working name was opened as "
        "the symbolic link somebody planted there rather than unlinked first (#404 R1-8)"
    )
    if plant == "existing-empty-target":
        assert target.read_bytes() == b"", "the rebuild wrote its database through the link"
    assert not store.path.is_symlink(), (
        "the published store is a symbolic link: the rename moved the planted link onto the "
        "live name, so every later read follows it out of the tree"
    )
    assert _served(store) == ("a/1.json",), "and the store must still publish its own rows"


# -- guard: the sidecar reap that runs before the publish ---------------------

#: A writer that committed into the write-ahead log and died before it
#: checkpointed, run as a child so the exit is a ``os._exit`` with no close --
#: closing a ``sqlite3`` connection checkpoints, which is exactly the step this
#: models the absence of. ``wal_autocheckpoint = 0`` keeps the frames in the log
#: rather than in the database file.
_KILLED_WRITER: Final = (
    "import os, sqlite3, sys\n"
    "connection = sqlite3.connect(sys.argv[1])\n"
    "connection.execute('PRAGMA journal_mode = WAL')\n"
    "connection.execute('PRAGMA wal_autocheckpoint = 0')\n"
    "connection.execute(\"UPDATE review_records SET record_key = 'STALE'\")\n"
    "connection.commit()\n"
    "os._exit(0)\n"
)


def _leave_a_log(store: SqliteReviewSearchStore, how: str) -> None:
    """Leave a write-ahead log beside the publish name, the way ``how`` says."""
    if how == "a serving read":
        store.search(ReviewSearchQuery(limit=1), text_chars=10)
        return
    subprocess.run(  # noqa: S603
        [sys.executable, "-c", _KILLED_WRITER, str(store.path)], check=True, capture_output=True
    )


@pytest.mark.parametrize(
    ("how", "log_carries_frames"),
    [("a serving read", False), ("a writer killed before it checkpointed", True)],
    ids=["reader-left-log", "killed-writers-log"],
)
def test_a_rebuild_publishes_over_the_sidecars_a_serve_left_behind(
    tmp_path: Path, how: str, log_carries_frames: bool
) -> None:
    """#404 R1-3 at the fourth store: the publish name never holds a foreign log.

    A database is three names, and the previous database's ``-wal``/``-shm``
    belong to no database once the main file has been replaced. ``replace_all``
    reaps them immediately before the rename, so no moment mixes a database with a
    log that is not its own.

    **Two ways the log gets there, and only the second is harmful -- measured, not
    assumed.** The **serve path** is the one a shipped project reaches every day:
    one ``mode=ro`` ``search`` against a freshly built store creates both
    companions at the publish name. That log is **empty** (measured here: 0 bytes,
    32 KiB of shared-memory index beside it), and SQLite ignores an empty one, so
    with the reap removed the store still answers correctly -- which is why this
    arm pins the *on-disk* invariant and claims no more. A **killed writer**'s log
    carries real frames of the database it was written for (measured: 4,152
    bytes), and with the reap removed the rebuild renames a new inode onto the
    name, leaves those frames beside it, and the next ``search`` answers
    ``a/1.json`` -- **the corpus the rebuild replaced**. That is the failure worth
    naming: a rebuild's decisions, withholding included, reverted by a file nobody
    reaped, with no error anywhere.

    A garbage log was measured too and is *not* driven here: 37 bytes of text at
    the same name is ignored like the empty one, because SQLite validates the
    log's header before it applies anything. Planting bytes would therefore have
    made an arm that cannot distinguish the guard from its absence.

    The premise comes first in both arms: if the log stopped appearing, or stopped
    carrying what this arm says it carries, the assertions below would pass over a
    state they never reached.
    """
    store = _store_at(tmp_path)
    store.replace_all(ReviewSearchLoad(records=(_record(relative_path="a/1.json"),)))
    assert _served(store) == ("a/1.json",)
    wal = store.path.with_name(store.path.name + "-wal")
    shm = store.path.with_name(store.path.name + "-shm")

    _leave_a_log(store, how)

    assert wal.exists() and shm.exists(), (
        f"the premise: {how} leaves a write-ahead log and its shared-memory index at the "
        f"publish name, which is the pair the next publish has to reap"
    )
    assert (wal.stat().st_size > 0) is log_carries_frames, (
        f"the premise: {how} leaves a log of {wal.stat().st_size} bytes, and this arm is "
        f"written for one that {'carries' if log_carries_frames else 'carries no'} frames"
    )

    store.replace_all(
        ReviewSearchLoad(records=(_record(relative_path="a/2.json", record_key="T2"),))
    )

    assert not wal.exists() and not shm.exists(), (
        "the previous store's companions survived the publish, so the live name now holds a "
        "new database beside a log that belongs to the one it replaced (#404 R1-3)"
    )
    assert _served(store) == ("a/2.json",), (
        "the published store answered from the log rather than from the database that was "
        "just renamed onto the name: a rebuild that changed what is served -- withheld a "
        "record, dropped one, added one -- had its whole decision reverted by a file the "
        "publish left behind"
    )


# -- guard: the shape refusal in front of the open ----------------------------

#: The child that opens a planted store path and reports what came back.
#:
#: **A child, because this refusal's absence is a hang, not a failure** (#586's
#: M-3 family). ``sqlite3.connect`` against a named pipe blocks inside ``open()``
#: -- measured on the sibling store, still blocked when a six-second watchdog
#: fired -- and the suite's ``hang_guard`` does not reach it, because SQLite
#: retries a call a signal interrupted. In the daemon that blocked call is inside
#: ``review.search``'s admission gate holding one of ``MAX_CONCURRENT_SEARCHES``
#: permits, and removing the artefact does not release it. The kill below is the
#: only bound; an in-process version of this test parks the whole run instead of
#: failing it.
_CHILD: Final = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from theurian.domain.review_search import ReviewSearchQuery\n"
    "from theurian.infrastructure.sqlite.review_search_store import (\n"
    "    ReviewSearchStoreError,\n"
    "    SqliteReviewSearchStore,\n"
    ")\n"
    "store = SqliteReviewSearchStore(Path(sys.argv[1]))\n"
    "try:\n"
    "    store.search(ReviewSearchQuery(limit=1), text_chars=10)\n"
    "except BaseException as exc:\n"
    "    print(json.dumps({\n"
    "        'type': type(exc).__name__,\n"
    "        'message': str(exc),\n"
    "        'remedy': getattr(exc, 'remedy', ''),\n"
    "        'graded': isinstance(exc, ReviewSearchStoreError),\n"
    "    }))\n"
    "else:\n"
    "    print(json.dumps({'type': None}))\n"
)

#: How long the child gets before it is killed and the test fails. Generous next
#: to a refusal that never opens the file, short next to a CI job.
_CHILD_TIMEOUT_SECONDS: Final = 30.0


def _refusal_in_a_child(path: Path) -> dict[str, object]:
    """Serve from ``path`` in a child process, killed if it does not return."""
    try:
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _CHILD, str(path)],
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`search` did not return within {_CHILD_TIMEOUT_SECONDS}s against {path.name} and "
            f"the child was killed: the open is unbounded again, and in the daemon that call "
            f"is holding an admission permit that removing the artefact does not release"
        )
    lines = done.stdout.strip().splitlines()
    assert lines, f"the child printed no report; it exited {done.returncode}: {done.stderr!r}"
    report: dict[str, object] = json.loads(lines[-1])
    return report


@contextmanager
def _planted(shape: str, path: Path) -> Iterator[None]:
    """Put ``shape`` at ``path`` for the body of the ``with``.

    The socket is bound by its **relative** name from inside the directory: an
    ``AF_UNIX`` address is capped near a hundred bytes and a pytest temporary
    directory already spends most of that, so binding the absolute path would
    raise and the case would skip on the platform it is written for.
    """
    if shape == "a named pipe (FIFO)":
        os.mkfifo(path)
        yield
    elif shape == "a socket":
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path.name)
            yield
    else:
        path.mkdir()
        yield


@pytest.mark.skipif(
    not _CAN_PLANT_A_PIPE_AND_A_SOCKET, reason="os.mkfifo and AF_UNIX are POSIX-only"
)
@pytest.mark.parametrize(
    "shape",
    ["a named pipe (FIFO)", "a socket", "a directory"],
    ids=["fifo", "socket", "directory"],
)
def test_an_artefact_at_the_store_path_is_refused_rather_than_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """#586's M-3 family at the fourth store: ``stat`` before ``connect``.

    ``mode=ro`` says nothing about *what* is at the path. ``stat`` answers from
    the directory entry and never opens anything, which is the only check that
    cannot itself be the thing that blocks, and this store asks it before the
    connection is made.

    Three shapes, one harness, and each fails differently with the check removed
    -- measured on this branch, macOS 26.6.2, SQLite 3.47.1. The **named pipe** is
    the member the guard exists for and the only one that *hangs*: the child was
    still inside ``connect`` when the kill fired at ``_CHILD_TIMEOUT_SECONDS``,
    with nothing published, and in the daemon that same call holds one of
    ``MAX_CONCURRENT_SEARCHES`` permits for as long as it blocks. The **socket**
    says the population is *not a regular file* rather than *a pipe*; neutered it
    comes back as the driver's ``unable to open database file``. The **directory**
    is a member of this store's refusal rather than of ``irregular_shape``
    itself -- the same widening ``findings_store._read`` makes -- because neutered
    it comes back as ``disk I/O error``, which names nothing an operator can act
    on and no cure the operator can run.

    The refusal is asserted to be the store's own class as well as its own words:
    ``review.search`` converts ``ReviewSearchStoreError`` into one constant refusal,
    so an escape of any other type is a second refusal shape for a second kind of
    damage (SEC-13).
    """
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.chdir(state)
    path = state / "theurian-review-local.sqlite"

    with _planted(shape, path):
        report = _refusal_in_a_child(path)

    assert report["graded"] is True, (
        f"the serving read answered a planted {shape} with a {report['type']} rather than the "
        f"store's own graded error, so the tool surface publishes it as something other than "
        f"the standing store-unavailable refusal: {report}"
    )
    assert shape in str(report["message"]), (
        f"the refusal does not name what is at the path: {report['message']!r}"
    )
    remedy = str(report["remedy"])
    assert str(path) in remedy, f"the remedy names no artefact to clear: {remedy!r}"
    assert "theurian review build" in remedy, f"the remedy names nothing to run: {remedy!r}"


# -- guard: the read-only URI -------------------------------------------------


def test_a_serving_read_of_a_missing_store_conjures_no_database(tmp_path: Path) -> None:
    """What ``mode=ro`` buys observably, which the refusal alone cannot show.

    ``search`` refuses a missing store either way: opened read-write, an absent
    file becomes an *empty* database whose first read then fails on the missing
    metadata table, and the same graded error comes back. So a test that asserted
    only the refusal passes over a read connection that has stopped being
    read-only -- the mutation the sibling store measured surviving its whole suite.

    What separates them is the file. A read that creates one has written into the
    project's state directory in order to answer a question, and leaves a
    stamped-as-nothing database where ``theurian review build`` expects either its
    own file or none: neutered, this directory holds a 0-byte
    ``theurian-review-local.sqlite`` afterwards.

    **The state directory has to exist for this test to be able to fail.**
    ``sqlite3.connect`` cannot create a database inside a directory that is not
    there, so an assertion written over a bare fixture path is satisfied by the
    missing *directory* and says nothing about the connection. A project that has
    run ``migrate apply`` has this directory, so this is also the shape a real
    missing store arrives in.
    """
    store = _store_at(tmp_path)
    store.path.parent.mkdir(parents=True)
    assert not store.path.exists(), "the premise: the directory exists and the store does not"

    with pytest.raises(ReviewSearchStoreError):
        store.search(ReviewSearchQuery(limit=1), text_chars=10)

    assert list(store.path.parent.iterdir()) == [], (
        f"serving a missing store left {[p.name for p in store.path.parent.iterdir()]} in the "
        f"state directory. The read connection must be `mode=ro`: a query that conjures an "
        f"empty database leaves a file nothing built, and the next reader finds a store with "
        f"no stamp rather than no store at all"
    )


# -- guard: the containment recompute for this store's own leaf ---------------


@pytest.mark.parametrize(
    ("store_id", "why"),
    [
        ("../../../etc/passwd", "an id that resolves out of the state directory"),
        ("with\x00nul", "an id the platform will not accept as a filename"),
    ],
    ids=["escaping", "unusable"],
)
def test_a_store_id_that_escapes_the_state_directory_is_refused(
    tmp_path: Path, store_id: str, why: str
) -> None:
    """``review_search_for``'s own check, which the containment sweep records as undriven.

    ``ProjectPaths.review_search_for`` is state-scoped rather than merely
    root-scoped: it resolves the candidate and asks whether it is still inside
    ``.theurian/state``. ``test_project_paths_containment.py`` sweeps what happens
    when ``state`` *itself* escapes -- refused by the ``self.state`` access this
    method makes first -- and records in the same note that the id's own escape is
    the half that sweep does not drive. This is that half.

    No shipped caller supplies an untrusted id today: ``mcp/tools.py`` passes the
    constant :data:`REVIEW_SEARCH_STORE_ID`. What the check buys now is the
    ordering -- the containment a pointer would need is already the one in force
    -- and the measured cost of getting that ordering wrong is on
    ``state_database_named``, where root-scoped containment served a decoy
    *inside* the checkout at exit 0. Neutered, the escaping id below is answered
    with a path under ``.theurian/`` that the state directory does not contain.

    Both arms, because they leave through different branches: the escape fails the
    containment comparison, and the NUL raises out of ``resolve`` before any
    comparison happens. A fix to one that skipped the other would publish a
    ``ValueError`` to a caller that may only catch ``TheurianError``.
    """
    root = tmp_path / "project"
    (root / ".theurian").mkdir(parents=True)
    paths = ProjectPaths.of(root)
    honest = paths.review_search_for(REVIEW_SEARCH_STORE_ID)
    assert honest.parent == paths.state.resolve(), (
        "the premise: an honest store id resolves to a leaf inside the state directory, or "
        "the refusal below is not about containment"
    )

    with pytest.raises(ProjectError) as excinfo:
        paths.review_search_for(store_id)

    assert excinfo.value.remedy == REVIEW_SEARCH_STORE_REMEDY, (
        f"{why} was refused with another artifact's cure: {excinfo.value.remedy!r}"
    )


# -- guard: the load that refuses two records under one path ------------------


def test_two_records_under_one_path_are_refused_before_a_file_exists(tmp_path: Path) -> None:
    """The store's primary key, moved forward to where the path can still be named.

    ``relative_path`` is a record's identity: the evidence reader guarantees it
    unique, and the store makes it the primary key. Two records claiming one path
    therefore means one of them would be lost, and *where that is discovered*
    decides what an operator is told.

    Neutered -- the loop removed from ``ReviewSearchLoad.__post_init__`` -- the
    load builds happily and the failure moves into the write, after a working file
    has been created and thrown away again by the cleanup arm. Measured there:
    ``The review search store could not be used (writing
    theurian-review-local.sqlite: UNIQUE constraint failed:
    review_records.relative_path)``, carrying the write-path remedy -- *check that
    .theurian/state is writable and there is free disk space* -- for a corpus
    whose fault is two records under one name and whose disk is fine. Refusing at
    construction names the path instead, which is the value somebody has to go and
    look at.

    The positive control is the second half: a load carrying two *distinct* paths
    must build and store both, or the refusal above is satisfied by a type that
    refuses every load.
    """
    duplicated = "a/review-thread/1.json"
    first = _record(relative_path=duplicated, record_key="T1")
    second = _record(relative_path=duplicated, record_key="T2")

    with pytest.raises(InvariantViolationError) as excinfo:
        ReviewSearchLoad(records=(first, second))

    assert duplicated in str(excinfo.value), (
        f"the refusal must name the path two records claim, which is the whole reason it "
        f"happens here rather than at the primary key: {excinfo.value}"
    )
    store = _store_at(tmp_path)
    assert not store.path.exists() and not store.building_path.exists(), (
        "a refused load must leave nothing behind: the refusal is at construction, so no "
        "write has begun and there is no working file for a later build to open and extend"
    )

    store.replace_all(
        ReviewSearchLoad(
            records=(first, _record(relative_path="a/review-thread/2.json", record_key="T2"))
        )
    )

    assert _served(store) == ("a/review-thread/1.json", "a/review-thread/2.json"), (
        "two records under two paths must both be stored, or the refusal above is a type "
        "that refuses every load rather than a duplicate one"
    )
