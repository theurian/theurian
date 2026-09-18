"""``review.generateKnowledgeCandidate``'s service half, through its own seams (ADR-0033).

The MCP tool is a later cluster's. What is driven here is the service under it --
:class:`~theurian.application.candidate_generation.CandidateGenerator` -- so a
defect is located in the gate, the verification or the request mapping rather
than in the wire.

**Every thread here is shaped the way the shipped adapter shapes one, and that is
the point of the file.** ADR-0033's Compliance section records that the suite
builds a :class:`~theurian.domain.review.PromotionGate` in exactly one place, out
of literal ``True``s, and that *nothing anywhere joins a gate to a*
``ReviewResolution`` -- which is how a design premise that the stored record
supplied a ``fix_commit`` stayed green for a milestone while being false. So
every resolution built below carries what ``review_provider.py`` builds: ``state``
and ``resolved_by``, with ``fix_commit`` **absent**. A gate that read the signal
off the record would refuse every test in this file.

**The collaborators are callables**, the shape ``review_search_builder`` uses:
the visibility resolve is the built review-search store's by-key lookup, the
record read is the evidence file behind it, and the ``fixCommit`` verification is
the local git repository. Injected here, bound by the composition root there.

Pure: no filesystem, no SQLite, no subprocess. The real git verification and the
written proposal directory are
``integration/test_candidate_generation_on_disk.py``'s.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.candidate_generation import (
    UNRESOLVED_RECORD_REFUSAL,
    CandidateGenerationError,
    CandidateGenerator,
    CandidateSubmission,
)
from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.proposal_service import (
    DraftedMigration,
    DraftedProposal,
    ProposalRequest,
)
from theurian.application.review_landing_gate import ReviewRecordPayload
from theurian.domain.enums import (
    KnowledgeKind,
    ReviewCommentCategory,
    ReviewThreadState,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.identifiers import (
    AgentId,
    ItemId,
    MigrationId,
    ProjectId,
    ProposalId,
    RevisionId,
    TaskId,
)
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.proposal import Evidence
from theurian.domain.review import (
    FixCommitVerdict,
    PromotionGate,
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.domain.values import ContentHash, MediaType

pytestmark = pytest.mark.unit

PROJECT: Final = ProjectId("demo")
REPOSITORY: Final = "theurian/theurian"

#: The thread's node id, which is also its record key inside the repository
#: (``EvidenceRecord.record_key``). GitHub's own shape prefix and deliberately
#: not a ULID -- it carries an ``O``, which a ULID cannot, because this is a
#: provider identifier Theurian stores rather than one it mints.
#:
#: **The tail is a deliberate run of one character, and it has to stay one.** A
#: realistic-looking node id reads as a credential to the secret scan: this
#: fixture was ``PRRT_kwDOABCD1M5abcde``, which tripped gitleaks'
#: ``generic-api-key`` at entropy 4.202 on the full-history scan of the commit
#: that introduced it. Measured with gitleaks 8.30.1 against this repository's
#: own ``.gitleaks.toml``: the old value is reported in a plain ``NAME = "..."``
#: binding, and this one is reported in neither that nor the annotated form --
#: the single-character run is what holds its entropy at 2.249. Making it look
#: real again re-trips the Secret scan job, and on ``main`` it would do so under
#: a fresh fingerprint no ``.gitleaksignore`` entry covers. That is why the value
#: moved rather than being suppressed per occurrence.
THREAD_KEY: Final = "PRRT_kwDOaaaaaaaaaaaa"

#: The pull request's number, which is *its* record key -- a pull request is keyed
#: by the number a reviewer types, a thread by its node id. Two lookups, two keys.
PULL_REQUEST: Final = 431

#: Where each record's evidence file sits under ``.theurian/review/``. Opaque to
#: the service: it resolves a path from the store and hands it straight to the
#: reader, so the only property that matters here is that the two differ.
THREAD_FILE: Final = f"sha256-1f0e/review-thread/{THREAD_KEY}.json"
EVENT_FILE: Final = f"sha256-1f0e/pull-request/{PULL_REQUEST}.json"

#: The file the thread is anchored to. Author-controlled and served as data
#: (ADR-0030 decision 6); what reads it here is the ``fixCommit`` verification,
#: which asks git whether the named commit touched it.
FILE_PATH: Final = "packages/theurian-core/src/theurian/application/retrying.py"

#: The commit the caller names on the wire. Verified against the local repository,
#: never believed (ADR-0033 decision 3).
FIX_COMMIT: Final = "9f2c1ab7de4455660c1d8e39ab17c5d4e2f80b33"

EVIDENCE: Final = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-431"),
    model="claude-opus-5",
    reasoning="PR #431's thread settled the lock ordering; this generalises it.",
    anchors=(
        SourceAnchor(
            provider="github",
            source_uri="https://github.com/theurian/theurian/pull/431#discussion_r1",
            repository=REPOSITORY,
            file_path=FILE_PATH,
            line_start=118,
            line_end=124,
        ),
    ),
)

#: The human the migration records as ``author``, distinct from the agent
#: ``evidence.json`` records as ``agentId`` -- the migration schema's own rule,
#: and ADR-0033 decision 1's table row.
HUMAN_AUTHOR: Final = "dana@example.com"


def _participant(login: str) -> ReviewParticipant:
    return ReviewParticipant(
        provider="github", external_id=f"MDQ6VXNlcg-{login}", display_name=login
    )


def _event(*, merged: bool = True, ci_successful: bool | None = True) -> ReviewEvent:
    """A pull request as the adapter records one."""
    merged_at = datetime(2026, 9, 2, 11, 30, tzinfo=UTC) if merged else None
    return ReviewEvent(
        project_id=PROJECT,
        provider="github",
        repository=REPOSITORY,
        number=PULL_REQUEST,
        title="Take the lock after the read",
        body="Fixes the retry deadlock reported in the payments incident.",
        author=_participant("author"),
        created_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST}",
        head_commit="a" * 40,
        base_commit="b" * 40,
        head_ref_name="fix/retry-deadlock",
        labels=("bug",),
        merged=merged,
        merge_commit=("c" * 40) if merged else None,
        merged_at=merged_at,
        ci_successful=ci_successful,
    )


def _thread(
    event: ReviewEvent,
    *,
    state: ReviewThreadState = ReviewThreadState.RESOLVED,
    file_path: str | None = FILE_PATH,
) -> ReviewThread:
    """A conversation as the adapter records one, ``fix_commit`` absent.

    ``resolution`` is built the way ``review_provider.py:_thread`` builds it:
    ``state`` and ``resolved_by``, with ``resolved_at`` and ``fix_commit`` left at
    ``None`` because GitHub's thread object carries neither (ADR-0030 decision 5,
    ADR-0033 decision 3).
    """
    resolved = state is ReviewThreadState.RESOLVED
    return ReviewThread(
        external_id=THREAD_KEY,
        project_id=PROJECT,
        event_key=event.external_key,
        file_path=file_path,
        comments=(
            ReviewComment(
                external_id="PRRC_kwDOABCD1M5aaaaa",
                author=_participant("reviewer"),
                body="This will deadlock under retry. Take the lock after the read.",
                created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
                category=ReviewCommentCategory.RELIABILITY_RULE,
                line_start=118,
                line_end=124,
            ),
        ),
        state=state,
        resolution=(
            ReviewResolution(state=ReviewThreadState.RESOLVED, resolved_by=_participant("reviewer"))
            if resolved
            else None
        ),
        line_start=118,
        line_end=124,
        commit_sha="d" * 40,
    )


class _Corpus:
    """The built review-search store and the evidence files behind it, as two seams.

    ``resolve`` is the store's by-key lookup: a record that was withheld at build
    time has no row, so it answers ``None`` for exactly the same reason a record
    that never existed does (``ReviewSearchBuildRequest.withheld_record_keys``).
    ``read`` is the evidence file behind a row that *did* resolve, and it records
    every path it was asked for -- which is what lets a test assert that a miss
    touched no file at all.
    """

    def __init__(self, records: dict[str, ReviewRecordPayload], paths: dict[tuple[str, str], str]):
        self._records = records
        self._paths = paths
        self.reads: list[str] = []

    def resolve(self, repository: str, record_key: str) -> str | None:
        return self._paths.get((repository, record_key))

    def read(self, relative_path: str) -> ReviewRecordPayload:
        self.reads.append(relative_path)
        return self._records[relative_path]


def _corpus(event: ReviewEvent, thread: ReviewThread) -> _Corpus:
    return _Corpus(
        records={THREAD_FILE: thread, EVENT_FILE: event},
        paths={
            (REPOSITORY, THREAD_KEY): THREAD_FILE,
            (REPOSITORY, str(PULL_REQUEST)): EVENT_FILE,
        },
    )


class _RecordingDrafts:
    """A :class:`DraftSurface` that records the request and writes nothing.

    The real :class:`DraftOnlyProposals` facade is wrapped around it rather than
    faked, so the service is held to the surface ADR-0032 decision 8 narrows it
    to: a double standing in for the facade itself would let a generator reach
    ``accept`` and nothing here would notice.
    """

    def __init__(self) -> None:
        self.requests: list[ProposalRequest] = []
        #: Which parent each draft was written under (ADR-0028). Recorded rather
        #: than discarded because a candidate drafted ``--local`` would be
        #: git-ignored, and a proposal nobody can review is not ADR-0013 point 7's
        #: shape whatever the migration says.
        self.local: list[bool] = []
        self._ids = SeededIdGenerator()

    def draft(self, request: ProposalRequest, *, local: bool = False) -> DraftedProposal:
        self.requests.append(request)
        self.local.append(local)
        proposal_id = ProposalId(self._ids.new_ulid().value)
        migration_id = MigrationId(self._ids.new_ulid().value)
        revision_id = RevisionId(self._ids.new_ulid().value)
        directory = Path("/proposals") / proposal_id.value
        return DraftedProposal(
            proposal_id=proposal_id,
            directory=directory,
            migration_id=migration_id,
            migration_file=directory / f"{migration_id.value}.yaml",
            revision_id=revision_id,
            expected_revision=request.expected_revision,
            body_file=directory / "body.md",
            evidence_file=directory / "evidence.json",
            content_file="../knowledge/body.md",
            content_sha256=ContentHash.of_text(request.body),
            body_destination=Path("/knowledge/body.md"),
        )

    def draft_from_document(
        self, document: Mapping[str, object], *, evidence: Evidence, local: bool = False
    ) -> DraftedMigration:
        raise AssertionError(
            f"the candidate path drafts a body and a revision, never an operations "
            f"document -- got {document!r} from {evidence.agent_id.value!r} "
            f"(local={local})"
        )


def _generator(
    corpus: _Corpus,
    drafts: _RecordingDrafts,
    *,
    verdict: FixCommitVerdict = FixCommitVerdict.VERIFIED,
) -> CandidateGenerator:
    return CandidateGenerator(
        resolve_evidence_path=corpus.resolve,
        read_record=corpus.read,
        verify_fix_commit=lambda _commit, _file_path: verdict,
        drafts=DraftOnlyProposals(drafts),
        clock=FrozenClock(),
    )


def _submission(**overrides: object) -> CandidateSubmission:
    """One caller's generalization, with the two fields Theurian verifies.

    ``fix_commit`` is on the wire because the record has none to read
    (ADR-0033 decision 3); no gate signal is, which
    :func:`test_the_submission_type_declares_no_gate_signal` holds structurally.
    """
    values: dict[str, object] = {
        "repository": REPOSITORY,
        "record_key": THREAD_KEY,
        "fix_commit": FIX_COMMIT,
        "item_id": ItemId("reliability.retry-lock-order"),
        "title": "Acquire locks after reads in retry-eligible paths",
        "body": "Acquire locks after reads, never before, in retry-eligible paths.\n",
        "kind": KnowledgeKind.CONVENTION,
        "category": ReviewCommentCategory.RELIABILITY_RULE,
        "owner": "platform-team",
        "author": HUMAN_AUTHOR,
        "description": "Generalise PR #431's deadlock thread into a locking rule",
        "evidence": EVIDENCE,
    }
    return CandidateSubmission(**{**values, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The happy path, and the control that the gate came out of the record.
# ---------------------------------------------------------------------------


def test_a_thread_meeting_every_signal_lands_a_proposal() -> None:
    """ADR-0033 decision 1: the tool's reachable behaviour is not only a refusal.

    The positive control the whole file rests on. ADR-0033 rejected two designs --
    reading ``fix_commit_present`` off the record, and treating ``generalizable``
    as underivable -- precisely because each shipped a tool that *structurally*
    could not produce a candidate while ``writeTools: true`` advertised it, which
    is ADR-0026's false-capability defect. Every refusal test below is satisfied
    by a build that refuses everything; this is what says the refusals are
    discriminating.
    """
    corpus = _corpus(_event(), _thread(_event()))
    drafts = _RecordingDrafts()

    generated = _generator(corpus, drafts).generate(_submission())

    assert len(drafts.requests) == 1, (
        f"a satisfiable thread produced {len(drafts.requests)} proposals through the "
        f"draft-only facade. One call, one proposal: ADR-0013 point 2's shape."
    )
    assert generated.candidate.gate.is_satisfied


def test_a_generated_candidate_is_drafted_where_a_reviewer_can_see_it() -> None:
    """ADR-0013 point 7: the proposal travels in the pull request, or nobody reviews it.

    ``ProposalService.draft``'s ``local`` picks the parent (ADR-0028), and
    ``--local`` writes into ``.theurian/proposals-local/``, which the managed
    ignore block keeps out of git. FR-V4 is the control that absorbs a wrong
    generalization, and it is exercised by a human reading the proposal in a pull
    request -- so a candidate drafted into the ignored location is one the gate
    admitted and no reviewer ever sees.
    """
    drafts = _RecordingDrafts()

    _generator(_corpus(_event(), _thread(_event())), drafts).generate(_submission())

    assert drafts.local == [False], (
        "the candidate was drafted into the git-ignored `--local` location, where "
        "the human review ADR-0013 point 7 requires cannot reach it"
    )


def test_the_gate_is_recomputed_from_the_stored_record() -> None:
    """ADR-0033 decision 3: a change to the record moves a signal (FR-V4).

    The control that stops every other test in this file from passing against a
    generator that ignores the gate entirely. One field of the *stored* record
    moves -- the pull request's ``merged`` -- and the refusal has to name the
    signal that reads it.

    ``pull_request_merged`` rather than ``has_evidence``, because ADR-0033
    decision 3 records that ``has_evidence`` is ``True`` over every thread that
    loads at all: ``ReviewThread.__post_init__`` refuses an empty ``comments``
    tuple and the codec decodes through that constructor, so a record edit cannot
    move it and a control built on it would pass on a generator that read
    nothing.
    """
    event = _event(merged=False)
    corpus = _corpus(event, _thread(event))

    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, _RecordingDrafts()).generate(_submission())

    assert "pull_request_merged" in str(refusal.value), (
        f"an unmerged pull request was refused with {str(refusal.value)!r}, which "
        f"does not name the signal that moved. Either the gate is not recomputed "
        f"from the stored record, or the refusal has stopped reporting "
        f"`PromotionGate.unmet()` -- which is what tells a caller which signal to "
        f"obtain instead of that the thread is unsuitable."
    )


@pytest.mark.parametrize(
    "state",
    [ReviewThreadState.OUTDATED, ReviewThreadState.DISMISSED],
    ids=["outdated", "dismissed"],
)
def test_a_thread_the_record_marks_outdated_or_dismissed_is_refused(
    state: ReviewThreadState,
) -> None:
    """ADR-0033 decision 3: the two state-derived signals come off the record too.

    ``pull_request_merged`` alone would leave the pair that reads
    ``ReviewThread.state`` unexercised, and hardcoding either to ``True`` is a
    one-line change with nothing to catch it: measured 2026-09-18 against a
    reference generator, ``thread_resolved=True`` survived every other test in
    this file.

    Both members are driven because the record reaches them by different routes.
    ``outdated`` is a state the shipped adapter emits directly
    (``review_provider.py`` maps ``isOutdated`` onto it); ``dismissed`` is one it
    never produces and the *codec* still accepts, which a clone-delivered
    evidence file can carry (threat model T-24). A gate that ranged only over what
    the adapter emits would promote the second.
    """
    event = _event()
    corpus = _corpus(event, _thread(event, state=state))

    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, _RecordingDrafts()).generate(_submission())

    named = str(refusal.value)
    assert "thread_resolved" in named and "not_dismissed_or_outdated" in named, (
        f"a {state.value} thread was refused with {named!r}, which does not name "
        f"both signals its stored state makes false. A signal fixed to `True` "
        f"reports a conversation as settled that the provider says was not."
    )


# ---------------------------------------------------------------------------
# `fixCommit` is verified, and its two failures are one refusal (decisions 3, 5).
# ---------------------------------------------------------------------------


def _refused_fix_commit(
    verdict: FixCommitVerdict,
) -> tuple[CandidateGenerationError, _RecordingDrafts]:
    corpus = _corpus(_event(), _thread(_event()))
    drafts = _RecordingDrafts()
    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, drafts, verdict=verdict).generate(_submission())
    return refusal.value, drafts


def _fix_commit_refusal(verdict: FixCommitVerdict) -> CandidateGenerationError:
    refusal, _drafts = _refused_fix_commit(verdict)
    return refusal


def test_a_fix_commit_that_names_no_commit_drafts_nothing() -> None:
    """ADR-0033 decision 3: the signal is satisfied by the verification, not the word.

    The caller names a commit and Theurian checks it, so a value that resolves to
    nothing in the repository -- a random forty hex digits, a commit from another
    clone, the fabricated ``"e" * 40`` this suite's own fixtures use -- does not
    satisfy ``fix_commit_present``. That class is the whole of what the check
    closes, and closing it is what separates a signal the caller writes from one
    it has to find something real to satisfy.

    Asserted on the *facade* and not only on the exception, because the failure
    that costs something is a proposal drafted before the gate refused it: a
    directory on disk, in a pull request, carrying a generalization that no
    verified commit backs.
    """
    _refusal, drafts = _refused_fix_commit(FixCommitVerdict.NO_SUCH_COMMIT)

    assert drafts.requests == [], (
        "an unverifiable fix commit still reached the draft-only facade, so the "
        "refusal came after a proposal directory had been asked for"
    )


def test_a_fix_commit_that_touches_no_file_here_is_refused_in_the_same_words() -> None:
    """ADR-0033 decisions 3 and 5: the two verification failures are one refusal.

    *That commit does not exist* and *that commit does not touch this thread's
    file* differ by a fact about the repository's contents, and the caller asking
    is one who was granted a review-evidence tool rather than the repository. Two
    messages here would be an oracle a caller walks: name a candidate sha, read
    which refusal came back, learn whether the object is present. It is the
    Milestone 5 family *an error that fires for one input and not another*, fixed
    now while the corpus has nothing withheld and it costs one branch.

    Byte-identical, and over both halves of the refusal -- the detail and the
    remedy -- because a cure that named the distinction would reopen the channel
    the message closed.

    The verification *does* know which happened: the seam answers with a
    three-member verdict because git answers three ways. Collapsing them is the
    service's decision, which is what makes this assertion something a change can
    break rather than something the types make impossible to state.
    """
    absent = _fix_commit_refusal(FixCommitVerdict.NO_SUCH_COMMIT)
    untouched = _fix_commit_refusal(FixCommitVerdict.TOUCHES_NOTHING_HERE)

    assert (str(absent), absent.remedy) == (str(untouched), untouched.remedy), (
        f"the two commit-verification refusals differ:\n"
        f"  no such commit   -- {str(absent)!r} / remedy {absent.remedy!r}\n"
        f"  touches nothing  -- {str(untouched)!r} / remedy {untouched.remedy!r}\n\n"
        f"ADR-0033 decision 5 binds them to one refusal in text and in duration, "
        f"because the difference between them is a fact about the repository and "
        f"not about the caller's request."
    )


def test_a_fix_commit_that_touches_the_threads_file_is_accepted() -> None:
    """The control without which refusing both the same way is satisfied by refusing all.

    ADR-0033's Compliance section names this one explicitly: a build that refused
    every commit would pass the byte-identity assertion above and would have
    verified nothing. This is the third arm -- the verdict that proceeds.
    """
    drafts = _RecordingDrafts()

    _generator(_corpus(_event(), _thread(_event())), drafts).generate(_submission())

    assert len(drafts.requests) == 1


def test_a_thread_with_no_file_anchor_is_refused_in_its_own_words() -> None:
    """ADR-0033 decision 3's amendment: an unverifiable signal does not pass.

    The adapter yields ``file_path=None`` for any node GitHub anchors to no string
    ``path`` (``review_provider.py:665``). For such a thread the *touches that
    path* half of the verification has nothing to check, and what is left is bare
    existence -- a choice set measured at the repository's whole history, 285
    commits at ``be977ea7``, which is very nearly no check and arrives exactly
    where verification is weakest. Slice B5 decided **refuse**, recording that
    widening refuse into accept later is additive while narrowing accept into
    refuse is a breaking change.

    **Its own words, and deliberately not decision 5's single commit refusal.**
    ``filePath`` is a published key on every ``review.search`` record
    (``mcp/review_search.py``'s ``review_record`` emits every key including the
    ``None`` ones), so the property is already caller-visible and naming it
    discloses nothing. The honest instruction is *this thread cannot generate a
    candidate in v1*, rather than sending the caller to hunt for a better commit
    -- which is what the shared refusal would say.
    """
    event = _event()
    corpus = _corpus(event, _thread(event, file_path=None))

    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, _RecordingDrafts()).generate(_submission())

    commit_refusal = _fix_commit_refusal(FixCommitVerdict.NO_SUCH_COMMIT)
    assert str(refusal.value) != str(commit_refusal), (
        f"a thread with no file anchor was refused in the commit-verification "
        f"refusal's own words: {str(refusal.value)!r}. The two say different things "
        f"to a caller -- one means go and find the right commit, the other means "
        f"this thread cannot generate a candidate at all -- and ADR-0033 decision "
        f"3's amendment records why the second is not folded into the first."
    )
    assert "file" in str(refusal.value).casefold(), (
        f"the refusal {str(refusal.value)!r} does not name the missing file anchor, "
        f"so a caller is told no and not why."
    )


# ---------------------------------------------------------------------------
# `ci_successful` is tri-state, unknown is unmet, and it is named (decision 4).
# ---------------------------------------------------------------------------
#
# Three inputs, because two of them would not distinguish "unknown is unmet" from
# "the gate ignores this signal" -- and a fourth assertion, because `unmet()`
# returns the name `ci_successful` for a `None` and a `False` alike. Without the
# fourth, an adapter that flattened `None` to `False` -- the alternative ADR-0033
# rejects, quoting ADR-0030's `a required field filled by the adapter is a
# fabricated measurement` -- passes all three.


def _ci_refusal(ci_successful: bool | None) -> CandidateGenerationError:
    event = _event(ci_successful=ci_successful)
    corpus = _corpus(event, _thread(event))
    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, _RecordingDrafts()).generate(_submission())
    return refusal.value


def test_an_unknown_ci_outcome_does_not_satisfy_the_gate() -> None:
    """ADR-0033 decision 4: ``None`` is unmet, and the caller is told which signal.

    ``ReviewEvent.ci_successful`` is already ``bool | None`` and the adapter maps
    "pending, expected, absent, or a value this version does not recognise" to
    ``None``. Treating that as satisfying would promote a thread whose fix nobody
    has seen pass, silently -- the caller could not tell a green run from no run.
    """
    refusal = _ci_refusal(None)

    assert "ci_successful" in str(refusal), (
        f"an unknown CI outcome was refused with {str(refusal)!r}, which does not "
        f"name `ci_successful`. The refusal's job is to tell the caller which "
        f"signal to obtain, not that the thread is unsuitable."
    )
    assert "nobody has told Theurian whether this thread's fix passed" in str(refusal), (
        f"the unknown-CI refusal is {str(refusal)!r}. ADR-0033 decision 4 fixes the "
        f"sentence, because `unmet()` cannot produce it: the tri-state has to reach "
        f"the message and not only the predicate."
    )


def test_a_failed_ci_outcome_does_not_satisfy_the_gate() -> None:
    """ADR-0033 decision 4: ``False`` is unmet too, and says something else.

    The sibling that makes the pair meaningful. Both refuse; what differs is the
    instruction -- go and get a CI result, against this thread is not a candidate.
    """
    refusal = _ci_refusal(False)

    assert "ci_successful" in str(refusal)
    assert "this thread's fix did not pass" in str(refusal), (
        f"the failed-CI refusal is {str(refusal)!r}, which is not the sentence "
        f"ADR-0033 decision 4 records for a definite `False`."
    )


def test_the_unknown_ci_refusal_is_not_the_failed_one() -> None:
    """The assertion that separates decision 4 from the adapter-flattening it rejects.

    ``PromotionGate.unmet()`` returns the names of the falsy signals, and ``None``
    and ``False`` are both falsy -- so it reports ``"ci_successful"`` for either
    and the two messages are identical unless the refusal reads the stored
    tri-state a second time. An implementation that flattened ``None`` to
    ``False`` at the adapter passes the three tests above and fails this one,
    which is the whole reason it is written.
    """
    unknown = _ci_refusal(None)
    failed = _ci_refusal(False)

    assert str(unknown) != str(failed), (
        f"unknown and failed CI both refuse with {str(unknown)!r}. The tri-state "
        f"reached the predicate and not the message, which is the state ADR-0033 "
        f"decision 4 says costs the caller the difference between `go and get a CI "
        f"result` and `this thread is not a candidate`."
    )


def test_a_successful_ci_outcome_satisfies_the_gate() -> None:
    """The third input, without which the two refusals do not distinguish anything.

    Two refusals alone are satisfied by a gate that ignores ``ci_successful``
    entirely and refuses for some other reason. This is what makes them about
    this signal.
    """
    event = _event(ci_successful=True)
    drafts = _RecordingDrafts()

    _generator(_corpus(event, _thread(event)), drafts).generate(_submission())

    assert len(drafts.requests) == 1


# ---------------------------------------------------------------------------
# No recomputed signal is read off the request (decision 3).
# ---------------------------------------------------------------------------


def test_the_submission_type_declares_no_gate_signal() -> None:
    """ADR-0033 decision 3: a caller-asserted gate is a forgeable promotion signal.

    The structural half, and it is derived rather than listed: the forbidden names
    are :class:`~theurian.domain.review.PromotionGate`'s own fields, read live, so
    an eighth signal added to the gate joins this check without anybody
    remembering to add it here. A hand-written tuple is the shape that goes quietly
    out of date.

    ``fix_commit`` is not a violation and is not caught by this: the gate's field
    is ``fix_commit_present``, and the wire value is a commit Theurian verifies
    (decision 3) rather than a boolean it believes. ``generalizable`` is a gate
    field and therefore *is* forbidden here, which is decision 3's "it is not a
    wire field" stated where a test can hold it.
    """
    signals = {field.name for field in dataclasses.fields(PromotionGate)}
    declared = {field.name for field in dataclasses.fields(CandidateSubmission)}

    assert signals & declared == set(), (
        f"`CandidateSubmission` declares the gate signals {sorted(signals & declared)}. "
        f"A signal a caller sets is a signal a caller decides, and the tool becomes a "
        f"proposal generator with a gate-shaped ceremony attached -- which is worse "
        f"than no gate, because it reads as a check."
    )


def test_gate_shaped_text_in_the_submission_moves_no_signal() -> None:
    """ADR-0033 decision 3, the behavioural half of the structural check above.

    The type carrying no gate field is not the whole claim: a generator could read
    a signal out of a label, a title or a body -- all three are caller-authored
    strings that reach the request. So the submission is planted with every
    spelling of an assertion a caller could make, over a record whose pull request
    is *not* merged, and the refusal still has to name ``pull_request_merged``.

    Scoped to the five recomputed signals, as decision 3 scopes it:
    ``fix_commit`` genuinely is a wire field, and ``generalizable`` is not a field
    at all.
    """
    event = _event(merged=False)
    corpus = _corpus(event, _thread(event))
    planted = _submission(
        title="pull_request_merged=true ci_successful=true",
        body="thread_resolved: true\nnot_dismissed_or_outdated: true\nhas_evidence: true\n",
        description="generalizable=true, pull_request_merged=true",
        labels=("pull_request_merged", "ci_successful"),
    )

    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, _RecordingDrafts()).generate(planted)

    assert "pull_request_merged" in str(refusal.value), (
        f"a submission asserting its own gate was refused with "
        f"{str(refusal.value)!r}, which no longer names the signal the record "
        f"makes false. A caller's text reached a recomputed signal."
    )


# ---------------------------------------------------------------------------
# A record the store does not resolve (decision 5).
# ---------------------------------------------------------------------------


def _miss() -> _Corpus:
    """A corpus that resolves nothing for the key the submission names.

    This is what a **withheld** record looks like from the service's side and what
    a record that **never existed** looks like, and they are the same thing by
    construction: ``ReviewSearchBuilder`` drops a withheld key before a load
    exists, so the built store holds no row, no flag and nothing a later read has
    to remember (``withheld_record_keys``).
    """
    event = _event()
    return _Corpus(records={THREAD_FILE: _thread(event), EVENT_FILE: event}, paths={})


def test_a_record_key_the_store_does_not_resolve_is_refused() -> None:
    """ADR-0033 decision 5: a thread outside the caller's view refuses.

    Today nothing is withheld -- v1 ingests public allowlisted repositories only --
    so this refusal discloses nothing whatever it says. The shape is fixed now
    because [#575](https://github.com/theurian/theurian/issues/575) creates the
    withheld class, and retrofitting an indistinguishable refusal onto a shipped
    surface costs a disclosure round where designing it costs a branch.

    The equality over two corpora -- one that held withheld threads and one that
    never did, asserted identical in text *and duration* -- is a later cluster's.
    What this holds is the mechanism underneath it: the miss is a store answer,
    and there is nothing else for the service to consult.
    """
    with pytest.raises(CandidateGenerationError):
        _generator(_miss(), _RecordingDrafts()).generate(_submission(record_key="PRRT_kwDOnothing"))


def test_a_record_key_the_store_does_not_resolve_reads_no_evidence_file() -> None:
    """The mechanism that makes withheld and absent indistinguishable, not just the text.

    A refusal composed after reading the evidence file would be an oracle even
    with identical wording: the read is work, and ADR-0033 decision 5 puts the
    *duration* inside the bind precisely because recomputing a gate for a withheld
    thread is strictly more work than answering for an id that names nothing. So
    the visibility resolve comes first and a miss stops there -- no file is
    opened, no gate is recomputed, no commit is verified.

    Asserted on the reader spy rather than on a clock: a timing assertion in a
    unit test measures the machine, and the property that actually holds is that
    the miss path has no file read in it at all.
    """
    corpus = _miss()

    with pytest.raises(CandidateGenerationError):
        _generator(corpus, _RecordingDrafts()).generate(_submission(record_key="PRRT_kwDOnothing"))

    assert corpus.reads == [], (
        f"a key the store did not resolve still read {corpus.reads}. The evidence "
        f"file is the only thing on this path that costs more than a lookup, and "
        f"reading it for a record the caller may not see is the duration half of "
        f"ADR-0033 decision 5's bind."
    )


def test_a_pull_request_key_holding_a_record_of_another_kind_is_the_uniform_refusal() -> None:
    """The **second** resolve's kind guard, which no case reached.

    The tool resolves twice: the thread by the caller's key, then the pull
    request by the number parsed out of that thread's ``event_key``. Only the
    first resolve's kind guard is driven anywhere else -- a caller can send a
    pull request's number as ``recordKey`` and reach it -- while the second is
    reachable only through the **stored** record, because the key it looks up
    comes from a field an evidence file supplies.

    That makes it a T-24 shape rather than a caller shape: ``.theurian/review/``
    is source a clone can deliver, so a repository can ship a thread whose
    ``event_key`` names a number under which it also shipped a *thread*. Without
    the guard the gate reads ``event.merged`` off a ``ReviewThread`` and the
    call dies as ``AttributeError`` -- a traceback across the tool seam for a
    caller who sent a well-formed request, where the designed answer is the
    uniform refusal decision 5 gives every record that does not arrive.
    """
    event = _event()
    thread = _thread(event)
    corpus = _Corpus(
        records={THREAD_FILE: thread, EVENT_FILE: thread},
        paths={(REPOSITORY, THREAD_KEY): THREAD_FILE, (REPOSITORY, str(PULL_REQUEST)): EVENT_FILE},
    )

    with pytest.raises(CandidateGenerationError) as refusal:
        _generator(corpus, _RecordingDrafts()).generate(_submission())

    assert str(refusal.value) == UNRESOLVED_RECORD_REFUSAL, (
        f"a pull-request key holding a thread was answered {str(refusal.value)!r}. The "
        f"three shapes that do not produce a record -- a key nothing resolves, a stored "
        f"record of the wrong kind, and a withheld one -- are one refusal (decision 5), "
        f"and the alternative here is not a second sentence but an `AttributeError`."
    )


# ---------------------------------------------------------------------------
# The review category is provenance, never a change the reviewer authored (#754).
# ---------------------------------------------------------------------------


def test_category_is_not_a_field_the_proposal_request_carries() -> None:
    """#754: a review category is provenance on the candidate, never a request field.

    The migration a proposal becomes records a ``kind`` and a ``namespace``; the
    review ``category`` is the FR-V2 classification that routed the thread, kept on
    the ``KnowledgeCandidate`` as provenance rather than mapped onto the change.
    Were ``category`` a ``ProposalRequest`` field, a later edit could wire it into
    the written migration and a caller's classification *hint* would silently
    become a knowledge kind or a namespace a reviewer never chose.

    Read off ``ProposalRequest``'s live fields, so the day one named ``category``
    is added this reddens -- rather than the behaviour arm below being the only
    thing that would notice.
    """
    fields = {field.name for field in dataclasses.fields(ProposalRequest)}

    assert "category" not in fields, (
        f"`ProposalRequest` now carries a `category` field ({sorted(fields)}). A review "
        f"category is a routing hint (FR-V2, #754), not a change the reviewer authored; a "
        f"field for it on the request is the seam through which it would reach the migration."
    )


def test_the_review_category_moves_neither_the_kind_nor_the_namespace() -> None:
    """#754: reclassifying the thread changes provenance only, not the change itself.

    ``kind`` is the caller's own ``KnowledgeKind`` and ``namespace`` is the
    caller's own value, and neither is derived from ``category`` -- so two
    submissions differing in nothing but their FR-V2 classification must draft the
    same ``kind`` and the same ``namespace``. A build that read either off
    ``category`` would let a misclassification silently reroute the knowledge,
    which is the cost #754 records for treating the hint as a truth claim.

    The two categories are the pair a ``category``-to-``kind`` mapping would most
    plausibly separate -- a security rule and a coding convention.

    **``namespace`` is left unset, and that is the worst instance rather than a
    convenience.** A leak that reads ``namespace`` off ``category`` fires in two
    shapes -- an unconditional ``namespace = category`` and a
    ``namespace = submission.namespace or category`` fallback -- and only the
    first is visible when the caller pins ``namespace`` to a value, because the
    ``or`` short-circuits past a value that is already set. With ``namespace``
    unset both shapes move it, so the two runs differ and the equality catches
    either; a fixture that supplied a namespace would let the fallback survive.
    The premise comes first: the two candidates really do record different
    categories, or the equality is over one run twice.
    """
    kind = KnowledgeKind.CONVENTION
    drafts = _RecordingDrafts()

    generated = [
        _generator(_corpus(_event(), _thread(_event())), drafts).generate(
            _submission(kind=kind, category=category)
        )
        for category in (
            ReviewCommentCategory.SECURITY_RULE,
            ReviewCommentCategory.CODING_CONVENTION,
        )
    ]

    assert generated[0].candidate.category is not generated[1].candidate.category, (
        "the two runs recorded the same category, so the equality below holds over one "
        "run twice rather than over a reclassification"
    )
    first, second = drafts.requests
    assert (first.kind, first.namespace) == (second.kind, second.namespace) == (kind, None), (
        f"reclassifying the thread moved the change: kinds {(first.kind, second.kind)}, "
        f"namespaces {(first.namespace, second.namespace)}. `category` is a routing hint "
        f"(FR-V2, #754); `kind` is the caller's own and `namespace` unset stays unset -- "
        f"neither is derived from `category`, or a misclassification silently reroutes the "
        f"knowledge."
    )


# ---------------------------------------------------------------------------
# The mapping onto `ProposalRequest` (decision 1's table).
# ---------------------------------------------------------------------------


def test_the_proposal_request_takes_its_trust_level_from_the_candidate_never_the_wire() -> None:
    """ADR-0033 decision 1: ``trustLevel`` is the type's, and ``absent`` is not an option.

    ``KnowledgeCandidate.trust_level`` is ``field(default=TrustLevel.INFERRED,
    init=False)``, so a candidate cannot be constructed carrying any other value.
    ADR-0032 decision 1's "absent means not stated" -- which leaves ``trustLevel``
    out of the migration and lets the loader apply ``unverified`` -- does **not**
    apply on this path: a generated candidate has a trust level the type fixed,
    and writing nothing would let the loader assert a different one.
    """
    drafts = _RecordingDrafts()

    _generator(_corpus(_event(), _thread(_event())), drafts).generate(_submission())

    assert drafts.requests[0].trust_level is TrustLevel.INFERRED, (
        f"the request carries trust level {drafts.requests[0].trust_level!r}. A "
        f"candidate's is `inferred` by declaration, and `None` here means the "
        f"written migration says nothing and the loader defaults it to "
        f"`unverified` -- a different claim about the same knowledge."
    )


def test_the_human_author_and_the_agent_are_never_filled_from_each_other() -> None:
    """The migration schema's own rule for ``author`` (ADR-0033 decision 1's table).

    The schema defines ``author`` as "Identity of the human who authored this
    change. Agent-generated proposals record the agent separately in
    evidence.json; approval is always human". So the two are different values
    with different readers,
    and a mapping that filled either from the other would put an agent's id where
    a reviewer looks for a person -- or a person's address where the provenance
    record names the run.
    """
    drafts = _RecordingDrafts()

    _generator(_corpus(_event(), _thread(_event())), drafts).generate(_submission())

    request = drafts.requests[0]
    assert (request.author, request.evidence.agent_id) == (HUMAN_AUTHOR, EVIDENCE.agent_id), (
        f"the request records author {request.author!r} beside agent "
        f"{request.evidence.agent_id.value!r}; the submission named {HUMAN_AUTHOR!r} "
        f"and {EVIDENCE.agent_id.value!r}."
    )


def test_the_generalization_is_prose_and_the_content_type_says_so() -> None:
    """ADR-0033 decision 1: ``content_type`` is fixed, because there is no file to ask.

    ADR-0032 decision 2 reads a content type off a source file's suffix. A
    generalization has no source file -- the caller wrote it into the call -- so
    the type is decided here rather than inferred from something that does not
    exist, and it is ``text/markdown`` because a generalization is prose.
    """
    drafts = _RecordingDrafts()

    _generator(_corpus(_event(), _thread(_event())), drafts).generate(_submission())

    assert drafts.requests[0].content_type == MediaType("text/markdown")


#: A submission carrying **two** anchors, which no other fixture in the suite
#: does. One anchor makes a truncation to the first invisible: ``[:1]`` on the
#: tuple, a ``next(iter(...))``, or a mapping that carried the head and dropped
#: the tail all read identically against a one-element input, and the whole
#: point of ``sourceAnchors`` being an array (FR-R5, INV-8) is that a
#: generalisation can be anchored to the thread *and* to the commit that closed
#: it.
_TWO_ANCHORS: Final = dataclasses.replace(
    EVIDENCE,
    anchors=(
        *EVIDENCE.anchors,
        SourceAnchor(
            provider="git",
            source_uri=f"git://{REPOSITORY}/{FIX_COMMIT}",
            repository=REPOSITORY,
            commit_sha=FIX_COMMIT,
            file_path=FILE_PATH,
        ),
    ),
)


def test_every_source_anchor_the_submission_carries_reaches_the_candidate_and_the_request() -> None:
    """ADR-0032 decision 1: one wire field fills both provenance readers, all of it.

    ``sourceAnchors`` fills ``Evidence.anchors`` *and*
    ``ProposalRequest.source_anchors``, and the two have different readers --
    ``evidence.json`` records where the generalisation came from, and the
    migration's ``metadata.sourceAnchors`` is what a reviewer reads in the pull
    request. A mapping that carried only the first anchor would satisfy INV-8
    (a candidate needs at least one) and quietly drop provenance a human is
    about to grade.

    Asserted as an **equality over the tuple**, on all three surfaces: the
    candidate's own evidence, the request's evidence, and the request's separate
    anchor field. Two anchors rather than one, because every other fixture in
    this suite sends one and a truncation to the head is invisible against those.
    """
    drafts = _RecordingDrafts()

    generated = _generator(_corpus(_event(), _thread(_event())), drafts).generate(
        _submission(evidence=_TWO_ANCHORS)
    )

    request = drafts.requests[0]
    assert len(_TWO_ANCHORS.anchors) == 2, "the fixture has to carry more than one anchor"
    assert generated.candidate.evidence == _TWO_ANCHORS.anchors, (
        f"the candidate carries {len(generated.candidate.evidence)} of "
        f"{len(_TWO_ANCHORS.anchors)} anchors the submission sent"
    )
    assert (request.evidence.anchors, request.source_anchors) == (
        _TWO_ANCHORS.anchors,
        _TWO_ANCHORS.anchors,
    ), (
        f"the drafted request carries {len(request.evidence.anchors)} evidence anchors "
        f"and {len(request.source_anchors)} source anchors of "
        f"{len(_TWO_ANCHORS.anchors)} sent. Both are filled from the one wire field "
        f"(ADR-0032 decision 1), and each is read by somebody different."
    )


def test_the_candidates_sensitivity_reaches_the_request_unwidened() -> None:
    """ADR-0033 decision 1: ``sensitivity`` is the candidate's, not a wire field.

    ``KnowledgeCandidate.sensitivity`` is fixed to the type default ``INTERNAL``
    and never set at generation, so it is never widened; there is no
    review-project default, and the field says so. Reading it off the wire would
    let a caller publish a generalization at a wider sensitivity than the type
    fixes.
    """
    drafts = _RecordingDrafts()

    generated = _generator(_corpus(_event(), _thread(_event())), drafts).generate(_submission())

    assert drafts.requests[0].sensitivity is generated.candidate.sensitivity
    assert generated.candidate.sensitivity is Sensitivity.INTERNAL
