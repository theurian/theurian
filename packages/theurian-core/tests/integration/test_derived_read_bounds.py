"""No read of a derived-state path waits on what somebody planted there (#586).

The remaining members of #526's class, box-split from #585 once that PR closed
the state-database open, the write-lock lifecycle and the findings serve path:

* the two **pointer** reads under ``.theurian/state/`` -- ``active.json``, which
  sits on every project-resolving command's path, and ``active-index.json``
  beside it;
* the **index** database opener, which every shipped caller guarded by probing
  ``is_file()`` first -- a maintained list, not a structure;
* the **token** at ``<data_dir>/auth/mcp-token``, whose no-follow openers carried
  ``O_NOFOLLOW`` and no ``O_NONBLOCK``.

**The bound is a subprocess, not ``SIGALRM``.** The suite's ``hang_guard`` turns
a blocked call into a failing test by raising from a signal handler, and that
does not reach an open SQLite retries (``test_state_database_faults.py`` records
the measurement: the timer fired at 3 s and the process was still inside
``__open`` at 150 s). Every probe that could meet an unbounded read therefore
runs in a child this module kills, and the kill is what makes a regression a red
test rather than a stalled suite.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.integration

#: ``os.mkfifo`` is POSIX-only, and a named pipe is the artefact this whole
#: module is about: it is the one shape a test can plant without a device node.
_CAN_MAKE_A_NAMED_PIPE: Final = hasattr(os, "mkfifo")

#: A `chflags` cannot refuse root, and Windows has no BSD file flags at all, so
#: a flag-based plant would report a *successful* rotation as a passing refusal.
#: The suite's standing idiom; offline CI runs as root.
_CANNOT_BE_REFUSED_BY_A_MODE: Final = sys.platform == "win32" or os.geteuid() == 0

#: How long a child gets before it is killed and the test fails. Generous next to
#: a command that reads a small JSON file and short next to a CI job.
_CHILD_TIMEOUT_SECONDS: Final = 60.0

runner = CliRunner()

MIGRATION_ID: Final = "01K1BBBBBB01234567890ABCDE"
REVISION_ID: Final = "01K1BBBREV01234567890ABCDE"
BODY: Final = "# Bounds\n\nEvery derived-state read is bounded.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-09-06T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.bounds
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.bounds
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/bounds.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Bounds
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/bounds.md
"""

#: The child program: run one CLI invocation and report what it published.
#:
#: A child rather than ``runner.invoke``, so a read that fails to bound itself is
#: killed by the parent instead of stalling the run. ``CliRunner`` rather than the
#: ``theurian`` console script, so the test does not depend on the package being
#: installed on ``PATH`` -- a dependency that turns a real failure into a silent
#: skip. ``SystemExit`` is how Typer *reports* an exit code, so it is not read as
#: an escape.
_CHILD: Final = (
    "import json, sys\n"
    "from typer.testing import CliRunner\n"
    "from theurian.cli.main import app\n"
    "result = CliRunner().invoke(app, sys.argv[1:])\n"
    "escaped = None if isinstance(result.exception, SystemExit) else result.exception\n"
    "print(json.dumps({\n"
    "    'code': result.exit_code,\n"
    "    'out': result.stdout,\n"
    "    'err': result.stderr,\n"
    "    'raised': type(escaped).__name__ if escaped is not None else None,\n"
    "}))\n"
)


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A real project with canonical state applied and an index published."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.chdir(root)

    started = runner.invoke(app, ["init", "--json"], catch_exceptions=False)
    assert started.exit_code == 0, started.stderr

    (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
    (root / ".theurian/knowledge/architecture/bounds.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-bounds.yaml").write_text(MIGRATION)

    applied = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)
    assert applied.exit_code == 0, applied.stderr
    indexed = runner.invoke(app, ["index", "build", "--json"], catch_exceptions=False)
    assert indexed.exit_code == 0, indexed.stderr
    yield root


