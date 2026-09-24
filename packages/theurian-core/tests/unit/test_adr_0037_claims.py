"""ADR-0037's load-bearing claims, held against the constants they were read from.

ADR-0037 ships no code (slice S1). That is exactly what makes its sentences
drift-prone: every repository fact in it was measured once, on 2026-09-24
against ``39ad65d1``, and nothing re-measures them. Slices S2 and S3 are written
*against* those sentences rather than against the tree, so a constant that moves
without the ADR moving sends an implementer at a population, a vocabulary or a
disclosure bound that no longer exists.

Each pin here is **bidirectional**: the fact half goes RED when the constant
moves, and the prose half goes RED when the ADR stops saying it. Neither half is
worth having alone -- a constant pin stays green against a document that was
quietly rewritten, and a fragment pin stays green against a tree that stopped
making the sentence true. That split is ``test_adr_0030_claims.py``'s, recorded
there at length.

What is held:

- **The exported population** (decision 3, and decision 7's ``stable``-only
  row). ``may_surface`` with ``include_unapproved=False`` admits ``APPROVED``
  alone out of :data:`~theurian.domain.enums.SURFACEABLE_STATUSES`, which is
  what makes ``stable`` the only OKF ``status`` the export can emit and the rest
  of decision 7's status mapping *reserved* rather than live.
- **The relation vocabulary** (decision 4, decision 5, Compliance). Fourteen
  members, mirrored by the published migration schema.
- **The required revision metadata** (the context section's two-file-split
  paragraph). Six required fields and nine carried ones, which is the field
  population decision 7's projection table has to account for.
- **The first instrument of the disclosure bound** (decision 7).
  ``result_payload``'s exact published key sets, including the two
  ``sourceAnchor`` fields it does *not* publish -- which is why the ADR drops
  them rather than exporting them.
- **Decision 7's projection table, walked against that bound.** Every row's OKF
  emission derives from a key one of the two measured instruments serves, or is
  one of the five widenings the ADR records against the bound. This is the pin
  the Compliance section says lands with this pull request.

Pure: it reads ``domain/enums.py``'s constants, one JSON schema, one Markdown
file and ``mcp/tools.py``'s AST, and builds one in-memory revision. No database,
no socket, no temporary directory.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple

import pytest

from theurian.domain.enums import (
    SURFACEABLE_STATUSES,
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
    may_surface,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeRevision, RevisionMetadata, SourceAnchor
from theurian.domain.retrieval import RaptorPathSegment
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.mcp.results import result_payload

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

ADR_0037: Final = REPO_ROOT / "docs" / "adr" / "0037-okf-is-the-knowledge-layer-interchange.md"
MIGRATION_SCHEMA: Final = REPO_ROOT / "schemas" / "migrations" / "migration.schema.json"

NOW: Final = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _collapsed(text: str) -> str:
    """Markdown emphasis and code markers dropped, whitespace runs flattened.

    ADR-0037 is line-wrapped Markdown, so every sentence pinned below breaks
    across source lines and a raw substring match would miss the real wording and
    pass vacuously.

    ``*`` and backticks go with the whitespace because they carry no claim.
    Bolding one more field name inside the served-payload enumeration would
    otherwise redden a claim that did not move -- the same failure the vocabulary
    pin avoids by splitting its citation into its own clause. Applied to the
    fragment as well as to the document, so a pin can still be written in the
    ADR's own markup.
    """
    return " ".join(text.replace("*", "").replace("`", "").split())


def _adr() -> str:
    return _collapsed(ADR_0037.read_text(encoding="utf-8"))


def _schema_defs() -> dict[str, Any]:
    return dict(json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))["$defs"])


def _assert_the_adr_states(fragment: str, *, because: str) -> None:
    """The prose half, in one helper so every claim reports the same way."""
    assert _collapsed(fragment) in _adr(), (
        f"ADR-0037 no longer states:\n\n  {fragment}\n\n{because}\n\n"
        f"The fact half is in this same module. If it is GREEN, nothing in the tree "
        f"moved and the document is what gets restored; if it is RED, the constant "
        f"moved and the sentence has to move with it."
    )


# ---------------------------------------------------------------------------
# 1. The exported population (decision 3; decision 7's `stable`-only row).
# ---------------------------------------------------------------------------


def test_the_default_channel_admits_approved_alone_out_of_the_surfaceable_set() -> None:
    """Decision 3's population is a shipped predicate, and the export reuses it.

    ADR-0037 defines the bundle's contents by reference rather than by rule: the
    exported population *is* the rows the default channel serves, which is
    ``may_surface(status, include_unapproved=False)``. Decision 7 then rests a
    second claim on the same constant -- ``stable`` is the only OKF ``status`` the
    export can emit, and the ``deprecated``/``superseded`` rows of its mapping are
    **reserved** rather than live, because neither status is in
    ``SURFACEABLE_STATUSES`` at all.

    **What should redden this is roadmap §9 ADR candidate 2** -- historical
    disclosure, a Phase D decision, which is the change that puts ``DEPRECATED``
    and ``SUPERSEDED`` into this set. When it lands, decision 7's reserved
    status-mapping paragraph goes live with it and the ADR's *"An export that
    emitted a ``deprecated`` concept today would be a disclosure defect, not a
    mapping gap"* stops being true. That is a coordinated change, not a
    constant edit, and this pin is what forces the two to move together.

    Driven over **every** ``KnowledgeStatus`` member rather than over ``APPROVED``
    and one neighbour: a hand-picked pair cannot reach the counterexample that
    matters, which is a retired status becoming admissible. The
    ``include_unapproved=True`` arm is asserted for the same reason from the other
    side -- it bounds what *any* flag could widen the set to, which is the
    mechanism behind decision 3's *"no flag that widens this population"*.
    """
    recorded = {KnowledgeStatus.APPROVED, KnowledgeStatus.DRAFT, KnowledgeStatus.PROPOSED}

    assert set(SURFACEABLE_STATUSES) == recorded, (
        f"`SURFACEABLE_STATUSES` is {sorted(SURFACEABLE_STATUSES)}. ADR-0037 decision 3 "
        f"spells this set out inline, and decision 7 reads `deprecated` and "
        f"`superseded` being absent from it as the reason its status mapping is "
        f"reserved rather than live."
    )

    default_channel = {
        status for status in KnowledgeStatus if may_surface(status, include_unapproved=False)
    }
    opted_in = {
        status for status in KnowledgeStatus if may_surface(status, include_unapproved=True)
    }

    assert default_channel == {KnowledgeStatus.APPROVED}, (
        f"the default channel admits {sorted(default_channel)}. ADR-0037 decision 3 "
        f"says the exported bundle is exactly this population, so a second admitted "
        f"status is a row in every distributed bundle that the ADR says cannot be "
        f"there -- and decision 7's `stable` is the only value emitted` is false with it."
    )
    assert opted_in == set(SURFACEABLE_STATUSES), (
        f"`include_unapproved=True` admits {sorted(opted_in)}, which is not "
        f"`SURFACEABLE_STATUSES`. The flag is meant to widen the set to its own "
        f"members and no further; a retired status reachable through it would make "
        f"decision 7's reserved mapping live through a flag rather than through the "
        f"Phase D decision that owns it."
    )


def test_the_adr_still_states_the_population_and_the_status_it_can_emit() -> None:
    """The prose half of the population pin: two sentences, two different losses.

    The first names the predicate and spells the set out inline, which is what
    makes decision 3 checkable instead of a gesture at "the default channel". The
    second is the one a reader of decision 7's table needs: without it the table's
    ``deprecated``/``superseded`` rows read as shippable mappings rather than as
    reserved ones, and S2 implements a disclosure defect from a governed record.
    """
    _assert_the_adr_states(
        "`domain/enums.py::may_surface` (with `include_unapproved=False`, which admits "
        "`APPROVED` alone out of `SURFACEABLE_STATUSES = {APPROVED, DRAFT, PROPOSED}`)",
        because=(
            "Decision 3 defines the exported population by naming this predicate and its "
            "set. A decision that stops naming them defines the bundle's contents by "
            "description, and S2 has to guess which rows the export walks."
        ),
    )
    _assert_the_adr_states(
        "**`stable` is the only OKF `status` the export can emit today, and the rest of "
        "the mapping is reserved rather than live.**",
        because=(
            "This sentence is what marks decision 7's `deprecated` and `superseded` rows "
            "as reserved. Without it the table reads as live mapping, and the ADR itself "
            "says an export emitting a `deprecated` concept today would be a disclosure "
            "defect rather than a mapping gap."
        ),
    )


# ---------------------------------------------------------------------------
# 2. The relation vocabulary (decisions 4 and 5, Compliance).
# ---------------------------------------------------------------------------


def test_the_relation_vocabulary_is_the_closed_fourteen_the_schema_mirrors() -> None:
    """ADR-0037 counts this enum three times and rests two decisions on it.

    Decision 4 projects each typed edge into ``theurian_relations``; decision 5
    refuses to *synthesize* one from a bare Markdown link and says a type outside
    the fourteen is refused by the published schema before a human sees it; the
    Compliance section repeats that as shipped enforcement. All three are the same
    fact, and the number is written out in prose in four places.

    Searched before writing (2026-09-24,
    ``git grep -n 'len(RelationType)\\|m.value for m in RelationType' ``): **no
    existing test mirrors ``$defs/relationType`` against this enum**, so the
    mirror is asserted here rather than cited. It is asserted as an ordered list,
    not as a set: the schema is a published contract whose enum a third party
    reads, and a reorder is a diff its reviewer should see.

    The count is pinned separately from the mirror because the two fail for
    different reasons -- a member added to *both* sides keeps the mirror green and
    falsifies "closed 14-member" everywhere the ADR writes it, which is precisely
    the enum pressure decision 5 exists to keep at zero.
    """
    schema_enum = _schema_defs()["relationType"]["enum"]

    assert len(RelationType) == 14, (
        f"`RelationType` has {len(RelationType)} members. ADR-0037 writes `14-member` in "
        f"decision 4, in the alternatives table and in the Consequences, and the "
        f"Compliance section says the schema `enumerates the 14 members`. "
        f"Widening the enum is roadmap §9 ADR candidate 3, which owes a compatibility "
        f"policy first -- so this is a decision, not an edit, and all four sentences "
        f"move with it."
    )
    assert schema_enum == [member.value for member in RelationType], (
        f"`$defs/relationType` enumerates {schema_enum}, against the domain enum's "
        f"{[member.value for member in RelationType]}. ADR-0037 decision 4 states the "
        f"schema mirrors this enum and decision 5 rests on it: a type outside the "
        f"vocabulary is refused by the published schema *before* any human reviews the "
        f"drafted migration, so a schema that drifts wider than the enum lets an "
        f"imported bundle propose an edge the domain cannot hold."
    )


def test_the_adr_still_states_the_vocabulary_and_the_mirror() -> None:
    """The prose half: the count, the mirror, and the refusal that reads them.

    The mirror clause is the load-bearing one. Decision 5's guarantee -- that a
    bundle cannot widen the enum by asserting a new value -- is a property of the
    *schema*, not of the domain enum, so a document that keeps "14-member" and
    drops "mirrored by" leaves the refusal resting on nothing a reader can find.
    """
    _assert_the_adr_states(
        "a closed 14-member `RelationType`",
        because=(
            "Decision 4 names the size of the vocabulary, and the alternatives table "
            "and Consequences repeat it. Widening it is roadmap §9 ADR candidate 3, "
            "which owes a compatibility policy first."
        ),
    )
    _assert_the_adr_states(
        "mirrored by `$defs/relationType` in the migration schema",
        because=(
            "Decision 5's `a bundle cannot widen the enum by asserting a new value` is "
            "enforced by that mirror and by nothing else. Pinned as its own clause, so "
            "that correcting the line-number citation beside it does not redden a "
            "vocabulary claim that did not move."
        ),
    )
    _assert_the_adr_states(
        "`schemas/migrations/migration.schema.json` enumerates the 14 members, so an "
        "imported operation naming anything else is refused at validation, before any "
        "human sees it.",
        because=(
            "The Compliance section lists this as enforcement that *already holds*, "
            "which is what lets ADR-0037 ship decision 5 without a test of its own."
        ),
    )


# ---------------------------------------------------------------------------
# 3. The required revision metadata (the two-file-split paragraph).
# ---------------------------------------------------------------------------

#: What ``$defs/revisionMetadata`` requires, in the schema's own order.
#:
#: Ordered rather than a set because ``required`` is a published JSON array and
#: ``test_schemas.py`` pins ``opUpsertRevision``'s the same way.
REQUIRED_REVISION_METADATA: Final[tuple[str, ...]] = (
    "title",
    "contentType",
    "kind",
    "namespace",
    "status",
    "owner",
)

#: The optional properties the same paragraph enumerates, as the schema spells
#: them. ``scope.paths`` is the ADR's spelling of the ``scope`` property.
CARRIED_REVISION_METADATA: Final[frozenset[str]] = frozenset(
    {
        "trustLevel",
        "sensitivity",
        "tenantId",
        "aclGroup",
        "validFrom",
        "validTo",
        "labels",
        "scope",
        "sourceAnchors",
    }
)


def test_the_governed_metadata_is_the_six_required_and_nine_carried_fields() -> None:
    """The field population decision 7's projection table has to account for.

    ADR-0037's second premise correction rests on this paragraph: governance
    lives *only* in the migration, and the paragraph names every field that
    means. Decision 7 then claims to enumerate what happens to each one -- mapped
    into an OKF slot, or dropped with a reason. That claim is only as complete as
    the population it ranges over, so the population is pinned here.

    Both directions and the whole property set, not just ``required``: a **new
    required field** is a governance fact decision 7's table does not map, and a
    **new optional field** is one its *Dropped without a slot* table does not
    account for either. Either way the ADR's *enumerated so that "lossy" is a list
    rather than an adjective* stops being true, and the export ships a projection
    whose gaps are undocumented.

    What this does **not** assert is that each field has a row in one of those two
    tables. That check was RED when this pin landed -- ``contentType`` had no
    disposition -- and the ADR was amended (the sidecar rule, ``14c97c50``) rather
    than the pin weakened; a new field landing here re-raises the same question.
    """
    metadata = _schema_defs()["revisionMetadata"]

    assert tuple(metadata["required"]) == REQUIRED_REVISION_METADATA, (
        f"`$defs/revisionMetadata` requires {metadata['required']}, and ADR-0037's "
        f"two-file-split paragraph records {list(REQUIRED_REVISION_METADATA)}. A field "
        f"added here is governance the OKF projection of decision 7 does not map; one "
        f"removed is a row in that table describing a field that no longer exists."
    )
    assert frozenset(metadata["properties"]) == frozenset(REQUIRED_REVISION_METADATA) | (
        CARRIED_REVISION_METADATA
    ), (
        f"`$defs/revisionMetadata` carries {sorted(metadata['properties'])}. ADR-0037 "
        f"names the six required fields and then the nine it `carries`; decision 7 "
        f"claims to say of each whether it is projected or dropped. A property in "
        f"neither list is governance metadata with no recorded disposition."
    )


def test_the_adr_still_states_the_governed_metadata_population() -> None:
    """The prose half: the sentence that makes governance a two-file split.

    It is the ADR's correction of #705's premise that Theurian's canonical
    knowledge is already markdown with YAML front matter, and it decides the
    direction of every rule in the document -- an exporter may synthesize front
    matter, an importer may never read it as governance. A rewrite that keeps the
    conclusion and drops this enumeration leaves the asymmetry asserted rather
    than argued.
    """
    _assert_the_adr_states(
        "`schemas/migrations/migration.schema.json`'s `$defs/revisionMetadata` requires "
        "`title`, `contentType`, `kind`, `namespace`, `status` and `owner`, and carries "
        "`trustLevel` (default `unverified`), `sensitivity` (default `internal`), "
        "`tenantId` (default `local`), `aclGroup` (default `default`), `validFrom`, "
        "`validTo`, `labels`, `scope.paths` and `sourceAnchors`.",
        because=(
            "This enumeration is the population decision 7's projection table and its "
            "dropped table are read against. Without it, `The governance projection is "
            "lossy, one-way, and enumerated` has nothing to be complete with respect to."
        ),
    )


# ---------------------------------------------------------------------------
# 4. The first instrument of the disclosure bound (decision 7).
# ---------------------------------------------------------------------------

#: Every top-level key ``result_payload`` publishes when no forest was walked.
SERVED_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "itemId",
        "revisionId",
        "title",
        "excerpt",
        "contentType",
        "status",
        "trustLevel",
        "sensitivity",
        "freshness",
        "sourceAnchors",
        "contentClassification",
        "mayContainInstructions",
        "executable",
    }
)

#: Every field published *per source anchor*. The ADR's `of exactly` list.
SERVED_ANCHOR_FIELDS: Final[frozenset[str]] = frozenset(
    {"provider", "sourceUri", "repository", "commitSha", "filePath", "lineStart", "lineEnd"}
)

#: The two anchor fields the canonical store holds and the serve path does not
#: publish, which is why decision 7 drops rather than exports them.
WITHHELD_ANCHOR_FIELDS: Final[frozenset[str]] = frozenset({"blobSha", "externalId"})


def _revision() -> KnowledgeRevision:
    """One approved revision whose anchor carries **every** field a store can hold.

    The anchor's ``blob_sha`` and ``external_id`` are populated deliberately. An
    anchor that left them ``None`` would make "the payload does not publish them"
    pass for the wrong reason -- the fixture could not produce the shape its own
    assertion is about -- so the values are set and asserted present before the
    payload's key set is read.
    """
    return KnowledgeRevision.create(
        revision_id=RevisionId("01K1REV00101234567890ABCDE"),
        item_id=ItemId("architecture.auth-policy"),
        project_id=ProjectId("demo"),
        migration_id=MigrationId("01K1MAG00101234567890ABCDE"),
        title="Authentication and authorization policy",
        body="Every call carries a signed token.",
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=NOW),
        author="engineer@example.com",
        created_at=NOW,
        source_anchors=(
            SourceAnchor(
                provider="github",
                source_uri="https://github.com/demo/repo/blob/main/a.md",
                repository="demo/repo",
                commit_sha="0123abc",
                blob_sha="4567def",
                file_path="a.md",
                line_start=1,
                line_end=4,
                external_id="PR-42",
            ),
        ),
    )


def test_the_served_payload_publishes_exactly_the_keys_the_okf_bound_is_drawn_at() -> None:
    """The first of the two payloads decision 7's bound is the union of.

    ADR-0037 draws the bound on *two measured instruments* -- this payload and
    ``knowledge.get``'s four additions -- with five widenings recorded against it.
    An earlier draft cited this payload alone and claimed the projection publishes
    nothing the serve path withholds, which the ADR now records as false of its
    own table. The correction kept this instrument, so two of decision 7's
    consequences still rest on the key set below: ``theurian_trust_level`` and
    ``theurian_sensitivity`` are exportable *because* a caller of this deployment
    already receives those labels on those same rows, and ``blobSha`` and
    ``externalId`` are dropped *because* this payload does not carry them.

    A field **added to** ``result_payload`` widens what the serve path publishes,
    so the export's own projection table now omits a label the audience already
    sees. A field **removed** from it -- ``trustLevel`` or ``sensitivity`` above
    all -- turns an exported ``theurian_*`` key into metadata the serve path
    withholds, which is the disclosure defect the sentence exists to forbid.
    Either way the ADR's bound must be re-drawn, and this pin is what stops the
    tree moving under it in silence.

    Asserted as exact key sets at all three levels the payload publishes, because
    a membership check ("``blobSha`` is absent") is satisfied by a payload that
    has stopped publishing anything at all.
    """
    revision = _revision()
    anchor = revision.source_anchors[0]

    assert anchor.blob_sha and anchor.external_id, (
        "the fixture's anchor must carry `blob_sha` and `external_id` before their "
        "absence from the payload can mean the serve path withholds them"
    )

    payload = result_payload(
        revision,
        status=KnowledgeStatus.APPROVED,
        sensitivity=Sensitivity.INTERNAL,
        now=NOW,
    )

    assert set(payload) == SERVED_TOP_LEVEL_KEYS, (
        f"`result_payload` publishes {sorted(payload)}. ADR-0037 decision 7 draws one "
        f"half of its `union of two payloads` bound at exactly this set: a NEW key "
        f"is a label the OKF projection table does not map though the audience already "
        f"receives it; a MISSING one turns the matching `theurian_*` key into an "
        f"export of something the serve path withholds. Move the ADR's bound paragraph "
        f"in the same change."
    )
    assert set(payload["freshness"]) == {"revisionCreatedAt", "isWithinValidity", "ageDays"}, (
        f"`freshness` publishes {sorted(payload['freshness'])}. ADR-0037 maps "
        f"`created_at` to OKF `generated.at` off `revisionCreatedAt`; a sibling added "
        f"here is published metadata the projection table does not account for."
    )

    published_anchor = payload["sourceAnchors"][0]

    assert set(published_anchor) == SERVED_ANCHOR_FIELDS, (
        f"the served anchor publishes {sorted(published_anchor)}, against the `of "
        f"exactly` list ADR-0037 decision 7 maps onto an OKF `sources[]` entry's "
        f"`theurian_anchor`. The export projects this set and no other."
    )
    assert WITHHELD_ANCHOR_FIELDS.isdisjoint(published_anchor), (
        f"the served anchor now carries {sorted(WITHHELD_ANCHOR_FIELDS & set(published_anchor))}. "
        f"ADR-0037 drops `blobSha` and `externalId` *because* this payload does not "
        f"publish them -- `exporting them would put provenance in a portable artifact "
        f"that the serve path withholds from the same rows`. If the serve path now "
        f"publishes them, that reason is gone and decision 7's dropped table is wrong."
    )


def test_the_served_payload_adds_only_the_forest_path_when_a_forest_was_walked() -> None:
    """The bound's second configuration, so the key set is closed over both.

    ``raptorPath`` is emitted only when the ranked path walked a forest (ADR-0008
    decision 8), so a key set measured on the unranked shape alone leaves the
    ranked one unbounded -- a field added under that branch would be published
    metadata outside ADR-0037's stated bound with nothing RED to say so.
    """
    payload = result_payload(
        _revision(),
        status=KnowledgeStatus.APPROVED,
        sensitivity=Sensitivity.INTERNAL,
        now=NOW,
        raptor_path=(RaptorPathSegment(node_id="root", level=2, title="Auth"),),
    )

    assert set(payload) == SERVED_TOP_LEVEL_KEYS | {"raptorPath"}, (
        f"the ranked path publishes {sorted(payload)}, which is not the unranked set "
        f"plus `raptorPath`. ADR-0037's bound is drawn over what `result_payload` "
        f"publishes, so a key reachable on one path only is still inside it."
    )


def test_the_adr_still_states_the_bound_and_the_two_fields_it_drops() -> None:
    """The prose half: the enumeration, and the drop that depends on it.

    Two fragments because each carries a different half. The first is the
    ``result_payload`` half of the bound -- the field list the projection draws
    from. The second is the consequence a reader of the dropped table needs:
    ``blobSha`` and ``externalId`` are absent from the bundle *because* they are
    absent from the payload, and a document that drops that clause leaves the two
    rows looking like a preference that a later slice may reverse.
    """
    _assert_the_adr_states(
        "it emits `itemId`, `revisionId`, `title`, `excerpt`, `contentType`, `status`, "
        "**`trustLevel`** (from `revision.metadata.trust_level`), **`sensitivity`** (the "
        "item's current one, threaded in rather than read off the revision), "
        "`freshness.revisionCreatedAt` and a `sourceAnchors[]` of exactly `provider`, "
        "`sourceUri`, `repository`, `commitSha`, `filePath`, `lineStart` and `lineEnd`.",
        because=(
            "This enumeration is the measured half of decision 7's bound. Without it, "
            "`the projection publishes nothing outside that union except the five "
            "widenings` is a judgement rather than a measurement -- the form the first "
            "draft of that paragraph shipped, and the one the ADR retracts."
        ),
    )
    _assert_the_adr_states(
        "**The two anchor fields that payload does not carry, `blobSha` and "
        "`externalId`, are therefore not exported either**",
        because=(
            "The dropped table's `blobSha`, `externalId` row points at this sentence "
            "for its reason. Losing it leaves the drop unexplained, and an S2 "
            "implementer with no ground to refuse a request to export them."
        ),
    )


# ---------------------------------------------------------------------------
# 5. Decision 7's projection table, walked against that bound.
# ---------------------------------------------------------------------------

#: The line the decision-7 table is found after. A prefix rather than the whole
#: heading: what identifies the table is *which decision* it belongs to, and the
#: Compliance section refers to it as "decision 7's projection table".
DECISION_7: Final = "### 7."

#: The header row of the widening table, which is its only stable anchor -- the
#: paragraph above it is line-wrapped prose that a rewrap would move.
WIDENING_TABLE: Final = "| Widened |"


class _ProjectionRow(NamedTuple):
    """One row of decision 7's table: its Theurian column, and its OKF slot.

    ``okf`` is the code spans of the OKF column rather than its text, so a §
    citation or a reworded note beside the slot does not redden the row set. The
    bundle-path row has none, and that empty tuple is what distinguishes it from
    the ``theurian_item_id`` row that shares its Theurian side.
    """

    theurian: str
    okf: tuple[str, ...]


#: Rows whose OKF value is derived from a key ``result_payload`` publishes, each
#: mapped to that key path. ``created_at`` is the instant the payload publishes
#: as ``freshness.revisionCreatedAt``; the bundle path and ``theurian_item_id``
#: are both the served ``itemId``.
PROJECTED_FROM_A_SERVED_KEY: Final[dict[_ProjectionRow, str]] = {
    _ProjectionRow("title", ("title",)): "title",
    _ProjectionRow("contentType", ("theurian_content_type",)): "contentType",
    _ProjectionRow("item id", ()): "itemId",
    _ProjectionRow("item id", ("theurian_item_id",)): "itemId",
    _ProjectionRow("status", ("status",)): "status",
    _ProjectionRow("status", ("theurian_status",)): "status",
    _ProjectionRow("sourceAnchors[]", ("sources[]",)): "sourceAnchors",
    _ProjectionRow("created_at", ("generated.at",)): "freshness.revisionCreatedAt",
    _ProjectionRow("trustLevel", ("theurian_trust_level",)): "trustLevel",
    _ProjectionRow("sensitivity", ("theurian_sensitivity",)): "sensitivity",
    _ProjectionRow("revisionId", ("theurian_revision_id",)): "revisionId",
}

#: Rows licensed by the *second* instrument only. ``result_payload`` carries no
#: relations at all, so this row's presence in the bound is what
#: ``knowledge.get``'s additions buy -- and the ADR says as much: the exported
#: triple "is `knowledge.get`'s triple".
PROJECTED_FROM_A_KNOWLEDGE_GET_ADDITION: Final[dict[_ProjectionRow, str]] = {
    _ProjectionRow("relations", ("theurian_relations",)): "relations",
}

#: Rows outside the union, each mapped to the OKF key decision 7's widening
#: table records it under.
PROJECTED_AS_A_RECORDED_WIDENING: Final[dict[_ProjectionRow, str]] = {
    _ProjectionRow("kind", ("type",)): "type",
    _ProjectionRow("namespace", ("theurian_namespace",)): "theurian_namespace",
    _ProjectionRow("labels", ("tags",)): "tags",
    _ProjectionRow("validTo", ("stale_after",)): "stale_after",
    _ProjectionRow("owner", ("theurian_owner",)): "theurian_owner",
}

#: ``knowledge.get``'s additions to ``result_payload``'s shape, as decision 7's
#: bound names them.
KNOWLEDGE_GET_ADDITIONS: Final[frozenset[str]] = frozenset(
    {"body", "relations", "structured", "integrity"}
)


def _markdown_table(*, after: str) -> tuple[tuple[str, ...], ...]:
    """The data rows of the first Markdown table at or after a line starting ``after``.

    Anchored on a line prefix and never on a line number: every paragraph added
    above a table moves its lines, and a pin that has to be renumbered is a pin
    that gets updated without being read.
    """
    lines = ADR_0037.read_text(encoding="utf-8").splitlines()
    anchored = [index for index, line in enumerate(lines) if line.strip().startswith(after)]

    assert anchored, f"ADR-0037 has no line starting {after!r}; this parse anchors on it."

    table: list[str] = []
    for line in lines[anchored[0] :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append(stripped)
        elif table:
            break

    assert len(table) >= 3 and set(table[1]) <= {"|", " ", ":", "-"}, (
        f"the first table after {after!r} is not a header, a `:--` separator and at "
        f"least one row. The parse below would read prose as rows, and a walk over "
        f"rows it invented asserts nothing."
    )
    return tuple(tuple(cell.strip() for cell in row.strip("|").split("|")) for row in table[2:])


def _decision_7_table_headings() -> tuple[str, ...]:
    """The first header cell of every table decision 7 ships, in document order."""
    lines = ADR_0037.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(DECISION_7))
    headings: list[str] = []
    inside = False
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if not line.startswith("|"):
            inside = False
        elif not inside:
            inside = True
            headings.append(_collapsed(line.strip("|").split("|")[0]))
    return tuple(headings)


def _projection_rows() -> tuple[_ProjectionRow, ...]:
    return tuple(
        _ProjectionRow(_collapsed(cells[0]), tuple(re.findall(r"`([^`]+)`", cells[1])))
        for cells in _markdown_table(after=DECISION_7)
    )


def _widening_rows() -> tuple[tuple[str, str], ...]:
    """The widening table's rows as ``(OKF key, Theurian field)``, its own spelling."""
    rows: list[tuple[str, str]] = []
    for cells in _markdown_table(after=WIDENING_TABLE):
        okf, arrow, theurian = _collapsed(cells[0]).partition("←")
        assert arrow and theurian.strip(), (
            f"widening row {cells[0]!r} is not `<OKF key> ← <Theurian field>`. The pin "
            f"below reads both halves; a row it cannot split is a widening it cannot check."
        )
        rows.append((okf.strip(), theurian.strip()))
    return tuple(rows)


