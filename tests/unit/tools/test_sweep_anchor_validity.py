"""A generated mutation must be anchorable, or it must not be generated (#378).

``tools/mutate.py`` requires ``--old`` to occur **exactly once** in the target
file, and refuses the batch otherwise -- ``_apply_edit`` raises, the run reports
``ERROR`` and exits 2. A generator that emits a two-occurrence anchor therefore
does not produce a wrong verdict; it produces a sweep that spent an hour of CI
and answered nothing. A generator that emits a *zero*-occurrence anchor is worse,
and that is not hypothetical for this repository: ``mutate_edits._apply_edit``'s
own docstring records that "a missing anchor produces a run that tests nothing
while reporting SURVIVED".

So uniqueness is checked here, by the generator, before a spec is written. The
widening ladder is segment -> full source line -> skip, and the fixture below is
built to reach all three rungs rather than the comfortable one: two comparisons
that are identical *and* sit on identical lines (skipped), one that repeats but
sits on a unique line (widened), and one that is unique as written (segment).

A skip is reported rather than swallowed. A generator that silently dropped
candidates could drop all of them and leave the night reading clean.
"""

from __future__ import annotations

import ast
import io
import textwrap
import tokenize
from collections.abc import Sequence
from datetime import date

import pytest
import sweep_census
import sweep_mutations

pytestmark = pytest.mark.unit

#: Engineered so that every rung of the widening ladder is reached. Line numbers
#: are located by :func:`_line_of` rather than written down, so editing the
#: fixture cannot silently re-point an assertion at another statement.
_FIXTURE = textwrap.dedent(
    """\
    def first(a, b):
        if a < b:
            return 1
        return 0


    def second(a, b):
        if a < b:
            return 2
        return 0


    def third(a, b):
        chosen = 1 if a < b else 2
        return chosen


    def fourth(a, b):
        return a <= b + 999


    def fifth(flag):
        first_gate = True
        second_gate = True
        return first_gate and flag
    """
)

#: Non-ASCII text *before* the operator on the same line, which is the only
#: shape that separates a character offset from a UTF-8 byte offset. The strings
#: are Japanese because this repository's sources and fixtures carry Japanese
#: (ADR-0023's tokenization evidence); any non-ASCII text would serve, and
#: translating it would delete the property being tested.
_WIDE_FIXTURE = textwrap.dedent(
    """\
    def chosen(state):
        label = "設計記録" if state == "確定" else "草案"
        return label


    def flags(state):
        return {"名前": state, "有効": True}
    """
)

_PATH = "packages/theurian-core/src/theurian/example.py"
_NIGHT = date(2026, 9, 16)


def _line_of(fragment: str, *, occurrence: int = 1) -> int:
    """The 1-based line holding the nth occurrence of ``fragment``."""
    found = [
        number for number, text in enumerate(_FIXTURE.splitlines(), start=1) if fragment in text
    ]
    return found[occurrence - 1]


def _token_strings(source: str) -> list[str]:
    """Every token's text, read with the stdlib rather than with the subject.

    Deliberately not ``sweep_mutations``' own tokeniser: a check that reuses the
    implementation's view of the file agrees with it by construction.
    """
    return [token.string for token in tokenize.generate_tokens(io.StringIO(source).readline)]


def _generated() -> sweep_mutations.Generated:
    return sweep_mutations.candidates(_PATH, _FIXTURE, on=_NIGHT)


def _source_of(target: str) -> str:
    return (sweep_census.REPO_ROOT / target).read_text(encoding="utf-8")


def test_every_emitted_anchor_occurs_exactly_once_in_the_target() -> None:
    """The contract ``tools/mutate.py`` enforces, enforced one layer earlier.

    Asserted over every candidate the fixture produces rather than over a chosen
    one: the interesting failure is the candidate the generator did *not* think
    about, and a spot check cannot see it.
    """
    generated = _generated()

    assert generated.candidates
    for candidate in generated.candidates:
        assert _FIXTURE.count(candidate.old) == 1, candidate.label


