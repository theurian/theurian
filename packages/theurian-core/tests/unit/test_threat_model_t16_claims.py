r"""T-16's corrected claim about which tests read the three release-claim surfaces.

``docs/security/threat-model.md``'s T-16 entry carried a parenthetical that was
**measured false** and corrected in place by
https://github.com/theurian/theurian/pull/470. It said, of ``README.md``,
``packages/theurian-core/CHANGELOG.md`` and the threat model itself, that *no test
reads* any of them, and that ``test_setup_claims.py`` reads the *plugin's* README
rather than the root one. Both limbs were wrong: files under
``packages/theurian-core/tests`` do name ``README.md`` -- seven at `5a9a1e5` and
eight at `9d51a04` on the entry's loose key, five at both on the strict root-only
key this module computes -- and ``test_setup_claims.py`` carries the **root**
``README.md`` in its own ``CORE_ARRIVAL_SURFACES`` tuple, which the entry already
recorded twenty lines further up, so the document contradicted itself.

The conclusion survived on a narrower fact, and the entry now states it **on two
keys**, because the two answer differently: one module names the probe
*function*, two name the *step id*, and neither of the two ties the probe to any
of the three surfaces. This module holds both sides of that correction, over the
step-id key -- the one the entry says the pin holds.

**The prose side.** The retracted wording must stay retracted. Its two limbs are
:data:`RETRACTED_CLAIMS`, and the rule is not "these words appear nowhere" --
they appear in the entry today, inside the quotation that records the
correction, and a rule that refused them would report the fix as the defect.
The rule is that **every occurrence inside T-16 sits inside a quotation, in a
block that says the quotation is retracted**, and it takes two halves to hold
because either one alone has a hole the other covers:

- **Quotation, over every block.** A drift back to the false claim would be
  plain prose, so an unquoted match is a defect wherever it lands -- including
  in the correction's own block, which matters here, because the corrected
  sentence shares one blank-line block with the ``#56`` paragraph above it and a
  block-level rule alone would let a fresh denial in beside the marker.
- **Marker, over every block that carries the wording.** Requiring the marker of
  only the block the correction record is *anchored* in leaves the symmetric
  hole, and it was open until #470's round one measured it: a second block
  reasserting the claim in quotation form -- ``A later summary: "no test reads
  ..." remains the shape of the concern.`` -- is quoted, so the first half does
  not reach it, and is not the anchored block, so the marker rule never read it.
  Driven through the document against the shipped rules, that sentence left the
  module green. The requirement is now over the population.

**The fact side, recomputed rather than restated.** Two derivations, and each is
the point at which the record would have to move from *stated* to *held*:

- ``test_setup_claims.CORE_ARRIVAL_SURFACES`` must still carry a README at the
  repository root. Read out of that module's source (see
  :func:`_literal_string_tuple`) rather than copied here, so the pin fails when
  the constant loses the root README instead of agreeing with a memory of it.
- Exactly two modules under ``packages/theurian-core/tests`` may name the **step
  id**, and neither may name any of the three surfaces. Two is the step-id key's
  answer, not the probe function's -- the entry tabulates both, and says the
  step-id key is the one the pin holds. A third naming module, or a member that
  starts reading one of the three, is the moment the entry's "narrower fact"
  stops holding.

**This module must not join the population it measures**, and the way it does
that is the one thing here that looks like a trick. The entry publishes its own
key -- ``git grep -ln <token> -- packages/theurian-core/tests`` -- with no
self-exclusion in it, so a pin that carried the token would make the sentence it
defends false by existing: the published command would answer three where the
sentence says two. ``test_connection_claims.py`` solves the same problem the
other way, by quoting a key with a ``':!*test_connection_claims.py'`` pathspec in
it; that option is not available here without editing the entry's published key.
So the token is never written in this file. It is **derived** from
``StepId.ARTIFACT_INTEGRITY`` -- a live production constant, upper case, which
the entry's case-sensitive key does not match -- and every place a reader would
expect to see it spelled out (the claim module's file name, the probe function's
name) is built from that derivation instead. The exclusion is asserted, not
assumed, by
:func:`test_this_module_is_not_a_member_of_the_population_it_measures`.

The residue, **measured rather than reasoned** -- the first version of this
paragraph gave a cause that the numbers do not support, and #470's round one
caught it. ``git grep -lni`` -- case-insensitive -- does not answer two, and this
file is not why:

- At `5a9a1e5`, the anchor the entry's own table names and a commit at which this
  module did not yet exist, it already answered **three**: the two modules the
  entry names, plus ``tests/integration/test_setup_service.py``, whose line 152
  asserts over ``StepId.ARTIFACT_INTEGRITY`` in upper case.
- At `99d1f4b` it answers **four** -- those three, plus this file, which spells
  the constant in upper case in its prose and its derivation.

So the upper-case population is a different key with its own answer, and this
module moved that answer by one rather than creating the gap. The entry's key is
case-sensitive, its published count is the count that key returns, and it is
unaffected either way:
:func:`test_this_module_is_not_a_member_of_the_population_it_measures` is what
holds that, by this file's bytes rather than by this paragraph.

**Reach.** This module holds (1) that T-16 does not re-assert **the recorded
wording** of the retracted claim unquoted -- ``RETRACTED_CLAIMS`` is two regexes,
not a paraphrase detector, so (1) reaches that wording and rewordings that keep
the subject inside its window, and no further; (2) that every block carrying that
wording marks it retracted, and that the record is still findable at its anchor;
(3) that the root README is still a member of ``CORE_ARRIVAL_SURFACES``; (4) that
the step-id-naming population is exactly two named modules; (5) that neither
names any of the three surfaces; and (6) that each of the three surfaces is named
by some *other* module under the core tests tree.

**What the two keys do not reach, measured 2026-09-01 at `99d1f4b`.** The
``no-test-reads`` key is ``\bno test\b[^.]{0,40}?\breads?\b``. A subject longer
than that window escapes it -- *"no test anywhere under
packages/theurian-core/tests, at any level, reads README.md"* puts 59 characters
between the two anchors and does not match -- and so does any rewording that
drops the words *no test*: *"nothing in this repository reads README.md"*, *"not
one test reads"* and *"no automated check reads"* were each run against the key
and each escaped. The ``plugin-readme-not-the-root`` key is the recorded sentence
almost verbatim, so it reaches that sentence and near-nothing else: *"reads the
plugin README rather than the root one"* escapes it. Widening either key trades
those misses for false positives across an entry that discusses READMEs and tests
in most of its paragraphs, and a rule that cries wolf is deleted by the next
author. Recorded rather than closed; the fact side above is the half that does
not depend on wording, and it is what a reworded denial still has to get past.

It does **not** hold: that any test *reads* a surface it names -- a text scan
cannot tell a path constant from a ``read_text`` -- so the entry's "All three
files are read under ``packages/theurian-core/tests/``" is pinned only in its
weaker *named* form, except for the root README, whose read follows from
``test_setup_claims``'s own rules over the tuple. It does not hold the entry's
loose-key ``README.md`` figures (seven at `5a9a1e5`, eight at `9d51a04`): they
are counts over a key that is not this module's, and they churn on every new
member. The strict root-only count this module does compute appears in a failure
message and is asserted nowhere. It does not judge the tracker facts in the entry
(#39 closed, #80 live, the gap owned by #472): those are prose about an issue
tracker, outside anything a test can settle. And it does not claim the
cross-surface artifact-integrity gap is *closed* -- the entry states that gap as
owned rather than held, and this module is a pin over the sentence, not a control
that discharges it.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT, collapsed

from theurian.application import setup_steps
from theurian.domain.setup import StepId

#: The document this module reads, and the entry inside it. The heading marker
#: carries its ``\n`` so the slice anchors on a line start: a bare ``#### T-16``
#: would also match a mention of the entry in another entry's prose, and a slice
#: that started there would scan the wrong text without failing.
THREAT_MODEL: Final = REPO_ROOT / "docs/security/threat-model.md"
_ENTRY_HEADING: Final = "\n#### T-16 "

#: Every heading level that ends the entry. ``"\n## "`` does not match ``"\n### "``
#: because the space is part of the marker, so the three are disjoint and the
#: earliest of them is the end of the slice.
#:
#: **The three omitted levels, and why the omission is not a gap.** ``h5`` and
#: ``h6`` are *deeper* than T-16's own ``h4``, so a ``##### `` inside the entry is
#: a subsection **of** it and belongs in the slice; stopping there would be the
#: silent truncation
#: :func:`test_the_t16_slice_starts_at_its_heading_and_stops_at_the_next` exists
#: to catch, not a fix for one. ``h1`` is the only real omission, and it is
#: harmless by measurement rather than by argument: the file's sole ``# `` line is
#: its title at line 1, 1665 lines above T-16 (measured 2026-09-01 at `99d1f4b`),
#: so it never falls inside the text this slices. Adding it would not be free --
#: :func:`_entry` slices the *raw* text, before :func:`_without_code_fences`, so a
#: ``"\n# "`` marker would also match a shell comment line inside any of T-16's
#: eleven fenced blocks and truncate the entry silently. Zero such lines today
#: (same measurement); the trade is recorded rather than taken.
_HEADING_MARKERS: Final = ("\n## ", "\n### ", "\n#### ")

_CODE_FENCE: Final = re.compile(r"^\s*```")

#: A double-quoted span of normalised prose. Sequential pairing, so a block with
#: an odd number of quotes would pair the wrong ones -- refused at the premise by
#: :func:`_quoted_spans` rather than silently mis-scoped.
_QUOTED: Final = re.compile(r'"[^"]*"')

#: The tests tree the entry's key searches, and the population every scan below
#: walks.
CORE_TESTS: Final = REPO_ROOT / "packages/theurian-core/tests"

#: The step whose probe the entry's narrower fact is about. The token is
#: **derived** from it and never written here -- see the module docstring for the
#: reason, and
#: :func:`test_this_module_is_not_a_member_of_the_population_it_measures` for the
#: check that the derivation actually kept this file out of the population.
PROBE_STEP: Final = StepId.ARTIFACT_INTEGRITY

#: ``StepId.ARTIFACT_INTEGRITY`` lowercased: the exact string the entry's
#: published key greps for.
PROBE_TOKEN: Final = PROBE_STEP.name.lower()

#: The probe function in ``application/setup_steps.py``, and the claim module
#: named after it. Both built from :data:`PROBE_TOKEN`, so a rename of the step
#: id takes the derivation RED rather than leaving it searching for a string
#: nothing uses any more.
PROBE_FUNCTION: Final = f"probe_{PROBE_TOKEN}"
_CLAIM_MODULE: Final = f"test_{PROBE_TOKEN}_claim.py"

#: The two modules the entry names, as file names under :data:`CORE_TESTS`.
#: ``test_dogfood_corpus_governance.py`` is a member because it names the claim
#: module in its own prose, which is enough for a text key -- recorded here so a
#: reader who greps for the probe and finds a corpus-governance module does not
#: read it as a second probe test.
PROBE_NAMING_MODULES: Final = frozenset({_CLAIM_MODULE, "test_dogfood_corpus_governance.py"})

#: The three surfaces the retracted claim was about, each mapped to the phrase
#: the entry's own quotation uses for it. Written here and asserted *equal* to
#: what that quotation names, by
#: :func:`test_the_three_surfaces_are_the_ones_the_retracted_quotation_names`,
#: rather than parsed out of the document: a tuple parsed from the prose it is
#: checked against agrees with that prose by construction and measures nothing.
#:
#: **A mapping rather than a tuple, because one of the three is deictic.** The
#: threat model calls itself *"or this file"*; keyed on its path, the premise
#: would report the entry as having stopped naming a surface it names in every
#: clause of the sentence. The values are lower case because they are matched
#: against text :func:`_prose` has already normalised.
RELEASE_CLAIM_SURFACES: Final = {
    "README.md": "readme.md",
    "packages/theurian-core/CHANGELOG.md": "packages/theurian-core/changelog.md",
    "docs/security/threat-model.md": "or this file",
}

#: The same three, as repository-relative paths, for the scans that look for
#: references to them in source rather than in the entry's prose.
THREE_SURFACES: Final = tuple(RELEASE_CLAIM_SURFACES)

#: The module that carries ``CORE_ARRIVAL_SURFACES``, and the constant's name.
#:
#: **Read as source rather than imported, and the reason is measured.** Taken
#: 2026-09-01 at `b080a9a` from inside a test run: the ``conftest`` puts
#: ``packages/theurian-core/tests`` on ``sys.path`` and *not* ``tests/unit``, and
#: pytest's ``importlib`` mode has already imported this file under the name
#: ``packages.theurian-core.tests.unit.test_setup_claims`` -- which no ``import``
#: statement can spell, because of the hyphen. So an import here would not reach
#: the module pytest is running; it would build a second copy of it, and run its
#: whole module body again for a tuple of six strings.
#:
#: The cost of reading is that the constant has to stay a literal. A
#: ``CORE_ARRIVAL_SURFACES`` computed at import time takes this RED, which is the
#: safe direction: the pin says it can no longer read what it claims to check.
SETUP_CLAIMS: Final = CORE_TESTS / "unit/test_setup_claims.py"
ARRIVAL_SURFACES_CONSTANT: Final = "CORE_ARRIVAL_SURFACES"

#: The two limbs of the retracted parenthetical, matched against prose that has
#: been through :func:`_prose` -- lowercased, backticks and emphasis removed,
#: soft wraps flattened. Every key is lowercase for that reason.
#:
#: The first is deliberately a small window rather than the exact sentence: "no
#: test in the tree reads" is the same claim reworded, and a pin on the exact
#: wording would let it back in. The window stops at a full stop so it cannot
#: span two sentences.
RETRACTED_CLAIMS: Final = {
    "no-test-reads": re.compile(r"\bno test\b[^.]{0,40}?\breads?\b"),
    "plugin-readme-not-the-root": re.compile(r"\breads the plugin's readme, not the root\b"),
}

#: What makes a block a correction record rather than an assertion. Any one of
#: them satisfies the rule. They are the words the entry uses today; a correction
#: reworded past all three takes this RED, and the failure message says so, since
#: the two causes -- record deleted, record reworded -- call for opposite
#: responses.
RETRACTION_MARKERS: Final = ("which was false", "used to offer", "measured false")

#: The standing claim the corrected sentence still makes, and the key the record
#: is located by. Chosen from the sentence that survived the correction rather
#: than from the correction itself: a key built from the retraction stops
#: matching the moment the retraction is deleted, so the block would drop out of
#: the population instead of failing. This phrase is in **both** the false
#: version and the corrected one, so a straight revert is still found and still
#: judged.
STANDING_CLAIM_KEY: Final = "holds any of the three to the step's own words"


def _prose(text: str) -> str:
    """*text* normalised for a prose scan: no markup, no wraps, lower case.

    ``collapsed`` (from ``write_lock_claims``, the shared primitive every claim
    pin uses) does the lowercasing and the wrap flattening. Backticks and
    asterisks go first, because the retracted sentence names its files in code
    spans and italicises *plugin's*, and a key written the way the sentence reads
    would miss both.
    """
    return collapsed(text.replace("`", "").replace("*", ""))


def _without_code_fences(text: str) -> str:
    """*text* with every fenced block removed, delimiters included.

    T-16 pastes eleven ``console`` blocks, several of them ``git grep`` samples.
    A prose rule has no business reading a command, and a future correction that
    pastes a grep for the retired wording would otherwise report itself as the
    defect returning.

    Paired with :func:`_fence_delimiters`: an unterminated fence makes this a
    truncation rather than a filter, and a scan whose text was cut short reports
    the tail clean without having read it.
    """
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if _CODE_FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            kept.append(line)
    return "\n".join(kept)


def _fence_delimiters(text: str) -> list[str]:
    """Every code-fence delimiter line in *text*, in document order."""
    return [line for line in text.splitlines() if _CODE_FENCE.match(line)]


def _entry(text: str) -> str:
    """The T-16 entry: its heading to the next heading of any level.

    Scoped rather than file-wide for the reason ADR-0018's pins are: the
    correction record quotes the retracted wording verbatim, and other entries
    make their own claims about what tests read. A file-wide scan would have to
    decide which occurrence is which, and it would get that wrong the first time
    another entry retracted something in the same shape.
    """
    assert text.count(_ENTRY_HEADING) == 1, (
        f"the threat model has {text.count(_ENTRY_HEADING)} lines starting "
        f"`{_ENTRY_HEADING.strip()}`, expected 1; with none of them this module "
        f"scans nothing, and with two it scans whichever came first"
    )

    rest = text.split(_ENTRY_HEADING, 1)[1]
    ends = [found for marker in _HEADING_MARKERS if (found := rest.find(marker)) >= 0]
    return rest[: min(ends)] if ends else rest


def _blocks(text: str) -> list[str]:
    """*text* split on blank lines, each block normalised by :func:`_prose`.

    Blank lines only. The finer block boundaries ADR-0018's ``_paragraphs`` draws
    (list items, blockquote lines) are not wanted here: the rule below judges a
    block by whether it carries a retraction marker, and a marker written one
    sentence away from the quotation it introduces has to stay in the same block
    as it.
    """
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if not line.strip():
            blocks.append([])
            continue
        blocks[-1].append(line)

    return [flattened for block in blocks if (flattened := _prose(" ".join(block)))]


def _quoted_spans(block: str) -> list[tuple[int, int]]:
    """Every double-quoted span of *block*, with its quote count asserted even.

    Sequential pairing is only meaningful over an even number of quotes. An odd
    one means every span from the stray quote onward is the *inverse* of a
    quotation -- the unquoted text between two quotations -- and a rule asking
    "is this match inside a quotation" would answer the opposite of the truth
    without failing.
    """
    quotes = block.count('"')
    assert quotes % 2 == 0, (
        f"a T-16 block carries {quotes} double quotes, an odd number, so the "
        f"quoted spans below pair the wrong ones and every containment answer "
        f"after the stray quote is inverted: {block[:160]}"
    )
    return [match.span() for match in _QUOTED.finditer(block)]


def _unquoted_matches(block: str, pattern: re.Pattern[str]) -> list[str]:
    """Every match of *pattern* in *block* that is not inside a quotation."""
    spans = _quoted_spans(block)
    return [
        match.group()
        for match in pattern.finditer(block)
        if not any(start <= match.start() and match.end() <= end for start, end in spans)
    ]


def _literal_string_tuple(source: pathlib.Path, name: str) -> tuple[str, ...]:
    """The literal tuple of strings assigned to *name* at *source*'s top level.

    Both ``name: Final = (...)`` and a bare ``name = (...)`` are accepted, so the
    pin fails on the claim rather than on an annotation style someone changed.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    assigned: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                assigned.append(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            assigned.append(node.value)

    assert len(assigned) == 1, (
        f"`{name}` is assigned {len(assigned)} times at the top level of "
        f"{source.name}, expected once. Zero means it was renamed, moved or is "
        f"now computed rather than written, and this module can no longer read "
        f"the constant it says it checks"
    )

    literal = ast.literal_eval(assigned[0])
    assert isinstance(literal, tuple), (
        f"{source.name}'s `{name}` is no longer a literal tuple: {type(literal).__name__}"
    )
    not_strings = [member for member in literal if not isinstance(member, str)]
    assert not not_strings, (
        f"{source.name}'s `{name}` holds members that are not strings, so the "
        f"path comparison below is about something else: {not_strings}"
    )
    # Nothing is dropped: the assertion above is what makes this filter total.
    return tuple(member for member in literal if isinstance(member, str))


def _test_modules() -> dict[str, str]:
    """Every Python source under :data:`CORE_TESTS`, file name to text.

    A filesystem walk rather than ``git grep``: it is a superset of the tracked
    population the entry's key reads, so a third probe-naming module goes RED
    here *before* it is committed. Measured 2026-09-01 at `b080a9a`: every
    tracked file under this tree is a ``.py`` file, so the two populations differ
    only by untracked sources.

    ``__pycache__`` is skipped. A compiled module embeds its own source's names
    as constants, so a stale ``.pyc`` would answer for a file rather than about
    it.
    """
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(CORE_TESTS.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _reference_pattern(surface: str) -> re.Pattern[str]:
    """A key matching a reference to *surface* and not to a namesake of it.

    A surface inside a directory is matched by its full repository-relative path,
    because its base name is shared: ``CHANGELOG.md`` exists at the repository
    root, under ``packages/theurian-core`` and under ``plugins/claude-code``, and
    a base-name key would report a reference to any of them as a reference to
    the one T-16 is about.

    A surface at the root is matched by its name with nothing path-like before
    it, which is what separates the root ``README.md`` -- the file the false
    claim got wrong -- from ``plugins/claude-code/README.md``.

    **The blindness, stated rather than chased.** A module that builds the path
    out of segments (``REPO_ROOT / "packages" / "theurian-core" /
    "CHANGELOG.md"``) names no key here and is invisible to this scan. No member
    does that today; a rule that also accepted base names would trade this miss
    for false positives on two other changelogs, and a rule that cries wolf gets
    deleted by the next author.
    """
    if "/" in surface:
        return re.compile(re.escape(surface))
    return re.compile(rf"(?<![\w/.\-]){re.escape(surface)}")


def _modules_naming(surface: str) -> list[str]:
    """Every module under :data:`CORE_TESTS`, other than this one, naming *surface*.

    This module is excluded because it names all three surfaces itself, and a
    population that counted its own scanner is the assertion that cannot fail.
    The exclusion is by file name, not by the byte-level rule
    :data:`PROBE_TOKEN` gets: no published key counts the modules naming these
    three, so there is no outside claim for the exclusion to keep true.
    """
    pattern = _reference_pattern(surface)
    own_name = pathlib.Path(__file__).name

    return sorted(
        name
        for name, source in _test_modules().items()
        if name != own_name and pattern.search(source)
    )


def _entry_blocks() -> list[str]:
    """T-16's blocks, fenced code removed and fence parity asserted."""
    entry = _entry(THREAT_MODEL.read_text(encoding="utf-8"))

    fences = _fence_delimiters(entry)
    assert len(fences) % 2 == 0, (
        f"T-16 carries {len(fences)} code-fence delimiters, an odd number, so "
        f"everything after the last one is dropped as fenced and every scan "
        f"below reports the tail clean without reading it"
    )
    return _blocks(_without_code_fences(entry))


def _the_one_block_carrying(blocks: list[str], key: str) -> str:
    """The single block containing *key*, or a failure naming both causes."""
    found = [block for block in blocks if key in block]

    assert len(found) == 1, (
        f"`{key}` no longer identifies exactly one block of T-16: found "
        f"{len(found)}. Zero means the sentence was deleted or reworded past the "
        f"claim it is keyed on, and the correction record it anchors is no "
        f"longer findable; more than one means anything read out of it is about "
        f"text this module never chose"
    )
    return found[0]


def _retraction_audit(blocks: list[str]) -> tuple[dict[int, list[str]], list[str]]:
    """Which blocks carry a retracted limb, and which of those carry no marker.

    Extracted rather than written inline so the synthetic driver below runs
    *this* predicate instead of a copy of it. A driver that restated the rule
    would go RED on its own restatement and stay green whatever the rule did.
    """
    carrying = {
        index: sorted(family for family, key in RETRACTED_CLAIMS.items() if key.search(block))
        for index, block in enumerate(blocks)
        if any(key.search(block) for key in RETRACTED_CLAIMS.values())
    }
    unmarked = [
        f"block {index} carries {families}: {blocks[index][:300]}"
        for index, families in carrying.items()
        if not any(marker in blocks[index] for marker in RETRACTION_MARKERS)
    ]
    return carrying, unmarked


def test_the_three_surfaces_are_the_ones_the_retracted_quotation_names() -> None:
    """The premise every other rule here rests on: the tuple and the entry agree.

    :data:`THREE_SURFACES` is written by hand, so it can drift from the sentence
    it is supposed to describe -- and a pin over a tuple that no longer matches
    the document is a pin over nothing. Parsing the tuple out of the quotation
    instead would agree with the quotation by construction and measure nothing at
    all, which is the ``write_lock_claims`` shape: write the literal
    independently, assert it equal.

    Each surface is also required to exist. A rule that scanned for references to
    a file that was renamed would report every module as clean.

    **Scoped to the block, not to the quotation span inside it**, and the name of
    the local says so. ``phrase not in record_block`` is also satisfied by a
    surface named in the block's unquoted prose rather than inside the quotation;
    ``README.md`` appears both ways today. Narrowing it to the span is available
    and deliberately not taken: it would couple this premise to
    :func:`_quoted_spans`'s pairing, so a stray quote anywhere in the block would
    take the premise and the prose rules RED together and the first failure a
    reader saw would name the wrong cause.
    """
    record_block = _the_one_block_carrying(_entry_blocks(), STANDING_CLAIM_KEY)

    missing = [
        surface for surface, phrase in RELEASE_CLAIM_SURFACES.items() if phrase not in record_block
    ]

    assert not missing, (
        f"the T-16 block that carries the correction record no longer names "
        f"{missing}, so this module's idea of the three release-claim surfaces "
        f"has drifted from the entry's: {record_block[:400]}"
    )
    absent = [surface for surface in THREE_SURFACES if not (REPO_ROOT / surface).is_file()]
    assert not absent, f"these release-claim surfaces do not exist to be read: {absent}"


def test_t16_still_records_that_the_no_test_reads_claim_was_measured_false() -> None:
    """The correction record must survive, or a reader meets the false claim alone.

    The retracted wording has to stay quoted at its anchor, because a correction
    that deletes what it corrects leaves a reader unable to tell which of two
    revisions they are looking at -- and because the rules below have nothing to
    judge if the quotation goes.

    **Only the record's survival is checked here.** Whether a quotation is marked
    as retracted is a property of *every* block that carries the wording, not of
    this one, and it is held by
    :func:`test_every_t16_block_that_carries_the_retracted_wording_marks_it_retracted`.
    That split is the fix for a measured escape and not a tidy-up: with the
    marker required only of the block this key selects, a second block quoting
    the claim carried no marker and was never read.

    RED when the record goes: delete the quotation and the key finds nothing.
    """
    block = _the_one_block_carrying(_entry_blocks(), STANDING_CLAIM_KEY)

    unrecorded = sorted(family for family, key in RETRACTED_CLAIMS.items() if not key.search(block))

    assert not unrecorded, (
        f"T-16 no longer quotes the retracted claim's {unrecorded} limb, so the "
        f"record of what was measured false is gone and nothing tells a reader "
        f"the sentence above replaced anything: {block[:400]}"
    )


def test_every_t16_block_that_carries_the_retracted_wording_marks_it_retracted() -> None:
    """A block may carry the false claim only if it says the claim is retracted.

    The rule used to be narrower, and the hole was exactly the width of the
    narrowing. The marker was required of the **one** block located by
    :data:`STANDING_CLAIM_KEY`, so a second block reasserting the claim in
    quotation form -- ``A later summary: "no test reads ..." remains the shape of
    the concern.`` -- escaped both halves at once: quoted, so
    :func:`test_no_unquoted_prose_in_t16_denies_that_a_test_reads_those_surfaces`
    saw nothing, and not the anchored block, so the marker rule never read it.
    Measured against the shipped module by appending that sentence to T-16 in a
    throwaway checkout: the file stayed green, 16 passed.

    So the requirement is over the population. Together the two halves cover the
    wording either way it is written -- unquoted anywhere is a defect outright,
    quoted is a defect unless its own block records the retraction -- and that is
    the sentence the module docstring makes: *every occurrence sits inside a
    quotation, in a block that says the quotation is retracted*.

    The two failure causes need opposite responses, so the message names both:
    a record deleted is restored, a record reworded past all of
    :data:`RETRACTION_MARKERS` moves the markers instead.
    """
    carrying, unmarked = _retraction_audit(_entry_blocks())

    assert carrying, (
        "no block of T-16 carries the retracted wording at all, so this rule "
        "read an empty population and would pass over any document. The record "
        "itself is what is gone, and "
        "`test_t16_still_records_that_the_no_test_reads_claim_was_measured_false` "
        "says that in the form a reader can act on"
    )
    assert not unmarked, (
        f"{len(unmarked)} block(s) of T-16 carry the retracted claim and say "
        f"nothing about it being retracted, so a reader meets the false wording "
        f"as a live statement. Every block quoting it must carry one of "
        f"{list(RETRACTION_MARKERS)}; either the marker was deleted -- restore "
        f"it -- or the record was reworded and `RETRACTION_MARKERS` is what has "
        f"to move: {unmarked}"
    )


@pytest.mark.parametrize("family", sorted(RETRACTED_CLAIMS))
def test_no_unquoted_prose_in_t16_denies_that_a_test_reads_those_surfaces(family: str) -> None:
    """The false claim may be quoted as history; it may not be asserted again.

    This is the rule the whole module exists for. The claim was corrected in
    place once; nothing stops it being written again by an author who reads the
    conclusion ("nothing holds the three to the step's own words") and reaches
    for the evidence that used to be offered for it.

    Quotation is the discriminator rather than the block, and that choice is
    forced by the document: the corrected sentence shares one blank-line block
    with the ``#56`` paragraph above it, so a block-level rule would accept a
    fresh, unquoted denial sitting beside the marker that retracts the old one.
    """
    offenders = {
        index: matches
        for index, block in enumerate(_entry_blocks())
        if (matches := _unquoted_matches(block, RETRACTED_CLAIMS[family]))
    }

    assert not offenders, (
        f"T-16 asserts the retracted `{family}` claim outside any quotation: "
        f"{offenders}. It is false, and the counter-evidence is measured here "
        f"rather than quoted from the entry: {len(_modules_naming('README.md'))} "
        f"modules under packages/theurian-core/tests name the *root* README.md, "
        f"counted here on the strict key the entry says the pin computes. Do not "
        f"read that against the entry's larger loose-key figure: "
        f"`git grep -ln 'README.md'` also matches path-prefixed namesakes -- "
        f"plugins/claude-code/README.md and tests/e2e/README.md -- and does not "
        f"exclude this module from its own population. "
        f"`{ARRIVAL_SURFACES_CONSTANT}` carries the root one. What survived the "
        f"correction is the narrower fact about the setup probe"
    )


def test_the_no_test_reads_limb_is_caught_when_it_comes_back_as_plain_prose() -> None:
    """RED means the rule above cannot fail, whatever the document says.

    Driven with synthetic text, because the shipped document is compliant and a
    rule measured only against a compliant document is indistinguishable from one
    that returns "nothing is wrong". The input is the sentence as it stood before
    https://github.com/theurian/theurian/pull/470, unquoted, with the quotation
    that legitimately carries it in the same block -- the exact arrangement the
    quotation discriminator exists for.
    """
    reverted = _prose(
        "Nothing in this repository holds any of the three to the step's own "
        'words -- no test reads `README.md` or this file -- and it said "no test '
        'reads `README.md` or this file", which was false.'
    )

    offenders = _unquoted_matches(reverted, RETRACTED_CLAIMS["no-test-reads"])

    assert offenders, (
        "the retracted claim written as plain prose was not caught, so the rule "
        "over the shipped entry passes for a reason that has nothing to do with "
        "the entry"
    )


def test_the_plugin_readme_limb_is_caught_when_it_comes_back_as_plain_prose() -> None:
    """The second limb needs its own driver, and did not have one.

    ``RETRACTED_CLAIMS`` is parametrized over both families, so the rule *runs*
    for both -- but running is not failing. Only the ``no-test-reads`` limb had a
    synthetic case proving its key can match, which left the
    ``plugin-readme-not-the-root`` key indistinguishable from one that matches
    nothing: a typo in it would have shown up as a green test.

    Same arrangement as its sibling: the limb unquoted, with the quotation that
    legitimately carries it in the same block, so the quotation discriminator has
    to separate them rather than reject the block wholesale.

    **What this pins is the recorded sentence, not the claim.** The key is that
    sentence almost verbatim, and *"reads the plugin README rather than the root
    one"* -- the same assertion reworded -- escapes it. Recorded in the module
    docstring's reach paragraph with the other measured escapes; not closed,
    because widening it would fire on an entry that discusses READMEs throughout.
    """
    reverted = _prose(
        "Nothing in this repository holds any of the three to the step's own "
        "words -- `test_setup_claims.py` reads the *plugin's* README, not the "
        "root one -- and it said \"`test_setup_claims.py` reads the *plugin's* "
        'README, not the root one", which was false.'
    )

    offenders = _unquoted_matches(reverted, RETRACTED_CLAIMS["plugin-readme-not-the-root"])

    assert offenders, (
        "the plugin-readme limb written as plain prose was not caught, so the "
        "parametrized rule runs for this family without being able to fail on it"
    )


def test_a_quoted_reassertion_in_a_marker_less_block_is_caught() -> None:
    """The escape sec-M3 measured, driven both directions through the real rule.

    Shape B, as the reviewer wrote it: the retracted wording quoted -- so the
    unquoted rule sees nothing -- in a block that is not the one
    :data:`STANDING_CLAIM_KEY` selects, which is how it also missed the marker
    rule before that rule was widened to the population. Both halves green, the
    claim reasserted.

    Driven through :func:`_retraction_audit`, the predicate the document rule
    calls, so this measures the shipped rule rather than a restatement of it. The
    second half is what stops the assertion being satisfiable by a rule that
    refuses every quotation: the *same sentence* with a marker in its block is
    required to come back clean, which is what keeps the correction record itself
    legal.
    """
    shape_b = _prose(
        'A later summary: "no test reads `README.md`, '
        '`packages/theurian-core/CHANGELOG.md` or this file" remains the shape '
        "of the concern."
    )

    _, unmarked = _retraction_audit([shape_b])

    assert unmarked, (
        "a block quoting the retracted claim with nothing marking it retracted "
        "was not caught, so the widened marker rule is green over the shipped "
        "entry for a reason that has nothing to do with the entry"
    )
    _, still_legal = _retraction_audit([f"{shape_b} {RETRACTION_MARKERS[0]}."])
    assert not still_legal, (
        f"the same sentence in a block carrying `{RETRACTION_MARKERS[0]}` was "
        f"also refused, so the rule rejects quotation rather than the absence of "
        f"a marker -- and the entry's own correction record would be the defect"
    )


def test_the_t16_slice_starts_at_its_heading_and_stops_at_the_next() -> None:
    """RED means the entry slice reaches text T-16 does not own.

    Synthetic again, and the two failures it separates are not symmetric. A slice
    that starts too high scans another entry's prose and can go RED on a
    correction that has nothing to do with this one. A slice that runs past the
    next heading widens every scan below it silently, which is worse: it never
    fails, it just answers about more text than it says it does.
    """
    document = (
        "#### T-15 the entry above\n\nabove the slice\n\n"
        "#### T-16 the entry this module reads\n\ninside the slice\n\n"
        "### TB-3 the section after\n\nbelow the slice\n"
    )

    sliced = _entry(document)

    assert "inside the slice" in sliced, f"the slice does not contain T-16's own body: {sliced!r}"
    assert "above the slice" not in sliced, f"the slice starts above T-16's heading: {sliced!r}"
    assert "below the slice" not in sliced, f"the slice runs past T-16's entry: {sliced!r}"


def test_the_probe_token_names_a_live_step_and_a_live_probe_function() -> None:
    """The derivation is anchored to production, or every scan below is a typo.

    :data:`PROBE_TOKEN` is the entry's search key rebuilt from
    ``StepId.ARTIFACT_INTEGRITY``, and :data:`PROBE_FUNCTION` is the probe's name
    rebuilt from that. If either stops resolving, every population scan below is
    searching the tree for a string nothing uses -- and each would then report an
    empty, compliant population rather than failing.

    Nothing here restates the derivation back to itself: an assertion that
    ``PROBE_TOKEN`` equals ``PROBE_STEP.name.lower()`` is true by the line that
    defines it and holds whatever production does. What is asserted is the two
    ways production could move out from under it -- the attribute, and the
    spelling in the source file that defines the attribute. The second is not
    redundant: a ``setup_steps`` that re-exported the probe under a new name
    would satisfy the first and leave the key pointing at a spelling the module
    no longer contains.
    """
    assert hasattr(setup_steps, PROBE_FUNCTION), (
        f"`application/setup_steps.py` has no `{PROBE_FUNCTION}`, so the token "
        f"this module derives from `StepId.{PROBE_STEP.name}` no longer names "
        f"the probe T-16's narrower fact is about, and every population scan "
        f"below is looking for a dead string"
    )

    defining_module = setup_steps.__file__
    assert defining_module is not None, "`setup_steps` has no source file to read"
    assert PROBE_TOKEN in pathlib.Path(defining_module).read_text(encoding="utf-8"), (
        f"`{PROBE_TOKEN}` does not appear in the source of "
        f"{pathlib.Path(defining_module).name}, so the step id and the probe "
        f"have been spelled apart and the entry's published key searches for a "
        f"string production stopped using"
    )


def test_the_root_readme_is_still_a_member_of_the_surfaces_setup_claims_reads() -> None:
    """The fact the corrected sentence cites by name, recomputed from the constant.

    The retracted claim said ``test_setup_claims.py`` reads the plugin's README
    and not the root one. What makes that false is one member of
    ``CORE_ARRIVAL_SURFACES``: a README with no directory part. Membership is
    what is asserted, because membership is what the entry claims -- and it is
    read out of the constant rather than restated, so this fails when the tuple
    changes rather than when someone's memory of it does.

    Root-ness is decided structurally rather than by comparing against a written
    ``"README.md"``: the distinction the false claim got wrong is *which*
    README, and a path with no parent is what "the root one" means.
    """
    surfaces = _literal_string_tuple(SETUP_CLAIMS, ARRIVAL_SURFACES_CONSTANT)

    readmes = [
        pathlib.PurePosixPath(surface) for surface in surfaces if "readme" in surface.lower()
    ]
    at_the_root = [str(readme) for readme in readmes if readme.parent == pathlib.PurePosixPath(".")]

    assert len(at_the_root) == 1, (
        f"`{ARRIVAL_SURFACES_CONSTANT}` carries {len(at_the_root)} READMEs at the "
        f"repository root, expected exactly one. T-16's correction says this "
        f"tuple carries the root README, and that sentence has to move with the "
        f"tuple: {[str(readme) for readme in readmes]}"
    )


def test_exactly_two_modules_under_the_core_tests_tree_name_the_setup_probe() -> None:
    """The narrower fact the entry fell back on, held as a population.

    The entry's conclusion survives on this and nothing wider: only two modules
    name the probe at all. A third is not a bug -- it is the moment the entry has
    to be rewritten, because the sentence enumerating the two by name stops being
    true and the gap it records may have acquired a holder.

    Held as an exact set rather than a count. A count goes green when one member
    leaves and another arrives, which is the same drift with both names changed.
    """
    naming = {name for name, source in _test_modules().items() if PROBE_TOKEN in source}

    assert naming == set(PROBE_NAMING_MODULES), (
        f"the modules under packages/theurian-core/tests naming `{PROBE_TOKEN}` "
        f"are {sorted(naming)}, and T-16 says they are "
        f"{sorted(PROBE_NAMING_MODULES)}. A third one means the entry's "
        f"'only two test modules name it at all' is false and the record of the "
        f"unowned cross-surface gap has to be re-taken -- it may now be held"
    )


def test_neither_module_that_names_the_probe_names_any_of_the_three_surfaces() -> None:
    """The second limb: naming the probe is not the same as holding the surfaces.

    Two modules name the probe, and the entry's claim is that neither of them
    ties it to ``README.md``, ``packages/theurian-core/CHANGELOG.md`` or the
    threat model. One of them starting to is exactly the change that would close
    the gap the entry records as unowned -- so this going RED is a prompt to move
    the record to *held*, not a defect to revert.
    """
    sources = _test_modules()
    missing = sorted(name for name in PROBE_NAMING_MODULES if name not in sources)
    assert not missing, (
        f"T-16 names {missing} as modules that reach the probe, and no such file "
        f"is under packages/theurian-core/tests, so this rule read nothing"
    )

    reached = {
        (name, surface)
        for name in PROBE_NAMING_MODULES
        for surface in THREE_SURFACES
        if _reference_pattern(surface).search(sources[name])
    }

    assert not reached, (
        f"a module that names the probe now also names a release-claim surface: "
        f"{sorted(reached)}. T-16 says neither does, and that sentence is the "
        f"whole of what survived the correction -- if this is a real pin, the "
        f"entry's 'the cross-surface pin has no owner' has to move to held"
    )


def test_this_module_is_not_a_member_of_the_population_it_measures() -> None:
    """The self-exclusion is measured, because the entry's published key has none.

    T-16 quotes ``git grep -ln <token> -- packages/theurian-core/tests`` and
    says the answer is two. A pin that carried the token would make that
    published command answer three, so the sentence this module defends would be
    false because the pin exists. The token is therefore derived from
    ``StepId.ARTIFACT_INTEGRITY`` and never written here -- and *never written*
    is a property of this file's bytes, which is a thing that can be checked
    rather than intended.

    RED is not a defect in the rules above: it means someone wrote the token into
    this file, this module joined the population, and either the token goes or
    the entry's published key needs a pathspec exclusion the way
    ``test_connection_claims.py``'s does.
    """
    own_source = pathlib.Path(__file__).read_text(encoding="utf-8")

    assert PROBE_TOKEN not in own_source, (
        f"this module now writes `{PROBE_TOKEN}` verbatim, so the entry's own "
        f"key returns one file more than the entry says it does, and "
        f"`test_exactly_two_modules_under_the_core_tests_tree_name_the_setup_probe` "
        f"is measuring a population it is a member of"
    )
    assert pathlib.Path(__file__).name not in PROBE_NAMING_MODULES, (
        "this module is listed as one of the two the entry names, which it is not"
    )


@pytest.mark.parametrize("surface", THREE_SURFACES)
def test_each_release_claim_surface_is_named_by_some_other_module_here(surface: str) -> None:
    """The half of the correction that falsified the old evidence, kept true.

    The retracted claim was that *no* test reads these three. What replaced it
    opens "All three files are read under ``packages/theurian-core/tests/``",
    and this is that sentence in its weaker, checkable form: some module other
    than this one names each surface. **Named is weaker than read** -- a text
    scan cannot tell a path constant from a ``read_text`` -- and the stronger
    reading is held for the root README alone, by ``test_setup_claims``'s own
    rules over ``CORE_ARRIVAL_SURFACES``.

    This module is excluded by :func:`_modules_naming`, because it names all
    three surfaces itself and a rule satisfied by its own existence is the
    assertion that cannot fail.
    """
    naming = _modules_naming(surface)

    assert naming, (
        f"nothing under packages/theurian-core/tests names `{surface}` any more, "
        f"so T-16's 'All three files are read under "
        f"packages/theurian-core/tests/' is no longer true of this one and the "
        f"retracted claim it replaced would be right about it"
    )
