"""What the write lock is taken on, derived once for every record that names it.

Two durable records name the same two objects. ADR-0018 Decision point 2 says
Milestone 1 enforces exclusivity with an OS advisory file lock on a separate lock
file, ``.theurian/runtime/write.lock``, guarding the state databases under
``.theurian/state/``. ADR-0027's decision-2 residue restates that clause in order
to reason about the accept path's file moves. Both said the lock was taken **on
the state database** until https://github.com/theurian/theurian/issues/432 and
https://github.com/theurian/theurian/issues/433 corrected them -- separately,
because nothing tied the copy to the original.

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
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from theurian.application.project_service import ProjectPaths
from theurian.domain.state import StateHash
from theurian.domain.values import ContentHash

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
#: **Shared, and scoped by each caller to its own clause -- never to a whole
#: file.** Both documents quote the retracted phrase verbatim in the note that
#: records the correction, so a document-wide scan for this pattern would go RED
#: on the amendment that fixed the defect. Each pin isolates its own clause and
#: searches only that.
#:
#: Measured escapes, recorded rather than chased: "on the SQLite file", "on the
#: state db", "against the database", "database-level lock". A rule that pins
#: grammar always has a next grammar.
LOCK_ON_DATABASE: Final = re.compile(r"\block\b[^.]{0,30}?\bon the (?:state )?database\b")


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
