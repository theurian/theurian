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
from dataclasses import dataclass
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
#: (``GIT_CONFIG``, the ``GIT_CONFIG_{COUNT,GLOBAL,SYSTEM}`` trio, and
#: ``GIT_CONFIG_PARAMETERS`` -- the last injects config as if by ``-c``, so an
#: inherited ``i18n.logOutputEncoding=UTF-16`` there would make ``git log`` emit
#: non-UTF-8 and break the parse). Defined here rather than imported from ``tools/``
#: or the test tree (which keep their own copy for a different read) so the adapter
#: owns its own contract.
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_PARAMETERS",
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
#: log -z`` and the explicit ``format:`` prefix (:data:`_FORMAT`) the fields of
#: each record are joined by NUL and records are NUL-*separated* -- ``format:`` is
#: *separator* semantics, not the *terminator* semantics of ``tformat:`` or a bare
#: format string, so there is no trailing NUL after the last record and a
#: well-formed stream is exactly ``_FIELDS_PER_RECORD * n`` tokens (verified
#: 2026-08-27). Either way NUL cannot occur in a field, so the stream partitions
#: unambiguously into fixed-width records that no commit content can reshape.
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
        column-0 keyed line whose record has a valid committer date is either an
        accepted :class:`ReviewFinding` or a :class:`RejectedTrailer` (its value
        failed the grammar); and a record whose committer date git emitted outside
        ``datetime``'s range is accounted as a single record-level
        :class:`RejectedTrailer`, its trailers skipped rather than parsed. A
        malformed line, and a whole record with an unrepresentable date, are both
        captured -- never silently dropped and never a fatal abort -- so neither one
        quoted grammar example nor one crafted committer date can brick the corpus.

        The accepted tuple is in total order ``(commit date, commit sha, position
        within the commit)`` -- the sha alone is already total (a position
        disambiguates lines sharing a commit) and the date leads it only to make the
        order chronological. The rejected tuple is ordered ``(commit sha, position)``
        *without* the date, because a record rejected precisely *because* its date is
        unrepresentable has no date to sort on; that key is still total and
        deterministic, so two runs over the same history produce byte-identical
        sequences (AC-6).

        Raises:
            GitHistoryUnavailableError: If ``git`` cannot run or
                ``refs/remotes/origin/main`` does not resolve.
            GitOutputFramingError: If the ``git log -z`` stream does not partition
                into whole NUL-delimited records.
        """
        records = _split_records(self._git_log(), self._repo_root)
        accepted: list[tuple[tuple[datetime, str, int], ReviewFinding]] = []
        rejected: list[tuple[tuple[str, int], RejectedTrailer]] = []
        for record in records:
            committed_at = record.committed_at
            if committed_at is None:
                # A committer date git emitted outside datetime's range (year >=
                # 10000) cannot become a valid ReviewFinding, and its parse runs
                # before any trailer -- so letting the ValueError escape would abort
                # the whole load, even for a trailer-less commit (D3). Account the
                # record as one rejected entry and keep loading its siblings; its
                # sha is git's own %H (D4), never author-forgeable.
                reason = (
                    f"unparseable committer date {record.date_iso!r} "
                    "(year exceeds datetime.max, so the record cannot be a finding)"
                )
                rejected.append(
                    ((record.sha, 0), RejectedTrailer(record.sha, record.date_iso, reason))
                )
                continue
            for position, line in enumerate(record.body.split("\n")):
                if not line.startswith(TRAILER_KEY):
                    continue
                try:
                    finding = finding_from_trailer(
                        line, commit_sha=record.sha, committed_at=committed_at
                    )
                except MalformedTrailerError as exc:
                    rejected.append(
                        ((record.sha, position), RejectedTrailer(record.sha, exc.line, exc.reason))
                    )
                else:
                    accepted.append(((committed_at, record.sha, position), finding))
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
            "-z",  # NUL-separate records (D4; `format:` separates, not terminates)
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
        return _decode_git_output(completed.stdout, self._repo_root)

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


def _decode_git_output(raw: bytes, repo_root: Path) -> str:
    """Decode ``git log``'s stdout as UTF-8, or fail with a remedy -- never a raw crash.

    The finding text carries an em-dash separator and other non-ASCII, so the
    stream is decoded as UTF-8 explicitly rather than under the process locale, to
    preserve the byte-exact mapping AC-1 depends on. A decode failure means ``git``
    emitted a non-UTF-8 encoding -- a repo-level ``i18n.logOutputEncoding`` or an
    unconvertible commit ``encoding`` header, the ``GIT_CONFIG_PARAMETERS`` vector
    being already stripped (see :data:`_INHERITED_GIT_OVERRIDES`). It is contained
    as a :class:`GitHistoryUnavailableError` -- the adapter never obtained readable
    history -- rather than surfaced as an uncaught ``UnicodeDecodeError``, which also
    matches stderr's decode (``errors="replace"``) instead of leaving stdout strict.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitHistoryUnavailableError(
            repo_root, f"git log output was not valid UTF-8 ({exc})"
        ) from exc


