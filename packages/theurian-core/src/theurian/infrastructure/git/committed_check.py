"""Ask git whether a migration file is committed unmodified at ``HEAD`` (ADR-0034, T-15).

This adapter answers decision 1's predicate for one migration: **the file is
tracked by git, and its working-tree bytes are identical to the bytes at
``HEAD``.** ``migrate apply`` refuses, by default, a migration that fails it --
the merge is this project's approval model, so a file that was never committed
was never reviewed (ADR-0013 point 4). The floor it enforces is *committed*, not
*merged into a protected branch*; ADR-0034 decision 1 states that limit.

**One query answers both halves of the predicate.** ``git cat-file blob
HEAD:<source_path>`` fails when the path is not in ``HEAD`` -- which *is* the
tracked half, since a staged-but-never-committed file is not at ``HEAD`` -- and
hands back the committed bytes when it is. The predicate is then a digest
comparison: ``ContentHash.of_bytes(blob)`` against the ``migration.checksum`` the
loader already computed over the exact bytes the engine will apply. There is no
second read of the working tree, so the shape that loses the check-to-load race
-- *check the file on disk, then let the engine load it* -- has no window here
(ADR-0034 decision 1's alternatives table).

**The ``HEAD:<path>`` spelling is the input guard, and it is load-bearing.** The
whole object argument begins with the literal ``HEAD:``, so a migration filename
shaped like an option (``--force.yaml``) is read as a path, never as a flag; and
git splits the tree-ish on its *first* colon, so a filename carrying a ``:``
still resolves *under* ``HEAD`` and never names a different revision. A ``../``
path, or one carrying a newline, names nothing in ``HEAD`` and fails closed to
"not committed" rather than escaping the tree. The vector is fixed by this
adapter -- ``git``, ``cat-file``, ``blob`` and one built object argument -- and
takes nothing from a document, a config, a URL or a remote (SEC-9). It reaches no
network, which is the same answer ``trailer_source.py`` gives for its own ``git
log``: it is on ``PROCESS_SPAWN_SITES`` only because it spawns a process.

**The binary is resolved to an absolute path, and this call is the ``gh``
precedent tier, not the bare-``git`` tier** (ADR-0034 decision 4's open choice).
The three ``git`` reads in ``cli/context.py`` collect *descriptive* metadata for
a ``Project`` record and spawn the bare name ``git``, letting the child's ``PATH``
resolve it. This call is different in kind: its result *decides whether an apply
proceeds*, so it follows :func:`~theurian.infrastructure.github.gh_cli.locate_binary`
(ADR-0030 clause 5) and resolves ``git`` through ``shutil.which`` once, at
construction. Letting the environment choose the executable that answers a
security question is exactly the shape clause 5 refuses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Final, final

from theurian.domain.values import ContentHash

#: Timeout on the one ``git cat-file`` this adapter spawns. A single object read
#: from local storage is cheap -- generous even for a migration at the loader's
#: 4 MiB source cap -- and an unbounded subprocess in a CLI a hook may call is a
#: hang the user cannot explain (SEC-19). The sibling ``git`` reads in
#: ``cli/context.py`` bound at the same five seconds for the same reason;
#: ``trailer_source.py``'s 30 is for a full-history ``git log``, not one blob.
GIT_TIMEOUT_SECONDS: Final = 5.0


class HeadComparison(Enum):
    """How a migration file compares to the version committed at ``HEAD``."""

    #: Tracked at ``HEAD``, and its working-tree bytes are identical to the
    #: committed ones -- the one outcome that lets an apply proceed.
    COMMITTED = "committed"
    #: Tracked at ``HEAD``, but its working-tree bytes differ from the committed
    #: ones: committed once and edited since (ADR-0034 decision 1's third row).
    #: The approved bytes are the ones at ``HEAD``, not the ones on disk.
    MODIFIED = "modified"
    #: Not committed at ``HEAD`` at all -- never committed,
    #: staged-but-never-committed, or a path git could not answer for. Fail-closed:
    #: an apply that cannot prove a migration committed refuses it.
    NOT_TRACKED = "not_tracked"


@final
class CommittedMigrationCheck:
    """Answers whether a migration file is committed unmodified at ``HEAD``.

    Built once per apply with the working-tree root; the git binary is resolved
    to an absolute path once at construction (the module docstring says why this
    call resolves the binary rather than leaving it to the child's ``PATH``).
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root
        # Resolved once, here, exactly as `gh_cli.locate_binary` resolves `gh`
        # (ADR-0030 clause 5): the operator's PATH is searched -- the same PATH a
        # child `git` would use -- so the executable that decides whether an apply
        # proceeds is not chosen by an inherited environment. `find_git_root`
        # already ran `git` to build the command context this check runs inside,
        # so on any tree that reaches here `git` resolves; a `None` means it
        # vanished mid-command, which fails closed below (every file reads
        # NOT_TRACKED, and the caller refuses with the escape-hatch remedy).
        found = shutil.which("git", path=os.environ.get("PATH"))
        self._git: Path | None = Path(found).resolve() if found is not None else None

    def compare_to_head(self, source_path: str, checksum: ContentHash) -> HeadComparison:
        """Compare *source_path*'s committed bytes against *checksum*.

        *checksum* is the loader's digest of the exact bytes the engine will apply
        (``Migration.checksum``), so the comparison is against what applies, not a
        second read of the working tree -- which is what closes the check-to-load
        race by construction (ADR-0034 decision 1).
        """
        blob = self._head_blob(source_path)
        if blob is None:
            return HeadComparison.NOT_TRACKED
        if ContentHash.of_bytes(blob) == checksum:
            return HeadComparison.COMMITTED
        return HeadComparison.MODIFIED

    def _head_blob(self, source_path: str) -> bytes | None:
        """The bytes of *source_path* as committed at ``HEAD``, or ``None``.

        ``None`` is every "cannot prove committed" outcome -- the path is not
        tracked at ``HEAD``, git cannot be run, or it times out -- so the caller
        refuses (fail-closed). The module docstring says why the ``HEAD:<path>``
        object argument keeps an option-shaped, colon-bearing, ``../`` or
        newline-bearing filename from naming a flag, escaping the tree, or
        resolving to a different revision.
        """
        if self._git is None:
            return None
        # Fixed vector, no shell. The object argument begins with the literal
        # `HEAD:`, so no element is user-chosen or option-shaped, and git splits
        # the tree-ish on its first colon -- the revision is always `HEAD` (SEC-9).
        args = [str(self._git), "cat-file", "blob", f"HEAD:{source_path}"]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed adapter-controlled vector, no shell; the object arg is `HEAD:<path>` so nothing user-supplied is option-shaped or names a revision (SEC-9)
                args,
                cwd=self._repo_root,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            # Non-zero is `git`'s own "exists on disk, but not in 'HEAD'" (exit
            # 128) and every other read failure -- the tracked half of the
            # predicate, fail-closed.
            return None
        return completed.stdout
