"""What ADR-0008 and ``raptor.md`` claim about who reads ``.theurian/config.yaml`` (#426).

Both documents recorded that **nothing in ``src/`` reads
``.theurian/config.yaml``**. That was true when each was written and stopped
being true with [ADR-0027](../../../../docs/adr/0027-accept-validates-before-it-moves.md)
decision 3: ``security/project_config.py::read_secret_scan_policy`` opens the
file, and ``application/proposal_service.py`` calls it at ``theurian propose
accept``. Neither conclusion leaned on the file being unread — both leaned on the
``raptor`` keys being unread — so #426 narrowed the premise rather than deleting
the paragraphs:

- ``docs/architecture/raptor.md``: "The threshold is real now and no ``raptor``
  key is read", followed by the reader the file *does* have, then the same
  conclusion — the CLI flag is the switch and the config key is not.
- ``docs/adr/0008-raptor-forest.md`` decision 10: "Nothing in ``src/`` reads
  ``raptor.enabled``, nor any other key in the ``raptor`` block", with a dated
  correction blockquote recording what the two sentences said and why the
  conclusions survive.

**A narrowed never-claim is still a never-claim, and it needs both halves.** The
fact half lives in ``tests/unit/test_config_key_call_sites.py``, whose scan
watches every key the schema publishes under ``raptor`` — derived from the schema,
not transcribed — so the day a loader names one, that enumeration goes RED. This
module holds the *prose*, which that scan cannot reach: a document rewritten back
to the universal makes a durable architectural record false again while no reader
was added anywhere, so nothing in ``src/`` moves and every structural test stays
green. That is the #198 failure shape (round-two mutations B1-B4, recorded in
``test_config_key_call_sites.py``) arriving from the documentation side.

**What this module enforces, which is narrower than "the record is true".** It is
a wording pin over two files:

- The **positive** half requires the narrowed sentences to still be there. A
  rewrite that simply deletes them asserts nothing false and would pass the
  negative half while leaving both records silent about a key an operator can set
  in the sample config and that nothing acts on.
- The **negative** half refuses the retracted universal in the two forms it
  actually took — "nothing … reads ``.theurian/config.yaml``" in the present
  tense, and "the config file is still unread". Measured escapes, recorded rather
  than chased, because a rule that pins grammar always has a next grammar: the
  past tense (which the corrected rationale deliberately uses, and which must
  keep passing), "nothing *opens* it", "no loader exists", "the file is not
  read", and "no config surface".
- It says nothing at all about the source tree. Whether a reader exists is
  ``test_config_key_call_sites.py``'s question; this module would stay green
  against a build that shipped a raptor loader and left both documents alone —
  which is exactly why the two halves are separate tests and neither is
  sufficient.

**Scoped to the two source documents, never a repo-wide walk.** The served corpus
carries governed snapshots of both —
``.theurian/knowledge/architecture/raptor-forest.<ulid>.md`` still says "Nothing
in ``src/`` reads ``.theurian/config.yaml``, so flipping the default" — held
byte-identical to their source anchor commits by
``test_dogfood_corpus_governance.py``, so only a governed re-seed can move them
(#199 unit C). A tree scan for this wording would go RED on those files on the
day it was written. The ADR-0008 scan additionally skips the dated correction
paragraph, which quotes the retracted sentence verbatim in order to retract it;
that paragraph is asserted to exist rather than assumed, so the exclusion cannot
silently widen.

**One sentence of the same class survives outside #426's scope, and it is
recorded rather than hidden** — see :data:`UNNARROWED_UNIVERSALS`. The scan is
file-wide and holds an *exact* ledger, so a new occurrence and a fixed one both
go RED.

Pure: it reads two Markdown files as text and opens no database, no socket and no
temporary directory.
"""

from __future__ import annotations

import pathlib
import re
from typing import Final

