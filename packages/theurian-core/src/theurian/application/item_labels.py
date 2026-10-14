"""The governance labels and status an existing item holds, as the drafter and ``accept`` read them.

``KnowledgeItem.with_revision`` adopts sensitivity, trust level and namespace from
the revision a content update lands, so a drafted update that leaves one out is
not neutral: the loader fills in its default and the item is re-labelled with no
reviewer asked (GHSA-v2qg-23fc-7fqp). This module carries what the item currently
holds, so the drafter can restate it and ``accept`` can compare it. Status rides
with them because ``upsertRevision`` adopts ``metadata.status`` too: an update
the drafter wrote as ``approved`` readmits a retired item unless something
compares the status before and after.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from theurian.application.authorization import DISCLOSURE_ORDER
from theurian.domain.enums import KnowledgeStatus, Sensitivity, TrustLevel, may_surface
from theurian.domain.identifiers import ItemId, RevisionId


@dataclass(frozen=True, slots=True)
class ItemLabels:
    sensitivity: Sensitivity
    trust_level: TrustLevel
    namespace: str
    status: KnowledgeStatus


@dataclass(frozen=True, slots=True)
class CurrentItem:
    """An existing item's labels and its current revision, if it has one.

    ``revision_id`` is ``None`` for an item that was created and never revised: it
    holds labels -- its ``createItem``'s, or a later ``changeSensitivity``'s -- that
    a first revision would overwrite, but has no revision for an
    ``expectedRevision`` to name.
    """

    revision_id: RevisionId | None
    labels: ItemLabels


#: Returns an existing item's labels and current revision, or ``None`` when the
#: caller has no such item to update. One lookup answers both the optimistic-
#: concurrency check and the labels a draft inherits, so the two cannot be read
#: from different states. Injected: the MCP tools supply a caller-scoped read of
#: the canonical store, the CLI the engine's own replay of the loaded migration
#: set (``cli/migration_pipeline.py::current_item_in``).
CurrentItemLookup = Callable[[ItemId], CurrentItem | None]

#: The revision an item currently has, answered without reading its labels. Lets a
#: composition root whose :data:`CurrentItemLookup` is expensive refuse a missing
#: or stale ``expectedRevision`` first.
CurrentRevisionLookup = Callable[[ItemId], RevisionId | None]

#: Every item a replayed migration set holds, by id.
ReplayedItems = Mapping[ItemId, ItemLabels]


def is_lowering(current: Sensitivity, proposed: Sensitivity) -> bool:
    """Whether ``proposed`` discloses to more readers than ``current``.

    By :data:`DISCLOSURE_ORDER`, never ``<``: :class:`Sensitivity` is a ``StrEnum``,
    so ``<`` orders the words alphabetically and ``confidential < internal`` holds.
    """
    return DISCLOSURE_ORDER.index(proposed) < DISCLOSURE_ORDER.index(current)


#: The one way to lower an item's sensitivity. A content update is not it: the
#: reclassification is its own reviewed migration, so a lowering is a decision
#: and never a side effect of rewording a body.
DECLASSIFY_REMEDY = (
    "To lower an item's sensitivity, hand-author a migration with a `changeSensitivity` "
    "operation under .theurian/migrations/ and check it with `theurian migrate validate`."
)

#: A refused proposal's migration id predates the migration its remedy sends the
#: reader to write, so accepting it again replays it before that migration.
DRAFT_AGAIN_CLAUSE = (
    "Once that migration lands, draft the update again with `theurian propose` rather than "
    "accepting this proposal again: its migration id predates it and replays before it, "
    "and only `dependsOn` places the new draft after it, so edit that migration's id into "
    "the `dependsOn` of the new draft's migration file."
)

#: The remedy ``accept`` gives for a lowering. One cause per step, because a
#: draft that restates the same label cannot cure a served state that lags the
#: landed set: that one needs ``migrate apply`` first.
ACCEPT_LOWERING_REMEDY = (
    "Nothing has moved. If the served state lags the landed migrations, run "
    "`theurian migrate apply`, then run `theurian propose` for the update again. Otherwise "
    "run `theurian propose` again naming the item's current sensitivity, or omitting it "
    "with this build. " + DECLASSIFY_REMEDY + " " + DRAFT_AGAIN_CLAUSE
)


def lowered_sensitivities(
    before: ReplayedItems, after: ReplayedItems
) -> tuple[tuple[ItemId, Sensitivity, Sensitivity], ...]:
    """Each item ``before`` holds whose effective sensitivity is lower ``after``.

    Compares effective post-state, never the keys a migration wrote: a proposal
    drafted before inheritance existed omits ``sensitivity`` and the loader then
    says ``internal``, which only a replay shows. Items ``after`` alone holds are
    new, so there is nothing for them to lower. Ordered by item id, which is
    unique, so the order is total.
    """
    return tuple(
        (item_id, held.sensitivity, after[item_id].sensitivity)
        for item_id, held in sorted(before.items(), key=lambda entry: entry[0].value)
        if item_id in after and is_lowering(held.sensitivity, after[item_id].sensitivity)
    )


#: The one way to readmit a retired item. A content update is not it: the item
#: was withdrawn by a reviewed decision, and bringing it back is its own
#: reviewed migration. The same sentence serves the draft-side refusal, which
#: must name no status.
READMIT_REMEDY = (
    "To readmit a retired item, hand-author a migration with a `restoreItem` operation "
    "under .theurian/migrations/ and check it with `theurian migrate validate`."
)

#: The remedy ``accept`` gives for a readmission.
ACCEPT_READMISSION_REMEDY = (
    "Nothing has moved. "
    + READMIT_REMEDY
    + " Once it lands, draft the update again with `theurian propose` rather than accepting "
    "this proposal again: its migration id predates the restoreItem and replays before it, "
    "and only `dependsOn` places the new draft after it, so edit that migration's id into "
    "the `dependsOn` of the new draft's migration file."
)


def readmitted_items(
    before: ReplayedItems, after: ReplayedItems
) -> tuple[tuple[ItemId, KnowledgeStatus, KnowledgeStatus], ...]:
    """Each item ``before`` holds retired whose status ``after`` is surfaceable.

    Both sides are decided by ``may_surface(..., include_unapproved=True)``, the
    definition the gates read, so under its widest flag the floor and the gate
    cannot disagree about what counts as served. That is also why ``draft`` or
    ``proposed`` moving to
    ``approved`` is not reported: the merge of the proposal is the approval.
    Ordered by item id, which is unique, so the order is total.
    """
    return tuple(
        (item_id, held.status, after[item_id].status)
        for item_id, held in sorted(before.items(), key=lambda entry: entry[0].value)
        if item_id in after
        and not may_surface(held.status, include_unapproved=True)
        and may_surface(after[item_id].status, include_unapproved=True)
    )


def describe_readmission(item_id: ItemId, was: KnowledgeStatus, now: KnowledgeStatus) -> str:
    return f"{item_id.value} from {was.value} to {now.value}"


def describe_lowering(item_id: ItemId, was: Sensitivity, now: Sensitivity) -> str:
    return f"{item_id.value} from {was.value} to {now.value}"
