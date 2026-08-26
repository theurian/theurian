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
    FindingLoad,
    MalformedTrailerError,
    RejectedTrailer,
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

#: The field/record separator (ADR-0029 Amendment 1, D4). NUL is the one byte
#: git **forbids** in a commit message (verified 2026-08-26:
#: ``error: a NUL byte in commit log message not allowed``), so an author cannot
#: place it in a subject or body -- which is exactly why the framing bytes must be
#: NUL and not RS (0x1e)/US (0x1f). RS/US are *permitted* in a commit body
#: (round-trip verified), so an RS/US framing is **forgeable**: an author could
#: embed those bytes to inject a fabricated record carrying an attacker-chosen sha,
#: date, subject and PR number, forging the FR-S3 provenance anchor. With ``git
#: log -z`` the fields of each record are joined by NUL and each record is
#: NUL-terminated, so the whole stream partitions unambiguously into fixed-width
#: records that no commit content can reshape.
#: The literal byte that separates records in ``git log -z`` output -- this is what
#: the *stdout* is split on. Distinct from :data:`_FORMAT`'s ``%x00`` placeholders,
#: which are the text git expands into these bytes: an actual NUL in the argv would
#: be rejected by ``subprocess`` ("embedded null byte"), so the format carries the
#: escape and only the output carries the byte.
_NUL: Final = "\x00"
_FORMAT: Final = "format:%H%x00%cI%x00%b"

#: sha, committer-date, body. Exactly three -- not "at least three" -- because no
#: field can contain the NUL separator, so the stream splits into an exact multiple
#: of this width. The subject is deliberately not read: it was only the source of
#: the deleted ``pull_request`` heuristic (D5), and nothing else needs it.
_FIELDS_PER_RECORD: Final = 3


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


class GitOutputFramingError(TheurianError):
    """``git log -z`` produced output the NUL framing could not partition.

    Distinct from :class:`GitHistoryUnavailableError` (history unreachable) and
    from a malformed trailer (the grammar failed): here ``git`` ran and the ref
    resolved, but the byte stream did not split into an exact multiple of
    :data:`_FIELDS_PER_RECORD` NUL-delimited fields. Because NUL cannot occur in a
    commit message (D4), this is not an author-forgeable condition and should not
    arise in practice; it signals a git version or invocation whose ``-z`` framing
    differs from the one this adapter is written against, so the remedy names that
    rather than a fetch or a trailer edit.
    """

    def __init__(self, repo_root: Path, reason: str) -> None:
        self.repo_root = repo_root
        self.reason = reason
        self.remedy = (
            f"git log -z output in {str(repo_root)!r} was not NUL-framed as expected; "
            "report this with the installed git version, as it indicates a framing mismatch."
        )
        super().__init__(f"Corrupt git log framing in {str(repo_root)!r}: {reason}")


@final
class GitTrailerFindingSource:
    """Reads ``Review-Finding:`` trailers from public history into findings.

    Satisfies :class:`~theurian.domain.ports.ReviewFindingSource` structurally.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def load_findings(self) -> FindingLoad:
        """Every ``Review-Finding:`` trailer on public history, accepted or rejected.

        **Extraction is a column-0 block, not git's own trailer parser** (D1): a
        genuine trailer is a line beginning at column 0 with the exact key,
        appearing anywhere in the commit body -- git's ``%(trailers)`` reads only
        the last paragraph and would drop the ~82% of this repo's trailers that sit
        ahead of the ``Signed-off-by:`` paragraph. **A trailer value is a single
        physical line** (D2): each ``split("\\n")`` line is one value, and an
        indented or wrapped continuation line is ordinary body text that does not
        begin with the key, so it is ignored rather than folded in.

        **The load is loss-free by accounting, not by aborting** (AC-1, D3): every
        column-0 keyed line is either an accepted :class:`ReviewFinding` or a
        :class:`RejectedTrailer` (its value failed the grammar). A malformed line is
        captured, never silently dropped and never a fatal abort -- so one quoted
        grammar example in a future commit body cannot brick the whole corpus.

        Both tuples are in the same total order -- the sort key ``(commit date,
        commit sha, position within the commit)`` -- so two runs over the same
        history produce byte-identical sequences (AC-6): the commit sha alone is
        already total (a position disambiguates lines sharing a commit), and the
        date leads it only to make the order chronological.

        Raises:
            GitHistoryUnavailableError: If ``git`` cannot run or
                ``refs/remotes/origin/main`` does not resolve.
            GitOutputFramingError: If the ``git log -z`` stream does not partition
                into whole NUL-delimited records.
        """
        records = _split_records(self._git_log(), self._repo_root)
        accepted: list[tuple[tuple[datetime, str, int], ReviewFinding]] = []
        rejected: list[tuple[tuple[datetime, str, int], RejectedTrailer]] = []
        for sha, committed_at, commit_body in records:
            for position, line in enumerate(commit_body.split("\n")):
                if not line.startswith(TRAILER_KEY):
                    continue
                key = (committed_at, sha, position)
                try:
                    finding = finding_from_trailer(line, commit_sha=sha, committed_at=committed_at)
                except MalformedTrailerError as exc:
                    rejected.append((key, RejectedTrailer(sha, exc.line, exc.reason)))
                else:
                    accepted.append((key, finding))
        accepted.sort(key=lambda item: item[0])
        rejected.sort(key=lambda item: item[0])
        return FindingLoad(
            accepted=tuple(finding for _, finding in accepted),
            rejected=tuple(entry for _, entry in rejected),
        )

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
            "-z",  # NUL-terminate each record (D4); pairs with the %x00 field seps
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


def _split_records(stdout: str, repo_root: Path) -> list[tuple[str, datetime, str]]:
    """Split a ``git log -z`` stream into whole (sha, date, body) records.

    The stream is every record's fields joined by NUL with each record
    NUL-terminated (D4), so it partitions into an exact multiple of
    :data:`_FIELDS_PER_RECORD` tokens. Because NUL cannot occur in a commit
    message, no field -- the multi-line body included -- can hold the separator, so
    the split is exact and needs no rejoining.

    Raises:
        GitOutputFramingError: if the stream does not partition into whole records
            (real ``repo_root``, and a framing-specific remedy -- not the fetch
            remedy of :class:`GitHistoryUnavailableError`, which is a different
            failure).
    """
    tokens = stdout.split(_NUL)
    # `git log -z` terminates the final record with a NUL, so a well-formed stream
    # has one trailing empty token. Drop exactly that one, and only when the count
    # says it is the terminator (``% width == 1``), so an empty final body -- a
    # legitimate last field -- is never mistaken for it.
    if len(tokens) % _FIELDS_PER_RECORD == 1 and tokens[-1] == "":
        tokens.pop()
    if len(tokens) % _FIELDS_PER_RECORD != 0:
        raise GitOutputFramingError(
            repo_root,
            f"git log -z stream has {len(tokens)} NUL-delimited fields, "
            f"not a multiple of {_FIELDS_PER_RECORD}",
        )
    records: list[tuple[str, datetime, str]] = []
    for start in range(0, len(tokens), _FIELDS_PER_RECORD):
        sha, date_iso, body = tokens[start : start + _FIELDS_PER_RECORD]
        records.append((sha, datetime.fromisoformat(date_iso), body))
    return records
