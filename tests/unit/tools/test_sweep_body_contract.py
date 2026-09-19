"""The filed body is a contract with two ends, and both of them are here (#378).

``tools/sweep.py`` writes an issue body; ``tools/audit/sweep_kill_rates.py``
reads it back and folds a per-module kill rate out of it. There is no second
copy of that data -- the tracker is the ledger -- so the body's shape is an
interface between two modules that are edited months apart, and the failure mode
is silent in the worst direction: a reader that stops recognising a heading
reports *no verdicts for that module*, which renders as a module nobody has
swept rather than as an error. The audit exits 0 either way, by design.

So the pin is a **round trip**, not two parallel descriptions of the format. A
table of "what the heading looks like" agrees with whichever side wrote the
table. Building a payload with the filer and parsing it with the reader agrees
with neither unless they agree with each other.

Two rules of the reader's that a round trip alone would not reach are planted
explicitly, because both are about what must *not* come back: the run's control
walk (a statement about the run, and crediting it to a module would hand
whichever module sorts first a kill it did not earn) and an outcome nobody can
attribute (the shape of a harness that ran something else). Each is rendered
into the body and asserted absent from the parse.

**Why ``tests/unit`` and not ``tests/integration``.** Nothing here opens a file,
a socket or a subprocess: both ends are imported and the body is a string. The
tier is about I/O, so the cross-module reach does not move it -- what it does
move is the import, since ``tools/audit`` is a flat script directory that no
conftest in this tree puts on the path.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
import sweep_census
import sweep_filing
import sweep_mutations
import sweep_verdict

pytestmark = pytest.mark.unit

_NIGHT = date(2026, 9, 16)
_RUN = sweep_census.run_index(_NIGHT)

#: Paths and labels shaped to break the delimiting rather than to read well. The
#: census holds no such path today and the payload builder is not allowed to
#: depend on that -- and a value carrying a backtick is exactly where a writer
#: and a reader part company, because only one of them has to know about
#: CommonMark's padding rule.
_HOSTILE_PATH = "packages/theurian-core/src/theurian/``odd``/name.py"
_TRAILING_TICK_PATH = "packages/theurian-core/src/theurian/odd`"
_HOSTILE_LABEL = "sweep-2026-09-16-a1e446-00-lt-to-le-l7-``tick``"


def _kill_rates() -> ModuleType:
    """``tools/audit/sweep_kill_rates.py``, imported as the audit itself runs it.

    ``tools/audit/`` is a flat script directory whose modules import each other
    by bare name, which is what a script's own directory on ``sys.path[0]``
    gives them. Reproducing that here is what makes this the same module the
    audit runs rather than a copy of it -- and the module's own path insert is
    what then resolves ``sweep_filing``, so the reader under test is reading the
    writer under test.
    """
    audit = str(Path(sweep_census.REPO_ROOT) / "tools" / "audit")
    if audit not in sys.path:
        sys.path.insert(0, audit)
    import sweep_kill_rates

    return sweep_kill_rates


@pytest.mark.parametrize("verdict", sorted(sweep_filing.KNOWN_VERDICTS))
@pytest.mark.parametrize(
    "label",
    ("sweep-2026-09-16-a1e446-00-le-to-lt-l42", _HOSTILE_LABEL, "`leading", "trailing`"),
    ids=("plain", "fenced", "leading-tick", "trailing-tick"),
)
@pytest.mark.parametrize("summary", ("", "5571 passed", "1 failed -- `` weird ``"), ids=range(3))
def test_a_verdict_line_reads_back_as_the_verdict_and_label_it_was_written_from(
    verdict: str, label: str, summary: str
) -> None:
    """The line-level half of the contract, over every value the writer can emit.

    One product rather than one example: the writer's two devices -- a delimiter
    longer than any backtick run inside, and a space of padding when the value
    itself begins or ends with one -- only fire on values that need them, so a
    round trip pinned on a plain label exercises neither and agrees with a reader
    that knows about neither. The summary is varied because it is appended after
    the label and is repository text: a reader that swallowed too much of the
    line would come back with a label nobody handed the harness.

    The seconds field is not asserted. It is rendered for a human and the reader
    does not read it, which is a fact worth having written down rather than
    rediscovered by whoever changes its format.
    """
    line = sweep_filing.verdict_line(verdict, label, 512.5, summary)

    assert sweep_filing.parse_verdict_line(line) == (verdict, label)


def test_a_verdict_the_writer_does_not_recognise_survives_the_round_trip_as_a_code_span() -> None:
    """The field is read out of the harness's JSON, so it is not a closed set.

    A record written by a future harness -- or a corrupted one -- can carry any
    string there. The writer renders anything off the allow-list as a code span
    rather than as emphasis, and the reader admits a code span for exactly that
    reason: the alternative is a verdict that reads as prose to the reader and is
    dropped, which lowers a module's mutation count without lowering its kill
    count and makes the rate *better* than the evidence.
    """
    line = sweep_filing.verdict_line("FUTURE-VERDICT", "sweep-a", 1.0)

    assert "`FUTURE-VERDICT`" in line
    assert sweep_filing.parse_verdict_line(line) == ("FUTURE-VERDICT", "sweep-a")


@pytest.mark.parametrize(
    "line",
    (
        "- **Target:** `packages/theurian-core/src/theurian/x.py`",
        "- **Harness:** `uv run --frozen python tools/mutate.py --with-git`",
        "- **Commit:** `9f2c1ab`",
        "- **Run:** 2026-09-16 (run 105696)",
        "Census index 4, visit 4466, 3 candidate(s), 1 dropped for a non-unique anchor.",
    ),
)
def test_a_header_line_shaped_like_a_verdict_is_not_read_as_one(line: str) -> None:
    """Emphasis plus a code span is the body's header shape, not its verdict shape.

    Read loosely, three header lines count as three mutations for whichever
    module they precede, and a kill rate computed over them is wrong in the
    direction that looks like evidence -- more mutations, none of them killed.
    The writer's own rule is what separates them: plain emphasis only for a
    verdict on the allow-list, a code span otherwise, and a header label is
    neither.
    """
    assert sweep_filing.parse_verdict_line(line) is None


@pytest.mark.parametrize("path", (_HOSTILE_PATH, _TRAILING_TICK_PATH, "a/b/c.py"))
def test_a_module_heading_reads_back_as_the_path_it_was_written_from(path: str) -> None:
    """The heading is how a verdict finds its module, so a lossy round trip misfiles it.

    Not "the heading contains the path": a reader that recovered the padding, or
    the fence, would key the tally on a path no census entry matches, and the
    table would grow a row per hostile path while the real module showed nothing.
    """
    heading = sweep_filing.module_heading(path)

    found = sweep_filing.MODULE_HEADING_PATTERN.match(heading)

    assert found is not None
    assert found.group("path") == path


def _attempt(
    path: str, *, candidate: sweep_mutations.Candidate | None, index: int
) -> sweep_mutations.Attempt:
    return sweep_mutations.Attempt(
        slot=sweep_census.Slot(path=path, index=index, lap=4466),
        generated=sweep_mutations.Generated(
            path=path, candidates=() if candidate is None else (candidate,), skipped=()
        ),
        candidate=candidate,
    )


def _candidate(label: str, path: str) -> sweep_mutations.Candidate:
    return sweep_mutations.Candidate(
        label=label,
        path=path,
        old="        if used <= budget:",
        new="        if used < budget:",
        line=42,
        swapped_from="<=",
        swapped_to="<",
        anchor="line",
    )


#: One planted run carrying every case the reader has a rule for: a module whose
#: mutation was killed, one whose mutation survived, a barren member that offers
#: nothing, a control walk that belongs to the run, and an outcome whose label
#: matches no mutation this run handed over.
_KILLED_PATH = "packages/theurian-core/src/theurian/held.py"
_SURVIVED_PATH = _HOSTILE_PATH
_BARREN_PATH = "packages/theurian-core/src/theurian/constants.py"


def _planted_night() -> sweep_filing.Night:
    killed = _candidate("sweep-2026-09-16-aaaaaa-00-le-to-lt-l42", _KILLED_PATH)
    survived = _candidate(_HOSTILE_LABEL, _SURVIVED_PATH)
    return sweep_filing.Night(
        on=_NIGHT,
        run=_RUN,
        attempts=(
            _attempt(_KILLED_PATH, candidate=killed, index=4),
            _attempt(_SURVIVED_PATH, candidate=survived, index=5),
            _attempt(_BARREN_PATH, candidate=None, index=6),
        ),
        harness=("uv", "run", "--frozen", "python", "tools/mutate.py", "--with-git"),
        command=("uv", "run", "--frozen", "python", "tools/sweep.py", "--date", "2026-09-16"),
        mutate_exit=1,
        outcomes=(
            sweep_verdict.Outcome("__control__", "control-green", 903.2, "5571 passed"),
            sweep_verdict.Outcome(killed.label, "KILLED", 480.1, "1 failed"),
            sweep_verdict.Outcome(survived.label, "SURVIVED", 512.5, "5571 passed"),
            sweep_verdict.Outcome("sweep-from-another-spec", "SURVIVED", 12.0, "orphaned"),
        ),
        reading=sweep_verdict.Reading(
            reason=sweep_verdict.SURVIVORS,
            detail="1 of 2 mutation(s) the suite does not hold",
            unheld=(survived.label,),
        ),
        commit="9f2c1ab4d5e6f70819a2b3c4d5e6f7089a1b2c3d",
    )


def test_the_kill_rate_reader_recovers_exactly_what_the_filer_wrote_about_each_module() -> None:
    """The mutual pin: one body, written by the filer, parsed by the audit.

    This is the one check that fails when the two ends part company. Each side
    has its own tests and both stay green through a heading change -- the writer
    writes the new shape, the reader looks for the old one, and every module's
    verdicts quietly stop being counted. The audit then prints a shorter table,
    exits 0, and reads as a repository whose modules have not been swept.

    Asserted as set equality over ``(path, label, verdict)`` rather than as a
    count: a reader that recovered two verdicts and attributed both to the same
    module has the right count and the wrong answer, and attributing one module's
    miss to another is how a kill rate points a reader at the wrong tests.
    """
    night = _planted_night()

    parsed = _kill_rates().parse_body(sweep_filing.build_payload(night).body)

    assert {(item.path, item.label, item.verdict) for item in parsed} == {
        (_KILLED_PATH, "sweep-2026-09-16-aaaaaa-00-le-to-lt-l42", "KILLED"),
        (_SURVIVED_PATH, _HOSTILE_LABEL, "SURVIVED"),
    }


def test_the_reader_credits_no_module_with_the_runs_control_or_an_orphan_verdict() -> None:
    """The two outcomes that are in the body and must not be in the tally.

    The control is a statement about the run: counted as a module's verdict it
    is a ``control-green`` folded in as a kill, and the module that sorts first
    collects it. An orphan -- an outcome whose label matches no mutation this run
    handed over -- is the shape of a harness that ran something else, and
    crediting it invents a mutation for a module that was never asked.

    Both are asserted by *label*, against the body that carries them, so the
    check is that the reader dropped them rather than that the writer omitted
    them -- the writer is asserted to have written them in the same breath.
    """
    body = sweep_filing.build_payload(_planted_night()).body

    parsed = _kill_rates().parse_body(body)

    assert "__control__" in body and "sweep-from-another-spec" in body
    assert "__control__" not in {item.label for item in parsed}
    assert "sweep-from-another-spec" not in {item.label for item in parsed}


def test_an_unattributed_section_below_a_module_is_still_not_that_modules_verdict() -> None:
    """The reader's rule, pinned where the writer's current layout cannot reach it.

    Today the filer renders ``## Unattributed verdicts`` above ``## Modules``, so
    the round trip above cannot tell a reader that stops at any ``##`` from one
    that stops at nothing. Move the section below the modules -- an ordinary
    editorial change to a body nobody would think of as a parser input -- and the
    weaker reader credits every orphan to the last module it saw.

    Hand-built for that reason: the body below is the one the writer does not
    produce yet, which is exactly the one worth pinning before it exists.
    """
    body = "\n".join(
        (
            sweep_filing.MODULES_HEADING,
            "",
            sweep_filing.module_heading(_KILLED_PATH),
            "",
            sweep_filing.verdict_line("KILLED", "sweep-held", 1.0),
            "",
            sweep_filing.UNATTRIBUTED_HEADING,
            "",
            sweep_filing.verdict_line("SURVIVED", "sweep-orphan", 2.0),
        )
    )

    parsed = _kill_rates().parse_body(body)

    assert [(item.path, item.label) for item in parsed] == [(_KILLED_PATH, "sweep-held")]


#: The pre-block per-file body, in the shape the five filed ones actually have.
#:
#: Read off issue #742 rather than imagined: a ``- **Target:**`` header, a
#: ``## Verdicts`` list carrying the control, a ``## Mutations`` section whose
#: ``###`` headings are *labels* rather than paths, and each mutation's diff.
#: The orphan section is the reviewer's reproduction -- it is what a legacy run
#: that could not attribute an outcome would have written, and what the reader
#: credited to the header target for the whole document.
_LEGACY_TARGET = "packages/theurian-core/src/theurian/infrastructure/sqlite/index_store.py"
_LEGACY_SURVIVED = "sweep-2026-09-17-00-false-to-true-l307"
_LEGACY_KILLED = "sweep-2026-09-17-03-false-to-true-l396"


def _legacy_body(*, tail: tuple[str, ...] = ()) -> str:
    """One pre-block body, optionally continued past its ``## Mutations`` section."""
    return "\n".join(
        (
            "<!-- async-sweep-target: 7f3a1c -->",
            "",
            "The scheduled red-team sweep over `main` (#378) did not come back clean.",
            "",
            f"- **Target:** `{_LEGACY_TARGET}`",
            "- **Night:** 2026-09-17",
            "- **Harness:** `uv run --frozen python tools/mutate.py --with-git`",
            "",
            "## Verdicts",
            "",
            sweep_filing.verdict_line("control-green", "__control__", 875.3, "7681 passed"),
            sweep_filing.verdict_line("SURVIVED", _LEGACY_SURVIVED, 874.6, "7681 passed"),
            sweep_filing.verdict_line("KILLED", _LEGACY_KILLED, 19.3, "1 failed"),
            "",
            "## Unattributed verdicts",
            "",
            sweep_filing.verdict_line("SURVIVED", "sweep-from-another-spec", 12.0, "orphaned"),
            "",
            "## Mutations",
            "",
            f"### `{_LEGACY_SURVIVED}`",
            "",
            "Line 307, `False` to `True`, anchored on the line.",
            "",
            "```diff",
            "-            return False",
            "+            return True",
            "```",
            *tail,
        )
    )


