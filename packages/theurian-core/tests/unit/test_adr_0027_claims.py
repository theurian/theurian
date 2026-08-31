"""What ADR-0027 says the write lock is taken on, against what ``ProjectPaths`` derives.

One clause is held here, in both directions. ADR-0027's decision-2 residue --
the bullet conceding that two ``accept`` invocations racing at the process level
stay deferred -- restates ADR-0018's Milestone 1 mechanism in order to reason
about the accept path's file moves, and it described that mechanism as "an OS
advisory file lock **on the state database**".

It never was. ``ProjectPaths.write_lock`` is ``.theurian/runtime/write.lock``
and ``ProjectPaths.database_for`` puts the databases under ``.theurian/state/``,
so ``write_transaction(database_path, lock_path)`` flocks a file that is not a
database. https://github.com/theurian/theurian/issues/432 corrected the original
clause in ADR-0018; this copy was left naming the old object for a further PR,
so the two ADRs disagreed about what the lock is taken on until
https://github.com/theurian/theurian/issues/433 narrowed this one the same way.

**Two bullets in this document concede that race, and only one of them is the
clause above.** The one held here is the residue *inside* Decision 2, which
spells the mechanism out ("ADR-0018 makes single-writer a contract ... enforced
in Milestone 1 by an OS advisory file lock on ..."); that opening is what
:data:`RESTATES_ADR_0018` keys on, and :func:`_decision_two_residue` requires it
to match exactly one item. The other is the later "Concurrency between two
``accept`` invocations" entry in the not-closed-here list, which hands the
mechanism to ADR-0018 by reference ("the advisory lock ADR-0018 point 2
describes") and names no lock object of its own. Nothing in that one can carry
the retracted attachment, and nothing here reads it.

**A copied claim is the failure mode, so the pin is shared rather than copied.**
``LOCK_PATH``, ``STATE_DIR``, :func:`find_lock_on_database` and the one
derivation that holds them against ``ProjectPaths`` live in
``write_lock_claims.py``; ``test_adr_0018_claims.py`` and this module both
import them. Move the lock in the code and both records go RED together -- which
is precisely what did not
happen when #432 corrected one document and left its copy standing. What is
*not* shared is the clause isolation: each pin finds its own document's clause,
because a document-wide scan for the retracted wording would go RED on the
correction note that quotes it.

**What this module enforces.**

- The residue bullet still says the lock is taken on a separate lock file, still
  names ``.theurian/runtime/write.lock`` and the ``.theurian/state/`` it guards,
  and still cites ADR-0018 as the record it is restating. The citation is
  asserted because it is what makes this a restatement rather than an
  independent claim free to drift again.
- The bullet does not reattach the lock to a database, in the wording that
  attachment has actually taken.
- Those two path strings are what ``ProjectPaths`` derives, and the lock does
  not share a directory or a filename with a state database.

**What it deliberately does not.**

- It does **not** prove a lock is taken, or taken on that file. That is a
  property of ``infrastructure/sqlite/connection.py``, held by its own tests;
  ``test_adr_0018_claims.py`` disclaims the same about its half of the same
  derivation. This module would stay green against a build that computed the
  right lock path and never used it.
- It says nothing about the *rest* of the bullet, which is the part ADR-0027 is
  actually deciding: that the accept path's file moves are not under that lock,
  and that the replay lengthens the examine-to-move interval. Neither is pinned
  here, and neither was touched by #433.
- It reads one clause. ADR-0027's later cross-reference to "the advisory lock
  ADR-0018 point 2 describes" names no object, was correct as it stood and is
  out of scope -- as is every other paragraph of the file, including any future
  note that quotes the retracted phrase to explain it.
- The prose halves are regression pins over the wording this claim has taken,
  not closure arguments. A rule that pins grammar always has a next grammar.
"""

from __future__ import annotations

import pathlib
from typing import Final

from write_lock_claims import (
    LOCK_PATH,
    REPO_ROOT,
    STATE_DIR,
    assert_the_lock_and_the_state_databases_resolve_apart,
    collapsed,
    find_lock_on_database,
)

ADR_0027 = REPO_ROOT / "docs" / "adr" / "0027-accept-validates-before-it-moves.md"

