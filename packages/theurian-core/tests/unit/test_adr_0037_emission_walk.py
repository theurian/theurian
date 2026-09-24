"""The walk over everything ADR-0037's export emits, and what licenses each byte.

Two sibling modules pin the instruments: ``test_adr_0037_claims.py`` holds the
constants the ADR measured, and ``test_adr_0037_knowledge_get_bound.py`` the
second payload its bound is drawn on. This module holds the claim they are
*for*: decision 7's universal, that **the projection publishes nothing outside
the measured union except the five recorded widenings**, and decision 2's
closure of it, that **every byte of the bundle belongs to exactly one
byte-source family**. Compliance says both land with this pull request.

**The walk's population is the emission inventory, not one table**, and that is
the ADR's own correction rather than a preference. Its first draft bounded the
projection by walking decision 7's table alone; ``theurian_body_file`` is
defined in decision 7's *prose*, outside that table, and for one round it drew a
filename extension from an author-written string no served payload publishes.
Scoping an instrument to one table is what let that through, so the population
is assembled from three separate enumerations the ADR ships:

- **decision 7's projection table** -- the OKF slot each governed field lands in;
- **the ``theurian_*`` key spellings** the *Neutral* consequences fix for S2,
  thirteen of them, which is where ``theurian_body_file``,
  ``theurian_export_version``, ``theurian_bundle_digest`` and ``theurian_anchor``
  live and where the table does not reach;
- **the exporter constants** decision 2's *Concept front matter* family admits,
  which is where ``generated.by`` lives -- a byte in every concept document that
  neither of the other two enumerations spells.

Every member holds exactly one of four licenses, each verified against the thing
it names rather than against another table: a served key path against a payload
this module builds, a ``knowledge.get`` addition against that tool's syntax
tree, a widening against the ADR's widening table, an exporter constant against
the family table's bound. An emission in none of them is a byte the export
publishes with nothing licensing it.

Pure: it reads one Markdown file and ``mcp/tools.py``'s AST, and builds one
in-memory revision. No database, no socket, no temporary directory.
"""

from __future__ import annotations

import re
from typing import Final, NamedTuple

import pytest
from adr_0037_support import (
    ADR_0037,
    NOW,
    SERVED_ANCHOR_FIELDS,
    assert_the_adr_states,
    collapsed,
    flowed,
    knowledge_get_additions,
    markdown_table,
    revision,
)

from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.retrieval import RaptorPathSegment
from theurian.mcp.results import result_payload

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Anchors. Every parse below is anchored on a line prefix or a phrase, never on
# a line number: a paragraph added above a table moves its lines, and a pin that
# has to be renumbered is a pin that gets updated without being read.
# ---------------------------------------------------------------------------

#: A prefix rather than the whole heading: what identifies this table is *which
#: decision* it belongs to, as the Compliance section's own reference to it does.
DECISION_7: Final = "### 7."

#: The header row of the widening table, which is its only stable anchor -- the
#: paragraph above it is line-wrapped prose that a rewrap would move.
WIDENING_TABLE: Final = "| Widened |"

FAMILY_TABLE: Final = "#### The bundle's byte sources"

#: The *Neutral* bullet that fixes the ``theurian_*`` spellings, sliced between
#: these two phrases. The opening one is pinned as prose below, because a
#: reworded anchor is a population this walk silently stops reading.
INVENTORY_OPENS: Final = "key spellings are fixed here** so S2 has nothing to invent:"
INVENTORY_CLOSES: Final = ". Snake case"

#: Decision 2's *Concept front matter* bound enumerates its exporter constants
#: after this phrase. Sliced rather than read whole, so a code span added
#: elsewhere in the cell -- a citation, a cross-reference -- does not read as a
#: third constant.
EXPORTER_CONSTANTS_OPEN: Final = "exporter constants:"


# ---------------------------------------------------------------------------
# The emissions, and the license each one holds.
# ---------------------------------------------------------------------------


class _ProjectionRow(NamedTuple):
    """One row of decision 7's table: its Theurian column, and its OKF slot.

    ``okf`` is the code spans of the OKF column rather than its text, so a §
    citation or a reworded note beside the slot does not redden the row set. The
    bundle-path row has none, and that empty tuple is what distinguishes it from
    the ``theurian_item_id`` row that shares its Theurian side.
    """

    theurian: str
    okf: tuple[str, ...]


