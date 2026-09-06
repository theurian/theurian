"""A hostile *value* in a derived state file reaches a caller as an envelope.

The neighbour class of ``test_contained_path_envelope.py``, and that file names
the seam between them: containment refuses a doctored **path** -- a symbolic link
force-added past ADR-0004's ignore, keyed on ``ProjectPaths._contain`` -- while
what this file drives is a doctored **value**, an ``indexBuildId`` read out of a
file that is derived, git-ignored and unsigned (SEC-7, the GHSA-266v family), or
the *mode* of the directory holding it. Any local process can write those; a
clone can deliver them.

**The class, stated so a neighbour cannot falsify it.** A derived, unsigned,
hand-editable state value -- a pointer payload field, or the mode of the
directory the pointer lives in -- must reach every ``--json`` CLI surface as a
refusal or a degraded payload, and never as an uncaught exception. It is *not*
"every way a command can fail": a containment refusal belongs to the
neighbouring file, and a corrupted database to the corruption suite.

**The MCP half has a different envelope contract and is split by which half it
falls in.** A value that stops the *index* from answering is a graded fallback,
and its recipes live in ``test_index_fallback.py``'s ``BREAKAGES`` table --
``pointer-names-an-unusable-filename`` is the one added for #388. A value that
stops ``_resolve`` from finding the canonical store at all is a refusal rather
than a fallback, so it has no row there, and the one this class reaches --
``databaseFilename`` -- is driven at the foot of this file.

**Measured on ``75fe9b4f`` before the fix, against the real CLI in a sandbox**
(``HOME`` and ``THEURIAN_DATA_DIR`` redirected), every row exited 1 with **zero
bytes on stdout** and a Rich traceback:

=========================================  ===========================  ========================
plant                                      surface                      traceback at
=========================================  ===========================  ========================
``indexBuildId`` = ``"../"*8 + "tmp/x"``   ``index gc --json``          ``index_commands.py:932``
``indexBuildId`` = 234 ``"A"``s            ``index gc --json``          ``index_commands.py:932``
``.theurian/state`` at mode ``000``        seven of nine ``CLI_SWEEP``  three different lines
=========================================  ===========================  ========================

The mode plant's seven are ``index build``, ``index gc``, ``index status``,
``migrate status``, ``migrate validate``, ``migrate apply`` and ``project
status``; ``project list`` and ``version`` answered normally, because neither
reads this project's state directory. Six of the seven raised through one
function -- ``read_active_state``'s ``exists()`` probe, which sat above the
``try`` whose ``except OSError`` was written for that errno -- and the seventh,
``project status``, raised from ``database.exists()`` inside its own payload
literal.

**Why the two axes are one class and not two.** The escaping id is refused by
``ProjectPaths.index_for``; the oversized one is not, because ``Path.resolve()``
in non-strict mode never stats, so the refusal that catches the first cannot see
the second and the caller's own ``os.stat`` raises ``ENAMETOOLONG``. The mode
plant is the same shape read from the other side: a probe outside the ``try``
whose ``except`` was written for exactly these errnos. One root cause -- *a
derived value is trusted as far as a syscall, and the syscall is not guarded* --
which is why they are driven together.

The population argument for the value axis is
:func:`test_every_index_for_caller_grades_the_stat_beside_the_call`, which reads
the call sites out of the source rather than listing them here.
"""

from __future__ import annotations

import ast
import asyncio
import errno
import json
import os
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
import typer.main
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from mcp.types import CallToolResult, TextContent
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import INDEX_POINTER_REMEDY, ProjectRegistry
from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

#: Skipped where a mode cannot refuse anything: Windows has no POSIX bits, and
#: root is not stopped by them. Offline CI runs as root, where a mode-000
#: directory denies nothing and every plant below would measure its own absence.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: 15 (``theurian-index-``) + this + 7 (``.sqlite``) is one byte past a 255-byte
#: ``NAME_MAX``. Written as the arithmetic rather than as ``234``, so a reader
#: can see which bound is being crossed; the pair of tests below asserts the
#: behaviour changes at exactly this length, which is what keeps a platform with
#: a different ``NAME_MAX`` from passing while measuring nothing.
_TOO_LONG_FOR_A_FILENAME: Final = 255 - len("theurian-index-") - len(".sqlite") + 1

#: An id that resolves out of ``.theurian/state/``. Eight levels, so the plant
#: escapes from any checkout depth ``tmp_path`` produces.
_ESCAPING_BUILD_ID: Final = "../" * 8 + "tmp/evil"

#: The command paths swept under the mode plant, mirroring
#: ``test_canonical_store_corruption.py``'s ``CLI_SWEEP`` for the reason
#: ``test_contained_path_envelope.py`` mirrors it too: what is safe to run
#: against one corpus many times over. The exclusions recorded there apply here
#: unchanged and are not restated. Held against the shipped app by
#: :func:`test_every_swept_command_is_one_the_app_still_ships`, so a rename
#: fails rather than silently sweeping nothing.
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

BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-03T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth
    revisionId: 01K1AREVAA01234567890ABCDE
    contentFile: ../knowledge/architecture/auth.md
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
          sourceUri: git://demo/auth.md
