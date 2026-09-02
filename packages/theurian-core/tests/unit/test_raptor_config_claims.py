"""What the governed records claim about who reads ``.theurian/config.yaml`` (#426).

Every one of them recorded that **nothing in ``src/`` reads
``.theurian/config.yaml``**.
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

Three more surfaces joined in #199 unit B, each already narrowed and none of them
pinned until then. ``schemas/config/project-config.schema.json``'s root
``description`` is #455's; ``application/forest_builder.py``,
``tests/unit/test_forest_derivation.py`` and ``tests/unit/test_schemas.py`` are
#447's, narrowed on 2026-09-01 with nothing watching the narrowed wording
afterwards. :data:`SCANNED_SURFACES` is the list, and it is the only one — a
second copy in this docstring is a second copy to keep in step.

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
a wording pin over a named set of files:

- The **positive** half requires the narrowed sentences to still be there. A
  rewrite that simply deletes them asserts nothing false and would pass the
  negative half while leaving the records silent about a key an operator can set
  in the sample config and that nothing acts on. Each positive pin is scoped to
  the region that carries the claim — ``raptor.md``'s "Three levels" section,
  ADR-0008 decision 10, ADR-0008's amendment to decision 3 — so a sentence
  surviving somewhere else in the file does not satisfy it.
- The **negative** half refuses the retracted universal in the three forms it
  actually took — "nothing … reads ``.theurian/config.yaml``" in the present
  tense, "the config file is still unread", and, where the pronoun has one
  possible referent, "nothing in ``src/`` reads this file".
- **The escape space is a table now, not a sentence.** This paragraph used to
  list five phrasings the scan does not catch and assert nothing about them;
  #199 unit B turned the list into :data:`MEASURED_ESCAPE_CASES`, so each one is
  a test result. Four are shapes a live reassertion could take and slip past —
  "nothing *opens* it", "no loader exists", "the file is not read", "no config
  surface" — and the fifth is the past tense, which the corrected rationale uses
  deliberately and which must keep passing. A row that stops escaping is RED, and
  that is the good direction: the scan widened, and this paragraph has to move
  with it.
- It says nothing at all about the source tree. Whether a reader exists is
  ``test_config_key_call_sites.py``'s question; this module would stay green
  against a build that shipped a raptor loader and left every scanned file
  alone — which is exactly why the two halves are separate tests and neither is
  sufficient.

**Scoped to a named file set, never a repo-wide walk.** The served corpus
carries governed snapshots of the two Markdown documents —
``.theurian/knowledge/architecture/raptor-forest.<ulid>.md`` still says "Nothing
in ``src/`` reads ``.theurian/config.yaml``, so flipping the default" — held
byte-identical to their source anchor commits by
``test_dogfood_corpus_governance.py``, so only a governed re-seed can move them
(#199 unit C). A tree scan for this wording would go RED on those files on the
day it was written.

**The dated correction notes are excluded by the quotation a retraction verb
introduces, not by paragraph and not by quotation marks alone.** A note quotes
the retracted sentence in order to retract it, so a scan that read the quotation
would report the fix as the defect. Skipping the whole note paragraph closed that
at the cost of a blind spot the size of the note: round-one mutation A7 put a
*live* reassertion inside ADR-0008's twenty-line note and nothing saw it, while
the same sentence one paragraph away was caught. Excising *every* quoted span
instead bought a smaller blind spot of the same kind, and round two measured the
shapes inside it: an assertion italicised anywhere in it (``*Nothing*``,
``*reads*``), one whose path is written in double quotes, and one bracketed by
the two asterisks of two ``SELECT *`` spans — that last a live reassertion the
full-suite mutation run reported as SURVIVED. So the excision is now scoped to a
quotation a **retraction verb** introduces — ``said``, ``stated``, ``quoted``,
``carried`` and the rest of :data:`_RETRACTION_LEAD`.

**Verb-scoping did not close the ``SELECT *`` shape; it moved that shape's
precondition, and round three measured what closes it.** With no retraction verb
in front of the asterisks the excision never fires, which is the only reason the
round-two E1 row passed — put a verb in front and *The note says ``metadata()``
does ``SELECT *``, and nothing in ``src/`` reads ``.theurian/config.yaml``
today* is hidden in exactly the way E1 was, here and in
``test_index_metadata_claims.py``. What closes it is :data:`_CODE_SPAN`: the
asterisks inside inline code spans are neutralised **before**
:data:`_QUOTED_RETRACTION` runs, because CommonMark cannot open emphasis from
inside a code span, and E1's asterisks come from ``SELECT *`` written in code
markup. Stripping *every* emphasis marker before the excision instead is the
other obvious move and it is wrong — measured, it makes ADR-0008's italic
quotation read as a live reassertion, because a quotation whose markers are
already gone is one the excision can no longer find.

Both shipped notes quote with ``said`` and stay excised. The verb-led probes
carry a lead verb too — that is what makes them probes — and stay visible
because :data:`_CODE_SPAN` leaves the excision no italic span to find. The
measurement is :data:`NOTE_EXCLUSION_CASES`, which carries the probes and the
shipped quoting styles as one parametrized set; what :data:`_CODE_SPAN` does
*not* reach is recorded there and bounded by
:func:`test_every_block_the_exclusion_runs_on_pairs_its_backticks`.

``read`` and ``reads`` are deliberately **not** retraction verbs. They are the
verb of the claim itself, so admitting them would excise the quoted path in
*Nothing in ``src/`` reads ".theurian/config.yaml"* — the double-quoted-path
probe — and hand back the hole this scoping exists to close. **The cost is a
class of false REDs rather than the one shape this paragraph used to name**, and
the class is measured in :data:`FALSE_RED_RESIDUE_CASES`: a quotation whose lead
verb is outside :data:`_RETRACTION_LEAD` (``read`` deliberately, and every verb
nobody enumerated), one whose verb sits further than forty characters from it,
and the second quotation after a single verb — ``re.sub`` resumes after the
first, so only a quotation with its own verb in front of it is excised. Each
leaves a quotation in place to be reported as the universal returning. A fourth
member runs the other way, the excision *creating* a span the paragraph does not
contain, and has its own pin in
:func:`test_a_quotation_carrying_a_sentence_end_joins_across_the_seam`. All four
cost a read; the direction they refuse to trade for costs the claim.

**The population of this class outside the files scanned here is tracked in the
issues, not counted here.** #461 holds a Markdown member in ``plugins/`` that no
pin here reaches. #447's three Python members are no longer on that list: unit B
scanned them, which is what closes the gap #487 recorded — narrowed prose with
nothing watching it. Two attempts to state that population as a number have been
wrong — round one said the schema string was the only one
outside ``src/``, and round two found the ``plugins/`` member outside every key
either attempt had used — so this docstring names the issues and stops counting.

**#455's member is no longer in that list: the schema is a scanned surface.**
Its root ``description`` is invisible to :data:`_FILE_UNREAD`, which requires the
path after the verb, and visible to :data:`_THIS_FILE_UNREAD`, which is what made
extending the pronoun pattern past the sample config the fix rather than widening
the path pattern. #199 unit B took the file, rewrote the sentence to name the one
reader ``.theurian/config.yaml`` has, and added
``schemas/config/project-config.schema.json`` to :data:`SCANNED_SURFACES` in the
same commit — so a rewrite back to the universal is RED here, and the positive
direction is held by ``test_config_key_call_sites.py``'s
``WATCHED_KEY_DESCRIPTIONS`` root row. The retracted sentence stays in both case
tables, transcribed from the schema at ``5a14145`` (byte-identical there to
``8286336``), so which pattern sees it remains a test result rather than a
paragraph.

**The ledger is empty, and that is a measured state rather than a scope
boundary** — see :data:`UNNARROWED_UNIVERSALS`.

Pure: it reads two Markdown files, one YAML file, one JSON schema and three
Python modules as text, and opens no database, no socket and no temporary
directory. :data:`SCANNED_SURFACES` is the list; this sentence deliberately does
not repeat it as names, because a second copy of a file set is a second copy to
keep in step.
"""

from __future__ import annotations

import json
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
PROJECT_CONFIG_SCHEMA: Final = REPO_ROOT / "schemas" / "config" / "project-config.schema.json"

