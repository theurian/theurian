"""Composite edits land atomically, restore in reverse, and stay in scope.

Issue #68: "does this guard still catch the defect once the walker is
weakened" cannot be asked as one edit -- two separate labels each get killed
by the other's absence. The composite-edit patch lets one ``Mutation`` carry
several ``Edit``s that land together, unwind together on a mid-apply failure,
and restore in reverse order. All of it happens on real files in a real
temporary directory; nothing here is mocked.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import mutate_edits
import pytest

pytestmark = pytest.mark.integration


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_composite_mutation_lands_every_edit_in_one_call(tmp_path: Path) -> None:
    """Two files, one ``Mutation``, one call to ``_apply``: both must land.

    Why it matters: a hypothesis needing two simultaneous changes is only
    tested if both are actually on disk together before the suite runs.
    """
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("AAA\n", encoding="utf-8")
    file_b.write_text("BBB\n", encoding="utf-8")
    mutation = mutate_edits.Mutation(
        label="two-file",
        path="a.py",
        old="AAA",
        new="XXX",
        also=(mutate_edits.Edit(path="b.py", old="BBB", new="YYY"),),
    )

    landed = mutate_edits._apply(tmp_path, mutation)

    assert file_a.read_text(encoding="utf-8") == "XXX\n"
    assert file_b.read_text(encoding="utf-8") == "YYY\n"
    assert len(landed) == 2


def test_a_failing_second_edit_rolls_back_the_first_that_already_landed(
    tmp_path: Path,
) -> None:
    """A composite that fails halfway must not leave a partially mutated tree.

    Why it matters (module docstring, composite-edits patch): "a composite
    that fails halfway would otherwise leave a partially mutated tree for the
    next job that borrows it, and the next job's verdict would be about a
    source nobody described." The second edit's anchor does not exist here,
    so ``_apply`` must raise *and* put the first file back exactly as it was.
    """
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("AAA\n", encoding="utf-8")
    file_b.write_text("BBB\n", encoding="utf-8")
    original_a = file_a.read_text(encoding="utf-8")
    mutation = mutate_edits.Mutation(
        label="second-edit-fails",
        path="a.py",
        old="AAA",
        new="XXX",
        # "ZZZ" does not occur in b.py: the anchor check inside _apply_edit
        # must reject this before any verdict is ever asked for.
        also=(mutate_edits.Edit(path="b.py", old="ZZZ", new="YYY"),),
    )

    with pytest.raises(mutate_edits.HarnessError):
        mutate_edits._apply(tmp_path, mutation)

    assert file_a.read_text(encoding="utf-8") == original_a
    assert file_b.read_text(encoding="utf-8") == "BBB\n"


def test_restoring_two_edits_in_one_file_reverses_the_apply_order(tmp_path: Path) -> None:
    """Two edits to the same file must restore to the true original.

    Why it matters (``_restore_all`` docstring): "two edits to one file would
    otherwise restore the earlier snapshot last and silently keep the later
    edit." Forward-order restore would leave the first edit's change in
    place; only reverse order recovers the pristine file.
    """
    target = tmp_path / "file.py"
    original = "AAA\nBBB\nCCC\n"
    target.write_text(original, encoding="utf-8")
    mutation = mutate_edits.Mutation(
        label="two-edits-one-file",
        path="file.py",
        old="AAA",
        new="XXX",
        also=(mutate_edits.Edit(path="file.py", old="BBB", new="YYY"),),
    )
    landed = mutate_edits._apply(tmp_path, mutation)
    assert target.read_text(encoding="utf-8") == "XXX\nYYY\nCCC\n"

    mutate_edits._restore_all(landed, tmp_path)

    assert target.read_text(encoding="utf-8") == original


def test_the_reported_mutated_digest_for_the_last_edit_matches_the_tree_at_apply_time(
    tmp_path: Path,
) -> None:
    """HIGH-1: the digest keyed ``:mutated`` must be the state the suite ran.

    Basename-only keys used to make two edits to one file collide: the
    reported ``:mutated`` digest was silently overwritten and ended up being
    edit 1's hash, not edit 2's -- a state the suite never actually ran
    against. Nothing held this; replacing the key with a constant left every
    existing test green.
    """
    target = tmp_path / "file.py"
    target.write_text("AAA\nBBB\nCCC\n", encoding="utf-8")
    mutation = mutate_edits.Mutation(
        label="two-edits",
        path="file.py",
        old="AAA",
        new="XXX",
        also=(mutate_edits.Edit(path="file.py", old="BBB", new="YYY"),),
    )

    landed = mutate_edits._apply(tmp_path, mutation)
    tree_digest_at_apply_time = _sha256(target)

    digests = mutate_edits._restore_all(landed, tmp_path)

    assert digests["file.py#1:mutated"] == tree_digest_at_apply_time
    assert len([key for key in digests if key.startswith("file.py")]) == 6


def test_two_files_with_the_same_basename_both_get_their_own_digest_entries(
    tmp_path: Path,
) -> None:
    """HIGH-1 face B: a shared basename in different directories must not collide.

    ``pkg_a/ranking.py`` and ``pkg_b/ranking.py`` used to report under the
    same ``ranking.py:*`` keys, so the second file's trail silently
    overwrote the first's.
    """
    (tmp_path / "pkg_a").mkdir()
    (tmp_path / "pkg_b").mkdir()
    file_a = tmp_path / "pkg_a" / "ranking.py"
    file_b = tmp_path / "pkg_b" / "ranking.py"
    file_a.write_text("A = 1\n", encoding="utf-8")
    file_b.write_text("B = 1\n", encoding="utf-8")
    mutation = mutate_edits.Mutation(
        label="same-basename",
        path="pkg_a/ranking.py",
        old="A = 1",
        new="A = 2",
        also=(mutate_edits.Edit(path="pkg_b/ranking.py", old="B = 1", new="B = 2"),),
    )

    landed = mutate_edits._apply(tmp_path, mutation)
    digests = mutate_edits._restore_all(landed, tmp_path)

    assert len([key for key in digests if key.startswith("pkg_a/ranking.py")]) == 3
    assert len([key for key in digests if key.startswith("pkg_b/ranking.py")]) == 3


def test_a_composite_edit_against_a_non_utf8_file_rolls_back_the_first_edit(
    tmp_path: Path,
) -> None:
    """HIGH-2 face 2: a non-UTF-8 second file must raise HarnessError, not crash.

    Before the fix, ``target.read_text(encoding="utf-8")`` on a binary file
    raised a bare ``UnicodeDecodeError`` -- not a ``HarnessError`` -- so
    ``_apply``'s rollback (``except HarnessError`` only) never triggered and
    the first edit's file was left mutated.
    """
    file_a = tmp_path / "a.py"
    file_a.write_text("AAA\n", encoding="utf-8")
    original_a = file_a.read_text(encoding="utf-8")
    binary = tmp_path / "asset.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01not valid utf-8")
    mutation = mutate_edits.Mutation(
        label="hits-binary",
        path="a.py",
        old="AAA",
        new="XXX",
        also=(mutate_edits.Edit(path="asset.bin", old="anything", new="else"),),
    )

    with pytest.raises(mutate_edits.HarnessError):
        mutate_edits._apply(tmp_path, mutation)

    assert file_a.read_text(encoding="utf-8") == original_a


def test_a_restore_failure_during_rollback_does_not_mask_the_original_cause_or_block_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HIGH-2 face 3: a failing restore must not hide why ``_apply`` failed.

    Three edits: the first two land, the third fails on a genuine anchor
    mismatch (the ORIGINAL cause). Rollback then tries to restore the second
    edit first (reverse order) and that restore is made to fail here. The
    original anchor-mismatch error must still be what propagates, and the
    first edit must still be restored despite the second edit's restore
    failing.
    """
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("AAA\n", encoding="utf-8")
    file_b.write_text("BBB\n", encoding="utf-8")
    original_a = file_a.read_text(encoding="utf-8")
    real_restore = mutate_edits._restore

    def _flaky_restore(applied: mutate_edits.Applied, tree: Path) -> str:
        if applied.target.name == "b.py":
            raise mutate_edits.HarnessError("simulated restore failure for b.py")
        return real_restore(applied, tree)

    monkeypatch.setattr(mutate_edits, "_restore", _flaky_restore)
    mutation = mutate_edits.Mutation(
        label="third-edit-fails",
        path="a.py",
        old="AAA",
        new="XXX",
        also=(
            mutate_edits.Edit(path="b.py", old="BBB", new="YYY"),
            # "ZZZ" does not occur in b.py (now "YYY\n"): the ORIGINAL cause.
            mutate_edits.Edit(path="b.py", old="ZZZ", new="QQQ"),
        ),
    )

    with pytest.raises(mutate_edits.HarnessError, match="anchor matched 0 times"):
        mutate_edits._apply(tmp_path, mutation)

    # a.py is restored *after* b.py's restore fails (reverse order): proof
    # that the loop did not stop when the earlier restore raised.
    assert file_a.read_text(encoding="utf-8") == original_a


