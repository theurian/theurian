"""``CommittedMigrationCheck`` compares the loader's bytes against ``HEAD`` (ADR-0034, T-15).

The adapter answers decision 1's predicate for one migration: the file is tracked
by git *and* the bytes the engine will apply hash -- under the path's
gitattributes -- to the object id git recorded at ``HEAD``. The verdict is a
git-blob-id comparison (``rev-parse`` for the committed id, ``hash-object
--path=`` for the applied id), so git's own normalization is on both sides of it.
Three properties are held here, at the adapter (and, for the first, the loader)
seam rather than through the CLI, because each is easier to demonstrate isolated:

* **the comparison is against the loader's bytes, not a second read of the
  working tree** (decision 1's race-closure). The CLI cannot stage "the file is
  replaced *between* the load and the check" -- the load happens in
  ``resolve_context`` and the check immediately after, with no injection point --
  so that direction is driven here, where the load and the check are separate
  calls;
* **the git query does not trust its input**: a ``source_path`` carrying a ``:``,
  a leading ``../`` or a newline resolves to the file it names or reads
  ``NOT_TRACKED`` -- never a different revision;
* **the spawn site is bounded and fixed** (decision 4): each argument vector is
  built by the adapter, cannot be handed a URL or a remote, and carries a timeout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin

from theurian.cli.context import schema_root
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.git.committed_check import (
    GIT_TIMEOUT_SECONDS,
    CommittedMigrationCheck,
    HeadComparison,
)

pytestmark = pytest.mark.integration

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
MIGRATION_RELPATH: Final = f".theurian/migrations/{MIGRATION_ID}-auth.yaml"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
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
          sourceUri: git://demo/auth-policy.md
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


def _repo(tmp_path: Path) -> Path:
    """A git repo carrying the migration and its body, both committed at ``HEAD``."""
    root = tmp_path / "repo"
    (root / ".theurian" / "migrations").mkdir(parents=True)
    (root / ".theurian" / "knowledge" / "architecture").mkdir(parents=True)
    (root / ".theurian" / "knowledge" / "architecture" / "auth-policy.md").write_text(BODY)
    (root / MIGRATION_RELPATH).write_text(MIGRATION)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add migration")
    return root


def _loaded_migration(root: Path) -> tuple[str, bytes]:
    """The one migration's ``(source_path, source_bytes)`` as the loader produced them.

    ``source_bytes`` is exactly the bytes the loader read -- the bytes the engine
    will apply -- which the committed-check hashes with ``git hash-object``, by
    construction, rather than re-reading the file. Feeding these to the check is
    what closes the check-to-load race (decision 1).
    """
    loaded = load_migrations(root, root / ".theurian" / "migrations", schema_root())
    (migration,) = loaded.migration_set.migrations
    assert migration.source_path is not None
    assert migration.source_bytes is not None
    return migration.source_path, migration.source_bytes


# -- decision 1: the comparison is against the loader's bytes ---------------------


def test_a_working_tree_edit_after_the_load_does_not_change_the_verdict(tmp_path: Path) -> None:
    """The check trusts the loader's bytes, so a post-load edit cannot fool it (decision 1).

    This is the race-closure property, stated positively. The migration is
    committed unmodified, so the loader's ``source_bytes`` hash -- via
    ``hash-object`` -- to the ``HEAD`` blob's id. Replacing the working-tree file
    *after* that load -- as an untrusted same-UID process would, between a naive
    check and the engine's read -- changes nothing: the check hashes the loader's
    bytes, not the tampered file on disk, so it still reads ``COMMITTED`` and the
    bytes that apply are the reviewed ones.

    RED if the adapter re-reads the working tree instead of hashing the passed
    ``source_bytes``: it would then read ``MODIFIED`` and refuse a migration whose
    approved bytes are exactly what the engine holds.
    """
    root = _repo(tmp_path)
    source_path, source_bytes = _loaded_migration(root)
    # The attacker replaces the file on disk after the loader has already read it.
    (root / source_path).write_text(MIGRATION + "# tampered after the load\n")

    verdict = CommittedMigrationCheck(root).compare_to_head(source_path, source_bytes)

    assert verdict is HeadComparison.COMMITTED, (
        "the check must hash the loader's bytes, not re-read the working tree -- a "
        "post-load edit is the race decision 1 closes"
    )


def test_an_edit_before_the_load_is_seen_as_modified(tmp_path: Path) -> None:
    """The inverse: bytes changed *before* the load are the bytes the check refuses.

    Committed, then edited, then loaded: the loader reads the tampered working-tree
    bytes, and the check finds they hash to a different id than ``HEAD`` -- the
    committed-then-edited predicate (decision 1's third row) at the seam. RED if the
    predicate accepts ``MODIFIED`` (the *tracked-only* drift), or if the check
    ignores the loader's bytes.
    """
    root = _repo(tmp_path)
    (root / MIGRATION_RELPATH).write_text(MIGRATION + "# edited before the load\n")
    source_path, source_bytes = _loaded_migration(root)

    verdict = CommittedMigrationCheck(root).compare_to_head(source_path, source_bytes)

    assert verdict is HeadComparison.MODIFIED, (
        "a file whose working-tree bytes differ from HEAD before the load must read "
        "MODIFIED -- the loader read the tampered bytes, and they are not the "
        "approved ones"
    )


# -- ADR still-owed: the git query does not trust its input -----------------------


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param("other:secret.yaml", id="colon-naming-a-ref"),
        pytest.param("../outside.yaml", id="leading-dotdot"),
        pytest.param("with\nnewline.yaml", id="embedded-newline"),
        pytest.param("--output=/tmp/x", id="option-shaped"),
    ],
)
def test_an_untrusted_filename_never_resolves_to_a_different_revision(
    tmp_path: Path, hostile: str
) -> None:
    """A migration filename is a path, and the ``HEAD:<path>`` form keeps it one.

    The adapter builds ``git rev-parse --verify --quiet HEAD:<source_path>``.
    Because the object argument begins with the literal ``HEAD:``, git splits the
    tree-ish on its *first* colon: the revision is always ``HEAD``, so a filename
    shaped like ``other:secret.yaml`` names the path ``other:secret.yaml`` *under*
    ``HEAD`` -- which does not exist -- rather than the ``secret.yaml`` committed on
    the ``other`` branch. A ``../`` path, a newline-bearing path and an
    option-shaped name all name nothing at ``HEAD`` and fail closed to
    ``NOT_TRACKED``.

    The repo below plants ``secret.yaml`` on a branch that is **not** ``HEAD``, with
    known bytes, so "resolved a different revision" is a concrete, checkable event:
    it would return ``COMMITTED`` against that branch's ``secret.yaml``. RED if the
    adapter drops the ``HEAD:`` prefix or otherwise lets ``source_path`` choose the
    revision.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "benign.yaml").write_text("benign\n")
    _git(root, "add", "benign.yaml")
    _git(root, "commit", "-q", "-m", "main")
    # A different ref carries `secret.yaml`; it is never merged into HEAD.
    _git(root, "checkout", "-q", "-b", "other")
    secret_bytes = b"secret-on-another-branch\n"
    (root / "secret.yaml").write_bytes(secret_bytes)
    _git(root, "add", "secret.yaml")
    _git(root, "commit", "-q", "-m", "secret")
    _git(root, "checkout", "-q", "main")

    verdict = CommittedMigrationCheck(root).compare_to_head(hostile, secret_bytes)

    assert verdict is HeadComparison.NOT_TRACKED, (
        f"a filename {hostile!r} resolved to something other than NOT_TRACKED; the "
        f"HEAD:<path> form must keep it a path at HEAD, never a different revision"
    )