def _served_key_paths() -> frozenset[str]:
    """Every key path ``result_payload`` publishes, on the richest shape it builds.

    Measured with a forest walked, so ``raptorPath`` is inside the set rather than
    outside it: a bound drawn on the unranked shape alone would refuse a row that
    projects a key the serve path does publish.
    """
    payload = result_payload(
        _revision(),
        status=KnowledgeStatus.APPROVED,
        sensitivity=Sensitivity.INTERNAL,
        now=NOW,
        raptor_path=(RaptorPathSegment(node_id="root", level=2, title="Auth"),),
    )
    return frozenset(
        set(payload)
        | {f"freshness.{key}" for key in payload["freshness"]}
        | {f"sourceAnchors[].{key}" for key in payload["sourceAnchors"][0]}
    )


def _knowledge_get_additions() -> tuple[str, ...]:
    """The keys ``knowledge.get`` assigns onto ``result_payload``'s payload.

    Read out of ``mcp/tools.py``'s AST rather than recorded as a constant, because
    both directions move the bound and neither is visible to a constant: a fifth
    addition widens what decision 7 may draw from, and a removed one turns an
    exported key into metadata the serve path withholds. Measured off the source
    and not off a response because the tool resolves a project, opens a store and
    reads a live index -- none of which belongs in a unit test.
    """
    spec = importlib.util.find_spec("theurian.mcp.tools")

    assert spec is not None and spec.origin is not None, "`theurian.mcp.tools` has no source"

    tree = ast.parse(pathlib.Path(spec.origin).read_text(encoding="utf-8"))
    defined = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "knowledge_get"
    ]

    assert len(defined) == 1, (
        f"`mcp/tools.py` defines {len(defined)} `knowledge_get`. This measurement reads "
        f"exactly one, and a walk over the wrong body bounds the wrong tool."
    )

    keys: list[str] = []
    for node in ast.walk(defined[0]):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "payload"
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                keys.append(target.slice.value)

    assert keys, (
        "no `payload[<key>] = ...` assignment in `knowledge_get`. The tool builds its "
        "additions some other way now, so this measurement reads nothing and every "
        "claim resting on it would pass vacuously."
    )
    return tuple(keys)


