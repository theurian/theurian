"""Splitting documents into retrievable passages (FR-R2).

**Nothing here imports ``MIN_CHARS`` or ``TARGET_CHARS``, and that is the point.**
Every threshold below is stated as a shape of text — one sentence, one paragraph,
a document with no structure at all — because an assertion written in terms of
the constant moves with the constant and pins nothing.

The runt test used to read ``assert all(c.char_count >= MIN_CHARS or len(chunks)
== 1 ...)``, which is the chunker's own postcondition restated: whatever
``MIN_CHARS`` becomes, ``_merge_runts`` enforces exactly that, so the assertion
follows it down. Measured on the fixture it had: at ``MIN_CHARS = 15`` that
document came back as three chunks of which the first was ``# Intro\\n\\nShort.``
— a heading and one sentence published alone, the precise failure the test is
named after — and it passed, because 15 >= 15.

See the two banner comments below for the band each constant is held in and how
each edge was measured.
"""

from __future__ import annotations

import pytest

from theurian.domain.chunking import ChunkingError, chunk_document

REVISION = "01K1AAAREV01234567890ABCDE"


def test_an_empty_body_yields_no_chunks() -> None:
    """An empty chunk matches nothing and costs a row."""
    assert chunk_document(REVISION, "   \n\n  ") == ()


def test_a_short_document_is_one_chunk() -> None:
    chunks = chunk_document(REVISION, "# Title\n\nA short policy statement about tokens.")

    assert len(chunks) == 1
    assert "short policy" in chunks[0].text


def test_chunk_ids_are_deterministic_and_scoped_to_the_revision() -> None:
    """FR-R7. An index rebuild over unchanged content must leave a pinned
    result resolvable, which requires the same text to produce the same ids."""
    body = "# A\n\n" + ("alpha " * 300) + "\n\n# B\n\n" + ("beta " * 300)

    first = chunk_document(REVISION, body)
    second = chunk_document(REVISION, body)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert all(c.chunk_id.startswith(f"{REVISION}#") for c in first)


def test_splitting_prefers_headings_over_length() -> None:
    """A heading is a boundary the author chose. A character count is one we
    picked because it was cheap."""
    body = "# Authentication\n\n" + ("auth " * 100) + "\n\n# Caching\n\n" + ("cache " * 100)

    chunks = chunk_document(REVISION, body)

    headings = [c.heading for c in chunks]
    assert "Authentication" in headings
    assert "Caching" in headings
    assert not any("auth" in c.text and "cache" in c.text for c in chunks)


def test_an_over_long_section_is_split_on_paragraphs_not_mid_sentence() -> None:
    """A passage cut in half retrieves on terms it no longer explains."""
    paragraphs = "\n\n".join(f"Paragraph {i} about tokens and rotation." * 12 for i in range(6))

    chunks = chunk_document(REVISION, f"# Long\n\n{paragraphs}", target_chars=500)

    assert len(chunks) > 1
    assert all(not c.text.endswith(("token", "rotatio")) for c in chunks)


# -- Where the runt threshold sits (`MIN_CHARS`) -----------------------------
#
# `MIN_CHARS` decides what is too small to be a passage, and it makes exactly one
# product claim: "A two-line chunk retrieves badly (too little signal) and reads
# badly (no context), so a heading with one sentence under it belongs with what
# follows." Two shapes of text fall either side of that sentence, and they are
# what the two tests below assert:
#
#     one heading and one ordinary sentence   ->  must be folded
#     one heading and one real paragraph      ->  must stand on its own
#
# **A band, not a value**, because no requirement fixes a point. The pair holds
# the constant between 92 and 222 — measured by mutation: 91 fails below (the
# stub stands alone), 223 fails above (the paragraph is swallowed). 120 sits
# inside it, and nothing here pretends to know more than that.
#
# The upper edge is the one that has to exist. `MIN_CHARS` folds *forward and
# without a ceiling* — `_merge_runts` merges "even when the result exceeds the
# target" — so a raised threshold does not fail, it silently returns fewer and
# larger passages. It is also what the T-17a flip guard in
# `tests/integration/test_retrieval_service.py` stands on: its withheld document
# is two chunks of 436 and 407 characters, and at `MIN_CHARS >= 408` they merge
# into one, which leaves `test_a_withheld_document_can_still_reorder_the_visible
# _ones` passing and only the guard red. Both edges here fire well below that,
# and they name the constant that moved.
#
# **Two other tests already fail on a raised `MIN_CHARS`, and neither is a
# bound.** `test_the_excerpt_is_the_passage_that_matched_not_the_head_of_the
# _document` goes red from 152, because that fixture's closing section is 151
# characters long — a bound nobody chose, which a one-sentence edit to prose in
# another file would remove without a word. And above 401 `chunk_document`
# refuses the `target_chars=400` several tests here pass, which reports
# "target_chars must be at least 420" from a Japanese-prose test: it names
# neither the threshold that moved nor the passage shape that changed.