@dataclass(frozen=True, slots=True)
class _Record:
    """One framed ``git log -z`` record: its sha, body, and committer date.

    ``committed_at`` is ``None`` exactly when git emitted a committer date this
    runtime cannot represent -- a year >= 10000, which a crafted
    ``GIT_COMMITTER_DATE`` (``@253402387200`` -> ``10000-01-02T00:00:00Z``)
    produces but ``datetime.max`` (year 9999) cannot hold. The parse runs for every
    record *before* any trailer is read, so a raised ``ValueError`` there would
    abort the whole load -- even for a trailer-less commit -- defeating the D3
    "never a fatal abort" invariant. Marking the date unrepresentable instead lets
    the caller account the record as rejected and keep going. ``date_iso`` keeps
    git's raw ``%cI`` verbatim so the rejection can name the value that failed; the
    date is never fabricated to fill the gap, because it is a published field and
    the finding's total-order sort key -- a record without a valid one cannot be a
    valid finding.
    """

    sha: str
    body: str
    date_iso: str
    committed_at: datetime | None


def _parse_committer_date(date_iso: str) -> datetime | None:
    """git's ``%cI`` as an aware datetime, or ``None`` when it is unrepresentable.

    ``datetime.fromisoformat`` raises ``ValueError`` on a year >= 10000, which git
    will emit for a crafted ``GIT_COMMITTER_DATE``. Returning ``None`` rather than
    raising lets the caller account that record as rejected and keep loading the
    rest (D3); a ``None`` is never replaced by a sentinel date, because the
    committer date is the finding's total-order sort key and a published field.
    """
    try:
        return datetime.fromisoformat(date_iso)
    except ValueError:
        return None


def _split_records(stdout: str, repo_root: Path) -> list[_Record]:
    """Split a ``git log -z`` stream into whole records, each date parsed.

    The stream is every record's fields joined by NUL with records NUL-*separated*
    (D4): ``--format=format:`` gives *separator* semantics, not the *terminator*
    semantics of ``tformat:`` or a bare format string, so there is no trailing NUL
    after the last record and a well-formed stream is exactly
    :data:`_FIELDS_PER_RECORD` * n tokens. Because NUL cannot occur in a commit
    message, no field -- the multi-line body included -- can hold the separator, so
    the split is exact and needs no rejoining.

    Each record's committer date is parsed here; a date git emitted outside
    ``datetime``'s range is carried as ``committed_at=None`` (see :class:`_Record`)
    rather than raised, so one crafted commit cannot abort the whole load.

    Raises:
        GitOutputFramingError: if the stream does not partition into whole records
            (real ``repo_root``, and a framing-specific remedy -- not the fetch
            remedy of :class:`GitHistoryUnavailableError`, which is a different
            failure).
    """
    tokens = stdout.split(_NUL)
    # Defensive, not the normal path: this adapter's `format:` separates records
    # (no trailing NUL), so real output is exactly `_FIELDS_PER_RECORD * n` tokens
    # and this branch does not fire. It is here only to tolerate a `-z` that
    # *terminates* records instead (`tformat:`, or a future git default), which
    # would leave one trailing empty token. Drop exactly that one, and only when the
    # count says it is the terminator (`% width == 1`), so an empty final body -- a
    # legitimate last field under separator semantics -- is never mistaken for it.
    if len(tokens) % _FIELDS_PER_RECORD == 1 and tokens[-1] == "":
        tokens.pop()
    # Also defensive: separator output is always `3n`, so this refuses only a
    # genuinely mis-framed stream (a git whose `-z` framing differs from both the
    # separator and terminator shapes above). Commit-4's tests drive both branches
    # so neither is dead code that would survive its own deletion.
    if len(tokens) % _FIELDS_PER_RECORD != 0:
        raise GitOutputFramingError(
            repo_root,
            f"git log -z stream has {len(tokens)} NUL-delimited fields, "
            f"not a multiple of {_FIELDS_PER_RECORD}",
        )
    records: list[_Record] = []
    for start in range(0, len(tokens), _FIELDS_PER_RECORD):
        sha, date_iso, body = tokens[start : start + _FIELDS_PER_RECORD]
        records.append(
            _Record(
                sha=sha,
                body=body,
                date_iso=date_iso,
                committed_at=_parse_committer_date(date_iso),
            )
        )
    return records
