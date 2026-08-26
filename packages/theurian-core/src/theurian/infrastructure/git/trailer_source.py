"""Read ``Review-Finding:`` trailers from public git history (ADR-0029).

The FR-S1 Git-commit-metadata source, implemented as a :class:`ReviewFindingSource`
adapter. It reads **only** the public default branch: the embargo closure
(ADR-0029 decision 6) rests on that scoping, because embargoed findings live on a
private fork and never reach public ``main``. ``git log`` defaults to the current
branch and reads everything under ``--all``, so the ref is pinned as a constant
here rather than accepted as a parameter -- an adapter that read ``--all`` would
silently ingest fetched private-fork commits and lose the structural protection.

**Public history is a verified authority, not a mutable local name** (ADR-0029
Amendment 1, D7). The short name ``origin/main`` is *not* a safe handle: it is
shadowed by ``refs/heads/origin/main``, ``refs/tags/origin/main`` and the bare
``refs/origin/main`` (gitrevisions(7) tries ``refs/<name>``, ``refs/tags/<name>``
and ``refs/heads/<name>`` before ``refs/remotes/<name>``), and by ``git replace``.
So the source hardens the read against every locally-forgeable channel that could
substitute an embargoed commit for the public tip:

- it reads the **fully-qualified** :data:`PUBLIC_REF`, never the short name, so a
  shadowing branch or tag cannot answer for the remote-tracking ref;
- it disables **object replacement** (``--no-replace-objects`` *and*
  ``GIT_NO_REPLACE_OBJECTS=1``), so a ``git replace`` cannot map the public tip's
  sha onto an author-chosen commit body;
- it runs ``git`` with inherited **``GIT_*`` overrides stripped**
  (:data:`_INHERITED_GIT_OVERRIDES`), so an env-injected ``GIT_DIR``, alternate
  object store, replace-ref base or config file cannot redirect what history means.

Verifying ``remote.origin.url`` against a recorded public origin is a stated
non-goal of this slice, owed to the FR-V serving arm that carries that identity
(ADR-0029 Amendment 1, D7).

``git`` is invoked as an argument vector with ``shell=False`` (SEC-9), and its
output is captured as bytes and decoded UTF-8 explicitly rather than through
``text=True``: the finding text carries an em-dash separator and other non-ASCII,
and decoding under the process locale rather than UTF-8 would corrupt the
byte-preservation the loss-free mapping (AC-1) depends on. ``log.showSignature``
is forced off and optional locks disabled so a repo-level config cannot inject
gpg lines into the parsed stream.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.errors import TheurianError
from theurian.domain.review_finding import (
    TRAILER_KEY,
    ReviewFinding,
    finding_from_trailer,
)

#: The one ref this source reads, fully qualified (ADR-0029 Amendment 1, D7). Not
#: a parameter, and not the short ``origin/main``: see the module docstring -- the
#: embargo closure is the reason the scoping is pinned and the ref hardened rather
#: than trusted. Reading ``refs/remotes/origin/main`` closes the ref-shadowing
#: channel (a local ``refs/heads/origin/main`` resolves ahead of the short name).
PUBLIC_REF: Final = "refs/remotes/origin/main"

#: Inherited ``GIT_*`` environment variables the child ``git`` must not see, so no
#: ambient process state can redirect what "public history" means (ADR-0029
#: Amendment 1, D7). Stripping a variable can only make ``git`` fall back to its
#: default (this repository, its real object store, no injected config) -- never to
#: a wrong answer -- so the list is a defensive superset. It covers three classes:
#: *which repository/objects* answer (``GIT_DIR``, ``GIT_WORK_TREE``,
#: ``GIT_OBJECT_DIRECTORY``, ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_COMMON_DIR``,
#: ``GIT_INDEX_FILE``, ``GIT_CEILING_DIRECTORIES``); *ref resolution and replacement*
#: (``GIT_NAMESPACE``, ``GIT_REPLACE_REF_BASE``); and *config injection*
#: (``GIT_CONFIG`` and the ``GIT_CONFIG_{COUNT,GLOBAL,SYSTEM}`` trio). Defined here
#: rather than imported from ``tools/`` or the test tree (which keep their own copy
#: for a different read) so the adapter owns its own contract.
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_REPLACE_REF_BASE",
        "GIT_WORK_TREE",
    }
)

#: An unbounded ``git log`` over a large history would hang a caller. The bound
#: is generous for a full-history read that is still local and I/O-cheap.
GIT_TIMEOUT_SECONDS: Final = 30.0

#: Record and field separators. ``git log`` emits one record per commit, each
#: opened by RS (0x1e) and its fields joined by US (0x1f); ``%b`` (the body)
#: carries newlines of its own, so a newline-delimited format could not be
#: reassembled. Both are C0 control characters that do not occur in authored
#: commit text, so they partition the stream unambiguously.
_RS: Final = "\x1e"
_US: Final = "\x1f"
_FORMAT: Final = f"format:{_RS}%H{_US}%cI{_US}%s{_US}%b"

#: sha, committer-date, subject, body -- four fields before the body may itself
#: hold a US byte, which is why the body is rejoined rather than indexed.
_MIN_FIELDS: Final = 4


class GitHistoryUnavailableError(TheurianError):
    """``git log`` could not be run, or the public ref does not resolve.

    Distinct from a malformed trailer: nothing about the trailer grammar failed;
    the history the source reads from could not be reached at all. The commonest
    cause on a fresh clone is that ``refs/remotes/origin/main`` has not been
    fetched, so the remedy names the fetch rather than an edit to a trailer.
    """

    def __init__(self, repo_root: Path, reason: str) -> None:
        self.repo_root = repo_root
        self.reason = reason
        self.remedy = (
            f"Ensure {PUBLIC_REF!r} resolves in {str(repo_root)!r} "
            f"(run 'git fetch origin main'), then retry."
        )
        super().__init__(f"Cannot read {PUBLIC_REF} history in {str(repo_root)!r}: {reason}")


@final
class GitTrailerFindingSource:
    """Reads ``Review-Finding:`` trailers from ``origin/main`` into findings.

    Satisfies :class:`~theurian.domain.ports.ReviewFindingSource` structurally.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def load_findings(self) -> tuple[ReviewFinding, ...]:
        """Every ``Review-Finding:`` trailer on ``origin/main``, in a total order.

        A keyed line becomes a record or the load fails; there is no silent drop,
        so the mapping is loss-free (AC-1). The order is a total sort key --
        ``(commit date, commit sha, position within the commit)`` -- so two runs
        over the same history produce a byte-identical sequence (AC-6): the commit
        sha alone is already total (a position disambiguates trailers sharing a
        commit), and the date leads it only to make the order chronological.

        Raises:
            GitHistoryUnavailableError: If ``git`` cannot run or ``origin/main``
                does not resolve.
            MalformedTrailerError: If a line carrying the trailer key does not
                satisfy the grammar.
        """
        body = self._git_log()
        collected: list[tuple[tuple[datetime, str, int], ReviewFinding]] = []
        for record in body.split(_RS):
            if not record:
                continue
            sha, committed_at, subject, commit_body = _split_record(record)
            for position, line in enumerate(commit_body.split("\n")):
                if not line.startswith(TRAILER_KEY):
                    continue
                finding = finding_from_trailer(
                    line, commit_sha=sha, committed_at=committed_at, subject=subject
                )
                collected.append(((committed_at, sha, position), finding))
        collected.sort(key=lambda item: item[0])
        return tuple(finding for _, finding in collected)

    def _git_log(self) -> str:
        # Top-level options (before ``log``) harden the read (ADR-0029 D7):
        # ``--no-replace-objects`` is *not* a ``log`` option -- placing it after
        # ``log`` gives ``fatal: unrecognized argument`` -- and ``-c
        # log.showSignature=false`` / ``--no-optional-locks`` stop a repo config
        # from injecting gpg lines or taking a lock during a read.
        args = [
            "git",
            "-c",
            "log.showSignature=false",
            "--no-optional-locks",
            "--no-replace-objects",
            "log",
            PUBLIC_REF,
            f"--format={_FORMAT}",
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - args are adapter-controlled, never user input
                args,
                cwd=self._repo_root,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
                env=self._child_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHistoryUnavailableError(self._repo_root, str(exc)) from exc
        if completed.returncode != 0:
            reason = completed.stderr.decode("utf-8", errors="replace").strip() or "git log failed"
            raise GitHistoryUnavailableError(self._repo_root, reason)
        return completed.stdout.decode("utf-8")

    @staticmethod
    def _child_env() -> dict[str, str]:
        """The sanitized environment the child ``git`` runs under (ADR-0029 D7).

        The ambient environment minus every inherited ``GIT_*`` override, plus an
        explicit ``GIT_NO_REPLACE_OBJECTS=1`` -- so neither a redirected object
        store nor an active ``git replace`` can substitute an embargoed commit for
        the public tip. ``PATH`` and the rest of the environment are kept so ``git``
        is still found and behaves normally.
        """
        env = {
            name: value
            for name, value in os.environ.items()
            if name not in _INHERITED_GIT_OVERRIDES
        }
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
        return env


def _split_record(record: str) -> tuple[str, datetime, str, str]:
    """Split one git-log record into sha, committer date, subject, and body.

    The body is rejoined on ``_US`` rather than indexed, so a body that itself
    carried the separator byte would still reassemble whole.
    """
    fields = record.split(_US)
    if len(fields) < _MIN_FIELDS:
        raise GitHistoryUnavailableError(
            Path(),
            f"git log record has {len(fields)} fields, expected at least {_MIN_FIELDS}",
        )
    sha, date_iso, subject = fields[0], fields[1], fields[2]
    body = _US.join(fields[3:])
    return sha, datetime.fromisoformat(date_iso), subject, body