def test_a_non_harnesserror_cause_still_rolls_back_the_edits_that_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM-2: rollback must trigger for causes ``_apply_edit`` never wraps.

    The only cause exercised elsewhere is an anchor mismatch, itself already
    a ``HarnessError`` -- so ``_apply``'s rollback catching ``BaseException``
    is unproven; narrowing it to ``except HarnessError`` would still pass
    every other test. Injects a genuine ``OSError`` from ``Path.write_text``
    on the third edit's target, the same shape as a real disk failure
    mid-composite, and confirms the first two edits still come back.
    """
    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_c = tmp_path / "c.py"
    file_a.write_text("AAA\n", encoding="utf-8")
    file_b.write_text("BBB\n", encoding="utf-8")
    file_c.write_text("CCC\n", encoding="utf-8")
    original_a = file_a.read_text(encoding="utf-8")
    original_b = file_b.read_text(encoding="utf-8")
    real_write_text = Path.write_text

    def _flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self.name == "c.py":
            raise OSError("simulated disk failure writing c.py")
        return real_write_text(self, data, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _flaky_write_text)
    mutation = mutate_edits.Mutation(
        label="third-edit-os-error",
        path="a.py",
        old="AAA",
        new="XXX",
        also=(
            mutate_edits.Edit(path="b.py", old="BBB", new="YYY"),
            mutate_edits.Edit(path="c.py", old="CCC", new="ZZZ"),
        ),
    )

    with pytest.raises(OSError, match="simulated disk failure"):
        mutate_edits._apply(tmp_path, mutation)

    assert file_a.read_text(encoding="utf-8") == original_a
    assert file_b.read_text(encoding="utf-8") == original_b


def test_digest_targets_reports_every_path_a_composite_mutation_touches() -> None:
    """The integrity check watches every file a composite mutation names.

    Why it matters: ``_report_checkout`` builds its in-scope set from
    ``mutation.paths``, via ``_digest_targets``. A composite mutation whose
    second file were missing from the digested set would let that file's
    real-checkout movement go unwatched. Uses two real, unmodified files from
    this checkout -- ``_digest_targets`` always reads the real checkout, never
    a copy, so there is no isolated-tree substitute for this one.
    """
    repo_root = mutate_edits.REPO_ROOT
    target_a = repo_root / "tools" / "mutate.py"
    target_b = repo_root / "tools" / "mutate_edits.py"
    mutation = mutate_edits.Mutation(
        label="digest-composite",
        path="tools/mutate.py",
        old="unused",
        new="unused",
        also=(mutate_edits.Edit(path="tools/mutate_edits.py", old="unused", new="unused"),),
    )

    digests = mutate_edits._digest_targets((mutation,))

    assert digests["tools/mutate.py"] == _sha256(target_a)
    assert digests["tools/mutate_edits.py"] == _sha256(target_b)


def test_restore_raises_when_the_written_content_does_not_match_the_recorded_digest(
    tmp_path: Path,
) -> None:
    """MEDIUM-4: ``_restore``'s "byte for byte" verify must actually fire.

    Every other test's restore happens to succeed, so the ``if after !=
    applied.before`` check inside ``_restore`` -- the thing the module
    docstring calls proof that "the restore put it back byte for byte" --
    has never been exercised failing. Constructs an ``Applied`` whose
    recorded ``before`` digest does not match what ``original`` actually
    hashes to, and confirms ``_restore`` refuses to report success.
    """
    target = tmp_path / "file.py"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    applied = mutate_edits.Applied(
        target=target,
        original="ORIGINAL\n",
        before="0" * 64,  # deliberately wrong: not sha256("ORIGINAL\n")
        mutated="1" * 64,
    )

    with pytest.raises(mutate_edits.HarnessError, match="restore failed"):
        mutate_edits._restore(applied, tmp_path)


def test_apply_edit_raises_when_old_and_new_are_identical(tmp_path: Path) -> None:
    """MEDIUM-4: ``_apply_edit``'s no-op-mutation detection must actually fire.

    The module docstring's whole case for hashing before and after is that a
    same-length or otherwise no-op write can look like it landed when it did
    not -- and every other test's edit genuinely changes the file, so the
    ``if mutated == before`` check has never been exercised failing. An edit
    whose ``old`` and ``new`` are identical writes back byte-identical
    content: a real, unmocked no-op, not a simulated one.
    """
    target = tmp_path / "file.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    edit = mutate_edits.Edit(path="file.py", old="VALUE = 1", new="VALUE = 1")

    with pytest.raises(mutate_edits.HarnessError, match="unchanged"):
        mutate_edits._apply_edit(tmp_path, "test-label", edit)
