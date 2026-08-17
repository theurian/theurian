"""Packaging and accepting a proposal (ADR-0013 §4).

The service is the half both composition roots share: the CLI drives it today
and Milestone 7's write-intent MCP tools drive the same calls. These tests use
it directly, so a defect is located in the packaging rather than in Typer.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    ProposalError,
    ProposalRequest,
    ProposalService,
)
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import InvariantViolationError, MigrationError, PathEscapeError
from theurian.domain.identifiers import AgentId, ItemId, ProposalId, RevisionId, TaskId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.values import JSON, MARKDOWN, YAML
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)

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
    # The current-revision lookup reads the project's *approved* migrations, so a
    # second draft for an item whose first proposal has been accepted sees it as
    # existing -- which is what exercises the #210 update guard end to end.
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    return ProposalService(
        paths=paths,
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=_validator,
        current_revision=current_revision,
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
    """A migration naming two bodies whose leaf names collide (`notes.<rev>.md`).

    Only what ``accept`` reads -- the ``contentFile`` of each operation -- has to
    be present; ``accept`` does not validate, so the metadata a real migration
    carries is left out on purpose.
    """
    return (
        "apiVersion: theurian.dev/v1\n"
        f"id: {migration_id}\n"
        "createdAt: '2026-08-02T12:00:00+00:00'\n"
        "author: a@example.com\n"
        "operations:\n"
        "- op: upsertRevision\n"
        "  itemId: alpha.notes\n"
        f"  revisionId: {rev_a}\n"
        f"  contentFile: ../knowledge/alpha/notes.{rev_a}.md\n"
        "- op: upsertRevision\n"
        "  itemId: beta.notes\n"
        f"  revisionId: {rev_b}\n"
        f"  contentFile: ../knowledge/beta/notes.{rev_b}.md\n"
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

    with pytest.raises(ProposalError, match="pins"):
        service.accept(second.proposal_id)

    # The first body is untouched and the whole set still loads.
    assert first.body_destination.read_text() == BODY
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


def test_a_yaml_body_can_be_drafted_and_accepted(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """HIGH-3: a YAML body's file is also a `*.yaml`, and used to look like a migration.

    Globbing `*.yaml` for the migration counted the body, so `accept` reported
    "two or more migration files" and a YAML-bodied proposal could never be
    accepted. The migration is identified by its `<ulid>-<slug>.yaml` name now,
    which a `<leaf>.<revision>.yaml` body does not match.
    """
    drafted = service.draft(
        _request(item_id=ItemId("api.limits"), body="max: 3\n", content_type=YAML)
    )
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
    for namespace, rev, text in (("alpha", rev_a, "A\n"), ("beta", rev_b, "B\n")):
        body = drafted.directory / namespace / f"notes.{rev}.md"
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(text, encoding="utf-8")

    accepted = service.accept(drafted.proposal_id)

    assert {m.destination.parent.name for m in accepted.bodies} == {"alpha", "beta"}
    assert (paths.knowledge / "alpha" / f"notes.{rev_a}.md").read_text() == "A\n"
    assert (paths.knowledge / "beta" / f"notes.{rev_b}.md").read_text() == "B\n"


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
