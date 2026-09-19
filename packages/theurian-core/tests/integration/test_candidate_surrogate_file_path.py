"""A stored ``filePath`` carrying a lone surrogate fails closed, it does not crash (ADR-0033, T-24).

The candidate path re-reads the thread's evidence file at generation time and
hands ``thread.file_path`` to ``FixCommitCheck.verify``, which UTF-8-encodes it to
compare against git's raw path bytes. A ``filePath`` a landed evidence file can
carry -- ``json.loads`` decodes a ``\\udcff`` escape into a lone surrogate in
U+DC80..U+DCFF, and ``surrogateescape`` gives the same shape to a non-UTF-8 disk
byte -- has no UTF-8 encoding, so that encode raises ``UnicodeEncodeError``
**outside** the adapter's fail-closed ``except``. End to end the caller receives
the SDK's bare ``Error executing tool`` crash sentence: not a designed refusal,
not fail-closed.

``.theurian/review/`` is source a clone can deliver and is not git-ignored
(T-24), so a record whose ``filePath`` carries a surrogate is not hypothetical --
it is a hand-authorable input the store was built without. The designed answer is
a refusal that names the untransportable stored path (a ``TheurianError`` the
adapter converts to a wire refusal), raised **before** the verification's encode.

* :func:`test_the_service_refuses_a_surrogate_file_path_rather_than_crashing`
  drives the service against a real ``git`` repository, so the encode is the real
  one and *no ``UnicodeEncodeError`` escapes* is a claim about the shipped path.
* :func:`test_a_surrogate_file_path_crosses_the_wire_as_a_designed_refusal` drives
  the whole stack over the transport, so the refusal is asserted to cross the tool
  boundary with its cure rather than as the crash sentence.

RED until HIGH-1's fix lands (the 0.4.0 anchored pass): today both arms observe an
uncaught ``UnicodeEncodeError``. Nothing here touches the developer's machine --
the git repository, the project and its data directory live under ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
import review_candidate_fixtures as corpus
from review_candidate_project import ServedProject, served_project

from theurian.application.candidate_generation import (
    CandidateGenerator,
    CandidateSubmission,
)
from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.project_service import REVIEW_SEARCH_STORE_ID, ProjectPaths
from theurian.application.proposal_service import DraftedMigration, DraftedProposal, ProposalRequest
from theurian.application.review_landing_gate import ReviewRecordPayload
from theurian.daemon.runner import build_server
from theurian.domain.enums import KnowledgeKind, ReviewCommentCategory
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import AgentId, ItemId, TaskId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.proposal import Evidence
from theurian.domain.review import ReviewThread
from theurian.domain.review_search import untransportable_reason
from theurian.infrastructure.git.fix_commit_check import FixCommitCheck
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

from fakes.clock import FrozenClock  # isort: skip
from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration

TOOL: Final = "review.generateKnowledgeCandidate"

#: A ``filePath`` a landed evidence file can carry, exactly the value
#: ``json.loads(r'{"p": "src/retrying\udcff.py"}')["p"]`` yields (T-24). U+DCFF is
#: in U+DC80..U+DCFF -- the range ``surrogateescape`` uses for a non-UTF-8 disk
#: byte -- so ``subprocess`` encodes it back to ``0xff`` and ``git`` runs, and the
#: crash is the strict ``file_path.encode("utf-8")`` the membership check makes.
#:
#: Built with ``chr(0xDCFF)`` rather than a string literal so mypy types it ``str``
#: rather than a ``Literal`` it would serialise to its cache -- serialising a
#: surrogate-bearing literal crashes mypy on the very ``.encode("utf-8")`` this
#: module is about (mypy 2.3.1, ``cache.py`` ``write_literal``).
SURROGATE_FILE_PATH: Final = "src/retrying" + chr(0xDCFF) + ".py"

#: What the domain says is wrong with it, and the phrase the designed refusal
#: folds in the way ``review_search_builder._refuse_untransportable`` does -- a
#: single source of truth for both consumers of a stored path. Narrowed to ``str``
#: at import: a lone surrogate has no UTF-8 encoding by construction, so this is
#: never ``None`` unless the domain stops grading the shape, which
#: :func:`test_the_reason_constant_names_the_shape_this_module_is_about` reports.
_SURROGATE_REASON = untransportable_reason(SURROGATE_FILE_PATH)
assert _SURROGATE_REASON is not None
SURROGATE_REASON: Final = _SURROGATE_REASON


def test_the_reason_constant_names_the_shape_this_module_is_about() -> None:
    """Guards the message assertions below, which quote the domain's own reason.

    They assert ``SURROGATE_REASON in <refusal text>``; if the domain reason stopped
    naming the surrogate, those pins would pass over a phrase that says nothing about
    it. (Its non-``None``-ness is held at import, above, so the message pins can take
    it as a ``str``.)
    """
    assert "surrogate" in SURROGATE_REASON, (
        f"the domain no longer names an unpaired surrogate as untransportable "
        f"({SURROGATE_REASON!r}); the message pins below would assert nothing."
    )


# -- The service, against a real repository (no UnicodeEncodeError escapes) ---


class _RefuseToDraft:
    """A draft surface that fails if reached: the refusal must precede any proposal.

    A surrogate ``filePath`` must be refused before the draft, so a call that
    produces no candidate leaves no proposal behind. Raising here turns "a proposal
    was drafted from an untransportable record" into a red test rather than a silent
    directory on disk.
    """

    def draft(self, request: ProposalRequest, *, local: bool = False) -> DraftedProposal:
        raise AssertionError(
            f"a surrogate-`filePath` thread reached the draft-only facade "
            f"(local={local}, item {request.item_id.value!r}); the refusal must fire "
            f"before any proposal is drafted"
        )

    def draft_from_document(
        self, document: object, *, evidence: Evidence, local: bool = False
    ) -> DraftedMigration:
        raise AssertionError(
            f"the candidate path drafts a body and a revision, never a document -- got "
            f"{document!r} from {evidence.agent_id.value!r} (local={local})"
        )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@e.c", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def verifying_commit(tmp_path: Path) -> tuple[Path, str]:
    """A real repository whose HEAD exists, so ``verify`` reaches its encode.

    The commit's existence is what matters: an absent object exits 128 and refuses
    before the membership check runs, so the surrogate would never reach the encode
    the crash lives at. HEAD here touches nothing special -- the point is only that
    ``git`` answers exit 0 for it.
    """
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    (repo / corpus.FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (repo / corpus.FILE_PATH).write_text("def retry():\n    pass\n", encoding="utf-8")
    _git(repo, "add", corpus.FILE_PATH)
    _git(repo, "commit", "-q", "-m", "add the retry helper")
    return repo, _git(repo, "rev-parse", "HEAD")


def _payloads() -> dict[str, ReviewRecordPayload]:
    """The corpus's satisfying thread with a surrogate ``file_path``, and its pull request.

    Built from :func:`review_candidate_fixtures.evidence_records` rather than
    hand-shaped, so the thread and event are exactly what the shipped adapter
    produces; only ``file_path`` is replaced -- the one field this test is about.
    """
    by_key = {
        (record.repository, record.record_key): record.payload
        for record in corpus.evidence_records()
    }
    thread = by_key[(corpus.REPOSITORY, corpus.THREAD_SATISFYING)]
    assert isinstance(thread, ReviewThread)
    event = by_key[(corpus.REPOSITORY, str(corpus.PULL_REQUEST_CI_PASSED))]
    return {
        "thread": dataclasses.replace(thread, file_path=SURROGATE_FILE_PATH),
        "event": event,
    }


def _submission(fix_commit: str) -> CandidateSubmission:
    return CandidateSubmission(
        repository=corpus.REPOSITORY,
        record_key=corpus.THREAD_SATISFYING,
        fix_commit=fix_commit,
        item_id=ItemId("reliability.retry-lock-order"),
        title="Acquire locks after reads in retry-eligible paths",
        body="Acquire locks after reads, never before, in retry-eligible paths.\n",
        kind=KnowledgeKind.CONVENTION,
        category=ReviewCommentCategory.RELIABILITY_RULE,
        owner="platform-team",
        author="dana@example.com",
        description="Generalise the deadlock thread into a locking rule",
        evidence=Evidence(
            agent_id=AgentId("claude-code"),
            task_id=TaskId("task-431"),
            model="claude-opus-5",
            reasoning="The thread settled the lock ordering; this generalises it.",
            anchors=(
                SourceAnchor(
                    provider="github",
                    source_uri=f"https://github.com/{corpus.REPOSITORY}/pull/431#discussion_r1",
                    repository=corpus.REPOSITORY,
                    file_path=corpus.FILE_PATH,
                ),
            ),
        ),
    )


def test_the_service_refuses_a_surrogate_file_path_rather_than_crashing(
    verifying_commit: tuple[Path, str],
) -> None:
    """A stored surrogate ``filePath`` earns a designed refusal at the verify seam (T-24).

    The commit is real, so ``verify`` reaches ``file_path.encode("utf-8")`` -- the
    line that raises ``UnicodeEncodeError`` for a lone surrogate, outside the
    adapter's fail-closed ``except``. The designed answer is a ``TheurianError`` the
    tool converts to a wire refusal, raised before the verification and naming why
    the stored path cannot be checked.

    ``UnicodeEncodeError`` is a ``ValueError`` but not a ``TheurianError``, so
    ``pytest.raises(TheurianError)`` fails on the raw crash and passes only once the
    candidate path inspects the stored path first. The message is asserted to name
    the transportability reason and **not** the commit-verification refusal's words:
    a fix that only hardened ``verify`` to ``NO_SUCH_COMMIT`` would send this refusal
    back saying the repository has no commit that touched the path -- false, and the
    misleading answer this pin exists to reject.
    """
    repo, fix_commit = verifying_commit
    payloads = _payloads()
    resolved = {
        (corpus.REPOSITORY, corpus.THREAD_SATISFYING): "thread",
        (corpus.REPOSITORY, str(corpus.PULL_REQUEST_CI_PASSED)): "event",
    }
    generator = CandidateGenerator(
        resolve_evidence_path=lambda repository, key: resolved.get((repository, key)),
        read_record=lambda relative: payloads[relative],
        verify_fix_commit=FixCommitCheck(repo).verify,
        drafts=DraftOnlyProposals(_RefuseToDraft()),
        clock=FrozenClock(),
    )

    with pytest.raises(TheurianError) as refusal:
        generator.generate(_submission(fix_commit))

    message = str(refusal.value)
    assert SURROGATE_REASON in message, (
        f"the refusal {message!r} does not name why the stored `filePath` cannot be "
        f"verified. The candidate path is expected to fold `untransportable_reason` "
        f"in, the way `review_search_builder._refuse_untransportable` does, so the "
        f"caller learns the stored path is the problem rather than the commit."
    )
    assert "this repository has that touched" not in message, (
        f"the refusal {message!r} says the repository has no commit that touched the "
        f"path -- the commit-verification refusal's words. The stored path is "
        f"untransportable, so the verification never ran; borrowing that sentence "
        f"sends the caller hunting for a commit that would satisfy an unsatisfiable "
        f"check."
    )


# -- The wire: a designed refusal, not the SDK's crash sentence ---------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServedProject]:
    """The served project of ``review_candidate_project``, withholding nothing."""
    yield from served_project(
        tmp_path, monkeypatch, records=corpus.evidence_records(), withheld=frozenset()
    )


def _arguments(fix_commit: str) -> dict[str, Any]:
    return {
        "projectId": "demo",
        "repository": corpus.REPOSITORY,
        "recordKey": corpus.THREAD_SATISFYING,
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
                "sourceUri": f"https://github.com/{corpus.REPOSITORY}/pull/431#discussion_r1",
                "repository": corpus.REPOSITORY,
                "filePath": corpus.FILE_PATH,
            }
        ],
    }


def _plant_surrogate_file_path(project: ServedProject) -> None:
    """Rewrite the built thread's evidence file so its payload ``filePath`` is a surrogate.

    The store is built from a transportable corpus -- ``ReviewSearchBuilder`` refuses
    an untransportable projection, so a surrogate cannot be present at build time.
    A clone delivering ``.theurian/review/`` after the build is the T-24 shape, and
    it is what reaches the candidate path: the resolve keys on ``record_key`` (a hash
    of the record key, unchanged by the edit), so the row still resolves and the tool
    re-reads this now-untransportable file.
    """
    paths = ProjectPaths.of(project.root)
    store = SqliteReviewSearchStore(paths.review_search_for(REVIEW_SEARCH_STORE_ID))
    relative = store.relative_path_for(corpus.REPOSITORY, corpus.THREAD_SATISFYING)
    assert relative is not None, (
        "the store does not resolve the satisfying thread, so the plant would test "
        "nothing -- the surrogate would never reach the re-read"
    )

    evidence_file = paths.review / relative
    document = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert document["record"]["filePath"] == corpus.FILE_PATH, (
        f"the built thread's stored `filePath` is {document['record'].get('filePath')!r}, "
        f"not the transportable one this plant replaces; the document shape moved"
    )
    document["record"]["filePath"] = SURROGATE_FILE_PATH
    # `ensure_ascii=True` escapes the surrogate to `\udcff`, so the bytes on disk are
    # valid UTF-8 and the reader's `raw.decode("utf-8")` succeeds -- `json.loads`
    # then decodes the escape back into the lone surrogate the codec preserves.
    evidence_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_a_surrogate_file_path_crosses_the_wire_as_a_designed_refusal(
    project: ServedProject, tmp_path: Path
) -> None:
    """The refusal crosses the tool boundary with its cure, not as ``Error executing tool``.

    The control is the satisfying thread the same call lands a proposal for
    (``test_candidate_generation_wire.py``); here its evidence file is rewritten to
    carry a surrogate ``filePath`` after the store was built, the T-24 shape. The
    candidate path re-reads the file, hands the surrogate to ``verify``, and the
    encode raises -- reaching the ``_forwarding`` seam as a non-``TheurianError``.

    ``isError`` alone does not separate the crash from the fix: the SDK marks both
    ``isError``, and it prefixes *every* tool error -- designed refusal included --
    with ``Error executing tool <name>``. What separates them is the message after
    that name. An uncaught exception surfaces as the **bare** ``Error executing tool
    review.generateKnowledgeCandidate`` (its own text kept server-side, SEC-13); a
    designed ``TheurianError`` surfaces as that name followed by ``: `` and the
    refusal -- which names the untransportable stored path and carries its cure.
    """
    bare_crash = f"Error executing tool {TOOL}"
    _plant_surrogate_file_path(project)

    with mcp_session(build_server(project.registry), tmp_path / "data") as call:
        answer = call(TOOL, _arguments(project.verifying))

    result: dict[str, Any] = answer["result"]
    content = result.get("content") or [{}]
    text: str = content[0].get("text", "")

    assert result["isError"] is True, result
    assert text != bare_crash, (
        f"a surrogate `filePath` reached the caller as the SDK's bare crash sentence "
        f"{text!r}. An uncaught exception surfaces with no message after the tool name; "
        f"the encode raised `UnicodeEncodeError`, which is not a `TheurianError`, so "
        f"`_forwarding` did not convert it to a designed refusal."
    )
    assert SURROGATE_REASON in text, (
        f"the wire refusal {text!r} does not name why the stored `filePath` cannot be "
        f"verified. A designed refusal folds `untransportable_reason` in so the caller "
        f"learns the stored record is the problem."
    )
    assert "theurian " in text, (
        f"the refusal {text!r} names no command a caller can run: a designed refusal on "
        f"this path carries a `remedy`, and `_with_remedy` folds it into the wire message."
    )
    assert project.proposals() == set(), "a refused surrogate-`filePath` call wrote a proposal"
