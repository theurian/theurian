"""``review.generateKnowledgeCandidate``'s refusals, driven over the transport (ADR-0033).

``tests/unit/test_candidate_generation.py`` holds what the service decides, through
its own seams and with a fake verification. What it cannot hold is the half a
wiring defect lives in: that the record resolve, the evidence read and the
``fixCommit`` check are joined to a *real* review store and a *real* repository,
and that each designed refusal crosses the tool boundary **with its cure attached**.
That last one is not hypothetical -- slice B4 shipped with ``ProposalError``
crossing the ``_forwarding`` seam as ``str(exc)``, which drops ``exc.remedy``, so a
caller was told the refusal and handed nothing to do about it.

Two of ADR-0033's own owed items are thread-level and are discharged here rather
than in the unit file, which deliberately did not cover them:

* **decision 4's message half** -- an unknown CI outcome and a failed one are one
  ``unmet()`` name and must not be one sentence;
* **decision 5's uniform refusal** -- the two commit-verification failures, and a
  record key the store does not answer for, arrive as one answer whichever of
  their causes fired.

**Every arm asserts it reached the tool.** An unregistered tool is refused by the
SEC-12 middleware with a fixed sentence, which would make the two *equality* arms
below pass over two copies of that sentence -- green, and about nothing. Measured:
with the guard removed, six of these eleven arms pass against a build that has not
registered the tool. So :func:`_call` refuses the middleware's answer before any
assertion sees it.

Everything goes through ``mcp_session`` for the reason
``test_input_validation_wire.py`` records: ``server.call_tool`` is the SDK's tool
dispatcher and sits below the ``ServerMiddleware`` tier, so an in-process call
never reaches the input-validation seat.

The project, its git repository and its two commits are
``review_candidate_project``'s; nothing here touches the developer's machine.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import pytest
import review_candidate_fixtures as corpus
from fix_commit_grammar import REFUSED
from review_candidate_project import ServedProject, served_project

from theurian.application.project_service import (
    REVIEW_SEARCH_STORE_ID,
    BuildProvenance,
    ProjectPaths,
)
from theurian.daemon.runner import build_server
from theurian.infrastructure.sqlite.review_search_schema import REVIEW_SEARCH_SCHEMA_VERSION
from theurian.mcp.tools import REVIEW_SEARCH_UNAVAILABLE_REFUSAL

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration

TOOL: Final = "review.generateKnowledgeCandidate"

#: The distinctive fragment of ``mcp/validation.py``'s ``NO_SCHEMA_REFUSAL``, which
#: is what an *unregistered* tool name is answered with. Quoted rather than
#: imported as a template because the template carries a ``{tool}`` placeholder;
#: ``test_candidate_input_schema.py`` is where the schema's own existence is a
#: claim, and this is only the guard that keeps the arms below from asserting
#: about that refusal.
_NO_SCHEMA_FRAGMENT: Final = "publishes no input schema for that tool"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServedProject]:
    """The served project, built by ``review_candidate_project``.

    The setup is not this module's subject and is shared with ADR-0033 decision
    5's two-corpora battery (``test_candidate_generation_absence_proof.py``), so
    it lives beside the corpus it serves; what matters here is only that the
    project, its review store and its two commits are all real.

    ``withheld`` is stated rather than defaulted, for
    ``ReviewSearchBuildRequest.withheld_record_keys``' reason: this module's
    subject is the refusals a caller reaches over a corpus that withholds
    **nothing**, and a signature that could supply that by omission is how the
    posture stops being a decision.
    """
    yield from served_project(
        tmp_path, monkeypatch, records=corpus.evidence_records(), withheld=frozenset()
    )


def _arguments(record_key: str, fix_commit: str, **overrides: Any) -> dict[str, Any]:
    """A well-formed call, so every arm below reaches the behaviour it names."""
    return {
        "projectId": "demo",
        "repository": corpus.REPOSITORY,
        "recordKey": record_key,
        "fixCommit": fix_commit,
        "itemId": "reliability.retry-lock-order",
        "title": "Acquire locks after reads in retry-eligible paths",
        "body": "Acquire locks after reads, never before, in retry-eligible paths.\n",
        "kind": "convention",
        "category": "reliability-rule",
        "owner": "platform-team",
        "author": "dana@example.com",
        "description": "Generalise the deadlock thread into a locking rule",
        "evidence": {
            "agentId": "claude-code",
            "taskId": "task-431",
            "model": "claude-opus-5",
            "reasoning": "The thread settled the lock ordering; this generalises it.",
        },
        "sourceAnchors": [
            {
                "provider": "github",
                "sourceUri": (
                    f"https://github.com/{corpus.REPOSITORY}/pull/"
                    f"{corpus.PULL_REQUEST_CI_PASSED}#discussion_r1"
                ),
                "repository": corpus.REPOSITORY,
                "filePath": corpus.FILE_PATH,
            }
        ],
        **overrides,
    }


def _call(project: ServedProject, tmp_path: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """One ``tools/call``, asserted to have reached the tool before it is read.

    An unregistered tool name is answered by the SEC-12 middleware with a fixed
    sentence (ADR-0031 decision 5). Every equality arm below would hold over two
    copies of *that* -- two refusals that differ by nothing because neither came
    from this tool -- so the guard is here rather than in each arm, and it is what
    makes a build that has not registered the tool RED instead of vacuously green.
    """
    with mcp_session(build_server(project.registry), tmp_path / "data") as call:
        answer = call(TOOL, arguments)

    result: dict[str, Any] = answer["result"]
    text = _text(result)

    assert _NO_SCHEMA_FRAGMENT not in text, (
        f"`{TOOL}` was answered by the input-validation middleware rather than by the "
        f"tool: {text!r}. Either it is not registered, or its published input schema "
        f"is not loaded -- and until one of those is fixed, every assertion in this "
        f"file would be about the middleware's own sentence."
    )
    return result


def _text(result: dict[str, Any]) -> str:
    content = result.get("content") or [{}]
    published: str = content[0].get("text", "")
    return published


# -- The control: a satisfying thread really does produce a candidate --------


def test_a_thread_meeting_every_signal_lands_a_proposal_over_the_wire(
    project: ServedProject, tmp_path: Path
) -> None:
    """The control without which every refusal below is satisfied by refusing all.

    ADR-0033's *Compliance* names this one explicitly for the commit-verification
    pair, and it is the same control for the gate and the resolve: a build that
    refused every call would pass the byte-identity arms, the differs-from arms and
    the no-proposal-written arms, having verified nothing.

    So one thread meets all seven signals -- merged pull request, CI green,
    resolved, not dismissed, comments present, a generalisation offered, and a
    ``fixCommit`` that this repository really has and that really touched the
    stored ``filePath`` -- and the proposal lands on disk where a reviewer can see
    it (``.theurian/proposals/``, not the ignored local directory: ADR-0013 point
    7).
    """
    result = _call(project, tmp_path, _arguments(corpus.THREAD_SATISFYING, project.verifying))

    assert result["isError"] is False, result
    landed = result["structuredContent"]["proposalId"]
    assert landed in project.proposals(), landed
    assert (project.root / ".theurian/proposals" / landed).is_dir(), (
        "the candidate's proposal landed somewhere other than `.theurian/proposals/`. "
        "ADR-0013 point 7: a `--local` proposal sits inside the managed ignore block, "
        "where the human review FR-V4 relies on cannot reach it"
    )


# -- SEC-12: a revision expression is refused at the wire, with the key path -

#: The member of the grammar corpus this arm sends: the exact expression two
#: reviewers recovered through, read off the shared table rather than spelled
#: again so the wire arm and the adapter battery cannot drift to different
#: vectors.
_MESSAGE_SEARCH: Final = next(member for member in REFUSED if member.label == "message-search")


def test_a_fix_commit_that_describes_a_commit_is_refused_at_the_wire_naming_the_field(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0031 decision 4: the caller is told which field broke, not that no commit matched.

    ``HEAD^{/planted}`` asks git to search history for a commit whose message
    matches -- the recovery two reviewers reached independently, because it
    satisfies ``fix_commit_present`` without the caller knowing any object id.
    The adapter's entry funnel refuses it too; what this seat adds is the
    **key path**, and the difference is what the caller does next. A gate
    refusal says *name a fixCommit this repository has that touched the file*,
    which sends a caller who sent an expression hunting for a better commit; the
    schema refusal says the value does not satisfy ``fixCommit``'s pattern.

    Three assertions, and the third is ADR-0031 decision 4's other half: the
    refusal quotes the *schema's* expectation and never the caller's own bytes,
    so a boundary taking untrusted input does not become an amplifier of it.

    Driven against the thread that meets every other signal, so nothing else
    about this call could have refused it -- the same arguments with a real sha
    land a proposal
    (:func:`test_a_thread_meeting_every_signal_lands_a_proposal_over_the_wire`).
    """
    result = _call(project, tmp_path, _arguments(corpus.THREAD_SATISFYING, _MESSAGE_SEARCH.value))

    assert result["isError"] is True, result
    text = _text(result)
    assert "published input schema" in text and "fixCommit" in text and "pattern" in text, (
        f"the refusal does not name the field and the constraint that rejected it: "
        f"{text!r}. SEC-12 answers a value-domain violation at the wire with the key "
        f"path (ADR-0031 decision 4); without the published `pattern` this call reaches "
        f"the handler and comes back as a gate refusal telling the caller to find a "
        f"better commit, which is not what went wrong. The middleware's own marker is "
        f"asserted with the field name because the gate refusal's cure says `fixCommit` "
        f"too, so that word alone does not say which seam answered."
    )
    assert _MESSAGE_SEARCH.value not in json.dumps(result), (
        f"the refusal echoes the caller's own value back: {text!r}"
    )
    assert project.proposals() == set(), "a call refused at the wire wrote a proposal"


