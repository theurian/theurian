"""The sweep must attack the same files twice given the same date (#378).

A sweep that picks its targets by anything but the date cannot be reproduced. The
finding it files names a mutation; whoever reads that issue has to be able to
regenerate exactly that mutation from the date in the title, without the sweep's
machine, its process memory or its ordering of a directory walk. Every rule here
exists so that "re-run the sweep for 2026-09-16" is a complete instruction.

The rotation, block and selection rules are pinned against a *synthetic* census, so
they stay claims about the scheme rather than about whatever
``packages/theurian-core/src`` holds today; the real census is then checked
separately for the properties the scheme assumes of it (non-empty, sorted, no
``__init__.py``).

Every walk below steps in *run* space: one step is seven days, the gap between
two scheduled runs, because the run index is ``ordinal // 7`` and six dates in
seven resolve to the run they fall in. A run consumes a contiguous **block** of
``size`` census positions opening at ``(run · size) % len``, so a pin written at
one census length and one block size can agree with an implementation that
ignores the block size entirely -- ``size`` = 6 against a census of 5 opens where
``size`` = 1 does. Every rule below that touches the opening is therefore
parametrized over lengths and sizes where the two part company.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from math import gcd
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

#: How far apart two consecutive scheduled runs are, and so the step every walk
#: below takes. It is the run bucket's own width: a one-day step is not a step at
#: all for six days out of seven.
_RUN_STRIDE = 7

#: The first run of every pinned window below, and a Sunday because that is
#: where a bucket starts (ordinal 7 is a Sunday). Its own run number is not 0 --
#: the count runs from ordinal zero, not from this date -- so
#: :func:`_run_opening_at` is what finds the run a given position belongs to.
_FIRST_RUN = date(2026, 9, 13)


def _run(offset: int) -> date:
    """The date of the run ``offset`` scheduled runs after :data:`_FIRST_RUN`."""
    return date.fromordinal(_FIRST_RUN.toordinal() + offset * _RUN_STRIDE)


def _census_of(size: int) -> tuple[str, ...]:
    """A synthetic census of ``size`` modules, named so sorted order is index order."""
    return tuple(f"m{index:02d}.py" for index in range(size))


def _run_opening_at(index: int, length: int, size: int) -> date:
    """The run whose block opens at census position ``index``.

    Selection, not expectation: it decides *which* run an assertion is made
    about, and the assertion then pins a path or an index the implementation has
    to produce. The search covers one full period of the opening sequence, which
    is ``length / gcd(size, length)`` runs -- past that the openings repeat, so a
    position not found there is not an opening at all and the caller has picked
    an index this pairing never reaches.
    """
    for offset in range(length // gcd(size, length)):
        when = _run(offset)
        if sweep_census.block(_census_of(length), when, size=size)[0].index == index:
            return when
    raise AssertionError(f"no run opens at index {index} for a census of {length} at size {size}")


def test_every_date_in_one_seven_day_bucket_is_the_same_run() -> None:
    """``workflow_dispatch``'s ``date`` input steers by the week, not by the day.

    :func:`sweep_census.run_index` states it and an operator acts on it: a rerun
    typed with the Wednesday has to draw the block that week's Sunday run drew,
    or the ``date`` input stops being the positive control the workflow offers it
    as -- it would aim at files whose verdicts nobody knows.

    The bucket's *origin* is pinned too, as the ordinal arithmetic rather than as
    a consequence. Sunday through Saturday is not the ISO week: it parts from it
    at the Sunday, and a grid shifted by a day would still give seven equal dates
    while putting the boundary where no cron fires.
    """
    bucket = [date.fromordinal(_FIRST_RUN.toordinal() + day) for day in range(_RUN_STRIDE)]

    runs = {sweep_census.run_index(day) for day in bucket}

    assert (_FIRST_RUN.weekday(), bucket[-1].weekday()) == (6, 5)
    assert runs == {105696}
    assert sweep_census.run_index(_run(1)) == 105697


@pytest.mark.parametrize(
    ("length", "size", "openings"),
    (
        (5, 1, (0, 1, 2, 3)),
        (5, 2, (0, 2, 4, 1)),
        (7, 6, (0, 6, 5, 4)),
        (142, 6, (0, 6, 12, 18)),
    ),
    ids=str,
)
def test_consecutive_runs_open_one_whole_block_further_on(
    length: int, size: int, openings: tuple[int, ...]
) -> None:
    """The block's opening scales with the block size, and nothing weaker does.

    An implementation that opened at ``run % len`` -- the single-slot form this
    replaces -- advances by one whatever the block holds, so consecutive runs
    would re-attack five of the six files they just attacked. Every pairing below
    except ``size`` = 1 separates the two, and ``size`` = 1 is here because it is
    the case where they agree: a pin written only at a pairing where
    ``size % len`` happens to be 1 (``size`` = 6 against a census of 5, which is
    the shipped default against this file's own synthetic census) passes over the
    defect entirely.

    Asserted as offsets from the first opening rather than as absolute indices,
    because the absolute one is a function of the calendar and the relation is
    what the scheme promises.
    """
    census = _census_of(length)

    drawn = [sweep_census.block(census, _run(offset), size=size)[0].index for offset in range(4)]

    assert [(index - drawn[0]) % length for index in drawn] == list(openings)


def test_a_run_consumes_a_contiguous_block_that_wraps_past_the_end_of_the_census() -> None:
    """Wrapping is ordinary here, and the lap is what makes it readable.

    A block opening near the end of the census runs off it, and the slots that
    continue at position 0 are a *later* lap of the same walk -- which is what
    :meth:`sweep_mutations.Generated.at_lap` indexes a file's candidates by. A
    block that truncated at the end would spend fewer walks than the run paid
    for; one that wrapped without incrementing the lap would ask those files the
    same question for ever.

    Pinned as three tuples over one hand-checkable census: the paths, the indices
    they claim to sit at, and the lap each slot carries relative to the first.
    The paths and the indices are asserted separately on purpose -- a slot whose
    ``index`` does not name its own ``path`` is exactly the drift that makes a
    filed issue point at the wrong module.
    """
    census = _census_of(7)

    drawn = sweep_census.block(census, _run_opening_at(4, 7, 6), size=6)

    assert tuple(slot.path for slot in drawn) == (
        "m04.py",
        "m05.py",
        "m06.py",
        "m00.py",
        "m01.py",
        "m02.py",
    )
    assert tuple(slot.index for slot in drawn) == (4, 5, 6, 0, 1, 2)
    assert tuple(slot.lap - drawn[0].lap for slot in drawn) == (0, 0, 0, 1, 1, 1)


def test_the_rotation_shows_the_whole_census_opening_at_this_runs_block() -> None:
    """The reader-facing ordering, and the one the block takes its head from.

    Two things a reader of a dry run acts on: the block is the *head* of this
    sequence, and the sequence is a rotation rather than a truncating slice, so
    what comes after the block is what the next run opens on. A rotation that
    dropped the wrapped tail would show a shorter census than the sweep indexes
    into, and the two would disagree about which file position 0 is.
    """
    census = _census_of(7)
    when = _run_opening_at(4, 7, 6)

    walked = sweep_census.rotation(census, when, size=6)

    assert walked == ("m04.py", "m05.py", "m06.py", "m00.py", "m01.py", "m02.py", "m03.py")
    assert sorted(walked) == sorted(census)
    assert walked[: len(sweep_census.block(census, when, size=6))] == tuple(
        slot.path for slot in sweep_census.block(census, when, size=6)
    )


@pytest.mark.parametrize(
    ("length", "size", "cover", "surplus"),
    (
        (12, 6, 2, 0),
        (14, 6, 3, 4),
        (142, 6, 24, 2),
        (13, 5, 3, 2),
        (7, 3, 3, 2),
    ),
    ids=str,
)
def test_consecutive_runs_cover_every_census_index_and_repeat_only_the_surplus(
    length: int, size: int, cover: int, surplus: int
) -> None:
    """Coverage in a bounded number of runs, which is the whole point of the block.

    The single-slot form covered a 142-module census in 142 runs -- weeks, at one
    run a week, and nobody waits that out. A block of six covers it in 24, and
    that bound is the claim: **every** index is drawn at least once within
    ``cover`` consecutive runs, from any starting run.

    What a cover does not promise is *exactly* once, and a pin asserting it would
    be false rather than strict. ``cover · size`` consecutive positions on a
    circle of ``length`` positions is ``cover · size - length`` positions of
    overlap, so that many indices are drawn twice and the rest once -- two of 142
    at the shipped size, four of fourteen at the pairing below that divides
    worst. Both numbers are pinned per case rather than recomputed, and the whole
    visit *distribution* is asserted rather than a floor: ``>= 1`` would hold for
    an implementation that drew one index a hundred times.

    **From any start, exhaustively.** The opening sequence repeats every
    ``length / gcd(size, length)`` runs, so checking each start in one period is
    every start there is -- 71 of them at 142 and 6. A handful of sampled offsets
    would leave the phase that aligns worst untested, and that phase is where an
    off-by-one in the wrap lives.
    """
    census = _census_of(length)

    for start in range(length // gcd(size, length)):
        visits = Counter(
            slot.index
            for offset in range(cover)
            for slot in sweep_census.block(census, _run(start + offset), size=size)
        )

        assert set(visits) == set(range(length)), f"start {start} left an index undrawn"
        assert sorted(visits.values()) == [1] * (length - surplus) + [2] * surplus, (
            f"start {start} drew {sorted(visits.values())}"
        )


def test_a_census_the_sweep_cannot_rotate_is_refused_rather_than_guessed() -> None:
    """An empty census means the walk found nothing; ``% 0`` would raise anyway.

    Refusing by name matters because the driver turns this into exit 1 -- "the
    sweep failed to run" -- and a sweep that quietly attacked nothing would
    otherwise report success. Both entry points are asserted: they are separate
    functions with separate guards, and the driver calls :func:`sweep_census.block`.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_census.rotation((), _FIRST_RUN)
    with pytest.raises(sweep_census.SweepError):
        sweep_census.block((), _FIRST_RUN)


@pytest.mark.parametrize("size", (0, -1))
def test_a_block_that_consumes_no_slot_is_refused_rather_than_reported_clean(size: int) -> None:
    """``--block-size 0`` asks nothing, so it cannot answer anything.

    An empty block hands the harness an empty spec, which comes back with a green
    control and no mutation -- a run that measured nothing and reads in the
    summary line exactly like a run that measured six things and held them all.
    A negative size is the same refusal by the same guard, and it is what a
    subtraction in a caller produces.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_census.block(_SYNTHETIC, _FIRST_RUN, size=size)


def test_the_real_census_is_the_sorted_production_tree_without_package_markers() -> None:
    """The population the rotation indexes into, checked against the repository.

    Three claims, because the index is meaningless without all three: the census
    is non-empty (an empty one makes every run exit 1), it is sorted (an
    unsorted one makes the index depend on the order a directory walk happened to
    return), and it excludes ``__init__.py`` (re-exports carry no branch a
    mutation operator can reach, so an included one costs a block slot).
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


#: A census of two, where one file offers three candidates and the other one.
#:
#: Two is what keeps a block of one from ever being wholly barren, so the runs
#: below exercise the selection rather than the all-barren guard; three
#: candidates is what makes "every label this file can produce" more than one
#: label.
_TWO_FILE_CENSUS = (
    ("multi.py", "plain.py"),
    {
        "multi.py": "def pick(a, b):\n    if a < b:\n        return True\n    return False\n",
        "plain.py": _SYNTHETIC_SOURCES["c.py"],
    },
)


def _source_of(path: str) -> str:
    return Path(sweep_census.REPO_ROOT / path).read_text(encoding="utf-8")


def _pinned_run(when: date = _NIGHT) -> tuple[sweep_mutations.Attempt, ...]:
    """One real run's block, selected and generated exactly as the driver does it."""
    slots = sweep_census.block(sweep_census.census(), when)
    return sweep_mutations.for_block(slots, _source_of, on=when)


def _synthetic_run(when: date) -> tuple[sweep_mutations.Attempt, ...]:
    """The whole synthetic census as one block, so every draw is exercised at once."""
    slots = sweep_census.block(_SYNTHETIC, when, size=len(_SYNTHETIC))
    return sweep_mutations.for_block(slots, lambda path: _SYNTHETIC_SOURCES[path], on=when)


def test_a_barren_block_member_keeps_its_slot_and_buys_no_walk() -> None:
    """A block member with nothing to mutate is a fact about the run, not an error.

    Every census holds modules of constants, SQL text and column tuples. The
    single-target form could only express one by advancing past it, which spent
    the run on a neighbour and left the file the date actually named unswept. A
    block reports it instead: the slot stays, its attempt carries no candidate,
    and the run's cost falls by one walk rather than that walk being handed to
    another file's second question.

    Pinned against the synthetic census so the live tree's size cannot re-index
    the draws, and over a block covering all five files so the assertion is about
    every member rather than a chosen one.
    """
    attempts = _synthetic_run(_FIRST_RUN)

    barren = tuple(attempt.path for attempt in attempts if attempt.candidate is None)
    mutated = tuple(attempt.path for attempt in attempts if attempt.candidate is not None)

    assert sorted(barren) == ["a.py", "d.py", "e.py"]
    assert sorted(mutated) == ["b.py", "c.py"]
    assert all(attempt.generated.path == attempt.path for attempt in attempts)


def test_a_block_with_nothing_to_mutate_anywhere_refuses_to_report_a_clean_run() -> None:
    """The silent-stop guard at the generation layer.

    A block whose every member is barren has not swept anything. Returning "no
    candidates" to the driver would let the run exit 0 with no issue filed, which
    is indistinguishable from a run that asked six questions and got six answers.
    """
    slots = sweep_census.block(("one.py", "two.py"), _NIGHT, size=2)

    with pytest.raises(sweep_census.SweepError):
        sweep_mutations.for_block(slots, lambda _: "VALUE = 3\n", on=_NIGHT)


def test_generating_the_same_run_twice_writes_byte_identical_specs() -> None:
    """The other half of AC1: selection *and* generation reproduce.

    Serialised before comparing, because the spec file is the artefact handed to
    ``tools/mutate.py`` and a JSON document is where a set's iteration order or a
    dict's insertion order would surface. Comparing the dataclasses would miss
    an ordering that only the encoder sees.
    """
    first = [attempt.candidate for attempt in _pinned_run() if attempt.candidate is not None]
    second = [attempt.candidate for attempt in _pinned_run() if attempt.candidate is not None]

    assert first
    assert json.dumps(sweep_mutations.spec_entries(first)) == json.dumps(
        sweep_mutations.spec_entries(second)
    )


def test_a_label_names_a_position_in_its_file_and_not_a_slot_in_the_block() -> None:
    """The filed issue is the durable artefact, and it names labels.

    A label numbered by its place in the run's spec would be re-pointed by the
    block that happened to draw it: the same source position would be ``-00-``
    in a run that drew it first and ``-03-`` in a run that drew it fourth, two
    issues about one defect would not be recognisable as such, and a reproduction
    typed from the issue would apply a different mutation than the one that
    survived.

    Asserted by drawing one file across enough runs and block sizes that every
    candidate it offers is handed over at some point, at a different position in
    the block each time: the labels the runs produce are exactly the labels the
    file gave itself.
    """
    census, sources = _TWO_FILE_CENSUS
    own = {
        candidate.label
        for candidate in sweep_mutations.candidates(
            "multi.py", sources["multi.py"], on=_NIGHT
        ).candidates
    }

    drawn = {
        attempt.candidate.label
        for size in (1, 2)
        for offset in range(6)
        for attempt in sweep_mutations.for_block(
            sweep_census.block(census, _run(offset), size=size), sources.__getitem__, on=_NIGHT
        )
        if attempt.candidate is not None and attempt.path == "multi.py"
    }

    assert len(own) == 3
    assert drawn == own


def test_two_files_offering_the_same_operator_at_the_same_position_get_different_labels() -> None:
    """A label is the only key joining a harness verdict back to its mutation.

    One run now hands the harness a mutation from each of several files at once,
    and the candidate index counts each file's own hits from zero -- so two files
    whose first mutable operator is the same token on the same line produce the
    same label everywhere except in the file slug. Without the slug the harness's
    record has two outcomes under one key: ``sweep_filing`` attributes one of
    them to whichever module it matched first and drops the other into
    ``## Unattributed verdicts``, and the kill rate reads a module that was never
    asked.

    The two sources below are byte-identical on purpose, which is the worst case
    rather than a contrived one: a repository of small modules has many.
    """
    twin = "def fits(size: int) -> bool:\n    return size < 3\n"

    left = sweep_mutations.candidates("one.py", twin, on=_NIGHT).candidates
    right = sweep_mutations.candidates("two.py", twin, on=_NIGHT).candidates

    assert (left[0].line, left[0].swapped_from) == (right[0].line, right[0].swapped_from)
    assert left[0].label != right[0].label


def test_a_label_carries_the_date_the_run_ran() -> None:
    """Reproduction starts from the issue title, which carries only a date.

    A label without the date makes two runs' findings on one file collide in
    search, and makes "re-run the sweep for that date" guesswork.
    """
    drawn = [attempt.candidate for attempt in _pinned_run() if attempt.candidate is not None]

    assert drawn
    assert all(candidate.label.startswith("sweep-2026-09-16-") for candidate in drawn)


def test_most_of_the_production_tree_has_something_to_mutate() -> None:
    """A census that is mostly barren makes the block mostly slots and no walks.

    A barren member costs its slot and buys no walk, so a low yield does not
    break a run -- it makes the run ask fewer questions than the budget paid for,
    quietly, and the coverage bound above goes on being true about files nobody
    mutated. Measured 2026-09-19 at ``92581f77`` over the 142-module census: 118
    files yield at least one anchorable mutation and 24 are barren. The floor
    below is half the census, which that measurement clears comfortably and which
    an over-eager skip rule would not.
    """
    files = sweep_census.census()

    productive = [
        target
        for target in files
        if sweep_mutations.candidates(target, _source_of(target), on=_NIGHT).candidates
    ]

    assert len(productive) >= len(files) // 2