#: Decision 7's projection table, as ``(Theurian field, OKF slot)`` pairs.
#:
#: Held as row identities beside the emission names below, because a *rewired*
#: row is the failure the emission names cannot see: changing
#: ``| trustLevel | theurian_trust_level |`` to ``| owner | ... |`` leaves the
#: emitted key untouched while the ADR now says it projects an unserved field.
DECISION_7_ROWS: Final[frozenset[_ProjectionRow]] = frozenset(
    {
        _ProjectionRow("kind", ("type",)),
        _ProjectionRow("title", ("title",)),
        _ProjectionRow("contentType", ("theurian_content_type",)),
        _ProjectionRow("item id", ()),
        _ProjectionRow("item id", ("theurian_item_id",)),
        _ProjectionRow("namespace", ("theurian_namespace",)),
        _ProjectionRow("status", ("status",)),
        _ProjectionRow("status", ("theurian_status",)),
        _ProjectionRow("labels", ("tags",)),
        _ProjectionRow("owner", ("theurian_owner",)),
        _ProjectionRow("sourceAnchors[]", ("sources[]",)),
        _ProjectionRow("validTo", ("stale_after",)),
        _ProjectionRow("created_at", ("generated.at",)),
        _ProjectionRow("trustLevel", ("theurian_trust_level",)),
        _ProjectionRow("sensitivity", ("theurian_sensitivity",)),
        _ProjectionRow("relations", ("theurian_relations",)),
        _ProjectionRow("revisionId", ("theurian_revision_id",)),
    }
)

#: The projection rows whose OKF column names no key, each with the emission it
#: contributes instead. One row qualifies: the concept's own path in the bundle,
#: a filename rather than a front-matter key and a byte of the bundle all the
#: same (decision 2's *Paths and names* family).
SPANLESS_ROW_EMISSIONS: Final[dict[_ProjectionRow, str]] = {
    _ProjectionRow("item id", ()): "the concept's path in the bundle",
}

#: The one emission no enumeration spells as a key: the Compliance bullet names
#: it in prose, as "and the body", and the *Concept body* and *Sidecar bytes*
#: families state its bound. Declared so the walk ranges over the single largest
#: thing the bundle carries.
THE_BODY: Final = "the concept body, embedded or as a sidecar"

#: Emissions drawn from a key ``result_payload`` publishes, each mapped to the
#: key path or paths it derives from.
#:
#: ``theurian_body_file`` is the one the ADR's first draft got wrong, and it is
#: **served-derived rather than served**: stem from the item id, extension from
#: ``contentType``, both published, and the canonical body file's own
#: author-written suffix -- neither -- dropped as an input. The ADR's sentence
#: stating that derivation is pinned below.
SERVED_BY_RESULT_PAYLOAD: Final[dict[str, tuple[str, ...]]] = {
    "title": ("title",),
    "status": ("status",),
    "generated.at": ("freshness.revisionCreatedAt",),
    "sources[]": ("sourceAnchors", "sourceAnchors[].sourceUri"),
    "theurian_item_id": ("itemId",),
    "theurian_revision_id": ("revisionId",),
    "theurian_status": ("status",),
    "theurian_trust_level": ("trustLevel",),
    "theurian_sensitivity": ("sensitivity",),
    "theurian_content_type": ("contentType",),
    "theurian_body_file": ("itemId", "contentType"),
    "theurian_anchor": (
        "sourceAnchors[].provider",
        "sourceAnchors[].repository",
        "sourceAnchors[].commitSha",
        "sourceAnchors[].filePath",
        "sourceAnchors[].lineStart",
        "sourceAnchors[].lineEnd",
    ),
    "the concept's path in the bundle": ("itemId",),
}

#: Emissions licensed by the *second* instrument only. ``result_payload`` carries
#: neither the whole body nor a relation, so these two are what ``knowledge.get``
#: buys the bound -- and the ADR says as much of the first: the exported triple
#: "is `knowledge.get`'s triple".
SERVED_BY_A_KNOWLEDGE_GET_ADDITION: Final[dict[str, str]] = {
    "theurian_relations": "relations",
    THE_BODY: "body",
}

