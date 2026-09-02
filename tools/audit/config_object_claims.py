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
The machine clears the rows it can defend -- a CHANGELOG entry, a past-tense
sentence, a key-scoped claim -- and everything left is a *suspect* that a person
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

from claim_surfaces import Sentence, governed_paths, load_json, repo_root, sentences

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
_RECORD_MARKERS: Final = re.compile(
    r"\b(?:said|stated|used\s+to|until|no\s+longer|previously|was|were|had\s+been|"
    r"when\s+this\s+was\s+written|before\s+this|corrected|retracted|falsified|"
    r"narrowed|carried|quoted|read\b(?!s))\b",
    re.IGNORECASE,
)

#: Files whose every entry is a dated record of a release, so a claim quoted in
#: one is history by construction. Correcting one would falsify the record.
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


def _quoted(path: str) -> str:
    """A path as prose spells it: bare, backticked, or quoted."""
    return r"[`\"'“]?" + re.escape(path).replace(r"\.theurian", r"\.?theurian") + r"[`\"'”]?"


#: How a file object is referred to when the sentence uses a pronoun instead of
#: its path. Admitted only inside a block that already names the object, which is
#: how the shipped ``ingest.md`` claim -- *"nothing reads that file today"*, one
#: clause after the path -- is reachable at all.
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
                    rf"{re.escape('.'.join(dotted))}|[`\"']{leaf}[`\"']", re.IGNORECASE
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


def _classify(shape: str, kinds: set[str], sentence: Sentence) -> str:
    """The verdict, in the order the rules have to be applied.

    Order is load-bearing. The release-note rule runs first because a CHANGELOG
    entry quoting a retracted universal is a record whatever tense it is in; the
    key-scoped rule runs last because it is the one a defect can hide behind --
    a sentence that names a key *and* claims the file is unread is the #461 shape
    exactly, and it stays a suspect as long as one file object is in its
    reference set.
    """
    if sentence.path.endswith(_RELEASE_RECORDS):
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
    """Which watched objects a block is about.

    A named reference wins outright. The *home* rule -- "every sentence in the
    published schema is about the file the schema governs" -- is the fallback and
    only the fallback, because applied unconditionally it makes each of the
    schema's eleven key descriptions a claim about the whole file, and the
    key-scoped sentences #426 landed would read as the universal returning.
    """
    named = [member for member in members if member.reference.search(block)]
    return named or [member for member in members if path in member.homes]


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
                        matched[0][1], {member.kind for member, _ in matched}, sentence
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
        "plugins/claude-code/commands/ingest.md",
        "nothing reads that file today",
        "DEFECT, #461",
        "False since ADR-0027 decision 3: `security/project_config.py` reads the file for "
        "`security.secretScan`. The conservative conclusion survives on the narrower fact "
        "-- `providers.review.repositories` has no reader -- and has to be re-derived on "
        "it, the #426 treatment. Corrected by #199 unit B's prose assignment.",
    ),
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
#: The two live members are here as controls too, because a control that only
#: ever runs against synthetic text does not show that the key reaches the
#: shipped surfaces.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, str, bool], ...]] = (
    (
        "the shipped schema root (#455)",
        "schemas/config/project-config.schema.json",
        "Nothing in src/ reads this file, so no value in it takes effect today.",
        True,
    ),
    (
        "the shipped plugin claim (#461)",
        "plugins/claude-code/commands/ingest.md",
        "A repository will have to be on the allowlist in `.theurian/config.yaml`; "
        "nothing reads that file today.",
        True,
    ),
    (
        "the bare pronoun, which no path-bearing key reaches",
        "docs/architecture/raptor.md",
        "`.theurian/config.yaml` is the file. Nothing reads it today.",
        True,
    ),
    (
        "no config surface -- the measured escape of the phrasing-keyed scan",
        "docs/architecture/raptor.md",
        "The summariser's budget lives in `.theurian/config.yaml` terms. It has no config surface.",
        True,
    ),
    (
        "the narrowed sentence #426 landed, which must NOT be a suspect",
        "docs/architecture/raptor.md",
        "Nothing in `src/` reads `raptor.enabled`, nor any other key in the `raptor` block.",
        False,
    ),
    (
        "a past-tense record, which must NOT be a suspect",
        "docs/architecture/raptor.md",
        "Two sentences said nothing in `src/` reads `.theurian/config.yaml`. Each was "
        "true when written.",
        False,
    ),
)


def _ledger_drift(rows: list[Row]) -> tuple[list[Row], list[tuple[str, str, str, str]]]:
    """Suspects no ledger row covers, and ledger rows the sweep no longer produces."""
    unrecorded = [
        row
        for row in rows
        if row.verdict.startswith("SUSPECT")
        and not any(
            row.sentence.path == path and fragment in row.sentence.text
            for path, fragment, _, _ in SUSPECTS
        )
    ]
    stale = [
        entry
        for entry in SUSPECTS
        if not any(
            row.verdict.startswith("SUSPECT")
            and row.sentence.path == entry[0]
            and entry[1] in row.sentence.text
            for row in rows
        )
    ]
    return unrecorded, stale


def _run_positive_controls() -> int:
    """Show each shape's key hitting a planted sentence before any zero is read."""
    root = repo_root()
    members = inventory(root)
    keys = {member.name: _keys_for(member) for member in members}
    failures = 0
    print("=== POSITIVE CONTROLS ===")
    for label, path, planted, expected in POSITIVE_CONTROLS:
        hit: str | None = None
        kinds: set[str] = set()
        for member in _referring(members, path, planted):
            for shape, key in keys[member.name]:
                if key.search(planted):
                    hit = hit or shape
                    kinds.add(member.kind)
                    break
        verdict = (
            _classify(hit, kinds, Sentence(path=path, line=0, text=planted, block=planted))
            if hit
            else "no match"
        )
        found = verdict.startswith("SUSPECT")
        status = "OK  " if found is expected else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}")
        print(f"        expected suspect={expected}  got {verdict!r}")
    return 1 if failures else 0


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
