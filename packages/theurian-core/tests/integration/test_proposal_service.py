"""Packaging and accepting a proposal (ADR-0013 §4).

The service is the half both composition roots share: the CLI drives it today
and Milestone 7's write-intent MCP tools drive the same calls. These tests use
it directly, so a defect is located in the packaging rather than in Typer.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    ChangeAlreadyInPlaceError,
    DraftedProposal,
    ProposalAlreadyAcceptedError,
    ProposalError,
    ProposalRequest,
    ProposalService,
)
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import InvariantViolationError, MigrationError, PathEscapeError
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProposalId, RevisionId, TaskId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.values import JSON, MARKDOWN, YAML
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

#: A ``chmod 0o000`` denies nothing to root and nothing on Windows, so a test
#: that needs the mode to actually refuse cannot run there (the offline CI job
#: runs as root). Same guard the sibling permission tests carry.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

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
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=_validator,
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
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


def _hand_authored_two_body_migration(migration_id: str, rev_a: str, rev_b: str) -> str:
    """A migration naming two bodies whose leaf names collide (both `notes.md`).

    The two ``contentFile`` paths differ only in namespace -- ``alpha/notes.md``
    and ``beta/notes.md`` -- so they share the leaf ``notes.md``. That is the
    exact shape a leaf-name lookup conflated: it found one file for both. The
    revisions still differ (``rev_a`` / ``rev_b``), so the two are genuinely two
    changes, not one written twice.

    Only what ``accept`` reads -- the ``contentFile`` of each operation and the
    ``id`` -- has to be present; ``accept`` does not validate, so the metadata a
    real migration carries is left out on purpose. The ``id`` is quoted because
    the seeded generator's ULIDs are all digits, which YAML would otherwise
    coerce to an int (a real ULID contains letters and needs no quoting).
    """
    return (
        "apiVersion: theurian.dev/v1\n"
        f"id: '{migration_id}'\n"
        "createdAt: '2026-08-02T12:00:00+00:00'\n"
        "author: a@example.com\n"
        "operations:\n"
        "- op: upsertRevision\n"
        "  itemId: alpha.notes\n"
        f"  revisionId: {rev_a}\n"
        "  contentFile: ../knowledge/alpha/notes.md\n"
        "- op: upsertRevision\n"
        "  itemId: beta.notes\n"
        f"  revisionId: {rev_b}\n"
        "  contentFile: ../knowledge/beta/notes.md\n"
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


# -- generation ------------------------------------------------------------


def test_generation_writes_only_under_the_proposal_directory(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """ADR-0013's first owed compliance item, checked over the whole tree.

    Scoped to files rather than to the three names the generator writes: the
    property ADR-0013 states is about everything a write-intent path may touch,
    and a version that wrote the body straight into ``.theurian/knowledge/``
    would satisfy any assertion phrased over the proposal directory alone.
    """
    before = _tree(paths.root)

    drafted = service.draft(_request())

    written = _tree(paths.root) - before
    directory = drafted.directory.relative_to(paths.root).as_posix()

    assert written, "the draft wrote nothing at all"
    assert all(path.startswith(f"{directory}/") for path in written), written
    assert directory.startswith(".theurian/proposals/")


def test_generation_modifies_no_file_outside_the_proposal_directory(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A new-file diff cannot see a *modified* existing file (adversarial b1).

    ``_tree`` returns the set of paths, so a draft that overwrote a file already
    present -- a knowledge body, another proposal's migration -- would leave the
    set unchanged and pass the test above. This snapshots content, so a
    modification outside the proposal directory is caught even when no path is
    added or removed.
    """
    seeded = paths.knowledge / "architecture" / "retry-policy.md"
    seeded.parent.mkdir(parents=True, exist_ok=True)
    seeded.write_text("pre-existing, must be untouched\n", encoding="utf-8")
    outside = {
        path: path.read_bytes()
        for path in paths.root.rglob("*")
        if path.is_file() and ".theurian/proposals/" not in path.relative_to(paths.root).as_posix()
    }

    service.draft(_request())

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
    """m05: the O_EXCL create, not the pre-check, is the real race guard.

    ``test_accept_refuses_to_land_a_migration_on_an_existing_name`` is caught by
    the pre-check that runs before any write. That leaves the O_EXCL create --
    the guard for a name that appears in the window *after* the pre-check --
    unexercised, so a mutation to ``O_TRUNC`` survives it. Here the pre-check is
    neutered so the create is what has to refuse, and the bytes already at the
    name must survive.
    """
    drafted = service.draft(_request())
    landed = paths.migrations / drafted.migration_file.name

    def _no_precheck(_self: ProposalService, _destination: Path) -> None:
        return None

    monkeypatch.setattr(ProposalService, "_refuse_if_migration_present", _no_precheck)
    landed.write_text("EXISTING\n", encoding="utf-8")

    with pytest.raises(ProposalError, match="appeared"):
        service.accept(drafted.proposal_id)

    assert landed.read_text() == "EXISTING\n", "O_EXCL must not overwrite the existing migration"
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