def test_a_legacy_bodys_orphan_section_is_not_credited_to_its_header_target() -> None:
    """The twin the round's HIGH asked for: the same claim, the other shape.

    The reader's section rule was written against the per-run shape, where
    leaving ``## Modules`` clears the owner. A legacy body never enters that
    region, so the owner fell back to the header's target for the *whole
    document* -- and an outcome under ``## Unattributed verdicts``, which exists
    precisely because nobody could attribute it, was counted against that module.
    It lands as an extra unheld mutation, so the module's rate falls: wrong in
    the direction that reads as a finding, in the table a human uses to choose
    where to look next.

    Asserted as the exact set, not as "the orphan is absent". A reader that
    dropped the orphan *and* the control's neighbours would satisfy an absence
    check while reporting a module that had been swept as one that had not.
    """
    parsed = _kill_rates().parse_body(_legacy_body())

    assert {(item.path, item.label, item.verdict) for item in parsed} == {
        (_LEGACY_TARGET, _LEGACY_SURVIVED, "SURVIVED"),
        (_LEGACY_TARGET, _LEGACY_KILLED, "KILLED"),
    }


def test_the_table_a_human_reads_shows_the_legacy_targets_own_two_verdicts() -> None:
    """The other end of the same twin: the fold, not the parse.

    ``parse_body`` is where the rule lives and ``tally`` is where anybody sees
    it, and the two can disagree -- a verdict dropped correctly at the parse can
    still be counted twice by a fold keyed on the wrong field. The numbers are
    pinned rather than the row's existence: with the orphan miscredited the rate
    is 1 killed of 3 rather than of 2, which is the difference between a module
    that holds half its mutations and one that holds a third.
    """
    rows = _kill_rates().tally([{"number": 742, "body": _legacy_body()}])

    assert set(rows) == {_LEGACY_TARGET}
    row = rows[_LEGACY_TARGET]
    assert (row.mutations, row.killed, row.unheld, row.rate) == (2, 1, 1, 0.5)


