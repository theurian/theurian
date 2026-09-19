"""Which production files the run attacks, decided by the date alone.

CLAUDE.md promises a standing async red-team sweep over ``main``, of which
single-file mutation runs are one leg. A run can afford seven full suite walks
against the workflow's 165-minute ceiling, and the harness spends one of them on
its own unmutated control, so the run buys six mutations (issue #378). Spending
them as one mutation each on six *files* rather than six on one file is what
:data:`BLOCK_SIZE` records. So the sweep needs a rule for *which* files, and the
rule has to hold two properties at once.

**Reproducible.** The issue the sweep files names a date and a mutation, and
whoever picks it up has to be able to regenerate that exact mutation from those
two facts. Nothing here may depend on the machine, the clock beyond the date, or
the order a directory walk returned.

**Spread.** Against a *fixed* census, indexing by the *run* number and consuming
a contiguous **block** -- run ``r`` takes positions ``[r·N, r·N + N)`` mod
``len`` -- covers every index in ``ceil(len / N)`` consecutive runs, which a hash
of the date does not. The cover needs no coprimality condition and holds for any
census size and any block size, because ``ceil(len / N)`` consecutive blocks are
``ceil(len / N) · N >= len`` consecutive positions on a circle of circumference
``len``, and that cannot miss one.

What a cover does **not** promise is *exactly* once. The last block wraps past
the start whenever ``N`` does not divide ``len``, and the surplus is
``ceil(len / N) · N - len`` positions. Measured 2026-09-19 through
:func:`block`, when the census held 142 modules and ``N`` was 6: the surplus is
144 - 142 = 2, so 24 consecutive runs covered 142 of 142 indices with indices 10
and 11 drawn twice, and 200 windows at random offsets each covered all 142.
Every count in that sentence is that day's census; the arithmetic around it is
not. Coverage is the claim; a uniform visit count is not.

Indexing by the *day* number, as this did, held that only while the sweep ran
daily. Two things break it and the original argument named one.

*The one it named:* the census is recomputed on every run, so its length moves
and the index moves with it -- measured across one week of growth, 0 of 30 dates
resolved to the same file, and a replay across real runs drew repeats well before
the census had been walked (PR #730's review round). Those were 30 draws because
the scheme being measured was the day-indexed one; the same sentence counted
against the run index would be 5, which is the mistake
:data:`sweep_filing._DRIFT_MECHANISM` records having made. That one is not
fixable here.

*The one it missed:* the stride. Consecutive runs advance a day index by the gap
between them, so a weekly cron strides 7 and the walk closes over the
``gcd(7, len)`` coset, reaching ``len / gcd`` indices and starving the rest.

**How bad that is depends on the census size, which is why the figure carries
one.** At the 140-module census of PR #759's round, ``gcd(7, 140)`` = 7: 20
indices reached and the other 120 never, confirmed over 104 consecutive Sundays
(20 distinct targets under the day index, 87 under the run index). At the 142
modules measured 2026-09-19, ``gcd(7, 142)`` = 1 and the same day index would
have starved nothing at all.

Read the example against its own census, because the defect is visible at almost
none of them: a weekly day index starves **only** where the census is a multiple
of 7, and 140 is the sole such size between 138 and 144 -- the next is 147. One
module added or removed would have hidden it entirely. The run index strides 1
by construction, so no ``gcd`` enters it at any census size; that, and not the
20, is why it is the fix.

The block form removes the last of it. A single-index run covered the census in
``len`` runs -- one week per module, which at any census this repository has had
is not a schedule anybody waits out. A block of six covers it in
``ceil(len / 6)``: 24 runs at the 142 modules of 2026-09-19.

**What holds is determinism, not coverage.** Given
a census and a date the target is fixed and reproducible; the interval before
every file has been attacked is unbounded while the census churns, which is why
a filed finding carries the commit it ran against and why the section in
``docs/contributing/orchestration.md`` -- where this is the workflow leg, one of
three -- calls it "census-limited mutation sampling, not coverage".

The census deliberately drops ``__init__.py``: in this codebase those are
re-export surfaces, and the three operators in :mod:`sweep_mutations` have
nothing to reach in one. A barren *block member* is no longer the problem it was
when a run attacked one file: it yields no candidate, so it buys no walk and
costs nothing but its slot -- see :func:`block`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

#: The production tree, repository-relative. Tests, fixtures and tooling are
#: deliberately outside it: a surviving mutation in a test file says nothing
#: about what the product holds.
CENSUS_ROOT: Final = "packages/theurian-core/src/theurian"


class SweepError(RuntimeError):
    """The sweep cannot run, as distinct from the sweep finding something.

    The driver turns this into exit 1. The distinction is the whole point of the
    exit codes: findings are a *successful* sweep (exit 0, an issue filed), and
    only a sweep that could not do its job is a failure the workflow alarms on.
    """


def census(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Every production file, repository-relative and sorted.

    Sorted on the POSIX string rather than on ``Path``, so the index the rotation
    computes means the same thing on every machine. ``rglob`` alone does not:
    it returns entries in whatever order the filesystem hands back.
    """
    root = repo_root / CENSUS_ROOT
    if not root.is_dir():
        raise SweepError(f"no production tree at {root}; the sweep has nothing to attack")
    files = tuple(
        sorted(
            found.relative_to(repo_root).as_posix()
            for found in root.rglob("*.py")
            if found.is_file() and found.name != "__init__.py"
        )
    )
    if not files:
        raise SweepError(f"{root} holds no production module outside __init__.py")
    return files


