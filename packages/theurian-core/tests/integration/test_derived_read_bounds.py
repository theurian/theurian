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
