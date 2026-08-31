"""A synthetic corpus in a temporary tree, and what `scan` makes of it.

``scan(root, tracked=...)`` is the seam the implementer left for exactly this:
with ``tracked`` passed explicitly the function never shells out to git, so a
directory holding ``.theurian/migrations/one.yaml`` and ``docs/one.md`` is a
complete, self-contained corpus. Nothing here reads this repository's own
committed migrations -- a test keyed to live content would flip the day
somebody re-seeds ADR-0005, and would say nothing about the branches no
committed anchor happens to exercise.

**The expected digest is computed with `hashlib.sha256`, not with
`ContentHash.of_bytes`.** The tool deliberately reuses the product's own call so
a second implementation cannot disagree with it; a test that reused it too would
agree with any hash function at all, including a broken one.

**One test here replaces `anchor_refusal`, and it is the only fake in the
file.** The seam it drives -- a refusal that is falsy but not ``None`` -- is not
reachable through the shipped clauses, every one of which returns a written
sentence, and the behaviour it protects is an anchor silently leaving the run
through neither list. A corpus cannot produce that input; only a future clause
can.

The uncomparable shapes get scan-level tests as well as the pure ones in
``tests/unit/tools/test_corpus_drift_uncomparable_anchors.py``, because the
refusal is only half the behaviour. The other half is what the run does with it:
a line-ranged anchor over a document that *has* changed must come back
``UNCHECKABLE`` and leave the run reporting ``NOTHING_COMPARED``, not ``CLEAN``
and not ``DRIFTED``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import corpus_drift
import pytest
import yaml
from corpus_drift import Status, Verdict, scan

pytestmark = pytest.mark.integration

_MIGRATION = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6A-adr-0001-example.yaml"
_BODY = ".theurian/knowledge/architecture/example.01MB4V3XKQ7ZPYE8R2NGT5HW6B.md"
_DOCUMENT = "docs/adr/0001-example.md"
_ITEM = "architecture.example"

_SNAPSHOT = "# ADR-0001: Example\n\nThe text as it stood when the snapshot was taken.\n"
_EDITED = _SNAPSHOT + "\n## Consequences\n\nA section added after the snapshot.\n"

_THIS_REPOSITORY = "https://github.com/theurian/theurian.git"
_COMMIT = "2a98d4c8963cdf46cc6169e43ac7add039745342"


def _anchor(**overrides: Any) -> dict[str, Any]:
    return {
        "provider": "git",
        "sourceUri": _THIS_REPOSITORY,
        "commitSha": _COMMIT,
        "filePath": _DOCUMENT,
    } | overrides


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _seed(
    root: Path,
    *,
    document: str | None = _SNAPSHOT,
    pin: bool = True,
    write_body: bool = True,
    anchors: list[Any] | None = None,
) -> str:
    """One migration, its pinned body, and the `docs/` document its anchor names.

    ``document=None`` leaves the source document unwritten -- the shape a
    deleted ADR takes. ``pin=False`` drops ``contentSha256``. The published
    schema requires it since ADR-0027, but this tool reads the tracked YAML
    directly and runs no schema check, so the shape still reaches it -- and it
    answers by re-deriving the digest from the committed body.
    """
    revision: dict[str, Any] = {
        "op": "upsertRevision",
        "itemId": _ITEM,
        "revisionId": "01MB4V3XKQ7ZPYE8R2NGT5HW6B",
        "contentFile": f"../{_BODY.removeprefix('.theurian/')}",
        "metadata": {
            "title": "ADR-0001: Example",
            "contentType": "text/markdown",
            "status": "approved",
            "trustLevel": "reviewed",
            "sensitivity": "public",
            "sourceAnchors": [_anchor()] if anchors is None else anchors,
        },
    }
    if pin:
        revision["contentSha256"] = hashlib.sha256(_SNAPSHOT.encode("utf-8")).hexdigest()

    _write(
        root,
        _MIGRATION,
        yaml.safe_dump(
            {
                "apiVersion": "theurian.dev/v1",
                "id": "01MB4V3XKQ7ZPYE8R2NGT5HW6A",
                "operations": [
                    {"op": "createItem", "itemId": _ITEM, "kind": "architecture"},
                    revision,
                ],
            },
            sort_keys=False,
        ),
    )
    if write_body:
        _write(root, _BODY, _SNAPSHOT)
    if document is not None:
        _write(root, _DOCUMENT, document)
    return _MIGRATION


# -- the comparison itself ---------------------------------------------------


def test_a_document_that_still_matches_its_snapshot_is_reported_matched(tmp_path: Path) -> None:
    """The healthy anchor: `docs/` still hashes to what the corpus pinned.

    Without this the whole file could pass against a tool that reported drift
    unconditionally, which is the mutation the next test cannot catch on its own.
    """
    migration = _seed(tmp_path)

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.CLEAN
    assert [item.verdict for item in report.comparisons] == [Verdict.MATCHED]


def test_a_document_edited_since_the_snapshot_is_reported_drifted(tmp_path: Path) -> None:
    """The finding this tool exists for (#263), and the one that had already happened.

    Measured 2026-08-22 at 64e33da: 24 of 26 anchors matched and two had drifted,
    160 lines added across three merged pull requests since the corpus was seeded.
    Theurian's own agents keep retrieving the older text, still labelled
    `status: approved` and `trustLevel: reviewed`, with an anchor naming a file
    whose current contents say something else.
    """
    migration = _seed(tmp_path, document=_EDITED)

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.DRIFTED
    assert [item.verdict for item in report.comparisons] == [Verdict.DRIFTED]
    assert report.drifted[0].file_path == _DOCUMENT
    assert report.drifted[0].expected == hashlib.sha256(_SNAPSHOT.encode("utf-8")).hexdigest()
    assert report.drifted[0].actual == hashlib.sha256(_EDITED.encode("utf-8")).hexdigest()


def test_the_drift_report_names_the_migration_that_holds_the_stale_snapshot(
    tmp_path: Path,
) -> None:
    """A maintainer fixes the corpus file, so the corpus file has to be in the output.

    The item id alone does not locate it: the migration is named after a ULID,
    and the re-seed happens against that file.
    """
    migration = _seed(tmp_path, document=_EDITED)

    report = scan(tmp_path, tracked=[migration])

    assert report.drifted[0].migration == migration
    assert report.drifted[0].item_id == _ITEM


def test_a_snapshot_of_a_document_this_repository_no_longer_publishes_is_drift(
    tmp_path: Path,
) -> None:
    """Deleting the ADR must not be the way to make its drift warning go away.

    The corpus still serves an `approved`, `reviewed` snapshot of a document
    that no longer exists, which is a worse state than a stale one -- nothing in
    `docs/` contradicts it any more.
    """
    migration = _seed(tmp_path, document=None)

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.DRIFTED
    assert [item.verdict for item in report.comparisons] == [Verdict.SOURCE_MISSING]
    assert "no longer publishes" in report.drifted[0].detail


# -- the pin, and what happens without it ------------------------------------


def test_a_revision_that_records_no_digest_is_held_to_its_committed_body(
    tmp_path: Path,
) -> None:
    """A migration missing its pin still reaches this tool, so it must be answered.

    ADR-0027 made `contentSha256` schema-required, and this tool validates
    nothing: it reads the tracked YAML directly, so a hand-edited migration with
    the pin line deleted arrives here ahead of the `migrate validate` run that
    refuses it. The tool re-derives the digest from the committed body with the
    same call that produced the recorded value, rather than treating the
    revision as uncheckable.
    """
    migration = _seed(tmp_path, pin=False)

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.CLEAN
    assert [item.verdict for item in report.comparisons] == [Verdict.MATCHED]


def test_deleting_the_pinned_digest_does_not_quietly_disable_the_drift_check(
    tmp_path: Path,
) -> None:
    """The load-bearing half of the fallback: drift is still caught without the pin.

    A revision whose `contentSha256` line is gone -- hand-edited, or written by
    a producer that omits it -- must not become an anchor nobody is watching.
    Reporting it as uncheckable would be honest but wrong: the body is right
    there, and it is what the recorded snapshot *is*.
    """
    migration = _seed(tmp_path, pin=False, document=_EDITED)

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.DRIFTED
    assert [item.verdict for item in report.comparisons] == [Verdict.DRIFTED]


def test_a_revision_with_neither_a_digest_nor_a_readable_body_is_uncheckable(
    tmp_path: Path,
) -> None:
    """Nothing was recorded that the current document could be held to.

    The one case the fallback must not paper over: with no pin and no body, any
    verdict at all would be invented.
    """
    migration = _seed(tmp_path, pin=False, write_body=False, document=_EDITED)

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.NOTHING_COMPARED
    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE]
    assert "nothing was recorded" in report.comparisons[0].detail


# -- anchors that cannot honestly be compared --------------------------------


def test_a_line_ranged_anchor_is_named_uncheckable_rather_than_slice_hashed(
    tmp_path: Path,
) -> None:
    """The document has changed, and this anchor still must not report drift.

    `contentSha256` digests the whole body; no per-extent digest is recorded
    anywhere, so a line-ranged anchor has nothing to be held to. Comparing the
    whole file against it would report drift for a change outside the range, and
    hashing the slice would invent a convention the product does not produce.
    """
    migration = _seed(tmp_path, document=_EDITED, anchors=[_anchor(lineStart=1, lineEnd=3)])

    report = scan(tmp_path, tracked=[migration])

    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE]
    assert report.uncheckable[0].file_path == _DOCUMENT
    assert "line range" in report.uncheckable[0].detail


def test_two_comparable_anchors_on_one_revision_are_both_left_uncompared(
    tmp_path: Path,
) -> None:
    """One recorded digest cannot speak for two source files.

    Neither is compared, and both are named: picking the first would assert that
    the second document's contents are irrelevant, and comparing both against
    the single digest would report drift on whichever one is not the body.
    """
    _write(tmp_path, "docs/adr/0002-second.md", "# ADR-0002\n")
    migration = _seed(
        tmp_path,
        anchors=[_anchor(), _anchor(filePath="docs/adr/0002-second.md")],
    )

    report = scan(tmp_path, tracked=[migration])

    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE] * 2
    assert {item.file_path for item in report.uncheckable} == {
        _DOCUMENT,
        "docs/adr/0002-second.md",
    }
    assert all("one digest cannot speak" in item.detail for item in report.uncheckable)


def test_an_anchor_naming_a_path_outside_the_tree_is_not_opened(tmp_path: Path) -> None:
    """A `..` in `filePath` must not be resolved against the root and then read.

    The file it would reach is written here, outside the repository root, so a
    regression that resolved the path would compare against real bytes and
    report `MATCHED` or `DRIFTED` instead of refusing.
    """
    (tmp_path / "outside.md").write_text(_SNAPSHOT, encoding="utf-8")
    root = tmp_path / "repository"
    migration = _seed(root, anchors=[_anchor(filePath="../outside.md")])

    report = scan(root, tracked=[migration])

    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE]
    assert "resolves outside the repository" in report.comparisons[0].detail


def _refuses_without_saying_why(anchor: Any) -> str:
    """Stands in for a future `anchor_refusal` clause that returns an empty reason."""
    return ""


def test_an_anchor_whose_refusal_says_nothing_is_still_named_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An anchor must never leave the run through neither list.

    `_compare` splits the anchors into refused and comparable. When those two
    sides disagreed -- one filtering on truthiness, the other testing `is None`
    -- an empty-string refusal satisfied neither, and the anchor was reported
    nowhere: not compared, not annotated, not counted. That is the one outcome
    this checker states it never produces, and it is silent by construction.

    Unreachable through the shipped clauses, every one of which returns a
    written sentence, so it is driven through the seam instead: a stand-in
    refusal that is falsy but not `None`, which is what the next clause somebody
    adds may well return.

    The assertion is on the named comparison and not on the status, deliberately.
    Before the fix this same corpus also reported `NOTHING_COMPARED` -- from zero
    comparisons rather than from one uncheckable anchor -- so a status assertion
    here would pass against the defect it is meant to catch.
    """
    migration = _seed(tmp_path)
    monkeypatch.setattr(corpus_drift, "anchor_refusal", _refuses_without_saying_why)

    report = scan(tmp_path, tracked=[migration])

    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE]
    assert report.uncheckable[0].file_path == _DOCUMENT
    assert report.uncheckable[0].migration == migration


