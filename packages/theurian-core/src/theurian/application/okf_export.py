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

import contextlib
from collections.abc import Callable, Container, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn, Protocol, final, override

from theurian.application.index_builder import both_ends_visible
from theurian.application.okf_bundle import Concept, relation_order, render
from theurian.domain.context import RequestContext
from theurian.domain.enums import Sensitivity, may_disclose, may_surface
from theurian.domain.errors import InvariantViolationError, TheurianError
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.knowledge import KnowledgeItem, KnowledgeRelation, KnowledgeRevision
from theurian.domain.ports.canonical_store import IndexBuildSession
from theurian.security.no_follow import write_text_without_following_a_link

#: Why an export target was refused. Seven arms, each with its own message and
#: its own cure, dispatched by ``match`` so an eighth cannot be added without
#: being written.
TargetRefusal = Literal[
    "not-empty",
    "not-a-directory",
    "symbolic-link",
    "unusable-ancestor",
    "ancestors-not-created",
    "target-not-probable",
    "tree-not-created",
]


class OkfExportError(TheurianError):
    """The export target, or a directory inside it, cannot hold a bundle.

    The two ancestor arms fire earliest, before the target itself is even probed.
    ``unusable-ancestor`` names a directory *above* the target that stops the path
    to it from being built at all; ``ancestors-not-created`` is the same failure
    with nothing standing in the way, and it carries ``detail`` -- what the
    filesystem answered -- because permissions, free space and a name too long for
    the filesystem need three different cures and only that answer tells them
    apart. The next two, ``not-empty`` and ``not-a-directory``, are raised before
    the walk, so nothing has been read and nothing written. The ``symbolic-link``
    arm fires either there or while the tree is being created -- before any bundle
    file is written, because :func:`_write` creates and checks every directory
    first -- so its cure can still promise a target that holds no partial bundle.
    ``target-not-probable`` is what :func:`_refuse_an_unusable_target` raises when
    one of its own probes cannot even answer -- a name too long for the filesystem
    at the target's own leaf, unlike ``ancestors-not-created``'s reach, which is the
    directories *above* it. ``tree-not-created`` is the same shape one layer
    later: a directory the tree needs, at or under the target, that
    :func:`_make_one_directory` could not create for a reason other than a
    symbolic link standing in for it -- always after the walk, since every
    directory but the root is discovered by it, and always before any bundle file
    is written, since :func:`_make_the_tree` runs whole before :func:`_write`'s
    third pass.

    **No cure here deletes anything, and none of them urges a removal.** The
    target is a path the operator named on the command line rather than a derived
    one, so what is at it may be authored: a directory holds names and a file
    holds bytes, and even the link arm names the listing rather than an ``rm``,
    because the operator may have pointed it somewhere on purpose.
    """

    def __init__(
        self, directory: Path, *, reason: TargetRefusal = "not-empty", detail: str = ""
    ) -> None:
        match reason:
            case "unusable-ancestor":
                # "Move or rename" rather than "remove": the class docstring's
                # no-removal claim covers this arm too, and repointing a link is
                # itself a move.
                self.remedy = (
                    f"Move or rename whatever is at {directory} -- or repoint it, if it is a "
                    f"symbolic link -- then run `theurian okf export` again with the same "
                    f"target. `ls -l {directory}` shows what is there now."
                )
                message = (
                    f"An ancestor of the export target, {directory}, already exists and is not "
                    f"a usable directory, so the path to the bundle cannot be created. Theurian "
                    f"will not write through it."
                )
            case "ancestors-not-created":
                # Names the deepest directory that *does* exist rather than the one
                # the `mkdir` could not create: an `ls` of a path that has never
                # existed answers the operator with a second error and says nothing
                # about the first.
                self.remedy = (
                    f"Check that you can write under {directory}, the deepest directory on the "
                    f"way to the target that exists: `ls -ld {directory}` shows its mode and "
                    f"owner, `df -h {directory}` its free space. Then run `theurian okf export` "
                    f"again, with a target Theurian can create under it."
                )
                message = (
                    f"The directories above the export target could not be created, and nothing "
                    f"standing in the way of them explains it -- the filesystem answered: "
                    f"{detail}. The deepest directory on the way to the target that exists is "
                    f"{directory}."
                )
            case "not-a-directory":
                # The cure moves rather than deletes, and names the listing that
                # says what would be lost: whatever is at the path holds bytes or
                # names, and this is an operator-chosen path.
                self.remedy = (
                    f"Move or rename whatever is at {directory}, then run `theurian okf export "
                    f"{directory}` again -- or export into a different directory. "
                    f"`ls -l {directory}` shows what is there now."
                )
                message = (
                    f"The export target {directory} exists and is not a directory, so no bundle "
                    f"tree can be written there. Theurian will not write over it."
                )
            case "symbolic-link":
                # Names the link *and* where it points, because that is the whole
                # of what the refusal is about, and offers a different target
                # rather than a removal: the link may be the operator's own.
                self.remedy = (
                    f"Export into a directory that is not a symbolic link -- `theurian okf "
                    f"export {directory.parent / 'okf-bundle'}`, say. `ls -l {directory}` shows "
                    f"where this one points. Nothing was written through it and nothing is "
                    f"deleted here; remove or repoint the link yourself if it is meant to be "
                    f"the target."
                )
                message = (
                    f"A bundle directory would be written through the symbolic link at "
                    f"{directory}, so the tree would land outside the target this export was "
                    f"given. Theurian will not follow it."
                )
            case "not-empty":
                self.remedy = (
                    f"Export into an empty or new directory: `theurian okf export "
                    f"{directory / 'okf-bundle'}` puts one beside what is already there, and "
                    f"`ls -la {directory}` shows what that is. Nothing is deleted here -- a "
                    f"bundle is regenerated rather than edited, so merging a new one into an "
                    f"old tree would leave members of the old export behind and make its "
                    f"digest wrong."
                )
                message = (
                    f"The export target {directory} is not empty. A bundle is a whole tree "
                    f"whose digest covers every file in it, so it is written into an empty "
                    f"directory and never merged into an existing one."
                )
            case "target-not-probable":
                self.remedy = (
                    f"Export into a target with a shorter name: {directory} could not be "
                    f"checked -- the filesystem answered: {detail}. `ls -ld {directory.parent}` "
                    f"shows what is under it now."
                )
                message = (
                    f"The export target {directory} could not even be checked for emptiness -- "
                    f"the filesystem answered: {detail}."
                )
            case "tree-not-created":
                # Fires after the walk has already read the whole corpus, so the
                # cure names the parent that needs to take the write rather than
                # sending an `ls` at a target that may not exist either.
                self.remedy = (
                    f"Check that you can write under {directory.parent}: `ls -ld "
                    f"{directory.parent}` shows its mode and owner, `df -h {directory.parent}` "
                    f"its free space. Then run `theurian okf export` again."
                )
                message = (
                    f"A directory the bundle needs, {directory}, could not be created -- the "
                    f"filesystem answered: {detail}."
                )
        super().__init__(message)


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

    def list_relations_by_literal_id(
        self, context: RequestContext, item_id: ItemId
    ) -> tuple[KnowledgeRelation, ...]:
        """``list_relations`` for the row ``item_id`` literally names (T-21).

        Declared here rather than on :class:`IndexBuildSession`, and that is the
        same decision the snapshot above records: the index build asks its
        relation question of ids it read from the corpus, while this export asks
        what one *named* item authored -- and ADR-0003's amendment table keeps a
        port from growing a method one use case needs. An alias-resolving read
        answers this question from another item's edges (see
        :func:`_visible_relations`).
        """
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

        ``bundlePath`` is the **canonical** target, not the string the operator
        typed: :func:`_the_canonical_target` collapses their ``..`` segments and
        widens through their ancestor links, so a path typed through a link
        published the link while the bundle was somewhere else (PR #809 round two,
        code review LOW).

        Raises:
            OkfExportError: If the directories above the target cannot be created
                or cannot even be probed, if the target is not a directory, if it
                already holds something, or if a symbolic link stands at the
                target or at a directory the tree needs to create. Every arm but
                the symbolic-link one is raised before the walk; that one can
                also fire while the tree is being created, which is still before
                any bundle file is written (see :func:`_write`).
            InvariantViolationError: If an item points at a revision belonging to
                another item. Refused for the whole export rather than skipped,
                the way ``IndexBuilder._build`` refuses it: the alternative is a
                concept document carrying another row's content -- possibly a
                withheld row's -- under this item's approved identity.
            OSError: If a file cannot be written. The tree is rendered whole
                first, so a refusal here is the filesystem and never the walk.
        """
        target = _the_canonical_target(request.output_directory)
        _create_the_canonical_ancestors(target)
        _refuse_an_unusable_target(target)
        bundle = render(self._walk(request))
        _write(target, bundle.files)
        return {
            "bundlePath": str(target),
            "concepts": bundle.concepts,
            "sidecars": bundle.sidecars,
            "indexes": bundle.indexes,
            "bundleDigest": bundle.digest,
        }

    def _walk(self, request: OkfExportRequest) -> tuple[Concept, ...]:
        """The exported population and its edges, read inside one snapshot."""
        context = RequestContext(project_id=ProjectId(request.project_id))
        rows: list[tuple[KnowledgeItem, KnowledgeRevision]] = []
        with self._store_factory(request.database) as store, store.read_snapshot():
            for item in store.list_items(context):
                if not may_surface(item.status, include_unapproved=False):
                    continue
                if not may_disclose(item.sensitivity, visible=request.visible_sensitivities):
                    continue
                if item.current_revision_id is None:
                    continue
                revision = store.get_revision(context, item.current_revision_id)
                if revision is None:  # pragma: no cover - a composite foreign key holds this
                    continue
                _refuse_a_foreign_pointer(item, revision)
                rows.append((item, revision))
            # **The relation gate's population is the concepts this walk produced,
            # and it is derived from `rows` rather than accumulated beside them so
            # the two cannot drift.** `index_builder` and
            # `mcp.tools._relation_is_visible` gate on the set that cleared both
            # authority filters -- correct for them, because `knowledge.get`
            # publishes an edge to an approved in-ceiling item whether or not that
            # item has a current revision to serve. A bundle cannot: it *renders a
            # link* to the far end's concept document, and an item with no current
            # revision produces no such file. Gated on the wider set, a bundle
            # shipped `/<namespace>/<id>.md` in both channels and wrote no such
            # member -- reachable through documented operations alone (createItem,
            # restoreItem, addRelation) and exactly what decision 4's "no exported
            # link is broken within the bundle" denies (round one, code review and
            # adversarial HIGH).
            exported = {item.item_id.value for item, _ in rows}
            # A second pass, because an edge's visibility depends on *both* ends
            # and the far one may not have been walked yet.
            return tuple(
                Concept(
                    item=item,
                    revision=revision,
                    relations=_visible_relations(store, context, item.item_id, exported=exported),
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
    exported: Container[str],
) -> tuple[KnowledgeRelation, ...]:
    """The edges ``item_id``'s own concept document renders, in decision 2's order.

    The read answers in either direction: of ``RelationType``'s 14 members,
    ``INVERSE_RELATIONS`` maps only the 4 that form the two invertible pairs
    (``implements``/``implemented_by``, ``supersedes``/``superseded_by``), so a
    relation of one of the other 10 types queried from its **target** comes back
    unchanged -- source and target exactly as stored, neither one this item.
    Rendered on that document regardless, it reads as a triple whose ``target``
    is the document's own item: a false self-edge. So an edge is kept only when
    ``item_id`` is its **source** after the read's own mapping -- the far end of
    a non-invertible edge then carries no entry for it, while an inverse-mapped
    pair still renders once on each end, under its own type.

    **The query key and the gate set are two independent halves, and both must
    be the literally named id.** The gate set has always been literal -- the
    walk's own ids, read from ``list_items`` and never resolved (``exported``
    narrows that set to the rows that became concepts, which is
    :meth:`OkfExporter._walk`'s own question, not this one's) -- so it inherits
    ``mcp.tools._relation_is_visible``'s alias property rather than restating it
    (T-21). The *query* was not: ``list_relations`` resolves an
    alias first, and an approved in-ceiling item whose id is also an ``addAlias``
    key is answered from the target's edges, every row carrying the target as
    its source -- so the source filter above discarded all of them and the item
    shipped ``theurian_relations: []`` and an empty ``## Relations`` while
    ``knowledge.get`` published the edge (PR #809 round one, security HIGH).
    :meth:`OkfExportSession.list_relations_by_literal_id` is the non-resolving
    read that closes it.

    ``both_ends_visible`` is imported rather than restated, for the reason
    ``_anchor_secrets`` imports ``AUTHORED_ANCHOR_FIELDS``: a security rule
    enumerated twice acquires an end on one side and not the other. An endpoint
    the corpus does not hold is absent from the set, so a dangling edge fails
    closed -- and so is one the corpus holds but the bundle does not write.
    """
    return tuple(
        sorted(
            (
                relation
                for relation in store.list_relations_by_literal_id(context, item_id)
                if relation.source_item_id == item_id and both_ends_visible(relation, exported)
            ),
            key=relation_order,
        )
    )


def _the_canonical_target(directory: Path) -> Path:
    """Where the bundle really lands, with the operator's own leaf never resolved.

    Two cases, dispatched on the raw path's final part. ``pathlib`` collapses a
    trailing ``.`` and a trailing ``/`` while parsing and keeps ``..``, so
    ``directory.name`` is already the leaf the operator meant:

    * a final ``..`` names a directory *by traversal*. There is no leaf name for a
      planted link to sit on, so the whole path resolves.
    * anything else is a real leaf name, and only the **parent** resolves: the raw
      name is joined back on unresolved, because resolving it would follow a link
      planted at the target and write the bundle wherever that points (round one,
      adversarial HIGH -- graded as a containment write escape).

    The result is absolute and holds no ``..``: ``resolve`` output is canonical and
    the join adds one plain name. That is what every probe below rests on --
    ``lstat`` on a path still holding a ``..`` answers about the directory it names
    lexically today rather than the one the walk will use once the component in
    front of the ``..`` exists, and ``absent/../out`` passed all three arms of
    :func:`_refuse_an_unusable_target` on exactly that difference (round three,
    adversarial HIGH).

    Ancestors are widened through, which is the recorded at-or-under scope: a
    component above the named target is the operator's own path (``no_follow``'s
    narrowed prefix sentence, round two security HIGH).
    """
    if directory.name == "..":
        return directory.resolve(strict=False)
    return directory.parent.resolve(strict=False) / directory.name


def canonical_export_target(directory: Path) -> Path:
    """Public alias of :func:`_the_canonical_target`.

    ``cli/okf_commands.py``'s recovery from a pass-3 write failure needs the same
    computation :meth:`OkfExporter.export` already ran, so its cure can name where
    the bundle actually lands rather than the operator-typed path -- which, through
    an ancestor link, names somewhere else entirely (round five, adversarial
    MEDIUM, L-3).
    """
    return _the_canonical_target(directory)


def _create_the_canonical_ancestors(target: Path) -> None:
    """Create every directory above ``target``, before any guard probes it.

    Must run ahead of :func:`_refuse_an_unusable_target` and of the walk: every
    probe there ``lstat``s ``target``, and a not-yet-existing component makes every
    one of them read as absent, so a target below one is accepted untouched (round
    three, adversarial HIGH).

    ``target`` is canonical, so what this creates is always *above* it. Debris
    inside the target -- which the next run would then refuse as non-empty, having
    written it itself -- is inexpressible here, because ``target.parent`` cannot
    lie inside ``target`` (round four, adversarial HIGH). Ancestors above it do
    survive a refusal below.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _refuse_an_unusable_ancestor(target, exc)