@pytest.mark.parametrize(
    "heading", ("## Reproduce", "## Proposed automation", "## Notes from triage")
)
def test_a_verdict_under_any_later_heading_of_a_legacy_body_attributes_to_nothing(
    heading: str,
) -> None:
    """The region rule's consequence, generalised past the one heading that caused it.

    ``## Unattributed verdicts`` is the section the defect was found through, and
    fixing that name alone would leave the class open: a triager pasting a verdict
    line under ``## Notes from triage``, or a future filer adding a section, would
    walk straight back into it. What the reader holds now is a *region* -- it
    opens at the heading its shape names and closes at the next ``##`` -- so any
    heading below is outside, whatever it is called.
    """
    body = _legacy_body(
        tail=("", heading, "", sweep_filing.verdict_line("SURVIVED", "sweep-late", 3.0))
    )

    parsed = _kill_rates().parse_body(body)

    assert "sweep-late" in body
    assert "sweep-late" not in {item.label for item in parsed}


#: A source line that renders, inside a ``diff`` block, as a verdict line: the
#: leading space becomes the diff's own ``-`` marker. Measured by the ci
#: specialist over the 142-module census -- 0 lines render this way today, so the
#: guard is against the rotating corpus rather than against this week's tree.
_SMUGGLED_LABEL = "sweep-smuggled-by-a-source-line"
_SMUGGLED_SOURCE = f" **KILLED** `{_SMUGGLED_LABEL}` (1.0s) or flag"