#: One heading and one sentence of ordinary English prose: 91 characters, of
#: which 13 words are the sentence. The shape the constant's docstring names, at
#: the length such a sentence really has — a shorter stub would prove less.
ONE_SENTENCE_SECTION = (
    "## Token rotation\n\nEvery service token is rotated on the ninetieth day after it was issued."
)

#: One heading and one paragraph: 222 characters, three sentences. Not a runt by
#: any reading of "too little signal to rank on" — it names the rule, the
#: evidence it leaves, and what a reader can conclude — so folding it into its
#: neighbour is a passage a caller asked for that they will not be shown alone.
PARAGRAPH_SECTION = (
    "## {title}\n\n"
    "{marker} the gateway refuses a request whose token has expired. "
    "The refusal is logged with the token identifier and the tenant it belonged to. "
    "An operator reading that log can tell a rotation lapse from an attack."
)


def _substantial(heading: str, marker: str) -> str:
    """A 319-character section, so no assertion below turns on *its* size.

    Well clear of the band's upper edge, and well clear of the target, so it is
    never itself a runt and never leaves too little room for a stub to fold into.
    """
    return f"## {heading}\n\n{marker} the operator opens the rotation runbook. " + (
        "Each step is signed off before the next one begins. " * 5
    )


def test_a_heading_with_one_sentence_is_folded_into_what_follows() -> None:
    """The direction of the fold, which decides which passage a caller reads.

    "It is folded *forward* because such a passage almost always introduces what
    comes next -- '## Rules' followed by one line belongs with the rules, not
    with the section before it." A stub folded backwards would attach the
    heading of one topic to the body of the previous one, and the caller would
    read an excerpt announcing a subject the passage never covers.

    Three sections rather than two, because with two the direction is
    unobservable: forwards and backwards produce the same single chunk. Written
    that way — as it was — this test asserted its own title and could not fail on
    it.
    """
    body = "\n\n".join(
        [
            _substantial("Scope", "before:"),
            ONE_SENTENCE_SECTION,
            _substantial("Procedure", "after:"),
        ]
    )

    chunks = chunk_document(REVISION, body)

    (carrying_the_stub,) = [c for c in chunks if "ninetieth day" in c.text]
    assert "after:" in carrying_the_stub.text, "a stub belongs with the section it introduces"
    assert "before:" not in carrying_the_stub.text, "and not with the one it followed"


def test_a_closing_one_line_note_is_folded_back_into_the_passage_before_it() -> None:
    """The other half of the lower edge: a stub with nothing after it.

    "The last passage has nothing to merge forward into, so it folds backward
    instead." A document ending in a one-line note — a caveat, a see-also, a
    signature — is the ordinary case for that branch, and nothing exercised it:
    every runt fixture in this file had a section following it, so the forward
    fold answered for both and the backward one was never run.

    **This is not a universal guarantee, and the shape that escapes it is
    reachable.** The backward fold is conditional on `len(previous) + len(stub)
    <= target`, so a document whose last full section already reaches the target
    publishes its closing note as a passage of its own: measured, a 1,001-
    character section followed by this stub yields chunks of 1,001 and 91. That
    residual belongs to `TARGET_CHARS`, not to this threshold, and is left
    recorded rather than asserted away.
    """
    body = f"{_substantial('Procedure', 'before:')}\n\n{ONE_SENTENCE_SECTION}"

    chunks = chunk_document(REVISION, body)

    (carrying_the_note,) = [c for c in chunks if "ninetieth day" in c.text]
    assert "before:" in carrying_the_note.text, (
        "a closing one-line note must not be published as a passage of its own"
    )