#: How the residue bullet is found: by the clause it restates, not by the words
#: it opens with. Keyed on the sentence that hands the mechanism to ADR-0018, so
#: reordering the residues list or rewriting the bullet's first line does not
#: silently narrow this module to nothing -- and so that a bullet which stops
#: citing ADR-0018 fails to be found rather than passing as clean.
RESTATES_ADR_0018: Final = "adr-0018 makes single-writer a contract"

#: The mechanism phrase the correction landed. Asserted together with the two
#: paths because either alone is what the record used to be: the retracted
#: sentence named a mechanism ("an OS advisory file lock") and attached it to the
#: wrong object, so a rewrite that keeps the mechanism and drops the path is the
#: same defect with the evidence removed.
SEPARATE_LOCK_FILE: Final = "a separate lock file"


def _list_items(text: str) -> list[str]:
    """Every top-level Markdown list item, soft wraps flattened, one per entry.

    The residue is one bullet wrapped over eleven lines, so a scan that stops at
    every newline never sees the clause whole -- ``lock on\\n  a separate lock
    file`` spans a line break. A scan that ignores newlines entirely reads the
    next bullet into this one, which would let the retracted attachment sit in a
    neighbouring residue and still be reported as absent from this one.

    An item runs from a line starting with ``- `` until the first line that is
    blank or unindented. Nested bullets are indented, so they stay part of their
    parent rather than opening an item of their own; that is deliberate, because
    the clause this module reads has no nesting and a nested-item model would be
    machinery with no test behind it.
    """
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("- "):
            if current is not None:
                items.append(collapsed(" ".join(current)))
            current = [line]
        elif current is not None:
            if line.strip() and line.startswith("  "):
                current.append(line)
            else:
                items.append(collapsed(" ".join(current)))
                current = None
    if current is not None:
        items.append(collapsed(" ".join(current)))
    return items


def _decision_two_residue(text: str) -> str:
    """The residue bullet that restates ADR-0018's lock, as one collapsed item.

    Isolated rather than scanned for across the document, for the reason the
    ADR-0018 module records about its own clause: a note explaining a correction
    quotes the retracted wording, and a file-wide scan reads the fix as the
    defect returning.

    Exactly one item must match. Zero means the bullet was rewritten past its own
    citation and this module is about to pass over nothing; more than one means
    the key no longer identifies a single clause and the negative test below
    would be reporting on text it was never scoped to.
    """
    residues = [item for item in _list_items(text) if RESTATES_ADR_0018 in item]

    assert len(residues) == 1, (
        f"ADR-0027's decision-2 residue is not findable as exactly one list item "
        f"keyed on `{RESTATES_ADR_0018}`: found {len(residues)}"
    )
    return residues[0]


# -- The prose: ADR-0027's decision-2 residue --------------------------------


def test_adr_0027_names_the_separate_lock_file_and_the_state_directory_it_guards() -> None:
    """RED means the residue stopped naming the file ADR-0018's lock is taken on.

    The positive half. It is not the negative one restated: a rewrite that drops
    the mechanism entirely, or that softens it to "ADR-0018 makes single-writer a
    contract the accept path is outside of", makes no false claim and would pass
    :func:`test_adr_0027_does_not_reattach_the_write_lock_to_a_database` while
    leaving the reader of *this* document with no way to check the object the
    lock is taken on -- which is the state it was in before #433.

    ``STATE_DIR`` is required too. The bullet says what the lock *guards*, and
    that half is what makes "separate" mean something rather than merely "not
    here".
    """
    residue = _decision_two_residue(ADR_0027.read_text(encoding="utf-8"))

    assert SEPARATE_LOCK_FILE in residue, (
        f"ADR-0027's decision-2 residue no longer says the lock is taken on `{SEPARATE_LOCK_FILE}`"
    )
    assert LOCK_PATH in residue, f"ADR-0027's decision-2 residue no longer names `{LOCK_PATH}`"
    assert STATE_DIR in residue, (
        f"ADR-0027's decision-2 residue no longer names the `{STATE_DIR}` the lock guards"
    )


