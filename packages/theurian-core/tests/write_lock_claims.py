"""What the write lock is taken on, derived once for every record that names it.

**Three durable records name the same two objects; two of them are pinned here.**
ADR-0018 Decision point 2 says Milestone 1 enforces exclusivity with an OS
advisory file lock on a separate lock file, ``.theurian/runtime/write.lock``,
guarding the state databases under ``.theurian/state/``. ADR-0027's decision-2
residue restates that clause in order to reason about the accept path's file
moves. Both said the lock was taken **on the state database** until
https://github.com/theurian/theurian/issues/432 and
https://github.com/theurian/theurian/issues/433 corrected them -- separately,
because nothing tied the copy to the original.

The third is the **served-corpus twin**,
``.theurian/knowledge/architecture/single-writer-synchronous-in-m1.<ulid>.md:30``,
which still carries the retracted wording and is meant to: the dogfood corpus is
held byte-identical to its source anchor commit by
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit``,
so only a governed re-seed can move it, and that re-seed is #199 unit C. It is
outside both pins on purpose -- a scan that reached it would report the
governance guard doing its job as drift. Recorded here rather than left to a
reader who greps the tree for the old sentence, finds a third copy, and has to
guess which of the three is the defect.

**That is why the derivation lives here rather than inside either pin.**
``tests/unit/test_adr_0018_claims.py`` and ``tests/unit/test_adr_0027_claims.py``
each assert that their own document names :data:`LOCK_PATH` and
:data:`STATE_DIR`, and each calls
:func:`assert_the_lock_and_the_state_databases_resolve_apart` to hold those two
strings against what ``ProjectPaths`` actually derives. A copy of the derivation
in each module would go RED in whichever one its author remembered to update and
stay green in the other -- which is the exact shape of the defect #433 fixed. One
derivation, called twice, fails both records together.

The literals are written here **independently** and asserted *equal* to the
derivation rather than read out of it. A constant lifted from ``ProjectPaths``
would make both pins green for whatever the code happens to say, which is the
drift they exist to catch. This is the ``test_setup_claims.py`` shape.

**What this holds, and what it does not.** It holds where the lock file and the
state databases resolve to, and that they resolve apart. It does **not** prove a
lock is taken, or taken on that file: whether ``write_transaction`` flocks
``lock_path`` rather than the database is a property of
``infrastructure/sqlite/connection.py``, and the path arithmetic here would stay
green against a build that computed the right lock path and then never used it.

**Two primitives every claim pin needs live here too**, for the reason the
derivation does. ``REPO_ROOT`` and :func:`collapsed` were written out three
times, once per claim module, in a file set whose whole thesis is that a copied
claim drifts from its original. Neither is about the write lock; both are here
because this is the module the claim pins already import, and a fourth home would
be a fourth thing to keep in step.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from theurian.application.project_service import ProjectPaths
from theurian.domain.state import StateHash
from theurian.domain.values import ContentHash

#: The repository root, from this file's own location. ``parents[3]`` because
#: this module sits at ``packages/theurian-core/tests/`` -- a claim module under
#: ``tests/unit/`` that computed its own would need ``parents[4]``, and getting
#: that index wrong is silent: it yields a directory that exists, so the paths
#: built on it merely fail to be found.
REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: The lock file both records name, as a repository-relative POSIX path. Written
#: here independently of ``ProjectPaths`` -- see the module docstring for why
#: that matters -- and required in each document by that document's own prose
#: pin, so ADR-0018, ADR-0027, both pins and ``ProjectPaths`` are held to one
#: string.
LOCK_PATH: Final = ".theurian/runtime/write.lock"

#: The directory both records say the lock *guards*, held to ``ProjectPaths.state``
#: the same way. The trailing slash is how the documents write it, and it is
#: stripped before the comparison rather than being asserted of the filesystem.
STATE_DIR: Final = ".theurian/state/"

#: A throwaway state hash, only so ``database_for`` has an argument. Its value
#: never reaches an assertion: what is asserted is the *directory* the database
#: lands in and that the lock does not share it, neither of which depends on the
#: hash. The same constant and the same reasoning as
#: ``test_project_paths_containment.py``, which builds it identically.
_SAMPLE_STATE_HASH: Final = StateHash(ContentHash("a" * 64))

#: The retracted attachment: a lock taken *on* a database. The window admits the
#: markup the sentence carried (``file lock** on the state database``) and a few
#: words, and stops at a period so it cannot span sentences.
#:
#: Private, and reached through :func:`find_lock_on_database`, because the
#: pattern alone had an unstated precondition: it was case-sensitive over a
#: whitespace-collapsed lowercase string, so "an OS advisory file **L**ock on the
#: **S**tate **D**atabase" -- the same claim, sentence-cased -- returned no match
#: and every pin over it stayed green. The callers all happened to hand in
#: lowercased text, which is what made the defect invisible rather than absent.
#:
#: Measured escapes, recorded rather than chased: "on the SQLite file", "on the
#: state db", "against the database", "database-level lock". A rule that pins
#: grammar always has a next grammar.
_LOCK_ON_DATABASE: Final = re.compile(
    r"\block\b[^.]{0,30}?\bon the (?:state )?database\b", re.IGNORECASE
)


def collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces.

    Every prose pin in this file set needs it, because every clause it reads is
    soft-wrapped in its source: "an OS advisory file lock on the\\n   state
    database" is one claim written over two lines, and a search that does not
    flatten the wrap passes while the sentence it is watching sits there intact.
    """
    return " ".join(text.lower().split())


