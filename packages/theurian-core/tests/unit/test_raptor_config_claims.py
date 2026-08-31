"""What three files claim about who reads ``.theurian/config.yaml`` (#426).

All three recorded that **nothing in ``src/`` reads ``.theurian/config.yaml``**.
That was true when each was written and stopped being true with
[ADR-0027](../../../../docs/adr/0027-accept-validates-before-it-moves.md)
decision 3: ``security/project_config.py::read_secret_scan_policy`` opens the
file, and ``application/proposal_service.py`` calls it at ``theurian propose
accept``. No conclusion leaned on the file being unread — each leaned on the
``raptor`` keys, or on ``providers.review.repositories``, being unread — so #426
narrowed the premises rather than deleting the paragraphs:

- ``docs/architecture/raptor.md``: "The threshold is real now and no ``raptor``
  key is read", followed by the reader the file *does* have, then the population
  that is unread, then the same conclusion — the CLI flag is the switch and the
  config key is not.
- ``docs/adr/0008-raptor-forest.md`` decision 10: "Nothing in ``src/`` reads
  ``raptor.enabled``, nor any other key in the ``raptor`` block", with a dated
  correction blockquote recording what the two sentences said and why the
  conclusions survive.
- ``docs/adr/0008-raptor-forest.md``'s amendment to decision 3 — the
  ``minChildrenPerSummary`` threshold note — reached "an operator cannot yet move
  it" from the same retracted premise. It now reaches it from "no key in the
  ``raptor`` block has a reader in ``src/``".
- ``examples/sample-project/.theurian/config.yaml``: the annotation above
  ``providers.review.repositories`` said "Nothing in ``src/`` reads this file".
  It now names the reader the file has and the key that has none.

**A narrowed never-claim is still a never-claim, and it needs both halves.** The
fact half lives in ``tests/unit/test_config_key_call_sites.py``, whose scan
watches every key the schema publishes under ``raptor`` — derived from the
schema, not transcribed — and holds an exact equality over ``(module, spelling)``
pairs. So a loader that names a key in the shape a loader must — the published
JSON spelling ``"enabled"``/``"maxLevels"``/``"minChildrenPerSummary"``, or the
SCREAMING_SNAKE constant a module would hold it in — adds a pair anywhere in the
package and that enumeration goes RED.

**The measured gap in that tripwire, which this docstring used to overstate.**
Two snake_case pairs already exist for ``application/forest_builder.py``:
``max_levels`` and ``min_children_per_summary``, the ``ForestOptions`` fields
carrying the schema's *defaults*. A loader added *in that one module* that binds
only those two names therefore adds no new pair and the enumeration stays green.
Round-one mutation A1 — a config read bound to ``max_levels`` inside
``forest_builder.py`` — SURVIVED for exactly that reason; A2, the same read in
another module, was KILLED. Every other combination trips it: any spelling of
``enabled`` (no ``enabled`` pair exists at all), any JSON or SCREAMING spelling
anywhere, and either snake name in any module other than ``forest_builder.py``.
The watch is not narrowed to close A1, because separating "a ``ForestOptions``
field" from "a config read" inside one module needs semantics that scan
deliberately refuses (see its own ordinary-words rule).

This module holds the *prose*, which that scan cannot reach: a document rewritten
back to the universal makes a durable architectural record false again while no
reader was added anywhere, so nothing in ``src/`` moves and every structural test
stays green. That is the #198 failure shape (round-two mutations B1-B4, recorded
in ``test_config_key_call_sites.py``) arriving from the documentation side.

**What this module enforces, which is narrower than "the record is true".** It is
a wording pin over three files:

- The **positive** half requires the narrowed sentences to still be there. A
  rewrite that simply deletes them asserts nothing false and would pass the
  negative half while leaving the records silent about a key an operator can set
  in the sample config and that nothing acts on. Each positive pin is scoped to
  the region that carries the claim — ``raptor.md``'s "Three levels" section,
  ADR-0008 decision 10, ADR-0008's amendment to decision 3 — so a sentence
  surviving somewhere else in the file does not satisfy it.
- The **negative** half refuses the retracted universal in the three forms it
  actually took — "nothing … reads ``.theurian/config.yaml``" in the present
  tense, "the config file is still unread", and, in the sample config only,
  "nothing in ``src/`` reads this file". Measured escapes, recorded rather than
  chased, because a rule that pins grammar always has a next grammar: the past
  tense (which the corrected rationale deliberately uses, and which must keep
  passing), "nothing *opens* it", "no loader exists", "the file is not read", and
  "no config surface".
- It says nothing at all about the source tree. Whether a reader exists is
  ``test_config_key_call_sites.py``'s question; this module would stay green
  against a build that shipped a raptor loader and left all three files alone —
  which is exactly why the two halves are separate tests and neither is
  sufficient.

**Scoped to three source files, never a repo-wide walk.** The served corpus
carries governed snapshots of the two Markdown documents —
``.theurian/knowledge/architecture/raptor-forest.<ulid>.md`` still says "Nothing
in ``src/`` reads ``.theurian/config.yaml``, so flipping the default" — held
byte-identical to their source anchor commits by
``test_dogfood_corpus_governance.py``, so only a governed re-seed can move them
(#199 unit C). A tree scan for this wording would go RED on those files on the
day it was written.

**The dated correction notes are excluded by quotation, not by paragraph.** A
note quotes the retracted sentence in order to retract it, so a scan that read
the quotation would report the fix as the defect. Skipping the whole note
paragraph closed that at the cost of a blind spot the size of the note: round-one
mutation A7 put a *live* reassertion inside ADR-0008's twenty-line note and
nothing saw it, while the same sentence one paragraph away was caught. So a note
paragraph is scanned with its quoted spans — italic ``*…*``, ``"…"`` — excised
first, and the note is asserted to exist rather than assumed, so the exclusion
cannot silently widen.

**The ledger is empty, and that is a measured state rather than a scope
boundary** — see :data:`UNNARROWED_UNIVERSALS`. Four sentences of this class are
live outside the three files scanned here, none of them reachable from this
module:

- ``application/forest_builder.py``, ``tests/unit/test_forest_derivation.py`` and
  ``tests/unit/test_schemas.py`` (#447) — Python, not the Markdown and YAML this
  module reads.
- the wheel-shipped root ``description`` in
  ``schemas/config/project-config.schema.json`` (#455) — a JSON string, not one of
  the three files scanned here. Measured at ``f205735``, its sentence "Nothing in
  src/ reads this file" is invisible to :data:`_FILE_UNREAD`, which requires the
  path after the verb, and visible to :data:`_THIS_FILE_UNREAD`, which is confined
  to the sample config because the pronoun's referent is only unambiguous there.
  Extending that pattern to the schema is #199 unit B's, with the file: #455
  carries the delimiter-tolerant sweep key that found it in the first place.

Pure: it reads two Markdown files and one YAML file as text, and opens no
database, no socket and no temporary directory.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable, Sequence
from typing import Final

import pytest

pytestmark = pytest.mark.unit

#: ``parents[4]`` is ``.../tests/unit/`` → ``tests`` → ``theurian-core`` →
#: ``packages`` → repo root, the same reckoning ``test_adr_0018_claims.py`` uses.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

ADR_0008: Final = REPO_ROOT / "docs" / "adr" / "0008-raptor-forest.md"
RAPTOR_MD: Final = REPO_ROOT / "docs" / "architecture" / "raptor.md"
SAMPLE_CONFIG: Final = REPO_ROOT / "examples" / "sample-project" / ".theurian" / "config.yaml"

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

#: An ATX heading at depth two, which is what bounds a section of ``raptor.md``.
#:
#: ``\s`` after the two hashes is what keeps ``### Negative`` from reading as a
#: section boundary. A fenced block containing a line that starts ``## `` would
#: end a section early, which makes a positive pin RED rather than green — the
#: safe direction, and the reason this is not defended further.
_SECTION_HEADING: Final = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)

#: An HTML comment, removed before a positive pin reads a section.
#:
#: Markdown renders nothing inside one, so a record whose sentences live in a
#: comment is a silent record. Round-one mutation A6 moved all four of
#: ``raptor.md``'s pinned sentences into a comment and both halves of this module
#: stayed green.
_HTML_COMMENT: Final = re.compile(r"<!--.*?-->", re.DOTALL)

#: Bold markers, which are emphasis rather than quotation and are removed before
#: :data:`_QUOTATION` looks for italic spans — otherwise ``**bold**`` reads as an
#: italic span and a live claim written in bold inside a correction note would be
#: excised along with the quotations.
_EMPHASIS: Final = re.compile(r"\*\*")

#: A quoted span inside a dated correction note: an italic run or a double-quoted
#: run. Both are the forms the notes actually use — ADR-0008's decision-10 note
#: quotes in italics, ADR-0024's decision-2 note quotes in double quotes — and
#: excising them is what lets a note be *scanned* rather than skipped whole.
_QUOTATION: Final = re.compile(r"\*[^*]+\*|\"[^\"]+\"|“[^”]+”")

#: The retracted universal, in the two shapes it took in the Markdown documents.
#:
#: 1. *"nothing in `src/` reads `.theurian/config.yaml`"* — ADR-0008 decision
#:    10's rationale, its "switch is the CLI flag" note, and the amendment to
#:    decision 3.
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

#: The third shape, and the reason it is applied to the sample config alone.
#:
#: The annotation the sample config carried said "Nothing in ``src/`` reads **this
#: file**" — a pronoun, never the path, so :data:`_FILE_UNREAD` cannot see it.
#: Inside ``config.yaml`` the pronoun has one possible referent and the rule is
#: safe. In ``raptor.md`` or an ADR it does not: "this file" there means the
#: document, and a sentence about a document that reads nothing is not this
#: claim. Applying the pattern to all three surfaces would buy one shape at the
#: cost of a false RED on prose that is true.
_THIS_FILE_UNREAD: Final = re.compile(
    r"\b(?:nothing|nobody|none|no code|no module)\b[^\n]{0,40}?"
    r"\breads\b\s+(?:this|that|the)\s+(?:config(?:uration)?\s+)?file\b",
    re.IGNORECASE,
)

#: The dated correction note, identified by the issue that owns it.
#:
#: Keyed on the ``issues/426`` **link form** rather than on the words "corrected
#: in", because ADR-0008 carries six paragraphs opening with a correction or
#: landing note and excluding all six would blind the scan to most of the
#: document.
#:
#: The bare ``#426`` spelling is deliberately outside this key, and ADR-0008
#: carries one paragraph in that form — the note under the amendment to decision
#: 3, which quotes nothing retracted and therefore needs no quotation handling.
#: Writing the link form there would make
#: :func:`test_the_correction_note_that_bounds_the_scan_is_actually_there` RED,
#: which is the intended signal: the count is the exclusion's own control.
_CORRECTION_NOTE: Final = re.compile(r"\bissues/426\b", re.IGNORECASE)

#: The ``raptor.md`` section that carries the narrowing.
_THREE_LEVELS: Final = "Three levels"

#: Sentences ``raptor.md``'s "Three levels" section has to keep, whitespace-collapsed.
#:
#: Four, one per moving part of the argument, because each can be dropped on its
#: own: the narrowed claim, the reader that bounds it, the population that is
#: unread, and the conclusion the claim was there to support. The third is the
#: one a rewrite drops most easily and the one that decides the record's meaning
#: — without it the paragraph names a reader and never says what is left over. A
#: rewrite that keeps only the conclusion leaves a reader no way to check it.
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

#: Sentences ADR-0008's amendment to decision 3 has to keep, whitespace-collapsed.
#:
#: **This is what discharging a ledger row looks like.** The row
#: :data:`UNNARROWED_UNIVERSALS` held was this clause, which reached "an operator
#: cannot yet move it" from the retracted file-wide premise. Deleting the row once
#: the sentence was narrowed would have left the *fixed* sentence watched by
#: nothing at all, and a gutted version — the bare conclusion with no population
#: and no named reader — would then pass every test in this module. So the row was
#: promoted to a positive pin of the same shape decision 10 has: the narrowed
#: claim, and the reader that bounds it.
DECISION_THREE_SENTENCES: Final = (
    "no key in the `raptor` block has a reader in `src/`, so an operator cannot yet move it",
    (
        "The one reader `.theurian/config.yaml` has takes `security.secretScan` from it "
        "and nothing else"
    ),
)

#: The sentences of this class still carrying the retracted universal, as
#: ``(where it is, a fragment that identifies it)``.
#:
#: **This is a debt ledger, not an allowlist, and it is empty.** It held one row
#: when this module landed: the amendment to ADR-0008 decision 3, where the same
#: universal #426 narrowed in decision 10 survived as the premise of a conclusion
#: that is still true ("an operator cannot yet move it"). Recording it here rather
#: than scoping the scan to decision 10 is what kept it visible to the suite.
#:
#: **A row is discharged by promotion, never by deletion.** That row's narrowed
#: replacement is :data:`DECISION_THREE_SENTENCES`, pinned in the same PR that
#: emptied the ledger. Deleting a row on its own trades a watched wrong sentence
#: for an unwatched right one, and the right one is then free to be gutted back to
#: its bare conclusion — measured during round one, where exactly that rewrite
#: left fifteen tests here green. The next survivor of this class is added here
#: and discharged the same way: narrow the sentence, pin the narrowed form, delete
#: the row.
#:
#: The assertion below is an exact match in both directions, which is what makes
#: the promotion unskippable: **correcting the sentence makes this module RED**
#: with an instruction to delete the row. That is intended, and it is the reason
#: this stays a tuple rather than becoming a comment — a ledger nobody has to
#: empty is a ledger that grows.
UNNARROWED_UNIVERSALS: Final[tuple[tuple[str, str], ...]] = ()

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
        "no key in the `raptor` block has a reader in `src/`, so an operator cannot yet move it",
        False,
    ),
    (
        "`tests/unit/test_config_key_call_sites.py` is what holds the source tree to that one key",
        False,
    ),
)

#: One case per form the sample config's pronoun scan claims to catch, and per
#: form it claims to let past.
#:
#: The first positive is the exact sentence the example carried before #426. The
#: negatives are transcribed from the annotation it carries now, so a pattern that
#: misread one would be RED on a clean tree.
PRONOUN_CASES: Final[tuple[tuple[str, bool], ...]] = (
    (
        "The allowlist review ingestion will read (SEC-10). Nothing in `src/` reads this "
        "file, so the allowlist is not in force; review ingestion is owed with Milestone "
        "7 (#129).",
        True,
    ),
    ("No module reads that configuration file", True),
    ("nothing in `src/` reads the file", True),
    # -- the annotation the example carries now, which must keep passing -----
    (
        "This file is read -- `security/project_config.py` opens it for "
        "`security.secretScan` alone -- but nothing in `src/` reads "
        "`providers.review.repositories`, so the allowlist is not in force.",
        False,
    ),
    (
        "It is owed with the first external fetch path (#429 owns it; #129 was closed on "
        "the wording rather than the control).",
        False,
    ),
    ("Every provider defaults to a deterministic in-tree implementation.", False),
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


def _comment_blocks(text: str) -> list[str]:
    """A YAML document's contiguous runs of ``#`` comment lines, joined and collapsed.

    The unit :func:`_paragraphs` is for Markdown, in the shape the sample config
    takes: its annotations are comment blocks, each broken across four or five
    lines, and a per-line scan would never see a sentence whole.

    **Comments only, which is the recorded bound.** A YAML *value* asserting that
    nothing reads the file is not a shape this reads — the file's prose lives in
    its annotations, and the keys it sets are booleans, integers and enumerated
    strings.
    """
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            blocks[-1].append(stripped.lstrip("#").strip())
        else:
            blocks.append([])

    return [collapsed for block in blocks if (collapsed := _collapsed(" ".join(block)))]


def _without_quotations(text: str) -> str:
    """``text`` with its quoted spans replaced by a space.

    Applied to a dated correction note before the note is scanned, so that the
    retracted sentence the note quotes is invisible while anything the note
    *asserts* is not. Emphasis markers go first: ``**bold**`` would otherwise read
    as an italic span, and a live reassertion written in bold would be excised
    with the quotations — which is the hole this function exists to close, not one
    to reintroduce.
    """
    return _QUOTATION.sub(" ", _EMPHASIS.sub("", text))


def _unread_claims(blocks: Sequence[str], patterns: Sequence[re.Pattern[str]]) -> list[str]:
    """Every sentence in ``blocks`` that one of ``patterns`` reads as the retracted claim.

    A dated correction note is scanned with its quotations excised rather than
    skipped whole. Sentences are split *after* the excision, so a quotation
    removed from the middle of a paragraph cannot join two neighbouring sentences
    into one that matches across the seam.
    """
    claims: list[str] = []
    for block in blocks:
        scanned = _without_quotations(block) if _CORRECTION_NOTE.search(block) else block
        claims.extend(
            sentence.strip()
            for sentence in _SENTENCE_END.split(scanned)
            if any(pattern.search(sentence) for pattern in patterns)
        )
    return claims


def _ledger_drift(
    found: Sequence[tuple[str, str]], ledger: Sequence[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(occurrences the ledger does not record, ledger rows the scan no longer finds)``.

    Both directions in one function so that both can be driven from memory. The
    second was dead code while :data:`UNNARROWED_UNIVERSALS` was empty — an
    assertion over an empty comprehension cannot fail — and a stale-row check
    nobody has ever seen fire is a stale-row check nobody should trust.
    """
    new = [
        (where, sentence)
        for where, sentence in found
        if not any(fragment in sentence for _, fragment in ledger)
    ]
    stale = [
        (where, fragment)
        for where, fragment in ledger
        if not any(fragment in sentence for _, sentence in found)
    ]
    return new, stale