def _refuse_an_unusable_ancestor(target: Path, cause: OSError) -> NoReturn:
    """Name what stopped the ancestors being created, never ``mkdir``'s own path.

    A blocking regular file two levels up raises ``NotADirectoryError`` naming the
    *attempted child* (``a-file/sub``), not the file that blocks it (``a-file``) --
    measured on 3.13, because ``mkdir(parents=True)`` only recurses on
    ``FileNotFoundError``, and a blocking file raises something else at the first
    ``os.mkdir`` instead, one level higher than the true cause. So the lineage is
    walked instead, and one ``lstat`` per ancestor asks the whole question --
    something is there and it is not a directory -- whatever shape that something
    has. It is ``exists(follow_symlinks=False)`` because ``exists()`` follows a
    link and reads a looping one as absent, and a loop is the one ancestor shape
    :func:`_the_canonical_target` cannot widen through.

    Direction cannot change which blocker is found -- nothing exists *under* a
    non-directory, so at most one ancestor can be one. Root-first is for the
    fallback: the last usable ancestor it sees is the deepest one that exists, and
    that is the path its cure offers a listing of.
    """
    deepest = Path(target.anchor)
    for ancestor in reversed(target.parents):
        try:
            here = ancestor.exists(follow_symlinks=False)
            usable = ancestor.is_dir()
        except OSError:
            # A component the filesystem will not even answer about -- a name too
            # long for it, measured as errno ENAMETOOLONG from `exists` itself.
            # Nothing deeper is answerable either, so the fallback below reports
            # what the `mkdir` said rather than a guess about this component.
            break
        if here and not usable:
            raise OkfExportError(ancestor, reason="unusable-ancestor") from cause
        if usable:
            deepest = ancestor
    raise OkfExportError(
        deepest,
        reason="ancestors-not-created",
        detail=cause.strerror or type(cause).__name__,
    ) from cause


