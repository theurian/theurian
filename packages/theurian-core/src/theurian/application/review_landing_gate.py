"""The SEC-11 gate that runs before a review record becomes a file (ADR-0030 decision 4).

``security.secretScan`` applies **at ingestion, per record, before a record
becomes a file**: ``block`` refuses the flagged record and it is never written,
``warn`` lands it and reports every finding, ``off`` scans nothing.

**This mirrors ``propose accept``, not ``index build``, and the reason is the
source's own premise.** ``application/index_secret_scan.py`` records why the
build reports rather than refuses: a landed secret is already readable through
``knowledge.search`` and ``knowledge.get`` the moment ``migrate apply`` writes
it, so a build that refused to publish would deny *ranking* without
un-disclosing anything. That premise is false here. Review ingestion runs
**before** the content exists anywhere in Theurian, so refusing genuinely
un-discloses relative to Theurian's own surfaces: nothing is written, so nothing
is served, indexed, ranked or reachable through any tool.

**Redaction runs first, and the scan then reads the landing candidate.** The
order is the gate's whole premise -- *before a record becomes a file* -- so what
is scanned has to be the bytes that would be written and not the bytes that
arrived. Two consequences follow and both are deliberate:

* a secret that sits **only** in a display name the redaction removes does not
  refuse the record under ``block``, because after redaction it is not in the
  landing candidate at all. Scanning the pre-redaction value instead would refuse
  a record over a string this run had already decided not to write;
* the placeholder that replaces a display name is itself scanned, like every
  other field, rather than skipped as trusted.

``tests/unit/test_review_landing_gate.py::test_a_secret_only_in_a_display_name_redaction_removes_does_not_refuse``
is what fails if the order is reversed.

**A participant id is read in both redaction states, and under redaction the raw
one no longer exists to read.** ``external_id`` is the provider's node id when the
response carried one and *the author's own login* when it did not
(:func:`~theurian.infrastructure.github.response.optional_participant`), so it is
not reliably structural and this module treats it as text a person may have
chosen. The two states close differently, and each has its own driving case:

* **redaction off** -- the value that would be written is the value scanned, so a
  credential-shaped login refuses the record under ``block``
  (``test_review_landing_gate.py::test_a_secret_in_a_login_fallback_participant_id_refuses_the_record``);
* **redaction on** -- :func:`redacted` runs first and replaces a login-fallback id
  with a deterministic pseudonym, so the raw login is gone before the scan and
  gone before the write; what the scan then reads is the pseudonym
  (``test_review_landing_gate.py::test_a_login_fallback_id_is_pseudonymised_before_it_can_land``).

Neither state writes the raw login while the other refuses it, which is the hole
this pair closes: with ``external_id`` unscanned and unredacted, *enabling* R-12
turned a refusal into a landing that published the very name R-12 promised to
replace (PR #596 round 1, adversarial H-D).

**The scanned population is derived from what the codec writes, not remembered.**
A hand-written field list has one failure mode and H-D was it, so
``tests/unit/test_review_scan_population.py`` walks every string leaf of the
three documents ``infrastructure/review_evidence/codec.py`` writes and requires
each to be either a value :func:`_scanned_values` yields or an entry on that
file's exemption list, each entry carrying the line that says who chooses the
value. Measured 2026-09-08 with the ``externalId`` rows filtered back out of
:func:`_scanned_values`: all three payload kinds report.

**The report names the record, never the matched bytes** -- repository, pull
request number, record id and, for a finding inside a comment, that comment's id.
``index_secret_scan.py``'s reason applies unchanged: a report that quotes the
secret is a second copy of it. Every finding's *location* is a fixed literal of
this module's own (:data:`_FIELD_LITERALS`' values), never a value that was
scanned, which is the treatment ``proposal_service`` arrived at when a body whose
own path was the credential republished it through the location (#360).

**ADR-0019 is discharged here rather than cited.** Labels, the head branch name
and the milestone are read as *content to scan* and by nothing else: no refusal,
no ordering and no selection in this module reads a label's value to decide
anything. What a label decides is whether it carries a secret, which is the same
question asked of a comment body.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.domain.review_ingest import bounded_quote
from theurian.security.content_secrets import MAX_FINDINGS, SecretFinding, scan_text
from theurian.security.project_config import (
    SecretScanPolicy,
    read_review_participant_redaction,
    read_secret_scan_policy,
)

#: What a redacted display name is replaced with (R-12).
#:
#: One constant for every participant rather than a per-person pseudonym: the
#: identity graph survives on ``external_id``, which redaction keeps -- verbatim
#: where it is the provider's node id, and as :func:`_pseudonym` of itself where
#: it is a login the adapter fell back to -- so a distinguishable placeholder
#: here would carry back exactly the linkage the setting was turned on to remove.
#: It is scanned like any other field -- a placeholder is not trusted input, it
#: is simply input this module wrote.
REDACTED_DISPLAY_NAME: Final = "[redacted]"

#: What a pseudonymised participant id starts with, so a reader meeting one in a
#: landed record can tell Theurian's substitution from the provider's own id.
REDACTED_ID_PREFIX: Final = "redacted-"

#: How many hex digits of the digest a pseudonym carries. Sixteen is 64 bits,
#: which is more than enough to keep one project's participants distinct, and it
#: keeps the whole value at 25 characters -- under ``content_secrets``'
#: 32-character candidate floor, and with no upper-case character to clear its
#: class gate with. Both are why the substitution this module writes is not then
#: reported as a credential by the scan that runs immediately after it;
#: ``test_review_landing_gate.py::test_a_pseudonymous_id_is_not_itself_reported_as_a_secret``
#: is what fails if either stops holding.
_PSEUDONYM_DIGITS: Final = 16

#: The payload types one landing candidate may carry.
ReviewRecordPayload = ReviewEvent | ReviewSubmission | ReviewThread

#: Where each scanned value sits, as this module names it in a report.
#:
#: **Fixed literals, never a value that was scanned.** A location derived from
#: the text would republish the match whenever the match *is* the location -- a
#: file path that is itself credential-shaped is exactly that case, and it is one
#: of the fields decision 3's table puts on the untrusted side. The spellings that
#: carry an index -- ``labels[i]`` and the ``comments[i]`` family, built inline
#: rather than kept here -- take an integer this module counts, not a value the
#: provider sent.
_FIELD_LITERALS: Final = {
    "title": "title",
    "body": "body",
    "head_ref_name": "headRefName",
    "milestone": "milestone",
    "author": "author.displayName",
    "author_id": "author.externalId",
    "file_path": "filePath",
    "resolved_by": "resolution.resolvedBy.displayName",
    "resolved_by_id": "resolution.resolvedBy.externalId",
}


@dataclass(frozen=True, slots=True)
class ReviewRecordIdentity:
    """Which record a verdict is about, in the terms ADR-0030 decision 4 names.

    ``record_id`` is ``None`` for the pull-request event itself, which the
    repository and the number already name; a submission and a thread carry the
    provider's node id. There is deliberately no *kind* field: the identity
    decision 4 requires is repository, pull request, record and comment, and a
    kind spelled here would be a second vocabulary beside the one the evidence
    layout already owns.
    """

    repository: str
    pull_request_number: int
    record_id: str | None = None

    def describe(self) -> str:
        """One line naming this record and nothing that was scanned.

        Both outside values are routed through
        :func:`~theurian.domain.review_ingest.bounded_quote` -- rendered and then
        cut, in that order. A repository name and a node id both arrive from a
        response, either can be a megabyte, and either can carry a control
        character that reorders the sentence printed around it.
        """
        named = "" if self.record_id is None else f" record {bounded_quote(self.record_id)}"
        return f"{bounded_quote(self.repository)}#{self.pull_request_number}{named}"


@dataclass(frozen=True, slots=True)
class ReviewSecretFinding:
    """One secret-shaped string in a record, described without reproducing it."""

    identity: ReviewRecordIdentity
    #: A fixed literal from :data:`_FIELD_LITERALS`, or one of its indexed forms.
    field: str
    #: The comment the finding sits in, when it sits in one. Refusal identity has
    #: to reach comment level (decision 4) even though the *unit* withheld is the
    #: whole thread the comment belongs to.
    comment_id: str | None
    finding: SecretFinding

    def describe(self) -> str:
        """One line: which record, which field, which comment, which family."""
        inside = "" if self.comment_id is None else f" comment {bounded_quote(self.comment_id)}"
        return self.finding.describe(at=f"{self.identity.describe()}{inside} {self.field}")


@dataclass(frozen=True, slots=True)
class LandingCandidate:
    """One record as it arrived, before redaction and before the scan.

    The repository and the pull-request number are carried beside the payload
    rather than parsed out of it. A submission and a thread hold an ``event_key``
    that *encodes* both, and reading them back out would make the report's
    identity depend on a string format rather than on what the adapter knew.
    """

    repository: str
    pull_request_number: int
    payload: ReviewRecordPayload

    @property
    def identity(self) -> ReviewRecordIdentity:
        """Which record this is, for a verdict and for a report."""
        match self.payload:
            case ReviewEvent():
                return ReviewRecordIdentity(self.repository, self.pull_request_number)
            case ReviewSubmission() | ReviewThread():
                return ReviewRecordIdentity(
                    self.repository, self.pull_request_number, self.payload.external_id
                )


@dataclass(frozen=True, slots=True)
class RecordVerdict:
    """What the gate decided about one record.

    ``landing`` is the payload as it would be written -- redacted if the project
    asked for that -- or ``None`` when the record was refused. A caller writes
    exactly what this field holds: a landing service that re-derived the payload
    from the candidate would write the pre-redaction value, which is the one
    mistake this shape exists to make impossible.
    """

    identity: ReviewRecordIdentity
    landing: ReviewRecordPayload | None
    findings: tuple[ReviewSecretFinding, ...] = ()

    @property
    def refused(self) -> bool:
        """Whether this record was withheld whole."""
        return self.landing is None


@dataclass(frozen=True, slots=True)
class ReviewScanOutcome:
    """What one run's gate did, and what it found.

    The policy rides along with the verdicts because an empty finding list means
    two different things: under ``warn`` the records were scanned and are clean,
    under ``off`` nothing was scanned at all. ``redacted`` is here for the same
    reason -- a run with no findings and no redaction is not the same artifact as
    a run with no findings whose display names were replaced, and only this field
    tells a report which it was.
    """

    policy: SecretScanPolicy
    redacted: bool
    verdicts: tuple[RecordVerdict, ...] = ()

    @property
    def landing(self) -> tuple[ReviewRecordPayload, ...]:
        """The payloads a caller may write, in candidate order."""
        return tuple(verdict.landing for verdict in self.verdicts if verdict.landing is not None)

    @property
    def refusals(self) -> tuple[RecordVerdict, ...]:
        """Every record this run withheld. Non-empty only under ``block``."""
        return tuple(verdict for verdict in self.verdicts if verdict.refused)

    @property
    def findings(self) -> tuple[ReviewSecretFinding, ...]:
        """Every finding, in candidate order and then in field order."""
        return tuple(finding for verdict in self.verdicts for finding in verdict.findings)

    @property
    def clean(self) -> bool:
        """Whether this run found nothing and withheld nothing.

        Read by a composition root deciding an exit code. ``off`` answers
        ``True`` because it found nothing -- which is honest only beside
        ``policy``, and is why that field is not optional.
        """
        return not self.findings and not self.refusals

    @property
    def warned(self) -> bool:
        """Whether a finding was reported **and the record carrying it landed**.

        The one state neither :attr:`clean` nor :attr:`refusals` expresses, and
        the reason it is asked here rather than joined together by a reader:
        under ``warn`` a run that finds a secret lands the file anyway, reports
        the run as clean and exits zero. That is the project's own recorded
        choice (SEC-11) and not a defect -- but a caller reading ``clean`` alone
        cannot see it, and the join that would reveal it (``policy == "warn"``
        and a non-empty finding list) is a rule each reader would have to know.

        ``False`` under ``block`` however many findings there are: there the
        finding withheld the record, which is what :attr:`refusals` says.
        """
        return self.policy is SecretScanPolicy.WARN and bool(self.findings)


def screen_landing_candidates(
    candidates: Iterable[LandingCandidate], *, root: Path, config_file: Path
) -> ReviewScanOutcome:
    """Redact, scan, and decide which records may become files.

    Args:
        candidates: The records this run fetched, in the order they should land.
        root: The project root, the containment boundary for reading the
            configuration.
        config_file: The project's ``config.yaml``, composed by the caller from
            ``ProjectPaths``.

    Returns:
        One verdict per candidate, in candidate order, each carrying the payload
        that may be written or ``None``.

    Raises:
        ProjectConfigError: If ``.theurian/config.yaml`` exists and cannot be
            read, states a ``security.secretScan`` value this build does not
            recognise, or states a ``providers.review.redactParticipantNames``
            that is not a boolean. Not caught: the fault is in the operator's own
            file and no record is at fault, so labelling it as a scan result
            would send them to look at a review.

    **Both settings are read here rather than injected**, which is
    ``proposal_service._scan_for_secrets``' recorded reasoning: an injected
    policy is one a composition root can forget to wire, and a security control a
    caller can omit by omission is not a control. They are read **before any
    candidate is touched**, so ``off`` means what it says -- a scan wired ahead of
    the policy read would leave a project that hit a false positive unable to
    ingest anything while reporting the policy as ``off``.

    **The finding budget is per record, not per run.** ``propose accept`` spends
    one :data:`~theurian.security.content_secrets.MAX_FINDINGS` budget across
    every channel because a single finding refuses the whole acceptance, so a
    budget that ran out had already decided the outcome. Here refusal is
    per record: a budget shared across records would decide record N's fate using
    record 1's text, and a run whose first thread filled it would land every later
    record unscanned. What bounds the total is the adapter's own caps --
    ``infrastructure/github/limits.py``'s ``MAX_PULL_REQUESTS``,
    ``MAX_PAGES`` and ``MAX_COMMENTS_PER_THREAD`` bound how many records one call
    can produce -- so the run's ceiling is that record count times this constant
    rather than nothing at all.
    """
    policy = read_secret_scan_policy(root, config_file)
    redact = read_review_participant_redaction(root, config_file)

    verdicts = tuple(_verdict(candidate, policy=policy, redact=redact) for candidate in candidates)
    return ReviewScanOutcome(policy=policy, redacted=redact, verdicts=verdicts)


def _verdict(
    candidate: LandingCandidate, *, policy: SecretScanPolicy, redact: bool
) -> RecordVerdict:
    """One record's verdict, with redaction applied before anything is read."""
    landing = redacted(candidate.payload) if redact else candidate.payload
    if policy is SecretScanPolicy.OFF:
        return RecordVerdict(identity=candidate.identity, landing=landing)

    identity = candidate.identity
    findings: list[ReviewSecretFinding] = []
    for field, comment_id, text in _scanned_values(landing):
        remaining = MAX_FINDINGS - len(findings)
        if remaining <= 0:
            break
        findings.extend(
            ReviewSecretFinding(
                identity=identity, field=field, comment_id=comment_id, finding=finding
            )
            for finding in scan_text(text, max_findings=remaining)
        )
    if policy is SecretScanPolicy.BLOCK and findings:
        return RecordVerdict(identity=identity, landing=None, findings=tuple(findings))
    return RecordVerdict(identity=identity, landing=landing, findings=tuple(findings))


