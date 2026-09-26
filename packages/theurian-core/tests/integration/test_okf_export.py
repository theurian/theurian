"""The OKF bundle export, over a real state database (ADR-0037 decisions 2, 3, 4, 7).

Scoped rather than exhaustive: the two-corpora equality, the determinism battery
and the mid-walk withdrawal pin are ADR-0037's owed S2 batteries and land beside
this file. What is here is the shape of each rule -- the population, the relation
gate, the sidecar split, the reserved-name escape, the index and manifest
shapes, the digest, and the two refusals -- each driven through
``SqliteCanonicalStore`` against a database ``write_transaction`` wrote, because
every one of them is a property of what the store actually returns.

The corpus is built row by row, and each row can set the **item**'s status and
sensitivity independently of the ones its revision's metadata carries. That is
not a test convenience: it is exactly what ``deprecateItem`` and
``changeSensitivity`` leave behind, and reading the revision instead of the item
is how an export would publish a row by the label it was authored under.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Final

import pytest
import yaml

from theurian.application import okf_bundle, okf_export
from theurian.application.okf_bundle import GENERATED_BY, MANIFEST_NAME
from theurian.application.okf_codec import EXPORT_VERSION
from theurian.application.okf_export import OkfExporter, OkfExportError, OkfExportRequest
from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.values import MARKDOWN, MediaType, ValidityPeriod
from theurian.infrastructure.sqlite.connection import (
    create_database,
    open_read_connection,
    write_transaction,
)
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

#: Where a POSIX mode decides nothing, so a test that turns on one must not run:
#: Windows has none to deny with, and root is exempt from the ones it has -- the
#: offline CI image runs as root, where a directory at 0500 takes a ``mkdir`` as
#: readily as one at 0700. The suite's standing idiom.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

PROJECT: Final = ProjectId("demo")
NOW: Final = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
ANCHOR: Final = SourceAnchor(
    provider="git",
    source_uri="git://demo/.theurian/knowledge/a.md",
    repository="acme/demo",
    commit_sha="a1b2c3d4" * 5,
    file_path=".theurian/knowledge/a.md",
    line_start=1,
    line_end=10,
)
#: An anchor with no repository, commit, path or line range -- the shape an
#: external source takes, and the one that proves the five optional anchor
#: fields are omitted from `theurian_anchor` rather than emitted null.
EXTERNAL_ANCHOR: Final = SourceAnchor(provider="confluence", source_uri="https://wiki/x")

#: What the deployment in these tests serves.
VISIBLE: Final = frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL})

OPENAPI_JSON: Final = MediaType("application/vnd.oai.openapi+json")
#: A structured type `domain.proposal.body_extension` refuses and decision 7's
#: rule sends to `.txt` -- the arm the first draft of that rule would have
#: refused the whole corpus over.
OPENAPI: Final = MediaType("application/vnd.oai.openapi")


@dataclass(frozen=True, slots=True)
class Row:
    """One knowledge row, with the item's authority separable from its revision's."""

    item_id: str
    n: int
    title: str = "A title"
    body: str = "A body.\n"
    content_type: MediaType = MARKDOWN
    #: The **item**'s status and sensitivity: what the gate reads.
    status: KnowledgeStatus = KnowledgeStatus.APPROVED
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    #: What the *revision's metadata* carries, when it has moved apart from the
    #: item's. ``None`` means the two agree.
    revision_status: KnowledgeStatus | None = None
    revision_sensitivity: Sensitivity | None = None
    namespace: str = "backend"
    labels: tuple[str, ...] = ()
    valid_to: datetime | None = None
    anchors: tuple[SourceAnchor, ...] = (ANCHOR,)

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
class Bundle:
    """A written bundle, read back off disk."""

    root: Path
    report: dict[str, object]
    files: dict[str, str] = field(default_factory=dict)

    def front_matter(self, name: str) -> dict[str, object]:
        block = self.files[name].split("---\n")[1]
        parsed = yaml.safe_load(block)
        assert isinstance(parsed, dict)
        return parsed

    def body(self, name: str) -> str:
        return self.files[name].split("---\n", 2)[2]

    def relations(self, name: str) -> list[dict[str, object]]:
        entries = self.front_matter(name)["theurian_relations"]
        assert isinstance(entries, list)
        return [entry for entry in entries if isinstance(entry, dict)]


def _project() -> Project:
    return Project(
        project_id=PROJECT,
        root_path="/nonexistent/demo",  # a value, never opened
        repository_url="https://example.com/demo",
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
            status=row.revision_status or row.status,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=row.revision_sensitivity or row.sensitivity,
            owner="platform-team",
            labels=row.labels,
        ),
        validity=ValidityPeriod(valid_from=NOW, valid_to=row.valid_to),
        author="engineer@example.com",
        # One second per row, so `generated.at` is distinguishable per concept
        # and cannot accidentally equal an export-time instant.
        created_at=NOW + timedelta(seconds=row.n),
        source_anchors=row.anchors,
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
        validity=ValidityPeriod(valid_from=NOW),
    ).with_revision(revision)
    # `with_revision` adopts the revision's status; a `deprecateItem` or a
    # `changeSensitivity` moves the item's own without writing a revision, which
    # is what these two fields reproduce.
    return replace(pointing, status=row.status, sensitivity=row.sensitivity)


def corpus(tmp_path: Path, rows: list[Row], edges: list[Edge] | None = None) -> Path:
    """A state database holding ``rows`` and ``edges``, written the real way."""
    database = tmp_path / "state" / "theurian-state-okf.sqlite"
    lock = tmp_path / "runtime" / "write.lock"
    create_database(database, state_hash="a" * 64, engine_version=1)
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(_project())
        for row in rows:
            revision = _revision(row)
            writer.append_revision(revision)
            writer.put_item(_item(row, revision))
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
    return database


def export(
    database: Path,
    target: Path,
    *,
    visible: frozenset[Sensitivity] = VISIBLE,
) -> Bundle:
    return Bundle(
        root=target, report=_report(database, target, visible=visible), files=_read(target)
    )


def _report(
    database: Path, target: Path, *, visible: frozenset[Sensitivity] = VISIBLE
) -> dict[str, object]:
    """The export's own report, for a target whose bytes the caller reads back itself.

    ``export`` above reads the bundle through the path it passed, which a target
    that names its directory by traversal (``inner/absent/..``) or through a link
    cannot do: those land at the canonical path the report publishes.
    """
    return OkfExporter(store_factory=SqliteCanonicalStore).export(
        OkfExportRequest(
            database=database,
            output_directory=target,
            project_id=PROJECT.value,
            visible_sensitivities=visible,
        )
    )


def _read(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def bundle_of(tmp_path: Path, rows: list[Row], edges: list[Edge] | None = None) -> Bundle:
    return export(corpus(tmp_path, rows, edges), tmp_path / "bundle")


# -- The population (decision 3) --------------------------------------------


def test_only_approved_in_ceiling_rows_become_concepts(tmp_path: Path) -> None:
    bundle = bundle_of(
        tmp_path,
        [
            Row("keeper", 1),
            Row("a-draft", 2, status=KnowledgeStatus.DRAFT),
            Row("retired", 3, status=KnowledgeStatus.DEPRECATED),
            Row("rejected-one", 4, status=KnowledgeStatus.REJECTED),
            Row("too-secret", 5, sensitivity=Sensitivity.CONFIDENTIAL),
        ],
    )

    assert sorted(name for name in bundle.files if name.endswith(".md")) == [
        "index.md",
        "keeper.md",
        MANIFEST_NAME,
    ]
    assert bundle.report["concepts"] == 1


def test_the_gate_reads_the_item_and_never_the_revisions_metadata(tmp_path: Path) -> None:
    """`deprecateItem` and `changeSensitivity` move the item alone (ADR-0005).

    Both directions, because reading the wrong one fails both ways: a row
    retired or reclassified after its last revision would be exported on the
    label it was authored under, and a row *approved* after a draft revision
    would be left out of a bundle that claims to hold what the deployment
    serves.
    """
    bundle = bundle_of(
        tmp_path,
        [
            Row("retired-since", 1, status=KnowledgeStatus.DEPRECATED),
            Row("raised-since", 2, sensitivity=Sensitivity.CONFIDENTIAL),
            Row("approved-since", 3, revision_status=KnowledgeStatus.DRAFT),
            Row("lowered-since", 4, revision_sensitivity=Sensitivity.CONFIDENTIAL),
        ],
    )

    assert sorted(name for name in bundle.files if name.endswith(".md")) == [
        "approved-since.md",
        "index.md",
        "lowered-since.md",
        MANIFEST_NAME,
    ]
    # And the published labels are the item's, not the revision's.
    assert bundle.front_matter("approved-since.md")["theurian_status"] == "approved"
    assert bundle.front_matter("lowered-since.md")["theurian_sensitivity"] == "internal"


def test_an_item_with_no_current_revision_is_no_concept_and_no_relation_target(
    tmp_path: Path,
) -> None:
    """The relation gate's population is what *became a concept* (decision 4).

    An approved, in-ceiling item with a `NULL` `current_revision_id` is what
    `createItem` plus `restoreItem` leaves behind, and it is a legal relation
    endpoint in canonical state: `knowledge.get` publishes an edge to it, which is
    why `index_builder` and `mcp.tools._relation_is_visible` gate on the set that
    cleared both authority filters. A bundle cannot follow them there -- it
    *renders a link* to the far end's concept document, and this item produces no
    such file -- so gating on that wider set put `/has-none.md` in both channels
    of a bundle that wrote no such member (round one, code review and adversarial
    HIGH).

    Both channels and the file set, because the link is rendered twice from one
    tuple and the whole claim is that no rendered target is missing from the tree.
    """
    database = corpus(tmp_path, [Row("has-one", 1)])
    lock = tmp_path / "runtime" / "write.lock"
    pointerless = Row("has-none", 2)
    with write_transaction(database, lock) as connection:
        writer = SqliteWriter(connection)
        writer.put_item(
            replace(
                _item(pointerless, _revision(pointerless)),
                current_revision_id=None,
            )
        )
        writer.add_relation(
            KnowledgeRelation(
                project_id=PROJECT,
                source_item_id=ItemId("has-one"),
                target_item_id=ItemId("has-none"),
                relation_type=RelationType.DEPENDS_ON,
                created_at=NOW,
                note="an edge to a member no bundle can hold",
            )
        )

    bundle = export(database, tmp_path / "bundle")

    assert "has-none.md" not in bundle.files
    assert bundle.report["concepts"] == 1
    assert bundle.relations("has-one.md") == []
    assert "/has-none.md" not in bundle.files["has-one.md"]
    assert "an edge to a member no bundle can hold" not in "".join(bundle.files.values())


# -- Paths, reserved names and sidecars (decision 7) -----------------------


def test_the_path_comes_from_the_item_id_and_never_from_the_namespace(tmp_path: Path) -> None:
    """`namespace` is free text where `../` is spellable; an `ItemId` is not.

    The pin ADR-0037's Compliance section asks for: a crafted `namespace` must
    not reach a path component, which is why the derivation reads the id's own
    dotted segments (``domain.proposal.body_relative_path``'s reason, one
    artifact over).
    """
    bundle = bundle_of(
        tmp_path,
        [Row("architecture.auth.policy", 1, namespace="../../../etc/passwd")],
    )

    assert "architecture/auth/policy.md" in bundle.files
    assert bundle.front_matter("architecture/auth/policy.md")["theurian_namespace"] == (
        "../../../etc/passwd"
    )
    assert [name for name in bundle.files if "etc" in name] == []


@pytest.mark.parametrize(
    ("item_id", "expected"),
    [
        ("index", "index_item.md"),
        ("log", "log_item.md"),
        ("theurian-bundle", "theurian-bundle_item.md"),
        ("architecture.index", "architecture/index_item.md"),
        ("architecture.log", "architecture/log_item.md"),
        # Reserved at the bundle root only: there is no manifest at this level
        # for it to displace, so renaming it would be a rename with no collision
        # behind it (decision 7's positional rule).
        ("architecture.theurian-bundle", "architecture/theurian-bundle.md"),
    ],
)
def test_a_reserved_leaf_escapes_exactly_where_the_name_means_something(
    tmp_path: Path, item_id: str, expected: str
) -> None:
    bundle = bundle_of(tmp_path, [Row(item_id, 1)])

    assert expected in bundle.files
    # Nothing the escape displaced went missing, and the id is unchanged.
    assert "index.md" in bundle.files
    assert MANIFEST_NAME in bundle.files
    assert bundle.front_matter(expected)["theurian_item_id"] == item_id


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        (MediaType("application/json"), ".json"),
        (MediaType("application/schema+json"), ".json"),
        (MediaType("application/yaml"), ".yaml"),
        (MediaType("text/x-yaml"), ".yaml"),
        (OPENAPI, ".txt"),
        (MediaType("text/plain"), ".txt"),
    ],
)
def test_a_non_markdown_body_becomes_a_sidecar_rather_than_a_refusal(
    tmp_path: Path, content_type: MediaType, extension: str
) -> None:
    """A gate-cleared row is never refused, whatever its media type (decision 7).

    ``OPENAPI`` is the arm that matters: ``domain.proposal.body_extension``
    refuses it, and the first draft of this rule would have denied the whole
    corpus its bundle over one hand-authored row.
    """
    body = '{"not": "markdown"}'
    bundle = bundle_of(tmp_path, [Row("structured", 1, body=body, content_type=content_type)])

    sidecar = f"structured{extension}"
    front_matter = bundle.front_matter("structured.md")
    assert bundle.files[sidecar] == body, "the sidecar is not the snapshot's body"
    assert front_matter["theurian_body_file"] == sidecar
    assert front_matter["theurian_content_type"] == content_type.value
    # The link's text *and* its target are the same derived filename, which is
    # `theurian_body_file`'s own derivation and not a second one.
    assert f"[{sidecar}]({sidecar})" in bundle.body("structured.md")
    assert bundle.report["sidecars"] == 1


def test_a_sidecar_holds_the_body_column_byte_for_byte(tmp_path: Path) -> None:
    """No added newline, no normalisation -- the bytes the revision row holds.

    Compared against the column itself rather than against the literal above,
    so the claim is about the snapshot and not about the fixture.
    """
    body = '{"trailing": "no newline"}'
    database = corpus(tmp_path, [Row("structured", 1, body=body, content_type=OPENAPI_JSON)])
    bundle = export(database, tmp_path / "bundle")

    with closing(open_read_connection(database)) as probe:
        stored = probe.execute("SELECT body FROM knowledge_revisions").fetchone()["body"]

    assert (bundle.root / "structured.json").read_bytes() == stored.encode("utf-8")


def test_a_markdown_body_is_embedded_and_no_sidecar_is_written(tmp_path: Path) -> None:
    bundle = bundle_of(tmp_path, [Row("prose", 1, body="# Heading\n\nText.\n")])

    assert [name for name in bundle.files if not name.endswith(".md")] == []
    assert "# Heading\n\nText." in bundle.body("prose.md")
    assert "theurian_body_file" not in bundle.front_matter("prose.md")


# -- Relations (decision 4) ------------------------------------------------


def test_a_relation_is_exported_only_when_both_endpoints_cleared_the_gate(
    tmp_path: Path,
) -> None:
    """The both-endpoints gate, in both orientations and with a dangling edge.

    The outgoing edge to a withheld item and the incoming edge from one are the
    same disclosure: an edge's target id and its `note` are published whether or
    not the body is, and ``list_relations`` answers in the stored orientation for
    a non-invertible type -- so a gate on the target alone would publish the
    incoming one.
    """
    bundle = bundle_of(
        tmp_path,
        [
            Row("visible-one", 1),
            Row("visible-two", 2),
            Row("hidden", 3, sensitivity=Sensitivity.CONFIDENTIAL),
        ],
        [
            Edge("visible-one", "visible-two", note="a published reason"),
            Edge("visible-one", "hidden", note="REJECTED BECAUSE hidden holds sk-live-9f2a"),
            Edge("hidden", "visible-two", note="an incoming reason"),
            Edge("visible-one", "never-existed", note="a dangling reason"),
        ],
    )

    for name in ("visible-one.md", "visible-two.md"):
        targets = [entry["target"] for entry in bundle.relations(name)]
        assert "hidden" not in targets
        assert "never-existed" not in targets
    whole = "".join(bundle.files.values())
    assert "sk-live-9f2a" not in whole
    assert "an incoming reason" not in whole
    assert "a dangling reason" not in whole
    assert "a published reason" in whole


def test_a_non_invertible_edge_renders_only_on_its_source_document(tmp_path: Path) -> None:
    """The far end of a `depends_on` edge is not itself a `depends_on` source.

    `depends_on` is one of the ten `RelationType` members `INVERSE_RELATIONS`
    does not map, so `list_relations` answers it in the stored orientation when
    queried from its **target**: the edge arrives with the target's own item as
    `target`, and rendering it there would assert `api.orders depends_on
    api.orders` -- a false self-edge, in both channels. The edge renders once,
    on its source, and carries no entry at all on the far end.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("architecture.auth-policy", 1), Row("api.orders", 2)],
        [Edge("architecture.auth-policy", "api.orders", RelationType.DEPENDS_ON)],
    )

    assert bundle.relations("api/orders.md") == []
    assert "[api.orders](/api/orders.md)" not in bundle.body("api/orders.md")
    assert bundle.body("api/orders.md").count("## Relations") == 1
    assert bundle.relations("architecture/auth-policy.md") == [
        {"type": "depends_on", "target": "api.orders"}
    ]
    # Bundle-wide: only one of the two concept documents carries the edge.
    carriers = [
        name
        for name in ("api/orders.md", "architecture/auth-policy.md")
        if any(entry["target"] == "api.orders" for entry in bundle.relations(name))
    ]
    assert carriers == ["architecture/auth-policy.md"]