"""


@dataclass(frozen=True, slots=True)
class Ran:
    """What one ``--json`` invocation produced, kept apart channel by channel.

    ``escaped`` is read off the runner rather than off the streams: ``CliRunner``
    keeps an uncaught exception on ``result.exception`` instead of letting
    Typer's Rich handler render it, so an escape would otherwise be invisible in
    what was captured -- and an escape is the whole subject of this file.
    """

    exit_code: int
    stdout: str
    stderr: str
    escaped: str | None

    @property
    def envelope(self) -> dict[str, Any] | None:
        if not self.stderr.strip():
            return None
        try:
            parsed = json.loads(self.stderr)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @property
    def payload(self) -> dict[str, Any] | None:
        if not self.stdout.strip():
            return None
        parsed = json.loads(self.stdout)
        return parsed if isinstance(parsed, dict) else None


def _run(*args: str) -> Ran:
    result = runner.invoke(app, [*args, "--json"])
    escaped = result.exception
    if isinstance(escaped, SystemExit):
        escaped = None
    return Ran(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr or "",
        escaped=None if escaped is None else type(escaped).__name__,
    )


def _refused_cleanly(ran: Ran, *, code: int = 1) -> dict[str, Any]:
    """The CP-2 contract: nonzero, one parseable document on stderr, clean stdout."""
    assert ran.escaped is None, (
        f"an exception reached the caller instead of a document: {ran.escaped}"
    )
    assert ran.exit_code == code, f"exit {ran.exit_code}, stderr: {ran.stderr}"
    assert ran.stdout == "", f"the machine channel was not clean: {ran.stdout!r}"
    envelope = ran.envelope
    assert envelope is not None, f"stderr held no JSON document: {ran.stderr!r}"
    assert envelope.get("error"), "the envelope carries no message"
    assert envelope.get("remedy"), "the envelope carries no cure"
    return envelope


@pytest.fixture(scope="module")
def _prepared(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """One registered, applied, indexed project, built **once** for this module.

    Module-scoped because the setup is four real CLI invocations -- `init`,
    `project register`, `migrate apply`, `index build` -- and this file has 22
    tests. Per test that is 20 index builds, and it cost the offline CI job
    (a 10-minute budget it already used 8m06s of on ``main``) more than its
    remaining headroom: the job timed out at 68%. Every test still gets a
    *pristine* project, restored by :func:`project` below rather than rebuilt.

    ``HOME``, ``THEURIAN_DATA_DIR`` and the working directory are set here with a
    hand-held ``MonkeyPatch`` and undone at module teardown, because the built-in
    ``monkeypatch`` fixture is function-scoped. Setting them is not optional and
    not merely hygiene: the CLI resolves a project from ``Path.cwd()``, so a run
    without the ``chdir`` initialises Theurian into the developer's own checkout.
    """
    base = tmp_path_factory.mktemp("derived-state")
    root = base / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("THEURIAN_DATA_DIR", str(base / "datadir"))
        patch.setenv("HOME", str(base / "home"))
        patch.chdir(root)

        assert _run("init").exit_code == 0
        (root / ".theurian/knowledge/architecture/auth.md").write_text(BODY, encoding="utf-8")
        (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(
            MIGRATION, encoding="utf-8"
        )
        for command in (["project", "register"], ["migrate", "apply"], ["index", "build"]):
            ran = _run(*command)
            assert ran.exit_code == 0, f"{command}: {ran.stdout}{ran.stderr}"

        shutil.copytree(root / ".theurian", base / "pristine")
        yield root


@pytest.fixture
def project(_prepared: Path) -> Iterator[Path]:
    """The prepared project, and ``.theurian/`` put back exactly as it was after.

    Restoring is what makes the module-scoped build safe, and it is a *copy of
    the whole subtree* rather than of the files each test is known to touch: a
    test that names its own damage cannot report damage it did not expect, which
    is how a shared fixture turns one test's failure into the next one's.

    The ``chmod`` loop runs first because a plant here is a mode-``000``
    directory, and ``rmtree`` cannot descend into one -- including on the path
    where a test failed before its own ``finally`` restored the bits.
    """
    yield _prepared
    theurian = _prepared / ".theurian"
    for path in (theurian, *theurian.rglob("*")):
        if path.is_dir():
            path.chmod(0o700)
    shutil.rmtree(theurian)
    shutil.copytree(_prepared.parent / "pristine", theurian)


def _builds(root: Path) -> list[str]:
    return sorted(p.name for p in (root / ".theurian/state").glob("theurian-index-*.sqlite"))


def _publish_a_build_id(root: Path, build_id: str) -> None:
    """Keep every published field and replace only the id, which is the axis."""
    pointer = root / ".theurian/state/active-index.json"
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["indexBuildId"] = build_id
    pointer.write_text(json.dumps(payload), encoding="utf-8")


# -- The id that resolves out of the state directory (#551) ------------------


@pytest.mark.parametrize("extra", [(), ("--dry-run",)], ids=["reclaiming", "dry-run"])
def test_index_gc_refuses_an_index_build_id_that_escapes_the_state_directory(
    project: Path, extra: tuple[str, ...]
) -> None:
    """#551, both arms of ``index gc``.

    ``--dry-run`` is parametrised rather than trusted to share the path: the
    probe that raised sits *above* the ``dry_run`` branch, so the reporting arm
    crashed identically, and a fix applied to one of the two would leave the
    other publishing a traceback for a command that changes nothing.
    """
    _publish_a_build_id(project, _ESCAPING_BUILD_ID)

    envelope = _refused_cleanly(_run("index", "gc", *extra))

    assert envelope["remedy"] == INDEX_POINTER_REMEDY, (
        "the escaping id must take the pointer's own cure, the one the sibling "
        f"unreadable-pointer arm publishes: {envelope['remedy']}"
    )


def test_the_escaping_id_refusal_reclaims_nothing(project: Path) -> None:
    """The safety half, which the envelope alone does not assert.

    A run that had already unlinked is still a refusal by every assertion above
    it, and ``gc``'s whole hazard is that it deletes: with the published build
    unknowable, *every* file on disk looks unreferenced.
    """
    before = _builds(project)
    assert before, "the fixture published no build, so this asserts nothing"
    _publish_a_build_id(project, _ESCAPING_BUILD_ID)

    _refused_cleanly(_run("index", "gc"))

    assert _builds(project) == before, f"a refused `gc` reclaimed something: {before}"


# -- The id the operating system will not accept as a filename (#388) --------


def test_index_gc_refuses_an_index_build_id_too_long_to_be_a_filename(project: Path) -> None:
    """#388's ``index gc`` face, which ``index_for``'s own refusal cannot see.

    Deliberately a separate test rather than a second parameter of the one
    above: the two reach the envelope through different ``except`` arms, and one
    tuple over both would stay green with either arm deleted.
    """
    _publish_a_build_id(project, "A" * _TOO_LONG_FOR_A_FILENAME)

    envelope = _refused_cleanly(_run("index", "gc"))

    assert envelope["remedy"] == INDEX_POINTER_REMEDY
    assert "will not answer" in envelope["error"], (
        "the message must say the probe could not be answered rather than "
        f"asserting the file is absent, which is not what was established: {envelope['error']}"
    )


def test_a_build_id_one_character_shorter_is_answered_normally(project: Path) -> None:
    """The narrowness control, and the reason the length constant is arithmetic.

    Without it, a fix that refused *every* unpublished id -- or a platform whose
    ``NAME_MAX`` is not 255 -- would read as green while the test above measured
    nothing. At one byte under the bound the id is merely a build that is not on
    disk, which is the pre-existing refusal with its own rebuild cure.
    """
    _publish_a_build_id(project, "A" * (_TOO_LONG_FOR_A_FILENAME - 1))

    envelope = _refused_cleanly(_run("index", "gc"))

    assert envelope["remedy"] != INDEX_POINTER_REMEDY, (
        "an id the filesystem accepts names a build that is simply missing, and "
        f"its cure is the rebuild: {envelope['remedy']}"
    )
    assert "not there" in envelope["error"], envelope["error"]


# -- The directory the pointers live in, unreadable (#389) -------------------


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
@pytest.mark.parametrize("command", CLI_SWEEP, ids=[" ".join(c) for c in CLI_SWEEP])
def test_a_state_directory_no_process_can_read_never_reaches_a_traceback(
    project: Path, command: tuple[str, ...]
) -> None:
    """#389's third face, swept rather than aimed at the two commands it named.

    Seven of these nine raised at ``75fe9b4f``, through three different lines, so
    a test over ``project status`` and ``index status`` alone -- the pair the
    issue lists -- would have called the class closed with five faces live. What
    each command answers is deliberately *not* asserted here: ``version`` and
    ``project list`` do not read this directory and answer 0, ``project status``
    degrades to a payload, and the rest refuse. The one property they share is
    the one the class is about.

    The ``pytest.raises`` is a **precondition**, not decoration: a mode cannot
    refuse the owning process on every platform, and a plant that denied nothing
    would assert the happy path under a hostile name.
    """
    state = project / ".theurian/state"
    state.chmod(0o000)
    try:
        with pytest.raises(OSError, match="Permission denied"):
            (state / "active.json").read_text(encoding="utf-8")

        ran = _run(*command)
    finally:
        state.chmod(0o700)

    assert ran.escaped is None, (
        f"{' '.join(command)} let an exception reach the caller instead of a "
        f"document: {ran.escaped}"
    )
    if ran.exit_code == 0:
        assert ran.payload is not None, f"{' '.join(command)} published no payload at exit 0"
    else:
        # `code=ran.exit_code` would compare the run with itself (round one,
        # LOW). The *value* is deliberately not pinned per command here -- this
        # sweep's claim is the envelope, and the individual codes belong to the
        # commands' own suites -- but it must be one of this CLI's, so a run that
        # ended in some other way cannot pass by defining its own expectation.
        assert ran.exit_code in {1, EXIT_STATE_ERROR}, (
            f"{' '.join(command)} exited {ran.exit_code}, which is neither of this "
            f"CLI's refusal codes"
        )
        _refused_cleanly(ran, code=ran.exit_code)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_project_status_says_it_cannot_know_rather_than_reporting_no_built_state(
    project: Path,
) -> None:
    """The one command in the sweep that degrades, and what its degradation says.

    ``stateBuilt: false`` would be a claim -- "this project has no built state" --
    about a question the probe was refused an answer to, and its cure (`theurian
    migrate apply`) cannot run in the condition that produced it either. ``null``
    is the "cannot know" ``registered`` beside it already spells, and the
    ``reason``/``remedy`` pair is what makes a ``null`` actionable.
    """
    state = project / ".theurian/state"
    built_before = _run("project", "status").payload
    assert built_before is not None and built_before["stateBuilt"] is True, (
        "the fixture applied no state, so `null` here would not be a degradation"
    )

    state.chmod(0o000)
    try:
        ran = _run("project", "status")
    finally:
        state.chmod(0o700)

    payload = ran.payload
    assert payload is not None, f"exit {ran.exit_code}, stderr: {ran.stderr}"
    assert payload["stateBuilt"] is None, (
        f"the probe could not be answered, so `stateBuilt` must not claim one: "
        f"{payload['stateBuilt']!r}"
    )
    assert payload.get("reason"), "a `cannot know` with no reason beside it is unactionable"
    # The cure names the delete *and* the `chmod`, in that order, because which
    # of the two is in the way depends on where the mode sits: `unlink` is
    # governed by the parent's bits, so a mode-000 *file* in a writable
    # directory really is deletable. The assertion message said "not the pointer
    # deletion", which contradicted the text it was asserting about (round one,
    # LOW); what it is actually about is that the delete is not published
    # *alone*.
    assert "chmod" in payload.get("remedy", ""), (
        f"the cure names the delete without the `chmod` that a mode failure may "
        f"require before it can be carried out: {payload.get('remedy')!r}"
    )


def test_every_swept_command_is_one_the_app_still_ships() -> None:
    """The vacuity guard for :data:`CLI_SWEEP`, since it is mirrored and not imported.

    A rename that left this tuple behind would sweep nine invocations that all
    fail for the same uninteresting reason -- "no such command" -- and every
    assertion above would still hold.
    """
    shipped = typer.main.get_command(app)
    for command in CLI_SWEEP:
        node: Any = shipped
        for word in command:
            assert hasattr(node, "commands"), f"{' '.join(command)}: {word} has no subcommands"
            assert word in node.commands, f"the app no longer ships `{' '.join(command)}`"
            node = node.commands[word]


# -- The second value on the axis: `databaseFilename` -------------------------
#
# Three joins build a path from it -- reproduce with `git grep -n
# 'active.database_filename' -- packages/theurian-core/src`, four lines on
# 2026-09-05, three joins and one message. Each is measured here or recorded:
# `mcp/tools.py`'s and `cli/commands.py::_verify_history`'s stat it and are
# driven below; `cli/index_commands.py`'s hands it to `IndexRequest` and is
# graded by `_run_build`'s `except (TheurianError, sqlite3.Error, OSError)`.


def _publish_a_database_filename(root: Path, filename: str) -> None:
    """Replace only ``databaseFilename`` in the canonical pointer."""
    pointer = root / ".theurian/state/active.json"
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["databaseFilename"] = filename
    pointer.write_text(json.dumps(payload), encoding="utf-8")


def _move_the_state_hash(root: Path) -> None:
    """Add a migration, so the recorded hash and the derived one disagree.

    ``_verify_history`` returns early while they match -- there is no *previous*
    state to check against -- so a plant made without this measures the early
    return rather than the join below it. Asserted rather than assumed by the
    tests that use it: they require the refusal, which cannot arrive from the
    early return.
    """
    second = MIGRATION.replace(MIGRATION_ID, "01K1BBBBBB01234567890ABCDE")
    second = second.replace("01K1AREVAA01234567890ABCDE", "01K1BREVBB01234567890ABCDE")
    second = second.replace("architecture.auth\n", "architecture.auth2\n")
    (root / ".theurian/migrations/01K1BBBBBB01234567890ABCDE-b.yaml").write_text(
        second, encoding="utf-8"
    )


@pytest.mark.parametrize(
    "command",
    [("migrate", "status"), ("migrate", "apply"), ("index", "build")],
    ids=["migrate-status", "migrate-apply", "index-build"],
)
def test_a_database_filename_the_os_will_not_answer_for_refuses_the_history_check(
    project: Path, command: tuple[str, ...]
) -> None:
    """The third join, and the one a subset would have missed.

    ``_verify_history`` runs inside ``_require_project``, so this reaches every
    command routed through it. It stats ``paths.state / active.database_filename``
    -- a value ``ActiveState.from_json`` only ``str()``s -- and a 260-character
    one raised ``ENAMETOOLONG`` through all three of these at exit 1 with **zero
    bytes on stdout**, measured after the two joins above were already converted.

    A **refusal** and not an early return, which is the property worth pinning:
    the early returns there are for "there is genuinely nothing to check
    against", and treating a name the OS will not answer for as absent would
    report a clean FR-K5 history at exit 0 for a project whose evidence was
    never read.
    """
    _move_the_state_hash(project)
    _publish_a_database_filename(project, "B" * 260 + ".sqlite")

    envelope = _refused_cleanly(_run(*command), code=4)

    assert "FR-K5" in envelope["error"], (
        f"the refusal does not say which guarantee went unconfirmed: {envelope['error']}"
    )
    assert "active.json" in envelope["remedy"], (
        f"the cure does not name the pointer that carries the bad value: {envelope['remedy']}"
    )


# -- The MCP refusal the fallback table cannot reach --------------------------


async def _search(registry: ProjectRegistry) -> Any:
    """``knowledge.search`` through the entry point the transport uses."""
    return await build_server(registry, None).call_tool(
        "knowledge.search", {"projectId": "demo", "query": "token"}
    )


def test_a_database_filename_the_os_will_not_answer_for_is_refused_with_a_cure(
    project: Path,
) -> None:
    """The third value on this axis, and the one the fallback table has no row for.

    ``databaseFilename`` is trusted the same way ``indexBuildId`` was:
    ``ActiveState.from_json`` only ``str()``s it, and ``verify_state_provenance``
    binds ``(root, state_hash)`` rather than the filename, so nothing above the
    join has read what it says. A 260-character one made ``_resolve``'s
    ``exists()`` raise ``ENAMETOOLONG`` past the ``except TheurianError``
    boundary every tool is wrapped in, and the client received the SDK's
    ``UnexpectedToolError`` -- "Error executing tool", carrying no remedy at all
    (measured at ``75fe9b4f``).

    Asserted on the *refusal's* content and not merely on its type: a
    ``ToolError`` whose text is the raw errno string would satisfy "it no longer
    crashes" and still leave an agent with nothing to do, and it would put the
    operator's absolute path on a surface that keeps them off (GHSA-97q9).
    """
    registry = ProjectRegistry.default(project.parent / "datadir")
    _publish_a_database_filename(project, "B" * 260 + ".sqlite")

    with pytest.raises(SdkToolError) as raised:
        asyncio.run(_search(registry))

    message = str(raised.value)
    assert "migrate apply" in message, f"the refusal names no next action: {message}"
    assert "active.json" in message, f"the refusal names no artefact to act on: {message}"
    assert str(project) not in message, (
        f"the refusal carries the operator's absolute path to a client: {message}"
    )


#: The two shapes an escaping ``databaseFilename`` takes, and they are not one
#: shape twice. ``../../`` resolves **inside** the project root -- a file at the
#: checkout's top level, which a clone delivers as ordinary tracked content --
#: while ``../../../`` leaves the tree. The first was served at exit 0 by a
#: containment check that proved only the root (round two, security H-1), so a
#: row over the second alone measures a guard the first walks past.
_ESCAPING_FILENAMES: Final = (
    pytest.param("../../decoy.sqlite", id="inside-the-root"),
    pytest.param("../../../outside.sqlite", id="past-the-root"),
)


@pytest.mark.parametrize("filename", _ESCAPING_FILENAMES)
def test_the_mcp_surface_refuses_a_state_database_outside_the_state_directory(
    project: Path, filename: str
) -> None:
    """Round two, security H-1: the exclusion cited a test row that did not exist.

    The plant is a **real, readable** database at the escaping path, copied from
    the project's own, so nothing downstream can refuse it for being absent or
    corrupt: without that, this row would pass over a file the guard never had to
    judge. Measured before the fix at ``4dd322d6`` with the in-root spelling:
    ``knowledge.search`` served the decoy's rows at exit 0.

    Three assertions, because "it refused" is satisfied by three different wrong
    refusals: the message must name the *state directory* (the working-tree
    wording is false of the in-root half), it must not carry the operator's
    absolute path (GHSA-97q9), and it must not carry ``derived_escape_remedy``'s
    "remove `.theurian/state`" -- a non-cure when the directory is intact and the
    pointer's field is what escaped.
    """
    registry = ProjectRegistry.default(project.parent / "datadir")
    _plant_a_readable_database_at(project, filename)

    with pytest.raises(SdkToolError) as raised:
        asyncio.run(_search(registry))

    message = str(raised.value)
    assert ".theurian/state/" in message, (
        f"the refusal describes the wrong boundary, which is false of a filename "
        f"that stays inside the working tree: {message}"
    )
    assert str(project) not in message, (
        f"the refusal carries the operator's absolute path to a client: {message}"
    )
    assert "Remove `.theurian/state`" not in message, (
        f"the refusal published the planted-link cure for a pointer whose own "
        f"field escaped, which removes derived state to fix a value: {message}"
    )
    assert "active.json" in message, f"the refusal names no artefact to act on: {message}"


@pytest.mark.parametrize("filename", _ESCAPING_FILENAMES)
def test_index_build_refuses_a_state_database_outside_the_state_directory(
    project: Path, filename: str
) -> None:
    """The CLI half of the same guard, which the MCP-side fix did not reach.

    ``index build`` joined the pointer's value itself. Measured at ``4dd322d6``
    with ``../../../outside.sqlite``: the command **read that file, built an
    index from it and published at exit 0** -- so a doctored pointer turned a
    build into a copy of knowledge the working tree does not hold.

    An absolute path in *this* refusal is correct and is not asserted against:
    the rule that keeps them out is the MCP one, and every sibling refusal on
    this surface prints one.
    """
    _plant_a_readable_database_at(project, filename)
    before = sorted(p.name for p in (project / ".theurian/state").glob("theurian-index-*.sqlite"))

    ran = _run("index", "build")

    assert ran.escaped is None, f"an exception reached the caller: {ran.escaped}"
    assert ran.exit_code != 0, (
        f"the build was published from outside the state directory: {ran.stdout}"
    )
    assert ran.envelope is not None, f"stderr held no document: {ran.stderr[:200]}"
    after = sorted(p.name for p in (project / ".theurian/state").glob("theurian-index-*.sqlite"))
    assert after == before, f"a refused build left an index behind: {before} -> {after}"


def test_the_history_check_refuses_a_state_database_outside_the_state_directory(
    project: Path,
) -> None:
    """The third consumer, on the path of every command `_require_project` routes.

    ``_verify_history`` joined the value too, and it runs before any command's
    own body. Driven with the state hash moved, because it returns early while
    the recorded hash and the derived one agree -- without that this row measures
    the early return rather than the join.
    """
    _move_the_state_hash(project)
    _plant_a_readable_database_at(project, "../../../outside.sqlite")

    ran = _run("migrate", "status")

    assert ran.escaped is None, f"an exception reached the caller: {ran.escaped}"
    assert ran.exit_code != 0, (
        f"the history check accepted a database outside the tree: {ran.stdout}"
    )
    assert ran.envelope is not None, f"stderr held no document: {ran.stderr[:200]}"


def _plant_a_readable_database_at(root: Path, filename: str) -> None:
    """Copy the project's own state database to the escaping path and point at it.

    A *readable* copy, so the guard under test is the only thing that can refuse:
    an absent target is refused by the missing-database arm and a corrupt one by
    the integrity guard, and either would make this row green without the
    containment check existing.
    """
    pointer = root / ".theurian/state/active.json"
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    honest = root / ".theurian/state" / payload["databaseFilename"]
    target = (root / ".theurian/state" / filename).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(honest, target)
    payload["databaseFilename"] = filename
    pointer.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("case", "filename"),
    [
        ("lone-surrogate", "\udcff.sqlite"),
        ("embedded-nul", "a\x00b.sqlite"),
        ("very-long", "B" * 200 + ".sqlite"),
    ],
)
def test_a_refusal_naming_a_derived_value_can_always_reach_the_wire(
    project: Path, case: str, filename: str
) -> None:
    """The refusal has to *serialise*, and a lone surrogate is where it did not.

    Round one, adversarial H-D. The missing-database arm interpolated
    ``databaseFilename`` verbatim, and ``json.dumps`` writes a lone surrogate as
    ``\\udcff`` while ``json.loads`` reads it straight back -- so a hand edit or a
    partially-decoded copy puts one in a pointer that parses. On the wire the
    encoder then dies: ``CallToolResult.model_dump_json()`` raises
    ``PydanticSerializationError`` ("surrogates not allowed"), the stream ends
    mid-message, and the client gets a 200 with an **empty body** -- no
    ``isError``, no text, no remedy. Strictly worse than the
    ``UnexpectedToolError`` the issue was filed for, because nothing on the wire
    says anything went wrong at all.

    Asserted by serialising the content the transport builds from the refusal,
    which is the exact step that raised; driving a real ASGI stream would assert
    the same thing through three more layers. The other two rows are the
    neighbours that already answered (NUL, and a length under the
    ``ENAMETOOLONG`` bound so it reaches *this* arm rather than the one above
    it) -- without them a fix that refused every unusual filename would read as
    green.
    """
    registry = ProjectRegistry.default(project.parent / "datadir")
    _publish_a_database_filename(project, filename)

    with pytest.raises(SdkToolError) as raised:
        asyncio.run(_search(registry))

    content = TextContent(type="text", text=str(raised.value))
    encoded = CallToolResult(content=[content], is_error=True).model_dump_json()

    assert "migrate apply" in encoded, f"{case}: the refusal lost its next action"
    assert len(str(raised.value)) < 400, (
        f"{case}: the refusal quotes the value back unbounded, so a pointer "
        f"holding a whole file becomes the reply: {len(str(raised.value))} chars"
    )


#: What a reply may not carry raw: a value this daemon read out of a file some
#: other process wrote. Two came from the pointer and the registry entry; the
#: third is the registry *key*, which round two measured as the one any client
#: reaches -- ask for an id that is not registered and the refusal lists it.
_VALUES_THE_DAEMON_DID_NOT_PRODUCE: Final = ("database_filename", "rootPath", "unreadable")

#: The sanitisers a value may reach a reply through. Two forms, one escaping:
#: the quoted one for message text, the bare one for a published field.
_SANITISERS: Final = ("_publishable", "_publishable_field")


def _unsanitised_publications(tree: ast.AST) -> list[str]:
    """Every place ``tree`` puts an untrusted value into a reply without a sanitiser.

    **Three shapes, because round two's three unconverted sites were three
    different ones** and a key seeing only the first called the class closed:

    * an f-string field, **whatever its conversion**. The first cut required
      ``conversion == -1``, which admits ``!s`` -- and ``!s`` *is* ``str()``, so
      the exclusion admitted the exact defect it was written to catch. ``!r``
      escapes, but it does not bound, and the 200,000-character registry key is
      what made that distinction matter.
    * a ``str.join`` over a comprehension or a name, which is how the two
      registry lists were built -- the value never appears in the f-string at
      all, only the joined result does.
    * a **dict literal** value, which is not message text and reaches the same
      wire encoder anyway: ``project.list``'s ``rootPath`` died there.

    The key is deliberately syntactic and therefore over-broad; a site that is
    genuinely safe is excluded by name in :data:`_PUBLISHED_RAW_ON_PURPOSE`
    rather than by narrowing the shape, so the exclusion is readable and
    attackable.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        for expression, where in _publication_sites(node):
            rendered = ast.unparse(expression)
            if not any(name in rendered for name in _VALUES_THE_DAEMON_DID_NOT_PRODUCE):
                continue
            if any(sanitiser in rendered for sanitiser in _SANITISERS):
                continue
            line = getattr(node, "lineno", 0)
            found.append(f"line {line}: {where} {rendered[:70]}")
    return found