def _section(text: str, title: str) -> str:
    """The ``##`` section named ``title``, HTML comments removed, as one collapsed run.

    Bounded by structure rather than by line numbers, the same reckoning
    :func:`_decision_ten` uses: the section runs from its own heading to the next
    ``##``, so a paragraph inserted inside it extends the region rather than
    escaping it.

    Asserted findable exactly once. A section that cannot be located is not a
    section whose wording passed — it is a scan with nothing to read, which a
    positive pin would report as compliance.
    """
    body = _HTML_COMMENT.sub(" ", text)
    headings = [match for match in _SECTION_HEADING.finditer(body) if match.group("title") == title]

    assert len(headings) == 1, (
        f"`## {title}` is not findable as exactly one heading (found {len(headings)}); "
        f"the pins below would read the wrong region"
    )

    start = headings[0].end()
    following = _SECTION_HEADING.search(body, start)
    return _collapsed(body[start : following.start() if following else len(body)])


def _decision_ten(text: str) -> str:
    """ADR-0008 Decision 10 and its amendments, as one collapsed run of paragraphs.

    Bounded by structure rather than by line numbers: it starts at the numbered
    item that names ``raptor.enabled``'s default and ends at the next heading or
    numbered item, so inserting an amendment inside the decision extends the
    region rather than escaping it.

    Asserted to be findable exactly once. A decision that cannot be located is not
    a decision whose wording passed — it is a scan with nothing to read, and this
    module's positive pins would report that as compliance.

    HTML comments are removed first, for the reason :func:`_section` gives: a
    decision commented out renders as nothing, and mutation A6 is not specific to
    ``raptor.md``. ADR-0008 carries no ``<!-- -->`` today, so this is a no-op on
    the shipped file and a closed door on the mutation.
    """
    paragraphs = _paragraphs(_HTML_COMMENT.sub(" ", text))
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


