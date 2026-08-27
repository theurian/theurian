"""Packaging and accepting a proposal (ADR-0013 §4).

The service is the half both composition roots share: the CLI drives it today
and Milestone 7's write-intent MCP tools drive the same calls. These tests use
it directly, so a defect is located in the packaging rather than in Typer.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path
from typing import Final, NoReturn

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator
from hang_guard import CAN_INTERRUPT_A_HANG, fails_rather_than_hanging

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    _PERMISSION_ERRNOS,
    MAX_UPSERT_OPERATIONS,
    ApprovedSetUnusableError,
    CandidateMigrationSet,
    ChangeAlreadyInPlaceError,
    DraftedProposal,
    ProposalAlreadyAcceptedError,
    ProposalError,
    ProposalRequest,
    ProposalService,
    _evidence_failure_reason,
    _refuse_past_the_operation_cap,
    _require_filename_matches_id,
)
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import (
    InputTooLargeError,
    InvariantViolationError,
    IrregularSourceFileError,
    MigrationError,
    PathEscapeError,
    SchemaUnreadableError,
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
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.values import JSON, MARKDOWN, YAML, ContentHash
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.security.paths import MAX_SOURCE_FILE_BYTES
from theurian.security.yaml_loading import load_yaml_mapping

#: A ``chmod 0o000`` denies nothing to root and nothing on Windows, so a test
#: that needs the mode to actually refuse cannot run there (the offline CI job
#: runs as root). Same guard the sibling permission tests carry.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: A FIFO is the shape whose ``st_size`` bounds nothing, and interrupting the
#: block it causes is what lets a missing guard fail rather than stall the suite
#: (``hang_guard``). Both halves are POSIX, so they are one skip condition.
_CAN_MAKE_A_BLOCKING_FILE = hasattr(os, "mkfifo") and CAN_INTERRUPT_A_HANG

#: Spellings that tell a reader the cure for a refused read is a permission
#: change (#227). The contract is that the remedy *names* the permission, not
#: that it uses one verb: the shipped remedy for an unreadable ``evidence.json``
#: says "Make ... readable" while :func:`_within`'s sibling says ``chmod``, and
#: both discharge it. What none of these matches is the answer that would be
#: wrong here -- "draft it again", or "list .theurian/proposals/".
_PERMISSION_REMEDY_WORDS = ("chmod", "readable", "permission")

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

ANCHOR = SourceAnchor(
    provider="git",
    source_uri="https://github.com/acme/api/commit/0123456789abcdef",
    commit_sha="0123456789abcdef",
)

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-7"),
    model="claude-opus-5",
    reasoning="The review thread on #41 settled the retry budget at three attempts.",
    anchors=(ANCHOR,),
)

BODY = "# Retry policy\n\nThree attempts, then fail loudly.\n"


def _request(**overrides: object) -> ProposalRequest:
    fields: dict[str, object] = {
        "item_id": ItemId("architecture.retry-policy"),
        "title": "Retry policy",
        "kind": KnowledgeKind.ARCHITECTURE,
        "owner": "platform-team",
        "author": "platform-team@example.com",
        "description": "Record the retry budget the API review settled on.",
        "body": BODY,
        "content_type": MARKDOWN,
        "evidence": EVIDENCE,
        "source_anchors": (ANCHOR,),
    }
    return ProposalRequest(**{**fields, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def paths(tmp_path: Path) -> Iterator[ProjectPaths]:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    yield project


@pytest.fixture
def service(paths: ProjectPaths) -> ProposalService:
    # All three lookups read the project's *approved* migration set, freshly
    # loaded on every call the same way the CLI's `resolve_context` loads it --
    # so a second draft sees the first proposal's accepted item (the #210 update
    # guard), `accept` reads the same set `migrate validate`/`apply` do when it
    # asks whether a recorded migration id is in place (#253), and the pin guard
    # reads it when it asks which bodies are already pinned (#234).
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
        project_id=ProjectId("demo"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=_validator,
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        # The real rehearsal, not a double: the pre-check's whole point is that
        # it reaches the pipeline `migrate apply` reaches (ADR-0027 decision 2),
        # and a stub here would assert the opposite of what it claims.
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _validator(document: Mapping[str, object]) -> None:
    validate_migration_document(document, SCHEMAS)


def _document(path: Path) -> Mapping[str, object]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _upsert(path: Path) -> Mapping[str, object]:
    operations = _document(path)["operations"]
    assert isinstance(operations, list)
    return next(op for op in operations if op["op"] == "upsertRevision")


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _hand_authored_two_body_migration(
    migration_id: str, revisions: tuple[tuple[str, str, str], ...]
) -> str:
    """A migration naming two bodies whose leaf names collide (both `notes.md`).

    The two ``contentFile`` paths differ only in namespace -- ``alpha/notes.md``
    and ``beta/notes.md`` -- so they share the leaf ``notes.md``. That is the
    exact shape a leaf-name lookup conflated: it found one file for both. The
    revisions still differ, so the two are genuinely two changes, not one
    written twice.

    Each entry is ``(namespace, revision id, body text)``, and the body's digest
    is computed here rather than passed in: since ADR-0027 ``accept`` puts the
    document through the published schema and then replays it, so a migration
    that pins nothing -- which is what this helper used to write, because
    ``accept`` validated nothing -- is refused before the layout question this
    fixture exists to ask is reached.

    The ``id`` is quoted because the seeded generator's ULIDs are all digits,
    which YAML would otherwise coerce to an int (a real ULID contains letters
    and needs no quoting).
    """
    operations = "".join(
        "- op: upsertRevision\n"
        f"  itemId: {namespace}.notes\n"
        f"  revisionId: {revision_id}\n"
        f"  contentFile: ../knowledge/{namespace}/notes.md\n"
        f"  contentSha256: {ContentHash.of_bytes(body.encode()).value}\n"
        "  metadata:\n"
        f"    title: {namespace} notes\n"
        "    contentType: text/markdown\n"
        "    kind: architecture\n"
        f"    namespace: {namespace}\n"
        "    status: approved\n"
        "    owner: platform-team\n"
        # INV-8: a revision states where it came from or declares that it came
        # from nowhere. The replay enforces it, which is the point -- this used
        # to be checked only by `migrate apply`, after the pull request merged.
        f"    labels: ['{AUTHORED_IN_THEURIAN}']\n"
        for namespace, revision_id, body in revisions
    )
    return (
        "apiVersion: theurian.dev/v1\n"
        f"id: '{migration_id}'\n"
        "createdAt: '2026-08-02T12:00:00+00:00'\n"
        "author: a@example.com\n"
        "operations:\n"
    ) + operations


def _createitem_migration(migration_id: str, item_id: str) -> str:
    """The smallest schema-valid, applyable migration: one ``createItem``.

    Body-free, so a test that needs a *landed* migration to exist -- rather than
    one that says anything in particular -- can plant one without also planting
    a body for the accept-path replay to read.
    """
    return (
        "apiVersion: theurian.dev/v1\n"
        f"id: '{migration_id}'\n"
        "createdAt: '2026-08-02T12:00:00+00:00'\n"
        "author: a@example.com\n"
        "operations:\n"
        "- op: createItem\n"
        f"  itemId: {item_id}\n"
        "  kind: architecture\n"
        f"  namespace: {item_id.rpartition('.')[0]}\n"
        "  owner: platform-team\n"
    )


def _tree_bytes(root: Path) -> str:
    """Every regular file's text under ``root``, concatenated.

    A leak check reads the *content* of the tree, not its shape: a symlinked
    read that exfiltrates a secret lands the secret's bytes in a real file, and
    that is what must be absent -- searching filenames would miss it.
    """
    out: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                out.append(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError):
                continue
    return "\n".join(out)


#: The two directories a proposal may live in, as ``(local, project-relative
#: parent)`` (ADR-0028). ``--local`` picks the parent and nothing else, so every
#: property below that is about *where* a proposal is written or read from has to
#: be asked of both -- a second location checked by a second test is a second
#: implementation waiting to happen, and SEC-7 is held by one or by none.
_LOCATIONS: Final = (
    pytest.param(False, ".theurian/proposals/", id="tracked"),
    pytest.param(True, ".theurian/proposals-local/", id="local"),
)


# -- generation ------------------------------------------------------------


@pytest.mark.parametrize(("local", "parent"), _LOCATIONS)
def test_generation_writes_only_under_the_proposal_directory(
    service: ProposalService, paths: ProjectPaths, local: bool, parent: str
) -> None:
    """ADR-0013's first owed compliance item, checked over the whole tree.

    Scoped to files rather than to the three names the generator writes: the
    property ADR-0013 states is about everything a write-intent path may touch,
    and a version that wrote the body straight into ``.theurian/knowledge/``
    would satisfy any assertion phrased over the proposal directory alone.

    Parametrized over both locations for ADR-0028's first owed item. A tracked
    draft writes only under its proposal directory. A ``--local`` draft writes
    *one* file outside it -- the managed ``.gitignore``, brought current because
    that ignore rule is what makes the local directory confidential (ADR-0028,
    HIGH-2). That single named exception is allowed here and nothing else is: a
    body written into ``.theurian/knowledge/`` is still caught. The directory is
    asserted whole (``<parent>/<proposal-id>``) rather than by prefix, because
    ``.theurian/proposals-local/`` and ``.theurian/proposals/`` are each other's
    near-misses: a draft that ignored the flag would still be "under a proposal
    directory" by any looser phrasing.
    """
    before = _tree(paths.root)

    drafted = service.draft(_request(), local=local)

    written = _tree(paths.root) - before
    directory = drafted.directory.relative_to(paths.root).as_posix()
    allowed_outside = {".gitignore"} if local else set[str]()

    assert written, "the draft wrote nothing at all"
    assert all(path.startswith(f"{directory}/") or path in allowed_outside for path in written), (
        written
    )
    assert directory == f"{parent}{drafted.proposal_id.value}"


@pytest.mark.parametrize(("local", "parent"), _LOCATIONS)
def test_generation_modifies_no_file_outside_the_proposal_directory(
    service: ProposalService, paths: ProjectPaths, local: bool, parent: str
) -> None:
    """A new-file diff cannot see a *modified* existing file (adversarial b1).

    ``_tree`` returns the set of paths, so a draft that overwrote a file already
    present -- a knowledge body, another proposal's migration -- would leave the
    set unchanged and pass the test above. This snapshots content, so a
    modification outside the proposal directory is caught even when no path is
    added or removed.

    The other half of ADR-0028's first owed item, and the reason it names a
    content snapshot as well as a tree diff. What it covers is every file that
    already exists outside the parent the flag chose -- including the sibling
    proposal directory's own contents, which is the one place a location switch
    could reach that the sibling test would still call "under a proposal
    directory". It says nothing about *new* files written elsewhere; that is the
    tree diff's half, and neither is sufficient alone.
    """
    seeded = paths.knowledge / "architecture" / "retry-policy.md"
    seeded.parent.mkdir(parents=True, exist_ok=True)
    seeded.write_text("pre-existing, must be untouched\n", encoding="utf-8")
    outside = {
        path: path.read_bytes()
        for path in paths.root.rglob("*")
        if path.is_file() and parent not in path.relative_to(paths.root).as_posix()
    }

    service.draft(_request(), local=local)

    assert {p: p.read_bytes() for p in outside} == outside, "a file outside the proposal changed"


def test_a_generated_revision_is_status_approved_and_carries_no_trust_level(
    service: ProposalService,
) -> None:
    """m07/b24: ``status: approved`` is fixed, and ``trustLevel`` is never invented.

    ``status: approved`` is right even though nobody has approved it: the file
    applies only after a human has merged it, and ``draft`` would keep the
    knowledge out of the default index. ``trustLevel: reviewed`` on an agent's
    draft, by contrast, would claim a review that has not happened -- so the
    generator writes ``trustLevel`` only when the caller states one (#249's
    ``--trust-level``); ``_request`` here states none, so it stays out of the
    file and the loader's ``unverified`` default applies.
    """
    metadata = _upsert(service.draft(_request()).migration_file)["metadata"]
    assert isinstance(metadata, dict)

    assert metadata["status"] == "approved"
    assert "trustLevel" not in metadata


def test_a_generated_migration_validates_against_the_published_schema(
    service: ProposalService,
) -> None:
    """ADR-0013 point 3: the gap is human review, not format conversion."""
    drafted = service.draft(_request())

    validate_migration_document(_document(drafted.migration_file), SCHEMAS)


def test_a_migration_that_would_not_validate_is_never_written(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The check above passes whether or not the generator performs one.

    Measured: deleting the service's own ``self._validate(document)`` call left
    ``test_a_generated_migration_validates_against_the_published_schema`` green,
    because it validates the file that was written rather than asking whether
    anything refused to write it. This is the test that goes RED for that
    deletion, and it needs an input the *schema* rejects while every check
    before it passes -- a title past ``revisionMetadata.title``'s 300, which is
    caller-supplied text and not a shape the generator controls.

    Writing it anyway would be the worse failure of the two: the proposal reads
    as reviewable, and the schema error arrives once a human has already read
    it and moved it into place.
    """
    before = _tree(paths.root)

    with pytest.raises(MigrationError, match="title"):
        service.draft(_request(title="x" * 400))

    assert _tree(paths.root) == before


def test_the_generated_migration_file_is_named_for_its_own_id(
    service: ProposalService,
) -> None:
    """Two proposals for the same item produce two files, not one overwrite.

    Neither is accepted, so the item does not yet exist in approved state and
    both are first-revision proposals -- two competing drafts to create the same
    item, only one of which a human will merge.
    """
    first = service.draft(_request())
    second = service.draft(_request())

    assert first.migration_file.name.startswith(first.migration_id.value)
    assert first.migration_file.name.endswith("-retry-policy.yaml")
    assert first.migration_file.name != second.migration_file.name


def test_a_generated_migration_always_pins_the_body_digest(
    service: ProposalService,
) -> None:
    """#210's acceptance item. Optional to the schema; never omitted here.

    The generator has the body in hand, so computing the digest costs nothing
    and it is what makes an out-of-band edit to the body detectable.
    """
    drafted = service.draft(_request())

    assert _upsert(drafted.migration_file)["contentSha256"] == drafted.content_sha256.value


def test_a_generated_migration_pins_the_expected_revision_on_an_update(
    service: ProposalService,
) -> None:
    """The other half of #210: an update states which revision it replaces.

    The item has to exist first for an update to be legal, so the first proposal
    is accepted before the second is drafted against its revision.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)

    drafted = service.draft(_request(expected_revision=first.revision_id))

    assert _upsert(drafted.migration_file)["expectedRevision"] == first.revision_id.value


def test_a_new_item_carries_no_expected_revision(service: ProposalService) -> None:
    """Absent means "this creates the first revision"; a value would conflict."""
    drafted = service.draft(_request())

    assert "expectedRevision" not in _upsert(drafted.migration_file)


def test_an_update_with_no_expected_revision_is_refused_at_generation(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-5 (#210): the generator must not emit an unguarded update.

    Reproduced before the fix: a second proposal for an existing item with no
    ``--expected-revision`` wrote an update with no guard, passed
    ``migrate validate``, and failed only at ``migrate apply`` -- after the pull
    request had merged -- with *"expected <none>, store holds ..."*. The
    generator derives the current revision from the approved migration set and
    refuses the draft instead.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    before = _tree(paths.root)

    with pytest.raises(ProposalError, match="already exists"):
        service.draft(_request(body="# Retry policy\n\nFive attempts.\n"))

    assert _tree(paths.root) == before, "a refused update writes nothing"


def test_a_stale_expected_revision_is_refused_at_generation(service: ProposalService) -> None:
    """A guard that names the wrong revision conflicts at apply just as surely."""
    first = service.draft(_request())
    service.accept(first.proposal_id)

    with pytest.raises(ProposalError, match="expected-revision names"):
        service.draft(_request(expected_revision=RevisionId("01K9D2G8YT6PXN0VKS4WBZ7RQM")))


def test_expected_revision_on_a_new_item_is_refused_at_generation(
    service: ProposalService,
) -> None:
    """A first revision has nothing to replace, so the guard is a mistake."""
    with pytest.raises(ProposalError, match="does not exist yet"):
        service.draft(_request(expected_revision=RevisionId("01K9D2G8YT6PXN0VKS4WBZ7RQM")))


def test_the_update_guard_checks_against_the_latest_of_several_revisions(
    service: ProposalService,
) -> None:
    """HIGH-5's foundation: ``current_revision_in`` is the *last* upsert (FR-K4).

    The draft guard derives an item's current revision from the approved
    migration set, where the last upsert for the item is the current one -- the
    same rule ``migrate apply`` follows to set ``current_revision_id``. With only
    one approved revision, a first-vs-last mix-up is invisible; it shows only
    once an item has two. So two revisions are approved here and a third drafted:
    the guard must accept ``--expected-revision`` naming the *second* (the
    current) and refuse it naming the *first* (stale). A first-upsert regression
    flips both assertions.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    second = service.draft(_request(expected_revision=first.revision_id, body="# Two.\n"))
    service.accept(second.proposal_id)

    # Naming the first (now stale) revision must be refused.
    with pytest.raises(ProposalError, match="expected-revision names"):
        service.draft(_request(expected_revision=first.revision_id, body="# Three.\n"))

    # Naming the second (the current) revision must be accepted.
    third = service.draft(_request(expected_revision=second.revision_id, body="# Three.\n"))
    assert third.expected_revision == second.revision_id
    assert _upsert(third.migration_file)["expectedRevision"] == second.revision_id.value


