"""Path containment and input limits (SEC-7, SEC-8, T-4, T-5).

Migration files come from a repository, and a repository can be written by
anyone. These are the tests that decide whether a crafted `contentFile` can read
``~/.ssh/id_ed25519``.
"""

from __future__ import annotations

import errno
import os
import socket
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from hang_guard import CAN_INTERRUPT_A_HANG, fails_rather_than_hanging

from theurian.domain.errors import (
    InputTooLargeError,
    IrregularSourceFileError,
    PathDepthExceededError,
    PathEscapeError,
    SecurityError,
)
from theurian.security.paths import (
    MAX_PATH_DEPTH,
    MAX_SOURCE_FILE_BYTES,
    _unbounded_shape,
    assert_no_symlink_escape,
    ensure_private_mode,
    is_world_accessible,
    read_source_file,
    resolve_within_root,
)

#: A FIFO is the shape that blocks, and interrupting the block is what lets a
#: missing guard fail rather than stall the suite (``hang_guard``). Both halves
#: are POSIX, so they are one skip condition rather than two.
_CAN_MAKE_A_BLOCKING_FILE = hasattr(os, "mkfifo") and CAN_INTERRUPT_A_HANG


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A project root with a file inside and a secret alongside but outside."""
    root = tmp_path / "repo"
    (root / ".theurian" / "knowledge").mkdir(parents=True)
    (root / ".theurian" / "knowledge" / "auth.md").write_text("# Auth policy\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "id_ed25519").write_text("PRIVATE KEY MATERIAL")
    return root


# -- Normal operation ------------------------------------------------------


def test_relative_path_inside_root_resolves(project_root: Path) -> None:
    resolved = resolve_within_root(project_root, ".theurian/knowledge/auth.md")
    assert resolved.read_text() == "# Auth policy\n"


def test_not_yet_existing_path_resolves(project_root: Path) -> None:
    """Migrations reference files that a later operation will create."""
    resolved = resolve_within_root(project_root, ".theurian/knowledge/new.md")
    assert resolved.parent.exists()
    assert not resolved.exists()


def test_dot_dot_that_stays_inside_is_allowed(project_root: Path) -> None:
    """`contentFile` is written relative to the migration file, so a leading
    `..` is the normal case rather than an attack."""
    resolved = resolve_within_root(project_root, ".theurian/migrations/../knowledge/auth.md")
    assert resolved == (project_root / ".theurian/knowledge/auth.md").resolve()


# -- T-4: traversal --------------------------------------------------------


@pytest.mark.parametrize(
    "attack",
    [
        "../outside/id_ed25519",
        "../../etc/passwd",
        ".theurian/../../outside/id_ed25519",
        ".theurian/knowledge/../../../outside/id_ed25519",
        "./../../outside/id_ed25519",
    ],
)
def test_traversal_above_root_is_refused(project_root: Path, attack: str) -> None:
    with pytest.raises(PathEscapeError):
        resolve_within_root(project_root, attack)


@pytest.mark.parametrize("attack", ["/etc/passwd", "/var/root/.ssh/id_rsa"])
def test_absolute_paths_are_refused(project_root: Path, attack: str) -> None:
    with pytest.raises(PathEscapeError):
        resolve_within_root(project_root, attack)


def test_excessive_depth_is_refused(project_root: Path) -> None:
    with pytest.raises(PathEscapeError):
        resolve_within_root(project_root, "/".join(["a"] * 40))


#: The marker every attack string below carries. Distinctive enough that its
#: presence anywhere in a refusal is unambiguous, and it is the same name the
#: fixture's out-of-tree secret uses.
_ECHO_MARKER = "id_ed25519"

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def _link_that_leaves_the_root(project_root: Path) -> None:
    """The fixture the symlink-shaped attacks below need."""
    link = project_root / ".theurian" / "knowledge" / f"{_ECHO_MARKER}.md"
    if not link.is_symlink():
        link.symlink_to(project_root.parent / "outside" / _ECHO_MARKER)


def _read_a_fifo_named_like_the_secret(project_root: Path) -> object:
    """The one refusal that fires *after* containment has been proved.

    Its caller string is written unnormalized on purpose: every branch above
    refuses before the path is resolved, so what a raise site holds is the
    caller's own text -- `.theurian/knowledge/../knowledge/...`, not the
    resolved form -- and echoing it hands back both the marker and the traversal
    the caller wrote.

    The filename is *not* `_link_that_leaves_the_root`'s: that entry is a
    symlink pointing outside, so reusing the name would refuse this call as an
    escape and pass this test without ever reaching the branch it exists to
    drive (measured, on the first attempt at this fixture).
    """
    relative = f".theurian/knowledge/../knowledge/pipe-{_ECHO_MARKER}.md"
    fifo = project_root / ".theurian" / "knowledge" / f"pipe-{_ECHO_MARKER}.md"
    if not fifo.exists():
        os.mkfifo(fifo)
    with fails_rather_than_hanging(5, waiting_for="read_source_file on a FIFO"):
        return read_source_file(project_root, relative)


def _read_a_file_past_the_size_cap(project_root: Path) -> object:
    """The size branch, driven without writing 8 MiB.

    ``truncate`` sets ``st_size`` without allocating content, and ``st_size`` is
    exactly what the cap reads -- so this refusal fires, as it does in
    production, before a byte is read. Its filename avoids
    `_link_that_leaves_the_root`'s for the reason recorded above, and here the
    collision would have been worse than a false pass: opening that name for
    writing follows the link and truncates the out-of-tree secret itself.
    """
    big = project_root / ".theurian" / "knowledge" / f"big-{_ECHO_MARKER}.md"
    with big.open("wb") as handle:
        handle.truncate(MAX_SOURCE_FILE_BYTES + 1)
    return read_source_file(project_root, f".theurian/knowledge/big-{_ECHO_MARKER}.md")


#: One entry per *reachable* raise site in ``security/paths.py``, each driven
#: with a path carrying `_ECHO_MARKER`. Three live in ``resolve_within_root``
#: (absolute, depth, resolves-outside); the fourth is
#: ``assert_no_symlink_escape``'s ``except ValueError``, which no
#: ``read_source_file`` call can reach -- ``resolve_within_root`` runs first and
#: has already proved containment -- so it is driven directly. The last two are
#: ``read_source_file``'s own: the irregular-shape refusal and the size cap,
#: which are the two that fire *after* containment holds and so are the two
#: whose caller string is still unnormalized when they raise.
#:
#: The population is every raise site in the module, not the escape family:
#: keying it on which *error type* carries a path would have let a branch join
#: the module by carrying one, which is exactly how the FIFO refusal (#215)
#: entered echoing its caller's path while this list still read as complete.
#:
#: Two raise sites are deliberately absent, and this list is not a claim to
#: cover them. ``assert_no_symlink_escape``'s in-loop check walks
#: ``target.resolve().relative_to(root)``, whose components are by construction
#: already symlink-free, so no component it visits can be a link absent a
#: concurrent replacement mid-loop; whether that guard should exist at all is
#: issue #288's question, not this test's. ``read_source_file``'s post-read size
#: re-check needs the file to grow between the ``stat`` and the ``read``, which
#: no single-threaded driver can arrange -- and it raises the same
#: ``InputTooLargeError``, from the same two constants, as the pre-read cap
#: below it.
_ECHO_ATTACKS = [
    pytest.param(
        lambda root: read_source_file(root, f"../outside/{_ECHO_MARKER}"),
        id="dotdot-climbs-above-the-root",
    ),
    pytest.param(
        lambda root: read_source_file(root, f"/etc/{_ECHO_MARKER}"),
        id="absolute-path",
    ),
    pytest.param(
        lambda root: read_source_file(root, "/".join(["deep"] * 40) + f"/{_ECHO_MARKER}"),
        id="past-the-depth-limit",
    ),
    pytest.param(
        lambda root: read_source_file(root, f".theurian/knowledge/{_ECHO_MARKER}.md"),
        id="symlink-target-leaves-the-root",
        marks=_NEEDS_SYMLINKS,
    ),
    pytest.param(
        lambda root: assert_no_symlink_escape(root, root.parent / "outside" / _ECHO_MARKER),
        id="assert-no-symlink-escape-target-outside",
    ),
    pytest.param(
        _read_a_fifo_named_like_the_secret,
        id="a-file-whose-size-bounds-nothing",
        marks=pytest.mark.skipif(
            not _CAN_MAKE_A_BLOCKING_FILE, reason="needs os.mkfifo and an interruptible timer"
        ),
    ),
    pytest.param(_read_a_file_past_the_size_cap, id="past-the-size-cap"),
]


@pytest.mark.parametrize("attack", _ECHO_ATTACKS)
def test_no_reachable_refusal_branch_echoes_the_attacker_supplied_path(
    project_root: Path, attack: Callable[[Path], object]
) -> None:
    """Reflecting attacker-controlled text into a message is its own problem.

    Parametrized over every reachable raise site rather than one of them: a
    single-case version of this test let a change adding the offending path to
    the absolute-path and depth branches pass. The remedy is checked alongside
    the message (issue #233) -- it is the second half of the same user-facing
    payload, `_fail` prints both, so a remedy naming the path would defeat this
    guard while the message still passed it.

    Caught as `SecurityError` rather than `PathEscapeError`: the invariant is
    about the module's refusals, not about one family of them, and the branch
    that broke it (#215's irregular-shape refusal) was the first one here that
    is not an escape at all. What each branch publishes is pinned separately,
    below and in the per-shape tests further down.
    """
    _link_that_leaves_the_root(project_root)

    with pytest.raises(SecurityError) as exc:
        attack(project_root)

    assert _ECHO_MARKER not in str(exc.value)
    assert _ECHO_MARKER not in exc.value.remedy


#: What each reachable raise site publishes. Split per branch because the depth
#: refusal is *not* an escape: a path can nest past the limit without ever
#: leaving the root, and "Path escapes the permitted root" was false for it.
_ESCAPE_MESSAGE = "Path escapes the permitted root"
_ESCAPE_REMEDY = (
    "Keep the referenced path inside the permitted root: remove any `..` that "
    "climbs above the root, remove any absolute prefix, and check whatever it "
    "traverses for a symbolic link that leaves the root, then retry."
)
_DEPTH_MESSAGE = f"Path exceeds the permitted depth limit of {MAX_PATH_DEPTH} segments"
_DEPTH_REMEDY = (
    f"This path nests more than {MAX_PATH_DEPTH} path segments below the permitted "
    f"root. Shorten it -- flatten the directories it nests through -- then retry."
)


@pytest.mark.parametrize(
    ("attack", "expected_message", "expected_remedy"),
    [
        pytest.param(
            lambda root: read_source_file(root, f"../outside/{_ECHO_MARKER}"),
            _ESCAPE_MESSAGE,
            _ESCAPE_REMEDY,
            id="dotdot-climbs-above-the-root",
        ),
        pytest.param(
            lambda root: read_source_file(root, f"/etc/{_ECHO_MARKER}"),
            _ESCAPE_MESSAGE,
            _ESCAPE_REMEDY,
            id="absolute-path",
        ),
        pytest.param(
            lambda root: read_source_file(root, f".theurian/knowledge/{_ECHO_MARKER}.md"),
            _ESCAPE_MESSAGE,
            _ESCAPE_REMEDY,
            id="symlink-target-leaves-the-root",
            marks=_NEEDS_SYMLINKS,
        ),
        pytest.param(
            lambda root: assert_no_symlink_escape(root, root.parent / "outside" / _ECHO_MARKER),
            _ESCAPE_MESSAGE,
            _ESCAPE_REMEDY,
            id="assert-no-symlink-escape-target-outside",
        ),
        pytest.param(
            lambda root: read_source_file(root, "/".join(["deep"] * 40) + f"/{_ECHO_MARKER}"),
            _DEPTH_MESSAGE,
            _DEPTH_REMEDY,
            id="past-the-depth-limit",
        ),
    ],
)
def test_each_reachable_refusal_branch_carries_a_remedy_that_names_no_path(
    project_root: Path,
    attack: Callable[[Path], object],
    expected_message: str,
    expected_remedy: str,
) -> None:
    """Issue #233: `PathEscapeError` set no ``.remedy`` at all, so the CLI's
    generic fallback -- "Run this inside an initialised Theurian project" --
    was printed to users who were already inside one.

    These call sites hold no name they may safely print (the test above is
    why), so the remedy names the rule rather than a path. It covers all four
    mechanisms rather than two, and stays root-agnostic: three raise sites in
    `application/proposal_service.py` protect `.theurian/knowledge` rather than
    the project root, so "keep it inside the project" was advice those callers
    had already followed. It must also not fall back to the absolute root the
    message used to carry -- no sibling refusal on this load path prints an
    absolute path.

    The depth branch is expected separately, and that split is the point: it
    used to publish "Path escapes the permitted root" for a path that never
    left the root, and an earlier commit on this branch pinned that false
    message as correct.
    """
    _link_that_leaves_the_root(project_root)

    with pytest.raises(PathEscapeError) as exc:
        attack(project_root)

    assert exc.value.entry is None, "this call site has no name it may safely print"
    assert str(exc.value) == expected_message
    assert exc.value.remedy == expected_remedy
    assert str(project_root) not in str(exc.value)
    assert str(project_root) not in exc.value.remedy


def test_a_path_that_is_merely_too_deep_is_not_reported_as_an_escape(
    project_root: Path,
) -> None:
    """The depth refusal keeps its own type, so a caller can tell the two apart
    without parsing prose -- and a path nested past the limit while staying
    lexically inside the root is exactly the case the shared message libelled.
    """
    inside_but_deep = "/".join(["deep"] * 40) + "/note.md"

    with pytest.raises(PathDepthExceededError) as exc:
        resolve_within_root(project_root, inside_but_deep)

    assert exc.value.limit == MAX_PATH_DEPTH
    assert "escapes" not in str(exc.value), "this path never left the root"
    assert isinstance(exc.value, PathEscapeError), (
        "every existing `except PathEscapeError` and exit-code route must still catch it"
    )


# -- T-5: symlink escape ---------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_pointing_outside_root_is_refused(project_root: Path) -> None:
    """The check that string prefix matching cannot make.

    `.theurian/knowledge/leak.md` is *lexically* inside the root. Only resolving
    the symlink first reveals that it is not.
    """
    link = project_root / ".theurian" / "knowledge" / "leak.md"
    link.symlink_to(project_root.parent / "outside" / "id_ed25519")

    with pytest.raises(PathEscapeError):
        read_source_file(project_root, ".theurian/knowledge/leak.md")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_directory_pointing_outside_root_is_refused(project_root: Path) -> None:
    """An intermediate component is just as dangerous as the final one."""
    link_dir = project_root / ".theurian" / "escape"
    link_dir.symlink_to(project_root.parent / "outside", target_is_directory=True)

    with pytest.raises(PathEscapeError):
        read_source_file(project_root, ".theurian/escape/id_ed25519")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_staying_inside_root_is_allowed(project_root: Path) -> None:
    """Containment, not a blanket ban on symlinks."""
    link = project_root / ".theurian" / "knowledge" / "alias.md"
    link.symlink_to(project_root / ".theurian" / "knowledge" / "auth.md")
    assert read_source_file(project_root, ".theurian/knowledge/alias.md") == b"# Auth policy\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_root_is_not_a_false_rejection(tmp_path: Path) -> None:
    """A symlinked project root must still work.

    Real cases: `/tmp` is a symlink to `/private/tmp` on macOS, and many people
    keep their repositories under a symlinked home directory. Resolving only the
    candidate and not the root would reject every read for those users.
    """
    real_root = tmp_path / "real"
    (real_root / ".theurian").mkdir(parents=True)
    (real_root / ".theurian" / "a.md").write_text("content")

    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    assert read_source_file(linked_root, ".theurian/a.md") == b"content"


# -- SEC-8: input limits ---------------------------------------------------


def test_oversized_file_is_refused(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("theurian.security.paths.MAX_SOURCE_FILE_BYTES", 16)
    big = project_root / ".theurian" / "knowledge" / "big.md"
    big.write_text("x" * 64)

    with pytest.raises(InputTooLargeError) as exc:
        read_source_file(project_root, ".theurian/knowledge/big.md")
    assert exc.value.limit == 16
    assert exc.value.observed == 64


@pytest.mark.parametrize(
    "limit_name",
    [
        "source file size",
        "YAML document size",
        "projected text size",
        "projected node count",
        "JSON document size",
    ],
    ids=[
        "source-file",
        "yaml-document",
        "projected-text",
        "projected-nodes",
        "json-document",
    ],
)
def test_input_too_large_error_carries_its_own_actionable_remedy(limit_name: str) -> None:
    """Issue #287: ``InputTooLargeError.__init__`` never set ``self.remedy``, so
    every one of its raise sites -- five statements across four modules:
    ``security/paths.py`` (bytes, which raises it twice), ``security/yaml_loading.py``,
    ``normalization/projection.py`` (characters *and* nodes, from one statement),
    and ``parsers/structured.py`` -- left ``TheurianError.remedy``'s empty-string
    default in place. ``cli/commands.py::_context_remedy`` prefers a non-empty
    ``exc.remedy`` over a type-keyed default (checked first, not per type), so an
    empty one here sent an oversized input to a generic fallback that says nothing
    about the size problem at all.

    ``limit_name`` is parametrized over the **five** distinct strings the raise
    sites pass. Statements and strings are different populations and neither is
    inferable from the other, so both are counted rather than reasoned about::

        grep -rn "InputTooLargeError(" packages/theurian-core/src/theurian/

    Five statements (2026-08-24): two in ``security/paths.py`` sharing
    ``"source file size"``, one each in ``security/yaml_loading.py`` and
    ``parsers/structured.py``, and one in ``normalization/projection.py`` that
    re-raises ``_Exhausted.limit_name`` -- so *that* statement carries two
    strings, built at the two ``_Exhausted(...)`` sites in the same file
    (``"projected text size"`` in ``_Spend.emit``, ``"projected node count"`` in
    ``_Spend.visit``, issue #232). The node ceiling was added after this list was
    written and was the string it did not have; the grep above is what settles it
    next time.

    The *unit* varies between them -- bytes for a source file, characters for
    projected text, and now a bare count for projected nodes -- so this pins only
    the wording the remedy must share regardless of unit: that the input is too
    large, and that shrinking or splitting it is the fix. It does not pin a whole
    sentence, which would force one raise site's unit onto every other one.
    """
    error = InputTooLargeError(limit_name, 100, 200)

    assert error.remedy, f"InputTooLargeError({limit_name!r}, ...).remedy must not be empty"
    assert "too large" in error.remedy
    assert "shrink" in error.remedy or "split" in error.remedy, (
        "the remedy must tell the user how to fix it, not only that it failed"
    )


def test_default_size_limit_is_generous_but_bounded() -> None:
    assert 1024 * 1024 <= MAX_SOURCE_FILE_BYTES <= 64 * 1024 * 1024


def test_missing_file_raises_file_not_found(project_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_source_file(project_root, ".theurian/knowledge/absent.md")


# -- SEC-8: a file whose size bounds nothing (issue #215) -------------------


@pytest.mark.skipif(
    not _CAN_MAKE_A_BLOCKING_FILE, reason="needs os.mkfifo and an interruptible timer"
)
def test_a_fifo_is_refused_before_it_is_opened(project_root: Path) -> None:
    """Issue #215: the size cap read ``st_size`` 0 and let the read through.

    A FIFO with no writer blocks in ``open()`` for as long as nobody writes to
    it, so the caller never returns at all -- reproduced against the real CLI as
    ``migrate validate --json`` hanging past an 8-second alarm with an empty
    stdout, and again in this suite by deleting the guard, where this test times
    out instead of passing.

    The timer is what makes this test able to fail rather than hang; the
    assertion is that it is never needed. ``stat`` answers from the directory
    entry without opening anything, which is why a refusal is reachable here at
    all.
    """
    fifo = project_root / ".theurian" / "knowledge" / "pipe.md"
    os.mkfifo(fifo)

    with (
        fails_rather_than_hanging(5, waiting_for="read_source_file on a FIFO"),
        pytest.raises(IrregularSourceFileError) as exc,
    ):
        read_source_file(project_root, ".theurian/knowledge/pipe.md")

    assert exc.value.shape == "a named pipe (FIFO)"
    assert str(exc.value) == "The referenced file is a named pipe (FIFO), not a regular file"
    assert exc.value.remedy == (
        "Replace it with a regular file, then retry. The size Theurian checks before it "
        "opens a file bounds nothing about what a read of a named pipe (FIFO) returns, so "
        "it is refused unread."
    )


def test_a_caller_that_holds_a_safe_name_attaches_it(project_root: Path) -> None:
    """The other half of the no-echo split: anonymous here, named by the caller.

    ``read_source_file`` refuses without a name because its argument is the
    author's, but a caller holding a name it has decided is safe to print -- the
    migration file ``iterdir()`` returned, in ``_parse_upsert`` -- re-raises with
    it attached, so the CLI payload still says which file to open. Pinned here
    rather than only at the CLI so the two halves cannot drift apart.
    """
    named = IrregularSourceFileError("a socket", referrer="01K1-add-auth-policy.yaml")

    assert str(named) == (
        "'01K1-add-auth-policy.yaml' names a file that is a socket, not a regular file"
    )
    assert named.remedy == (
        "Replace the file '01K1-add-auth-policy.yaml' names with a regular file, then "
        "retry. The size Theurian checks before it opens a file bounds nothing about what "
        "a read of a socket returns, so it is refused unread."
    )


@pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX sockets are POSIX here")
def test_a_socket_is_refused_with_its_own_shape(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second member: a socket also reports ``st_size`` 0.

    It does not hang -- ``open()`` on a socket fails at once, measured here as
    ``ENOTSUP`` ("Operation not supported on socket") and documented as
    ``ENXIO`` elsewhere -- so what the guard changes is *which* refusal
    arrives: a named shape and a remedy, rather than a bare ``OSError`` whose
    ``strerror`` describes an operation the reader never asked to perform.

    Bound from inside its own directory because ``sockaddr_un.sun_path`` holds
    barely a hundred bytes, and pytest's ``tmp_path`` alone spends most of them.
    """
    knowledge = project_root / ".theurian" / "knowledge"
    monkeypatch.chdir(knowledge)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind("sock.md")

        with pytest.raises(IrregularSourceFileError) as exc:
            read_source_file(project_root, ".theurian/knowledge/sock.md")

    assert exc.value.shape == "a socket"


def test_a_directory_keeps_the_refusal_that_names_it_a_directory(project_root: Path) -> None:
    """The guard's deliberate non-member, pinned so a widening cannot pass unseen.

    A directory cannot block and cannot stream: ``open()`` refuses it outright
    with ``EISDIR``, and ``domain/errors.py::_read_failure_remedy`` answers that
    errno with a remedy naming the fault exactly ("names a directory, not a
    file"). Folding directories into :class:`IrregularSourceFileError` would
    take that refusal away from the branch that says it best -- and silently,
    because both refusals are refusals.
    """
    with pytest.raises(IsADirectoryError) as exc:
        read_source_file(project_root, ".theurian/knowledge")

    assert exc.value.errno == errno.EISDIR


def test_a_regular_file_is_not_refused(project_root: Path) -> None:
    """The false-refusal side: the shape check must let ordinary content through."""
    assert read_source_file(project_root, ".theurian/knowledge/auth.md") == b"# Auth policy\n"


#: Every file type ``_unbounded_shape`` distinguishes, keyed by ``st_mode``.
#:
#: Written as literal mode bits rather than ``stat`` constants for the two
#: residual rows: ``stat.S_IFDOOR`` is ``0`` on macOS and ``0o150000`` on
#: Solaris, so a table built from it would silently test a different thing on
#: each platform -- and ``0``, which it collapses to here, is a mode no real
#: ``stat`` returns and therefore a fixture that proves nothing about doors.
#: The named types keep their constants, because those are the same everywhere
#: POSIX and naming them is what makes the row readable.
_MODE_SHAPES: list[tuple[int, str | None, str]] = [
    (stat.S_IFREG, None, "regular-file"),
    (stat.S_IFDIR, None, "directory"),
    (stat.S_IFIFO, "a named pipe (FIFO)", "fifo"),
    (stat.S_IFSOCK, "a socket", "socket"),
    (stat.S_IFCHR, "a character device", "character-device"),
    (stat.S_IFBLK, "a block device", "block-device"),
    (0o150000, "a special file", "solaris-door"),
    (0o160000, "a special file", "whiteout-entry"),
    (0, "a special file", "a-type-with-no-constant-here"),
]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(mode, expected) for mode, expected, _ in _MODE_SHAPES],
    ids=[label for _, _, label in _MODE_SHAPES],
)
def test_the_shape_check_names_every_file_type_it_meets(mode: int, expected: str | None) -> None:
    """``_unbounded_shape`` is a pure function of ``st_mode``, tested as one.

    Issue #215's guard is reachable through a real file for only two of these
    rows: a FIFO and a socket are makeable in a test, a character or block device
    needs root, and a Solaris door needs Solaris. So the branches that name the
    rest were carried by nothing at all -- deleting the character-device branch,
    deleting the block-device branch, and turning the residual ``return "a
    special file"`` into ``return None`` each passed the whole suite.

    The residual row is the load-bearing one. ``read_source_file`` refuses on
    ``shape is not None``, so a residual that returns ``None`` does not merely
    lose a name: it lets a type this build has never met through to a read whose
    ``st_size`` bounded nothing, which is the entire fault #215 is about. The
    branch that keeps the check total must not survive its own deletion.

    ``S_IFLNK`` is deliberately not a row: ``read_source_file`` stats the
    *resolved* path, so a symlink's own mode never reaches this function --
    ``resolve_within_root`` and ``assert_no_symlink_escape`` answer that case
    first, and a row here would read as a claim that they do not.
    """
    assert _unbounded_shape(mode) == expected


def test_permission_bits_do_not_change_the_shape() -> None:
    """The type bits are the whole question; the mode's low bits are not part of it.

    A guard written against the whole ``st_mode`` rather than ``S_IFMT`` of it
    would answer differently for the same file type at different permissions,
    which is a refusal that depends on something SEC-8 does not care about.
    """
    assert _unbounded_shape(stat.S_IFIFO | 0o600) == "a named pipe (FIFO)"
    assert _unbounded_shape(stat.S_IFREG | 0o777) is None


# -- SEC-4: credential file permissions ------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_world_accessible_detection(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.write_text("secret")
    os.chmod(private, 0o600)
    assert not is_world_accessible(private)

    exposed = tmp_path / "exposed"
    exposed.write_text("secret")
    os.chmod(exposed, 0o644)
    assert is_world_accessible(exposed)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_ensure_private_mode_reports_whether_it_changed_anything(tmp_path: Path) -> None:
    """Setup reports what it changed rather than fixing things silently (§34)."""
    token = tmp_path / "token"
    token.write_text("secret")
    os.chmod(token, 0o644)

    assert ensure_private_mode(token) is True
    assert (token.stat().st_mode & 0o777) == 0o600
    assert ensure_private_mode(token) is False
