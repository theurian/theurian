"""Which production file the run attacks, decided by the date alone.

CLAUDE.md promises a standing async red-team sweep over ``main``, of which
single-file mutation runs are one leg. One run can afford one file: a verdict
costs a full suite walk, and the per-run budget is about two hours (issue #378).
So the sweep needs a rule for *which* file, and the rule has to hold two
properties at once.

**Reproducible.** The issue the sweep files names a date and a mutation, and
whoever picks it up has to be able to regenerate that exact mutation from those
two facts. Nothing here may depend on the machine, the clock beyond the date, or
the order a directory walk returned.

**Spread.** Against a *fixed* census, indexing by the *run* number --
``ordinal // 7 % len`` -- walks every file before repeating any of it, which a
hash of the date does not. Consecutive runs advance the index by exactly 1
whatever the census size, for any cadence that lands on one weekday: there is no
gcd condition left to satisfy.

Indexing by the *day* number, as this did, held that only while the sweep ran
daily. Two things break it and the original argument named one.

*The one it named:* the census is recomputed on every run, so its length moves
and the index moves with it -- measured across one week of growth, 0 of 30 dates
resolved to the same file, and a replay across real runs drew repeats well before
the census had been walked (PR #730's review round). That one is not fixable
here.

*The one it missed:* the stride. Consecutive runs advance a day index by the gap
between them, so a weekly cron strides 7, and against a 140-module census the
walk closes over the gcd(7, 140) = 7 coset -- 20 indices reached, the other 120
never (measured in PR #759's round, over 104 consecutive Sundays: 20 distinct
targets under the day index, 87 under the run index). The cadence flip is what
exposed it; the run index above is what removes it.

**What holds is determinism, not coverage.** Given
a census and a date the target is fixed and reproducible; the interval before
every file has been attacked is unbounded while the census churns, which is why
a filed finding carries the commit it ran against and why the section in
``docs/contributing/orchestration.md`` -- where this is the workflow leg, one of
three -- calls it "census-limited mutation sampling, not coverage".

The census deliberately drops ``__init__.py``: in this codebase those are
re-export surfaces, and the three operators in :mod:`sweep_mutations` have
nothing to reach in one. A barren target costs a whole run, so the rotation
also lets the driver advance -- see :func:`rotation`, which hands back the full
census in walk order rather than a single name.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def rotation(files: Sequence[str], on: date) -> tuple[str, ...]:
    """The census re-ordered to start at this run's target.

    The whole census rather than a single name, because a target can be barren --
    a module of constants and dataclasses holds no comparison, no boolean literal
    and no ``and``. The driver walks this sequence until one file yields
    candidates, and because the sequence is a rotation rather than a slice, that
    walk both terminates and can reach every file *in this call*.

    The index is the *run* number, ``ordinal // 7``, not the day number, so
    consecutive scheduled runs advance it by exactly 1 -- see the module
    docstring for the stride this replaces and what it cost. The bucket is a
    fixed 7-day grid, which happens to run Sunday through Saturday (ordinal 7 is
    a Sunday) and is therefore not the ISO week. Dates inside one bucket are
    equivalent, so a ``workflow_dispatch`` aims the rotation a week at a time
    rather than a day at a time.

    That reach is per-call and says nothing across runs: a file this rotation
    could have reached may be at a different index next run, because the census
    it indexes into is recomputed each time. See the module docstring -- what
    this scheme guarantees is determinism given a census and a date, not that
    every file is eventually attacked.
    """
    if not files:
        raise SweepError("an empty census cannot be rotated; there is no file to attack")
    start = (on.toordinal() // 7) % len(files)
    return (*files[start:], *files[:start])
