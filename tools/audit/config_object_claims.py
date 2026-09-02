"""Class 1: liveness claims about a watched object, keyed on the object (#199 unit B).

**Why the key is object-shaped.** Subject-keyed sweeps for the "nothing in
``src/`` reads ``.theurian/config.yaml``" class missed a live member in five
consecutive passes -- #426's five faces, #447's three, #455's schema root (which
even #455's own corrected key missed), and #461's plugin face, which drops
``src/`` from the sentence entirely and names the file by pronoun. The theorem
those five misses demonstrate, and the rule this module is built on:
**you cannot enumerate the phrasings of a claim; you can enumerate the
references to a bounded object.**

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

**Every row is classified, and the classification is exact in both directions.**
The machine clears the rows it can defend -- a *dated* CHANGELOG entry, a
past-tense sentence, a key-scoped claim -- and everything left is a *suspect*
that a person
verified and recorded in :data:`SUSPECTS` with a verdict. A suspect the ledger
does not carry is a finding; a ledger row the sweep no longer produces means
somebody fixed or moved a sentence and the ledger has to be discharged in the
same change. Both are exit status 1.

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
    governed_paths,
    load_json,
    repo_root,
    sentences,
    unreleased_lines,
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
#: it is edited on every merge, and it was cleared unread.
#: :func:`claim_surfaces.unreleased_lines` is what separates the two.
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
#: **The house style is two backticks.** Measured at ``5d0b1d9``: 323 of this
#: repository's 352 governed ``.py`` files write inline code in the RST form
#: ``` ``like this`` ```, because Sphinx-flavoured docstrings are what the
#: package is written in. A single optional delimiter therefore could not see a
#: claim written the way most of the tree writes one -- round one planted
#: ``Nothing in ``src/`` reads ``.theurian/config.yaml``.`` in a wheel-shipped
#: module and every audit and every pin stayed green. The escape was in the
#: *markup*, not in the phrasing, which is exactly what an object-keyed census
#: is supposed to be immune to.
#:
#: ``{0,2}`` over one character class admits a mismatched pair (`` `" ``), which
#: over-approximates in the direction that costs a read rather than a miss.
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
    shape: str, kinds: set[str], sentence: Sentence, *, unreleased: frozenset[int] = frozenset()
) -> str:
    """The verdict, in the order the rules have to be applied.

    Order is load-bearing. The release-note rule runs first because a dated
    CHANGELOG entry quoting a retracted universal is a record whatever tense it
    is in; the key-scoped rule runs last because it is the one a defect can hide
    behind -- a sentence that names a key *and* claims the file is unread is the
    #461 shape exactly, and it stays a suspect as long as one file object is in
    its reference set.

    ``unreleased`` carries the lines of the document's ``[Unreleased]`` section,
    so the first rule can decline to clear them. A sentence there is not a record
    of anything: it describes the tree.
    """
    if sentence.path.endswith(_RELEASE_RECORDS) and sentence.line not in unreleased:
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


def sweep(root: Path) -> list[Row]:
    """Every classified row, in path order.

    A sentence enters the population *because* its block refers to a watched
    object; it stays as one row, carrying every object it refers to. The shape
    recorded is the first that matched, and the verdict reads the whole set.
    """
    members = inventory(root)
    keys = {member.name: _keys_for(member) for member in members}
    rows: list[Row] = []
    for path in governed_paths(root):
        if path.startswith(SELF):
            continue
        unreleased = (
            unreleased_lines((root / path).read_text(encoding="utf-8", errors="surrogateescape"))
            if path.endswith(_RELEASE_RECORDS)
            else frozenset()
        )
        for sentence in sentences(root, path):
            if not _ANY_CLAIM.search(sentence.text):
                continue
            matched: list[tuple[WatchedObject, str]] = []
            for member in _referring(members, path, sentence.block):
                for shape, key in keys[member.name]:
                    if key.search(sentence.text):
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
                        sentence,
                        unreleased=unreleased,
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
#: **Exact in both directions.** A suspect no row covers is a finding: a live
#: file-wide liveness claim nobody has judged. A row the sweep no longer produces
#: means the sentence was fixed, moved or reworded, and the row has to be
#: discharged in the same change -- which is what makes the fix and the record
#: land together instead of the record rotting behind the fix.
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
    (
        "plugins/claude-code/CHANGELOG.md",
        "so the allowlist protects no one yet",
        "DEFECT -- file, outside this assignment's file set",
        "The retracted universal, live, in the plugin CHANGELOG's `[Unreleased]` section: "
        "'nothing reads that file, so the allowlist protects no one yet'. ADR-0027 "
        "decision 3 shipped the reader -- `security/project_config.py` opens the file for "
        "`security.secretScan` -- so the sentence should name the key rather than the "
        "file, exactly as #461 did for `ingest.md`. It is the first row this ledger has "
        "ever carried from a CHANGELOG, because the blanket release-note clear covered "
        "`[Unreleased]` until round one's M-j scoped it to dated sections. The census "
        "assignment may not edit `plugins/` or a CHANGELOG, so it is recorded here for "
        "the prose pass rather than corrected.",
    ),
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
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, str, bool, bool], ...]] = (
    (
        "the schema root as it shipped before #455 (corrected)",
        "schemas/config/project-config.schema.json",
        "Nothing in src/ reads this file, so no value in it takes effect today.",
        True,
        False,
    ),
    (
        "the plugin claim as it shipped before #461 (corrected)",
        "plugins/claude-code/commands/ingest.md",
        "A repository will have to be on the allowlist in `.theurian/config.yaml`; "
        "nothing reads that file today.",
        True,
        False,
    ),
    (
        "the bare pronoun, which no path-bearing key reaches",
        "docs/architecture/raptor.md",
        "`.theurian/config.yaml` is the file. Nothing reads it today.",
        True,
        False,
    ),
    (
        "no config surface -- the measured escape of the phrasing-keyed scan",
        "docs/architecture/raptor.md",
        "The summariser's budget lives in `.theurian/config.yaml` terms. It has no config surface.",
        True,
        False,
    ),
    (
        "the narrowed sentence #426 landed, which must NOT be a suspect",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads `raptor.enabled`, nor any other key in the `raptor` block.",
        False,
        False,
    ),
    (
        "a past-tense record, which must NOT be a suspect",
        "docs/architecture/raptor.md",
        "Two sentences said nothing in `src/` reads `.theurian/config.yaml`. Each was "
        "true when written.",
        False,
        False,
    ),
    (
        "round one's H-B: the wheel-shipped schema root naming a key while denying the "
        "file has a reader at all",
        PROJECT_CONFIG_SCHEMA,
        "Per-repository configuration. `security.secretScan` is published here, and "
        "nothing reads it today.",
        True,
        False,
    ),
    (
        "round one's H-F: the same claim in this repository's RST house style, in a "
        "wheel-shipped module",
        "packages/theurian-core/src/theurian/application/forest_builder.py",
        "Nothing in ``src/`` reads ``.theurian/config.yaml``, so no default here is in force.",
        True,
        False,
    ),
    (
        "the RST form of the *narrowed* sentence, which must still NOT be a suspect",
        "docs/architecture/raptor.md",
        "Nothing in ``src/`` reads ``raptor.enabled``, nor any other key in the ``raptor`` block.",
        False,
        False,
    ),
    (
        "round one's M-i: a live universal with a future clause, which `until` cleared",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads `.theurian/config.yaml` until review ingestion lands.",
        True,
        False,
    ),
    (
        "round one's M-i: the same universal inside a modal, which the bare verb `read` "
        "cleared as a past tense",
        "docs/architecture/raptor.md",
        "Nothing in `src/` can read `.theurian/config.yaml`, so the default is safe to flip.",
        True,
        False,
    ),
    (
        "the past tense the corrected rationale actually uses, which must still be cleared",
        "docs/architecture/raptor.md",
        "When this was written nothing in `src/` read `.theurian/config.yaml` at all.",
        False,
        False,
    ),
    (
        "round one's M-j: a live universal in a CHANGELOG's `[Unreleased]` section",
        "plugins/claude-code/CHANGELOG.md",
        "Nothing in `src/` reads `.theurian/config.yaml`, so the allowlist is not in force.",
        True,
        True,
    ),
    (
        "the same sentence in a dated release section, which stays a record",
        "plugins/claude-code/CHANGELOG.md",
        "Nothing in `src/` reads `.theurian/config.yaml`, so the allowlist is not in force.",
        False,
        False,
    ),
    (
        "the narrowed key-scoped sentence in a home, which the additive rule must still let past",
        "examples/sample-project/.theurian/config.yaml",
        "This file is read -- `security/project_config.py` opens it for "
        "`security.secretScan` alone -- but nothing in `src/` reads "
        "`providers.review.repositories`, so the allowlist is not in force.",
        False,
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
    """
    return row.sentence.path == entry[0] and entry[1].lower() in row.sentence.text.lower()


