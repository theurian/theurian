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
in application order -- the loader's own order, which ``migration_paths``
already sorts by (see ``theurian.domain.migration.current_revision_in``, the
same rule stated for the state-rebuild path). Not derived from
``expectedRevision`` chains: the 26 original seed migrations carry no
``expectedRevision`` field at all, so a fix keyed off that field would leave
the whole corpus uncompared.

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
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import corpus_drift
import pytest
import yaml
from corpus_drift import Status, held_to_floor, scan

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


def _write_migration(root: Path, migration_id: str, operations: list[dict[str, Any]]) -> str:
    """A tracked migration file holding exactly ``operations``, in that order."""
    migration_path = f".theurian/migrations/{migration_id}.yaml"
    _write(
        root,
        migration_path,
        yaml.safe_dump(
            {"apiVersion": "theurian.dev/v1", "id": migration_id, "operations": operations},
            sort_keys=False,
        ),
    )
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

#: Sorted ahead of the re-seed IDs below, matching a real ULID's chronology --
#: `migration_paths` sorts by this same string, and it is the key
#: `current_revision_in` calls "application order" (see the module docstring).
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
    """
    original, reseed = _reseeded_corpus(tmp_path)

    report = scan(tmp_path, tracked=[original, reseed])

    assert len(report.compared) == 1
    assert report.compared[0].revision_id == _RESEED_REVISION
    assert report.compared[0].migration == reseed


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
