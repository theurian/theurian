"""Class 3: does any governed sentence name a closed issue as an owner? (#199 unit B).

A cite in **owner position** says somebody still owes this: *"[#N] removes this
face"*, *"owned by [#N]"*, *"when [#N] lands"*. If #N is closed, the residual it
names has no owner at all, and the sentence reads as covered work that nobody is
doing. A cite in **historical** position -- *"since [#N]"*, *"fixed in [#N]"* --
is correct precisely when #N is closed.

**No regex separates the two, and this module does not pretend one does.** #427
recorded that finding and hand-classified its file; #428 hand-classified three
numbers across the tree. What a machine *can* do is narrow the population to the
rows a person has to read, and that is what this is: closed number, an
owner-position phrase within :data:`_PROXIMITY` characters of the cite, and no
historical marker in the sentence.

Three population rules earn their place, each because leaving it out produced
noise a reader would have had to filter by hand:

* **Proximity, not sentence membership.** "will", "owes" and "pending" occur in
  ordinary prose; requiring them *beside* the cite took the suspect set from 115
  to 25 at ``141cf6f`` without dropping a member a person then judged defective.
* **A CHANGELOG entry is a record.** Every entry states what a release did on its
  date, so a closed owner in one is correct by construction. Correcting one would
  falsify the record.
* **A merged pull request in owner position is a defect**, not an exemption: a
  merged PR closes and can own nothing afterwards. That is the #444 shape, and
  :data:`tracker_state.OPEN_STATES` is where it is enforced.

**This class terminates as a classified population, not as a fix set** -- unit
B's Definition of Ready says so explicitly. Every suspect carries a hand verdict
in :data:`SUSPECTS`, and a verdict of ``DEFECT`` is a *filing*, not something
this audit's own branch corrects.

Run it::

    uv run --frozen python tools/audit/owner_position_cites.py
    uv run --frozen python tools/audit/owner_position_cites.py --positive-control
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import tracker_state
from claim_surfaces import Sentence, governed_paths, repo_root, sentences

#: Directory prefixes in scope.
GOVERNED_ROOTS: Final[tuple[str, ...]] = ("docs/", "schemas/", "plugins/")

#: Individual files in scope, by repository-relative path. Spelled in full rather
#: than by basename: matching ``README.md`` anywhere puts ``tools/audit/README.md``
#: -- this directory's own notes -- into a population about governed prose.
GOVERNED_FILES: Final[tuple[str, ...]] = (
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "packages/theurian-core/README.md",
    "packages/theurian-core/CHANGELOG.md",
    "plugins/claude-code/CHANGELOG.md",
)

#: Files whose entries are dated release records.
_RELEASE_RECORDS: Final = "CHANGELOG.md"

#: Every spelling of a cite: bracketed, as a URL, or bare. The bare form is in the
#: population deliberately -- #427 found three (a)-class defects there that a
#: bracket-only key does not reach.
_CITE: Final = re.compile(
    r"\[#(?P<bracketed>\d+)\]|issues/(?P<issue>\d+)|pull/(?P<pull>\d+)"
    r"|(?:^|[^0-9A-Za-z/])#(?P<bare>\d+)(?![0-9])"
)

#: Owner-position phrasing. Every alternative names an obligation that has not
#: been discharged; none of them is a tense marker.
#:
#: ``owns`` is spelled without the optional ``s``: ``\bowns?\b`` also matches the
#: possessive *"its own ingestion bounds"*, which is not an owner cite at all, and
#: it put one such row into the suspect set before this was narrowed.
_OWNER_POSITION: Final = re.compile(
    r"\bowns\b|\bowner\b|\bowned\s+by\b|\bowes?\b|\bowed\b|\btracked\s+by\b"
    r"|\bremoves?\s+this\b|\bpending\b|\bblocked\s+by\b|\bwill\s+\w+\b"
    r"|\bis\s+owed\b|\bstill\s+open\b|\bstays\s+open\b|\bnot\s+yet\b"
    r"|\bto\s+be\s+\w+ed\b"
    # Measured out of the unmarked residue rather than imagined: each of these
    # three carries a live defect at `141cf6f` that the list above did not reach.
    # "when [#N] lands", "removed when [#N]", "tracked as their own face ([#N])".
    r"|\blands\b|\bremoved\s+when\b|\btracked\s+as\b",
    re.IGNORECASE,
)

#: How far from the cite an owner-position phrase may sit and still be about it.
_PROXIMITY: Final = 60

#: Historical framing anywhere in the sentence. Present, the cite is a provenance
#: record and a closed number is correct.
_HISTORICAL: Final = re.compile(
    r"\bsince\b|\bestablished\b|\bfixed\s+in\b|\bclosed\b|\blanded\b|\brecorded\b"
    r"|\bcorrected\b|\bmeasured\b|\bwas\b|\bwere\b|\bafter\b|\bfound\b|\bshipped\b"
    r"|\badded\b|\bintroduced\b|\bfiled\b|\bsplit\b|\bdischarged\b|\bnarrowed\b"
    r"|\bretracted\b|\bfalsified\b|\bhistor|\bdone\s+in\b",
    re.IGNORECASE,
)

_MAX_EXCERPT: Final = 140


@dataclass(frozen=True, slots=True)
class Cite:
    number: str
    state: str
    verdict: str
    sentence: Sentence


def in_scope(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in GOVERNED_ROOTS) or path in GOVERNED_FILES


def _numbers(text: str) -> list[str]:
    found: list[str] = []
    for match in _CITE.finditer(text):
        number = next(group for group in match.groups() if group)
        if number not in found:
            found.append(number)
    return found


def _in_owner_position(text: str, number: str) -> bool:
    for match in _CITE.finditer(text):
        if next(group for group in match.groups() if group) != number:
            continue
        window = text[max(0, match.start() - _PROXIMITY) : match.end() + _PROXIMITY]
        if _OWNER_POSITION.search(window):
            return True
    return False


def classify(sentence: Sentence, number: str, state: str) -> str:
    if state in tracker_state.OPEN_STATES:
        return "open owner"
    if sentence.path.endswith(_RELEASE_RECORDS):
        return "record (release note)"
    if _HISTORICAL.search(sentence.text):
        return "history"
    if _in_owner_position(sentence.text, number):
        return "SUSPECT"
    return "unmarked"


def sweep(root: Path, *, offline: bool = False) -> tuple[list[Cite], str, int]:
    table, provenance = tracker_state.states(offline=offline)
    rows: list[Cite] = []
    raw = 0
    for path in governed_paths(root):
        if not in_scope(path):
            continue
        for sentence in sentences(root, path):
            occurrences = list(_CITE.finditer(sentence.text))
            raw += len(occurrences)
            for number in _numbers(sentence.text):
                state = table.get(number, "(absent from the tracker)")
                rows.append(
                    Cite(
                        number=number,
                        state=state,
                        verdict=classify(sentence, number, state),
                        sentence=sentence,
                    )
                )
    return rows, provenance, raw


#: Every suspect the sweep produces, with the verdict a person reached, as
#: ``(path, number, verdict, why)``.
#:
#: **DEFECT here means "file it", not "fix it in this branch".** Unit B's DoR puts
#: the general cite classification in the known-unfinished set: it terminates as a
#: classified population plus proposed filings, because the fix set is bounded by
#: the files that unit names.
#:
#: Exact in both directions, like every ledger in this directory: a suspect with
#: no row is a finding, and a row the sweep no longer produces means the cite was
#: repointed and the row goes with it.
SUSPECTS: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "SECURITY.md",
        "198",
        "history",
        "Provenance: #198 is what shipped the `propose accept` scan, and the sentence "
        "describes the shipped control. It is here only because `lands with them` sits "
        "beside the cite -- a false positive of the owner key, kept rather than tuned away.",
    ),
    (
        "docs/adr/0018-single-writer-synchronous-in-m1.md",
        "468",
        "DEFECT -- file",
        "The sentence says '#468 stays open for both halves'. #468 closed COMPLETED on "
        "2026-09-01 (the `migrate apply` serialisation). #439 beside it is open and "
        "correct, which is why only half the sentence is wrong -- the shape a reader "
        "trusts most.",
    ),
    (
        "docs/adr/0023-trigram-index-beside-the-word-index.md",
        "16",
        "DEFECT -- file",
        "'a mitigation for this one gap, removed when `IndexStore` states its own "
        "exhaustion ([#16])'. #16 closed COMPLETED on 2026-08-08 and its title is that "
        "exact sentence, so either the mitigation should be gone or the owner is dead.",
    ),
    (
        "docs/adr/0027-accept-validates-before-it-moves.md",
        "349",
        "DEFECT -- file",
        "'a YAML comment, and the migration and body filenames, are unscanned and tracked "
        "as their own face ([#349])'. #349 closed COMPLETED on 2026-08-26, and the threat "
        "model now says the opposite at :1524 -- 'the scan covers ... the artifacts it "
        "lands them as ([#349])'. Two governed surfaces disagree about a security control.",
    ),
    (
        "docs/security/threat-model.md",
        "349",
        "history",
        "The other side of that disagreement, and the one that matches #349's completion: "
        "a provenance cite for a shipped widening.",
    ),
    (
        "docs/security/threat-model.md",
        "40",
        "history",
        "A table cell under the column heading 'Corrected in'. The owner key fires on "
        "'what `/theurian:setup` announces it will do' in the *previous* column, which is "
        "about the command and not about the pull request.",
    ),
    (
        "docs/security/threat-model.md",
        "15",
        "DEFECT -- file",
        "The confirmed (a)-class member #427 recorded and #464 owns: 'the index purge in "
        "[#15] removes this face', with #15 closed. This audit reproducing it is the "
        "positive control for the whole key.",
    ),
    (
        "docs/security/threat-model.md",
        "16",
        "DEFECT -- file",
        "'Both go with the cache when [#16] lands', #16 closed. The sibling of the "
        "ADR-0023 row above: one residue, two surfaces, one dead owner.",
    ),
)

#: What the key must do before any count is read, as
#: ``(what it demonstrates, sentence, number, state, expected verdict)``.
#:
#: The first row is #427's own confirmed member, transcribed. The rest are the
#: three verdicts the classifier has to keep apart -- a live owner, a provenance
#: cite, and a merged pull request standing in owner position, which is the #444
#: shape and a defect rather than an exemption.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    (
        "#427's confirmed member: a closed issue named as what removes a residual",
        "The index purge in [#15](https://github.com/theurian/theurian/issues/15) "
        "removes this face.",
        "15",
        "issue:closed",
        "SUSPECT",
    ),
    (
        "a provenance cite, which a closed number makes correct",
        "The allowlist wording was corrected in [#129](https://github.com/theurian/"
        "theurian/issues/129).",
        "129",
        "issue:closed",
        "history",
    ),
    (
        "an open owner, which needs no further judgement",
        "It is owed by [#429](https://github.com/theurian/theurian/issues/429).",
        "429",
        "issue:open",
        "open owner",
    ),
    (
        "a merged pull request in owner position (#444's shape)",
        "That residue is owned by [#113](https://github.com/theurian/theurian/pull/113).",
        "113",
        "pr:merged",
        "SUSPECT",
    ),
    (
        "a bare mention, which the bracket-only key of earlier sweeps could not see",
        "#468 stays open for both halves.",
        "468",
        "issue:closed",
        "SUSPECT",
    ),
)


def _run_positive_controls() -> int:
    failures = 0
    print("=== POSITIVE CONTROLS ===")
    for label, text, number, state, expected in POSITIVE_CONTROLS:
        verdict = classify(
            Sentence(path="control.md", line=0, text=text, block=text), number, state
        )
        status = "OK  " if verdict == expected else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: got {verdict!r}, expected {expected!r}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if "--positive-control" in argv:
        return _run_positive_controls()

    root = repo_root()
    rows, provenance, raw = sweep(root, offline="--offline" in argv)

    print(f"tracker states: {provenance}")
    print("\n=== POPULATION ===")
    print(f"  raw cite occurrences: {raw}")
    print(f"  rows (one per sentence per number): {len(rows)}")
    print(f"  distinct numbers: {len({row.number for row in rows})}")
    print(f"  files in scope: {len({row.sentence.path for row in rows})}")

    print("\n=== CLASSIFICATION ===")
    tally: dict[str, int] = {}
    for row in rows:
        tally[row.verdict] = tally.get(row.verdict, 0) + 1
    for verdict, count in sorted(tally.items()):
        print(f"  {count:5}  {verdict}")
    print(
        "\n  `unmarked` is the measured escape space, not a clean bucket: a closed number\n"
        "  whose sentence carries neither an owner phrase nor a historical one. Three of\n"
        "  this branch's confirmed defects were sitting in it, and the three phrasings that\n"
        "  reached them were added to the owner key rather than guessed at. List it with\n"
        "  --unmarked and read it; that is the work this class terminates as."
    )

    if "--unmarked" in argv:
        print("\n=== UNMARKED (the escape space, listed) ===")
        for row in rows:
            if row.verdict == "unmarked":
                print(f"  #{row.number:<5} {row.sentence.path}:{row.sentence.line}")
                print(f"      {row.sentence.text[:_MAX_EXCERPT]}")

    suspects = [row for row in rows if row.verdict == "SUSPECT"]
    print("\n=== SUSPECTS (closed number, owner-position phrasing, no historical marker) ===")
    for row in suspects:
        recorded = next(
            (
                entry
                for entry in SUSPECTS
                if entry[0] == row.sentence.path and entry[1] == row.number
            ),
            None,
        )
        print(
            f"  #{row.number:<5} [{row.state}] {row.sentence.path}:{row.sentence.line}"
            f"  {recorded[2] if recorded else 'UNRECORDED'}"
        )
        print(f"      {row.sentence.text[:_MAX_EXCERPT]}")

    produced = {(row.sentence.path, row.number) for row in suspects}
    unrecorded = [
        row
        for row in suspects
        if not any(entry[0] == row.sentence.path and entry[1] == row.number for entry in SUSPECTS)
    ]
    stale = [entry for entry in SUSPECTS if (entry[0], entry[1]) not in produced]

    if unrecorded:
        print("\nUNRECORDED SUSPECTS -- a closed number in owner position nobody judged:")
        for row in unrecorded:
            print(f"  {row.sentence.path}:{row.sentence.line}  #{row.number}")
    if stale:
        print("\nSTALE LEDGER ROWS -- the sweep no longer produces these:")
        for path, number, verdict, _ in stale:
            print(f"  {path}  #{number}  [{verdict}]")
    if not suspects and not SUSPECTS:
        print("\n  none -- run --positive-control before reading that as a clean tree")
    return 1 if unrecorded or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
