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

**A sentence's own block is not the whole record.** This repository retracts a
paragraph by amending it *in place*, in the block below, so a verdict formed from
the flagged sentence alone reads deliberately preserved history as a live claim.
Round one graded four such rows ``DEFECT`` on that reading and all four were
wrong; #503 was filed on it and closed by refutation. :data:`_SUPERSEDED` and
:func:`succeeding_blocks` are the probe that closes it, and
:data:`SUPERSEDED_IN_PLACE` is the verdict a retracted sentence gets.

Three population rules earn their place, each because leaving it out produced
noise a reader would have had to filter by hand:

* **Proximity, not sentence membership.** "will", "owes" and "pending" occur in
  ordinary prose; requiring them *beside* the cite took the suspect set from 115
  to 25 at ``141cf6f`` without dropping a member a person then judged defective.
* **A dated CHANGELOG entry is a record.** Every released section states what a
  release did on its date, so a closed owner in one is correct by construction and
  correcting it would falsify the record. The rule is asked positively -- is this
  line *inside* a dated section? -- so both of its faces are one rule:
  ``[Unreleased]`` is not such an entry (round one's M-j), and neither is any line
  of a changelog with no dated sections at all, which the repository-root
  ``CHANGELOG.md`` is (round two's R2-j). Both are classified like any other
  governed prose.
* **A merged pull request in owner position is a defect**, not an exemption: a
  merged PR closes and can own nothing afterwards. That is the #444 shape, and
  :data:`tracker_state.OPEN_STATES` is where it is enforced.

**This class terminates as a classified population, not as a fix set** -- unit
B's Definition of Ready says so explicitly. Every judged row carries a hand
verdict in :data:`SUSPECTS`, and a verdict of ``DEFECT`` is a *filing*, not
something this audit's own branch corrects.

Run it::

    uv run --frozen python tools/audit/owner_position_cites.py
    uv run --frozen python tools/audit/owner_position_cites.py --positive-control
    uv run --frozen python tools/audit/owner_position_cites.py --overlap
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import tracker_state
from claim_surfaces import (
    Sentence,
    dated_lines,
    governed_paths,
    planted_changelog,
    print_control_tally,
    repo_root,
    sentences,
)

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

#: Files whose *dated* entries are release records, so a closed owner in one is
#: correct by construction and correcting it would falsify the record.
#:
#: ``[Unreleased]`` is **not** one of those entries, which is round one's M-j.
#: It describes the tree a reader has checked out, it is rewritten on every
#: merge, and a dead owner written into it is a live claim in a governed file.
#: Neither is a changelog with no dated sections at all -- the root
#: ``CHANGELOG.md`` -- which round two's R2-j is.
#: :func:`claim_surfaces.dated_lines` answers both by asking whether the line is
#: *inside* a dated section rather than whether it is outside ``[Unreleased]``.
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

#: An in-place amendment: the form this repository uses to retract a paragraph
#: without deleting it.
#:
#: **Why a forward walk and not a wider sentence key.** Round one graded four hand
#: ``DEFECT`` verdicts here and all four were wrong the same way: the flagged
#: sentence is real, and the block *after* it retracts it. ADR-0018:150 says
#: "#468 stays open for both halves" and :155 opens "**Closed on 2026-09-01
#: ([#468])**"; ADR-0023:351, threat-model:4560 and ADR-0027:460 are the same
#: shape. A verdict formed from the sentence's own block cannot see any of them,
#: which is why #503 was filed and closed by refutation
#: (``issuecomment-5508377135``) -- and why executing its proposed fix would have
#: edited deliberately preserved history.
#:
#: ``further amended`` needs no alternative of its own: ``\bamended\b`` reaches it.
_SUPERSEDED: Final = re.compile(
    r"\bamended\b|\bsuperseded\b|\bclosed\s+on\b|\bno\s+longer\s+holds\b"
    r"|\bleft\s+standing\b|\bdated\s+history\b",
    re.IGNORECASE,
)

#: The **bold run a block opens with**, and the reason the marker is looked for
#: only there.
#:
#: Measured, not assumed, over the four members round one found: each is written
#: as an emphasised opener -- ``**Closed on 2026-09-01 ([#468]).**``,
#: ``**Amended in Milestone 6: ...**``, ``**Amended in Milestone 6. Everything
#: from ... is left standing ...**``, ``**Further amended in Milestone 7 ...**``.
#: Searching the *whole* following block instead was tried first and cleared a
#: row it must not: SECURITY.md:452's #198 cite, whose next-but-one block happens
#: to say "a file that **no longer holds** the withdrawn rows" about an index
#: file. The marker has to be the block's own claim about the record above it,
#: which is what an opener is and what a sentence buried in a paragraph is not.
#:
#: A bullet-led amendment (``- **Amended ...**``) is deliberately outside this
#: key: nothing in the tree is written that way, and admitting the form is what
#: re-opened the false clear above.
_AMENDMENT_OPENER: Final = re.compile(r"^\*\*(?P<opener>[^*]+)\*\*")

#: How many blocks past the sentence's own the walk reads.
#:
#: **Measured from below, and only from below.** At ``be4b67c``, over the four
#: superseded members: the retraction sits **one** block later in ADR-0018,
#: ADR-0023 and the threat model, and **two** later in ADR-0027, where a
#: paragraph about T-15's grade sits between. Two is therefore the smallest reach
#: that covers every member, and a reach of one leaves ADR-0027's member reported
#: as a live dead-owner claim.
#:
#: **From above the tree bounds it far higher than this, and the earlier note
#: claiming otherwise was unmeasured** -- round two's R2-i. The threat model's #15
#: row is the control in that direction: a genuine dead owner that must stay
#: ``SUSPECT``. Measured at ``b92449b`` by running :data:`TREE_CONTROLS` at every
#: reach from 0 to 60, all four controls hold for **every reach from 2 to 38**,
#: and #15 first clears wrongly at **39**. So the interval this file's own
#: controls pin is ``[2, 38]``.
#:
#: Two rather than three is therefore a **judgement, not a measurement**: it is
#: the smallest value that works, chosen because a walk that reads no further
#: than it has been shown to need cannot clear a row nobody has looked at. What
#: would settle it is a member whose retraction sits three blocks down, and this
#: tree has none.
#:
#: The walk stops at a heading, because a reader following a heading has left the
#: region the amendment was written for. That is what keeps the interval as wide
#: as it is: most walks terminate on a heading long before the count runs out.
_SUPERSESSION_REACH: Final = 2

#: A section boundary, spelled the way :mod:`claim_surfaces` spells it. ``#468``
#: is deliberately not one: an ATX heading needs the space.
_HEADING: Final = re.compile(r"^#{1,6}\s")

#: The verdict a sentence gets when the block after it retracts it. Named rather
#: than spelled inline because the ledger's own reconciliation compares against
#: it, and a typo on either side would read as drift rather than as a typo.
SUPERSEDED_IN_PLACE: Final = "history (superseded in place)"

#: The verdicts that put a row in front of a person, and so into the ledger.
_JUDGED_VERDICTS: Final[frozenset[str]] = frozenset({"SUSPECT", SUPERSEDED_IN_PLACE})

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


def succeeding_blocks(rows: list[Sentence]) -> list[tuple[str, ...]]:
    """For each sentence of one file, the blocks that follow its own.

    Parallel to ``rows``, so element *i* is what the supersession probe reads for
    ``rows[i]``. The blocks of one file arrive from :func:`claim_surfaces.sentences`
    in document order and every sentence of a block carries the *same* block
    object, so the position is recovered by identity rather than by matching text
    -- two paragraphs of a document can be byte-identical, and a text match would
    walk forward from the wrong one.

    Returned as separate blocks and never as one joined string, because the probe
    reads each block's *opener*: joining them would put the second block's first
    words in the middle of the first block's text, where the key does not look.
    """
    blocks: list[str] = []
    position: list[int] = []
    for sentence in rows:
        if not blocks or blocks[-1] is not sentence.block:
            blocks.append(sentence.block)
        position.append(len(blocks) - 1)

    def ahead(start: int) -> tuple[str, ...]:
        read: list[str] = []
        for block in blocks[start : start + _SUPERSESSION_REACH]:
            if _HEADING.match(block):
                break
            read.append(block)
        return tuple(read)

    return [ahead(at + 1) for at in position]


def amendment_opener(block: str) -> str:
    """The block's emphasised opener when it retracts what came before, else ``""``."""
    match = _AMENDMENT_OPENER.match(block)
    if match is None:
        return ""
    opener = match.group("opener")
    return opener if _SUPERSEDED.search(opener) else ""


def classify(
    sentence: Sentence,
    number: str,
    state: str,
    succeeding: tuple[str, ...] = (),
    dated: frozenset[int] = frozenset(),
) -> str:
    """The verdict for one cite, read with the blocks that follow it.

    ``succeeding`` defaults to empty so a planted sentence can be classified with
    no document around it, which is what most of the synthetic controls do. Every
    real row is classified with :func:`succeeding_blocks` supplying it.
    """
    if state in tracker_state.OPEN_STATES:
        return "open owner"
    if sentence.path.endswith(_RELEASE_RECORDS) and sentence.line in dated:
        return "record (release note)"
    if _HISTORICAL.search(sentence.text):
        return "history"
    if _in_owner_position(sentence.text, number):
        if any(amendment_opener(block) for block in succeeding):
            return SUPERSEDED_IN_PLACE
        return "SUSPECT"
    return "unmarked"


def sweep(root: Path, *, offline: bool = False) -> tuple[list[Cite], str, int]:
    table, provenance = tracker_state.states(offline=offline)
    rows: list[Cite] = []
    raw = 0
    for path in governed_paths(root):
        if not in_scope(path):
            continue
        found = sentences(root, path)
        following = succeeding_blocks(found)
        dated = (
            dated_lines((root / path).read_text(encoding="utf-8", errors="surrogateescape"))
            if path.endswith(_RELEASE_RECORDS)
            else frozenset()
        )
        for sentence, succeeding in zip(found, following, strict=True):
            occurrences = list(_CITE.finditer(sentence.text))
            raw += len(occurrences)
            for number in _numbers(sentence.text):
                state = table.get(number, "(absent from the tracker)")
                rows.append(
                    Cite(
                        number=number,
                        state=state,
                        verdict=classify(sentence, number, state, succeeding, dated),
                        sentence=sentence,
                    )
                )
    return rows, provenance, raw


#: Every row the sweep hands a person, with the verdict they reached, as
#: ``(path, number, fragment, verdict, why)``.
#:
#: **The fragment is the third key dimension, and round one is why.** Keyed on
#: ``(path, number)`` alone, a *new* dead-owner sentence naming a number this file
#: already carries is absorbed by the existing row: the sweep produces two rows,
#: the ledger has one, and every direction of the reconciliation stays silent.
#: Reproduced -- a second "the index purge in [#15] removes this one too" appended
#: to the threat model left this audit at exit 0. So the key carries a fragment of
#: the sentence, matched case-insensitively, and a second sentence about the same
#: number in the same file is an unrecorded suspect **unless it contains that
#: fragment**, which is what the ambiguity direction added in round two catches.
#:
#: **DEFECT here means "file it", not "fix it in this branch".** Unit B's DoR puts
#: the general cite classification in the known-unfinished set: it terminates as a
#: classified population plus proposed filings, because the fix set is bounded by
#: the files that unit names.
#:
#: **The population is ``SUSPECT`` *and* :data:`SUPERSEDED_IN_PLACE`.** A row the
#: supersession probe clears is still a row a person read, and dropping it here
#: would delete the record of that reading -- and with it the only thing that
#: would notice if the amendment block were later deleted. So the reconciliation
#: compares the *machine* verdict against the one recorded in the row's opening
#: words: a superseded row whose amendment goes away comes back as ``SUSPECT``,
#: the two no longer agree, and the audit exits 1.
#:
#: Reconciled in four directions: a judged row with no ledger entry is a finding;
#: an entry the sweep no longer produces means the cite was repointed and the row
#: goes with it; a recorded verdict the classifier disagrees with is drift; and an
#: entry covering *two* judged rows is one judgement absorbing a sentence nobody
#: read, which the fragment key made rarer without making it impossible (round
#: two's R2-A). Every fragment below is chosen to identify one sentence, and
#: :data:`LEDGER_CONTROLS`' last row is what holds that.
SUSPECTS: Final[tuple[tuple[str, str, str, str, str], ...]] = (
    (
        "SECURITY.md",
        "198",
        "lands with them",
        "history",
        "Provenance: #198 is what shipped the `propose accept` scan, and the sentence "
        "describes the shipped control. It is here only because `lands with them` sits "
        "beside the cite -- a false positive of the owner key, kept rather than tuned away.",
    ),
    # `plugins/claude-code/CHANGELOG.md`'s 'It now states FR-V5 as owed with
    # review ingestion ([#129])' stood here as a `DEFECT` until #199 unit B's
    # prose assignment repointed it. It was the first row this ledger ever
    # carried from a CHANGELOG: the blanket release-note clear covered
    # `[Unreleased]` until round one's M-j scoped it to dated sections.
    #
    # The live owner is **#479**, not the #429 the schema's allowlist description
    # took on this branch: #429 owns the T-7 *fetch controls* and #479 owns
    # review ingestion, and FR-V5 is a review-ingestion requirement. The sentence
    # this entry describes -- `ingest.md`'s own FR-V5 bullet -- was repointed
    # #129 -> #479 in `ec0dbcd` (#482) on `main`; the plugin CHANGELOG was
    # outside that pass's file set, which is why the member survived here. The
    # entry now names #479 and keeps #129 in historical position with the reason
    # it stopped owning anything, so the sweep verdicts it `history` and no row
    # is owed.
    (
        "docs/adr/0018-single-writer-synchronous-in-m1.md",
        "468",
        "stays open for both halves",
        f"{SUPERSEDED_IN_PLACE} -- ADR-0018:155",
        "The sentence says '#468 stays open for both halves', and #468 closed COMPLETED on "
        "2026-09-01. It is not a live claim: :128-131 marks the paragraph "
        "'**Superseded by the 2026-09-01 closure below -- read this paragraph as dated "
        "history**', :155 opens '**Closed on 2026-09-01 ([#468])**', and :179 says '#468 "
        "is closed for both halves.' Round one graded this `DEFECT` from the sentence's "
        "own block alone; #503 was filed on that reading and closed by refutation.",
    ),
    (
        "docs/adr/0023-trigram-index-beside-the-word-index.md",
        "16",
        "removed when `IndexStore` states its own exhaustion",
        f"{SUPERSEDED_IN_PLACE} -- ADR-0023:356",
        "'a mitigation for this one gap, removed when `IndexStore` states its own "
        "exhaustion ([#16])', with #16 closed COMPLETED on 2026-08-08. The next block, "
        ":356, is '**Amended in Milestone 6: #16 landed, and the second call is gone "
        "rather than made cheap.**' -- the retraction, in place, one block down.",
    ),
    (
        "docs/adr/0027-accept-validates-before-it-moves.md",
        "349",
        "tracked as their own face",
        f"{SUPERSEDED_IN_PLACE} -- ADR-0027:479",
        "'a YAML comment, and the migration and body filenames, are unscanned and tracked "
        "as their own face ([#349])', with #349 closed COMPLETED on 2026-08-26. :479 is "
        "'**Further amended in Milestone 7, by the artifact-scan CL ([#349]) ... that "
        "boundary no longer holds**', and it quotes this sentence verbatim in order to "
        "retract it. Round one's `DEFECT` verdict here carried the claim that two "
        "governed surfaces disagree about #349's scan; that claim was false. "
        "threat-model:1524, SECURITY.md:470 and this :479 amendment record the widening "
        "consistently, and the fourth surface is the paragraph :479 retracts. This is the "
        "member whose retraction sits two blocks down rather than one, and it is what "
        "sets `_SUPERSESSION_REACH`.",
    ),
    (
        "docs/security/threat-model.md",
        "349",
        "the artifacts it lands them as",
        "history",
        "The provenance cite for that same shipped widening, and the one that matches "
        "#349's completion.",
    ),
    (
        "docs/security/threat-model.md",
        "40",
        "announces it will do",
        "history",
        "A table cell under the column heading 'Corrected in'. The owner key fires on "
        "'what `/theurian:setup` announces it will do' in the *previous* column, which is "
        "about the command and not about the pull request.",
    ),
    (
        "docs/security/threat-model.md",
        "15",
        "removes this face",
        "DEFECT -- file",
        "The confirmed (a)-class member #427 recorded and #464 owns: 'the index purge in "
        "[#15] removes this face', with #15 closed. This audit reproducing it is the "
        "positive control for the whole key -- and, since round one, for the supersession "
        "probe too: no block within `_SUPERSESSION_REACH` of it carries an amendment "
        "marker, which is what a genuine dead owner looks like beside the four that are "
        "retracted in place.",
    ),
    (
        "docs/security/threat-model.md",
        "16",
        "Both go with the cache when",
        f"{SUPERSEDED_IN_PLACE} -- threat-model:4566",
        "'Both go with the cache when [#16] lands', #16 closed. The sibling of the "
        "ADR-0023 row above, and superseded the same way: :4566 opens '**Amended in "
        "Milestone 6. Everything from ... to here is the Milestone 5 record and is left "
        "standing; none of it describes code that still exists.**'",
    ),
)

#: What the key must do before any count is read, as
#: ``(what it demonstrates, sentence, the blocks that follow it, number, state,
#: the document it is planted in, which section of it, expected verdict)``.
#:
#: **The path is a field and not a guess, which is round three's code-M2.** It was
#: derived from the other fields -- a changelog path when the section was not
#: ``none``, ``control.md`` otherwise -- and the one row that most needs a changelog
#: path is a ``none`` row: R2-j, the sentence in a changelog with *no dated
#: sections*, which is the root ``CHANGELOG.md``. It was classified as
#: ``control.md``, where the release-record rule never applies, so the row reported
#: the right verdict for the wrong reason and would have kept reporting it with
#: that rule deleted.
#:
#: The first row is #427's own confirmed member, transcribed. The next three are
#: the verdicts the classifier has to keep apart -- a live owner, a provenance
#: cite, and a merged pull request standing in owner position, which is the #444
#: shape and a defect rather than an exemption.
#:
#: **The last four drive the routes round one found unexercised.** Three are the
#: supersession probe, planted so the classifier is run on blocks it has never
#: seen: the amendment shape, ordinary prose, and the shape that actually broke
#: the first version of this probe -- a following block whose *middle* says "no
#: longer holds" about something else entirely. The fourth is the ``_HISTORICAL``
#: over-clear that M-a measures: an incidental past tense anywhere in the sentence
#: outranks the owner phrasing, and the expected verdict here records that
#: behaviour rather than wishing it away. :func:`main` prints how many real rows
#: it reaches.
POSITIVE_CONTROLS: Final[tuple[tuple[str, str, tuple[str, ...], str, str, str, str, str], ...]] = (
    (
        "#427's confirmed member: a closed issue named as what removes a residual",
        "The index purge in [#15](https://github.com/theurian/theurian/issues/15) "
        "removes this face.",
        (),
        "15",
        "issue:closed",
        "control.md",
        "none",
        "SUSPECT",
    ),
    (
        "a provenance cite, which a closed number makes correct",
        "The allowlist wording was corrected in [#129](https://github.com/theurian/"
        "theurian/issues/129).",
        (),
        "129",
        "issue:closed",
        "control.md",
        "none",
        "history",
    ),
    (
        "an open owner, which needs no further judgement",
        "It is owed by [#429](https://github.com/theurian/theurian/issues/429).",
        (),
        "429",
        "issue:open",
        "control.md",
        "none",
        "open owner",
    ),
    (
        "a merged pull request in owner position (#444's shape)",
        "That residue is owned by [#113](https://github.com/theurian/theurian/pull/113).",
        (),
        "113",
        "pr:merged",
        "control.md",
        "none",
        "SUSPECT",
    ),
    (
        "a bare mention, which the bracket-only key of earlier sweeps could not see",
        "#468 stays open for both halves.",
        (),
        "468",
        "issue:closed",
        "control.md",
        "none",
        "SUSPECT",
    ),
    (
        "the same sentence retracted by the next block, which is the ADR-0018 shape",
        "#468 stays open for both halves.",
        (
            "**Closed on 2026-09-01 ([#468](https://github.com/theurian/theurian/issues/"
            "468)).** The engineering landed with the serialisation.",
        ),
        "468",
        "issue:closed",
        "control.md",
        "none",
        SUPERSEDED_IN_PLACE,
    ),
    (
        "an un-superseded sentence whose next block is ordinary prose -- still a suspect",
        "#468 stays open for both halves.",
        (
            "The single writer is the daemon, and the CLI reaches it over the socket "
            "rather than opening the database itself.",
        ),
        "468",
        "issue:closed",
        "control.md",
        "none",
        "SUSPECT",
    ),
    (
        "SECURITY.md:452's shape: a marker word buried mid-block, about something else",
        "#468 stays open for both halves.",
        (
            "- **Search ranking, during a withdrawal.** A search after the apply is "
            "scored against a file that no longer holds the withdrawn rows.",
        ),
        "468",
        "issue:closed",
        "control.md",
        "none",
        "SUSPECT",
    ),
    (
        "a live owner cite carrying an incidental past tense, which `_HISTORICAL` clears",
        "The index purge in [#15](https://github.com/theurian/theurian/issues/15) removes "
        "this face, and the read count was measured at fifty.",
        (),
        "15",
        "issue:closed",
        "control.md",
        "none",
        "history",
    ),
    (
        "round one's M-j: a dead owner in a CHANGELOG's `[Unreleased]` section",
        "It now states FR-V5 as owed with review ingestion "
        "([#129](https://github.com/theurian/theurian/issues/129)).",
        (),
        "129",
        "issue:closed",
        "plugins/claude-code/CHANGELOG.md",
        "unreleased",
        "SUSPECT",
    ),
    (
        "the same sentence in a dated release section, which stays a record",
        "It now states FR-V5 as owed with review ingestion "
        "([#129](https://github.com/theurian/theurian/issues/129)).",
        (),
        "129",
        "issue:closed",
        "plugins/claude-code/CHANGELOG.md",
        "dated",
        "record (release note)",
    ),
    (
        "round two's R2-j: the same sentence in a changelog with no dated sections at "
        "all, which is the root `CHANGELOG.md` and which the outside-`[Unreleased]` "
        "rule cleared whole",
        "It now states FR-V5 as owed with review ingestion "
        "([#129](https://github.com/theurian/theurian/issues/129)).",
        (),
        "129",
        "issue:closed",
        "CHANGELOG.md",
        "none",
        "SUSPECT",
    ),
)

#: The two verdicts the supersession probe has to keep apart, checked against the
#: **real documents** rather than against planted text, as ``(what it
#: demonstrates, path, number, expected verdict)``.
#:
#: A synthetic control shows the key can fire; it cannot show the key fires on the
#: surface it was written for. Round one's four false ``DEFECT`` verdicts all lived
#: in real amendment blocks, and the reach the walk needs was measured on them --
#: so ADR-0027's member, whose retraction sits two blocks down, is the one that
#: pins :data:`_SUPERSESSION_REACH` from above. The threat model's #15 pins it
#: from below: it is a genuine dead owner, and a walk that grew until it started
#: clearing that row would be reported here.
TREE_CONTROLS: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "ADR-0027's member, retracted two blocks down by the `:479` amendment",
        "docs/adr/0027-accept-validates-before-it-moves.md",
        "349",
        SUPERSEDED_IN_PLACE,
    ),
    (
        "ADR-0018's member, retracted one block down by the `:155` closure note",
        "docs/adr/0018-single-writer-synchronous-in-m1.md",
        "468",
        SUPERSEDED_IN_PLACE,
    ),
    (
        "the threat model's #15: a dead owner with no amendment in reach, still a suspect",
        "docs/security/threat-model.md",
        "15",
        "SUSPECT",
    ),
    (
        "SECURITY.md's #198: the row a whole-block search wrongly cleared",
        "SECURITY.md",
        "198",
        "SUSPECT",
    ),
)


