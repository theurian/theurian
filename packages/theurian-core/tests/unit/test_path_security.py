"""Path containment and input limits (SEC-7, SEC-8, T-4, T-5).

Migration files come from a repository, and a repository can be written by
anyone. These are the tests that decide whether a crafted `contentFile` can read
``~/.ssh/id_ed25519``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from theurian.domain.errors import (
    InputTooLargeError,
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


def test_default_size_limit_is_generous_but_bounded() -> None:
    assert 1024 * 1024 <= MAX_SOURCE_FILE_BYTES <= 64 * 1024 * 1024


def test_missing_file_raises_file_not_found(project_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_source_file(project_root, ".theurian/knowledge/absent.md")


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
