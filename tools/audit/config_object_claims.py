"""Class 1: liveness claims about a watched object, keyed on the object (#199 unit B).

**Why the key is object-shaped.** Subject-keyed sweeps for the "nothing in
``src/`` reads ``.theurian/config.yaml``" class missed a live member in five
consecutive passes -- #426's five faces, #447's three, #455's schema root (which
even #455's own corrected key missed), and #461's plugin face, which drops
``src/`` from the sentence entirely and names the file by pronoun. The theorem
those five misses demonstrate, and the rule this module is built on:
**you cannot enumerate the phrasings of a claim; you can enumerate the
references to a bounded object.**

**The qualifier the theorem needs, which round two measured.** Enumerating the
objects bounds *what* a claim can be about; it does not bound *how the sentence
spells the reference*, and that is a second surface with its own escapes. A path
wrapped in bold is the same reference to the same enumerated object and defeated
every key here (R2-B); :func:`as_read` is what removes that whole dimension, by
matching on text whose emphasis is gone rather than on text whose markup a
pattern has to anticipate. What is left of the dimension is not argued away:
:data:`MEASURED_ESCAPES` runs it, so the bound is a table that fails rather than
a sentence that rots.

So the population is built the other way round. First an *inventory* of objects,
each derived by a machine rather than transcribed:

1. **The schema's key surface** -- ``schemas/config/project-config.schema.json``
   parsed, every ``properties`` path that publishes a ``description``, plus the
   root description, which is outside the key-block count and is exactly the
   member #455 records every sweep having missed.
2. **The ``ProjectPaths`` file surface** -- the class imported and every
   ``Path``-valued property read, so a file the product learns to write joins
   this inventory without anybody editing a list.
3. **The paths a governed document names** -- every ``.theurian/...`` path
   spelled in governed prose, at the depth a claim is made about.

Then, for each object, one claim key. A row is a *sentence* -- wrap-joined, so a
claim typed across two source lines is one string here -- that carries a
negated-liveness shape whose grammatical object is the watched object, in a block
that refers to it.

**Four shapes, and the last two exist because they were measured escaping.**
:data:`_NEGATED_LIVENESS_TEMPLATE` is the file-wide claim; the key-scoped form is
its sibling and is *not* a defect, which is the whole point of #426's narrowing.
:data:`_UNSCOPED_TEMPLATE` is "no config surface" --
``test_raptor_config_claims.py:74`` records it as a phrasing that module's scan
measurably does not catch, and ``application/forest_builder.py``'s corrected
block opens with exactly those words. :data:`_PRONOUN_TEMPLATE` is *"nothing
reads it"*, the bare pronoun, which no path-bearing key can see at all.

**Every row is classified, and the ledger reconciles in three directions.**
The machine clears the rows it can defend -- a *dated* CHANGELOG entry, a
past-tense sentence, a key-scoped claim -- and everything left is a *suspect*
that a person
verified and recorded in :data:`SUSPECTS` with a verdict. A suspect the ledger
does not carry is a finding; a ledger row the sweep no longer produces means
somebody fixed or moved a sentence and the ledger has to be discharged in the
same change; and a ledger row that covers *more than one* suspect is one
judgement standing in for two sentences, which is what a substring key absorbs
(round two's R2-A). All three are exit status 1.

Run it::

    uv run --frozen python tools/audit/config_object_claims.py

``--positive-control`` runs the key against planted sentences instead of the
tree, so a zero from the real run is only ever read after the key has been shown
to hit something. A sweep whose key matches nothing reports the same silence as a
clean tree.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from claim_surfaces import (
    Sentence,
    dated_lines,
    governed_paths,
    load_json,
    planted_changelog,
    repo_root,
    sentences,
    without_emphasis,
)

#: The published schema whose key surface is enumeration source 1.
PROJECT_CONFIG_SCHEMA: Final = "schemas/config/project-config.schema.json"

#: This directory, excluded from the sweep's own population.
#:
#: **The recursion boundary, and it is not a convenience.** A census's ledger has
#: to quote the sentences it classifies and its positive controls have to plant
#: them; both live in tracked files here. Without this the sweep reports its own
#: ledger rows as unrecorded suspects, and recording them would add fresh copies
#: of the same sentences for the next run to report. Measured: the commit that
#: first *tracked* these modules took this audit from exit 0 to exit 1 with seven
#: new rows, every one a quotation inside this file -- an untracked file is
#: invisible to ``git ls-files``, so the population moved when the commit landed
#: and not when the text was written.
#:
#: **Scoped to this directory, never to ``tools/``.** A liveness claim written
#: into ``tools/corpus_drift.py`` or ``tools/mutate.py`` is a claim this
#: repository ships, and it stays in the population.
SELF: Final = "tools/audit/"

#: Files whose *subject* is the project config, so a pronoun inside them resolves
#: to it. Derived from nothing -- this is a judgement, and it is stated here
#: rather than buried: the schema describes the file, and the sample project *is*
#: one. Both are surfaces where "this file" has one possible referent.
CONFIG_HOMES: Final[tuple[str, ...]] = (
    PROJECT_CONFIG_SCHEMA,
    "examples/sample-project/.theurian/config.yaml",
)

_NEGATION: Final = r"(?:nothing|nobody|none|no\s+one|no\s+code|no\s+module)"
_LIVENESS: Final = r"(?:reads?|opens?|loads?|consumes?|honours?|honors?|acts\s+on|consults?)"

#: The file-wide claim: a negation, a liveness verb, and the watched object as
#: that verb's grammatical object. ``{obj}`` is substituted per inventory member.
_NEGATED_LIVENESS_TEMPLATE: Final = (
    r"\b{neg}\b[^.;]{{0,60}}?\b{live}\b\s*(?:back\s+)?{obj}"
    r"|\bno\s+(?:reader|loader|consumer)\s+(?:of|for)\s+{obj}"
    r"|{obj}[^.;]{{0,40}}?\b(?:is|are)\s+(?:still\s+)?unread\b"
    r"|{obj}[^.;]{{0,40}}?\bhas\s+no\s+(?:reader|loader|consumer)\b"
)

#: The bare pronoun: *"nothing reads it"*. It names no object at all, so it is
#: admitted only inside a block that refers to the watched object -- which is
#: how a reader resolves it too.
_PRONOUN_TEMPLATE: Final = r"\b{neg}\b[^.;]{{0,60}}?\b{live}\b\s+it\b"

#: "no config surface", and the reason it is its own shape: it asserts the
#: absence of a *whole class* of reader without naming a verb or an object, so
#: neither of the two above can see it. Measured escaping
#: ``test_raptor_config_claims.py``'s scan; recorded at that module's line 74.
_UNSCOPED_TEMPLATE: Final = r"\bno\s+config(?:uration)?\s+surface\b"

#: The tense and framing markers that make a sentence a record rather than an
#: assertion. Deliberately narrow: ``says`` is absent, because *"the schema says
#: nothing reads this file"* is a live claim wearing a reporting verb.
#:
#: **Two alternatives were removed in round one, both of which cleared live
#: prose.** ``until`` is a *future* marker as often as a past one -- "nothing in
#: ``src/`` reads ``.theurian/config.yaml`` until review ingestion lands" is the
#: retracted universal with a clause attached, and it cleared as a record. And
#: the bare verb ``read`` matched the present tense inside a modal: "nothing in
#: ``src/`` **can read** ``.theurian/config.yaml``" is a live claim, and
#: ``read\b(?!s)`` read it as the past tense of the same verb. The past tense the
#: corrected rationale actually uses -- "when this was written nothing read
#: ``.theurian/config.yaml`` at all" -- is still cleared, by
#: ``when this was written`` and by ``was``, so nothing this list exists to
#: protect moved.
_RECORD_MARKERS: Final = re.compile(
    r"\b(?:said|stated|used\s+to|no\s+longer|previously|was|were|had\s+been|"
    r"when\s+this\s+was\s+written|before\s+this|corrected|retracted|falsified|"
    r"narrowed|carried|quoted)\b",
    re.IGNORECASE,
)

#: Files whose *dated* entries are records of a release, so a claim quoted in one
#: is history by construction. Correcting one would falsify the record.
#:
#: ``[Unreleased]`` is **not** one of those entries, which is round one's M-j:
#: it describes the tree a reader has checked out rather than what a release did,
#: it is edited on every merge, and it was cleared unread. Neither is a changelog
#: with no dated sections at all -- the root ``CHANGELOG.md`` -- which round two's
#: R2-j is. :func:`claim_surfaces.dated_lines` answers both by asking whether the
#: line is *inside* a dated section rather than whether it is outside one.
_RELEASE_RECORDS: Final = "CHANGELOG.md"

#: A ``.theurian/`` path a document names, at the depth a claim is made about.
#: Deeper paths are corpus documents, which are enumeration source 3's noise
#: rather than its signal, and which nothing here may edit anyway.
_NAMED_PATH: Final = re.compile(r"\.theurian/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?")

_MAX_ROW_TEXT: Final = 150


@dataclass(frozen=True, slots=True)
class WatchedObject:
    """One member of the inventory, with the enumeration that produced it.

    ``kind`` is what decides whether a claim about this object is the #426 defect
    or its *correction*. "Nothing reads this key" is the narrowed sentence #426
    landed and must keep saying; "nothing reads this file" is the universal it
    retracted. Same words, opposite verdicts, and the object is what tells them
    apart -- which is the whole argument for keying on the object.
    """

    name: str
    source: str
    kind: str
    #: The spellings that count as naming this object in prose.
    reference: re.Pattern[str]
    #: The grammatical-object alternation the claim keys substitute.
    grammatical_object: str
    #: Paths whose subject is this object, where a pronoun resolves to it.
    homes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Row:
    """One classified claim: a sentence, the objects it is about, and a verdict.

    One row per sentence, never per object: a sentence whose block names four
    watched objects makes *one* claim, and four rows would be four copies of one
    judgement for a person to keep in step.
    """

    objects: tuple[str, ...]
    shape: str
    verdict: str
    sentence: Sentence


#: A markup delimiter **run**, not a single optional delimiter.
#:
#: **The house style is two backticks.** Measured at ``5d0b1d9``, a commit on
#: #501's branch and not on ``main``: 323 of this repository's 352 governed
#: ``.py`` files write inline code in the RST form ``` ``like this`` ```, because
#: Sphinx-flavoured docstrings are what the package is written in.
#:
#: **The figure is dated, not standing** -- the tree gains ``.py`` files -- so it
#: is derived here once and the prose sites that cite it point at this constant
#: rather than each carrying a count of their own. Numerator then denominator::
#:
#:     git grep -lF '``' -- '*.py' ':!.theurian/' | wc -l
#:     git ls-files -- '*.py' ':!.theurian/' | wc -l
#:
#: The pathspec is :data:`claim_surfaces.EXCLUDED_PREFIXES` spelled for ``git``;
#: ``docs/work-logs/`` holds no ``.py`` file, so excluding it changes neither
#: number. A single optional delimiter therefore could not see a
#: claim written the way most of the tree writes one -- round one planted
#: ``Nothing in ``src/`` reads ``.theurian/config.yaml``.`` in a wheel-shipped
#: module and every audit and every pin stayed green. The escape was in the
#: *markup*, not in the phrasing, which is exactly what an object-keyed census
#: is supposed to be immune to.
#:
#: ``{0,2}`` over one character class admits a mismatched pair (`` `" ``), which
#: over-approximates in the direction that costs a read rather than a miss.
#:
#: **Widening this run is not what closed the markup family, and round two is
#: why.** Any run spelled here is a run some wrapper is outside of: bold defeated
#: this one because ``*`` is not in the class, and adding it would have left the
#: next wrapper. :func:`as_read` removes the emphasis before any key runs, so the
#: composition stops being enumerable rather than being enumerated one delimiter
#: at a time. What this run still does is separate a *spelled* reference from a
#: bare English word, which is why it stays.
_DELIMITER_RUN: Final = r"[`\"'“”]{0,2}"

#: The same run where a delimiter is **required**, for the bare-leaf key surface.
#:
#: ``providers``, ``enabled`` and ``repositories`` are ordinary English words, so
#: an optional delimiter there would make every sentence carrying one a sentence
#: about a schema key. Widening that key from one character to a run must not
#: quietly widen it to zero as well.
_REQUIRED_DELIMITER_RUN: Final = r"[`\"'“”]{1,2}"


def _quoted(path: str) -> str:
    """A path as prose spells it: bare, backticked, RST double-backticked, or quoted."""
    return _DELIMITER_RUN + re.escape(path).replace(r"\.theurian", r"\.?theurian") + _DELIMITER_RUN


#: How a file object is referred to when the sentence uses a pronoun instead of
#: its path. Admitted only inside a block that already names the object, which is
#: how ``ingest.md``'s shipped claim -- *"nothing reads that file today"*, one
#: clause after the path -- was reachable at all. #199 unit B corrected that
#: sentence; this shape stays because the paragraph can be written that way
#: again, and no path-bearing key would see it if it were.
_PRONOUN_FILE: Final = r"(?:it|this|that|the)\s+(?:config(?:uration)?\s+)?file\b"

#: The same for a key object.
_PRONOUN_KEY: Final = r"(?:it|this|that|the)\s+key\b"


def _schema_key_objects(root: Path) -> list[WatchedObject]:
    """Enumeration source 1: every published key description, and the root.

    The root is a member in its own right. It is outside the eleven key blocks
    every earlier count reported, and it is where the false claim #455 records
    actually lives -- so a population that counts key blocks alone re-misses it
    by construction.
    """
    schema = load_json(root, PROJECT_CONFIG_SCHEMA)
    found: list[WatchedObject] = []

    def walk(node: object, dotted: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        if isinstance(node.get("description"), str):
            if dotted:
                # The dotted path, or the leaf inside code markup. A *bare* leaf
                # is deliberately not a spelling: `providers`, `enabled` and
                # `repositories` are ordinary English words, and admitting them
                # made every sentence carrying one refer to a schema key.
                leaf = re.escape(dotted[-1])
                reference = re.compile(
                    rf"{re.escape('.'.join(dotted))}"
                    rf"|{_REQUIRED_DELIMITER_RUN}{leaf}{_REQUIRED_DELIMITER_RUN}",
                    re.IGNORECASE,
                )
            else:
                # The root has no name to spell, so it is reached through its
                # homes alone -- which is exactly why every path-bearing key ever
                # written for this class missed it (#455).
                reference = re.compile(r"(?!x)x")
            found.append(
                WatchedObject(
                    name=".".join(dotted) if dotted else "(schema root)",
                    source="schema key surface (json parse)",
                    kind="key" if dotted else "file",
                    reference=reference,
                    grammatical_object=_PRONOUN_KEY if dotted else _PRONOUN_FILE,
                    homes=() if dotted else CONFIG_HOMES,
                )
            )
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for child, subschema in value.items():
                    walk(subschema, (*dotted, child))

    walk(schema, ())
    return found


def _project_paths_objects() -> list[WatchedObject]:
    """Enumeration source 2: every file ``ProjectPaths`` hands out.

    Read off the class rather than transcribed, so a path the product learns to
    write is in this inventory the day it is added. ``config`` is the member the
    whole #426 class is about; the others are here because the same claim shape
    ("nothing reads the active pointer back") is available about every one of
    them, and an inventory that carried only the interesting member would be a
    list, not an enumeration.
    """
    from theurian.application.project_service import ProjectPaths  # noqa: PLC0415

    root = Path("/PROJECT")
    paths = ProjectPaths(root=root, knowledge_dir=root / ".theurian")
    found: list[WatchedObject] = []
    for name in sorted(dir(type(paths))):
        if name.startswith("_") or not isinstance(getattr(type(paths), name, None), property):
            continue
        relative = getattr(paths, name).relative_to(root).as_posix()
        found.append(
            WatchedObject(
                name=relative,
                source="ProjectPaths file surface (introspection)",
                kind="file",
                reference=re.compile(re.escape(relative), re.IGNORECASE),
                grammatical_object=f"(?:{_quoted(relative)}|{_PRONOUN_FILE})",
                homes=CONFIG_HOMES if relative.endswith("config.yaml") else (),
            )
        )
    return found


def _document_named_objects(root: Path) -> list[WatchedObject]:
    """Enumeration source 3: every ``.theurian/`` path governed prose names."""
    named: set[str] = set()
    for path in governed_paths(root):
        if path.startswith(SELF):
            continue
        text = (root / path).read_text(encoding="utf-8", errors="surrogateescape")
        named.update(match.group(0) for match in _NAMED_PATH.finditer(text))
    return [
        WatchedObject(
            name=relative,
            source="file paths governed docs name (grep)",
            kind="file",
            reference=re.compile(re.escape(relative), re.IGNORECASE),
            grammatical_object=f"(?:{_quoted(relative)}|{_PRONOUN_FILE})",
        )
        for relative in sorted(named)
    ]


def inventory(root: Path) -> tuple[WatchedObject, ...]:
    """The union of the three enumerations, one member per distinct object name.

    The union is what the completeness argument rests on: a claim about an object
    outside it is a claim about an object no governed surface names, no published
    schema key describes, and no ``ProjectPaths`` property hands out.
    """
    merged: dict[str, WatchedObject] = {}
    for member in (
        *_schema_key_objects(root),
        *_project_paths_objects(),
        *_document_named_objects(root),
    ):
        merged.setdefault(member.name, member)
    return tuple(merged.values())


def _keys_for(member: WatchedObject) -> tuple[tuple[str, re.Pattern[str]], ...]:
    substitution = {
        "neg": _NEGATION,
        "live": _LIVENESS,
        "obj": member.grammatical_object,
    }
    return (
        ("named", re.compile(_NEGATED_LIVENESS_TEMPLATE.format(**substitution), re.IGNORECASE)),
        ("pronoun", re.compile(_PRONOUN_TEMPLATE.format(**substitution), re.IGNORECASE)),
        ("unscoped", re.compile(_UNSCOPED_TEMPLATE, re.IGNORECASE)),
    )


def _classify(
    shape: str, kinds: set[str], sentence: Sentence, *, dated: frozenset[int] = frozenset()
) -> str:
    """The verdict, in the order the rules have to be applied.

    Order is load-bearing. The release-note rule runs first because a dated
    CHANGELOG entry quoting a retracted universal is a record whatever tense it
    is in; the key-scoped rule runs last because it is the one a defect can hide
    behind -- a sentence that names a key *and* claims the file is unread is the
    #461 shape exactly, and it stays a suspect as long as one file object is in
    its reference set.

    ``dated`` carries the lines the document's *dated* release sections cover, so
    the first rule clears a sentence only where a release states it. A sentence in
    ``[Unreleased]``, or anywhere in a changelog that has no dated sections at
    all, is not a record of anything: it describes the tree.
    """
    if sentence.path.endswith(_RELEASE_RECORDS) and sentence.line in dated:
        return "record (release note)"
    if _RECORD_MARKERS.search(sentence.text):
        return "record (past tense)"
    if kinds == {"key"}:
        return "record (key-scoped)"
    return "SUSPECT" if shape == "named" else f"SUSPECT ({shape})"


#: A cheap pre-filter: any negated-liveness shape at all, with no object bound.
#:
#: It exists for speed and for nothing else, and it is deliberately *wider* than
#: every per-object key below -- a sentence it rejects cannot match any of them,
#: which is the only property that makes the pre-filter safe. Without it the
#: sweep runs three keys per inventory member over every sentence in the
#: repository, which is tens of millions of searches.
_ANY_CLAIM: Final = re.compile(
    rf"\b{_NEGATION}\b[^.;]{{0,60}}?\b{_LIVENESS}\b"
    rf"|\bno\s+(?:reader|loader|consumer)\b"
    rf"|\b(?:is|are)\s+(?:still\s+)?unread\b"
    rf"|{_UNSCOPED_TEMPLATE}",
    re.IGNORECASE,
)


def _referring(members: tuple[WatchedObject, ...], path: str, block: str) -> list[WatchedObject]:
    """Which watched objects a block is about: the ones it names, **and** its home.

    The two rules are **additive, not ranked**, and round one is why. As a
    fallback the home rule was suppressed by any key name appearing in the block,
    so a file-wide liveness claim written into the wheel-shipped schema *root* --
    a description that mentions ``security.secretScan`` in one clause and denies
    that anything reads the file in the next -- carried only the key object,
    cleared as ``record (key-scoped)``, and shipped with every audit and every pin
    green. That is the #461 shape one surface over: the sentence that names a key
    is not automatically a sentence *about* only that key.

    The cost of adding rather than ranking is the false RED the fallback was
    written to avoid: a genuinely key-scoped sentence inside a home now carries
    the file object too and reaches :func:`_classify` as a suspect. That direction
    is the safe one -- it puts a row in front of a person -- and the schema's
    ``providers.review.repositories`` description is the member that pays it,
    recorded in :data:`SUSPECTS` with its verdict.
    """
    named = [member for member in members if member.reference.search(block)]
    at_home = [
        member
        for member in members
        if path in member.homes and not any(member is other for other in named)
    ]
    return named + at_home


def as_read(sentence: Sentence) -> Sentence:
    """The twin of ``sentence`` every key here is applied to.

    **One normalisation seam for the whole audit**, which is the point: the
    pre-filter, the three claim keys, the reference keys, the record markers and
    the ledger fragments all run against this and none of them against the raw
    text, so a markup form that defeats one cannot defeat only one.

    Today the normalisation is :func:`claim_surfaces.without_emphasis`, and round
    two's R2-B is why. The population and the line numbers are unaffected -- the
    row still reports the raw sentence at the line it opens on, because that is
    what a person opens.
    """
    return Sentence(
        path=sentence.path,
        line=sentence.line,
        text=without_emphasis(sentence.text),
        block=without_emphasis(sentence.block),
    )


def sweep(root: Path) -> list[Row]:
    """Every classified row, in path order.

    A sentence enters the population *because* its block refers to a watched
    object; it stays as one row, carrying every object it refers to. The shape
    recorded is the first that matched, and the verdict reads the whole set.

    Matching runs on :func:`as_read`'s twin; the row keeps the raw sentence, so
    what is printed is what the file says.
    """
    members = inventory(root)
    keys = {member.name: _keys_for(member) for member in members}
    rows: list[Row] = []
    for path in governed_paths(root):
        if path.startswith(SELF):
            continue
        dated = (
            dated_lines((root / path).read_text(encoding="utf-8", errors="surrogateescape"))
            if path.endswith(_RELEASE_RECORDS)
            else frozenset()
        )
        for sentence in sentences(root, path):
            read = as_read(sentence)
            if not _ANY_CLAIM.search(read.text):
                continue
            matched: list[tuple[WatchedObject, str]] = []
            for member in _referring(members, path, read.block):
                for shape, key in keys[member.name]:
                    if key.search(read.text):
                        matched.append((member, shape))
                        break
            if not matched:
                continue
            rows.append(
                Row(
                    objects=tuple(sorted({member.name for member, _ in matched})),
                    shape=matched[0][1],
                    verdict=_classify(
                        matched[0][1],
                        {member.kind for member, _ in matched},
                        read,
                        dated=dated,
                    ),
                    sentence=sentence,
                )
            )
    return rows


#: Every suspect the sweep produces, with the verdict a person reached for it, as
#: ``(path, a fragment that identifies the sentence, verdict, why)``.
#:
#: **Keyed on a fragment rather than on a line number**, because a line number is
#: invalidated by any edit above it and a ledger that goes RED on unrelated
#: reflowing is a ledger somebody deletes.
#:
#: **Reconciled in three directions.** A suspect no row covers is a finding: a
#: live file-wide liveness claim nobody has judged. A row the sweep no longer
#: produces means the sentence was fixed, moved or reworded, and the row has to be
#: discharged in the same change -- which is what makes the fix and the record
#: land together instead of the record rotting behind the fix. And a row covering
#: two suspects is a judgement about one sentence absorbing another, which the
#: first two directions cannot see: the fragment is a substring test, so a second
#: live claim containing a recorded fragment reads as recorded (round two's R2-A).
#: Every fragment below is therefore chosen to identify exactly one sentence, and
#: :data:`LEDGER_CONTROLS`' last row is what holds that.
SUSPECTS: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "docs/adr/0027-accept-validates-before-it-moves.md",
        "Nothing reads it today",
        "DEFECT, outside this unit's fix set",
        "ADR-0027's own decision 3 shipped the reader this sentence denies. The paragraph "
        "was written while the diff was in flight and reads as the present tense now that "
        "it has landed -- the prose-written-at-RED-time family. Recorded for filing; the "
        "ADR is not in #199 unit B's measured file set.",
    ),
    (
        "packages/theurian-core/src/theurian/application/forest_builder.py",
        "It has no config surface",
        "true",
        "The claim's subject is `SUMMARY_MAX_TOKENS`, not `.theurian/config.yaml`: the "
        "constant has no published key, which the next sentence says in full. This is the "
        "measured escape `test_raptor_config_claims.py:74` records -- a phrasing that "
        "names no verb and no object, so it is in this sweep as its own shape and is "
        "cleared by hand rather than by a pattern.",
    ),
    (
        "packages/theurian-core/tests/unit/test_config_key_call_sites.py",
        "Nothing reads it, its description says so",
        "true",
        "`it` is `providers.review.repositories`, named in the sentence before. A "
        "key-scoped claim, and the correct one.",
    ),
    (
        "packages/theurian-core/tests/unit/test_config_key_call_sites.py",
        "those documents say so",
        "true",
        "The same key-scoped claim inside the pin's own failure message, explaining why "
        "six documents describe `providers.review.repositories` as reserved. `it` is that "
        "key, named in the clause before. It reached the ledger when `_RECORD_MARKERS` "
        "stopped clearing a sentence for carrying the bare verb `read` (round one's M-i).",
    ),
    # `plugins/claude-code/CHANGELOG.md`'s "nothing reads that file, so the
    # allowlist protects no one yet" stood here as a `DEFECT` until #199 unit B's
    # prose assignment corrected it. It was the first row this ledger ever
    # carried from a CHANGELOG: the blanket release-note clear covered
    # `[Unreleased]` until round one's M-j scoped it to dated sections, and a
    # `[Unreleased]` section is live prose. The entry now narrows the negation to
    # `providers.review.repositories` and says the file *is* read for one key --
    # the #461 treatment one surface over -- so the sweep produces no row for it
    # and the ledger carries none. A file-wide claim returning to that entry is
    # an unrecorded suspect again, which is the direction that has to stay RED.
    # The rows below arrived with two round-one fixes and are the evidence that
    # both reach the tree rather than only the controls. The delimiter run (H-F)
    # brought in every quotation written in the RST house style, which a single
    # optional delimiter could not see; the narrowed `_RECORD_MARKERS` (M-i)
    # brought in the ones a bare `read` or an `until` had been clearing. None is a
    # claim: every one quotes the retracted universal inside a docstring whose
    # subject is that it was retracted.
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "followed by the reader the file",
        "quotation",
        "The module docstring's bullet, quoting what `raptor.md` says now in order to "
        "describe the narrowing. `_RECORD_MARKERS` used to clear it for carrying `read`.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "they are the verb of the claim itself",
        "quotation",
        "`_RETRACTION_LEAD`'s docstring, quoting the double-quoted probe to explain why "
        "`read`/`reads` are excluded from that key.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "a few words earlier, about",
        "quotation",
        "`_FILE_UNREAD`'s docstring, quoting the *other* half of the corrected rationale "
        "-- the key-scoped 'nothing in `src/` reads it' about `raptor.enabled` -- to say "
        "why the verb has to sit against its object.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "Round two's third probe wrote the path in double quotes",
        "quotation",
        "The same docstring, quoting round two's probe to record why the delimiter class "
        "is not a bare backtick.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "Every one of them recorded that",
        "transcription",
        "The module docstring's opening: what the governed records used to say, quoted in "
        "the sentence that says it stopped being true with ADR-0027.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "still says",
        "transcription",
        "The served corpus's snapshot, quoted in the paragraph that explains why the scan "
        "is scoped to a named file set: the snapshot holds the retracted wording "
        "byte-identically by design (#199 unit C), and only a governed re-seed moves it.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        'ADR-0008 decision 10\'s rationale, its "switch is the CLI flag" note',
        "transcription",
        "`_FILE_UNREAD`'s docstring quotes the two shapes the retracted universal took, "
        "which is how the pattern's own scope is stated.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "``raptor.md``'s section heading sentence",
        "transcription",
        "The second of the same two shapes.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "so no value in it takes effect today",
        "transcription",
        "`UNIVERSAL_CASES` carries #455's member as a *negative* case -- the row that "
        "makes it a test result rather than a sentence that `_FILE_UNREAD` is blind to "
        "the schema root.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "a plain live reassertion (round-one A7)",
        "transcription",
        "`NOTE_EXCLUSION_CASES` transcribes each shape a live reassertion inside a "
        "correction note can take; every one is the universal by construction.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        'found = [("0008-raptor-forest.md", "nothing in `src/` reads',
        "transcription",
        "A ledger-drift test's fixture: the sentence is the input the test feeds its own "
        "reporting path.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "so an operator cannot yet move it",
        "transcription",
        "The same, for the row-absorbed case.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "so `SELECT *` ends",
        "transcription",
        "The unbalanced-backtick probe (E1), quoted in the docstring that states what it measures.",
    ),
    (
        "packages/theurian-core/tests/unit/test_raptor_config_claims.py",
        "only the watched claim where the pronoun has one possible referent",
        "quotation",
        "`_THIS_FILE_UNREAD`'s own test docstring, quoting the shape that pattern matches "
        "in order to say which two surfaces it may run on. **This row is what the "
        "emphasis strip added** (round two's R2-B): the quotation writes the pronoun as "
        "`**this file**`, and until `as_read` ran, the two asterisks sat between the verb "
        "and its object and no key here could see it. It is a quotation inside a test "
        "module about the pattern, not a claim about the tree.",
    ),
    # `plugins/claude-code/commands/ingest.md`'s "nothing reads that file today"
    # stood here as `DEFECT, #461` until #199 unit B's prose assignment corrected
    # it. The sentence now names the one reader and narrows the negation to
    # `providers.review.repositories`, so the sweep produces no row for it and
    # the ledger carries none: a file-wide claim returning to that paragraph is
    # an unrecorded suspect again, which is the direction that has to stay RED.
    (
        "schemas/config/project-config.schema.json",
        "Nothing reads it today; owed with",
        "true",
        "`providers.review.repositories`' own description, and the claim is key-scoped "
        "and correct. It is a suspect only because the JSON reader's block is one source "
        "line, so the enclosing key name is not in the block and the home rule supplies "
        "the file object instead -- a limitation whose direction is a false RED, never a "
        "false clear. Its owner cite was repointed #129 -> #429 in the same commit that "
        "rewrote the root description, because class 2's open-owner rule is what "
        "`controls_discharge.py` reported dead here.",
    ),
)

#: Planted sentences, one per shape, run instead of the tree under
#: ``--positive-control``.
#:
#: **A zero from a sweep is only readable after the key has hit something.** A
#: pattern that matches nothing reports exactly what a clean tree reports, and
#: this repository has already shipped one sweep whose key had stopped matching.
#: The two members this repository actually published are here as controls too,
#: because a control that only ever runs against synthetic text does not show
#: that the key reaches a real surface. **Both have since been corrected on this
#: branch** -- the schema root by #455, the plugin claim by #461 -- so neither
#: sentence is in the tree any more and what these two rows demonstrate is
#: narrower than it was: the key still reaches the exact wording those two
#: surfaces shipped, which is the wording a regression would restore. They are
#: kept in their published form for that reason and are not re-tensed into the
#: corrected text, which the key is not supposed to match.
#:
#: **The ``expected=False`` rows are the other edge, and round two's
#: normalisation is why they matter more than they did.** :func:`as_read` widens
#: what the keys can see, and a matcher measured only by what it catches drifts
#: towards catching everything: the narrowed #426 sentence, the past-tense
#: record, the key-scoped claim inside a home and -- since R2-B -- ADR-0028's
#: house style, a bold-wrapped ``.theurian/`` path in a sentence whose negation is
#: about something else entirely. That last row is not vacuous: :data:`_ANY_CLAIM`
#: fires on it and the block names a watched object, so it reaches the per-object
#: keys and is declined there rather than filtered out before they run.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, str, bool, str], ...]] = (
    (
        "the schema root as it shipped before #455 (corrected)",
        "schemas/config/project-config.schema.json",
        "Nothing in src/ reads this file, so no value in it takes effect today.",
        True,
        "none",
    ),
    (
        "the plugin claim as it shipped before #461 (corrected)",
        "plugins/claude-code/commands/ingest.md",
        "A repository will have to be on the allowlist in `.theurian/config.yaml`; "
        "nothing reads that file today.",
        True,
        "none",
    ),
    (
        "the bare pronoun, which no path-bearing key reaches",
        "docs/architecture/raptor.md",
        "`.theurian/config.yaml` is the file. Nothing reads it today.",
        True,
        "none",
    ),
    (
        "no config surface -- the measured escape of the phrasing-keyed scan",
        "docs/architecture/raptor.md",
        "The summariser's budget lives in `.theurian/config.yaml` terms. It has no config surface.",
        True,
        "none",
    ),
    (
        "the narrowed sentence #426 landed, which must NOT be a suspect",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads `raptor.enabled`, nor any other key in the `raptor` block.",
        False,
        "none",
    ),
    (
        "a past-tense record, which must NOT be a suspect",
        "docs/architecture/raptor.md",
        "Two sentences said nothing in `src/` reads `.theurian/config.yaml`. Each was "
        "true when written.",
        False,
        "none",
    ),
    (
        "round one's H-B: the wheel-shipped schema root naming a key while denying the "
        "file has a reader at all",
        PROJECT_CONFIG_SCHEMA,
        "Per-repository configuration. `security.secretScan` is published here, and "
        "nothing reads it today.",
        True,
        "none",
    ),
    (
        "round one's H-F: the same claim in this repository's RST house style, in a "
        "wheel-shipped module",
        "packages/theurian-core/src/theurian/application/forest_builder.py",
        "Nothing in ``src/`` reads ``.theurian/config.yaml``, so no default here is in force.",
        True,
        "none",
    ),
    (
        "the RST form of the *narrowed* sentence, which must still NOT be a suspect",
        "docs/architecture/raptor.md",
        "Nothing in ``src/`` reads ``raptor.enabled``, nor any other key in the ``raptor`` block.",
        False,
        "none",
    ),
    (
        "round one's M-i: a live universal with a future clause, which `until` cleared",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads `.theurian/config.yaml` until review ingestion lands.",
        True,
        "none",
    ),
    (
        "round one's M-i: the same universal inside a modal, which the bare verb `read` "
        "cleared as a past tense",
        "docs/architecture/raptor.md",
        "Nothing in `src/` can read `.theurian/config.yaml`, so the default is safe to flip.",
        True,
        "none",
    ),
    (
        "the past tense the corrected rationale actually uses, which must still be cleared",
        "docs/architecture/raptor.md",
        "When this was written nothing in `src/` read `.theurian/config.yaml` at all.",
        False,
        "none",
    ),
    (
        "round one's M-j: a live universal in a CHANGELOG's `[Unreleased]` section",
        "plugins/claude-code/CHANGELOG.md",
        "Nothing in `src/` reads `.theurian/config.yaml`, so the allowlist is not in force.",
        True,
        "unreleased",
    ),
    (
        "the same sentence in a dated release section, which stays a record",
        "plugins/claude-code/CHANGELOG.md",
        "Nothing in `src/` reads `.theurian/config.yaml`, so the allowlist is not in force.",
        False,
        "dated",
    ),
    (
        "round two's R2-j: the same sentence in a changelog with no dated sections at all, "
        "which is the root `CHANGELOG.md` and which the outside-`[Unreleased]` rule cleared whole",
        "CHANGELOG.md",
        "Nothing in `src/` reads `.theurian/config.yaml`, so the allowlist is not in force.",
        True,
        "none",
    ),
    (
        "the narrowed key-scoped sentence in a home, which the additive rule must still let past",
        "examples/sample-project/.theurian/config.yaml",
        "This file is read -- `security/project_config.py` opens it for "
        "`security.secretScan` alone -- but nothing in `src/` reads "
        "`providers.review.repositories`, so the allowlist is not in force.",
        False,
        "none",
    ),
    (
        "round two's R2-B: the same claim with the path wrapped in bold, in a wheel-shipped "
        "module outside the pin's seven surfaces",
        "packages/theurian-core/src/theurian/security/project_config.py",
        "Nothing in ``src/`` reads **`.theurian/config.yaml`**, so no default here is in force.",
        True,
        "none",
    ),
    (
        "the same claim italicised on its verb, the emphasis form that is not bold",
        "docs/architecture/raptor.md",
        "Nothing in `src/` *reads* `.theurian/config.yaml`, so the default is safe to flip.",
        True,
        "none",
    ),
    (
        "the house style the strip must NOT turn into a claim: a bold-wrapped path in a "
        "sentence whose negation is about something else (ADR-0028's shape)",
        "docs/adr/0028-a-local-proposal-is-a-different-directory.md",
        "**`.theurian/proposals-local/<proposal-id>/`** is a different directory, and "
        "nothing in `src/` reads a draft the author has not published.",
        False,
        "none",
    ),
)

#: The escape space, as a table that **runs** rather than a sentence that rots, as
#: ``(what the escape is, path, the planted sentence, does a key reach it today)``.
#:
#: A census that states its bound in prose states it once and then drifts: the pin
#: side learned this in #199 unit B, where a docstring's list of escapes described
#: a scan that no longer existed, and turned the list into
#: ``test_raptor_config_claims.py``'s ``MEASURED_ESCAPE_CASES``. This is the same
#: instrument on the census side. A row whose ``reached`` flag stops being true is
#: a FAIL either way round: an escape that closed is news the record has to carry,
#: and an escape that opened is a key that moved.
#:
#: **Every row here is a way of writing a reference, not a way of phrasing a
#: claim** -- which is precisely the dimension the module's opening theorem does
#: not bound, and the reason it now carries a qualifier.
#:
#: **What the last two would cost to close, measured rather than guessed.**
#: Adding ``[`` and ``]`` to :data:`_DELIMITER_RUN`'s class and taking its bound
#: from ``{0,2}`` to ``{0,3}`` reaches both. Measured at ``f7eac97``: that variant
#: takes both rows to ``SUSPECT`` and leaves the tree exactly where it is -- 55
#: rows, 19 suspects, no drift in any ledger direction -- with the passive-voice
#: row and the ADR-0028 negative both still clear. It is not taken here because it
#: is a *second* mechanism in a round whose closure argument is the first one, and
#: a delimiter class widened to reach two more spellings is the enumeration this
#: module exists to stop doing. Recorded so the next change decides it on a
#: measurement rather than rediscovering it.
MEASURED_ESCAPES: Final[tuple[tuple[str, str, str, bool], ...]] = (
    (
        "adv-L3: passive voice -- the object first, the negation as the agent",
        "docs/architecture/raptor.md",
        "`.theurian/config.yaml` is read by nothing in `src/`, so the default is safe to flip.",
        False,
    ),
    (
        "the path as a Markdown link, whose `[` the delimiter run does not spell",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads [`.theurian/config.yaml`](../../.theurian/config.yaml), "
        "so the default is safe to flip.",
        False,
    ),
    (
        "the path in a run of three backticks, one more than the delimiter run admits",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads ```.theurian/config.yaml```, so the default is safe to flip.",
        False,
    ),
)


def _covers(entry: tuple[str, str, str, str], row: Row) -> bool:
    """Whether one ledger entry is the record of this suspect.

    **Case-insensitive on the fragment**, which is round one's H-C on this
    audit. A fragment is a slice of a sentence, and a sentence moved to the head
    of a rewritten paragraph is recapitalised: keyed case-sensitively, the same
    claim then reads as a new unrecorded suspect *and* leaves its own row stale --
    two findings for one edit, neither of them real. The sibling ledgers here take
    the same rule.

    **Emphasis-insensitive for the same reason**, and it is the same rule
    :func:`as_read` applies to the keys: italicising one word of a recorded
    sentence is a typographic edit, and it must not read as a new claim plus a
    stale row. Both sides are normalised, so a fragment transcribed *with* its
    markup still matches.

    This is the one place the question "is this row recorded" is answered.
    :func:`main`'s own display lookup used to re-implement it case-sensitively
    and without the path-and-fragment pair the ledger uses, so a recorded suspect
    could print as ``UNRECORDED`` while the reconciliation below said it was
    covered (round two's code-L2/L3).
    """
    return row.sentence.path == entry[0] and _fragment_key(entry[1]) in _fragment_key(
        row.sentence.text
    )


def _fragment_key(text: str) -> str:
    """The form both sides of a ledger fragment comparison are reduced to."""
    return without_emphasis(text).lower()


def ledger_drift(
    rows: list[Row], ledger: tuple[tuple[str, str, str, str], ...]
) -> tuple[
    list[Row],
    list[tuple[str, str, str, str]],
    list[tuple[tuple[str, str, str, str], list[str]]],
]:
    """``(unrecorded, stale, ambiguous)`` for one produced set against one ledger.

    *Unrecorded* is a suspect no ledger row covers; *stale* is a ledger row the
    sweep no longer produces; *ambiguous* is the third direction, and it is the
    one a substring key needs. :func:`_covers` tests containment and counts
    nothing, so a **second** live suspect whose sentence contains a recorded
    fragment is absorbed by the row written for the first: the sweep produces two
    suspects, the ledger carries one judgement, and both of the other directions
    stay silent. Round two's R2-A, converged on by three reviewers, and the
    direction ``controls_discharge.py`` already carried while this module and
    ``owner_position_cites.py`` did not.

    The ledger is a parameter so all three directions can be **driven** from
    planted input rather than only observed on a tree where none fires --
    :data:`LEDGER_CONTROLS`, and round one's code-M6 across all five audits here.
    """
    suspects = [row for row in rows if row.verdict.startswith("SUSPECT")]
    unrecorded = [row for row in suspects if not any(_covers(entry, row) for entry in ledger)]
    stale = [entry for entry in ledger if not any(_covers(entry, row) for row in suspects)]
    ambiguous = [
        (entry, covered)
        for entry in ledger
        if len(covered := [str(row.sentence) for row in suspects if _covers(entry, row)]) > 1
    ]
    return unrecorded, stale, ambiguous


def _ledger_drift(
    rows: list[Row],
) -> tuple[
    list[Row],
    list[tuple[str, str, str, str]],
    list[tuple[tuple[str, str, str, str], list[str]]],
]:
    return ledger_drift(rows, SUSPECTS)


#: What the ledger reconciliation must do, driven from synthetic rows, as
#: ``(what it demonstrates, the produced rows, the ledger, unrecorded, stale,
#: ambiguous)``.
#:
#: Round one's code-M6: every ledger here claimed exactness in both directions and
#: no control ran either one. The fifth row is the case that made the claim false
#: on this audit -- a fragment keyed case-sensitively turns one recapitalisation
#: into two findings, neither of them real.
#:
#: **The last row is round two's R2-A**, and it drives the direction this ledger
#: did not have: a second live suspect whose sentence *contains* the recorded
#: fragment. Both other directions read it as recorded, which is the shape the
#: shipped rows deliberately avoid -- every fragment in :data:`SUSPECTS` is
#: distinctive enough that no sibling sentence contains it, so nothing in the tree
#: exercises the absorption and only a plant can.
LEDGER_CONTROLS: Final[
    tuple[
        tuple[
            str,
            tuple[tuple[str, str, str], ...],
            tuple[tuple[str, str, str, str], ...],
            int,
            int,
            int,
        ],
        ...,
    ]
] = (
    (
        "a suspect its ledger row covers: no drift in any direction",
        (("a.md", "SUSPECT", "nothing reads it today, and nothing will"),),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        0,
        0,
    ),
    (
        "a suspect with no ledger row -- the unrecorded direction",
        (("a.md", "SUSPECT", "nothing reads it today, and nothing will"),),
        (),
        1,
        0,
        0,
    ),
    (
        "a ledger row the sweep no longer produces -- the stale direction",
        (),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        1,
        0,
    ),
    (
        "a cleared row, which is not a suspect and must leave its ledger row stale",
        (("a.md", "record (past tense)", "nothing reads it today, and nothing will"),),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        1,
        0,
    ),
    (
        "the same sentence recapitalised at the head of a rewritten paragraph, which "
        "a case-sensitive fragment reported as both a new suspect and a stale row",
        (("a.md", "SUSPECT", "Nothing reads it today, and nothing will"),),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        0,
        0,
    ),
    (
        "a second live suspect whose sentence contains the recorded fragment -- the "
        "absorption a substring key has and only a cardinality check sees",
        (
            ("a.md", "SUSPECT", "nothing reads it today, and nothing will"),
            ("a.md", "SUSPECT", "A second residue: nothing reads it today either."),
        ),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        0,
        1,
    ),
)


def _run_ledger_controls() -> int:
    """Drive all three reconciliation directions from planted rows and planted ledgers."""
    failures = 0
    print("\n=== LEDGER CONTROLS (the reconciliation, driven) ===")
    for label, produced, ledger, want_new, want_stale, want_ambiguous in LEDGER_CONTROLS:
        rows = [
            Row(
                objects=(".theurian/config.yaml",),
                shape="named",
                verdict=verdict,
                sentence=Sentence(path=path, line=number, text=text, block=text),
            )
            for number, (path, verdict, text) in enumerate(produced)
        ]
        unrecorded, stale, ambiguous = ledger_drift(rows, ledger)
        got = (len(unrecorded), len(stale), len(ambiguous))
        want = (want_new, want_stale, want_ambiguous)
        status = "OK  " if got == want else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: (unrecorded, stale, ambiguous)={got}, expected {want}")
    return 1 if failures else 0


def _verdict_for_planted(
    members: tuple[WatchedObject, ...],
    keys: dict[str, tuple[tuple[str, re.Pattern[str]], ...]],
    planted: Sentence,
    dated: frozenset[int] = frozenset(),
) -> str:
    """One planted sentence classified through exactly the seam :func:`sweep` uses.

    :func:`as_read` runs here too, and that is not a detail: a control that
    matched the raw plant would report a key reaching a sentence the sweep never
    hands it, which is the failure mode a positive control exists to rule out.
    """
    read = as_read(planted)
    hit: str | None = None
    kinds: set[str] = set()
    for member in _referring(members, planted.path, read.block):
        for shape, key in keys[member.name]:
            if key.search(read.text):
                hit = hit or shape
                kinds.add(member.kind)
                break
    if hit is None:
        return "no match"
    return _classify(hit, kinds, read, dated=dated)


def _run_positive_controls() -> int:
    """Show each shape's key hitting a planted sentence before any zero is read."""
    root = repo_root()
    members = inventory(root)
    keys = {member.name: _keys_for(member) for member in members}
    failures = 0
    print("=== POSITIVE CONTROLS ===")
    for label, path, planted, expected, section in POSITIVE_CONTROLS:
        # The section membership is *computed* by the rule under test over a
        # synthetic document, never asserted here -- round two's R2-g.
        lines, line = planted_changelog(planted, section=section)
        verdict = _verdict_for_planted(
            members,
            keys,
            Sentence(path=path, line=line, text=planted, block=planted),
            lines,
        )
        found = verdict.startswith("SUSPECT")
        status = "OK  " if found is expected else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}")
        print(f"        expected suspect={expected}  got {verdict!r}")
    return (1 if failures else 0) | _run_escape_controls(members, keys) | _run_ledger_controls()


