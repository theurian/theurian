"""Read ``Review-Finding:`` trailers from public git history (ADR-0029).

The FR-S1 Git-commit-metadata source, implemented as a :class:`ReviewFindingSource`
adapter. It reads **only** the public default branch, ``origin/main``: the embargo
closure (ADR-0029 decision 6) rests on that scoping, because embargoed findings
live on a private fork and never reach public ``main``. ``git log`` defaults to
the current branch and reads everything under ``--all``, so the ref is pinned as a
constant here rather than accepted as a parameter -- an adapter that read
``--all`` would silently ingest fetched private-fork commits and lose the
structural protection.

``git`` is invoked as an argument vector with ``shell=False`` (SEC-9), and its
output is captured as bytes and decoded UTF-8 explicitly rather than through
``text=True``: the finding text carries an em-dash separator and other non-ASCII,
and decoding under the process locale rather than UTF-8 would corrupt the
byte-preservation the loss-free mapping (AC-1) depends on.
"""

from __future__ import annotations

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

#: The one ref this source reads. Not a parameter: see the module docstring --
#: the embargo closure is the reason the scoping is pinned rather than trusted.
PUBLIC_REF: Final = "origin/main"

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
    cause on a fresh clone is that ``origin/main`` has not been fetched, so the
    remedy names the fetch rather than an edit to a trailer.
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
        args = ["git", "log", PUBLIC_REF, f"--format={_FORMAT}"]
        try:
            completed = subprocess.run(  # noqa: S603 - args are adapter-controlled, never user input
                args,
                cwd=self._repo_root,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHistoryUnavailableError(self._repo_root, str(exc)) from exc
        if completed.returncode != 0:
            reason = completed.stderr.decode("utf-8", errors="replace").strip() or "git log failed"
            raise GitHistoryUnavailableError(self._repo_root, reason)
        return completed.stdout.decode("utf-8")


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
