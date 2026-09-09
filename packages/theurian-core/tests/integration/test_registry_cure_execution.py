"""Each arm of the registry cure, executed in the condition it was written for.

``tests/unit/test_registry_deletion_cure_claims.py`` pins what these texts *say*,
byte for byte. Nothing pinned whether a reader who does what they say gets out,
and round two found two ways that gap paid: an arm naming a cause that was false
for the reader standing in it, and an arm whose own ``chmod`` did not unblock the
deletion its own next sentence instructs. Neither is a wording defect a shape
check can see. Both are visible the moment somebody plants the condition and runs
the instructions.

So each test below **plants** one condition in ``tmp_path``, **follows** the arm's
operational steps with the real ``chmod`` and ``rm`` a reader would type, and
**asserts recovery** -- the registry readable again and ``theurian project
register`` succeeding in-process. A cure that names a false cause fails
mechanically, because the step it prescribes does not apply to the condition it
was handed to. A cure whose instruction does not clear the way fails at the step
after it. A goal statement ("the reader gets back to a working registry") passes
only because recovery is measured rather than described.

**The (arm x condition) table, derived from the routing authority.** Not
hand-listed: ``_arm_for_a_refused_registry`` in
``application/project_service.py`` is::

    return when_refused if exc.errno == errno.EACCES else RegistryFailureArm.UNKNOWN

and ``ProjectRegistry._raw_entries`` has exactly four raises, two of which reach
it::

    self.path.exists()     -> except OSError -> when_refused=DIRECTORY_UNREADABLE
    self.path.read_text()  -> except OSError -> when_refused=FILE_UNREADABLE
    json.loads(...)        -> except (JSONDecodeError, UnicodeDecodeError) -> UNPARSABLE
    not isinstance(loaded, dict)                                           -> UNPARSABLE

The first two rows each split on the ``errno`` predicate and the last two do not
split at all, so the arms a reader can be handed are the six pairs below. The
condition column is a filesystem state that produces that errno at that call, and
every one of them was driven through ``ProjectRegistry.load`` to confirm which
arm came back before it was written down.

===============  =============  ====================  =======================================
Raise            ``errno``      Arm                   Condition, and where it is executed
===============  =============  ====================  =======================================
exists probe     ``EACCES``     DIRECTORY_UNREADABLE  data directory at mode ``000`` -- here
exists probe     anything else  UNKNOWN               ``ENAMETOOLONG``: an over-long data
                                                      directory path -- here, refusal only
read             ``EACCES``     FILE_UNREADABLE       registry file at mode ``000`` -- here
read             anything else  UNKNOWN               ``EISDIR``: a directory where the file
                                                      belongs -- here
decode           n/a            UNPARSABLE            bytes that are not JSON -- here
top level        n/a            UNPARSABLE            a JSON array;
                                                      ``test_project_registry_errors.py``
===============  =============  ====================  =======================================

**One exemption, recorded rather than skipped.** The ``ENAMETOOLONG`` row has no
recovery expressible in a sandbox: what cures it is a shorter
``THEURIAN_DATA_DIR``, which is a change to the reader's environment and not an
operation on the file. That case executes the refusal, the arm and the
message-names-the-cause assertions, and stops there; the reason is repeated at
the test itself so it cannot be read as an oversight.

**Every quoted instruction is quoted, adjacent, and checked against the shipped
text.** The soft spot in an execution test is a charitable script -- one that
does what the author knows works rather than what the text says -- so each test
carries the sentences it implements as literals in
:data:`THE_INSTRUCTIONS_EACH_TEST_FOLLOWS`, asserts they are in the cure the
condition actually produced, and then carries out those sentences and nothing
else. A re-pin of any arm breaks the containment assertion here and forces the
script to be re-read beside the new text. The pairing is what a reviewer should
attack: read the quote, then read the script, and ask whether the second is the
first.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from registry_deletion_cure_claims import the_pinned_cure
from typer.testing import CliRunner

from theurian.application.project_service import (
    ProjectError,
    ProjectRegistry,
    RegistryFailureArm,
)
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

#: A `chmod` cannot refuse root, and Windows has no POSIX mode bits at all -- the
#: same guard `test_cli_commands.py` and `test_project_registry_errors.py` use
#: before a permission-refusal test. Without it the two `EACCES` conditions below
#: would not be planted at all as root, and every following step would succeed
#: for the wrong reason: the predicate would report that the cure works exactly
#: where it cannot have been tested.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command in-process and parse its JSON.

    In-process on purpose: nothing here may run a real ``setup``, register a
    service, or start a daemon. ``theurian project register`` is the only command
    these tests need, and it writes the registry under ``THEURIAN_DATA_DIR`` and
    nothing else.
    """
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _shell(*command: str) -> subprocess.CompletedProcess[bytes]:
    """One command from a cure, run the way the reader would type it.

    ``chmod`` and ``rm`` rather than ``Path.chmod`` and ``Path.unlink``: the cure
    names those two programs, and the claim under test is that *those* commands
    do what the sentence says. A Python call that happens to have the same effect
    would be the test agreeing with the author rather than with the text.
    """
    return subprocess.run(list(command), check=False, capture_output=True)  # noqa: S603


