"""The OKF bundle export: which rows leave, and when (ADR-0037 decisions 3 and 4).

An **Index-class** derivative under ADR-0010: never a record of truth, never
cited as team knowledge, losable without loss. The artifact says so about itself,
in the manifest's holder notice and in `theurian_export_version` on every
concept.

Every *byte* of it belongs to :mod:`theurian.application.okf_bundle`; this module
owns the population and the read.

**The population is the rows the default channel serves, and no argument widens
it** (decision 3). `may_surface(..., include_unapproved=False)` and
`may_disclose` decide it, both read off the **item** rather than the revision:
`deprecateItem` and `changeSensitivity` move status and sensitivity without
writing a new revision, so the immutable revision keeps the label it was authored
under -- the caveat `index_builder` and `mcp/results.py` both carry. There is no
`--include-unapproved` bundle: a caller who may not read a row through `search`
or `knowledge.get` may not read it through a bundle either, and the bundle is the
easier artifact to forward.

**Every read is one snapshot** (decision 3), through ``read_snapshot`` on the
injected session. Without it a `migrate apply` landing mid-walk leaves a bundle
straddling two states: a withdrawn row present as a concept, absent from the
index file, and pointed at by a relation gated under the newer state. Bodies come
from the revision row's ``body`` column, never from `.theurian/knowledge/`, so
the snapshot covers the bundle's contents entirely and there is no second
consistency rule to state.

**The bundle is rendered whole before the first byte is written.** A walk that
raises therefore writes nothing at all, which is why the target is checked for
emptiness before the walk rather than cleaned up after it: a partial bundle looks
complete, and its digest would name files that are not there.

Takes its session factory by injection, so nothing here names an adapter
(ADR-0003).
"""

from __future__ import annotations

from collections.abc import Callable, Container, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, final, override

from theurian.application.index_builder import both_ends_visible
from theurian.application.okf_bundle import Concept, relation_order, render
from theurian.domain.context import RequestContext
from theurian.domain.enums import Sensitivity, may_disclose, may_surface
from theurian.domain.errors import InvariantViolationError, TheurianError
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.knowledge import KnowledgeItem, KnowledgeRelation, KnowledgeRevision
from theurian.domain.ports.canonical_store import IndexBuildSession
from theurian.security.no_follow import write_text_without_following_a_link


class OkfExportError(TheurianError):
    """The export target cannot hold a bundle.

    Raised before the walk, so nothing has been read and nothing written. Both
    arms carry their own cure, and neither cure deletes anything: the target is
    the operator's own directory, and what is already in it may be the copy
    somebody was handed.
    """

    def __init__(self, directory: Path, *, not_a_directory: bool = False) -> None:
        if not_a_directory:
            # The cure moves rather than deletes, and names the listing that says
            # what would be lost: whatever is at the path holds bytes or names,
            # and this is an operator-chosen path rather than a derived one.
            self.remedy = (
                f"Move or rename whatever is at {directory}, then run `theurian okf export "
                f"{directory}` again -- or export into a different directory. "
                f"`ls -l {directory}` shows what is there now."
            )
            super().__init__(
                f"The export target {directory} exists and is not a directory, so no bundle "
                f"tree can be written there. Theurian will not write over it."
            )
            return
        self.remedy = (
            f"Export into an empty or new directory: `theurian okf export "
            f"{directory / 'okf-bundle'}` puts one beside what is already there, and "
            f"`ls -la {directory}` shows what that is. Nothing is deleted here -- a bundle "
            f"is regenerated rather than edited, so merging a new one into an old tree "
            f"would leave members of the old export behind and make its digest wrong."
        )
        super().__init__(
            f"The export target {directory} is not empty. A bundle is a whole tree whose "
            f"digest covers every file in it, so it is written into an empty directory and "
            f"never merged into an existing one."
        )