def _refuse_an_unusable_target(directory: Path) -> None:
    """Refuse anything but an absent or empty directory, before the walk runs.

    A silent merge into a prior bundle would leave members of the old export
    behind -- a concept whose row has since been withdrawn is not overwritten by
    anything -- and the new manifest's digest, computed over what this run
    rendered, would not describe the tree on disk.

    **The link check is first, because every other probe here follows one.**
    ``exists`` and ``is_dir`` answer about the link's *target*, so a link to an
    empty directory passed all three and the whole bundle landed wherever it
    pointed -- outside the target this command was given, with `bundlePath`
    naming the link (round one, adversarial; graded HIGH as a containment write
    escape). The leaf is this check's whole reach; the components above it are
    :func:`_make_one_directory`'s.

    **No probe here is vacuous.** ``directory`` is
    :func:`_the_canonical_target`'s output, so it holds no ``..`` that could name
    one directory lexically and another on disk, and
    :func:`_create_the_canonical_ancestors` has already made every component above
    it real.

    **Every probe is wrapped, not just ``lstat``.** ``pathlib``'s ignored-errno set
    is ``ENOENT``/``ENOTDIR``/``EBADF``/``ELOOP`` -- ``ENAMETOOLONG`` is not in it,
    so a 300-character leaf raised straight out of ``is_symlink()`` untyped (round
    five, adversarial MEDIUM). :func:`_refuse_an_unusable_ancestor`'s lineage walk
    already wraps the same shape one level up; this is the target's own.

    **This is a check, not a lock.** Two concurrent exports into the same target
    can both clear every probe here and then merge their trees in ``_write`` --
    a same-operator race the ``--help`` text now names, recorded against the
    distributed-bundle threat-model entry rather than closed here: a lock file
    would trade it for a crashed run's retry finding the lock still held, which
    is the worse face (round five, adversarial HIGH, converted).
    """
    try:
        if directory.is_symlink():
            raise OkfExportError(directory, reason="symbolic-link")
        if not directory.exists():
            return
        if not directory.is_dir():
            raise OkfExportError(directory, reason="not-a-directory")
        if any(directory.iterdir()):
            raise OkfExportError(directory)
    except OSError as exc:
        raise OkfExportError(
            directory, reason="target-not-probable", detail=exc.strerror or type(exc).__name__
        ) from exc