import pytest

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` → ``tests`` → ``theurian-core`` →
#: ``packages`` → repo root, the same reckoning ``test_adr_0018_claims.py`` uses.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

ADR_0008: Final = REPO_ROOT / "docs" / "adr" / "0008-raptor-forest.md"
RAPTOR_MD: Final = REPO_ROOT / "docs" / "architecture" / "raptor.md"

#: Leading Markdown blockquote markers, however deeply nested.
#:
#: **Stripped before anything else looks at a line**, and that is not cosmetic:
#: the whole of ADR-0008 decision 10 lives inside a ``>`` amendment block, so a
#: block-boundary rule that treats ``>`` as the start of a new block — which
#: ``test_adr_0018_claims.py``'s does, correctly, for a document whose claim sits
#: outside one — would make every wrapped line its own paragraph here. A sentence
#: that spans a soft wrap would then never be seen whole, and this module's
#: pins would pass vacuously against wording being rewritten around them.
_BLOCKQUOTE_MARKERS: Final = re.compile(r"^(?:[ \t]*>)+[ \t]?")

#: A line that begins a new block rather than continuing the one above it,
#: applied *after* the markers are stripped. ``>`` is deliberately absent from
#: this list for the reason above. Otherwise the same rule as
#: ``test_setup_claims.py`` and ``test_adr_0018_claims.py``.
_BLOCK_START: Final = re.compile(r"[ \t]*(?:#{1,6}\s|[-*+]\s|\d+\.\s|\||```|---\s*$)")

#: The end of a sentence, which is not every period. ``.theurian/config.yaml``
#: carries one that ends nothing, and so does every Markdown link — the trap the
#: ADR-0013 and ADR-0018 modules both record, and the reason the lookahead
#: demands whitespace.
_SENTENCE_END: Final = re.compile(r"\.(?=\s|$)")

#: The retracted universal, in the two shapes it actually took.
#:
#: 1. *"nothing in `src/` reads `.theurian/config.yaml`"* — ADR-0008 decision
#:    10's rationale and its "switch is the CLI flag" note, and the surviving
#:    sentence in :data:`UNNARROWED_UNIVERSALS`.
#: 2. *"the config file is still unread"* — ``raptor.md``'s section heading
#:    sentence.
#:
#: **``reads``, never ``read``**, and the tense is the whole point rather than an
#: accident of spelling. The corrected rationale says "when this was written
#: nothing read ``.theurian/config.yaml`` at all" — a *tensed* claim about the
#: past, which is true and must keep passing. That sentence also contains
#: "nothing in ``src/`` reads it" a few words earlier, about ``raptor.enabled``,
#: which is likewise true; a rule that fired on ``nothing … reads … config.yaml``
#: with a loose gap would read the two halves as one claim and go RED on the
#: correction. So the verb has to sit against its object: the gap is bounded and
#: the file name must follow ``reads`` directly.
#:
#: The second alternative refuses "is unread" and deliberately not "is not
#: unread", which is how ``raptor.md`` now opens its narrowing paragraph.
_FILE_UNREAD: Final = re.compile(
    r"\b(?:nothing|nobody|none|no code|no module)\b[^\n]{0,40}?"
    r"\breads\b\s+(?:the\s+)?`?\.?theurian/config\.yaml"
    r"|`?\.?theurian/config\.yaml`?[^.]{0,20}\bis\s+(?:still\s+)?unread\b"
    r"|\bconfig(?:uration)?\s+file\s+is\s+(?:still\s+)?unread\b",
    re.IGNORECASE,
)

#: The dated correction note, identified by the issue that owns it.
#:
#: Its whole job is to quote the retracted sentence, so a file-wide scan would
#: report the amendment that fixed the defect as the defect returning — the trap
#: ``test_adr_0018_claims.py`` records for ``_decision_point_two``, met here at
#: paragraph rather than section granularity. Keyed on ``issues/426`` rather than
#: on the words "corrected in", because ADR-0008 carries six paragraphs opening
#: with a correction or landing note and excluding all six would blind the scan to
#: most of the document.
_CORRECTION_NOTE: Final = re.compile(r"\bissues/426\b", re.IGNORECASE)