#: What the ledger reconciliation must do, driven from synthetic rows, as
#: ``(what it demonstrates, the judged rows, the ledger, unrecorded, stale,
#: drifted, ambiguous)``.
#:
#: **Round one's code-M6, closed here and in the four audits beside this one.**
#: Every ledger in this directory claimed to be exact in both directions and no
#: control ran either one, so the exactness was an assertion about code nobody had
#: executed with a mismatch in it. The third row is the absorption the fragment
#: key exists for: a *second* dead-owner sentence about a number the file already
#: carries, which a ``(path, number)`` key reports as recorded.
#:
#: **The last row is round two's R2-A**, and it is the absorption the *fragment*
#: key still had: the second sentence there contains the recorded fragment rather
#: than merely sharing its number, so the three directions above it all read it as
#: recorded. Nothing in the tree drives it -- every fragment in :data:`SUSPECTS`
#: identifies one sentence -- which is why it has to be planted.
LEDGER_CONTROLS: Final[
    tuple[
        tuple[
            str,
            tuple[tuple[str, str, str], ...],
            tuple[tuple[str, str, str, str, str], ...],
            int,
            int,
            int,
            int,
        ],
        ...,
    ]
] = (
    (
        "a judged row its ledger entry covers: no drift in any direction",
        (("a.md", "15", "the purge in [#15] removes this face"),),
        (("a.md", "15", "removes this face", "DEFECT -- file", "why"),),
        0,
        0,
        0,
        0,
    ),
    (
        "a judged row with no ledger entry at all -- the unrecorded direction",
        (("a.md", "15", "the purge in [#15] removes this face"),),
        (),
        1,
        0,
        0,
        0,
    ),
    (
        "a second sentence about a number the file already carries -- the absorption "
        "a `(path, number)` key reports as recorded",
        (
            ("a.md", "15", "the purge in [#15] removes this face"),
            ("a.md", "15", "a second residue: [#15] removes this one too"),
        ),
        (("a.md", "15", "removes this face", "DEFECT -- file", "why"),),
        1,
        0,
        0,
        0,
    ),
    (
        "a ledger entry the sweep no longer produces -- the stale direction",
        (),
        (("a.md", "15", "removes this face", "DEFECT -- file", "why"),),
        0,
        1,
        0,
        0,
    ),
    (
        "the same sentence recapitalised, which must NOT read as a new member",
        (("a.md", "15", "Removes This Face, the purge in [#15] does"),),
        (("a.md", "15", "removes this face", "DEFECT -- file", "why"),),
        0,
        0,
        0,
        0,
    ),
    (
        "a row recorded as superseded that comes back a suspect -- the verdict drift",
        (("a.md", "16", "both go with the cache when [#16] lands"),),
        (
            (
                "a.md",
                "16",
                "go with the cache",
                f"{SUPERSEDED_IN_PLACE} -- a.md:99",
                "why",
            ),
        ),
        0,
        0,
        1,
        0,
    ),
    (
        "a second sentence that CONTAINS the recorded fragment -- the absorption the "
        "fragment key still had, and only a cardinality check sees",
        (
            ("a.md", "15", "the purge in [#15] removes this face"),
            ("a.md", "15", "a second residue, which [#15] removes this face of too"),
        ),
        (("a.md", "15", "removes this face", "DEFECT -- file", "why"),),
        0,
        0,
        0,
        1,
    ),
)