def _write(root: Path, files: Mapping[str, str]) -> None:
    """The one place this module writes, so every member takes the same guards.

    Three passes, in this order: every member's key is checked, then every
    directory is created and checked, then the files are written.

    **A failure in either of the first two passes leaves ``root`` exactly as it
    was found.** Pass 1 raises before anything is created. Pass 2 used to create
    directories one at a time with nothing to unwind: a failure partway --
    ``ENAMETOOLONG`` from a deep member path, ``ENOSPC``, a denying parent --
    left every directory already made sitting inside the target, and the retry
    then refused that debris as not-empty, blaming the operator for what the
    export itself had written (round five, adversarial HIGH). :func:`_make_the_tree`
    now tracks which directories it created and, on any exception, removes
    exactly those in reverse order before re-raising; a directory pass 2 found
    already there is never touched. **Pass 3 makes no such promise** -- a file
    write can fail after some files are already on disk, and those stay, which is
    why the CLI's own cure for a pass-3 ``OSError`` offers ``ls -la`` rather than
    claiming an empty target.

    **What is guarded, exactly, is at or under ``root``.** The *leaf* of each
    member takes ``O_NOFOLLOW``
    (:func:`~theurian.security.no_follow.write_text_without_following_a_link`),
    which refuses a symbolic link at the final component and nowhere else. The
    *prefix under root* is guarded by :func:`_make_one_directory`, one component
    at a time, because ``mkdir(parents=True)`` walks a planted directory link
    without complaint -- measured, in the real export window, writing the tree
    outside the bundle root. Neither guard closes the race between the check and
    the use of a component: that needs an ``openat`` walk against directory
    descriptors, which is [#577](https://github.com/theurian/theurian/issues/577)
    and is not closed here. ``root``'s own ancestors are created by
    :func:`_create_the_canonical_ancestors`, before this function runs and before
    every guard in :func:`_refuse_an_unusable_target` does too -- not here, because
    a guard that ``lstat``s a not-yet-existing ancestor reads it as absent
    regardless of what the same path names once that ancestor is real (round
    three, adversarial HIGH). They are still the operator's own path, named on the
    command line rather than derived like every member key is, and the containment
    claim above starts at ``root`` and never claims them.
    """
    for relative in files:
        _refuse_a_member_outside_the_root(root, relative)
    _make_the_tree(root, files)
    for relative, contents in files.items():
        write_text_without_following_a_link(root / relative, contents)