def test_an_invertible_edge_renders_once_on_each_end_under_its_own_type(tmp_path: Path) -> None:
    """`implements`/`implemented_by`: the two pairs `INVERSE_RELATIONS` maps.

    `list_relations` synthesises the inverse for these, so each end's own query
    already returns `item_id` as the edge's source -- the source filter passes
    both, unlike the ten non-invertible types above.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("api.orders", 1), Row("spec.orders", 2)],
        [Edge("api.orders", "spec.orders", RelationType.IMPLEMENTS)],
    )

    assert bundle.relations("api/orders.md") == [{"type": "implements", "target": "spec.orders"}]
    assert bundle.relations("spec/orders.md") == [
        {"type": "implemented_by", "target": "api.orders"}
    ]


def test_an_invertible_pair_stored_both_ways_orders_each_channel_by_its_own_key(
    tmp_path: Path,
) -> None:
    """The key for `relation_order`'s "they are not the same order".

    `(type, target)` tells every other pair of edges apart -- the primary key on
    `knowledge_relations` is `(project, source, type, target)` -- but not this
    one: `list_relations` maps the incoming `implemented_by` to an outgoing
    `implements`, so `alpha` receives two entries of one type and target carrying
    two different notes. The front matter orders them by note, because the
    codec's key is total over the entry; the body keeps the order
    `list_relations`'s own `ORDER BY source_item_id, relation_type,
    target_item_id` handed the walk. Each order is fixed, and they are not the
    same order.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("alpha", 1), Row("beta", 2)],
        [
            Edge("alpha", "beta", RelationType.IMPLEMENTS, note="the stored direction"),
            Edge("beta", "alpha", RelationType.IMPLEMENTED_BY, note="an inverse of the same"),
        ],
    )

    assert [entry["note"] for entry in bundle.relations("alpha.md")] == [
        "an inverse of the same",
        "the stored direction",
    ]
    assert bundle.body("alpha.md").split("## Relations\n")[1] == (
        "\n### implements\n\n"
        "* [beta](/beta.md)\n"
        "  * the stored direction\n"
        "* [beta](/beta.md)\n"
        "  * an inverse of the same\n"
    )


def test_the_relations_section_renders_the_served_triple_in_order(tmp_path: Path) -> None:
    """Grouped by type, ordered by `(type, target)`, linked bundle-absolutely.

    The link text is the target's **item id** -- the triple's own `target` --
    so the section draws on no second row's fields, and the type is conveyed by
    the heading it sits under (§6.1).
    """
    bundle = bundle_of(
        tmp_path,
        [Row("subject", 1), Row("zeta", 2), Row("alpha", 3), Row("architecture.index", 4)],
        [
            Edge("subject", "zeta", RelationType.DEPENDS_ON),
            Edge("subject", "alpha", RelationType.DEPENDS_ON),
            Edge("subject", "architecture.index", RelationType.SUPERSEDES),
        ],
    )

    body = bundle.body("subject.md")
    assert body.split("## Relations\n")[1] == (
        "\n### depends_on\n\n"
        "* [alpha](/alpha.md)\n"
        "* [zeta](/zeta.md)\n"
        "\n### supersedes\n\n"
        # The far end's path is the escaped one the bundle actually wrote, which
        # is why one derivation answers for both ends of an edge.
        "* [architecture.index](/architecture/index_item.md)\n"
    )
    assert [(entry["type"], entry["target"]) for entry in bundle.relations("subject.md")] == [
        ("depends_on", "alpha"),
        ("depends_on", "zeta"),
        ("supersedes", "architecture.index"),
    ]


def test_a_note_cannot_forge_a_heading_on_any_of_its_lines(tmp_path: Path) -> None:
    """Decision 2's Markdown half, over the whole value rather than its first line.

    The schema bounds a `note` by length alone, so a newline is spellable in it,
    and the renderer gives each of the note's lines a line start of its own. A
    run of `#` opening one of them is an ATX heading inside that list item -- a
    section the note invented, in a document whose headings a consumer reads as
    the exporter's own.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("subject", 1), Row("other", 2)],
        [Edge("subject", "other", note="## Forged\n\n### Second\ntail")],
    )

    body = bundle.body("subject.md")
    assert body.count("## Relations") == 1
    assert "\n## Forged" not in body
    assert "\n### Second" not in body
    assert "\\## Forged" in body
    assert "\\### Second" in body
    # The heading structure a consumer reads is the exporter's own, whatever the
    # note said: one section heading, one type heading.
    assert [line for line in body.splitlines() if line.startswith("#")] == [
        "## Relations",
        "### related_to",
    ]


