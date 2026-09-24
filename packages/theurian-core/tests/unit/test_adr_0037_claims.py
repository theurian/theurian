"""ADR-0037's load-bearing constants, held against the tree they were read from.

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

What is held here:

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

**What the export emits is walked in three sibling modules**, all reading this
document and this fixture through ``adr_0037_support``:
``test_adr_0037_emission_walk.py`` holds the emission inventory against the
disclosure bound and decision 2's seven byte-source families,
``test_adr_0037_knowledge_get_bound.py`` holds the bound's second instrument --
``knowledge.get``'s four additions, and the syntax-tree reader that measures
them -- and ``test_adr_0037_sidecar_extension.py`` holds decision 7's
extension rule against the media types it ranges on.

Pure: it reads ``domain/enums.py``'s constants, one JSON schema, one Markdown
file, and builds one in-memory revision. No database, no socket, no temporary
directory.
"""

from __future__ import annotations

import json
from typing import Any, Final

import pytest
from adr_0037_support import (
    NOW,
    REPO_ROOT,
    SERVED_ANCHOR_FIELDS,
    assert_the_adr_states,
    revision,
)

from theurian.domain.enums import (
    SURFACEABLE_STATUSES,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    may_surface,
)
from theurian.domain.retrieval import RaptorPathSegment
from theurian.mcp.results import result_payload

pytestmark = pytest.mark.unit

MIGRATION_SCHEMA: Final = REPO_ROOT / "schemas" / "migrations" / "migration.schema.json"


def _schema_defs() -> dict[str, Any]:
    return dict(json.loads(MIGRATION_SCHEMA.read_text(encoding="utf-8"))["$defs"])


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
    assert_the_adr_states(
        "`domain/enums.py::may_surface` (with `include_unapproved=False`, which admits "
        "`APPROVED` alone out of `SURFACEABLE_STATUSES = {APPROVED, DRAFT, PROPOSED}`)",
        because=(
            "Decision 3 defines the exported population by naming this predicate and its "
            "set. A decision that stops naming them defines the bundle's contents by "
            "description, and S2 has to guess which rows the export walks."
        ),
    )
    assert_the_adr_states(
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
    assert_the_adr_states(
        "a closed 14-member `RelationType`",
        because=(
            "Decision 4 names the size of the vocabulary, and the alternatives table "
            "and Consequences repeat it. Widening it is roadmap §9 ADR candidate 3, "
            "which owes a compatibility policy first."
        ),
    )
    assert_the_adr_states(
        "mirrored by `$defs/relationType` in the migration schema",
        because=(
            "Decision 5's `a bundle cannot widen the enum by asserting a new value` is "
            "enforced by that mirror and by nothing else. Pinned as its own clause, so "
            "that correcting the line-number citation beside it does not redden a "
            "vocabulary claim that did not move."
        ),
    )
    assert_the_adr_states(
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
    assert_the_adr_states(
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

#: The two anchor fields the canonical store holds and the serve path does not
#: publish, which is why decision 7 drops rather than exports them.
WITHHELD_ANCHOR_FIELDS: Final[frozenset[str]] = frozenset({"blobSha", "externalId"})


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
    current = revision()
    anchor = current.source_anchors[0]

    assert anchor.blob_sha and anchor.external_id, (
        "the fixture's anchor must carry `blob_sha` and `external_id` before their "
        "absence from the payload can mean the serve path withholds them"
    )

    payload = result_payload(
        current,
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
        revision(),
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
    assert_the_adr_states(
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
    assert_the_adr_states(
        "**The two anchor fields that payload does not carry, `blobSha` and "
        "`externalId`, are therefore not exported either**",
        because=(
            "The dropped table's `blobSha`, `externalId` row points at this sentence "
            "for its reason. Losing it leaves the drop unexplained, and an S2 "
            "implementer with no ground to refuse a request to export them."
        ),
    )
