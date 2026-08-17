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
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.values import MARKDOWN
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
    return ProposalService(
        paths=paths,
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=_validator,
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
    """Two proposals for the same item produce two files, not one overwrite."""
    first = service.draft(_request())
    second = service.draft(_request(expected_revision=first.revision_id))

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
    """The other half of #210: an update states which revision it replaces."""
    revision = RevisionId("01K9D2G8YT6PXN0VKS4WBZ7RQM")

    drafted = service.draft(_request(expected_revision=revision))

    assert _upsert(drafted.migration_file)["expectedRevision"] == revision.value


def test_a_new_item_carries_no_expected_revision(service: ProposalService) -> None:
    """Absent means "this creates the first revision"; a value would conflict."""
    drafted = service.draft(_request())

    assert "expectedRevision" not in _upsert(drafted.migration_file)


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
    refuses to exist without anchors and reasoning, so a test that called its
    constructor would prove that one class holds the rule and say nothing about
    whether the generation path does. Bypassing it is the only way to ask the
    question this compliance item actually asks -- and the answer has to be the
    same, because ADR-0013's promise is about what gets written, not about which
    constructor a caller reached first.

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
    object.__setattr__(hollow, "anchors", ())
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


def test_accept_replaces_the_body_because_a_proposal_may_mean_to(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The permissive half of the asymmetry, on the case that still reaches it.

    ``accept`` never refuses a body: a proposal that targets a path already
    holding a file is replacing it on purpose, and refusing would make that
    unstateable. The *generated* path carries a fresh revision id, so a
    generated proposal no longer produces this collision -- what does is a
    hand-written one, which is exactly the flow ADR-0013 §4 describes and
    ``plugins/claude-code/commands/propose.md`` walks a human through.

    So the collision is built the way a hand-written proposal builds it: by
    naming a ``contentFile`` that already exists.
    """
    first = service.draft(_request())
    service.accept(first.proposal_id)
    second = service.draft(_request(body="# Retry policy\n\nFive attempts.\n"))
    second.migration_file.write_text(
        second.migration_file.read_text(encoding="utf-8").replace(
            second.content_file, first.content_file
        ),
        encoding="utf-8",
    )
    (second.directory / first.body_file.name).write_bytes(second.body_file.read_bytes())

    accepted = service.accept(second.proposal_id)

    assert accepted.bodies[0].replaced
    assert first.body_destination.read_text().endswith("Five attempts.\n")


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
