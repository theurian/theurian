"""What ADR-0029's #492 landing note and its CHANGELOG entries claim, against the code.

**Effective scope, stated as narrowly as it is true.** This module reads exactly
two records -- ``docs/adr/0029-review-findings-are-governed-knowledge.md`` and the
one ``##`` section of ``packages/theurian-core/CHANGELOG.md`` that cites
``pull/492`` -- and holds four claims they make about the findings pipeline
against five live symbols: :data:`FINDINGS_SCHEMA_VERSION`,
``trailer_source._FORMAT``, ``review_finding._STAMP_PROBES`` with
:func:`keyed_lines`, ``SqliteReviewFindingStore.replace_all``'s publish, and
``FindingsBuilder``'s ``write_section``. **It does not test the findings pipeline.**
Every mechanism named below has its own driver, cited at each pin; what is held
here is that the *records* move when the mechanism does. A build that computed the
right ``.building`` path and never renamed onto it would fail
``test_findings_store.py``, not this module -- except where a pin below says
otherwise, and each one says which.

Four claims, each with a prose half over the records and a fact half over the code:

-- 1. The residuals paragraph is closed, not standing --------------------------

ADR-0029's slice-2 note carries a paragraph that says, in the present tense, that
the write is not yet atomic, that ``committed_at``'s order is not chronological,
that ``PARSER_STAMP`` reaches no mechanic, and that ``%b`` can drop a folded
trailer. All four are false as of #492. The paragraph is deliberately **kept as
written** -- it records what slice-2 shipped and what each fix then had to answer
-- so what makes the record true is not its wording but its *position*: a closure
marker immediately above it, and a landing note below.

That is a structural claim, so the pin is structural.
:func:`test_the_closure_marker_stands_immediately_above_the_residuals_it_closes`
asserts the marker is the paragraph *directly* preceding, because a marker that
drifts down the document leaves a reader meeting four present-tense falsehoods
with nothing between them and the claim. The landing note is held only to sit
*below* the residuals rather than directly below: it says "see *Landed in #492*
below it", which a paragraph inserted between them does not falsify, and pinning
adjacency there would go RED on an ordinary edit that makes no record wrong.

Both are asserted to match **exactly one** paragraph. Zero means the anchor was
rewritten and the ordering pin is about to pass over nothing; more than one means
the anchor no longer identifies a single paragraph and "immediately above" would
be reporting on whichever copy came first.

-- 2. The schema version the records spell is the one the code carries (#405) ---

Both records state the move as ``FINDINGS_SCHEMA_VERSION`` "moves/moved 1 -> 2".
:data:`_SCHEMA_VERSION_MOVE` harvests **both** numbers out of each record and
asserts the target equals the live constant and the source is one below it. Two
independently written things held equal, the ``test_adr_0018_claims.py`` shape --
so the pin fails when either side moves, rather than being green for whatever the
code happens to say.

**What a bump to 3 does here, and why that RED is wanted.** Both pins go RED, and
the two REDs mean different things, so they are two tests with two messages. The
CHANGELOG's is a defect: its section describes the version *this release ships*,
and a release that moves the constant 1 -> 3 while the entry says 1 -> 2 is a
false release note. The ADR's is a **decision to record**: its note is anchored to
#492 and "moved 1 -> 2 for this" stays true as history, so the remedy is to append
the next move to the landing note, not to delete the pin. Recording that choice is
the point -- a constant that bumps with neither record touched is exactly the
silent divergence this file set exists for.

-- 3. The stamp's behaviour section, and the residual stated on it (#406) -------

Both records state a residual rather than claiming #406 closed: "the residual is
stated rather than closed ... a widening no probe separates leaves the stamp still
and owes a probe". That sentence is *about* the probe matrix, so it is vacuous if
there is no probe matrix in the stamp at all -- which is precisely the pre-#406
shape it is describing the exit from.

The fact half therefore drives the section rather than reading it:
:func:`test_the_behaviour_section_the_406_residual_speaks_of_is_driven_by_the_probes`
perturbs ``_STAMP_PROBES`` in both directions -- a probe added, and the tuple
emptied -- and requires the recomputed stamp to differ each time. A
``_compute_parser_stamp`` that dropped the behaviour section would return the
baseline for both and fail. ``_STAMP_PROBES`` is **not** among the constants
``test_review_finding.py::test_the_parser_stamp_changes_when_any_grammar_element_changes``
perturbs (six cases: the key, the separator, the two vocabularies, and the alias
map twice), so this is a gap that module leaves rather than a copy of it.

The second fact half is the #406 *bind*. ADR-0029 says the extraction rule "moved
into the domain as ``keyed_lines`` ... it was grammar the git adapter owned
privately, and therefore unreachable to the stamp". Two things make that true and
both are asserted: the function is defined in ``theurian.domain.review_finding``,
and the git adapter uses **that object** rather than a private rule of its own. A
re-privatised copy in ``trailer_source`` would leave the stamp measuring a rule
nothing reads, which is the #406 shape returning with the symbol name intact.

What the mechanics themselves do is
``test_review_finding.py::test_the_parser_stamp_moves_when_a_parser_mechanic_widens``
(which parametrises over ``keyed_lines``) and
``...::test_the_parser_stamp_moves_when_a_vocabulary_gains_a_matching_hook``. Not
repeated here.

-- 4. What the two mechanisms the records name actually are ---------------------

**``%B``, not ``%b`` (#410).** ``_FORMAT`` must carry ``%B`` and must not carry
``%b``, and -- because a constant nothing reads is the vacuity this file set keeps
meeting -- ``_FORMAT`` must be *loaded inside* ``GitTrailerFindingSource._git_log``,
the one place the argv is built. Reverting the format alone reddens this without
anything else in the suite having to notice.

**Publish by rename (#404).** Read from the AST of ``replace_all`` rather than by
running it, because what the record claims is a *shape*: "assembles at a
``.building`` sibling and publishes with ``os.replace`` ... never written under
the live name". Three things are held together, and it is the conjunction that
means something: exactly one ``os.replace``, its destination is ``self._path``,
and its source is the same local ``sqlite3.connect`` was handed. That last
equality is what rules out the pre-#404 shape -- connect on the live path, no
rename -- without keying on the local's name, which either edit would move.
The ``.building`` name is separately asserted to be a *sibling* of the publish
path, since ``os.replace`` is atomic only within a filesystem and that is the
reason the record gives for the name being a sibling at all.

**One continuous hold (#404).** ``FindingsBuilder.__init__`` must take
``write_section`` as a keyword-only parameter, **and the shipped composition root
must pass the project's lock into it** -- a parameter defaulting to
``nullcontext`` that no caller feeds is a lock that is never taken, and the
signature alone cannot tell those apart. The ordering inside ``build`` -- git read
outside the hold, ``replace_all`` inside it, one entry -- is
``test_findings_builder.py::test_the_build_publishes_inside_one_continuous_write_section``
and is not repeated here.

-- What this module deliberately does not hold ---------------------------------

- **It proves no atomicity and takes no lock.** The AST pins say the source has the
  publish-by-rename shape and the CLI passes a lock factory; whether the rename is
  atomic on a given filesystem, and whether the lock excludes a second writer, are
  ``test_findings_store.py`` and ``test_findings_build_cli.py``.
- **It reads two records and no third.** Every other paragraph of ADR-0029 is out
  of scope, including the *Re-anchored census* and its ``%b``/``%B`` equivalence
  table: that table is a measurement of named commits, which is the one form of a
  written number this file set accepts without a fact side.
- **The prose halves are regression pins over the wording these claims have taken**,
  not closure arguments. A rule that pins grammar always has a next grammar.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT, collapsed

from theurian.application.findings_builder import FindingsBuilder
from theurian.domain import review_finding
from theurian.domain.review_finding import keyed_lines
from theurian.infrastructure.git import trailer_source
from theurian.infrastructure.sqlite.findings_schema import FINDINGS_SCHEMA_VERSION
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore

ADR_0029: Final = REPO_ROOT / "docs" / "adr" / "0029-review-findings-are-governed-knowledge.md"
CHANGELOG: Final = REPO_ROOT / "packages" / "theurian-core" / "CHANGELOG.md"

_SOURCE_ROOT: Final = REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian"

FINDINGS_STORE_MODULE: Final = _SOURCE_ROOT / "infrastructure" / "sqlite" / "findings_store.py"
TRAILER_SOURCE_MODULE: Final = _SOURCE_ROOT / "infrastructure" / "git" / "trailer_source.py"
FINDINGS_COMMANDS_MODULE: Final = _SOURCE_ROOT / "cli" / "findings_commands.py"

#: The four residuals #492 closed. The population of every per-residual assertion
#: below, so a fifth entry or a dropped one fails as a count rather than being
#: skipped by a loop that ranges over whatever it finds.
RESIDUAL_ISSUES: Final = ("404", "405", "406", "410")

#: The pull request the landing note is anchored to, as the link target rather
#: than a bare ``#492``: an anchor that is not reachable from the record is one a
#: reader cannot follow. Same rule as ``test_adr_0018_claims.py``'s ``LIVE_OWNER``.
LANDING_PR: Final = "pull/492"

#: The closure marker, keyed on what it *asserts* rather than on the cross-reference
#: it carries. A key built from "see *Landed in #492*" would stop matching the
#: moment the note was renamed -- and the paragraph would then drop out of the
#: population rather than fail, which is the silent narrowing every scan in this
#: file set asserts its premise against.
CLOSURE_MARKER: Final = "all four residuals in the next paragraph are closed"

#: The residuals paragraph itself, keyed on its opening claim. Deliberately not
#: keyed on any of the four present-tense residuals it states: those sentences are
#: what the marker exists to close, and a key on one of them would fail to find the
#: paragraph exactly when someone had started editing it -- reporting a missing
#: paragraph two edits away from the ordering this module actually holds.
RESIDUALS_PARAGRAPH: Final = "three residuals from review are recorded, not blocking"

#: The landing note's own paragraph. Keyed on the claim, not on the PR link, for
#: :data:`CLOSURE_MARKER`'s reason; the link is asserted separately, in the
#: paragraph this finds.
LANDING_NOTE: Final = "residuals above are closed, still with no serving"

#: What both records must keep saying about #406, as two fragments rather than one
#: sentence: the records word the middle clause differently ("is exact only for" in
#: the ADR, "separates only" in the CHANGELOG) and pinning the whole sentence would
#: hold a difference neither record is wrong about. The first fragment is the
#: retraction of a closure claim; the second is the residual's actual content, and
#: a note cut down to the first alone would read as a closure that merely hedges.
PROBE_RESIDUAL: Final = (
    "the residual is stated rather than closed",
    "leaves the stamp still and owes a probe",
)

#: How each record spells the schema-version move, with **both** numbers captured.
#: The source number is captured too because a record that kept only the target --
#: "FINDINGS_SCHEMA_VERSION is 2" -- would state a fact without stating that
#: anything moved, which is the sentence the #405 landing exists to carry.
#:
#: Matched against :func:`collapsed` output, so the key is lowercase and the soft
#: wrap between "moves 1" and the arrow (CHANGELOG) is already flattened.
_SCHEMA_VERSION_MOVE: Final = re.compile(r"`findings_schema_version` (?:moves|moved) (\d+) → (\d+)")

#: One bullet of the ADR's landing note, opening on the residual it closes. Anchored
#: at the start of the collapsed item so a mention of ``#404`` inside another
#: bullet's prose cannot be counted as that residual's own entry.
_LANDING_BULLET: Final = re.compile(r"^- \*\*#(\d+) —")

#: Every key above that is matched against :func:`collapsed` prose, so the premise
#: test can refuse a capital before a pin reads a record. ``collapsed`` lowercases
#: both sides; a key carrying a capital matches no paragraph however intact the
#: record is, and the pin that reads it then reports the prose as deleted. A reader
#: acts on that by restoring text that is already there -- the failure
#: ``test_adr_0018_claims.py`` records paying for twice.
_COLLAPSED_KEYS: Final = {
    "CLOSURE_MARKER": CLOSURE_MARKER,
    "RESIDUALS_PARAGRAPH": RESIDUALS_PARAGRAPH,
    "LANDING_NOTE": LANDING_NOTE,
    # `LANDING_PR` reads a record too (the landing note must name `pull/492`), so it
    # belongs in the premise guard's population -- omitting it left one prose key a
    # future capital could break while the guard reported the record clean (#404
    # R1-9).
    "LANDING_PR": LANDING_PR,
    "PROBE_RESIDUAL[0]": PROBE_RESIDUAL[0],
    "PROBE_RESIDUAL[1]": PROBE_RESIDUAL[1],
    "_SCHEMA_VERSION_MOVE": _SCHEMA_VERSION_MOVE.pattern,
    "_LANDING_BULLET": _LANDING_BULLET.pattern,
}

#: A probe the shipped matrix does not carry, used to perturb the behaviour section.
#: It has to contain a column-0 keyed line, or :func:`keyed_lines` yields nothing
#: from it and the perturbation would be a tuple change the stamp never sees --
#: a test that passed for the wrong reason.
_UNSHIPPED_PROBE: Final = "Review-Finding: security HIGH — a probe this matrix does not carry"


# -- reading the records ------------------------------------------------------


def _blocks(text: str) -> tuple[str, ...]:
    """Every Markdown block of *text*, raw, in document order.

    A block runs between blank lines, which is what makes "the paragraph directly
    above" expressible at all. Raw rather than collapsed because the caller needs
    both: the collapsed form to match a key against, and the raw form to recognise
    a ``##`` heading and to slice a section out.
    """
    return tuple(block for block in re.split(r"\n[ \t]*\n", text) if block.strip())


def _the_one_block_carrying(blocks: tuple[str, ...], key: str, *, record: str) -> int:
    """The index of the single block matching *key*, or a failure that says which way.

    Zero and many fail differently on purpose. Zero means the anchor was rewritten
    past itself and every assertion downstream is about to pass over nothing; many
    means the anchor stopped identifying one paragraph, so an ordering assertion
    would be reporting on whichever copy the scan reached first.
    """
    matches = [index for index, block in enumerate(blocks) if key in collapsed(block)]

    assert len(matches) == 1, (
        f"{record} is not findable as exactly one paragraph keyed on `{key}`: found {len(matches)}"
    )
    return matches[0]


def _section_from(blocks: tuple[str, ...], start: int) -> str:
    """The blocks from *start* up to the next top-level heading, joined.

    The landing note is a paragraph followed by a bullet list, and both belong to
    the claim; stopping at ``##`` is what keeps the following amendment out of it.
    """
    end = next(
        (index for index in range(start + 1, len(blocks)) if blocks[index].startswith("## ")),
        len(blocks),
    )
    return "\n\n".join(blocks[start:end])


def _bullets(text: str) -> tuple[str, ...]:
    """Every top-level Markdown list item of *text*, soft wraps flattened, one per entry.

    ``test_adr_0027_claims.py``'s ``_list_items`` with one difference that its
    records do not need: **a blank line does not end an item.** A CHANGELOG entry
    here is routinely two or three paragraphs under one ``- ``, and an extractor
    that closed at the first blank line would read only each entry's opening
    paragraph -- so the ``FINDINGS_SCHEMA_VERSION`` sentence, which sits in the
    second paragraph of the #405 entry, would be outside every item and reported
    absent.

    An item opens on a line starting ``- `` and continues through blank lines and
    indented lines; it closes on the first unindented, non-blank line. Trailing
    blanks are held back rather than appended, so a block that never resumes does
    not drag the gap into the item -- immaterial after :func:`collapsed`, but it
    keeps the rule stateable.
    """
    items: list[str] = []
    current: list[str] | None = None
    pending: list[str] = []
    for line in text.splitlines():
        if line.startswith("- "):
            if current is not None:
                items.append(collapsed(" ".join(current)))
            current, pending = [line], []
        elif current is None:
            continue
        elif not line.strip():
            pending.append(line)
        elif line.startswith("  "):
            current.extend([*pending, line])
            pending = []
        else:
            items.append(collapsed(" ".join(current)))
            current, pending = None, []
    if current is not None:
        items.append(collapsed(" ".join(current)))
    return tuple(items)


def _changelog_section_carrying(key: str) -> str:
    """The one ``##`` section of the CHANGELOG that cites *key*.

    Found by the key rather than sliced at ``## [Unreleased]`` on purpose: the
    section is renamed to a version heading at release time, and a pin anchored to
    ``[Unreleased]`` would go RED on the release rather than on any record becoming
    false. Exactly one section must carry it, so a stray copy of the cite in an
    older release's entries fails loudly instead of making the scan ambiguous.
    """
    sections: list[list[str]] = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
    carrying = ["\n".join(section) for section in sections if key in "\n".join(section)]

    assert len(carrying) == 1, (
        f"the CHANGELOG does not carry `{key}` in exactly one `##` section: found {len(carrying)}"
    )
    return carrying[0]


def _schema_version_moves(text: str) -> tuple[tuple[int, int], ...]:
    """Every ``FINDINGS_SCHEMA_VERSION moves N -> M`` a record states, as ``(N, M)``."""
    return tuple(
        (int(source), int(target))
        for source, target in _SCHEMA_VERSION_MOVE.findall(collapsed(text))
    )


# -- reading the code ---------------------------------------------------------


def _method(module_path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    """The AST of one method, or a failure naming the one that went missing.

    Parsed from the file rather than reached through :func:`inspect.getsource`
    because the pins over it are about statements the method contains, and a
    rename of either the class or the method has to fail here rather than leave a
    scan looking at nothing.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    pytest.fail(f"{module_path.name} defines no {class_name}.{method_name} to read")


