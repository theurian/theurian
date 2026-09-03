"""What ``_is_contention`` reads as another writer, and what it deliberately does not.

The predicate decides which of two cures a caller is handed. A write conflict is
transient over an undamaged file and is converted into
``WriteTransactionBusyError``, whose remedy is to wait and retry; everything else
falls through to the CLI's ``(OSError, sqlite3.Error)`` backstops, whose remedy
instructs deleting ``.theurian/state/`` (#484). Reading one as the other in the
*wrong* direction is what round one found: a `database is locked` answered with a
delete-your-state cure.

**The decision this module pins is the fail-closed one.**
``sqlite_errorcode`` exists only on errors SQLite itself raised. A module-raised
``sqlite3.Error`` -- a ``ProgrammingError`` for the wrong number of bind
parameters, say -- carries no such attribute at all, measured on CPython 3.13,
and the predicate reads that absence as *not* contention rather than assuming it
away. Nothing held that: the pre-round-two mutation sweep (2026-09-03) flipped
the ``getattr`` default from ``None`` to ``SQLITE_BUSY``, so an attribute-less
error became a write conflict, and all 5134 tests stayed green. That mutation is
what this module exists to kill.

**Pure, and deliberately so.** The predicate is a function of one exception
object, so nothing here opens a database: the exception shapes are constructed
directly. What that costs is stated rather than hidden -- a constructed
``ProgrammingError`` is not *proof* that a module-raised one lacks the attribute,
it is a stand-in for the shape. The proof is the measurement recorded above, and
:func:`test_the_shapes_this_module_feeds_really_lack_a_result_code` keeps the
stand-in honest by asserting the attribute really is absent, so a future CPython
that starts setting it fails here rather than emptying every case below.
"""

from __future__ import annotations

import sqlite3

import pytest

from theurian.infrastructure.sqlite.connection import _is_contention

pytestmark = pytest.mark.unit


def _a_result_code_sqlite_set() -> sqlite3.Error:
    """An error SQLite raised, carrying the primary code for "another writer holds it".

    The positive control, and it is not optional: every other case below asserts
    ``False``, which a predicate that had stopped looking at anything would
    satisfy perfectly.

    A factory rather than a module-level object (round two, code review LOW-4).
    Exception instances are mutable and ``sqlite_errorcode`` is set here by
    assignment, so a module-level one is shared state a test could edit for the
    next test to inherit -- the shape that makes a suite order-dependent under
    `pytest-randomly`, which this repository runs.
    """
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def _without_a_result_code() -> dict[str, sqlite3.Error]:
    """The three attribute-less shapes, labelled by what each is a stand-in for.

    The second matters most. Its *message* is the exact text a real conflict
    carries, so a predicate that matched on the string rather than on the result
    code would call it contention -- and the commit that introduced the predicate
    claims to be "structural, not a string match". This is where that claim is
    checked.

    A factory for the same reason as above.
    """
    return {
        "a bare OperationalError": sqlite3.OperationalError(),
        "an OperationalError whose message says it": sqlite3.OperationalError("database is locked"),
        "a module-raised ProgrammingError": sqlite3.ProgrammingError(
            "Incorrect number of bindings supplied"
        ),
    }


def test_the_shapes_this_module_feeds_really_lack_a_result_code() -> None:
    """Guards every case below. A shape that grew the attribute asserts nothing.

    The negative cases are all about what the predicate does when
    ``sqlite_errorcode`` is *absent*. If a future CPython set it on
    module-constructed errors -- or if someone replaced one of these shapes with
    an error SQLite itself raised -- those cases would go on passing while
    measuring a different question entirely.
    """
    carrying = {
        label: getattr(exc, "sqlite_errorcode", None)
        for label, exc in _without_a_result_code().items()
        if hasattr(exc, "sqlite_errorcode")
    }

    assert carrying == {}, (
        f"a shape this module feeds as attribute-less now carries a result code, "
        f"so the cases below no longer test the absence branch: {carrying}"
    )
    assert getattr(_a_result_code_sqlite_set(), "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY, (
        "the positive control lost its result code, so 'the predicate still says "
        "True for something' is no longer being asked"
    )


def test_an_error_without_a_result_code_is_not_read_as_another_writer() -> None:
    """The absence of ``sqlite_errorcode`` fails closed, and is not assumed away.

    Asserted as one mapping rather than as four separate tests so the positive
    control cannot be separated from the cases it controls. A predicate that
    returned ``False`` for everything -- the shape a broken narrowing produces --
    fails on the last entry; one that returned ``True`` for everything, which is
    what flipping the ``getattr`` default does, fails on the first three.

    What each ``False`` buys: an error the driver did not raise is answered with
    the state-database cure rather than with "wait for the other writer", which
    is right, because nothing about a ``ProgrammingError`` says another process
    holds the file. Reading it as contention would tell an operator to wait for a
    writer that does not exist, forever.
    """
    verdicts = {label: _is_contention(exc) for label, exc in _without_a_result_code().items()}
    verdicts["an error SQLite raised with SQLITE_BUSY"] = _is_contention(
        _a_result_code_sqlite_set()
    )

    assert verdicts == {
        "a bare OperationalError": False,
        "an OperationalError whose message says it": False,
        "a module-raised ProgrammingError": False,
        "an error SQLite raised with SQLITE_BUSY": True,
    }
