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
exists probe     ``EACCES``     DIRECTORY_UNREADABLE  data directory at mode ``000`` -- here;
                                                      *and* an ancestor at ``000`` with the
                                                      data directory at ``0700`` -- here
exists probe     anything else  UNKNOWN               ``ENAMETOOLONG``: an over-long data
                                                      directory path -- here, refusal only
read             ``EACCES``     FILE_UNREADABLE       registry file at mode ``000`` -- here
read             anything else  UNKNOWN               ``EISDIR``: a directory where the file
                                                      belongs -- here
decode           n/a            UNPARSABLE            bytes that are not JSON -- here
top level        n/a            UNPARSABLE            a JSON array;
                                                      ``test_project_registry_errors.py``
===============  =============  ====================  =======================================

**One arm, two members.** The ``EACCES`` row at the probe is two filesystem
states, not one: the refusal can be the data directory's own or any ancestor's,
and the ``OSError`` is the same shape either way -- ``filename`` is the whole
registry path and no component is named. So one text has to serve both, and both
are planted here. At ``b9e8296b`` only the first was, and the second was
described in a comment as a gap; the arm's opening ``chmod`` exits 1 for it.

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

**And the commands come out of the quote, not from beside it.** Containment
alone leaves the script's own ``chmod u+rwx`` a second, independent literal:
re-pin the sentence and the script keeps running what it always ran. Round three
measured the cost of that -- two mutations that rewrote a mode in production and
re-pinned every transcription in lockstep survived the whole suite, one of them
reinstating a defect round two had already fixed. So a command a cure backticks
is asserted to be among that quote's backticked spans before it runs
(:func:`_shell_what_the_cure_backticks`), and where the quote names exactly one
it is read out and run (:func:`_the_only_command_the_cure_backticks`). Three
commands are *not* backticked anywhere and are run as **goal-statement
acceptances**, each recorded at its test: ``cat`` and ``ls`` for "inspect it" and
"Read {path}" -- the sentence names a goal and the program that reaches it
depends on what is at the path -- and ``rm`` (``rm -r`` over a directory) for
"delete it".
"""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

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

    ``stdin`` is closed. BSD ``rm`` prompts before removing a file whose mode
    denies write when its standard input is a terminal, and under ``pytest -s``
    it inherits one: the run would block on a prompt no assertion answers.
    ``DEVNULL`` turns that into an immediate refusal, which is an outcome a test
    can read.
    """
    return subprocess.run(  # noqa: S603
        list(command), check=False, capture_output=True, stdin=subprocess.DEVNULL
    )


@dataclass(frozen=True)
class _ARegisteredProject:
    """A Git working tree with one real registration, and the file that holds it."""

    root: Path
    registry: Path
    #: The id the registration landed under, taken from what ``project register``
    #: reported rather than assumed from the directory name. The tail's recovery
    #: is ``--project-id <its projectId>``, so every script here has an id to pass
    #: and a key to find afterwards.
    project_id: str
    #: The directory every path above holds, and the boundary of what these tests
    #: may ``chmod``. A cure that says "every directory above it" is followed only
    #: as far as the sandbox owns; see the directory-arm tests for why.
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

    **The data directory sits one level down**, under ``tmp_path/above``, and
    that level is the point: the ``directory-unreadable`` arm is reached by a
    refusal at the data directory *or* at any directory over it, and until this
    fixture owned an ancestor it could ``chmod`` there was nowhere to plant the
    second member. ``above`` is inside ``tmp_path``, so both members are planted
    and undone within the sandbox.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "above" / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    assert _invoke("init")[0] == 0, "the fixture must produce a project"
    code, registered = _invoke("project", "register")
    assert code == 0, "and a registration to lose"

    registry = data_dir / "projects.json"
    written = registry.read_text(encoding="utf-8")
    assert str(root) in written and registered["projectId"] in written, (
        "the registry must name this root under this id, or 'the ids and roots it lists' is "
        "not what a test reads back out of it"
    )
    yield _ARegisteredProject(
        root=root,
        registry=registry,
        project_id=registered["projectId"],
        sandbox_root=tmp_path,
    )