def _run_ledger_controls() -> int:
    """Drive all four reconciliation directions from planted rows and planted ledgers."""
    failures = 0
    ran = 0
    print("\n=== LEDGER CONTROLS (the reconciliation, driven) ===")
    for (
        label,
        produced,
        ledger,
        want_new,
        want_stale,
        want_drift,
        want_ambiguous,
    ) in LEDGER_CONTROLS:
        ran += 1
        judged = [
            Cite(
                number=number,
                state="issue:closed",
                verdict="SUSPECT",
                sentence=Sentence(path=path, line=line, text=text, block=text),
            )
            for line, (path, number, text) in enumerate(produced)
        ]
        unrecorded, stale, disagreed, ambiguous = ledger_drift(judged, ledger)
        got = (len(unrecorded), len(stale), len(disagreed), len(ambiguous))
        want = (want_new, want_stale, want_drift, want_ambiguous)
        status = "OK  " if got == want else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: (unrecorded, stale, drift, ambiguous)={got}, expected {want}")
    print_control_tally("LEDGER_CONTROLS", ran, failures)
    return 1 if failures else 0


def _run_positive_controls(*, offline: bool) -> int:
    failures = 0
    ran = 0
    print("=== POSITIVE CONTROLS ===")
    for label, text, succeeding, number, state, path, section, expected in POSITIVE_CONTROLS:
        ran += 1
        # The section membership is *computed* by the rule under test over a
        # synthetic document, never asserted here -- round two's R2-g. The old
        # control handed `classify` a hardcoded `frozenset({0})` and asserted the
        # verdict that premise implies, so gutting `unreleased_lines` survived it.
        #
        # The path is the row's own, for the reason POSITIVE_CONTROLS records: it
        # used to be derived from `section`, which gave the R2-j row -- a changelog
        # with no dated sections -- a path the release-record rule does not even
        # look at.
        lines, line = planted_changelog(text, section=section)
        verdict = classify(
            Sentence(path=path, line=line, text=text, block=text),
            number,
            state,
            succeeding,
            lines,
        )
        status = "OK  " if verdict == expected else "FAIL"
        failures += status == "FAIL"
        print(f"  {status} {label}: got {verdict!r}, expected {expected!r}")
    print_control_tally("POSITIVE_CONTROLS", ran, failures)

    tree_ran = 0
    tree_failures = 0
    root = repo_root()
    table, provenance = tracker_state.states(offline=offline)
    print(f"\n=== TREE CONTROLS (tracker states: {provenance}) ===")
    for label, path, number, expected in TREE_CONTROLS:
        tree_ran += 1
        found = sentences(root, path)
        following = succeeding_blocks(found)
        verdicts = [
            classify(sentence, number, table.get(number, "(absent from the tracker)"), succeeding)
            for sentence, succeeding in zip(found, following, strict=True)
            if number in _numbers(sentence.text)
            and _in_owner_position(sentence.text, number)
            and not _HISTORICAL.search(sentence.text)
        ]
        status = "OK  " if verdicts == [expected] else "FAIL"
        tree_failures += status == "FAIL"
        print(f"  {status} {label}: got {verdicts}, expected {[expected]}")
    print_control_tally("TREE_CONTROLS", tree_ran, tree_failures)
    return (1 if failures or tree_failures else 0) | _run_ledger_controls()