def test_adr_0027_does_not_reattach_the_write_lock_to_a_database() -> None:
    """RED means the retracted attachment is back in ADR-0027's residue.

    The negative half, and it catches what the positive one cannot: a residue
    that names the lock file and *also* says the lock is taken on the database.
    That is not hypothetical -- it is how ADR-0018 itself read for a whole
    milestone, point 2 attaching the lock to the database while the Negative
    consequence below it named both paths correctly, and it is how this document
    read for the interval between #432 and #433.

    Scoped to the residue. A scan of the whole file would report any future note
    that quotes the retracted phrase to explain the correction as the correction
    coming undone.
    """
    residue = _decision_two_residue(ADR_0027.read_text(encoding="utf-8"))

    attachments = find_lock_on_database(residue)

    assert not attachments, (
        f"ADR-0027's decision-2 residue attaches the write lock to a database again: {residue}"
    )


def test_the_reattachment_scan_still_fires_on_the_wording_adr_0027_carried() -> None:
    """RED means the shared scan stopped matching, so the test above passes over nothing.

    The one assertion here driven by synthetic input rather than by the shipped
    document, and it exists because the shipped document cannot drive it: the
    scan's whole point is that it finds nothing today, so a
    :func:`find_lock_on_database` gutted to match nothing at all would leave every
    other test in this module and in ``test_adr_0018_claims.py`` green. Both now
    depend on that one function, so nothing else in the suite would notice.

    The sample is the sentence ADR-0027 actually carried until #433, quoted from
    the pre-correction file, and a second phrasing without the "state" qualifier
    -- the two shapes the window inside the scan is sized for.
    """
    as_shipped = collapsed(
        "ADR-0018 makes single-writer a contract in the application layer, "
        "enforced in Milestone 1 by an OS advisory file lock on the state "
        "database — and the accept path's file moves are not under that lock."
    )
    without_the_qualifier = collapsed("the writer takes an advisory lock on the database")

    assert find_lock_on_database(as_shipped), (
        "the shared scan no longer matches the sentence ADR-0027 carried before #433"
    )
    assert find_lock_on_database(without_the_qualifier), (
        "the shared scan no longer matches a lock attached to `the database`"
    )


def test_the_reattachment_scan_normalises_case_and_line_wraps_itself() -> None:
    """RED means the scan is back to requiring a normalisation it never stated.

    The driving test for :func:`find_lock_on_database`'s own contract, and it is
    driven by synthetic input because no caller in this repository can drive it:
    every one of them happens to hand in text that :func:`collapsed` has already
    flattened, which is exactly what kept the missing precondition invisible.
    The pattern behind the function is a lowercase, single-spaced rule, so
    sentence case alone -- "an OS advisory file **L**ock on the **S**tate
    **D**atabase" -- and a soft wrap between "on the" and "state database" each
    returned no match, and a pin fed raw document text reported a clean record it
    had never read.

    Both perturbations are asserted separately. A function that lowercased but
    did not flatten, or flattened but did not lowercase, would satisfy one and
    fail the record on the other.
    """
    sentence_cased = "An OS Advisory File Lock On The State Database."
    soft_wrapped = "enforced by an OS advisory file lock on the\n   state database."

    assert find_lock_on_database(sentence_cased), (
        "the scan no longer normalises case, so the same claim escapes it "
        "sentence-cased -- which is how a Markdown heading or a title-cased "
        "summary line would carry it"
    )
    assert find_lock_on_database(soft_wrapped), (
        "the scan no longer flattens soft wraps, so a caller handing in raw "
        "document text gets a clean report from a rule that never saw the clause"
    )


# -- The fact: what `ProjectPaths` derives ------------------------------------


def test_the_lock_adr_0027_names_resolves_outside_the_state_directory(
    tmp_path: pathlib.Path,
) -> None:
    """RED means the lock moved -- and ADR-0027's residue must move with ADR-0018's point.

    The fact half, and it is the *same* derivation ``test_adr_0018_claims.py``
    runs, called from here with this document's name in the failure message. The
    two documents naming one pair of objects is what let them disagree for a PR;
    one derivation asserted from both pins is what stops the next disagreement
    from being silent.

    It holds where the lock and the databases resolve to, and that they resolve
    apart. It does not prove a lock is taken on that file -- see the module
    docstring, and the same disclaimer on the ADR-0018 side.
    """
    assert_the_lock_and_the_state_databases_resolve_apart(
        tmp_path / "repo", record="ADR-0027's decision-2 residue"
    )