def run_index(on: date) -> int:
    """Which scheduled run this date belongs to.

    ``ordinal // 7`` and not the ordinal itself: a cadence firing once a week
    advances this by exactly 1 per run, which is what makes consecutive runs
    consume consecutive blocks whatever the census size. The bucket is a fixed
    7-day grid running Sunday through Saturday (ordinal 7 is a Sunday) and is
    therefore **not** the ISO week; every date inside one bucket is the same run,
    so a ``workflow_dispatch`` aims the sweep a week at a time rather than a day
    at a time, and a rerun inside the same week re-draws the same block.
    """
    return on.toordinal() // 7


def _position(on: date, offset: int, size: int) -> int:
    """Where on the endless walk this run's ``offset``-th slot falls.

    The one place the scheme is written down. :func:`rotation` and :func:`block`
    both read it, so the ordering a reader is shown and the slots a run actually
    consumes cannot drift apart. Unbounded on purpose: the census length turns a
    position into an index (``% len``) and into a visit count (``// len``), and
    keeping the position itself is what makes those two agree.
    """
    return run_index(on) * size + offset


def _start(length: int, on: date, size: int) -> int:
    """The census index this run's block opens at."""
    return _position(on, 0, size) % length


@dataclass(frozen=True)
class Slot:
    """One census index a run consumes, and which visit to it this is."""

    path: str
    #: Position in the sorted census, which is what the rotation indexes.
    index: int
    #: How many times the walk has wrapped past this index before now.
    #:
    #: The *visit* number, and it increments by exactly one between consecutive
    #: visits to the same index -- the walk reaches position ``index + k·len`` on
    #: its ``k``-th lap, whatever the block size and whatever the census size.
    #: :meth:`sweep_mutations.Generated.at_lap` indexes a file's candidates by
    #: it, so a file offering more than one candidate is asked a different
    #: question on its next visit -- measured there, with the single-candidate
    #: files that are the exception.
    lap: int


#: Census indices one run consumes.
#:
#: Six, because a run's budget is *walks* and not files. The harness runs one
#: unmutated control for the whole spec and one walk per mutation, so six files
#: at one mutation each is ``1 + 6`` = 7 walks -- the same seven the single-file
#: form spent on six mutations of one module, against the same ceiling. What
#: changed is the reach: six modules per run instead of one, and a census cover
#: in ``ceil(len / 6)`` runs instead of ``len`` -- 24 instead of 142 at the
#: census of 2026-09-19. ``tools/sweep.py``'s Budget section derives the
#: seven; :data:`sweep_mutations.MUTATIONS_PER_FILE` holds the other factor.
BLOCK_SIZE: Final = 6


def rotation(files: Sequence[str], on: date, *, size: int = BLOCK_SIZE) -> tuple[str, ...]:
    """The census re-ordered to open at this run's block.

    The whole census rather than the block alone, so a reader can see what comes
    next and :func:`block` has one ordering to take its head from. The start is
    ``(run · size) % len`` -- see the module docstring for the cover this buys and
    for the day-indexed stride it replaces.

    This ordering is per-call and says nothing across runs: a file at one index
    here may be at another next run, because the census it indexes into is
    recomputed each time. What the scheme guarantees is determinism given a
    census and a date, not that every file is eventually attacked.
    """
    if not files:
        raise SweepError("an empty census cannot be rotated; there is no file to attack")
    start = _start(len(files), on, size)
    return (*files[start:], *files[:start])


def block(files: Sequence[str], on: date, *, size: int = BLOCK_SIZE) -> tuple[Slot, ...]:
    """The ``size`` census slots this run consumes, in order.

    The head of :func:`rotation`, carrying the two numbers the rotation drops:
    which census index each file sits at, and which visit this is. A run hands
    one mutation per slot to the harness, so this is also the run's cost model --
    at most ``size`` mutation walks, fewer when a slot is barren.

    Wrapping is ordinary, not an edge case: the block that runs off the end of
    the census continues at the start, which is what makes consecutive covers
    seamless rather than re-aligned.
    """
    if not files:
        raise SweepError("an empty census cannot be blocked; there is no file to attack")
    if size <= 0:
        raise SweepError(f"a block of {size} consumes nothing; the sweep would report a clean run")
    length = len(files)
    slots: list[Slot] = []
    for offset in range(size):
        position = _position(on, offset, size)
        index = position % length
        slots.append(Slot(path=files[index], index=index, lap=position // length))
    return tuple(slots)