# -- decision 4: the spawn site is bounded and its vector is fixed ----------------


def test_the_git_vectors_are_fixed_and_carry_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both new spawn vectors are the adapter's, bounded, and foreclose an option (decision 4).

    ``PROCESS_SPAWN_SITES`` records this site, and the checklist that file states
    binds it: each vector is fixed by the adapter rather than taken from a document,
    a config, a URL or a remote, and each carries a timeout. The verdict is now a
    blob-id comparison, so the adapter spawns two calls -- the committed id from
    ``rev-parse`` and the applied id from ``hash-object`` -- and both are captured
    and asserted here.

    - each executable is an **absolute path** (resolved once, the ``gh`` tier, so the
      environment does not choose it), and neither call reaches a shell;
    - the committed vector is exactly ``git rev-parse --verify --quiet
      HEAD:<source_path>``; its object argument **begins with the literal ``HEAD:``**,
      so the revision is pinned and ``source_path`` can name neither an option, a URL
      nor a revision;
    - the applied vector is exactly ``git hash-object --stdin --path=<source_path>``;
      its path is the **``--path=<value>`` form** (one token, an ``=`` inside it), so
      an option-shaped filename cannot be read as a flag, and the loader's bytes are
      fed on **stdin** -- no second read of the working tree;
    - each call passes a ``timeout``, and it is :data:`GIT_TIMEOUT_SECONDS`.

    RED if a timeout is dropped, if the revision becomes a parameter (the ``rev-parse``
    object argument stops beginning with ``HEAD:``), if the ``--path=`` guard is
    dropped (the ``hash-object`` path stops carrying an ``=``), or if a call reaches
    a shell.
    """
    calls: list[dict[str, Any]] = []
    fake_id = "a" * 40

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args, returncode=0, stdout=f"{fake_id}\n".encode(), stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    source_path = ".theurian/migrations/01K1AAAAAA01234567890ABCDE-auth.yaml"
    source_bytes = b"the migration file's committed bytes\n"
    CommittedMigrationCheck(tmp_path).compare_to_head(source_path, source_bytes)

    assert len(calls) == 2, f"expected a rev-parse then a hash-object, got {calls!r}"
    for call in calls:
        binary = call["args"][0]
        assert Path(binary).is_absolute(), f"the git binary is not an absolute path: {binary!r}"
        assert Path(binary).name in {"git", "git.exe"}, call["args"]
        assert call["kwargs"].get("timeout") == GIT_TIMEOUT_SECONDS, (
            f"each git call must carry a timeout of {GIT_TIMEOUT_SECONDS}s (SEC-19); "
            f"got {call['kwargs'].get('timeout')!r}"
        )
        assert call["kwargs"].get("shell", False) is False, "no git call may reach a shell"

    rev_parse, hash_object = calls[0]["args"], calls[1]["args"]

    assert rev_parse[1:] == ["rev-parse", "--verify", "--quiet", f"HEAD:{source_path}"], rev_parse
    assert rev_parse[-1].startswith("HEAD:"), (
        "the rev-parse object argument must begin with the literal HEAD:, so the "
        "revision is pinned and source_path cannot name an option or a different revision"
    )
    assert calls[0]["kwargs"].get("input") is None, (
        "rev-parse takes no stdin -- it reads only the committed id, never the blob "
        "into this process's memory (the security MEDIUM)"
    )

    assert hash_object[1:] == ["hash-object", "--stdin", f"--path={source_path}"], hash_object
    path_token = hash_object[-1]
    assert path_token.startswith("--path=") and "=" in path_token, (
        "the hash-object path must be the --path=<value> form (an = inside the one "
        "token), so an option-shaped filename cannot be read as a flag"
    )
    assert calls[1]["kwargs"].get("input") == source_bytes, (
        "the applied bytes must be fed to hash-object on stdin -- the loader's read, "
        "not a second read of the working tree (decision 1's race-closure)"
    )
