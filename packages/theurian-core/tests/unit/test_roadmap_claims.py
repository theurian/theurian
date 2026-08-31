r"""The roadmap's corrected claim about what the threat model's T-16 summary row reads.

``docs/roadmap.md``'s Phase 0 release-gate paragraph used to close with *"Tracked
by [#80] -- the summary table still points at #39, which is closed while its
install-time half is not."* The clause after the dash is not a statement about the
roadmap; it is a claim about what **another file in this repository contains**,
and it stopped being true at ``efd30fe``
(https://github.com/theurian/theurian/pull/425), which repointed the threat
model's T-16 summary row from #39 to #80. The
https://github.com/theurian/theurian/issues/428 sweep found it and rewrote the
sentence to quote what that row reads today.

A quoted claim about another file's contents rots from **two** sides, and this
module holds both: the roadmap can drift back to the retired assertion, and the
row can move out from under the quotation when T-16's install-time control lands.
One side alone is a pin that reports safety it does not have -- a roadmap frozen
against a threat model free to move is the same defect one file over.

**Scoped to this one claim.** The file is named for the document, not for the
claim, because the roadmap will acquire other corrected claims and a module per
claim would be a module per sentence. What keeps it honest is that everything
below is derived from the two live files: nothing here is a second copy of either
document that a future edit could leave behind.

**The fact side, derived on both sides.** The only string this module takes from
the roadmap is the one the roadmap itself quotes, read out of the document at run
time; the only string it takes from the threat model is the summary row, located
by its table shape. Neither is written here. The comparison is a substring test
between them, over text that has been through :func:`_normalised` -- whitespace
collapsed (the roadmap soft-wraps the quotation across two lines), lower-cased,
and every Markdown inline link reduced to its label, because the roadmap quotes
``([#80]; ...)`` where the row writes ``([#80](https://.../issues/80); ...)``.
Reducing links is the one normalisation with a direction worth stating: it is
applied identically to both sides, and a link whose target carries a space or a
parenthesis is left alone, which can only make the comparison stricter.

**Which quotation is the row's, decided by the row.** The block carries two
quotations -- the entry's grade line and the summary row -- so one has to be
chosen, and choosing it by position or by the sentence that introduces it would
pin grammar. It is chosen by the row's own Markdown-link labels: exactly one
quotation in the block carries all of them. When the row's owner cite moves, that
selection returns zero and the failure names the direction the drift came from.

**The prose side.** The retired assertion may come back reworded, in a block that
still carries the corrected quotation beside it -- and then the fact side stays
green while a reader meets the false sentence. :data:`RETIRED_ROW_CLAIM` is the
key for it, applied to **every** block of the roadmap rather than to the located
one, because a claim about the row written three sections away is the same defect
in a place the locator does not look.

The discriminator is a correction marker, not a tense: the corrected sentence and
the retired one both name the summary row and #39 in one breath, and what
separates them is that the live one says the pointing **ended** (*"pointed at
closed #39 **until** ``efd30fe`` **repointed** it"*). So a sentence naming both is
an offender unless it carries one of :data:`CORRECTION_MARKERS`. ``closed`` is
deliberately not among them: the retired sentence carried it too ("#39, which is
closed while its install-time half is not"), and admitting it would let the
retired wording straight back in.

Quotations are **not** stripped before this scan, which is what makes the two
sides cover each other. A reassertion smuggled in as a quotation -- ``a later
note: "the summary table still points at #39"`` -- is caught here *and* by
:func:`test_every_quotation_in_the_roadmaps_t16_block_is_the_threat_models_own_words`,
which refuses any quotation in the block that is not the threat model's own words.
It costs nothing today: the shipped quotation of the row names #39 and does not
name the summary row, so it cannot match this key. Measured, not reasoned -- the
scan over the shipped document returns zero.

**Reach.** This module holds (1) that the roadmap carries exactly one block making
the T-16 release-gate claim, located by a key that is in both the retired and the
corrected wording, so a straight revert is still found rather than dropping out of
the population; (2) that the threat model carries exactly one T-16 summary row;
(3) that exactly one quotation in that block carries the row's link labels and is
a substring of the live row; (4) that every quotation in that block is a substring
of the live threat model; and (5) that no block of the roadmap says the summary
table or row points at #39 without recording that the pointing ended.

It does **not** hold the tracker facts in that paragraph -- that #80 is open, that
#39 is closed, that #80 carries ``post-1.0``. Who owns a gap is a fact about
GitHub, and #80 itself records why no pin reaches it: a liveness check "would
reach the network from the unit suite, which this project does not do". Those
sentences are prose, and this module is deliberately silent about them. It does
not hold the count in the block's last line (21 open ``pre-1.0`` issues, measured
2026-08-20), for the same reason. It does not tie the quotation to the sentence
that attributes it: the block is required to name the summary row and to quote it,
but a rewrite that kept both while attaching the quotation to something else would
pass. And it does not claim T-16's install-time control exists -- the paragraph
states that gap as owned, and this is a pin over the sentence, not a control that
discharges it.

**Measured escapes**, run 2026-09-01 against this module's own rule and recorded
rather than chased. :data:`RETIRED_ROW_CLAIM` is one regex with a 60-character
window, so a long subject escapes it -- *"the summary table, which nobody has
looked at since the release-engineering rewrite of last spring, still points at
#39"* puts 91 characters between the anchors -- and so does a rewording that drops
the words *summary table* and *summary row*: *"the threat model's owner cell still
points at #39"*. Widening the window trades those for false positives across a
document that discusses issue numbers in most of its paragraphs, and a rule that
cries wolf is deleted by the next author. The fact side is the half that does not
depend on wording, and a reworded denial still has to get the row's own words past
it.

This module's own ``#39`` mentions are history cites -- class (b) under the #428
sweep's rule, which grades a cite by whether it names a closed issue as the
**owner** of something unbuilt. Nothing here cites #39 as an owner.
"""