def _calls_to(node: ast.AST, module: str, attribute: str) -> list[ast.Call]:
    """Every ``module.attribute(...)`` call inside *node*."""
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == attribute
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == module
    ]


def _is_self_path(node: ast.expr) -> bool:
    """Whether *node* is exactly ``self._path``, the store's published name."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _names_loaded_in(node: ast.AST) -> set[str]:
    """Every bare name read inside *node*."""
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


# -- the premise --------------------------------------------------------------


def test_every_prose_key_here_can_match_the_collapsed_records_at_all() -> None:
    """RED means a key carries a capital, so the pin reading it reports the wrong cause.

    The premise, asserted before any pin reads a record. :func:`collapsed`
    lowercases what it is given and the keys are compared against its output, so a
    capital in a key matches no paragraph however intact the record is -- and the
    pin that reads it then fails saying the prose was deleted or gutted. A reader
    acts on that by restoring text that is already there.

    ``test_adr_0018_claims.py`` records paying for this twice, once when the guard
    covered only one constant family and a capital elsewhere produced a report of
    prose drift over a record nothing had touched. Every key of this module is in
    :data:`_COLLAPSED_KEYS`, regex patterns included.
    """
    uppercased = {name: key for name, key in _COLLAPSED_KEYS.items() if key != key.lower()}

    assert not uppercased, (
        f"these keys are matched against lowercased prose and can never match: {uppercased}"
    )


# -- 1. the residuals paragraph is closed, not standing -----------------------


def test_the_closure_marker_stands_immediately_above_the_residuals_it_closes() -> None:
    """RED means a reader meets four present-tense falsehoods with nothing marking them.

    The ADR keeps the slice-2 residuals paragraph **as written** -- it records what
    slice-2 shipped -- so the record is made true by position rather than by
    wording. Adjacency is the assertion: a marker that drifts to another section,
    or is deleted, leaves the paragraph asserting that the write is not atomic,
    that ``committed_at`` is not chronological, that ``PARSER_STAMP`` reaches no
    mechanic and that ``%b`` can drop a folded trailer -- four claims #492
    falsified, standing alone in a durable architectural record.

    Not a substring search over the file. Both paragraphs are isolated as exactly
    one block each first, because "immediately above" is meaningless against a scan
    that found two candidates and read the first.
    """
    blocks = _blocks(ADR_0029.read_text(encoding="utf-8"))

    marker = _the_one_block_carrying(blocks, CLOSURE_MARKER, record="ADR-0029's closure marker")
    residuals = _the_one_block_carrying(
        blocks, RESIDUALS_PARAGRAPH, record="ADR-0029's slice-2 residuals paragraph"
    )

    assert marker + 1 == residuals, (
        f"ADR-0029's closure marker is no longer the paragraph directly above the "
        f"residuals it closes (marker at block {marker}, residuals at block {residuals}), "
        f"so the four residual claims read as current"
    )


def test_the_adr_carries_the_492_landing_note_below_the_residuals_it_closes() -> None:
    """RED means the closure marker points at a note that is gone or above its subject.

    The marker says "see *Landed in #492* below it", so the note has to exist, has
    to be reachable -- the PR as a link, not a bare ``#492`` -- and has to be below.

    Held to *below* rather than to *directly below*, deliberately. A paragraph
    inserted between the two makes no record false and the marker's own wording
    still holds; pinning adjacency there would go RED on an edit this module has no
    claim about. The adjacency that does matter is the marker's, above.
    """
    blocks = _blocks(ADR_0029.read_text(encoding="utf-8"))

    residuals = _the_one_block_carrying(
        blocks, RESIDUALS_PARAGRAPH, record="ADR-0029's slice-2 residuals paragraph"
    )
    landing = _the_one_block_carrying(blocks, LANDING_NOTE, record="ADR-0029's #492 landing note")

    assert landing > residuals, (
        f"ADR-0029's landing note sits above the residuals paragraph it closes "
        f"(note at block {landing}, residuals at block {residuals}), so the marker's "
        f"`see *Landed in #492* below it` points the wrong way"
    )
    assert LANDING_PR in blocks[landing], (
        f"ADR-0029's landing note no longer names `{LANDING_PR}`, so a reader cannot "
        f"reach the change that closed the residuals"
    )


def test_the_landing_note_closes_each_of_the_four_residuals_in_its_own_entry() -> None:
    """RED means a residual lost its entry, or four were rolled into one claim.

    Per residual, because that is what the note asserts: each was a separate gap
    between a decision and its implementation, and a roll-up entry saying "all four
    are fixed" would leave a reader unable to check any one of them. The set is
    held to :data:`RESIDUAL_ISSUES` by **equality**, so a fifth entry fails as
    loudly as a missing one -- a residual added to the note without being added to
    this module's population would otherwise ride along unread.
    """
    blocks = _blocks(ADR_0029.read_text(encoding="utf-8"))
    landing = _the_one_block_carrying(blocks, LANDING_NOTE, record="ADR-0029's #492 landing note")

    closed = [
        match.group(1)
        for item in _bullets(_section_from(blocks, landing))
        if (match := _LANDING_BULLET.match(item)) is not None
    ]

    assert sorted(closed) == sorted(RESIDUAL_ISSUES), (
        f"ADR-0029's landing note does not close exactly the four residuals #492 "
        f"closed, one entry each: {sorted(closed)}"
    )


def test_the_changelog_records_each_residual_as_its_own_entry_against_pr_492() -> None:
    """RED means the release notes stopped accounting for one of the four residuals.

    The CHANGELOG is the record a consumer reads, and its claim is four separate
    Fixed entries -- one per residual -- each citing the pull request that carried
    it. Entries citing more than one residual are excluded from the count on
    purpose: the Documentation entry cites all four because it describes the ADR
    correction, and counting it would let a single roll-up bullet satisfy the whole
    population.

    Found by the PR cite rather than under ``## [Unreleased]``, so the pin survives
    the release rename and fails only when a record does -- see
    :func:`_changelog_section_carrying`.
    """
    section = _changelog_section_carrying(LANDING_PR)

    entries = [item for item in _bullets(section) if LANDING_PR in item]
    per_residual = [
        cited
        for item in entries
        if len(cited := [issue for issue in RESIDUAL_ISSUES if f"issues/{issue}" in item]) == 1
    ]

    assert sorted(issue for [issue] in per_residual) == sorted(RESIDUAL_ISSUES), (
        f"the CHANGELOG section citing `{LANDING_PR}` does not carry one entry per "
        f"residual: {sorted(issue for [issue] in per_residual)}"
    )


# -- 2. the schema version the records spell (#405) ---------------------------


def test_the_changelog_spells_the_schema_version_this_release_ships() -> None:
    """RED means the release notes state a schema version the release does not ship.

    The fact half of #405, over the record that describes *this release*. Both
    numbers are read out of the entry and held against the live constant: the
    target must equal it, and the source must be one below, so a record that kept
    the figure while the code moved -- or moved the figure while the code did not --
    fails naming which side it read.

    This is the ``test_adr_0018_claims.py`` write-method-count shape: two
    independently written things asserted equal, rather than a pin that reads the
    number out of the code and is therefore green for whatever the code says.

    A bump to 3 makes this a **defect in the entry**, not a stale-but-true note: a
    release that moves the constant 1 -> 3 while its own notes say 1 -> 2 is a false
    release note, and the remedy is to correct the entry.
    """
    moves = _schema_version_moves(_changelog_section_carrying(LANDING_PR))

    assert moves, (
        f"the CHANGELOG section citing `{LANDING_PR}` no longer states the "
        f"FINDINGS_SCHEMA_VERSION move, so nothing in the release notes says the "
        f"stored `committed_at` encoding changed"
    )
    assert all(target == FINDINGS_SCHEMA_VERSION for _source, target in moves), (
        f"the CHANGELOG says FINDINGS_SCHEMA_VERSION reaches {[t for _s, t in moves]}, "
        f"but the release ships {FINDINGS_SCHEMA_VERSION}"
    )
    assert all(source == FINDINGS_SCHEMA_VERSION - 1 for source, _target in moves), (
        f"the CHANGELOG says FINDINGS_SCHEMA_VERSION moves from "
        f"{[s for s, _t in moves]}, which is not one below the shipped "
        f"{FINDINGS_SCHEMA_VERSION}"
    )


def test_the_adr_landing_note_spells_the_schema_version_the_committed_at_fix_reached() -> None:
    """RED means ADR-0029 and the constant disagree about where the version stands.

    The same equality over the durable record. It is a separate test from the
    CHANGELOG's because a future RED means something different here: the landing
    note is anchored to #492, so "moved 1 -> 2 for this" stays true as history and
    the remedy on the next bump is to **append the next move**, not to rewrite this
    one and not to delete the pin.

    Recording that choice is the point. A constant that bumps with neither record
    touched is the silent divergence this file set exists for, and a RED that names
    the decision is what converts it into one.
    """
    blocks = _blocks(ADR_0029.read_text(encoding="utf-8"))
    landing = _the_one_block_carrying(blocks, LANDING_NOTE, record="ADR-0029's #492 landing note")

    moves = _schema_version_moves(_section_from(blocks, landing))

    assert moves, (
        "ADR-0029's landing note no longer states the FINDINGS_SCHEMA_VERSION move, "
        "so the record of why the version bumped without a DDL change is gone"
    )
    assert all(
        (source, target) == (FINDINGS_SCHEMA_VERSION - 1, FINDINGS_SCHEMA_VERSION)
        for source, target in moves
    ), (
        f"ADR-0029's landing note states FINDINGS_SCHEMA_VERSION moving {moves} while "
        f"the code carries {FINDINGS_SCHEMA_VERSION}; if the constant has bumped again, "
        f"append that move to the landing note rather than restating this one"
    )


# -- 3. the stamp's behaviour section, and the residual on it (#406) ----------


def test_both_records_state_the_406_probe_residual_rather_than_claiming_it_closed() -> None:
    """RED means a stated residual quietly became a closure claim.

    #406's behaviour section is exact only for the mechanics its probes
    distinguish, and both records say so. That sentence is what stops a reader
    concluding the stamp is total over the grammar -- which would make an unmoved
    stamp mean "the parser is unchanged" when it can also mean "no probe separates
    the change that was made".

    Two fragments per record rather than one sentence: the records word the middle
    clause differently and neither is wrong about it, while a note cut down to "the
    residual is stated rather than closed" alone would read as a closure that
    merely hedges. Both halves have to survive.
    """
    blocks = _blocks(ADR_0029.read_text(encoding="utf-8"))
    landing = _the_one_block_carrying(blocks, LANDING_NOTE, record="ADR-0029's #492 landing note")
    records = {
        "ADR-0029's landing note": collapsed(_section_from(blocks, landing)),
        f"the CHANGELOG section citing `{LANDING_PR}`": collapsed(
            _changelog_section_carrying(LANDING_PR)
        ),
    }

    missing = {
        f"{record}: {fragment!r}"
        for record, text in records.items()
        for fragment in PROBE_RESIDUAL
        if fragment not in text
    }

    assert not missing, (
        f"#406's stated residual is no longer carried whole, so the parser stamp "
        f"reads as total over the grammar: {sorted(missing)}"
    )


def test_the_behaviour_section_the_406_residual_speaks_of_is_driven_by_the_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED means the residual describes a probe matrix the stamp does not consult.

    The fact half, and it drives the section rather than reading it, because the
    residual sentence is *about* the probes: if the behaviour section were dropped
    from the stamp, both records would carry a caveat about a mechanism that no
    longer exists, and every string pin above would stay green.

    Perturbed in **both** directions on purpose. A probe added must move the stamp;
    the tuple emptied must move it too. A ``_compute_parser_stamp`` that stopped
    hashing the probe verdicts returns the baseline for both, so neither leg alone
    is a control and together they are one.

    ``_STAMP_PROBES`` is not among the six constants
    ``test_review_finding.py::test_the_parser_stamp_changes_when_any_grammar_element_changes``
    perturbs, so this closes a gap that module leaves rather than repeating it. What
    the probes *distinguish* is that module's
    ``test_the_parser_stamp_moves_when_a_parser_mechanic_widens``.
    """
    assert review_finding._STAMP_PROBES, (
        "the stamp's probe matrix is empty, so the residual both records state -- "
        "`a widening no probe separates leaves the stamp still` -- is true of every "
        "widening there is"
    )
    baseline = review_finding._compute_parser_stamp()

    monkeypatch.setattr(
        review_finding, "_STAMP_PROBES", (*review_finding._STAMP_PROBES, _UNSHIPPED_PROBE)
    )
    with_a_probe_added = review_finding._compute_parser_stamp()
    monkeypatch.setattr(review_finding, "_STAMP_PROBES", ())
    with_no_probes = review_finding._compute_parser_stamp()

    assert with_a_probe_added != baseline, (
        "adding a probe left PARSER_STAMP unchanged: the behaviour section does not "
        "reach the probe matrix, so #406's stated residual describes a mechanism the "
        "stamp does not have"
    )
    assert with_no_probes != baseline, (
        "emptying the probe matrix left PARSER_STAMP unchanged: the stamp carries no "
        "behaviour section at all, which is the pre-#406 shape both records say was left"
    )