def _run_escape_controls(
    members: tuple[WatchedObject, ...],
    keys: dict[str, tuple[tuple[str, re.Pattern[str]], ...]],
) -> int:
    """Run the recorded escape space, so the bound fails instead of rotting."""
    failures = 0
    print("\n=== MEASURED ESCAPES (the bound, run) ===")
    for label, path, planted, reached in MEASURED_ESCAPES:
        verdict = _verdict_for_planted(
            members, keys, Sentence(path=path, line=0, text=planted, block=planted)
        )
        found = verdict.startswith("SUSPECT")
        status = "OK  " if found is reached else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: recorded reached={reached}, got {verdict!r}")
    return 1 if failures else 0


def _report_drift(rows: list[Row]) -> int:
    """Reconcile the produced suspects against the ledger, in three directions.

    *Unrecorded* is a suspect nobody judged; *stale* is a ledger row the sweep no
    longer produces; *ambiguous* is one recorded judgement covering two suspects,
    which is what a substring fragment can do and neither of the others can see.
    """
    unrecorded, stale, ambiguous = _ledger_drift(rows)
    if unrecorded:
        print("\nUNRECORDED SUSPECTS -- a liveness claim about a watched object nobody judged:")
        for row in unrecorded:
            print(f"  {row.sentence}  {row.sentence.text[:_MAX_ROW_TEXT]}")
    if stale:
        print("\nSTALE LEDGER ROWS -- the sweep no longer produces these:")
        for path, fragment, verdict, _ in stale:
            print(f"  {path}  [{verdict}]  {fragment!r}")
        print(
            "\n  This is the good direction: a sentence was fixed, moved or reworded. "
            "Delete the row in the same commit as the change that discharged it."
        )
    if ambiguous:
        print("\nAMBIGUOUS LEDGER ROWS -- one judgement covering more than one suspect:")
        for (path, fragment, _, _), covered in ambiguous:
            print(f"  {path}  {fragment!r}  covers {covered}")
        print(
            "\n  A person judged one sentence and the fragment absorbs another. Narrow the\n"
            "  fragment until it identifies one, and judge the rest."
        )
    return 1 if unrecorded or stale or ambiguous else 0