def test_a_new_item_is_created_before_its_first_revision(service: ProposalService) -> None:
    document = _document(service.draft(_request()).migration_file)
    operations = document["operations"]
    assert isinstance(operations, list)

    assert [op["op"] for op in operations] == ["createItem", "upsertRevision"]


def test_the_evidence_file_records_the_origin_a_reviewer_reads(
    service: ProposalService,
) -> None:
    """Never read by Core, which is why nothing else asserts on its contents."""
    drafted = service.draft(_request())

    evidence = json.loads(drafted.evidence_file.read_text(encoding="utf-8"))

    assert evidence["agentId"] == "claude-code"
    assert evidence["taskId"] == "task-7"
    assert evidence["model"] == "claude-opus-5"
    assert evidence["reasoning"].startswith("The review thread")
    assert evidence["sourceAnchors"][0]["commitSha"] == "0123456789abcdef"
    assert evidence["proposalId"] == drafted.proposal_id.value


def test_a_proposal_with_no_evidence_is_rejected_at_generation(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """ADR-0013 point 5, and the third owed compliance item.

    The evidence is emptied *after* construction on purpose. ``Evidence``
    refuses to exist without reasoning, so a test that called its constructor
    would prove that one class holds the rule and say nothing about whether the
    generation path does. Bypassing it is the only way to ask the question this
    compliance item actually asks -- and the answer has to be the same, because
    ADR-0013's promise is about what gets written, not about which constructor a
    caller reached first.

    The reasoning is what is emptied, because that is what ADR-0013 point 5
    requires: a source anchor is INV-8's separate concern, and knowledge that
    originates in Theurian has none.

    Asserted over the whole tree as well as the exception: a refusal that has
    already created the directory leaves a half-written proposal a reviewer may
    find and read as though somebody meant it.
    """
    hollow = Evidence(
        agent_id=AgentId("claude-code"),
        task_id=TaskId("task-7"),
        model="claude-opus-5",
        reasoning="The review thread on #41 settled the retry budget.",
        anchors=(ANCHOR,),
    )
    object.__setattr__(hollow, "reasoning", "   ")
    before = _tree(paths.root)

    with pytest.raises(InvariantViolationError, match="no evidence"):
        service.draft(_request(evidence=hollow))

    assert _tree(paths.root) == before
    assert not [path for path in paths.proposals.iterdir() if path.is_dir()]


def test_a_revision_with_no_anchor_and_no_label_is_refused_at_generation(
    service: ProposalService,
) -> None:
    """INV-8 is what ``migrate apply`` enforces, and validation cannot see it.

    Measured on the shipped sample project (#36): a revision with no anchor
    validates and then exits 4 with "has no source anchor". Refusing here means
    the failure arrives before a human reviews the proposal rather than after
    the pull request has merged.
    """
    with pytest.raises(ProposalError, match="source anchor"):
        service.draft(_request(source_anchors=()))


def test_knowledge_that_originates_here_may_declare_it_instead_of_anchoring(
    service: ProposalService,
) -> None:
    drafted = service.draft(_request(source_anchors=(), labels=(AUTHORED_IN_THEURIAN,)))

    metadata = _upsert(drafted.migration_file)["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["labels"] == [AUTHORED_IN_THEURIAN]


def test_a_body_that_is_not_the_declared_format_is_refused(service: ProposalService) -> None:
    with pytest.raises(ProposalError, match="empty"):
        service.draft(_request(body="   \n"))


# -- acceptance ------------------------------------------------------------


def test_accept_moves_the_migration_under_the_name_it_already_had(
    service: ProposalService, paths: ProjectPaths
) -> None:
    drafted = service.draft(_request())

    accepted = service.accept(drafted.proposal_id)

    assert accepted.migration.destination == paths.migrations / drafted.migration_file.name
    assert accepted.migration.destination.is_file()
    assert not drafted.migration_file.exists()


def test_the_content_file_resolves_from_the_migrations_directory_after_acceptance(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The path is written for where the file will be, not where it sits now.

    ``contentFile`` is resolved relative to the migration file, and after
    acceptance that file is in ``.theurian/migrations/``. A proposal-relative
    path parses and then fails to resolve after the move, which is #205.
    """
    drafted = service.draft(_request())
    service.accept(drafted.proposal_id)

    loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)

    assert len(loaded.migration_set) == 1
    assert drafted.body_destination.read_text() == BODY
    assert drafted.body_destination.parent == paths.knowledge / "architecture"


def test_two_accepted_proposals_for_one_item_leave_a_set_that_still_loads(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The defect a run found and no test did, pinned where it was introduced.

    Generating both proposals into ``architecture/retry-policy.md`` made the
    second acceptance replace the body the *first* migration had pinned. The
    loader re-reads every ``contentFile`` on every load, so the whole set
    stopped loading -- ``theurian migrate validate`` exited 4 with *"hashes to
    abc7cdb70713 but the migration pins 4f9c5503e198"*, and no migration in the
    project could be applied afterwards.

    Loading the set is the assertion, not the file names: a future change that
    keeps the names distinct by some other means still passes, and any change
    that reintroduces a shared body path fails here whatever it calls it.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    second = service.draft(_request(expected_revision=first.revision_id, body="# Five.\n"))
    service.accept(second.proposal_id)

    loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)

    assert len(loaded.migration_set) == 2
    assert first.body_destination.read_text() == BODY, "the first body is still what it pinned"


def test_accept_refuses_to_land_a_migration_on_an_existing_name(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The name carries the id, so a collision means that migration is in place.

    The refusal is what #89 measured the absence of: the second acceptance
    replaced the first migration and reported nothing, and the change it
    replaced was gone from the set with its body file orphaned.
    """
    drafted = service.draft(_request())
    landed = paths.migrations / drafted.migration_file.name
    landed.write_text("apiVersion: theurian.dev/v1\n", encoding="utf-8")

    with pytest.raises(ProposalError, match="already"):
        service.accept(drafted.proposal_id)

    assert landed.read_text() == "apiVersion: theurian.dev/v1\n"
    # The body too, and it is the half a weaker check misses: the collision is
    # also caught by the `O_EXCL` move itself, but by then the body has already
    # been replaced. Asserting only on the migration leaves that reachable.
    assert drafted.migration_file.is_file(), "a refused acceptance moves nothing"
    assert drafted.body_file.is_file()
    assert not drafted.body_destination.exists()


def test_accept_refuses_a_migration_id_the_loaded_set_holds_under_another_name(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """Round-one HIGH (CLASS-D): the "already in place" refusal keyed on the filename.

    ``_refuse_if_migration_present`` checked only ``destination.exists()``, and the
    destination name is ``<id>-<slug>.yaml``. The loader keys migrations by their
    *inner* ``id`` (``MigrationSet._by_id``), so a hand-authored proposal named
    ``<landed-id>-other-slug.yaml`` carrying ``id: <landed-id>`` collided on the
    inner id while its filename was free: the name check waved it through,
    ``_require_filename_matches_id`` was satisfied (prefix equals id), and
    ``accept`` landed a *duplicate* migration id. ``migrate
    validate``/``status``/``apply`` then all exit 4 on "duplicate migration id"
    (reproduced end to end, ``p15_duplicate_migration_id``).

    This is the third accept-path detector to be moved off a filesystem heuristic
    and onto the loaded set (#234/#253/#254 did the sibling two). The fix keeps
    the filename check for the on-disk-name-collision case and adds an id check
    against the same ``MigrationSet`` the loader reads.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # A genuinely different change, then its migration id rewritten to the landed
    # one and its file renamed to a different slug -- the committed shape that
    # collides on the inner id while its destination name stays free.
    second = service.draft(
        _request(item_id=ItemId("architecture.other"), body="# Other\n\nFive.\n")
    )
    text = second.migration_file.read_text(encoding="utf-8").replace(
        second.migration_id.value, first.migration_id.value
    )
    renamed = second.directory / f"{first.migration_id.value}-other-slug.yaml"
    renamed.write_text(text, encoding="utf-8")
    second.migration_file.unlink()

    with pytest.raises(ChangeAlreadyInPlaceError, match="duplicate migration id"):
        service.accept(second.proposal_id)

    # Nothing landed: the migrations directory still holds only the first, and the
    # loaded set is still the single valid migration `migrate validate` would read.
    landed_names = sorted(p.name for p in paths.migrations.glob("*.yaml"))
    assert landed_names == [first.migration_file.name], landed_names
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_moves_the_body_out_of_the_proposal_directory(
    service: ProposalService,
) -> None:
    """m10: the body is moved, not copied -- the source is gone after a move.

    A copy would leave the body in the proposal directory *and* under
    ``knowledge/``, and a later index build would ingest a stray copy no
    migration references. Asserting the source is gone is what a
    ``copy``-instead-of-``move`` mutation fails.
    """
    drafted = service.draft(_request())

    service.accept(drafted.proposal_id)

    assert not drafted.body_file.exists()
    assert not drafted.migration_file.exists()
    assert drafted.body_destination.is_file()


def test_accept_uses_o_excl_when_the_name_appears_after_the_precheck(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """m05: the O_EXCL create, not the name check, is the real race guard.

    ``test_accept_refuses_to_land_a_migration_on_an_existing_name`` is caught by
    the name check that runs before any write. That leaves the O_EXCL create --
    the guard for a name that appears in the window *after* that check --
    unexercised, so a mutation to ``O_TRUNC`` survives it. Here the name check is
    neutered so the create is what has to refuse, and the bytes already at the
    name must survive.

    The file planted at the name is a *valid* migration rather than the
    ``EXISTING\\n`` this used to write. Since ADR-0027 ``accept`` loads and
    replays the approved set before it moves anything, and a project whose
    ``.theurian/migrations/`` holds a file that is not a migration is refused by
    that pre-check long before the write this test is aiming at.
    """
    drafted = service.draft(_request())
    landed = paths.migrations / drafted.migration_file.name
    planted = _createitem_migration("00000000000000000000000099", "planted.item")

    def _no_precheck(
        _self: ProposalService, _destination: Path, _document: Mapping[str, object]
    ) -> None:
        return None

    monkeypatch.setattr(ProposalService, "_refuse_if_migration_present", _no_precheck)
    landed.write_text(planted, encoding="utf-8")

    with pytest.raises(ProposalError, match="appeared"):
        service.accept(drafted.proposal_id)

    assert landed.read_text() == planted, "O_EXCL must not overwrite the existing migration"
    assert not drafted.body_destination.exists(), "the body write is rolled back"


def test_the_write_primitive_refuses_to_follow_a_destination_symlink(tmp_path: Path) -> None:
    """m09: ``_write_file`` never follows a symlink at its destination (O_NOFOLLOW).

    A steady-state symlink at a body's ``contentFile`` is already handled before
    the write -- ``_destination_of`` resolves it, so it either escapes
    ``knowledge/`` (refused) or resolves to a name the proposal never authored
    (refused). ``O_NOFOLLOW`` is the defence for the race that check cannot cover:
    a symlink planted at the final destination between the resolve and the write.
    Pinned on the primitive, where it is deterministic: without ``O_NOFOLLOW`` the
    write follows the link and clobbers its target.
    """
    from theurian.application.proposal_service import _write_file

    target = tmp_path / "keep.md"
    target.write_text("do not clobber me\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(target)

    with pytest.raises(OSError):  # ELOOP from O_NOFOLLOW
        _write_file(link, b"clobber", exclusive=False)

    assert target.read_text() == "do not clobber me\n"


def test_accept_refuses_a_filename_that_does_not_match_the_inner_id(
    service: ProposalService,
) -> None:
    """A file named for one ULID carrying another is refused.

    The loader keys migrations by the inner ``id`` while the accept-time "already
    in place" check sees the filename, so a mismatch slips a real id collision
    past that check and fails downstream as a duplicate id. The two must agree.
    """
    drafted = service.draft(_request())
    document = drafted.migration_file.read_text(encoding="utf-8")
    # Replace the id's value in place, leaving whatever quoting the serialiser
    # chose -- the value is the first place it appears, before the revision id.
    drafted.migration_file.write_text(
        document.replace(drafted.migration_id.value, "01K1AAAAAA01234567890ABCDE", 1),
        encoding="utf-8",
    )

    with pytest.raises(ProposalError, match="filename ULID must equal"):
        service.accept(drafted.proposal_id)


def test_a_filename_check_refuses_an_alias_bomb_id_without_rendering_it() -> None:
    """HIGH-1 Face B (adversarial e19): ``id: *anchor`` is a T-6 denial of service.

    ``_require_filename_matches_id`` runs *before* stage-1 schema validation, so
    ``document["id"]`` is still raw YAML. When it aliases a DAG, the value is a
    container whose ``{inner!r}`` re-expands the graph PyYAML collapsed -- 551
    bytes rendered to 3.4 GB in the reproduction. The guard refuses a non-scalar
    id before rendering it; the short, key-only message is what pins that, because
    the vulnerable form rendered the whole expansion into the refusal.
    """
    lines = ["a0: &a0 'x'"]
    for level in range(1, 7):
        refs = ", ".join(f"*a{level - 1}" for _ in range(6))
        lines.append(f"a{level}: &a{level} [{refs}]")
    lines.append("id: *a6")
    document = load_yaml_mapping("\n".join(lines))
    migration_file = Path("01K1AAAAAA01234567890ABCDE-x.yaml")

    with pytest.raises(ProposalError) as caught:
        _require_filename_matches_id(migration_file, document)

    assert len(str(caught.value)) < 1000, "the refusal rendered the expanded alias graph"
    assert "filename ULID must equal" in str(caught.value)


def test_a_filename_check_still_names_a_short_wrong_id(tmp_path: Path) -> None:
    """A short, wrong id is still echoed -- the guard only refuses to render a bomb.

    The helpful diagnosis (``its id is '...'``) is preserved for the ordinary
    mismatch: a scalar id that is simply not the filename's ULID.
    """
    migration_file = Path("01K1AAAAAA01234567890ABCDE-x.yaml")

    with pytest.raises(ProposalError) as caught:
        _require_filename_matches_id(migration_file, {"id": "01K1BBBBBB01234567890ABCDE"})

    assert "01K1BBBBBB01234567890ABCDE" in str(caught.value)


def test_a_dangling_symlink_in_one_location_still_forces_the_ambiguity_refusal(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """adversarial M-4: presence is ``lexists``, so a broken link is "in the way".

    A proposal exists in ``.theurian/proposals/`` and a *dangling* symlink of the
    same id sits in ``.theurian/proposals-local/``. Both are present -- the link
    is something in the way of this id even though it resolves to nothing -- so
    the accept is refused naming both, never resolved by precedence to the real
    one (ADR-0028). ``exists()`` following the link would drop it and let the
    tracked proposal win silently, the choice the ambiguity refusal exists to
    prevent; ``exists(follow_symlinks=False)`` is what keeps it real.
    """
    drafted = service.draft(_request())
    link = paths.proposals_local / drafted.proposal_id.value
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(paths.proposals_local / "target-that-does-not-exist")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "two places at once" in str(caught.value), str(caught.value)
    assert "proposals-local" in str(caught.value) and "proposals/" in str(caught.value)


def test_a_symlinked_migration_file_is_named_as_such_not_read_through(
    service: ProposalService,
) -> None:
    """adversarial M-5: the specific refusal, not a generic downstream one.

    ``_require_migration`` rejects a name-matching migration file that is a
    *symlink* by name, before anything reads through it -- so the author is told a
    link is in the way rather than sent to draft again over a "missing" migration.
    Dropping that check (``symlinked = []``) lets the link be read as the
    migration; containment then holds through ``_reject_symlink_in_chain``, but
    the specific diagnosis is lost. This pins the message; the containment is
    covered separately.
    """
    drafted = service.draft(_request())
    migration = drafted.migration_file
    # Move the real file aside under a non-migration name, then leave a symlink of
    # the migration's own name pointing at it: name-matched, but a link.
    real = migration.with_name(migration.name + ".real")
    migration.rename(real)
    migration.symlink_to(real)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "symlinked migration file" in str(caught.value), str(caught.value)


def test_accept_replaces_an_unpinned_file_at_the_destination(
    service: ProposalService,
) -> None:
    """The narrow permissive case: a body may replace a file nothing pins.

    A generated proposal's body path carries a fresh revision id, so it never
    lands on an existing file. What can is a stray file already sitting at the
    destination that no migration references -- replacing it is safe, because no
    pin depends on its bytes -- and ``replaced`` records that it happened.
    """
    drafted = service.draft(_request())
    drafted.body_destination.parent.mkdir(parents=True, exist_ok=True)
    drafted.body_destination.write_text("stale, pinned by nothing\n", encoding="utf-8")

    accepted = service.accept(drafted.proposal_id)

    assert accepted.bodies[0].replaced
    assert drafted.body_destination.read_text() == BODY


def test_accept_refuses_a_replacement_that_would_break_an_existing_pin(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-1: a body replacement that invalidates an applied migration's pin.

    Reproduced before the fix (adversarial e19): accept proposal 1 -- validate
    and apply green -- then accept a second proposal whose hand-authored
    ``contentFile`` reuses proposal 1's body path. The overwrite made proposal
    1's pinned ``contentSha256`` wrong, and ``migrate validate`` then exited 4
    for the whole project with no undo command.

    ``accept`` must never leave the set unable to validate, so it refuses the
    replacement -- and refusing breaks nothing legitimate, because a generated
    update never reuses a ``contentFile`` (its revision id makes the path fresh).

    The second proposal is drafted for a *different* item so the draft-side
    guard does not fire, then its ``contentFile`` is hand-repointed at the first
    item's body path -- which is how a committed, contributor-authored proposal
    directory reaches this check.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    second = service.draft(
        _request(item_id=ItemId("architecture.other"), body="# Retry policy\n\nFive attempts.\n")
    )
    second.migration_file.write_text(
        second.migration_file.read_text(encoding="utf-8").replace(
            second.content_file, first.content_file
        ),
        encoding="utf-8",
    )
    tail = first.body_destination.relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(second.body_file.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    # The first body is untouched and the whole set still loads.
    assert first.body_destination.read_text() == BODY
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_the_pin_guard_sees_a_pin_held_by_a_symlinked_landed_migration(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """#234: the replacement guard once skipped a symlink the loader follows.

    The same class ``_landed_state`` closed for the accepted-detector (#253), on
    the guard above. That guard once re-enumerated
    ``.theurian/migrations/*.yaml`` from the filesystem and ``continue``d on
    ``migration.is_symlink()``, while ``load_migrations`` follows a symlinked
    entry that points at a real in-project migration and loads its pins -- so the
    two readers disagree about which bodies are pinned, and the disagreement is
    the guard's blind spot rather than a cosmetic one.

    Confirmed end to end before the fix: with the landed migration moved to
    ``<root>/migration-store/`` and a relative symlink left in its place, the
    loader still loaded the set (count 1) and ``accept`` did **not** refuse the
    replacement -- and the set then failed to load at all, with
    *"... hashes to abc7cdb70713 but the migration pins 539a4030033a"*, the exit-4
    shape ``_refuse_if_a_replacement_breaks_an_existing_pin`` exists to prevent.

    The symlinked layout is not exotic input: a project that keeps its migrations
    under version control elsewhere, or a contributor who relocates one, produces
    it, and the loader is what decides whether the set is real. The fix direction
    is recorded in :meth:`ProposalService._landed_state`'s docstring -- read the
    loaded migration set, so no filename shape can make the guard and the loader
    disagree.

    The second proposal is drafted for a *different* item and its ``contentFile``
    hand-repointed at the first item's body path, exactly as
    :func:`test_accept_refuses_a_replacement_that_would_break_an_existing_pin`
    does; only the shape of the landed migration differs.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    landed = next(paths.migrations.glob(f"{first.migration_id.value}-*.yaml"))
    store = paths.root / "migration-store"
    store.mkdir()
    landed.rename(store / landed.name)
    landed.symlink_to(Path("..") / ".." / store.name / landed.name)
    # Preconditions, so this cannot pass by never reaching the guard: the entry
    # is a symlink, and the loader reads the set through it.
    assert landed.is_symlink(), "the landed migration is now a symlink"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1

    second = service.draft(
        _request(item_id=ItemId("architecture.other"), body="# Retry policy\n\nFive attempts.\n")
    )
    second.migration_file.write_text(
        second.migration_file.read_text(encoding="utf-8").replace(
            second.content_file, first.content_file
        ),
        encoding="utf-8",
    )
    # `body_destination` is resolved on both sides: on macOS the project root
    # reaches this test through /var, whose real path is /private/var, and an
    # unresolved left-hand side is not relative to the resolved knowledge dir.
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(second.body_file.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    # The first body is untouched and the whole set still loads -- the property
    # the refusal exists for, checked rather than assumed.
    assert first.body_destination.read_text() == BODY
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def _fs_is_case_insensitive(directory: Path) -> bool:
    """Whether ``directory``'s filesystem reaches one inode by many spellings.

    The case-variant face exists only where the filesystem folds case (APFS,
    NTFS): on a case-sensitive volume the variant names a *different* file, so
    there is no shared inode to protect and nothing to reproduce. Probed against
    the real directory the test writes into rather than inferred from
    ``sys.platform`` -- a case-sensitive volume can be mounted on macOS and a
    case-insensitive one on Linux.
    """
    probe = directory / "TheurianCaseProbe"
    probe.write_text("x", encoding="utf-8")
    try:
        return (directory / "theuriancaseprobe").exists()
    finally:
        probe.unlink()


def test_accept_refuses_a_case_variant_of_a_landed_body(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-A: a ``contentFile`` differing only in case reaches a landed inode.

    The replacement guard compared resolved path *strings*, and ``Path.resolve()``
    folds ``.``/``..``/symlinks but never case or NFC/NFD (the loader records this
    on ``UpsertRevision.content_identity``, #210). So a hand-authored
    ``contentFile`` spelling a landed body's path with a different case reached
    the very same physical file while the guard, comparing strings, saw no pin --
    ``accept`` overwrote a pinned body and ``migrate validate`` then exited 4 for
    the whole project with no undo. Keying the guard on the destination's
    ``(st_dev, st_ino)`` collapses every spelling onto one inode, so the variant
    is refused. Reproduced end to end by the orchestrator (``probes/e1``).
    """
    if not _fs_is_case_insensitive(paths.knowledge):
        pytest.skip("a case variant names a different file on a case-sensitive filesystem")
    first = service.draft(_request())
    service.accept(first.proposal_id)
    second = service.draft(
        _request(item_id=ItemId("architecture.other"), body="# Retry policy\n\nFive attempts.\n")
    )
    variant = first.content_file.replace("/architecture/", "/Architecture/")
    assert variant != first.content_file, "the case variant must differ from the landed spelling"
    second.migration_file.write_text(
        second.migration_file.read_text(encoding="utf-8").replace(second.content_file, variant),
        encoding="utf-8",
    )
    # The hand-authored body sits at the variant tail; on a case-insensitive
    # volume it is the same file the first proposal landed. `.resolve()` on the
    # left for /var vs /private/var, as the symlink-pin sibling above documents.
    tail = (paths.migrations / variant).resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(second.body_file.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    assert first.body_destination.read_text() == BODY, "the landed body is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_refuses_a_byte_identical_replacement_of_a_pinned_body(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The revision conjunct decides: a byte-identical body under a *new* revision id.

    "Byte-identical changes nothing" is false of the *set*. Re-pointing a second
    revision's ``contentFile`` at a landed body -- on the **same item**, with the
    identical bytes, but under its own fresh revision id -- leaves two revisions
    naming one physical file (``refuse_duplicate_content_files``, exit 4) even
    though nothing about the bytes moved. The skip that admits the one legitimate
    in-place re-declare fires only when item id, revision id *and* bytes all match
    (proposal_service ``_refuse_if_a_replacement_breaks_an_existing_pin``); here
    item and bytes match, so the **revision** conjunct is the sole decider, and it
    refuses. Reproduced by the orchestrator (``probe_classa`` shape 2: same item,
    different revision, identical bytes -> REFUSED).

    This is the only same-item byte-identical face that is *not* the allowed
    in-place re-declare -- the sibling test
    ``test_accept_allows_the_same_revision_re_declared_against_its_own_body`` keeps
    the same revision id, and that one difference is what flips accept from allowed
    to refused: the revision id is left the second's own here, so the test pins the
    revision conjunct rather than the byte one. Before this
    branch's rewrite the second kept a *different item id* too, so the item
    conjunct short-circuited the skip and the revision conjunct was never
    evaluated -- a mutation neutering it survived the whole suite.

    The second proposal is drafted for a throwaway item so the draft-side #210
    guard does not fire, then its ``contentFile`` and *item id* are hand-repointed
    at the first's while its own revision id is deliberately left in place -- the
    committed, contributor-authored shape ADR-0013 point 7 admits.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # Same item, byte-identical body, but the second's own (fresh) revision id: a
    # byte-identical body landing as a *second* revision on the first's body file.
    second = service.draft(_request(item_id=ItemId("architecture.other"), body=BODY))
    text = second.migration_file.read_text(encoding="utf-8")
    text = text.replace(second.content_file, first.content_file)
    text = text.replace("architecture.other", "architecture.retry-policy")
    second.migration_file.write_text(text, encoding="utf-8")
    assert "architecture.other" not in text, "the item id re-declare failed"
    assert f"revisionId: '{first.revision_id.value}'" not in text, (
        "the revision id must stay the second's own, or the revision conjunct is not the decider"
    )
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(second.body_file.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    assert first.body_destination.read_text() == BODY, "the landed body is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_refuses_a_byte_different_redeclare_of_a_pinned_landed_revision(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The byte conjunct decides: the same revision, re-declared with *different* bytes.

    Round-two HIGH-1: an equal revision id is not a licence to change the bytes.
    The equal-id skip once fired on the revision id alone, on the premise that a
    landed revision re-declared under its own id must carry its own body. That is
    false of contributor-authored input (ADR-0013 point 7): a hand-authored
    proposal can reuse an *existing* landed revision id **on its own item**, point
    its ``contentFile`` at that revision's pinned body, and supply *different*
    bytes. The old guard waved it through, the overwrite made the pin wrong, and
    ``migrate validate`` exited 4 for the whole set with no undo -- reproduced end
    to end (``repro_high1_r2``: accept exit 0 -> set fails to load).

    A revision's content is immutable, so the only legitimate in-place re-declare
    (ADR-0024 decision 5) carries byte-identical content. Here the item id **and**
    the revision id are re-pointed to match the landed revision, so both of those
    conjuncts pass and byte-identity is the *sole* conjunct left to decide: the
    proposal carries different bytes, so the guard refuses. That isolation is the
    whole point of the rewrite -- the prior version left a *different item id*, so
    the item conjunct short-circuited the skip and a mutation making
    ``_reads_identical_bytes`` trivially true survived. The one legitimate face
    (identical item, revision and bytes) stays allowed by
    :func:`test_accept_allows_the_same_revision_re_declared_against_its_own_body`.
    The second proposal is drafted for a throwaway item so the draft-side guard
    does not fire, then its ``contentFile``, ``revisionId`` and *item id* are
    hand-repointed at the first's -- exactly the committed, contributor-authored
    shape that reaches this check.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    second = service.draft(
        _request(
            item_id=ItemId("architecture.other"),
            body="# Retry policy\n\nFIVE HUNDRED attempts.\n",
        )
    )
    text = second.migration_file.read_text(encoding="utf-8")
    text = text.replace(second.content_file, first.content_file)
    text = text.replace(
        f"revisionId: '{second.revision_id.value}'",
        f"revisionId: '{first.revision_id.value}'",
    )
    text = text.replace("architecture.other", "architecture.retry-policy")
    second.migration_file.write_text(text, encoding="utf-8")
    assert f"revisionId: '{first.revision_id.value}'" in text, "the revision id re-declare failed"
    assert "architecture.other" not in text, "the item id re-declare failed"
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(second.body_file.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    assert first.body_destination.read_text() == BODY, "the pinned body is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_allows_the_same_revision_re_declared_against_its_own_body(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The one landed reference the identity guard must *not* refuse.

    Re-declaring one revision against its own body **on the same item** is how an
    in-place status change is written -- the item and revision ids do not move,
    ``append_revision`` is a no-op, only ``status`` differs (the
    ``reject``/``inplace-draft`` faces, ADR-0024 decision 5). It lands on the same
    body path (the path carries the revision id), so it *is* a replacement -- but
    of a body its own revision already reads, not a second one. The guard keys on
    the *quadruple* (identity, item id, revision id, bytes) for exactly this: an
    equal item id, revision id and byte content are skipped, so the legitimate
    re-declare is allowed while every different-item, different-revision and
    different-byte face is refused. Without the skip the guard would refuse this
    too, breaking a supported flow; this pins that it does not.

    The re-declare is hand-authored because ``draft`` cannot produce it: a second
    proposal for the existing item would require ``--expected-revision`` and then
    mint a *fresh* revision id. So a proposal drafted for a throwaway item is
    hand-edited to re-declare the first item's own item id, revision id and body
    -- the committed shape ADR-0013 point 7 admits.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # A fresh migration id (so the "already in place" pre-check passes), then the
    # first item's own item id, revision id and byte-identical body re-declared --
    # what a hand-authored in-place status change looks like.
    second = service.draft(_request(item_id=ItemId("architecture.other")))
    text = second.migration_file.read_text(encoding="utf-8")
    text = text.replace(second.content_file, first.content_file)
    text = text.replace(
        f"revisionId: '{second.revision_id.value}'",
        f"revisionId: '{first.revision_id.value}'",
    )
    # The item id is re-declared alongside the revision id: this is the *same
    # item*, which is the whole of what makes the re-declare legitimate and the
    # cross-item test below a refusal.
    text = text.replace("architecture.other", "architecture.retry-policy")
    second.migration_file.write_text(text, encoding="utf-8")
    assert f"revisionId: '{first.revision_id.value}'" in text, "the revision id re-declare failed"
    assert "architecture.other" not in text, "the item id re-declare failed"
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(first.body_destination.read_bytes())

    accepted = service.accept(second.proposal_id)

    assert accepted.bodies[0].replaced, "the re-declare lands on the existing body"
    assert first.body_destination.read_text() == BODY, "the body is unchanged"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 2


def test_accept_refuses_a_cross_item_byte_identical_redeclare_of_a_landed_revision(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """Round-one HIGH: an equal revision id and equal bytes under a *different* item.

    The two-conjunct skip (equal revision id, equal bytes) let a byte-identical
    body re-declared under a *different* item's id through: id and bytes both
    match, so the skip fired and ``accept`` returned 0. ``migrate validate`` does
    not check cross-item revision ownership, so it too passed -- and ``migrate
    apply`` then refused the whole set at exit 4 ("a revision id belongs to one
    item", INV-1/SEC-13) after the pull request had merged, the proposal already
    consumed with no undo. Reproduced end to end by the orchestrator
    (``repro_propose_high1``: accept 0 -> validate 0 -> apply 4).

    The item conjunct moves that refusal to the accept door. This is the only
    difference from
    :func:`test_accept_allows_the_same_revision_re_declared_against_its_own_body`:
    there the item id is re-declared to match, here it is left pointing at a
    different item, and that alone flips accept from allowed to refused.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # A different item, but the first's revision id and byte-identical body: the
    # cross-item reuse the two-conjunct skip missed.
    second = service.draft(_request(item_id=ItemId("architecture.other"), body=BODY))
    text = second.migration_file.read_text(encoding="utf-8")
    text = text.replace(second.content_file, first.content_file)
    text = text.replace(
        f"revisionId: '{second.revision_id.value}'",
        f"revisionId: '{first.revision_id.value}'",
    )
    second.migration_file.write_text(text, encoding="utf-8")
    assert f"revisionId: '{first.revision_id.value}'" in text, "the revision id re-declare failed"
    assert "architecture.other" in text, "the item id must stay a different item"
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(first.body_destination.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    assert first.body_destination.read_text() == BODY, "the landed body is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_allows_a_replacement_over_a_body_no_landed_revision_reads(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The guard refuses the matched inode alone, not every replacement while a set exists.

    ``_operation_reads`` discriminates by the destination's ``(st_dev, st_ino)``:
    a landed revision reading body *A* does not make a replacement of a
    *different* body *B* a break. Nothing pinned that discrimination -- measured
    (adversarial round two, mutation ``opreads-always-true``): replacing the
    inode comparison with ``operation.content_identity is not None`` -- always
    true for a loaded operation -- made every ``replaced`` move match the first
    landed operation, over-refusing this legitimate replacement, and the whole
    proposal suite still passed (92 green under the mutation).

    One migration lands, reading body A. A second proposal, for a different item,
    replaces a stray file at its own fresh path (inode B) that no landed revision
    reads -- the permissive case ``_refuse_if_a_replacement_breaks_an_existing_pin``
    must allow. It only reaches the guard *because* the set is non-empty and the
    move is a replacement, which is exactly the state the always-true mutation
    turns into a false break; the earlier permissive test lands on an empty set,
    so ``_operation_reads`` is never called there and the mutation survives it.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)

    # A second item with distinct content, so B shares neither path nor bytes
    # with the landed body A.
    second = service.draft(
        _request(item_id=ItemId("architecture.other"), body="# Timeout policy\n\nThirty seconds.\n")
    )
    # A stray file at B: read by no landed revision, so replacing it is
    # legitimate. Without it the move is a create, not a replace, and the guard
    # loop is never entered.
    second.body_destination.parent.mkdir(parents=True, exist_ok=True)
    second.body_destination.write_text("stale, pinned by nothing\n", encoding="utf-8")
    # Preconditions, so a pass cannot come from never reaching the discrimination:
    # A and B are different inodes, and the landed set (reading A) is non-empty.
    assert first.body_destination.stat().st_ino != second.body_destination.stat().st_ino
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1

    accepted = service.accept(second.proposal_id)

    assert accepted.bodies[0].replaced, "the stray file at B was replaced"
    assert second.body_destination.read_text() == "# Timeout policy\n\nThirty seconds.\n"
    assert first.body_destination.read_text() == BODY, "the landed body A is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 2


def test_accept_refuses_a_content_file_hardlinked_to_a_landed_body(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The hardlink face of the identity guard (#234): one inode, two names.

    ``_operation_reads`` keys on ``(st_dev, st_ino)`` -- the identity the loader
    took from the same ``stat`` that read the body -- so it reaches a landed body
    by *every* name that resolves to its inode, where the old path-string compare
    saw a distinct file. A second proposal for a different item, at its own
    distinct path, whose body destination is a **hardlink** to a landed body has a
    different path *string* but the same inode; accepting it would leave two
    revisions naming one physical file (``refuse_duplicate_content_files``, exit
    4). The guard refuses it -- the inode match reaches the landed operation, and
    the differing item id then makes it a break -- where a path compare would have
    let it land. This is the CHANGELOG's ``hardlink`` face, previously untested
    (the case-variant face is covered by the loader's identity tests; a Unicode
    NFC/NFD variant reaches the same inode but stays honestly noted as untested).
    Reproduced by the orchestrator (``probe_hardlink``: same inode -> REFUSED).
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    landed_body = first.body_destination.resolve()

    # A second item at its own distinct path; its body destination is a hardlink
    # to the landed body, so the two paths share one inode.
    second = service.draft(_request(item_id=ItemId("architecture.other"), body=BODY))
    dest = second.body_destination
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(landed_body, dest)
    except OSError as exc:  # pragma: no cover - only on a filesystem without hardlinks
        pytest.skip(f"the filesystem does not support hardlinks here: {exc}")

    # Preconditions, so a pass cannot come from the two being the same file: a
    # different path string, but the same (st_dev, st_ino) a string compare misses.
    assert dest.resolve() != landed_body, "the hardlink must live at a different path"
    assert dest.stat().st_ino == landed_body.stat().st_ino, "the hardlink must share the inode"

    # Identical bytes in the proposal directory so the move is a replacement the
    # guard examines, not a create.
    tail = dest.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(BODY.encode("utf-8"))

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    assert first.body_destination.read_text() == BODY, "the landed body is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_leaves_the_evidence_where_a_reviewer_will_read_it(
    service: ProposalService,
) -> None:
    """Proposal directories are committed; ``evidence.json`` is review input."""
    drafted = service.draft(_request())

    service.accept(drafted.proposal_id)

    assert drafted.evidence_file.is_file()


def test_accept_reports_an_unknown_proposal_rather_than_raising_from_the_filesystem(
    service: ProposalService,
) -> None:
    with pytest.raises(ProposalError, match="No proposal"):
        service.accept(ProposalId("01K9C7VN4TQZB2M8XR5HD3JFEW"))


def test_the_unknown_proposal_refusal_sends_the_reader_to_both_locations(
    service: ProposalService,
) -> None:
    """A remedy is an instruction, and "list A or B" is one that misses (ADR-0028).

    Which of the two directories holds the proposal is exactly what is not known
    at this point, so a reader who lists one of them correctly has still not
    found it. "or" belongs in the *message*, which states where the id was not
    found; "and" belongs in the *remedy*, which says what to do next.

    Pinned because the message and the remedy drifted apart in the shipped code
    and no test noticed: the suite asserted on ``No proposal`` and never read the
    remedy, so the wrong instruction was found by running the CLI by hand. Both
    conjunctions are asserted, in the field each belongs to, because a fix that
    put "and" in both would make the message claim the id was absent from a
    combined location that does not exist.
    """
    with pytest.raises(ProposalError) as caught:
        service.accept(ProposalId("01K9C7VN4TQZB2M8XR5HD3JFEW"))

    assert ".theurian/proposals/ or .theurian/proposals-local/" in str(caught.value)
    assert ".theurian/proposals/ and .theurian/proposals-local/" in caught.value.remedy


def test_accept_refuses_a_proposal_id_that_exists_in_both_locations(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """ADR-0028 decision 2: the ambiguity is refused, never resolved by precedence.

    The two directories are independent trees that can hold different migrations
    under one proposal id -- a draft written with ``--local``, then copied into
    the committable location and edited, is enough. A lookup that graded one
    location and fell through to the other on failure would resolve that
    silently: ``accept`` would consume one directory while the author was
    reading the other, and the loser's body would be left behind with nothing
    said.

    So this asserts the refusal *and* that neither side landed, which is what
    separates it from a precedence assertion: a test that only checked which
    directory won would pass against exactly the behaviour the ADR forbids. The
    bodies are deliberately different, so a silent pick is a real change to the
    project rather than a harmless one.

    Both paths are named because the reader is being asked to compare two
    directories and delete one; a message naming a single path is an instruction
    to delete something without seeing what it is being compared against.
    """
    drafted = service.draft(_request())
    twin = paths.proposals_local / drafted.proposal_id.value
    shutil.copytree(drafted.directory, twin)
    twin_body = twin / drafted.body_file.relative_to(drafted.directory)
    twin_body.write_text(
        "# Retry policy\n\nFive attempts, from the local copy.\n", encoding="utf-8"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    said = str(caught.value) + caught.value.remedy
    assert f".theurian/proposals/{drafted.proposal_id.value}" in said, said
    assert f".theurian/proposals-local/{drafted.proposal_id.value}" in said, said
    # Neither was chosen: nothing landed, and both directories are still whole.
    assert not (paths.migrations / drafted.migration_file.name).exists()
    assert not drafted.body_destination.exists()
    assert drafted.migration_file.is_file(), "the tracked proposal is intact"
    assert twin_body.is_file(), "and so is the local one"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_proposal_directory_that_cannot_be_read_is_answered_rather_than_crashing(
    service: ProposalService,
) -> None:
    """#227: a raw ``PermissionError`` escapes ``accept`` for an unreadable proposal.

    Confirmed through the real CLI: ``propose accept <id> --json`` on a proposal
    directory at mode ``0o000`` exited 1 with an empty stdout and a traceback
    bottoming at ``PermissionError: [Errno 13] Permission denied: .../evidence.json``
    -- so ``--json`` published no ``{error, remedy}`` document at all (CP-2), and
    the reader is handed a stack trace instead of the one-line ``chmod`` that
    fixes it.

    Measured mechanism at mode ``0o000`` (CPython 3.13): ``directory.iterdir()``
    in :meth:`ProposalService._require_migration` needs the read bit and raises
    ``PermissionError``, which :meth:`accept`'s examination clause translates. A
    previous ``directory.glob("*.yaml")`` swallowed that error and yielded
    nothing, so ``_require_migration`` found no candidate and fell to
    ``_no_migration_error`` over a migration sitting right there, unread -- the
    silent false negative #214/#227 replaced. Either way the answer must not
    conclude: "could not read it" is not "it has been accepted"
    (:meth:`ProposalService._read_evidence_record`).

    The permission is not exotic: a proposal directory arrives through a pull
    request and a contributor's umask, an interrupted checkout, or a
    root-owned file in a container all produce one this process cannot read.
    """
    drafted = service.draft(_request())
    drafted.directory.chmod(0o000)
    try:
        with pytest.raises(ProposalError) as caught:
            service.accept(drafted.proposal_id)
    finally:
        drafted.directory.chmod(0o755)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError), (
        "an unreadable directory cannot prove the change is in place"
    )
    remedy = caught.value.remedy
    assert any(word in remedy.lower() for word in _PERMISSION_REMEDY_WORDS), remedy
    assert f".theurian/proposals/{drafted.proposal_id.value}" in remedy
    # Project-relative, not the developer's home directory: an `OSError`'s own
    # text carries the absolute path, so a message built from `str(exc)` leaks it
    # (:meth:`ProposalService._evidence_indeterminate`, :func:`_within`).
    published = f"{caught.value} {remedy}"
    assert str(drafted.directory) not in published, "the absolute path must not leak"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_proposal_directory_whose_entries_cannot_be_examined_is_answered(
    service: ProposalService,
) -> None:
    """#227 at a second unguarded call: the directory lists, but nothing stats.

    At mode ``0o444`` the directory can be *listed* -- ``iterdir()`` returns the
    migration -- while every ``stat`` under it is refused. Measured: the raw
    ``path.is_symlink()`` probe in :meth:`ProposalService._require_migration` is
    what raises here, not the evidence read the sibling above reaches.

    One defect, several unguarded calls: the accept path probes the filesystem
    with raw ``iterdir`` / ``is_symlink`` / ``is_file`` / read calls and
    translates none of their ``OSError``s, so *which* one fires is an accident of
    the mode.
    A fix that guards only the call the first test happens to reach leaves this
    one crashing, which is why the mode that selects a different probe is pinned
    separately rather than folded into a parametrize.
    """
    drafted = service.draft(_request())
    drafted.directory.chmod(0o444)
    try:
        with pytest.raises(ProposalError) as caught:
            service.accept(drafted.proposal_id)
    finally:
        drafted.directory.chmod(0o755)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError), (
        "a directory that cannot be examined cannot prove the change is in place"
    )
    remedy = caught.value.remedy
    assert any(word in remedy.lower() for word in _PERMISSION_REMEDY_WORDS), remedy
    assert f".theurian/proposals/{drafted.proposal_id.value}" in remedy


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_migration_file_that_cannot_be_opened_is_answered_rather_than_crashing(
    service: ProposalService,
) -> None:
    """#227 at the read itself, which no mode on the *directory* can reach.

    The shape a container produces: the proposal directory is perfectly normal --
    it lists, and every entry stats -- and the migration file alone cannot be
    opened, because it belongs to another user. Every probe in
    :meth:`ProposalService._require_migration` therefore succeeds, and the
    ``PermissionError`` comes out of the read one line later, from
    :func:`read_source_file` inside the security layer.

    Measured, and the reason this is not a duplicate of the ``0o444`` sibling:
    with the directory at ``0o444`` the failure lands at ``is_symlink`` and the
    read is never attempted, so *neither* of the other two tests exercises the
    read. A fix that wraps only ``_require_migration``'s probes passes both of
    them and still crashes here.
    """
    drafted = service.draft(_request())
    drafted.migration_file.chmod(0o000)
    try:
        with pytest.raises(ProposalError) as caught:
            service.accept(drafted.proposal_id)
    finally:
        drafted.migration_file.chmod(0o644)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError), (
        "a migration that cannot be opened cannot prove the change is in place"
    )
    remedy = caught.value.remedy
    assert any(word in remedy.lower() for word in _PERMISSION_REMEDY_WORDS), remedy
    assert f".theurian/proposals/{drafted.proposal_id.value}" in remedy


def _poison_content_file(drafted: DraftedProposal, quoted_value: str) -> None:
    """Repoint the drafted migration's ``contentFile`` at a hand-authored value.

    ``accept`` computes its body moves before its pre-check runs, so a value the
    JSON Schema would reject -- one holding a NUL byte or an unpaired surrogate --
    reaches ``_destination_of``'s ``resolve()`` unfiltered, ahead of stage 1's
    schema check (ADR-0027 decision 2). The order is what makes these faults
    reachable, not an absence of validation: the moves are an *input* to the
    pre-check, so :meth:`_body_moves` necessarily runs first.
    ``quoted_value`` is a YAML double-quoted scalar so its ``\\0`` / ``\\uXXXX``
    escapes decode to the real code points.
    """
    text = drafted.migration_file.read_text(encoding="utf-8")
    replaced = text.replace(f"contentFile: {drafted.content_file}", f"contentFile: {quoted_value}")
    assert replaced != text, "the contentFile anchor did not match"
    drafted.migration_file.write_text(replaced, encoding="utf-8")


def test_accept_translates_a_nul_in_the_content_file_path(service: ProposalService) -> None:
    """CP-2 (adversarial e14): a NUL in ``contentFile`` escaped ``accept`` raw.

    ``Path.resolve()`` -> ``os.path.realpath`` -> ``lstat`` raises ``ValueError``
    (*"embedded null character in path"*) before any containment check, and
    ``ValueError`` is not an ``OSError``, so the examination clause did not catch
    it: the raw exception left ``accept`` and ``--json`` published zero bytes. The
    loader translates its own ``resolve()`` with ``except (ValueError, OSError)``;
    ``accept`` must match. Every accept-path exception is a ``ProposalError``
    carrying a remedy.
    """
    drafted = service.draft(_request())
    _poison_content_file(drafted, '"../knowledge/architecture/a\\0b.md"')

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert caught.value.remedy.strip(), "a translated failure carries a remedy"


def test_accept_translates_a_lone_surrogate_in_the_content_file_path(
    service: ProposalService,
) -> None:
    """CP-2 (adversarial e14): a lone surrogate in ``contentFile`` escaped ``accept`` raw.

    The sibling of the NUL above, and the reason the widening is ``ValueError``
    and not just the NUL's exact type: an unpaired surrogate cannot be encoded to
    UTF-8 for the ``lstat``, so ``resolve()`` raises ``UnicodeEncodeError`` -- a
    ``ValueError`` subclass, caught by the same clause, and neither an ``OSError``
    nor a ``TheurianError`` before the fix.
    """
    drafted = service.draft(_request())
    _poison_content_file(drafted, '"../knowledge/architecture/a\\uD800b.md"')

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert caught.value.remedy.strip(), "a translated failure carries a remedy"


def test_the_unreadable_remedy_does_not_prescribe_chmod_for_a_non_permission_fault(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """Round-one HIGH (CLASS-B): ``_unreadable`` prescribed ``chmod`` for any errno.

    A ``contentFile`` naming a directory reaches ``read_source_file`` as
    ``../knowledge`` -- the body moves are computed before the pre-check, so this
    is read before stage 1's schema check ever sees the document (ADR-0027
    decision 2) -- and the open raises ``IsADirectoryError`` (``EISDIR``), which
    the examination clause translates. The old remedy said *"Make ... readable
    -- chmod u+rX on it"*
    regardless, but no ``chmod`` cures ``EISDIR``: the fault is the authored
    ``contentFile``, which is what the remedy must name. This is the class
    ``c7cf455`` (#233) closed for :class:`PathEscapeError`, reopened at this site.
    Reproduced end to end (``repro_classB_remedy`` face 2).
    """
    drafted = service.draft(_request())
    _poison_content_file(drafted, "../knowledge")

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    remedy = caught.value.remedy
    assert "chmod" not in remedy.lower(), f"chmod cures no EISDIR: {remedy}"
    # The cause is the input, so the remedy names it rather than a permission bit.
    assert "contentFile" in remedy, remedy
    # The absolute path still must not leak (the discipline `_project_relative` keeps).
    assert str(paths.root) not in f"{caught.value} {remedy}", "the absolute path must not leak"


def test_an_eperm_read_failure_earns_the_permission_remedy_like_eacces(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """EPERM routes to the ``chmod`` remedy, exactly as EACCES does (#227).

    The accept-path read-failure remedy branches on ``_PERMISSION_ERRNOS``: a
    permission errno earns a ``chmod`` cure, and every other errno earns the
    neutral "no permission change cures that" text (:meth:`_read_failure_remedy`).
    A read can be refused with ``EPERM`` as well as ``EACCES`` -- a mandatory
    lock, a MAC policy, or an immutable/append-only flag all surface as ``EPERM``
    -- so both belong in the set and both must reach the permission remedy.
    Nothing induced ``EPERM`` end to end (a ``chmod`` fixture yields ``EACCES``),
    so dropping it from the set sent a genuine permission failure to the
    unactionable remedy and the drop survived the whole suite (adversarial round
    two, mutation ``eperm-out``). This pins the routing at the errno, not at a
    ``chmod`` that happens to reproduce EACCES.

    A unit test of the routing: it constructs the ``OSError`` objects directly, so
    it does not depend on a mode actually refusing a read and needs no root skip.
    """
    readable = paths.root / "child.md"
    readable.write_text("body\n", encoding="utf-8")
    filename = str(readable)

    assert errno.EPERM in _PERMISSION_ERRNOS

    eperm = OSError(errno.EPERM, os.strerror(errno.EPERM), filename)
    eacces = OSError(errno.EACCES, os.strerror(errno.EACCES), filename)
    eisdir = OSError(errno.EISDIR, os.strerror(errno.EISDIR), filename)

    eperm_remedy = service._read_failure_remedy(eperm, "child.md")

    # EPERM earns the permission remedy -- the same one EACCES earns -- not the
    # neutral "no permission change cures that" text a non-permission errno gets.
    assert any(word in eperm_remedy.lower() for word in _PERMISSION_REMEDY_WORDS), eperm_remedy
    assert eperm_remedy == service._read_failure_remedy(eacces, "child.md"), (
        "EPERM and EACCES are both permission failures and must route to one remedy"
    )
    assert "chmod" not in service._read_failure_remedy(eisdir, "child.md").lower(), (
        "the routing is by errno: a non-permission fault earns no chmod"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_unreadable_remedy_names_the_directory_when_the_parent_is_unsearchable(
    service: ProposalService,
) -> None:
    """Round-one HIGH (CLASS-B): the ``chmod`` was pointed at an unreachable file.

    At ``0o444`` the proposal directory lists but cannot be traversed, so the
    ``is_symlink`` stat of the migration file inside it raises ``EACCES`` -- and
    the failing path is the *child* ``.yaml``. The old remedy said *"Make
    <that .yaml> readable -- chmod u+rX on it"*, but the child is unreachable: the
    cure is ``chmod u+x`` on the parent directory, which is what lacks the search
    bit. The remedy must name the directory, not the file it cannot reach.
    Reproduced end to end (``repro_classB_remedy`` face 1).
    """
    drafted = service.draft(_request())
    drafted.directory.chmod(0o444)
    try:
        with pytest.raises(ProposalError) as caught:
            service.accept(drafted.proposal_id)
    finally:
        drafted.directory.chmod(0o755)

    remedy = caught.value.remedy
    # The directory is what is at fault, so it is what the cure names -- and the
    # unreachable file, which chmod-ing does nothing for, is *not* the target.
    assert f".theurian/proposals/{drafted.proposal_id.value}" in remedy, remedy
    assert drafted.migration_file.name not in remedy, (
        f"the unreachable file must not be the chmod target: {remedy}"
    )
    assert "chmod u+x" in remedy, f"the cure is a search bit on the directory: {remedy}"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_the_unreadable_remedy_points_at_migrations_before_re_drafting(
    service: ProposalService,
) -> None:
    """#89 half (adversarial ``unreadable-remedy-drops-migrations-pointer``): a
    refused read is not evidence that nothing landed.

    A read the filesystem refused says nothing about whether this proposal's
    migration has already landed, so re-drafting one that *has* mints a duplicate
    (#89). The remedy therefore sends the reader to ``.theurian/migrations/``
    first and never says *"draft the proposal again"* outright. The surviving
    mutation dropped the ``.theurian/migrations/`` pointer and replaced the tail
    with exactly that instruction; nothing pinned it, so it survived. This pins
    it on the ``0o000`` permission face, where the tail is reached.
    """
    drafted = service.draft(_request())
    drafted.directory.chmod(0o000)
    try:
        with pytest.raises(ProposalError) as caught:
            service.accept(drafted.proposal_id)
    finally:
        drafted.directory.chmod(0o755)

    remedy = caught.value.remedy
    assert ".theurian/migrations/" in remedy, remedy
    assert "draft the proposal again" not in remedy.lower(), remedy
    # The migrations pointer comes before any re-draft mention, so the reader
    # reads what is there before minting a second migration.
    assert remedy.index(".theurian/migrations/") < remedy.lower().index("re-draft"), remedy


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_directory_that_lists_but_does_not_stat_is_examined_not_declared_absent(
    service: ProposalService,
) -> None:
    """MEDIUM (adversarial e5): ``iterdir`` raises where ``glob`` would swallow.

    ``0o111`` is the one mode that separates the two: the directory can be
    *traversed* (every known child stats and opens) but not *listed*.
    ``iterdir()`` needs the read bit and raises ``PermissionError``, which
    ``accept`` translates to "could not be examined" -- correct, since the
    migration is right there, unread. ``glob("*.yaml")`` swallows the same error
    and yields nothing, so ``_require_migration`` would fall to the missing-file
    diagnosis and, finding the recorded id absent from ``.theurian/migrations/``,
    conclude *"nothing it drafted has been accepted"* -- the re-draft answer, over
    a migration that is present. The three ``#227`` tests use ``0o000``/``0o444``,
    where the diagnosis itself raises whichever enumerator is used, so none of
    them pins the ``iterdir`` choice; this one does.
    """
    drafted = service.draft(_request())
    drafted.directory.chmod(0o111)
    try:
        with pytest.raises(ProposalError) as caught:
            service.accept(drafted.proposal_id)
    finally:
        drafted.directory.chmod(0o755)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    # The signal that separates `iterdir` from `glob`: a permission remedy, not
    # the "draft it again" answer `glob`'s swallowed listing would reach.
    remedy = caught.value.remedy
    assert any(word in remedy.lower() for word in _PERMISSION_REMEDY_WORDS), remedy
    assert "could not be examined" in str(caught.value)


# -- has this proposal been accepted? (#253) -------------------------------
#
# Two answers with opposite remedies -- re-drafting an accepted proposal mints a
# second migration for a change already in history (#89), and telling the author
# of an interrupted draft that no action is needed discards work that exists
# nowhere else. `evidence.json` is committed and contributor-controlled (ADR-0013
# point 7), so the diagnosis is best-effort over untrusted input, not tamper-
# proof: a recorded `migrationId` is a claim, cross-checked by `itemId` against
# the migration it names; a read failure is answered indeterminate; and every
# fallible branch points the reader at `.theurian/migrations/` before it could
# discard work. The tests are grouped by what the answer is derived from -- a
# checkable recorded id, an unreadable record, or (legacy) the directory's shape.


def _edit_evidence(drafted: DraftedProposal, **fields: object) -> None:
    """Overwrite fields of a real draft's ``evidence.json``.

    A value of ``None`` deletes the key. Editing a file the service itself wrote
    keeps everything else exactly what ``draft`` produced, so a test forges one
    field rather than hand-assembling a record.
    """
    document = json.loads(drafted.evidence_file.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    drafted.evidence_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _as_legacy(drafted: DraftedProposal) -> None:
    """Rewrite ``evidence.json`` as one of the 26 proposals committed before #253.

    Those predate both ``migrationId`` and ``itemId``, so the diagnosis has no
    claim to check and falls to inference over the directory. Reproduced by
    removing both keys from a real draft.
    """
    _edit_evidence(drafted, migrationId=None, itemId=None)


def test_re_accepting_an_accepted_proposal_reports_it_accepted(
    service: ProposalService,
) -> None:
    """The proof path: the recorded migration is landed and names the same item.

    Re-accepting used to report "holds no migration file -- draft it again",
    which mints a second migration for a change already landed. The answer now
    names the migration ``evidence.json`` records, confirmed to be in
    ``.theurian/migrations/`` and to operate on the item the proposal names.
    Dies if the glob stops matching by id (the landed migration is not found).
    """
    drafted = service.draft(_request())
    accepted = service.accept(drafted.proposal_id)

    with pytest.raises(ProposalAlreadyAcceptedError, match="appears to have been accepted") as e:
        service.accept(drafted.proposal_id)

    assert accepted.migration.destination.name in str(e.value)
    assert "pull request" in e.value.remedy


def test_a_migration_id_pointing_at_another_proposals_migration_is_not_confirmed(
    service: ProposalService,
) -> None:
    """The forge: the item cross-check refuses to confirm another's migration.

    A never-accepted proposal sets ``migrationId`` to another proposal's landed
    migration, which operates on a *different* item than this proposal's record
    names. Keyed on the id alone that read as "already accepted / no action".

    The cross-check does not confirm it (round three), and -- because a migration
    IS in place under the recorded id -- the answer is read-before-acting, not
    "nothing landed" (round four, adversarial M1): the message says the item
    cannot be confirmed and the remedy says to correct the record rather than
    re-draft, which would mint a second migration. It stays a ``ChangeAlreadyInPlaceError``
    (exit 4), never the plain-``ProposalError`` re-draft code. Dies if the
    cross-check is removed -- then the id match alone reports it *accepted* with
    the confirming message.
    """
    landed = service.draft(_request(item_id=ItemId("architecture.landed")))
    service.accept(landed.proposal_id)
    forger = service.draft(_request(item_id=ItemId("architecture.forger")))
    _edit_evidence(forger, migrationId=landed.migration_id.value)
    forger.migration_file.unlink()

    with pytest.raises(ChangeAlreadyInPlaceError, match="cannot confirm") as caught:
        service.accept(forger.proposal_id)

    message = str(caught.value)
    assert "operates on the item this proposal names" not in message, "not the confirming message"
    assert landed.migration_id.value in message
    assert "correct evidence.json" in caught.value.remedy


def test_a_recorded_id_is_not_rescued_by_another_proposals_migration_for_its_item(
    service: ProposalService,
) -> None:
    """The other half of the centre: the glob must match by id, not scan them all.

    This proposal records its own (unlanded) migration id and claims the item a
    *different* accepted proposal landed. Its own migration is not in
    ``.theurian/migrations/``, so nothing it drafted was accepted -- even though a
    migration for the claimed item exists. Dies if the glob drops the id and
    scans every migration: it then finds the other proposal's and reports this one
    accepted.
    """
    landed = service.draft(_request(item_id=ItemId("architecture.shared")))
    service.accept(landed.proposal_id)
    waiting = service.draft(_request(item_id=ItemId("architecture.waiting")))
    _edit_evidence(waiting, itemId="architecture.shared")
    waiting.migration_file.unlink()

    with pytest.raises(ProposalError) as caught:
        service.accept(waiting.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert waiting.migration_id.value in str(caught.value)


def test_a_landed_migration_with_no_item_recorded_is_read_before_acting(
    service: ProposalService,
) -> None:
    """adversarial M1: migrationId landed, itemId absent -> not "nothing landed".

    A migration IS in ``.theurian/migrations/`` under the recorded id, but the
    record names no item to cross-check it against -- a contributor-edited
    ``evidence.json``, or an intermediate-build one that predates ``itemId``.
    Reporting "nothing has been accepted" and exit 1 (whose #254 contract is
    "re-draft") would tell the author to duplicate a change on disk (#89). It is
    read-before-acting instead: a ``ChangeAlreadyInPlaceError`` (exit 4), a
    message that does not assert nothing landed, and a remedy pointing at the
    migration set.

    Dies if "present-but-unconfirmed" is folded back into the "nothing landed"
    branch: the exception type drops to a plain ``ProposalError``.
    """
    drafted = service.draft(_request())
    service.accept(drafted.proposal_id)
    _edit_evidence(drafted, itemId=None)

    with pytest.raises(ChangeAlreadyInPlaceError, match="cannot confirm") as caught:
        service.accept(drafted.proposal_id)

    assert "nothing" not in str(caught.value).lower(), "the migration is landed under the id"
    assert ".theurian/migrations/" in caught.value.remedy
    assert "correct evidence.json" in caught.value.remedy


def test_a_symlinked_landed_migration_is_recognised_as_landed(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A symlinked landed migration is landed, and the symlink caveat dissolves.

    The migration loader follows symlinks, so a symlink at
    ``.theurian/migrations/<id>-<slug>.yaml`` pointing at a real in-project
    migration is landed and ``migrate validate`` sees it. Round five's own
    landed-detector skipped a symlinked candidate before it could record it, so it
    fell through to ``absent`` -> exit 1 "nothing landed" (#89).

    Reading the loaded set closes it without special-casing the symlink: the
    migration is in ``_by_id`` because the loader already read it through the
    symlink-escape guard, so ``_landed_state`` finds it and confirms the item from
    the *loaded* operations -- no link is followed here. With a matching item it is
    ``confirmed`` (exit 4 "appears to have been accepted"), the same as a
    non-symlinked landed migration, which is more precise than round five's
    forced "cannot confirm". The property that matters is unchanged: never
    ``absent``, never exit 1.
    """
    landed = service.draft(_request(item_id=ItemId("architecture.landed")))
    service.accept(landed.proposal_id)
    real = next(paths.migrations.glob(f"{landed.migration_id.value}-*.yaml"))
    hidden = real.rename(real.with_suffix(".yaml.real"))
    real.symlink_to(hidden.name)
    assert real.is_symlink(), "the migration under the recorded id is now a symlink"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1

    proposal = service.draft(_request(item_id=ItemId("architecture.pointer")))
    _edit_evidence(proposal, migrationId=landed.migration_id.value, itemId="architecture.landed")
    proposal.migration_file.unlink()

    with pytest.raises(ChangeAlreadyInPlaceError) as caught:
        service.accept(proposal.proposal_id)

    message = str(caught.value)
    assert "nothing" not in message.lower(), "a symlinked migration is landed, not absent"
    assert "operates on the item this proposal names" in message, "the loaded operations confirm it"


def test_a_landed_migration_renamed_off_its_ulid_prefix_is_still_landed(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH (round six): the loader keys by inner id, so `_landed_state` must too.

    A landed migration renamed off its ULID-prefix filename -- or filed under a
    different prefix with the same inner id -- is still loaded and keyed by its
    *inner* id, so ``migrate validate`` sees it (exit 0). Round five's landed
    detector globbed ``<id>-*.yaml`` on the *filename* ULID prefix, so it missed
    the renamed file -> ``absent`` -> exit 1 "nothing landed" -> duplicate-mint
    (#89), the same observable as the symlink face, a different filename shape.

    Reading the loaded set's ``_by_id`` (the same dict the loader builds) closes
    the class: no filename shape can make ``_landed_state`` and the loader disagree.
    Dies if the wiring reverts to a filename glob -- the renamed file is then absent
    and this reports "nothing landed".
    """
    landed = service.draft(_request(item_id=ItemId("architecture.landed")))
    service.accept(landed.proposal_id)
    real = next(paths.migrations.glob(f"{landed.migration_id.value}-*.yaml"))
    # Rename off the ULID prefix entirely; the inner id is unchanged, so the
    # loader still keys it under `landed.migration_id`.
    real.rename(paths.migrations / "zzz-renamed-off-its-prefix.yaml")
    loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
    assert loaded.migration_set.get(landed.migration_id) is not None, "still landed by inner id"

    proposal = service.draft(_request(item_id=ItemId("architecture.pointer")))
    _edit_evidence(proposal, migrationId=landed.migration_id.value, itemId="architecture.landed")
    proposal.migration_file.unlink()

    with pytest.raises(ChangeAlreadyInPlaceError) as caught:
        service.accept(proposal.proposal_id)

    message = str(caught.value)
    assert "nothing" not in message.lower(), "the renamed migration is landed, not absent"
    assert "operates on the item this proposal names" in message, "confirmed from the loaded set"


def test_a_landed_migration_under_a_different_prefix_but_matching_inner_id_is_landed(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A file whose *name* ULID-prefix differs from its inner id is keyed by inner id.

    Distinct from the rename-off-prefix face: here the filename still looks like a
    migration (``<other-ulid>-<slug>.yaml``) but its inner id is the recorded one.
    A filename-glob keyed on the *recorded* id's prefix would not match it, while
    the loader -- and now ``_landed_state`` -- key on the inner id and find it.
    """
    landed = service.draft(_request(item_id=ItemId("architecture.landed")))
    service.accept(landed.proposal_id)
    real = next(paths.migrations.glob(f"{landed.migration_id.value}-*.yaml"))
    real.rename(paths.migrations / "01K9C7VN4TQZB2M8XR5HD3JFEW-a-different-prefix.yaml")
    loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
    assert loaded.migration_set.get(landed.migration_id) is not None

    proposal = service.draft(_request(item_id=ItemId("architecture.pointer")))
    _edit_evidence(proposal, migrationId=landed.migration_id.value, itemId="architecture.landed")
    proposal.migration_file.unlink()

    with pytest.raises(ChangeAlreadyInPlaceError) as caught:
        service.accept(proposal.proposal_id)

    assert "nothing" not in str(caught.value).lower(), "landed by inner id, not absent"


def test_an_interrupted_draft_with_a_recorded_id_is_not_reported_as_accepted(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The recorded id names a migration that was never landed: nothing accepted.

    The remedy points at ``.theurian/migrations/`` first and never says an
    unconditional "no action is needed", so it cannot tell the author to discard
    a draft that never landed (#253).
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    assert drafted.migration_id.value in str(caught.value)
    assert ".theurian/migrations/" in caught.value.remedy
    assert "theurian propose" in caught.value.remedy
    assert not list(paths.migrations.glob("*.yaml"))


def test_a_migration_renamed_off_kebab_case_is_not_reported_absent_from_the_directory(
    service: ProposalService,
) -> None:
    """#253 round three, MEDIUM: the message must not lie about the directory.

    A migration renamed off ``<id>-<slug>.yaml`` no longer matches
    ``_require_migration``'s name test, so the accept path reaches the
    no-migration diagnosis while the file is still *in* the directory. The message
    says only what was checked -- no file named ``<id>-<slug>.yaml`` -- and the
    remedy allows that it may be present under a different name rather than
    instructing a delete that would discard it.
    """
    drafted = service.draft(_request())
    renamed = drafted.migration_file.rename(drafted.directory / "migration.yaml")
    assert renamed.is_file()

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    assert "under a different name" in caught.value.remedy
    assert "delete" not in caught.value.remedy.lower()


# -- a read failure is indeterminate, never an answer -----------------------


def test_a_present_but_unreadable_evidence_file_is_indeterminate(
    service: ProposalService,
) -> None:
    """ "Could not read the record" is not "no record", and must not conclude.

    Collapsing an unreadable ``evidence.json`` into "no record" dropped to legacy
    inference, which can conclude accepted -- an acceptance verdict granted to
    anyone who can corrupt the file. It is answered indeterminate now.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.write_text("{ this is not valid json ", encoding="utf-8")

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert "not valid JSON" in str(caught.value)


def test_a_deeply_nested_evidence_file_is_indeterminate_not_a_crash(
    service: ProposalService,
) -> None:
    """A ``RecursionError`` from ``json.loads`` is a read failure, not a traceback.

    Reproduced: deeply nested JSON raised ``RecursionError`` (a ``RuntimeError``,
    uncaught) -- a raw traceback, exit 1, and no ``--json`` document (CP-2). It is
    caught and answered indeterminate.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.write_text("[" * 20000 + "]" * 20000, encoding="utf-8")

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert "nested too deeply" in str(caught.value)


def test_an_oversized_evidence_file_is_indeterminate_and_names_evidence_json(
    service: ProposalService,
) -> None:
    """An ``evidence.json`` over SEC-8's cap names itself, not ``contentFile``.

    ``_read_within_project`` raises ``InputTooLargeError`` (a ``TheurianError``),
    which used to reach the CLI's generic handler and print a remedy about the
    migration's ``contentFile`` -- the wrong file. It is indeterminate now and the
    remedy names ``evidence.json``.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.write_bytes(b'{"x": "' + b"a" * (MAX_SOURCE_FILE_BYTES + 1) + b'"}')

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert "size cap" in str(caught.value)
    assert "evidence.json" in caught.value.remedy
    assert "contentFile" not in caught.value.remedy


def test_an_evidence_path_that_is_a_directory_is_indeterminate(
    service: ProposalService,
) -> None:
    """``evidence.json`` as a directory is present but unreadable, not absent.

    ``exists()`` is true, so it is not the absent case; reading it raises an
    ``OSError``, which is caught and answered indeterminate rather than dropped to
    inference. What a checkout half-restored into a stale tree can leave behind.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.unlink()
    drafted.evidence_file.mkdir()

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)


def test_a_non_object_evidence_file_is_indeterminate(service: ProposalService) -> None:
    """A present, parseable, non-object ``evidence.json`` records no fields.

    ``[1, 2, 3]`` is valid JSON but not a mapping, so it carries no ``migrationId``
    to check and cannot prove acceptance. Answered indeterminate, not dropped to
    inference -- which, with no generated body left, could conclude accepted.
    Dies if the non-object branch falls through to ``return None`` (absent).
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert "not a JSON object" in str(caught.value)


def test_a_non_utf8_evidence_file_is_indeterminate_as_bad_utf8(service: ProposalService) -> None:
    """The reachable ``UnicodeDecodeError`` branch, pinned.

    ``_read_within_project`` returns bytes, and ``json.loads`` on non-UTF-8 bytes
    raises ``UnicodeDecodeError`` before it reaches JSON syntax -- so the reason is
    "not valid UTF-8", not "not valid JSON". Dies if that branch is dropped or
    ordered after ``ValueError`` (which would report it as JSON).
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.write_bytes(b'{"migrationId": "\xff\xfe"}')

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert "not valid UTF-8" in str(caught.value)


@pytest.mark.skipif(
    not _CAN_MAKE_A_BLOCKING_FILE, reason="needs os.mkfifo and an interruptible timer"
)
def test_an_evidence_file_that_is_a_fifo_says_it_is_not_a_regular_file(
    service: ProposalService,
) -> None:
    """The reason table is closed, so a new refusal type must be added to it.

    ``read_source_file`` grew ``IrregularSourceFileError`` for a file whose size
    bounds nothing (#215). It is a ``TheurianError``, so
    ``_read_evidence_record`` already caught it and the verdict was already
    indeterminate -- but no entry matched it, and the table's fallthrough is
    "it is not a JSON object", which is the one thing this file demonstrably is
    not: nothing parsed it at all. Reproduced before the entry existed.

    The timer is what makes a regression fail rather than stall the suite: were
    the shape guard removed, this read would block in ``open()`` for a writer
    that never comes.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.unlink()
    os.mkfifo(drafted.evidence_file)

    with (
        fails_rather_than_hanging(5, waiting_for="accept over a FIFO evidence.json"),
        pytest.raises(ProposalError, match="could not be examined") as caught,
    ):
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)
    assert "not a regular file" in str(caught.value)
    assert "not a JSON object" not in str(caught.value), (
        "the fallthrough claimed the record parsed and held the wrong shape"
    )


def test_every_read_failure_the_evidence_read_can_raise_has_its_own_reason() -> None:
    """The correspondence the closed table rests on, checked rather than assumed.

    ``_evidence_failure_reason``'s fallthrough is a *verdict* -- "it is not a
    JSON object" -- not an "unknown" label, so a refusal type with no entry is
    reported as a fact about a document nobody managed to parse. Each type
    ``read_source_file`` documents itself as raising is therefore driven here,
    directly rather than through a fixture: a filesystem cannot produce every
    one of them on demand, and the mapping is what is under test.

    Dies if any entry is removed from ``_EVIDENCE_FAILURE_REASONS``: each
    assertion below is the entry's own text, and every one of these types falls
    through to the same wrong sentence without it.
    """
    assert _evidence_failure_reason(IrregularSourceFileError("a socket")) == (
        "it is not a regular file"
    )
    assert _evidence_failure_reason(InputTooLargeError("source file size", 1, 2)) == (
        "it is larger than the size cap"
    )
    assert _evidence_failure_reason(PathEscapeError("x", "/root")) == (
        "its path escapes the project"
    )
    assert _evidence_failure_reason(FileNotFoundError(errno.ENOENT, "No such file")) == (
        "no such file"
    ), "an OSError still answers with its own strerror, and is not in the table"


def test_a_dangling_symlink_evidence_file_is_indeterminate_not_absent(
    service: ProposalService,
) -> None:
    """A broken symlink named ``evidence.json`` is present, not a missing record.

    ``exists()`` is ``False`` for a dangling link, so without the ``is_symlink()``
    half of the absent test it reads as "no record" and drops to inference -- which
    can conclude accepted from a directory a contributor placed the link in. It is
    indeterminate. Dies if ``and not evidence.is_symlink()`` is removed.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    drafted.evidence_file.unlink()
    drafted.evidence_file.symlink_to(drafted.directory / "does-not-exist.json")

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)


def test_an_in_project_symlink_evidence_file_is_indeterminate(service: ProposalService) -> None:
    """A symlinked ``evidence.json`` is refused by the read guard, then indeterminate.

    A committed proposal's files are real (ADR-0013 point 7); a link resolving to
    a real in-project file still routes through ``_reject_symlink_in_chain`` and is
    caught as a present-but-unreadable record rather than followed and trusted.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    real = drafted.directory / "real-evidence.json"
    real.write_text(drafted.evidence_file.read_text(encoding="utf-8"), encoding="utf-8")
    drafted.evidence_file.unlink()
    drafted.evidence_file.symlink_to(real)

    with pytest.raises(ProposalError, match="could not be examined") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ProposalAlreadyAcceptedError)


@pytest.mark.parametrize(
    "value",
    [123, ["01K9C7VN4TQZB2M8XR5HD3JFEW"], {"id": "x"}, None, True, "short", "not a ulid!!!", ""],
)
def test_a_malformed_migration_id_is_treated_as_absent_not_a_crash(
    service: ProposalService, value: object
) -> None:
    """A ``migrationId`` of the wrong shape falls to inference, never crashes.

    A parsed record whose ``migrationId`` is not a ULID string is *no usable
    claim*, distinct from an unreadable file: the JSON read fine. It drops to
    inference (here, an unfinished draft), not to a crash and not to indeterminate.
    """
    drafted = service.draft(_request())
    drafted.migration_file.unlink()
    _edit_evidence(drafted, migrationId=value)

    with pytest.raises(ProposalError, match="looks unfinished") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    assert "could not be examined" not in str(caught.value)


# -- absent record: legacy inference over the directory ---------------------


def test_an_accepted_proposal_whose_evidence_was_removed_points_at_migrations_first(
    service: ProposalService,
) -> None:
    """#253 round three, HIGH: the one branch that said "draft again" unconditionally.

    An accepted proposal whose ``evidence.json`` is gone has its migration and
    body landed and its directory emptied of bodies. With no record to check, the
    answer is inferred -- and it must point at ``.theurian/migrations/`` first and
    never instruct re-drafting outright, or it tells the author to duplicate a
    change already in history.
    """
    drafted = service.draft(_request())
    service.accept(drafted.proposal_id)
    drafted.evidence_file.unlink()

    with pytest.raises(ProposalAlreadyAcceptedError, match="appears to have been accepted") as e:
        service.accept(drafted.proposal_id)

    # Points at the migration set before it mentions re-drafting, and re-drafting
    # is conditional ("If it is not") -- never the unconditional "draft again"
    # that would tell the author to duplicate a change already in history.
    assert ".theurian/migrations/" in e.value.remedy
    assert e.value.remedy.index(".theurian/migrations/") < e.value.remedy.index(
        "draft the change again"
    )


def test_a_legacy_proposal_infers_acceptance_from_the_generated_body_shape(
    service: ProposalService,
) -> None:
    """Both inferred answers, on directories a real draft produced.

    A generated-shape body still present reads as unfinished; none left reads as
    accepted. Both point the reader at the migration set before acting, because
    the inference is best-effort over a contributor-controlled directory.
    """
    unfinished = service.draft(_request())
    _as_legacy(unfinished)
    unfinished.migration_file.unlink()

    with pytest.raises(ProposalError, match="looks unfinished") as caught:
        service.accept(unfinished.proposal_id)
    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    assert ".theurian/migrations/" in caught.value.remedy

    accepted = service.draft(_request(item_id=ItemId("architecture.other-policy")))
    service.accept(accepted.proposal_id)
    _as_legacy(accepted)

    with pytest.raises(ProposalAlreadyAcceptedError, match="appears to have been accepted") as e:
        service.accept(accepted.proposal_id)
    assert ".theurian/migrations/" in e.value.remedy


def test_a_file_accept_left_behind_does_not_flip_the_legacy_inference(
    service: ProposalService,
) -> None:
    """The leftover-file regression, on the path that still infers.

    A real accepted proposal now carries a ``migrationId`` and is answered by the
    proof path, so leftover files never reach the inference for it. On the legacy
    path they still would, and the generated-shape filter is what keeps a
    ``Thumbs.db``, a reviewer's notes or a nested stray from reading as an unmoved
    body and reporting the accepted proposal unfinished.
    """
    drafted = service.draft(_request())
    service.accept(drafted.proposal_id)
    _as_legacy(drafted)
    for name in ("REVIEW-NOTES.md", "Thumbs.db", "desktop.ini", "evidence.json~"):
        (drafted.directory / name).write_text("left behind\n", encoding="utf-8")
    (drafted.directory / "nested").mkdir()
    (drafted.directory / "nested" / "leftover.md").write_text("also left\n", encoding="utf-8")

    with pytest.raises(ProposalAlreadyAcceptedError, match="appears to have been accepted"):
        service.accept(drafted.proposal_id)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_an_unreadable_subtree_on_the_legacy_path_is_indeterminate(
    service: ProposalService,
) -> None:
    """A subtree the walk cannot read is indeterminate, and the path it names is relative.

    ``rglob`` swallowed the ``PermissionError`` and yielded what it could reach,
    so an unreadable subdirectory hiding the body reported the draft accepted. The
    walk refuses instead. The named path is project-relative, not the developer's
    absolute home directory (:func:`_within`).
    """
    drafted = service.draft(_request())
    _as_legacy(drafted)
    drafted.migration_file.unlink()
    subdirectory = drafted.body_file.parent
    subdirectory.chmod(0o000)
    try:
        with pytest.raises(ProposalError, match="could not be examined") as caught:
            service.accept(drafted.proposal_id)
    finally:
        subdirectory.chmod(0o755)

    message = str(caught.value)
    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    assert "chmod" in caught.value.remedy
    assert str(drafted.directory) not in message, "the absolute path must not leak"
    assert repr(subdirectory.relative_to(drafted.directory).as_posix()) in message


def test_the_legacy_inference_sees_a_symlinked_body(service: ProposalService) -> None:
    """A body that is a symlink is still a body the generator's move did not take.

    ``accept`` refuses a symlinked body by name later; what matters *here* is that
    the file has not been moved out, so the draft is unfinished. Replace the
    walk's name test with one that follows the link and this reads as accepted.
    """
    drafted = service.draft(_request())
    _as_legacy(drafted)
    drafted.migration_file.unlink()
    target = drafted.body_file.rename(drafted.directory / "elsewhere.md")
    drafted.body_file.symlink_to(target)

    with pytest.raises(ProposalError, match="looks unfinished"):
        service.accept(drafted.proposal_id)


def test_the_legacy_inference_names_two_bodies_in_one_directory_in_sorted_order(
    service: ProposalService,
) -> None:
    """Two bodies in ONE directory, one order, on every filesystem.

    A directory listing is not ordered, so a message built from ``walk`` order
    differs between machines. The two planted bodies share the proposal's top
    level -- the case ``walk`` reorders on APFS, unlike two in different
    directories where its top-down order already matches sorted -- so removing the
    ``sorted`` reorders this message. ``aaa`` must precede ``zzz`` regardless.
    """
    drafted = service.draft(_request())
    _as_legacy(drafted)
    drafted.migration_file.unlink()
    drafted.body_file.unlink()
    zzz = drafted.directory / "zzz.01K9C7VN4TQZB2M8XR5HD3JFEW.md"
    aaa = drafted.directory / "aaa.01K9C7VN4TQZB2M8XR5HD3JFEV.md"
    zzz.write_text("z\n", encoding="utf-8")
    aaa.write_text("a\n", encoding="utf-8")

    with pytest.raises(ProposalError, match="looks unfinished") as caught:
        service.accept(drafted.proposal_id)

    message = str(caught.value)
    assert message.index(repr(aaa.name)) < message.index(repr(zzz.name))


def test_a_content_file_cannot_forge_this_command_s_own_error_output(
    service: ProposalService,
) -> None:
    """A committed migration chooses text that reaches a terminal (T-3's shape).

    ``ESC [ 2 K`` erases the line a terminal has already drawn and a carriage
    return returns the cursor to its start, so a name carrying both prints
    whatever follows *as if this command had printed it*. A proposal directory is
    committed (ADR-0013 point 7), and YAML's double-quoted escapes carry both
    characters past a parser that refuses them literally (``\\e``, ``\\r``). This
    is the error path; the success path is covered at the CLI, where the render
    sink escapes controls.
    """
    drafted = service.draft(_request())
    forged = '"../knowledge/\\e[2K\\rerror: has already been accepted.md"'
    document = drafted.migration_file.read_text(encoding="utf-8")
    drafted.migration_file.write_text(
        document.replace(drafted.content_file, forged), encoding="utf-8"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    message = f"{caught.value}{caught.value.remedy}"
    assert "\x1b" not in message and "\r" not in message
    assert "\\x1b[2K\\r" in message, "rendered as an escape, so a terminal draws it"


def test_the_bodies_a_refusal_names_are_capped_rather_than_all_listed(
    service: ProposalService,
) -> None:
    """One directory sets the length of the message it provokes.

    A proposal directory is the contributor's, so its file count is too:
    50,000 files produced a 600 KB error string in 1.5 s. Five names, then a
    count. Counting the names listed rather than looking for the "and N more"
    suffix: with the bound removed and the suffix left in place, the message lists
    all ten *and* says "and 5 more", and an assertion on the suffix alone passes.
    """
    drafted = service.draft(_request())
    _as_legacy(drafted)
    drafted.migration_file.unlink()
    planted = [f"filler-{index}.01K9C7VN4TQZB2M8XR5HD3JFE{index}.md" for index in range(9)]
    for name in planted:
        (drafted.directory / name).write_text("filler\n", encoding="utf-8")

    with pytest.raises(ProposalError, match="looks unfinished") as caught:
        service.accept(drafted.proposal_id)

    message = str(caught.value)
    body = drafted.body_file.relative_to(drafted.directory).as_posix()
    listed = [name for name in [*planted, body] if repr(name) in message]

    assert len(listed) == 5, f"five names, not {len(listed)}: {message}"
    assert "and 5 more" in message, "and a count of the rest"


def test_accept_refuses_a_body_path_that_leaves_the_project(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A proposal directory may be committed, so its migration is untrusted input.

    ``accept`` is the one point where a hand-edited ``contentFile`` chooses a
    write destination, and SEC-7 covers every path rather than the ones that
    look like user input.
    """
    drafted = service.draft(_request())
    document = drafted.migration_file.read_text(encoding="utf-8")
    assert drafted.content_file in document
    drafted.migration_file.write_text(
        document.replace(drafted.content_file, "../../../../escaped.md"), encoding="utf-8"
    )

    with pytest.raises(PathEscapeError):
        service.accept(drafted.proposal_id)

    assert not (paths.root.parent / "escaped.md").exists()


# -- accept trusts nothing in the proposal directory (CRITICAL) ------------
#
# A proposal directory is committed and arrives through a contributor's pull
# request (ADR-0013 point 7), so every path in it is untrusted. Each test below
# is one face of "accept read or wrote a file it should not have", reproduced
# against this service before the fix: a symlink exfiltrated an out-of-project
# secret into a tracked migration, and an in-root `contentFile` outside
# `knowledge/` wrote an executable git hook.


def test_accept_refuses_a_symlinked_migration_and_leaks_nothing(
    service: ProposalService, paths: ProjectPaths, tmp_path: Path
) -> None:
    """Face A: a `*.yaml` symlink to an out-of-project secret must not be read.

    `is_file()` follows the link, so before the fix the target's bytes were read
    and written into a tracked `.theurian/migrations/*.yaml`, exit 0. The secret
    is mapping-shaped (a real `~/.claude.json` is valid JSON, hence valid YAML),
    so it is the *content*, not a parse error, that the old path would have
    surfaced.
    """
    drafted = service.draft(_request())
    secret = tmp_path / "claude.json"
    secret.write_text('{"token": "SUPER-SECRET", "org": "acme"}\n', encoding="utf-8")
    landed = paths.migrations / drafted.migration_file.name
    drafted.migration_file.unlink()
    drafted.migration_file.symlink_to(secret)

    with pytest.raises(ProposalError, match="symlink"):
        service.accept(drafted.proposal_id)

    assert not landed.exists(), "the migration name must hold nothing"
    assert "SUPER-SECRET" not in _tree_bytes(paths.root)


@pytest.mark.parametrize(("local", "parent"), _LOCATIONS)
def test_accept_refuses_a_symlinked_proposal_directory(
    service: ProposalService, paths: ProjectPaths, tmp_path: Path, local: bool, parent: str
) -> None:
    """Face B: the ULID name is safe, but not what it resolves to.

    The proposal directory itself is a symlink to an out-of-project directory
    whose `*.yaml` would otherwise be pulled onto the accept path.

    Parametrized over both locations for ADR-0028's sixth owed item. The
    git-ignored directory is *more* exposed to this shape, not less: nothing in
    a pull request review ever looks at it, so a link planted there is seen by
    no human before ``accept`` reads it. If the two locations ever disagree here
    the containment guarantee has two implementations and one of them is wrong,
    so the remedy's own path is asserted too -- that string is where a lookup
    that graded one location and fell through to the other would show itself,
    by sending the author of a local proposal to a directory that does not hold
    it.
    """
    drafted = service.draft(_request(), local=local)
    elsewhere = tmp_path / "elsewhere"
    drafted.directory.rename(elsewhere)
    drafted.directory.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ProposalError, match="symlink") as caught:
        service.accept(drafted.proposal_id)

    assert f"{parent}{drafted.proposal_id.value}" in caught.value.remedy
    assert not (paths.migrations / drafted.migration_file.name).exists(), "nothing landed"


def test_accept_refuses_a_symlinked_body_source(
    service: ProposalService, paths: ProjectPaths, tmp_path: Path
) -> None:
    """A body that is a link to an out-of-project file must not be copied in."""
    drafted = service.draft(_request())
    secret = tmp_path / "id_ed25519"
    secret.write_text("PRIVATE-KEY-MATERIAL\n", encoding="utf-8")
    drafted.body_file.unlink()
    drafted.body_file.symlink_to(secret)

    with pytest.raises(ProposalError, match="symlink"):
        service.accept(drafted.proposal_id)

    assert not drafted.body_destination.exists()
    assert "PRIVATE-KEY-MATERIAL" not in _tree_bytes(paths.root)


def test_accept_refuses_an_in_project_intermediate_directory_symlink(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """No symlink anywhere in the read chain -- intermediate components included.

    ``read_source_file`` follows an intermediate symlink that stays inside the
    project root: ``assert_no_symlink_escape`` refuses only one that leaves the
    root. So a namespaced body whose *subdirectory* in the proposal is an
    in-project directory symlink was read through -- the accept path read an
    in-project file the proposal never authored. No out-of-project disclosure
    (root containment holds) and ``contentSha256`` catches it downstream, but the
    accept docstring claims "no symlink anywhere in its chain", so the chain is
    walked and any symlink component is refused, matching ``_require_directory``.
    """
    drafted = service.draft(_request())
    leaf = drafted.body_file.name
    real_subdir = drafted.body_file.parent  # <proposal>/architecture/
    decoy = paths.proposals / "decoy"  # an in-project directory the proposal did not author
    decoy.mkdir()
    (decoy / leaf).write_text("BYTES THE PROPOSAL NEVER AUTHORED\n", encoding="utf-8")
    shutil.rmtree(real_subdir)
    real_subdir.symlink_to(decoy, target_is_directory=True)

    with pytest.raises(ProposalError, match="symlink"):
        service.accept(drafted.proposal_id)

    assert not drafted.body_destination.exists()
    assert "NEVER AUTHORED" not in _tree_bytes(paths.knowledge)


def test_accept_refuses_a_content_file_inside_the_root_but_outside_knowledge(
    service: ProposalService,
    paths: ProjectPaths,
) -> None:
    """Face C: `../../.git/hooks/pre-commit` is inside the root and must be refused.

    Reproduced end to end before the fix: `accept` wrote an executable git hook
    that runs on the maintainer's next commit, invisible to `git status`. The
    destination boundary is `.theurian/knowledge/`, not the project root, so an
    in-root escape from `knowledge/` is refused like an out-of-root one.
    """
    (paths.root / ".git" / "hooks").mkdir(parents=True)
    drafted = service.draft(_request())
    document = drafted.migration_file.read_text(encoding="utf-8")
    drafted.migration_file.write_text(
        document.replace(drafted.content_file, "../../.git/hooks/pre-commit"), encoding="utf-8"
    )
    (drafted.directory / "pre-commit").write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")

    with pytest.raises(PathEscapeError):
        service.accept(drafted.proposal_id)

    assert not (paths.root / ".git" / "hooks" / "pre-commit").exists()


def test_accept_never_lands_an_executable_body(
    service: ProposalService,
) -> None:
    """A body chmod 0755 in the proposal directory must not land executable.

    `Path.replace` preserves the source's permission bits; the fix writes with
    an explicit mode instead, so the executable bit never survives the move.
    """
    drafted = service.draft(_request())
    os.chmod(drafted.body_file, 0o755)  # noqa: S103 - the executable bit is the input under test

    service.accept(drafted.proposal_id)

    assert drafted.body_destination.stat().st_mode & 0o111 == 0


def test_accept_does_not_write_through_a_destination_symlink(
    service: ProposalService, paths: ProjectPaths, tmp_path: Path
) -> None:
    """A body destination that is a planted symlink must be refused, not followed.

    The destination resolves inside `knowledge/`, so containment passes; the
    `O_NOFOLLOW` write is what stops the body being written through the link to
    wherever it points.
    """
    outside = tmp_path / "outside.md"
    drafted = service.draft(_request())
    drafted.body_destination.parent.mkdir(parents=True, exist_ok=True)
    drafted.body_destination.symlink_to(outside)

    # An out-of-knowledge target is caught by containment on the resolved path;
    # an in-knowledge one would reach the O_NOFOLLOW write. Either is a refusal.
    with pytest.raises((ProposalError, PathEscapeError, OSError)):
        service.accept(drafted.proposal_id)

    assert not outside.exists()


def test_accept_names_the_body_it_cannot_find(
    service: ProposalService,
) -> None:
    drafted = service.draft(_request())
    drafted.body_file.unlink()

    with pytest.raises(ProposalError, match=r"retry-policy\."):
        service.accept(drafted.proposal_id)


def test_accept_refuses_a_proposal_directory_holding_two_migrations(
    service: ProposalService,
) -> None:
    """One proposal is one change; two files make "the migration" ambiguous."""
    drafted = service.draft(_request())
    (drafted.directory / "01K1AAAAAA01234567890ABCDE-other.yaml").write_text("{}\n")

    with pytest.raises(ProposalError, match="two"):
        service.accept(drafted.proposal_id)


def test_a_namespaceless_yaml_body_can_be_drafted_and_accepted(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-3: a YAML body's file is also a `*.yaml`, and used to look like a migration.

    Globbing `*.yaml` for the migration counted the body, so `accept` reported
    "two or more migration files" and a YAML-bodied proposal could never be
    accepted. The migration is identified by its `<ulid>-<slug>.yaml` name now.

    The item is deliberately **namespace-less** (``limits``, not ``api.limits``):
    a namespaced body mirrors into a subdirectory and a top-level ``glob`` never
    sees it, so only a body at the top level -- beside the migration -- exercises
    the name check that keeps them apart. Reverting to ``glob("*.yaml")`` makes
    this find two migrations.
    """
    drafted = service.draft(_request(item_id=ItemId("limits"), body="max: 3\n", content_type=YAML))
    assert drafted.body_file.parent == drafted.directory, "the body is at the top level"
    assert drafted.body_file.suffix == ".yaml"

    accepted = service.accept(drafted.proposal_id)

    assert accepted.migration.destination.name == drafted.migration_file.name
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_a_json_body_can_be_drafted_and_accepted(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The other structured format `--help` promises, exercised end to end."""
    drafted = service.draft(
        _request(item_id=ItemId("api.schema"), body='{"max": 3}\n', content_type=JSON)
    )

    service.accept(drafted.proposal_id)

    assert drafted.body_destination.read_text() == '{"max": 3}\n'
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_translates_a_malformed_migration_rather_than_crashing(
    service: ProposalService,
) -> None:
    """HIGH-2: a malformed migration YAML must be a `{error, remedy}`, not a traceback.

    `load_yaml_mapping` raises `yaml.YAMLError`, which is not a `ValueError`, so
    it escaped the translation and reached `--json` as a bare traceback (the
    unguarded-YAMLError class filed as #217, here on the accept path).
    """
    drafted = service.draft(_request())
    drafted.migration_file.write_text("operations: [ : : ]\n", encoding="utf-8")

    with pytest.raises(ProposalError, match="could not be read as a migration"):
        service.accept(drafted.proposal_id)


def test_two_content_files_sharing_a_leaf_name_do_not_collide(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-2(a): `alpha/notes.md` and `beta/notes.md` are two bodies, not one.

    A flat, leaf-named layout found one file for both, consumed the first, and
    raised a bare `FileNotFoundError` on the second -- leaving an orphan in
    `knowledge/` and a half-consumed proposal. The mirrored sub-path layout
    finds each by its full relative path.
    """
    drafted = service.draft(_request())
    revisions = (
        ("alpha", "01K9AAAAAA0000000000000001", "A\n"),
        ("beta", "01K9AAAAAA0000000000000002", "B\n"),
    )
    migration = _hand_authored_two_body_migration(drafted.migration_id.value, revisions)
    drafted.migration_file.write_text(migration, encoding="utf-8")
    drafted.body_file.unlink()
    # Both bodies are named `notes.md` -- the collision -- and told apart only by
    # their subdirectory, which is what a leaf lookup threw away.
    for namespace, _revision_id, text in revisions:
        body = drafted.directory / namespace / "notes.md"
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(text, encoding="utf-8")

    accepted = service.accept(drafted.proposal_id)

    assert {m.destination.parent.name for m in accepted.bodies} == {"alpha", "beta"}
    assert {m.destination.name for m in accepted.bodies} == {"notes.md"}
    assert (paths.knowledge / "alpha" / "notes.md").read_text() == "A\n"
    assert (paths.knowledge / "beta" / "notes.md").read_text() == "B\n"


def test_a_failing_body_write_rolls_the_migration_set_back(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HIGH-2: a mid-move failure leaves knowledge/ and migrations/ as they were.

    A partial move used to strand an orphan body in `knowledge/` and, on the
    O_EXCL migration refusal, leave a wrong-body first migration. The commit is
    staged and rolled back now, so a failure part way through the writes lands
    nothing.
    """
    from theurian.application import proposal_service as module

    drafted = service.draft(_request())
    before = _tree(paths.root)

    real_write = module._write_file
    calls = {"n": 0}

    def failing_write(destination: Path, data: bytes, *, exclusive: bool) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            real_write(destination, data, exclusive=exclusive)  # body lands
            raise OSError("disk full")
        real_write(destination, data, exclusive=exclusive)

    monkeypatch.setattr(module, "_write_file", failing_write)

    with pytest.raises(ProposalError, match="could not write"):
        service.accept(drafted.proposal_id)

    assert _tree(paths.root) == before, "a failed accept leaves the tree exactly as it was"


# -- accept validates before it moves (ADR-0027 decision 2) -----------------
#
# The three faces #307 demonstrated, and the recovery property the decision buys:
# a proposal survives its own rejection, because nothing was consumed. Each test
# asserts both halves, because an assertion on the refusal alone passes against a
# version that refuses *after* deleting the sources -- which is the shape #307
# reported, and which was run here as a mutation: with the pre-check moved below
# the move, all three keep raising and all three go RED on the second half.
#
# The racing face lives in `test_propose_cli.py`, where `migrate apply` can say
# whether the set left behind really applies.


def _contents(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root``, keyed by its project-relative path.

    Bytes, not the path set :func:`_tree` returns: a version that rewrote a file
    in place -- or deleted one and wrote another under the same name -- leaves the
    path set unchanged. No digest is taken either, so nothing here can agree with
    a broken :class:`ContentHash`; the comparison is byte equality.
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_two_operations_naming_one_body_are_refused_with_the_proposal_intact(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """#307 face 1: a breakage contained entirely inside one proposal.

    A pair of ``upsertRevision`` operations naming one ``contentFile`` involves
    no landed migration at all, so
    :meth:`_refuse_if_a_replacement_breaks_an_existing_pin` -- which judges the
    *landed* set, and which the sibling tests above cover -- has nothing to say
    about it. Before ADR-0027 decision 2 the pair landed, ``migrate validate``
    then refused the whole project at exit 4, and the proposal that produced it
    had already been deleted.

    ``refuse_duplicate_content_files`` now refuses it before anything moves, and
    the replay reaches that guard *twice*: once as one of the whole-set guards in
    stage 3, and again inside ``MigrationEngine.apply`` in stage 4. Measured --
    deleting the stage-3 call alone leaves this green, because the engine's own
    copy still refuses; deleting both is what turns it RED. So this pins the
    pipeline's answer rather than one call site's, which is what ADR-0027's
    closure argument is about.

    The second assertion is the one an exit-code check cannot make and the one
    #307 asked for: **the proposal directory is byte-identical afterwards**, so
    the author still has the sources to correct rather than re-draft.
    """
    drafted = service.draft(_request())
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    operations = document["operations"]
    assert isinstance(operations, list)
    upsert = next(op for op in operations if op["op"] == "upsertRevision")
    # A second, genuinely different revision on a second, genuinely created item
    # -- so the shared `contentFile` is the only thing wrong with the document.
    operations.append(
        {
            "op": "createItem",
            "itemId": "architecture.twin-policy",
            "kind": "architecture",
            "namespace": "architecture",
            "owner": "platform-team",
        }
    )
    operations.append(
        {
            **upsert,
            "itemId": "architecture.twin-policy",
            "revisionId": "01K9AAAAAA0000000000000009",
        }
    )
    drafted.migration_file.write_text(yaml.safe_dump(document), encoding="utf-8")
    before = _contents(drafted.directory)

    with pytest.raises(ProposalError, match="same body file on disk"):
        service.accept(drafted.proposal_id)

    assert _contents(drafted.directory) == before, "the refused proposal must survive intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"
    assert not drafted.body_destination.exists()


# -- #306: the operation-count cap and the shared-body dedup ---------------


def test_accept_refuses_a_proposal_past_the_operation_cap(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-5 (#306): past ``MAX_UPSERT_OPERATIONS``, ``accept`` refuses before any body is read.

    ``_body_moves`` is monkeypatched to raise the instant it is *called* --
    not merely when its generator is driven -- so this proves the cap fires
    before line 869's ``moves = tuple(self._body_moves(...))``, not only that
    the migration is eventually refused. The padding operations carry no
    ``contentFile`` at all, which is itself part of the proof: nothing about
    them needs to resolve to a real file, because the cap has to refuse before
    anything tries to.
    """
    drafted = service.draft(_request())
    document = _document(drafted.migration_file)
    operations = document["operations"]
    assert isinstance(operations, list)
    padding = [{"op": "noop", "n": i} for i in range(MAX_UPSERT_OPERATIONS + 1 - len(operations))]
    padded = {**document, "operations": [*operations, *padding]}
    drafted.migration_file.write_text(yaml.safe_dump(padded, sort_keys=False), encoding="utf-8")
    before = _contents(drafted.directory)

    def _must_not_run(
        _self: ProposalService, _directory: Path, _document: Mapping[str, object]
    ) -> NoReturn:
        raise AssertionError("_body_moves ran after the operation cap should have refused")

    monkeypatch.setattr(ProposalService, "_body_moves", _must_not_run)

    with pytest.raises(ProposalError, match="operations") as caught:
        service.accept(drafted.proposal_id)

    assert str(MAX_UPSERT_OPERATIONS) in str(caught.value)
    assert caught.value.remedy, "the refusal must name a remedy"
    assert _contents(drafted.directory) == before, "the refused proposal must survive intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"


def test_accept_reads_a_shared_content_file_once(service: ProposalService) -> None:
    """AC-6 (#306): N operations naming ONE ``contentFile`` share a single resident read.

    Drives ``_body_moves`` directly at a count well above
    ``MAX_UPSERT_OPERATIONS`` -- the dedup this pins is a property of
    ``_body_moves`` itself, independent of the operation-count cap AC-5 pins,
    so calling it directly (bypassing ``accept``'s cap) proves the mechanism
    holds on its own rather than being confused for the cap's effect.
    """
    drafted = service.draft(_request())
    document = _document(drafted.migration_file)
    operations = document["operations"]
    assert isinstance(operations, list)
    base = _upsert(drafted.migration_file)
    extra = MAX_UPSERT_OPERATIONS + 250
    clones = [dict(base) for _ in range(extra)]
    padded = {**document, "operations": [*operations, *clones]}

    moves = list(service._body_moves(drafted.directory, padded))

    upsert_moves = [move for move in moves if move.destination == drafted.body_destination]
    assert len(upsert_moves) == extra + 1, "one _BodyMove per operation naming the body"
    distinct_reads = {id(move.data) for move in upsert_moves}
    assert len(distinct_reads) == 1, "every operation naming one contentFile must share one read"
    assert upsert_moves[0].data == BODY.encode("utf-8")


def test_body_moves_dedups_two_content_files_sharing_one_inode(service: ProposalService) -> None:
    """FIX 2 (#306): two DIFFERENT ``contentFile`` values reaching one physical
    file share the single read AC-6 pins for one *repeated* value.

    Before this fix ``_body_moves``'s dedup cache was keyed on the resolved
    source path *string*. Two ``contentFile`` entries naming the same inode
    through two different spellings -- APFS/NTFS case-folding is the class
    the review measured (verified on Darwin) -- each resolve to a *different*
    string and so each missed the cache, holding two resident copies of one
    physical file. A hardlink reproduces the identical class -- one inode,
    two path strings -- without depending on filesystem case-folding, so this
    proof holds on every platform the CI matrix runs. Keying the cache on
    ``(st_dev, st_ino)`` instead collapses both onto the single read the
    first triggers.
    """
    drafted = service.draft(_request())
    document = _document(drafted.migration_file)
    operations = document["operations"]
    assert isinstance(operations, list)
    base = _upsert(drafted.migration_file)

    alias_source = drafted.body_file.parent / "alias.md"
    os.link(drafted.body_file, alias_source)
    assert alias_source.stat().st_ino == drafted.body_file.stat().st_ino, (
        "the hardlink must share the inode"
    )
    alias_content_file = drafted.content_file.rsplit("/", 1)[0] + "/alias.md"
    assert alias_content_file != drafted.content_file

    clone = dict(base)
    clone["contentFile"] = alias_content_file
    padded = {**document, "operations": [*operations, clone]}

    moves = list(service._body_moves(drafted.directory, padded))

    assert len(moves) == 2, "one _BodyMove per operation, even though they share an inode"
    destinations = {move.destination for move in moves}
    assert destinations == {drafted.body_destination, drafted.body_destination.parent / "alias.md"}
    assert moves[0].data is moves[1].data, (
        "two content files naming one inode must share the one resident read"
    )
    assert moves[0].data == BODY.encode("utf-8")


def test_the_operation_cap_boundary_is_inclusive() -> None:
    """FIX 3 (#306): pins the ``<=`` boundary AT the constant, independent of AC-5.

    AC-5 (``test_accept_refuses_a_proposal_past_the_operation_cap``) drives the
    cap through ``accept()`` with a document one operation over the limit --
    it proves the cap fires before ``_body_moves`` runs, but it does not by
    itself distinguish ``<=`` from ``<``: a mutant narrowing the check to
    ``<`` (an effective cap one below the constant) still refuses "one over"
    and AC-5 stays green. This calls ``_refuse_past_the_operation_cap``
    directly at the exact boundary on both sides: a document of exactly
    ``MAX_UPSERT_OPERATIONS`` operations must NOT be refused by the cap (it
    may still be refused later, by schema or replay, for reasons this test
    does not touch), and one operation past it MUST be.
    """
    at_cap = {"operations": [{"op": "noop"}] * MAX_UPSERT_OPERATIONS}

    _refuse_past_the_operation_cap(at_cap)  # must not raise

    past_cap = {"operations": [{"op": "noop"}] * (MAX_UPSERT_OPERATIONS + 1)}
    with pytest.raises(ProposalError, match=str(MAX_UPSERT_OPERATIONS)) as caught:
        _refuse_past_the_operation_cap(past_cap)
    assert caught.value.remedy, "the refusal must name a remedy"


def test_the_operation_cap_holds_the_two_channel_memory_ceiling() -> None:
    """FIX 3 amendment (#306): pins the cap VALUE, not only the ``<=`` logic above.

    The boundary test pins the comparison operator but not the constant
    itself: with ``MAX_UPSERT_OPERATIONS`` loosened to, say, 5,000, that test
    would still pass at 5,000/5,001 -- it has no opinion on what the number
    should *be*. The real cost claim -- the two-channel peak
    ``_commit`` can hold resident (the incoming ``moves`` bytes and, for every
    replaced body, the ``restored`` destination bytes kept for rollback; see
    ``MAX_UPSERT_OPERATIONS``'s docstring) -- is only proven at real memory
    scale by the e2e ``AC-7`` proof, which CI's ``-m "not e2e"`` jobs exclude.
    This recomputes that same bound from the LIVE constants and pins it
    against the 4 GiB ceiling the constant's own docstring targets, so a
    constant change that breaks the ceiling goes red here, in the CI-run
    suite, rather than only in the excluded e2e job.
    """
    two_channel_peak_bytes = MAX_UPSERT_OPERATIONS * 2 * MAX_SOURCE_FILE_BYTES
    four_gib = 4 * 1024**3
    assert two_channel_peak_bytes <= four_gib, (
        f"MAX_UPSERT_OPERATIONS={MAX_UPSERT_OPERATIONS} admits a two-channel peak of "
        f"{two_channel_peak_bytes} bytes (moves + restored, each up to "
        f"MAX_SOURCE_FILE_BYTES={MAX_SOURCE_FILE_BYTES} per operation), past the "
        f"{four_gib}-byte ceiling MAX_UPSERT_OPERATIONS's own docstring claims"
    )


def test_accept_refuses_an_oversized_replaced_destination_without_reading_it_whole(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """#400: the per-entry face of #306's class -- ``_commit``'s restored read was raw.

    #306 bounds how many operations one migration may declare, not the size of
    any *one* destination a replace operation overwrites. A body committed
    directly to ``.theurian/knowledge/`` -- exactly what a ``git clone``
    delivers -- can be arbitrarily large: landing it through Git never routes
    it through ``MAX_SOURCE_FILE_BYTES``. Before this fix, ``_commit`` read
    such a destination with a raw ``Path.read_bytes()`` to build the rollback
    snapshot, so a proposal whose own incoming body is small and otherwise
    valid still forced the whole of an oversized *committed* destination
    resident -- one replace operation, independent of the operation-count cap
    entirely.

    The destination is planted just over the cap (not gigabytes), so the test
    stays fast; what this pins is that the refusal fires at all -- SEC-8's
    check reads ``stat().st_size`` before any byte of the file is read
    (:func:`~theurian.security.paths.read_source_file`), so a refusal here
    proves the oversized bytes were never read into memory, not merely that
    they were read and then rejected.
    """
    drafted = service.draft(_request())
    drafted.body_destination.parent.mkdir(parents=True, exist_ok=True)
    oversized = b"x" * (MAX_SOURCE_FILE_BYTES + 1024 * 1024)  # ~1 MiB over SEC-8's cap
    drafted.body_destination.write_bytes(oversized)

    with pytest.raises(InputTooLargeError, match="source file size") as caught:
        service.accept(drafted.proposal_id)

    assert caught.value.remedy, "the refusal must name a remedy"
    # Refused before anything moved: the committed destination is untouched,
    # the proposal's own sources survive, and nothing landed in migrations/.
    assert drafted.body_destination.read_bytes() == oversized
    assert drafted.migration_file.exists(), "a refused accept leaves the proposal intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"


def test_a_mid_loop_oversized_replacement_rolls_back_an_earlier_write_too(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """#400: the new size-cap raise rolls back like any other mid-commit failure.

    Two replace operations in one migration, processed in document order: the
    first destination is small and legitimate, so ``_commit`` writes it and
    stages its prior bytes in ``restored``; the second destination is the
    oversized committed body the size cap must refuse. The refusal fires while
    reading the *second* move -- :meth:`ProposalService._commit`'s rollback
    must still undo the *first* move's already-landed write, or its own
    "either everything lands or nothing changed" promise would be broken by
    the very fault this cap exists to catch. Modelled on
    ``test_two_content_files_sharing_a_leaf_name_do_not_collide`` for the
    two-body layout.
    """
    drafted = service.draft(_request())
    revisions = (
        ("alpha", "01K9AAAAAA0000000000000001", "A\n"),
        ("beta", "01K9AAAAAA0000000000000002", "B\n"),
    )
    migration = _hand_authored_two_body_migration(drafted.migration_id.value, revisions)
    drafted.migration_file.write_text(migration, encoding="utf-8")
    drafted.body_file.unlink()
    for namespace, _revision_id, text in revisions:
        body = drafted.directory / namespace / "notes.md"
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(text, encoding="utf-8")

    # Alpha (processed first) already sits at a small, legitimate destination;
    # beta (processed second) sits at an oversized one -- the committed body
    # the size cap must refuse before it is read.
    alpha_destination = paths.knowledge / "alpha" / "notes.md"
    beta_destination = paths.knowledge / "beta" / "notes.md"
    alpha_destination.parent.mkdir(parents=True, exist_ok=True)
    beta_destination.parent.mkdir(parents=True, exist_ok=True)
    alpha_destination.write_text("stale alpha\n", encoding="utf-8")
    oversized = b"x" * (MAX_SOURCE_FILE_BYTES + 1024 * 1024)
    beta_destination.write_bytes(oversized)

    with pytest.raises(InputTooLargeError):
        service.accept(drafted.proposal_id)

    assert alpha_destination.read_text() == "stale alpha\n", (
        "the earlier move's write must be rolled back, not left holding the incoming body"
    )
    assert beta_destination.read_bytes() == oversized, "the refused destination is untouched"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"


def test_a_pin_that_does_not_match_its_own_body_is_refused_with_the_proposal_intact(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """#307 face 2: the migration pins a digest its own body no longer has.

    The authoring slip is editing the body after the migration was written. The
    loader verifies a declared pin every time it re-reads a referenced body, and
    the pre-check's replay is what puts the incoming proposal through that loader
    (stage 2) -- before ADR-0027 decision 2 nothing on the accept path re-read the
    body at all, so the pair landed and ``migrate validate`` refused the project
    afterwards with the proposal gone.
    """
    drafted = service.draft(_request())
    drafted.body_file.write_text(BODY + "\nAppended after the migration was written.\n")
    before = _contents(drafted.directory)

    with pytest.raises(ProposalError, match="but the migration pins") as caught:
        service.accept(drafted.proposal_id)

    assert _contents(drafted.directory) == before, "the refused proposal must survive intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"
    # The refusal comes from inside the replay, whose loader read a copy under a
    # temporary directory. What reaches the author must name their own file and
    # not that copy, or the remedy points at a path that no longer exists.
    assert drafted.migration_file.name in str(caught.value)
    assert "theurian-rehearsal" not in f"{caught.value} {caught.value.remedy}"


def test_an_empty_content_file_is_refused_with_the_proposal_intact(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """#307 face 3: ``contentFile: ""`` under a schema ``minLength`` of 1.

    The empty value never reaches the body moves -- ``_upsert_bodies`` skips an
    operation whose ``contentFile`` is empty, so ``accept`` had nothing to move
    and moved the migration alone. What refuses it is stage 1, the published
    schema, run against the proposal's *own* document rather than the replay's
    copy so that the message names the file the author has to correct.
    """
    drafted = service.draft(_request())
    text = drafted.migration_file.read_text(encoding="utf-8")
    poisoned = text.replace(f"contentFile: {drafted.content_file}", 'contentFile: ""')
    assert poisoned != text, "the contentFile anchor did not match"
    drafted.migration_file.write_text(poisoned, encoding="utf-8")
    before = _contents(drafted.directory)

    with pytest.raises(ProposalError, match="is not a valid migration") as caught:
        service.accept(drafted.proposal_id)

    assert _contents(drafted.directory) == before, "the refused proposal must survive intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"
    assert drafted.migration_file.name in str(caught.value)
    assert "Nothing has moved" in caught.value.remedy


def test_a_refused_acceptance_modifies_no_file_anywhere_in_the_project(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """ADR-0027 decision 2's last owed compliance item, over the whole tree.

    The replay stages the union of the landed set and the incoming proposal on
    disk and applies it into a database, and it must do all of that outside the
    project. Scoped to every file under the root and to their *bytes* -- the
    shape ``test_generation_modifies_no_file_outside_the_proposal_directory``
    uses for ``draft`` -- because a replay that wrote into ``.theurian/state/``
    or over a landed body would leave the path set unchanged.

    Driven by the racing refusal deliberately: it is the one that runs every
    stage, so the replay had loaded the set, created its database and begun
    applying before it refused. A refusal at stage 1 would prove far less.

    The landed migration is placed **by hand rather than accepted**, and that is
    load-bearing rather than convenience: an earlier ``accept`` would have run a
    replay of its own, so anything that replay wrote into the project would
    already be in the baseline, and a leak that writes the same path on every run
    would be absorbed instead of caught. Measured -- against a version whose
    replay wrote one fixed file into the project root, the accepted-first shape
    stayed green.
    """
    first = service.draft(_request())
    second = service.draft(_request(body="# Retry policy\n\nFive attempts.\n"))
    shutil.copy(first.migration_file, paths.migrations / first.migration_file.name)
    first.body_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(first.body_file, first.body_destination)
    shutil.rmtree(first.directory)
    before = _contents(paths.root)

    with pytest.raises(ProposalError, match="Revision conflict"):
        service.accept(second.proposal_id)

    assert _contents(paths.root) == before, "a refused acceptance wrote somewhere in the project"


def test_the_replay_removes_the_throwaway_tree_it_staged_the_union_in(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The replay's target is throwaway, and a leak of it is a leak of knowledge.

    What is staged there is a copy of every landed body plus the incoming one, so
    a target left behind is the project's knowledge sitting in a temporary root
    -- not a stray empty directory. ``tempfile.tempdir`` is redirected so this can
    name the directory the rehearsal chose rather than trust the stdlib's
    default, and ``_materialize`` is wrapped so the emptiness assertion cannot
    pass against a run that never staged anything at all.
    """
    from theurian.cli import migration_pipeline as pipeline

    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    staged: list[Path] = []
    real_materialize = pipeline._materialize

    def spy(candidate: CandidateMigrationSet, target: Path) -> Path:
        staged.append(target)
        return real_materialize(candidate, target)

    monkeypatch.setattr(pipeline, "_materialize", spy)
    first = service.draft(_request())
    second = service.draft(_request(body="# Retry policy\n\nFive attempts.\n"))
    # Landed by hand, as in the test above, so every target `staged` records
    # belongs to the refused acceptance under test and to nothing before it.
    shutil.copy(first.migration_file, paths.migrations / first.migration_file.name)
    first.body_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(first.body_file, first.body_destination)
    shutil.rmtree(first.directory)

    with pytest.raises(ProposalError, match="Revision conflict"):
        service.accept(second.proposal_id)

    assert staged, "the replay staged nothing: the assertions below would prove nothing"
    assert all(path.is_relative_to(system_temp) for path in staged), staged
    assert not [path for path in staged if path.exists()], "a replay target was left behind"
    assert list(system_temp.iterdir()) == [], "the replay left residue in the temporary root"


# -- stage 1's second half, and the faults it must not claim -----------------
#
# Both guards below are ones no real input reaches, which on this project is the
# shape that survives its own deletion. Each is driven synthetically instead.


def test_a_build_that_disagrees_with_the_schema_about_apiversion_refuses_the_proposal(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 1's second half: the ``apiVersion`` this *build* understands.

    Not the schema's ``const`` written twice. The schema is a file located at
    runtime and :data:`MIGRATION_API_VERSION` is compiled into the build, so a
    document declaring an ``apiVersion`` the schema rejects never gets here -- the
    ``const`` refuses it first, and the branch is reachable only when the two
    disagree. That is why it is driven by moving the constant rather than by
    editing the document: the proposal is drafted against today's pair, and the
    build's answer is then changed underneath it, which is exactly the
    installed-schema-versus-installed-code skew the check exists for.

    The direction matters as much as the refusal. This is a plain
    :class:`ProposalError` and not an :class:`ApprovedSetUnusableError`, so
    ``propose accept`` reports it on the code that means *this proposal could not
    be used as it stands* rather than the one that means *read the knowledge
    state* -- and the proposal survives, so correcting it is possible at all.
    """
    from theurian.application import proposal_service as module

    drafted = service.draft(_request())
    before = _contents(drafted.directory)
    monkeypatch.setattr(module, "MIGRATION_API_VERSION", "theurian.dev/v2")

    with pytest.raises(ProposalError, match="apiVersion") as caught:
        service.accept(drafted.proposal_id)

    assert not isinstance(caught.value, ApprovedSetUnusableError), (
        "the proposal's own apiVersion is the proposal's fault, not the landed set's"
    )
    assert not isinstance(caught.value, ChangeAlreadyInPlaceError)
    assert drafted.migration_file.name in str(caught.value)
    assert "theurian.dev/v2" in str(caught.value), "the reader is told what this build reads"
    assert "Nothing has moved" in caught.value.remedy
    assert _contents(drafted.directory) == before, "the refused proposal must survive intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"


@pytest.mark.parametrize("broken_stage", ["the schema check", "the replay"])
def test_an_unreadable_installed_schema_is_not_reported_as_the_proposals_fault(
    service: ProposalService,
    paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    broken_stage: str,
) -> None:
    """A broken installation is not something the author of a proposal can fix.

    The pre-check reads the published JSON Schemas at two points -- stage 1
    against the proposal's own document, and again inside the replay when the
    loader reads the staged copy -- and each is wrapped in a clause that
    translates a :class:`TheurianError` into a :class:`ProposalError` naming the
    proposal. :class:`SchemaUnreadableError` is a ``TheurianError``, so without an
    explicit re-raise ahead of those clauses it would arrive as *"correct the
    migration in the proposal directory"* for a file that is perfectly correct;
    what it needs to say is *reinstall theurian* (#205, #227). ``propose accept``
    routes on exactly that distinction: :class:`ProposalError` takes exit 1 with a
    remedy about the proposal, and everything else keeps its own remedy.

    Driven by a real unreadable schema rather than a raised stand-in: the
    published schemas are copied and ``migration.schema.json`` emptied, so
    ``json.loads`` fails inside the loader's own validator construction and the
    error is the one the product raises. Both points are exercised, because a
    re-raise deleted at one of them is invisible from the other.
    """
    from theurian.cli import migration_pipeline as pipeline

    drafted = service.draft(_request())
    broken = tmp_path / "broken-schemas"
    shutil.copytree(SCHEMAS, broken)
    (broken / "migrations" / "migration.schema.json").write_text("", encoding="utf-8")
    if broken_stage == "the schema check":
        # The injected schema check, pointed at the broken installation -- the
        # same dependency `cli/propose_commands.py::_service` builds from
        # `schema_root()`, wired to a root whose schema cannot be read.
        monkeypatch.setattr(
            service, "_validate", lambda document: validate_migration_document(document, broken)
        )
    else:
        monkeypatch.setattr(pipeline, "schema_root", lambda: broken)
    before = _contents(drafted.directory)

    with pytest.raises(SchemaUnreadableError) as caught:
        service.accept(drafted.proposal_id)

    # Not a `ProposalError`, which is what keeps it off exit 1's "correct your
    # proposal" branch, and its own remedy is what the caller then publishes.
    assert not isinstance(caught.value, ProposalError)
    assert "Reinstall theurian" in caught.value.remedy, caught.value.remedy
    assert _contents(drafted.directory) == before, "the refused proposal must survive intact"
    assert not list(paths.migrations.glob("*.yaml")), "no migration may land from a refusal"
