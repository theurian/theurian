"""Path containment and input limits (SEC-7, SEC-8, T-4, T-5).

Migration files come from a repository, and a repository can be written by
anyone. These are the tests that decide whether a crafted `contentFile` can read
``~/.ssh/id_ed25519``.
"""

from __future__ import annotations

import errno
import os
import socket
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
)
from theurian.security.paths import (
    MAX_PATH_DEPTH,
    MAX_SOURCE_FILE_BYTES,
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


#: One entry per *reachable* raise site in ``security/paths.py``, each driven
#: with a path carrying `_ECHO_MARKER`. Three live in ``resolve_within_root``
#: (absolute, depth, resolves-outside); the fourth is
#: ``assert_no_symlink_escape``'s ``except ValueError``, which no
#: ``read_source_file`` call can reach -- ``resolve_within_root`` runs first and
#: has already proved containment -- so it is driven directly.
#:
#: The fifth raise site, ``assert_no_symlink_escape``'s in-loop check, is
#: deliberately absent and this list is not a claim to cover it. That loop walks
#: ``target.resolve().relative_to(root)``, whose components are by construction
#: already symlink-free, so no component it visits can be a link absent a
#: concurrent replacement mid-loop. Whether the guard should exist at all is
#: issue #288's question, not this test's -- hence the "reachable" in the test
#: names below.
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
    """
    _link_that_leaves_the_root(project_root)

    with pytest.raises(PathEscapeError) as exc:
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
    ["source file size", "YAML document size", "projected text size", "JSON document size"],
    ids=["source-file", "yaml-document", "projected-text", "json-document"],
)
def test_input_too_large_error_carries_its_own_actionable_remedy(limit_name: str) -> None:
    """Issue #287: ``InputTooLargeError.__init__`` never set ``self.remedy``, so
    every one of its raise sites -- five statements across four modules:
    ``security/paths.py`` (bytes, which raises it twice), ``security/yaml_loading.py``,
    ``normalization/projection.py`` (characters), and ``parsers/structured.py`` --
    left ``TheurianError.remedy``'s empty-string default in place.
    ``cli/commands.py::_context_remedy`` prefers a non-empty ``exc.remedy`` over a
    type-keyed default (checked first, not per type), so an empty one here sent an
    oversized input to a generic fallback that says nothing about the size problem
    at all.

    ``limit_name`` is parametrized over the four distinct strings the raise sites
    pass (the two in ``security/paths.py`` share ``"source file size"``), and its
    *unit* varies between them (bytes for a source file, characters for projected
    text) -- so this pins only the wording the remedy must share regardless of
    unit: that the input is too large, and that shrinking or splitting it is the
    fix. It does not pin a whole sentence, which would force one raise site's unit
    onto every other one.
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
    assert str(exc.value) == (
        "'.theurian/knowledge/pipe.md' is a named pipe (FIFO), not a regular file"
    )
    assert "Replace" in exc.value.remedy, "a refusal names the command that fixes it"
    assert exc.value.remedy.endswith("refused unread."), exc.value.remedy


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