def _run_in_a_child(project: Path, argv: list[str]) -> dict[str, object]:
    """One CLI invocation in a child, killed if it does not return.

    Fails the test naming the command if the child had to be killed, which is the
    only shape a lost bound can take here: a command that never returns publishes
    nothing to assert against.
    """
    try:
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _CHILD, *argv],
            cwd=project,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_SECONDS,
            # A refusing command exits non-zero on purpose; that is the subject
            # here, not a failure of the harness.
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`theurian {' '.join(argv)}` did not return within {_CHILD_TIMEOUT_SECONDS}s "
            f"and was killed: the read is unbounded again, and nothing reaches the caller "
            f"to be graded"
        )
    lines = done.stdout.strip().splitlines()
    assert lines, (
        f"the child running `theurian {' '.join(argv)}` printed no report; it exited "
        f"{done.returncode} with stderr {done.stderr!r}"
    )
    report: dict[str, object] = json.loads(lines[-1])
    return report


def _published(report: dict[str, object], argv: list[str]) -> dict[str, object]:
    """The `--json` document the command wrote, from whichever channel carried it."""
    assert report["raised"] is None, (
        f"`theurian {' '.join(argv)}` escaped `--json` as a {report['raised']} rather than "
        f"a document, so a caller parsing stdout cannot tell a refusal from a crash"
    )
    text = str(report["out"]) or str(report["err"])
    assert text.strip(), (
        f"`theurian {' '.join(argv)}` published nothing on either channel: {report}"
    )
    payload: dict[str, object] = json.loads(text)
    return payload


# -- The active pointer -------------------------------------------------------

#: The commands measured reaching ``read_active_state`` on 2026-09-06 with a named
#: pipe at ``.theurian/state/active.json``: each was still inside ``read()`` when
#: a 12-second kill fired, with **zero bytes** on stdout and on stderr. They are
#: listed by name rather than swept, because what makes the list the right one is
#: that every entry was run in that configuration and killed.
_COMMANDS_OVER_THE_ACTIVE_POINTER: Final = (
    ["migrate", "status", "--json"],
    ["project", "status", "--json"],
    ["index", "status", "--json"],
    ["findings", "build", "--json"],
)


def _plant_a_named_pipe(path: Path) -> None:
    path.unlink(missing_ok=True)
    os.mkfifo(path)


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
@pytest.mark.parametrize(
    "argv", _COMMANDS_OVER_THE_ACTIVE_POINTER, ids=lambda argv: " ".join(argv[:2])
)
def test_a_named_pipe_at_the_active_pointer_is_answered_rather_than_waited_on(
    built: Path, argv: list[str]
) -> None:
    """#586 member 1. RED before ``read_active_state`` read through a descriptor.

    ``Path.exists()`` answers ``True`` for a named pipe and ``read_text`` then
    waits for a writer, so this sat on ``_require_project``'s path and reached
    every project-resolving command. The kill in :func:`_run_in_a_child` is the
    bound; without the fix each of these is killed rather than graded.

    What is asserted is that *something* arrives, not the exit code: ``project
    status`` reports the fault as a field of a successful document (it exists to
    describe a project's condition), while the other three refuse. Both are
    correct answers; neither is a stall.
    """
    _plant_a_named_pipe(built / ".theurian/state/active.json")
    report = _run_in_a_child(built, argv)
    payload = _published(report, argv)
    rendered = json.dumps(payload)
    assert "named pipe" in rendered, (
        f"`theurian {' '.join(argv)}` returned without naming what is at the pointer path, "
        f"so the reader cannot tell a planted artefact from a corrupt one: {rendered}"
    )
    assert "active.json" in rendered, (
        f"`theurian {' '.join(argv)}` does not name the file to act on: {rendered}"
    )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_the_active_pointer_refusal_carries_a_cure_the_reader_can_run(built: Path) -> None:
    """The remedy names the artefact *and* a command, not merely a non-empty string.

    Pinned once rather than at every swept command above, and asserted as two
    separable things: an operator meeting a planted pipe has to know which file
    to delete and what to type afterwards. A truthy remedy is not a remedy.
    """
    _plant_a_named_pipe(built / ".theurian/state/active.json")
    argv = ["migrate", "status", "--json"]
    payload = _published(_run_in_a_child(built, argv), argv)
    remedy = str(payload["remedy"])
    assert ".theurian/state/active.json" in remedy, (
        f"the remedy does not name the file holding the pipe: {remedy!r}"
    )
    assert "`theurian migrate apply`" in remedy, (
        f"the remedy names nothing the reader can run to get back to a working state: {remedy!r}"
    )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_at_the_index_pointer_is_reported_as_a_corrupt_pointer(
    built: Path,
) -> None:
    """#586's index-pointer face, which was bounded and *wrong* rather than stalled.

    ``read_active_index_pointer`` probed ``is_file()``, which answers ``False``
    for a named pipe, so the planted artefact was reported as *no pointer at
    all*: measured 2026-09-06, ``index status`` published ``built: false``,
    ``indexBuildId: null`` and ``indexPointerCorrupt: false`` at exit 0 for a
    project whose index had just been built. RED before the probe became
    ``exists()`` and the read became a descriptor read.

    ``indexPointerCorrupt`` is the field that decides the remedy the command
    prints -- delete the pointer *then* build, against build -- so this asserts
    the field and not only the absence of a stall.
    """
    _plant_a_named_pipe(built / ".theurian/state/active-index.json")
    argv = ["index", "status", "--json"]
    payload = _published(_run_in_a_child(built, argv), argv)
    assert payload["indexPointerCorrupt"] is True, (
        f"a named pipe at the index pointer is reported as a project with no index, which "
        f"sends the reader to build one they already have: {json.dumps(payload)}"
    )