def test_a_note_that_opens_with_a_newline_renders_no_empty_bullet(tmp_path: Path) -> None:
    """An empty first line took the list marker and a trailing space.

    The value's own bytes are published in `theurian_relations` either way, so
    dropping the leading empties from the *rendered* list costs nothing and keeps
    a generated file free of an item that carries nothing.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("subject", 1), Row("other", 2)],
        [Edge("subject", "other", note="\n\nthe reason, after two blank lines")],
    )

    section = bundle.body("subject.md").split("## Relations\n")[1]
    assert section == (
        "\n### related_to\n\n* [other](/other.md)\n  * the reason, after two blank lines\n"
    )
    assert "  * \n" not in section
    assert bundle.relations("subject.md") == [
        {
            "type": "related_to",
            "target": "other",
            "note": "\n\nthe reason, after two blank lines",
        }
    ]


def test_the_rendered_bundle_hands_out_no_mapping_a_caller_can_edit(tmp_path: Path) -> None:
    """The digest covers exactly the entries `files` holds, so it is a read-only view."""
    database = corpus(tmp_path, [Row("keeper", 1)])
    concepts = OkfExporter(store_factory=SqliteCanonicalStore)._walk(
        OkfExportRequest(
            database=database,
            output_directory=tmp_path / "never-written",
            project_id=PROJECT.value,
            visible_sensitivities=VISIBLE,
        )
    )

    bundle = okf_bundle.render(concepts)

    with pytest.raises(TypeError):
        bundle.files["planted.md"] = "not covered by the digest"  # type: ignore[index]


def test_a_title_cannot_close_an_index_entrys_link_early(tmp_path: Path) -> None:
    """The other structural site decision 2 names, over the codec's whole inline set.

    `]` followed by `(` closes the label early and opens an attacker-chosen
    destination; `<` opens an autolink or raw inline HTML, and a backtick opens a
    code span that runs to the next one and swallows the entry's own
    `](destination)`. Every member is escaped at this site, so the link a
    consumer follows is the one the exporter wrote.
    """
    hostile = "Policy [see](http://evil) ]( oops <img src=x> `code`"
    bundle = bundle_of(tmp_path, [Row("subject", 1, title=hostile)])

    entries = [line for line in bundle.files["index.md"].splitlines() if line.startswith("* ")]
    assert entries == [
        "* [Policy \\[see\\](http://evil) \\]( oops \\<img src=x\\> \\`code\\`](/subject.md)",
        "* [Theurian Bundle](/theurian-bundle.md)",
    ]


# -- Index files and the manifest (decision 2) -----------------------------


def test_every_directory_gets_an_index_even_with_no_concept_of_its_own(
    tmp_path: Path,
) -> None:
    """The narrower rule breaks the walk it exists to support (decision 2).

    `architecture/` holds nothing but a subdirectory here, and skipping its
    index would leave everything beneath it unreachable by following indexes.
    """
    bundle = bundle_of(tmp_path, [Row("architecture.auth.session.policy", 1)])

    assert sorted(name for name in bundle.files if name.endswith("index.md")) == [
        "architecture/auth/index.md",
        "architecture/auth/session/index.md",
        "architecture/index.md",
        "index.md",
    ]
    assert bundle.report["indexes"] == 4
    assert bundle.files["architecture/index.md"].splitlines()[:3] == [
        "# architecture",
        "",
        "* [auth](/architecture/auth/)",
    ]


def test_the_root_index_lists_its_own_files_the_manifest_and_its_subdirectories(
    tmp_path: Path,
) -> None:
    """One section, one list, path-ordered bytewise, no descriptions.

    One total order over concept documents and subdirectories alike, which is
    what decision 2's determinism pin needs -- and `/alpha.md` ahead of
    `/architecture/` is that order rather than files-then-directories: the fifth
    byte decides it, `l` before `r`.
    """
    bundle = bundle_of(
        tmp_path,
        [Row("zeta", 1, title="Zeta"), Row("architecture.policy", 2), Row("alpha", 3, title="Al")],
    )

    assert bundle.files["index.md"] == (
        "---\nokf_version: '0.2'\n---\n"
        "\n"
        "# Theurian Bundle\n"
        "\n"
        "* [Al](/alpha.md)\n"
        "* [architecture](/architecture/)\n"
        "* [Theurian Bundle](/theurian-bundle.md)\n"
        "* [Zeta](/zeta.md)\n"
    )


def test_a_non_root_index_carries_no_front_matter(tmp_path: Path) -> None:
    """§8 permits an index no front matter, with the root's `okf_version` the one
    exception (decision 2)."""
    bundle = bundle_of(tmp_path, [Row("architecture.policy", 1)])

    assert bundle.files["architecture/index.md"].startswith("# architecture\n")
    assert "okf_version" not in bundle.files["architecture/index.md"]


def test_a_sidecar_is_not_an_index_entry(tmp_path: Path) -> None:
    """A sidecar is not a concept document (§3.1), so no index lists one."""
    bundle = bundle_of(tmp_path, [Row("structured", 1, content_type=OPENAPI_JSON, body="{}")])

    assert "structured.json" in bundle.files
    assert "structured.json" not in bundle.files["index.md"]


def test_the_manifest_carries_the_holder_notice_and_two_constants(tmp_path: Path) -> None:
    """The one control in the purge residual that travels with the artifact.

    Its three obligations are what the notice is mandatory *for*, so each is
    asserted rather than the paragraph being compared whole: a point-in-time
    Index-class copy, no later withdrawal reaching it, and regeneration rather
    than trust.
    """
    bundle = bundle_of(tmp_path, [Row("keeper", 1)])

    front_matter = bundle.front_matter(MANIFEST_NAME)
    assert front_matter == {
        "type": "Theurian Bundle",
        "theurian_export_version": EXPORT_VERSION,
        "theurian_bundle_digest": bundle.report["bundleDigest"],
    }
    body = bundle.body(MANIFEST_NAME)
    assert "point-in-time" in body
    assert "Index-class" in body
    assert "withdrawal" in body
    assert "secret" in body
    assert "Regenerate" in body
    # No deployment is named, and no `generated` block exists to carry an instant
    # the manifest does not have.
    assert "demo" not in body
    assert "generated" not in front_matter


def test_the_bundle_digest_covers_every_file_but_the_manifest(tmp_path: Path) -> None:
    """Recomputed from the tree on disk, in decision 2's stated construction."""
    bundle = bundle_of(
        tmp_path,
        [Row("architecture.policy", 1), Row("structured", 2, content_type=OPENAPI_JSON, body="{}")],
    )

    covered = {name: text for name, text in bundle.files.items() if name != MANIFEST_NAME}
    digest = hashlib.sha256()
    for name in sorted(covered, key=lambda each: each.encode("utf-8")):
        digest.update(f"{name}\n{hashlib.sha256(covered[name].encode()).hexdigest()}\n".encode())

    assert bundle.report["bundleDigest"] == digest.hexdigest()
    assert len(covered) == 5, sorted(covered)