def redacted(payload: ReviewRecordPayload) -> ReviewRecordPayload:
    """``payload`` with every participant's display name replaced (R-12).

    A provider's node id is untouched on purpose: it is what the identity graph
    is built on, so redaction that dropped it would not anonymise the record, it
    would disconnect it. :class:`~theurian.domain.review.ReviewParticipant`
    records the same contract on the type.

    **An id that is the author's own login is replaced too, and the record itself
    is what says which one it is.** The GitHub adapter builds ``external_id`` as
    *node id or login* and ``display_name`` as *login or external id*, so an
    actor whose node id the response did not carry arrives here with the two
    fields holding the same author-chosen string. That equality is the signature
    this function keys on, and the substitution is :func:`_pseudonym` of the value
    -- deterministic, so the record keeps a stable identity across runs, while
    the login itself never reaches a file (PR #596 round 1, adversarial H-D).

    **The signature is deliberately conservative in one direction.** An actor with
    a node id and *no* login has the same two fields equal as well, so its node id
    is pseudonymised too. That trade is chosen: the cost is one project-local
    identifier where correlating back to GitHub needs the pseudonym's own mapping,
    against the cost of the other error, which is landing a raw login under a
    setting whose whole promise is that names do not land. A deleted account
    (``response.GHOST_LOGIN``) matches for the same reason and collapses onto one
    pseudonym -- which is the collapse ``ghost`` already is.

    **The population is every participant a record can reach**, which is four
    positions and not one: an event's author, a submission's author, each of a
    thread's comments' authors, and the participant a thread's resolution names.
    ``test_review_landing_gate.py::test_no_participant_reachable_from_a_landed_record_keeps_its_name``
    walks the returned object rather than these four positions, so a participant
    added anywhere in ``domain/review.py`` fails there instead of being missed.
    """
    match payload:
        case ReviewEvent():
            return replace(payload, author=_redacted_participant(payload.author))
        case ReviewSubmission():
            return replace(payload, author=_redacted_participant(payload.author))
        case ReviewThread():
            return replace(
                payload,
                comments=tuple(
                    replace(comment, author=_redacted_participant(comment.author))
                    for comment in payload.comments
                ),
                resolution=_redacted_resolution(payload.resolution),
            )