from __future__ import annotations

import re
from typing import Final

from write_lock_claims import REPO_ROOT, collapsed

#: The two documents this module reads. Everything asserted below is a relation
#: between them; neither is restated here.
ROADMAP: Final = REPO_ROOT / "docs/roadmap.md"
THREAT_MODEL: Final = REPO_ROOT / "docs/security/threat-model.md"

#: A Markdown inline link, reduced to its label by :func:`_normalised`. The target
#: is deliberately narrow -- no whitespace, no parenthesis -- so a link this does
#: not recognise is left intact on both sides and the comparison fails rather than
#: passing on a normalisation that ate more than it should.
_INLINE_LINK: Final = re.compile(r"\[([^\[\]]*)\]\([^()\s]*\)")

#: A double-quoted span, capturing what is inside it. Sequential pairing, so the
#: block's quote count is asserted even before anything is read out of it.
_QUOTED: Final = re.compile(r'"([^"]*)"')

#: The summary-table row for T-16, anchored at the line start so a mention of the
#: entry inside another row's prose cannot be mistaken for the row itself.
_SUMMARY_ROW: Final = re.compile(r"^\|\s*T-16\s*\|")

#: A sentence boundary in text :func:`_normalised` has already collapsed: a full
#: stop or a semicolon followed by a space. The trailing space is what keeps
#: ``0.1.0-stable`` and ``post-1.0`` -- both in the block -- from being split into
#: fragments that would carry a claim's subject and its correction marker apart.
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.;])\s")

#: The key the T-16 release-gate block is located by. Taken from the sentence that
#: **survived** the correction rather than from the correction itself: a key built
#: out of the new wording stops matching the moment someone reverts it, so the
#: block would silently leave the population instead of failing.
T16_BLOCK_KEY: Final = "that unmet half is what the critical grade names"

#: The retired assertion, as a shape rather than as its wording: the summary table
#: or row, and #39, inside one sentence. The window stops at a full stop or a
#: semicolon so it cannot reach across a sentence boundary and pair a subject with
#: someone else's number.
RETIRED_ROW_CLAIM: Final = re.compile(r"\bsummary (?:table|row)\b[^;.]{0,60}?#39")

#: What turns a sentence naming the row and #39 into a record that the pointing
#: ended. Any one of them is enough. ``closed`` is **not** here on purpose: the
#: retired sentence carried it ("#39, which is closed while its install-time half
#: is not"), so admitting it would readmit the wording this module exists to
#: refuse.
CORRECTION_MARKERS: Final = ("until", "repointed", "no longer", "used to", "before")

#: A floor on the quotation of the row, written rather than derived, because its
#: job is to refuse a **degenerate** quote. ``"[#80]"`` carries the row's label and
#: is a substring of the row, so it would satisfy every other rule here while
#: saying nothing about what the row reads. 40 is below the 86 characters the
#: shipped quotation runs to (measured 2026-09-01) and far above anything that
#: could match the row by accident.
MIN_ROW_QUOTE_CHARS: Final = 40


