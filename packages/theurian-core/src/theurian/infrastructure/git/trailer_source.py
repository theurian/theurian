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

**The stream is framed before it is decoded, and decoded one field at a time**
(#496). Decoding the whole stdout first made *any* commit whose message is not
valid UTF-8 -- a hand-built object, an older git, an ``encoding``-header path --
raise, so one such commit anywhere on public history took the entire corpus with
it and the remedy named a ``git fetch`` that could not help: the "one commit
bricks the corpus" abort ADR-0029 D3 forbids. Framing first is exact rather than
convenient: NUL is a whole code point in UTF-8 and never a continuation byte, so
the byte-level partition and the decoded-string partition are the *same*
partition. Each record's git-generated metadata (``%H``, ``%cI`` -- ASCII by
construction) then decodes strictly and fatally, while its author-controlled
message decodes strictly and, on failure, is **contained**: that record's trailers
are skipped and the record is accounted as one
:class:`~theurian.domain.review_finding.RejectedTrailer`, never silently dropped
and never fatal.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.errors import TheurianError
from theurian.domain.review_finding import (
    FindingLoad,
    MalformedTrailerError,
    RejectedTrailer,
    ReviewFinding,
    finding_from_trailer,
    keyed_lines,
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
#: the *stdout* is split on, as ``bytes``, **before** anything is decoded (#496).
#: Distinct from :data:`_FORMAT`'s ``%x00`` placeholders, which are the text git
#: expands into these bytes: an actual NUL in the argv would be rejected by
#: ``subprocess`` ("embedded null byte"), so the format carries the escape and only
#: the output carries the byte. Splitting the raw bytes loses nothing the decoded
#: split had: U+0000 encodes to the single byte ``0x00`` and no continuation byte
#: of a multi-byte sequence can be ``0x00``, so a byte-level occurrence of the
#: separator is exactly a code-point-level one.
_NUL: Final = b"\x00"
#: ``%B`` -- the **raw whole message**, subject included -- and deliberately not
#: ``%b`` (#410). git's ``%b`` excludes the first *paragraph*, not the first line:
#: in a message whose subject is not followed by a blank line, every following line
#: folds into the subject and ``%b`` is empty, so a column-0 ``Review-Finding:``
#: line sitting there reached neither tuple of the load -- silently unaccounted,
#: which is exactly what :class:`FindingLoad`'s loss-free invariant forbids.
#: Reading the whole message makes the population "every column-0 keyed line of the
#: commit message", with no paragraph rule between an author's bytes and the parse.
_FORMAT: Final = "format:%H%x00%cI%x00%B"

#: sha, committer-date, whole message. Exactly three -- not "at least three" --
#: because no field can contain the NUL separator, so the stream splits into an
#: exact multiple of this width. The subject is not read as its *own* field: it was
#: only the source of the deleted ``pull_request`` heuristic (D5), and nothing needs
#: it separately. It arrives inside ``%B`` as ordinary message text, where it is a
#: candidate keyed line like any other and nothing can re-derive a PR from it,
#: because no field tells the parser which line the subject was.
_FIELDS_PER_RECORD: Final = 3

#: How many **bytes** of an undecodable commit message reach its rejection's
#: ``raw_line`` (#496). A commit message is unbounded author-controlled input, and
#: a record-level rejection is written to the store and counted in an operator's
#: build report, so the excerpt that locates the failure is capped rather than
#: copied: 120 bytes is a subject line's worth -- enough to recognise which commit
#: it is -- and replacement-decoding at most this many bytes yields at most this
#: many characters, so the bound holds on both sides of the decode. The slice is
#: taken *before* decoding, so a multi-megabyte message is never materialised as a
#: ``str`` at all.
_UNDECODABLE_EXCERPT_BYTES: Final = 120


class GitHistoryUnavailableError(TheurianError):
    """``git log`` could not be run, or the public ref does not resolve.

    Distinct from a malformed trailer: nothing about the trailer grammar failed;
    the history the source reads from could not be reached at all. The commonest
    cause on a fresh clone is that ``refs/remotes/origin/main`` has not been
    fetched, so the remedy names the fetch rather than an edit to a trailer.

    **A commit message that is not valid UTF-8 no longer arrives here** (#496).
    History was perfectly reachable in that case, so the fetch remedy was advice
    that could not work; it is contained per record instead (see
    :func:`_decode_message`). What is left is exactly what the remedy fits: git
    could not be run, or the public ref does not resolve.
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
    """``git log -z`` produced a stream this adapter's framing cannot read.

    Distinct from :class:`GitHistoryUnavailableError` (history unreachable) and
    from a malformed trailer (the grammar failed): here ``git`` ran and the ref
    resolved, but the byte stream is not the one this adapter is written against.
    Two shapes reach it, and neither is author-forgeable:

    - the stream did not split into an exact multiple of
      :data:`_FIELDS_PER_RECORD` NUL-delimited fields. NUL cannot occur in a commit
      message (D4), so no commit content can reshape the partition;
    - a record's **git-generated metadata** -- ``%H`` (40 hex characters) or ``%cI``
      (an ISO-8601 instant), both ASCII by construction -- was not valid UTF-8
      (#496). An author cannot reach those fields, so bytes that are not ASCII
      there mean the *stream* is wrong, not the history: a ``-z`` framing this
      adapter does not know, or output under an encoding it did not ask for.

    Both therefore signal a git version or invocation that differs from the one
    this adapter is written against, so the remedy names that rather than a fetch
    or a trailer edit. An undecodable **message** is the opposite case and is
    contained per record instead (:func:`_decode_message`): its bytes *are*
    author-controlled, so one of them must never be fatal (D3).
    """

    def __init__(self, repo_root: Path, reason: str) -> None:
        self.repo_root = repo_root
        self.reason = reason
        self.remedy = (
            f"git log -z output in {str(repo_root)!r} was not the NUL-framed, "
            "ASCII-metadata stream expected; report this with the installed git "
            "version, as it indicates a framing mismatch."
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
        appearing anywhere in the commit message -- git's ``%(trailers)`` reads only
        the last paragraph and would drop the ~82% of this repo's trailers that sit
        ahead of the ``Signed-off-by:`` paragraph. **A trailer value is a single
        physical line** (D2): each ``split("\\n")`` line is one value, and an
        indented or wrapped continuation line is ordinary message text that does not
        begin with the key, so it is ignored rather than folded in.

        **The population is the whole message, subject included** (:data:`_FORMAT`,
        #410). The candidate lines are every ``\\n``-delimited line of ``%B``, so a
        trailer folded into the subject paragraph -- which ``%b`` dropped, since
        ``%b`` excludes the first *paragraph* rather than the first line -- is a
        keyed line like any other, and a subject that is itself a keyed line is a
        finding rather than an excluded special case. Two bounds this states rather
        than hides: "line" means ``\\n``-delimited, so a message whose separators are
        lone ``CR`` bytes is one line -- at most its first line is a candidate, and
        when that line is keyed the CR-joined remainder (further trailers, a
        sign-off) becomes that one finding's opaque text (D2), not further findings
        (#404 R1-4); and the subject arrives with no marker saying it *is* the
        subject, which is what keeps the deleted ``pull_request``-from-subject
        heuristic (D5) unreachable.

        **The load is loss-free by accounting, not by aborting** (AC-1, D3): every
        column-0 keyed line whose record has a readable message and a valid
        committer date is either an accepted :class:`ReviewFinding` or a
        :class:`RejectedTrailer` (its value failed the grammar); and a record whose
        **message git emitted as non-UTF-8 bytes** (#496), or whose committer date
        git emitted outside ``datetime``'s range, is accounted as a single
        record-level :class:`RejectedTrailer`, its trailers skipped rather than
        parsed. A malformed line, a whole record whose message cannot be decoded,
        and a whole record with an unrepresentable date are all captured -- never
        silently dropped and never a fatal abort -- so no one quoted grammar
        example, raw byte, or crafted committer date can brick the corpus.

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
                into whole NUL-delimited records, or a record's git-generated
                metadata field is not valid UTF-8.
        """
        records = _split_records(self._git_log(), self._repo_root)
        accepted: list[tuple[tuple[datetime, str, int], ReviewFinding]] = []
        rejected: list[tuple[tuple[str, int], RejectedTrailer]] = []
        for record in records:
            committed_at = record.committed_at
            # A record earns at most ONE record-level rejection, and the decode is
            # asked first: a message whose bytes are not UTF-8 has no candidate
            # lines at all, so naming the decode locates the failure where naming
            # the date would only say which record it was on. (A record failing
            # both is one entry either way -- the accounting is what must not
            # double-count.)
            if record.message is None:
                rejected.append(
                    (
                        (record.sha, 0),
                        RejectedTrailer(
                            record.sha, record.undecodable_excerpt, record.undecodable_reason
                        ),
                    )
                )
                continue
            if committed_at is None:
                # A committer date this runtime cannot hold as a UTC instant -- a
                # year >= 10000, or (unreachable from `%cI`) a value with no offset
                # at all -- cannot become a valid ReviewFinding, and its parse runs
                # before any trailer, so letting it escape would abort the whole
                # load, even for a trailer-less commit (D3). Account the record as
                # one rejected entry and keep loading its siblings; its sha is git's
                # own %H (D4), never author-forgeable.
                reason = (
                    f"unusable committer date {record.date_iso!r} "
                    "(not an offset-bearing instant datetime can hold, so the record "
                    "cannot be a finding)"
                )
                rejected.append(
                    ((record.sha, 0), RejectedTrailer(record.sha, record.date_iso, reason))
                )
                continue
            # The extraction rule is `keyed_lines` in the domain, not a `startswith`
            # written out here: which lines are candidates is grammar, and grammar
            # the adapter owned privately was unreachable to PARSER_STAMP (#406).
            for position, line in keyed_lines(record.message):
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

    def _git_log(self) -> bytes:
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
        # Raw bytes, undecoded: the stream is framed first and decoded one field at
        # a time (#496), so that a message which is not UTF-8 costs its own record
        # and not the corpus. See the module docstring for why the byte-level NUL
        # split is the same partition the decoded one was.
        return completed.stdout

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


def _decode_metadata(raw: bytes, *, field: str, repo_root: Path) -> str:
    """Decode one git-generated metadata field as UTF-8, or fail with a remedy.

    ``%H`` and ``%cI`` are ASCII by construction -- 40 hex characters and an
    ISO-8601 instant -- and no author can reach either. So bytes that do not decode
    *there* say the stream is not the one this adapter was written against, and the
    honest answer is the fatal :class:`GitOutputFramingError` with its
    report-your-git-version remedy: not the fetch remedy of
    :class:`GitHistoryUnavailableError` (history was reachable), and not a
    per-record containment (a record whose own sha cannot be read cannot be
    accounted against a commit at all).

    Fatal, and deliberately so, is safe here only because the field is
    git-generated. The author-controlled twin is :func:`_decode_message`, which
    must contain instead (#496, D3).
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GitOutputFramingError(
            repo_root,
            f"the {field} field git emitted is not valid UTF-8 ({exc}), "
            "so the record's own provenance cannot be read",
        ) from exc


# The trailer grammar is untouched by the containment below, so `PARSER_STAMP` does
# not move (#496) -- deliberately, not by omission. The stamp answers "would this
# parser read the corpus differently from the one that built the store", and none
# of this is parser mechanics: which lines are candidates and what a line means
# stay `keyed_lines` and `parse_trailer_line`, byte-identical. What changed is the
# *source*'s framing and decoding, upstream of the grammar. Nor is there a store to
# mark stale: a history carrying an undecodable message produced NO store before
# this fix -- the build aborted -- so no file exists that was written under the old
# behaviour and could now silently diverge from one written under the new.
def _decode_message(raw: bytes) -> tuple[str | None, str, str]:
    """One record's ``%B`` decoded strictly, or the contained account of why not.

    Returns ``(message, excerpt, reason)``: the decoded message with an empty
    excerpt and reason, or ``None`` with a bounded excerpt and a reason naming the
    failure. **Contained rather than raised** (#496, ADR-0029 D3): a commit message
    is author-controlled content, and history is signed and append-only, so a
    message that is merely not UTF-8 must cost its own record's trailers and
    nothing else. Decoding the whole ``git log`` stdout at once made one such
    commit anywhere on public history raise, taking every well-formed sibling with
    it and answering with a ``git fetch`` remedy that could not help -- the exact
    "one commit permanently bricks the entire corpus with no forward fix" D3
    forbids.

    **How such a commit arises**, since a passing familiarity with git suggests it
    cannot. The causes named for the decode before #496 -- a repo-level
    ``i18n.logOutputEncoding``, or a commit ``encoding`` header git cannot convert
    -- are real but were not the whole set, and they left out the simplest one: a
    message whose bytes are just not UTF-8. git stores a commit message verbatim
    and validates nothing, so ``git hash-object -t commit --stdin --literally``
    writes one directly, and it pushes and fetches like any other object (measured
    2026-09-03). The ordinary porcelain does *not* produce one on git 2.47.1:
    both ``git commit-tree`` and ``git commit -F`` re-encode a non-UTF-8 message to
    UTF-8 -- a lone ``0x80`` was stored as ``0xc2 0x80`` -- and warn while doing it
    (both measured 2026-09-03), which is why a fixture built with either silently
    exercises valid UTF-8 and proves nothing. So the population is hand-built
    objects, older
    or differently-configured gits, and the ``encoding``-header paths -- narrow,
    but every one of them is a *public commit* the corpus must survive.

    The excerpt is untrusted and bounded (:data:`_UNDECODABLE_EXCERPT_BYTES`): the
    raw bytes are sliced *before* decoding and then decoded with
    ``errors="replace"``, so no undecodable byte is ever stored, the replacement
    characters show where the message went wrong, and an unbounded message cannot
    inflate the row that reports it.
    """
    try:
        return raw.decode("utf-8"), "", ""
    except UnicodeDecodeError as exc:
        excerpt = raw[:_UNDECODABLE_EXCERPT_BYTES].decode("utf-8", errors="replace")
        reason = (
            f"commit message is not valid UTF-8 ({exc}), so no trailer can be read "
            f"from it; raw_line is its first {_UNDECODABLE_EXCERPT_BYTES} bytes "
            "decoded with replacement characters"
        )
        return None, excerpt, reason


@dataclass(frozen=True, slots=True)
class _Record:
    """One framed ``git log -z`` record: its sha, whole message, and committer date.

    ``message`` is git's ``%B`` -- subject and body together, not the ``%b`` body
    alone -- because a paragraph rule between the author's bytes and the parse is
    what let a subject-folded trailer go unaccounted (#410, and :data:`_FORMAT`).
    Named for what it holds so no reader infers a subject was stripped on the way in.

    It is ``None`` exactly when git emitted message bytes that are not valid UTF-8
    (#496), and then ``undecodable_excerpt`` and ``undecodable_reason`` carry the
    bounded account :meth:`GitTrailerFindingSource.load_findings` turns into one
    record-level rejection; both are empty for every record whose message decoded,
    which in practice is every record. The two travel on the record rather than
    being recomputed by the caller so the ``UnicodeDecodeError``'s own byte and
    position -- the only thing that locates a failure past the excerpt's cap --
    survives the one place it exists (:func:`_decode_message`).

    ``committed_at`` is a **UTC instant** (#405) and is ``None`` exactly when git
    emitted a committer date this runtime cannot hold as one -- a year >= 10000,
    which a crafted ``GIT_COMMITTER_DATE`` (``@253402387200`` ->
    ``10000-01-02T00:00:00Z``) produces but ``datetime.max`` (year 9999) cannot
    hold, or a value carrying no offset, which ``%cI`` never emits and which cannot
    be converted without reading the machine's own timezone. The parse runs for
    every record *before* any trailer is read, so a raised ``ValueError`` there
    would abort the whole load -- even for a trailer-less commit -- defeating the D3
    "never a fatal abort" invariant. Marking the date unrepresentable instead lets
    the caller account the record as rejected and keep going. ``date_iso`` keeps
    git's raw ``%cI`` verbatim -- offset and all -- so the rejection can name the
    value that failed and no provenance is lost by the normalisation; the date is
    never fabricated to fill the gap, because it is a published field and the
    finding's total-order sort key, and a record without a valid one cannot be a
    valid finding.
    """

    sha: str
    message: str | None
    undecodable_excerpt: str
    undecodable_reason: str
    date_iso: str
    committed_at: datetime | None


def _parse_committer_date(date_iso: str) -> datetime | None:
    """git's ``%cI`` as a UTC instant, or ``None`` when it is unrepresentable.

    ``datetime.fromisoformat`` raises ``ValueError`` on a year >= 10000, which git
    will emit for a crafted ``GIT_COMMITTER_DATE``. Returning ``None`` rather than
    raising lets the caller account that record as rejected and keep loading the
    rest (D3); a ``None`` is never replaced by a sentinel date, because the
    committer date is the finding's total-order sort key and a published field.

    **Normalised to UTC (#405).** ``%cI`` carries the committer's own offset, and
    an offset-preserving value is not a sort key once it is written down: the store
    keeps it as TEXT, and TEXT order over mixed offsets is not chronological. The
    conversion is an identity on the *instant*, so the in-memory total order below
    is byte-for-byte what it was -- aware datetimes already compared as instants --
    and only the recorded representation changes.

    **A date without an offset is unrepresentable, not local time.** ``%cI`` always
    carries one, so this is unreachable from git; it is refused rather than passed
    through because ``astimezone`` on a naive value silently reads the *machine's*
    own offset, which would make the load a function of where it ran. Without this
    branch such a value also reached ``ReviewFinding.__post_init__``'s
    timezone-aware check and raised a ``DomainError`` no caller catches -- a fatal
    abort of the whole load, which is precisely what D3 forbids.

    **Two operations can raise, and they raise *different* exception types, which is
    why the guard names both** (#405 R1-1). ``fromisoformat`` raises ``ValueError``
    on a year >= 10000. ``astimezone(UTC)`` raises ``OverflowError`` -- an
    ``ArithmeticError``, not a ``ValueError`` -- when it shifts a *representable*
    local datetime across ``datetime``'s range: a max-year value with a negative
    offset (``9999-12-31T23:00:00-01:00`` lands in year 10000), or a min-year value
    with a positive one (``0001-01-01T00:00:00+05:00`` lands before year 1). The
    opposite-sign offset at each boundary shifts *inward* and is representable.
    Catching only ``ValueError`` here let the ``OverflowError`` escape past every
    ``TheurianError`` handler as a raw traceback, bricking the whole corpus on one
    crafted trailer-less commit -- the exact D3 abort this function exists to
    prevent, reintroduced on an axis its first cut did not cover.
    """
    try:
        parsed = datetime.fromisoformat(date_iso)
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _split_records(stdout: bytes, repo_root: Path) -> list[_Record]:
    """Split a ``git log -z`` stream into whole records, each field decoded.

    **The raw bytes, framed before anything is decoded** (#496). The stream is
    every record's fields joined by NUL with records NUL-*separated* (D4):
    ``--format=format:`` gives *separator* semantics, not the *terminator*
    semantics of ``tformat:`` or a bare format string, so there is no trailing NUL
    after the last record and a well-formed stream is exactly
    :data:`_FIELDS_PER_RECORD` * n tokens. Because NUL cannot occur in a commit
    message, no field -- the multi-line ``%B`` message included -- can hold the
    separator, so the split is exact and needs no rejoining; and because NUL is a
    whole code point in UTF-8, splitting the bytes partitions the stream exactly
    where decoding it first would have (see :data:`_NUL`).

    Each field is then decoded on its own terms, which is the whole point of doing
    it here: the git-generated ``%H`` and ``%cI`` decode strictly and fatally
    (:func:`_decode_metadata`), while the author-controlled ``%B`` decodes strictly
    and, on failure, is contained on the record (:func:`_decode_message`) for the
    caller to account as one rejection.

    Each record's committer date is parsed here too; a date git emitted outside
    ``datetime``'s range is carried as ``committed_at=None`` (see :class:`_Record`)
    rather than raised, so one crafted commit cannot abort the whole load.

    Raises:
        GitOutputFramingError: if the stream does not partition into whole records,
            or a record's git-generated metadata field is not valid UTF-8 (real
            ``repo_root``, and a framing-specific remedy -- not the fetch remedy of
            :class:`GitHistoryUnavailableError`, which is a different failure).
    """
    tokens = stdout.split(_NUL)
    # Defensive, not the normal path: this adapter's `format:` separates records
    # (no trailing NUL), so real output is exactly `_FIELDS_PER_RECORD * n` tokens
    # and this branch does not fire. It is here only to tolerate a `-z` that
    # *terminates* records instead (`tformat:`, or a future git default), which
    # would leave one trailing empty token. Drop exactly that one, and only when the
    # count says it is the terminator (`% width == 1`), so an empty final message --
    # a legitimate last field under separator semantics -- is never mistaken for it.
    if len(tokens) % _FIELDS_PER_RECORD == 1 and tokens[-1] == b"":
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
        raw_sha, raw_date, raw_message = tokens[start : start + _FIELDS_PER_RECORD]
        sha = _decode_metadata(raw_sha, field="commit sha (%H)", repo_root=repo_root)
        date_iso = _decode_metadata(raw_date, field="committer date (%cI)", repo_root=repo_root)
        message, excerpt, reason = _decode_message(raw_message)
        records.append(
            _Record(
                sha=sha,
                message=message,
                undecodable_excerpt=excerpt,
                undecodable_reason=reason,
                date_iso=date_iso,
                committed_at=_parse_committer_date(date_iso),
            )
        )
    return records
