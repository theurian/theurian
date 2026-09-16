"""The nightly sweep must attack the same file twice given the same date (#378).

A sweep that picks its target by anything but the date cannot be reproduced. The
finding it files names a mutation; whoever reads that issue has to be able to
regenerate exactly that mutation from the date in the title, without the sweep's
machine, its process memory or its ordering of a directory walk. Every rule here
exists so that "re-run the sweep for 2026-09-16" is a complete instruction.

The rotation rules are pinned against a *synthetic* census, so they stay claims
about the scheme rather than about whatever ``packages/theurian-core/src`` holds
today; the real census is then checked separately for the properties the scheme
assumes of it (non-empty, sorted, no ``__init__.py``).
"""

from __future__ import annotations

from datetime import date

import pytest
import sweep_census

pytestmark = pytest.mark.unit

#: A census small enough to enumerate by hand, so every expectation below is a
#: pinned value rather than a restatement of the implementation.
_SYNTHETIC = ("a.py", "b.py", "c.py", "d.py", "e.py")


def test_the_target_is_the_ordinal_of_the_date_modulo_the_census() -> None:
    """The recorded key, pinned at three worst-case positions, not one.

    A single favourable example would pass for any date-keyed scheme, including
    one that is off by one at the wrap. The three dates below were chosen by
    their *remainder*: one lands on index 0 (the wrap boundary), one on the last
    index (the other wrap boundary), and one in the middle. An implementation
    that rotated by ``ordinal % len - 1``, or that sorted the census differently,
    fails at least one of them.
    """
    remainders = {date.fromordinal(739870 + offset): (739870 + offset) % 5 for offset in range(5)}
    at_zero = next(day for day, index in remainders.items() if index == 0)
    at_last = next(day for day, index in remainders.items() if index == 4)
    in_between = next(day for day, index in remainders.items() if index == 2)

    assert sweep_census.rotation(_SYNTHETIC, at_zero)[0] == "a.py"
    assert sweep_census.rotation(_SYNTHETIC, at_last)[0] == "e.py"
    assert sweep_census.rotation(_SYNTHETIC, in_between)[0] == "c.py"


def test_the_rotation_offers_every_file_exactly_once() -> None:
    """Advancing to the next file has to terminate, and has to cover the census.

    The driver walks this sequence when the keyed file yields no mutation
    candidates. A sequence that repeated a file would re-parse it for nothing;
    one that dropped a file would make part of the census permanently
    unreachable on a night when its predecessor was barren. Starting at the last
    index is the case that distinguishes a wrapping rotation from a truncating
    slice, so it is the one asserted.
    """
    at_last = date.fromordinal(739874)

    walked = sweep_census.rotation(_SYNTHETIC, at_last)

    assert walked == ("e.py", "a.py", "b.py", "c.py", "d.py")
    assert sorted(walked) == sorted(_SYNTHETIC)


def test_consecutive_nights_walk_the_whole_census_before_repeating() -> None:
    """Rotation, not sampling: no file waits longer than the census is long.

    ``ordinal % len`` is what buys this, and nothing weaker does -- a date hashed
    into the range would revisit some files twice in a cycle and skip others
    entirely, which is invisible on any single night. Asserted over a full cycle
    because that is the only window in which the difference shows.
    """
    start = date.fromordinal(739870)

    first_of_each = [
        sweep_census.rotation(_SYNTHETIC, date.fromordinal(start.toordinal() + night))[0]
        for night in range(len(_SYNTHETIC))
    ]

    assert sorted(first_of_each) == sorted(_SYNTHETIC)


def test_a_census_the_sweep_cannot_rotate_is_refused_rather_than_guessed() -> None:
    """An empty census means the walk found nothing; ``% 0`` would raise anyway.

    Refusing by name matters because the driver turns this into exit 1 -- "the
    sweep failed to run" -- and a sweep that quietly attacked nothing would
    otherwise report success.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_census.rotation((), date.fromordinal(739870))


def test_the_real_census_is_the_sorted_production_tree_without_package_markers() -> None:
    """The population the rotation indexes into, checked against the repository.

    Three claims, because the index is meaningless without all three: the census
    is non-empty (an empty one makes every night exit 1), it is sorted (an
    unsorted one makes the index depend on the order a directory walk happened to
    return), and it excludes ``__init__.py`` (re-exports carry no branch a
    mutation operator can reach, so an included one costs a whole night).
    """
    files = sweep_census.census()

    assert files
    assert list(files) == sorted(files)
    assert not [path for path in files if path.endswith("/__init__.py")]
    assert all(path.startswith(f"{sweep_census.CENSUS_ROOT}/") for path in files)
    assert all(path.endswith(".py") for path in files)


def test_the_real_census_reads_the_same_twice() -> None:
    """The byte-identity half of AC1, over the input the rotation consumes.

    ``Path.rglob`` returns entries in whatever order the filesystem hands back.
    If any of that ordering survived into the census, two runs on one machine
    could agree while two machines disagreed -- so the assertion is on the tuple,
    which is also what the rotation indexes.
    """
    assert sweep_census.census() == sweep_census.census()