def _redacted_participant(participant: ReviewParticipant) -> ReviewParticipant:
    identified = participant.external_id != participant.display_name
    return replace(
        participant,
        external_id=(
            participant.external_id if identified else _pseudonym(participant.external_id)
        ),
        display_name=REDACTED_DISPLAY_NAME,
    )


def _pseudonym(external_id: str) -> str:
    """A stable stand-in for ``external_id``, carrying none of its characters.

    SHA-256 rather than anything cheaper because the property wanted is
    determinism *and* one-wayness: a run next month must produce the same
    pseudonym for the same person, and a reader holding a landed record must not
    be able to run the mapping backwards over the small space of plausible
    logins any faster than by guessing them. Guessing them is not prevented --
    the input space is small and unsalted by design, since a salt no run shares
    would give the same person a different id in every project and break exactly
    the linkage R-12 keeps. What this buys is that the raw string is not *in* the
    file; SECURITY.md states the control at that strength and not higher.

    ``surrogatepass`` because the value arrives from ``json.loads``, which
    decodes ``\\ud800`` into a lone surrogate that plain UTF-8 encoding refuses --
    and a ``UnicodeEncodeError`` raised here would be a traceback at an operator
    over a document GitHub sent, in a module whose whole job is to decide what a
    file may contain.
    """
    digest = hashlib.sha256(external_id.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{REDACTED_ID_PREFIX}{digest[:_PSEUDONYM_DIGITS]}"


def _redacted_resolution(resolution: ReviewResolution | None) -> ReviewResolution | None:
    if resolution is None or resolution.resolved_by is None:
        return resolution
    return replace(resolution, resolved_by=_redacted_participant(resolution.resolved_by))


def _scanned_values(payload: ReviewRecordPayload) -> Iterator[tuple[str, str | None, str]]:
    """Every author-controlled value in a record, as ``(field, comment id, text)``.

    Decision 3's untrusted rows, **and every participant's** ``external_id``.
    A *record's* ids, its timestamps, shas, states, line numbers, the
    pull-request number, the linked issue ids and the ``event_key`` are the
    provider's and are not here; the ``SourceAnchor`` and the run stamp are
    Theurian's own writes and are not here either.

    **A participant id is the row decision 3's table got wrong, and its own note
    now says so.** The table put ``external_id`` on the provider's side, which
    holds only while a node id exists: the GitHub adapter falls back to the
    author's *login*, and a login is a string its owner chooses and can change.
    **Which of the two a given id is cannot be decided here.** What reaches this
    module is a string, and a rule that recognised node-id shapes would be a
    second copy of the adapter's mapping -- wrong the day GitHub mints ids in
    another shape, and wrong in the direction that skips a login. So the id is
    read in both redaction states rather than only in the state where it can be
    author-chosen; a node id the detector does flag costs one refused record
    naming ``author.externalId``, which is the cheaper of the two errors
    (PR #596 round 1, adversarial H-D).

    ``milestone`` and ``file_path`` are yielded only when the provider gave one:
    ``None`` is an absence, and scanning the string ``"None"`` would be scanning
    this module's own rendering.

    ``tests/unit/test_review_scan_population.py`` is what keeps this list level
    with the codec's: every string a landed record carries is yielded here or
    named on that file's exemption list, and every value yielded here is one the
    codec writes.
    """
    match payload:
        case ReviewEvent():
            yield from _event_values(payload)
        case ReviewSubmission():
            yield _FIELD_LITERALS["body"], None, payload.body
            yield _FIELD_LITERALS["author"], None, payload.author.display_name
            yield _FIELD_LITERALS["author_id"], None, payload.author.external_id
        case ReviewThread():
            yield from _thread_values(payload)


def _event_values(event: ReviewEvent) -> Iterator[tuple[str, str | None, str]]:
    """The seven author-controlled positions on a pull-request event.

    Four of them look structural and are not: anyone who can open a pull request
    chooses its labels, its head branch name and its milestone (decision 3's most
    easily mistaken row), so each carries text a person wrote -- and the author's
    ``external_id`` is a login whenever the response carried no node id, which is
    the row the table itself had wrong.
    """
    yield _FIELD_LITERALS["title"], None, event.title
    yield _FIELD_LITERALS["body"], None, event.body
    yield _FIELD_LITERALS["author"], None, event.author.display_name
    yield _FIELD_LITERALS["author_id"], None, event.author.external_id
    yield _FIELD_LITERALS["head_ref_name"], None, event.head_ref_name
    for index, label in enumerate(event.labels):
        yield f"labels[{index}]", None, label
    if event.milestone is not None:
        yield _FIELD_LITERALS["milestone"], None, event.milestone


def _thread_values(thread: ReviewThread) -> Iterator[tuple[str, str | None, str]]:
    """A thread's own path, every comment in it, and the participant that closed it.

    The comment id rides along so a refusal reaches comment level even though the
    *unit* withheld is the whole thread -- which is the grain the evidence store
    writes, because a thread with no comments is not a value the domain can hold.
    """
    if thread.file_path is not None:
        yield _FIELD_LITERALS["file_path"], None, thread.file_path
    for index, comment in enumerate(thread.comments):
        yield from _comment_values(index, comment)
    if thread.resolution is not None and thread.resolution.resolved_by is not None:
        yield (
            _FIELD_LITERALS["resolved_by"],
            None,
            thread.resolution.resolved_by.display_name,
        )
        yield (
            _FIELD_LITERALS["resolved_by_id"],
            None,
            thread.resolution.resolved_by.external_id,
        )


def _comment_values(index: int, comment: ReviewComment) -> Iterator[tuple[str, str | None, str]]:
    yield f"comments[{index}].body", comment.external_id, comment.body
    yield (
        f"comments[{index}].author.displayName",
        comment.external_id,
        comment.author.display_name,
    )
    yield (
        f"comments[{index}].author.externalId",
        comment.external_id,
        comment.author.external_id,
    )