def _refuse_a_member_outside_the_root(root: Path, relative: str) -> None:
    """Refuse a member key that does not stay under ``root``.

    Every key is built from item-id segments and the bundle module's own
    constants, so none can traverse; the check is kept because "no member can
    traverse" is a property of the *derivation*, and a future member derived some
    other way would inherit a guard here and escape one at the seam.

    **Keyed on the segments, not on ``is_relative_to`` alone**, which is a lexical
    comparison of path parts: ``Path('/a/b/../c').is_relative_to('/a/b')`` is
    ``True`` (measured on 3.13), so the traversal this guard exists to catch is
    exactly what it passed (round one, code review). A ``..`` segment is refused
    before any directory is created, so it cannot reach
    :func:`_make_the_tree` either.
    """
    parts = PurePosixPath(relative).parts
    if ".." in parts or not (root / relative).is_relative_to(root):
        msg = f"A bundle member resolved outside the bundle root: {relative!r}"
        raise InvariantViolationError(msg)


def _make_the_tree(root: Path, files: Mapping[str, str]) -> None:
    """Create ``root`` and every directory the members need, shallowest first.

    Built from ``parents`` so no ancestor can be missed, and sorted by depth --
    with the path as the tie-break, so the order is total and a refusal names the
    same component on every run.

    **Tracks what it created, and undoes exactly that on any failure.** A
    directory this pass finds already there is never in ``created`` and is never
    touched; one this pass makes is undone, deepest first, if a later component
    in the same pass fails (round five, adversarial HIGH -- see :func:`_write`).
    """
    directories = sorted(
        {parent for relative in files for parent in PurePosixPath(relative).parents},
        key=lambda each: (len(each.parts), each.as_posix()),
    )
    created: list[Path] = []
    try:
        for directory in directories:
            path = root / directory
            if _make_one_directory(path):
                created.append(path)
    except BaseException:
        _unwind_the_directories_this_pass_created(created)
        raise


