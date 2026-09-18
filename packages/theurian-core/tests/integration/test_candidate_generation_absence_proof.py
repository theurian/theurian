"""One battery against two corpora, at ``review.generateKnowledgeCandidate`` (ADR-0033 decision 5).

ADR-0030 slice 3 built this instrument for ``review.search``
(``test_review_search_absence_proof.py`` at the store,
``test_review_search_tool_absence_proof.py`` at the response). ADR-0033 decision
5 states that ``review.generateKnowledgeCandidate`` **inherits nothing** from it:
a new surface owes its own round, over its own responses *and its own refusals*.
This is that round.

The claim, in the ADR's words: *a thread outside the caller's view refuses
indistinguishably from a thread that does not exist -- in its text and in how
long it takes*.

Why this tool is not covered by ``review.search``'s proof
---------------------------------------------------------
Between a store hit and this tool's answer sit things ``review.search`` has
none of: a **second** resolve (the pull request named by a thread's
``event_key``), an evidence-file read per resolved record, a **git spawn** that
verifies the caller's ``fixCommit``, a promotion gate recomputed from the stored
record, a refusal per unmet signal, and a proposal written to disk. Each is work
a miss does not do, and each is a place a value computed over a record the
caller may not see could reach the wire.

Three deployments, and the third is why the first two mean anything
-------------------------------------------------------------------
``withholding``
    the whole corpus, built while withholding :data:`WITHHELD_KEYS`. The
    withheld records' **evidence files are on disk**; the built review-search
    store never held a row for them.
``never_held``
    the corpus **minus** those records, built withholding nothing.
``control``
    the whole corpus, built withholding nothing. The positive control: every
    request the pair must answer identically answers *differently* here for the
    arms that reach the plant, and
    :func:`test_the_battery_really_reaches_the_withheld_records` measures that
    column rather than declaring it.

Each is a separate project under the same ``projectId``, so the compared
requests are byte-identical -- including ``fixCommit``, because
``review_candidate_project`` pins its commit dates and the three repositories
therefore carry the same shas
(:func:`test_the_three_deployments_present_one_repository_to_the_verification`).

What is compared, and the one thing that is masked
--------------------------------------------------
The whole JSON-RPC message: ``isError``, the structured content and every
content block, serialised into one string, so a refusal folds into the same
comparison as an answer and *error distinguishability* is part of the property
rather than a separate claim (SEC-13).

A **successful** call mints three fresh ULIDs -- the proposal, the migration and
the revision -- which no two calls share, so a raw byte comparison of two
successes is unachievable for a reason that is not a leak. Those three values
are replaced with fixed tokens, and nothing else is.
:func:`test_a_success_response_varies_only_in_the_three_identifiers_it_mints`
is what makes that honest rather than a mask over a real difference: it measures
that two calls to the **same** deployment differ raw and agree once masked, so
the substitution is exactly sufficient and the remaining comparison covers every
other byte -- the paths the ids appear inside included.

The duration half
-----------------
ADR-0033 decision 5 puts *how long it takes* inside the bind, because a gate
recompute plus a commit verification is strictly more work than a miss on an id
that names nothing. This module holds that the way 0.2.3 holds T-26's
size-independence: **a count pinned at zero, not a stopwatch**
(``test_pre_gate_body_materialization.py``'s "the proof is a byte count").
:class:`_Spend` tallies the two costs that sit below the resolve -- evidence-file
reads and git spawns -- and a miss pays **zero of each**, whether the key is
withheld or was never landed, while the same key un-withheld pays both. A cost
that is never incurred cannot make a refusal's timing carry the record.

The wall-clock corroboration is out of band and machine-specific, as T-26's is,
and it is not asserted here: this suite's committed wall-clock assertions are
timeout bounds -- a read finishes under a cap -- and a comparison of two
*distributions* is not one of them, because what it would assert is the machine.
Measured 2026-09-19 on the production code at ``83245a71`` (Apple M1 Max, macOS
26.6.2, CPython 3.13.3, SQLite 3.47.1), ``perf_counter_ns`` around one
``tools/call`` on an open session, five resolve-miss arms rotated, n=400 after
40 discarded: the withheld key against ``withholding`` and against
``never_held`` answered at medians 3.8610 ms and 3.8651 ms,
**P(withheld > median(never-held)) = 0.477** -- collapsed onto 0.5. The whole
spread across the five miss medians was 0.052 ms, and two keys that are plainly
absent in one corpus differed by as much as the withheld/never-held pair did, so
the residual tracks the key string rather than the corpus. The instrument
separates when there is work to separate: the same key un-withheld is a hit at
**+19.0 ms, P = 1.000**.

What this module does not reach
-------------------------------
- **Generated requests.** ``test_review_search_tool_absence_proof.py`` carries a
  hypothesis arm; every example here is a real ``tools/call`` over a real ASGI
  transport against a real git repository, and the battery is hand-enumerated.
- **The store's own artifact.** "No byte of a withheld record survives in the
  file" is ``test_review_search_absence_proof.py``'s.

Marked ``integration``: three real git projects, three real CLI-registered
states, three real evidence stores and three real SQLite review-search stores.
Writes only under pytest's temporary directories; ``HOME`` and
``THEURIAN_DATA_DIR`` are redirected for the fixture's whole life.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

import pytest
import review_candidate_fixtures as corpus
from review_candidate_project import ServedProject, served_project

from theurian.daemon.runner import build_server
from theurian.domain.review import ReviewThread
from theurian.infrastructure.git.fix_commit_check import FixCommitCheck
from theurian.infrastructure.review_evidence import EvidenceRecord
from theurian.infrastructure.review_evidence.reader import EvidenceReader

from mcp_wire_session import ToolCall, mcp_session  # isort: skip

pytestmark = pytest.mark.integration

TOOL: Final = "review.generateKnowledgeCandidate"

#: The keys ``withholding`` withholds and ``never_held`` never had.
WITHHELD_KEYS: Final = corpus.WITHHELD_KEYS

#: Values no ``withholding`` answer may carry: **content of a withheld record**,
#: and never a key the caller sent.
#:
#: The distinction was measured rather than reasoned. An earlier draft scanned
#: for :data:`corpus.THREAD_WITHHELD`, and a mutation that made the refusal echo
#: the caller's own ``recordKey`` turned that scan red under the message "no
#: withheld value may reach the answer" -- a true failure of a *different*
#: property (the refusal is a constant, ``UNRESOLVED_RECORD_REFUSAL``), reported
#: as a disclosure. A key is something the caller already holds. These two are
#: not: the payload is planted inside the withheld records' own text, and
#: ``pull/434`` is a spelling of the withheld pull request no request here
#: carries. Both are distinctive on purpose -- a bare ``434`` would false-fire
#: inside a ``contentSha256``.
#:
#: What holds the constant-refusal property instead is the withheld-versus-absent
#: differential in
#: :func:`test_a_withheld_thread_answers_as_a_thread_that_never_existed`.
PLANTED_VALUES: Final = (
    corpus.WITHHELD_PAYLOAD,
    f"pull/{corpus.PULL_REQUEST_WITHHELD}",
)

#: The distinctive fragment of ``mcp/validation.py``'s ``NO_SCHEMA_REFUSAL``,
#: which an *unregistered* tool name is answered with. Every equality below would
#: hold over two copies of that sentence -- two answers that agree because
#: neither came from this tool -- so :func:`_message` refuses it before any
#: assertion sees it. Quoted rather than imported because the template carries a
#: ``{tool}`` placeholder.
_NO_SCHEMA_FRAGMENT: Final = "publishes no input schema for that tool"

#: The three identifiers a successful draft mints, and the whole of what is
#: masked. Each is a fresh ULID from ``UlidGenerator``, so none of them can carry
#: a bit about the corpus: what a mask over them could hide is nothing a withheld
#: record could put there.
_MINTED_KEYS: Final = ("proposalId", "migrationId", "revisionId")


class _Commit(Enum):
    """Which of the three ``fixCommit`` inputs an arm names.

    A sentinel rather than the sha itself, because the sha is a property of the
    deployment's repository and the battery is built at import. Resolved per
    deployment by :func:`_fix_commit`, and the three deployments are asserted to
    resolve every sentinel to the same string, so the compared requests really
    are byte-identical.
    """

    #: A commit this repository has that touched the stored thread's ``filePath``.
    VERIFYING = "verifying"
    #: A commit this repository has that touched nothing the thread names.
    UNRELATED = "unrelated"
    #: Forty hex digits that resolve to no object in any repository.
    ABSENT = "absent"


def _fix_commit(project: ServedProject, commit: _Commit) -> str:
    match commit:
        case _Commit.VERIFYING:
            return project.verifying
        case _Commit.UNRELATED:
            return project.unrelated
        case _Commit.ABSENT:
            return corpus.ABSENT_COMMIT


@dataclass(frozen=True)
class _Deployment:
    """One project, one built review-search store, one open MCP session."""

    label: str
    project: ServedProject
    call: ToolCall
    data_dir: Path


@dataclass(frozen=True)
class _Corpora:
    """Two deployments that differ only in records no caller may be told about."""

    withholding: _Deployment
    never_held: _Deployment
    #: The same corpus as :attr:`withholding`, built with nothing withheld.
    control: _Deployment


@pytest.fixture(scope="module")
def corpora(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Corpora]:
    """The three deployments, built once for the module.

    Module-scoped because each one runs ``git init``, three CLI commands, a real
    evidence landing and a real store build, and every test below only reads
    them.

    ``HOME`` and ``THEURIAN_DATA_DIR`` are redirected for the fixture's whole
    life, and the working directory is restored once the builds are done:
    ``theurian init`` writes into the process's working directory and takes no
    argument that says where, so the ``chdir`` ``served_project`` makes is what
    contains it and this is what undoes it -- whether the builds succeeded or
    not.
    """
    origin = Path.cwd()
    base = tmp_path_factory.mktemp("candidate-two-corpora")
    home = base / "home"
    home.mkdir()
    whole = (*corpus.visible_corpus(), *corpus.withheld_corpus())
    build = contextmanager(served_project)

    with ExitStack() as stack, pytest.MonkeyPatch.context() as patched:
        patched.setenv("HOME", str(home))

        def deploy(
            label: str, records: tuple[EvidenceRecord, ...], withheld: frozenset[str]
        ) -> _Deployment:
            root = base / label
            root.mkdir()
            project = stack.enter_context(build(root, patched, records=records, withheld=withheld))
            data_dir = root / "daemon"
            call = stack.enter_context(mcp_session(build_server(project.registry), data_dir))
            return _Deployment(label=label, project=project, call=call, data_dir=data_dir)

        try:
            built = _Corpora(
                withholding=deploy("withholding", whole, WITHHELD_KEYS),
                never_held=deploy("never-held", corpus.visible_corpus(), frozenset()),
                control=deploy("control", whole, frozenset()),
            )
        finally:
            os.chdir(origin)
        yield built


# -- what is compared ------------------------------------------------------------


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


def _message(deployment: _Deployment, arguments: dict[str, Any]) -> dict[str, Any]:
    """One ``tools/call``, asserted to have reached the tool before it is read."""
    answer = deployment.call(TOOL, arguments)
    published = json.dumps(answer, sort_keys=True, ensure_ascii=False)

    assert _NO_SCHEMA_FRAGMENT not in published, (
        f"`{TOOL}` was answered by the input-validation middleware rather than by the "
        f"tool against {deployment.label}: {published!r}. Either it is not registered "
        f"or its published input schema is not loaded, and until one of those is fixed "
        f"every equality in this file would be about the middleware's own sentence."
    )
    return answer


def _raw(message: dict[str, Any]) -> str:
    """One call's whole outcome as a single string, refusals included.

    The whole JSON-RPC message rather than a field list, because the property is
    about everything published and a field list is what this shape exists to stop
    maintaining. ``id`` is the constant ``mcp_wire_session`` sends, so it carries
    nothing about the corpus.
    """
    return json.dumps(message, sort_keys=True, ensure_ascii=False)


def _masked(message: dict[str, Any]) -> str:
    """:func:`_raw`, with the three minted identifiers replaced by fixed tokens.

    A refusal mints nothing, so its masked form is its raw form. A success is
    masked everywhere each id appears -- inside ``proposalDirectory``,
    ``migrationFile``, ``bodyFile`` and the rest -- because the substitution is
    over the serialised string rather than over named keys.
    """
    structured = (message.get("result") or {}).get("structuredContent") or {}
    published = _raw(message)
    for key in _MINTED_KEYS:
        minted = structured.get(key)
        if isinstance(minted, str) and minted:
            published = published.replace(minted, f"<{key}>")
    return published


def _answer(deployment: _Deployment, arguments: dict[str, Any]) -> str:
    return _masked(_message(deployment, arguments))


def _proposals(deployment: _Deployment) -> set[str]:
    return deployment.project.proposals()


@dataclass(frozen=True)
class _Arm:
    """One request in the battery, and whether it is expected to reach the plant."""

    name: str
    record_key: str
    commit: _Commit
    #: Whether the **control** answers this request differently from a corpus
    #: that never held the withheld records. ``True`` says the arm has bite;
    #: ``False`` says it is here for another reason -- a visible thread, an
    #: absent key, a refusal upstream of the corpus -- and is not evidence of
    #: reach. :func:`test_the_battery_really_reaches_the_withheld_records`
    #: measures the whole column.
    reaches_withheld: bool
    overrides: tuple[tuple[str, Any], ...] = ()

    def arguments(self, deployment: _Deployment) -> dict[str, Any]:
        return _arguments(
            self.record_key, _fix_commit(deployment.project, self.commit), **dict(self.overrides)
        )


#: The battery. Each arm is here because it could separate the two corpora in a
#: different way: the first four name a withheld record, directly or through the
#: visible thread that depends on one; ``absent-thread`` is the differential that
#: makes "withheld" and "never existed" one event (SEC-13); the visible arms
#: cover every refusal this tool designs plus the answer it produces, because a
#: request that reaches no withheld record must still not start varying with one;
#: and the last two are refused before the gate, where a corpus-dependent message
#: would be a different channel to the same end.
BATTERY: Final[tuple[_Arm, ...]] = (
    _Arm("withheld-thread", corpus.THREAD_WITHHELD, _Commit.VERIFYING, True),
    _Arm("withheld-thread-absent-commit", corpus.THREAD_WITHHELD, _Commit.ABSENT, True),
    _Arm(
        "visible-thread-on-withheld-pull-request",
        corpus.THREAD_ON_WITHHELD_PULL_REQUEST,
        _Commit.VERIFYING,
        True,
    ),
    _Arm(
        "visible-thread-on-withheld-pull-request-unrelated-commit",
        corpus.THREAD_ON_WITHHELD_PULL_REQUEST,
        _Commit.UNRELATED,
        True,
    ),
    _Arm(
        "withheld-pull-request-by-its-own-key",
        str(corpus.PULL_REQUEST_WITHHELD),
        _Commit.VERIFYING,
        False,
    ),
    _Arm("absent-thread", corpus.THREAD_ABSENT, _Commit.VERIFYING, False),
    _Arm("satisfying", corpus.THREAD_SATISFYING, _Commit.VERIFYING, False),
    _Arm("satisfying-absent-commit", corpus.THREAD_SATISFYING, _Commit.ABSENT, False),
    _Arm("satisfying-unrelated-commit", corpus.THREAD_SATISFYING, _Commit.UNRELATED, False),
    _Arm("ci-unknown", corpus.THREAD_CI_UNKNOWN, _Commit.VERIFYING, False),
    _Arm("ci-failed", corpus.THREAD_CI_FAILED, _Commit.VERIFYING, False),
    _Arm("no-file-anchor", corpus.THREAD_NO_FILE_ANCHOR, _Commit.VERIFYING, False),
    _Arm("wrong-kind-record", str(corpus.PULL_REQUEST_CI_PASSED), _Commit.VERIFYING, False),
    _Arm(
        "refused-unknown-category",
        corpus.THREAD_SATISFYING,
        _Commit.VERIFYING,
        False,
        (("category", "not-a-category"),),
    ),
    _Arm(
        "refused-empty-body",
        corpus.THREAD_SATISFYING,
        _Commit.VERIFYING,
        False,
        (("body", ""),),
    ),
)


# -- the premises every comparison below rests on ---------------------------------


def _rendered(records: tuple[EvidenceRecord, ...]) -> str:
    """Every field of every record's payload, as one string.

    ``dataclasses.asdict`` rather than a field list, so the guard below covers a
    field added to ``ReviewThread`` or ``ReviewEvent`` by the change that adds
    it: a walk somebody maintains is a walk that falls behind the corpus it
    guards, which is how a planted value comes to be spelled by a visible record
    without anything saying so.
    """
    return " ".join(str(asdict(record.payload)) for record in records)


def test_the_two_corpora_differ_by_exactly_the_withheld_records() -> None:
    """The fixture's own arithmetic, so a corpus edit cannot make the pair unequal.

    ``withholding`` is built from the visible corpus **plus** the withheld one
    with those keys withheld; ``never_held`` from the visible corpus alone. If
    the two ever stopped being the same set of kept records, every equality in
    this module would be comparing two different corpora and would fail for a
    reason that is not a leak.

    Then the plants themselves, each of which would make a case below hold for
    any implementation if it stopped being true: two key shapes are withheld;
    every planted value really is in the withheld corpus and in no visible
    record -- both directions, because a value no record carries is absent from
    every answer whatever the tool does, and a value a *visible* record carries
    would fire the absence scan on a row the caller may read; and the visible
    thread still names the withheld pull request, which is the whole of what the
    second-resolve arms rest on.
    """
    visible = {record.record_key for record in corpus.visible_corpus()}
    withheld = {record.record_key for record in corpus.withheld_corpus()}

    assert withheld == WITHHELD_KEYS
    assert ({*visible, *withheld} - WITHHELD_KEYS) == visible, (
        "withholding the planted keys must leave exactly the visible corpus"
    )
    assert not visible & withheld, "a planted key is also a visible one"
    assert len(withheld) == 2, (
        "both record-key shapes must be withheld -- a thread by its node id and a "
        "pull request by its number -- or one of the two is never exercised"
    )
    withheld_text = _rendered(corpus.withheld_corpus())
    visible_text = _rendered(corpus.visible_corpus())
    for planted in PLANTED_VALUES:
        assert planted in withheld_text, (
            f"no withheld record carries {planted!r}, so every assertion that it is "
            f"absent from an answer is looking for a string this corpus never holds"
        )
        assert planted not in visible_text, (
            f"a visible record carries {planted!r}, so the absence scan would fire on "
            f"a row the caller is entitled to read"
        )
    dependant = next(
        record.payload
        for record in corpus.visible_corpus()
        if record.record_key == corpus.THREAD_ON_WITHHELD_PULL_REQUEST
    )
    assert isinstance(dependant, ReviewThread), (
        f"the dependent record is a {type(dependant).__name__}; only a thread carries "
        f"the `event_key` the second resolve reads"
    )
    assert dependant.event_key.endswith(f"#{corpus.PULL_REQUEST_WITHHELD}"), (
        f"the visible thread no longer names the withheld pull request "
        f"({dependant.event_key!r}), so the second-resolve arms reach nothing"
    )


def test_the_three_deployments_present_one_repository_to_the_verification(
    corpora: _Corpora,
) -> None:
    """The premise that makes the compared requests byte-identical.

    ``fixCommit`` is a wire field and a commit sha is a property of the
    deployment's own repository, so three projects built at three instants would
    put three different strings in the one field the comparison must hold equal
    -- and the tempting repair is to stop comparing that field, which is how a
    real difference gets masked. ``review_candidate_project`` pins the commit
    dates instead; this measures that it worked, in both directions: the shas
    agree across deployments, and the two within one deployment differ from each
    other, or the ``verifying``/``unrelated`` arms are naming the same input
    twice.
    """
    deployments = (corpora.withholding, corpora.never_held, corpora.control)
    for sentinel in _Commit:
        resolved = {_fix_commit(one.project, sentinel) for one in deployments}
        assert len(resolved) == 1, (
            f"the three deployments resolve {sentinel.value!r} to {sorted(resolved)}, so "
            f"the battery compares answers to requests that differ in `fixCommit`"
        )
        assert next(iter(resolved)), f"{sentinel.value!r} resolved to an empty string"
    assert corpora.withholding.project.verifying != corpora.withholding.project.unrelated, (
        "the verifying and unrelated commits are the same sha, so the arms that name "
        "them are one arm written twice"
    )


def test_a_served_answer_carries_no_identity_of_the_deployment_that_served_it(
    corpora: _Corpora,
) -> None:
    """GHSA-97q9's family, and the second premise the byte comparisons rest on.

    Three deployments necessarily differ somewhere -- here their project roots
    and their data directories, since the project id is held equal by
    construction. If any of that reached the wire the comparisons below would be
    unachievable for a reason that is not a leak, and the repair anyone would
    reach for is to normalise the differing field away, deleting the channel this
    file watches. ``HOME`` is in the token set for a different reason: it is the
    same for all three, so it could never break an equality, and an answer
    carrying it would be a disclosure nothing else here would catch.

    Measured over both shapes a caller can receive, because they are built by
    different code: a **success**, whose payload is assembled from real paths and
    is the one that could plausibly carry a root, and a **refusal**. Each token
    is asserted to be a real, non-empty identity of this deployment first, so the
    search cannot pass by looking for the empty string.
    """
    deployment = corpora.withholding
    success = _raw(
        _message(
            deployment,
            _arguments(corpus.THREAD_SATISFYING, deployment.project.verifying),
        )
    )
    refusal = _raw(
        _message(deployment, _arguments(corpus.THREAD_ABSENT, deployment.project.verifying))
    )
    tokens = {
        "project root": str(deployment.project.root),
        "data directory": str(deployment.data_dir),
        "home": os.environ["HOME"],
    }

    assert '"isError": false' in success, f"the probe needs a served answer: {success!r}"
    for label, token in tokens.items():
        assert token, f"the {label} token is empty, so searching for it proves nothing"
    assert Path(tokens["home"]).parent in deployment.project.root.parents, (
        "the deployment's root and the redirected HOME must share the fixture's "
        "temporary tree, or these tokens are not the deployment identity they claim "
        "to be -- and a test asserting a real machine path is absent proves nothing"
    )
    for label, token in tokens.items():
        for shape, published in (("answer", success), ("refusal", refusal)):
            assert token not in published, (
                f"the served {shape} carries the deployment's {label} ({token!r}). That is "
                f"a finding in its own right -- an operator's layout on a tool surface -- "
                f"and it also means the comparisons in this module cannot be byte "
                f"comparisons; they would have to normalise it away"
            )


def test_a_success_response_varies_only_in_the_three_identifiers_it_mints(
    corpora: _Corpora,
) -> None:
    """The premise that makes masking honest rather than a mask over a difference.

    ``ProposalService.draft`` mints a fresh proposal, migration and revision id
    on every call, so two successes never compare equal raw. The battery replaces
    those three values and nothing else; whether that is *exactly sufficient* is
    measured here rather than asserted by the shape of the code.

    Both directions matter. The raw inequality says the ids really are fresh, so
    the substitution is not masking a constant; the masked equality says nothing
    else in a success response moves between two calls to one deployment, so a
    difference the cross-corpus comparison finds is a difference in the corpus
    rather than in the minting. If a fourth minted value ever joined the payload,
    the masked equality is what reddens.
    """
    arguments = _arguments(corpus.THREAD_SATISFYING, corpora.withholding.project.verifying)
    first = _message(corpora.withholding, arguments)
    second = _message(corpora.withholding, arguments)

    assert first["result"]["isError"] is False, first
    minted = {key: first["result"]["structuredContent"][key] for key in _MINTED_KEYS}
    assert len(set(minted.values())) == len(_MINTED_KEYS), (
        f"the three identifiers are not three distinct values ({minted}), so masking "
        f"one of them would silently mask another"
    )
    assert _raw(first) != _raw(second), (
        "two successive successes were byte-identical, so nothing is being minted and "
        "the masking below removes a constant rather than a fresh value"
    )
    assert _masked(first) == _masked(second), (
        f"two successes against one deployment differ in something other than the three "
        f"minted identifiers, so the masked comparison is not a comparison of everything "
        f"else:\n  {_masked(first)}\n  {_masked(second)}"
    )


# -- the property ------------------------------------------------------------------


def test_a_withheld_thread_answers_as_a_thread_that_never_existed(corpora: _Corpora) -> None:
    """ADR-0033 decision 5, SEC-13. The mandatory case, stated explicitly.

    The request names the withheld thread's own key -- the request a caller makes
    when it already suspects what is being withheld. Three answers, in the order
    that makes a failure readable: the control must answer *differently*, or
    nothing was withheld and the equality would hold over a build that filtered
    nothing; then the pair must be byte-identical; and then the same key must
    answer exactly as a key this installation never landed, which is the
    withheld-versus-absent differential that an equality over two deployments
    cannot see on its own.

    The state on disk is part of the answer: a refusal that wrote a proposal
    directory would be observable to anyone who can list the project, whatever
    the response said.
    """
    request = _arguments(corpus.THREAD_WITHHELD, corpora.withholding.project.verifying)
    absent = _arguments(corpus.THREAD_ABSENT, corpora.withholding.project.verifying)
    before = _proposals(corpora.withholding)

    withholding = _answer(corpora.withholding, request)
    never_held = _answer(corpora.never_held, request)
    control = _answer(corpora.control, request)

    assert control != withholding, (
        "the control and the withholding deployment answered alike for the withheld "
        "thread's own key, so nothing was withheld and the equality below would pass "
        "over a build that did no withholding at all"
    )
    assert withholding.encode("utf-8") == never_held.encode("utf-8"), (
        "a deployment that withheld a thread answered a request naming that thread "
        "differently from one that never held it"
    )
    assert withholding == _answer(corpora.withholding, absent), (
        "a withheld thread and a thread this installation never landed are two "
        "different events on the wire, which is the oracle ADR-0033 decision 5 forbids"
    )
    for planted in PLANTED_VALUES:
        assert planted not in withholding, f"and no withheld {planted!r} may reach the answer"
    assert _proposals(corpora.withholding) == before, (
        "the refused call wrote a proposal directory, which is an observable a caller "
        "who can list the project reads as the record having been there"
    )


def test_a_visible_thread_whose_pull_request_is_withheld_answers_as_one_whose_never_existed(
    corpora: _Corpora,
) -> None:
    """The **second** resolve, which ``review.search`` has no analogue of.

    ``pull_request_merged`` and ``ci_successful`` are fields of the pull request,
    not of the thread, so this tool resolves twice: the caller's key, then the
    number parsed out of the thread's ``event_key``. The thread here is one the
    caller may read in both corpora; what differs is the record it *depends on*.
    A build that ran the two resolves through different visibility -- or that
    said "this thread's pull request is not available to you" where the other
    said "no such record" -- is caught by this case and by the two battery arms
    that name the same thread; no other request in this module reaches the
    second resolve with the record it asks for missing.

    The control answers this request with a **proposal**, so the equality is not
    satisfied by a pair of deployments that refuse everything.
    """
    request = _arguments(
        corpus.THREAD_ON_WITHHELD_PULL_REQUEST, corpora.withholding.project.verifying
    )

    withholding = _answer(corpora.withholding, request)
    never_held = _answer(corpora.never_held, request)
    control = _message(corpora.control, request)

    assert control["result"]["isError"] is False, (
        f"the control must generate a candidate for this thread, or the equality below "
        f"holds because the pull request is unreachable for some other reason: {control}"
    )
    assert withholding == never_held, (
        "a visible thread answered differently depending on whether the pull request it "
        "names was withheld or was never landed"
    )
    for planted in PLANTED_VALUES:
        assert planted not in withholding, f"and no withheld {planted!r} may reach the answer"


@pytest.mark.parametrize("arm", BATTERY, ids=[arm.name for arm in BATTERY])
def test_every_request_in_the_battery_answers_identically_over_the_two_corpora(
    corpora: _Corpora, arm: _Arm
) -> None:
    """ADR-0033 decision 5, over the whole published message.

    Nothing is named and only the three minted identifiers are masked, so a
    difference in *which* record reached a field separates here as loudly as a
    difference in a field's value, and a request refused against one corpus and
    answered against the other separates too.

    Which arms have bite is measured in
    :func:`test_the_battery_really_reaches_the_withheld_records` over the whole
    battery at once, rather than per arm: "the control differs" is meaningful
    only for the arms that name a withheld record, and asserting it case by case
    would either drop the other arms from the property or manufacture a
    precondition they cannot meet.
    """
    withholding = _answer(corpora.withholding, arm.arguments(corpora.withholding))
    never_held = _answer(corpora.never_held, arm.arguments(corpora.never_held))

    assert withholding == never_held, (
        "a published value varies with a record the caller may not be told about"
    )
    for planted in PLANTED_VALUES:
        assert planted not in withholding, f"and no withheld {planted!r} may reach the answer"


def test_the_battery_really_reaches_the_withheld_records(corpora: _Corpora) -> None:
    """The guard on the battery: an equality over two silences proves nothing.

    Measured rather than declared. Every arm is answered against the control and
    against the corpus that never held the withheld records, and the arms whose
    answers differ are the arms that really reach the plant. That measured set is
    compared against :attr:`_Arm.reaches_withheld`, so **both** directions redden
    -- an arm that quietly stopped biting because the corpus was edited, and an
    arm declared inert that has started answering for a withheld record.

    Without it, the equality above keeps passing over a corpus whose planted
    records have become unreachable, which is how a battery stops testing
    anything.
    """
    reaching = {
        arm.name
        for arm in BATTERY
        if _answer(corpora.control, arm.arguments(corpora.control))
        != _answer(corpora.never_held, arm.arguments(corpora.never_held))
    }
    declared = {arm.name for arm in BATTERY if arm.reaches_withheld}

    assert reaching == declared, (
        f"the battery's reach moved: arms that bite but are declared inert are "
        f"{sorted(reaching - declared)}, and arms declared to bite that no longer do are "
        f"{sorted(declared - reaching)}"
    )
    assert declared, "no arm in the battery reaches the withheld records at all"


def test_the_battery_carries_both_shapes_a_caller_can_receive(corpora: _Corpora) -> None:
    """The guard on the arm names: a battery of one shape tests half the property.

    ADR-0033 decision 5 owes the equality over responses **and** refusals, so an
    equality that only ever compared refusals would discharge half of it while
    reading as the whole. And an arm named ``refused-`` that quietly succeeded
    would be a success arm under a name that says otherwise -- writing proposals
    while its reader believed it refused before the gate.
    """
    answered = {
        arm.name
        for arm in BATTERY
        if _message(corpora.withholding, arm.arguments(corpora.withholding))["result"]["isError"]
        is False
    }
    named_refusals = {arm.name for arm in BATTERY if arm.name.startswith("refused-")}

    assert answered, "no arm in the battery is answered, so the response half is untested"
    assert len(answered) < len(BATTERY), "every arm is answered, so the refusal half is untested"
    assert named_refusals and not (named_refusals & answered), (
        f"an arm named as a refusal was answered: {sorted(named_refusals & answered)}"
    )


def test_the_control_generates_a_candidate_from_the_withheld_thread_through_the_same_tool(
    corpora: _Corpora,
) -> None:
    """The positive control, stated once as a case rather than only as a precondition.

    Everything above is an equality, and an equality between two deployments that
    can serve nothing holds for any implementation. This says the planted thread
    is ordinary, reachable review evidence: built with an empty withheld set,
    through the same builder, the same store and the same tool, the request that
    answers a miss against ``withholding`` lands a proposal here -- and lands it
    in ``.theurian/proposals/``, where the human review FR-V4 relies on can reach
    it (ADR-0013 point 7).

    It is also what fixes the *direction* of the property: without it, a build
    that dropped every record would satisfy this module completely.
    """
    request = _arguments(corpus.THREAD_WITHHELD, corpora.control.project.verifying)
    before = _proposals(corpora.control)

    result = _message(corpora.control, request)["result"]

    assert result["isError"] is False, result
    landed = result["structuredContent"]["proposalId"]
    assert _proposals(corpora.control) - before == {landed}, (
        f"the control's candidate did not land as a new proposal directory: {landed}"
    )
    assert (corpora.control.project.root / ".theurian/proposals" / landed).is_dir(), (
        "the candidate's proposal landed somewhere other than `.theurian/proposals/`"
    )


# -- the duration half: a cost that is never paid ----------------------------------


class _Spend:
    """What one call spends **below** the resolve, tallied on the real classes.

    Two costs sit there and nothing else does: the evidence file the resolved
    relative path names, read once per resolved record through
    ``EvidenceReader.record_at``; and the ``git`` processes ``FixCommitCheck``
    spawns to verify the caller's ``fixCommit``. Everything above the resolve --
    the project resolve, the provenance check, the store lookup -- is work both
    corpora do identically for every key.

    Patched on the **classes**, because the tool constructs both for itself per
    call (``mcp/tools.py``'s ``_review_evidence_reads`` and the
    ``FixCommitCheck(paths.root)`` beside it), so an instance wrapper cannot
    reach them. ``FixCommitCheck._run`` is the private name deliberately: its own
    docstring records it as "The single spawn site in this module", so counting
    it is counting processes, while ``verify`` counts only that the verification
    was entered.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.evidence_reads = 0
        self.commit_verifications = 0
        self.git_spawns = 0
        self._count(monkeypatch, EvidenceReader, "record_at", "evidence_reads")
        self._count(monkeypatch, FixCommitCheck, "verify", "commit_verifications")
        self._count(monkeypatch, FixCommitCheck, "_run", "git_spawns")

    def _count(self, monkeypatch: pytest.MonkeyPatch, owner: type, name: str, tally: str) -> None:
        original: Callable[..., Any] = getattr(owner, name)

        def wrapper(subject: Any, *args: Any, **kwargs: Any) -> Any:
            setattr(self, tally, getattr(self, tally) + 1)
            return original(subject, *args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)


