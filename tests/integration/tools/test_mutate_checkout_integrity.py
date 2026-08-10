"""``_report_checkout``'s own-target-moved detection is the only thing gating exit 2.

tools/mutate.py's own module docstring: "Exit 2 means one of [this run's own
mutation targets] moved, which is either a mis-specified path that wrote to
the live tree or a source file that changed underneath a batch whose trees
were copied at different moments. Both make every verdict in the batch
worthless, and nothing else does." ``_report_checkout``'s ``return
bool(touched)`` is the boolean ``_verdict_mode`` turns directly into exit 2
-- and no test had ever exercised it returning ``True``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import mutate_checkout
import pytest
from mutate_edits import Mutation, _digest_targets

pytestmark = pytest.mark.integration


@pytest.fixture
def scratch_target_in_repo_root() -> Iterator[Path]:
    """A throwaway file inside REPO_ROOT.

    ``_digest_targets``/``_report_checkout`` always read the real checkout,
    never a copy, so there is no way to point them at ``tmp_path`` --
    unlike ``_apply``, whose isolated-tree parameter makes that possible
    everywhere else in this suite. Cleaned up unconditionally.
    """
    target = mutate_checkout.REPO_ROOT / "tools" / "_medium4_scratch_target.txt"
    target.write_text("original\n", encoding="utf-8")
    try:
        yield target
    finally:
        target.unlink(missing_ok=True)


def test_report_checkout_returns_true_when_the_mutations_own_target_moved(
    scratch_target_in_repo_root: Path,
) -> None:
    """The one condition the docstring says must gate exit 2 must actually gate it.

    Simulates the hazard by name: this run's own mutation target changed in
    the real checkout underneath it -- not through ``_apply``, which only
    ever writes inside an isolated copy, so this is the only way to
    reproduce the condition ``_report_checkout`` exists to catch.
    """
    relative = str(scratch_target_in_repo_root.relative_to(mutate_checkout.REPO_ROOT))
    mutation = Mutation(label="self-target-moved", path=relative, old="original", new="mutated")
    before_targets = _digest_targets((mutation,))
    before_status = mutate_checkout._porcelain_entries()

    scratch_target_in_repo_root.write_text("moved-underneath\n", encoding="utf-8")

    touched = mutate_checkout._report_checkout((mutation,), before_targets, before_status)

    assert touched is True


def test_report_checkout_returns_false_when_nothing_moved(
    scratch_target_in_repo_root: Path,
) -> None:
    """Regression guard: an untouched target must not gate exit 2."""
    relative = str(scratch_target_in_repo_root.relative_to(mutate_checkout.REPO_ROOT))
    mutation = Mutation(label="self-target-untouched", path=relative, old="original", new="mutated")
    before_targets = _digest_targets((mutation,))
    before_status = mutate_checkout._porcelain_entries()

    touched = mutate_checkout._report_checkout((mutation,), before_targets, before_status)

    assert touched is False