#: Sentences ``raptor.md`` has to keep, whitespace-collapsed.
#:
#: Three sentences and not one, because the narrowing is an argument and each
#: piece of it can be dropped on its own: the claim, the reader that bounds it,
#: and the conclusion the claim was there to support. A rewrite that keeps only
#: the conclusion leaves a reader no way to check it.
RAPTOR_MD_SENTENCES: Final = (
    "**The threshold is real now and no `raptor` key is read.**",
    "`security/project_config.py` opens it for `security.secretScan` alone",
    "What is unread is the `raptor` block",
    "the CLI flag is the switch and the config key is not",
)

#: Sentences ADR-0008 decision 10 has to keep, whitespace-collapsed.
#:
#: The first is the narrowed claim itself. The second is the correction note's
#: evidence: without a named reader, "the ``raptor`` block is unread" is an
#: assertion a future reader can only re-derive by grep, which is the habit #426
#: exists to break.
DECISION_TEN_SENTENCES: Final = (
    "Nothing in `src/` reads `raptor.enabled`, nor any other key in the `raptor` block",
    "`security/project_config.py::read_secret_scan_policy` opens the file",
)

#: The sentences of this class still carrying the retracted universal, as
#: ``(where it is, a fragment that identifies it)``.
#:
#: **This is a debt ledger, not an allowlist.** #426 narrowed the two sentences
#: its commit named — decision 10's rationale and its "switch is the CLI flag"
#: note — and the same wording survives once more in the same file, in the
#: amendment to decision 3, where it is the premise of a conclusion that is still
#: true ("an operator cannot yet move it"). It is the same defect: a universal
#: falsified by ADR-0027 decision 3, left standing.
#:
#: It is recorded here rather than left outside the scan's reach because the
#: alternative — scoping the scan to decision 10 — makes the survivor invisible
#: to the suite and to whoever reads it next. The assertion below is an exact
#: match in both directions, so **correcting the sentence makes this module RED**
#: with an instruction to delete the row. That is intended: a ledger nobody has to
#: empty is a ledger that grows.
UNNARROWED_UNIVERSALS: Final[tuple[tuple[str, str], ...]] = (
    (
        "ADR-0008, the amendment to decision 3 (the `minChildrenPerSummary` threshold note)",
        "nothing reads `.theurian/config.yaml`, so an operator cannot yet move it",
    ),
)

#: One case per form the scan claims to catch, and per form it claims to let past.
#:
#: Both halves are load-bearing and the negatives carry the harder half: every
#: one of them is transcribed from the corrected documents, so a scan that read
#: any of them as the universal returning would be RED on a clean tree, and the
#: only way to make it green again would be to un-narrow the prose this module
#: exists to protect.
#:
#: Without these, the two scans below could go green with a pattern that matches
#: nothing at all — the failure mode a pin whose expected result is the empty set
#: has no other way to detect, and the one
#: ``test_adr_0018_claims.py::test_the_filesystem_api_sweep_catches_a_probe_in_synthetic_source``
#: was written for.
UNIVERSAL_CASES: Final[tuple[tuple[str, bool], ...]] = (
    # -- the retracted wording, as each document actually carried it ---------
    (
        "Nothing in `src/` reads `.theurian/config.yaml`, so flipping the default "
        "changes no behaviour",
        True,
    ),
    (
        "That is not a decision anyone took: nothing in `src/` reads it, or reads "
        "`.theurian/config.yaml` at all",
        True,
    ),
    ("nothing reads `.theurian/config.yaml`, so an operator cannot yet move it", True),
    ("**The threshold is real now and the config file is still unread.**", True),
    ("No module reads the .theurian/config.yaml file", True),
    # -- the narrowed wording, which must keep passing -----------------------
    (
        "That is not a decision anyone took: nothing in `src/` reads it, and when this "
        "was written nothing read `.theurian/config.yaml` at all",
        False,
    ),
    (
        "`.theurian/config.yaml` is not unread -- `security/project_config.py` opens it "
        "for `security.secretScan` alone",
        False,
    ),
    ("**The threshold is real now and no `raptor` key is read.**", False),
    (
        "Nothing in `src/` reads `raptor.enabled`, nor any other key in the `raptor` block",
        False,
    ),
    ("What is unread is the `raptor` block", False),
    (
        "`tests/unit/test_config_key_call_sites.py` is what holds the source tree to that one key",
        False,
    ),
)


