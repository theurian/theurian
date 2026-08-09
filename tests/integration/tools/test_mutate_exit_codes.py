"""A HUNG verdict must fail the batch (exit 1), not report success (exit 0).

Issue #68: a hung mutation is "the same class of finding as SURVIVED" -- the
suite cannot go RED for it -- so ``_verdict_mode`` treats it the same way for
exit-code purposes. Only ``_execute`` is replaced here (it drives the
expensive isolated-tree build and real ``uv sync``, exercised for real
elsewhere in this suite and, separately, via a manual CLI run against this
worktree). Everything that actually decides the exit code -- the real
checkout-integrity digest comparison, ``_report_checkout``, and the exit-code
expression itself -- runs unmodified against this real checkout.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import mutate
import pytest
from mutate_edits import Mutation

pytestmark = pytest.mark.integration


def _args() -> argparse.Namespace:
    # A real, harmless target: this run never actually mutates it, because
    # `_execute` is replaced below before `_apply` would ever be reached.
    return argparse.Namespace(
        spec=None,
        file="tools/mutate.py",
        old="unused-anchor",
        new="unused-replacement",
        old_file=None,
        new_file=None,
        label="exit-code-check",
    )


def _options() -> mutate.Options:
    return mutate.Options(
        workers=1,
        fail_fast=True,
        control=False,
        timeout=30,
        keep_trees=False,
        json_path=None,
        work_dir=None,
    )


def _fake_execute(outcomes: Sequence[mutate.Outcome]) -> object:
    def _execute(mutations: tuple[Mutation, ...], options: mutate.Options) -> list[mutate.Outcome]:
        del mutations, options
        return list(outcomes)

    return _execute


def test_a_hung_mutation_alone_exits_one_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HUNG alone (no SURVIVED, no ERROR, no control-red) must still exit 1.

    Reverting the exit-code union back to checking only ``"SURVIVED"`` makes
    this fall through to the ``else 0`` branch -- reporting success for a
    mutation the suite never actually got to judge.
    """
    outcome = mutate.Outcome(
        label="hangs", verdict="HUNG", suite_green=None, seconds=1.0, summary="did not finish"
    )
    monkeypatch.setattr(mutate, "_execute", _fake_execute([outcome]))

    exit_code = mutate._verdict_mode(_args(), _options())

    assert exit_code == 1


def test_a_killed_only_batch_still_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the ordinary all-KILLED batch must still exit 0."""
    outcome = mutate.Outcome(
        label="caught", verdict="KILLED", suite_green=False, seconds=1.0, summary="1 failed"
    )
    monkeypatch.setattr(mutate, "_execute", _fake_execute([outcome]))

    exit_code = mutate._verdict_mode(_args(), _options())

    assert exit_code == 0


def test_a_survived_mutation_still_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: ordinary SURVIVED must keep exiting 1 alongside HUNG."""
    outcome = mutate.Outcome(
        label="uncaught", verdict="SURVIVED", suite_green=True, seconds=1.0, summary="5 passed"
    )
    monkeypatch.setattr(mutate, "_execute", _fake_execute([outcome]))

    exit_code = mutate._verdict_mode(_args(), _options())

    assert exit_code == 1