#: The sentences each test below carries out, transcribed from the shipped cure
#: with ``{path}`` and ``{parent}`` as the only holes -- the same two holes
#: ``registry_deletion_cure_claims`` uses.
#:
#: Transcribed rather than read out of the pin table on purpose. A quote taken
#: from the pin would move with the pin, and the whole point of writing it here
#: is that a rewritten arm breaks
#: :func:`_assert_this_script_follows_the_cure` and sends a human back to compare
#: the new sentences against the steps below them.
#: The shared tail's recovery sentence, which every test that gets its reader out
#: carries out: read the ids and roots, delete, re-register each project under the
#: id it had, inside the root it had. Spelled once and referenced from each key
#: below, because a tail transcribed four times drifts between the four -- round
#: three found the ``unknown`` key quoting no tail at all while its test executed
#: one, so a lockstep re-pin of the tail left that test green with its three
#: siblings RED.
_THE_RECOVERY: Final = (
    "So read out every entry's projectId (the key it sits under) and its rootPath first; "
    "then delete it and re-register each project with `theurian project register`, passing "
    "`--project-id <its projectId>` and running it inside that rootPath."
)

THE_INSTRUCTIONS_EACH_TEST_FOLLOWS: dict[str, tuple[str, ...]] = {
    "unparsable": (
        "Inspect {path} before removing it -- the ids and roots it lists are legible by eye "
        "even where its top level is not something this build can read.",
        _THE_RECOVERY,
    ),
    "file-unreadable": (
        "Restore read access to {path} first -- `chmod u+r` on it -- because this process "
        "could not open the file, so nothing in it can be read before it is destroyed. Then "
        "inspect it.",
        _THE_RECOVERY,
    ),
    "directory-unreadable": (
        "Restore access to {parent} first, working from the top down: `chmod u+rx` on each "
        "directory above it you cannot `cd` into, then `chmod u+rwx` on {parent} itself.",
        "Then inspect it.",
        _THE_RECOVERY,
    ),
    "directory-unreadable-order": (
        "The order matters: `chmod` on {parent} is itself refused while a directory above it "
        "denies the search.",
    ),
    "directory-unreadable-warning": (
        "removing the file needs the write bit on {parent} as well as the search bit, so "
        "`u+rx` alone would restore the read and leave the deletion below refused.",
    ),
    "unknown": (
        "Read {path} before removing it. What refused it is named in the message beside this "
        "remedy rather than here, so read that first: it is not always a permission, and "
        "neither a directory sitting where the file belongs nor a path the filesystem will "
        "not accept is cured by a mode change.",
        _THE_RECOVERY,
    ),
    "unknown-without-the-recovery": (
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


def _the_commands_the_cure_backticks(quoting: str) -> frozenset[str]:
    """Every backticked span in the sentences this test says it carries out.

    The cure writes its operational words in backticks -- ``chmod u+rx``,
    ``chmod u+rwx``, ``cd``, ``u+rx``, ``theurian project register``,
    ``--project-id <its projectId>`` -- so the words a script may run are
    derivable from the quote rather than typed a second time beside it. Round
    three measured what the second typing costs: two mutations that rewrote a
    mode in production *and* re-pinned every transcription in lockstep survived
    the suite, because the script's own ``u+rwx`` and ``u+r`` were independent
    literals that no assertion compared against the sentence above them.

    Read from the unformatted templates, which is safe because no backticked span
    in any of them contains a ``{path}`` or ``{parent}`` hole -- asserted below.
    """
    spans = frozenset(
        span
        for sentence in THE_INSTRUCTIONS_EACH_TEST_FOLLOWS[quoting]
        for span in re.findall(r"`([^`]+)`", sentence)
    )
    assert not any("{" in span for span in spans), (
        f"a backticked span in the {quoting} quote now interpolates a path, so reading the "
        f"argv out of the template no longer yields the words the reader types: {spans!r}"
    )
    return spans


def _assert_the_cure_backticks(named: str, *, quoting: str) -> None:
    """``named`` is a command the quoted sentences actually name."""
    backticked = _the_commands_the_cure_backticks(quoting)
    assert named in backticked, (
        f"the {quoting} script runs `{named}`, which the sentences it quotes do not name -- "
        f"so the script is following its author rather than the text. The cure backticks "
        f"{sorted(backticked)!r}; re-read it and run what it says."
    )


def _the_only_command_the_cure_backticks(quoting: str) -> str:
    """The one backticked span of a single-sentence quote, as the script's argument.

    Derivation rather than assertion, where the quote leaves no ambiguity about
    which span is meant: the counterfactual tests run the mode their sentence
    names, so a re-pin that changes that mode changes what they run and they fail
    at the outcome the sentence promises rather than passing on a stale literal.
    """
    backticked = _the_commands_the_cure_backticks(quoting)
    assert len(backticked) == 1, (
        f"the {quoting} quote no longer names exactly one command, so which of "
        f"{sorted(backticked)!r} the script should run is now a choice a human has to make"
    )
    return next(iter(backticked))


def _shell_what_the_cure_backticks(
    named: str, *operands: str, quoting: str
) -> subprocess.CompletedProcess[bytes]:
    """Run ``named`` with ``operands``, where ``named`` is words the cure backticks.

    The verb and its flags come from the sentence; only the path is the test's.
    """
    _assert_the_cure_backticks(named, quoting=quoting)
    return _shell(*named.split(), *operands)


def _the_reader_can_cd_into(directory: Path, *, quoting: str) -> bool:
    """The cure's own locator: can this reader ``cd`` into ``directory``?

    ``cd`` is a shell builtin, so this is the one quoted command whose argv is not
    the backticked words themselves -- there is no ``cd`` executable to hand
    them to. The containment check still binds it, and the path is passed as an
    argument to a fixed one-line script rather than interpolated into it: a cure
    quotes shell commands, and a backtick inside a double-quoted shell word is a
    command substitution.
    """
    _assert_the_cure_backticks("cd", quoting=quoting)
    return _shell("sh", "-c", 'cd "$1"', "sh", str(directory)).returncode == 0


def _the_directories_above_the_reader_cannot_enter(
    data_dir: Path, sandbox_root: Path, *, quoting: str
) -> list[Path]:
    """ "each directory above it you cannot `cd` into", top down, inside the sandbox.

    Bounded to the sandbox because the directories over ``tmp_path`` belong to
    the machine: ``chmod`` on one of them is refused whatever mode is asked for,
    and a script that walked to ``/`` would be measuring the developer's
    filesystem rather than the cure. They are traversable already, necessarily --
    the fixture wrote a registry through them.

    Top down, which is the order the sentence gives and the order the refusal
    needs: ``chmod`` on a directory whose own parent denies the search is refused
    too.
    """
    inside_the_sandbox = [
        above
        for above in reversed(data_dir.parents)
        if above == sandbox_root or sandbox_root in above.parents
    ]
    return [
        above for above in inside_the_sandbox if not _the_reader_can_cd_into(above, quoting=quoting)
    ]


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


def _the_id_flag_the_cure_backticks(quoting: str) -> str:
    """The recovery's flag, read out of the tail's own backticks.

    The tail spells it ``--project-id <its projectId>``: an option and a
    placeholder for the value the reader carried across. Only the option is a
    command word, so the placeholder is dropped and the value comes from what the
    reader read out of the file.
    """
    options = sorted(
        span for span in _the_commands_the_cure_backticks(quoting) if span.startswith("--")
    )
    assert len(options) == 1, (
        f"the {quoting} quote must name exactly one option for the recovery to pass, or the "
        f"script is choosing between {options!r} on its own"
    )
    return options[0].split()[0]


def _assert_the_reader_is_out(sandbox: _ARegisteredProject, *, quoting: str) -> None:
    """Recovery, measured on all three halves of what the cure promises.

    The file reads again, the invocation the cure ends with succeeds, and the
    registration comes back **under the id it had**. Any one alone would pass in a
    state the reader would still call broken: a registry that reads as ``{}`` has
    lost the registration, a ``register`` that reports success without the entry
    landing is the same loss one step later, and an entry that lands under a
    re-derived id is the SEC-13 misrouting the tail's ``--project-id`` clause
    exists to prevent -- ``theurian project register`` with no flag derives the id
    from the directory name, so a project registered under any other id comes back
    addressed differently and every agent naming the old id resolves nothing.

    The invocation is assembled from what the cure backticks, not typed here: the
    command, the flag, and the working directory ("running it inside that
    rootPath") are the three things the sentence prescribes, and the fixture has
    already made ``rootPath`` the process's directory.
    """
    assert ProjectRegistry(path=sandbox.registry).load() == {}, (
        "following the cure removes the file, so the read that used to refuse now answers "
        "with an empty registry rather than raising"
    )

    _assert_the_cure_backticks("theurian project register", quoting=quoting)
    assert Path.cwd().resolve() == sandbox.root.resolve(), (
        "'running it inside that rootPath' is half of the instruction, so the re-registration "
        "has to happen with the reader's working directory in the root they read out"
    )
    code, payload = _invoke(
        "project", "register", _the_id_flag_the_cure_backticks(quoting), sandbox.project_id
    )

    assert code == 0, f"the invocation the cure ends with must succeed: {payload!r}"
    entries = ProjectRegistry(path=sandbox.registry).load()
    assert {pid: Path(entry["rootPath"]).resolve() for pid, entry in entries.items()} == {
        sandbox.project_id: sandbox.root.resolve()
    }, "and the registration the reader lost is back, under the id it was lost under"


def test_a_reader_of_the_unparsable_arm_recovers_by_following_it(
    sandbox: _ARegisteredProject,
) -> None:
    """The bytes are in front of the reader, and the arm says so -- executed.

    Quoted from the ``unparsable`` cure::

        Inspect {path} before removing it -- the ids and roots it lists are
        legible by eye even where its top level is not something this build can
        read.
        ...
        So read out every entry's projectId (the key it sits under) and its
        rootPath first; then delete it and re-register each project with
        `theurian project register`, passing `--project-id <its projectId>` and
        running it inside that rootPath.

    Three steps, in that order: read the raw bytes and check that the ids *and*
    the roots really are legible in them, remove the file, re-register under the
    id that was read out. The first is the one a shape check cannot make --
    "legible by eye" is a claim about this condition, and a truncated registry is
    where it has to hold. The id half of that claim is what the recovery consumes:
    a reader who can see only the roots cannot pass ``--project-id``.
    """
    truncated = f'{{"{sandbox.project_id}": {{"rootPath": "{sandbox.root}"'
    sandbox.registry.write_text(truncated, encoding="utf-8")

    failure = _the_refusal(sandbox.registry)
    _assert_the_arm_is(failure, RegistryFailureArm.UNPARSABLE, sandbox.registry)
    _assert_this_script_follows_the_cure(
        failure.remedy, quoting="unparsable", path=sandbox.registry
    )

    by_eye = sandbox.registry.read_text(encoding="utf-8")
    assert str(sandbox.root) in by_eye and sandbox.project_id in by_eye, (
        "the arm's own promise: a top level this build cannot read still shows the reader "
        "which ids and which roots to re-register"
    )
    removed = _shell("rm", str(sandbox.registry))
    assert removed.returncode == 0, f"the deletion the cure offers must run: {removed.stderr!r}"

    _assert_the_reader_is_out(sandbox, quoting="unparsable")


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
        So read out every entry's projectId (the key it sits under) and its
        rootPath first; then delete it and re-register each project with
        `theurian project register`, passing `--project-id <its projectId>` and
        running it inside that rootPath.

    Four steps: the ``chmod`` the text names, the read it says the ``chmod``
    makes possible, the deletion, the re-registration. The reason this arm's
    ``chmod`` is read-only and the directory arm's is not is measured in
    ``test_project_registry_errors.py`` -- unlinking needs the bits on the
    directory, so a mode-``000`` file inside a traversable parent is removable
    exactly as the tail offers, and this test's ``rm`` is that measurement
    arriving through the cure.

    The mode is not typed here: ``u+r`` is asserted to be the mode this arm's own
    sentence backticks before it is run. Round three's ``m8`` rewrote it to
    ``u+w`` in production and re-pinned every transcription in lockstep, and this
    script's independent ``u+r`` kept the test green over a cure whose own next
    sentence had become impossible.
    """
    sandbox.registry.chmod(0o000)
    try:
        failure = _the_refusal(sandbox.registry)
        _assert_the_arm_is(failure, RegistryFailureArm.FILE_UNREADABLE, sandbox.registry)
        _assert_this_script_follows_the_cure(
            failure.remedy, quoting="file-unreadable", path=sandbox.registry
        )

        restored = _shell_what_the_cure_backticks(
            "chmod u+r", str(sandbox.registry), quoting="file-unreadable"
        )
        assert restored.returncode == 0, f"the cure's own first step: {restored.stderr!r}"

        by_eye = sandbox.registry.read_text(encoding="utf-8")
        assert str(sandbox.root) in by_eye and sandbox.project_id in by_eye, (
            "'Then inspect it.' is only an instruction if the chmod above made the file "
            "readable, and what the recovery needs out of it is the id as well as the root"
        )
        removed = _shell("rm", str(sandbox.registry))
        assert removed.returncode == 0, f"and the deletion must run: {removed.stderr!r}"
    finally:
        if sandbox.registry.exists():
            sandbox.registry.chmod(0o600)

    _assert_the_reader_is_out(sandbox, quoting="file-unreadable")


def _follow_the_directory_arm(sandbox: _ARegisteredProject) -> None:
    """The ``directory-unreadable`` arm's steps, top down, exactly as written.

    Quoted from the ``directory-unreadable`` cure::

        Restore access to {parent} first, working from the top down: `chmod u+rx`
        on each directory above it you cannot `cd` into, then `chmod u+rwx` on
        {parent} itself.
        ...
        Then inspect it.
        ...
        So read out every entry's projectId (the key it sits under) and its
        rootPath first; then delete it and re-register each project with
        `theurian project register`, passing `--project-id <its projectId>` and
        running it inside that rootPath.

    Shared by both of the arm's members, because the whole claim under test is
    that one text serves both: the refusal is at the data directory or over it,
    the kernel names no component, and the cure has to be runnable either way.
    Each caller plants its own member and asserts what the locator should find
    there; the steps below never learn which one they are in.

    Six steps: locate the directories the reader cannot ``cd`` into, ``u+rx``
    each, ``u+rwx`` the data directory, read, delete, re-register. At
    ``2d1f60c2`` the data directory's own mode was ``u+rx`` and the deletion after
    it exited 1 -- the reader landed at exactly the refusal this arm was written
    to lift, with ``u+w`` named nowhere. At ``b9e8296b`` the ``u+rwx`` came
    *first*, before any ancestor's, and for the ancestor member it exited 1: the
    arm's opening command was unrunnable for half its readers.

    Both modes are read out of the sentence rather than typed here. Round three's
    ``m3`` reverted this arm to ``u+rx`` in production and re-pinned every
    transcription in lockstep; the script's independent ``u+rwx`` kept it green.
    """
    data_dir = sandbox.registry.parent

    failure = _the_refusal(sandbox.registry)
    _assert_the_arm_is(failure, RegistryFailureArm.DIRECTORY_UNREADABLE, sandbox.registry)
    _assert_this_script_follows_the_cure(
        failure.remedy, quoting="directory-unreadable", path=sandbox.registry
    )

    for above in _the_directories_above_the_reader_cannot_enter(
        data_dir, sandbox.sandbox_root, quoting="directory-unreadable"
    ):
        ancestor = _shell_what_the_cure_backticks(
            "chmod u+rx", str(above), quoting="directory-unreadable"
        )
        assert ancestor.returncode == 0, (
            f"'`chmod u+rx` on each directory above it you cannot `cd` into' has to be "
            f"runnable on every one of them, and top down is the order that makes it so: "
            f"{above} said {ancestor.stderr!r}"
        )

    restored = _shell_what_the_cure_backticks(
        "chmod u+rwx", str(data_dir), quoting="directory-unreadable"
    )
    assert restored.returncode == 0, (
        f"the data directory's own mode comes after the ancestors', and by then nothing above "
        f"it may still deny the search: {restored.stderr!r}"
    )

    by_eye = sandbox.registry.read_text(encoding="utf-8")
    assert str(sandbox.root) in by_eye and sandbox.project_id in by_eye, (
        "'Then inspect it.' has to be possible by then, and the recovery needs the id out of "
        "it as well as the root"
    )
    removed = _shell("rm", str(sandbox.registry))
    assert removed.returncode == 0, (
        f"this is the step `u+rx` left refused: the arm's own chmod has to grant what the "
        f"deletion needs, not only what the read needs: {removed.stderr!r}"
    )

    _assert_the_reader_is_out(sandbox, quoting="directory-unreadable")


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_reader_whose_data_directory_is_unreadable_recovers_by_following_the_arm(
    sandbox: _ARegisteredProject,
) -> None:
    """The arm's first member: the data directory itself at mode ``000``.

    The locator finds nothing to do here, and that is the assertion. Every
    directory over the data directory is traversable -- necessarily, since the
    fixture wrote a registry through them -- so "each directory above it you
    cannot `cd` into" is the empty set, and a script that ``chmod``-ed them anyway
    would report the clause as executed while executing nothing. That is what the
    loop did at ``b9e8296b``: it ran ``chmod u+rx`` over ancestors nothing had
    refused, which succeeds whatever the cure says, and left the arm's other
    member unplanted and unmeasured.
    """
    data_dir = sandbox.registry.parent

    data_dir.chmod(0o000)
    try:
        assert not _the_directories_above_the_reader_cannot_enter(
            data_dir, sandbox.sandbox_root, quoting="directory-unreadable"
        ), (
            "for this member the refusal is the data directory's own, so the reader can `cd` "
            "into every directory above it and the first clause has nothing to restore"
        )

        _follow_the_directory_arm(sandbox)
    finally:
        data_dir.chmod(0o700)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_reader_whose_data_directorys_ancestor_is_unreadable_recovers_by_following_the_arm(
    sandbox: _ARegisteredProject,
) -> None:
    """The arm's second member, which round three found unserved by its own text.

    The data directory is at ``0700`` and a directory *above* it is at ``000``.
    That reader reaches this same arm -- the ``OSError`` is ``EACCES`` with the
    whole registry path as its ``filename`` and no component named, so nothing at
    the raise can tell the two members apart -- and at ``b9e8296b`` the text
    handed them a cause that was false ("could not look inside that directory",
    when the directory was fine) and an opening command that exits 1.

    What this test adds over its sibling is a locator with something to find: the
    planted ancestor is the one directory the reader cannot ``cd`` into, so the
    first clause restores something real and the ``u+rwx`` after it is reachable.
    Asserted as an equality against the plant, not as "at least one" -- a locator
    that answered with every ancestor would also be non-empty, and would be
    ``chmod``-ing directories nothing refused.
    """
    data_dir = sandbox.registry.parent
    ancestor = data_dir.parent
    assert ancestor != sandbox.sandbox_root and sandbox.sandbox_root in ancestor.parents, (
        "the plant has to be a directory the sandbox owns and the data directory sits under, "
        "or this is testing the machine's own filesystem"
    )

    ancestor.chmod(0o000)
    try:
        assert _the_directories_above_the_reader_cannot_enter(
            data_dir, sandbox.sandbox_root, quoting="directory-unreadable"
        ) == [ancestor], (
            "the cure's locator must name the planted refuser and nothing else -- it is what "
            "decides which directories the reader is told to chmod"
        )

        _follow_the_directory_arm(sandbox)
    finally:
        if ancestor.exists():
            ancestor.chmod(0o700)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_directory_arms_claim_that_the_order_matters_is_true_of_the_filesystem(
    tmp_path: Path,
) -> None:
    """The sentence that makes the arm top-down, executed as its own claim.

    Quoted from the ``directory-unreadable`` cure::

        The order matters: `chmod` on {parent} is itself refused while a
        directory above it denies the search.

    If that were false the ordering would be an instruction with nothing behind
    it, and the arm could keep the ``b9e8296b`` order that put ``u+rwx`` on the
    data directory first. Driven in its own sandbox because it is the
    counterfactual -- the order the arm no longer prescribes -- and running it
    inside a recovery test would leave that test measuring a failure path.
    """
    data_dir = tmp_path / "above" / "datadir"
    data_dir.mkdir(parents=True, mode=0o700)
    registry = data_dir / "projects.json"
    registry.write_text('{"demo": {"rootPath": "/tmp/demo"}}\n', encoding="utf-8")
    cure = the_pinned_cure(RegistryFailureArm.DIRECTORY_UNREADABLE.value, registry)
    _assert_this_script_follows_the_cure(cure, quoting="directory-unreadable-order", path=registry)
    program = _the_only_command_the_cure_backticks("directory-unreadable-order")

    data_dir.parent.chmod(0o000)
    try:
        out_of_order = _shell(program, "u+rwx", str(data_dir))
        top_down = (
            _shell(program, "u+rx", str(data_dir.parent)),
            _shell(program, "u+rwx", str(data_dir)),
        )
    finally:
        data_dir.parent.chmod(0o700)

    assert out_of_order.returncode != 0, (
        "the data directory's own mode cannot be set through an ancestor that denies the "
        "search; if this ever passes, the arm's top-down order has lost its reason"
    )
    assert b"denied" in out_of_order.stderr.lower(), (
        f"and it has to be refused for the reason the sentence gives rather than for a "
        f"missing file or an unwritable mode: {out_of_order.stderr!r}"
    )
    assert [step.returncode for step in top_down] == [0, 0], (
        f"and the order the arm does prescribe has to run: {[s.stderr for s in top_down]!r}"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_directory_arms_warning_about_u_plus_rx_is_true_of_the_filesystem(
    tmp_path: Path,
) -> None:
    """The sentence that justifies the ``w``, executed as its own claim.

    Quoted from the ``directory-unreadable`` cure::

        removing the file needs the write bit on {parent} as well as the search
        bit, so `u+rx` alone would restore the read and leave the deletion below
        refused.

    That is a statement about POSIX, and the arm above is written on the strength
    of it: if it were false the extra ``w`` would be an instruction with nothing
    behind it. Driven in its own sandbox, because it is the *counterfactual* --
    the mode the arm no longer prescribes -- and running it in the recovery test
    would leave that test measuring a failure path.

    The mode is *derived* from the sentence rather than typed beside it: the
    clause backticks exactly one thing, and that is the mode the counterfactual
    is about. A re-pin that names a different one runs a different ``chmod`` here
    and fails at whichever of the three outcomes below the new mode breaks, which
    is the behaviour the module's opening claims for every re-pinned text.
    ``chmod`` is the program the arm's own first sentence names; only the mode is
    in question here.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    registry = data_dir / "projects.json"
    registry.write_text('{"demo": {"rootPath": "/tmp/demo"}}\n', encoding="utf-8")
    cure = the_pinned_cure(RegistryFailureArm.DIRECTORY_UNREADABLE.value, registry)
    _assert_this_script_follows_the_cure(
        cure, quoting="directory-unreadable-warning", path=registry
    )
    mode = _the_only_command_the_cure_backticks("directory-unreadable-warning")

    data_dir.chmod(0o000)
    try:
        restored = _shell("chmod", mode, str(data_dir))
        read = _shell("cat", str(registry))
        removed = _shell("rm", str(registry))
    finally:
        data_dir.chmod(0o700)

    assert restored.returncode == 0, "the counterfactual mode has to be settable"
    assert read.returncode == 0, f"`{mode}` alone does restore the read, exactly as the arm says"
    assert removed.returncode != 0, (
        f"and `{mode}` does leave the deletion refused, which is why the arm prescribes "
        f"`u+rwx`; if this ever passes, the arm's extra `w` has lost its reason"
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
        ...
        So read out every entry's projectId (the key it sits under) and its
        rootPath first; then delete it and re-register each project with
        `theurian project register`, passing `--project-id <its projectId>` and
        running it inside that rootPath.

    Following it has three parts. **"Read {path}"** is an imperative like any
    other arm's, and it is executed: over a directory the read that succeeds is a
    listing, so ``ls`` is what discharges it. That is a goal-statement
    acceptance, recorded here as explicitly as the ``rm -r`` below -- the
    sentence names a goal ("read this") and not a program, and the program that
    reaches the goal depends on what is at the path. Until round three this step
    was skipped under the justification that the lead "prescribes no operation",
    which its own first word contradicts.

    **The message.** The lead sends the reader there for the cause, so it must
    name the condition -- and it does, as ``Is a directory``.

    **The removal**, which for this condition is ``rm -r`` and not the ``chmod
    u+r`` this reader was handed until the errno split: a reader who ran that
    would have changed a mode that was never the problem and still had a
    directory in the way. The tail is executed here in full, which is why it is
    now quoted here: at ``b9e8296b`` this test carried out a recovery sentence it
    never quoted, so a lockstep re-pin of the tail left it green while its three
    siblings went RED.
    """
    sandbox.registry.unlink()
    sandbox.registry.mkdir()

    failure = _the_refusal(sandbox.registry)
    _assert_the_arm_is(failure, RegistryFailureArm.UNKNOWN, sandbox.registry)
    _assert_this_script_follows_the_cure(failure.remedy, quoting="unknown", path=sandbox.registry)

    listed = _shell("ls", str(sandbox.registry))
    assert listed.returncode == 0, (
        f"'Read {sandbox.registry}' is this lead's first imperative, and for a directory the "
        f"read that succeeds is a listing: {listed.stderr!r}"
    )
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

    _assert_the_reader_is_out(sandbox, quoting="unknown")


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
    that refused. The lead's "Read {path}" cannot be carried out either: the path
    is the thing the filesystem refuses to accept, so there is nothing at it to
    read. What is executed is therefore the refusal, the arm, and that the
    message names the cause the lead points at.

    **So this test quotes the lead alone**, under its own key, while the
    ``EISDIR`` test above quotes the same lead *with* the tail it carries out.
    One key covering both would have this test asserting containment of sentences
    it never runs, which is the decoration the quoting discipline exists to
    prevent.

    Asserted on ``errno`` as well as on the message, so a platform that answered
    some other refusal would say so rather than quietly testing a different
    condition.
    """
    too_long = tmp_path / ("x" * 300) / "projects.json"

    failure = _the_refusal(too_long)
    _assert_the_arm_is(failure, RegistryFailureArm.UNKNOWN, too_long)
    _assert_this_script_follows_the_cure(
        failure.remedy, quoting="unknown-without-the-recovery", path=too_long
    )

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
