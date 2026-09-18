"""The proposal a generated candidate writes, end to end onto a real disk (ADR-0033).

The half of ADR-0033 that a unit test cannot hold, because the subject *is* the
thing on disk. ``trustLevel: inferred`` on the in-memory candidate is the type's
declaration; on the *written migration* it is a mapping that could have dropped
it, and a dropped field is not a visible failure -- the loader applies
``unverified`` and the knowledge claims less trust than the candidate carried. The
same for ``author`` and ``evidence.json``'s ``agentId``, which the migration
schema defines as a human and an agent respectively.

The whole stack is real: a ``git init`` repository whose commit the verification
actually checks, the codec, and ``ProposalService`` writing a proposal directory.
**The ``fixCommit`` verification's own cases are
``test_fix_commit_check_adapter.py``'s** -- the three verdicts, the root commit,
the pathspec-magic class and the captured vector -- and what the commit below is
for is only to get this path as far as a written proposal.

The evidence records are read back through the codec rather than handed over as
objects, because that is the path a stored record takes
(``thread_from_json`` validates through the domain constructors, which is where
``has_evidence``'s vacuity comes from -- ADR-0033 decision 3's note).

Nothing here touches the developer's machine: the git repository is created under
``tmp_path`` and every ``git`` call names it with ``cwd``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Collection, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.candidate_generation import (
    CandidateGenerator,
    CandidateSubmission,
    ReadEvidenceRecord,
)
from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import ProposalService
from theurian.application.review_landing_gate import ReviewRecordPayload
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.enums import KnowledgeKind, ReviewCommentCategory, ReviewThreadState
from theurian.domain.identifiers import (
    AgentId,
    ItemId,
    MigrationId,
    ProjectId,
    RevisionId,
    TaskId,
)
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.infrastructure.git.fix_commit_check import FixCommitCheck
from theurian.infrastructure.review_evidence.codec import (
    event_from_json,
    event_to_json,
    thread_from_json,
    thread_to_json,
)

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

PROJECT = ProjectId("demo")
REPOSITORY = "theurian/theurian"
#: Low-entropy on purpose, and this is the occurrence the secret scan reported.
#: The measurement, and why the tail must stay a run of one character, is on the
#: same constant in ``tests/unit/test_candidate_generation.py``.
THREAD_KEY = "PRRT_kwDOaaaaaaaaaaaa"
PULL_REQUEST = 431
THREAD_FILE = f"sha256-1f0e/review-thread/{THREAD_KEY}.json"
EVENT_FILE = f"sha256-1f0e/pull-request/{PULL_REQUEST}.json"

#: The file the thread is anchored to, and the one the verification asks about.
FILE_PATH = "src/retrying.py"

HUMAN_AUTHOR = "dana@example.com"

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-431"),
    model="claude-opus-5",
    reasoning="PR #431's thread settled the lock ordering; this generalises it.",
    anchors=(
        SourceAnchor(
            provider="github",
            source_uri=f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST}#discussion_r1",
            repository=REPOSITORY,
            file_path=FILE_PATH,
        ),
    ),
)

#: An identity and a signing setting on every call, so the commits below never
#: depend on -- nor are broken by -- the developer's ambient `~/.gitconfig`; a
#: global `commit.gpgsign = true` would otherwise fail the commit.
_GIT_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)


def _participant(login: str) -> ReviewParticipant:
    return ReviewParticipant(
        provider="github", external_id=f"MDQ6VXNlcg-{login}", display_name=login
    )


def _event() -> ReviewEvent:
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
        merged=True,
        merge_commit="c" * 40,
        merged_at=datetime(2026, 9, 2, 11, 30, tzinfo=UTC),
        ci_successful=True,
    )


def _thread(event: ReviewEvent) -> ReviewThread:
    """A conversation as ``review_provider.py`` builds one: ``fix_commit`` absent."""
    return ReviewThread(
        external_id=THREAD_KEY,
        project_id=PROJECT,
        event_key=event.external_key,
        file_path=FILE_PATH,
        comments=(
            ReviewComment(
                external_id="PRRC_kwDOABCD1M5aaaaa",
                author=_participant("reviewer"),
                body="This will deadlock under retry. Take the lock after the read.",
                created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
                category=ReviewCommentCategory.RELIABILITY_RULE,
            ),
        ),
        state=ReviewThreadState.RESOLVED,
        resolution=ReviewResolution(
            state=ReviewThreadState.RESOLVED, resolved_by=_participant("reviewer")
        ),
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *_GIT_IDENTITY, *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit(repo: Path, relative: str, body: str, message: str) -> str:
    """Write ``relative``, commit it, and answer the commit's own sha."""
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[Path]:
    """A throwaway git repository holding the file the stored thread is anchored to.

    Setup for the proposal half, not its subject: ``_generate`` commits a change to
    that file so the verification passes and the path reaches a written proposal.
    What the verification *answers* is ``test_fix_commit_check_adapter.py``'s.
    """
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "-c", "init.defaultBranch=main", "init", "-q")
    _commit(repo, FILE_PATH, "def retry():\n    pass\n", "add the retry helper")
    yield repo


