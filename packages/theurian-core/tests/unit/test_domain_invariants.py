"""Domain invariants (INV-1 .. INV-10)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from theurian.domain.enums import (
    INVERSE_RELATIONS,
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import InvalidIdentifierError, InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId, SpecId
from theurian.domain.knowledge import (
    AUTHORED_IN_THEURIAN,
    KnowledgeAlias,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.values import MARKDOWN, ContentHash, ValidityPeriod

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
PROJECT = ProjectId("backend-service")
ITEM = ItemId("architecture.auth-policy")
REVISION = RevisionId("01K1DEFREV1234567890ABCDEF")
MIGRATION = MigrationId("01K1DEFABC1234567890ABCDEF")

ANCHOR = SourceAnchor(
    provider="git",
    source_uri="git://backend-service/.theurian/knowledge/architecture/auth-policy.md",
    repository="acme/backend-service",
    commit_sha="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
    file_path=".theurian/knowledge/architecture/auth-policy.md",
    line_start=1,
    line_end=42,
)


def _metadata(**overrides: object) -> RevisionMetadata:
    base: dict[str, object] = {
        "kind": KnowledgeKind.ARCHITECTURE,
        "namespace": "backend",
        "status": KnowledgeStatus.APPROVED,
        "trust_level": TrustLevel.REVIEWED,
        "sensitivity": Sensitivity.INTERNAL,
        "owner": "platform-team",
    }
    base.update(overrides)
    return RevisionMetadata(**base)  # type: ignore[arg-type]


def _revision(**overrides: object) -> KnowledgeRevision:
    base: dict[str, object] = {
        "revision_id": REVISION,
        "item_id": ITEM,
        "project_id": PROJECT,
        "migration_id": MIGRATION,
        "title": "Authentication and authorization policy",
        "body": "All service-to-service calls carry a signed token.",
        "content_type": MARKDOWN,
        "metadata": _metadata(),
        "validity": ValidityPeriod(valid_from=NOW),
        "author": "engineer@example.com",
        "created_at": NOW,
        "source_anchors": (ANCHOR,),
    }
    base.update(overrides)
    return KnowledgeRevision.create(**base)  # type: ignore[arg-type]


# -- INV-1: revisions are immutable ---------------------------------------


def test_revision_is_frozen() -> None:
    revision = _revision()
    with pytest.raises(dataclasses.FrozenInstanceError):
        revision.body = "rewritten"  # type: ignore[misc]


def test_revision_has_no_mutating_api() -> None:
    """No method may exist that edits a revision in place.

    A citation to a revision id has to mean the same thing forever; a method
    named ``update`` or ``set_body`` would quietly break every past citation.
    """
    forbidden = {"update", "set_body", "set_metadata", "edit", "mutate"}
    assert not forbidden & set(dir(KnowledgeRevision))


# -- INV-3: content hash matches the body ---------------------------------


def test_content_hash_is_computed_from_body() -> None:
    revision = _revision(body="a specific body")
    assert revision.content_sha256 == ContentHash.of_text("a specific body")


def test_declared_hash_that_disagrees_with_body_is_rejected() -> None:
    """Guards deserialisation: a tampered stored hash must not be trusted."""
    with pytest.raises(InvariantViolationError, match="content hash mismatch"):
        KnowledgeRevision(
            revision_id=REVISION,
            item_id=ITEM,
            project_id=PROJECT,
            migration_id=MIGRATION,
            title="Title",
            body="the real body",
            content_type=MARKDOWN,
            content_sha256=ContentHash.of_text("a different body"),
            metadata=_metadata(),
            validity=ValidityPeriod(valid_from=NOW),
            author="engineer@example.com",
            created_at=NOW,
            source_anchors=(ANCHOR,),
        )


def test_content_hash_does_not_normalise_line_endings() -> None:
    """Normalisation would make one file hash differently per checkout."""
    assert ContentHash.of_text("a\r\nb") != ContentHash.of_text("a\nb")


# -- INV-8: everything is attributable ------------------------------------


def test_revision_without_anchor_or_marker_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="no source anchor"):
        _revision(source_anchors=())


def test_revision_authored_in_theurian_needs_no_anchor() -> None:
    revision = _revision(
        source_anchors=(),
        metadata=_metadata(labels=(AUTHORED_IN_THEURIAN,)),
    )
    assert revision.source_anchors == ()


# -- INV-2: the item pointer stays within the item -------------------------


def _item(**overrides: object) -> KnowledgeItem:
    base: dict[str, object] = {
        "item_id": ITEM,
        "project_id": PROJECT,
        "namespace": "backend",
        "kind": KnowledgeKind.ARCHITECTURE,
        "status": KnowledgeStatus.DRAFT,
        "current_revision_id": None,
        "owner": "platform-team",
        "trust_level": TrustLevel.UNVERIFIED,
        "sensitivity": Sensitivity.INTERNAL,
        "validity": ValidityPeriod(valid_from=NOW),
    }
    base.update(overrides)
    return KnowledgeItem(**base)  # type: ignore[arg-type]


def test_item_adopts_metadata_from_its_revision() -> None:
    updated = _item().with_revision(_revision())
    assert updated.current_revision_id == REVISION
    assert updated.status is KnowledgeStatus.APPROVED
    assert updated.trust_level is TrustLevel.REVIEWED


def test_item_rejects_a_revision_belonging_to_another_item() -> None:
    other = _revision(item_id=ItemId("domain.pricing"))
    with pytest.raises(InvariantViolationError, match="belongs to"):
        _item().with_revision(other)


def test_item_rejects_a_revision_from_another_project() -> None:
    """Cross-project pointer assignment would be a silent isolation failure."""
    other = _revision(project_id=ProjectId("other-service"))
    with pytest.raises(InvariantViolationError, match="project"):
        _item().with_revision(other)


# -- INV-4: validity windows ----------------------------------------------


def test_valid_to_must_follow_valid_from() -> None:
    with pytest.raises(InvariantViolationError, match="must be after"):
        ValidityPeriod(valid_from=NOW, valid_to=NOW - timedelta(days=1))


def test_valid_to_must_follow_valid_from_by_instant_not_by_offset() -> None:
    """#117: this guard is what a caller relies on once the SQL ``CHECK`` is gone.

    ``knowledge_revisions.CHECK (valid_to > valid_from)`` compared stored ISO
    strings as TEXT and was dropped in schema version 4 because that comparison
    disagrees with instant order across differing UTC offsets. The direction
    that matters here is the one a TEXT check gets *wrong the other way*:
    ``2031-01-01T05:00:00+09:00`` (the instant 2030-12-31T20:00Z) sorts after
    ``2031-01-01T00:00:00+00:00`` (2031-01-01T00:00Z) as a string, even though
    it names an instant four hours *earlier*. A lexicographic check would have
    let this window through; ``ValidityPeriod`` compares the aware ``datetime``s
    themselves and refuses it, which is what makes it safe for the schema to
    stop checking at all.
    """
    with pytest.raises(InvariantViolationError, match="must be after"):
        ValidityPeriod(
            valid_from=datetime.fromisoformat("2031-01-01T00:00:00+00:00"),
            valid_to=datetime.fromisoformat("2031-01-01T05:00:00+09:00"),
        )


def test_valid_to_equal_to_valid_from_is_rejected() -> None:
    """The dropped SQL ``CHECK`` was strict (``valid_to > valid_from``, not
    ``>=``), and INV-4 must refuse the equal-instant boundary exactly as
    strictly or the schema's removal quietly loosened what a window may be.
    Nothing pinned this before: mutating ``ValidityPeriod.__post_init__``'s
    ``self.valid_to <= self.valid_from`` to ``<`` survived the whole suite,
    because every other test in this module compares two genuinely distinct
    instants.

    Two shapes, because "equal" can arrive either way a caller might write it:
    the identical ``datetime`` twice, and two different UTC offsets that happen
    to name the same instant -- the same case ``test_valid_to_must_follow_
    valid_from_by_instant_not_by_offset`` above exercises for a *different*
    instant, applied here to the boundary where the two collapse to one.
    """
    with pytest.raises(InvariantViolationError, match="must be after"):
        ValidityPeriod(valid_from=NOW, valid_to=NOW)

    with pytest.raises(InvariantViolationError, match="must be after"):
        ValidityPeriod(
            valid_from=datetime.fromisoformat("2031-01-01T00:00:00+00:00"),
            valid_to=datetime.fromisoformat("2031-01-01T09:00:00+09:00"),
        )


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="timezone-aware"):
        ValidityPeriod(valid_from=datetime(2026, 8, 1))  # noqa: DTZ001


def test_validity_window_is_half_open() -> None:
    """``valid_to`` is exclusive, so adjacent windows do not both match."""
    period = ValidityPeriod(valid_from=NOW, valid_to=NOW + timedelta(days=1))
    assert period.contains(NOW)
    assert not period.contains(NOW + timedelta(days=1))


def test_revision_outside_its_validity_window_is_not_current() -> None:
    revision = _revision(validity=ValidityPeriod(valid_from=NOW, valid_to=NOW + timedelta(days=1)))
    assert revision.is_current_at(NOW)
    assert not revision.is_current_at(NOW + timedelta(days=2))


def test_unapproved_revision_is_never_current() -> None:
    revision = _revision(metadata=_metadata(status=KnowledgeStatus.DRAFT))
    assert not revision.is_current_at(NOW)


# -- Relations -------------------------------------------------------------


def test_self_relation_is_rejected() -> None:
    with pytest.raises(InvariantViolationError, match="Self-relation"):
        KnowledgeRelation(
            project_id=PROJECT,
            source_item_id=ITEM,
            relation_type=RelationType.DEPENDS_ON,
            target_item_id=ITEM,
            created_at=NOW,
        )


def test_directional_relations_expose_their_inverse() -> None:
    relation = KnowledgeRelation(
        project_id=PROJECT,
        source_item_id=ITEM,
        relation_type=RelationType.SUPERSEDES,
        target_item_id=ItemId("architecture.auth-policy-v1"),
        created_at=NOW,
    )
    inverse = relation.inverse
    assert inverse is not None
    assert inverse.relation_type is RelationType.SUPERSEDED_BY
    assert inverse.source_item_id == relation.target_item_id


def test_symmetric_relations_have_no_inverse() -> None:
    relation = KnowledgeRelation(
        project_id=PROJECT,
        source_item_id=ITEM,
        relation_type=RelationType.RELATED_TO,
        target_item_id=ItemId("domain.pricing"),
        created_at=NOW,
    )
    assert relation.inverse is None


def test_the_four_types_that_reach_a_reader_unmirrored_have_no_inverse() -> None:
    """The premise the `knowledge.get` relation-gate corpus is built on.

    `SqliteCanonicalStore.list_relations` mirrors an edge on the way out only
    when its type has an entry here. Every other type reaches the caller in the
    orientation it was stored in, so an *incoming* edge arrives with
    `target_item_id` set to the item being fetched — which is what made the old
    gate's question ("may the target be surfaced?") a tautology on those rows,
    and why the integration tests parametrise the gate over exactly these four.

    If one of them ever gains an inverse it leaves that class, and those tests
    would go on passing while covering three cases instead of four. This is
    where that gets noticed.
    """
    unmirrored = {
        RelationType.REJECTS,
        RelationType.RELATED_TO,
        RelationType.CONTRADICTS,
        RelationType.DEPENDS_ON,
    }

    assert unmirrored.isdisjoint(INVERSE_RELATIONS), (
        "an incoming edge of one of these types is published in its stored "
        "orientation, and the gate's corpus depends on that"
    )


def test_supersedes_is_marked_acyclic() -> None:
    relation = KnowledgeRelation(
        project_id=PROJECT,
        source_item_id=ITEM,
        relation_type=RelationType.SUPERSEDES,
        target_item_id=ItemId("architecture.auth-policy-v1"),
        created_at=NOW,
    )
    assert relation.must_be_acyclic


# -- Aliases and evidence --------------------------------------------------


def test_alias_cannot_point_at_itself() -> None:
    with pytest.raises(InvariantViolationError, match="itself"):
        KnowledgeAlias(alias=ITEM, item_id=ITEM, project_id=PROJECT, created_at=NOW)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_evidence_confidence_is_bounded(confidence: float) -> None:
    with pytest.raises(InvariantViolationError, match=r"\[0.0, 1.0\]"):
        KnowledgeEvidence(
            item_id=ITEM,
            project_id=PROJECT,
            anchor=ANCHOR,
            description="Derived from PR #431",
            confidence=confidence,
            created_at=NOW,
        )


# -- Source anchors --------------------------------------------------------


def test_anchor_line_range_must_be_ordered() -> None:
    with pytest.raises(InvariantViolationError, match="must not precede"):
        SourceAnchor(provider="git", source_uri="git://x", line_start=10, line_end=5)


def test_anchor_lines_are_one_based() -> None:
    with pytest.raises(InvariantViolationError, match="1-based"):
        SourceAnchor(provider="git", source_uri="git://x", line_start=0)


def test_git_anchored_requires_commit_and_path() -> None:
    assert ANCHOR.is_git_anchored
    assert not SourceAnchor(provider="github", source_uri="https://x").is_git_anchored


# -- INV-10: identifiers ---------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "01K1DEFABC1234567890ABCDE",  # 25 characters # invalid-ulid
        "01K1DEFABC1234567890ABCDEFG",  # 27 characters # invalid-ulid
        "81K1DEFABC1234567890ABCDEF",  # overflows the 128-bit ULID space # invalid-ulid
        "01K1DEFABC1234567890ABCDEI",  # 'I' is excluded from Crockford base32 # invalid-ulid
        "01K1DEFABC1234567890ABCDEL",  # so is 'L' # invalid-ulid
        "01K1DEFABC1234567890ABCDEU",  # and 'U' # invalid-ulid
        "",
    ],
)
def test_malformed_ulids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        RevisionId(value)


def test_ulid_parse_normalises_case() -> None:
    """Crockford base32 is case-insensitive; storage is uppercase so that
    byte-wise sorting matches ULID ordering."""
    assert RevisionId.parse("01k1defabc1234567890abcdef").value == "01K1DEFABC1234567890ABCDEF"


@pytest.mark.parametrize(
    "value",
    ["Architecture.Auth", "architecture..auth", "architecture.auth_policy", "-leading", ""],
)
def test_malformed_item_ids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        ItemId(value)


def test_item_id_exposes_its_namespace() -> None:
    assert ItemId("architecture.backend.auth-policy").namespace == "architecture.backend"
    assert ItemId("standalone").namespace == ""


@pytest.mark.parametrize("value", ["Backend", "backend_service", "backend service", ""])
def test_malformed_project_ids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        ProjectId(value)


@pytest.mark.parametrize(
    ("build", "value"),
    [
        (ProjectId, "backend-service\n"),
        (ItemId, "architecture.auth-policy\n"),
        (SpecId, "spec.order-cancellation\n"),
        (RevisionId, "01K1DEFABC1234567890ABCDEF\n"),
    ],
    ids=["project", "item", "spec", "revision"],
)
def test_an_identifier_with_a_trailing_newline_is_rejected(
    build: type[ProjectId | ItemId | SpecId | RevisionId], value: str
) -> None:
    r"""Every value here is legal up to the newline, which is the point.

    Python's ``$`` matches at the end of the string *and* immediately before a
    trailing newline, so while these patterns were ``^...$`` all four of these
    constructed. A real ``project.list`` response then published ``"demo"`` and
    ``"demo\n"`` side by side in one array, from one registry, indistinguishable
    to whoever read it. ``80f94b6`` anchored them ``\A...\Z``.

    Nothing went red for that fix, and this is why: the rejection cases above
    enumerate character-class and length violations and contain no whitespace at
    all, in either direction. The pattern could be put back to ``$`` today and
    the only thing that would notice is this test and the published-pattern
    agreement in ``test_schemas.py``.
    """
    with pytest.raises(InvalidIdentifierError):
        build(value)


def test_identifier_types_are_not_interchangeable() -> None:
    """Distinct types are the point: every id is a string at rest.

    mypy rejects this comparison as non-overlapping, which is exactly the
    guarantee under test -- passing a MigrationId where a RevisionId belongs is
    a type error, not a runtime mystery. The runtime assertion below confirms
    the two do not compare equal even though they wrap identical strings.
    """
    revision: object = RevisionId(REVISION.value)
    migration: object = MigrationId(REVISION.value)
    assert revision != migration