#: Emissions outside the union, each mapped to the Theurian field decision 7's
#: widening table records it against.
A_RECORDED_WIDENING: Final[dict[str, str]] = {
    "type": "kind",
    "theurian_namespace": "namespace",
    "tags": "labels",
    "stale_after": "validTo",
    "theurian_owner": "owner",
}

#: Emissions that project no knowledge row at all, each mapped to the
#: byte-source family whose bound admits it.
#:
#: ``theurian_bundle_digest`` is **not** a constant, and it is categorized here
#: on the ADR's own reasoning rather than on the resemblance: decision 2 makes it
#: a digest over the files the export writes, whose "inputs are the bundle and
#: nothing else, which is the property that makes it publishable". A function of
#: bytes the holder already has discloses no row, which is what puts it outside
#: the disclosure bound the other three licenses are drawn against. The manifest
#: family is its bound, and that is what gets checked.
NOT_A_ROW_PROJECTION: Final[dict[str, str]] = {
    "theurian_export_version": "Concept front matter",
    "generated.by": "Concept front matter",
    "theurian_bundle_digest": "The manifest",
}

#: The four licenses, and nothing else. A fifth is a decision to publish on a
#: ground ADR-0037 does not record; it owes a paragraph there before a map here.
LICENSES: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("served by `result_payload`", frozenset(SERVED_BY_RESULT_PAYLOAD)),
    ("served by a `knowledge.get` addition", frozenset(SERVED_BY_A_KNOWLEDGE_GET_ADDITION)),
    ("a recorded widening", frozenset(A_RECORDED_WIDENING)),
    ("not a row projection", frozenset(NOT_A_ROW_PROJECTION)),
)

#: Decision 2's seven byte-source families.
BYTE_SOURCE_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "Paths and names",
        "Concept front matter",
        "Concept body",
        "Sidecar bytes",
        "Index files",
        "The manifest",
        "Bundle-structural constants",
    }
)

# ---------------------------------------------------------------------------
# Readers over the document.
# ---------------------------------------------------------------------------


def _spans(text: str) -> tuple[str, ...]:
    """The code spans of *text*, in order."""
    return tuple(re.findall(r"`([^`]+)`", text))


def _projection_rows() -> tuple[_ProjectionRow, ...]:
    return tuple(
        _ProjectionRow(collapsed(cells[0]), _spans(cells[1]))
        for cells in markdown_table(after=DECISION_7)
    )


def _decision_7_table_headings() -> tuple[str, ...]:
    """The first header cell of every table decision 7 ships, in document order."""
    lines = ADR_0037.read_text(encoding="utf-8").splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip().startswith(DECISION_7)),
        None,
    )

    assert start is not None, (
        f"ADR-0037 has no line starting {DECISION_7!r}; this parse anchors on it."
    )

    headings: list[str] = []
    inside = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if not stripped.startswith("|"):
            inside = False
        elif not inside:
            inside = True
            headings.append(collapsed(stripped.strip("|").split("|")[0]))
    return tuple(headings)


def _widening_rows() -> tuple[tuple[str, str], ...]:
    """The widening table's rows as ``(OKF key, Theurian field)``, its own spelling."""
    rows: list[tuple[str, str]] = []
    for cells in markdown_table(after=WIDENING_TABLE):
        okf, arrow, theurian = collapsed(cells[0]).partition("←")
        assert arrow and theurian.strip(), (
            f"widening row {cells[0]!r} is not `<OKF key> ← <Theurian field>`. The pin "
            f"below reads both halves; a row it cannot split is a widening it cannot check."
        )
        rows.append((okf.strip(), theurian.strip()))
    return tuple(rows)


class _Family(NamedTuple):
    bound: str
    check: str


def _families() -> dict[str, _Family]:
    """Decision 2's byte-source families, by name, with their cells unparsed."""
    families: dict[str, _Family] = {}
    for cells in markdown_table(after=FAMILY_TABLE):
        name = collapsed(cells[0])
        assert name not in families, (
            f"decision 2's family table names {name!r} twice. Every pin below reads this "
            f"as a mapping, so the second row would silently replace the first."
        )
        families[name] = _Family(bound=cells[1], check=cells[2])
    return families