# -- The local access token ---------------------------------------------------

#: The commands measured reaching ``FileSecretStore`` with a 0600 named pipe at
#: ``<data_dir>/auth/mcp-token`` (#585 round two's off-list find, this issue's
#: comment 1). Before the fix each ran until it was killed with **zero bytes on
#: both channels**, and ``daemon start`` did so *after* taking the daemon lock,
#: so every later starter read a stale holder.
#:
#: ``--port 7420`` on both, and ``--foreground`` on the starter: a development
#: machine's resident daemon owns 7419, and nothing here may register a service
#: or leave one detached. Neither command reaches a bind in this configuration --
#: the refusal comes first -- and the child is killed if it ever does.
_COMMANDS_OVER_THE_TOKEN: Final = (
    ["auth", "rotate", "--json", "--port", "7420"],
    ["daemon", "start", "--foreground", "--json", "--port", "7420"],
)

#: At the default umask a named pipe is created ``0644``, where
#: ``is_world_accessible`` refuses it first and this face never runs. 0600 is what
#: puts the artefact past that guard and into the open.
_A_PRIVATE_MODE: Final = 0o600


#: Every shape a local account can put at the token's name in one command, and
#: the sweep is the point rather than the list (#586 round two, H-3). The first
#: cut of this fixture planted a pipe and only a pipe, so a **directory** -- one
#: ``mkdir`` for the same actor -- went unmeasured: the probes reached for the
#: *stall* shape vocabulary, which excludes a directory on purpose, and published
#: "No local access token yet" over it while ``auth rotate --json`` escaped as an
#: ``IsADirectoryError`` traceback with an empty machine channel. A sweep over
#: the shapes is what would have caught that at implementation time.
#:
#: A **symbolic link** is here as the neighbour that must keep its own refusal:
#: it is #371's face, it has a different cure, and a fix that folded it into the
#: shape family would take the compromise warning away from the branch that says
#: it best.
_PLANTED_TOKEN_SHAPES: Final = ("pipe", "socket", "directory", "symlink")


def _plant(shape: str, path: Path, elsewhere: Path) -> None:
    """Put ``shape`` at ``path``. ``elsewhere`` is the symlink's target."""
    if shape == "pipe":
        os.mkfifo(path)
        path.chmod(_A_PRIVATE_MODE)
    elif shape == "socket":
        # Bound through a relative name from the directory: an `AF_UNIX` address
        # is capped near a hundred bytes and a pytest temporary path spends most
        # of it. The socket object is kept alive by the caller's `with`.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
            cwd = Path.cwd()
            os.chdir(path.parent)
            try:
                endpoint.bind(path.name)
            finally:
                os.chdir(cwd)
        path.chmod(_A_PRIVATE_MODE)
    elif shape == "directory":
        path.mkdir(mode=0o700)
    elif shape == "symlink":
        elsewhere.write_text("an-attacker-chosen-token-value\n", encoding="utf-8")
        elsewhere.chmod(_A_PRIVATE_MODE)
        path.symlink_to(elsewhere)
    else:  # pragma: no cover - the parametrization is the population
        raise AssertionError(f"unknown plant {shape!r}")