@dataclass(frozen=True)
class _ARegisteredProject:
    """A Git working tree with one real registration, and the file that holds it."""

    root: Path
    registry: Path
    #: The directory every path above holds, and the boundary of what these tests
    #: may ``chmod``. A cure that says "every directory above it" is followed only
    #: as far as the sandbox owns; see the directory-arm test for why.
    sandbox_root: Path


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_ARegisteredProject]:
    """A registered project whose registry is a real file this test may break.

    Registered for real, through the CLI, so the bytes a test then corrupts are
    the bytes the product wrote -- including the ``registeredAt`` the cure warns
    is lost. ``THEURIAN_DATA_DIR`` and the working directory are both inside
    ``tmp_path``: nothing here writes to the developer's home directory, and the
    only commands run are ``init`` and ``project register``, neither of which
    registers a service or starts a daemon.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    assert _invoke("init")[0] == 0, "the fixture must produce a project"
    assert _invoke("project", "register")[0] == 0, "and a registration to lose"

    registry = data_dir / "projects.json"
    assert str(root) in registry.read_text(encoding="utf-8"), (
        "the registry must name this root, or 'the roots it lists' is not what a test reads "
        "back out of it"
    )
    yield _ARegisteredProject(root=root, registry=registry, sandbox_root=tmp_path)


#: The sentences each test below carries out, transcribed from the shipped cure
#: with ``{path}`` and ``{parent}`` as the only holes -- the same two holes
#: ``registry_deletion_cure_claims`` uses.
#:
#: Transcribed rather than read out of the pin table on purpose. A quote taken
#: from the pin would move with the pin, and the whole point of writing it here
#: is that a rewritten arm breaks
#: :func:`_assert_this_script_follows_the_cure` and sends a human back to compare
#: the new sentences against the steps below them.
THE_INSTRUCTIONS_EACH_TEST_FOLLOWS: dict[str, tuple[str, ...]] = {
    "unparsable": (
        "Inspect {path} before removing it -- the roots it lists are legible by eye even "
        "where its top level is not something this build can read.",
        "Once you have read out the roots you need, delete it and re-register each project "
        "with `theurian project register`.",
    ),
    "file-unreadable": (
        "Restore read access to {path} first -- `chmod u+r` on it -- because this process "
        "could not open the file, so nothing in it can be read before it is destroyed. Then "
        "inspect it.",
        "Once you have read out the roots you need, delete it and re-register each project "
        "with `theurian project register`.",
    ),
    "directory-unreadable": (
        "Restore access to {parent} first -- `chmod u+rwx` on it, and `chmod u+rx` on every "
        "directory above it -- because this process could not look inside that directory, and "
        "while it cannot, {path} can be neither read nor deleted.",
        "Then inspect it.",
        "Once you have read out the roots you need, delete it and re-register each project "
        "with `theurian project register`.",
    ),
    "directory-unreadable-warning": (
        "Removing the file needs the write bit on {parent} as well as the search bit, so "
        "`u+rx` alone would restore the read and leave the deletion below refused.",
    ),
    "unknown": (
        "Read {path} before removing it. What refused it is named in the message beside this "
        "remedy rather than here, so read that first: it is not always a permission, and "
        "neither a directory sitting where the file belongs nor a path the filesystem will "
        "not accept is cured by a mode change.",
    ),
}


def _assert_this_script_follows_the_cure(cure: str, *, quoting: str, path: Path) -> None:
    """The quoted instructions are the shipped ones, so the script implements the text.

    Without this the quotes are decoration: a reviewer asked "does this script
    follow this text?" could only compare the script against a transcription
    nobody checked. Here the transcription is checked, and the script is beside
    it.
    """
    for sentence in THE_INSTRUCTIONS_EACH_TEST_FOLLOWS[quoting]:
        expected = sentence.format(path=path, parent=path.parent)
        assert expected in cure, (
            f"the {quoting} script carries out a sentence this cure no longer contains, so "
            f"the steps below are following a text that is not shipped. Re-read the new cure "
            f"and re-write the steps to match it.\nquoted: {expected!r}\n cure: {cure!r}"
        )


def _the_refusal(registry: Path) -> ProjectError:
    """The ``ProjectError`` ``load`` raises for whatever has been planted at ``registry``."""
    with pytest.raises(ProjectError) as excinfo:
        ProjectRegistry(path=registry).load()
    return excinfo.value


def _assert_the_arm_is(failure: ProjectError, arm: RegistryFailureArm, registry: Path) -> None:
    """The cure that arrived is this arm's, byte for byte -- the routing pin."""
    assert failure.remedy == the_pinned_cure(arm.value, registry), (
        f"this condition must be cured on the {arm.value} arm; a cure for a different one "
        f"names a cause its reader does not have.\ngot: {failure.remedy!r}"
    )