def _historical_overlap_rows(root: Path, *, offline: bool) -> list[tuple[str, int, str, str]]:
    """The rows the historical rule clears that the owner key would otherwise flag.

    M-a: ``_HISTORICAL`` runs over the whole sentence and outranks the owner
    phrasing, so one incidental ``was`` anywhere in a sentence clears a cite that
    is otherwise in owner position. That is the audit's largest silent
    over-clear; :func:`main` prints the count and ``--overlap`` lists the members.

    **Scoped the way :func:`classify` is scoped, which is round two's R2-k.** This
    walk kept the blanket "a CHANGELOG is a record" skip that round one's M-j
    removed from the classifier, so the number it printed described a narrower
    population than the one being classified -- a measured escape space that left
    out exactly the section the same round had just brought in. It now skips a
    changelog sentence only where a *dated* release states it, so the two
    populations are the same population.
    """
    table, _ = tracker_state.states(offline=offline)
    found: list[tuple[str, int, str, str]] = []
    for path in governed_paths(root):
        if not in_scope(path):
            continue
        dated = (
            dated_lines((root / path).read_text(encoding="utf-8", errors="surrogateescape"))
            if path.endswith(_RELEASE_RECORDS)
            else frozenset()
        )
        for sentence in sentences(root, path):
            if sentence.path.endswith(_RELEASE_RECORDS) and sentence.line in dated:
                continue
            if not _HISTORICAL.search(sentence.text):
                continue
            for number in _numbers(sentence.text):
                if table.get(number, "") in tracker_state.OPEN_STATES:
                    continue
                if _in_owner_position(sentence.text, number):
                    found.append((path, sentence.line, number, sentence.text[:_MAX_EXCERPT]))
    return found