def test_every_projection_row_emits_from_a_served_key_or_a_recorded_widening() -> None:
    """The walk decision 7's universal is only a universal because something runs it.

    ADR-0037's first draft of that paragraph asserted the projection publishes
    nothing the serve path withholds and cited ``result_payload`` alone. It was
    false of its own table -- five rows emit keys that payload never carries --
    and the correction is a bound with two instruments and five named widenings.
    A prose universal drifts back the same way the moment a row is appended, so
    this walks the table the ADR ships against the payloads the tree measures.

    The three mappings are kept apart rather than unioned because a row's
    *license* is the claim. ``relations`` is served by ``knowledge.get`` and by
    nothing in ``result_payload``, so a row claiming it from the wrong instrument
    would pass a union check and assert the wrong thing.
    """
    assert _decision_7_table_headings() == ("Theurian", "Widened", "Dropped"), (
        f"decision 7 ships tables headed {_decision_7_table_headings()}. The walk below "
        f"reads the first one; a fourth is mapping that nothing here walks. Either its "
        f"rows are categorized like the projection table's, or say in the ADR why they "
        f"are not emitted at all."
    )

    rows = _projection_rows()
    categorized = (
        PROJECTED_FROM_A_SERVED_KEY.keys()
        | PROJECTED_FROM_A_KNOWLEDGE_GET_ADDITION.keys()
        | PROJECTED_AS_A_RECORDED_WIDENING.keys()
    )

    assert len(set(rows)) == len(rows), (
        f"decision 7's table has two identical `(Theurian, OKF slot)` rows in "
        f"{rows}. The walk below is over a set, so a duplicate would hide one of them."
    )
    assert set(rows) == categorized, (
        f"decision 7's table and this pin disagree about its rows.\n"
        f"  in the ADR, uncategorized here: {sorted(set(rows) - categorized)}\n"
        f"  categorized here, not in the ADR: {sorted(categorized - set(rows))}\n"
        f"An added row has to be categorized: derived from a key `result_payload` "
        f"publishes, derived from one of `knowledge.get`'s four additions, or a "
        f"widening -- which owes a row in the ADR's widening table and a line of "
        f"justification. A row that is none of the three publishes metadata the "
        f"serve path withholds, which is the defect the ADR's first draft shipped."
    )

    served = _served_key_paths()
    additions = _knowledge_get_additions()

    for row, key in PROJECTED_FROM_A_SERVED_KEY.items():
        assert key in served, (
            f"decision 7 projects {row.theurian!r} into {row.okf} off `result_payload`'s "
            f"{key!r}, which that payload no longer publishes: it publishes "
            f"{sorted(served)}. The export would carry metadata the serve path "
            f"withholds, and decision 7's bound has to be re-drawn before S2 ships it."
        )
    for row, key in PROJECTED_FROM_A_KNOWLEDGE_GET_ADDITION.items():
        assert key in additions, (
            f"decision 7 projects {row.theurian!r} into {row.okf} off `knowledge.get`'s "
            f"{key!r}, and that tool now adds {sorted(additions)}. Nothing in the serve "
            f"path publishes the value any more, so exporting it is new disclosure."
        )