def _publication_sites(node: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """The expressions ``node`` publishes, with what kind of site each one is.

    A conditional yields its two *branches* rather than itself: the whole
    ``IfExp`` renders with its condition in it, so ``"...text..." if unreadable
    else None`` -- a fixed remedy string chosen by a flag -- read as a
    publication of ``unreadable``. What reaches the reply is the branch.
    """
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                yield part.value, "f-string"
    elif isinstance(node, ast.Dict):
        for value in node.values:
            if value is not None:
                yield from _reaches_the_reply(value, "dict value")
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"join", "format"}
    ):
        for argument in node.args:
            yield from _reaches_the_reply(argument, f".{node.func.attr}()")


def _reaches_the_reply(value: ast.AST, where: str) -> Iterator[tuple[ast.AST, str]]:
    """``value``, or a conditional's branches, dropping plain constants."""
    if isinstance(value, ast.IfExp):
        yield from _reaches_the_reply(value.body, where)
        yield from _reaches_the_reply(value.orelse, where)
    elif not isinstance(value, ast.Constant):
        yield value, where


#: Sites the key reports that are safe for a reason, named rather than narrowed
#: out of the shape. Keyed on the rendered expression, so a site that changes has
#: to be re-classified instead of inheriting an exemption.
_PUBLISHED_RAW_ON_PURPOSE: Final = {
    "list(unreadable)": (
        "not a publication: it is the argument to `_registry_snapshot`'s own return, "
        "which every consumer then sanitises at its own surface"
    ),
}