def _normalised(text: str) -> str:
    """*text* with links reduced to labels, whitespace collapsed and case dropped.

    Applied identically to both documents, which is the whole point: the roadmap
    soft-wraps its quotation across two source lines and writes ``[#80]`` where
    the threat model's row writes the full link, so a raw comparison of the two
    fails on markup rather than on the claim.
    """
    return collapsed(_INLINE_LINK.sub(lambda match: f"[{match.group(1)}]", text))


def _blocks(document: str) -> list[str]:
    """*document* split on blank lines, each block joined and :func:`_normalised`.

    Blank lines only. The roadmap writes this paragraph as one indented run with
    no blank line inside it, so a finer boundary would cut the sentence that makes
    the claim away from the quotation that discharges it.
    """
    blocks: list[list[str]] = [[]]
    for line in document.splitlines():
        if line.strip():
            blocks[-1].append(line)
        else:
            blocks.append([])

    return [flattened for block in blocks if (flattened := _normalised(" ".join(block)))]


def _block_carrying(document: str, key: str) -> str:
    """The single block of *document* containing *key*, or a failure naming both causes."""
    found = [block for block in _blocks(document) if key in block]

    assert len(found) == 1, (
        f"`{key}` no longer identifies exactly one block of the roadmap: found "
        f"{len(found)}. Zero means the T-16 release-gate paragraph was deleted or "
        f"reworded past the sentence that survived the correction, and the claim "
        f"this module pins is no longer findable; more than one means everything "
        f"read out of it below is about text this module never chose"
    )
    return found[0]


def _quotations(block: str) -> list[str]:
    """Every double-quoted span of *block*, without its quotes, parity asserted first.

    Sequential pairing only means anything over an even number of quotes. With an
    odd one, every span from the stray quote onward is the *inverse* of a
    quotation -- the prose between two quotations -- and the substring rules below
    would be comparing the wrong text against the threat model without failing on
    the mismatch.
    """
    quotes = block.count('"')
    assert quotes % 2 == 0, (
        f"the roadmap block carries {quotes} double quotes, an odd number, so the "
        f"spans read out of it pair the wrong ones and every quotation this module "
        f"checks is text the roadmap never quoted: {block[:200]}"
    )
    return _QUOTED.findall(block)


def _summary_row() -> str:
    """The threat model's T-16 summary-table row, raw, asserted unique.

    Located by the table's own shape rather than by a line number, so the pin
    survives every edit above it and fails on the one edit that matters. Zero rows
    and two rows are different defects and the message says so: with none there is
    nothing for the roadmap's quotation to be a substring of, and with two the
    quotation is checked against whichever the file happens to list first.
    """
    rows = [
        line
        for line in THREAT_MODEL.read_text(encoding="utf-8").splitlines()
        if _SUMMARY_ROW.match(line)
    ]

    assert len(rows) == 1, (
        f"the threat model has {len(rows)} summary-table rows for T-16, expected 1. "
        f"With none, the roadmap's quotation of that row describes a row that is "
        f"not there; with more than one, this module reads the first and says "
        f"nothing about the rest: {rows}"
    )
    return rows[0]


def _row_link_labels(row: str) -> list[str]:
    """The Markdown-link labels of *row*, lower-cased -- the key the quotation is selected by.

    Derived from the threat model so that a repointed row changes what the roadmap
    has to quote. Asserted non-empty because an empty key would be carried by
    every quotation in the block, and a selection that matches everything selects
    nothing.
    """
    labels = [match.group(1).lower() for match in _INLINE_LINK.finditer(row)]

    assert labels, (
        f"the T-16 summary row carries no Markdown link, so there is no derived "
        f"key to pick the roadmap's quotation of it out by. Either the row's owner "
        f"cite lost its link -- in which case the roadmap's quotation of it has to "
        f"be re-taken -- or the row was rewritten entirely: {row}"
    )
    return labels


def _offending_sentences(text: str) -> list[str]:
    """Sentences of *text* asserting the summary row points at #39, with no marker.

    Extracted rather than written inline so the synthetic driver below runs *this*
    predicate. A driver that restated the rule would go RED on its own restatement
    and stay green whatever the shipped rule did.
    """
    return [
        sentence
        for sentence in _SENTENCE_BREAK.split(text)
        if RETIRED_ROW_CLAIM.search(sentence)
        and not any(marker in sentence for marker in CORRECTION_MARKERS)
    ]


