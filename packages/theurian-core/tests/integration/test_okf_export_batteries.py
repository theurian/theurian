"""ADR-0037 slice S2's owed batteries: the invariants a bundle is safe to hand over on.

``test_okf_export.py`` holds the *shape* of each export rule, one rule per test.
This module holds the four batteries its docstring defers and the assembler-side
pins beside them -- the claims that quantify over **every byte of the bundle**
rather than over one field:

* decision 3's two-corpora equality, the disclosure regression the whole design
  rests on, with a marker sweep whose ability to see is proved by flipping one
  withheld row's gate;
* decision 2's determinism, Phase F ②'s exit criterion, over a corpus that
  reaches all four ordering rules;
* decision 3's one-snapshot rule at the *exporter*, driven by a real second
  connection committing a withdrawal mid-walk
  (``test_canonical_read_snapshot.py`` drives the primitive underneath it);
* decision 7's positional reserved-name rule and its sidecar faces;
* decision 2's escaping rule at **both** site kinds, through the full export
  rather than at the codec;
* the manifest's fixed notice and its digest, recomputed from the bytes on disk;
* a negative sweep over every value decision 7's *Dropped without a slot* table
  says has no home, each one first proved to be in the canonical state it is
  swept out of.

**Every zero-assertion here carries a positive control**, because a sweep that
cannot see is indistinguishable from a bundle that holds nothing: the state pair
is asserted to differ, the marker grep is shown finding the markers once the gate
admits the row, and the sweep machinery is shown finding a planted value in a
scratch copy of a bundle file.

The row fixture is re-spelled rather than imported: the suite runs under
``--import-mode=importlib`` with only ``tests/`` on ``sys.path``, so a sibling
module under ``tests/integration/`` is not importable. It carries fields
``test_okf_export.py``'s does not -- the tenant, the ACL group, the scope globs,
the author, the structured payload and the source commit -- because those are the
negative sweep's own population.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
import typer.main
import yaml
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.okf_bundle import (
    INDEX_NAME,
    MANIFEST_NAME,
    Concept,
    render,
)
from theurian.application.okf_export import OkfExporter, OkfExportRequest
from theurian.cli.main import app
from theurian.cli.okf_commands import okf_app
from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import InvalidIdentifierError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeAlias,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.values import (
    MARKDOWN,
    AclGroup,
    MediaType,
    TenantId,
    ValidityPeriod,
)
from theurian.infrastructure.sqlite.connection import (
    create_database,
    open_read_connection,
    write_transaction,
)
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("okf-battery-project")
NOW: Final = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

#: What the deployment in these tests serves.
VISIBLE: Final = frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL})

ANCHOR: Final = SourceAnchor(
    provider="git",
    source_uri="git://okf/.theurian/knowledge/a.md",
    repository="acme/okf",
    commit_sha="a1b2c3d4" * 5,
    file_path=".theurian/knowledge/a.md",
    line_start=1,
    line_end=10,
)
SECOND_ANCHOR: Final = SourceAnchor(provider="confluence", source_uri="https://wiki/second")

YAML_TYPE: Final = MediaType("application/yaml")
OPENAPI: Final = MediaType("application/vnd.oai.openapi")

#: The corpus's own CJK sample (ADR-0023, the CHANGELOG and the codec fixtures
#: all carry it): a body whose bytes and whose characters are different counts,
#: which is what makes the digest's read-the-bytes construction a real claim.
CJK_BODY: Final = "# 認証\n\n署名付きトークンを持つ。\n"


@dataclass(frozen=True, slots=True)
class Row:
    """One knowledge row. The item's status and sensitivity are what the gate reads."""

    item_id: str
    n: int
    title: str = "A title"
    body: str = "A body.\n"
    content_type: MediaType = MARKDOWN
    status: KnowledgeStatus = KnowledgeStatus.APPROVED
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    namespace: str = "backend"
    labels: tuple[str, ...] = ()
    valid_from: datetime = NOW
    valid_to: datetime | None = None
    anchors: tuple[SourceAnchor, ...] = (ANCHOR,)
    author: str = "engineer@example.com"
    tenant_id: str = "local"
    acl_group: str = "default"
    scope_paths: tuple[str, ...] = ()
    structured: dict[str, object] | None = None
    source_commit: str | None = None
    #: Whether the **item** points at its revision. ``False`` is what
    #: ``createItem`` followed by ``restoreItem`` leaves behind: an approved,
    #: in-ceiling item with a `NULL` `current_revision_id`, which produces no
    #: concept document and so can be no relation target inside a bundle.
    has_current_revision: bool = True

    @property
    def revision_id(self) -> RevisionId:
        return RevisionId(f"01K1REV{self.n:03d}01234567890ABCDE")


@dataclass(frozen=True, slots=True)
class Edge:
    """One relation to write."""

    source: str
    target: str
    relation_type: RelationType = RelationType.RELATED_TO
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Alias:
    """One ``addAlias`` row: a retired key and the item it resolves to.

    The corpus fixtures held none until PR #809 round one, which is what let the
    export's relation read resolve an alias unnoticed -- the key is an
    author-chosen string, so an item's own id can be one (T-21).
    """

    key: str
    item_id: str


@dataclass(frozen=True, slots=True)
class Bundle:
    """A written bundle, read back off disk as bytes."""

    root: Path
    report: dict[str, object]
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def text(self) -> dict[str, str]:
        return {name: data.decode("utf-8") for name, data in self.files.items()}

    @property
    def every_byte(self) -> bytes:
        return b"".join(self.files[name] for name in sorted(self.files))

    def front_matter_block(self, name: str) -> str:
        return self.text[name].split("---\n")[1]

    def front_matter(self, name: str) -> dict[str, object]:
        parsed = yaml.safe_load(self.front_matter_block(name))
        assert isinstance(parsed, dict)
        return parsed

    def body(self, name: str) -> str:
        return self.text[name].split("---\n", 2)[2]

    def entries(self, name: str, key: str) -> list[dict[str, object]]:
        """One front-matter list, as mappings -- `sources` or `theurian_relations`."""
        listed = self.front_matter(name)[key]
        assert isinstance(listed, list)
        return [entry for entry in listed if isinstance(entry, dict)]

    def named(self, path: str) -> dict[str, object]:
        return self.report | {"bundlePath": path}


def _project() -> Project:
    return Project(
        project_id=PROJECT,
        root_path="/nonexistent/okf",  # a value, never opened
        repository_url="https://example.com/okf",
        default_branch="main",
        knowledge_directory=PurePosixPath(".theurian"),
        registered_at=NOW,
    )


def _revision(row: Row) -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=row.revision_id,
        item_id=ItemId(row.item_id),
        project_id=PROJECT,
        migration_id=MigrationId("01K1AAAAAA01234567890ABCDE"),
        title=row.title,
        body=row.body,
        content_type=row.content_type,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace=row.namespace,
            status=row.status,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=row.sensitivity,
            owner="platform-team",
            tenant_id=TenantId(row.tenant_id),
            acl_group=AclGroup(row.acl_group),
            scope_paths=row.scope_paths,
            labels=row.labels,
        ),
        validity=ValidityPeriod(valid_from=row.valid_from, valid_to=row.valid_to),
        author=row.author,
        # One second per row, so `generated.at` is distinguishable per concept.
        created_at=NOW + timedelta(seconds=row.n),
        source_commit=row.source_commit,
        source_anchors=row.anchors,
        structured=row.structured,
    )


def _item(row: Row, revision: KnowledgeRevision) -> KnowledgeItem:
    pointing = KnowledgeItem(
        item_id=ItemId(row.item_id),
        project_id=PROJECT,
        namespace=row.namespace,
        kind=KnowledgeKind.ARCHITECTURE,
        status=KnowledgeStatus.DRAFT,
        current_revision_id=None,
        owner="platform-team",
        trust_level=TrustLevel.UNVERIFIED,
        sensitivity=row.sensitivity,
        validity=ValidityPeriod(valid_from=row.valid_from),
    ).with_revision(revision)
    return replace(pointing, status=row.status, sensitivity=row.sensitivity)


def state_hash_of(rows: list[Row]) -> str:
    """A state hash that is a function of the rows, as the real one is.

    A constant here would make the two-corpora battery's own control vacuous: the
    two canonical states have to *differ*, and the state hash is the value
    ADR-0016 says moves when a `rejected`, `draft` or above-ceiling row moves.
    """
    return hashlib.sha256("\n".join(sorted(row.item_id for row in rows)).encode()).hexdigest()


def corpus(
    directory: Path,
    rows: list[Row],
    edges: list[Edge] | None = None,
    aliases: list[Alias] | None = None,
) -> Path:
    """A state database holding ``rows``, ``edges`` and ``aliases``, written the real way."""
    database = directory / "state" / "theurian-state-okf.sqlite"
    lock = directory / "runtime" / "write.lock"
    create_database(database, state_hash=state_hash_of(rows), engine_version=1)
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        for row in rows:
            revision = _revision(row)
            writer.append_revision(revision)
            item = _item(row, revision)
            writer.put_item(
                item if row.has_current_revision else replace(item, current_revision_id=None)
            )
        for edge in edges or []:
            writer.add_relation(
                KnowledgeRelation(
                    project_id=PROJECT,
                    source_item_id=ItemId(edge.source),
                    target_item_id=ItemId(edge.target),
                    relation_type=edge.relation_type,
                    created_at=NOW,
                    note=edge.note,
                )
            )
        for alias in aliases or []:
            writer.add_alias(
                KnowledgeAlias(
                    alias=ItemId(alias.key),
                    item_id=ItemId(alias.item_id),
                    project_id=PROJECT,
                    created_at=NOW,
                )
            )
    return database


