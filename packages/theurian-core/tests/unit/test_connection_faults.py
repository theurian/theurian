"""What an opener of the state database or its write lock is allowed to say (#526, #530).

`connection.py` names two kinds of thing it will not open: an artefact that is
not a regular file, and -- for the lock -- one it may not open. Both refusals
publish a *shape* ("a named pipe (FIFO)"), and that vocabulary already exists in
`security/paths.py::_unbounded_shape`, where SEC-8's byte cap uses it for a
`contentFile`. The two are separate functions on purpose (different layers,
different populations, neither refusal implying the other), and this module is
what keeps them from drifting into two phrasings for one fault.
"""

from __future__ import annotations

import stat

import pytest

from theurian.infrastructure.sqlite.connection import _shape_of
from theurian.security.paths import _unbounded_shape

pytestmark = pytest.mark.unit

#: Every file type `st_mode` can carry, as the `stat` module spells them, plus
#: the residual both functions are required to have. Derived from `stat`'s own
#: `S_IF*` constants rather than written out, so a type neither function names
#: still arrives here and is compared.
_FILE_TYPES = {
    name: getattr(stat, name)
    for name in dir(stat)
    if name.startswith("S_IF") and isinstance(getattr(stat, name), int)
}


def test_the_file_type_population_is_not_empty_and_holds_the_shapes_that_matter() -> None:
    """The premise, asserted before the comparison that rests on it.

    A comparison over an empty mapping agrees with everything, so a rename in
    `stat` that emptied the derivation above would report perfect agreement
    between two functions that had stopped agreeing. The three named here are the
    ones the two callers actually meet -- a FIFO at the lock path (#526), a FIFO
    or socket at the state-database path -- so their presence is what makes the
    sweep about something.
    """
    assert _FILE_TYPES, "the S_IF* derivation found no file types; the sweep below is vacuous"
    assert {"S_IFREG", "S_IFDIR", "S_IFIFO", "S_IFSOCK"} <= set(_FILE_TYPES), (
        f"the file types the two refusals actually meet are not in the swept "
        f"population: {sorted(_FILE_TYPES)}"
    )


@pytest.mark.parametrize("name", sorted(_FILE_TYPES))
def test_both_shape_namers_answer_alike_for_every_file_type(name: str) -> None:
    """RED means an operator can meet two phrasings for one fault.

    `connection.py::_shape_of` and `security/paths.py::_unbounded_shape` are
    deliberately not one function -- SEC-8's cap over authored source files and
    the bound on an `open` of derived state are different populations, and
    sharing a symbol between the security layer and a SQLite adapter to save six
    lines would tie two refusals together that have no reason to move together.
    What they *do* share is the sentence the user reads, and nothing but this
    test holds that.

    The comparison covers the residual as well: a file type neither function
    names must fall to the same "a special file" in both, which is what stops the
    agreement from being a coincidence over the enumerated cases.
    """
    mode = _FILE_TYPES[name] | 0o600

    assert _shape_of(mode) == _unbounded_shape(mode), (
        f"the two shape namers disagree about {name}: connection.py says "
        f"{_shape_of(mode)!r} and security/paths.py says {_unbounded_shape(mode)!r}, so "
        f"the same artefact is described two ways depending on which opener met it"
    )


def test_a_directory_is_not_a_shape_either_namer_reports() -> None:
    """The one exclusion both functions make, pinned because both callers rely on it.

    `open()` refuses a directory outright before a byte moves, and each caller
    already publishes a refusal that names it better: `sqlite3.connect` reports
    its own error for a directory at the database path, and #520's `EISDIR`
    branch answers one at the lock path -- driven by
    `test_migrate_apply_lock_confinement.py::
    test_a_lock_the_open_cannot_take_is_refused_as_a_document`'s directory
    artefact. Making a directory a shape would take those refusals away from the
    branches that say them best, and it would do it silently.
    """
    assert _shape_of(stat.S_IFDIR | 0o755) is None, (
        "a directory is now reported as a shape, which replaces two refusals that "
        "name the fault exactly with one that says only 'not a regular file'"
    )
    assert _shape_of(stat.S_IFREG | 0o644) is None, (
        "a regular file is reported as a shape, so every ordinary open would be refused"
    )