def test_a_candidate_that_cannot_be_uniquely_anchored_is_skipped_and_said_so() -> None:
    """Two identical comparisons on two identical lines: neither rung is unique.

    This is the rung that must *not* silently vanish. The two ``if a < b:`` lines
    in the fixture are byte-identical, so widening to the line buys nothing, and
    the only honest outcome is to drop both -- visibly. The skip record is what a
    reader of the dry-run output sees; without it a generator that dropped every
    candidate would be indistinguishable from a file with nothing to mutate.
    """
    generated = _generated()

    ambiguous = (_line_of("if a < b:", occurrence=1), _line_of("if a < b:", occurrence=2))
    emitted = [item.line for item in generated.candidates if item.line in ambiguous]

    assert emitted == []
    assert sorted(skip.line for skip in generated.skipped) == sorted(ambiguous)
    assert {skip.reason for skip in generated.skipped} == {"anchor-not-unique"}


def test_a_repeated_expression_on_a_unique_line_widens_to_that_line() -> None:
    """The middle rung, pinned as an exact anchor rather than as "it is unique".

    ``a < b`` occurs three times in the fixture, so the segment cannot anchor
    this candidate; the line it sits on occurs once, so the line can. Asserting
    the exact ``old``/``new`` pair is what distinguishes widening from giving up:
    an implementation that skipped whenever the segment repeated would pass a
    uniqueness-only assertion by emitting nothing here.
    """
    generated = _generated()
    wanted = _line_of("chosen = 1 if a < b else 2")

    widened = [item for item in generated.candidates if item.line == wanted]

    assert len(widened) == 1
    assert widened[0].old == "    chosen = 1 if a < b else 2"
    assert widened[0].new == "    chosen = 1 if a <= b else 2"


def test_a_unique_expression_anchors_on_the_expression_alone() -> None:
    """The narrow rung: no wider anchor than the mutation needs.

    A generator that always widened to the line would still be *correct* under
    the uniqueness rule, and would still be worse: the anchor is what a reader of
    the filed issue matches against the file, and an anchor carrying unrelated
    code invites a manual application at the wrong place.
    """
    generated = _generated()
    wanted = _line_of("a <= b + 999")

    narrow = [item for item in generated.candidates if item.line == wanted]

    assert len(narrow) == 1
    assert narrow[0].old == "a <= b + 999"
    assert narrow[0].new == "a < b + 999"


def test_a_boolean_literal_and_a_boolean_operator_are_both_reachable() -> None:
    """All three operator families fire, not just the comparison one.

    The generator's whole value is that an unattended sweep asks questions
    nobody wrote by hand. A family that never fires asks nothing, and a
    comparison-only generator passes every assertion above.
    """
    generated = _generated()

    by_line = {item.line: item for item in generated.candidates}
    flipped = by_line[_line_of("first_gate = True")]
    loosened = by_line[_line_of("return first_gate and flag")]

    assert flipped.old == "    first_gate = True"
    assert flipped.new == "    first_gate = False"
    assert loosened.old == "first_gate and flag"
    assert loosened.new == "first_gate or flag"


def test_applying_a_candidate_changes_exactly_the_operator_it_names() -> None:
    """The mutation must be the one the label claims, and nothing else.

    Checked by replacing the anchor and diffing the *token stream* rather than
    the text: the anchor may be a whole line, so a textual diff cannot say
    whether the line's other tokens survived. Exactly one token differs, and the
    result still parses -- a mutant that does not parse comes back KILLED by a
    collection error, which is a false KILLED and reads exactly like a property
    the suite holds.
    """
    generated = _generated()
    before = _token_strings(_FIXTURE)

    for candidate in generated.candidates:
        mutated = _FIXTURE.replace(candidate.old, candidate.new, 1)
        after = _token_strings(mutated)
        differing = [
            index for index, (was, now) in enumerate(zip(before, after, strict=True)) if was != now
        ]

        ast.parse(mutated)
        assert len(differing) == 1, candidate.label
        assert before[differing[0]] == candidate.swapped_from, candidate.label
        assert after[differing[0]] == candidate.swapped_to, candidate.label