#: The three Python surfaces #447 narrowed, whose prose nothing pinned until
#: #199 unit B took them.
_CORE: Final = REPO_ROOT / "packages" / "theurian-core"
FOREST_BUILDER: Final = _CORE / "src" / "theurian" / "application" / "forest_builder.py"
FOREST_DERIVATION_TEST: Final = _CORE / "tests" / "unit" / "test_forest_derivation.py"
SCHEMAS_TEST: Final = _CORE / "tests" / "unit" / "test_schemas.py"

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

#: A comment that shares its line with a key, in the sample config.
#:
#: A ``#`` opens a comment when it follows whitespace, which is YAML's own rule
#: for the unquoted case; the whole-line form is handled before this is reached.
#: The first such ``#`` wins, so the rest of the line — including any further
#: ``#`` — is the comment's own text.
_INLINE_COMMENT: Final = re.compile(r"\s#(?P<comment>.*)$")

#: A ``#:`` or ``#`` comment marker at the head of a Python line, stripped before
#: the prose behind it is read.
#:
#: Python is the third source language this module scans, and it needs its own
#: marker rule for the same reason YAML did: an attribute docstring in
#: ``application/forest_builder.py`` is a run of ``#:`` lines, and a sentence
#: wraps across four of them.
_PYTHON_COMMENT: Final = re.compile(r"^[ \t]*#:?[ \t]?")

#: An ATX heading at depth two, which is what bounds a section of ``raptor.md``.
#:
#: ``\s`` after the two hashes is what keeps ``### Negative`` from reading as a
#: section boundary. A fenced block containing a line that starts ``## `` would
#: end a section early, which makes a positive pin RED rather than green — the
#: safe direction, and the reason this is not defended further.
_SECTION_HEADING: Final = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)

#: An HTML comment, removed before a positive pin reads a section — **closed or
#: not**.
#:
#: Markdown renders nothing inside one, so a record whose sentences live in a
#: comment is a silent record. Round-one mutation A6 moved all four of
#: ``raptor.md``'s pinned sentences into a comment and both halves of this module
#: stayed green.
#:
#: The second alternative is round two's E2. A ``<!--`` that is never closed is
#: an HTML block that runs to the next line carrying ``-->`` or, if there is
#: none, to the end of the document — so GitHub renders nothing after it, while a
#: pattern demanding the closing delimiter matches nothing and every pin here
#: stays green. Measured on ADR-0008 with an unclosed ``<!--`` inserted above
#: decision 10: with one alternative :func:`_decision_ten` and
#: :func:`_decision_three_amendment` both still found their regions; with two
#: both raise. Ordered alternation is what keeps this faithful — the closed form
#: is tried first, so a real comment ends at its own ``-->`` and the runaway form
#: only fires when no ``-->`` follows at all. Both Markdown documents scanned here
#: carry ``-->`` arrows in their mermaid diagrams; none of them starts a match,
#: which :func:`test_a_mermaid_arrow_does_not_open_an_html_comment` holds, and
#: substituting this pattern into the shipped surfaces leaves each of them
#: byte-identical, which
#: :func:`test_no_scanned_surface_carries_an_html_comment_today` holds.
_HTML_COMMENT: Final = re.compile(r"<!--.*?-->|<!--.*", re.DOTALL)

#: Bold markers, which are emphasis rather than quotation and are removed before
#: :data:`_QUOTED_RETRACTION` looks for italic spans — otherwise ``**bold**``
#: reads as an italic span and a live claim written in bold inside a correction
#: note would be excised along with the quotations.
_BOLD: Final = re.compile(r"\*\*")

#: A single emphasis marker, removed from **every** block after the excision.
#:
#: **What this strip buys is scannability, and only that.** An asterisk is not
#: part of the sentence a reader sees, and leaving it in makes the patterns depend
#: on typography: ``\breads\b\s+`` does not match ``*reads*`` because the closing
#: asterisk sits where the space has to be, so a live reassertion italicised on
#: its verb was invisible whether or not it was excised (round two, probe 2).
#:
#: **It is not what closes E1, and this comment claimed it was from ``5d97f93``
#: until round three.** The strip runs *after* :data:`_QUOTED_RETRACTION` —
#: :func:`_unread_claims` is where the order is — so the excision sees every
#: asterisk the block carries,
#: ``SELECT *`` included. What kept the shipped E1 row visible is that its note
#: carries no retraction verb in front of those asterisks; put one there and the
#: row is hidden again, which is round three's H-1. :data:`_CODE_SPAN` is what
#: closes it. Moving *this* strip ahead of the excision does not: measured, it
#: makes ADR-0008's italic quotation read as a live reassertion, because a
#: quotation whose markers are already gone is one the excision can no longer
#: find.
_EMPHASIS: Final = re.compile(r"\*")

#: An inline code span, matched by its own backtick fence.
#:
#: **The asterisks inside one are neutralised before :data:`_QUOTED_RETRACTION`
#: runs**, which is round three's H-1 remedy. CommonMark cannot open emphasis from
#: inside a code span, so the ``*`` of ``SELECT *`` is a character and never a
#: delimiter — while the excision's italic alternative ``\*[^*]+\*`` read the
#: asterisks of two ``SELECT *`` spans as one italic run and threw away the live
#: reassertion between them, whenever a retraction verb sat within forty
#: characters of the first. E1's asterisks come from ``SELECT *`` written in code
#: markup, which is what :data:`NOTE_EXCLUSION_CASES` carries and how ADR-0024
#: decision 2 — the decision the twin module is about — writes it. An asterisk
#: outside a code span is emphasis or nothing, and this leaves it alone.
#:
#: The fence is back-referenced rather than fixed at one backtick, so a span
#: written with a double fence is still one span.
#:
#: **The pairing is an approximation and an unbalanced backtick breaks it both
#: ways** — measured, not deduced, and the dangerous direction is the first:
#:
#: * a stray backtick can pair with a real span's *opening* one, leaving that
#:   span's asterisk exposed and the verb-led E1 hidden again — a **false green**,
#:   which is this remedy failing to reach a shape rather than breaking one it
#:   used to reach, since before it every asterisk was exposed;
#: * or it can pair across a real italic marker and delete it, leaving a quotation
#:   unexcised and reported — a false RED, the same direction as
#:   :data:`FALSE_RED_RESIDUE_CASES`.
#:
#: :func:`test_every_block_the_exclusion_runs_on_pairs_its_backticks` is what
#: bounds the first on the shipped surfaces; a note that gains an unbalanced
#: backtick makes somebody look.
_CODE_SPAN: Final = re.compile(r"(?P<fence>`+).+?(?P=fence)", re.DOTALL)

#: The verbs a dated note uses to introduce the sentence it retracts.
#:
#: ``read`` and ``reads`` are absent on purpose and :data:`_QUOTED_RETRACTION`
#: records why.
_RETRACTION_LEAD: Final = (
    r"said|says|say|stated|states|quoted|quotes|carried|carries|asserted|asserts"
    r"|claimed|claims|wrote"
)