def test_a_revision_that_declares_no_anchors_names_no_document_to_compare(
    tmp_path: Path,
) -> None:
    """A revision seeded without provenance is reported, not skipped in silence."""
    migration = _seed(tmp_path, anchors=[])

    report = scan(tmp_path, tracked=[migration])

    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE]
    assert "declares no sourceAnchors" in report.comparisons[0].detail


def test_a_migration_whose_yaml_does_not_parse_is_named_rather_than_taking_the_run_down(
    tmp_path: Path,
) -> None:
    """25 healthy migrations must still be compared when the 26th is malformed."""
    healthy = _seed(tmp_path)
    broken = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6C-broken.yaml"
    _write(tmp_path, broken, "operations: [ this is not: valid yaml\n")

    report = scan(tmp_path, tracked=[healthy, broken])

    assert report.status is Status.CLEAN
    assert report.matched[0].migration == healthy
    assert report.uncheckable[0].migration == broken
    assert "unreadable" in report.uncheckable[0].detail


def test_a_migration_that_upserts_no_revision_pins_no_body_to_any_document(
    tmp_path: Path,
) -> None:
    """A rename-only or delete-only migration is not a snapshot of anything."""
    migration = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6D-rename.yaml"
    _write(
        tmp_path,
        migration,
        yaml.safe_dump({"operations": [{"op": "deleteItem", "itemId": _ITEM}]}),
    )

    report = scan(tmp_path, tracked=[migration])

    assert [item.verdict for item in report.comparisons] == [Verdict.UNCHECKABLE]
    assert "declares no upsertRevision" in report.comparisons[0].detail