class OkfExportSession(IndexBuildSession, Protocol):
    """What the export asks of one pass over canonical state.

    :class:`~theurian.domain.ports.canonical_store.IndexBuildSession` plus the
    snapshot of decision 3, and a use-case-local Protocol for the reason
    ``withdrawal_purge``'s :class:`PurgeableIndex` is one: the narrow shape keeps
    the concrete adapter named only at the composition root (ADR-0003), and the
    base is *not* widened because the index build's own collaborators have no
    business growing a method only this use case asks for.
    """

    def read_snapshot(self) -> AbstractContextManager[None]:
        """Every read in the body against one snapshot of the state (decision 3)."""
        ...

    @override
    def __enter__(self) -> OkfExportSession:
        """Acquire the handle here, for :class:`CanonicalReadSession`'s reason.

        Re-declared only to narrow the return type, as
        :class:`IndexBuildSession` re-declares it: a caller entering this
        Protocol must get back something that can still open a snapshot.
        """
        ...


@dataclass(frozen=True, slots=True)
class OkfExportRequest:
    """What to export, and where to put it."""

    database: Path
    #: Where the bundle tree goes. Created when absent; refused when it already
    #: holds anything (see :class:`OkfExportError`).
    output_directory: Path
    project_id: str
    #: The disclosure classes this deployment serves, already expanded from the
    #: operator's declared ceiling (#119, ADR-0025).
    #:
    #: **No default, for the reason :class:`~theurian.application.index_builder
    #: .IndexRequest` has none**: "everything is visible" is the state this
    #: filter exists to stop being implicit, and a default parameter is how it
    #: would come back.
    visible_sensitivities: frozenset[Sensitivity]


@final
class OkfExporter:
    """Writes one OKF bundle from one canonical snapshot."""

    def __init__(self, *, store_factory: Callable[[Path], OkfExportSession]) -> None:
        self._store_factory = store_factory

    def export(self, request: OkfExportRequest) -> dict[str, object]:
        """Write the bundle, and report what it holds.

        Every published value is a count over the rows this deployment serves by
        default, or the digest over the files they produced: a row the gate
        withheld moves none of them, which is what keeps them out of T-17's
        shape. The digest is the manifest's own value rather than a second
        computation beside it.

        Raises:
            OkfExportError: If the target is not a directory, or is one that
                already holds something. Raised before the walk.
            InvariantViolationError: If an item points at a revision belonging to
                another item. Refused for the whole export rather than skipped,
                the way ``IndexBuilder._build`` refuses it: the alternative is a
                concept document carrying another row's content -- possibly a
                withheld row's -- under this item's approved identity.
            OSError: If a file cannot be written. The tree is rendered whole
                first, so a refusal here is the filesystem and never the walk.
        """
        _refuse_an_unusable_target(request.output_directory)
        bundle = render(self._walk(request))
        _write(request.output_directory, bundle.files)
        return {
            "bundlePath": str(request.output_directory),
            "concepts": bundle.concepts,
            "sidecars": bundle.sidecars,
            "indexes": bundle.indexes,
            "bundleDigest": bundle.digest,
        }

    def _walk(self, request: OkfExportRequest) -> tuple[Concept, ...]:
        """The exported population and its edges, read inside one snapshot."""
        context = RequestContext(project_id=ProjectId(request.project_id))
        rows: list[tuple[KnowledgeItem, KnowledgeRevision]] = []
        # Every item that cleared both gates, whether or not it has a revision to
        # project. It is the relation gate's population, not the bundle's: an
        # approved, in-ceiling item with no current revision is still a
        # legitimate far end of an edge published from one that has -- the line
        # `index_builder` and `mcp.tools._relation_is_visible` both draw.
        visible: set[str] = set()
        with self._store_factory(request.database) as store, store.read_snapshot():
            for item in store.list_items(context):
                if not may_surface(item.status, include_unapproved=False):
                    continue
                if not may_disclose(item.sensitivity, visible=request.visible_sensitivities):
                    continue
                visible.add(item.item_id.value)
                if item.current_revision_id is None:
                    continue
                revision = store.get_revision(context, item.current_revision_id)
                if revision is None:  # pragma: no cover - a composite foreign key holds this
                    continue
                _refuse_a_foreign_pointer(item, revision)
                rows.append((item, revision))
            # A second pass, because an edge's visibility depends on *both* ends
            # and the far one may not have been walked yet.
            return tuple(
                Concept(
                    item=item,
                    revision=revision,
                    relations=_visible_relations(store, context, item.item_id, visible=visible),
                )
                for item, revision in rows
            )