def _unwind_the_directories_this_pass_created(created: list[Path]) -> None:
    """Best-effort ``rmdir`` of exactly what :func:`_make_the_tree` just made.

    Deepest first (``created`` is shallowest-first, so this reverses it), because
    a shallower directory cannot come off while one it contains still exists.
    Each ``rmdir`` is independent and its own failure is swallowed: this is
    already unwinding after one error, and it must never replace that error with
    a second one, or leave half the chain removed and call it done.
    """
    for path in reversed(created):
        with contextlib.suppress(OSError):
            path.rmdir()


def _make_one_directory(path: Path) -> bool:
    """``mkdir`` one component, and refuse a symbolic link standing in for it.

    Returns whether this call created ``path``, which is what lets
    :func:`_make_the_tree` undo exactly what it made and nothing a prior run left
    behind.

    ``exist_ok`` is spelled as a caught ``FileExistsError`` rather than passed,
    because the two differ on exactly the case this function is for: ``mkdir``
    with ``exist_ok=True`` accepts a *symbolic link to a directory* as "already
    there" and walks on through it, while the raise-and-check form gets to ask
    ``lstat`` what the name really is.

    ``is_symlink`` after the ``mkdir`` and not before it: a probe taken first
    describes a name that can be re-pointed before the next call. Taken after,
    the window is smaller and not closed -- #577's ``openat`` walk is what closes
    it -- and the ordinary case (we created the directory ourselves) has no
    window at all.

    Any other ``OSError`` -- a denying parent, a name the filesystem refuses --
    used to escape untyped, from a call the walk had already run past (round
    five, adversarial MEDIUM). It is now the one case ``exist_ok`` was never
    going to help with, and is converted to the same typed refusal every other
    unusable directory here gets.
    """
    try:
        path.mkdir()
    except FileExistsError:
        if path.is_symlink():
            raise OkfExportError(path, reason="symbolic-link") from None
        return False
    except OSError as exc:
        raise OkfExportError(
            path, reason="tree-not-created", detail=exc.strerror or type(exc).__name__
        ) from exc
    return True


__all__ = [
    "OkfExportError",
    "OkfExportRequest",
    "OkfExportSession",
    "OkfExporter",
    "canonical_export_target",
]
