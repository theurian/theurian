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
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
import typer.main
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import INDEX_POINTER_REMEDY, ProjectRegistry
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


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A registered project with canonical state applied and one index published.

    ``HOME`` and ``THEURIAN_DATA_DIR`` go through ``monkeypatch`` along with the
    ``chdir``: the CLI resolves a project from the working directory, so a test
    that forgot either would run against the developer's own checkout.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)

    assert _run("init").exit_code == 0
    (root / ".theurian/knowledge/architecture/auth.md").write_text(BODY, encoding="utf-8")
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(
        MIGRATION, encoding="utf-8"
    )
    for command in (["project", "register"], ["migrate", "apply"], ["index", "build"]):
        ran = _run(*command)
        assert ran.exit_code == 0, f"{command}: {ran.stdout}{ran.stderr}"
    yield root


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
    assert "chmod" in payload.get("remedy", ""), (
        f"the cure must name the act that clears a mode failure, not the pointer "
        f"deletion that cannot be carried out through it: {payload.get('remedy')!r}"
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
    project: Path, tmp_path: Path
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
    registry = ProjectRegistry.default(tmp_path / "datadir")
    _publish_a_database_filename(project, "B" * 260 + ".sqlite")

    with pytest.raises(SdkToolError) as raised:
        asyncio.run(_search(registry))

    message = str(raised.value)
    assert "migrate apply" in message, f"the refusal names no next action: {message}"
    assert "active.json" in message, f"the refusal names no artefact to act on: {message}"
    assert str(project) not in message, (
        f"the refusal carries the operator's absolute path to a client: {message}"
    )


# -- The population, read out of the source ----------------------------------

_SOURCE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "theurian"

#: What a caller must catch at the probe: the refusal ``index_for`` raises, and
#: the ``OSError`` it cannot convert because ``resolve()`` never stats.
_BOTH_FAMILIES: Final = frozenset({"TheurianError", "OSError"})

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


def _index_for_callers() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``src`` that calls ``index_for``, keyed by position.

    Read out of the source rather than listed, so a call site added later joins
    this key by failing the test below instead of sitting silently outside it.
    """
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            calls = {
                child.func.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
            }
            if "index_for" in calls:
                found[f"{path.relative_to(_SOURCE_ROOT)}::{node.name}"] = node
    return found


def _names_a_call_to(node: ast.AST, method: str) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == method
        for child in ast.walk(node)
    )


def _caught_around(node: ast.AST, method: str) -> frozenset[str]:
    """The exception names guarding a call to ``method``, flattened.

    Only the handlers of a ``try`` whose **body** holds that call, and that
    narrowness is the point. Collecting every ``except`` in the function instead
    would pass a function whose handler sits somewhere else entirely -- which is
    the exact defect being swept: a probe one line above the ``try`` written for
    it. Both faces of #389 had that shape, so a key that could not see it would
    have called them closed.
    """
    caught: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Try):
            continue
        if not any(_names_a_call_to(statement, method) for statement in child.body):
            continue
        for handler in child.handlers:
            if handler.type is None:
                continue
            listed = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
            caught |= {name.id for name in listed if isinstance(name, ast.Name)}
    return frozenset(caught)


#: Every function that joins ``database_filename`` onto a path and does not
#: grade an ``OSError`` itself, with the reason it does not have to.
_JOIN_GRADED_ELSEWHERE: Final = {
    "cli/index_commands.py::index_build": (
        "the join is handed to `IndexRequest` and never stat-ed here; the read that "
        "does touch it is `_run_build`'s, whose "
        "`except (TheurianError, sqlite3.Error, OSError)` converts it"
    ),
    "application/project_service.py::database_for": (
        "a different `database_filename`: `StateHash`'s, computed from the migration "
        "set rather than read from a pointer, so it carries no attacker-chosen bytes "
        "-- and this helper returns the path without stat-ing it, so each caller's "
        "own probe is where the mode failure is graded"
    ),
}


def _database_filename_joins() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``src`` that builds a path out of ``database_filename``.

    The second key, and it is a *different* one from the ``index_for`` sweep
    rather than a widening of it: this value never passes through a
    ``ProjectPaths`` helper at all -- the three call sites join it onto
    ``paths.state`` directly -- so no reflection over that class can see them and
    a sweep keyed on the helper would have called the class closed with this
    third of it live.

    The key is the *attribute name*, so it catches a fourth function whose
    ``database_filename`` is a different one: ``StateHash``'s, computed from the
    migration set. That is not a narrowing to add -- a key that told the two
    apart would be keying on what it is trying to prove -- so it is recorded in
    :data:`_JOIN_GRADED_ELSEWHERE` with the reason, where a reader can attack it.
    """
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            joins = any(
                isinstance(child, ast.BinOp)
                and isinstance(child.op, ast.Div)
                and isinstance(child.right, ast.Attribute)
                and child.right.attr == "database_filename"
                for child in ast.walk(node)
            )
            if joins:
                found[f"{path.relative_to(_SOURCE_ROOT)}::{node.name}"] = node
    return found


def test_every_database_filename_join_grades_the_stat_that_follows_it() -> None:
    """The closure argument for the pointer's other value, derived the same way.

    ``ActiveState.from_json`` only ``str()``s ``databaseFilename``, and
    ``verify_state_provenance`` binds ``(root, state_hash)`` rather than the
    filename, so nothing between the pointer and these joins has read what it
    says. Reproduce the population with ``git grep -n 'active.database_filename'
    -- packages/theurian-core/src``: four lines on 2026-09-05, three joins and
    one message.

    The key demonstrably hits: it is what found ``_verify_history`` after the
    other two joins had already been converted, which is the "a guard covers the
    whole set, never a convenient subset" failure caught before it shipped.
    """
    joins = _database_filename_joins()
    assert joins, "the AST key found no `database_filename` join at all"

    stale = frozenset(_JOIN_GRADED_ELSEWHERE) - frozenset(joins)
    assert not stale, f"an exclusion names a join that no longer exists: {sorted(stale)}"

    ungraded = sorted(
        position
        for position, node in joins.items()
        if position not in _JOIN_GRADED_ELSEWHERE
        and "OSError" not in _caught_around(node, "exists")
    )
    assert not ungraded, (
        "a function joins `databaseFilename` onto a path and does not grade the "
        f"`OSError` the stat that follows can raise: {ungraded}"
    )


def test_every_index_for_caller_grades_the_stat_beside_the_call() -> None:
    """The closure argument for the value axis, derived rather than asserted.

    ``index_for`` hands back a path whose *stat* can still raise, so the function
    that stats it owes both families a handler. The population is read out of the
    source here; reproduce it with ``git grep -n 'index_for(' --
    packages/theurian-core/src``, which returned seven lines on 2026-09-05 -- the
    definition and six call sites, sitting in five functions because
    ``withdrawal_purge`` holds two of them: ``publish_purge_for_withdrawal``,
    ``index_build``, ``_the_published_build_is_on_disk``,
    ``index_schema_version`` and ``_searchable_file``.

    The key demonstrably hits: dropping ``OSError`` from
    ``_the_published_build_is_on_disk``'s handlers fails this by name, which is
    how the exclusions below were checked rather than assumed.
    """
    callers = _index_for_callers()
    assert callers, "the AST key found no `index_for` caller at all, so this asserts nothing"

    stale = frozenset(_GRADED_ELSEWHERE) - frozenset(callers)
    assert not stale, f"an exclusion names a caller that no longer exists: {sorted(stale)}"

    ungraded = sorted(
        position
        for position, node in callers.items()
        if position not in _GRADED_ELSEWHERE
        and not (guarding := _caught_around(node, "index_for")) >= _BOTH_FAMILIES
        and "Exception" not in guarding
    )
    assert not ungraded, (
        "a caller of `index_for` stats what it hands back without grading both "
        f"`TheurianError` and `OSError`, and is not excluded with a reason: {ungraded}"
    )