def test_the_extraction_rule_the_stamp_measures_is_the_one_the_git_adapter_reads() -> None:
    """RED means ``keyed_lines`` went back to being grammar the adapter owns privately.

    ADR-0029's #406 entry says the extraction rule "moved into the domain as
    ``keyed_lines`` to make that possible: it was grammar the git adapter owned
    privately, and therefore unreachable to the stamp". Two things make that true
    and both are held: the rule is defined in the domain, and the adapter uses
    **that object**.

    The second is the one that catches the shape returning with the symbol name
    intact. A private ``keyed_lines`` re-defined in ``trailer_source`` would leave
    the domain's copy in the stamp measuring a rule nothing reads, and every other
    pin in this module -- and the stamp's own drivers -- would stay green.

    Reached through :func:`getattr` rather than as an attribute so that an adapter
    which stops importing the rule altogether fails *here*, with this message,
    instead of raising ``AttributeError`` from a line that reads like a typo.
    """
    adapters_rule = getattr(trailer_source, "keyed_lines", None)

    assert keyed_lines.__module__ == "theurian.domain.review_finding", (
        f"the trailer extraction rule is no longer domain grammar; it is defined in "
        f"{keyed_lines.__module__}, where PARSER_STAMP cannot reach it (#406)"
    )
    assert adapters_rule is keyed_lines, (
        f"the git adapter no longer reads the domain's extraction rule, so the stamp "
        f"measures a rule the load does not use (#406): {adapters_rule!r}"
    )