def _decision_three_amendment(text: str) -> str:
    """ADR-0008's amendment to decision 3 — the ``minChildrenPerSummary`` threshold note.

    One paragraph rather than a range, because the whole clause lives in one: the
    amendment blockquote under the third Negative consequence. Anchored on its own
    opening sentence and asserted findable exactly once, for the reason
    :func:`_decision_ten` gives — a region that cannot be located reads as
    compliance.

    ADR-0008 carries more than forty paragraphs opening ``**Amended in Milestone
    6``, which is why the anchor runs on into "The skip is real".

    HTML comments are removed first, the same closure :func:`_decision_ten` takes.
    """
    anchor = "**Amended in Milestone 6, by the forest-builder CL. The skip is real"
    matches = [
        paragraph
        for paragraph in _paragraphs(_HTML_COMMENT.sub(" ", text))
        if paragraph.startswith(anchor)
    ]

    assert len(matches) == 1, (
        f"ADR-0008's amendment to decision 3 is not findable as exactly one paragraph "
        f"opening {anchor!r} (found {len(matches)}); the pin below would read the wrong "
        f"region, or nothing at all"
    )
    return matches[0]


#: The files this module scans, as ``(path, how its prose is blocked, what it is
#: scanned for)``.
#:
#: The pronoun pattern rides the YAML file alone; :data:`_THIS_FILE_UNREAD`
#: records why.
SCANNED_SURFACES: Final[
    tuple[tuple[pathlib.Path, Callable[[str], list[str]], tuple[re.Pattern[str], ...]], ...]
] = (
    (ADR_0008, _paragraphs, (_FILE_UNREAD,)),
    (RAPTOR_MD, _paragraphs, (_FILE_UNREAD,)),
    (SAMPLE_CONFIG, _comment_blocks, (_FILE_UNREAD, _THIS_FILE_UNREAD)),
)


