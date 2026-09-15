"""0.2.3: the read gates decide from metadata and never materialise a withheld body.

**The class.** ``SqliteCanonicalStore.get_item``/``get_item_exact`` read an item
through ``_ITEM_WITH_CURRENT_CONTENT_SQL``, which ``LEFT JOIN``s the current
revision and materialises its **body** to recompute
``current_served_content_sha256`` for the GHSA-3f65 serve gate. Three read gates
decide-then-withhold on ``status`` and ``sensitivity`` -- both on the pointer row,
neither needing the body -- yet read a *withheld* item's whole body first, so the
refusal's wall-clock scaled with that body's size. A caller could time existence
and approximate size of content it may not read (existence + size, not content
bytes). The class's key is **pre-gate body materialization**, and it is distinct
from the #338 sensitivity-COUNT residual (a bounded VM-steps term on a non-indexed
predicate), which these tests deliberately do not touch.

**The proof is a byte count, not a stopwatch.** The size signal a caller times is
the number of bytes the refusal path materialises for a withheld item. This module
pins that number at **zero** for every withheld face, at any body size -- so the
signal collapses by construction, which is the durable form of "the two corpora
take the same time" (the 8 MiB two-corpora ``P``-values are measured out of band).
:class:`_BodyReadCounter` is the instrument: it tallies the body-materialising
reads (``get_item``/``get_item_exact``/``get_revision``/``current_revision``)
separately from the bodyless metadata reads, so a regression that reads a withheld
body again turns these zeros non-zero. Each pin is RED before 0.2.3, where the
withheld path read the body through the joined ``get_item``/``get_item_exact``.

**The three shipped faces**, each with the withheld corpus member the class needs:

* **knowledge.get** (``test_knowledge_get_...``) -- an explicit id, so a
  withheld-*status* item reaches the body read directly; its withheld corpus is a
  normal ``deprecated`` item.
* **_relation_is_visible** (``test_relation_gate_...``) -- likewise an explicit
  endpoint id; a normal withheld-status endpoint.
* **CanonicalVisibility._may_surface** (``test_may_surface_...``) -- reached from a
  *search*, so its withheld corpus member must be a **T-17a-window row**: one
  surfaceable at index-build time and withdrawn after, whose stale index entry
  still ranks it. A currently-withheld-status row is dropped by #158's build-time
  filter and never reaches this gate, so it would prove nothing. Reachability of
  this window is what ``test_purged_build_quantities.py``'s *stale* build measures;
  here the window is reproduced by ranking a row whose canonical status is now
  ``deprecated``.

**GHSA-3f65 is preserved**: a *surfaceable* candidate still gets its
served-content-hash-vs-index check, which reads the body -- but only for a row that
has already cleared status, sensitivity and revision, i.e. content the caller may
already see on those axes (``test_may_surface_reads_the_body_of_a_surfaceable_row``).

**The search substring path needs no fix** and this module pins why
(``test_substring_scan_never_materialises_a_withheld_body``):
``list_items_by_status`` excludes withheld-status AND above-ceiling-sensitivity
rows in the SQL ``WHERE``, so no withheld row's body is ever materialised; the
residual there is a bounded per-row VM-steps term on a non-indexed predicate
(#338), not a body-size channel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from theurian.application.visibility import CanonicalVisibility
from theurian.domain.context import RequestContext
from theurian.domain.enums import (
    KnowledgeKind,
    KnowledgeStatus,
    RelationType,
    Sensitivity,
    TrustLevel,
    may_disclose,
    may_surface,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
    served_content_hash,
)
from theurian.domain.project import Project
from theurian.domain.ranking import Ranked
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter
from theurian.mcp.tools import _relation_is_visible

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("body-materialization")
CONTEXT: Final = RequestContext(project_id=PROJECT)
EVERY_SENSITIVITY: Final = frozenset(Sensitivity)
CREATED: Final = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
MIGRATION: Final = MigrationId("01K1MGBBBB01234567890ABCDE")
STATE_HASH: Final = "b" * 64

#: The worst instance the brief names: a body at MAX_SOURCE_FILE_BYTES. Written to
#: a real SQLite blob, so ``get_item``'s join actually materialises 8 MiB and a
#: read of it is unmistakable in the byte tally. Direct canonical writes only --
#: no index build -- so 8 MiB costs a blob insert, not a chunking pass.
EIGHT_MIB: Final = 8 * 1024 * 1024
LARGE_BODY: Final = "# boundary\n" + ("x" * (EIGHT_MIB - len("# boundary\n")))
TITLE: Final = "Boundary policy"

VISIBLE_ID: Final = ItemId("architecture.keep")
VISIBLE_REV: Final = RevisionId("01K1B" + f"{1:021d}")
VISIBLE_ID2: Final = ItemId("architecture.also-keep")
VISIBLE_REV2: Final = RevisionId("01K1C" + f"{3:021d}")
WITHHELD_ID: Final = ItemId("architecture.gone")
WITHHELD_REV: Final = RevisionId("01K1G" + f"{2:021d}")


class _BodyReadCounter:
    """A real ``SqliteCanonicalStore`` session that tallies which reads touch a body.

    ``body_reads`` counts the body-materialising reads -- ``get_item`` and
    ``get_item_exact`` (each joins the current revision's body),
    ``get_revision`` and ``current_revision`` (each returns one). ``body_bytes``
    sums the bodies it can see on a return value (the two revision reads), so a
    read that materialised 8 MiB is unmistakable. ``metadata_reads`` counts the
    bodyless ``get_item_metadata``/``get_item_exact_metadata`` reads the gates make
    since 0.2.3. Everything else forwards untouched.
    """

    def __init__(self, inner: SqliteCanonicalStore) -> None:
        self._inner = inner
        self.body_reads = 0
        self.body_bytes = 0
        self.metadata_reads = 0

    def list_items(self, context: RequestContext) -> tuple[KnowledgeItem, ...]:
        return self._inner.list_items(context)

    def get_item(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        self.body_reads += 1
        return self._inner.get_item(context, item_id)

    def get_item_exact(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        self.body_reads += 1
        return self._inner.get_item_exact(context, item_id)

    def get_item_metadata(self, context: RequestContext, item_id: ItemId) -> KnowledgeItem | None:
        self.metadata_reads += 1
        return self._inner.get_item_metadata(context, item_id)

    def get_item_exact_metadata(
        self, context: RequestContext, item_id: ItemId
    ) -> KnowledgeItem | None:
        self.metadata_reads += 1
        return self._inner.get_item_exact_metadata(context, item_id)

    def get_revision(
        self, context: RequestContext, revision_id: RevisionId
    ) -> KnowledgeRevision | None:
        self.body_reads += 1
        revision = self._inner.get_revision(context, revision_id)
        if revision is not None:
            self.body_bytes += len(revision.body)
        return revision

    def current_revision(
        self, context: RequestContext, item: KnowledgeItem
    ) -> KnowledgeRevision | None:
        self.body_reads += 1
        revision = self._inner.current_revision(context, item)
        if revision is not None:
            self.body_bytes += len(revision.body)
        return revision

    def __enter__(self) -> _BodyReadCounter:
        self._inner.__enter__()
        return self

    def __exit__(self, *details: object) -> None:
        self._inner.__exit__()


def _revision(item_id: ItemId, revision_id: RevisionId, body: str) -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=revision_id,
        item_id=item_id,
        project_id=PROJECT,
        migration_id=MIGRATION,
        title=TITLE,
        body=body,
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=CREATED),
        author="engineer@example.com",
        created_at=CREATED,
        source_anchors=(SourceAnchor(provider="git", source_uri=f"git://demo/{item_id.value}.md"),),
    )


def _item(item_id: ItemId, revision_id: RevisionId, status: KnowledgeStatus) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=item_id,
        project_id=PROJECT,
        namespace="backend",
        kind=KnowledgeKind.ARCHITECTURE,
        status=status,
        current_revision_id=revision_id,
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=CREATED),
    )


def _ranked(item_id: ItemId, revision_id: RevisionId, body: str) -> Ranked:
    """A row shaped as the index would have ranked this item while it was
    surfaceable -- its served-content hash matches canonical's, so the row clears
    the GHSA-3f65 check and only status/sensitivity/revision decide it."""
    return Ranked(
        chunk_id=f"{revision_id.value}#0",
        item_id=item_id.value,
        revision_id=revision_id.value,
        served_content_sha256=served_content_hash(TITLE, body).value,
    )


def _database(tmp_path: Path, *, withheld_status: KnowledgeStatus) -> Path:
    """A real state database: one visible (approved) item and one withheld item,
    both with an 8 MiB current-revision body.

    ``withheld_status`` sets the withheld item's *item-pointer* status. Its
    revision is written ``approved`` (so an index built now would rank it -- the
    T-17a window) and the pointer is then moved to ``withheld_status``, exactly the
    withdraw-after-build shape.
    """
    database = tmp_path / "state.sqlite3"
    create_database(database, state_hash=STATE_HASH, engine_version=1)
    with write_transaction(database, tmp_path / "write.lock") as connection:
        writer = SqliteWriter(connection)
        writer.register_project(
            Project(
                project_id=PROJECT,
                root_path=str(tmp_path),
                repository_url=None,
                default_branch="main",
                knowledge_directory=PurePosixPath(".theurian"),
                registered_at=CREATED,
            )
        )
        writer.append_revision(_revision(VISIBLE_ID, VISIBLE_REV, LARGE_BODY))
        writer.put_item(_item(VISIBLE_ID, VISIBLE_REV, KnowledgeStatus.APPROVED))
        writer.append_revision(_revision(VISIBLE_ID2, VISIBLE_REV2, LARGE_BODY))
        writer.put_item(_item(VISIBLE_ID2, VISIBLE_REV2, KnowledgeStatus.APPROVED))
        writer.append_revision(_revision(WITHHELD_ID, WITHHELD_REV, LARGE_BODY))
        writer.put_item(_item(WITHHELD_ID, WITHHELD_REV, withheld_status))
    return database


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """The store the gate tests read: withheld item retired to ``deprecated``.

    ``deprecated`` because ``may_surface`` refuses it under *every* flag, so the
    withheld arm cannot be reached by passing ``includeUnapproved``.
    """
    return _database(tmp_path, withheld_status=KnowledgeStatus.DEPRECATED)


# -- Functional: the metadata read agrees with the full read, minus the body ---


def test_metadata_read_matches_the_full_read_without_the_body(database: Path) -> None:
    """No behavioural regression: ``get_item_metadata`` returns the same status,
    sensitivity and current revision as ``get_item`` -- only the served-content
    hash differs, because no body was read to compute it (it is ``None``).
    """
    with SqliteCanonicalStore(database) as store:
        for item_id in (VISIBLE_ID, WITHHELD_ID):
            full = store.get_item(CONTEXT, item_id)
            metadata = store.get_item_metadata(CONTEXT, item_id)
            exact = store.get_item_exact(CONTEXT, item_id)
            exact_metadata = store.get_item_exact_metadata(CONTEXT, item_id)
            assert full is not None and metadata is not None
            assert exact is not None and exact_metadata is not None

            assert metadata.status == full.status
            assert metadata.sensitivity == full.sensitivity
            assert metadata.current_revision_id == full.current_revision_id
            assert metadata.item_id == full.item_id
            # The full read hashed the body; the metadata read did not, so its
            # served hash is None and the full read's is set -- the one field the
            # bodyless read cannot carry, and the GHSA-3f65 check reads the full one.
            assert metadata.current_served_content_sha256 is None
            assert full.current_served_content_sha256 is not None
            # The alias-free reads agree on the same axes.
            assert exact_metadata.status == exact.status
            assert exact_metadata.current_revision_id == exact.current_revision_id
            assert exact_metadata.current_served_content_sha256 is None


# -- Face 1: knowledge.get's gate ---------------------------------------------
#
# knowledge.get reads the item, gates on status/sensitivity, refuses if withheld,
# and only on the visible path reads the body -- through `current_revision`. The
# tool builds its own store, so the gate sequence is reproduced here over the
# counter to measure the bytes each path materialises; the wire behaviour is
# covered by the e2e refusal tests, and the tool now calls `get_item_metadata`
# where this reproduces it.


def _knowledge_get_body_reads(store: _BodyReadCounter, item_id: ItemId) -> int:
    """Replay knowledge.get's read sequence and return the body reads it made.

    Mirrors ``tools.knowledge_get``: a metadata read for the gate, a refusal if
    withheld or absent, and the current revision's body only on the visible path.
    """
    item = store.get_item_metadata(CONTEXT, item_id)
    withheld = item is not None and (
        not may_surface(item.status, include_unapproved=False)
        or not may_disclose(item.sensitivity, visible=EVERY_SENSITIVITY)
    )
    if item is None or item.current_revision_id is None or withheld:
        return store.body_reads  # refused -- no body read on this path
    store.current_revision(CONTEXT, item)  # the visible path's body read
    return store.body_reads


def test_knowledge_get_reads_no_body_when_it_withholds(database: Path) -> None:
    """The refusal materialises zero bytes of the withheld 8 MiB body (RED before
    0.2.3, where the gate read went through the body-joining ``get_item``)."""
    with _BodyReadCounter(SqliteCanonicalStore(database)) as store:
        reads = _knowledge_get_body_reads(store, WITHHELD_ID)
        assert reads == 0, "the withheld refusal must not read the body"
        assert store.body_bytes == 0, "no bytes of the withheld body were materialised"
        assert store.metadata_reads == 1, "the gate decided from one bodyless read"


def test_knowledge_get_reads_the_body_on_the_visible_path(database: Path) -> None:
    """The visible item still returns its body -- read once, through
    ``current_revision``, after the metadata gate cleared it."""
    with _BodyReadCounter(SqliteCanonicalStore(database)) as store:
        _knowledge_get_body_reads(store, VISIBLE_ID)
        assert store.body_bytes == EIGHT_MIB, "the visible item's body is served"
        assert store.metadata_reads == 1


# -- Face 2: _relation_is_visible ---------------------------------------------


def _relation(source: ItemId, target: ItemId) -> KnowledgeRelation:
    return KnowledgeRelation(
        project_id=PROJECT,
        source_item_id=source,
        relation_type=RelationType.RELATED_TO,
        target_item_id=target,
        created_at=CREATED,
        note="edge under test",
    )


def test_relation_gate_reads_no_body_for_a_withheld_endpoint(database: Path) -> None:
    """Gating a relation to a withheld endpoint reads only status and sensitivity,
    never the endpoint's 8 MiB body (RED before 0.2.3's ``get_item_exact_metadata``)."""
    with _BodyReadCounter(SqliteCanonicalStore(database)) as store:
        visible = _relation_is_visible(
            store,
            CONTEXT,
            _relation(VISIBLE_ID, WITHHELD_ID),
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        )
        assert visible is False, "an edge to a withheld endpoint is withheld"
        assert store.body_reads == 0
        assert store.body_bytes == 0


def test_relation_gate_reads_no_body_even_for_a_visible_edge(database: Path) -> None:
    """The gate never needs a body: even a wholly visible edge is decided from
    metadata alone, so neither endpoint's 8 MiB body is materialised."""
    with _BodyReadCounter(SqliteCanonicalStore(database)) as store:
        visible = _relation_is_visible(
            store,
            CONTEXT,
            _relation(VISIBLE_ID, VISIBLE_ID2),
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        )
        assert visible is True
        assert store.body_reads == 0
        assert store.body_bytes == 0