def test_the_roadmap_carries_exactly_one_t16_release_gate_block() -> None:
    """The premise every fact-side rule rests on: there is one block, and it is T-16's.

    :data:`T16_BLOCK_KEY` is a sentence, and a sentence can be deleted. If it is,
    every rule below reads a block this module did not choose, or no block at all
    -- and a scan over nothing reports clean. Held on its own so that failure
    arrives named, rather than as a confusing message from the substring rules.

    The block is also required to still **attribute** its quotation, by naming the
    summary table or row. Without that sentence the paragraph quotes a string with
    no claim attached to it, and the correction the #428 sweep made would be gone
    while the quotation it introduced stayed behind.
    """
    block = _block_carrying(ROADMAP.read_text(encoding="utf-8"), T16_BLOCK_KEY)

    assert re.search(r"summary (?:table|row)", block), (
        f"the roadmap's T-16 release-gate block no longer names the threat model's "
        f"summary table or row, so nothing in it says what the quotation below is "
        f"a quotation *of*: {block[:400]}"
    )


def test_the_threat_model_carries_exactly_one_t16_summary_row() -> None:
    """The other premise, on the other side of the claim.

    RED here means the roadmap's sentence describes a row that this module can no
    longer find -- which is the same defect the #428 sweep fixed, arriving from
    the threat model's side instead of the roadmap's.
    """
    row = _summary_row()

    assert _row_link_labels(row), f"the T-16 summary row names no linked issue: {row}"


def test_the_roadmap_quotes_what_the_t16_summary_row_reads_today() -> None:
    """The claim itself, held against both live files and copied from neither.

    This is the pin the correction owes. The roadmap says the summary row *"now
    reads"* a particular string; that string is read out of the roadmap here and
    required to be what the row actually reads. It goes RED from either direction
    -- the roadmap drifting back to a sentence about #39, or the row moving when
    T-16's install-time control lands and its owner cite is repointed -- and the
    message names both, because the two call for opposite responses.

    The quotation is selected by the **row's** link labels rather than by its
    position in the block, so the selection itself moves when the row does.
    """
    block = _block_carrying(ROADMAP.read_text(encoding="utf-8"), T16_BLOCK_KEY)
    row = _summary_row()
    labels = _row_link_labels(row)

    carrying = [
        quotation for quotation in _quotations(block) if all(label in quotation for label in labels)
    ]

    assert len(carrying) == 1, (
        f"{len(carrying)} of the roadmap's T-16 quotations name {labels}, expected "
        f"exactly one. Zero is the drift this pin exists for, from whichever side "
        f"moved: the roadmap stopped quoting the row, or the row was repointed and "
        f"the roadmap still quotes the issue it used to name. More than one means "
        f"the quotation checked below is not necessarily the row's"
    )
    quotation = carrying[0]

    assert len(quotation) >= MIN_ROW_QUOTE_CHARS, (
        f"the roadmap's quotation of the T-16 summary row is {len(quotation)} "
        f"characters, under the {MIN_ROW_QUOTE_CHARS} this module requires. A "
        f"quotation this short is a substring of the row without saying what the "
        f"row reads, and the rule below would pass on it: {quotation!r}"
    )
    assert quotation in _normalised(row), (
        f"the roadmap says the threat model's T-16 summary row reads "
        f"{quotation!r}, and it does not. Either the roadmap drifted -- restate "
        f"the sentence from the row -- or the row moved, in which case the "
        f"roadmap's paragraph is the record that has to follow it. The row reads: "
        f"{_normalised(row)!r}"
    )


def test_every_quotation_in_the_roadmaps_t16_block_is_the_threat_models_own_words() -> None:
    """The block quotes the threat model twice, and both quotations are pinned.

    The second quotation -- the entry's grade line, *"publication ships,
    install-time verification does not"* -- is the same shape of claim as the
    first: the roadmap asserting what another file says. Pinning the pair rather
    than the one costs nothing today and closes the route a reassertion would
    otherwise take, since a sentence smuggled into this block inside quotation
    marks is not the threat model's words and fails here.

    RED can also mean a quotation of a **third** source was added to this block.
    That is not drift, and the fix is to move it or to scope this rule -- the
    message says so, because the two responses are opposite.
    """
    block = _block_carrying(ROADMAP.read_text(encoding="utf-8"), T16_BLOCK_KEY)
    document = _normalised(THREAT_MODEL.read_text(encoding="utf-8"))

    foreign = [quotation for quotation in _quotations(block) if quotation not in document]

    assert not foreign, (
        f"the roadmap's T-16 block quotes {foreign}, and the threat model does not "
        f"contain those words. Either a quotation drifted from the document it "
        f"claims to come from, or a quotation of some other source was added to "
        f"this block -- in which case this rule needs scoping rather than the "
        f"paragraph needing a fix"
    )