# -- The scanners, which the absence pins below are worthless without ---------


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
        f"`test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file`, "
        f"which would keep passing against a pattern that matches nothing."
    )


@pytest.mark.parametrize(
    ("sentence", "is_the_universal"),
    PRONOUN_CASES,
    ids=[case[0][:60] for case in PRONOUN_CASES],
)
def test_the_pronoun_scan_sees_the_sample_configs_retracted_wording(
    sentence: str, is_the_universal: bool
) -> None:
    """RED means the sample config's half of the negative pin asserts nothing.

    The example never named the path — it said "Nothing in ``src/`` reads **this
    file**" — so :data:`_FILE_UNREAD` cannot see the shape that was actually
    wrong, and the file-wide pin below would report the retracted annotation
    returning as compliance. The first positive is that sentence verbatim; the
    negatives are the annotation the example carries now.
    """
    found = bool(_THIS_FILE_UNREAD.search(_collapsed(sentence)))

    assert found is is_the_universal, (
        f"the pronoun scan read {sentence!r} as "
        f"{'the retracted universal' if found else 'acceptable wording'}, expected the "
        f"opposite. The scanner is broken, not the example: fix `_THIS_FILE_UNREAD` "
        f"before trusting a green result from "
        f"`test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file`."
    )


def test_the_correction_note_exclusion_hides_a_quotation_and_not_an_assertion() -> None:
    """RED means the note exclusion is back to skipping whole paragraphs.

    Round-one mutation A7: a live reassertion placed inside ADR-0008's twenty-line
    correction note was invisible, while the identical sentence one paragraph away
    was caught. The exclusion is now quotation-shaped, so this asserts both halves
    of that shape against synthetic notes rather than against the shipped file —
    the shipped file has no live reassertion in it, and a scan that had stopped
    excluding anything would look identical there.
    """
    quoting = (
        "Corrected in the #199 unit-A follow-up "
        "(https://github.com/theurian/theurian/issues/426). This paragraph said "
        '"nothing in `src/` reads `.theurian/config.yaml`". That was true when written.'
    )
    asserting = (
        "Corrected in the #199 unit-A follow-up "
        "(https://github.com/theurian/theurian/issues/426). Nothing in `src/` reads "
        "`.theurian/config.yaml` today. That is why the default is safe to flip."
    )

    assert not _unread_claims([quoting], (_FILE_UNREAD,)), (
        "the quoted retraction is reported as the universal returning, so the note that "
        "records the fix reads as the defect and this module is RED on a clean tree"
    )
    assert _unread_claims([asserting], (_FILE_UNREAD,)), (
        "a live reassertion inside a correction note is invisible, which is round-one "
        "mutation A7. The exclusion must hide the note's quotations, not the note."
    )