def test_a_paragraph_that_answers_a_question_is_a_passage_of_its_own() -> None:
    """The upper edge of the band, stated where the constant lives.

    `_merge_runts` folds forward "even when the result exceeds the target", so
    raising `MIN_CHARS` does not fail anywhere — it quietly returns fewer,
    longer passages, and a caller asking about token expiry receives a passage
    that also argues about logging. FR-R2's whole reason for returning chunks
    rather than documents is that one paragraph often answers the question; a
    threshold that swallows paragraphs undoes that a section at a time.

    The two sections here are 222 characters each, three sentences apiece, and
    the size was chosen from that description rather than from the constant: at
    `MIN_CHARS = 223` they merge and this fails.

    **A looser bound than one the suite already has, kept deliberately.**
    `test_the_excerpt_is_the_passage_that_matched_not_the_head_of_the_document`
    fails from 152 — but only because its fixture's last section happens to be
    151 characters, so lengthening that prose by two sentences would silently
    delete the only thing holding this edge. This one holds it on purpose, at
    the layer the threshold is defined, and its failure names the shape that
    changed rather than an excerpt two layers downstream.
    """
    body = "\n\n".join(
        [
            PARAGRAPH_SECTION.format(title="Expiry", marker="alpha:"),
            PARAGRAPH_SECTION.format(title="Logging", marker="beta:"),
        ]
    )

    chunks = chunk_document(REVISION, body)

    carried = [(("alpha:" in c.text), ("beta:" in c.text)) for c in chunks]
    assert carried == [(True, False), (False, True)], (
        "a paragraph is a retrievable unit; folding two together publishes neither alone"
    )


# -- How large a passage may be (`TARGET_CHARS`) -----------------------------
#
# **`TARGET_CHARS` was set to 5,000 and all 1,259 tests passed.** Every other
# test in this file states its own `target_chars`, so the default was exercised
# by nothing, and a five-fold passage was invisible from the CLI, the MCP surface
# and the index alike.
#
# The constant makes two claims. One is assertable and is the band below:
#
#     large enough to carry an argument   ->  >= 740, or a conclusion is
#                                             published without its premise
#     one paragraph answers the question  ->  <= 1,358, or a page of six
#                                             subjects is one passage
#
# 1,000 sits inside that. Both edges are measured by mutation and both are as
# tight as the fixture they are read off — a project whose arguments run to two
# pages could honestly hold a larger target, and the honest response then is to
# re-measure these fixtures, not to widen the band until it stops complaining.
#
# **The other claim cannot be pinned here, because the product no longer makes
# it.** "Small enough that several fit in a caller's budget alongside their own
# prompt" describes a cost that is not charged: `within_budget` prices *result
# payloads*, and a payload carries `excerpt(...)`, capped at
# `results.EXCERPT_CHARS` = 280 characters of the passage. A 5,000-character
# chunk and a 500-character chunk cost a caller the same. So no assertion below
# mentions the budget, and the sentence in `chunking.py` is out of date rather
# than untested.