def test_no_roadmap_block_says_the_t16_summary_row_still_points_at_39() -> None:
    """The retired assertion may be recorded as ended; it may not be made again.

    This is the rule the prose side exists for. Nothing stops an author reading
    the paragraph's conclusion -- T-16's install-time half is unmet, #39 is closed
    -- and reaching for the evidence the sentence used to offer for it.

    Applied to every block of the roadmap, not to the located one. The same claim
    written three sections away is the same defect, and a block-scoped rule would
    report the document clean while it sat there.
    """
    offenders = {
        index: found
        for index, block in enumerate(_blocks(ROADMAP.read_text(encoding="utf-8")))
        if (found := _offending_sentences(block))
    }

    assert not offenders, (
        f"the roadmap asserts that the threat model's T-16 summary table or row "
        f"points at #39, with nothing recording that the pointing ended: "
        f"{offenders}. It stopped being true at `efd30fe`, which repointed that "
        f"row. If the sentence is meant as history, say what ended it -- one of "
        f"{list(CORRECTION_MARKERS)} -- and if the row really has moved back, the "
        f"quotation in the same paragraph is what has to move with it"
    )


def test_the_retired_summary_table_claim_is_caught_when_it_comes_back() -> None:
    """RED means the rule above cannot fail, whatever the roadmap says.

    Driven with synthetic text, because the shipped document is compliant and a
    rule measured only against a compliant document is indistinguishable from one
    that answers "nothing is wrong". The input is the sentence as it stood before
    https://github.com/theurian/theurian/pull/425 made it false, verbatim.

    The second half is what stops the assertion being satisfiable by a rule that
    refuses every mention of the row and #39 together: the **corrected** sentence,
    which names both, is required to come back clean. A rule that failed on it
    would report the shipped roadmap as the defect, and the discriminator this
    module claims to use -- a recorded ending, not a tense -- would be fiction.
    """
    retired = _normalised(
        "Tracked by [#80](https://github.com/theurian/theurian/issues/80) — the\n"
        "summary table still points at #39, which is closed while its install-time\n"
        "half is not."
    )

    assert _offending_sentences(retired), (
        "the retired sentence was not caught, so the rule over the shipped "
        "roadmap passes for a reason that has nothing to do with the roadmap"
    )

    corrected = _normalised(
        "The threat model's summary row pointed at closed #39 until `efd30fe`\n"
        "repointed it; it now reads what the paragraph quotes."
    )

    assert not _offending_sentences(corrected), (
        f"the corrected sentence was also refused, so the rule rejects any mention "
        f"of the summary row beside #39 rather than the absence of a recorded "
        f"ending -- and the roadmap's own paragraph would be the defect. "
        f"{list(CORRECTION_MARKERS)} is what has to admit it"
    )


def test_the_block_locator_reads_one_block_and_not_its_neighbours() -> None:
    """RED means the fact side is checking quotations the T-16 block does not carry.

    The two failures this separates are not symmetric. A locator that returns the
    wrong block goes RED loudly on the first substring rule. A locator that
    returns *too much* -- blank lines mis-handled, the whole document collapsed
    into one block -- widens the quotation population silently, and
    :func:`test_the_roadmap_quotes_what_the_t16_summary_row_reads_today` would then
    be selecting among quotations from anywhere in the file.

    Driven through :func:`_block_carrying` and :func:`_quotations` together, since
    it is their composition that decides what the fact side reads.
    """
    document = (
        'above the block, "a quotation that is not the T-16 block\'s"\n\n'
        "T-16 is graded Critical, and that unmet half is what the Critical grade\n"
        'names. The summary row now reads "the quotation this module reads".\n\n'
        'below the block, "another quotation"\n'
    )

    quotations = _quotations(_block_carrying(document, T16_BLOCK_KEY))

    assert quotations == ["the quotation this module reads"], (
        f"the locator did not isolate the T-16 block's own quotations: {quotations}"
    )