def ledger_drift(
    rows: list[Row], ledger: tuple[tuple[str, str, str, str], ...]
) -> tuple[list[Row], list[tuple[str, str, str, str]]]:
    """Suspects no ledger row covers, and ledger rows the sweep no longer produces.

    The ledger is a parameter so both directions can be **driven** from planted
    input rather than only observed on a tree where neither fires --
    :data:`LEDGER_CONTROLS`, and round one's code-M6 across all five audits here.
    """
    suspects = [row for row in rows if row.verdict.startswith("SUSPECT")]
    unrecorded = [row for row in suspects if not any(_covers(entry, row) for entry in ledger)]
    stale = [entry for entry in ledger if not any(_covers(entry, row) for row in suspects)]
    return unrecorded, stale


def _ledger_drift(rows: list[Row]) -> tuple[list[Row], list[tuple[str, str, str, str]]]:
    return ledger_drift(rows, SUSPECTS)


#: What the ledger reconciliation must do, driven from synthetic rows, as
#: ``(what it demonstrates, the produced rows, the ledger, unrecorded, stale)``.
#:
#: Round one's code-M6: every ledger here claimed exactness in both directions and
#: no control ran either one. The last row is the case that made the claim false
#: on this audit -- a fragment keyed case-sensitively turns one recapitalisation
#: into two findings, neither of them real.
LEDGER_CONTROLS: Final[
    tuple[
        tuple[
            str, tuple[tuple[str, str, str], ...], tuple[tuple[str, str, str, str], ...], int, int
        ],
        ...,
    ]
] = (
    (
        "a suspect its ledger row covers: no drift in either direction",
        (("a.md", "SUSPECT", "nothing reads it today, and nothing will"),),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        0,
    ),
    (
        "a suspect with no ledger row -- the unrecorded direction",
        (("a.md", "SUSPECT", "nothing reads it today, and nothing will"),),
        (),
        1,
        0,
    ),
    (
        "a ledger row the sweep no longer produces -- the stale direction",
        (),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        1,
    ),
    (
        "a cleared row, which is not a suspect and must leave its ledger row stale",
        (("a.md", "record (past tense)", "nothing reads it today, and nothing will"),),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        1,
    ),
    (
        "the same sentence recapitalised at the head of a rewritten paragraph, which "
        "a case-sensitive fragment reported as both a new suspect and a stale row",
        (("a.md", "SUSPECT", "Nothing reads it today, and nothing will"),),
        (("a.md", "nothing reads it today", "true", "why"),),
        0,
        0,
    ),
)