def test_every_untrusted_value_a_reply_carries_goes_through_a_sanitiser() -> None:
    """The population for the wire-encoder class, read out of ``mcp/tools.py``'s AST.

    **The first cut of this key was tautological** (round two, adversarial H-2):
    it required ``conversion == -1`` and matched two hardcoded names -- the two
    already converted -- so it was a restatement of the fix rather than a check
    on it. Three unconverted sites reached the wire while it passed, and one of
    them is on the path any client takes by asking for an unregistered id.

    What the key asks now is where an untrusted value can *reach a reply*, in
    the three shapes those sites used, with the conversion ignored because
    ``!s`` is ``str()``. :func:`test_the_publication_key_reports_a_planted_site`
    is its vacuity control -- this assertion is a "not found", which a key
    matching nothing satisfies perfectly.
    """
    tree = ast.parse((_SOURCE_ROOT / "mcp" / "tools.py").read_text(encoding="utf-8"))

    unsanitised = [
        site
        for site in _unsanitised_publications(tree)
        if not any(excluded in site for excluded in _PUBLISHED_RAW_ON_PURPOSE)
    ]

    assert not unsanitised, (
        "a reply carries a value this daemon did not produce without routing it "
        f"through `_publishable`: {unsanitised}"
    )


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("f-string-no-conversion", 'msg = f"names {active.database_filename}"'),
        ("f-string-str-conversion", 'msg = f"names {active.database_filename!s}"'),
        ("f-string-repr-conversion", 'msg = f"names {active.database_filename!r}"'),
        ("join-over-a-comprehension", 'msg = ", ".join(name for name in unreadable)'),
        ("dict-literal-value", 'payload = {"rootPath": entry["rootPath"]}'),
        ("format-call", 'msg = "names {}".format(active.database_filename)'),
    ],
)
def test_the_publication_key_reports_a_planted_site(shape: str, source: str) -> None:
    """The vacuity control: the key must *hit* each shape it claims to cover.

    Every assertion in the sweep above is a "nothing found", which a key that
    matches nothing satisfies perfectly -- and the first cut of this key very
    nearly was that: it saw one of these six. Each is planted and required to be
    reported, ``!r`` included, because escaping is not bounding and the
    200,000-character key is what proved the difference.
    """
    reported = _unsanitised_publications(ast.parse(textwrap.dedent(source)))

    assert reported, f"the key does not see a `{shape}` publication at all: {source}"


