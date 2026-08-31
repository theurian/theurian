"""A re-seed must be able to clear the drift it answers (#317).

**Before this fix**, ``scan`` compared the anchors of **every**
``upsertRevision`` across every tracked migration, and migrations are
append-only: a governed re-seed *adds* a migration, it never retracts the one
it supersedes. Reproduced on a real branch (PR #440, 2026-08-31): 27 anchors
compared, 12 drifted, ADR-0013's warning still naming the superseded
``01M0D5GZ...K3N`` migration while the new revision compared clean right
beside it -- a warning the re-seed could never clear. :data:`corpus_drift.REMEDY`
now promises the opposite -- that a re-seed clears the warning -- and says why
in its own text; see that string, not a quotation of it here, since a copy in
this file would drift the moment the source is reworded again.

**The fix landed in this same tree.** It restricts the comparison to each
item's *current* revision: the **last** ``upsertRevision`` for that ``itemId``
in application order -- the loader's own order, keyed on each migration's own
inner ``id`` (a Kahn walk over ``dependsOn``, ULID tie-break when nothing
declares one), never on ``migration_paths``' path sort, which is a population
filter and a different question (see
``theurian.domain.migration.current_revision_in``, the same rule stated for
the state-rebuild path). Not derived from ``expectedRevision`` chains: the 26
original seed migrations carry no ``expectedRevision`` field at all, so a fix
keyed off that field would leave the whole corpus uncompared.

Every test below builds a synthetic migration corpus on a real ``tmp_path``
and drives it through the public ``scan`` seam, the same way
``tests/integration/tools/test_corpus_drift_scan.py`` does -- there is no
lower seam to unit-test against: the current/superseded split has to be read
out of tracked migration files naming the same ``itemId``, and nothing below
``scan`` sees more than one file at a time.

**AC-1** and **AC-3** drove the fix: RED before it landed, GREEN now that it
has, and that transition is the reproduction and its close, not a mistake.
**AC-2** passed both before and after the fix: it is the check that the fix
mutes the superseded revision and *only* the superseded revision, not every
revision an item has ever had. **AC-4** is the floor interaction the brief
calls out by name: a corpus whose total anchors cleared a floor before the fix
no longer clears it once superseded anchors stop padding the count. **AC-5**
interleaves two items across three migrations so that "the last tracked
migration" and "each item's own last upsert" disagree about which file is
current for which item -- an implementation keyed on the corpus's last
migration, rather than per-item terminality in application order, would pass
AC-1 through AC-4 (both only ever touch one item) and fail only here; the
shipped fix passes it too.

PR #449 round one found three more gaps in the mechanism fix above, each
pinned by its own driving test below and closed in 7b17d8f. **HIGH-1** was a
wrong ordering key: pre-fix, ``_current_operations`` walked migrations in
``migration_paths``' PATH-sorted order, not the loader's actual application
order (a Kahn walk over ``dependsOn``, tie-broken by the migration's own inner
``id``), so a hand-renamed migration file could flip which revision the
checker treated as current -- reproduced on the real corpus with a ``git mv``
to ``zz-...``. **MEDIUM-1** was an ``upsertRevision`` with no ``itemId``:
pre-fix, it collapsed onto the ``""`` key alongside every other itemId-less
upsert, and only the last one survived -- the earlier one, and any drift it
carried, silently vanished rather than being reported uncheckable.
**MEDIUM-4** was ``_current_operations``' own identity check defeated by a
YAML anchor and alias: two positions in one migration's ``operations`` list
that reference the *same* Python object (as a repeated anchor/alias construct
produces) both satisfied ``current[item_id] is operation``, so one item's
terminal revision was compared twice. All three drove 7b17d8f: RED before it
landed, GREEN after.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import corpus_drift
import pytest
import yaml
from corpus_drift import Status, Verdict, held_to_floor, scan

pytestmark = pytest.mark.integration

_THIS_REPOSITORY = "https://github.com/theurian/theurian.git"
_COMMIT = "2a98d4c8963cdf46cc6169e43ac7add039745342"


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _anchor(file_path: str, **overrides: Any) -> dict[str, Any]:
    return {
        "provider": "git",
        "sourceUri": _THIS_REPOSITORY,
        "commitSha": _COMMIT,
        "filePath": file_path,
    } | overrides


def _upsert_revision(
    *, item_id: str, revision_id: str, body: str, file_path: str
) -> tuple[dict[str, Any], str, str]:
    """One ``upsertRevision`` operation, plus the body path/content it pins.

    Returned separately from a migration file, rather than written directly,
    so a caller can place several of these -- for *different* items -- into
    one migration's ``operations`` list. That shape is legal: the loader
    enumerates operations generically and never requires one item per file
    (AC-5 below), and ``_migration`` cannot produce it since it writes exactly
    one revision per call.
    """
    body_path = f".theurian/knowledge/architecture/example.{revision_id}.md"
    operation = {
        "op": "upsertRevision",
        "itemId": item_id,
        "revisionId": revision_id,
        "contentFile": f"../{body_path.removeprefix('.theurian/')}",
        "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "metadata": {
            "title": "ADR example",
            "contentType": "text/markdown",
            "status": "approved",
            "trustLevel": "reviewed",
            "sensitivity": "public",
            "sourceAnchors": [_anchor(file_path)],
        },
    }
    return operation, body_path, body


def _write_migration(
    root: Path,
    migration_id: str,
    operations: list[dict[str, Any]],
    *,
    depends_on: list[str] | None = None,
) -> str:
    """A tracked migration file holding exactly ``operations``, in that order.

    File name and inner ``id`` are the same string here. HIGH-1 needs them to
    differ -- see :func:`_write_migration_named`. ``depends_on`` defaults to
    ``None``, meaning the field is omitted from the document entirely -- every
    caller before the dependsOn-refusal tests below relies on that, since a
    document with no ``dependsOn`` key at all is the ordinary migration shape
    this whole file otherwise builds.
    """
    return _write_migration_named(
        root, migration_id, inner_id=migration_id, operations=operations, depends_on=depends_on
    )


def _write_migration_named(
    root: Path,
    file_stem: str,
    *,
    inner_id: str,
    operations: list[dict[str, Any]],
    depends_on: list[str] | None = None,
) -> str:
    """A tracked migration whose file name and inner ``id`` are independent.

    :func:`_write_migration` ties the two together, which is exactly what
    HIGH-1's fixture needs to *not* do: the loader's application order is
    keyed on the document's own ``id`` (a Kahn walk over ``dependsOn``, ULID
    tie-break), never on the file's name -- ``migration_paths`` sorting by
    path is a population filter, not a claim about apply order (see its own
    docstring: a migration renamed ``seed-adr-0005.yaml`` still loads and
    still applies, in ``id`` order, wherever the loader's walk puts it). A
    hand-renamed migration file is exactly where file order and ``id`` order
    can disagree.

    ``depends_on`` is folded into the document only when it is not ``None``:
    ``None`` omits the key entirely, ``[]`` declares it and leaves it empty.
    The dependsOn-refusal tests need exactly that distinction -- the schema
    places no ``minItems`` on ``dependsOn`` and gives it a default of ``[]``,
    so "declared empty" and "never mentioned" are the same shape on the wire,
    and a refusal keyed on key-presence rather than a real edge would treat
    them differently anyway.
    """
    document: dict[str, Any] = {
        "apiVersion": "theurian.dev/v1",
        "id": inner_id,
        "operations": operations,
    }
    if depends_on is not None:
        document["dependsOn"] = depends_on
    migration_path = f".theurian/migrations/{file_stem}.yaml"
    _write(root, migration_path, yaml.safe_dump(document, sort_keys=False))
    return migration_path


def _migration(  # noqa: PLR0913 -- every field is a distinct part of the migration shape
    root: Path,
    migration_id: str,
    *,
    item_id: str,
    revision_id: str,
    body: str,
    file_path: str,
    create: bool = False,
) -> str:
    """One tracked migration, upserting one revision, its pinned body alongside it.

    The same operation shape ``tests/integration/tools/test_corpus_drift_scan.py``'s
    ``_seed`` builds, split so a caller can chain several of these against the
    *same* ``itemId`` -- the exact shape a governed re-seed produces: an
    original-seed migration and a later re-seed migration, both tracked, both
    naming the same ``docs/`` anchor. ``create`` is only ``True`` on the first
    migration for an item; a re-seed upserts a revision on an item that already
    exists, the same as ``theurian propose accept`` does.
    """
    operation, body_path, body = _upsert_revision(
        item_id=item_id, revision_id=revision_id, body=body, file_path=file_path
    )
    operations: list[dict[str, Any]] = []
    if create:
        operations.append({"op": "createItem", "itemId": item_id, "kind": "architecture"})
    operations.append(operation)
    migration_path = _write_migration(root, migration_id, operations)
    _write(root, body_path, body)
    return migration_path


# -- AC-1 / AC-3: the re-seed corpus (PR #440's actual shape) ----------------

_ITEM = "architecture.reseeded-example"
_DOCUMENT = "docs/adr/0001-reseeded-example.md"

#: The text as the *original* seed pinned it.
_ORIGINAL_TEXT = "# ADR-0001: Reseeded Example\n\nThe text as it stood at the original seed.\n"
#: The text after the ADR was edited, and as the re-seed pinned it.
_RESEEDED_TEXT = _ORIGINAL_TEXT + "\n## Consequences\n\nA section added since the original seed.\n"

#: Sorted ahead of the re-seed IDs below, matching a real ULID's chronology.
#: `_write_migration` gives each migration the same string as its file name
#: and its inner `id`, so this also happens to sort by path here -- but it is
#: the inner `id`, not `migration_paths`' path sort, that `current_revision_in`
#: calls "application order" (see the module docstring; HIGH-1 below is the
#: test where the two orders are made to disagree).
_ORIGINAL_MIGRATION_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW1A"
_ORIGINAL_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW1B"
_RESEED_MIGRATION_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW2A"
_RESEED_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW2B"


def _reseeded_corpus(tmp_path: Path) -> tuple[str, str]:
    """The original seed plus the re-seed that supersedes it, and the document now.

    Both migrations' anchors name the same ``docs/`` file. Only the document as
    it stands *now* is written -- holding ``_RESEEDED_TEXT`` -- which is what
    makes the original revision's pin stale and the re-seed's pin current.
    """
    original = _migration(
        tmp_path,
        _ORIGINAL_MIGRATION_ID,
        item_id=_ITEM,
        revision_id=_ORIGINAL_REVISION,
        body=_ORIGINAL_TEXT,
        file_path=_DOCUMENT,
        create=True,
    )
    reseed = _migration(
        tmp_path,
        _RESEED_MIGRATION_ID,
        item_id=_ITEM,
        revision_id=_RESEED_REVISION,
        body=_RESEEDED_TEXT,
        file_path=_DOCUMENT,
    )
    _write(tmp_path, _DOCUMENT, _RESEEDED_TEXT)
    return original, reseed


def test_a_superseded_revisions_stale_anchor_is_not_compared_after_a_reseed(
    tmp_path: Path,
) -> None:
    """AC-1: a later migration upserting the same item mutes the one it supersedes.

    Given the original seed (pins `_ORIGINAL_TEXT`) and a later re-seed (pins
    `_RESEEDED_TEXT`) for the same item, and the document now reading
    `_RESEEDED_TEXT`, when `scan` runs, the superseded revision must not be
    compared at all -- not reported drifted, not reported anywhere.

    RED before the fix, GREEN after: before it landed, `scan` compared every
    `upsertRevision` in every tracked migration, so the original seed's stale
    pin was compared against the current document and reported `DRIFTED`,
    permanently, exactly as reproduced on PR #440's branch (27 compared, 12
    drifted).
    """
    original, reseed = _reseeded_corpus(tmp_path)

    report = scan(tmp_path, tracked=[original, reseed])

    assert report.status is Status.CLEAN
    assert report.drifted == ()
    assert all(item.revision_id != _ORIGINAL_REVISION for item in report.comparisons)


def test_the_compared_population_after_a_reseed_is_current_revisions_only(
    tmp_path: Path,
) -> None:
    """AC-3: the floor-relevant count is current revisions only, not every revision.

    RED before the fix, GREEN after: before it landed, both the original
    seed's anchor and the re-seed's anchor were compared, so
    `len(report.compared) == 2` -- padding the count with a revision nobody
    can act on any more.

    The `report.detail` assertion is PR #449 round two's MEDIUM-A: the
    "; K superseded" clause `_verdict` prints was pinned by nothing --
    mutating `superseded` to a constant `0`, and to `superseded * 2`, both
    survived the full battery before this assertion existed. `"1 superseded"`
    is the exact count for this corpus (two migrations, one item, one
    superseded op), so either mutation now changes the printed digit and this
    line goes RED: `0` renders "0 superseded" and `superseded * 2` renders "2
    superseded", neither of which contains the substring asserted here.
    """
    original, reseed = _reseeded_corpus(tmp_path)

    report = scan(tmp_path, tracked=[original, reseed])

    assert len(report.compared) == 1
    assert report.compared[0].revision_id == _RESEED_REVISION
    assert report.compared[0].migration == reseed
    assert "1 superseded" in report.detail


# -- AC-2: the guard survives the fix, it does not survive by luck -----------

_GUARD_ITEM = "architecture.guarded-example"
_GUARD_DOCUMENT = "docs/adr/0002-guarded-example.md"

#: What the *original* migration pinned, and what the document still reads --
#: unrelated to the finding this fix closes.
_GUARD_UNCHANGED_TEXT = "# ADR-0002: Guarded Example\n\nUnchanged since the original migration.\n"
#: What the *current* (later) migration's own snapshot pins -- a genuine
#: mismatch against the document as it stands, independent of the re-seed
#: mechanism entirely.
_GUARD_STALE_CURRENT_TEXT = (
    _GUARD_UNCHANGED_TEXT
    + "\n## Consequences\n\nWhat the current revision pins, and the document does not have.\n"
)

_GUARD_OLD_MIGRATION_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW3A"
_GUARD_OLD_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW3B"
_GUARD_NEW_MIGRATION_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW4A"
_GUARD_NEW_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW4B"


def test_the_current_revisions_own_drift_is_still_reported_after_a_reseed(
    tmp_path: Path,
) -> None:
    """AC-2: muting the superseded revision must not mute the current one too.

    Given item Y with two migrations -- the first upserts a revision whose pin
    still matches the document (irrelevant to the finding), the second (later,
    current) upserts a revision whose pin does *not* match the document -- when
    `scan` runs, Y's current revision IS reported DRIFT.

    This must pass both before and after the fix: it is the check that a fix
    restricting comparison to "current revisions" does not go on to restrict it
    to *no* revisions, and that it identifies "current" as the later migration
    (application order) rather than accidentally comparing the earlier one --
    either mistake would still pass AC-1 while silently losing this finding.
    """
    old = _migration(
        tmp_path,
        _GUARD_OLD_MIGRATION_ID,
        item_id=_GUARD_ITEM,
        revision_id=_GUARD_OLD_REVISION,
        body=_GUARD_UNCHANGED_TEXT,
        file_path=_GUARD_DOCUMENT,
        create=True,
    )
    new = _migration(
        tmp_path,
        _GUARD_NEW_MIGRATION_ID,
        item_id=_GUARD_ITEM,
        revision_id=_GUARD_NEW_REVISION,
        body=_GUARD_STALE_CURRENT_TEXT,
        file_path=_GUARD_DOCUMENT,
    )
    _write(tmp_path, _GUARD_DOCUMENT, _GUARD_UNCHANGED_TEXT)

    report = scan(tmp_path, tracked=[old, new])

    assert report.status is Status.DRIFTED
    assert [item.revision_id for item in report.drifted] == [_GUARD_NEW_REVISION]
    assert [item.item_id for item in report.drifted] == [_GUARD_ITEM]


# -- AC-4: the floor must not be padded by a revision nobody can act on ------


def test_a_reseed_that_drops_below_the_floor_once_superseded_anchors_are_excluded_exits_two(
    tmp_path: Path,
) -> None:
    """AC-4: a corpus clearing a floor only because a superseded anchor pads the count.

    The re-seed corpus above has 2 total anchors and 1 current one. Held to a
    floor of 2, `scan` before the fix compared both (the superseded one
    included) and cleared the floor -- so this exited 1 (drift), not 2. Once
    the superseded anchor stops being compared, only 1 anchor clears the floor
    of 2, and the run must report `NOTHING_COMPARED` and exit 2 -- the floor
    existing precisely to catch a run that is quietly checking less than it
    claims.

    RED before the fix, GREEN after, for that reason: `held_to_floor` itself
    is unmodified by the fix and is not the defect -- what changed is how many
    anchors reach it from the same two tracked migrations.
    """
    original, reseed = _reseeded_corpus(tmp_path)

    report = held_to_floor(scan(tmp_path, tracked=[original, reseed]), 2)

    assert report.status is Status.NOTHING_COMPARED
    assert corpus_drift.exit_code(report, advisory=False) == 2


# -- AC-5: two items interleaved so file order and item terminality disagree --

_ITEM_A = "architecture.interleaved-a"
_ITEM_B = "architecture.interleaved-b"
_DOCUMENT_A = "docs/adr/0003-interleaved-a.md"
_DOCUMENT_B = "docs/adr/0004-interleaved-b.md"

#: What m1 pinned for each item -- superseded for both by the time the corpus
#: is scanned, and both drifted against the document as it stands.
_STALE_TEXT_A = "# ADR-0003: Interleaved A\n\nThe text as m1 pinned it.\n"
_STALE_TEXT_B = "# ADR-0004: Interleaved B\n\nThe text as m1 pinned it.\n"
#: What each item's *own* later migration pins, and what the document now reads.
_CURRENT_TEXT_A = _STALE_TEXT_A + "\n## Consequences\n\nWhat m2 re-seeds A to.\n"
_CURRENT_TEXT_B = _STALE_TEXT_B + "\n## Consequences\n\nWhat m3 re-seeds B to.\n"

#: Sorted m1 < m2 < m3, matching application order. A's current revision sits
#: in the *middle* migration -- m3, the corpus's last tracked migration,
#: touches only B.
_INTERLEAVED_M1_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW5A"
_INTERLEAVED_M2_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW6A"
_INTERLEAVED_M3_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW7A"
_A_STALE_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW5B"
_B_STALE_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW5C"
_A_CURRENT_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW6B"
_B_CURRENT_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW7B"


def test_per_item_terminality_disagrees_with_last_migration_and_the_fix_must_follow_the_item(
    tmp_path: Path,
) -> None:
    """AC-5: two items interleaved across three migrations, file order != item terminality.

    Given m1 upserting BOTH item A and item B (a single migration carrying
    upserts for two different items, which the loader applies as written --
    it enumerates operations generically and never requires one item per
    file), m2 upserting only A's current revision, and m3 upserting only B's
    current revision, when `scan` runs, only A's and B's *own* last upsert is
    compared: A's current revision (in m2, not the corpus's last migration)
    and B's current revision (in m3). Neither stale revision from m1 is
    compared.

    This is the test AC-1 through AC-4 cannot stand in for: every corpus
    those four build touches exactly one item, so an implementation that
    conflates "the corpus's last tracked migration" with "an item's own last
    upsert" -- comparing only m3's operations, which would drop A's current
    revision (in m2) as if it were superseded, while still correctly muting
    A's and B's stale m1 revisions -- passes all four and fails only here.

    RED before the fix, GREEN after: before it landed, `scan` compared every
    `upsertRevision` in every tracked migration, so all four anchors were
    compared -- both items' stale and current revisions -- and 2 of them (the
    stale ones) were reported drifted.
    """
    a_stale, a_stale_body_path, a_stale_body = _upsert_revision(
        item_id=_ITEM_A, revision_id=_A_STALE_REVISION, body=_STALE_TEXT_A, file_path=_DOCUMENT_A
    )
    b_stale, b_stale_body_path, b_stale_body = _upsert_revision(
        item_id=_ITEM_B, revision_id=_B_STALE_REVISION, body=_STALE_TEXT_B, file_path=_DOCUMENT_B
    )
    m1 = _write_migration(
        tmp_path,
        _INTERLEAVED_M1_ID,
        [
            {"op": "createItem", "itemId": _ITEM_A, "kind": "architecture"},
            {"op": "createItem", "itemId": _ITEM_B, "kind": "architecture"},
            a_stale,
            b_stale,
        ],
    )
    _write(tmp_path, a_stale_body_path, a_stale_body)
    _write(tmp_path, b_stale_body_path, b_stale_body)

    a_current, a_current_body_path, a_current_body = _upsert_revision(
        item_id=_ITEM_A,
        revision_id=_A_CURRENT_REVISION,
        body=_CURRENT_TEXT_A,
        file_path=_DOCUMENT_A,
    )
    m2 = _write_migration(tmp_path, _INTERLEAVED_M2_ID, [a_current])
    _write(tmp_path, a_current_body_path, a_current_body)

    b_current, b_current_body_path, b_current_body = _upsert_revision(
        item_id=_ITEM_B,
        revision_id=_B_CURRENT_REVISION,
        body=_CURRENT_TEXT_B,
        file_path=_DOCUMENT_B,
    )
    m3 = _write_migration(tmp_path, _INTERLEAVED_M3_ID, [b_current])
    _write(tmp_path, b_current_body_path, b_current_body)

    _write(tmp_path, _DOCUMENT_A, _CURRENT_TEXT_A)
    _write(tmp_path, _DOCUMENT_B, _CURRENT_TEXT_B)

    report = scan(tmp_path, tracked=[m1, m2, m3])

    assert report.status is Status.CLEAN
    assert len(report.compared) == 2
    assert {item.revision_id for item in report.compared} == {
        _A_CURRENT_REVISION,
        _B_CURRENT_REVISION,
    }
    assert all(
        item.revision_id not in {_A_STALE_REVISION, _B_STALE_REVISION}
        for item in report.comparisons
    )


# -- HIGH-1: terminality follows the inner migration id, not the file name ---

_HIGH1_ITEM = "architecture.reordered-example"
_HIGH1_DOCUMENT = "docs/adr/0005-reordered-example.md"

#: What the original seed pinned.
_HIGH1_STALE_TEXT = "# ADR-0005: Reordered Example\n\nThe text as the original pinned it.\n"
#: What the re-seed pins, and what the document now reads.
_HIGH1_CURRENT_TEXT = (
    _HIGH1_STALE_TEXT + "\n## Consequences\n\nWhat the re-seed pins, and the document now reads.\n"
)

#: The re-seed's inner `id` sorts AFTER the original's -- real ULID chronology
#: -- even though the file it is written to sorts BEFORE the original's file
#: by name. `migration_paths` sorts by path; the loader applies by `id`.
_HIGH1_ORIGINAL_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW8A"
_HIGH1_ORIGINAL_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW8B"
_HIGH1_RESEED_ID = "01MB4V3XKQ7ZPYE8R2NGT5HW9A"
_HIGH1_RESEED_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HW9B"


def test_terminality_follows_the_inner_migration_id_not_the_file_name_sort(
    tmp_path: Path,
) -> None:
    """HIGH-1 (PR #449 round one): a hand-renamed migration file must not flip apply order.

    Given the original seed -- the earlier inner `id` -- written to a file
    that sorts LAST by name (`zzz-original.yaml`), and the re-seed -- the
    later inner `id`, carrying the `expectedRevision` it supersedes -- written
    to a file that sorts FIRST (`aaa-reseed.yaml`), both legal:
    `migration_paths` admits any tracked `*.yaml` directly under the
    migrations directory, named or renamed, and never reads the name to
    decide order (`test_corpus_drift_population.py`'s `seed-adr-0005.yaml`
    case is the same shape on the real loader). When `scan` runs, the
    re-seed's revision is terminal -- its anchor matches the document, and
    the original's stale anchor is not compared at all.

    RED before 7b17d8f, GREEN after: pre-fix, `_current_operations` walked
    `revisions_by_path` in `migration_paths`' PATH-sorted order, not the
    document's own `id`, so `zzz-original.yaml` -- sorted last by name -- was
    read last and won as "current" even though its `id` is the earlier one.
    The true current revision (`aaa-reseed.yaml`) was dropped as if
    superseded, and the stale original was compared and reported DRIFTED
    against the document it no longer describes. Reproduced by the
    orchestrator on the real corpus with a `git mv` to `zz-...` (PR #449
    round-one HIGH-1).
    """
    original_op, original_body_path, original_body = _upsert_revision(
        item_id=_HIGH1_ITEM,
        revision_id=_HIGH1_ORIGINAL_REVISION,
        body=_HIGH1_STALE_TEXT,
        file_path=_HIGH1_DOCUMENT,
    )
    original = _write_migration_named(
        tmp_path,
        "zzz-original",
        inner_id=_HIGH1_ORIGINAL_ID,
        operations=[
            {"op": "createItem", "itemId": _HIGH1_ITEM, "kind": "architecture"},
            original_op,
        ],
    )
    _write(tmp_path, original_body_path, original_body)

    reseed_op, reseed_body_path, reseed_body = _upsert_revision(
        item_id=_HIGH1_ITEM,
        revision_id=_HIGH1_RESEED_REVISION,
        body=_HIGH1_CURRENT_TEXT,
        file_path=_HIGH1_DOCUMENT,
    )
    reseed = _write_migration_named(
        tmp_path,
        "aaa-reseed",
        inner_id=_HIGH1_RESEED_ID,
        operations=[{**reseed_op, "expectedRevision": _HIGH1_ORIGINAL_REVISION}],
    )
    _write(tmp_path, reseed_body_path, reseed_body)

    _write(tmp_path, _HIGH1_DOCUMENT, _HIGH1_CURRENT_TEXT)

    report = scan(tmp_path, tracked=[original, reseed])

    assert report.status is Status.CLEAN
    assert len(report.compared) == 1
    assert report.compared[0].revision_id == _HIGH1_RESEED_REVISION
    assert all(item.revision_id != _HIGH1_ORIGINAL_REVISION for item in report.comparisons)


# -- MEDIUM-1: a missing itemId must not collapse two upserts onto one bucket -

_MEDIUM1_DOCUMENT_A = "docs/adr/0006-anonymous-a.md"
_MEDIUM1_DOCUMENT_B = "docs/adr/0007-anonymous-b.md"

#: A's pin is stale against the document written below -- real drift, on an
#: operation this fixture never gives an `itemId`.
_MEDIUM1_PINNED_TEXT_A = "# Anonymous A\n\nWhat migration A's upsert pins.\n"
_MEDIUM1_ACTUAL_TEXT_A = (
    _MEDIUM1_PINNED_TEXT_A + "\n## Drifted\n\nThe document has moved since A pinned it.\n"
)
#: B's pin matches its document -- a clean control, so a report that reads
#: CLEAN overall (because only B survived the collision) is visibly wrong.
_MEDIUM1_TEXT_B = "# Anonymous B\n\nWhat migration B's upsert pins, and the document still reads.\n"

_MEDIUM1_MIGRATION_A_ID = "01MB4V3XKQ7ZPYE8R2NGT5HWCA"
_MEDIUM1_MIGRATION_B_ID = "01MB4V3XKQ7ZPYE8R2NGT5HWDA"
_MEDIUM1_REVISION_A = "01MB4V3XKQ7ZPYE8R2NGT5HWCB"
_MEDIUM1_REVISION_B = "01MB4V3XKQ7ZPYE8R2NGT5HWDB"


def test_an_upsert_with_no_itemid_is_uncheckable_not_silently_dropped(tmp_path: Path) -> None:
    """MEDIUM-1 (PR #449 round one): a missing `itemId` must not erase another item's finding.

    Given two migrations, each carrying one `upsertRevision` with no `itemId`
    key at all and a distinct body, and the first one's document drifted from
    its pin, when `scan` runs, both are reported UNCHECKABLE -- neither
    participates in per-item terminality, because neither names an item to be
    terminal *for*.

    RED before 7b17d8f, GREEN after: pre-fix, `_current_operations` keyed on
    `str(operation.get("itemId", ""))`, so both itemId-less upserts collapsed
    onto the same `""` bucket. Only the physically last one (B, by
    `migration_paths`' path order) satisfied the `is operation` identity
    check; A's operation was silently dropped from `revisions_by_path`'s walk
    before it ever reached `_compare` -- not compared, not reported
    uncheckable, simply absent. Because B's own document matched its pin, the
    report read CLEAN overall, and A's real drift vanished without a trace.
    """
    a_op, a_body_path, a_body = _upsert_revision(
        item_id="placeholder",
        revision_id=_MEDIUM1_REVISION_A,
        body=_MEDIUM1_PINNED_TEXT_A,
        file_path=_MEDIUM1_DOCUMENT_A,
    )
    del a_op["itemId"]
    migration_a = _write_migration(tmp_path, _MEDIUM1_MIGRATION_A_ID, [a_op])
    _write(tmp_path, a_body_path, a_body)
    _write(tmp_path, _MEDIUM1_DOCUMENT_A, _MEDIUM1_ACTUAL_TEXT_A)

    b_op, b_body_path, b_body = _upsert_revision(
        item_id="placeholder",
        revision_id=_MEDIUM1_REVISION_B,
        body=_MEDIUM1_TEXT_B,
        file_path=_MEDIUM1_DOCUMENT_B,
    )
    del b_op["itemId"]
    migration_b = _write_migration(tmp_path, _MEDIUM1_MIGRATION_B_ID, [b_op])
    _write(tmp_path, b_body_path, b_body)
    _write(tmp_path, _MEDIUM1_DOCUMENT_B, _MEDIUM1_TEXT_B)

    report = scan(tmp_path, tracked=[migration_a, migration_b])

    verdicts_by_revision = {item.revision_id: item.verdict for item in report.comparisons}
    assert verdicts_by_revision.get(_MEDIUM1_REVISION_A) is Verdict.UNCHECKABLE
    assert verdicts_by_revision.get(_MEDIUM1_REVISION_B) is Verdict.UNCHECKABLE
    assert len(report.comparisons) == 2


# -- MEDIUM-4: a YAML anchor/alias must not compare one revision twice -------

_MEDIUM4_ITEM = "architecture.aliased-example"
_MEDIUM4_DOCUMENT = "docs/adr/0008-aliased-example.md"
_MEDIUM4_TEXT = "# ADR-0008: Aliased Example\n\nWhat the single upsert pins, unchanged.\n"

_MEDIUM4_MIGRATION_ID = "01MB4V3XKQ7ZPYE8R2NGT5HWEA"
_MEDIUM4_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HWEB"


def test_a_yaml_anchor_and_alias_do_not_compare_one_revision_twice(tmp_path: Path) -> None:
    """MEDIUM-4 (PR #449 round one): the same operation object at two positions is one revision.

    Given one migration whose `operations` list places the *same* Python
    object at two positions -- `- &up {op: upsertRevision, ...}` then
    `- *up`, which is what a repeated YAML anchor/alias round-trips to
    through `yaml.safe_load` (confirmed empirically: `ops[i] is ops[j]` is
    `True` for the two positions) -- when `scan` runs, the item's terminal
    revision produces exactly one `Comparison`.

    RED before 7b17d8f, GREEN after: pre-fix, `_current_operations` picked
    the winner by identity (`current[item_id] is operation`), on the
    assumption that two *distinct* objects are never the same object even
    when byte-identical. A YAML alias defeated that assumption directly: both
    positions in `revisions` referenced the identical object `current[item_id]`
    was set to, so the identity check was `True` at *both* positions, and both
    were compared -- one revision, two `Comparison` entries.
    """
    shared_op, body_path, body = _upsert_revision(
        item_id=_MEDIUM4_ITEM,
        revision_id=_MEDIUM4_REVISION,
        body=_MEDIUM4_TEXT,
        file_path=_MEDIUM4_DOCUMENT,
    )
    migration = _write_migration(
        tmp_path,
        _MEDIUM4_MIGRATION_ID,
        [
            {"op": "createItem", "itemId": _MEDIUM4_ITEM, "kind": "architecture"},
            shared_op,
            shared_op,  # same object, twice -- what a YAML anchor/alias round-trips to
        ],
    )
    _write(tmp_path, body_path, body)
    _write(tmp_path, _MEDIUM4_DOCUMENT, _MEDIUM4_TEXT)

    report = scan(tmp_path, tracked=[migration])

    matching = [item for item in report.comparisons if item.revision_id == _MEDIUM4_REVISION]
    assert len(matching) == 1
    assert matching[0].verdict is Verdict.MATCHED


# -- The dependsOn refusal: a limit this tool states, pinned from both sides -

_DEPENDS_ON_ITEM = "architecture.depends-on-example"
_DEPENDS_ON_DOCUMENT = "docs/adr/0009-depends-on-example.md"
_DEPENDS_ON_TEXT = (
    "# ADR-0009: DependsOn Example\n\nWhat the single upsert pins, matching the document.\n"
)

_DEPENDS_ON_MIGRATION_ID = "01MB4V3XKQ7ZPYE8R2NGT5HWFA"
_DEPENDS_ON_REVISION = "01MB4V3XKQ7ZPYE8R2NGT5HWFB"
#: A well-formed ULID edge -- what a schema-valid `dependsOn` entry looks
#: like. Its target need not exist as a migration of its own:
#: `_depends_on_refusal` fires on the declaration itself, before anything
#: about the graph it names is resolved.
_DEPENDS_ON_EDGE = "01MB4V3XKQ7ZPYE8R2NGT5HWGA"


def _depends_on_corpus(tmp_path: Path, *, depends_on: list[str] | None) -> str:
    """One tracked, otherwise-ordinary migration, with `dependsOn` set as given.

    The upsert's own anchor matches the document written alongside it, so a
    run that does *not* refuse compares clean. That is deliberate: it is the
    only way the assertions below can tell "refused" apart from "compared,
    and happened to find nothing wrong" -- a corpus that already drifted
    would report `NOTHING_COMPARED` for the wrong reason on a mutant that
    dropped the refusal but broke the comparison some other way.
    """
    operation, body_path, body = _upsert_revision(
        item_id=_DEPENDS_ON_ITEM,
        revision_id=_DEPENDS_ON_REVISION,
        body=_DEPENDS_ON_TEXT,
        file_path=_DEPENDS_ON_DOCUMENT,
    )
    migration = _write_migration(
        tmp_path,
        _DEPENDS_ON_MIGRATION_ID,
        [
            {"op": "createItem", "itemId": _DEPENDS_ON_ITEM, "kind": "architecture"},
            operation,
        ],
        depends_on=depends_on,
    )
    _write(tmp_path, body_path, body)
    _write(tmp_path, _DEPENDS_ON_DOCUMENT, _DEPENDS_ON_TEXT)
    return migration


def test_a_declared_dependson_edge_refuses_the_whole_run_even_under_advisory(
    tmp_path: Path,
) -> None:
    """Pin the guard, direction one (PR #449 round two, item 1): a real edge must refuse.

    Given one tracked migration declaring `dependsOn: ["<ulid>"]` -- one
    well-formed edge, the shape the published schema allows -- when `scan`
    runs, the whole run reports `NOTHING_COMPARED`, its detail naming
    `dependsOn` as this tool's own limit rather than a finding about the
    corpus, and `exit_code(report, advisory=True)` is still 2: the refusal is
    not drift, so `--advisory` -- which downgrades drift, and only drift, to
    exit 0 -- must not read it as green.

    GREEN against the shipped code: this pins a guard that already exists
    (`_depends_on_refusal`, landed in 7b17d8f) rather than driving one in --
    it is not a RED driver. It goes RED the moment that guard is deleted or
    weakened: deleting it lets this migration's own clean anchor straight
    through `_compare`, so `scan` would report `CLEAN` -- not
    `NOTHING_COMPARED` -- and `exit_code(..., advisory=True)` would read `0`.
    Weakening it to key on the field's mere *presence* rather than a real
    edge would still pass this test on its own; the test below is what tells
    that mutation apart from the real guard.
    """
    migration = _depends_on_corpus(tmp_path, depends_on=[_DEPENDS_ON_EDGE])

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.NOTHING_COMPARED
    assert "dependsOn" in report.detail
    assert "tool" in report.detail
    assert corpus_drift.exit_code(report, advisory=True) == 2


def test_a_declared_but_empty_dependson_does_not_refuse_the_run(tmp_path: Path) -> None:
    """Pin the guard, direction two: no edges is not a declared dependency graph.

    Given the same shape of migration with `dependsOn: []` -- declared, and
    empty; the schema places no `minItems` on the field and its own default is
    `[]`, so this is indistinguishable, on the wire, from a migration that
    never mentions `dependsOn` at all -- when `scan` runs, the run proceeds
    normally: the item's anchor is compared, and nothing is refused.

    GREEN against the shipped code, a pin and not a RED driver, and the
    deletion-direction case (a) above cannot cover by itself: a mutant that
    weakens `_depends_on_refusal` to `"dependsOn" in document` (key presence)
    rather than `bool(document.get("dependsOn"))` (a real edge) still passes
    (a), because both readings refuse a non-empty list -- but that mutant
    turns this migration's clean anchor into a false `NOTHING_COMPARED` here,
    which is exactly the failure `_depends_on_refusal`'s own `not depends_on`
    clause exists to avoid. `report.compared` is asserted, matching how AC-3
    and AC-4 above check the same thing, and it is what a maintainer reading
    the floor logic (`held_to_floor`) already holds this corpus to.
    """
    migration = _depends_on_corpus(tmp_path, depends_on=[])

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.CLEAN
    assert len(report.compared) > 0
    assert report.compared[0].revision_id == _DEPENDS_ON_REVISION