def test_no_byte_of_the_bundle_carries_the_moment_it_was_exported(tmp_path: Path) -> None:
    """`generated.at` is the revision's own instant (decision 2).

    Asserted against the revision's `created_at` *and* against the absence of
    the current year-month-day-hour, so a clock reaching any other byte fails
    here too rather than only the one field being checked.
    """
    row = Row("keeper", 1)
    bundle = bundle_of(tmp_path, [row])

    generated = bundle.front_matter("keeper.md")["generated"]
    assert generated == {
        "by": GENERATED_BY,
        "at": (NOW + timedelta(seconds=1)).isoformat(),
    }
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert today not in "".join(bundle.files.values()) or today == NOW.strftime("%Y-%m-%d")


def test_the_report_names_the_bundle_its_counts_and_its_digest(tmp_path: Path) -> None:
    bundle = bundle_of(
        tmp_path,
        [Row("architecture.policy", 1), Row("structured", 2, content_type=OPENAPI_JSON, body="{}")],
    )

    assert bundle.report == {
        "bundlePath": str(bundle.root),
        "concepts": 2,
        "sidecars": 1,
        "indexes": 2,
        "bundleDigest": bundle.front_matter(MANIFEST_NAME)["theurian_bundle_digest"],
    }


# -- Front matter (decision 7) ---------------------------------------------


def test_the_projection_emits_decision_sevens_keys_for_one_row(tmp_path: Path) -> None:
    """One whole front-matter block, so a key appearing or vanishing is caught.

    Two anchors, because the optional five are omitted rather than emitted null:
    the external one's `theurian_anchor` is its `provider` alone, and asserting
    the block whole is what catches a null creeping back in.
    """
    bundle = bundle_of(
        tmp_path,
        [
            Row(
                "architecture.policy",
                1,
                title="Policy",
                labels=("security", "auth"),
                valid_to=NOW + timedelta(days=30),
                anchors=(ANCHOR, EXTERNAL_ANCHOR),
            )
        ],
    )

    assert bundle.front_matter("architecture/policy.md") == {
        "type": "architecture",
        "title": "Policy",
        "tags": ["security", "auth"],
        "status": "stable",
        "stale_after": (NOW + timedelta(days=30)).isoformat(),
        "generated": {"by": GENERATED_BY, "at": (NOW + timedelta(seconds=1)).isoformat()},
        "sources": [
            {
                "resource": ANCHOR.source_uri,
                "theurian_anchor": {
                    "provider": "git",
                    "repository": "acme/demo",
                    "commit_sha": ANCHOR.commit_sha,
                    "file_path": ANCHOR.file_path,
                    "line_start": 1,
                    "line_end": 10,
                },
            },
            {
                "resource": EXTERNAL_ANCHOR.source_uri,
                "theurian_anchor": {"provider": "confluence"},
            },
        ],
        "theurian_export_version": EXPORT_VERSION,
        "theurian_item_id": "architecture.policy",
        "theurian_revision_id": "01K1REV00101234567890ABCDE",
        "theurian_status": "approved",
        "theurian_namespace": "backend",
        "theurian_owner": "platform-team",
        "theurian_trust_level": "reviewed",
        "theurian_sensitivity": "internal",
        "theurian_content_type": "text/markdown",
        "theurian_relations": [],
    }