#: A single argument in three paragraphs — premise, the evidence for it, the
#: conclusion drawn from both — and 740 characters long. Written as prose first
#: and measured afterwards, so the number is a property of an ordinary argument
#: rather than a number chosen to sit under the constant.
ARGUMENT = "\n\n".join(
    [
        "The gateway verifies every token before a handler runs, and it is the only "
        "component that does. A request that reaches a handler has therefore already "
        "been authenticated, which is why the handlers carry no authentication code "
        "of their own.",
        "That arrangement is what makes the audit log trustworthy. Verification happens "
        "in one place, so every rejection is recorded by the same component in the same "
        "format, and an operator reading the log sees the whole population of rejected "
        "requests rather than a sample of it.",
        "So moving verification into the handlers would cost more than it appears to. "
        "It would spread one rule across a dozen packages, and it would leave the audit "
        "log describing whichever handlers had happened to keep writing to it.",
    ]
)

#: Six subjects, six paragraphs, no headings, 1,359 characters: the meeting note
#: someone pastes in whole. `_split_to_length` names this shape — "a pasted
#: transcript, a generated summary, a wrapped log" — as the case length has to
#: answer for, because there is no author-chosen boundary anywhere in it.
TRANSCRIPT = "\n\n".join(
    [
        "opening: the review begins with the rotation schedule, which nobody has "
        "changed since the gateway was first deployed and which everyone in the room "
        "agrees is now too long for the number of services holding a token.",
        "The second subject is the staging soak. Releases go out on Thursday after the "
        "soak has run for a day, and the soak has caught two regressions this quarter, "
        "both of them in the retry path rather than in anything the release changed.",
        "The third subject is the incident from March. A tenant was quarantined for "
        "eleven minutes because a signing key was rotated without warning, and the "
        "runbook that should have named the owner of that key named a team that no "
        "longer exists.",
        "The fourth subject is cost. The index rebuild runs nightly and takes eleven "
        "minutes, which is cheap, but it runs against the whole corpus rather than "
        "against what changed, so the cost grows with the corpus and not with the work.",
        "The fifth subject is documentation. Every decision above is written down "
        "somewhere, and no two of them are written down in the same place, which is "
        "the reason this review takes an hour rather than the ten minutes it should.",
        "closing: the meeting ends without deciding anything about the rotation "
        "schedule, which is the one subject everyone agreed at the start was urgent, "
        "and it is carried to the next review with the same owner and no date.",
    ]
)


def test_a_three_paragraph_argument_is_not_split_across_passages() -> None:
    """The lower edge: what "large enough to carry an argument" has to mean.

    A passage is what a caller is shown and, on the MCP surface, all they are
    shown. Splitting an argument between two of them publishes a conclusion
    with no premise under it — and the caller cannot tell, because a passage
    carries no sign that it began mid-thought. That is a worse failure than a
    passage slightly too large, which merely costs recall.

    Measured: at a target of 739 the greedy paragraph fill drops the conclusion
    into a second passage and this fails. No `target_chars` is passed, because
    the argument is with the *default* — every other test in this file states
    its own and therefore says nothing about the shipped value.
    """
    chunks = chunk_document(REVISION, ARGUMENT)

    assert len(chunks) == 1, "premise, evidence and conclusion belong to one passage"


def test_a_page_of_pasted_prose_is_not_returned_as_a_single_passage() -> None:
    """The upper edge: what "one paragraph answers the question" has to mean.

    Retrieval ranks passages, so a passage that holds six subjects is a hit that
    is 'about' all six. It ranks on vocabulary the caller did not ask about, and
    the excerpt they receive is the opening of the document rather than the part
    that matched — which is the failure chunking exists to prevent, arriving by
    length instead of by structure. Headings cannot help here: a pasted
    transcript has none, so `TARGET_CHARS` is the only boundary left.

    Both halves are asserted. "More than one chunk" alone would pass on a
    document cut anywhere, including mid-subject; what matters is that the last
    subject is reachable without the first, which is what makes it a *passage*
    rather than a slice.

    Measured: at a target of 1,359 — the document's own length — it is one chunk
    and this fails; 1,358 is the last value that splits it, and at the shipped
    1,000 it is two. The bound is only as tight as this fixture is long, and it
    is deliberately a compact page rather than the "ten pages" the module
    docstring imagines — a bound written from ten pages would have let 5,000
    through.
    """
    chunks = chunk_document(REVISION, TRANSCRIPT)

    (carrying_the_close,) = [c for c in chunks if "closing:" in c.text]
    assert len(chunks) > 1, "a page with six subjects in it is not one passage"
    assert "opening:" not in carrying_the_close.text, (
        "and the last subject must be retrievable without the first"
    )


