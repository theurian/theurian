"""A built project serving the candidate-generation corpus, over a real repository.

Split out of ``tests/integration/test_candidate_generation_wire.py`` because the
setup is not that module's subject and is needed by more than one of them: the wire
refusal battery drives it, and ADR-0033 decision 5's two-corpora battery needs the
same project built twice over two corpora. A second copy of this would be a second
place for the review store's provenance record, or one of the two commits, to be
quietly dropped -- and either would leave a battery refusing every call for a
reason that has nothing to do with what it claims to measure.

**The two commits are what the ``fixCommit`` verification is separated by**, so
they are made here rather than left to a caller: one touching the stored thread's
``filePath`` and one touching a different tracked file. A single sweeping commit
would leave no input that exists in the repository and touches nothing the thread
names, which is the verdict that tells a verification apart from a bare
``cat-file -e``.

Nothing here touches the developer's machine: the project, its git repository and
its data directory are created under the caller's ``tmp_path``, and every ``git``
call names the repository with ``cwd`` rather than depending on the process's
working directory.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
import review_candidate_fixtures as corpus
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    REVIEW_SEARCH_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.main import app
from theurian.cli.review_commands import evidence_entries, evidence_fingerprints
from theurian.infrastructure.review_evidence import EvidenceRecord, ReviewEvidenceStore
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

runner = CliRunner()

PROJECT_ID: Final = "demo"

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

#: An identity and a signing setting on every commit, so the repository below never
#: depends on -- nor is broken by -- the developer's ambient ``~/.gitconfig``; a
#: global ``commit.gpgsign = true`` would otherwise fail the commit.
_GIT_IDENTITY: Final = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)

#: Author and committer date on every commit this module's projects make, so a
#: commit sha is a function of content alone. See :func:`served_project` for the
#: comparison that needs it and the measurement that confirms it.
_COMMIT_INSTANT: Final = "2026-09-19T12:00:00+00:00"


@dataclass(frozen=True)
class ServedProject:
    """A served project, with the two commit shas the verification separates."""

    registry: ProjectRegistry
    root: Path

    #: A commit that exists here **and touched the stored thread's ``filePath``**.
    #: The only one of the three inputs that satisfies ``fix_commit_present``.
    verifying: str

    #: A commit that exists here and touched a different file -- the
    #: ``TOUCHES_NOTHING_HERE`` verdict.
    unrelated: str

    def proposals(self) -> set[str]:
        """The proposal directory names under this project, both locations.

        Both, because a refusal that wrote nothing and a draft that landed in the
        ignored ``proposals-local`` tree are different failures: the second is a
        proposal a reviewer cannot see (ADR-0013 point 7), and a counter that
        looked only at the reviewed directory would report it as nothing written.
        """
        found: set[str] = set()
        for parent in (self.root / ".theurian/proposals", self.root / ".theurian/proposals-local"):
            if parent.exists():
                found |= {entry.name for entry in parent.iterdir() if entry.is_dir()}
        return found


def run_cli(*args: str) -> None:
    """One CLI command in-process, asserted to have succeeded."""
    if args[:2] == ("migrate", "apply"):
        # ADR-0034: the migration must be tracked and byte-identical to HEAD
        # before the apply, which this helper is what arranges.
        commit_migrations()
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", *_GIT_IDENTITY, *args],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def commit_one(repo: Path, relative: str, body: str, message: str) -> str:
    """Write ``relative``, commit it **alone**, and answer that commit's own sha.

    Alone rather than through ``git add -A``: what each sha has to mean here is
    *this commit touched this path and no other*, and a sweeping stage would make
    both commits touch both files.
    """
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    git(repo, "add", relative)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def land_and_build(
    registry: ProjectRegistry,
    root: Path,
    *,
    records: Sequence[EvidenceRecord],
    withheld: frozenset[str],
) -> None:
    """Land ``records`` and project them, through the real writer, reader and builder.

    ``evidence_entries`` is the composition root's own mapping, imported rather
    than re-implemented: a harness that mapped the records itself would keep
    passing over a root that had stopped carrying a field.

    **Neither keyword has a default**, for the reason
    ``ReviewSearchBuildRequest.withheld_record_keys`` has none: "nothing is
    withheld" is the state that must never be implicit, and a default parameter
    is exactly how it would come back. Every caller states its corpus and its
    withholding posture.

    The provenance record is not optional. ``review.search`` -- and any tool
    reading the same store -- refuses one this installation has no record of
    building (ADR-0004, SEC-7, T-19), so without this line every call would meet
    the unavailable-store refusal instead of the behaviour under test.
    """
    paths = ProjectPaths.of(root)
    evidence = ReviewEvidenceStore(paths.review)
    evidence.write(tuple(records), run=corpus.INGESTION_RUN)
    store = SqliteReviewSearchStore(paths.review_search_for(REVIEW_SEARCH_STORE_ID))
    ReviewSearchBuilder(
        read_evidence=evidence_entries(evidence),
        list_evidence_fingerprints=evidence_fingerprints(paths.review),
        write=store.replace_all,
    ).build(ReviewSearchBuildRequest(withheld_record_keys=withheld))
    BuildProvenance.for_registry(registry).record_review(paths.root, REVIEW_SEARCH_STORE_ID)


def served_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    records: Sequence[EvidenceRecord],
    withheld: frozenset[str],
) -> Iterator[ServedProject]:
    """A registered, migrated project serving the corpus, over a real repository.

    A generator rather than a fixture, so each consuming module wraps it in its own
    ``@pytest.fixture`` under whatever name reads best there; making it a fixture
    here would need a ``conftest`` and would put it in scope for the whole tree.

    Everything the tool's own resolution needs is real -- registry entry, active
    state pointer, canonical-state provenance, review-store provenance -- so a call
    refused against this project is refused by the candidate gate rather than by an
    unresolvable project or an unbuilt store.

    The two commits are made **after** ``migrate apply``: that command's own
    committed-check sweeps the working tree into a commit, and a file staged before
    it would land there instead of in a commit of its own.

    **Both commit shas are deterministic**, because a caller that builds this
    project twice compares answers across the two and a ``fixCommit`` that moved
    with the wall clock would make the two requests differ in the one field the
    comparison must hold equal. A commit's sha is a function of its tree, its
    parents, its identity and its **dates**, and the first three are already
    fixed here; the two ``GIT_*_DATE`` variables fix the last. Measured
    2026-09-19: two independently built projects answer the same
    ``verifying``, the same ``unrelated`` and the same ``HEAD``. The evidence
    files land *after* both commits, so a corpus difference cannot move them --
    ``test_candidate_generation_absence_proof.py`` asserts that equality rather
    than assuming it.
    """
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GIT_AUTHOR_DATE", _COMMIT_INSTANT)
    monkeypatch.setenv("GIT_COMMITTER_DATE", _COMMIT_INSTANT)

    root = tmp_path / PROJECT_ID
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.chdir(root)
    run_cli("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    run_cli("project", "register")
    run_cli("migrate", "apply")

    verifying = commit_one(root, corpus.FILE_PATH, "def retry():\n    pass\n", "add retry helper")
    unrelated = commit_one(root, corpus.OTHER_FILE_PATH, "PLACEHOLDER = 1\n", "touch another file")

    registry = ProjectRegistry.default(data_dir)
    land_and_build(registry, root, records=records, withheld=withheld)
    yield ServedProject(registry=registry, root=root, verifying=verifying, unrelated=unrelated)