def test_stale_after_is_absent_when_the_revision_records_no_valid_to(tmp_path: Path) -> None:
    bundle = bundle_of(tmp_path, [Row("keeper", 1)])

    assert "stale_after" not in bundle.front_matter("keeper.md")


def test_a_title_carrying_a_newline_forges_no_front_matter_key(tmp_path: Path) -> None:
    """Decision 2's YAML half: the one with a governance consequence.

    A title ending in a line break followed by `theurian_sensitivity: public`
    would otherwise assert its own sensitivity label to every consumer that
    reads front matter.
    """
    hostile = "Policy\ntheurian_sensitivity: public"
    bundle = bundle_of(tmp_path, [Row("keeper", 1, title=hostile)])

    front_matter = bundle.front_matter("keeper.md")
    assert front_matter["title"] == hostile
    assert front_matter["theurian_sensitivity"] == "internal"


# -- Refusals --------------------------------------------------------------


def test_a_target_that_already_holds_something_is_refused_with_a_cure(tmp_path: Path) -> None:
    """A silent merge would leave members of the old export behind.

    The cure names a runnable command and the listing that says what is there,
    and it urges no deletion: the target is the operator's own directory.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    target = tmp_path / "bundle"
    target.mkdir()
    (target / "something-of-mine.md").write_text("mine", encoding="utf-8")

    with pytest.raises(OkfExportError) as caught:
        export(database, target)

    assert "not empty" in str(caught.value)
    assert "theurian okf export" in caught.value.remedy
    assert f"ls -la {target}" in caught.value.remedy
    assert "rm " not in caught.value.remedy
    # Nothing was written, and what was there is untouched.
    assert sorted(path.name for path in target.iterdir()) == ["something-of-mine.md"]


def test_a_target_that_is_not_a_directory_is_refused_without_writing_over_it(
    tmp_path: Path,
) -> None:
    database = corpus(tmp_path, [Row("keeper", 1)])
    target = tmp_path / "a-file"
    target.write_text("bytes that are not a bundle", encoding="utf-8")

    with pytest.raises(OkfExportError) as caught:
        export(database, target)

    assert "not a directory" in str(caught.value)
    assert "Move or rename" in caught.value.remedy
    assert target.read_text(encoding="utf-8") == "bytes that are not a bundle"


def test_a_target_that_is_a_symbolic_link_is_refused_rather_than_followed(
    tmp_path: Path,
) -> None:
    """`exists` and `is_dir` answer about the link's target, so both cleared it.

    A link to an empty directory satisfied every probe the refusal made, and the
    whole bundle was written wherever it pointed -- outside the directory the
    command was given, with `bundlePath` naming the link (round one, adversarial;
    graded HIGH as a containment write escape). The destination is asserted still
    empty, which is the claim: not that a refusal happened, but that nothing
    landed through the link.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = tmp_path / "bundle"
    target.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(OkfExportError) as caught:
        export(database, target)

    assert "symbolic link" in str(caught.value)
    assert "theurian okf export" in caught.value.remedy
    assert f"ls -l {target}" in caught.value.remedy
    assert "rm " not in caught.value.remedy
    assert list(elsewhere.iterdir()) == [], "the bundle was written through the link"
    assert target.is_symlink(), "the refusal removed the operator's own link"


def test_a_symbolic_link_to_a_nonempty_directory_is_still_refused_by_its_own_guard(
    tmp_path: Path,
) -> None:
    """Two guards can both refuse a link target, and only one names a safe cure.

    An *empty*-directory link, above, falls through `_refuse_an_unusable_target`
    under `if directory.is_symlink(): -> if False:` and is still refused --
    correctly, but by `_make_one_directory`'s separate check during `_write`, not
    by this one -- so that pin proves nothing about which guard fired and the
    mutation survived 111 tests (adversarial MEDIUM). A link to a *non-empty*
    directory tells the two guards apart: mutated away, `is_symlink()` is skipped
    and `iterdir()` -- which follows the link -- finds what is inside it, so the
    "not-empty" arm raises first and `_write` is never reached. Its cure offers
    `directory / 'okf-bundle'`, which resolves *through* the link into what it
    points at, while the symbolic-link arm's cure offers `directory.parent /
    'okf-bundle'`, beside the link. Following the wrong one would write the next
    attempt through the same link this refusal exists to stop.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "something-of-mine.md").write_text("mine", encoding="utf-8")
    target = tmp_path / "bundle"
    target.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(OkfExportError) as caught:
        export(database, target)

    assert "symbolic link" in str(caught.value)
    assert str(target.parent / "okf-bundle") in caught.value.remedy
    assert str(target / "okf-bundle") not in caught.value.remedy
    assert sorted(path.name for path in elsewhere.iterdir()) == ["something-of-mine.md"]


def test_a_symbolic_link_standing_in_for_a_directory_inside_the_bundle_is_refused(
    tmp_path: Path,
) -> None:
    """`mkdir(parents=True)` walks a planted directory link without complaint.

    ``O_NOFOLLOW`` covers the final component only, so the leaf guard says nothing
    about `<root>/architecture` being a link: the concept document was then created
    inside whatever that named. Driven at ``_write`` rather than through
    :meth:`OkfExporter.export`, because a target holding the plant is already
    refused as non-empty one guard earlier -- and it is ``_write`` that owns this
    one. Planting during the window between the two is the residual recorded
    against #577.
    """
    root = tmp_path / "bundle"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "architecture").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(OkfExportError) as caught:
        okf_export._write(root, {"architecture/policy.md": "would have landed outside"})

    assert "symbolic link" in str(caught.value)
    assert str(root / "architecture") in str(caught.value)
    assert list(elsewhere.iterdir()) == [], "a bundle member was written through the link"
    assert [path.name for path in root.iterdir()] == ["architecture"]


def test_a_member_key_with_a_parent_segment_is_refused_before_anything_is_created(
    tmp_path: Path,
) -> None:
    """The containment check is keyed on the segments, not on `is_relative_to`.

    `Path('/a/b/../c').is_relative_to('/a/b')` is `True` -- the comparison is
    lexical over path parts -- so the traversal the guard exists to catch was
    exactly what it passed (round one, code review). No such key is derivable
    today, which is why the crafted mapping is handed to ``_write`` directly: the
    guard is for the member somebody derives *some other way* later, and a test
    that could only reach it through the current derivation would not be testing
    it at all.
    """
    root = tmp_path / "bundle"

    with pytest.raises(InvariantViolationError) as caught:
        okf_export._write(root, {"../escape.md": "outside the bundle root"})

    assert "outside the bundle root" in str(caught.value)
    assert not (tmp_path / "escape.md").exists()
    assert not root.exists(), "a refused member left a directory behind"


# -- The target's own ancestors -----------------------------------------------


def test_a_target_with_two_absent_parent_levels_is_created_and_filled(tmp_path: Path) -> None:
    """`--help` promises "Created if absent" for the whole path, not just the leaf.

    The round-one symlink fix walks directories one component at a time from
    each member's own relative path, which only reaches components at or under
    the bundle root -- the root's own missing ancestors were never created, so
    `okf export ~/exports/2026-09-26` against an absent `exports/` raised
    `FileNotFoundError` (round two, adversarial HIGH). Compared against a flat
    target rather than merely asserting success: the earlier bug's own symptom
    was a raised exception, and a weaker assertion would not catch a regression
    that instead wrote an incomplete tree.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])

    flat = export(database, tmp_path / "flat-bundle")
    nested = export(database, tmp_path / "two" / "absent" / "levels" / "bundle")

    assert nested.files == flat.files
    assert nested.report["bundleDigest"] == flat.report["bundleDigest"]


