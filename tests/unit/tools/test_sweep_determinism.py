"""The nightly sweep must attack the same file twice given the same date (#378).

A sweep that picks its target by anything but the date cannot be reproduced. The
finding it files names a mutation; whoever reads that issue has to be able to
regenerate exactly that mutation from the date in the title, without the sweep's
machine, its process memory or its ordering of a directory walk. Every rule here
exists so that "re-run the sweep for 2026-09-16" is a complete instruction.

The rotation and advance rules are pinned against a *synthetic* census, so they
stay claims about the scheme rather than about whatever
``packages/theurian-core/src`` holds today; the real census is then checked
separately for the properties the scheme assumes of it (non-empty, sorted, no
``__init__.py``).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import sweep_census
import sweep_mutations

pytestmark = pytest.mark.unit

#: A census small enough to enumerate by hand, so every expectation below is a
#: pinned value rather than a restatement of the implementation.
_SYNTHETIC = ("a.py", "b.py", "c.py", "d.py", "e.py")

#: What each synthetic file holds, so that "barren" is a property of the source
#: rather than of a stub: only ``b.py`` and ``c.py`` carry an operator family the
#: generator can reach, and the rest have the shape real barren modules have --
#: a column tuple, SQL text, a table name.
_SYNTHETIC_SOURCES = {
    "a.py": "COLUMNS = ('id', 'body')\n",
    "b.py": "def fits(size: int) -> bool:\n    return size < 3\n",
    "c.py": "DEFAULT = True\n",
    "d.py": "QUERY = 'SELECT id FROM item'\n",
    "e.py": "TABLE = 'item'\n",
}

#: The night the pinned values below were measured on. It reaches a mutation
#: only as the date in the label, so the synthetic cases take it too.
_NIGHT = date(2026, 9, 16)

#: The first night of every pinned window below; the ordinals derive from it.
_FIRST_NIGHT = date(2026, 9, 11)


def test_the_target_is_the_ordinal_of_the_date_modulo_the_census() -> None:
    """The recorded key, pinned at three worst-case positions, not one.

    A single favourable example would pass for any date-keyed scheme, including
    one that is off by one at the wrap. The three dates below were chosen by
    their *remainder*: one lands on index 0 (the wrap boundary), one on the last
    index (the other wrap boundary), and one in the middle. An implementation
    that rotated by ``ordinal % len - 1``, or that sorted the census differently,
    fails at least one of them.
    """
    first = _FIRST_NIGHT.toordinal()
    remainders = {date.fromordinal(first + offset): (first + offset) % 5 for offset in range(5)}
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
    at_last = date.fromordinal(_FIRST_NIGHT.toordinal() + 4)

    walked = sweep_census.rotation(_SYNTHETIC, at_last)

    assert walked == ("e.py", "a.py", "b.py", "c.py", "d.py")
    assert sorted(walked) == sorted(_SYNTHETIC)


def test_consecutive_nights_walk_the_whole_census_before_repeating() -> None:
    """Rotation, not sampling: no file waits longer than the census is long.

    ``ordinal % len`` is what buys this, and nothing weaker does -- a date hashed
    into the range would revisit some files twice in a cycle and skip others
    entirely, which is invisible on any single night. Asserted over a full cycle
    because that is the only window in which the difference shows.

    **The frozen synthetic census is the condition, not a convenience.** This
    property holds only while the census does not change, and the real one is
    recomputed nightly: measured across one week of this repository's growth, 0
    of 30 dates resolved to the same file, and a replay across real nights drew
    repeats well before the census had been walked (measured in PR #730's review
    round). So this pins the *scheme* -- given a fixed population, the index
    walks it before repeating -- and deliberately not a claim about the sweep's
    coverage of the production tree, which :mod:`sweep_census`'s own docstring
    now declines to make.
    """
    start = _FIRST_NIGHT

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
        sweep_census.rotation((), _FIRST_NIGHT)


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


def _source_of(path: str) -> str:
    return Path(sweep_census.REPO_ROOT / path).read_text(encoding="utf-8")


def _pinned_night(night: date = _NIGHT) -> sweep_mutations.Generated:
    """One real night, selected and generated exactly as the driver does it."""
    walk = sweep_census.rotation(sweep_census.census(), night)
    return sweep_mutations.first_productive(walk, _source_of, on=night)


def _landings(census: tuple[str, ...]) -> tuple[str, ...]:
    """The file each possible draw ends up sweeping, in census order.

    Every draw, not a chosen one: consecutive ordinals cover every remainder
    modulo the census length, so the ``len(census)`` nights from ``_FIRST_NIGHT``
    are the whole population of starting positions.
    """
    by_draw = {}
    for offset in range(len(census)):
        night = date.fromordinal(_FIRST_NIGHT.toordinal() + offset)
        walk = sweep_census.rotation(census, night)
        by_draw[walk[0]] = sweep_mutations.first_productive(
            walk, lambda path: _SYNTHETIC_SOURCES[path], on=night
        ).path

    assert set(by_draw) == set(census)

    return tuple(by_draw[path] for path in census)


def test_a_barren_target_advances_to_the_next_productive_file_in_the_rotation() -> None:
    """A draw with nothing to mutate has to become an ordinary night, not a clean one.

    Every census holds modules of constants, SQL text and column tuples, and a
    night that stopped on one would file nothing while proving nothing. The
    advance must land on the *next* productive file in the walk -- wrapping past
    the end of the census when the draw is late in it -- rather than on an
    arbitrary productive one, or the run stops being reproducible from the date.
    Pinned against the synthetic census, so the live tree's size cannot re-index
    the draws this asserts over.
    """
    barren = tuple(
        path
        for path, source in _SYNTHETIC_SOURCES.items()
        if not sweep_mutations.candidates(path, source, on=_NIGHT).candidates
    )

    landings = _landings(_SYNTHETIC)

    assert barren == ("a.py", "d.py", "e.py")
    assert landings == ("b.py", "b.py", "c.py", "b.py", "b.py")


def test_a_census_where_nothing_can_be_mutated_refuses_to_report_a_clean_night() -> None:
    """The silent-stop guard at the generation layer.

    A rotation that walks every file and finds nothing has not swept anything.
    Returning "no candidates" to the driver would let the night exit 0 with no
    issue filed, which is indistinguishable from a sweep that ran and found
    nothing wrong.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_mutations.first_productive(("one.py", "two.py"), lambda _: "VALUE = 3\n", on=_NIGHT)