def test_the_ledger_reports_an_occurrence_it_does_not_record() -> None:
    """RED means a re-added universal can be absorbed by an unrelated ledger row.

    :func:`_ledger_drift` decides what the negative pin below reports, and its
    first direction is the one that fires on a real regression. Driven from memory
    because the shipped input is an empty ledger against an empty scan, where both
    comprehensions are empty and the assertions cannot fail whatever the function
    does.
    """
    found = [("0008-raptor-forest.md", "nothing in `src/` reads `.theurian/config.yaml`")]

    new, stale = _ledger_drift(found, [("somewhere else", "a fragment that is not in it")])

    assert new == found, f"the unrecorded occurrence was not reported: {new}"
    assert stale == [("somewhere else", "a fragment that is not in it")], (
        f"the row whose fragment the scan never found was not reported as stale: {stale}"
    )


def test_the_ledger_reports_a_row_whose_sentence_was_fixed() -> None:
    """RED means discharging a ledger row stops being forced.

    The second direction, and the reason the ledger is exact: a row the scan no
    longer finds means somebody narrowed the sentence, which is good news and
    still RED, because the fix owes a positive pin (see
    :data:`DECISION_THREE_SENTENCES`) and a deleted row. A ledger that silently
    tolerates stale rows stops being read.
    """
    ledger = [("ADR-0008, the amendment to decision 3", "an operator cannot yet move it")]

    new, stale = _ledger_drift([("0008-raptor-forest.md", "no key in the block")], ledger)

    assert stale == ledger, f"the fixed row was not reported as stale: {stale}"
    assert new == [("0008-raptor-forest.md", "no key in the block")], (
        f"an occurrence outside the ledger was swallowed by the stale row: {new}"
    )