def _run_ledger_controls() -> int:
    """Drive both reconciliation directions from planted rows and planted ledgers."""
    failures = 0
    print("\n=== LEDGER CONTROLS (the reconciliation, driven) ===")
    for label, produced, ledger, want_new, want_stale in LEDGER_CONTROLS:
        rows = [
            Row(
                objects=(".theurian/config.yaml",),
                shape="named",
                verdict=verdict,
                sentence=Sentence(path=path, line=0, text=text, block=text),
            )
            for path, verdict, text in produced
        ]
        unrecorded, stale = ledger_drift(rows, ledger)
        got = (len(unrecorded), len(stale))
        want = (want_new, want_stale)
        status = "OK  " if got == want else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: (unrecorded, stale)={got}, expected {want}")
    return 1 if failures else 0


def _run_positive_controls() -> int:
    """Show each shape's key hitting a planted sentence before any zero is read."""
    root = repo_root()
    members = inventory(root)
    keys = {member.name: _keys_for(member) for member in members}
    failures = 0
    print("=== POSITIVE CONTROLS ===")
    for label, path, planted, expected, unreleased in POSITIVE_CONTROLS:
        hit: str | None = None
        kinds: set[str] = set()
        for member in _referring(members, path, planted):
            for shape, key in keys[member.name]:
                if key.search(planted):
                    hit = hit or shape
                    kinds.add(member.kind)
                    break
        verdict = (
            _classify(
                hit,
                kinds,
                Sentence(path=path, line=0, text=planted, block=planted),
                # The planted sentence sits at line 0, so a control marked
                # `unreleased` is one whose line is inside the section.
                unreleased=frozenset({0}) if unreleased else frozenset(),
            )
            if hit
            else "no match"
        )
        found = verdict.startswith("SUSPECT")
        status = "OK  " if found is expected else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}")
        print(f"        expected suspect={expected}  got {verdict!r}")
    return (1 if failures else 0) | _run_ledger_controls()


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
        recorded = next(
            (
                entry
                for entry in SUSPECTS
                if entry[0] == row.sentence.path and entry[1] in row.sentence.text
            ),
            None,
        )
        print(f"  {row.sentence}  [{row.shape}]  {recorded[2] if recorded else 'UNRECORDED'}")
        print(f"      {row.sentence.text[:_MAX_ROW_TEXT]}")

    unrecorded, stale = _ledger_drift(rows)
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
    return 1 if unrecorded or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