def main(argv: list[str]) -> int:
    if "--positive-control" in argv:
        return _run_positive_controls()

    root = repo_root()
    members = inventory(root)
    rows = sweep(root)

    print("=== INVENTORY (the completeness argument's base) ===")
    per_source: dict[str, int] = {}
    for member in members:
        per_source[member.source] = per_source.get(member.source, 0) + 1
    for source, count in sorted(per_source.items()):
        print(f"  {count:4}  {source}")
    print(f"  {len(members):4}  union, distinct object names")

    print("\n=== CLASSIFICATION ===")
    per_verdict: dict[str, int] = {}
    for row in rows:
        per_verdict[row.verdict] = per_verdict.get(row.verdict, 0) + 1
    for verdict, count in sorted(per_verdict.items()):
        print(f"  {count:4}  {verdict}")
    print(f"  {len(rows):4}  rows")

    print("\n=== SUSPECTS ===")
    for row in rows:
        if not row.verdict.startswith("SUSPECT"):
            continue
        recorded = next((entry for entry in SUSPECTS if _covers(entry, row)), None)
        print(f"  {row.sentence}  [{row.shape}]  {recorded[2] if recorded else 'UNRECORDED'}")
        print(f"      {row.sentence.text[:_MAX_ROW_TEXT]}")

    return _report_drift(rows)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