def _collapsed(text: str) -> str:
    """Runs of whitespace flattened to single spaces, case preserved.

    Case is kept, unlike ``test_adr_0018_claims.py``'s equivalent, because the
    sentences pinned here carry identifiers — ``security.secretScan``,
    ``raptor.enabled`` — that a lowercasing collapse would render as spellings
    neither the schema nor the source uses, and a needle nobody can search for is
    a needle nobody maintains. The patterns that need to ignore case say so with
    :data:`re.IGNORECASE`.
    """
    return " ".join(text.split())


def _paragraphs(text: str) -> list[str]:
    """The document's paragraphs, blockquote markers stripped and soft wraps joined.

    A scan that stops at every newline never sees a sentence that spans a soft
    wrap, and both claims here do. A scan that ignores newlines entirely reads the
    next bullet into this one, letting a re-added universal borrow the
    neighbouring paragraph's exclusion. Blocks are the unit in between.
    """
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        line = _BLOCKQUOTE_MARKERS.sub("", raw)
        if not line.strip() or _BLOCK_START.match(line):
            blocks.append([])
        blocks[-1].append(line)

    return [collapsed for block in blocks if (collapsed := _collapsed(" ".join(block)))]


def _unread_file_claims(text: str) -> list[str]:
    """Every sentence outside a dated correction note that says the config file is unread."""
    claims: list[str] = []
    for paragraph in _paragraphs(text):
        if _CORRECTION_NOTE.search(paragraph):
            continue
        claims.extend(
            sentence.strip()
            for sentence in _SENTENCE_END.split(paragraph)
            if _FILE_UNREAD.search(sentence)
        )
    return claims


def _decision_ten(text: str) -> str:
    """ADR-0008 Decision 10 and its amendments, as one collapsed run of paragraphs.

    Bounded by structure rather than by line numbers: it starts at the numbered
    item that names ``raptor.enabled``'s default and ends at the next heading or
    numbered item, so inserting an amendment inside the decision extends the
    region rather than escaping it.

    Asserted to be findable exactly once. A decision that cannot be located is not
    a decision whose wording passed — it is a scan with nothing to read, and this
    module's positive pins would report that as compliance.
    """
    paragraphs = _paragraphs(text)
    starts = [
        index
        for index, paragraph in enumerate(paragraphs)
        if paragraph.startswith("10.") and "`raptor.enabled` defaults to" in paragraph
    ]

    assert len(starts) == 1, (
        f"ADR-0008 Decision 10 is not findable as exactly one numbered paragraph "
        f"(found {len(starts)}); the pins below would read the wrong region"
    )

    start = starts[0]
    end = next(
        (
            index
            for index in range(start + 1, len(paragraphs))
            if paragraphs[index].startswith("#") or re.match(r"\d+\.\s", paragraphs[index])
        ),
        len(paragraphs),
    )
    return " ".join(paragraphs[start:end])


# -- The scanner, which the two absence pins below are worthless without ------