def export(
    database: Path,
    target: Path,
    *,
    visible: frozenset[Sensitivity] = VISIBLE,
    store_factory: Any = SqliteCanonicalStore,
) -> Bundle:
    report = OkfExporter(store_factory=store_factory).export(
        OkfExportRequest(
            database=database,
            output_directory=target,
            project_id=PROJECT.value,
            visible_sensitivities=visible,
        )
    )
    return Bundle(root=target, report=report, files=_read(target))


def _read(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def bundle_of(
    tmp_path: Path,
    rows: list[Row],
    edges: list[Edge] | None = None,
    *,
    name: str = "one",
) -> Bundle:
    return export(corpus(tmp_path / name, rows, edges), tmp_path / f"bundle-{name}")


#: The generated `## Relations` heading, as a **line**.
#:
#: Anchored rather than searched as a substring, and that is what tells it from a
#: note's escaped copy: ``escape_markdown_list_line`` renders the note's own
#: `## Relations` as `    \## Relations`, which still *contains* the substring
#: `## Relations\n` -- measured, by a `rpartition` on that substring landing
#: inside the note and reading the section as three lines where five were
#: rendered. The escape defeats CommonMark's ATX rule, not a substring search.
_RELATIONS_HEADING_LINE: Final = re.compile(r"(?m)^## Relations$\n")


def _relations_section_of(document: str) -> str:
    """Everything after the generated `## Relations` heading.

    The **last** match, not the first: the body is not escaped at all -- it is
    preserved verbatim (ADR-0010 rule 5) -- so an authored document whose own
    prose opens a `## Relations` line would otherwise have that read as the
    exporter's section. The generated one is always last.
    """
    return _RELATIONS_HEADING_LINE.split(document)[-1]


def _counts(database: Path) -> dict[str, int]:
    with closing(open_read_connection(database)) as probe:
        return {
            table: probe.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
            for table in ("knowledge_items", "knowledge_revisions", "knowledge_relations")
        }


# ---------------------------------------------------------------------------
# Battery 1: the two-corpora equality (decision 3).
# ---------------------------------------------------------------------------

#: Every byte a withheld row could contribute, one marker per channel. The item
#: ids are markers too: a relation entry publishes a far end's id whether or not
#: it publishes its body.
DRAFT_ROW: Final = Row(
    "secret-draft",
    3,
    title="withheld-draft-title-4f1c",
    body="withheld-draft-body-7a2e\n",
    status=KnowledgeStatus.DRAFT,
)
REJECTED_ROW: Final = Row(
    "secret-rejected",
    4,
    title="withheld-rejected-title-1b9d",
    body="withheld-rejected-body-3c8f\n",
    status=KnowledgeStatus.REJECTED,
)
CONFIDENTIAL_ROW: Final = Row(
    "secret-confidential",
    5,
    title="withheld-confidential-title-6e5a",
    body="withheld-confidential-body-2d4b\n",
    sensitivity=Sensitivity.CONFIDENTIAL,
)
DEPRECATED_ROW: Final = Row(
    "secret-deprecated",
    6,
    title="withheld-deprecated-title-8f0c",
    body="withheld-deprecated-body-5a1e\n",
    status=KnowledgeStatus.DEPRECATED,
)

SERVED_ROWS: Final = [
    Row("architecture.auth-policy", 1, title="Auth policy", body="# Auth\n\nServed prose.\n"),
    Row("api.orders", 2, title="Orders API"),
]
WITHHELD_ROWS: Final = [DRAFT_ROW, REJECTED_ROW, CONFIDENTIAL_ROW, DEPRECATED_ROW]

SERVED_EDGE: Final = Edge(
    "architecture.auth-policy", "api.orders", RelationType.RELATED_TO, note="a published reason"
)
#: One edge per orientation and per invertibility class, each into or out of a
#: withheld row. The two `IMPLEMENTS` members are the ones `list_relations`
#: rewrites: it synthesises the inverse, so a withheld source arrives at a served
#: item's own query as that item's outgoing edge and the source filter passes it.
WITHHELD_EDGES: Final = [
    Edge(
        "architecture.auth-policy",
        "secret-confidential",
        RelationType.DEPENDS_ON,
        note="withheld-outgoing-note-9b3d",
    ),
    Edge(
        "secret-confidential",
        "api.orders",
        RelationType.DEPENDS_ON,
        note="withheld-incoming-note-0c7a",
    ),
    Edge(
        "architecture.auth-policy",
        "secret-draft",
        RelationType.IMPLEMENTS,
        note="withheld-invertible-out-note-4e6b",
    ),
    Edge(
        "secret-rejected",
        "api.orders",
        RelationType.IMPLEMENTS,
        note="withheld-invertible-in-note-2f8c",
    ),
    Edge(
        "secret-deprecated",
        "architecture.auth-policy",
        RelationType.SUPERSEDES,
        note="withheld-supersedes-note-1d5f",
    ),
]

#: Every string a withheld row owns, across every channel a bundle has.
WITHHELD_MARKERS: Final = tuple(
    [row.item_id for row in WITHHELD_ROWS]
    + [row.title for row in WITHHELD_ROWS]
    + [row.body.strip() for row in WITHHELD_ROWS]
    + [edge.note for edge in WITHHELD_EDGES if edge.note is not None]
)


def test_a_corpus_holding_withheld_rows_exports_the_bundle_of_one_that_never_did(
    tmp_path: Path,
) -> None:
    """Decision 3's two-corpora equality: the disclosure regression this design rests on.

    A withheld row must move no byte of the bundle -- not a file's presence, not
    a file's contents, not the digest. Movement is what carries a bit: a
    displaced index entry or a one-character difference in a served row's
    document is as readable as the row itself would be, and a bundle is the
    easiest artifact to forward.
    """
    withheld = corpus(
        tmp_path / "with", SERVED_ROWS + WITHHELD_ROWS, [SERVED_EDGE, *WITHHELD_EDGES]
    )
    never_held = corpus(tmp_path / "without", SERVED_ROWS, [SERVED_EDGE])

    held = export(withheld, tmp_path / "bundle-with")
    clean = export(never_held, tmp_path / "bundle-without")

    assert _counts(withheld) != _counts(never_held), "the two corpora are the same state"
    assert state_hash_of(SERVED_ROWS + WITHHELD_ROWS) != state_hash_of(SERVED_ROWS)
    assert sorted(held.files) == sorted(clean.files)
    assert held.files == clean.files
    assert held.named("x") == clean.named("x")


def test_no_byte_of_the_bundle_carries_a_withheld_rows_id_title_body_or_note(
    tmp_path: Path,
) -> None:
    """The marker sweep the equality above is belted with, over every bundle byte.

    Equality alone would hold if *both* bundles leaked -- the corpora differ in
    which rows they hold, not in the exporter that read them. This says the
    withheld strings are absent in absolute terms, and
    ``test_the_marker_sweep_finds_a_withheld_row_once_the_gate_admits_it`` is what
    proves the sweep can see them at all.
    """
    bundle = export(
        corpus(tmp_path / "with", SERVED_ROWS + WITHHELD_ROWS, [SERVED_EDGE, *WITHHELD_EDGES]),
        tmp_path / "bundle",
    )

    found = [marker for marker in WITHHELD_MARKERS if marker.encode("utf-8") in bundle.every_byte]

    assert found == []
    assert b"a published reason" in bundle.every_byte, "the served edge's note is missing too"


def test_the_marker_sweep_finds_a_withheld_row_once_the_gate_admits_it(tmp_path: Path) -> None:
    """The positive control for the sweep above: it is looking in the right place.

    One row's sensitivity is lowered into the served ceiling and nothing else
    changes. Its id, title, body and both of its edge notes then appear in the
    bundle -- so a sweep that reported nothing over the withheld corpus was
    reporting an absence rather than failing to look.
    """
    admitted = replace(CONFIDENTIAL_ROW, sensitivity=Sensitivity.INTERNAL)
    rows = [*SERVED_ROWS, DRAFT_ROW, REJECTED_ROW, admitted, DEPRECATED_ROW]

    bundle = export(
        corpus(tmp_path / "admitted", rows, [SERVED_EDGE, *WITHHELD_EDGES]), tmp_path / "bundle"
    )

    whole = bundle.every_byte
    for marker in (
        CONFIDENTIAL_ROW.item_id,
        CONFIDENTIAL_ROW.title,
        CONFIDENTIAL_ROW.body.strip(),
        "withheld-outgoing-note-9b3d",
        "withheld-incoming-note-0c7a",
    ):
        assert marker.encode("utf-8") in whole, marker


# ---------------------------------------------------------------------------
# Battery 1b: an alias key equal to a served item's own id (T-21).
# ---------------------------------------------------------------------------

#: The served item whose id is *also* an ``addAlias`` key. An alias key is an
#: author-chosen string, so nothing stops it naming a live item (T-21).
ALIASED: Final = Row("aliased", 1, title="The aliased item", body="Aliased prose.\n")
PARTNER: Final = Row("partner", 2, title="The partner", body="Partner prose.\n")
#: What the alias points at, held above this deployment's ceiling: every string it
#: owns is a marker, so a read redirected to it is visible as a leak and not only
#: as an omission.
ALIAS_TARGET: Final = Row(
    "alias-target",
    3,
    title="withheld-alias-target-title-7d3e",
    body="withheld-alias-target-body-2c9f\n",
    sensitivity=Sensitivity.CONFIDENTIAL,
)

ALIAS_ROWS: Final = [ALIASED, PARTNER, ALIAS_TARGET]
ALIAS_EDGES: Final = [
    Edge("aliased", "partner", RelationType.DEPENDS_ON, note="the aliased item's own edge"),
    # Incoming and invertible, so it renders on `aliased` only if the inverse
    # mapping is keyed on the id the walk named rather than on a resolved one.
    Edge("partner", "aliased", RelationType.IMPLEMENTS, note="an edge into the aliased item"),
    Edge(
        "alias-target",
        "partner",
        RelationType.DEPENDS_ON,
        note="withheld-alias-target-note-5b1a",
    ),
]
ALIAS_MARKERS: Final = (
    ALIAS_TARGET.item_id,
    ALIAS_TARGET.title,
    ALIAS_TARGET.body.strip(),
    "withheld-alias-target-note-5b1a",
)


def test_an_aliased_items_own_edges_reach_both_channels_and_its_target_reaches_neither(
    tmp_path: Path,
) -> None:
    """T-21's precondition, at the export's relation read (round one, security HIGH).

    ``list_relations`` resolves an alias before it queries, so an approved
    in-ceiling item whose id is also an ``addAlias`` key was answered from the
    *target*'s edges -- every row carrying the target as its source, so the
    walk's source filter discarded all of them and the item shipped
    ``theurian_relations: []`` with an empty ``## Relations`` while
    ``knowledge.get`` published the edge. Both channels are asserted, because the
    front matter and the section are two renderings of one tuple and a fix that
    reached only one of them would be a bundle that contradicts itself.

    The far half is asserted too: nothing the redirected read *could* have
    returned reaches the bundle. Reading by the literal id is what makes both
    true at once, and a read keyed on the resolved id fails whichever half the
    corpus happens to arrange.
    """
    database = corpus(
        tmp_path / "state", ALIAS_ROWS, ALIAS_EDGES, [Alias("aliased", "alias-target")]
    )
    bundle = export(database, tmp_path / "bundle")

    assert [
        (entry["type"], entry["target"], entry["note"])
        for entry in bundle.entries("aliased.md", "theurian_relations")
    ] == [
        ("depends_on", "partner", "the aliased item's own edge"),
        ("implemented_by", "partner", "an edge into the aliased item"),
    ]
    assert _relations_section_of(bundle.text["aliased.md"]) == (
        "\n### depends_on\n\n"
        "* [partner](/partner.md)\n"
        "  * the aliased item's own edge\n"
        "\n### implemented_by\n\n"
        "* [partner](/partner.md)\n"
        "  * an edge into the aliased item\n"
    )
    assert [marker for marker in ALIAS_MARKERS if marker.encode("utf-8") in bundle.every_byte] == []


def test_the_alias_row_really_redirects_the_read_the_export_no_longer_makes(
    tmp_path: Path,
) -> None:
    """The positive control for the pin above: the corpus reaches T-21's precondition.

    Without an alias row in the database the pin asserts an ordinary export and
    says nothing about aliases. Asked of the store directly, because the property
    is the difference between its two reads: the resolving one answers the
    aliased item's query with the *target*'s edge and none of its own, while the
    literal one -- the export's -- answers with its own.
    """
    database = corpus(
        tmp_path / "state", ALIAS_ROWS, ALIAS_EDGES, [Alias("aliased", "alias-target")]
    )
    context = RequestContext(project_id=PROJECT)

    with SqliteCanonicalStore(database) as store:
        aliases = store.list_aliases(context)
        resolving = store.list_relations(context, ItemId("aliased"))
        literal = store.list_relations_by_literal_id(context, ItemId("aliased"))

    assert [(alias.alias.value, alias.item_id.value) for alias in aliases] == [
        ("aliased", "alias-target")
    ]
    assert [(relation.source_item_id.value, relation.note) for relation in resolving] == [
        ("alias-target", "withheld-alias-target-note-5b1a")
    ]
    assert sorted(relation.note or "" for relation in literal) == [
        "an edge into the aliased item",
        "the aliased item's own edge",
    ]


# ---------------------------------------------------------------------------
# Battery 2: determinism (decision 2; Phase F ②'s exit criterion).
# ---------------------------------------------------------------------------

#: A corpus that reaches all four of decision 2's ordering rules: labels and
#: anchors in row order, `(type, target)` over the relation channels including an
#: invertible pair stored both ways, and the bytewise path order over a nested
#: namespace. Plus a sidecar row, a reserved leaf and a CJK body, so the run
#: covers the derivations that are not orderings.
ORDERED_ROWS: Final = [
    Row(
        "architecture.auth.session.policy",
        1,
        title="Session policy",
        labels=("zeta", "alpha", "mid"),
        anchors=(ANCHOR, SECOND_ANCHOR),
        valid_to=NOW + timedelta(days=30),
    ),
    Row("api.orders", 2, title="Orders API", body=CJK_BODY),
    Row("api.schema", 3, title="Orders schema", body='{"a": 1}', content_type=YAML_TYPE),
    Row("architecture.log", 4, title="A reserved leaf"),
    Row("zeta", 5, title="Zeta"),
    Row("alpha", 6, title="Alpha"),
]
ORDERED_EDGES: Final = [
    Edge("zeta", "alpha", RelationType.DEPENDS_ON, note="to alpha"),
    Edge("zeta", "api.orders", RelationType.DEPENDS_ON, note="to orders"),
    Edge("zeta", "api.schema", RelationType.RELATED_TO, note="a second type"),
    # The invertible pair stored both ways: two entries of one type and target on
    # `alpha`, told apart only by their notes, which is where the front matter's
    # key and the body's key stop agreeing.
    Edge("alpha", "zeta", RelationType.IMPLEMENTS, note="the stored direction"),
    Edge("zeta", "alpha", RelationType.IMPLEMENTED_BY, note="an inverse of the same"),
]


def test_two_exports_of_one_canonical_state_write_byte_identical_trees(tmp_path: Path) -> None:
    """Phase F ②'s exit criterion, over a corpus that reaches every ordering rule.

    The criterion is what lets a holder check a bundle for staleness by
    regenerating it and comparing digests, which is the remedy the manifest's
    notice tells them to use instead of trusting the copy they hold.
    """
    database = corpus(tmp_path / "state", ORDERED_ROWS, ORDERED_EDGES)

    first = export(database, tmp_path / "first")
    second = export(database, tmp_path / "second")

    assert first.files == second.files
    assert first.report["bundleDigest"] == second.report["bundleDigest"]
    assert first.named("x") == second.named("x")


def test_the_determinism_corpus_reaches_every_ordering_rule_it_claims_to(tmp_path: Path) -> None:
    """The positive control for the determinism run: its fixture is not a single row.

    A fixture that reached one ordering rule would make the byte-identity above
    hold for a corpus with no sequence in it. Each of decision 2's four orderings
    is asserted present -- labels and anchors in row order, two relation channels
    disagreeing on the invertible pair, and a nested namespace's index chain --
    together with the sidecar and the reserved-leaf escape.
    """
    bundle = export(corpus(tmp_path / "state", ORDERED_ROWS, ORDERED_EDGES), tmp_path / "bundle")

    policy = "architecture/auth/session/policy.md"
    assert bundle.front_matter(policy)["tags"] == ["zeta", "alpha", "mid"], "not in row order"
    assert [entry["resource"] for entry in bundle.entries(policy, "sources")] == [
        ANCHOR.source_uri,
        SECOND_ANCHOR.source_uri,
    ]
    assert [entry["note"] for entry in bundle.entries("alpha.md", "theurian_relations")] == [
        "an inverse of the same",
        "the stored direction",
    ]
    assert _relations_section_of(bundle.body("alpha.md")) == (
        "\n### implements\n\n"
        "* [zeta](/zeta.md)\n"
        "  * the stored direction\n"
        "* [zeta](/zeta.md)\n"
        "  * an inverse of the same\n"
    ), "the body channel is not on `list_relations`'s own order"
    assert sorted(name for name in bundle.files if name.endswith(INDEX_NAME)) == [
        "api/index.md",
        "architecture/auth/index.md",
        "architecture/auth/session/index.md",
        "architecture/index.md",
        "index.md",
    ]
    assert "api/schema.yaml" in bundle.files, "the sidecar row produced no sidecar"
    assert "architecture/log_item.md" in bundle.files, "the reserved leaf did not escape"


def test_the_rendered_bundle_does_not_depend_on_the_order_the_walk_returned(
    tmp_path: Path,
) -> None:
    """Decision 2's invariant says *a function of the population* -- a set, not a sequence.

    Driven at :func:`~theurian.application.okf_bundle.render`, because the walk's
    own order comes from one `ORDER BY` and a mutation to it would be invisible
    through the store. An index that appended entries in walk order rather than
    sorting them would still pass the two runs above and fail here.

    The concepts are the **walk's own**, edges included, rather than a tuple this
    module assembles: built without relations, the permutation reached no
    relation-derived byte at all -- not the `## Relations` section, not
    `theurian_relations`, not the link targets -- and every ordering rule that
    lives in those channels was outside what it measured (round one, adversarial).
    """
    concepts = _walked(tmp_path, ORDERED_ROWS, ORDERED_EDGES)

    forwards = render(concepts)
    backwards = render(tuple(reversed(concepts)))

    assert [concept.item.item_id.value for concept in concepts] != [
        concept.item.item_id.value for concept in reversed(concepts)
    ], "the two orders are the same order"
    assert sum(len(concept.relations) for concept in concepts) >= 5, (
        "the permutation reached no relation-derived byte"
    )
    assert dict(forwards.files) == dict(backwards.files)
    assert forwards.digest == backwards.digest


def _walked(
    tmp_path: Path, rows: list[Row], edges: list[Edge] | None = None
) -> tuple[Concept, ...]:
    """The concepts the real walk produces, relations and all.

    ``_walk`` reads the store and nothing else -- it never touches
    ``output_directory`` -- so this is the population :func:`render` is handed in
    a shipped export, which is what makes a permutation of it a claim about the
    shipped bytes.
    """
    exporter = OkfExporter(store_factory=SqliteCanonicalStore)
    return exporter._walk(
        OkfExportRequest(
            database=corpus(tmp_path / "walked", rows, edges),
            output_directory=tmp_path / "never-written",
            project_id=PROJECT.value,
            visible_sensitivities=VISIBLE,
        )
    )


#: The child's whole program: export the database it is given and print a digest
#: per file plus the manifest's own, and ``hash`` of a fixed string -- which is
#: the control, because it is the one value `PYTHONHASHSEED` is *meant* to move.
_EXPORT_IN_A_CHILD: Final = """
import hashlib, json, sys
from pathlib import Path

from theurian.application.okf_export import OkfExporter, OkfExportRequest
from theurian.domain.enums import Sensitivity
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

database, target, project_id = sys.argv[1], sys.argv[2], sys.argv[3]
report = OkfExporter(store_factory=SqliteCanonicalStore).export(
    OkfExportRequest(
        database=Path(database),
        output_directory=Path(target),
        project_id=project_id,
        visible_sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL}),
    )
)
root = Path(target)
print(json.dumps({
    "digest": report["bundleDigest"],
    "files": {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    },
    "probe": hash("a-string-whose-hash-is-seeded"),
}))
"""


def _export_under_a_hash_seed(database: Path, target: Path, seed: str) -> dict[str, Any]:
    child = subprocess.run(  # noqa: S603 - this interpreter, a literal program
        [sys.executable, "-c", _EXPORT_IN_A_CHILD, str(database), str(target), PROJECT.value],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": seed},
    )
    answered = json.loads(child.stdout)
    assert isinstance(answered, dict)
    return answered


def test_two_exports_under_different_hash_seeds_agree_byte_for_byte(tmp_path: Path) -> None:
    """Determinism *across processes*, which is the property a holder can use.

    Both runs above share one interpreter, so every set and dict in them iterates
    in one process's order: a byte that varied with `PYTHONHASHSEED` -- a set
    iterated into an index listing, a dict whose order reached a digest -- would
    be identical in both and invisible to them. The manifest tells a holder to
    regenerate the bundle and compare `theurian_bundle_digest`, and they will do
    that in a different process from the one that produced the copy they hold.

    Two children rather than one, with the parent's own seed left alone: the claim
    is that no seed moves a byte, and comparing against the parent would only say
    that one other seed agrees with whatever pytest happened to run under. The
    ``probe`` hash is the control -- it differs between the two children, so the
    seeds really did differ.
    """
    database = corpus(tmp_path / "state", ORDERED_ROWS, ORDERED_EDGES)
    in_process = export(database, tmp_path / "parent")

    first = _export_under_a_hash_seed(database, tmp_path / "seed-one", "1")
    second = _export_under_a_hash_seed(database, tmp_path / "seed-two", "4294967295")

    assert first["probe"] != second["probe"], "both children ran under the same hash seed"
    assert first["digest"] == in_process.report["bundleDigest"]
    assert second["digest"] == in_process.report["bundleDigest"]
    expected = {name: hashlib.sha256(data).hexdigest() for name, data in in_process.files.items()}
    assert first["files"] == expected
    assert second["files"] == expected


# ---------------------------------------------------------------------------
# Battery 3: one snapshot, mid-walk (decision 3).
# ---------------------------------------------------------------------------

DOOMED: Final = "architecture.doomed"
SNAPSHOT_ROWS: Final = [
    Row("architecture.auth-policy", 1, title="Auth policy"),
    Row(DOOMED, 2, title="About to be withdrawn", body="still-served-body-3f1a\n"),
]
SNAPSHOT_EDGES: Final = [
    Edge("architecture.auth-policy", DOOMED, RelationType.DEPENDS_ON, note="an edge to the doomed")
]
#: The note on the edge the interleaved write adds. It is the byte that makes the
#: pin able to fail: a status flip alone is read only by ``list_items``, which the
#: walk has already made, so a bundle built without the snapshot would be
#: byte-identical anyway and the pin would be measuring nothing.
LATE_EDGE_NOTE: Final = "an edge added after the walk began"


def land_the_interleaved_writes(database: Path, lock: Path) -> None:
    """The mid-walk write, issued the way ``migrate apply`` issues one.

    Two statements in one transaction, and the pair is deliberate. The
    **withdrawal** is what decision 3 names; the **new edge** is what any read
    after ``list_items`` can still see, so it is what a walk without the snapshot
    would publish -- and it publishes it *gated against the pre-write visible
    set*, which is the straddle: an edge to a row that is withdrawn at the moment
    the edge is read.
    """
    with write_transaction(database, lock) as connection:
        connection.execute(
            "UPDATE knowledge_items SET status = ? WHERE item_id = ?",
            (KnowledgeStatus.DEPRECATED.value, DOOMED),
        )
        SqliteWriter(connection).add_relation(
            KnowledgeRelation(
                project_id=PROJECT,
                source_item_id=ItemId("architecture.auth-policy"),
                target_item_id=ItemId(DOOMED),
                relation_type=RelationType.RELATED_TO,
                created_at=NOW,
                note=LATE_EDGE_NOTE,
            )
        )


class Withdrawing:
    """A session that lands a real withdrawal through a *second* connection mid-walk.

    A delegating wrapper rather than a subclass, because ``SqliteCanonicalStore``
    is ``@final``; satisfying ``OkfExportSession`` structurally is also what
    proves the export is typed against the Protocol. The write fires once,
    immediately after the walk's first read returns, and is committed by
    ``write_transaction`` -- ``BEGIN IMMEDIATE``, the statements, ``COMMIT`` --
    which is how ``migrate apply`` writes.

    ``committed_mid_walk`` is the control: a *fresh* reader is opened straight
    after the commit and asked what it can see. If the write had not landed, the
    whole pin would be measuring an interleaving that never happened.
    """

    def __init__(self, database: Path, lock: Path) -> None:
        self.inner = SqliteCanonicalStore(database)
        self.database = database
        self.lock = lock
        self.order: list[str] = []
        self.committed_mid_walk: tuple[str, int] | None = None

    def __enter__(self) -> Withdrawing:
        self.inner.__enter__()
        return self

    def __exit__(self, *details: object) -> None:
        self.inner.__exit__(*details)

    @contextmanager
    def read_snapshot(self) -> Iterator[None]:
        self.order.append("snapshot-opened")
        with self.inner.read_snapshot():
            yield
        self.order.append("snapshot-closed")

    def _withdraw(self) -> None:
        land_the_interleaved_writes(self.database, self.lock)
        self.order.append("withdrawal-committed")
        with closing(open_read_connection(self.database)) as fresh:
            status = fresh.execute(
                "SELECT status FROM knowledge_items WHERE item_id = ?", (DOOMED,)
            ).fetchone()["status"]
            edges = fresh.execute(
                "SELECT COUNT(*) AS n FROM knowledge_relations WHERE note = ?", (LATE_EDGE_NOTE,)
            ).fetchone()["n"]
        self.committed_mid_walk = (status, edges)

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        self.order.append("list_items")
        items = self.inner.list_items(context)
        if self.committed_mid_walk is None:
            self._withdraw()
        return items

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None:
        self.order.append("get_revision")
        return self.inner.get_revision(context, revision_id)

    def list_relations(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRelation, ...]:
        self.order.append("list_relations")
        return self.inner.list_relations(context, item_id)

    def list_relations_by_literal_id(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRelation, ...]:
        self.order.append("list_relations_by_literal_id")
        return self.inner.list_relations_by_literal_id(context, item_id)

    def get_item(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        return self.inner.get_item(context, item_id)

    def get_item_exact(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        return self.inner.get_item_exact(context, item_id)

    def get_item_metadata(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        return self.inner.get_item_metadata(context, item_id)

    def get_item_exact_metadata(
        self, context: RequestContext, item_id: ItemId
    ) -> KnowledgeItem | None:
        return self.inner.get_item_exact_metadata(context, item_id)


def test_a_withdrawal_landing_mid_walk_leaves_the_bundle_wholly_before_it(tmp_path: Path) -> None:
    """Decision 3's own pin: the bundle is on one side of a mid-walk write, never both.

    Without the snapshot the walk straddles two states. The relation reads happen
    after ``list_items``, and the gate they pass through is the *pre-write*
    visible set -- so an edge committed mid-walk to a row withdrawn in the same
    transaction is published on a served concept, pointing at a row the newer
    state no longer serves. That bundle is internally inconsistent in a way no
    consumer can detect, and its digest names files describing a state that never
    existed.
    """
    before = export(corpus(tmp_path / "quiet", SNAPSHOT_ROWS, SNAPSHOT_EDGES), tmp_path / "before")
    contended = corpus(tmp_path / "contended", SNAPSHOT_ROWS, SNAPSHOT_EDGES)
    lock = tmp_path / "contended" / "runtime" / "write.lock"
    sessions: list[Withdrawing] = []

    def withdrawing(path: Path) -> Withdrawing:
        session = Withdrawing(path, lock)
        sessions.append(session)
        return session

    during = export(contended, tmp_path / "during", store_factory=withdrawing)

    assert sessions[0].committed_mid_walk == (KnowledgeStatus.DEPRECATED.value, 1), (
        "the second connection's write never committed, so nothing was interleaved"
    )
    landed = sessions[0].order.index("withdrawal-committed")
    assert "list_relations_by_literal_id" in sessions[0].order[landed:], (
        "no read followed the write"
    )
    assert during.files == before.files
    assert during.named("x") == before.named("x")
    assert LATE_EDGE_NOTE.encode("utf-8") not in during.every_byte


def test_the_same_interleaved_write_does_change_a_later_export(tmp_path: Path) -> None:
    """The positive control for the pin above: the write it ignored is a real one.

    Exported after the transaction has landed, the doomed row is gone and the new
    edge is gated -- and an export of the *same* state with the withdrawal
    reverted publishes that edge, so each statement in the interleaved write
    changes a bundle on its own. ``during == before`` above is therefore the
    snapshot holding rather than a write that would have changed nothing.
    """
    database = corpus(tmp_path / "state", SNAPSHOT_ROWS, SNAPSHOT_EDGES)
    lock = tmp_path / "state" / "runtime" / "write.lock"
    before = export(database, tmp_path / "before")
    land_the_interleaved_writes(database, lock)

    after = export(database, tmp_path / "after")
    with write_transaction(database, lock) as connection:
        connection.execute(
            "UPDATE knowledge_items SET status = ? WHERE item_id = ?",
            (KnowledgeStatus.APPROVED.value, DOOMED),
        )
    restored = export(database, tmp_path / "restored")

    assert after.files != before.files
    assert "architecture/doomed.md" in before.files
    assert "architecture/doomed.md" not in after.files
    assert b"still-served-body-3f1a" not in after.every_byte
    assert b"an edge to the doomed" not in after.every_byte
    assert LATE_EDGE_NOTE.encode("utf-8") not in after.every_byte
    # The edge alone, with both ends served again: the byte the snapshot suppressed.
    assert LATE_EDGE_NOTE.encode("utf-8") in restored.every_byte


# ---------------------------------------------------------------------------
# Battery 4: the reserved-name rule is positional (decision 7).
# ---------------------------------------------------------------------------

RESERVED_ROWS: Final = [
    Row("index", 1, title="Root index row", namespace=""),
    Row("log", 2, title="Root log row", namespace=""),
    Row("theurian-bundle", 3, title="Root manifest row", namespace=""),
    Row("architecture.index", 4, title="Nested index row"),
    Row("architecture.log", 5, title="Nested log row"),
    Row("architecture.theurian-bundle", 6, title="Nested manifest row"),
]


def test_every_reserved_name_escapes_where_it_means_something_and_nowhere_else(
    tmp_path: Path,
) -> None:
    """All six faces of decision 7's positional rule in one corpus, as a whole file set.

    Asserted as the exact set rather than one membership at a time, because the
    failure the rule exists to stop is a *displacement*: a concept written over
    the directory listing or the manifest, or dropped in favour of one. Only the
    whole set says that neither the escaped concept nor the file it would have
    displaced went missing.
    """
    bundle = bundle_of(tmp_path, RESERVED_ROWS)

    assert sorted(bundle.files) == [
        "architecture/index.md",
        "architecture/index_item.md",
        "architecture/log_item.md",
        # Reserved at the bundle root only: nothing at this level for it to
        # displace, so it keeps its own derived name.
        "architecture/theurian-bundle.md",
        "index.md",
        "index_item.md",
        "log_item.md",
        MANIFEST_NAME,
        "theurian-bundle_item.md",
    ]
    assert bundle.front_matter("architecture/theurian-bundle.md")["theurian_item_id"] == (
        "architecture.theurian-bundle"
    )
    assert "theurian_bundle_digest" not in bundle.front_matter("architecture/theurian-bundle.md")
    assert "theurian_item_id" not in bundle.front_matter(MANIFEST_NAME)
    for escaped, item_id in (
        ("index_item.md", "index"),
        ("log_item.md", "log"),
        ("theurian-bundle_item.md", "theurian-bundle"),
        ("architecture/index_item.md", "architecture.index"),
        ("architecture/log_item.md", "architecture.log"),
    ):
        assert bundle.front_matter(escaped)["theurian_item_id"] == item_id


def test_the_index_at_each_level_lists_the_escaped_concept_beside_itself(tmp_path: Path) -> None:
    """The nested face ``test_okf_export.py``'s parametrisation cannot see.

    Its per-case assertion checks the *root* `index.md` survived; the file an
    `architecture.index` row would displace is `architecture/index.md`. Both the
    displaced listing and the escaped concept have to be reachable by following
    indexes, which is what §8's progressive disclosure is for.
    """
    bundle = bundle_of(tmp_path, RESERVED_ROWS)

    nested = bundle.text["architecture/index.md"]
    assert "* [Nested index row](/architecture/index_item.md)" in nested
    assert "* [Nested log row](/architecture/log_item.md)" in nested
    assert "* [Nested manifest row](/architecture/theurian-bundle.md)" in nested
    root = bundle.text[INDEX_NAME]
    assert "* [Root index row](/index_item.md)" in root
    assert "* [architecture](/architecture/)" in root
    assert f"* [Theurian Bundle](/{MANIFEST_NAME})" in root


def test_a_reserved_leafs_sidecar_follows_the_escaped_stem(tmp_path: Path) -> None:
    """Decision 7 says "and its sidecar follows the same stem".

    The sidecar path is derived from the concept's stem, so an escape that ran at
    the concept and not at the sidecar would write `index.json` beside
    `index_item.md` -- a file OKF reserves nothing about, but one whose concept
    document then points at the wrong name.
    """
    bundle = bundle_of(
        tmp_path,
        [
            Row(
                "index",
                1,
                namespace="",
                body='{"a": 1}',
                content_type=MediaType("application/json"),
            )
        ],
    )

    assert sorted(bundle.files) == ["index.md", "index_item.json", "index_item.md", MANIFEST_NAME]
    assert bundle.front_matter("index_item.md")["theurian_body_file"] == "index_item.json"
    assert "[index_item.json](index_item.json)" in bundle.body("index_item.md")


def test_no_item_id_can_be_spelled_as_an_escaped_name() -> None:
    """Why the escape cannot itself collide -- a property of the id grammar.

    ADR-0037 decision 7 and ``okf_bundle._ESCAPE_SUFFIX``'s own comment both rest
    the no-collision claim on this refusal rather than on a check in the
    exporter. An id alphabet that later admitted the underscore would make the
    escape reachable by a second row, silently, with no test between here and a
    bundle where one concept overwrote another.
    """
    for escaped in ("index_item", "log_item", "theurian-bundle_item", "architecture.index_item"):
        with pytest.raises(InvalidIdentifierError):
            ItemId(escaped)


# ---------------------------------------------------------------------------
# Battery 5: the sidecar split (decision 7).
# ---------------------------------------------------------------------------


def test_a_media_type_outside_the_proposal_map_exports_rather_than_refusing(
    tmp_path: Path,
) -> None:
    """The arm the rejected refusing rule would have denied the whole corpus over.

    ``domain/proposal.body_extension`` raises on `application/vnd.oai.openapi`,
    and one hand-authored row carrying it would have refused the export for every
    other row in the project. Asserted over the whole file set, so the row is
    present *and* nothing else was dropped in accommodating it.
    """
    bundle = bundle_of(
        tmp_path,
        [
            Row("served", 1, namespace=""),
            Row("api-spec", 2, namespace="", body="openapi: 3.1.0\n", content_type=OPENAPI),
        ],
    )

    assert sorted(bundle.files) == [
        "api-spec.md",
        "api-spec.txt",
        "index.md",
        "served.md",
        MANIFEST_NAME,
    ]
    assert bundle.files["api-spec.txt"] == b"openapi: 3.1.0\n"
    assert bundle.report["sidecars"] == 1


def test_a_sidecar_and_its_concept_document_are_two_files_neither_overwriting_the_other(
    tmp_path: Path,
) -> None:
    """The collision face: the sidecar extension is never `.md`, so the paths differ.

    The rejected *derive-from-the-body-file-suffix* rule wrote the sidecar at the
    concept document's own path for a row whose `contentType` and `contentFile`
    suffix disagreed, silently losing whichever was written first -- the
    governance front matter, or the body bytes. Both survive here, and the
    concept keeps the whole projection while the sidecar keeps the bytes.
    """
    body = "a: 1\nb: 2\n"
    bundle = bundle_of(tmp_path, [Row("spec", 1, namespace="", body=body, content_type=YAML_TYPE)])

    assert bundle.files["spec.yaml"] == body.encode("utf-8")
    front_matter = bundle.front_matter("spec.md")
    assert front_matter["theurian_body_file"] == "spec.yaml"
    assert front_matter["theurian_item_id"] == "spec"
    assert body not in bundle.text["spec.md"], "the concept document swallowed the body too"
    assert "---" not in bundle.text["spec.yaml"], "the sidecar carries front matter"


def test_the_snapshot_holds_no_body_file_path_for_a_suffix_rule_to_read(tmp_path: Path) -> None:
    """Decision 7's third reason for taking `contentType` alone: it is the only input there is.

    The rejected rule read the canonical body file's own suffix. ADR-0037 records
    that `knowledge_revisions` has no path column, so reading one would have to
    leave decision 3's single transaction -- and this is that claim held against
    the schema rather than against the prose.
    """
    database = corpus(tmp_path / "state", [Row("served", 1)])

    with closing(open_read_connection(database)) as probe:
        columns = {
            row["name"]
            for row in probe.execute("PRAGMA table_info(knowledge_revisions)").fetchall()
        }

    assert columns & {"content_file", "contentFile", "body_file", "body_path", "path"} == set()
    assert "content_type" in columns, "the one input the rule does take is gone"


# ---------------------------------------------------------------------------
# Battery 5b: the same collision, through the real migration path.
# ---------------------------------------------------------------------------

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
#: A YAML body in a file named `.md` -- a hand-authored mistake nothing refuses,
#: and the only way a `contentFile` suffix can reach an export at all.
MISMATCHED_BODY: Final = "openapi: 3.1.0\ninfo:\n  title: Orders\n"

MISMATCHED_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: api.orders-spec
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: api.orders-spec
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/api/orders-spec.md
    contentSha256: {body_pin(MISMATCHED_BODY)}
    metadata:
      title: Orders specification
      contentType: application/yaml
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://okf/orders-spec.md
"""

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A git working tree with an isolated data directory, as the CWD.

    ``THEURIAN_DATA_DIR`` and the working directory are both inside ``tmp_path``,
    so nothing here reaches the developer's own environment -- the pattern
    ``test_cli_commands.py`` sets and ``test_okf_commands.py`` follows.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    if args[:2] == ("migrate", "apply"):
        commit_migrations()
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def test_a_yaml_row_whose_body_file_ends_md_still_exports_two_distinct_files(
    project: Path,
) -> None:
    """The collision case named in ADR-0037's rejected-alternatives table, at its own layer.

    A `contentFile` ending `.md` under `contentType: application/yaml` is the
    plausible hand-authored mistake the rejected suffix rule turned into silent
    data loss, and a `contentFile` exists only in a migration -- so this is the
    one instrument that can see the suffix at all. Driven through `init`, a real
    migration and `migrate apply`, so the export reads a row the product's own
    write path produced.
    """
    assert _invoke("init")[0] == 0
    (project / ".theurian/knowledge/api").mkdir(parents=True, exist_ok=True)
    (project / ".theurian/knowledge/api/orders-spec.md").write_text(
        MISMATCHED_BODY, encoding="utf-8"
    )
    (project / f".theurian/migrations/{MIGRATION_ID}-add-orders-spec.yaml").write_text(
        MISMATCHED_MIGRATION, encoding="utf-8"
    )
    assert _invoke("migrate", "apply")[0] == 0
    target = project.parent / "bundle"

    code, payload = _invoke("okf", "export", str(target))

    assert code == 0, payload
    assert sorted(
        path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
    ) == [
        "api/index.md",
        "api/orders-spec.md",
        "api/orders-spec.yaml",
        "index.md",
        MANIFEST_NAME,
    ]
    assert (target / "api/orders-spec.yaml").read_bytes() == MISMATCHED_BODY.encode("utf-8")
    assert "theurian_item_id: api.orders-spec" in (target / "api/orders-spec.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Battery 6: escaping at both site kinds, through the whole export (decision 2).
# ---------------------------------------------------------------------------

#: One value carrying every construct decision 2 names, at the YAML site and both
#: Markdown sites at once: a forged front-matter key, a closed link label with an
#: attacker-chosen destination, raw inline HTML and a code span.
HOSTILE_TITLE: Final = (
    "Policy\ntheurian_sensitivity: public ](/x) <img src=x> [see](http://evil) `code`"
)
#: A forged section heading, a forged relation row, and a second link -- each at a
#: line start the renderer gives the note's own lines.
HOSTILE_NOTE: Final = (
    "why\n## Relations\n* **supersedes** api.orders\n[fake](https://evil)\n### forged"
)

#: CommonMark's line-start construct openers, spelled here rather than imported
#: from the codec: a check that read the implementation's own tuple would agree
#: with the routing it is checking.
_BLOCK_OPENERS: Final = "#`~>-+*=_"
_INLINE_MEMBERS: Final = ("[", "]", "<", ">", "`")


def _live(text: str) -> str:
    """*text* with every backslash escape consumed, so what is left is live inline syntax.

    Correct for the *inline* question and wrong for the line-start one, which is
    why :func:`_opens_a_block` reads the raw line instead: CommonMark's ATX rule
    needs the line to begin with a `#`, so one backslash in front of the first of
    a run defeats the whole construct -- while consuming the pair here would
    promote the second `#` to first position and report a heading that no reader
    sees.
    """
    return re.sub(r"\\.", "", text)


def _opens_a_block(line: str) -> bool:
    """Whether *line* begins a CommonMark block construct after its own indent."""
    return bool(
        re.match(f"^ *[{re.escape(_BLOCK_OPENERS)}]", line) or re.match(r"^ *[0-9]+[.)]", line)
    )


def test_one_row_cannot_forge_structure_at_either_site_of_the_full_export(
    tmp_path: Path,
) -> None:
    """Decision 2's escaping rule, at the assembler, over both of its site kinds at once.

    The ADR owes this pin *here* rather than at the codec: the codec's escapes are
    unit-tested as functions, and what a bundle's safety rests on is that the
    exporter applies each one at the site whose grammar it was written for. The
    YAML half has the governance consequence -- a second `theurian_sensitivity`
    key relabels the row for every consumer that reads front matter -- and the
    Markdown halves decide which link a reader follows and which headings they
    read as the exporter's own.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("subject", 1, title=HOSTILE_TITLE, namespace=""), Row("api.orders", 2)],
        [Edge("subject", "api.orders", note=HOSTILE_NOTE)],
    )

    concept = bundle.text["subject.md"]
    # (a) The YAML site: one `title`, one `theurian_sensitivity`, and it is the
    # item's own label rather than the one the title asserted.
    assert len(re.findall(r"^theurian_sensitivity:", concept, re.MULTILINE)) == 1
    assert len(re.findall(r"^title:", concept, re.MULTILINE)) == 1
    assert bundle.front_matter("subject.md")["theurian_sensitivity"] == "internal"
    assert bundle.front_matter("subject.md")["title"] == HOSTILE_TITLE
    # (b) The index-entry site: one line, the genuine target, no live inline
    # construct left in the label.
    entries = [line for line in bundle.text[INDEX_NAME].splitlines() if line.startswith("* ")]
    assert len(entries) == 3, entries  # the subject, `api/`, the manifest -- no forged fourth
    subject = [line for line in entries if line.endswith("](/subject.md)")]
    assert len(subject) == 1, entries
    label = subject[0].removeprefix("* [").removesuffix("](/subject.md)")
    assert [member for member in _INLINE_MEMBERS if member in _live(label)] == []
    # (c) The relation-note site: the exporter's own headings, and no live
    # construct opener or inline member in any note line.
    assert [line for line in concept.splitlines() if line.startswith("#")] == [
        "## Relations",
        "### related_to",
    ]
    notes = _note_lines(concept)
    assert len(notes) == len(HOSTILE_NOTE.split("\n")), notes
    for note in notes:
        assert not _opens_a_block(note), note
        assert [member for member in _INLINE_MEMBERS if member in _live(note)] == [], note


def _note_lines(document: str) -> list[str]:
    """The note's own lines out of the `## Relations` section, prefixes removed.

    The exporter renders a note as one list line plus one continuation line per
    further line of the value, so a note that had been split into extra rows or
    folded into one shows up as a different count.
    """
    return [
        line.removeprefix("  * ").removeprefix("    ")
        for line in _relations_section_of(document).splitlines()
        if line.startswith(("  * ", "    "))
    ]


def test_the_hostile_row_would_forge_structure_if_it_were_not_escaped(tmp_path: Path) -> None:
    """The positive control for the battery above: the payload really is live syntax.

    A title and a note that opened no construct would make every assertion above
    hold over inert text. Rendered raw, each construct the payloads carry either
    opens a line or closes a label -- and both values reach the bundle intact, so
    nothing was normalised away before the escape ran.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("subject", 1, title=HOSTILE_TITLE, namespace=""), Row("api.orders", 2)],
        [Edge("subject", "api.orders", note=HOSTILE_NOTE)],
    )

    assert "theurian_sensitivity: public" in HOSTILE_TITLE
    assert [member for member in _INLINE_MEMBERS if member in HOSTILE_TITLE] == list(
        _INLINE_MEMBERS
    )
    assert [line for line in HOSTILE_NOTE.split("\n") if _opens_a_block(line)] == [
        "## Relations",
        "* **supersedes** api.orders",
        "### forged",
    ]
    # Both values survive into the bundle rather than being dropped or truncated,
    # so the escapes are what made them inert.
    assert bundle.front_matter("subject.md")["title"] == HOSTILE_TITLE
    assert [entry["note"] for entry in bundle.entries("subject.md", "theurian_relations")] == [
        HOSTILE_NOTE
    ]


# ---------------------------------------------------------------------------
# Battery 7: the manifest (decision 2).
# ---------------------------------------------------------------------------

#: The holder notice, as sentences rather than as wrapped lines. The wording is a
#: control rather than a courtesy -- it is the only part of the purge residual
#: that travels with the artifact -- so a change to it is a deliberate act and
#: reddens here.
NOTICE_PARAGRAPHS: Final = [
    "# Theurian Bundle",
    "This bundle is a point-in-time copy of approved knowledge, derived from a Theurian "
    "deployment. It is an Index-class artifact: never a record of truth, never to be cited "
    "as team knowledge, and losable without loss.",
    "Nothing that happens in the deployment it came from reaches it. A withdrawal, a "
    "correction, or the removal of a secret applies there and to the indexes built there; it "
    "does not propagate to a copy already distributed, and no part of this bundle can be "
    "updated in place.",
    "So do not read it as current. Regenerate the bundle from the deployment it came from and "
    "compare `theurian_bundle_digest`, rather than trusting what is written here.",
]


def _paragraphs(text: str) -> list[str]:
    return [" ".join(block.split()) for block in text.strip().split("\n\n")]


def test_the_holder_notice_is_the_same_fixed_text_for_every_corpus(tmp_path: Path) -> None:
    """Fixed text naming no deployment (decision 2), measured across three corpora.

    "Fixed" is a claim about the population it does not vary with, so it is
    driven over corpora that differ in every way a corpus can -- including the
    empty one, where the notice is the only prose in the bundle.
    """
    populated = bundle_of(tmp_path, ORDERED_ROWS, ORDERED_EDGES, name="ordered")
    reserved = bundle_of(tmp_path, RESERVED_ROWS, name="reserved")
    empty = bundle_of(tmp_path, [], name="empty")

    assert _paragraphs(populated.body(MANIFEST_NAME)) == NOTICE_PARAGRAPHS
    assert populated.body(MANIFEST_NAME) == reserved.body(MANIFEST_NAME)
    assert populated.body(MANIFEST_NAME) == empty.body(MANIFEST_NAME)
    assert sorted(empty.files) == [INDEX_NAME, MANIFEST_NAME]


def test_the_manifest_names_no_deployment_no_target_and_no_state(tmp_path: Path) -> None:
    """Whoever holds the bundle knows where they got it; the artifact does not say.

    A project id, an operator's directory layout or a state hash in the manifest
    would travel with every copy of the bundle. The state hash is the sharpest of
    the three: ADR-0016 makes it a statistic over every migration in the working
    tree, so it moves when a `rejected`, `draft` or above-ceiling row moves.
    """
    database = corpus(tmp_path / "state", SERVED_ROWS + WITHHELD_ROWS)
    target = tmp_path / "bundle"
    bundle = export(database, target)

    manifest = bundle.text[MANIFEST_NAME]
    for absent in (
        PROJECT.value,
        str(target),
        str(database),
        database.name,
        state_hash_of(SERVED_ROWS + WITHHELD_ROWS),
    ):
        assert absent not in manifest, absent


def test_the_bundle_digest_recomputes_from_the_bytes_on_disk(tmp_path: Path) -> None:
    """The value the manifest publishes, rebuilt in decision 2's stated construction.

    Recomputed from the *bytes* rather than from the rendered strings, and
    compared against the **front-matter** value rather than the report's: what a
    holder can check is the file on disk against the key inside it, and the CJK
    body is what makes the two readings different counts.
    """
    bundle = bundle_of(tmp_path, ORDERED_ROWS, ORDERED_EDGES)

    digest = hashlib.sha256()
    covered = {name: data for name, data in bundle.files.items() if name != MANIFEST_NAME}
    for name in sorted(covered, key=lambda each: each.encode("utf-8")):
        digest.update(f"{name}\n{hashlib.sha256(covered[name]).hexdigest()}\n".encode())

    assert bundle.front_matter(MANIFEST_NAME)["theurian_bundle_digest"] == digest.hexdigest()
    assert bundle.report["bundleDigest"] == digest.hexdigest()
    assert len(covered) == 12, sorted(covered)
    assert CJK_BODY.strip() in bundle.text["api/orders.md"], "the multi-byte body never landed"


# ---------------------------------------------------------------------------
# Battery 8: the values decision 7 drops without a slot.
# ---------------------------------------------------------------------------

#: A row carrying one distinctive value per member of ADR-0037's *Dropped without
#: a slot* table, so each can be swept for by an exact string.
DROPPED: Final = Row(
    "architecture.governed",
    1,
    title="Governed row",
    body="Served prose.\n",
    labels=("published-label",),
    valid_from=datetime(2019, 3, 4, 5, 6, 7, tzinfo=UTC),
    valid_to=NOW + timedelta(days=30),
    anchors=(
        replace(
            ANCHOR,
            blob_sha="b10b" * 16,
            external_id="external-id-marker-5c2f",
        ),
    ),
    author="author-marker-4f1c@example.invalid",
    tenant_id="tenant-marker-7a3e",
    acl_group="acl-marker-91bd",
    scope_paths=("services/payments/marker-2b7a/**",),
    structured={"marker": "structured-marker-8e4d"},
    source_commit="5ca1ab1e" * 5,
)


#: What :data:`DROPPED` plants, by the name ADR-0037's *Dropped without a slot*
#: table gives it. `stateHash` is the one member no revision carries -- it is
#: `schema_metadata`'s, and
#: ``test_the_state_hash_the_bundle_must_not_carry_is_the_one_the_database_records``
#: is where its provenance is checked.
PLANTED: Final[dict[str, str]] = {
    "stateHash": state_hash_of([DROPPED]),
    "blobSha": "b10b" * 16,
    "externalId": "external-id-marker-5c2f",
    "contentSha256": hashlib.sha256(DROPPED.body.encode("utf-8")).hexdigest(),
    "validFrom": DROPPED.valid_from.isoformat(),
    "author": DROPPED.author,
    "tenantId": DROPPED.tenant_id,
    "aclGroup": DROPPED.acl_group,
    "scope.paths": DROPPED.scope_paths[0],
    "structured": "structured-marker-8e4d",
    "sourceCommit": DROPPED.source_commit or "",
    "migrationId": "01K1AAAAAA01234567890ABCDE",
}


def dropped_values(database: Path) -> dict[str, str]:
    """Each dropped value, read off the revision the exporter's own snapshot returns.

    Read through the store rather than off the :data:`DROPPED` fixture, so a value
    is swept for only if the read path really hands it to the exporter. A member
    the query never selected would be absent from a bundle for a reason that has
    nothing to do with decision 7's projection, and the sweep would be reporting a
    property it does not have.
    """
    with SqliteCanonicalStore(database) as store, store.read_snapshot():
        revision = store.get_revision(RequestContext(project_id=PROJECT), DROPPED.revision_id)
    assert revision is not None
    anchor = revision.source_anchors[0]
    assert anchor.blob_sha is not None
    assert anchor.external_id is not None
    assert revision.structured is not None
    return {
        "stateHash": state_hash_of([DROPPED]),
        "blobSha": anchor.blob_sha,
        "externalId": anchor.external_id,
        "contentSha256": revision.content_sha256.value,
        "validFrom": revision.validity.valid_from.isoformat(),
        "author": revision.author,
        "tenantId": revision.metadata.tenant_id.value,
        "aclGroup": revision.metadata.acl_group.value,
        "scope.paths": revision.metadata.scope_paths[0],
        "structured": str(revision.structured["marker"]),
        "sourceCommit": revision.source_commit or "",
        "migrationId": revision.migration_id.value,
    }


def test_every_value_the_projection_drops_reaches_the_exporter_and_no_bundle_byte(
    tmp_path: Path,
) -> None:
    """ADR-0037's *Dropped without a slot* table, swept over every byte of the bundle.

    A belt over the emission walk rather than a restatement of it: the walk
    enumerates what the exporter emits, and this asks the finished artifact
    whether any dropped value arrived by a route nobody enumerated.

    The control is that the swept values are the ones the exporter's **own read**
    returns and the ones the fixture planted -- both, because either alone is
    hollow. Off the fixture only, a value the read path never selects would sweep
    clean for a reason that is not the projection; off the read only, a value the
    fixture left unset would sweep clean because there was nothing to find.
    """
    database = corpus(tmp_path / "state", [DROPPED])
    bundle = export(database, tmp_path / "bundle")
    values = dropped_values(database)

    leaked = [name for name, value in values.items() if value.encode("utf-8") in bundle.every_byte]

    assert values == PLANTED, "the exporter's read is not the row the fixture planted"
    assert leaked == []
    # The row itself did reach the bundle, so the sweep ran over a populated one.
    assert b"published-label" in bundle.every_byte
    assert b"Served prose." in bundle.every_byte


def test_the_state_hash_the_bundle_must_not_carry_is_the_one_the_database_records(
    tmp_path: Path,
) -> None:
    """The swept hex is the value the database records, not a string the test invented.

    The sweep above finds each value in the database *file*, which says a matching
    byte run exists somewhere; this asks `schema_metadata` for the value itself.
    ADR-0016 makes the state hash a statistic over every migration in the working
    tree, which is why it is the member of the dropped table whose presence would
    falsify decision 3's two-corpora equality by construction -- the bundle would
    differ across the two corpora in exactly one field.
    """
    database = corpus(tmp_path / "state", [DROPPED])
    bundle = export(database, tmp_path / "bundle")

    with closing(open_read_connection(database)) as probe:
        recorded = probe.execute("SELECT state_hash FROM schema_metadata").fetchone()["state_hash"]

    assert recorded == state_hash_of([DROPPED])
    assert recorded.encode("utf-8") not in bundle.every_byte


def test_the_sweep_reads_every_file_shape_a_bundle_holds(tmp_path: Path) -> None:
    """The positive control for the sweep: what it can see, measured on disk.

    A value is planted into a **scratch copy** of the exported tree, one file at a
    time, and the sweep is re-run over that copy from the filesystem -- so the
    thing under test is the reader's reach across every path shape a bundle has: a
    root document, a nested one, a nested index, a sidecar whose name does not end
    `.md`, and the manifest. A sweep that globbed one level, or only `*.md`, would
    report a clean bundle for a leak in the file it never opened. The exported
    tree itself is never written to.
    """
    database = corpus(
        tmp_path / "state",
        [
            DROPPED,
            Row("api.schema", 2, body="{}", content_type=YAML_TYPE),
            Row("top", 3, namespace=""),
        ],
    )
    bundle = export(database, tmp_path / "bundle")
    planted_value = dropped_values(database)["author"].encode("utf-8")

    unseen = []
    for name in bundle.files:
        scratch = tmp_path / f"scratch-{name.replace('/', '-')}"
        _copy_tree(bundle.root, scratch)
        target = scratch / name
        target.write_bytes(target.read_bytes() + planted_value)
        copy = Bundle(root=scratch, report={}, files=_read(scratch))
        if planted_value not in copy.every_byte or sorted(copy.files) != sorted(bundle.files):
            unseen.append(name)

    assert unseen == []
    assert sorted(bundle.files) == [
        "api/index.md",
        "api/schema.md",
        "api/schema.yaml",
        "architecture/governed.md",
        "architecture/index.md",
        "index.md",
        MANIFEST_NAME,
        "top.md",
    ]


def _copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        copy = destination / path.relative_to(source)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(path.read_bytes())


def test_no_bundle_file_carries_a_verified_key_or_a_log_document(tmp_path: Path) -> None:
    """Two absences ADR-0037 states as decisions rather than omissions.

    OKF §5.3 derives a consumer's whole trust tier from `verified`, and Theurian
    holds no approving identity to fill it: absence means *unverified*, which is
    the true answer, while a `verified` built from the authoring identity would
    launder authorship into review. §9's `log.md` needs a per-change instant,
    which the no-clock invariant forbids. The corpus holds a row literally named
    `log`, so the filename sweep is over an escape rather than over a bundle that
    never came near one.
    """
    bundle = bundle_of(tmp_path, [DROPPED, Row("log", 2, namespace=""), Row("architecture.log", 3)])

    assert [name for name in bundle.files if PurePosixPath(name).name == "log.md"] == []
    assert "log_item.md" in bundle.files
    assert "architecture/log_item.md" in bundle.files
    for name, text in bundle.text.items():
        assert re.search(r"^ *verified:", text, re.MULTILINE) is None, name


def test_every_link_the_exporter_generates_resolves_inside_the_bundle(tmp_path: Path) -> None:
    """Every path the *exporter* renders is item-id-derived too, not just every path it writes.

    A concept's own filename is one derivation; a relation's link target, an index
    entry's target and the sidecar link are three more, and a `namespace` reaching
    any of them would put `../../../etc/passwd` in a document rather than in a
    filename -- where a check over the written file set cannot see it. The row
    below carries a crafted namespace and sits at both ends of an edge.

    **The population is the generated channels, and the authored body is outside
    it** (round one, code review): the front matter, the `## Relations` section,
    the index files, the manifest and the sidecar paragraph -- listed by
    :func:`_generated_regions`. A link in the body is the *author's*, and the body
    is preserved byte for byte by a recorded decision (ADR-0010 rule 5,
    ADR-0037 decision 7), so a bundle whose author linked out of it is correct
    rather than broken; rewriting or refusing those links is an import-side
    question and routes to S3. The earlier name said "every link target in the
    bundle", which claimed the body too and held only because no fixture body
    carried a link.

    **A target need not be crafted to be broken.** The corpus also holds an
    approved, in-ceiling item with no current revision at the far end of an edge:
    it is a legal relation endpoint in canonical state and produces no concept
    document, so a relation gate keyed on the rows that *cleared the gate* rather
    than on the rows that *became concepts* rendered `/api/pointerless.md` in both
    channels of a bundle that wrote no such file (round one, code review and
    adversarial HIGH).
    """
    crafted = "../../../etc/passwd"
    bundle = bundle_of(
        tmp_path,
        [
            Row("architecture.auth.policy", 1, title="Policy", namespace=crafted),
            Row("api.orders", 2, title="Orders", namespace=crafted),
            Row("api.schema", 3, body="{}", content_type=YAML_TYPE, namespace=crafted),
            Row(
                "api.pointerless",
                4,
                title="Pointerless",
                namespace=crafted,
                has_current_revision=False,
            ),
            Row("api.authored", 5, title="Authored", namespace=crafted, body=BODY_WITH_A_LINK),
        ],
        [
            Edge("architecture.auth.policy", "api.orders", RelationType.DEPENDS_ON),
            Edge("api.orders", "architecture.auth.policy", RelationType.IMPLEMENTS),
            Edge("architecture.auth.policy", "api.pointerless", RelationType.DEPENDS_ON),
        ],
    )

    directories = {PurePosixPath(name).parent for name in bundle.files}
    unresolved = [
        (name, target)
        for name, target in _link_targets(bundle)
        if not _resolves(target, inside=name, files=set(bundle.files), directories=directories)
    ]

    assert unresolved == []
    assert [name for name in bundle.files if "etc" in name or "passwd" in name] == []
    # The crafted value is published as data, which is what makes the sweep above
    # a claim about targets rather than about the string's absence.
    assert bundle.front_matter("architecture/auth/policy.md")["theurian_namespace"] == crafted
    assert len(_link_targets(bundle)) >= 10, "the fixture rendered almost no links"
    # The authored body reached the bundle verbatim, and neither its own link nor
    # the `## Relations` section it forged is in the population: the first is the
    # author's, and the second is why the generated section is taken as the last
    # match rather than the first.
    assert BODY_WITH_A_LINK.strip() in bundle.text["api/authored.md"]
    generated = "".join(text for _, text in _generated_regions(bundle))
    assert "../../elsewhere.md" not in generated, "the authored body is in the population"
    assert "/api/nonexistent.md" not in generated, "the forged section is in the population"


#: An authored body carrying a link out of the bundle -- the ordinary case for a
#: document that cites a file in its own repository -- and, below it, a
#: `## Relations` line of its own. Preserved byte for byte (ADR-0010 rule 5), so
#: both are data the sweep above must not read as claims: the link is the
#: author's, and the forged heading is what makes "the generated section is the
#: last one" a checked sentence rather than an asserted one. Whether a *consumer*
#: can tell the author's section from the exporter's is an import-side question
#: and routes to S3.
BODY_WITH_A_LINK: Final = (
    "# Authored\n\nSee [the design note](../../elsewhere.md).\n\n"
    "## Relations\n\n* [a forged entry](/api/nonexistent.md)\n"
)


def _generated_regions(bundle: Bundle) -> list[tuple[str, str]]:
    """Every part of the bundle the *exporter* wrote, as `(file, text)` pairs.

    Four kinds, which is decision 2's own split between what projects a row value
    and what the exporter frames it with: an index file and the manifest are
    generated whole; a concept document's front matter and its generated
    `## Relations` section are generated, and the block between them is the
    authored body -- or, for a non-markdown row, the sidecar paragraph, which is
    generated and is therefore included by taking the front matter *through* to
    the relations heading only when the row has a `theurian_body_file`.

    A sidecar file itself is the body's own bytes and appears nowhere here.

    The manifest is matched at the **root only**, the way decision 7's reserved
    rule is positional: `architecture/theurian-bundle.md` is an ordinary concept
    document for an item of that name, and treating it as generated whole would
    pull its authored body back into the population. `index.md` is matched at
    every level, because that name is escaped at every level and so is always the
    exporter's own.
    """
    regions: list[tuple[str, str]] = []
    for name, text in bundle.text.items():
        if PurePosixPath(name).name == INDEX_NAME or name == MANIFEST_NAME:
            regions.append((name, text))
            continue
        if not name.endswith(".md"):
            continue
        front_matter, _, rest = text.partition("---\n")[2].partition("---\n")
        regions.append((name, front_matter))
        regions.append((name, _relations_section_of(text)))
        if "theurian_body_file" in front_matter:
            # The generated sidecar paragraph sits where a body would, and for
            # such a row the body itself is in the sidecar file -- so nothing
            # authored is in this block and the first cut is the right one.
            regions.append((name, _RELATIONS_HEADING_LINE.split(rest)[0]))
    return regions


def _link_targets(bundle: Bundle) -> list[tuple[str, str]]:
    return [
        (name, target)
        for name, text in _generated_regions(bundle)
        for target in re.findall(r"\]\(([^)]*)\)", text)
    ]


def _resolves(
    target: str, *, inside: str, files: set[str], directories: set[PurePosixPath]
) -> bool:
    """Whether *target*, read from the file at *inside*, names a bundle member.

    Bundle-absolute (§6.1's recommended form) for a concept or a directory entry,
    and relative for the sidecar link, which sits beside its own document.
    """
    base = PurePosixPath(".") if target.startswith("/") else PurePosixPath(inside).parent
    resolved = (base / target.lstrip("/")).as_posix().removeprefix("./")
    if target.endswith("/"):
        return PurePosixPath(resolved) in directories
    return resolved in files


# ---------------------------------------------------------------------------
# Battery 9: the command's own surface (decision 3).
# ---------------------------------------------------------------------------


def test_the_export_command_registers_exactly_one_argument_and_one_option() -> None:
    """No option widens the population (decision 3), asked of the registered params.

    ``test_okf_commands.py`` asks the same question of the rendered ``--help``,
    and the two instruments are not the same: a ``typer.Option(hidden=True)``
    appears in neither the options list nor the usage line, while a caller can
    still pass it. This one reads the command object, so a hidden flag is in
    scope. It also holds the group to one subcommand -- a second command under
    ``okf`` would be a second population with its own disclosure question.

    Reached through ``getattr`` rather than ``isinstance(node, click.Group)``:
    Typer vendors Click as ``typer._click.core``, so a ``TyperGroup`` is not an
    instance of the ``click.Group`` a test imports, and the check would be
    ``False`` for every group (``tests/command_extraction.py``'s trap).
    """
    okf = typer.main.get_command(app).commands["okf"]  # type: ignore[attr-defined]
    command = okf.commands["export"]

    assert sorted(okf.commands) == ["export"]
    assert [info.name for info in okf_app.registered_commands] == ["export"]
    assert [(param.name, tuple(param.opts), param.param_type_name) for param in command.params] == [
        ("directory", ("directory",), "argument"),
        ("as_json", ("--json",), "option"),
    ]
    argument, option = command.params
    assert argument.required
    assert option.is_flag
    assert not option.default
    assert [param.name for param in command.params if param.hidden] == []