def test_an_ancestor_that_is_a_symbolic_link_to_a_real_directory_is_followed(
    tmp_path: Path,
) -> None:
    """An ancestor of the named target is the operator's own path (the ruling): it is
    walked exactly as ``no_follow``'s narrowed scope sentence says -- refused ``at
    the named target, or at any component under it``, never above. macOS proves
    this is not academic: ``/tmp`` is itself a symlink to ``/private/tmp``, so a
    future hardening that ``lstat``-refuses an ancestor must turn this pin red and
    confront that recorded reason.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    real_directory = tmp_path / "real-directory"
    real_directory.mkdir()
    parent_link = tmp_path / "parentlink"
    parent_link.symlink_to(real_directory, target_is_directory=True)

    flat = export(database, tmp_path / "flat-bundle")
    nested = export(database, parent_link / "bundle")

    assert nested.files == flat.files
    assert nested.report["bundleDigest"] == flat.report["bundleDigest"]


def test_a_target_reached_through_an_absent_then_dotdot_component_is_still_refused_as_not_empty(
    tmp_path: Path,
) -> None:
    """``absent/../out`` is the operator naming ``out``, and it is refused as ``out``.

    Every probe in ``_refuse_an_unusable_target`` ``lstat``s the exact path it is
    given, and a not-yet-existing component makes all three read as absent --
    ``exists()`` is ``False``, so the not-empty arm never ran -- regardless of
    what the same path names once that component is real. The ancestor ``mkdir``
    used to run afterward, inside ``_write``, past every guard: it materialised
    ``absent``, and the identical path then resolved into ``out``, a separately
    populated prior bundle, which the walk merged into without ever refusing
    (round three, adversarial HIGH). Canonicalizing the target first is what
    makes this refuse instead, and it refuses the *real* target: nothing is
    created to make the path resolve, so ``absent`` does not exist afterward
    either.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    out = tmp_path / "out"
    export(database, out)
    (out / "withdrawn.md").write_text("a stale member from an earlier export", encoding="utf-8")
    before = _read(out)

    with pytest.raises(OkfExportError) as caught:
        _report(database, tmp_path / "absent" / ".." / "out")

    assert "not empty" in str(caught.value)
    assert str(out) in str(caught.value), "the refusal named the typed path, not the target"
    assert _read(out) == before, "the second export merged into the populated prior bundle"
    assert list(tmp_path.rglob("absent")) == [], "the refusal created a component of its own"


def test_a_trailing_dotdot_target_lands_in_the_directory_it_traverses_to(tmp_path: Path) -> None:
    """``inner/absent/..`` names ``inner``, and nothing named ``absent`` is created.

    A final ``..`` names a directory by traversal, so there is no leaf for a
    planted link to sit on and the whole path canonicalizes (Case 2 of
    ``_the_canonical_target``). The cut before this one instead created the
    missing ``absent`` component *first*, so that its guards would not ``lstat``
    a path holding a ``..`` -- which put an empty directory of the export's own
    making inside the very target it then refused as non-empty, poisoning every
    retry (round four, adversarial HIGH). Here the export succeeds, and it is the
    *second* one, named plainly, that is refused: what is in the way then is the
    first one's bundle.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    flat = export(database, tmp_path / "flat-bundle")
    inner = tmp_path / "inner"

    report = _report(database, inner / "absent" / "..")

    assert report["bundlePath"] == str(inner)
    assert _read(inner) == flat.files
    assert list(tmp_path.rglob("absent")) == [], "the export created a component of its own"
    with pytest.raises(OkfExportError) as caught:
        export(database, inner)
    assert "not empty" in str(caught.value)


def test_a_trailing_dotdot_target_into_a_populated_directory_is_refused_with_no_debris(
    tmp_path: Path,
) -> None:
    """The refusal is the traversed-to directory's own state, and it writes nothing.

    The materialise-first cut refused this too, and only after creating
    ``inner/absent`` -- a directory inside the target it was refusing (round four,
    adversarial HIGH). Both halves are asserted: the prior bundle is unchanged
    byte for byte, and nothing named ``absent`` exists anywhere afterward.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    inner = tmp_path / "inner"
    export(database, inner)
    (inner / "withdrawn.md").write_text("a stale member from an earlier export", encoding="utf-8")
    before = _read(inner)

    with pytest.raises(OkfExportError) as caught:
        _report(database, inner / "absent" / "..")

    assert "not empty" in str(caught.value)
    assert _read(inner) == before, "the second export merged into the populated prior bundle"
    assert list(tmp_path.rglob("absent")) == [], "the refusal created a component of its own"


@pytest.mark.parametrize("populated", [False, True])
def test_a_multi_dotdot_target_lands_in_the_directory_it_climbs_back_to(
    tmp_path: Path, *, populated: bool
) -> None:
    """``out/a/b/../..`` climbs to ``out``, and two ``..`` are not a special case of one.

    The final part is still ``..``, so the path canonicalizes once and no
    intermediate component is created to make it resolve -- neither ``a`` nor
    ``b`` exists on either side of this, whichever way ``out`` itself decides the
    outcome.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    out = tmp_path / "out"
    flat = export(database, tmp_path / "flat-bundle")

    if populated:
        before = export(database, out).files
        with pytest.raises(OkfExportError) as caught:
            _report(database, out / "a" / "b" / ".." / "..")
        assert "not empty" in str(caught.value)
        assert _read(out) == before
    else:
        report = _report(database, out / "a" / "b" / ".." / "..")
        assert report["bundlePath"] == str(out)
        assert _read(out) == flat.files
    assert list(tmp_path.rglob("a")) == [], "a component was created to make the path resolve"


@pytest.mark.parametrize("suffix", ["", "/", "/.", "/./"])
def test_a_target_written_with_a_trailing_dot_or_slash_is_its_collapsed_leaf(
    tmp_path: Path, suffix: str
) -> None:
    """``pathlib`` collapses both while parsing, so neither reaches the dispatch.

    All four spellings are one target: ``Path`` drops a trailing ``.`` and a
    trailing ``/`` at construction and keeps a trailing ``..``, which is why the
    dispatch reads the final *part* rather than the string. So each of these is
    Case 1, where the leaf is the operator's own name and is never resolved --
    what keeps a link planted at the target refused.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    flat = export(database, tmp_path / "flat-bundle")
    target = tmp_path / "bundle"

    report = _report(database, Path(f"{target}{suffix}"))

    assert report["bundlePath"] == str(target)
    assert _read(target) == flat.files