def _assert_the_reader_is_out(sandbox: _ARegisteredProject) -> None:
    """Recovery, measured on both halves of what the cure promises.

    The file reads again *and* the invocation the cure ends with succeeds. Either
    alone would pass in a state the reader would still call broken: a registry
    that reads as ``{}`` has lost the registration, and a ``register`` that
    reports success without the entry landing is the same loss one step later.
    """
    assert ProjectRegistry(path=sandbox.registry).load() == {}, (
        "following the cure removes the file, so the read that used to refuse now answers "
        "with an empty registry rather than raising"
    )

    code, payload = _invoke("project", "register")

    assert code == 0, f"the invocation the cure ends with must succeed: {payload!r}"
    entries = ProjectRegistry(path=sandbox.registry).load()
    assert [Path(entry["rootPath"]).resolve() for entry in entries.values()] == [
        sandbox.root.resolve()
    ], "and the registration the reader lost is back"


def test_a_reader_of_the_unparsable_arm_recovers_by_following_it(
    sandbox: _ARegisteredProject,
) -> None:
    """The bytes are in front of the reader, and the arm says so -- executed.

    Quoted from the ``unparsable`` cure::

        Inspect {path} before removing it -- the roots it lists are legible by
        eye even where its top level is not something this build can read.
        ...
        Once you have read out the roots you need, delete it and re-register
        each project with `theurian project register`.

    Three steps, in that order: read the raw bytes and check that the roots
    really are legible in them, remove the file, re-register. The first is the
    one a shape check cannot make -- "legible by eye" is a claim about this
    condition, and a truncated registry is where it has to hold.
    """
    truncated = f'{{"demo": {{"rootPath": "{sandbox.root}"'
    sandbox.registry.write_text(truncated, encoding="utf-8")

    failure = _the_refusal(sandbox.registry)
    _assert_the_arm_is(failure, RegistryFailureArm.UNPARSABLE, sandbox.registry)
    _assert_this_script_follows_the_cure(
        failure.remedy, quoting="unparsable", path=sandbox.registry
    )

    by_eye = sandbox.registry.read_text(encoding="utf-8")
    assert str(sandbox.root) in by_eye, (
        "the arm's own promise: a top level this build cannot read still shows the reader "
        "which roots to re-register"
    )
    removed = _shell("rm", str(sandbox.registry))
    assert removed.returncode == 0, f"the deletion the cure offers must run: {removed.stderr!r}"

    _assert_the_reader_is_out(sandbox)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_reader_of_the_file_unreadable_arm_recovers_by_following_it(
    sandbox: _ARegisteredProject,
) -> None:
    """``chmod u+r`` on the file, then the inspection it makes possible -- executed.

    Quoted from the ``file-unreadable`` cure::

        Restore read access to {path} first -- `chmod u+r` on it -- because this
        process could not open the file, so nothing in it can be read before it
        is destroyed. Then inspect it.
        ...
        Once you have read out the roots you need, delete it and re-register
        each project with `theurian project register`.

    Four steps: the ``chmod`` the text names, the read it says the ``chmod``
    makes possible, the deletion, the re-registration. The reason this arm's
    ``chmod`` is read-only and the directory arm's is not is measured in
    ``test_project_registry_errors.py`` -- unlinking needs the bits on the
    directory, so a mode-``000`` file inside a traversable parent is removable
    exactly as the tail offers, and this test's ``rm`` is that measurement
    arriving through the cure.
    """
    sandbox.registry.chmod(0o000)
    try:
        failure = _the_refusal(sandbox.registry)
        _assert_the_arm_is(failure, RegistryFailureArm.FILE_UNREADABLE, sandbox.registry)
        _assert_this_script_follows_the_cure(
            failure.remedy, quoting="file-unreadable", path=sandbox.registry
        )

        restored = _shell("chmod", "u+r", str(sandbox.registry))
        assert restored.returncode == 0, f"the cure's own first step: {restored.stderr!r}"

        by_eye = sandbox.registry.read_text(encoding="utf-8")
        assert str(sandbox.root) in by_eye, (
            "'Then inspect it.' is only an instruction if the chmod above made the file readable"
        )
        removed = _shell("rm", str(sandbox.registry))
        assert removed.returncode == 0, f"and the deletion must run: {removed.stderr!r}"
    finally:
        if sandbox.registry.exists():
            sandbox.registry.chmod(0o600)

    _assert_the_reader_is_out(sandbox)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_reader_of_the_directory_unreadable_arm_recovers_by_following_it(
    sandbox: _ARegisteredProject,
) -> None:
    """The arm round two caught: its ``chmod`` must unblock the ``rm`` it goes on to instruct.

    Quoted from the ``directory-unreadable`` cure::

        Restore access to {parent} first -- `chmod u+rwx` on it, and `chmod u+rx`
        on every directory above it -- because this process could not look inside
        that directory, and while it cannot, {path} can be neither read nor
        deleted.
        ...
        Then inspect it.
        ...
        Once you have read out the roots you need, delete it and re-register
        each project with `theurian project register`.

    Five steps: the mode on the data directory, the mode on each directory above
    it, the read, the deletion, the re-registration. At ``2d1f60c2`` the first
    step was ``chmod u+rx`` and the fourth exited 1 -- the reader landed at
    exactly the refusal this arm was written to lift, with ``u+w`` named nowhere.

    **What the second step does and does not cover, twice over.** "Every
    directory above it" is executed over the ancestors *inside the sandbox* and
    stops there: the ones above ``tmp_path`` belong to the machine, this test
    does not own them, and ``chmod`` on a directory another user owns is refused
    whatever mode is asked for -- a script that walked to ``/`` would be
    measuring the developer's filesystem rather than the cure. They are already
    traversable, necessarily: the fixture could not have written a registry
    through them otherwise. And a reader whose *ancestor* is at mode ``000`` is
    not planted at all. That reader meets a problem the text does not order for
    them -- the first command needs search on the ancestor to reach the directory
    it is about, so the two clauses would have to be run in the opposite order.
    Recorded here rather than asserted, since it is a gap in the cure and not in
    this script.
    """
    data_dir = sandbox.registry.parent
    inside_the_sandbox = [
        above
        for above in data_dir.parents
        if above == sandbox.sandbox_root or sandbox.sandbox_root in above.parents
    ]
    data_dir.chmod(0o000)
    try:
        failure = _the_refusal(sandbox.registry)
        _assert_the_arm_is(failure, RegistryFailureArm.DIRECTORY_UNREADABLE, sandbox.registry)
        _assert_this_script_follows_the_cure(
            failure.remedy, quoting="directory-unreadable", path=sandbox.registry
        )

        restored = _shell("chmod", "u+rwx", str(data_dir))
        assert restored.returncode == 0, f"the cure's own first step: {restored.stderr!r}"
        assert inside_the_sandbox, (
            "'every directory above it' has to reach at least one directory here, or the "
            "second clause is being reported as executed while executing nothing"
        )
        for above in inside_the_sandbox:
            ancestor = _shell("chmod", "u+rx", str(above))
            assert ancestor.returncode == 0, (
                f"'and `chmod u+rx` on every directory above it' has to be runnable on every "
                f"one of them: {above} said {ancestor.stderr!r}"
            )

        by_eye = sandbox.registry.read_text(encoding="utf-8")
        assert str(sandbox.root) in by_eye, "'Then inspect it.' has to be possible by then"
        removed = _shell("rm", str(sandbox.registry))
        assert removed.returncode == 0, (
            f"this is the step `u+rx` left refused: the arm's own chmod has to grant what the "
            f"deletion needs, not only what the read needs: {removed.stderr!r}"
        )
    finally:
        data_dir.chmod(0o700)

    _assert_the_reader_is_out(sandbox)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_directory_arms_warning_about_u_plus_rx_is_true_of_the_filesystem(
    tmp_path: Path,
) -> None:
    """The sentence that justifies the ``w``, executed as its own claim.

    Quoted from the ``directory-unreadable`` cure::

        Removing the file needs the write bit on {parent} as well as the search
        bit, so `u+rx` alone would restore the read and leave the deletion below
        refused.

    That is a statement about POSIX, and the arm above is written on the strength
    of it: if it were false the extra ``w`` would be an instruction with nothing
    behind it. Driven in its own sandbox, because it is the *counterfactual* --
    the mode the arm no longer prescribes -- and running it in the recovery test
    would leave that test measuring a failure path.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    registry = data_dir / "projects.json"
    registry.write_text('{"demo": {"rootPath": "/tmp/demo"}}\n', encoding="utf-8")
    cure = the_pinned_cure(RegistryFailureArm.DIRECTORY_UNREADABLE.value, registry)
    _assert_this_script_follows_the_cure(
        cure, quoting="directory-unreadable-warning", path=registry
    )

    data_dir.chmod(0o000)
    try:
        restored = _shell("chmod", "u+rx", str(data_dir))
        read = _shell("cat", str(registry))
        removed = _shell("rm", str(registry))
    finally:
        data_dir.chmod(0o700)

    assert restored.returncode == 0, "the counterfactual mode has to be settable"
    assert read.returncode == 0, "`u+rx` alone does restore the read, exactly as the arm says"
    assert removed.returncode != 0, (
        "and does leave the deletion refused, which is why the arm prescribes `u+rwx`; if this "
        "ever passes, the arm's extra `w` has lost its reason"
    )
    assert registry.exists(), "the file the reader was told to delete is still there"


def test_a_reader_whose_registry_path_holds_a_directory_is_not_sent_to_chmod_it(
    sandbox: _ARegisteredProject,
) -> None:
    """``EISDIR`` -- the condition that made the errno split necessary.

    Quoted from the ``unknown`` cure::

        Read {path} before removing it. What refused it is named in the message
        beside this remedy rather than here, so read that first: it is not always
        a permission, and neither a directory sitting where the file belongs nor
        a path the filesystem will not accept is cured by a mode change.

    This lead prescribes no operation, so following it has two parts. First the
    text's own instruction: go to the message, which must therefore name the
    condition -- and it does, as ``Is a directory``. Then the removal *that*
    condition needs, which is ``rm -r`` and not the ``chmod u+r`` this reader was
    handed until the split; a reader who ran that would have changed a mode that
    was never the problem and still had a directory in the way.

    Both halves are asserted: that the arm is ``UNKNOWN``, and that its lead
    names no ``chmod`` -- an arm that regained one would be false for exactly
    this reader.
    """
    sandbox.registry.unlink()
    sandbox.registry.mkdir()

    failure = _the_refusal(sandbox.registry)
    _assert_the_arm_is(failure, RegistryFailureArm.UNKNOWN, sandbox.registry)
    _assert_this_script_follows_the_cure(failure.remedy, quoting="unknown", path=sandbox.registry)

    assert os.strerror(errno.EISDIR) in str(failure), (
        "the lead sends the reader to this message for the cause, so the message has to carry "
        f"it: {failure}"
    )
    assert "chmod" not in failure.remedy, (
        "there is no mode to restore here, and a cure naming one states a cause this reader "
        "does not have"
    )

    removed = _shell("rm", "-r", str(sandbox.registry))
    assert removed.returncode == 0, f"the removal this condition needs: {removed.stderr!r}"

    _assert_the_reader_is_out(sandbox)


def test_a_data_directory_path_the_filesystem_refuses_is_not_sent_to_chmod_either(
    tmp_path: Path,
) -> None:
    """``ENAMETOOLONG`` at the probe: the arm and the message, with recovery exempted.

    The other half of the errno split, and the one that arrives at the
    ``.exists()`` probe rather than at the read. Until ``30e460b9`` this reader
    was handed the data directory's ``chmod`` story for a path no mode could
    make acceptable.

    **Recovery is exempted here, and this is the reason.** What cures an
    over-long ``THEURIAN_DATA_DIR`` is a shorter one -- a change to the reader's
    environment, not an operation on any file -- so there is no sequence of
    commands inside this sandbox that ends with the registry readable at the path
    that refused. The cure's own lead is honest about that: it prescribes nothing
    and sends the reader to the message. What is executed is therefore the
    refusal, the arm, and that the message names the cause the lead points at.

    Asserted on ``errno`` as well as on the message, so a platform that answered
    some other refusal would say so rather than quietly testing a different
    condition.
    """
    too_long = tmp_path / ("x" * 300) / "projects.json"

    failure = _the_refusal(too_long)
    _assert_the_arm_is(failure, RegistryFailureArm.UNKNOWN, too_long)
    _assert_this_script_follows_the_cure(failure.remedy, quoting="unknown", path=too_long)

    cause = failure.__cause__
    assert isinstance(cause, OSError) and cause.errno == errno.ENAMETOOLONG, (
        f"the fixture must reach the probe's refusal for this errno: {cause!r}"
    )
    assert os.strerror(errno.ENAMETOOLONG) in str(failure), (
        f"the lead sends the reader to this message for the cause: {failure}"
    )
    assert "chmod" not in failure.remedy, (
        "no mode change makes this path acceptable, so naming one would send the reader to "
        "correct a permission that was never their problem"
    )