def test_the_recorded_widenings_are_the_five_rows_the_adr_tables() -> None:
    """The other half of the bound: what the ADR admits it emits outside the union.

    The walk above licenses five rows by calling them widenings. That license is
    worth exactly as much as the ADR's own table of them, so the two are held
    against each other: a sixth widening added to the document without a row in
    decision 7's table, or a fifth row silently reclassified here, reddens.

    Asserted as ``(OKF key, Theurian field)`` pairs rather than as a count,
    because the failure that matters is a widening whose source changed --
    ``tags ← labels`` becoming ``tags ← scope.paths`` keeps the count and exports
    a field the dropped table says has no home.
    """
    tabled = _widening_rows()
    licensed = {(okf, row.theurian) for row, okf in PROJECTED_AS_A_RECORDED_WIDENING.items()}

    assert len(set(tabled)) == len(tabled), (
        f"the widening table repeats a row: {tabled}. A duplicate hides a member "
        f"from the set comparison below."
    )
    assert set(tabled) == licensed, (
        f"the ADR's widening table and this pin disagree.\n"
        f"  tabled in the ADR, not licensed here: {sorted(set(tabled) - licensed)}\n"
        f"  licensed here, not tabled in the ADR: {sorted(licensed - set(tabled))}\n"
        f"Each widening is a recorded decision to publish something outside the "
        f"measured bound. Adding one is a disclosure decision with a justification "
        f"owed; removing one leaves a row of decision 7's table emitting with no "
        f"license at all."
    )