def _covers(entry: tuple[str, str, str, str, str], row: Cite) -> bool:
    """Whether one ledger entry is the record of this judged row.

    Three dimensions, and the fragment is matched **case-insensitively** so a
    sentence recapitalised at the head of a rewritten paragraph does not read as
    a new member.
    """
    return (
        entry[0] == row.sentence.path
        and entry[1] == row.number
        and entry[2].lower() in row.sentence.text.lower()
    )


def ledger_drift(
    judged: list[Cite], ledger: tuple[tuple[str, str, str, str, str], ...]
) -> tuple[
    list[Cite],
    list[tuple[str, str, str, str, str]],
    list[tuple[Cite, tuple[str, str, str, str, str]]],
    list[tuple[tuple[str, str, str, str, str], list[str]]],
]:
    """``(unrecorded, stale, verdict drift, ambiguous)`` for one set against one ledger.

    Both arguments are parameters rather than module globals, so the
    reconciliation can be **driven** from synthetic input. Round one's code-M6
    was that no ``--positive-control`` in this directory exercised any ledger
    direction at all: a reconciliation that had stopped reporting would have
    reported the same clean tree a clean tree reports.
    :data:`LEDGER_CONTROLS` is what drives it now.

    **Ambiguity is the fourth direction and round two's R2-A.** The fragment
    dimension round one added closed the ``(path, number)`` absorption only for a
    second sentence whose wording *differs*: :func:`_covers` tests containment and
    counts nothing, so a second dead-owner sentence about the same number in the
    same file that happens to contain the recorded fragment is covered by the
    existing row, and unrecorded, stale and drift all stay silent. One recorded
    judgement then stands in for two live sentences.
    """
    unrecorded = [row for row in judged if not any(_covers(entry, row) for entry in ledger)]
    stale = [entry for entry in ledger if not any(_covers(entry, row) for row in judged)]
    disagreed = [
        (row, entry)
        for row in judged
        for entry in ledger
        if _covers(entry, row)
        and entry[3].startswith(SUPERSEDED_IN_PLACE) is not (row.verdict == SUPERSEDED_IN_PLACE)
    ]
    ambiguous = [
        (entry, covered)
        for entry in ledger
        if len(
            covered := [
                f"{row.sentence.path}:{row.sentence.line}" for row in judged if _covers(entry, row)
            ]
        )
        > 1
    ]
    return unrecorded, stale, disagreed, ambiguous