def _applies_cleanly_and_once(
    candidate: sweep_mutations.Candidate, source: str, before: Sequence[str]
) -> None:
    """The two rules of the module docstring, against one real file.

    Checked on the token stream rather than on the text: the anchor may be a
    whole line, so a textual diff cannot say whether the line's other tokens
    survived. A mutant that does not parse comes back KILLED by a collection
    error, which is a false KILLED and reads exactly like a property the suite
    holds -- so the parse is asserted too.

    ``before`` is passed in rather than recomputed: the unmutated token stream is
    the same for every candidate of one file, and tokenizing it per candidate
    doubled the whole-census case's 6 s.
    """
    assert source.count(candidate.old) == 1, f"{candidate.path}: {candidate.label}"
    mutated = source.replace(candidate.old, candidate.new, 1)
    after = _token_strings(mutated)
    differing = [
        index for index, (was, now) in enumerate(zip(before, after, strict=True)) if was != now
    ]

    ast.parse(mutated)
    assert len(differing) == 1, candidate.label
    assert before[differing[0]] == candidate.swapped_from, candidate.label
    assert after[differing[0]] == candidate.swapped_to, candidate.label


def test_every_mutation_the_real_tree_would_run_applies_cleanly_and_once() -> None:
    """The same two rules, against the population a run actually hands over.

    The fixture above proves the widening ladder reaches all three rungs. It
    cannot prove the ladder holds against 142 files of f-strings, walrus
    operators, match statements, nested comprehensions, CJK string literals and
    multi-line boolean expressions -- and the byte-versus-character column
    arithmetic ``ast`` forces on this module is exactly the kind of bug that is
    invisible on a short ASCII fixture and wrong on a real file.

    The population is built the way the driver builds it -- ``block`` then
    ``for_block``, over a **full cover** of consecutive runs -- so every census
    file is drawn at least once through the code path that picks it, rather than
    through a loop this test wrote itself. Measured 2026-09-19 at ``92581f77``:
    24 runs, 144 slots, 120 mutations handed over and 24 barren slots. A run's
    own block is six files, so a check written against one run would say nothing
    about the other 136.
    """
    census = sweep_census.census()
    cover = -(-len(census) // sweep_census.BLOCK_SIZE)
    sources = {target: _source_of(target) for target in census}
    tokens = {target: _token_strings(source) for target, source in sources.items()}
    checked = 0

    for offset in range(cover):
        when = date.fromordinal(_NIGHT.toordinal() + offset * 7)
        for attempt in sweep_mutations.for_block(
            sweep_census.block(census, when), sources.__getitem__, on=when
        ):
            if attempt.candidate is None:
                continue
            _applies_cleanly_and_once(
                attempt.candidate, sources[attempt.path], tokens[attempt.path]
            )
            checked += 1

    assert checked >= len(census) // 2


def test_every_candidate_the_whole_census_offers_applies_cleanly_and_once() -> None:
    """The cover above draws one candidate per file; a file's others run later.

    :meth:`sweep_mutations.Generated.at_lap` hands a file's next candidate over
    on its next visit, so the population a run can eventually hand to
    ``tools/mutate.py`` is every candidate of every census file -- 1159 of them
    measured 2026-09-19 at ``92581f77``, against 506 dropped for a non-unique
    anchor. A check that stopped at the ones this week's cover happens to draw
    would leave the rest to be discovered by an unattended job at 01:17 UTC,
    which is where a mis-anchored mutation becomes a fabricated SURVIVED.
    """
    checked = 0

    for target in sweep_census.census():
        source = _source_of(target)
        before = _token_strings(source)
        for candidate in sweep_mutations.candidates(target, source, on=_NIGHT).candidates:
            _applies_cleanly_and_once(candidate, source, before)
            checked += 1

    assert checked >= 1000


def test_an_operator_behind_non_ascii_text_still_anchors_where_it_actually_is() -> None:
    """``ast`` columns are UTF-8 byte offsets; ``tokenize`` columns are characters.

    On an all-ASCII line the two agree, which is why every other rule in this
    file passes with the conversion removed -- and why the conversion needs a
    line where they disagree. Here ``"設計記録"`` costs twelve bytes and four
    characters, so an implementation that slices the source with the ``ast``
    column anchors four characters to the right of the operator it named: either
    on text that is not the operator, or -- as measured -- nowhere at all.

    The whole-tree check below cannot substitute for this: no production file
    currently carries non-ASCII text ahead of a mutable operator on the same
    line, so removing the conversion leaves all 558 of its mutations green.
    """
    generated = sweep_mutations.candidates(_PATH, _WIDE_FIXTURE, on=_NIGHT)

    by_token = {candidate.swapped_from: candidate for candidate in generated.candidates}

    assert set(by_token) == {"==", "True"}
    assert by_token["=="].old == 'state == "確定"'
    assert by_token["=="].new == 'state != "確定"'
    assert _WIDE_FIXTURE.count(by_token["=="].old) == 1
    assert _WIDE_FIXTURE.replace(by_token["True"].old, by_token["True"].new, 1).endswith(
        '{"名前": state, "有効": False}\n'
    )


def test_a_label_numbers_every_operator_the_file_offers_not_only_the_usable_ones() -> None:
    """A label has to keep naming the same source position across edits.

    The fixture's first two comparisons are dropped as unanchorable, so a label
    numbered by *emitted* order would call the third candidate ``-00-``; adding a
    test that makes the first one unique would then renumber every label in the
    file, and two runs' issues about one defect would not be recognisable as
    the same defect. Numbering by the operator token's position in the file is
    what keeps the name stable.
    """
    generated = _generated()
    wanted = _line_of("chosen = 1 if a < b else 2")

    widened = next(item for item in generated.candidates if item.line == wanted)

    assert widened.label == f"sweep-2026-09-16-a1e446-02-lt-to-le-l{wanted}"


#: A form feed used as a page separator -- the conventional one in Python source,
#: and a character ``str.splitlines`` treats as a line break while ``ast`` and
#: ``tokenize`` treat it as whitespace inside a line. Written as a raw byte, not
#: an escape, because the escape would be four ordinary characters and would test
#: nothing.
_PAGE_BREAK_FIXTURE = (
    "def described(value):\n"
    '    """A docstring that says True on purpose."""\n'
    "    return value\n"
    "\n"
    "\x0cdef gated(flag):\n"
    "    return flag is True\n"
)


def test_a_form_feed_does_not_shift_every_offset_after_it() -> None:
    """The line model has to be the tokenizer's, not ``str.splitlines``'.

    ``splitlines`` breaks on ``\x0c`` and five other characters that Python's
    own grammar does not, so one page separator put this module's numbering a
    line ahead of ``ast``'s for the rest of the file. The reviewer's
    reproduction then emitted an anchor that was unique, parsed, and mutated a
    *docstring* under a label claiming a ``True``/``False`` flip in code -- a
    SURVIVED fabricated from a mutation that never touched a branch, and its
    mirror, a KILLED holding nothing.

    The assertion is that the anchor names the code and that applying it flips
    the operator the label claims, because "it produced something" was never the
    problem.
    """
    generated = sweep_mutations.candidates(_PATH, _PAGE_BREAK_FIXTURE, on=_NIGHT)

    assert len(generated.candidates) == 1
    only = generated.candidates[0]
    mutated = _PAGE_BREAK_FIXTURE.replace(only.old, only.new, 1)
    assert only.old == "    return flag is True"
    assert only.new == "    return flag is False"
    assert "A docstring that says True on purpose." in mutated
    ast.parse(mutated)


def test_a_windows_line_ending_keeps_its_offsets() -> None:
    """The other side of the split-on-newline model, which it must not break.

    ``\r\n`` is one break to Python and the ``\r`` sits at the end of the line,
    past every column offset -- so a model built on ``"\n"`` handles it without
    a special case. Asserted rather than assumed, since a naive fix could have
    left the ``\r`` shifting each line's length by one.
    """
    source = "def gated(flag):\r\n    return flag is True\r\n"

    generated = sweep_mutations.candidates(_PATH, source, on=_NIGHT)

    assert len(generated.candidates) == 1
    assert generated.candidates[0].swapped_from == "True"
    assert source.count(generated.candidates[0].old) == 1


def test_a_file_with_nothing_to_mutate_yields_no_candidates_rather_than_raising() -> None:
    """A barren block member is an ordinary run, not an error.

    Constants-and-dataclasses modules are common in this codebase -- 24 of the
    142 in the census, measured 2026-09-19 at ``92581f77`` -- and hold nothing
    any of the three operators can reach. ``for_block`` reports such a slot as a
    member that bought no walk, which it can only do if the generator returns
    empty instead of raising.
    """
    barren = "VALUE = 3\n\n\ndef identity(item):\n    return item\n"

    generated = sweep_mutations.candidates(_PATH, barren, on=_NIGHT)

    assert generated.candidates == ()
    assert generated.skipped == ()


def test_a_target_that_does_not_parse_stops_the_sweep_instead_of_reading_barren() -> None:
    """An unparseable production file is not "nothing to mutate".

    Treating it as barren would report the slot as a module with nothing to
    offer and carry on with the rest of the block, so a repository that cannot
    even be imported would produce a run that files nothing about it. The driver
    turns this into exit 1.
    """
    with pytest.raises(sweep_census.SweepError):
        sweep_mutations.candidates(_PATH, "def broken(:\n", on=_NIGHT)


#: A file offering four candidates, one per operator family plus a second
#: comparison, so "the next visit asks the next question" has somewhere to go
#: and a cycle short enough to walk twice by hand.
_FOUR_CANDIDATE_FIXTURE = textwrap.dedent(
    """\
    def gate(a, b, flag):
        if a < b:
            return True
        if a > b:
            return flag
        return False
    """
)


def test_consecutive_visits_to_one_file_draw_consecutive_candidates() -> None:
    """One mutation per file per run only buys depth if the question moves.

    A run hands the harness one mutation per block member, so a selector that
    answered a file's first candidate every time would ask that file the same
    question for ever -- ``candidates[0]``, which is what a budget of one made of
    the old evenly-spaced pick, and a defect measured rather than reasoned about.
    Measured 2026-09-19 at ``92581f77`` over 60 runs of the live census: every one
    of the 112 revisited files offering more than one candidate drew a different
    one on its next visit under the lap index, and none did under the old one.

    Asserted as a whole cycle rather than as "two visits differ": a selector that
    alternated between two of four candidates satisfies the weaker claim and
    still never asks the other two. The tail assertion is the wrap -- the cycle
    returns to the first candidate only after all of them have run.
    """
    generated = sweep_mutations.candidates(_PATH, _FOUR_CANDIDATE_FIXTURE, on=_NIGHT)
    span = len(generated.candidates)

    drawn = [generated.at_lap(lap) for lap in range(span)]

    assert span == 4
    assert drawn == list(generated.candidates)
    assert generated.at_lap(span) == generated.candidates[0]


def test_the_advance_holds_at_the_lap_numbers_a_real_run_carries() -> None:
    """A lap is an absolute visit count, not an offset from the first run.

    ``sweep_census.block`` computes it as ``position // len(census)``, which is
    around 90,000 for any date this decade -- so a selector correct only for
    small laps, or one keyed on the run number rather than the visit, is wrong
    everywhere it actually runs. The two laps below are consecutive *visits*,
    which is what the block hands over.
    """
    generated = sweep_mutations.candidates(_PATH, _FOUR_CANDIDATE_FIXTURE, on=_NIGHT)
    lap = sweep_census.block(sweep_census.census(), _NIGHT)[0].lap

    here, next_visit = generated.at_lap(lap), generated.at_lap(lap + 1)

    assert lap > 1000
    assert here is not None and next_visit is not None
    span = len(generated.candidates)
    assert generated.candidates.index(next_visit) == (generated.candidates.index(here) + 1) % span


def test_a_file_offering_one_candidate_asks_the_same_question_on_every_visit() -> None:
    """The exception the claim above is written around, and it is a real population.

    Six of the 142 census modules offer exactly one anchorable mutation, of the
    118 that offer any (measured 2026-09-19 at ``92581f77``). A file with one
    candidate cannot ask a different question on
    its next visit, so "a later visit asks a different question" would be false
    as a universal -- what holds is that the *index* advances, which for a single
    candidate returns the same one. Pinned so that a future selector cannot
    quietly start skipping such a file to make the stronger sentence true.
    """
    generated = sweep_mutations.candidates(
        _PATH, "def fits(size):\n    return size < 3\n", on=_NIGHT
    )

    drawn = {generated.at_lap(lap) for lap in range(5)}

    assert len(generated.candidates) == 1
    assert drawn == {generated.candidates[0]}


def test_a_barren_file_offers_nothing_at_any_lap() -> None:
    """The block reports a barren slot; it must not raise or fabricate a pick.

    ``for_block`` turns this ``None`` into an attempt that buys no walk and a
    ``## Modules`` section saying the module offered nothing. An index error or a
    stray first candidate here would either kill the run or file a verdict about
    a mutation that was never generated.
    """
    generated = sweep_mutations.candidates(_PATH, "VALUE = 3\n", on=_NIGHT)

    assert generated.candidates == ()
    assert generated.at_lap(0) is None
    assert generated.at_lap(7919) is None