# -- Face 3: CanonicalVisibility._may_surface (the search-reachable gate) ------


def _cleared(store: _BodyReadCounter, row: Ranked) -> tuple[Ranked, ...]:
    return CanonicalVisibility(
        store,
        CONTEXT,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    ).cleared((row,))


def test_may_surface_reads_no_body_for_a_withdrawn_window_row(database: Path) -> None:
    """A T-17a-window row -- surfaceable at build, canonical now ``deprecated`` --
    reaches ``_may_surface`` and is refused from the pointer row alone, so its
    8 MiB body is never materialised (RED before 0.2.3).

    Reachability is not assumed: this is the shape ``test_purged_build_quantities``'s
    *stale* build hands the gate for real; here the row is ranked directly to hold
    the gate under the instrument without an 8 MiB index build.
    """
    with _BodyReadCounter(SqliteCanonicalStore(database)) as store:
        cleared = _cleared(store, _ranked(WITHHELD_ID, WITHHELD_REV, LARGE_BODY))
        assert cleared == (), "the withdrawn row does not surface"
        assert store.body_reads == 0, "and its body is never read to decide that"
        assert store.body_bytes == 0
        assert store.metadata_reads == 1


def test_may_surface_reads_the_body_of_a_surfaceable_row(database: Path) -> None:
    """GHSA-3f65 preserved: a surfaceable row still gets its served-content-hash
    check, which reads the body -- but only after status, sensitivity and revision
    have cleared on the bodyless read, so it is content the caller may already see.
    """
    with _BodyReadCounter(SqliteCanonicalStore(database)) as store:
        cleared = _cleared(store, _ranked(VISIBLE_ID, VISIBLE_REV, LARGE_BODY))
        assert cleared == (_ranked(VISIBLE_ID, VISIBLE_REV, LARGE_BODY),), (
            "the visible row surfaces"
        )
        assert store.metadata_reads == 1, "the gate read the pointer row first"
        # The content check reads the body through the joined `get_item`, which
        # recomputes the served-content hash; the join materialises the 8 MiB body
        # in SQLite even though the returned `KnowledgeItem` does not expose it, so
        # the read is counted but its bytes are not observable on the return value.
        assert store.body_reads == 1, "then read the body once, for the content check"