def test_a_mid_path_dotdot_with_every_component_present_names_its_sibling(tmp_path: Path) -> None:
    """``a/../bundle`` is Case 1: the final part is a name, so only the parent resolves.

    The ``..`` sits above the leaf and is collapsed by that resolve rather than by
    anything the guards do, and ``a`` -- which exists here, so the kernel would
    walk the path too -- is left untouched.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    (tmp_path / "a").mkdir()
    flat = export(database, tmp_path / "flat-bundle")

    report = _report(database, tmp_path / "a" / ".." / "bundle")

    assert report["bundlePath"] == str(tmp_path / "bundle")
    assert _read(tmp_path / "bundle") == flat.files
    assert list((tmp_path / "a").iterdir()) == []


def test_the_report_names_where_the_bundle_is_and_not_the_path_that_was_typed(
    tmp_path: Path,
) -> None:
    """``bundlePath`` is the canonical target (PR #809 round two, code review LOW).

    A target through an ancestor link is written where the link points -- the
    recorded at-or-under scope, pinned above -- while the report published the
    typed path, which named the link rather than the place the bundle is at. The
    value is the export's own record of where it wrote, so it is the canonical
    one.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    real_directory = tmp_path / "real-directory"
    real_directory.mkdir()
    parent_link = tmp_path / "parentlink"
    parent_link.symlink_to(real_directory, target_is_directory=True)

    report = _report(database, parent_link / "bundle")

    assert report["bundlePath"] == str(real_directory / "bundle")
    assert (real_directory / "bundle" / MANIFEST_NAME).is_file()


def test_an_ancestor_that_is_a_regular_file_is_refused_with_a_named_cure(tmp_path: Path) -> None:
    """A file standing in for a directory ancestor blocks the whole path, not just its own name.

    ``directory.parent.mkdir(parents=True, exist_ok=True)`` raises
    ``FileExistsError`` when the blocking file *is* the immediate parent --
    measured on 3.13 -- naming that file correctly on its own, which is the
    shape this pin drives; the child-of-a-file shape one level further is
    :func:`test_an_ancestor_two_levels_above_the_target_that_is_a_regular_file_is_refused`'s,
    because the exception there names a different path.
    """
    blocking = tmp_path / "a-file"
    blocking.write_text("not a directory", encoding="utf-8")
    database = corpus(tmp_path, [Row("keeper", 1)])

    with pytest.raises(OkfExportError) as caught:
        export(database, blocking / "bundle")

    assert str(blocking) in str(caught.value)
    assert "Move or rename" in caught.value.remedy
    assert f"ls -l {blocking}" in caught.value.remedy
    assert "rm " not in caught.value.remedy
    assert blocking.read_text(encoding="utf-8") == "not a directory"
    assert not (blocking / "bundle").exists()


def test_an_ancestor_two_levels_above_the_target_that_is_a_regular_file_is_refused(
    tmp_path: Path,
) -> None:
    """The blocking file is not the path ``mkdir`` itself reports.

    ``os.mkdir`` raises ``NotADirectoryError`` naming the *attempted child*
    (``a-file/sub``), not the file that blocks it (``a-file``) -- measured on
    3.13, because ``mkdir(parents=True)`` only recurses through
    ``FileNotFoundError``, and a blocking file raises something else one level
    higher than the true cause instead. A refusal built from the raised
    exception's own path would send an operator to ``ls -l`` a path that has
    never existed.
    """
    blocking = tmp_path / "a-file"
    blocking.write_text("not a directory", encoding="utf-8")
    database = corpus(tmp_path, [Row("keeper", 1)])

    with pytest.raises(OkfExportError) as caught:
        export(database, blocking / "sub" / "bundle")

    assert str(blocking) in str(caught.value)
    assert f"ls -l {blocking}" in caught.value.remedy
    assert not (blocking / "sub").exists()


def test_an_ancestor_that_is_a_dangling_symbolic_link_is_followed_and_its_target_created(
    tmp_path: Path,
) -> None:
    """An ancestor link is followed whether or not its target exists yet (the ruling).

    ``resolve`` widens through every component above the leaf, and a dangling link
    is one of those components: the bundle lands at the path it names, created on
    the way there. Its twin one pin up -- a link to a directory that already
    exists -- has been followed since that ruling, and an attacker's plant chooses
    the same destination in both, so refusing only the dangling one was a
    distinction ``mkdir(parents=True)``'s errno drew rather than one this export
    decided (the cut before this one refused it as ``unusable-ancestor``).

    It goes further than the shell here, and that is the part to weigh: ``mkdir -p
    danglink/bundle`` refuses with "No such file or directory" and creates nothing
    (measured, BSD ``mkdir``, macOS 26.6), while this creates the link's target.
    ``bundlePath`` is what says so -- it names the place, not the link. A
    hardening that refuses an ancestor link whose target is absent must turn this
    pin red and confront the twin above it.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    pointed_at = tmp_path / "does-not-exist"
    link = tmp_path / "danglink"
    link.symlink_to(pointed_at)
    flat = export(database, tmp_path / "flat-bundle")

    report = _report(database, link / "bundle")

    assert report["bundlePath"] == str(pointed_at / "bundle")
    assert _read(pointed_at / "bundle") == flat.files
    assert link.is_symlink(), "the export replaced the operator's own link"


def test_an_ancestor_that_is_a_looping_symbolic_link_is_refused_with_a_named_cure(
    tmp_path: Path,
) -> None:
    """The one ancestor shape ``resolve`` cannot widen through, so the ``lstat`` probe shows.

    ``resolve(strict=False)`` expands every ancestor link it can and leaves a loop
    as it found it -- measured on 3.13: ``loop -> loop`` stays in the canonical
    path -- so the ancestor ``mkdir`` meets the link itself, raises
    ``FileExistsError``, and the lineage walk is what names it. That walk probes
    with ``exists(follow_symlinks=False)`` because ``exists()`` follows the link
    and reads a loop as absent; keyed on ``exists()`` alone, this refusal falls
    through to the no-blocker arm and blames the parent's mode. It is also the
    only ancestor still *being* a symbolic link once the parent has resolved,
    which is what the cure's "repoint it" clause is for.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    loop = tmp_path / "loop"
    loop.symlink_to(loop)

    with pytest.raises(OkfExportError) as caught:
        export(database, loop / "bundle")

    assert str(loop) in str(caught.value)
    assert "Move or rename" in caught.value.remedy
    assert "repoint" in caught.value.remedy
    assert "rm " not in caught.value.remedy
    assert loop.is_symlink(), "the refusal removed the operator's own link"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_parent_that_denies_writes_is_refused_without_blaming_a_path_that_never_existed(
    tmp_path: Path,
) -> None:
    """The ancestor walk finds no blocker here, and must not invent one.

    Every directory on the way to this target that exists *is* a usable
    directory; what stops the ``mkdir`` is the mode on ``read-only/``. The walk's
    fallback used to raise ``unusable-ancestor`` against the target's own parent,
    publishing "already exists and is not a usable directory" about a path that
    has never existed and sending ``ls -l`` at it (round four, adversarial
    MEDIUM). The cure names the deepest directory that does exist, and the message
    carries what the filesystem answered, because permissions and free space need
    different cures and only that answer tells them apart.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    target = read_only / "absent" / "bundle"
    read_only.chmod(0o500)
    try:
        with pytest.raises(OkfExportError) as caught:
            export(database, target)
    finally:
        read_only.chmod(0o700)

    assert "Permission denied" in str(caught.value)
    assert str(read_only) in str(caught.value)
    assert f"ls -ld {read_only}" in caught.value.remedy
    assert str(target.parent) not in caught.value.remedy, "the cure lists a path that never existed"
    assert "rm " not in caught.value.remedy
    assert not target.parent.exists()


def test_a_relative_target_is_created_where_the_working_directory_puts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative target is as legitimate as ``../bundle`` shell usage (the ruling).

    The only thing a relative target is resolved against is the process's own
    working directory: ``resolve`` reaches the *parent* and the leaf is joined
    back on as typed, which is what keeps the round-one leaf-symlink refusal from
    being undone by following it. The report then names the absolute path, because
    that is where the bundle is whatever the process's working directory becomes
    next.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    monkeypatch.chdir(tmp_path)

    result = export(database, Path("rel") / "deep" / "bundle")

    assert result.report["bundlePath"] == str(tmp_path / "rel" / "deep" / "bundle")
    assert (tmp_path / "rel" / "deep" / "bundle" / MANIFEST_NAME).is_file()


# -- The tree pass's own failures (round five) ------------------------------


def _root_near_the_path_limit(base: Path, length: int) -> Path:
    """A directory under ``base`` whose absolute path is ``length`` characters.

    Built from a chain of near-``NAME_MAX`` components rather than one long
    name, so the limit crossed is this filesystem's own ``PATH_MAX`` -- queried
    rather than assumed, since the two platforms this suite runs on disagree
    about its value.
    """
    deep = base
    while len(str(deep)) < length - 200:
        deep = deep / ("d" * 200)
    remaining = length - len(str(deep)) - 1
    if remaining > 0:
        deep = deep / ("z" * remaining)
    return deep


def test_a_tree_pass_failure_leaves_the_target_exactly_as_it_was_found(
    tmp_path: Path,
) -> None:
    """``_make_the_tree`` used to create directories one at a time and unwind
    nothing: a component crossing this filesystem's ``PATH_MAX`` failed the pass
    partway through, and every directory already made -- the target itself
    included -- was left inside it, so a retry into the same place was refused
    as not-empty over debris the export itself had written (round five,
    adversarial HIGH). Falsifies the earlier ``_write`` docstring's "a refusal in
    either of the first two [passes] leaves no partial bundle".

    Two namespace segments, so the target and the first fit under ``PATH_MAX``
    and the second does not: the tree pass creates the target and the first
    segment, then fails on the second, with the whole corpus already read.
    """
    segment = "s" * 90
    database = corpus(tmp_path, [Row(f"{segment}.{segment}.leaf", 1)])
    target = _root_near_the_path_limit(tmp_path, os.pathconf(str(tmp_path), "PC_PATH_MAX") - 100)

    with pytest.raises((OkfExportError, OSError)):
        export(database, target)

    assert not target.exists(), "the failed tree pass left debris inside the target"

    retry = export(corpus(tmp_path / "second-corpus", [Row("keeper", 1)]), target)
    assert retry.report["concepts"] == 1, "the retry was poisoned by the failed pass's debris"


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_leaf_link_under_an_unsearchable_directory_is_refused_as_a_symbolic_link(
    tmp_path: Path,
) -> None:
    """The link check runs first, so it never has to resolve through the link.

    Moving it after ``exists()``/``is_dir()`` survives every other shape in the
    suite (mutation ``guard-link-check-last``), because both of those *follow*
    the link -- and ``Path.exists()`` does not swallow ``EACCES``, so a leaf
    link pointing *under* a directory the process cannot search would raise an
    untyped ``PermissionError`` in place of this typed refusal (round five,
    adversarial MEDIUM).
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    closed = tmp_path / "closed"
    (closed / "real").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(closed / "real")
    closed.chmod(0o000)
    try:
        with pytest.raises(OkfExportError) as caught:
            export(database, link)
    finally:
        closed.chmod(0o700)

    assert "symbolic link" in str(caught.value)


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_parent_that_denies_writes_at_the_targets_own_level_is_refused_as_a_document(
    tmp_path: Path,
) -> None:
    """The sibling, one level shallower, of
    :func:`test_a_parent_that_denies_writes_is_refused_without_blaming_a_path_that_never_existed`.

    There the denying directory is the target's *grandparent*, so the ancestor
    walk finds it. Here it **is** the target's own parent: nothing is missing for
    :func:`_create_the_canonical_ancestors` to create, every probe in
    :func:`_refuse_an_unusable_target` passes because the target does not exist
    yet, and the whole corpus is read before ``_make_one_directory(root)`` used
    to raise a raw, untyped ``PermissionError`` (round five, adversarial MEDIUM).
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    target = read_only / "bundle"
    read_only.chmod(0o500)
    try:
        with pytest.raises(OkfExportError) as caught:
            export(database, target)
    finally:
        read_only.chmod(0o700)

    assert "Permission denied" in str(caught.value)
    assert not target.exists()


def test_a_leaf_name_the_filesystem_will_not_take_is_refused_as_a_document(
    tmp_path: Path,
) -> None:
    """``_refuse_an_unusable_target``'s own probes raised ``ENAMETOOLONG`` untyped
    for a leaf this long -- ``pathlib``'s ignored-errno set is
    ``ENOENT``/``ENOTDIR``/``EBADF``/``ELOOP``, and ``ENAMETOOLONG`` is not in it
    (round five, adversarial MEDIUM). Unlike the ancestor's own version of this
    failure, this one never reaches ``_create_the_canonical_ancestors`` at all:
    the target's parent exists already, so the leaf itself is what the guard
    cannot probe.
    """
    database = corpus(tmp_path, [Row("keeper", 1)])
    target = tmp_path / ("o" * 300)

    with pytest.raises(OkfExportError) as caught:
        export(database, target)

    assert "File name too long" in str(caught.value)


class Recording:
    """An ``OkfExportSession`` that delegates to the real store and records the order.

    A delegating wrapper rather than a subclass, because ``SqliteCanonicalStore``
    is ``@final``, and a better double for it: satisfying the Protocol
    structurally is what proves the export is typed against a contract rather
    than against that class.

    The four reads the export makes record. The five below only delegate: the
    ``get_item``/``get_item_exact``/``get_item_metadata``/
    ``get_item_exact_metadata`` family, and ``list_relations`` -- all declared by
    ``IndexBuildSession``, which ``OkfExportSession`` extends, so this class is
    also the measurement of what that base demands and this use case does not ask
    for. ``list_relations`` records anyway, and is asserted *absent* below: the
    alias-resolving read answers an aliased item's query from another item's
    edges, so a walk that reached for it again would be caught by name (T-21).
    """

    def __init__(self, database: Path) -> None:
        self.inner = SqliteCanonicalStore(database)
        self.order: list[str] = []

    def __enter__(self) -> Recording:
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

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        self.order.append("list_items")
        return self.inner.list_items(context)

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


def test_every_read_the_walk_makes_is_inside_the_snapshot(tmp_path: Path) -> None:
    """The key for the module docstring's "every read is one snapshot".

    ADR-0037 decision 3's own pin -- a withdrawal landing mid-walk leaving the
    bundle wholly on one side of it -- is the owed battery beside this file, and
    it is not this test. This is the narrower claim that battery rests on, and the
    one a refactor breaks silently: that the reads happen *inside* the context at
    all. Recorded around the real session, so the order asserted is the order
    SQLite saw.
    """
    database = corpus(tmp_path, [Row("keeper", 1), Row("other", 2)], [Edge("keeper", "other")])
    sessions: list[Recording] = []

    def recording(path: Path) -> Recording:
        session = Recording(path)
        sessions.append(session)
        return session

    OkfExporter(store_factory=recording).export(
        OkfExportRequest(
            database=database,
            output_directory=tmp_path / "bundle",
            project_id=PROJECT.value,
            visible_sensitivities=VISIBLE,
        )
    )

    assert len(sessions) == 1, "the export opened more than one session"
    order = sessions[0].order
    assert order[0] == "snapshot-opened"
    assert order[-1] == "snapshot-closed"
    assert set(order[1:-1]) == {"list_items", "get_revision", "list_relations_by_literal_id"}
    assert "list_relations" not in order, "the walk read edges through the alias-resolving query"


@pytest.mark.parametrize("module", [okf_bundle, okf_export])
def test_neither_export_module_reads_a_clock(module: ModuleType) -> None:
    """The key for "no byte varies with when the export ran" (decision 2).

    Structural rather than behavioural, and the two are different claims:
    ``test_no_byte_of_the_bundle_carries_the_moment_it_was_exported`` holds it for
    the corpus that test builds, while this holds it for every corpus by saying
    the modules cannot ask. ``datetime`` is imported by
    ``application/okf_bundle.py`` for a type annotation, which is why the key is
    the *call* and not the import.
    """
    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    reached = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name)
    }

    assert not {name for name in reached if "now" in name or "time" in name}, sorted(reached)


def test_an_item_pointing_at_another_items_revision_refuses_the_whole_export(
    tmp_path: Path,
) -> None:
    """Refused rather than skipped, as `IndexBuilder._build` refuses it.

    The pointer is type-valid, satisfies the composite foreign key and moves
    neither #30 integrity count, so nothing upstream catches it -- and followed
    here it writes one row's title and body into another row's concept document,
    under that row's approved status and sensitivity. The message names no id:
    the id it would carry is the withheld row's.
    """
    rows = [Row("keeper", 1), Row("other", 2)]
    database = corpus(tmp_path, rows)
    lock = tmp_path / "runtime" / "write.lock"
    with write_transaction(database, lock) as connection:
        connection.execute(
            "UPDATE knowledge_items SET current_revision_id = ? WHERE item_id = ?",
            (rows[1].revision_id.value, "keeper"),
        )

    with pytest.raises(InvariantViolationError) as caught:
        export(database, tmp_path / "bundle")

    assert "belongs to a different item" in str(caught.value)
    assert "keeper" not in str(caught.value)
    assert not (tmp_path / "bundle").exists(), "a refused walk wrote part of a bundle"