@pytest.fixture
def paths(tmp_path: Path) -> ProjectPaths:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    return project


def _proposal_service(paths: ProjectPaths) -> ProposalService:
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    def landed_migration(migration_id: MigrationId) -> Migration | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set.get(migration_id)

    def landed_migrations() -> Collection[Migration]:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set

    return ProposalService(
        paths=paths,
        project_id=PROJECT,
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _evidence_directory(root: Path) -> Path:
    """The two records on disk, as JSON documents the codec reads back."""
    review = root / "review"
    for relative, document in (
        (THREAD_FILE, thread_to_json(_thread(_event()))),
        (EVENT_FILE, event_to_json(_event())),
    ):
        target = review / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return review


def _read_record(review: Path) -> ReadEvidenceRecord:
    """The composition root's evidence read, in miniature.

    Dispatched on the middle path segment, which *is* the record kind: the layout
    names each kind's directory after the ``EvidenceKind`` member itself, so there
    is no second table here that could disagree with the one that wrote the file.
    """

    def read(relative_path: str) -> ReviewRecordPayload:
        document = json.loads((review / relative_path).read_text(encoding="utf-8"))
        if "/review-thread/" in relative_path:
            return thread_from_json(document, relative_path)
        return event_from_json(document, relative_path)

    return read


def _generator(repo: Path, review: Path, service: ProposalService) -> CandidateGenerator:
    resolved = {
        (REPOSITORY, THREAD_KEY): THREAD_FILE,
        (REPOSITORY, str(PULL_REQUEST)): EVENT_FILE,
    }
    check = FixCommitCheck(repo)
    return CandidateGenerator(
        resolve_evidence_path=lambda repo_name, key: resolved.get((repo_name, key)),
        read_record=_read_record(review),
        verify_fix_commit=check.verify,
        drafts=DraftOnlyProposals(service),
        clock=FrozenClock(),
    )


#: The same evidence carrying a **second** anchor: the thread it generalises and
#: the commit that closed it. Every other fixture in the suite sends one, which
#: is what made a truncation to the first invisible all the way to the file --
#: this is the one place the *written* ``metadata.sourceAnchors`` can show it.
TWO_ANCHORS = dataclasses.replace(
    EVIDENCE,
    anchors=(
        *EVIDENCE.anchors,
        SourceAnchor(
            provider="git",
            source_uri=f"git://{REPOSITORY}/fix",
            repository=REPOSITORY,
            file_path=FILE_PATH,
        ),
    ),
)


def _submission(fix_commit: str, *, evidence: Evidence = EVIDENCE) -> CandidateSubmission:
    return CandidateSubmission(
        repository=REPOSITORY,
        record_key=THREAD_KEY,
        fix_commit=fix_commit,
        item_id=ItemId("reliability.retry-lock-order"),
        title="Acquire locks after reads in retry-eligible paths",
        body="Acquire locks after reads, never before, in retry-eligible paths.\n",
        kind=KnowledgeKind.CONVENTION,
        category=ReviewCommentCategory.RELIABILITY_RULE,
        owner="platform-team",
        author=HUMAN_AUTHOR,
        description="Generalise PR #431's deadlock thread into a locking rule",
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# What the proposal on disk says (decision 1's table).
# ---------------------------------------------------------------------------


def _generate(
    repository: Path, paths: ProjectPaths, tmp_path: Path, *, evidence: Evidence = EVIDENCE
) -> Path:
    """Generate one candidate against a verifying commit, and answer its directory."""
    touching = _commit(repository, FILE_PATH, "def retry():\n    return None\n", "fix the deadlock")
    review = _evidence_directory(tmp_path)
    generated = _generator(repository, review, _proposal_service(paths)).generate(
        _submission(touching, evidence=evidence)
    )
    return generated.proposal.directory


def _migration(directory: Path) -> Mapping[str, object]:
    (migration_file,) = [p for p in directory.iterdir() if p.suffix in {".yaml", ".yml"}]
    parsed = yaml.safe_load(migration_file.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_the_written_migration_records_the_candidates_inferred_trust_level(
    repository: Path, paths: ProjectPaths, tmp_path: Path
) -> None:
    """ADR-0033 decision 1: ``trustLevel: inferred`` on the file, not only in memory.

    ``KnowledgeCandidate.trust_level`` is ``init=False`` and fixed, so the
    in-memory value cannot be wrong; what can be wrong is the mapping onto the
    migration, where ADR-0032 decision 1's "absent means not stated" would leave
    the key out and let the loader apply ``unverified``. That is a quieter defect
    than a wrong value: nothing refuses, and the knowledge claims less trust than
    the candidate that produced it -- which is the claim a reviewer reads when
    deciding whether to merge.
    """
    directory = _generate(repository, paths, tmp_path)

    operations = _migration(directory)["operations"]
    assert isinstance(operations, list)
    upsert = next(op for op in operations if op["op"] == "upsertRevision")

    assert upsert["metadata"]["trustLevel"] == "inferred", (
        f"the written migration records trust level "
        f"{upsert['metadata'].get('trustLevel')!r}. A candidate cannot be "
        f"constructed with any other value, so this is the mapping dropping it."
    )


def test_every_source_anchor_the_submission_carries_reaches_the_written_migration(
    repository: Path, paths: ProjectPaths, tmp_path: Path
) -> None:
    """FR-R5 and INV-8 on the file a reviewer opens, not only on the object in memory.

    ``sourceAnchors`` is where a generalisation came from, and the copy that
    matters is the one in the pull request: ``metadata.sourceAnchors`` on the
    written migration is what a human reads before deciding whether the
    generalisation is fair (FR-V4). A mapping that carried the head of the tuple
    and dropped the tail satisfies INV-8 -- a candidate needs *an* anchor -- and
    refuses nothing, so the loss is silent and lands in the record.

    Two anchors, because with one the truncation is unobservable: ``[:1]``,
    ``next(iter(...))`` and the correct mapping all write the same document.
    """
    directory = _generate(repository, paths, tmp_path, evidence=TWO_ANCHORS)

    operations = _migration(directory)["operations"]
    assert isinstance(operations, list)
    written = next(op for op in operations if op["op"] == "upsertRevision")["metadata"][
        "sourceAnchors"
    ]

    assert len(TWO_ANCHORS.anchors) == 2, "the fixture has to carry more than one anchor"
    assert [anchor["sourceUri"] for anchor in written] == [
        anchor.source_uri for anchor in TWO_ANCHORS.anchors
    ], (
        f"the written migration records {len(written)} of {len(TWO_ANCHORS.anchors)} "
        f"anchors the submission carried, in {[a['sourceUri'] for a in written]}. Order "
        f"is asserted as well as membership: `sourceAnchors` is a list a reviewer reads "
        f"top down, and the caller chose which anchor comes first."
    )


def test_the_migration_names_the_human_and_the_evidence_names_the_agent(
    repository: Path, paths: ProjectPaths, tmp_path: Path
) -> None:
    """The migration schema's ``author`` rule, held across the two files that carry it.

    The schema defines ``author`` as "Identity of the human who authored this
    change. Agent-generated proposals record the agent separately in
    evidence.json; approval is always human". Two values, two readers, and
    ADR-0033 decision 1 records
    that they are never filled from each other: an agent id where a reviewer looks
    for a person would make an unattributable change look attributed, and a
    person's address in the provenance record would name a human as the run.
    """
    directory = _generate(repository, paths, tmp_path)

    migration = _migration(directory)
    evidence = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))

    assert (migration["author"], evidence["agentId"]) == (
        HUMAN_AUTHOR,
        EVIDENCE.agent_id.value,
    ), (
        f"the migration names author {migration['author']!r} and the evidence names "
        f"agent {evidence['agentId']!r}; the submission named {HUMAN_AUTHOR!r} and "
        f"{EVIDENCE.agent_id.value!r}."
    )