#: A quoted span a retraction verb introduces inside a dated correction note: an
#: italic run, a double-quoted run or a curly-quoted run, within forty characters
#: of the verb.
#:
#: Both shipped notes take this shape — ADR-0008's decision-10 note quotes in
#: italics after ``said``, ADR-0024's decision-2 note in double quotes after
#: ``said`` — so both stay excised, which is the constraint this scoping had to
#: keep. What it gives up is the blanket rule: a quoted span with no retraction
#: verb in front of it is *the note talking*, and round two demonstrated four
#: live reassertions the blanket rule hid (:data:`NOTE_EXCLUSION_CASES`).
#:
#: ``read``/``reads`` are excluded from :data:`_RETRACTION_LEAD` because they are
#: the verb of the claim itself: with them, *Nothing in ``src/`` reads
#: ".theurian/config.yaml"* has its object excised and the reassertion is hidden
#: again.
#:
#: **The residue is a class, not the one shape this comment used to name.**
#: Anything the excision does not reach leaves a quotation in place to be reported
#: as the universal returning — a false RED on a fix, which costs a read where the
#: direction refused costs the claim. Three members are measured in
#: :data:`FALSE_RED_RESIDUE_CASES`: a lead verb outside :data:`_RETRACTION_LEAD`,
#: a lead verb further than forty characters from the quotation it introduces, and
#: the second quotation after one verb — ``re.sub`` resumes past the first match,
#: so a second quotation is excised only if it has a verb of its own in front of
#: it. The fourth member runs the other way, the excision *creating* a span the
#: paragraph does not contain, and is pinned by
#: :func:`test_a_quotation_carrying_a_sentence_end_joins_across_the_seam`.
#:
#: The lead is preserved by the substitution, so the excision removes the
#: quotation and not the sentence around it. What it looks at is
#: :func:`_without_quotations`'s two neutralisations away from the raw text:
#: emphasis inside a code span, and ``**``.
_QUOTED_RETRACTION: Final = re.compile(
    rf"(?P<lead>\b(?:{_RETRACTION_LEAD})\b[^\n]{{0,40}}?)" r"(?:\*[^*]+\*|\"[^\"]+\"|“[^”]+”)"
)

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
#:
#: The delimiter in front of the path is any of a backtick, a double quote, a
#: single quote and a curly opening quote, rather than a backtick alone. Round
#: two's third probe wrote the path in double quotes — *Nothing in ``src/`` reads
#: ".theurian/config.yaml"* — and a backtick-only rule read that as acceptable
#: wording while the verb-scoped excision left the sentence in place for it. Both
#: halves had to move for the probe to be seen, which is why they are recorded
#: together.
_FILE_UNREAD: Final = re.compile(
    r"\b(?:nothing|nobody|none|no code|no module)\b[^\n]{0,40}?"
    r"\breads\b\s+(?:the\s+)?[\"'`“]?\.?theurian/config\.yaml"
    r"|[\"'`“]?\.?theurian/config\.yaml`?[^.]{0,20}\bis\s+(?:still\s+)?unread\b"
    r"|\bconfig(?:uration)?\s+file\s+is\s+(?:still\s+)?unread\b",
    re.IGNORECASE,
)

#: The third shape, and the reason it rides two surfaces and not every one.
#:
#: The annotation the sample config carried said "Nothing in ``src/`` reads **this
#: file**" — a pronoun, never the path, so :data:`_FILE_UNREAD` cannot see it.
#: Inside ``config.yaml`` the pronoun has one possible referent and the rule is
#: safe; inside the published schema, whose whole subject is that file, it has the
#: same one, which is why #199 unit B could take the schema root with this pattern
#: rather than by loosening the path one. In ``raptor.md``, an ADR or a Python
#: module it does not: "this file" there means the document or the module a reader
#: is holding, and a sentence about a document that reads nothing is not this
#: claim. Applying the pattern to every scanned surface would buy one shape at the
#: cost of a false RED on prose that is true.
_THIS_FILE_UNREAD: Final = re.compile(
    r"\b(?:nothing|nobody|none|no code|no module)\b[^\n]{0,40}?"
    r"\breads\b\s+(?:this|that|the)\s+(?:config(?:uration)?\s+)?file\b",
    re.IGNORECASE,
)

#: The dated correction note, identified by the issue that owns it.
#:
#: Keyed on the ``issues/426`` **link form** rather than on the words "corrected
#: in". ADR-0008 is an amended document whose paragraphs routinely open with a
#: correction, an amendment or a landing note, so a words-based key excludes a
#: share of the document that grows with every amendment — and it is a share
#: nobody can state, because it moves with the wording chosen for the key: the
#: candidate keys tried during round two returned a different population each
#: time. The link form is keyed on a link the note has to carry to be findable at
#: all, and the per-surface note count below — see
#: :data:`CORRECTION_NOTES_PER_SURFACE` — measures what it selects rather than
#: leaving this comment to guess.
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
    # -- #455's member, transcribed from the schema at `5a14145` -------------
    # The wheel-shipped root `description` as it read until #199 unit B rewrote
    # it. The schema is a scanned surface now, so this row no longer says "a live
    # member nothing here watches"; what it still says, and the reason it stays,
    # is that *this* pattern is blind to the sentence because it names no path.
    # `_THIS_FILE_UNREAD` is what sees it, and the matching row in `PRONOUN_CASES`
    # is that half. Keeping both rows is what makes "which pattern catches the
    # schema root" a test result rather than a paragraph.
    (
        "Nothing in src/ reads this file, so no value in it takes effect today: where a "
        "default here is also honoured by the product, the code carries its own copy.",
        False,
    ),
)

