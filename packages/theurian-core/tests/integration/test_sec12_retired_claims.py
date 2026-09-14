"""#669's corrections must not be undone in silence (ADR-0031 Amendment 1).

Its sibling ``test_sec12_record_figures.py`` holds what the records *print*
against what the code computes. This file holds what they must no longer
*claim* -- a different failure, because a retired claim is prose and has no live
constant to disagree with. Both were corrections to the same records, and both
were unpinned until now: ADR-0031's own addendum says *"What is not yet enforced
is that they stay that way ... Until that lands, a revert of these corrections
is silent."*

**Two claims, retired at two different moments, refused two different ways.**

* **That the render budget cannot be reached over the shipped transport.** T-11
  said ``MAX_PARAMS_RENDERED_CHARS`` *"is unreachable over the shipped
  transport"*, that ``build_app`` called the SDK *"without
  ``max_request_body_size``"*, and that *"the reconciliation is owed"*. All
  three are now false, and none survives anywhere as a corrected quotation -- so
  this one is refused outright, keyed to the render budget as its subject.
* **Three figures that were measurements over a favourable instance.** The
  all-ASCII memory row read as the per-request cost; the interim cap's
  53,476,811 read as the shipped cap's; *"charges every leaf what ``repr``
  renders it as"* read as a universal over a charge that returned zero for two
  leaf types. These are **not** refused outright, and that is the whole design
  of the second rule: each survives in the tree on purpose, inside a sentence
  naming what it was measured over. Delete the sentence and the class goes with
  it, which is how the same mistake gets made a third time. So what is refused
  is the figure standing *unattributed* -- which is what a half-finished revert
  leaves, and what a fresh reassertion looks like.

**Every absence rule here carries its own positive control**, driven against the
text it guards. An arm asserting that a key matches nothing is green against a
key that has stopped matching -- a narrowed regex, a figure re-spelled without
its commas, a normalisation that stopped folding a wrap -- and it is green most
convincingly at exactly that moment.

**Reach, and it differs by arm.** The *figure* arms read three records --
T-11's residual paragraph, ADR-0031's Amendment 1 and the CHANGELOG's #669
entries, through ``sec12_records``. The *new-dress* arm reads five: those three
plus ``daemon/server.py``'s ``MAX_REQUEST_BODY_BYTES`` and
``mcp/validation.py``'s ``MAX_PARAMS_RENDERED_CHARS``, which carry no retired
figure and never should but do state the universal in its corrected wording --
so the subject half of that key is live in each, and they are where the next
dress is most likely to land. :data:`_FIGURE_RECORDS` and
:data:`_UNIVERSAL_RECORDS` are the two corpora, and each names its own reason.

Out of both: the roadmap's SEC-12 cell, which carries no #669 figure since the
reconciliation left its *owed* list, and **T-6** -- which the round-two brief
named and which measurably carries none of these: ``roughly 3.0x``,
``53,476,811``, ``4.25x`` and the retired universal appear in it zero times.
Including it would look like a record held and be an empty read, the reason
``test_sec12_shipped_claims.py``'s ``_REASSERTION_RECORDS`` gives for leaving
``schemas/README.md`` out of its own sweep. This module's own prose is out too,
and for a mechanism reason rather than an empty-read one: a round-4 reviewer
drove self-inclusion and it reports seven windows, four of them the regex source
literals themselves. Excluding a key's own definition from its sweep needs a
mechanism that does not exist here, and it is
https://github.com/theurian/theurian/issues/697's to build.

Two files read as text. No database, no socket, nothing written anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

import pytest
from sec12_records import amendment_one, changelog_entries, t11_residual_paragraph
from threat_model_claims import pairings, prose
from write_lock_claims import REPO_ROOT

from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.mcp.validation import MAX_PARAMS_RENDERED_CHARS

pytestmark = pytest.mark.integration

#: How far apart a subject and a phrasing may sit before they stop being one
#: claim. ``test_sec12_shipped_claims.py``'s own figure, carried here for the
#: reason it chose it: the retired passages put their phrasing before the
#: subject as often as after.
_REACH_CHARS: Final = 240

#: What names the render budget as the subject of a sentence. Three spellings
#: because the retired residual named it by constant and the shipped one also
#: names it by what it bounds.
_RENDER_BOUND_SUBJECT: Final = re.compile(
    r"max_params_rendered_chars|rendered-character bound|render budget",
)

#: What a sentence reaches for to say the two caps were never reconciled -- that
#: the render bound cannot be met over the shipped transport, or that the
#: reconciliation is still owed. Keyed off the retired residual's own three
#: clauses, which said it three ways in one paragraph.
#:
#: **Deliberately narrower than the sibling module's `_UNSHIPPED` key**, and for
#: a reason that is the opposite of that key's. The shipped residual paragraph
#: still talks about reachability at length -- it opens *"no longer the
#: reachability of
#: ``MAX_PARAMS_RENDERED_CHARS``, which is settled"* and closes that clause with
#: *"reachable in the shipped default configuration"* -- so an over-approximation
#: keyed on ``reachab`` or on the SDK's ``4 MiB`` figure (which the corrected
#: sentence quotes, to say what the cap is derived *instead of*) would be RED
#: against the record it is meant to protect. ``unreachable`` matches neither
#: ``reachable`` nor ``reachability``, which is what makes it usable here.
_UNRECONCILED: Final = re.compile(
    r"is unreachable|unreachable over|unreachable in"
    r"|without max_request_body_size"
    r"|reconciliation is owed|reconciliation of|reconciliation with",
)


#: The three figures round two corrected, each of which survives in the tree
#: *as a corrected claim* and must not come back as a live one. Keyed on the
#: figure itself rather than on a subject, because each is unmistakable: no
#: sentence in these records reaches for ``53,476,811`` about anything else.
#:
#: **A verbatim key only catches the claim in the dress it was retired in**,
#: which is why :data:`_REWORDED_UNIVERSAL_SUBJECT` and
#: :data:`_REWORDED_UNIVERSAL_PHRASING` sit beside it. The numbers here are safe
#: to key verbatim -- a figure re-spelled is a different figure -- but the
#: universal is a *sentence*, and a sentence has as many dresses as an editor
#: wants. One was in the tree when this key was written, and this key walked
#: straight past it.
_RETIRED_FIGURES: Final = {
    "the-universal-charge-claim": re.compile(r"every leaf what repr renders it as"),
    "the-ascii-only-memory-row": re.compile(r"roughly 3\.0x"),
    "the-interim-cap-render": re.compile(r"53,476,811"),
    "the-interim-cap-ratio": re.compile(r"4\.25x"),
    "the-interim-cap-widening": re.compile(r"3\.2x"),
}

#: What marks a retired figure as retired. Three families, because the records
#: retire in three registers: the figure was *recorded as* something, it was
#: measured against the *interim* cap, or it was *withdrawn*/*replaced*. An
#: over-approximation, deliberately -- the cost of a false pass is a figure that
#: reads as attributed, and the cost of a false failure is a record that cannot
#: say what it corrected.
_ATTRIBUTION: Final = re.compile(
    r"interim|recorded as|earlier draft|withdrew|withdrawn|replaced|what the record said",
)

#: How far an attribution may sit from the figure it attributes. Measured
#: 2026-09-15 across all three records: every one of the **ten** live
#: occurrences carries its attribution within **116** characters, the worst
#: being the CHANGELOG's *"that reproduction ran against the interim body cap"*
#: clause. Set well above that so an ordinary re-wrap does not redden it, and
#: far below the 17,012 characters of Amendment 1 so an unattributed
#: reassertion elsewhere in the same record is still caught.
#:
#: (Eight was this comment's first count, taken over two records before the
#: CHANGELOG joined the corpus. The census below now derives the number rather
#: than restating it.)
_ATTRIBUTION_REACH: Final = 200

#: The retired universal in any dress: a subject naming the population, a
#: phrasing equating the charge to what ``repr`` renders. The verbatim key above
#: catches only the sentence Amendment 1 retired; this catches a restatement,
#: which is not hypothetical: the CHANGELOG's own *Fixed* heading carried one
#: until #685's docs stage (*"is charged what ``repr`` actually renders, for
#: every leaf"* -- what a reader of that entry would have taken away as the
#: shipped guarantee), and the verbatim key walked straight past it. The
#: quotation survives here as the specimen the key was built from, not as a
#: description of the record's current contents.
#:
#: The shipped wording -- *"at least the number of characters that leaf
#: contributes to the render"* -- pairs with neither half, which is the property
#: that makes this key usable rather than an over-approximation that reddens the
#: record it protects. Checked by
#: :func:`test_the_reworded_key_passes_over_the_shipped_wording`.
_REWORDED_UNIVERSAL_SUBJECT: Final = re.compile(r"every leaf|each leaf")
_REWORDED_UNIVERSAL_PHRASING: Final = re.compile(
    r"what repr renders|as repr renders|repr renders it as|repr actually renders",
)

#: The records the *figure* arms range over, each read at call time.
#:
#: The two production docstrings that carry #669's decision -- ``daemon/server.py``'s
#: ``MAX_REQUEST_BODY_BYTES`` and ``mcp/validation.py``'s
#: ``MAX_PARAMS_RENDERED_CHARS`` -- are deliberately **not** here. Measured
#: 2026-09-15, neither quotes any of the five retired figures, and neither
#: should: they describe the charge that ships, not the reproduction that was
#: filed under the wrong cap. A record with none of the keys is an empty read,
#: and an absence arm over an empty read is the vacuity ``_REASSERTION_RECORDS``
#: records for ``schemas/README.md``.
_FIGURE_RECORDS: Final[dict[str, Callable[[], str]]] = {
    "threat-model T-11 residual": t11_residual_paragraph,
    "ADR-0031 Amendment 1": amendment_one,
    "CHANGELOG #669 entries": lambda: changelog_entries("#669"),
}

#: The records the *reworded universal* arm ranges over -- the three above plus
#: the two production docstrings, and the difference is not an inconsistency.
#:
#: A figure key over those docstrings reads nothing, because they carry no
#: figure. The universal is the opposite case: both state it, in the corrected
#: wording, so the subject half of the key is live in each and a restatement
#: would be caught rather than passed over. They are also where a restatement is
#: most likely to land, being the constants' own prose -- the place a reader goes
#: to learn what the charge guarantees.
_UNIVERSAL_RECORDS: Final[dict[str, Callable[[], str]]] = {
    **_FIGURE_RECORDS,
    "daemon/server.py's MAX_REQUEST_BODY_BYTES": lambda: (
        REPO_ROOT / "packages/theurian-core/src/theurian/daemon/server.py"
    ).read_text(encoding="utf-8"),
    "mcp/validation.py's MAX_PARAMS_RENDERED_CHARS": lambda: (
        REPO_ROOT / "packages/theurian-core/src/theurian/mcp/validation.py"
    ).read_text(encoding="utf-8"),
}


def _unreconciled_reassertions(text: str) -> list[str]:
    """Every place *text* pairs the render bound with an unreconciled phrasing."""
    return pairings(text, _RENDER_BOUND_SUBJECT, _UNRECONCILED, _REACH_CHARS)


def _unattributed_windows(text: str, key: re.Pattern[str]) -> list[str]:
    """Every place *text* states ``key`` with no attribution within reach.

    **One implementation, used by the arm and by its own control.** An earlier
    draft had the control drive the *key* directly -- ``key.finditer`` over a
    stripped record -- while the arm drove key-plus-reach. So the control proved
    the regex still matched and proved nothing about the arithmetic deciding
    whether a match counts, which is the half that silently widens or narrows
    every rule built on it. A control that does not run the code under test is
    not a control.
    """
    normalised = prose(text)
    marks = [mark.span() for mark in _ATTRIBUTION.finditer(normalised)]

    return [
        normalised[max(0, hit.start() - 60) : hit.end() + 60]
        for hit in key.finditer(normalised)
        if not any(
            max(start - hit.end(), hit.start() - end, 0) <= _ATTRIBUTION_REACH
            for start, end in marks
        )
    ]


def test_the_t11_residual_no_longer_says_the_render_bound_is_unreachable() -> None:
    """RED means the residual went back to calling a reachable bound unreachable.

    Until #669, T-11's residual said ``MAX_PARAMS_RENDERED_CHARS`` *"is
    unreachable over the shipped transport"*, because ``build_app`` called
    ``streamable_http_app`` with no ``max_request_body_size`` and the SDK's own
    4 MiB default answered ``413`` before any MCP framing existed -- so the
    reconciliation was owed. Both halves are now false: the transport cap is
    derived and passed, and ``_rendered_width`` charges **every leaf at least the
    number of characters that leaf contributes to the render**, so the budget is
    held by the charge at any transport cap rather than by the caps' ordering.

    **That wording is load-bearing, and this module got it wrong once.** The
    sentence above used to read *"charges every leaf what ``repr`` renders it
    as"* -- which is the retired universal :data:`_RETIRED_FIGURES` refuses two
    arms down, restated as fact inside the file whose subject is refusing it.
    It is false in two places: a container is charged zero by design, and the
    ``bytes`` arm charges its whole repr including the ``b''`` delimiters. A
    pin that carries the claim it guards cannot notice the claim coming back.

    The failure this arm exists for is that correction being undone -- by a
    revert, by a merge resolved the wrong way, or by somebody re-deriving the
    old sentence from an older record. It is the same failure shape
    :func:`test_no_record_says_sec_12_is_unimplemented_again` guards one claim
    up, and it costs the same way: a security record saying a recorded bound
    cannot be reached is read as an admitted gap by a reviewer and as an open
    slice by a planner.

    **Keyed on this paragraph, not on every record that once carried the
    claim.** ``docs/roadmap.md``'s SEC-12 *owed* cell said it too -- *"the
    reconciliation of ``MAX_PARAMS_RENDERED_CHARS`` with the transport's own
    4 MiB body cap"* -- and its corrected cell names no render-bound subject at
    all, leading with #691's ``maxLength`` unit question instead. A
    parametrization that included it would look like two records held and be
    one, which is the reasoning ``test_sec12_shipped_claims.py``'s
    ``_REASSERTION_RECORDS`` already records for ``schemas/README.md``.

    **The subject premise is what stops this passing on an empty read**, exactly
    as in the arm above: a slice that stopped finding the paragraph reports a
    clean record whatever the document says.
    """
    paragraph = t11_residual_paragraph()

    assert _RENDER_BOUND_SUBJECT.search(prose(paragraph)), (
        f"T-11's residual paragraph names no render-bound subject at all, so the scan "
        f"below has nothing to pair an unreconciled phrasing with and would report a "
        f"clean record whatever it said. Either the slice is reading the wrong bytes or "
        f"the paragraph stopped being about the bound it records a residual "
        f"for:\n\n{paragraph[:400]}"
    )

    reassertions = _unreconciled_reassertions(paragraph)

    assert not reassertions, (
        f"T-11's residual says the render bound is unreachable, or that the two caps "
        f"are unreconciled, within {_REACH_CHARS} characters of naming it:\n"
        + "".join(f"\n  ...{window}..." for window in reassertions)
        + f"\n\nBoth were closed by #669. `build_app` passes "
        f"`MAX_REQUEST_BODY_BYTES` ({MAX_REQUEST_BODY_BYTES} bytes), and "
        f"`mcp/validation.py`'s `_rendered_width` charges every leaf at least the "
        f"number of characters that leaf contributes to the render, so the "
        f"{MAX_PARAMS_RENDERED_CHARS}-character render budget is held by the charge "
        f"rather than by the caps' ordering and its bounded refusal is reachable in the "
        f"shipped default configuration. If the sentence is about some *other* bound "
        f"that genuinely cannot be met, key it to that bound so it no longer reads as a "
        f"claim about this one."
    )


@pytest.mark.parametrize("record", sorted(_FIGURE_RECORDS))
@pytest.mark.parametrize("figure", sorted(_RETIRED_FIGURES))
def test_a_retired_figure_never_stands_without_the_instance_it_was_taken_over(
    figure: str, record: str
) -> None:
    """RED means a corrected figure came back as a live one.

    Round two found three faces of one class: a measurement over a favourable
    instance, written down as a bound. The all-ASCII memory row read as the
    per-request cost; the interim cap's 53,476,811 read as the shipped cap's;
    and *"charges every leaf what ``repr`` renders it as"* read as a universal
    over a charge that returned zero for two leaf types.

    **Deleting those figures is not the fix, and this arm is built around
    that.** Each survives in the tree on purpose, inside a sentence that says
    what it was measured over -- lose the sentence and the class goes with it,
    which is how the same mistake gets made again. So what is refused is the
    figure standing *alone*: present with no attribution within
    :data:`_ATTRIBUTION_REACH` characters, which is what a half-finished revert
    leaves and what a fresh reassertion looks like.

    Absent is fine too, and deliberately so. A record that never quoted the
    figure has nothing to attribute, and requiring the quotation would pin
    narrative rather than numbers.
    """
    unattributed = _unattributed_windows(_FIGURE_RECORDS[record](), _RETIRED_FIGURES[figure])

    assert unattributed == [], (
        f"the {record} states {figure} with no attribution within {_ATTRIBUTION_REACH} "
        f"characters:\n"
        + "".join(f"\n  ...{window}..." for window in unattributed)
        + "\n\nEach of these was a measurement over one instance, recorded as a bound. It "
        "may appear in a record -- the correction is worth more than the number -- but only "
        "inside a sentence naming what it was taken over: the interim `2 *` cap for the "
        "render figures, the all-ASCII row for the memory one, the two leaf types the "
        "charge missed for the universal."
    )


def test_every_retired_figure_is_reported_when_its_attribution_is_taken_away() -> None:
    """The premise under the arm above: the rule can still fire, in every record.

    Five keys times three records, and an absence arm is green against any pair
    whose key has stopped matching -- a tightened regex, a figure re-spelled
    without its commas, a normalisation that stopped folding a wrap. It is green
    most convincingly at exactly that moment.

    So every record is stripped of its attributions and run back through
    :func:`_unattributed_windows` -- the same function the arm calls, reach
    arithmetic included -- and every figure that record carries must come back
    reported. Driving the bare key instead would leave the reach untested, which
    is what an earlier draft did.

    **Every record, not just Amendment 1.** The CHANGELOG carries two of these
    figures and the earlier control never read it, so its values were unpinned:
    changing its ``53,476,811`` to ``53,476,812`` was driven and survived. A
    record's figures are checked here exactly when that record is swept here.
    """
    unreported = [
        f"{record}/{figure}"
        for record, read in sorted(_FIGURE_RECORDS.items())
        for figure, key in sorted(_RETIRED_FIGURES.items())
        if key.search(prose(read()))
        and not _unattributed_windows(_ATTRIBUTION.sub("(removed)", read()), key)
    ]

    assert unreported == [], (
        f"these record/figure pairs carry the figure and are still reported clean with every "
        f"attribution removed: {unreported}. The arm above is green for them whatever the "
        "record says -- the key was narrowed, the reach arithmetic stopped pairing, or the "
        "figure is spelled some other way now"
    )


def test_the_census_of_live_retired_figures_is_the_one_the_reach_was_measured_over() -> None:
    """The population behind the reach constant, derived rather than restated.

    :data:`_ATTRIBUTION_REACH` is justified by a measurement over the live
    occurrences -- "every one of the ten carries its attribution within 116
    characters" -- and that sentence was written with eight, taken before the
    CHANGELOG joined the corpus. A justification quoting a population is only
    worth what the population is, so the count is recomputed here and the
    *margin* is asserted rather than the prose.

    What this pins is that the reach is not sitting just above the worst live
    case by luck: the widest gap any live figure needs stays well inside the
    reach, so a re-wrap does not redden the arm and a genuine reassertion still
    fails it.
    """
    gaps = []
    for read in _FIGURE_RECORDS.values():
        normalised = prose(read())
        marks = [mark.span() for mark in _ATTRIBUTION.finditer(normalised)]
        for key in _RETIRED_FIGURES.values():
            for hit in key.finditer(normalised):
                gaps.append(
                    min(max(start - hit.end(), hit.start() - end, 0) for start, end in marks)
                )

    assert gaps, (
        "no record carries any retired figure, so the attribution arm has nothing to hold "
        "and the reach constant is justified by an empty measurement"
    )
    assert max(gaps) <= _ATTRIBUTION_REACH - 50, (
        f"the widest gap a live retired figure needs is {max(gaps)} characters against a "
        f"reach of {_ATTRIBUTION_REACH}, over {len(gaps)} occurrences -- less than the "
        f"50 characters of margin this arm requires. Measured 2026-09-15 the worst was 116, "
        f"leaving 84. The reach is no longer comfortably above the population it was sized "
        f"for, so an ordinary re-wrap will start reddening the attribution arm: re-measure "
        f"and re-record rather than widening the constant until it stops complaining"
    )


def test_the_reworded_key_passes_over_the_shipped_wording() -> None:
    """The premise under the reworded-restatement arm: it is not an over-approximation.

    A key broad enough to catch any dress is a key that reddens the corrected
    record, and then the only way out is to narrow it back to the dress it was
    written for. So the *shipped* sentence is driven through the same pairing
    the arm uses and must come back clean, and the retired sentence must come
    back reported -- both halves, because either alone is satisfied by a key
    that matches everything or nothing.
    """
    shipped = (
        "`_rendered_width` charges every leaf at least the number of characters that "
        "leaf contributes to the render, float and None included."
    )
    bare = "`_rendered_width` charges every leaf what `repr` renders it as."
    quoted = 'the render charge, recorded as charging "every leaf what `repr` renders it as"'

    assert pairings(
        bare, _REWORDED_UNIVERSAL_SUBJECT, _REWORDED_UNIVERSAL_PHRASING, _REACH_CHARS
    ), (
        "the key does not pair on the retired sentence stated bare, so it guards nothing and "
        "a restatement in any dress walks past it"
    )
    assert not pairings(
        shipped, _REWORDED_UNIVERSAL_SUBJECT, _REWORDED_UNIVERSAL_PHRASING, _REACH_CHARS
    ), (
        "the key pairs on the *shipped* wording. It would redden every record that states "
        "the correction, and the only repair a reader would find is to narrow it back to the "
        "one dress it was written for"
    )
    assert not _reworded_universal(quoted), (
        f"the attribution filter does not exempt the retired sentence quoted as retired: "
        f"{_reworded_universal(quoted)}. Amendment 1 carries exactly that quotation, so the "
        f"arm would refuse the record for recording what it corrected"
    )


#: The retired T-11 residual, verbatim at ``11825776^`` -- the state immediately
#: before #669's records commit, in the same frame as the passages above. Its
#: three clauses are what :data:`_UNRECONCILED` is keyed off: the bound "is
#: unreachable", the builder calls the SDK "without ``max_request_body_size``",
#: and "the reconciliation is owed". Line breaks included, because the claim
#: wraps four times and folding the wrap is what lets the rule see it.
#:
#: Only the ``#669`` half is carried. The ``#665`` sentence that closed the same
#: paragraph is still shipped, word for word, so including it would put live
#: prose in a constant named for retired prose.
_RETIRED_T11_RESIDUAL: Final = (
    "**Residual risk:** two, both recorded rather than discovered later.\n"
    "`MAX_PARAMS_RENDERED_CHARS` is unreachable over the shipped transport —\n"
    "`daemon/server.py` calls `streamable_http_app` without `max_request_body_size`,\n"
    "so the SDK's 4 MiB default answers `413` before any MCP framing exists, and the\n"
    "reconciliation is owed by\n"
    "[#669](https://github.com/theurian/theurian/issues/669) at the slice that opens\n"
    "the write surface."
)


def test_the_unreconciled_scan_reports_the_passage_it_was_written_for() -> None:
    """The premise under the unreachability arm: that rule can still fire.

    :data:`_UNRECONCILED` is deliberately narrow -- the shipped paragraph talks
    about reachability throughout, so the usual over-approximation would be RED
    against the record it protects -- and a narrow key is exactly the kind that
    stops matching without anyone noticing. One character trimmed from
    ``unreachable``, a subject spelling that drifts, a normalisation that stops
    folding the wrap, and
    :func:`test_the_t11_residual_no_longer_says_the_render_bound_is_unreachable`
    is green whatever the threat model now says.

    So the retired residual is held here verbatim, and the rule is required to
    report it. Verbatim including the line breaks, which is the half a synthetic
    string would miss: the claim wraps four times, and a rule that stopped
    normalising would report nothing while the shipped record stayed green.

    The shipped paragraph is asserted clean by the arm itself; what this adds is
    that *clean* means the rule looked.
    """
    reported = _unreconciled_reassertions(_RETIRED_T11_RESIDUAL)

    assert reported, (
        f"the retired T-11 residual, the text itself, is no longer reported.\n\n"
        f"The rule guarding that paragraph has stopped matching the passage it was "
        f"written for -- a narrowed key, a subject spelling that drifted, or a "
        f"normalisation that stopped folding the wraps this passage carries. The "
        f"unreachability arm is green whatever the record now "
        f"says.\n\n{_RETIRED_T11_RESIDUAL}"
    )


def _reworded_universal(text: str) -> list[str]:
    """Every place *text* restates the retired universal **unattributed**, in any dress.

    Two filters, in order. The pairing finds a subject naming the population
    within reach of a phrasing equating the charge to what ``repr`` renders;
    the attribution filter then drops any that sit beside a marker saying the
    sentence is quoting what was corrected.

    The second is not optional. Amendment 1 carries the retired sentence
    verbatim, inside *"the render charge, recorded as charging ..."*, and a rule
    without this filter refuses the record for recording what it fixed -- the
    exact over-approximation that forces a key to be narrowed back to the one
    dress it was written for.

    The windows are recomputed here rather than filtered out of
    :func:`~threat_model_claims.pairings`' output, because that output is text
    and two windows in a long record can be identical: locating them again by
    substring would attribute one occurrence's neighbourhood to another's.
    """
    normalised = prose(text)
    marks = [mark.span() for mark in _ATTRIBUTION.finditer(normalised)]
    subjects = [subject.span() for subject in _REWORDED_UNIVERSAL_SUBJECT.finditer(normalised)]

    found: list[str] = []
    for hit in _REWORDED_UNIVERSAL_PHRASING.finditer(normalised):
        paired = [
            (start, end)
            for start, end in subjects
            if max(start - hit.end(), hit.start() - end, 0) <= _REACH_CHARS
        ]
        if not paired:
            continue
        if any(
            max(start - hit.end(), hit.start() - end, 0) <= _ATTRIBUTION_REACH
            for start, end in marks
        ):
            continue
        opening = max(0, min(hit.start(), *(start for start, _ in paired)) - 40)
        found.append(normalised[opening : max(hit.end(), *(end for _, end in paired)) + 40])
    return found


@pytest.mark.parametrize(
    "record",
    [
        "threat-model T-11 residual",
        "ADR-0031 Amendment 1",
        "daemon/server.py's MAX_REQUEST_BODY_BYTES",
        "mcp/validation.py's MAX_PARAMS_RENDERED_CHARS",
        "CHANGELOG #669 entries",
    ],
)
def test_no_record_restates_the_retired_universal_in_a_new_dress(record: str) -> None:
    """RED means the withdrawn charge claim came back wearing different words.

    ``_rendered_width`` does **not** charge every leaf what ``repr`` renders it
    as, and the difference is not pedantry: a container is charged zero by
    design, because the walk descends into it and charging it would double-count
    its members, and the ``bytes`` arm charges its whole repr including the
    ``b''`` delimiters. The shipped claim is the weaker, true one -- *at least
    the number of characters that leaf contributes to the render*.

    The verbatim key beside this one catches the sentence Amendment 1 retired
    and nothing else. A universal is a sentence, and a sentence has as many
    dresses as an editor wants: one restatement was in the tree when this arm
    was written, in the CHANGELOG heading a reader took the shipped guarantee
    from, and the verbatim key did not see it -- #685's docs stage corrected the
    heading, and this arm is what would have caught the next one. So this pairs
    a *subject* naming the population with a *phrasing* equating the charge to
    the render, which is the shape any dress of the claim has to take.

    Two of the four phrasing alternatives are reached by no fixture, which means
    a rewording landing on exactly those would pass this arm in silence. That is
    gap 2 on https://github.com/theurian/theurian/issues/697, which owes a
    control fixture per alternative; what is here holds the two dresses that
    have actually been written.

    Held over five records rather than the three the figure arms read: the two
    production docstrings carry no retired *figure* and never should, but both
    state the *universal* in its corrected wording, so the subject half of this
    key is live in each and they are where the next dress is most likely to land
    -- a reader learns what the charge guarantees from the constant's own prose.
    The next dress will be in whichever record is edited next, and
    :func:`test_the_reworded_key_passes_over_the_shipped_wording` is what makes
    that affordable: the corrected wording pairs with neither half, so a record
    stating the correction is not reddened by stating it.
    """
    restatements = _reworded_universal(_UNIVERSAL_RECORDS[record]())

    assert restatements == [], (
        f"the {record} states that the charge is what `repr` renders, within "
        f"{_REACH_CHARS} characters of naming every leaf:\n"
        + "".join(f"\n  ...{window}..." for window in restatements)
        + "\n\nThat universal is false in two places and was withdrawn in round two: a "
        "container is charged zero by design, and `bytes` is charged its whole repr "
        "including the `b''` delimiters. What ships is 'every leaf at least the number of "
        "characters that leaf contributes to the render'. If the sentence is describing the "
        "defect that was fixed rather than the guarantee that ships, say so where it stands "
        "-- an attribution inside the sentence is what tells the two apart."
    )


#: Which record carries which retired figure, measured 2026-09-15. Amendment 1
#: carries all five, the CHANGELOG the two interim-cap render figures, and
#: T-11's residual none -- it states the correction without reproducing the
#: numbers behind it.
_LIVE_CITATIONS: Final = frozenset(
    {
        ("ADR-0031 Amendment 1", "the-ascii-only-memory-row"),
        ("ADR-0031 Amendment 1", "the-interim-cap-ratio"),
        ("ADR-0031 Amendment 1", "the-interim-cap-render"),
        ("ADR-0031 Amendment 1", "the-interim-cap-widening"),
        ("ADR-0031 Amendment 1", "the-universal-charge-claim"),
        ("CHANGELOG #669 entries", "the-interim-cap-ratio"),
        ("CHANGELOG #669 entries", "the-interim-cap-render"),
    }
)


def test_each_record_still_carries_the_retired_figures_it_is_recorded_as_carrying() -> None:
    """RED means a retired figure's *value* moved, which every arm above would miss.

    The attribution arm refuses a figure standing unattributed; the control
    refuses a key that has stopped matching. Both are conditional on the figure
    being *there*: edit ``53,476,811`` to ``53,476,812`` and the key matches
    nothing, the pair drops out of both sweeps, and both stay green while the
    record now teaches a number no measurement produced. That mutation was
    driven and survived, which is the only reason this arm exists.

    Set equality over the ``(record, figure)`` pairs closes it from both sides. A
    pair that vanished is a figure whose value was edited or whose sentence was
    deleted -- either way a change to what the record teaches, and a decision
    rather than a typo. A pair that appeared is a record newly quoting a retired
    measurement, which is exactly what the attribution arm then has to hold.

    It does not check the figures against anything computable, because they are
    not: they are reproductions taken at a cap that no longer ships, and the only
    honest pin on a historical measurement is that nobody has quietly changed it.
    """
    live = {
        (record, figure)
        for record, read in _FIGURE_RECORDS.items()
        for figure, key in _RETIRED_FIGURES.items()
        if key.search(prose(read()))
    }

    assert live == _LIVE_CITATIONS, (
        f"the retired figures the records carry have changed. Gone: "
        f"{sorted(_LIVE_CITATIONS - live)} -- a value edited, or a sentence dropped, and "
        f"every arm above goes quiet about it because the key stops matching. Appeared: "
        f"{sorted(live - _LIVE_CITATIONS)} -- a record newly quoting a measurement that was "
        f"withdrawn, which needs an attribution beside it"
    )