def test_the_ledger_absorbs_the_occurrence_it_records() -> None:
    """RED means a recorded survivor is reported as new, and the ledger cannot be satisfied.

    The third case, and the one the two above do not reach: in both of those the
    ledger and the scan disagree, so a :func:`_ledger_drift` that ignored the
    ledger entirely would answer them identically -- measured, by deleting the
    filter and watching them both stay green. This is the agreement case. A ledger
    row exists to *absorb* the occurrence it records, so that a known survivor is
    debt rather than an alarm; a version that reported it anyway would leave the
    negative pin RED with no wording change that could make it green.
    """
    found = [
        (
            "0008-raptor-forest.md",
            "nothing reads `.theurian/config.yaml`, so an operator cannot yet move it",
        )
    ]
    ledger = [("ADR-0008, the amendment to decision 3", "so an operator cannot yet move it")]

    new, stale = _ledger_drift(found, ledger)

    assert not new, f"a recorded survivor was reported as a new occurrence: {new}"
    assert not stale, f"the row whose fragment the scan did find was reported as stale: {stale}"


# -- docs/architecture/raptor.md ---------------------------------------------


def test_raptor_md_says_the_raptor_block_is_what_is_unread_and_names_the_reader() -> None:
    """RED means the narrowing is gone — deleted, moved out of the section, or unrendered.

    The positive half for ``raptor.md``, and it is not the negative one restated.
    A rewrite that drops the paragraph entirely asserts nothing false and would
    pass
    :func:`test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file`
    while leaving the file silent about a key the sample config sets and nothing
    acts on — which is the state that produced ADR-0008 decision 10 in the first
    place.

    **Scoped to the "Three levels" section, with HTML comments removed.** A
    file-wide substring match is satisfied by the sentences existing *anywhere*,
    including inside a ``<!-- -->`` comment that renders as nothing: round-one
    mutation A6 moved all four into a comment elsewhere in the file and this test
    stayed green while the record was silent. The section is the region the
    narrowing argument lives in, so a sentence that leaves it has left the
    argument.

    The reader is required by name alongside the claim. "No ``raptor`` key is
    read" with no statement of who *does* read the file is a bare assertion, and a
    bare assertion is what #426 found: the sentence had been true, nobody
    re-derived it, and it stayed for a milestone after ADR-0027 decision 3
    falsified it.
    """
    section = _section(RAPTOR_MD.read_text(encoding="utf-8"), _THREE_LEVELS)

    for sentence in RAPTOR_MD_SENTENCES:
        assert sentence in section, (
            f"docs/architecture/raptor.md's `## {_THREE_LEVELS}` section no longer "
            f"states {sentence!r}.\n\n"
            f"That sentence is one leg of #426's narrowing: the claim (`no `raptor` key "
            f"is read`), the reader that bounds it (`security/project_config.py`, "
            f"ADR-0027 decision 3), the population that is unread (the `raptor` block), "
            f"and the conclusion it supports (the CLI flag is the switch). A sentence "
            f"moved to another section or into an HTML comment does not count: the "
            f"record has to be where the argument is, and rendered. If a raptor config "
            f"loader has landed, `tests/unit/test_config_key_call_sites.py` records the "
            f"reader and this paragraph has to be corrected in the same change; if it "
            f"has not, restore the sentence."
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


def test_adr_0008_decision_three_reaches_its_conclusion_from_the_raptor_block() -> None:
    """RED means the discharged ledger row's replacement was gutted or deleted.

    The amendment to decision 3 concludes "an operator cannot yet move it". Until
    #426 it reached that from *nothing reads the config file*, which ADR-0027
    decision 3 had already falsified; it now reaches it from *no key in the
    ``raptor`` block has a reader*, with the reader the file does have named
    beside it.

    This pin exists because the negative half cannot hold it. Cutting the clause
    back to the bare conclusion asserts nothing false — it simply stops saying
    why — and the ledger row that used to watch this sentence was deleted when the
    sentence was corrected. Promoting the row to this pin is what keeps the fixed
    wording watched; see :data:`UNNARROWED_UNIVERSALS`.
    """
    amendment = _decision_three_amendment(ADR_0008.read_text(encoding="utf-8"))

    for sentence in DECISION_THREE_SENTENCES:
        assert sentence in amendment, (
            f"ADR-0008's amendment to decision 3 no longer states {sentence!r}.\n\n"
            f"It reads:\n  {amendment!r}\n\n"
            f"#426 narrowed this clause's premise from the whole configuration file to "
            f"the `raptor` block, and named the one reader the file does have. Without "
            f"both, `an operator cannot yet move it` is a conclusion with no checkable "
            f"premise -- which is the defect #426 was opened for. If a loader has "
            f"landed, `tests/unit/test_config_key_call_sites.py` records it and this "
            f"clause changes in the same commit; if it has not, restore the sentence."
        )


# -- All three surfaces: the universal must not come back --------------------


def test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file() -> None:
    """RED means the retracted universal is back, or a recorded survivor was fixed.

    The negative half, and it catches what the positive one cannot: a file that
    keeps "no ``raptor`` key is read" and reasserts the file-wide claim somewhere
    else. Two sentences of ADR-0008 read that way for a milestone after ADR-0027
    decision 3 shipped a reader, and neither was noticed by anything.

    **The sample config is in the population** (round one, cr-M1). Its annotation
    carried the same universal in the pronoun form, and nothing watched it: the
    retracted sentence could be restored verbatim with every test in the suite
    still green. :data:`_THIS_FILE_UNREAD` is what sees that shape, and it is
    applied here and nowhere else.

    **An exact ledger in both directions.** A sentence the scan finds that is not
    in :data:`UNNARROWED_UNIVERSALS` is the universal returning. A recorded entry
    the scan no longer finds means someone fixed it — which is good news and still
    RED, because a ledger that keeps stale rows stops being read. Delete the row
    and pin the narrowed sentence in the same change.

    Scoped to :data:`SCANNED_SURFACES`. The served corpus carries governed
    snapshots of the two documents, still holding the retracted wording
    byte-identically by design (#199 unit C), so a repo-wide walk over this
    pattern would report those anchors as drift.
    """
    found = [
        (path.name, sentence)
        for path, blocks, patterns in SCANNED_SURFACES
        for sentence in _unread_claims(blocks(path.read_text(encoding="utf-8")), patterns)
    ]

    new, stale = _ledger_drift(found, UNNARROWED_UNIVERSALS)

    assert not new, (
        "a file asserts again that nothing in `src/` reads `.theurian/config.yaml`, "
        "which ADR-0027 decision 3 falsified -- `security/project_config.py` opens it "
        "for `security.secretScan`:\n"
        + "\n".join(f"  {name}: {sentence}" for name, sentence in new)
        + "\n\nNarrow the sentence to the population that is still unread (the `raptor` "
        "block, or `providers.review.repositories` in the example) rather than deleting "
        "it; #426 is the change that did this for decision 10, for the amendment to "
        "decision 3, for docs/architecture/raptor.md and for the sample config."
    )
    assert not stale, (
        "a recorded un-narrowed universal is no longer in the document, so the ledger "
        "in `UNNARROWED_UNIVERSALS` has a stale row:\n"
        + "\n".join(f"  {where}: {fragment}" for where, fragment in stale)
        + "\n\nThis is the good direction -- pin the narrowed sentence the way "
        "`DECISION_THREE_SENTENCES` pins the row this ledger used to hold, then delete "
        "the row. The ledger is exact so that the debt it records has to be discharged "
        "rather than accumulated."
    )


def test_the_correction_note_that_bounds_the_scan_is_actually_there() -> None:
    """RED means the exclusion above stopped being about anything, and widened silently.

    :func:`_unread_claims` excises the quoted spans of paragraphs naming issue
    #426 in its ``issues/426`` link form, because the dated correction note quotes
    the retracted sentence in order to retract it and a scan that read the
    quotation would report the fix as the defect.

    An exclusion nobody checks is the same shape as a population nobody counts.
    If the note is deleted or its issue reference is dropped, the excision stops
    matching that paragraph -- which would make the scan *wider*, not narrower,
    and the quoted universal there would be reported as new. Asserting the note
    exists is what keeps the exclusion honest and keeps the record findable: the
    only place ADR-0008 says why those two sentences changed.

    **Exactly one, and the second note is why the count is checked.** ADR-0008
    also carries a correction note under the amendment to decision 3, written with
    the bare ``#426`` spelling; it quotes nothing retracted, so it needs no
    excision and is deliberately outside the key. Rewriting it to the link form
    would make this RED -- correctly, because the excision would then cover a
    paragraph nobody decided it should.
    """
    notes = [
        paragraph
        for paragraph in _paragraphs(ADR_0008.read_text(encoding="utf-8"))
        if _CORRECTION_NOTE.search(paragraph)
    ]

    assert len(notes) == 1, (
        f"ADR-0008 carries {len(notes)} paragraphs naming issue #426 in its "
        f"`issues/426` link form, expected exactly one -- the dated correction note "
        f"that records what decision 10's two `nothing in `src/` reads "
        f"`.theurian/config.yaml`` sentences said and why the conclusions survive their "
        f"narrowing. The amendment to decision 3 carries a second note in the bare "
        f"`#426` spelling, which quotes nothing retracted and is outside this key on "
        f"purpose"
    )
    assert "ADR-0027" in notes[0], (
        f"the correction note no longer names ADR-0027, which is what falsified the "
        f"retracted sentences: {notes[0]}"
    )
