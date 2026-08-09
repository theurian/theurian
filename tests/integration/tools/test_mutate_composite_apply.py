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