def test_a_verdict_shaped_source_line_inside_a_diff_is_not_a_verdict() -> None:
    """The body quotes repository source, and source is not evidence about itself.

    A mutation's diff is rendered inside the module's own section, so a removed
    line that reads as a verdict lands exactly where an attributed one would --
    the reader credits that module with a KILLED nobody ran, and a module's rate
    rises on the strength of its own source text. The whole shape is one leading
    space: the diff's ``-`` marker supplies the rest.

    Built through the filer rather than hand-written, because the claim is about
    what the real rendering does with a real candidate: measured, the rendered
    line parses as ``('KILLED', 'sweep-smuggled-by-a-source-line')`` the moment
    it is read outside its fence.
    """
    smuggler = sweep_mutations.Candidate(
        label="sweep-2026-09-16-bbbbbb-00-or-to-and-l9",
        path=_KILLED_PATH,
        old=_SMUGGLED_SOURCE,
        new=_SMUGGLED_SOURCE.replace(" or ", " and "),
        line=9,
        swapped_from="or",
        swapped_to="and",
        anchor="line",
    )
    night = sweep_filing.Night(
        on=_NIGHT,
        run=_RUN,
        attempts=(_attempt(_KILLED_PATH, candidate=smuggler, index=4),),
        harness=("uv", "run", "python", "tools/mutate.py"),
        command=("uv", "run", "python", "tools/sweep.py"),
        mutate_exit=1,
        outcomes=(
            sweep_verdict.Outcome("__control__", "control-green", 903.2, ""),
            sweep_verdict.Outcome(smuggler.label, "SURVIVED", 512.5, ""),
        ),
        reading=sweep_verdict.Reading(
            reason=sweep_verdict.SURVIVORS, detail="1 of 1", unheld=(smuggler.label,)
        ),
    )

    body = sweep_filing.build_payload(night).body
    parsed = _kill_rates().parse_body(body)

    assert f"- **KILLED** `{_SMUGGLED_LABEL}`" in body
    assert [(item.path, item.label) for item in parsed] == [(_KILLED_PATH, smuggler.label)]