@pytest.mark.parametrize(
    ("sentence", "is_the_universal"),
    UNIVERSAL_CASES,
    ids=[case[0][:60] for case in UNIVERSAL_CASES],
)
def test_the_unread_file_scan_sees_the_retracted_wording_and_not_the_narrowed_one(
    sentence: str, is_the_universal: bool
) -> None:
    """RED means the scan stopped discriminating, so the pins below assert nothing.

    A pin whose expected result is the empty set cannot tell a clean document from
    a dead pattern: both report "no universal found". The positives here are the
    sentences ADR-0008 and ``raptor.md`` actually carried before #426; the
    negatives are the sentences they carry now.

    The pair that matters most is the rationale, before and after. Both versions
    contain "nothing in ``src/`` reads" *and* ``.theurian/config.yaml``; what
    separates them is one letter of tense and which noun the verb sits against.
    A looser pattern reads them as the same sentence and goes RED on the
    correction — the exact false-RED that would get this module deleted.
    """
    found = bool(_FILE_UNREAD.search(_collapsed(sentence)))

    assert found is is_the_universal, (
        f"the unread-file scan read {sentence!r} as "
        f"{'the retracted universal' if found else 'acceptable wording'}, expected the "
        f"opposite. The scanner is broken, not the documents: fix `_FILE_UNREAD` before "
        f"trusting a green result from "
        f"`test_no_document_reasserts_that_nothing_in_src_reads_the_config_file`, which "
        f"would keep passing against a pattern that matches nothing."
    )


# -- docs/architecture/raptor.md ---------------------------------------------


def test_raptor_md_says_the_raptor_block_is_what_is_unread_and_names_the_reader() -> None:
    """RED means the narrowing is gone — deleted, or softened back to a claim with no evidence.

    The positive half for ``raptor.md``, and it is not the negative one restated.
    A rewrite that drops the paragraph entirely asserts nothing false and would
    pass :func:`test_no_document_reasserts_that_nothing_in_src_reads_the_config_file`
    while leaving the file silent about a key the sample config sets and nothing
    acts on — which is the state that produced ADR-0008 decision 10 in the first
    place.

    The reader is required by name alongside the claim. "No ``raptor`` key is
    read" with no statement of who *does* read the file is a bare assertion, and a
    bare assertion is what #426 found: the sentence had been true, nobody
    re-derived it, and it stayed for a milestone after ADR-0027 decision 3
    falsified it.
    """
    document = _collapsed(RAPTOR_MD.read_text(encoding="utf-8"))

    for sentence in RAPTOR_MD_SENTENCES:
        assert sentence in document, (
            f"docs/architecture/raptor.md no longer states {sentence!r}.\n\n"
            f"That sentence is one leg of #426's narrowing: the claim (`no `raptor` key "
            f"is read`), the reader that bounds it (`security/project_config.py`, "
            f"ADR-0027 decision 3), and the conclusion it supports (the CLI flag is the "
            f"switch). If a raptor config loader has landed, "
            f"`tests/unit/test_config_key_call_sites.py` records the reader and this "
            f"paragraph has to be corrected in the same change; if it has not, restore "
            f"the sentence."
        )


# -- docs/adr/0008-raptor-forest.md ------------------------------------------


def test_adr_0008_decision_ten_says_no_key_in_the_raptor_block_is_read() -> None:
    """RED means decision 10 stopped bounding its own never-claim.

    The positive half for the ADR. Decision 10 decides the *default* of a key, so
    it is phrased throughout in terms of a key nothing reads; if the sentence
    stating that is dropped, the decision reads as though flipping the default did
    something, which is the misreading its own note exists to prevent.

    The correction note's named reader is required with it, for the reason the
    ``raptor.md`` pin gives: the narrowing is only checkable if the document says
    what it was narrowed *to*.
    """
    decision = _decision_ten(ADR_0008.read_text(encoding="utf-8"))

    for sentence in DECISION_TEN_SENTENCES:
        assert sentence in decision, (
            f"ADR-0008 Decision 10 no longer states {sentence!r}.\n\n"
            f"#426 narrowed this decision's two `nothing in `src/` reads "
            f"`.theurian/config.yaml`` sentences to the `raptor` block, which is the "
            f"population that is still unread. `tests/unit/test_config_key_call_sites.py` "
            f"holds the source tree to it. If a loader has landed, correct the decision "
            f"in the same change; if it has not, restore the sentence."
        )