def test_a_key_the_store_does_not_resolve_costs_no_evidence_read_and_no_git_spawn(
    corpora: _Corpora, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0033 decision 5's duration half, held as a count rather than a stopwatch.

    A refusal composed after reading the evidence file, or after spawning git,
    would be an oracle with identical wording: the work is observable even when
    the words are not. So the property is that the miss path *has no such work in
    it* -- the shape 0.2.3 used to close T-26, where the durable guarantee is a
    byte count pinned at zero and the wall clock is out-of-band corroboration.

    Both halves of the pair are driven, because "withheld costs nothing" and
    "absent costs nothing" are two statements and only together do they say the
    two are indistinguishable by cost. A cost that is never incurred cannot
    scale with the record that was withheld.

    ``tests/unit/test_candidate_generation.py::test_a_record_key_the_store_does_not_resolve_reads_no_evidence_file``
    holds the read half at the service, over injected seams. This holds both
    halves end to end: the real store, the real reader, the real git adapter and
    the real transport.
    """
    spend = _Spend(monkeypatch)

    for label, record_key in (
        ("withheld", corpus.THREAD_WITHHELD),
        ("absent", corpus.THREAD_ABSENT),
    ):
        result = _message(
            corpora.withholding,
            _arguments(record_key, corpora.withholding.project.verifying),
        )["result"]
        assert result["isError"] is True, (label, result)

    assert spend.evidence_reads == 0, (
        f"a key the store did not resolve still opened {spend.evidence_reads} evidence "
        f"file(s). The read is the first thing on this path that costs more than a "
        f"lookup, and paying it for a record the caller may not see is the duration "
        f"half of ADR-0033 decision 5's bind."
    )
    assert (spend.commit_verifications, spend.git_spawns) == (0, 0), (
        f"a key the store did not resolve still ran the commit verification "
        f"({spend.commit_verifications} call(s), {spend.git_spawns} git process(es)). "
        f"A process spawn is the largest single cost on this path."
    )


def test_the_same_key_unwithheld_pays_both_the_evidence_read_and_the_git_spawn(
    corpora: _Corpora, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control for the two zeros above, on the very same key.

    Without it, ``== 0`` is satisfied by an instrument that counts nothing, by a
    tool that reads no evidence at all, and by a build that refuses every call --
    three ways for the pin above to hold vacuously. Here the *same* request
    against the *control* deployment, where the key is not withheld, pays two
    evidence reads (the thread and its pull request, ADR-0033 decision 1's two
    resolves) and spawns git.

    **Two of the three counts are lower bounds and the third is an equality, and
    the split is the point.** What the pin above needs from the reads and the
    verification entries is only that the instrument separates a miss from a hit,
    so fixing their exact numbers would turn an added evidence read into a failure
    of the wrong test. The **git spawns** are different in kind: ADR-0033
    decision 5 binds the refusal's duration, and a process is the largest thing
    on this path, so *how many* is the property rather than an implementation
    detail the control should be blind to. This assertion used to read ``>= 1``
    and its reason used to be that an added ``rev-parse`` should not fail the
    wrong test -- which is exactly backwards: that second spawn was the
    +7.2 ms channel the C4b battery measured, and an equality here is what
    refuses its return.
    """
    spend = _Spend(monkeypatch)

    result = _message(
        corpora.control, _arguments(corpus.THREAD_WITHHELD, corpora.control.project.verifying)
    )["result"]

    assert result["isError"] is False, result
    assert spend.evidence_reads >= 2, (
        f"the hit path read {spend.evidence_reads} evidence file(s); it resolves a thread "
        f"and the pull request the thread names, so fewer than two means the instrument "
        f"is not seeing the reads the miss path is pinned not to make"
    )
    assert spend.commit_verifications >= 1, "the hit path did not verify the commit at all"
    assert spend.git_spawns == 1, (
        f"the hit path spawned {spend.git_spawns} git process(es). Zero means the pin "
        f"above is a zero the instrument would report either way; more than one means "
        f"the verification asks git a question it can sometimes skip, and a step that "
        f"is sometimes skipped is a duration the caller can time (ADR-0033 decision 5)."
    )


@pytest.mark.parametrize(
    "commit",
    [_Commit.ABSENT, _Commit.UNRELATED],
    ids=["no such commit", "touches nothing here"],
)
def test_each_commit_refusal_spends_exactly_one_git_process(
    corpora: _Corpora, monkeypatch: pytest.MonkeyPatch, commit: _Commit
) -> None:
    """Decision 5's duration bind on the *reachable* pair, through the whole tool.

    The two commit-verification refusals are one refusal in text, and
    ``test_candidate_generation.py`` holds that the strings are byte-identical.
    Identical strings are not enough: the caller cannot read the message it was
    refused with, but it can time the call, and until this branch the two arms
    cost a different number of processes -- an absent object was answered by
    ``rev-parse`` alone, a real commit needed ``diff-tree`` too. The C4b battery
    measured what that is worth from outside: **+7.2 ms, P=1.000**.

    So the property is a count, not a clock. ``tests/integration/``'s absence
    proof is where a wall-clock comparison would live, and it would be the weaker
    instrument: a machine-dependent number that a busy runner turns into a flake,
    where the count is exact and reproduces everywhere. The adapter's own half is
    ``test_fix_commit_check_adapter.py``'s byte-identical-vector pair; this is the
    same property where the caller stands, with the real store, the real reader,
    the real git adapter and the real transport between them.

    Both arms are refusals of the *same visible thread*, so nothing but the
    caller's ``fixCommit`` differs between them -- the record, the gate and the
    response shape are held constant by construction.
    """
    spend = _Spend(monkeypatch)

    result = _message(
        corpora.control,
        _arguments(corpus.THREAD_SATISFYING, _fix_commit(corpora.control.project, commit)),
    )["result"]

    assert result["isError"] is True, (
        f"the {commit.value} arm was not refused at all, so this says nothing about "
        f"what a refusal costs: {result}"
    )
    assert spend.commit_verifications == 1, (
        f"the {commit.value} arm entered the verification {spend.commit_verifications} "
        f"time(s); it must be reached exactly once, or the spawn count below is about "
        f"a path that was never taken"
    )
    assert spend.git_spawns == 1, (
        f"the {commit.value} refusal spent {spend.git_spawns} git process(es). Both "
        f"commit-verification refusals must cost the same work as well as carry the "
        f"same words: the difference between them is a fact about the repository, and "
        f"a caller that can count processes has been told it (ADR-0033 decision 5)."
    )