# -- 4. the two mechanisms the records name (#404, #410) ----------------------


def test_the_git_source_reads_the_whole_message_the_records_name() -> None:
    """RED means the trailer population is back to ``%b``'s body (#410).

    Both records say the population is the whole commit message: git's ``%b``
    excludes the first *paragraph* rather than the first line, so a column-0 trailer
    folded into an unseparated subject reached neither tuple of the load.

    Both directions are asserted, and the second is not the first restated. ``%B``
    must be present, or a format that carries no message at all would satisfy "does
    not read ``%b``" while reading nothing; ``%b`` must be absent, or a format
    carrying both would read the body twice and satisfy "reads ``%B``".

    The reference check is what stops this being a pin over a constant nothing
    reads: ``_FORMAT`` has to be loaded inside the one method that builds the argv.
    What the whole-message population then *does* is
    ``test_git_trailer_source.py::test_a_trailer_folded_into_the_subject_paragraph_is_accounted``.
    """
    git_log = _method(TRAILER_SOURCE_MODULE, "GitTrailerFindingSource", "_git_log")

    assert "%B" in trailer_source._FORMAT, (
        f"the git source's format no longer carries `%B`, so the trailer population "
        f"is not the whole commit message: {trailer_source._FORMAT!r}"
    )
    assert "%b" not in trailer_source._FORMAT, (
        f"the git source's format carries `%b` again, which excludes the first "
        f"paragraph and drops a trailer folded into an unseparated subject (#410): "
        f"{trailer_source._FORMAT!r}"
    )
    assert "_FORMAT" in _names_loaded_in(git_log), (
        "`_FORMAT` is not read by `GitTrailerFindingSource._git_log`, so the constant "
        "the assertions above hold is not the format git is given"
    )