def _report_drift(judged: list[Cite]) -> int:
    """Reconcile the judged population against the ledger, in four directions.

    *Unrecorded* is a row nobody read; *stale* is a ledger entry the sweep no
    longer produces; *drift* is the third and the one the supersession probe
    needs -- a row recorded as superseded that comes back a suspect means the
    amendment block moved or was deleted, and the sentence is a live dead-owner
    claim again with a ledger row saying otherwise. *Ambiguous* is the fourth: one
    entry covering two judged rows is one judgement absorbing a sentence nobody
    read.
    """
    unrecorded, stale, disagreed, ambiguous = ledger_drift(judged, SUSPECTS)

    if unrecorded:
        print("\nUNRECORDED SUSPECTS -- a closed number in owner position nobody judged:")
        for row in unrecorded:
            print(f"  {row.sentence.path}:{row.sentence.line}  #{row.number}")
            print(f"      {row.sentence.text[:_MAX_EXCERPT]}")
    if stale:
        print("\nSTALE LEDGER ROWS -- the sweep no longer produces these:")
        for path, number, fragment, verdict, _ in stale:
            print(f"  {path}  #{number}  [{verdict}]  {fragment!r}")
    if disagreed:
        print("\nVERDICT DRIFT -- the ledger and the classifier disagree about supersession:")
        for row, entry in disagreed:
            print(
                f"  {row.sentence.path}:{row.sentence.line}  #{row.number}  "
                f"classifier says {row.verdict!r}, the ledger says {entry[3]!r}"
            )
        print(
            "\n  A row recorded as superseded that comes back a suspect means the amendment\n"
            "  block moved or was deleted, and the sentence is a live dead-owner claim again."
        )
    if ambiguous:
        print("\nAMBIGUOUS LEDGER ROWS -- one judgement covering more than one judged row:")
        for (path, number, fragment, _, _), covered in ambiguous:
            print(f"  {path}  #{number}  {fragment!r}  covers {covered}")
        print(
            "\n  A person judged one sentence and the fragment absorbs another. Narrow the\n"
            "  fragment until it identifies one, and judge the rest."
        )
    if not judged and not SUSPECTS:
        print("\n  none -- run --positive-control before reading that as a clean tree")
    return 1 if unrecorded or stale or disagreed or ambiguous else 0