# -- Both documents: the universal must not come back ------------------------


def test_no_document_reasserts_that_nothing_in_src_reads_the_config_file() -> None:
    """RED means the retracted universal is back, or a recorded survivor was fixed.

    The negative half, and it catches what the positive one cannot: a file that
    keeps "no ``raptor`` key is read" and reasserts the file-wide claim somewhere
    else. Two sentences of ADR-0008 read that way for a milestone after ADR-0027
    decision 3 shipped a reader, and neither was noticed by anything.

    **An exact ledger in both directions.** A sentence the scan finds that is not
    in :data:`UNNARROWED_UNIVERSALS` is the universal returning. A recorded entry
    the scan no longer finds means someone fixed it — which is good news and still
    RED, because a ledger that keeps stale rows stops being read. Delete the row
    in the same change.

    Scoped to the two source documents. The served corpus carries governed
    snapshots of both, still holding the retracted wording byte-identically by
    design (#199 unit C), so a repo-wide walk over this pattern would report those
    anchors as drift.
    """
    found = [
        (path.name, sentence)
        for path in (ADR_0008, RAPTOR_MD)
        for sentence in _unread_file_claims(path.read_text(encoding="utf-8"))
    ]

    new = [
        (name, sentence)
        for name, sentence in found
        if not any(fragment in sentence for _, fragment in UNNARROWED_UNIVERSALS)
    ]
    assert not new, (
        "a document asserts again that nothing in `src/` reads `.theurian/config.yaml`, "
        "which ADR-0027 decision 3 falsified -- `security/project_config.py` opens it "
        "for `security.secretScan`:\n"
        + "\n".join(f"  {name}: {sentence}" for name, sentence in new)
        + "\n\nNarrow the sentence to the population that is still unread (the `raptor` "
        "block) rather than deleting it; #426 is the change that did this for decision "
        "10 and for docs/architecture/raptor.md."
    )

    fixed = [
        (where, fragment)
        for where, fragment in UNNARROWED_UNIVERSALS
        if not any(fragment in sentence for _, sentence in found)
    ]
    assert not fixed, (
        "a recorded un-narrowed universal is no longer in the document, so the ledger "
        "in `UNNARROWED_UNIVERSALS` has a stale row:\n"
        + "\n".join(f"  {where}: {fragment}" for where, fragment in fixed)
        + "\n\nThis is the good direction -- delete the row. The ledger is exact so that "
        "the debt it records has to be discharged rather than accumulated."
    )


def test_the_correction_note_that_the_scan_skips_is_actually_there() -> None:
    """RED means the exclusion above stopped being about anything, and widened silently.

    :func:`_unread_file_claims` skips paragraphs naming issue #426, because the
    dated correction note quotes the retracted sentence in order to retract it and
    a scan that read the quotation would report the fix as the defect.

    An exclusion nobody checks is the same shape as a population nobody counts.
    If the note is deleted or its issue reference is dropped, the skip stops
    matching that paragraph -- which would make the scan *wider*, not narrower,
    and the surviving universal there would be reported as new. Asserting the note
    exists is what keeps the exclusion honest and keeps the record findable: the
    only place ADR-0008 says why those two sentences changed.
    """
    notes = [
        paragraph
        for paragraph in _paragraphs(ADR_0008.read_text(encoding="utf-8"))
        if _CORRECTION_NOTE.search(paragraph)
    ]

    assert len(notes) == 1, (
        f"ADR-0008 carries {len(notes)} paragraphs naming issue #426, expected exactly "
        f"one -- the dated correction note that records what decision 10's two "
        f"`nothing in `src/` reads `.theurian/config.yaml`` sentences said and why the "
        f"conclusions survive their narrowing"
    )
    assert "ADR-0027" in notes[0], (
        f"the correction note no longer names ADR-0027, which is what falsified the "
        f"retracted sentences: {notes[0]}"
    )