def test_the_publication_key_accepts_a_sanitised_site() -> None:
    """The other half: a key reporting everything would satisfy the control above.

    Both sanitiser spellings, because the field form exists precisely so a
    published *field* is not quoted -- and a key that only knew the message form
    would push every field through the wrong one.
    """
    sanitised = ast.parse(
        textwrap.dedent(
            """
msg = f"names {_publishable(active.database_filename)}"
payload = {"rootPath": _publishable_field(entry["rootPath"])}
joined = ", ".join(_publishable(name) for name in unreadable)
"""
        )
    )

    assert not _unsanitised_publications(sanitised), (
        "the key reports a site that is already sanitised, so its findings say "
        "nothing about whether a value is protected"
    )


def _the_state_path_is_too_long(root: Path) -> bool:
    """Whether the OS refuses to stat this root's state database *by name*.

    The stat is what decides, so the stat is what is asked -- a length
    comparison here would be this file guessing at a platform constant. Nothing
    needs to exist: a name past the limit is refused before the lookup, which is
    what lets the search below run without touching the filesystem.
    """
    probe = root / ".theurian/state/theurian-state-000000000000.sqlite"
    try:
        probe.exists()
    except OSError as exc:
        return exc.errno == errno.ENAMETOOLONG
    return False


#: Long enough to reach the path limit in few components, short enough to stay
#: under ``NAME_MAX`` (255): a single component sized to the whole shortfall is
#: refused by the *name* limit rather than the path one, which is a different
#: platform bound and not the one under test.
_COMPONENT_CHARS: Final = 200


def _a_root_whose_state_path_is_just_too_long(base: Path) -> Path | None:
    """The *shortest* root under ``base`` whose state stat is refused, or ``None``.

    Shortest, because the window is narrow and one component too many closes it:
    ``git init`` creates its own tree under the root, so a depth chosen by
    stepping in fixed-size components overshoots what the checkout itself
    accepts -- measured, a 40-character step lands past it every time. Grown one
    character at a time within a bounded number of ``NAME_MAX``-safe components,
    and searched on the *name* rather than on disk: a path past the limit is
    refused before any lookup, so nothing here has to be created to find the
    boundary.
    """
    root = base
    for _ in range(16):
        for extra in range(1, _COMPONENT_CHARS + 1):
            candidate = root / ("d" * extra)
            if _the_state_path_is_too_long(candidate):
                return candidate
        root = root / ("d" * _COMPONENT_CHARS)
    return None