def test_generating_the_same_night_twice_writes_byte_identical_specs() -> None:
    """The other half of AC1: selection *and* generation reproduce.

    Serialised before comparing, because the spec file is the artefact handed to
    ``tools/mutate.py`` and a JSON document is where a set's iteration order or a
    dict's insertion order would surface. Comparing the dataclasses would miss
    an ordering that only the encoder sees.
    """
    first = _pinned_night().picked(6)
    second = _pinned_night().picked(6)

    assert json.dumps(sweep_mutations.spec_entries(first)) == json.dumps(
        sweep_mutations.spec_entries(second)
    )
    assert first


def test_a_candidate_keeps_its_label_when_the_night_asks_for_fewer_mutations() -> None:
    """A label names a candidate in the file, not a slot in tonight's batch.

    The filed issue is the durable artefact, and it names labels. If the same
    source position were called ``-00-`` under ``--max-mutations 6`` and ``-01-``
    under a different limit, two issues about the same defect would not be
    recognisable as such, and a reproduction typed from the issue would apply a
    different mutation than the one that survived.
    """
    night = _pinned_night()

    six = night.picked(6)
    one = night.picked(1)

    assert len(one) == 1
    assert one[0].label in {candidate.label for candidate in six}
    assert one[0].label == six[0].label


def test_a_label_carries_the_date_the_night_ran() -> None:
    """Reproduction starts from the issue title, which carries only a date.

    A label without the date makes two nights' findings on one file collide in
    search, and makes "re-run the sweep for that night" guesswork.
    """
    generated = _pinned_night().picked(2)

    assert all(candidate.label.startswith("sweep-2026-09-16-") for candidate in generated)


def test_most_of_the_production_tree_has_something_to_mutate() -> None:
    """A rotation whose files are mostly barren sweeps almost nothing.

    Every barren draw is answered by advancing, so a low yield does not break a
    night -- it makes the night attack a neighbour instead, and the file the date
    actually named goes unswept for ever. Measured 2026-09-16 at ``e46fab2a``:
    115 of 139 files yield at least one anchorable mutation, 1138 candidates in
    total against 494 dropped for a non-unique anchor. The floor below is half
    the census, which that measurement clears comfortably and which an
    over-eager skip rule would not.
    """
    files = sweep_census.census()

    productive = [
        target
        for target in files
        if sweep_mutations.candidates(target, _source_of(target), on=_NIGHT).candidates
    ]

    assert len(productive) >= len(files) // 2