def _refuse_a_foreign_pointer(item: KnowledgeItem, revision: KnowledgeRevision) -> None:
    """Refuse a `current_revision_id` naming another item's revision.

    Type-valid, satisfies the composite foreign key and moves neither #30
    integrity count, so nothing upstream refuses it -- and followed here it puts
    that revision's title and body into a concept document under this item's
    approved identity and sensitivity. Names no id: the id it would carry is the
    withheld row's.
    """
    if item.owns(revision):
        return
    msg = (
        "This project's canonical state disagrees with its own records: an item points at "
        "a revision that belongs to a different item, so this export would write that "
        "revision's content under the wrong item."
    )
    raise InvariantViolationError(msg)


def _visible_relations(
    store: OkfExportSession,
    context: RequestContext,
    item_id: ItemId,
    *,
    visible: Container[str],
) -> tuple[KnowledgeRelation, ...]:
    """The edges this deployment would publish for ``item_id``, in decision 2's order.

    ``both_ends_visible`` is imported rather than restated, for the reason
    ``_anchor_secrets`` imports ``AUTHORED_ANCHOR_FIELDS``: a security rule
    enumerated twice acquires an end on one side and not the other. What makes
    the application-layer form safe is where its set comes from -- the walk's own
    visible ids, each read by the id it literally names -- so it inherits the
    alias property of ``mcp.tools._relation_is_visible`` rather than restating it
    (T-21). An endpoint the corpus does not hold is absent from the set, so a
    dangling edge fails closed.
    """
    return tuple(
        sorted(
            (
                relation
                for relation in store.list_relations(context, item_id)
                if both_ends_visible(relation, visible)
            ),
            key=relation_order,
        )
    )


def _refuse_an_unusable_target(directory: Path) -> None:
    """Refuse anything but an absent or empty directory, before the walk runs.

    A silent merge into a prior bundle would leave members of the old export
    behind -- a concept whose row has since been withdrawn is not overwritten by
    anything -- and the new manifest's digest, computed over what this run
    rendered, would not describe the tree on disk.
    """
    if not directory.exists():
        return
    if not directory.is_dir():
        raise OkfExportError(directory, not_a_directory=True)
    if any(directory.iterdir()):
        raise OkfExportError(directory)


def _write(root: Path, files: Mapping[str, str]) -> None:
    """The one place this module writes, so every member takes the same guard.

    Every path is built from item-id segments and the bundle module's own
    constants, so none can escape ``root``; the check is kept because "no member
    can traverse" is a property of the *derivation*, and a future member derived
    some other way would inherit a guard here and escape one at the seam.

    ``write_text_without_following_a_link`` rather than ``Path.write_text``: the
    target is a directory the operator named, and a symbolic link planted in it
    between the ``mkdir`` and the write would otherwise be written through.
    """
    for relative, contents in files.items():
        path = root / relative
        if not path.is_relative_to(root):  # pragma: no cover - the grammar forbids it
            msg = f"A bundle member resolved outside the bundle root: {relative!r}"
            raise InvariantViolationError(msg)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_without_following_a_link(path, contents)


__all__ = [
    "OkfExportError",
    "OkfExportRequest",
    "OkfExportSession",
    "OkfExporter",
]