def test_a_legacy_bodys_inlined_diff_cannot_smuggle_a_verdict_into_its_verdict_region() -> None:
    """The same smuggle where only the fence rule can refuse it.

    Under ``## Mutations`` two rules refuse it independently -- the region closed
    at that heading, and the fence -- so a body shaped like the filed ones cannot
    separate them. Inside ``## Verdicts`` the region is open and the fence is the
    only thing standing between repository source and a module's kill rate, which
    is why the diff is inlined there: it is the arrangement that measures the
    rule rather than the arrangement that happens to be filed.

    The verdict above the block is the positive control. Without it a reader that
    swallowed the whole region would pass by reporting nothing at all.
    """
    body = "\n".join(
        (
            f"- **Target:** `{_LEGACY_TARGET}`",
            "",
            "## Verdicts",
            "",
            sweep_filing.verdict_line("KILLED", _LEGACY_KILLED, 19.3),
            "",
            "```diff",
            f"-{_SMUGGLED_SOURCE}",
            f"+{_SMUGGLED_SOURCE.replace(' or ', ' and ')}",
            "```",
        )
    )

    parsed = _kill_rates().parse_body(body)

    assert [(item.path, item.label) for item in parsed] == [(_LEGACY_TARGET, _LEGACY_KILLED)]


