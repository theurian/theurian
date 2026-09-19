"""``review.generateKnowledgeCandidate``'s service half (ADR-0033).

Theurian runs no model here. The caller authors the generalisation -- title,
body, ``kind``, ``category`` -- and this service does the three things that are
verification and packaging: it recomputes the promotion gate from the **stored
record**, verifies the caller's ``fixCommit`` against the **local repository**,
and routes the result through the draft-only proposal facade so a human reviews
it (ADR-0013 point 7, FR-V4).

**No gate signal is satisfied by a caller saying so** (decision 3). Five are
recomputed from the record, ``fix_commit_present`` is supplied-and-verified, and
``generalizable`` is satisfied by the submission itself -- offering a
generalisation *is* the claim the gate forwards. That there is no boolean on the
wire for any of them is structural and held as such:
``tests/unit/test_candidate_generation.py::test_the_submission_type_declares_no_gate_signal``
intersects :class:`CandidateSubmission`'s fields with the gate's own, read live.

**The record load is two resolves, not one.** ``pull_request_merged`` and
``ci_successful`` are fields of :class:`~theurian.domain.review.ReviewEvent`, not
of the thread, so the thread's record is resolved by the caller's key and the
pull request's by the number parsed out of the thread's ``event_key`` -- through
the *same* visibility resolve, so a withheld pull-request record refuses exactly
as a withheld thread does.

**A record the resolve does not answer for refuses uniformly, and nothing is read
on that path** (decision 5). Withheld and absent are the same answer by
construction: ``ReviewSearchBuilder`` drops a withheld key before a row exists,
so there is no flag a later read could consult. The resolve therefore runs before
the evidence read, the commit verification and the draft -- the steps that cost
more than a lookup -- which is the duration half of the bind and not only the
text.

**A thread with no ``file_path`` refuses, and that is a recorded decision**
(ADR-0033 decision 3's amendment, 2026-09-18). For such a thread the *touches
that path* half of the verification has nothing to check and what is left is bare
existence, which is very nearly no check and arrives exactly where verification
is weakest; widening refuse into accept later is additive, while narrowing accept
into refuse would be a breaking change. Its refusal says so in its own words
rather than borrowing the commit-verification one: ``mcp/review_search.py``'s
``review_record`` emits ``filePath`` on every record it builds, the ``None`` ones
included, so naming the property discloses nothing -- and *this thread cannot
generate a candidate in v1* is a different instruction from *go and find a better
commit*.

**The two commit-verification failures arrive as one refusal.** *That commit does
not exist* and *that commit does not touch this thread's file* differ by a fact
about the repository's contents, not about the request, so the verdict's
three-way answer is collapsed here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, final

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.proposal_service import DraftedProposal, ProposalRequest
from theurian.application.review_landing_gate import ReviewRecordPayload
from theurian.domain.enums import KnowledgeKind, ReviewCommentCategory, ReviewThreadState
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import ItemId, RevisionId
from theurian.domain.ports.determinism import Clock
from theurian.domain.proposal import Evidence
from theurian.domain.review import (
    FixCommitVerdict,
    KnowledgeCandidate,
    PromotionGate,
    ReviewEvent,
    ReviewThread,
)
from theurian.domain.review_ingest import bounded_quote
from theurian.domain.review_search import untransportable_reason
from theurian.domain.values import MARKDOWN

#: ``(repository, record_key) -> relative evidence path``, or ``None`` when the
#: built review-search store answers for no such row. The composition root backs
#: it with that store's by-key lookup, which is where withheld and absent become
#: the same answer.
ResolveEvidencePath = Callable[[str, str], str | None]

#: ``relative evidence path -> parsed record``, through the codec, so a stored
#: file is validated by the domain constructors rather than trusted as JSON.
ReadEvidenceRecord = Callable[[str], ReviewRecordPayload]

#: ``(fix_commit, file_path) -> verdict``, against the local git repository.
VerifyFixCommit = Callable[[str, str], FixCommitVerdict]

#: The thread states that make ``not_dismissed_or_outdated`` false. ``DISMISSED``
#: is here although the shipped adapter never emits it: the codec accepts one, so
#: a clone-delivered evidence file can carry it (T-24).
_EXCLUDED_STATES: Final = frozenset({ReviewThreadState.OUTDATED, ReviewThreadState.DISMISSED})

#: The pull request's number at the end of a thread's ``event_key``
#: (``provider:owner/name#number``), which is that record's own key. ``re.ASCII``
#: for ``review_search_builder``'s load-bearing reason: ``event_key`` is
#: author-controlled data a clone can deliver (T-24), and without the flag ``\d``
#: ranges over every Unicode decimal, so a key ending in a fullwidth-digit number
#: (U+FF10..U+FF19) parses to a record key no store holds rather than to nothing.
_PULL_REQUEST_NUMBER: Final = re.compile(r"#(\d+)\Z", re.ASCII)

#: What every record that does not arrive is refused with, and a **constant**: it
#: interpolates nothing -- not the key, not the repository, nothing read from the
#: store -- the shape ``mcp/tools.py``'s ``REVIEW_SEARCH_UNAVAILABLE_REFUSAL``
#: takes for the same reason, down to saying so in the text.
#:
#: The counter-argument is real and loses on one clause: the bytes this used to
#: echo were the caller's own request and carry zero bits about the store today,
#: but decision 5 is the seam #575's withheld class arrives on, and a constant is
#: the shape that cannot regress into an oracle when it does.
UNRESOLVED_RECORD_REFUSAL: Final = (
    "Theurian cannot generate a candidate for that record: the review evidence it needs "
    "is not in this installation's built review store. This refusal message is a "
    "constant: it carries nothing from your request or from any project's contents."
)

#: The cure that travels with it, constant for the same reason -- the repository is
#: a placeholder rather than the caller's own string.
UNRESOLVED_RECORD_CURE: Final = (
    "Land the repository's review history and project it: `theurian review ingest "
    "<owner>/<name>` followed by `theurian review build`."
)


class CandidateGenerationError(TheurianError):
    """No candidate was generated, with the cure for the reason it was not."""

    def __init__(self, message: str, *, remedy: str) -> None:
        self.remedy = remedy
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CandidateSubmission:
    """One caller's generalisation, plus the two values Theurian verifies for it.

    ``fix_commit`` is on the wire because the stored record has none to read; no
    gate signal is, which is what keeps the gate off the forgeable list.
    """

    repository: str
    record_key: str
    fix_commit: str
    item_id: ItemId
    title: str
    body: str
    kind: KnowledgeKind
    category: ReviewCommentCategory
    owner: str
    author: str
    description: str
    evidence: Evidence
    labels: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    namespace: str | None = None
    expected_revision: RevisionId | None = None


@dataclass(frozen=True, slots=True)
class GeneratedCandidate:
    """The candidate that met the gate, and the proposal it was drafted into."""

    candidate: KnowledgeCandidate
    proposal: DraftedProposal


@final
class CandidateGenerator:
    """Verifies a submission against the stored record and lands a proposal."""

    def __init__(
        self,
        *,
        resolve_evidence_path: ResolveEvidencePath,
        read_record: ReadEvidenceRecord,
        verify_fix_commit: VerifyFixCommit,
        drafts: DraftOnlyProposals,
        clock: Clock,
    ) -> None:
        self._resolve_evidence_path = resolve_evidence_path
        self._read_record = read_record
        self._verify_fix_commit = verify_fix_commit
        self._drafts = drafts
        self._clock = clock

    def generate(self, submission: CandidateSubmission) -> GeneratedCandidate:
        """Verify the submission against the stored record and draft it as a proposal.

        Every refusal is raised before the draft, so a call that produces no
        candidate leaves no proposal directory behind either.
        """
        thread = self._thread(submission)
        if thread.file_path is None:
            raise _no_file_anchor(submission)
        # The stored `file_path` is author-controlled data a clone can deliver
        # (T-24), and `verify` hands it to git and UTF-8-encodes it for the
        # membership check. A value git cannot receive -- a lone surrogate a
        # `\udcXX` escape in a landed evidence file decodes to -- is refused here,
        # before the verification, so the untransportable path never reaches the
        # encode that would raise `UnicodeEncodeError` outside the adapter's
        # fail-closed `except`. The builder refuses the same shape where it
        # projects a record; this is its symmetric check on the re-read path.
        reason = untransportable_reason(thread.file_path)
        if reason is not None:
            raise _untransportable_file_path(reason)
        event = self._event(submission, thread)
        gate = PromotionGate(
            pull_request_merged=event.merged,
            thread_resolved=thread.state is ReviewThreadState.RESOLVED,
            fix_commit_present=(
                self._verify_fix_commit(submission.fix_commit, thread.file_path)
                is FixCommitVerdict.VERIFIED
            ),
            not_dismissed_or_outdated=thread.state not in _EXCLUDED_STATES,
            ci_successful=event.ci_successful,
            generalizable=True,
            has_evidence=bool(thread.comments),
        )
        if not gate.is_satisfied:
            raise _gate_refusal(submission, gate, event, thread.file_path)

        candidate = KnowledgeCandidate(
            candidate_id=f"{thread.external_id}:{submission.item_id.value}",
            project_id=thread.project_id,
            source_thread_id=thread.external_id,
            proposed_item_id=submission.item_id,
            title=submission.title,
            body=submission.body,
            kind=submission.kind,
            category=submission.category,
            gate=gate,
            evidence=submission.evidence.anchors,
            generated_at=self._clock.now(),
            generator_model=submission.evidence.model,
        )
        # `local=False` is ADR-0013 point 7: `--local` writes into the managed
        # ignore block, where the human review FR-V4 relies on cannot reach it.
        proposal = self._drafts.draft(_request(submission, candidate), local=False)
        return GeneratedCandidate(candidate=candidate, proposal=proposal)

    def _thread(self, submission: CandidateSubmission) -> ReviewThread:
        record = self._record(submission, submission.record_key)
        if not isinstance(record, ReviewThread):
            raise _unresolved()
        return record

    def _event(self, submission: CandidateSubmission, thread: ReviewThread) -> ReviewEvent:
        number = _PULL_REQUEST_NUMBER.search(thread.event_key)
        if number is None:
            raise _unresolved()
        record = self._record(submission, number.group(1))
        if not isinstance(record, ReviewEvent):
            raise _unresolved()
        return record

    def _record(self, submission: CandidateSubmission, record_key: str) -> ReviewRecordPayload:
        relative_path = self._resolve_evidence_path(submission.repository, record_key)
        if relative_path is None:
            raise _unresolved()
        return self._read_record(relative_path)


def _unresolved() -> CandidateGenerationError:
    """The shared refusal for a record that does not arrive.

    Three shapes reach it: a key the resolve does not answer for, a stored record
    whose kind is not the one its key promised, and -- once #575 creates the class
    -- a withheld record, which the resolve answers for exactly as it answers for
    an absent one. They share a sentence because the difference between them is
    material the caller was not granted (decision 5). It takes no argument, which
    is how the constants above stay the whole of what this path can publish.
    """
    return CandidateGenerationError(UNRESOLVED_RECORD_REFUSAL, remedy=UNRESOLVED_RECORD_CURE)


def _untransportable_file_path(reason: str) -> CandidateGenerationError:
    """A stored ``file_path`` git cannot be handed refuses before the verification (T-24).

    ``reason`` is ``untransportable_reason``'s own phrase -- the single source of
    truth ``review_search_builder._refuse_untransportable`` folds in too -- so both
    consumers of a stored path name the same defect. The untransportable value is
    not echoed: it has no clean rendering by construction (that is the defect), and
    the reason phrase is a constant that says what is wrong without carrying it.
    """
    return CandidateGenerationError(
        f"The stored thread's file anchor cannot be verified: {reason}.",
        remedy=(
            "The stored review record carries a file anchor Theurian cannot check. "
            "Re-ingest and rebuild the review evidence so the record matches the "
            "provider's own: `theurian review ingest <owner>/<name>` followed by "
            "`theurian review build`."
        ),
    )


def _no_file_anchor(submission: CandidateSubmission) -> CandidateGenerationError:
    return CandidateGenerationError(
        f"The stored thread {bounded_quote(submission.record_key)} carries no file anchor, so "
        f"its fix-commit signal cannot be verified: this thread cannot generate a candidate "
        f"in v1.",
        remedy=(
            "Propose the generalisation directly instead -- `theurian propose --item-id "
            "<id> --title <title> --body-file <path>` -- or generate from a thread whose "
            "`filePath` the review.search record names."
        ),
    )


def _gate_refusal(
    submission: CandidateSubmission,
    gate: PromotionGate,
    event: ReviewEvent,
    file_path: str,
) -> CandidateGenerationError:
    """Name every unmet signal, why the record makes it unmet, and what cures it.

    ``ci_successful`` reads the stored tri-state a second time, because
    :meth:`PromotionGate.unmet` reports the same name for ``None`` and ``False``
    and the two are different instructions: go and get a CI result, against this
    thread is not a candidate (decision 4). ``fix_commit_present`` says only that
    the conjunction failed -- never which half -- which is what keeps the two
    verdicts one refusal.
    """
    quoted_path = bounded_quote(file_path)
    reasons = {
        "pull_request_merged": "the stored pull request record does not record it merged",
        "thread_resolved": "the stored record does not record this conversation resolved",
        "fix_commit_present": (
            f"the commit this call names is not one this repository has that touched {quoted_path}"
        ),
        "not_dismissed_or_outdated": (
            "the stored record marks this conversation dismissed or outdated"
        ),
        "ci_successful": (
            "nobody has told Theurian whether this thread's fix passed"
            if event.ci_successful is None
            else "this thread's fix did not pass"
        ),
        "generalizable": "this submission offers no generalisation to forward",
        "has_evidence": "the stored thread carries no comments",
    }
    # Author-controlled text (T-24) is named in prose and kept out of the command a
    # reader may paste; `_refresh` does the same for the caller's `repository`.
    commit_cure = (
        f"Name a fixCommit this repository has that touched {quoted_path}, the `filePath` "
        f"on this thread's review.search record; `git log --oneline -- <that path>` lists "
        f"the commits that did. If that is not the file this thread was anchored to, the "
        f"stored record is the thing to correct rather than the commit -- a review "
        f"evidence directory is source a clone can deliver (T-24), and `theurian review "
        f"ingest <owner>/<name>` followed by `theurian review build` replaces it with the "
        f"provider's own."
    )
    cures = dict.fromkeys(
        commit_cure if name == "fix_commit_present" else _refresh(submission)
        for name in gate.unmet()
    )
    return CandidateGenerationError(
        f"The promotion gate is unmet for {bounded_quote(submission.record_key)}: "
        + "; ".join(f"{name}: {reasons[name]}" for name in gate.unmet())
        + ".",
        remedy=" ".join(cures),
    )


def _refresh(submission: CandidateSubmission) -> str:
    return (
        f"Obtain the signal upstream, then refresh the stored record for "
        f"{bounded_quote(submission.repository)}: `theurian review ingest <owner>/<name>` "
        f"followed by `theurian review build`."
    )


def _request(submission: CandidateSubmission, candidate: KnowledgeCandidate) -> ProposalRequest:
    """ADR-0033 decision 1's candidate-to-request table, applied.

    ``trust_level`` and ``sensitivity`` are the *candidate's*: the first is fixed
    by its declaration (``init=False``), so there is no wire value to read, and
    omitting it would leave the loader asserting ``unverified`` on the written
    migration -- a different claim about the same knowledge. ``content_type`` is
    fixed because a generalisation has no source file whose suffix could say
    otherwise. ``author`` is the human the migration schema requires and
    ``evidence.agent_id`` the agent that produced the run: two values with two
    readers, neither filled from the other here.
    """
    return ProposalRequest(
        item_id=candidate.proposed_item_id,
        title=candidate.title,
        kind=candidate.kind,
        owner=submission.owner,
        author=submission.author,
        description=submission.description,
        body=candidate.body,
        content_type=MARKDOWN,
        evidence=Evidence(
            agent_id=submission.evidence.agent_id,
            task_id=submission.evidence.task_id,
            model=submission.evidence.model,
            reasoning=submission.evidence.reasoning,
            anchors=candidate.evidence,
        ),
        source_anchors=candidate.evidence,
        labels=submission.labels,
        scope_paths=submission.scope_paths,
        trust_level=candidate.trust_level,
        sensitivity=candidate.sensitivity,
        namespace=submission.namespace,
        expected_revision=submission.expected_revision,
    )