# -- runs that asserted nothing ----------------------------------------------


def test_a_tree_with_no_tracked_migrations_reports_that_it_compared_nothing(
    tmp_path: Path,
) -> None:
    """The committed corpus being gone is a finding, not a drift-free result."""
    _seed(tmp_path)

    report = scan(tmp_path, tracked=["README.md", "docs/adr/0001-example.md"])

    assert report.status is Status.NOTHING_COMPARED
    assert report.comparisons == ()
    assert "The committed corpus is gone" in report.detail


def test_a_corpus_whose_every_anchor_is_uncheckable_reports_that_it_compared_nothing(
    tmp_path: Path,
) -> None:
    """26 anchors and 0 comparisons must not read the same as 26 healthy anchors.

    This is the shape a schema change produces: every anchor gains a field the
    tool refuses, the run goes quiet, and nothing else in the repository notices.
    """
    migration = _seed(tmp_path, document=_EDITED, anchors=[_anchor(lineStart=1)])

    report = scan(tmp_path, tracked=[migration])

    assert report.status is Status.NOTHING_COMPARED
    assert "proves nothing" in report.detail


def test_a_migration_git_does_not_track_is_not_read_even_though_it_is_on_disk(
    tmp_path: Path,
) -> None:
    """The population is what ships, and a local-only vault note does not ship.

    Measured 2026-08-22 on the maintainer's machine, `.theurian/migrations/`
    held 82 `*.yaml` of which 26 were tracked; the other 56 are fenced in
    `.git/info/exclude`. Here the untracked one is drifted, so a filesystem glob
    anywhere in this path turns the run red -- noisy on the one machine that has
    the corpus and quiet in CI, which is #262's failure mode exactly.
    """
    tracked = _seed(tmp_path)
    untracked = ".theurian/migrations/01MB4V3XKQ7ZPYE8R2NGT5HW6C-vault-note.yaml"
    _write(tmp_path, untracked, (tmp_path / tracked).read_text(encoding="utf-8"))
    _write(tmp_path, _DOCUMENT, _SNAPSHOT)

    report = scan(tmp_path, tracked=[tracked])

    assert report.status is Status.CLEAN
    assert [item.migration for item in report.comparisons] == [tracked]


def test_a_tree_git_cannot_answer_for_is_a_refusal_not_a_filesystem_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No git, and a drifted corpus sitting right there on disk, and still no verdict.

    There is no last-resort glob on purpose: the only tree where the difference
    between "tracked" and "on disk" matters is the tree where the fallback would
    be wrong. A run that cannot establish its own population must say so and
    exit 2, not compare whatever it can find.

    `HOME` and `GIT_CEILING_DIRECTORIES` are redirected so this asks about
    `tmp_path` and never discovers a repository above it -- including the
    developer's own checkout, when a mutation run has put `TMPDIR` inside a copy
    of the tree.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", f"{tmp_path}:{tmp_path.resolve()}")
    root = tmp_path / "not-a-repository"
    _seed(root, document=_EDITED)

    report = scan(root)

    assert report.status is Status.NOTHING_COMPARED
    assert report.comparisons == ()
    assert "no filesystem fallback on purpose" in report.detail