#: One case per form the pronoun scan claims to catch, and per form it claims to
#: let past.
#:
#: The first positive is the exact sentence the example carried before #426. The
#: negatives are transcribed from the annotation it carries now, and from the
#: schema description #199 unit B rewrote, so a pattern that misread one would be
#: RED on a clean tree.
PRONOUN_CASES: Final[tuple[tuple[str, bool], ...]] = (
    (
        "The allowlist review ingestion will read (SEC-10). Nothing in `src/` reads this "
        "file, so the allowlist is not in force; review ingestion is owed with Milestone "
        "7 (#129).",
        True,
    ),
    ("No module reads that configuration file", True),
    ("nothing in `src/` reads the file", True),
    # #455's member, transcribed from `schemas/config/project-config.schema.json`
    # at `5a14145`. This row used to say the pattern *would* see the sentence if
    # the schema were ever scanned; #199 unit B scanned it, so the row now says
    # what the scan does. Restoring that sentence to the published schema is RED
    # in `test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file`
    # by way of this pattern, and this row is what proves the pattern is the one
    # that gets there -- `_FILE_UNREAD` never does, which its own table records.
    (
        "Nothing in src/ reads this file, so no value in it takes effect today: where a "
        "default here is also honoured by the product, the code carries its own copy.",
        True,
    ),
    # -- the descriptions the schema carries now, which must keep passing ----
    (
        "This file has one reader: `security/project_config.py` takes `security.secretScan` "
        "from it and nothing else (ADR-0027 decision 3), so that one key is in force and "
        "every other key published here is reserved.",
        False,
    ),
    (
        "Setting a reserved key changes nothing, and where a default below is also honoured "
        "by the product the code carries its own copy rather than reading this file.",
        False,
    ),
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

#: The phrasings both scans measurably do **not** catch, as ``(what the shape is,
#: the sentence, is it a defect the scan would miss)``.
#:
#: The module docstring used to carry these five as a list and assert nothing
#: about them. A list of escapes nobody runs is a list that stops being true the
#: first time either pattern moves, in whichever direction: widen one and a row
#: here is silently wrong, narrow one and a shape nobody recorded starts escaping.
#: So the list is a table and the table is a test.
#:
#: **The third field is what the row means, and the two values are opposites.**
#: ``True`` is the escape space: a sentence that would be a live reassertion of
#: the retracted universal, which neither :data:`_FILE_UNREAD` nor
#: :data:`_THIS_FILE_UNREAD` reaches. ``False`` is correct prose that must keep
#: passing — the past tense the corrected rationale deliberately uses, and which a
#: looser pattern would flag on the fix itself. Both are asserted the same way
#: (neither scan matches), and the field is there so a reader knows which rows are
#: debt and which are the guard against over-reach.
#:
#: **"no config surface" is the row with a live subject.**
#: ``application/forest_builder.py``'s corrected block opens with exactly those
#: words, about ``SUMMARY_MAX_TOKENS`` rather than about the file — true there, and
#: a shape that would hide a false claim about the file if one were ever written
#: that way. #199 unit B's object-keyed census carries it as its own key shape for
#: the same reason (``tools/audit/config_object_claims.py``).
#:
#: **A row that stops escaping is RED, and that is the good direction.** Delete it
#: and move the docstring paragraph in the same commit, naming the mechanism that
#: closed it — the discipline :data:`FALSE_RED_RESIDUE_CASES` and
#: :data:`UNNARROWED_UNIVERSALS` are run on.
MEASURED_ESCAPE_CASES: Final[tuple[tuple[str, str, bool], ...]] = (
    (
        "the verb `opens` instead of `reads`",
        "Nothing in `src/` opens `.theurian/config.yaml`.",
        True,
    ),
    (
        "the absence stated as a missing loader",
        "No loader for `.theurian/config.yaml` exists anywhere in `src/`.",
        True,
    ),
    (
        "the passive voice",
        "`.theurian/config.yaml` is not read by anything in `src/`.",
        True,
    ),
    (
        "no config surface, which names neither a verb nor an object",
        "It has no config surface.",
        True,
    ),
    (
        "the past tense, which the corrected rationale uses and must keep passing",
        "When this was written nothing in `src/` read `.theurian/config.yaml` at all.",
        False,
    ),
)

#: The opening every case in :data:`NOTE_EXCLUSION_CASES` shares: the link form
#: :data:`_CORRECTION_NOTE` keys on, so each case is scanned as a note.
_NOTE_OPENING: Final = (
    "Corrected in the #199 unit-A follow-up (https://github.com/theurian/theurian/issues/426)."
)

#: One case per shape a live reassertion inside a correction note can take, and
#: per shape the two shipped notes actually quote in, as
#: ``(what the shape is, the note, is it an assertion the scan must report)``.
#:
#: **The positives are the measured misses of the rule this replaced.** Excising
#: every quoted span hid all five: the plain sentence was the only one it caught.
#: Round-one mutation A7 is the first, round two's probes are the next four, and
#: the ``SELECT *`` one is E1 — a live reassertion whose only protection was two
#: asterisks belonging to two unrelated SQL fragments, which the full-suite
#: mutation run reported as SURVIVED.
#:
#: **The last two positives are round three's, and they are E1 with a verb in
#: front of it.** Verb-scoping closed E1 only for as long as the note carried no
#: retraction verb; *The note says … ``SELECT *`` …* hands the excision an italic
#: run again and the reassertion between the asterisks goes with it. Both were
#: hidden on ``ee9b5c4``, in this module and in ``test_index_metadata_claims.py``.
#: They are held by :data:`_CODE_SPAN` rather than by :data:`_RETRACTION_LEAD`, so
#: a widening of either mechanism is caught here.
#:
#: **The negatives are transcribed from the shipped notes' quoting styles**, so a
#: rule that stopped covering one of them would be RED against ADR-0008 or
#: ADR-0024 on a clean tree. The curly-quote case is not a style either note uses
#: today; it is here because a Markdown renderer and an editor both produce it
#: from a typed ``"``, and a note reflowed through one would otherwise lose its
#: exclusion silently.
NOTE_EXCLUSION_CASES: Final[tuple[tuple[str, str, bool], ...]] = (
    (
        "a plain live reassertion (round-one A7)",
        f"{_NOTE_OPENING} Nothing in `src/` reads `.theurian/config.yaml` today. "
        "That is why the default is safe to flip.",
        True,
    ),
    (
        "a live reassertion italicised on its subject",
        f"{_NOTE_OPENING} *Nothing* in `src/` reads `.theurian/config.yaml` today.",
        True,
    ),
    (
        "a live reassertion italicised on its verb",
        f"{_NOTE_OPENING} Nothing in `src/` *reads* `.theurian/config.yaml` today.",
        True,
    ),
    (
        "a live reassertion whose path is in double quotes",
        f'{_NOTE_OPENING} Nothing in `src/` reads ".theurian/config.yaml" today.',
        True,
    ),
    (
        "a live reassertion bracketed by two `SELECT *` asterisks (E1)",
        f"{_NOTE_OPENING} `metadata()` does `SELECT *`, and nothing in `src/` reads "
        "`.theurian/config.yaml` today, which is why `SELECT *` fetches it.",
        True,
    ),
    (
        "a live reassertion in bold",
        f"{_NOTE_OPENING} **Nothing in `src/` reads `.theurian/config.yaml`** today.",
        True,
    ),
    (
        "a verb-led E1: `says` in front of two `SELECT *` spans",
        f"{_NOTE_OPENING} The note says `metadata()` does `SELECT *`, and nothing in "
        "`src/` reads `.theurian/config.yaml` today, which is why `SELECT *` fetches it.",
        True,
    ),
    (
        "a verb-led E1: `carries` in front of a `*.yaml` glob and a `SELECT *`",
        f"{_NOTE_OPENING} The paragraph carries `*.yaml` examples, and nothing in "
        "`src/` reads `.theurian/config.yaml` today, so `SELECT *` fetches a default "
        "nobody set.",
        True,
    ),
    # -- the shipped notes' own quoting styles, which must stay hidden --------
    (
        "the italic quotation ADR-0008's note carries",
        f"{_NOTE_OPENING} Two sentences said *nothing in `src/` reads "
        "`.theurian/config.yaml`*. Each was true when written.",
        False,
    ),
    (
        "the double-quoted quotation ADR-0024's note carries",
        f'{_NOTE_OPENING} This paragraph said "nothing in `src/` reads '
        '`.theurian/config.yaml`". That was true when written.',
        False,
    ),
    (
        "the same quotation in curly quotes",
        f"{_NOTE_OPENING} This paragraph said “nothing in `src/` reads "
        "`.theurian/config.yaml`”. That was true when written.",
        False,
    ),
)

#: The false-RED residue of the verb-scoped excision, as ``(what the shape is, the
#: note)``. Every one of them is **reported**, and every one is a note quoting
#: rather than asserting.
#:
#: **This is the class the residue note used to record as a single shape** — "a
#: note that writes *the paragraph read "…"*" — which is round three's M-2. The
#: excision reaches a quotation only when a listed verb sits within forty
#: characters in front of it, and each row below is one way that fails to hold
#: while the note is still quoting rather than reasserting.
#:
#: These are deliberately **not** :data:`NOTE_EXCLUSION_CASES` rows. That table's
#: question is *is this an assertion the scan must report?*, and the honest answer
#: for all three is no, while the scan says yes. Filing them there would record a
#: known false RED as intended behaviour and make the exclusion table stop meaning
#: what it says.
#:
#: **A member that stops being reported is RED, and that is the good direction.**
#: It means the excision now reaches a shape it did not, which widens what a
#: correction note may hide; delete the row and name the mechanism that closed it
#: in the same commit. A ledger nobody has to empty is a ledger that grows — see
#: :data:`UNNARROWED_UNIVERSALS`, which is run on the same principle.
FALSE_RED_RESIDUE_CASES: Final[tuple[tuple[str, str], ...]] = (
    (
        "a lead verb outside `_RETRACTION_LEAD` -- `read`, excluded on purpose",
        f'{_NOTE_OPENING} The paragraph read "nothing in `src/` reads '
        '`.theurian/config.yaml`" before ADR-0027 decision 3 shipped a reader.',
    ),
    (
        "a lead verb further than forty characters from its quotation",
        f"{_NOTE_OPENING} The paragraph said, in the sentence decision 10 has held "
        'since Milestone 3, "nothing in `src/` reads `.theurian/config.yaml`".',
    ),
    (
        "the second quotation after one verb -- `re.sub` resumes past the first",
        f'{_NOTE_OPENING} The paragraph said "the default is safe to flip" and '
        '"nothing in `src/` reads `.theurian/config.yaml`".',
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


def _published_descriptions(text: str) -> list[str]:
    """Every ``description`` a JSON Schema publishes, one block each, root included.

    The third block reader, and it exists because the third scanned surface is
    neither Markdown nor YAML. ``project-config.schema.json`` is **wheel-shipped**
    (``hatch_build.py`` force-includes ``schemas/``), so every string here is a
    published field a user reads out of an installed artifact.

    Parsed rather than read as lines, for two reasons that both cut the same way.
    A JSON string cannot carry a raw newline, so there is no wrap to join and the
    *whole file as one block* — what :func:`_paragraphs` would produce, since the
    document holds no blank line — would let one description's sentence borrow the
    next one's exclusion. And the walk reaches the **root** description, which is
    not inside any ``properties`` block: that is precisely the member every
    population key for this class counted past (#455), because they counted key
    blocks and the root is not one.

    Non-``description`` strings are left out. They are enums, patterns and
    ``$id``s, and none of them is prose a reader takes a claim from.
    """
    blocks: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            description = node.get("description")
            if isinstance(description, str):
                blocks.append(_collapsed(description))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(text))
    return blocks


def _python_prose(text: str) -> list[str]:
    """A Python module's prose, joined and collapsed, one block per paragraph.

    The fourth block reader, for the three surfaces #447 narrowed and #199 unit B
    took: ``application/forest_builder.py``'s ``#:`` attribute docstrings and two
    test-module docstrings. Their sentences wrap exactly as the Markdown ones do,
    so a per-line scan would never see one whole.

    Blank lines are the boundary and comment markers are stripped;
    :func:`_paragraphs` cannot be reused because its block rule treats ``# `` as
    an ATX heading, which would make every commented line its own block and split
    every wrapped sentence in the file.

    **Code lines are read as prose, deliberately.** A retracted universal
    transcribed into a string literal is a sentence this repository ships, and
    a reader who greps for it finds it there; excluding literals would be a rule
    whose only effect is to hide members of the class this module watches.
    """
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        line = _PYTHON_COMMENT.sub("", raw)
        if not line.strip():
            blocks.append([])
            continue
        blocks[-1].append(line)

    return [collapsed for block in blocks if (collapsed := _collapsed(" ".join(block)))]


def _comment_blocks(text: str) -> list[str]:
    """A YAML document's comment prose, joined and collapsed, one block per run.

    The unit :func:`_paragraphs` is for Markdown, in the shape the sample config
    takes: its annotations are comment blocks, each broken across four or five
    lines, and a per-line scan would never see a sentence whole.

    **Whole-line and inline comments both** (round two, E3). A run of whole-line
    ``#`` comments is one block, because a sentence wraps across it. An *inline*
    comment is its own block and ends the run above it: it annotates the one key
    it sits on, and joining it to the paragraph above would let a sentence borrow
    half of an unrelated annotation. Reading whole lines only was a measured hole
    rather than a theoretical one — the pronoun sentence moved to
    ``repositories:  # Nothing in `src/` reads this file`` was invisible to this
    module while the block-comment form was caught.

    **What a ``#`` is taken to mean, which is the remaining bound.** A ``#`` that
    opens a line, or that follows whitespace, opens a comment. YAML also lets a
    ``#`` sit inside a quoted scalar, where it is data; this reads that as a
    comment and would report a value whose text happened to carry the retracted
    sentence. That over-approximates in the RED direction, which costs a read —
    and the file's values are booleans, integers and enumerated strings, so no
    such scalar exists here today.
    """
    blocks: list[list[str]] = [[]]
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            blocks[-1].append(stripped.lstrip("#").strip())
            continue

        inline = _INLINE_COMMENT.search(raw)
        blocks.append([inline.group("comment").strip()] if inline else [])
        blocks.append([])

    return [collapsed for block in blocks if (collapsed := _collapsed(" ".join(block)))]


def _without_code_span_emphasis(text: str) -> str:
    """``text`` with the asterisks inside inline code spans removed.

    CommonMark cannot open emphasis from inside a code span, so ``SELECT *``
    contributes a character and never a delimiter. :data:`_QUOTED_RETRACTION` has
    no such rule and read the asterisks of two such spans as one italic run, which
    is round three's H-1; :data:`_CODE_SPAN` records the measurement.
    """
    return _CODE_SPAN.sub(lambda span: _EMPHASIS.sub("", span.group()), text)


def _without_quotations(text: str) -> str:
    """``text`` with the quoted spans a retraction verb introduces replaced by a space.

    Applied to a dated correction note before the note is scanned, so that the
    retracted sentence the note quotes is invisible while anything the note
    *asserts* is not.

    Two neutralisations run first, and each exists to stop
    :data:`_QUOTED_RETRACTION` reading an emphasis span where CommonMark renders
    none:

    * :func:`_without_code_span_emphasis`, because ``SELECT *`` inside backticks
      is a character rather than a delimiter, and two of them bracketed a live
      reassertion that a retraction verb then handed to the excision (H-1);
    * :data:`_BOLD`, because ``**bold**`` would otherwise read as an italic span
      and a live reassertion written in bold would be excised with the quotations
      — the hole this function exists to close, not one to reintroduce.

    The retraction verb is what separates a quotation from an assertion, and
    :data:`_QUOTED_RETRACTION` records the trade. The lead is kept and only the
    quotation is replaced, so a sentence the note goes on to assert is still there
    to be read.
    """
    return _QUOTED_RETRACTION.sub(
        lambda match: f"{match.group('lead')} ",
        _BOLD.sub("", _without_code_span_emphasis(text)),
    )


def _unread_claims(blocks: Sequence[str], patterns: Sequence[re.Pattern[str]]) -> list[str]:
    """Every sentence in ``blocks`` that one of ``patterns`` reads as the retracted claim.

    A dated correction note is scanned with the quotations a retraction verb
    introduces excised, rather than skipped whole. Emphasis markers are then
    removed from every block, note or not, so that an italicised word is scanned
    as the word.

    **That order — excise, then strip — is what makes :data:`_CODE_SPAN`
    necessary**, and it is visible in the expression below rather than only
    described here. Because :data:`_EMPHASIS` runs afterwards, the excision reads
    every asterisk the block carries, so an asterisk that CommonMark would not
    treat as a delimiter has to be neutralised earlier or it opens an italic span
    that is not there.

    **The excision runs before the sentence split, and that order is a trade
    rather than a safeguard.** Removing a quotation that carries a sentence end
    joins its two neighbours into one span, which can then match across the seam:
    measured, and pinned by
    :func:`test_a_quotation_carrying_a_sentence_end_joins_across_the_seam`, which
    prints both orders. The reverse order — split, then excise within each
    sentence — does not join, and instead *breaks* an assertion whose middle
    carries such a quotation into two halves that neither match. The failure this
    order takes is a false RED on a paragraph that quotes; the failure it refuses
    is a live reassertion going unseen, which is what this module exists to
    prevent. So the order stays and the docstring says which way it fails.
    """
    claims: list[str] = []
    for block in blocks:
        scanned = _without_quotations(block) if _CORRECTION_NOTE.search(block) else block
        claims.extend(
            sentence.strip()
            for sentence in _SENTENCE_END.split(_EMPHASIS.sub("", scanned))
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
#: The pronoun pattern rides the two surfaces where "this file" has exactly one
#: possible referent — the sample config and the published schema, both of which
#: *are* or *describe* ``.theurian/config.yaml``. :data:`_THIS_FILE_UNREAD`
#: records why it may not ride the two Markdown documents, where "this file"
#: means the document a reader is holding.
#:
#: **The schema joined in #199 unit B, and it is the surface with the most to
#: lose.** Its root description is wheel-shipped, it was false from ADR-0027
#: decision 3 until unit B rewrote it, and it survived four sweeps of this class
#: because every key was subject-shaped and it names no path (#455). Nothing
#: watched it; this is what watches it now, in the negative direction, while
#: ``test_config_key_call_sites.py``'s ``WATCHED_KEY_DESCRIPTIONS`` holds the
#: positive one.
SCANNED_SURFACES: Final[
    tuple[tuple[pathlib.Path, Callable[[str], list[str]], tuple[re.Pattern[str], ...]], ...]
] = (
    (ADR_0008, _paragraphs, (_FILE_UNREAD,)),
    (RAPTOR_MD, _paragraphs, (_FILE_UNREAD,)),
    (SAMPLE_CONFIG, _comment_blocks, (_FILE_UNREAD, _THIS_FILE_UNREAD)),
    (PROJECT_CONFIG_SCHEMA, _published_descriptions, (_FILE_UNREAD, _THIS_FILE_UNREAD)),
    (FOREST_BUILDER, _python_prose, (_FILE_UNREAD,)),
    (FOREST_DERIVATION_TEST, _python_prose, (_FILE_UNREAD,)),
    (SCHEMAS_TEST, _python_prose, (_FILE_UNREAD,)),
)

#: How many ``issues/426`` correction notes each scanned surface carries, keyed by
#: the surface.
#:
#: **One per surface, not one in total**, which is round two's half of this
#: control. The excision defined by :data:`_CORRECTION_NOTE` can widen on *any*
#: surface it is applied to, and only ADR-0008's count was asserted: a note link
#: written into ``raptor.md``'s narrowing paragraph or into the sample config's
#: annotation would have started excising the quoted spans there with nothing
#: saying so. The zeroes are as load-bearing as the one.
#:
#: Both directions redden. A note deleted, or its link form dropped, makes the
#: excision stop matching the paragraph it was defined for — the scan then
#: *widens*, and the quotation it was hiding is reported as the universal
#: returning. A note added anywhere else narrows the scan by exactly one
#: paragraph, silently.
_NOTE_COUNTS: Final[dict[pathlib.Path, int]] = {
    ADR_0008: 1,
    RAPTOR_MD: 0,
    SAMPLE_CONFIG: 0,
    PROJECT_CONFIG_SCHEMA: 0,
    FOREST_BUILDER: 0,
    FOREST_DERIVATION_TEST: 1,
    SCHEMAS_TEST: 0,
}

#: The parametrized form, **derived from :data:`SCANNED_SURFACES` rather than
#: written out beside it**, which is round three's M-1.
#:
#: The two used to be hand-maintained tuples that happened to list the same three
#: paths in the same order, and nothing held them in step. A fourth surface added
#: to the scan and not here would have been scanned with its note count
#: unmeasured — the exact hole the per-surface counts were added to close, one
#: level up. A count recorded for a surface nobody scans would have read as
#: coverage of a file this module never opens.
#:
#: So the row set comes from :data:`SCANNED_SURFACES`, :data:`_NOTE_COUNTS`
#: supplies only the number, and
#: :func:`test_every_scanned_surface_has_a_recorded_note_count` holds the two key
#: sets equal in **both** directions — the set comparison is what catches a count
#: whose surface is not scanned, since no parametrized row is generated for it. A
#: scanned surface with no count arrives at its own case as ``None`` and fails
#: there by name, rather than erroring at import and taking every other test in
#: the module with it.
CORRECTION_NOTES_PER_SURFACE: Final[
    tuple[tuple[pathlib.Path, Callable[[str], list[str]], int | None], ...]
] = tuple((path, blocks, _NOTE_COUNTS.get(path)) for path, blocks, _ in SCANNED_SURFACES)


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
    ("shape", "sentence", "is_a_missed_defect"),
    MEASURED_ESCAPE_CASES,
    ids=[case[0][:60] for case in MEASURED_ESCAPE_CASES],
)
def test_the_recorded_escapes_are_still_escapes(
    shape: str,
    sentence: str,
    is_a_missed_defect: bool,
) -> None:
    """RED means a scan moved and the docstring's escape list is stale.

    The module docstring tells a reader how far these patterns reach, and until
    #199 unit B the sentence "measured escapes, recorded rather than chased" was
    the only thing standing behind that. It named five phrasings and no test ran
    any of them, so either pattern could have been widened -- or narrowed -- with
    the paragraph left describing a scan that no longer exists.

    Both directions are the same assertion here and mean different things. A row
    marked as a missed defect that starts matching is **good news and still RED**:
    the scan reaches a shape it did not, so delete the row and say in the same
    commit what closed it. The past-tense row starting to match is bad news of the
    opposite kind -- a pattern that flags the corrected sentence would make this
    module RED on a clean tree, and the only way back to green would be to
    un-narrow the prose it exists to protect.

    This asserts nothing about whether such a sentence is *true*. It asserts what
    the scans see, which is the only thing a scan can be held to.
    """
    collapsed = _collapsed(sentence)

    matched = bool(_FILE_UNREAD.search(collapsed)) or bool(_THIS_FILE_UNREAD.search(collapsed))

    assert not matched, (
        f"{shape}: {sentence!r} is now caught by one of the two scans, and the module "
        f"docstring still records it as a phrasing they do not reach.\n\n"
        + (
            "This is the good direction -- the scan is wider than it was. Delete the row "
            "from `MEASURED_ESCAPE_CASES`, and name the mechanism that closed it in the "
            "same commit."
            if is_a_missed_defect
            else "This is the bad direction. That sentence is the *corrected* wording, in "
            "the past tense, and a pattern that flags it goes RED against prose this "
            "module exists to protect. Narrow the pattern rather than the prose."
        )
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


@pytest.mark.parametrize(
    ("shape", "note", "is_a_live_assertion"),
    NOTE_EXCLUSION_CASES,
    ids=[case[0] for case in NOTE_EXCLUSION_CASES],
)
def test_the_correction_note_exclusion_hides_a_quotation_and_not_an_assertion(
    shape: str, note: str, is_a_live_assertion: bool
) -> None:
    """RED means the note exclusion is hiding what a note asserts, not what it quotes.

    Round-one mutation A7: a live reassertion placed inside ADR-0008's twenty-line
    correction note was invisible, while the identical sentence one paragraph away
    was caught. Excising the note's quoted spans closed that — and round two
    measured that a blanket quotation rule reopened it in four narrower shapes,
    one of which (E1, the reassertion bracketed by two ``SELECT *`` asterisks)
    SURVIVED a full-suite mutation run.

    **Two mechanisms hold these rows, and round three is why they are named
    separately.** Scoping the excision to a quotation a retraction verb introduces
    is what shows the four round-two shapes. It did *not* close E1: it only made
    E1 need a verb, and the two verb-led rows were hidden on ``ee9b5c4`` for that
    reason. :data:`_CODE_SPAN` is what closes them, by neutralising the asterisks
    inside inline code spans before the excision reads them. Stripping every
    emphasis marker first instead — the mechanism this docstring used to credit —
    would make the ADR-0008 negative below RED.

    The positives and the negatives are load-bearing in opposite directions. A
    positive that stops firing is a live reassertion the record no longer catches.
    A negative that starts firing is the *fix* being reported as the defect, which
    would make this module RED on a clean tree — and the last three are the two
    shipped notes' own quoting styles, so there would be no wording that made it
    green again short of deleting the notes.

    Asserted against synthetic notes rather than against the shipped documents,
    because neither shipped note carries a live reassertion: an exclusion that had
    stopped excluding anything would look identical there.
    """
    claims = _unread_claims([note], (_FILE_UNREAD,))

    assert bool(claims) is is_a_live_assertion, (
        f"a correction note with {shape} was read as "
        f"{'a live reassertion' if claims else 'a quotation'}, expected the opposite.\n\n"
        f"  note    : {note}\n"
        f"  excised : {_without_quotations(note)}\n"
        f"  claims  : {claims}\n\n"
        f"The exclusion must hide what a note quotes and show what it asserts. If a "
        f"positive stopped firing, `_QUOTED_RETRACTION` widened or `_CODE_SPAN` stopped "
        f"reaching an asterisk, and a reassertion inside a note is invisible again "
        f"(round-one A7, round-two E1, round-three's verb-led E1). If "
        f"a negative started firing, the excision no longer covers the shape a shipped "
        f"note actually uses and "
        f"`test_no_scanned_surface_reasserts_that_nothing_in_src_reads_the_config_file` "
        f"is RED on a clean tree."
    )


@pytest.mark.parametrize(
    ("shape", "note"),
    FALSE_RED_RESIDUE_CASES,
    ids=[case[0] for case in FALSE_RED_RESIDUE_CASES],
)
def test_the_verb_scoped_excision_still_reports_its_recorded_false_reds(
    shape: str, note: str
) -> None:
    """RED means a recorded false RED closed, and the record has to name what closed it.

    :data:`FALSE_RED_RESIDUE_CASES` is the class the module docstring and
    :data:`_QUOTED_RETRACTION` both record as the price of scoping the excision to
    a retraction verb. It was recorded as a single shape from ``5d97f93`` until
    round three -- "a note that writes *the paragraph read "…"*" -- and it is
    three, each a different way for a listed verb to fail to sit within forty
    characters in front of a quotation that is still a quotation.

    Asserted in the *reporting* direction, which is the one that can move
    silently. Each note here quotes rather than asserts, so a scan that reported
    nothing would look like the module working; what the assertion holds is that
    the cost is still being paid where the record says it is. Widening the
    excision to close a member is a real change to what a correction note may
    hide, and this is what makes somebody say so.
    """
    claims = _unread_claims([note], (_FILE_UNREAD,))

    assert claims, (
        f"the recorded false RED for {shape} is no longer reported.\n\n"
        f"  note    : {note}\n"
        f"  excised : {_without_quotations(note)}\n\n"
        f"That is the good direction and it is still RED: something widened the "
        f"excision -- `_RETRACTION_LEAD`, the forty-character window, or the way "
        f"`re.sub` resumes past a match -- so a correction note can now hide a shape "
        f"it could not before. Check that the widening does not also hide a live "
        f"reassertion (`NOTE_EXCLUSION_CASES` is the control), then delete this row "
        f"and name the mechanism in the module docstring's residue paragraph."
    )


def test_a_quotation_carrying_a_sentence_end_joins_across_the_seam() -> None:
    """RED means the excision/split order moved, and :func:`_unread_claims` says otherwise.

    The order is a trade and this is the measurement of it, landed because round
    two produced three disagreeing answers about whether a seam join was possible
    at all. It is.

    Excising a quotation that carries a sentence end removes the boundary between
    the two sentences around it, so the pattern's bounded gap can span what were
    two sentences — reported here as a claim the paragraph does not make. The
    reverse order is computed alongside and finds nothing, which is the other half
    of the trade: splitting first would instead cut an assertion whose middle
    carries such a quotation into halves that neither match, and a live
    reassertion going unseen is the failure this module exists to prevent. So the
    shipped order is the sensitive one, its cost is a false RED on a paragraph
    that quotes, and swapping it reddens here with both outputs printed.
    """
    note = (
        "Corrected in the #199 unit-A follow-up "
        "(https://github.com/theurian/theurian/issues/426). The note said nothing "
        '"at the time. It was fine" reads `.theurian/config.yaml` at all.'
    )

    joined = _unread_claims([note], (_FILE_UNREAD,))
    split_first = [
        sentence
        for sentence in _SENTENCE_END.split(note)
        if _FILE_UNREAD.search(_EMPHASIS.sub("", _without_quotations(sentence)))
    ]

    assert [_collapsed(claim) for claim in joined] == [
        "The note said nothing reads `.theurian/config.yaml` at all"
    ], (
        f"the excision no longer joins the two sentences a quoted sentence end "
        f"separated, so `_unread_claims`'s docstring describes an order it does not "
        f"take: {joined}"
    )
    assert not split_first, (
        f"splitting before the excision found the same span, so the two orders no "
        f"longer differ and the trade the docstring records is not the one in the "
        f"code: {split_first}"
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


# -- Every scanned surface: the universal must not come back -----------------


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


@pytest.mark.parametrize(
    ("path", "blocks", "expected"),
    CORRECTION_NOTES_PER_SURFACE,
    ids=[path.name for path, _, _ in CORRECTION_NOTES_PER_SURFACE],
)
def test_each_scanned_surface_carries_the_notes_the_exclusion_is_defined_for(
    path: pathlib.Path, blocks: Callable[[str], list[str]], expected: int | None
) -> None:
    """RED means the exclusion stopped being about anything, or covers a surface nobody chose.

    :func:`_unread_claims` excises the quoted spans of paragraphs naming issue
    #426 in its ``issues/426`` link form, because the dated correction note quotes
    the retracted sentence in order to retract it and a scan that read the
    quotation would report the fix as the defect.

    An exclusion nobody checks is the same shape as a population nobody counts,
    and both directions cost something. If ADR-0008's note is deleted or its issue
    reference is dropped, the excision stops matching that paragraph -- the scan
    then *widens*, and the quoted universal there is reported as new. If a note
    link appears on a surface that has none, the excision starts hiding the quoted
    spans of a paragraph nobody decided it should, and it does that silently.

    **Counted per surface, which is round two's half of this.** Only ADR-0008's
    count was asserted before, so ``raptor.md`` and the sample config could each
    have gained a note -- and with it an unreviewed exclusion -- with every test
    here green. The zeroes are pins, not documentation.

    **Exactly one for ADR-0008, and its second note is why the count is checked.**
    ADR-0008 also carries a correction note under the amendment to decision 3,
    written with the bare ``#426`` spelling; it quotes nothing retracted, so it
    needs no excision and is deliberately outside the key. Rewriting it to the
    link form would make this RED -- correctly.

    The rows come from :data:`SCANNED_SURFACES` and the numbers from
    :data:`_NOTE_COUNTS`, so a surface cannot be scanned without appearing here.
    ``expected is None`` is that derivation firing: the surface is scanned and
    nobody recorded what the exclusion reaches on it.
    """
    assert expected is not None, (
        f"{path.name} is scanned by this module and `_NOTE_COUNTS` records no note "
        f"count for it, so the reach of the `_CORRECTION_NOTE` exclusion on that "
        f"surface is unmeasured -- a note added there would start hiding quoted spans "
        f"silently, which is what the per-surface count exists to prevent. Record the "
        f"count in `_NOTE_COUNTS` beside the other surfaces."
    )

    notes = [
        block
        for block in blocks(path.read_text(encoding="utf-8"))
        if _CORRECTION_NOTE.search(block)
    ]

    assert len(notes) == expected, (
        f"{path.name} carries {len(notes)} blocks naming issue #426 in its "
        f"`issues/426` link form, expected {expected}.\n\n"
        f"That key is what `_unread_claims` excises quotations inside, so this count "
        f"decides how much of each surface is scanned with its quotations hidden. "
        f"ADR-0008 owns the one dated correction note that records what decision 10's "
        f"two `nothing in `src/` reads `.theurian/config.yaml`` sentences said; its "
        f"second note, under the amendment to decision 3, uses the bare `#426` spelling "
        f"and is outside this key on purpose. `raptor.md` and the sample config carry "
        f"none: a note added to either widens the exclusion there, so add it here in the "
        f"same change and say why the paragraph needs one.\n\n"
        + "\n".join(f"  {note[:120]}" for note in notes)
    )
    if expected:
        assert all("ADR-0027" in note for note in notes), (
            f"a correction note in {path.name} no longer names ADR-0027, which is what "
            f"falsified the retracted sentences: {notes}"
        )


def test_every_block_the_exclusion_runs_on_pairs_its_backticks() -> None:
    """RED means a correction note gained a backtick that pairs the wrong way.

    :data:`_CODE_SPAN` closes the verb-led E1 by neutralising the asterisks inside
    inline code spans, and it locates those spans by pairing backticks. An
    *unbalanced* backtick pairs a stray one with a real span's opening backtick,
    which leaves that span's asterisk exposed to :data:`_QUOTED_RETRACTION` again
    -- measured, and the false-**green** direction, so it is the one that needs a
    tripwire rather than a note. The probe:

        It says ` and `SELECT *`, and nothing in `src/` reads
        `.theurian/config.yaml` today, so `SELECT *` ends.

    is HIDDEN with the stray backtick and REPORTED without it.

    Scoped to the blocks the exclusion actually runs on -- the ones
    :data:`_CORRECTION_NOTE` selects -- because that is the whole reach of the
    neutralisation. A backtick elsewhere in a scanned file changes nothing here.
    The set of those blocks is held by
    :func:`test_each_scanned_surface_carries_the_notes_the_exclusion_is_defined_for`,
    so this cannot pass by finding none: a surface that lost its note reddens
    there first.

    An even count is a tripwire and not a parse. It catches the stray backtick,
    which is the shape that opens the hole; it does not prove that every pair a
    reader sees is the pair this matches.
    """
    notes = [
        (path.name, block)
        for path, blocks, _ in SCANNED_SURFACES
        for block in blocks(path.read_text(encoding="utf-8"))
        if _CORRECTION_NOTE.search(block)
    ]

    unbalanced = [(name, block) for name, block in notes if block.count("`") % 2]

    assert not unbalanced, (
        "a block the `_CORRECTION_NOTE` exclusion runs on carries an odd number of "
        "backticks, so `_CODE_SPAN` pairs them differently from the way a reader "
        "does -- a real code span's asterisk can be left exposed and a verb-led "
        "reassertion hidden with it (round three's H-1, reopened):\n"
        + "\n".join(f"  {name}: {block[:160]}" for name, block in unbalanced)
        + "\n\nBalance the backticks in the note, or say here why the pairing is "
        "still the one a reader sees."
    )


def test_every_scanned_surface_has_a_recorded_note_count() -> None:
    """RED means the two surface tables drifted, in whichever direction it happened.

    :data:`SCANNED_SURFACES` says which files are read and with which patterns;
    :data:`_NOTE_COUNTS` says how much of each is read with its quotations hidden.
    They were two hand-maintained tuples listing the same three paths, and nothing
    compared them -- so the control that counts notes *per surface* had no control
    of its own.

    Both directions are silent without this. A surface added to the scan and not
    to the counts would be scanned with the ``_CORRECTION_NOTE`` exclusion
    unmeasured on it; that case now reaches
    :func:`test_each_scanned_surface_carries_the_notes_the_exclusion_is_defined_for`
    as ``None`` and fails there by name, and this test names it too. A count
    recorded for a surface the scan does not read generates **no** parametrized
    row at all, so this set comparison is the only thing that sees it -- which is
    why the assertion is an equality rather than a subset.
    """
    scanned = {path for path, _, _ in SCANNED_SURFACES}

    assert set(_NOTE_COUNTS) == scanned, (
        f"`_NOTE_COUNTS` and `SCANNED_SURFACES` name different surfaces.\n\n"
        f"  scanned with no recorded note count: "
        f"{sorted(path.name for path in scanned - set(_NOTE_COUNTS))}\n"
        f"  counted but not scanned            : "
        f"{sorted(path.name for path in set(_NOTE_COUNTS) - scanned)}\n\n"
        f"`CORRECTION_NOTES_PER_SURFACE` is derived from `SCANNED_SURFACES`, so a "
        f"surface in the first list is scanned with the reach of the "
        f"`_CORRECTION_NOTE` exclusion unmeasured on it, and one in the second reads "
        f"as coverage of a file this module never opens. Record the count, or drop it "
        f"with the surface."
    )


# -- The region readers: a record that renders as nothing is a silent record --


def test_a_section_does_not_read_a_sentence_that_lives_in_an_html_comment() -> None:
    """RED means round-one mutation A6 is open again on ``raptor.md``.

    Markdown renders nothing inside ``<!-- -->``, so a pinned sentence moved into
    a comment leaves the record silent while a substring match over the raw text
    still finds it. :func:`_section` strips comments first; without a synthetic
    document that puts a sentence in one, that strip is the identity on every
    shipped input and deleting it changes no test in the suite -- which is what
    round two measured.
    """
    document = (
        "## Three levels\n\n"
        "**The threshold is real now and no `raptor` key is read.**\n\n"
        "<!-- What is unread is the `raptor` block, and this sentence renders as "
        "nothing. -->\n"
    )

    section = _section(document, _THREE_LEVELS)

    assert "**The threshold is real now and no `raptor` key is read.**" in section, (
        f"the rendered sentence was lost along with the comment: {section!r}"
    )
    assert "What is unread is the `raptor` block" not in section, (
        f"a sentence inside an HTML comment was read as part of the section, so a "
        f"record that renders as nothing would satisfy the positive pins: {section!r}"
    )


@pytest.mark.parametrize(
    ("region", "reader"),
    (
        ("`## Three levels`", lambda text: _section(text, _THREE_LEVELS)),
        ("ADR-0008 decision 10", _decision_ten),
        ("ADR-0008's amendment to decision 3", _decision_three_amendment),
    ),
    ids=["section", "decision-ten", "decision-three-amendment"],
)
def test_a_region_commented_out_with_an_unclosed_marker_is_not_findable(
    region: str, reader: Callable[[str], str]
) -> None:
    """RED means E2 is open: a ``<!--`` with no ``-->`` hides the region and nothing says so.

    An unclosed ``<!--`` opens an HTML block that runs to the end of the document
    when no line carries ``-->``, so GitHub renders nothing from there on. Measured
    on ADR-0008 with the marker inserted above decision 10: with a pattern that
    demanded the closing delimiter, :func:`_decision_ten` and
    :func:`_decision_three_amendment` both still found their regions and every test
    in the suite passed while the decision was invisible on the page.

    Each region reader is asserted to *refuse* rather than to return something
    empty. A reader that quietly returned an empty region would let the positive
    pins fail with "the sentence is gone", which reads as a wording change rather
    than as a document that renders as nothing.
    """
    document = (
        "<!-- everything from here is an unclosed HTML block\n\n"
        "## Three levels\n\n"
        "**The threshold is real now and no `raptor` key is read.**\n\n"
        "10. **`raptor.enabled` defaults to `false`.** `schemas/config/"
        "project-config.schema.json` declared it.\n\n"
        "**Amended in Milestone 6, by the forest-builder CL. The skip is real** and the "
        "threshold is a `ForestOptions` field.\n"
    )

    with pytest.raises(AssertionError, match="not findable"):
        reader(document)


def test_a_mermaid_arrow_does_not_open_an_html_comment() -> None:
    """RED means the unclosed-comment alternative reads a diagram edge as a comment.

    ``raptor.md`` carries twenty ``-->`` arrows and ADR-0008 eight. None of them
    is preceded by a ``<!--``, so none may start a match -- and the direction that
    would go wrong is a false RED on a document whose prose is intact, which is
    the failure mode that gets a pin deleted rather than fixed.
    """
    diagram = 'flowchart TB\n    A["Query"] --> B["Pre-filter"]\n    B --> C["Search"]\n'

    assert _HTML_COMMENT.sub(" ", diagram) == diagram, (
        "a mermaid arrow was stripped as an HTML comment; the closed form must be "
        "tried first and the unclosed form must need a `<!--` of its own"
    )


@pytest.mark.parametrize(
    "path",
    [path for path, _, _ in SCANNED_SURFACES],
    ids=[path.name for path, _, _ in SCANNED_SURFACES],
)
def test_no_scanned_surface_carries_an_html_comment_today(path: pathlib.Path) -> None:
    """RED means a scanned surface gained an HTML comment, and someone has to look at it.

    The comment strip is asserted to be a no-op on every shipped surface, which is
    what makes the region readers' "this is a no-op today and a closed door on the
    mutation" a measurement rather than a recollection.

    **It catches a comment appearing, and only some of the ways the pattern could
    widen** -- a widening is caught here exactly when it starts matching something
    the documents contain. Measured: replacing the runaway ``<!--`` alternative
    with ``<`` reddens this, because ``raptor.md`` carries ``<br/>``; replacing it
    with ``<!`` does not, because no surface carries a bare ``<!``. The widening
    that actually threatens these pins -- a ``-->`` opening a comment nobody
    opened -- has its own case in
    :func:`test_a_mermaid_arrow_does_not_open_an_html_comment`.

    A comment added on purpose is not a defect, and the remedy is not to delete
    it. It is to check that no pinned sentence is inside it -- Markdown renders
    nothing there -- and then to record the comment here, because a surface with
    a comment is a surface where the difference between "the record says it" and
    "a reader sees it" has stopped being free.
    """
    text = path.read_text(encoding="utf-8")

    assert _HTML_COMMENT.sub(" ", text) == text, (
        f"{path.name} now carries an HTML comment. Markdown renders nothing inside "
        f"one, so check first that no sentence this module pins -- "
        f"`RAPTOR_MD_SENTENCES`, `DECISION_TEN_SENTENCES`, `DECISION_THREE_SENTENCES` "
        f"or the sample config's annotation -- has moved into it (round-one mutation "
        f"A6). If the comment is deliberate and holds none of them, record it here."
    )


def test_the_sample_config_reader_sees_an_inline_comment_as_well_as_a_block() -> None:
    """RED means E3 is open: the retracted sentence hides on the key line it annotates.

    :func:`_comment_blocks` read whole-line comments only, so the pronoun sentence
    moved to ``repositories:  # Nothing in `src/` reads this file`` was invisible
    to this module while the block-comment form one line up was caught -- measured
    as a surviving mutation against the shipped sample config.

    The block boundaries are asserted, not just the text, because an inline
    comment that merged into the run above it would let a sentence be assembled
    out of two annotations that a reader sees as separate.
    """
    document = (
        "providers:\n"
        "  review:\n"
        "    # The allowlist review ingestion will read (SEC-10).\n"
        "    # This file is read for `security.secretScan` alone.\n"
        "    repositories:  # Nothing in `src/` reads this file.\n"
        "      - acme/order-service\n"
    )

    blocks = _comment_blocks(document)

    assert blocks == [
        "The allowlist review ingestion will read (SEC-10). This file is read for "
        "`security.secretScan` alone.",
        "Nothing in `src/` reads this file.",
    ], f"the sample config's comment prose was not blocked as expected: {blocks}"
    assert _unread_claims(blocks, (_FILE_UNREAD, _THIS_FILE_UNREAD)) == [
        "Nothing in `src/` reads this file"
    ], "the retracted pronoun sentence written as an inline comment was not reported"


def test_the_sample_config_reader_does_not_read_a_value_as_a_comment() -> None:
    """RED means a key line with no comment ends up in the prose the scan reads.

    The other direction of the same reader. A line that carries no ``#`` is data,
    and a reader that treated the whole line as prose would report the sample
    config's own values as sentences -- noise that would get the pin silenced.
    """
    document = "providers:\n  review:\n    adapter: none\n    repositories:\n"

    assert _comment_blocks(document) == [], (
        f"a YAML document with no comments produced prose: {_comment_blocks(document)}"
    )