def test_the_store_publishes_by_renaming_the_file_it_built_onto_the_live_name() -> None:
    """RED means the rebuild writes under the published name again (#404).

    The shape both records claim: assemble at a ``.building`` sibling, publish with
    ``os.replace``, never write under the live name. Read from the AST rather than
    by running a build, because what is claimed is the shape -- and because the
    behaviour it buys is already driven by
    ``test_findings_store.py::test_a_reader_polling_through_a_rebuild_sees_only_whole_stores``
    and ``test_findings_build_cli.py::
    test_concurrent_builds_all_succeed_and_leave_one_complete_store``.

    Three assertions that only mean something together. Exactly one ``os.replace``,
    so a second rename cannot make the pin ambiguous about which one publishes; its
    destination is ``self._path``; and its source is the same local that
    ``sqlite3.connect`` was handed. That last equality is what refuses the pre-#404
    shape -- connect on the live path, no rename -- while keying on neither the
    local's name nor the suffix, both of which an ordinary edit may move.
    """
    replace_all = _method(FINDINGS_STORE_MODULE, "SqliteReviewFindingStore", "replace_all")

    renames = _calls_to(replace_all, "os", "replace")
    connects = _calls_to(replace_all, "sqlite3", "connect")

    assert len(renames) == 1, (
        f"`SqliteReviewFindingStore.replace_all` makes {len(renames)} `os.replace` "
        f"calls; the publish is meant to be the one atomic primitive (#404)"
    )
    assert len(connects) == 1, (
        f"`SqliteReviewFindingStore.replace_all` opens {len(connects)} sqlite "
        f"connections; the pin below cannot say which file is being assembled"
    )
    assert _is_self_path(renames[0].args[1]), (
        f"the rebuild's `os.replace` no longer publishes onto `self._path` but onto "
        f"`{ast.unparse(renames[0].args[1])}`, so the published name is not what the "
        f"whole store is moved to (#404)"
    )
    assert isinstance(connects[0].args[0], ast.Name) and isinstance(renames[0].args[0], ast.Name), (
        f"the rebuild no longer assembles at a named working path: it connects to "
        f"`{ast.unparse(connects[0].args[0])}` and renames "
        f"`{ast.unparse(renames[0].args[0])}`"
    )
    assert connects[0].args[0].id == renames[0].args[0].id, (
        f"the rebuild renames `{ast.unparse(renames[0].args[0])}` but assembles at "
        f"`{ast.unparse(connects[0].args[0])}`; the file sqlite writes is not the file "
        f"published, so the store can be built under the live name again (#404)"
    )