def find_lock_on_database(text: str) -> list[str]:
    """Every place *text* attaches the write lock to a database, normalised first.

    The normalisation is the point. :data:`_LOCK_ON_DATABASE` matches lowercased,
    whitespace-collapsed prose, and a caller that hands in raw document text gets
    a clean report from a scan that never had a chance -- a soft wrap or a capital
    letter is enough. Doing it here means the precondition cannot be forgotten by
    the next caller, which is the failure mode this whole module exists for.

    **What normalising does not remove is the scoping precondition.** Hand in one
    clause, not a whole document: ADR-0018 and ADR-0027 each quote the retracted
    phrase verbatim in the note that *records* the correction, so a file-wide call
    reports the fix as the defect returning. Each pin isolates its own clause --
    ``_decision_point_two`` and ``_decision_two_residue`` -- and calls this on
    that alone.
    """
    return _LOCK_ON_DATABASE.findall(collapsed(text))


def assert_the_lock_and_the_state_databases_resolve_apart(root: Path, *, record: str) -> None:
    """Hold :data:`LOCK_PATH` and :data:`STATE_DIR` to what ``ProjectPaths`` derives.

    Called by every pin over a document that names those two objects, with
    ``record`` naming the clause whose author has to move when this fails --
    "ADR-0018 Decision point 2", "ADR-0027's decision-2 residue". One derivation
    and one set of assertions, so a lock that moves fails every record at once
    rather than the one someone remembered.

    Derived from a real ``ProjectPaths`` rather than from path strings. No fake
    is needed and none is used: ``ProjectPaths.of`` resolves a root that does not
    have to exist, so a throwaway directory gives the genuine production
    derivation, containment checks included.

    The premises come first. ``runtime`` and ``state`` must be different
    directories before "the parents are disjoint" asserts anything, and both
    paths must sit under the root before ``relative_to`` can express them as the
    strings the documents name.
    """
    paths = ProjectPaths.of(root)
    database = paths.database_for(_SAMPLE_STATE_HASH)
    lock = paths.write_lock

    assert paths.runtime != paths.state, (
        "runtime and state resolve to one directory, so `separate lock file` "
        "would be true of nothing"
    )
    assert lock.is_relative_to(paths.root) and database.is_relative_to(paths.root), (
        "the lock or the database resolves outside the project root; the "
        "repository-relative wording in the records cannot describe that"
    )

    assert lock.relative_to(paths.root).as_posix() == LOCK_PATH, (
        f"the write lock is no longer `{LOCK_PATH}`, which {record} names: "
        f"{lock.relative_to(paths.root).as_posix()}"
    )
    assert paths.state.relative_to(paths.root).as_posix() == STATE_DIR.rstrip("/"), (
        f"the state databases no longer live under `{STATE_DIR}`, which {record} "
        f"names as what the lock guards"
    )
    assert lock.parent != database.parent, (
        f"the write lock now shares a directory with the state databases, so it is "
        f"no longer a separate lock file, and {record} says it is: {lock.parent}"
    )
    assert lock.name != database.name, (
        f"the write lock is named like a state database, and {record} calls it a "
        f"separate lock file: {lock.name}"
    )
