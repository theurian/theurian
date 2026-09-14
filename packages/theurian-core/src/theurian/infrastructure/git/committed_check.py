"""Ask git whether a migration file is committed unmodified at ``HEAD`` (ADR-0034, T-15).

This adapter answers decision 1's predicate for one migration: **the file is
tracked by git, and the bytes the engine will apply are byte-for-byte what git
would store for that path at ``HEAD``.** ``migrate apply`` refuses, by default, a
migration that fails it -- the merge is this project's approval model, so a file
that was never committed was never reviewed (ADR-0013 point 4). The floor it
enforces is *committed*, not *merged into a protected branch*; ADR-0034 decision 1
states that limit.

**The predicate is a git-blob-id comparison, so git's own normalization is on
both sides of it.** An earlier shape hashed the working-tree bytes and compared
that digest to the *stored* blob, which read ``MODIFIED`` for a committed,
git-clean file whenever an ``eol=lf`` attribute or ``core.autocrlf`` (the Windows
default) meant the stored blob differs from the bytes on disk -- refusing every
text migration on Windows by default (round-1 code-review HIGH). The two ids
compared here are:

* the **committed** id -- ``git rev-parse --verify --quiet HEAD:<path>``, which
  is the object id git recorded for that path at ``HEAD``. A path not present at
  ``HEAD`` makes it exit non-zero with no output, which *is* the tracked half of
  the predicate: a staged-but-never-committed file is not at ``HEAD``;
* the **applied** id -- ``git hash-object --stdin --path=<path>`` fed the exact
  bytes the engine will apply. ``--path`` makes git apply the same gitattributes
  (eol conversion, a clean filter) it would apply on commit, so a file that is
  clean under those attributes yields the committed id even when its worktree
  bytes differ from the stored blob. This is the load-bearing flag: a bare
  ``hash-object`` without ``--path`` would hash the raw bytes and reintroduce the
  false ``MODIFIED``.

Both ids are 40 hex characters, so the comparison is bounded and ``rev-parse``
returns only the id -- the ``HEAD`` blob is never read into this process's memory
(round-1 security MEDIUM: the earlier ``cat-file blob`` buffered the whole
committed blob with no cap, while the applied side already capped at
:data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES`).

**The applied id is computed from the loader's already-read bytes, never a second
read of the working tree.** ``compare_to_head`` is handed ``source_bytes`` --
``Migration.source_bytes``, the exact bytes the loader digested into
``Migration.checksum`` -- and feeds them to ``hash-object`` on stdin. So the shape
that loses the check-to-load race -- *check the file on disk, then let the engine
load it* -- has no window here (ADR-0034 decision 1's alternatives table); this is
load-bearing and the reason ``source_bytes`` is threaded rather than re-read.

**Neither git argument is user-chosen, and this is the input guard.** The
``rev-parse`` object argument begins with the literal ``HEAD:``, so a migration
filename shaped like an option (``--force.yaml``) is a path, never a flag; git
splits the tree-ish on its *first* colon, so a ``:``-bearing filename still
resolves *under* ``HEAD`` and never names a different revision; a ``../`` or
newline-bearing path names nothing in ``HEAD`` and fails closed. The
``hash-object`` path is passed as ``--path=<value>`` (the ``=`` form, not a
separate ``--path <value>`` token), so an option-shaped filename is part of the
one token and cannot be read as a flag. The ``<path>`` on both sides is the git
spelling -- forward slashes -- because ``source_path`` is built with ``os.sep``
and a Windows backslash spelling would resolve nothing at ``HEAD`` (round-1
code-review HIGH). The vector is fixed by this adapter -- ``git``, two literal
subcommands and one built argument each -- and takes nothing from a document, a
config, a URL or a remote (SEC-9). It reaches no network, which is the same answer
``trailer_source.py`` gives for its own ``git log``: it is on
``PROCESS_SPAWN_SITES`` only because it spawns a process.

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
from pathlib import Path, PurePath
from typing import Final, final

#: Timeout on each ``git`` call this adapter spawns. A single object read or hash
#: from local storage is cheap -- generous even for a migration at the loader's
#: 8 MiB source cap (:data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES`) -- and
#: an unbounded subprocess in a CLI a hook may call is a hang the user cannot
#: explain (SEC-19). The sibling ``git`` reads in ``cli/context.py`` bound at the
#: same five seconds for the same reason; ``trailer_source.py``'s 30 is for a
#: full-history ``git log``, not one blob.
GIT_TIMEOUT_SECONDS: Final = 5.0


class HeadComparison(Enum):
    """How a migration file compares to the version committed at ``HEAD``."""

    #: Tracked at ``HEAD``, and the bytes the engine will apply hash -- under the
    #: path's gitattributes -- to the id git recorded for it: the one outcome that
    #: lets an apply proceed.
    COMMITTED = "committed"
    #: Tracked at ``HEAD``, but the bytes the engine will apply hash to a different
    #: id than the committed one even after normalization: committed once and
    #: edited since (ADR-0034 decision 1's third row). The approved bytes are the
    #: ones at ``HEAD``, not the ones the loader read.
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

    def compare_to_head(self, source_path: str, source_bytes: bytes) -> HeadComparison:
        """Compare *source_bytes* against ``HEAD``'s version of *source_path*.

        *source_bytes* is the loader's read of the bytes the engine will apply
        (``Migration.source_bytes``, the same bytes ``Migration.checksum`` digests),
        so the comparison is against what applies, not a second read of the working
        tree -- which is what closes the check-to-load race by construction
        (ADR-0034 decision 1).

        The verdict is a blob-id comparison (module docstring): the committed id
        from ``rev-parse`` and the applied id from ``hash-object --path=``. A
        committed id git cannot produce -- the path is not at ``HEAD``, git is
        absent, or a spawn failed -- is the fail-closed ``NOT_TRACKED`` half of the
        predicate. An applied id git cannot produce (the second spawn failed after
        the first succeeded) also fails closed to ``NOT_TRACKED``: the enum's own
        "a path git could not answer for" case, since an unproven apply must refuse.
        """
        posix_source_path = PurePath(source_path).as_posix()
        committed_id = self._committed_blob_id(posix_source_path)
        if committed_id is None:
            return HeadComparison.NOT_TRACKED
        applied_id = self._applied_blob_id(posix_source_path, source_bytes)
        if applied_id is None:
            return HeadComparison.NOT_TRACKED
        if applied_id == committed_id:
            return HeadComparison.COMMITTED
        return HeadComparison.MODIFIED

    def _committed_blob_id(self, posix_source_path: str) -> str | None:
        """The object id git recorded for *posix_source_path* at ``HEAD``, or ``None``.

        ``None`` is every "not committed at ``HEAD``" outcome -- the path is not
        tracked there, git cannot be run, or it times out -- so the caller refuses
        (fail-closed). ``--verify --quiet`` makes ``rev-parse`` print only the id
        and exit zero on success, and exit non-zero with no output when the object
        argument names nothing at ``HEAD``; the object argument begins with the
        literal ``HEAD:`` so nothing user-supplied is option-shaped or names a
        different revision (module docstring).
        """
        completed = self._run(["rev-parse", "--verify", "--quiet", f"HEAD:{posix_source_path}"])
        if completed is None or completed.returncode != 0:
            # Non-zero is `git`'s own "not present at 'HEAD'" and every other read
            # failure -- the tracked half of the predicate, fail-closed.
            return None
        return self._one_object_id(completed.stdout)

    def _applied_blob_id(self, posix_source_path: str, source_bytes: bytes) -> str | None:
        """The id *source_bytes* would have if committed at *posix_source_path*, or ``None``.

        ``--stdin`` feeds the loader's already-read bytes -- no second read of the
        working tree, which is what keeps the check-to-load race closed (module
        docstring). ``--path=<value>`` (the ``=`` form) both selects the
        gitattributes to apply -- so eol conversion and a clean filter match what
        git would store on commit -- and keeps an option-shaped filename inside the
        one token, unable to be read as a flag. ``None`` on a spawn failure fails
        closed.
        """
        completed = self._run(
            ["hash-object", "--stdin", f"--path={posix_source_path}"], stdin_bytes=source_bytes
        )
        if completed is None or completed.returncode != 0:
            return None
        return self._one_object_id(completed.stdout)

    @staticmethod
    def _one_object_id(stdout: bytes) -> str | None:
        """The single object id in *stdout*, or ``None`` when git printed nothing.

        Both git calls print one 40-hex id and a newline on success; an empty or
        whitespace-only stdout (which ``--quiet`` produces on a miss) is ``None``,
        so a blank read never compares equal to a real id.
        """
        decoded = stdout.decode("ascii", "replace").strip()
        return decoded or None

    def _run(
        self, git_args: list[str], *, stdin_bytes: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes] | None:
        """Spawn one ``git`` call, or ``None`` if the binary is absent or the spawn fails.

        The single spawn site in this module (``PROCESS_SPAWN_SITES`` records the
        module, not the call count). Fixed vector, no shell; the caller builds
        ``git_args`` from a literal subcommand and one argument whose user-supplied
        half is foreclosed at the boundary (``HEAD:<path>`` / ``--path=<path>``).
        """
        if self._git is None:
            return None
        try:
            return subprocess.run(  # noqa: S603 - fixed adapter-controlled vector, no shell; the rev-parse arg begins with `HEAD:` and the hash-object path uses the `--path=<value>` form, so no user-supplied element is option-shaped or names a revision (SEC-9)
                [str(self._git), *git_args],
                cwd=self._repo_root,
                input=stdin_bytes,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