def test_knowledge_get_adds_exactly_the_four_keys_the_bound_names() -> None:
    """The second instrument, measured, because the bound is only as true as it is.

    ADR-0037 names these four in prose and draws half its disclosure bound on
    them. A fifth addition widens what the export may draw from without anyone
    deciding to widen it; a removed one -- ``relations`` above all -- leaves
    decision 7's ``theurian_relations`` row exporting a triple the serve path no
    longer hands out. Either way the sentence in the ADR stops being true, and
    the walk above starts licensing rows against an instrument that moved.
    """
    additions = _knowledge_get_additions()

    assert set(additions) == KNOWLEDGE_GET_ADDITIONS, (
        f"`knowledge.get` adds {sorted(additions)} to `result_payload`'s shape. "
        f"ADR-0037 draws half of decision 7's bound on exactly "
        f"{sorted(KNOWLEDGE_GET_ADDITIONS)}: a NEW addition widens the bound with no "
        f"recorded decision, a MISSING one unlicenses whatever decision 7 projects "
        f"from it. Move the bound paragraph in the same change."
    )
    assert len(additions) == len(KNOWLEDGE_GET_ADDITIONS), (
        f"`knowledge.get` assigns {additions}, with a key written twice. The set "
        f"comparison above cannot see it, and `exactly four` is a count."
    )


