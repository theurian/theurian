"""What one run may cost, and what it tells the log it may cost (#378).

The walk budget is the sweep's whole cost model: one full suite walk for the
control the block shares, one per mutation handed over. It is what the red-team
workflow's ``timeout-minutes`` is set against, what
``docs/contributing/orchestration.md`` states in words, and the only number in
the run's output that a reader converts into an expectation about how long the
job may run.

Nothing stores it. :func:`sweep.walk_budget` computes it from the block size and
:data:`sweep_mutations.MUTATIONS_PER_FILE`, which is what keeps those three
places from drifting apart -- and is why the arithmetic is pinned here rather
than the figure.

Unit rather than through ``main``: ``block`` always returns exactly ``size``
slots and ``for_block`` one attempt per slot, so a run whose realised block is
shorter than its configured one is not reachable from the CLI today. That is
precisely the divergence the printed ceiling has to survive, so the only way to
measure it is to call the private renderer with the two numbers apart.
"""

from __future__ import annotations

import pytest
import sweep
import sweep_census
import sweep_mutations

pytestmark = pytest.mark.unit


def _attempt(index: int) -> sweep_mutations.Attempt:
    """One block slot carrying one mutation, which is what costs a walk."""
    path = f"packages/theurian-core/src/theurian/m{index:02d}.py"
    candidate = sweep_mutations.Candidate(
        label=f"sweep-2026-09-16-aaaaa{index}-00-le-to-lt-l42",
        path=path,
        old="        if used <= budget:",
        new="        if used < budget:",
        line=42,
        swapped_from="<=",
        swapped_to="<",
        anchor="line",
    )
    return sweep_mutations.Attempt(
        slot=sweep_census.Slot(path=path, index=index, lap=4466),
        generated=sweep_mutations.Generated(path=path, candidates=(candidate,), skipped=()),
        candidate=candidate,
    )


@pytest.mark.parametrize(
    ("block_size", "walks"), ((1, 2), (6, 7), (142, 143)), ids=("one", "shipped", "whole-census")
)
def test_a_run_costs_one_shared_control_plus_one_walk_per_block_member(
    block_size: int, walks: int
) -> None:
    """The arithmetic the ceiling, the prose and the timeout all read from.

    Six mutations of one module and one mutation each of six modules cost the
    same seven walks -- the control is per *spec*, not per file, and that is the
    whole reason the block form was affordable. A budget that charged a control
    per module would be 12 walks at the shipped size and would not fit the job's
    165 minutes.

    Pinned at three sizes including the shipped one, and as literals: a rule that
    recomputed ``1 + size * MUTATIONS_PER_FILE`` would agree with the function
    whatever either of them came to say. Three sizes rather than one because a
    frozen literal passes at whichever size it was frozen from -- the shipped 7
    is exactly the value a hard-coded return would carry.
    """
    assert sweep.walk_budget(block_size) == walks


def test_the_printed_ceiling_is_the_configured_block_size_and_not_the_realised_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bound derived from the thing it bounds is not a bound.

    ``walk_budget(len(attempts))`` re-derives the ceiling from the block that was
    actually built, so a run that built a short block prints its own shortfall as
    the budget: two members become "of at most 3", the line reads as compliant,
    and the one number in the log that would have said "this run asked less than
    it was configured to" says nothing. The configured size is the only honest
    source, because it is the number the workflow chose.

    Both strings are asserted -- the ceiling that must be there and the one that
    must not -- because the realised figure is what a reader would see and accept
    without a second thought.
    """
    sweep._describe((_attempt(4), _attempt(5)), 6)

    printed = capsys.readouterr().out

    assert "budget    3 walk(s) of at most 7" in printed
    assert "of at most 3" not in printed


def test_a_barren_member_lowers_what_the_run_spends_and_not_what_it_may_spend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two halves of the line move independently, which is the point of it.

    A barren slot yields no mutation and buys no walk, and its walk is not handed
    to another file's second question -- so the spend falls while the ceiling
    stays where the block size put it. A line that moved both together would
    report every run as having spent its whole budget, and the one signal that
    distinguishes a six-module run from a six-module run that found four barren
    members would be gone.
    """
    barren = sweep_mutations.Attempt(
        slot=sweep_census.Slot(
            path="packages/theurian-core/src/theurian/constants.py", index=6, lap=4466
        ),
        generated=sweep_mutations.Generated(
            path="packages/theurian-core/src/theurian/constants.py", candidates=(), skipped=()
        ),
        candidate=None,
    )

    sweep._describe((_attempt(4), barren), 6)

    printed = capsys.readouterr().out

    assert "budget    2 walk(s) of at most 7" in printed
    assert "barren" in printed