@pytest.fixture
def token_plant(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A data directory whose token path holds the requested shape.

    Returns the working directory to run in. Parametrized indirectly, so one
    fixture serves the whole shape sweep and a shape added to
    :data:`_PLANTED_TOKEN_SHAPES` is driven by every test that takes it.
    """
    shape = getattr(request, "param", "pipe")
    data_dir = tmp_path / "datadir"
    auth = data_dir / "auth"
    auth.mkdir(parents=True)
    auth.chmod(0o700)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "work").mkdir()
    _plant(shape, auth / "mcp-token", tmp_path / "elsewhere")
    return tmp_path / "work"


@pytest.fixture
def token_pipe(token_plant: Path) -> Path:
    """The named-pipe plant, for the tests whose subject is the *stall*."""
    return token_plant


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
@pytest.mark.parametrize("argv", _COMMANDS_OVER_THE_TOKEN, ids=lambda argv: " ".join(argv[:2]))
def test_a_named_pipe_at_the_token_is_answered_rather_than_waited_on(
    token_pipe: Path, argv: list[str]
) -> None:
    """#586 member 3, both faces.

    RED in two different ways, and the second is why this is a parametrized
    sweep rather than one test:

    * before ``O_NONBLOCK`` and the ``fstat``, both commands sat inside the open
      until they were killed;
    * with the refusal raised but ``auth rotate`` still naming only the *link*
      class in its ``except``, the new refusal escaped ``--json`` as a
      ``SecretPathIsNotAFileError`` at exit 1 with both channels empty --
      bounded, and publishing nothing to grade.

    The envelope must name the shape and carry a cure that names the artefact and
    something to run: a reader who did not plant the pipe cannot act on "the
    token file is wrong".
    """
    payload = _published(_run_in_a_child(token_pipe, argv), argv)
    assert "a named pipe (FIFO)" in str(payload["error"]), (
        f"`theurian {' '.join(argv)}` returned without naming what is at the token path: {payload}"
    )
    remedy = str(payload["remedy"])
    assert "mcp-token" in remedy, f"the remedy does not name the artefact to clear: {remedy!r}"
    assert "`theurian auth rotate`" in remedy, (
        f"the remedy names nothing the reader can run once the artefact is gone: {remedy!r}"
    )


#: The two setup probes over the token's own name. Both reached
#: ``if not path.is_file()`` after their symbolic-link arm, which answers
#: ``False`` for a named pipe.
_TOKEN_PROBES: Final = ("token", "token-storage")


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_doctor_reports_the_planted_token_rather_than_an_absent_one(token_pipe: Path) -> None:
    """``doctor``'s face of the same probe defect, which was bounded and false.

    Measured 2026-09-06 before the fix: both steps published ``missing`` -- "No
    local access token yet" and "The token file does not exist yet" -- over a
    0600 named pipe sitting at that exact path. ``missing`` is the status that
    makes ``setup`` *act*, and acting means minting into that name, which the
    store now declines; so the report was not merely worded wrongly, it pointed
    the next command at a write that cannot land.

    ``--dry-run`` is ``doctor``'s default and nothing here registers anything;
    ``--port 7420`` keeps the health probe off a development machine's resident
    daemon.
    """
    argv = ["doctor", "--json", "--port", "7420"]
    payload = _published(_run_in_a_child(token_pipe, argv), argv)
    published = payload["steps"]
    assert isinstance(published, list), f"`doctor --json` published no step list: {payload}"
    steps = {str(step["id"]): step for step in published}
    for probe in _TOKEN_PROBES:
        assert steps[probe]["status"] == "conflicting", (
            f"`doctor`'s `{probe}` step reports {steps[probe]['status']!r} over a named pipe "
            f"at the token's path: {steps[probe]}"
        )
        assert "named pipe" in str(steps[probe]["summary"]), (
            f"`doctor`'s `{probe}` step does not name what is at the path: {steps[probe]}"
        )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
@pytest.mark.parametrize("token_plant", _PLANTED_TOKEN_SHAPES, indirect=True)
def test_every_planted_shape_at_the_token_is_named_rather_than_escaping(
    token_plant: Path,
) -> None:
    """#586 round two, H-3. The sweep the first cut of this file did not run.

    Every shape a local account can put at ``<data_dir>/auth/mcp-token`` must
    reach ``auth rotate --json`` as a document naming the artefact. RED for the
    **directory** before this round: the probes asked the stall vocabulary, so
    ``auth rotate`` reached ``FileSecretStore.get``'s read and escaped as an
    ``IsADirectoryError`` traceback with an empty machine channel -- while the
    pipe at the same path published its refusal.

    The symbolic link is swept beside them and keeps its own wording on purpose:
    reading through one hands back a value somebody else chose, so its cure adds
    the compromise warning the shape refusals correctly do not carry.
    """
    argv = ["auth", "rotate", "--json", "--port", "7420"]
    payload = _published(_run_in_a_child(token_plant, argv), argv)
    error = str(payload["error"])
    remedy = str(payload["remedy"])
    assert "mcp-token" in error, (
        f"`auth rotate --json` does not name the artefact at the token's path: {error!r}"
    )
    assert "not a secret file" in error, f"the refusal does not say what is wrong: {error!r}"
    assert "mcp-token" in remedy and "`theurian auth rotate`" in remedy, (
        f"the remedy does not name both the artefact to clear and something to run: {remedy!r}"
    )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
@pytest.mark.parametrize("token_plant", ["directory"], indirect=True)
def test_doctor_names_a_directory_at_the_token_rather_than_calling_it_absent(
    token_plant: Path,
) -> None:
    """The probe half of the same finding, which decides whether setup *acts*.

    Measured RED: both steps published ``missing`` -- "No local access token yet"
    and "The token file does not exist yet" -- over a directory sitting at that
    path, and ``MISSING`` is the status that makes ``setup`` mint into the name.
    """
    argv = ["doctor", "--json", "--port", "7420"]
    payload = _published(_run_in_a_child(token_plant, argv), argv)
    published = payload["steps"]
    assert isinstance(published, list), f"`doctor --json` published no step list: {payload}"
    steps = {str(step["id"]): step for step in published}
    for probe in _TOKEN_PROBES:
        assert steps[probe]["status"] == "conflicting", (
            f"`doctor`'s `{probe}` step reports {steps[probe]['status']!r} over a directory "
            f"at the token's path: {steps[probe]}"
        )
        assert "a directory" in str(steps[probe]["summary"]), (
            f"`doctor`'s `{probe}` step does not name what is at the path: {steps[probe]}"
        )


# -- The env file -------------------------------------------------------------


@pytest.fixture
def env_plant(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """A data directory with a real token and the requested shape at ``env``.

    Returns the working directory and the symlink's victim. The token has to be
    mintable: ``auth rotate`` writes it *before* it touches the env file, so a
    planted token would stop the command one step earlier and this face would go
    unmeasured.
    """
    shape = getattr(request, "param", "pipe")
    data_dir = tmp_path / "datadir"
    auth = data_dir / "auth"
    auth.mkdir(parents=True)
    auth.chmod(0o700)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "work").mkdir()
    victim = tmp_path / "dotfiles-env"
    if shape == "pipe":
        os.mkfifo(data_dir / "env")
    else:
        victim.write_text("# a file its author wrote\nexport SOMETHING=else\n", encoding="utf-8")
        (data_dir / "env").symlink_to(victim)
    return tmp_path / "work", victim


def _next_steps(payload: dict[str, object]) -> str:
    """``nextSteps`` as one string, so a phrase can be looked for across its lines."""
    lines = payload["nextSteps"]
    assert isinstance(lines, list), f"`auth rotate --json` published no nextSteps: {payload}"
    return " ".join(str(line) for line in lines)


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
@pytest.mark.parametrize("env_plant", ["pipe"], indirect=True)
def test_a_named_pipe_at_the_env_file_does_not_hold_a_rotation(
    env_plant: tuple[Path, Path],
) -> None:
    """#586 round two, H-2 face (a).

    The env writer's open carried the creation mode and nothing else -- no
    ``O_NONBLOCK`` -- so a named pipe at ``<data_dir>/env`` held ``auth rotate``
    inside it: measured RED, killed at 12 s with both channels empty, *after* the
    token had already been replaced. Nothing here is allowed to fail the
    rotation, so what is asserted is that the command completes and says what it
    could not do.
    """
    working, _ = env_plant
    argv = ["auth", "rotate", "--json", "--port", "7420"]
    payload = _published(_run_in_a_child(working, argv), argv)
    assert payload["rotated"] is True, (
        f"the env file must not be able to fail a rotation the token write already "
        f"finished: {payload}"
    )
    steps = _next_steps(payload)
    assert "named pipe" in steps, (
        f"the rotation does not say what is at the env path, so nothing tells the operator "
        f"why their shell will not export the token: {steps!r}"
    )


@pytest.mark.parametrize("env_plant", ["symlink"], indirect=True)
def test_a_symlink_at_the_env_file_does_not_write_through_it(
    env_plant: tuple[Path, Path],
) -> None:
    """#586 round two, H-2 face (b) -- the write escape, at the CLI.

    Measured RED: the managed block was written *through* the link, into a file
    outside the data directory that its author wrote, at exit 0. The victim's own
    bytes are the assertion, because that is what the escape destroyed.
    """
    working, victim = env_plant
    before = victim.read_text(encoding="utf-8")
    argv = ["auth", "rotate", "--json", "--port", "7420"]
    payload = _published(_run_in_a_child(working, argv), argv)
    assert victim.read_text(encoding="utf-8") == before, (
        "the rotation wrote through the symbolic link and out of the data directory, "
        "overwriting a file it does not own"
    )
    steps = _next_steps(payload)
    assert "symbolic link" in steps, (
        f"the rotation does not say the env file was left untouched or why: {steps!r}"
    )


#: ``chflags uchg`` is BSD/macOS. Linux's equivalent, ``chattr +i``, needs
#: ``CAP_LINUX_IMMUTABLE`` -- root -- so there is no unprivileged way to plant
#: this fault there and the test skips rather than pretending. What it drives is
#: the one class of ``store.set`` failure that #572 measured as genuinely
#: unrepairable: a directory-mode fault self-repairs, because ``set``
#: re-``mkdir``s ``auth/`` and re-asserts its mode before the open.
_CAN_MAKE_A_FILE_IMMUTABLE: Final = sys.platform == "darwin" and shutil.which("chflags") is not None


@pytest.mark.skipif(not _CAN_MAKE_A_FILE_IMMUTABLE, reason="needs BSD chflags and not root")
@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="root is refused by nothing here")
def test_a_token_that_cannot_be_rewritten_is_a_document_and_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes #572, whose recorded fix shape this is.

    ``auth rotate`` wrapped ``store.set`` in ``except SecretPathIsASymbolicLinkError``
    and then, from #586 round one, ``except SecurityError``. Neither covers the
    faults the store does not *classify* -- another account's file, a read-only
    mount, ``ENOSPC``, or the immutable flag planted here -- so each escaped
    ``--json`` as a Rich traceback with empty stdout, the CP-2 shape on a command
    whose contract is a parseable document.

    The remedy is asserted to name the artefact and something runnable, not
    merely to be non-empty: an operator meeting this has to know which file
    refused, and `chflags nouchg` is the cure for the fault this test plants.
    """
    data_dir = tmp_path / "datadir"
    auth = data_dir / "auth"
    auth.mkdir(parents=True)
    auth.chmod(0o700)
    token = auth / "mcp-token"
    token.write_text("an-existing-token-value-long-enough-to-pass\n", encoding="utf-8")
    token.chmod(0o600)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "work").mkdir()

    subprocess.run(["chflags", "uchg", str(token)], check=True, capture_output=True)  # noqa: S603, S607
    try:
        # The positive control on the plant: a filesystem that ignores the flag
        # would let the rotation succeed and this test would assert nothing.
        with pytest.raises(OSError):
            token.write_text("probe", encoding="utf-8")

        argv = ["auth", "rotate", "--json", "--port", "7420"]
        payload = _published(_run_in_a_child(tmp_path / "work", argv), argv)
    finally:
        subprocess.run(["chflags", "nouchg", str(token)], check=False, capture_output=True)  # noqa: S603, S607

    error = str(payload["error"])
    remedy = str(payload["remedy"])
    assert "could not be written" in error, f"the refusal does not say what failed: {error!r}"
    assert str(token) in remedy, (
        f"the remedy does not name the file that refused the write: {remedy!r}"
    )
    assert "chflags nouchg" in remedy and "`theurian auth rotate`" in remedy, (
        f"the remedy names nothing the reader can run to clear the fault and retry: {remedy!r}"
    )
    assert token.read_text(encoding="utf-8").strip() == (
        "an-existing-token-value-long-enough-to-pass"
    ), "and the old token is still in place, which is what the remedy tells the reader"