def test_a_document_without_headings_still_chunks() -> None:
    prose = " ".join(f"Sentence {i} of plain prose about tokens." for i in range(80))

    chunks = chunk_document(REVISION, prose, target_chars=500)

    assert len(chunks) > 1
    assert all(c.heading == "" for c in chunks)


def test_text_with_no_sentence_or_paragraph_boundaries_is_still_bounded() -> None:
    """A base64 blob, a minified file, or a long token list yields to none of
    the structural boundaries. Returning it as one unbounded chunk would spend a
    caller's entire budget on a single hit they cannot use.
    """
    blob = "alpha " * 800  # no punctuation, no blank lines

    chunks = chunk_document(REVISION, blob, target_chars=500)

    assert len(chunks) > 1
    assert all(c.char_count <= 600 for c in chunks), "bounded even with no structure"
    assert all(not c.text.startswith("lpha") for c in chunks), "never mid-word"


def test_ordinals_are_contiguous_and_start_at_zero() -> None:
    chunks = chunk_document(REVISION, "# A\n\n" + ("x " * 600) + "\n\n# B\n\n" + ("y " * 600))

    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_no_content_is_lost_when_splitting() -> None:
    """The index is derived, but silently dropping a paragraph would make a
    document unfindable by the one sentence that mattered."""
    body = "# A\n\n" + "\n\n".join(f"unique-marker-{i} " * 40 for i in range(8))

    chunks = chunk_document(REVISION, body, target_chars=400)
    combined = " ".join(c.text for c in chunks)

    assert all(f"unique-marker-{i}" in combined for i in range(8))


def test_a_nonsensical_target_is_refused() -> None:
    with pytest.raises(ChunkingError, match="at least"):
        chunk_document(REVISION, "text", target_chars=10)


def test_japanese_prose_is_split_on_its_own_sentence_marks() -> None:
    """Japanese puts no space after a full stop.

    A sentence pattern that required trailing whitespace would match nothing,
    and the word fallback splits on spaces that are not there either -- so an
    entire Japanese document would come back as one chunk. This is the case that
    only showed up by running it.
    """
    body = "認証は必ずトークンを検証する。" * 60

    chunks = chunk_document(REVISION, body, target_chars=400)

    assert len(chunks) > 1
    assert all(c.char_count <= 400 for c in chunks)


def test_text_with_no_boundaries_of_any_kind_is_still_bounded() -> None:
    """Unbroken CJK prose has no spaces and no sentence marks. Nothing above the
    hard character cut can split it, and it must still be bounded."""
    chunks = chunk_document(REVISION, "認証" * 2000, target_chars=400)

    assert len(chunks) > 1
    assert all(c.char_count <= 400 for c in chunks)


def test_an_abbreviation_does_not_split_english_prose_mid_sentence() -> None:
    """`.` splits only when whitespace follows, so "e.g." and "3.14" stay put."""
    body = "The rate is 3.14 per second, e.g. under load. " * 40

    chunks = chunk_document(REVISION, body, target_chars=500)

    assert all(not c.text.startswith(("14", "g.")) for c in chunks)


def test_the_hard_character_cut_loses_no_content() -> None:
    """`_by_length` is the last resort and the only cut that always terminates.

    Advancing its window by two targets instead of one silently dropped half of
    every document that reached it, and the whole suite stayed green: every
    chunking test asserted a *bound* on the output and none compared the output
    against the input.
    """
    body = "".join(chr(0x3042 + (index % 80)) for index in range(5_000))

    chunks = chunk_document(REVISION, body, target_chars=400)

    assert "".join(chunk.text for chunk in chunks) == body, "no character is dropped"
    assert all(chunk.char_count <= 400 for chunk in chunks)