def test_the_building_sibling_shares_a_directory_with_the_name_it_publishes_onto() -> None:
    """RED means the rename can degrade into a copy across a device boundary (#404).

    ADR-0029's #404 entry gives this as the reason the working name is a *sibling*
    rather than a temporary directory: ``os.replace`` is atomic only within a
    filesystem. So the property the record rests on is a path derivation, and it is
    asserted as one -- from a real store on a path that need not exist, which is why
    this stays a unit test and touches no disk.

    Containment against the project root is a different claim with a different
    owner: ``test_findings_store.py::test_the_building_sibling_stays_inside_the_state_directory``.
    """
    store = SqliteReviewFindingStore(Path("/nonexistent/state/findings.db"))

    assert store.building_path.parent == store.path.parent, (
        f"the working name is no longer a sibling of the published one "
        f"({store.building_path} vs {store.path}), so `os.replace` can cross a "
        f"filesystem and stop being atomic (#404)"
    )
    assert store.building_path != store.path, (
        "the working name and the published name are the same path, so the rebuild "
        "writes under the live name (#404)"
    )


def test_the_shipped_findings_build_hands_the_builder_the_projects_write_lock() -> None:
    """RED means the rebuild's critical section is a lock nothing takes (#404).

    Both records say ``theurian findings build`` holds the project's ``write_lock``
    across the whole store write. Two halves, and the signature alone is the half
    that cannot fail: ``write_section`` defaults to ``nullcontext``, so a builder
    whose parameter exists and whose caller stopped passing a lock serialises
    nothing while every type and every unit test stays green.

    So the composition root is read too. It must construct the builder with
    ``write_section=`` and the expression must name ``write_lock`` -- the project's
    one writer (ADR-0018), not some other context manager. Keyword-only is asserted
    because that is what makes the argument unmistakable at the call site rather
    than a third positional a refactor could silently reorder.

    That the hold is *continuous*, entered once around ``replace_all`` with the git
    read outside it, is
    ``test_findings_builder.py::test_the_build_publishes_inside_one_continuous_write_section``.
    That the lock is a *real* one that actually excludes a second writer -- the
    behavioural half this AST pin cannot reach, and where a "different lock file"
    mutation goes RED rather than passing a substring check -- is
    ``test_findings_build_cli.py::test_findings_build_blocks_on_the_projects_write_lock_held_by_another_writer``
    (#404 R1-6).
    """
    parameter = inspect.signature(FindingsBuilder.__init__).parameters.get("write_section")
    tree = ast.parse(FINDINGS_COMMANDS_MODULE.read_text(encoding="utf-8"))
    constructions = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        if call.func.id == "FindingsBuilder"
    ]

    assert parameter is not None and parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"`FindingsBuilder.__init__` takes no keyword-only `write_section`, so the "
        f"rebuild has no critical section to hold: {parameter}"
    )
    assert len(constructions) == 1, (
        f"`cli/findings_commands.py` builds {len(constructions)} `FindingsBuilder`s; "
        f"the pin below cannot say which one the command ships"
    )
    passed = [
        ast.unparse(keyword.value)
        for keyword in constructions[0].keywords
        if keyword.arg == "write_section"
    ]

    assert passed and all("write_lock" in expression for expression in passed), (
        f"`theurian findings build` no longer hands the builder the project's write "
        f"lock, so `write_section` falls back to `nullcontext` and two rebuilds "
        f"assemble at one working name (#404): {passed}"
    )