def _exporter_constants() -> tuple[str, ...]:
    """The constants the *Concept front matter* family admits beside the served union."""
    bound = _families()["Concept front matter"].bound
    _, marker, enumerated = bound.partition(EXPORTER_CONSTANTS_OPEN)

    assert marker, (
        f"decision 2's `Concept front matter` bound no longer enumerates its constants "
        f"after {EXPORTER_CONSTANTS_OPEN!r}: it reads {bound!r}. That phrase is where "
        f"this walk reads `generated.by` from, and nothing else in the ADR spells it."
    )
    return _spans(enumerated)


def _key_inventory() -> tuple[str, ...]:
    """The ``theurian_*`` key spellings the *Neutral* consequences fix for S2.

    Sliced out of the bullet's own enumeration rather than filtered by prefix:
    the sentence introducing it contains the literal span ``theurian_*``, and a
    prefix filter that has to exclude that glob would also exclude whatever a
    later key is misspelled as -- a key added to the ADR and invisible to the
    walk that is supposed to license it.
    """
    _, opened, tail = flowed().partition(INVENTORY_OPENS)

    assert opened, (
        f"ADR-0037 no longer says {INVENTORY_OPENS!r}. The `theurian_*` key inventory is "
        f"sliced from that phrase, so this walk would range over the projection table "
        f"alone -- the scoping the ADR corrected after `theurian_body_file` escaped it."
    )

    enumerated, closed, _ = tail.partition(INVENTORY_CLOSES)

    assert closed, (
        f"the `theurian_*` key inventory no longer ends at {INVENTORY_CLOSES!r}, so this "
        f"slice runs into the rest of the document and reads every later code span as a "
        f"key spelling."
    )
    return _spans(enumerated)


def _emission_inventory() -> frozenset[str]:
    """Everything ADR-0037 says the export emits, from all three enumerations."""
    emissions = {THE_BODY, *_key_inventory(), *_exporter_constants()}
    for row in _projection_rows():
        if row.okf:
            emissions.update(row.okf)
            continue
        assert row in SPANLESS_ROW_EMISSIONS, (
            f"decision 7's {row.theurian!r} row names no key in its OKF column, and this "
            f"walk has no emission declared for it. A row whose OKF side is prose emits "
            f"something all the same -- the `item id` row emits the concept's path -- so "
            f"name what it emits and license it, or the walk skips a byte of the bundle."
        )
        emissions.add(SPANLESS_ROW_EMISSIONS[row])
    return frozenset(emissions)