# -- The search substring path needs no fix, and here is why -------------------


def test_substring_scan_never_materialises_a_withheld_body(database: Path) -> None:
    """The regression pin that keeps "the search path materialises no withheld
    body" a re-checkable property.

    ``knowledge.search``'s scan below the trigram floor reads canonical through
    ``list_items_by_status``, which excludes withheld-status AND above-ceiling
    rows in the SQL ``WHERE`` -- so a withheld, body-heavy row a query WOULD scan
    is never returned to the scan and its body is never materialised, whatever its
    size. A future change moving that filter into Python would reopen the
    channel; this pin fails first.

    Distinct from the #338 residual: the cost of that SQL predicate on the
    non-indexed ``sensitivity`` column is a bounded per-row VM-steps term, not a
    body-size channel, and this test does not touch it.
    """
    with SqliteCanonicalStore(database) as store:
        surfaceable = store.list_items_by_status(
            CONTEXT,
            statuses=frozenset({KnowledgeStatus.APPROVED}),
            sensitivities=EVERY_SENSITIVITY,
        )
    returned = {item.item_id for item in surfaceable}
    assert VISIBLE_ID in returned, "the visible item is scanned"
    assert WITHHELD_ID not in returned, (
        "the withheld item is excluded in SQL before any body read -- so no query "
        "that would scan its text can materialise its 8 MiB body"
    )
    # And the rows the scan does get carry no body to time in the first place.
    assert all(item.current_served_content_sha256 is None for item in surfaceable)