# -- Decision 4: unknown CI is unmet, named, and not the failed sentence -----


def test_an_unknown_ci_outcome_is_refused_naming_the_signal_over_the_wire(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0033 decision 4, over the wire: the caller is told *which* signal to obtain.

    The stored pull request's ``ciSuccessful`` is ``null`` -- what the adapter
    writes for pending, expected, absent, or a status this version does not
    recognise. ``None`` does not satisfy the gate, and the refusal names
    ``ci_successful`` among the unmet signals rather than telling the caller the
    thread is unsuitable, because *go and get a CI result* is an instruction and
    *no* is not.

    The cure is asserted as well as the name, and that is the half a seam drops:
    the refusal's ``remedy`` is what carries ``theurian review ingest`` and
    ``theurian review build``, and a ``TheurianError`` crossing ``_forwarding``
    arrives as ``str(exc)`` with the remedy left behind (the defect ADR-0032
    decision 3 met one tool over).
    """
    result = _call(project, tmp_path, _arguments(corpus.THREAD_CI_UNKNOWN, project.verifying))

    assert result["isError"] is True, result
    text = _text(result)
    assert "ci_successful" in text, text
    assert "theurian review build" in text, (
        f"the refusal reached the wire without its cure: {text!r}. The `theurian "
        f"review build` step lives only in `CandidateGenerationError.remedy`, which "
        f"the `_forwarding` seam drops unless the tool body folds it in."
    )
    assert project.proposals() == set(), "a refused unknown-CI call wrote a proposal"


def test_the_unknown_ci_refusal_is_not_the_words_a_failed_ci_thread_gets(
    project: ServedProject, tmp_path: Path
) -> None:
    """The fourth assertion that separates decision 4 from the alternative it rejects.

    ``PromotionGate.unmet()`` returns the *names* of the falsy signals, and ``None``
    and ``False`` are both falsy -- so it reports ``ci_successful`` for either and
    an implementation that flattened ``None`` to ``False`` at the adapter, which is
    exactly the alternative ADR-0033 rejects, would pass every other CI arm in this
    file. What separates them is that the two sentences differ: *nobody has told
    Theurian whether this thread's fix passed* is an instruction to go and get a CI
    result, and *this thread's fix did not pass* is an instruction to stop.

    Driven over the wire rather than only at the service, because the tri-state has
    to survive the tool boundary: a handler that re-derived the sentence from
    ``unmet()`` alone would produce one message for both.
    """
    unknown = _text(
        _call(project, tmp_path, _arguments(corpus.THREAD_CI_UNKNOWN, project.verifying))
    )
    failed = _text(_call(project, tmp_path, _arguments(corpus.THREAD_CI_FAILED, project.verifying)))

    assert unknown != failed, (
        f"an unknown CI outcome and a failed one are refused in the same words on the "
        f"wire: {unknown!r}. `unmet()` reports one name for both, so the distinction "
        f"comes from a second read of the stored tri-state -- and without it the "
        f"adapter-flattening ADR-0033 rejects is indistinguishable from the decision "
        f"it took."
    )


# -- Decision 5: the two commit-verification failures are one refusal --------


def test_the_two_commit_verification_failures_are_one_refusal_on_the_wire(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0033 decisions 3 and 5: the difference between them is about the repository.

    *That commit does not exist here* and *that commit exists and touched nothing
    this thread names* differ by a fact about the repository's contents, and the
    caller asking was granted a review-evidence tool rather than the repository.
    Two messages would be an oracle a caller walks: name a candidate sha, read which
    refusal came back, learn whether the object is present.

    The verification **does** know which happened -- the seam answers with a
    three-member verdict because git answers three ways -- so collapsing them is a
    decision a change can break, which is what makes this worth asserting. Asserted
    on the whole published text, so a cure that named the distinction reopens the
    channel the message closed.

    ``project.unrelated`` is the input that makes this more than a pair of identical
    refusals from a tool that refuses everything: it is a real commit in this
    repository, and the control above shows that a commit which *does* touch the
    thread's file is accepted.

    **Both cures are asserted to cross, because the refusal offers two.** One tells
    the caller to name a different commit; the other tells it that the *stored
    record* may be the thing that is wrong -- a review evidence directory is source
    a clone can deliver (T-24), so the file the thread claims to be anchored to is
    not necessarily one this repository ever had. An equality alone would be
    satisfied by two identically cure-less refusals, which is the shape the
    ``_forwarding`` seam produces when a ``remedy`` is dropped.
    """
    absent = _text(
        _call(project, tmp_path, _arguments(corpus.THREAD_SATISFYING, corpus.ABSENT_COMMIT))
    )
    untouching = _text(
        _call(project, tmp_path, _arguments(corpus.THREAD_SATISFYING, project.unrelated))
    )

    assert absent == untouching, (
        f"the two commit-verification refusals differ on the wire:\n"
        f"  no such commit  -- {absent!r}\n"
        f"  touches nothing -- {untouching!r}\n\n"
        f"ADR-0033 decision 5 binds them to one refusal, because what separates them "
        f"is a fact about this repository's contents rather than about the request."
    )
    assert "git log --oneline" in absent, (
        f"the refusal does not name the commits that would satisfy it: {absent!r}"
    )
    assert "the stored record is the thing to correct rather than the commit" in absent, (
        f"the refusal offers only the name-a-better-commit cure: {absent!r}. The second "
        f"cure is the one that applies when the thread's stored `filePath` is wrong "
        f"rather than the caller's commit, and a caller sent hunting through a file's "
        f"history for a commit that cannot exist has been given a loop to run."
    )
    assert project.proposals() == set(), "a refused fix-commit call wrote a proposal"


def test_a_thread_with_no_file_anchor_is_refused_in_its_own_words_over_the_wire(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0033 decision 3's amendment: a distinct refusal, deliberately not folded in.

    The adapter stores ``file_path=None`` for any thread GitHub anchors to no string
    ``path``. For such a thread the *touches that path* half of the verification has
    nothing to check, so the call refuses -- and it refuses in its **own** words
    rather than borrowing the commit-verification refusal above.

    That is a decision with a recorded reason: ``filePath`` is a published key on
    every ``review.search`` record, ``None`` ones included, so naming the property
    discloses nothing -- and *this thread cannot generate a candidate in v1* is a
    different instruction from *go and find a better commit*, which is what the
    shared refusal says.

    The commit here is the **verifying** one, so the refusal cannot be explained by
    the commit: what is missing is the anchor the check would have used.
    """
    anchorless = _text(
        _call(project, tmp_path, _arguments(corpus.THREAD_NO_FILE_ANCHOR, project.verifying))
    )
    commit_refusal = _text(
        _call(project, tmp_path, _arguments(corpus.THREAD_SATISFYING, corpus.ABSENT_COMMIT))
    )

    assert anchorless != commit_refusal, (
        f"a thread with no file anchor is refused in the commit-verification "
        f"refusal's own words: {anchorless!r}. The two say different things to a "
        f"caller, and ADR-0033 decision 3's amendment records why the second is not "
        f"folded into the first."
    )
    assert "file" in anchorless.casefold(), (
        f"the refusal {anchorless!r} does not name the missing file anchor, so a "
        f"caller is told no and not why."
    )
    assert project.proposals() == set(), "a refused no-file-anchor call wrote a proposal"


# -- Decision 5: a record the resolve does not answer for is one refusal -----


def test_a_record_key_the_store_never_held_is_refused_as_a_wrong_kind_record_is(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0033 decision 5's uniform refusal, over the two shapes that reach it today.

    Three shapes share this answer: a key the resolve does not answer for, a stored
    record whose kind is not the one its key promised, and -- once
    [#575](https://github.com/theurian/theurian/issues/575) creates the class -- a
    **withheld** record, which the resolve answers for exactly as it answers for an
    absent one. The third cannot be driven yet; the first two can, and they are the
    ones that would drift apart if the service grew a second sentence.

    The wrong-kind input is a pull request's own record key -- a number, which this
    corpus really holds -- so it is a key that resolves to a file and then to a
    record of the wrong type. A build that refused it in different words would be
    telling the caller that *something* is stored under that key, which is the
    distinction the uniform refusal exists to remove.
    """
    absent = _text(_call(project, tmp_path, _arguments(corpus.THREAD_ABSENT, project.verifying)))
    wrong_kind = _text(
        _call(
            project,
            tmp_path,
            _arguments(str(corpus.PULL_REQUEST_CI_PASSED), project.verifying),
        )
    )

    assert absent == wrong_kind, (
        f"a key this store never held and a key naming a record of the wrong kind are "
        f"refused differently:\n"
        f"  never held -- {absent!r}\n"
        f"  wrong kind -- {wrong_kind!r}\n\n"
        f"They differ by what the store holds, which is what decision 5's uniform "
        f"refusal is about -- and #575's withheld class arrives on this same path."
    )
    assert "theurian review build" in absent, (
        f"the uniform refusal reached the wire without its cure: {absent!r}"
    )
    assert project.proposals() == set(), "a refused unresolved-record call wrote a proposal"


# -- The store this tool reads is governed the way `review.search`'s is ------


def _store_path(project: ServedProject) -> Path:
    return ProjectPaths.of(project.root).review_search_for(REVIEW_SEARCH_STORE_ID)


def _forget_the_review_build(project: ServedProject) -> None:
    """Drop this root's ``review`` provenance record, leaving the store on disk.

    The hostile-clone shape, minus the clone: a well-formed, current, fully
    readable review store that **this installation never built**, which a
    repository contributor produces with ``git add -f`` past ADR-0004's ignore.
    Only the one family is dropped -- ``state`` and ``index`` stay, or the call
    would be refused for an unresolvable project before it reached the review
    store at all, and the arm would be measuring the wrong guard.
    """
    provenance = BuildProvenance.for_registry(project.registry)
    recorded = json.loads(provenance.path.read_text(encoding="utf-8"))
    entry = recorded[str(project.root.resolve())]

    assert entry.pop("review", None), (
        "this project has no `review` provenance record to drop, so the refusal below "
        "would be the state a fresh project is already in rather than the plant"
    )
    provenance.path.write_text(json.dumps(recorded, indent=2, sort_keys=True), encoding="utf-8")


def test_a_review_store_this_installation_did_not_build_refuses_as_a_missing_one_does(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0004, SEC-7, T-19: provenance before presence, on this tool's read too.

    The review search store is derived and git-ignored, so presence on disk is
    evidence of nothing: a contributor force-adds a fabricated one and a victim
    who clones and registers gets it served as the repository's own review
    history. ``review.search`` refuses that, and this tool reads the **same
    store** through its own composition -- so the guard has to be on this path
    as well, and until it was driven, deleting it left 180 tests green.

    What a missing guard produces here is worse than a bad search result: the
    fabricated record satisfies five of the seven gate signals, so the call
    **lands a proposal** built from review history nobody wrote.

    The control comes first and is what makes the refusal mean something: the
    identical call against the same project, with the provenance record intact,
    lands a proposal. Then the record is dropped and the same call is refused --
    byte-identically to the same call against a project whose store file is not
    there at all, so a victim cannot tell which of the two states they are in
    and whoever planted the store learns nothing about whether it was detected.
    """
    arguments = _arguments(corpus.THREAD_SATISFYING, project.verifying)
    control = _call(project, tmp_path, arguments)
    assert control["isError"] is False, control
    landed = project.proposals()

    _forget_the_review_build(project)
    planted = _text(_call(project, tmp_path, arguments))
    _store_path(project).unlink()
    absent = _text(_call(project, tmp_path, arguments))

    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in planted, (
        f"a review store this installation never built was served to "
        f"`{TOOL}`: {planted!r}. Provenance is the one thing a repository "
        f"contributor cannot forge, and without it a planted store reaches the gate."
    )
    assert planted == absent, (
        f"a planted store and an absent one are refused differently:\n"
        f"  planted -- {planted!r}\n"
        f"  absent  -- {absent!r}\n\n"
        f"The difference tells a victim which of the two states they are in, which is "
        f"the disclosure the shared constant exists to remove."
    )
    assert project.proposals() == landed, (
        "a call refused for an unvouched store still wrote a proposal, so the refusal "
        "fired after the draft rather than before the read"
    )


def test_a_store_a_superseded_schema_built_is_refused_in_the_request_independent_constant(
    project: ServedProject, tmp_path: Path
) -> None:
    """SEC-13: the adapter's message names the store file, and this tool must not publish it.

    ``SqliteReviewSearchStore`` refuses a store stamped by a schema version it
    does not serve, and its message names the file and the version -- text that
    varies with what is on disk. ``review.search`` converts that into one
    constant for exactly that reason; this tool catches the same exception from
    the same store and owed the same conversion, and nothing drove it.

    The stamp is moved by hand because there is no older build to run, and the
    control is the store's *reachability*: the same call against the same
    project answered before the stamp moved, so what is being measured is the
    conversion rather than a project that could never have been served.
    """
    arguments = _arguments(corpus.THREAD_SATISFYING, project.verifying)
    control = _call(project, tmp_path, arguments)
    assert control["isError"] is False, control
    landed = project.proposals()

    store = _store_path(project)
    with closing(sqlite3.connect(store)) as connection:
        connection.execute(
            "UPDATE review_search_metadata SET review_search_schema_version = ?",
            (REVIEW_SEARCH_SCHEMA_VERSION + 1,),
        )
        connection.commit()

    result = _call(project, tmp_path, arguments)
    text = _text(result)

    assert result["isError"] is True, result
    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in text, (
        f"a store stamped by a superseded schema was refused in the adapter's own words "
        f"rather than in the constant: {text!r}. That message names the file and the "
        f"failure and varies with the store's state -- the *an error that fires for one "
        f"input and not another* channel SEC-13 closes."
    )
    assert store.name not in text and "superseded" not in text, (
        f"the refusal carries the adapter's description of what is on disk: {text!r}"
    )
    assert project.proposals() == landed, "a refused superseded-store call wrote a proposal"


# -- The house result contract: every refusal is isError-classified ----------


@pytest.mark.parametrize(
    ("label", "record_key", "fix_commit"),
    [
        ("unknown ci", corpus.THREAD_CI_UNKNOWN, None),
        ("failed ci", corpus.THREAD_CI_FAILED, None),
        ("no such commit", corpus.THREAD_SATISFYING, corpus.ABSENT_COMMIT),
        ("no file anchor", corpus.THREAD_NO_FILE_ANCHOR, None),
        ("unresolved record", corpus.THREAD_ABSENT, None),
    ],
    ids=["unknown-ci", "failed-ci", "no-such-commit", "no-file-anchor", "unresolved-record"],
)
def test_every_designed_refusal_is_error_classified_and_carries_a_cure(
    project: ServedProject, tmp_path: Path, label: str, record_key: str, fix_commit: str | None
) -> None:
    """The house result contract, applied to every refusal this tool can produce.

    ``isError`` is how a client tells a refusal from an answer without parsing
    prose; a refusal published as a successful result is read by an agent as the
    candidate having been generated. And a refusal with no cure is a dead end --
    which is the concrete defect ADR-0032 decision 3 met when ``exc.remedy`` was
    dropped at the ``_forwarding`` seam, so the cure is asserted here for every
    refusal rather than only for the two that have an arm of their own above.

    Parametrized over the refusals rather than written five times, so a sixth added
    to the service joins by being driven rather than by somebody remembering the
    contract applies to it too.
    """
    result = _call(project, tmp_path, _arguments(record_key, fix_commit or project.verifying))

    assert result["isError"] is True, (label, result)
    text = _text(result)
    assert text.strip(), f"the {label} refusal published no text at all"
    assert "theurian " in text, (
        f"the {label} refusal names no command a caller can run: {text!r}. Every "
        f"`CandidateGenerationError` carries a `remedy`, and every one of them names "
        f"a `theurian` command; a refusal arriving without it is the remedy being "
        f"dropped on the way to the wire."
    )
    assert project.proposals() == set(), f"a refused {label} call wrote a proposal"