def _served_key_paths() -> frozenset[str]:
    """Every key path ``result_payload`` publishes, on the richest shape it builds.

    Measured with a forest walked, so ``raptorPath`` is inside the set rather than
    outside it: a bound drawn on the unranked shape alone would refuse a row that
    projects a key the serve path does publish.
    """
    payload = result_payload(
        revision(),
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


# ---------------------------------------------------------------------------
# The walk.
# ---------------------------------------------------------------------------


def test_every_emission_the_adr_enumerates_carries_exactly_one_license() -> None:
    """The universal is only a universal because something ranges over it.

    ADR-0037's first draft of decision 7's bound asserted the projection
    publishes nothing the serve path withholds and cited ``result_payload``
    alone; it was false of its own table. The *second* refutation is the one this
    population exists for: the walk over the corrected bound read decision 7's
    table only, and ``theurian_body_file`` is defined in decision 7's prose, so
    an author-written filename suffix reached a bundle path with the pin green.

    **Exactly one license, not at least one.** The four are not interchangeable
    -- ``theurian_relations`` is served by ``knowledge.get`` and by nothing in
    ``result_payload`` -- so an emission holding two is checked against whichever
    instrument a reader happens to read.
    """
    assert [name for name, _ in LICENSES] == [
        "served by `result_payload`",
        "served by a `knowledge.get` addition",
        "a recorded widening",
        "not a row projection",
    ], (
        f"this walk now runs {[name for name, _ in LICENSES]}. A fifth license is a "
        f"decision to publish something on a ground ADR-0037 does not record, which "
        f"owes a paragraph in the ADR before it owes a map here."
    )

    licensed: set[str] = set()
    for name, emissions in LICENSES:
        assert not licensed & emissions, (
            f"{sorted(licensed & emissions)} is licensed twice, once as {name!r}. Each "
            f"license is verified against a different instrument, so an emission holding "
            f"two of them is checked against whichever a reader happens to read."
        )
        licensed |= emissions

    assert licensed == _emission_inventory(), (
        f"ADR-0037's emission inventory and this walk disagree.\n"
        f"  emitted by the ADR, unlicensed here: {sorted(_emission_inventory() - licensed)}\n"
        f"  licensed here, emitted nowhere: {sorted(licensed - _emission_inventory())}\n"
        f"An added emission has to be licensed: drawn from a key `result_payload` "
        f"publishes, drawn from one of `knowledge.get`'s four additions, a widening "
        f"recorded in decision 7's own table, or no projection of a row at all. One "
        f"that is none of the four publishes metadata the serve path withholds, which "
        f"is the defect the ADR's first two drafts shipped."
    )


def test_the_key_inventory_holds_the_thirteen_keys_the_compliance_section_counts() -> None:
    """The count the walk's own governed record states, held against the list.

    The license walk sees a key added *or* removed, because either breaks its set
    equality. What it cannot see is a key added here **and** licensed in the same
    change: every assertion stays green while the Compliance section's *all
    thirteen* -- the sentence that says how wide this walk's subject is -- quietly
    becomes false.
    """
    inventory = _key_inventory()

    assert len(set(inventory)) == len(inventory), (
        f"the `theurian_*` inventory lists a key twice: {list(inventory)}. The walk "
        f"compares sets, so a repeated spelling is invisible to it and the count below "
        f"would be read off a list that does not say what it seems to."
    )
    assert len(inventory) == 13, (
        f"ADR-0037 fixes {len(inventory)} `theurian_*` key spellings: {list(inventory)}. "
        f"The Compliance section says this walk's subject is `all thirteen`, and the "
        f"*Neutral* bullet is the list it counts. A fourteenth key is a front-matter "
        f"key S2 will emit, so it owes a license here and a corrected count there."
    )


def test_every_served_emission_names_a_key_the_serve_path_actually_publishes() -> None:
    """The two licenses that cite an instrument, checked against the instrument.

    A category is worth nothing if it is only ever compared to another table.
    Both maps are verified against something the tree measures: a payload this
    module builds, and the syntax tree of the tool that adds to it. That is what
    makes a *recategorization* fail -- moving ``theurian_owner`` into the served
    map names ``owner``, which no payload publishes, and which the ADR records as
    a widening for exactly that reason.
    """
    served = _served_key_paths()
    additions = knowledge_get_additions()

    for emission, paths in SERVED_BY_RESULT_PAYLOAD.items():
        for path in paths:
            assert path in served, (
                f"ADR-0037 emits {emission!r} off `result_payload`'s {path!r}, which that "
                f"payload no longer publishes: it publishes {sorted(served)}. The export "
                f"would carry metadata the serve path withholds, and decision 7's bound "
                f"has to be re-drawn before S2 ships it."
            )

    for emission, addition in SERVED_BY_A_KNOWLEDGE_GET_ADDITION.items():
        assert addition in additions, (
            f"ADR-0037 emits {emission!r} off `knowledge.get`'s {addition!r}, and that "
            f"tool now adds {sorted(additions)}. Nothing in the serve path publishes the "
            f"value any more, so exporting it is new disclosure."
        )


def test_the_recorded_widenings_are_the_five_rows_the_adr_tables() -> None:
    """The other half of the bound: what the ADR admits it emits outside the union.

    The walk licenses five emissions by calling them widenings, and that license
    is worth exactly as much as the ADR's own table of them. Asserted as
    ``(OKF key, Theurian field)`` pairs rather than as a count, because the
    failure that matters is a widening whose source changed -- ``tags ← labels``
    becoming ``tags ← scope.paths`` keeps the count and exports a field the
    dropped table says has no home.
    """
    tabled = _widening_rows()

    assert len(set(tabled)) == len(tabled), (
        f"the widening table repeats a row: {tabled}. A duplicate hides a member "
        f"from the set comparison below."
    )
    assert set(tabled) == set(A_RECORDED_WIDENING.items()), (
        f"the ADR's widening table and this walk disagree.\n"
        f"  tabled in the ADR, not licensed here: "
        f"{sorted(set(tabled) - set(A_RECORDED_WIDENING.items()))}\n"
        f"  licensed here, not tabled in the ADR: "
        f"{sorted(set(A_RECORDED_WIDENING.items()) - set(tabled))}\n"
        f"Each widening is a recorded decision to publish something outside the "
        f"measured bound. Adding one is a disclosure decision with a justification "
        f"owed; removing one leaves an emission with no license at all."
    )


def test_the_exporter_emissions_are_the_constants_the_family_table_bounds() -> None:
    """The fourth license, checked against the family whose bound admits it.

    ``theurian_export_version`` and ``generated.by`` are admitted by the
    invariant's *and the exporter's own version* clause, and decision 2's
    *Concept front matter* row names **those two** -- a third would be a byte
    of every concept document varying with something the invariant does not
    admit. The row bounds front-matter keys, not every constant spelling.
    """
    families = _families()

    assert _exporter_constants() == ("theurian_export_version", "generated.by"), (
        f"decision 2's `Concept front matter` family admits {list(_exporter_constants())} "
        f"as exporter constants. The ADR bounds them at exactly two, and the invariant "
        f"admits them as `the exporter's own version` -- a third is a byte of every "
        f"concept document that the invariant does not cover."
    )

    for emission, family in NOT_A_ROW_PROJECTION.items():
        assert family in families, (
            f"{emission!r} is licensed by decision 2's {family!r} family, which the "
            f"family table no longer has. Its bound is what says the emission projects "
            f"no row, so the license rests on nothing."
        )
        assert emission in _spans(families[family].bound), (
            f"decision 2's {family!r} bound no longer names {emission!r}: it reads "
            f"{collapsed(families[family].bound)!r}. This walk licenses that emission by "
            f"it projecting no knowledge row, and the family's bound is the only place "
            f"the ADR says so."
        )


def test_the_byte_source_families_are_the_seven_the_adr_enumerates() -> None:
    """The ratchet's fact half: the table a new byte source has to take a row in.

    Decision 2 closes its every-byte invariant by enumeration rather than by
    assertion, after two closure arguments over populations nobody had listed
    were refuted in turn. Its prose ratchet -- a new byte source takes a row here
    *first*, not a bound widened to admit it -- is enforceable only against a
    pinned set: an eighth family arrived without the enumeration being re-read,
    and a deleted one is a bound that stopped being stated while its bytes kept
    being written. The cells are asserted non-empty because that is the closure
    sentence's own requirement, and a row with an empty bound would satisfy the
    set pin while closing nothing.
    """
    families = _families()

    assert set(families) == BYTE_SOURCE_FAMILIES, (
        f"decision 2's byte-source families are {sorted(families)}.\n"
        f"  in the ADR, not pinned here: {sorted(set(families) - BYTE_SOURCE_FAMILIES)}\n"
        f"  pinned here, not in the ADR: {sorted(BYTE_SOURCE_FAMILIES - set(families))}\n"
        f"Every byte of the bundle is supposed to belong to exactly one of these, so a "
        f"family added or removed changes what the closure argument covers."
    )

    for name, family in families.items():
        assert collapsed(family.bound) and collapsed(family.check), (
            f"decision 2's {name!r} row states bound {family.bound!r} and check "
            f"{family.check!r}. The closure sentence requires each family to state both; "
            f"a row missing either closes nothing while keeping the set pin green."
        )


def test_the_projection_table_rows_are_the_ones_this_walk_categorizes() -> None:
    """The row identities, held apart from the emissions they contribute.

    A rewired row is invisible to the emission walk: the emitted key stays
    exactly where it was while the ADR starts saying it projects another field.
    """
    rows = _projection_rows()

    assert len(set(rows)) == len(rows), (
        f"decision 7's table has two identical `(Theurian, OKF slot)` rows in "
        f"{rows}. The comparison below is over a set, so a duplicate would hide one."
    )
    assert set(rows) == DECISION_7_ROWS, (
        f"decision 7's table and this pin disagree about its rows.\n"
        f"  in the ADR, not pinned here: {sorted(set(rows) - DECISION_7_ROWS)}\n"
        f"  pinned here, not in the ADR: {sorted(DECISION_7_ROWS - set(rows))}\n"
        f"A row added or rewired changes which governed field lands in which OKF slot, "
        f"which is the mapping S2 implements from."
    )


def test_decision_7_ships_the_three_tables_this_walk_reads() -> None:
    """A fourth table under decision 7 is a mapping nothing here walks.

    The projection walk reads the first; the widening pin reads the second by its
    own header. A table appended to that section is a governance projection with
    no pin over it, and every other assertion in this module stays green.
    """
    headings = _decision_7_table_headings()

    assert headings == ("Theurian", "Widened", "Dropped"), (
        f"decision 7 ships tables headed {headings}. This walk reads the first two; a "
        f"fourth is mapping that nothing here licenses. Either its rows are licensed "
        f"like the projection table's, or the ADR says why they are not emitted at all."
    )


def test_the_exported_anchor_fields_are_exactly_the_ones_the_serve_path_publishes() -> None:
    """The one emission that is a field list, walked field by field.

    Every other row of decision 7's table projects one value. The
    ``sourceAnchors[]`` row projects a brace list into ``theurian_anchor``, and
    that list is where a disclosure defect would sit unread: adding ``blob_sha``
    to it exports provenance the serve path withholds from the same rows, which
    is precisely the drop the ADR justifies by this payload not carrying it.

    The row is selected by scanning rather than through a dict keyed on the
    Theurian column: decision 7's table holds two ``item id`` rows and two
    ``status`` rows, so such a dict silently keeps one of each pair.
    """
    rows = [
        cells
        for cells in markdown_table(after=DECISION_7)
        if collapsed(cells[0]) == "sourceAnchors[]"
    ]

    assert len(rows) == 1, (
        f"decision 7's table has {len(rows)} `sourceAnchors[]` rows. This walk reads one; "
        f"a second would describe an anchor projection nothing here checks."
    )

    braced = re.search(r"theurian_anchor: \{([^}]*)\}", rows[0][2])

    assert braced is not None, (
        "decision 7's `sourceAnchors[]` row no longer spells its `theurian_anchor: "
        "{ ... }` field list, so this walk reads nothing and would pass over any "
        "field the row now exports."
    )

    snake = [field.strip() for field in braced.group(1).split(",")]
    exported = {re.sub(r"_(.)", lambda match: match.group(1).upper(), field) for field in snake}

    assert exported | {"sourceUri"} == SERVED_ANCHOR_FIELDS, (
        f"decision 7 exports {sorted(exported)} per anchor, beside `sourceUri` as the "
        f"entry's `resource`. The serve path publishes {sorted(SERVED_ANCHOR_FIELDS)}. "
        f"A field here that the payload withholds -- `blobSha` or `externalId` above "
        f"all -- is provenance in a portable artifact that the same rows do not carry "
        f"on the serve path."
    )


# ---------------------------------------------------------------------------
# The prose halves.
# ---------------------------------------------------------------------------


def test_the_adr_still_states_the_union_bound_and_its_five_widenings() -> None:
    """The prose half of the bound: its shape, and the universal drawn on it.

    Two fragments, two different losses. Without the first the bound has no
    stated shape and the walk licenses emissions against something the document
    does not claim. Without the second the widening table reads as commentary
    rather than as the exhaustive exception list the walk treats it as.

    The sentence naming the bound's *second* instrument is pinned beside the
    measurement of it, in ``test_adr_0037_knowledge_get_bound.py``.
    """
    assert_the_adr_states(
        "**What the projection may publish is bounded by two measured instruments, and "
        "five deliberate widenings are recorded against that bound.**",
        because=(
            "It is the shape of the bound this walk ranges over. A document that drops "
            "it leaves an inventory walked against a union it never claims, which is a "
            "pin with no governed record behind it."
        ),
    )
    assert_the_adr_states(
        "**So the universal is: the projection publishes nothing outside that union "
        "except the five widenings above.**",
        because=(
            "This is the claim the walk is the check for, and the ADR says as much -- "
            "`a statement a check can walk`. Losing it leaves the five widenings "
            "reading as examples rather than as the closed exception list."
        ),
    )


def test_the_adr_still_states_the_two_served_inputs_the_body_file_derives_from() -> None:
    """The prose half of the one emission that has a *derivation* rather than a source.

    ``SERVED_BY_RESULT_PAYLOAD`` names ``theurian_body_file``'s two inputs -- the
    item id for the stem, ``contentType`` for the extension -- and checks both
    against the payload. What it cannot check is whether the ADR still derives
    the name from those two and no others: a third input restored to the rule
    would leave every assertion here green, because the map is what declares the
    derivation and the map would not have moved.

    That is not hypothetical. The rejected first draft of the sidecar rule took
    the canonical body file's own suffix as step one -- an author-written string
    no served payload publishes -- and the alternatives table now carries the
    three ways it failed. This fragment is what makes restoring it RED.
    """
    assert_the_adr_states(
        "so `theurian_body_file` is a function of served data end to end: the stem "
        "from the item id, the extension from `contentType`, and `itemId` is served too.",
        because=(
            "It is the derivation `SERVED_BY_RESULT_PAYLOAD` declares for this key, and "
            "the only place the ADR bounds its inputs at two. A third input -- the "
            "rejected `contentFile` suffix above all -- is unserved data reaching a "
            "bundle filename, which is the defect that widened this walk's population."
        ),
    )


def test_the_adr_still_states_the_family_enumeration_and_its_ratchet() -> None:
    """The prose half of decision 2's closure: the enumeration, and the next one's rule.

    The first fragment is the closure argument itself. Decision 2's invariant --
    every byte a function of the exported population and the exporter's version
    -- was closed twice by argument and refuted twice, so the form that survives
    is an enumeration, and this sentence is what says the enumeration is
    exhaustive. Without it the family table reads as a helpful summary and the
    set pinned above bounds nothing.

    The second is the ratchet, the rule that keeps the *next* byte source from
    arriving as a widened bound instead of a row -- which is how both earlier
    closures failed. Being a rule about how the document changes, it is
    enforceable only as prose plus the family set beside it.
    """
    assert_the_adr_states(
        "**every byte of the bundle belongs to exactly one family below, and each "
        "family states its bound and what checks it.**",
        because=(
            "This is the closure argument for decision 2's invariant, and the reason "
            "the family table is a governed enumeration rather than a summary. The pin "
            "on the seven family names is the check for it."
        ),
    )
    assert_the_adr_states(
        "a new byte source takes a row in this table *first*. Not a bound widened to "
        "admit it, not a sentence elsewhere describing it — a row, with its own bound "
        "and its own check.",
        because=(
            "The ratchet is what makes the enumeration survive the next design change. "
            "A `theurian_body_file`-shaped addition described in prose beside the table "
            "is exactly what it forbids, and it was written after that happened twice."
        ),
    )


def test_the_adr_still_states_the_key_inventory_and_the_subject_of_this_walk() -> None:
    """The prose half of the population: the list parsed, and the scope claimed.

    The first fragment is this module's own parse anchor. The inventory is sliced
    out of that sentence, so a rewrite would leave the walk reading the projection
    table alone -- and green. Pinning the anchor is what makes a rewrite RED
    rather than quiet.

    The second is the Compliance sentence that says what the walk must range
    over. It is the governed record this module discharges, and a version of it
    that dropped "the whole emission inventory" would let a table-scoped walk
    claim to satisfy it.
    """
    assert_the_adr_states(
        "**The `theurian_*` key spellings are fixed here** so S2 has nothing to invent:",
        because=(
            "The thirteen-key inventory is sliced out of this sentence. A reworded "
            "anchor does not make the walk fail -- it makes it read one enumeration "
            "fewer, which is the failure mode this module was widened to close."
        ),
    )
    assert_the_adr_states(
        "Its subject is **the whole emission inventory**, not one table: all thirteen "
        "`theurian_*` keys (*Neutral*, third bullet) **and** the OKF-side emissions — "
        "`type`, `title`, `tags`, `status`, `stale_after`, `generated`, `sources[]` and "
        "the body.",
        because=(
            "This is the Compliance obligation this module discharges. Scoping it back "
            "to one table is the defect it exists to catch, and the ADR names the key "
            "that escaped: `theurian_body_file`, defined in prose outside the table."
        ),
    )