def test_the_exported_anchor_fields_are_exactly_the_ones_the_serve_path_publishes() -> None:
    """The one row whose emission is a field list, walked field by field.

    Every other row of decision 7's table projects one value. The
    ``sourceAnchors[]`` row projects a brace list into ``theurian_anchor`` plus
    ``resource``, and that list is where a disclosure defect would sit unread:
    adding ``blob_sha`` to it exports provenance the serve path withholds from
    the same rows, which is precisely the drop the ADR justifies by this payload
    not carrying it.

    Read out of the row's own notes rather than restated here, so the check is
    over the document S2 implements from.
    """
    notes = {_collapsed(cells[0]): cells[2] for cells in _markdown_table(after=DECISION_7)}
    braced = re.search(r"theurian_anchor: \{([^}]*)\}", notes["sourceAnchors[]"])

    assert braced is not None, (
        "decision 7's `sourceAnchors[]` row no longer spells its `theurian_anchor: "
        "{ ... }` field list, so this walk reads nothing and would pass over any "
        "field the row now exports."
    )

    snake = [field.strip() for field in braced.group(1).split(",")]
    exported = {re.sub(r"_(.)", lambda m: m.group(1).upper(), field) for field in snake}

    assert exported | {"sourceUri"} == SERVED_ANCHOR_FIELDS, (
        f"decision 7 exports {sorted(exported)} per anchor, beside `sourceUri` as the "
        f"entry's `resource`. The serve path publishes {sorted(SERVED_ANCHOR_FIELDS)}. "
        f"A field here that the payload withholds -- `blobSha` or `externalId` above "
        f"all -- is provenance in a portable artifact that the same rows do not carry "
        f"on the serve path."
    )


