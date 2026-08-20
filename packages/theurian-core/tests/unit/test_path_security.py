"""Path containment and input limits (SEC-7, SEC-8, T-4, T-5).

Migration files come from a repository, and a repository can be written by
anyone. These are the tests that decide whether a crafted `contentFile` can read
``~/.ssh/id_ed25519``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from theurian.domain.errors import InputTooLargeError, PathEscapeError
from theurian.security.paths import (
    MAX_SOURCE_FILE_BYTES,
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


def test_error_does_not_echo_the_attacker_supplied_path(project_root: Path) -> None:
    """Reflecting attacker-controlled text into a message is its own problem.

    The remedy is checked alongside the message (issue #233): it is the second
    half of the same user-facing payload -- `_fail` prints both -- so a remedy
    that named the offending path would defeat this guard while the message
    still passed it.
    """
    with pytest.raises(PathEscapeError) as exc:
        resolve_within_root(project_root, "../outside/id_ed25519")
    assert "id_ed25519" not in str(exc.value)
    assert "id_ed25519" not in exc.value.remedy


def test_the_refusal_carries_a_remedy_that_does_not_name_the_absolute_root(
    project_root: Path,
) -> None:
    """Issue #233: `PathEscapeError` set no ``.remedy`` at all, so the CLI's
    generic fallback -- "Run this inside an initialised Theurian project" --
    was printed to users who were already inside one.

    ``resolve_within_root`` holds no name it may safely print (the test above
    is why), so its remedy names the rule rather than a path. It still has to
    say what to *do*, and it must not fall back to the absolute root the
    message used to carry: every sibling refusal on this load path prints
    paths ``relative_to(project_root)``.
    """
    with pytest.raises(PathEscapeError) as exc:
        resolve_within_root(project_root, "../outside/id_ed25519")

    assert exc.value.entry is None, "this call site has no name it may safely print"
    assert str(exc.value) == "Path escapes the permitted root"
    assert exc.value.remedy == (
        "Keep every referenced path inside the project: remove any `..` that climbs "
        "above it, and repoint or remove any symbolic link that leaves it, then retry."
    )
    assert str(project_root) not in str(exc.value)
    assert str(project_root) not in exc.value.remedy


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