def main(argv: list[str]) -> int:
    if "--positive-control" in argv:
        return _run_positive_controls(offline="--offline" in argv)

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

    overlap = _historical_overlap_rows(root, offline="--offline" in argv)
    print(
        f"\n  `history` rows that are ALSO in owner position: {len(overlap)}\n"
        "  The second measured escape space, and the larger one. `_HISTORICAL` runs over\n"
        "  the whole sentence and outranks the owner key, so one incidental `was` clears a\n"
        "  cite that names a dead owner. These rows are cleared without a person reading\n"
        "  them; --overlap lists them."
    )
    if "--overlap" in argv:
        print("\n=== HISTORY/OWNER-POSITION OVERLAP (cleared by tense alone) ===")
        for path, line, number, text in overlap:
            print(f"  #{number:<5} {path}:{line}")
            print(f"      {text}")

    if "--unmarked" in argv:
        print("\n=== UNMARKED (the escape space, listed) ===")
        for row in rows:
            if row.verdict == "unmarked":
                print(f"  #{row.number:<5} {row.sentence.path}:{row.sentence.line}")
                print(f"      {row.sentence.text[:_MAX_EXCERPT]}")

    judged = [row for row in rows if row.verdict in _JUDGED_VERDICTS]
    print("\n=== JUDGED (closed number, owner-position phrasing, no historical marker) ===")
    for row in judged:
        # `_covers`, never a second spelling of it: keyed on `(path, number)`
        # alone this printed a recorded verdict beside a row the reconciliation
        # below counts as unrecorded, which is the display half of round two's
        # code-L3 -- one rule, one place.
        recorded = next((entry for entry in SUSPECTS if _covers(entry, row)), None)
        print(
            f"  #{row.number:<5} [{row.state}] {row.sentence.path}:{row.sentence.line}"
            f"  [{row.verdict}]  {recorded[2] if recorded else 'UNRECORDED'}"
        )
        print(f"      {row.sentence.text[:_MAX_EXCERPT]}")

    return _report_drift(judged)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