def test_the_state_probes_own_failure_is_published_under_its_own_keys(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Round one, code review M-3 / adversarial M-2: the limb's text reached no payload.

    ``_state_database_is_built``'s message and remedy were folded into the
    pointer's ``reason``/``remedy`` pair, published only when the pointer read
    had *not* failed. Under the mode plant the pointer always fails first, so the
    probe's own strings were produced by nothing the suite ran and two mutations
    rewriting them survived; the plant that was supposed to drive them was
    satisfied by the pointer's text instead.

    The limb is reachable without a mode at all, which is what this recipe is: a
    checkout deep enough that the state database's full path passes ``PATH_MAX``
    while every component stays under ``NAME_MAX``, with the state directory
    perfectly readable and no pointer written yet. ``ENAMETOOLONG`` and
    ``EACCES`` also take **different cures** -- `chmod` fixes nothing here -- so
    the remedy is asserted for the errno that actually stopped the probe.
    """
    # Grown until the *database* path is the one the OS refuses, and no further:
    # the limit is the platform's, not a constant this file may assume, and one
    # component too many puts `git init` itself past it. Measured rather than
    # computed, in the smallest step that lands inside the window (macOS 26.6
    # accepts the checkout and refuses the stat at a root of about 970).
    found = _a_root_whose_state_path_is_just_too_long(tmp_path_factory.mktemp("deep"))
    if found is None:  # pragma: no cover - a platform with no such limit
        pytest.skip("this platform accepts a state path no single component here can exceed")
    root = found
    root.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("THEURIAN_DATA_DIR", str(root / "datadir"))
        patch.setenv("HOME", str(root / "home"))
        patch.chdir(root)
        assert _run("init").exit_code == 0
        assert _run("project", "register").exit_code == 0
        ran = _run("project", "status")

    payload = ran.payload
    assert payload is not None, f"exit {ran.exit_code}, stderr: {ran.stderr}"
    assert payload["stateBuilt"] is None, (
        f"the probe answered a question the OS refused: {payload['stateBuilt']!r}"
    )
    assert payload.get("reason") is None, (
        "the pointer read succeeded here, so a `reason` means the probe borrowed "
        "the pointer's keys again -- which is what kept its own text unpublished"
    )
    assert "could not be established" in payload["stateBuiltReason"], (
        f"the probe's own message was not published: {payload.get('stateBuiltReason')!r}"
    )
    assert "shorter path" in payload["stateBuiltRemedy"], (
        f"the cure names something other than the length that actually stopped it: "
        f"{payload.get('stateBuiltRemedy')!r}"
    )
    assert "chmod" not in payload["stateBuiltRemedy"], (
        "the length failure took the mode failure's cure, which fixes nothing here"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_doctor_reports_rather_than_raises_over_an_unreadable_state_directory(
    project: Path,
) -> None:
    """Round one, adversarial M-8: `doctor` was the eighth red command and pinned by nothing.

    It is outside ``CLI_SWEEP`` -- it exits non-zero on a healthy corpus, because
    the fixture installs no Claude Code -- so the nine-command sweep above could
    not see it. It raised through ``index_secret_scan._loaded_mapping``'s probe,
    which this branch moved inside its ``try`` for the pointer readers' reason;
    without a row here that fix is incidental rather than driven.

    ``--port 7420`` for the reason every dev-time invocation takes it: 7419 is
    where a resident daemon lives, and a probe that reached one would be
    describing something other than this fixture.
    """
    state = project / ".theurian/state"
    state.chmod(0o000)
    try:
        with pytest.raises(OSError, match="Permission denied"):
            (state / "active.json").read_text(encoding="utf-8")

        ran = _run("doctor", "--port", "7420")
    finally:
        state.chmod(0o700)

    assert ran.escaped is None, (
        f"doctor let an exception reach the caller instead of a report: {ran.escaped}"
    )
    assert ran.payload is not None, (
        f"doctor published no report at exit {ran.exit_code}: {ran.stderr[:200]}"
    )


# -- The population, read out of the source ----------------------------------

_SOURCE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "theurian"

#: What a caller must catch at the probe: the refusal ``index_for`` raises, and
#: the ``OSError`` it cannot convert because ``resolve()`` never stats.
_BOTH_FAMILIES: Final = frozenset({"TheurianError", "OSError"})

#: Every way this codebase asks the filesystem about a path. The key below wants
#: the *stat*, not the path construction: a function that builds a path and hands
#: it on has nothing to grade, and one that asks about it does.
_ASKS_THE_FILESYSTEM: Final = frozenset(
    {
        "exists",
        "glob",
        "is_dir",
        "is_file",
        "iterdir",
        "lstat",
        "open",
        "read_bytes",
        "read_text",
        "stat",
        "touch",
    }
)

#: The two spellings of a path join. ``joinpath`` was outside the first cut of
#: key 2 and is a one-character-cheaper evasion of it (round one, MEDIUM-1).
_JOIN_SPELLINGS: Final = frozenset({"joinpath"})


def _own_statements(node: ast.AST) -> Iterator[ast.AST]:
    """Every node inside ``node``, **not** descending into a nested definition.

    ``ast.walk`` descends into everything, which grades an outer function by an
    inner one's ``try``: ``mcp/tools.py``'s ``register`` scored as guarded
    because ``_resolve``, defined inside it, carries the handler (round one,
    MEDIUM-1). A nested function is its own unit of grading, so the walk stops
    at its ``def``.
    """
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        for child in ast.iter_child_nodes(current):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            yield child
            stack.append(child)


def _functions(tree: ast.AST, module: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``tree``, keyed ``<module>::<name>``, nested ones included.

    Nested definitions are *members* of the population -- ``_resolve`` is the
    site that matters in ``mcp/tools.py`` -- while :func:`_own_statements` keeps
    each one's grading to its own body. Those are two different questions and
    conflating them is what MEDIUM-1 caught.
    """
    return {
        f"{module}::{node.name}": node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _self_and_own(node: ast.AST) -> Iterator[ast.AST]:
    """``node`` itself, then everything under it that is not a nested definition.

    ``_own_statements`` yields only descendants, and a key handed an *expression*
    -- the ``BinOp`` of a join, say -- would then never test the node it was
    given. That is a silent always-false, so the two are separate names.
    """
    yield node
    yield from _own_statements(node)


def _calls_named(node: ast.AST, names: frozenset[str] | set[str]) -> bool:
    """Whether ``node``'s own body calls any of ``names`` as a method or a function."""
    return any(
        isinstance(child, ast.Call)
        and (
            (isinstance(child.func, ast.Attribute) and child.func.attr in names)
            or (isinstance(child.func, ast.Name) and child.func.id in names)
        )
        for child in _self_and_own(node)
    )


def _handlers_over(node: ast.AST, guarded: Callable[[ast.AST], bool]) -> frozenset[str]:
    """The exception names caught by every ``try`` whose body satisfies ``guarded``.

    ``try`` blocks are found in ``node``'s own body only, and each one's *body*
    is what ``guarded`` is asked about -- not the handlers, not the ``else``. The
    defect this file sweeps is a probe sitting one line above the handler written
    for it, so a key that accepted "somewhere in this function there is a
    ``try``" could not see its own subject.
    """
    caught: set[str] = set()
    for child in _own_statements(node):
        if not isinstance(child, ast.Try):
            continue
        if not any(guarded(statement) for statement in child.body):
            continue
        for handler in child.handlers:
            if handler.type is None:
                caught.add("BareExcept")
                continue
            listed = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            caught |= {name.id for name in listed if isinstance(name, ast.Name)}
    return frozenset(caught)


#: Every function that reaches ``index_for`` and whose stat is graded by
#: something other than an ``except`` naming both families, with the reason.
#: Keyed by ``<module>::<qualname>``, so a move is a failure and not a pass.
_GRADED_ELSEWHERE: Final = {
    "application/withdrawal_purge.py::publish_purge_for_withdrawal": (
        "fail-closed: both calls sit inside this function's own `except Exception`, "
        "which reports an unpublished purge rather than raising"
    ),
    "cli/index_commands.py::index_build": (
        "the id is a ULID this command minted a few lines earlier, so it reaches no "
        "derived file and carries no attacker-chosen bytes"
    ),
}


def _index_for_callers(
    tree: ast.AST, module: str
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``tree`` that calls ``index_for`` in its own body."""
    return {
        position: node
        for position, node in _functions(tree, module).items()
        if _calls_named(node, {"index_for"})
    }


def _index_for_targets(body: list[ast.stmt]) -> frozenset[str]:
    """The local names bound to what ``index_for`` returned, inside ``body``."""
    targets: set[str] = set()
    for statement in body:
        if not isinstance(statement, ast.Assign) or not _calls_named(
            statement.value, {"index_for"}
        ):
            continue
        targets |= {name.id for name in statement.targets if isinstance(name, ast.Name)}
    return frozenset(targets)


def _stats_that_target(body: list[ast.stmt], targets: frozenset[str]) -> bool:
    """Whether ``body`` stats the path ``index_for`` returned, rather than some other one.

    Round two: the first cut asked only that *a* stat shared the ``try`` body,
    so a function that resolved the build id and then stat-ed an unrelated path
    inside the handler read as guarded while the real probe sat below it. The
    receiver has to be the bound name -- or the ``index_for`` call itself, which
    is the chained spelling ``paths.index_for(id).is_file()``.
    """
    for statement in body:
        for call in ast.walk(statement):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in _ASKS_THE_FILESYSTEM:
                continue
            receiver = call.func.value
            if isinstance(receiver, ast.Name) and receiver.id in targets:
                return True
            if _calls_named(receiver, {"index_for"}):
                return True
    return False


def _grades_the_index_stat(node: ast.AST) -> frozenset[str]:
    """What guards the ``index_for`` call **and** the stat of what it returned.

    Both in one ``try`` body, and the stat aimed at the *same path*. Round one's
    MEDIUM-1 moved the first half here -- the original key asked only that the
    ``index_for`` call was inside a ``try``, so moving the ``is_file()`` one line
    below the handler passed while reopening the defect. Round two moved the
    second: a stat on a **different** path in that body satisfied the widened key
    just as well, which is the same "an exit code is not membership" mistake one
    level down.
    """
    caught: set[str] = set()
    for child in _own_statements(node):
        if not isinstance(child, ast.Try):
            continue
        if not any(_calls_named(statement, {"index_for"}) for statement in child.body):
            continue
        targets = _index_for_targets(child.body)
        if not _stats_that_target(child.body, targets):
            continue
        for handler in child.handlers:
            if handler.type is None:
                caught.add("BareExcept")
                continue
            listed = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            caught |= {name.id for name in listed if isinstance(name, ast.Name)}
    return frozenset(caught)


#: The functions allowed to join ``database_filename`` onto a path, and why.
#:
#: **The key changed shape in round two, because the fix did.** It used to ask
#: whether each joining function graded the stat that followed; every consumer
#: now goes through :meth:`ProjectPaths.state_database_named` instead, so the
#: property worth holding is stronger and simpler: *a bare join is the defect*.
#: Two joins remain and neither takes a pointer's value.
#: **The containment helper is not in this set and is not visible to the key**,
#: which is a bound rather than an omission: it takes the filename as a bare
#: parameter, so its own join reads ``state / database_filename`` -- a ``Name``,
#: not the ``<pointer>.database_filename`` attribute this key matches. What holds
#: the helper is its own escaping rows below and the two containment sweeps that
#: reflect over ``ProjectPaths``; what this key holds is that no *consumer* goes
#: round it.
_MAY_JOIN_THE_FILENAME: Final = {
    "application/project_service.py::database_for": (
        "a different `database_filename`: `StateHash`'s, computed from the migration "
        "set rather than read from a pointer. The guard on *that* one is upstream and "
        "is not this class's: `StateHash` is built from a 64-hex `ContentHash`, so the "
        "filename cannot carry an attacker's bytes at all -- provenance says nothing "
        "about it"
    ),
}


def _joins_database_filename(node: ast.AST) -> bool:
    """Whether ``node``'s own body builds a path out of ``.database_filename``.

    Both spellings: ``base / value.database_filename`` and
    ``base.joinpath(value.database_filename)``. The second was outside the first
    cut of this key and is the cheaper evasion of the two (round one, MEDIUM-1).
    """
    for child in _self_and_own(node):
        if (
            isinstance(child, ast.BinOp)
            and isinstance(child.op, ast.Div)
            and isinstance(child.right, ast.Attribute)
            and child.right.attr == "database_filename"
        ):
            return True
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr in _JOIN_SPELLINGS
            and any(
                isinstance(arg, ast.Attribute) and arg.attr == "database_filename"
                for arg in child.args
            )
        ):
            return True
    return False


def _join_targets(node: ast.AST) -> frozenset[str]:
    """The local names bound to a path built from ``.database_filename``.

    Tracking the *target* is what makes the stat requirement mean something: the
    first cut asked only that some ``exists()`` in the function was inside a
    ``try``, so a function that joined the value, bound it, and stat-ed a
    *different* path inside a handler read as graded (round one, MEDIUM-1).
    """
    targets: set[str] = set()
    for child in _own_statements(node):
        if not isinstance(child, ast.Assign):
            continue
        if not _joins_database_filename(child.value):
            continue
        targets |= {t.id for t in child.targets if isinstance(t, ast.Name)}
    return frozenset(targets)


def _grades_the_join_stat(node: ast.AST) -> frozenset[str]:
    """What guards a stat **on the joined path itself**, not on some other path."""
    targets = _join_targets(node)
    if not targets:
        return frozenset()

    def guarded(statement: ast.AST) -> bool:
        return any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in _ASKS_THE_FILESYSTEM
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in targets
            for call in ast.walk(statement)
        )

    return _handlers_over(node, guarded)


def _swept(
    key: Callable[[ast.AST, str], dict[str, ast.FunctionDef | ast.AsyncFunctionDef]],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Run one population key over every module under ``src``."""
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found |= key(tree, str(path.relative_to(_SOURCE_ROOT)))
    return found


def _join_sites(tree: ast.AST, module: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        position: node
        for position, node in _functions(tree, module).items()
        if _joins_database_filename(node)
    }


def test_no_consumer_joins_the_pointers_database_filename_onto_a_path() -> None:
    """The closure argument for the pointer's other value, after round two moved it.

    Round one asked whether each joining function graded the stat that followed,
    which was the right question while three consumers joined the value
    themselves. Round two measured what that left open: containment lived only on
    the MCP side, so ``index build`` with ``databaseFilename`` set to
    ``../../../outside.sqlite`` **read that file, built from it and published at
    exit 0** (measured 2026-09-06), and `_verify_history` -- on the path of every
    command `_require_project` routes -- joined it too.

    So the key is now membership rather than grading: every consumer goes through
    :meth:`ProjectPaths.state_database_named`, and a bare join is the defect
    whatever it does afterwards. Reproduce the population with ``git grep -n
    'database_filename' -- packages/theurian-core/src``; the two functions in
    :data:`_MAY_JOIN_THE_FILENAME` are the only ones that may build the path, and
    only one of them takes a pointer's value.

    The key demonstrably hits: it is what reported ``index_build`` leaving the
    population when that call was converted, and
    :func:`test_the_population_keys_catch_what_defeated_their_first_cut` runs its
    evasion shapes.
    """
    joins = _swept(_join_sites)
    assert joins, "the AST key found no `database_filename` join at all"

    stale = frozenset(_MAY_JOIN_THE_FILENAME) - frozenset(joins)
    assert not stale, f"an allowance names a join that no longer exists: {sorted(stale)}"

    bare = sorted(frozenset(joins) - frozenset(_MAY_JOIN_THE_FILENAME))
    assert not bare, (
        "a consumer builds a path out of the pointer's `databaseFilename` instead "
        f"of asking `state_database_named` for it, so nothing contains it: {bare}"
    )


def test_every_index_for_caller_grades_the_stat_beside_the_call() -> None:
    """The closure argument for the value axis, derived rather than asserted.

    ``index_for`` hands back a path whose *stat* can still raise, so the function
    that stats it owes both families a handler. Reproduce the population with
    ``git grep -n 'index_for(' -- packages/theurian-core/src``, which returned
    seven lines on 2026-09-05 -- the definition and six call sites, sitting in
    five functions because ``withdrawal_purge`` holds two of them:
    ``publish_purge_for_withdrawal``, ``index_build``,
    ``_the_published_build_is_on_disk``, ``index_schema_version`` and
    ``_searchable_file``.

    The key requires the ``index_for`` call and the stat in the **same** ``try``
    body, which round one's MEDIUM-1 is: the first cut asked only about the call,
    so moving the ``is_file()`` below the handler passed while reopening the
    defect. :func:`test_the_population_keys_catch_what_defeated_their_first_cut`
    plants exactly that.
    """
    callers = _swept(_index_for_callers)
    assert callers, "the AST key found no `index_for` caller at all, so this asserts nothing"

    stale = frozenset(_GRADED_ELSEWHERE) - frozenset(callers)
    assert not stale, f"an exclusion names a caller that no longer exists: {sorted(stale)}"

    ungraded = sorted(
        position
        for position, node in callers.items()
        if position not in _GRADED_ELSEWHERE
        and not (guarding := _grades_the_index_stat(node)) >= _BOTH_FAMILIES
        and not {"Exception", "BaseException", "BareExcept"} & guarding
    )
    assert not ungraded, (
        "a caller of `index_for` stats what it hands back without grading both "
        f"`TheurianError` and `OSError`, and is not excluded with a reason: {ungraded}"
    )


#: The four evasions round one measured against the first cut of these keys, as
#: source a key can be run over. Each one is code the key **must** report; a key
#: that passes any of them is a key whose green means nothing, which is what
#: "zero only counts with a positive control" is about.
_EVASIONS: Final = {
    "stat-below-the-handler": (
        _grades_the_index_stat,
        """
def gc(paths, published):
    try:
        path = paths.index_for(published)
    except (TheurianError, OSError):
        return None
    return path.is_file()
""",
    ),
    "graded-by-a-nested-def": (
        _grades_the_index_stat,
        """
def register(paths, published):
    def resolve():
        try:
            return paths.index_for(published).is_file()
        except (TheurianError, OSError):
            return None

    return paths.index_for(published).is_file()
""",
    ),
    "joined-with-joinpath": (
        _grades_the_join_stat,
        """
def resolve(paths, active):
    database = paths.state.joinpath(active.database_filename)
    return database.exists()
""",
    ),
    "index-stat-on-a-different-path": (
        _grades_the_index_stat,
        """
def gc(paths, published, other):
    try:
        path = paths.index_for(published)
        other.is_file()
    except (TheurianError, OSError):
        return None
    return path.is_file()
""",
    ),
    "stat-on-a-different-path": (
        _grades_the_join_stat,
        """
def resolve(paths, active, other):
    database = paths.state / active.database_filename
    try:
        other.exists()
    except OSError:
        return None
    return database.exists()
""",
    ),
}


@pytest.mark.parametrize("evasion", sorted(_EVASIONS), ids=sorted(_EVASIONS))
def test_the_population_keys_catch_what_defeated_their_first_cut(evasion: str) -> None:
    """The vacuity control for both keys, planted rather than argued.

    Each snippet is a shape that passed the first cut of one of the keys while
    leaving the defect open -- measured by round one, not imagined here. What is
    asserted is that the key no longer reports the guard it would need: the
    function is *in* the population and its grading comes back short, which is
    what makes the sweep above report it by name.

    Both keys are exercised, so strengthening one and not the other cannot read
    as green.
    """
    grades, source = _EVASIONS[evasion]
    tree = ast.parse(textwrap.dedent(source))
    body = next(node for node in tree.body if isinstance(node, ast.FunctionDef))

    # Membership first, because two of the four evaded by not being *in* the
    # population: a `joinpath` join was outside key 2's shape entirely, so its
    # grading was never asked. A key that cannot see a shape reports a smaller
    # set than the sentence beside it claims, and its green is about that set.
    population = _join_sites if grades is _grades_the_join_stat else _index_for_callers
    assert population(tree, "planted.py"), (
        f"`{evasion}` is not in the population at all, so the sweep would never "
        f"reach it -- the shape is invisible rather than graded"
    )

    guarding = grades(body)

    assert not guarding >= _BOTH_FAMILIES and "OSError" not in guarding, (
        f"the key still reads `{evasion}` as guarded, so its green says nothing "
        f"about the shape it was written to catch: caught {sorted(guarding)}"
    )


def test_the_evasion_controls_pass_the_shape_they_are_a_control_for() -> None:
    """The other half of the control: the keys must still accept correct code.

    A key that reported *everything* would satisfy every assertion above while
    being useless, so the two guarded spellings this codebase actually uses are
    run through the same functions and must come back guarded.
    """
    correct_index = ast.parse(
        textwrap.dedent("""
def on_disk(paths, published):
    try:
        names_a_file = paths.index_for(published).is_file()
    except TheurianError:
        return False
    except OSError:
        return False
    return names_a_file
""")
    ).body[0]
    correct_join = ast.parse(
        textwrap.dedent("""
def resolve(paths, active):
    database = paths.state / active.database_filename
    try:
        return database.exists()
    except OSError:
        return None
""")
    ).body[0]

    assert _grades_the_index_stat(correct_index) >= _BOTH_FAMILIES
    assert "OSError" in _grades_the_join_stat(correct_join)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_pointer_refusal_names_the_file_relative_to_the_project(project: Path) -> None:
    """Round two, adversarial: the GHSA-97q9 fix on this message was unpinned.

    `read_active_state`'s refusal reaches MCP clients verbatim -- `_resolve`
    publishes it through `_with_remedy` -- and both halves of it used to carry
    the operator's absolute path: the pointer directly, and the OS cause, because
    an `OSError`'s ``str`` appends the filename its ``strerror`` leaves out. Both
    were made project-relative, and a mutation putting either back survived,
    because nothing asserted the property.

    Driven at the MCP surface rather than at the function, because that is where
    the rule applies: the CLI prints absolute paths on purpose.
    """
    registry = ProjectRegistry.default(project.parent / "datadir")
    state = project / ".theurian/state"
    state.chmod(0o000)
    try:
        with pytest.raises(OSError, match="Permission denied"):
            (state / "active.json").read_text(encoding="utf-8")

        with pytest.raises(SdkToolError) as raised:
            asyncio.run(_search(registry))
    finally:
        state.chmod(0o700)

    message = str(raised.value)
    assert str(project) not in message, (
        f"the refusal carries the operator's absolute path to a client: {message}"
    )
    assert ".theurian/state/active.json" in message, (
        f"the refusal names no file at all, so relative-ness was bought by saying "
        f"nothing: {message}"
    )
    assert "Permission denied" in message, (
        f"the refusal dropped the OS's own reason along with the path it appended: {message}"
    )
