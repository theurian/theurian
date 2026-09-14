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

**Reach.** The records are T-11's residual paragraph, ADR-0031's Amendment 1 and
the CHANGELOG's #669 entries, read through ``sec12_records``. Not the roadmap's
SEC-12 cell, which carries no #669 figure since the reconciliation left its
*owed* list, and not **T-6** -- which the round-two brief named and which
measurably carries none of these: ``roughly 3.0x``, ``53,476,811``, ``4.25x``
and the retired universal appear in it zero times. Including it would look like
a record held and be an empty read, the reason
``test_sec12_shipped_claims.py``'s ``_REASSERTION_RECORDS`` gives for leaving
``schemas/README.md`` out of its own sweep.

Two files read as text. No database, no socket, nothing written anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

import pytest
from sec12_records import amendment_one, changelog_entries, t11_residual_paragraph
from threat_model_claims import pairings, prose

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
#: 2026-09-15 over both records: every one of the eight live occurrences carries
#: its attribution within **116** characters, the worst being the CHANGELOG's
#: *"that reproduction ran against the interim body cap"* clause. Set well above
#: that so an ordinary re-wrap does not redden it, and far below the 17,012
#: characters of Amendment 1 so an unattributed reassertion elsewhere in the
#: same record is still caught.
_ATTRIBUTION_REACH: Final = 200

#: The records the attribution arm ranges over, each read at call time.
_FIGURE_RECORDS: Final[dict[str, Callable[[], str]]] = {
    "threat-model T-11 residual": t11_residual_paragraph,
    "ADR-0031 Amendment 1": amendment_one,
    "CHANGELOG #669 entries": lambda: changelog_entries("#669"),
}


def _unreconciled_reassertions(text: str) -> list[str]:
    """Every place *text* pairs the render bound with an unreconciled phrasing."""
    return pairings(text, _RENDER_BOUND_SUBJECT, _UNRECONCILED, _REACH_CHARS)


def test_the_t11_residual_no_longer_says_the_render_bound_is_unreachable() -> None:
    """RED means the residual went back to calling a reachable bound unreachable.

    Until #669, T-11's residual said ``MAX_PARAMS_RENDERED_CHARS`` *"is
    unreachable over the shipped transport"*, because ``build_app`` called
    ``streamable_http_app`` with no ``max_request_body_size`` and the SDK's own
    4 MiB default answered ``413`` before any MCP framing existed -- so the
    reconciliation was owed. Both halves are now false: the transport cap is
    derived and passed, and ``_rendered_width`` charges every leaf what ``repr``
    renders it as, so the render budget is held by the charge at any transport
    cap rather than by the caps' ordering.

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
        f"`mcp/validation.py`'s `_rendered_width` charges every leaf what `repr` "
        f"renders it as, so the {MAX_PARAMS_RENDERED_CHARS}-character render budget is "
        f"held by the charge rather than by the caps' ordering and its bounded refusal "
        f"is reachable in the shipped default configuration. If the sentence is about "
        f"some *other* bound that genuinely cannot be met, key it to that bound so it "
        f"no longer reads as a claim about this one."
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
    text = prose(_FIGURE_RECORDS[record]())
    key = _RETIRED_FIGURES[figure]

    unattributed = [
        text[max(0, hit.start() - 60) : hit.end() + 60]
        for hit in key.finditer(text)
        if not any(
            max(mark.start() - hit.end(), hit.start() - mark.end(), 0) <= _ATTRIBUTION_REACH
            for mark in _ATTRIBUTION.finditer(text)
        )
    ]

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
    """The premise under the arm above: the rule can still fire on each key.

    Five keys, and an absence arm is green against any of them that has stopped
    matching -- a tightened regex, a figure re-spelled without its commas, a
    normalisation that stopped folding a wrap. It is green most convincingly at
    exactly that moment.

    So each key is driven through the same rule against a passage carrying it
    *unattributed*, and each must be reported. Built by stripping the
    attribution from the record's own live sentence rather than from a synthetic
    one, so the control tests the rule against the text it actually guards.
    """
    never_reported = []
    for figure, key in sorted(_RETIRED_FIGURES.items()):
        stripped = _ATTRIBUTION.sub("(removed)", prose(amendment_one()))
        hits = [
            stripped[max(0, hit.start() - 60) : hit.end() + 60] for hit in key.finditer(stripped)
        ]
        if not hits:
            never_reported.append(figure)

    assert never_reported == [], (
        f"these keys match nothing in Amendment 1 even with every attribution removed: "
        f"{never_reported}. The arm above is green for them whatever the record says -- the "
        "key was narrowed, or the figure is spelled some other way now, or the record "
        "stopped carrying the correction this rule exists to protect"
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