def test_accept_refuses_replacing_an_unpinned_landed_body(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-C(i): an *unpinned* landed body still backs a revision the set validates.

    ``contentSha256`` is optional -- the loader adopts the body's current hash
    where it is absent (#210) -- so a landed migration may reference a body it
    does not freeze. The old guard filtered on ``content_pinned`` and so waved
    such a replacement through, yet the *set* then holds two distinct revisions
    on one physical file, which ``refuse_duplicate_content_files`` refuses for the
    whole project (exit 4). The identity key carries no pinning filter, so the
    already-claimed inode is refused whether or not the claim was frozen.
    Reproduced by the orchestrator (``probes/e8``: accept exit 0 -> validate exit 4).
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # Strip the declared pin from the landed migration: the body is now
    # referenced but not frozen -- the case the `content_pinned` filter missed.
    landed = next(paths.migrations.glob(f"{first.migration_id.value}-*.yaml"))
    landed.write_text(
        "\n".join(
            line
            for line in landed.read_text(encoding="utf-8").splitlines()
            if "contentSha256" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    assert "contentSha256" not in landed.read_text(encoding="utf-8")
    # An unpinned reference is legal: the set still loads and would validate.
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
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
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
    """HIGH-C(ii): "byte-identical changes nothing" is false of the *set*.

    The old guard refused only ``pin == current and replacement != current``, so
    a byte-identical replacement of a pinned body passed its second conjunct and
    landed. The bytes never change, but the set afterwards has two revisions on
    one body file (``refuse_duplicate_content_files``, exit 4). The identity key
    refuses on the shared inode, not on a byte comparison, so it catches this.
    Reproduced by the orchestrator (``probes/e9``: accept exit 0 -> validate exit 4).
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # A second item whose body is byte-identical to the first's, repointed at the
    # first's landed path.
    second = service.draft(_request(item_id=ItemId("architecture.other"), body=BODY))
    second.migration_file.write_text(
        second.migration_file.read_text(encoding="utf-8").replace(
            second.content_file, first.content_file
        ),
        encoding="utf-8",
    )
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(second.body_file.read_bytes())

    with pytest.raises(ProposalError, match="backing a landed revision"):
        service.accept(second.proposal_id)

    assert first.body_destination.read_text() == BODY, "the landed body is untouched"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 1


def test_accept_allows_the_same_revision_re_declared_against_its_own_body(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The one landed reference the identity guard must *not* refuse.

    Re-declaring one revision against its own body is how an in-place status
    change is written -- the revision id does not move, ``append_revision`` is a
    no-op, only ``status`` differs (the ``reject``/``inplace-draft`` faces,
    ADR-0024 decision 5). It lands on the same body path (the path carries the
    revision id), so it *is* a replacement -- but of a body its own revision
    already reads, not a second one. The guard keys on the *pair* (identity,
    other revision id) for exactly this: an equal revision id is skipped, so the
    legitimate re-declare is allowed while every different-revision face above is
    refused. Without the skip the guard would refuse this too, breaking a
    supported flow; this pins that it does not.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    # A fresh migration id (so the "already in place" pre-check passes), but the
    # first item's own revision id re-declared against its own byte-identical
    # body -- what a hand-authored in-place status change looks like.
    second = service.draft(_request(item_id=ItemId("architecture.other")))
    text = second.migration_file.read_text(encoding="utf-8")
    text = text.replace(second.content_file, first.content_file)
    text = text.replace(
        f"revisionId: '{second.revision_id.value}'",
        f"revisionId: '{first.revision_id.value}'",
    )
    second.migration_file.write_text(text, encoding="utf-8")
    assert f"revisionId: '{first.revision_id.value}'" in text, "the revision id re-declare failed"
    tail = first.body_destination.resolve().relative_to(paths.knowledge.resolve())
    hand_authored = second.directory / tail
    hand_authored.parent.mkdir(parents=True, exist_ok=True)
    hand_authored.write_bytes(first.body_destination.read_bytes())

    accepted = service.accept(second.proposal_id)

    assert accepted.bodies[0].replaced, "the re-declare lands on the existing body"
    assert first.body_destination.read_text() == BODY, "the body is unchanged"
    assert len(load_migrations(paths.root, paths.migrations, SCHEMAS).migration_set) == 2


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

    ``accept`` does not schema-validate the proposal's migration, so a value the
    JSON Schema would reject -- one holding a NUL byte or an unpaired surrogate --
    reaches ``_destination_of``'s ``resolve()`` unfiltered. ``quoted_value`` is a
    YAML double-quoted scalar so its ``\\0`` / ``\\uXXXX`` escapes decode to the
    real code points.
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


def test_accept_refuses_a_symlinked_proposal_directory(
    service: ProposalService, paths: ProjectPaths, tmp_path: Path
) -> None:
    """Face B: the ULID name is safe, but not what it resolves to.

    The proposal directory itself is a symlink to an out-of-project directory
    whose `*.yaml` would otherwise be pulled onto the accept path.
    """
    drafted = service.draft(_request())
    elsewhere = tmp_path / "elsewhere"
    drafted.directory.rename(elsewhere)
    drafted.directory.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ProposalError, match="symlink"):
        service.accept(drafted.proposal_id)


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
    rev_a = "01K9AAAAAA0000000000000001"
    rev_b = "01K9AAAAAA0000000000000002"
    migration = _hand_authored_two_body_migration(drafted.migration_id.value, rev_a, rev_b)
    drafted.migration_file.write_text(migration, encoding="utf-8")
    drafted.body_file.unlink()
    # Both bodies are named `notes.md` -- the collision -- and told apart only by
    # their subdirectory, which is what a leaf lookup threw away.
    for namespace, text in (("alpha", "A\n"), ("beta", "B\n")):
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