def test_the_adr_still_states_the_union_bound_and_its_five_widenings() -> None:
    """The prose half of the walk: the bound, its second instrument, the universal.

    Three fragments, three different losses. Without the first the bound has no
    stated shape and the walk pins a table against something the document does not
    claim. Without the second ``knowledge.get`` is not an instrument at all, and
    ``theurian_relations`` is exported with nothing licensing it. Without the third
    the widening table reads as commentary rather than as the exhaustive
    exception list the walk treats it as.

    The fragments stop short of the ADR's `mcp/tools.py` line citation: a
    renumbered citation beside a claim must not redden the claim.
    """
    _assert_the_adr_states(
        "**What the projection may publish is bounded by two measured instruments, and "
        "five deliberate widenings are recorded against that bound.**",
        because=(
            "It is the shape of the bound this walk ranges over. A document that drops "
            "it leaves a table walked against a union it never claims, which is a pin "
            "with no governed record behind it."
        ),
    )
    _assert_the_adr_states(
        "which are exactly four: **`body`** — the whole body, not the excerpt — "
        "**`relations`**, each `{relationType, targetItemId, note}` and each gated by "
        "`_relation_is_visible`, **`structured`**, and a conditional `integrity`.",
        because=(
            "`knowledge.get` is the second instrument. `result_payload` carries no "
            "relations, so this sentence is the whole licence for decision 7's "
            "`theurian_relations` row and for the `## Relations` body section with it."
        ),
    )
    _assert_the_adr_states(
        "**So the universal is: the projection publishes nothing outside that union "
        "except the five widenings above.**",
        because=(
            "This is the claim the walk is the check for, and the ADR says as much -- "
            "`a statement a check can walk`. Losing it leaves the five widenings "
            "reading as examples rather than as the closed exception list."
        ),
    )