def test_a_shorter_backtick_run_inside_a_longer_fence_does_not_close_it() -> None:
    """CommonMark's closing rule, and the filer sizes its fences by it.

    ``sweep_filing._block`` opens with one backtick more than the longest run in
    the text, precisely so a diff containing three can be carried by four. A
    reader that treated any fence line as a toggle would end the block at that
    inner run and hand the rest of the diff back as prose -- which is where the
    smuggled line sits.

    The trailing verdict is the positive control, and it is what separates this
    from the rule below: the block really does close at the four-backtick line,
    so a reader that simply swallowed everything after an opener is not what is
    being pinned here.
    """
    body = "\n".join(
        (
            "## Modules",
            "",
            sweep_filing.module_heading(_KILLED_PATH),
            "",
            "````diff",
            "```",
            f"-{_SMUGGLED_SOURCE}",
            "````",
            "",
            sweep_filing.verdict_line("SURVIVED", "sweep-after-the-block", 2.0),
        )
    )

    parsed = _kill_rates().parse_body(body)

    assert [(item.path, item.label) for item in parsed] == [(_KILLED_PATH, "sweep-after-the-block")]


def test_an_unclosed_fence_swallows_the_rest_of_the_body_rather_than_handing_it_back() -> None:
    """The safe direction, chosen deliberately, and worth pinning as the choice it is.

    A body whose fence never closes is malformed, and the two readings are: treat
    the remainder as prose, or treat it as fenced. Treating it as prose is the
    reading that lets the quoted source underneath be counted as verdicts, which
    is the failure this whole rule exists to prevent -- so an unclosed fence
    costs the verdicts below it and the audit reports one issue short rather than
    one issue's source text as evidence.

    The verdict above the opener is the positive control: it is what makes this a
    claim about the remainder rather than about the reader having given up.
    """
    body = "\n".join(
        (
            "## Modules",
            "",
            sweep_filing.module_heading(_KILLED_PATH),
            "",
            sweep_filing.verdict_line("KILLED", "sweep-before-the-fence", 1.0),
            "",
            "```diff",
            f"-{_SMUGGLED_SOURCE}",
            "",
            sweep_filing.verdict_line("SURVIVED", "sweep-after-the-fence", 2.0),
        )
    )

    parsed = _kill_rates().parse_body(body)

    assert [(item.path, item.label) for item in parsed] == [
        (_KILLED_PATH, "sweep-before-the-fence")
    ]


def test_a_body_the_reader_cannot_recognise_yields_nothing_rather_than_raising() -> None:
    """A hand-edited issue is not a reason for a reporting tool to stop reporting.

    ``sweep_kill_rates`` folds one table over every issue under the label, and a
    body somebody rewrote by hand -- or an issue filed before the block form,
    which the reader handles by its own legacy rule -- must cost that issue's
    rows and no more. Raising here would empty the whole table on the strength of
    one malformed member.
    """
    assert _kill_rates().parse_body("## Modules\n\nnothing shaped like a verdict here") == ()
    assert _kill_rates().parse_body("") == ()
