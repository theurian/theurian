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

import ast
import inspect
import pathlib
import stat

import pytest

from theurian.domain.errors import TheurianError
from theurian.infrastructure.sqlite import connection as connection_module
from theurian.infrastructure.sqlite.connection import _shape_of
from theurian.infrastructure.sqlite.store import _ALREADY_ANSWERED
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


# -- The population `store.py::_ALREADY_ANSWERED` has to cover -----------------
#
# `_reading()` re-wraps everything it does not recognise into
# `StateDatabaseUnreadableError`, whose cure deletes the state. So a `_prepare`
# refusal that is *not* damage -- contention, an unwritable state directory -- is
# undone one layer up unless it is listed there, and that list is exactly the kind
# of thing nobody remembers to extend. `store.py` records that this has already
# happened; this is what stops it happening silently again.


def _connection_source() -> str:
    return pathlib.Path(inspect.getfile(connection_module)).read_text(encoding="utf-8")


def _functions_of(module_source: str) -> dict[str, ast.FunctionDef]:
    """Every function and method in `connection.py`, by the name a call site writes.

    Methods are keyed unqualified -- `_open` and `_acquire` belong to the write
    lock's class -- because the call graph below matches on what the caller
    wrote, not on where the definition lives. The class is named that way rather
    than spelled out on purpose: `test_connection_claims.py` keeps a population
    of the test files that *construct* the lock, keyed on that exact word, and
    this file does not construct one.
    """
    tree = ast.parse(module_source)
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _reachable_from(entry: str, functions: dict[str, ast.FunctionDef]) -> set[str]:
    """Names `entry` can reach through calls made inside this module.

    A name-level graph, which over-approximates: a call to something this module
    does not define is simply not in `functions` and drops out, and two functions
    sharing a name would be merged. Over-approximation is the safe direction --
    it can only add exception types to the population that must be answered for,
    never remove one.
    """
    seen: set[str] = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    pending.append(target.id)
                elif isinstance(target, ast.Attribute):
                    pending.append(target.attr)
    return seen


def _raised_in(names: set[str], functions: dict[str, ast.FunctionDef]) -> set[str]:
    """Every `raise <Name>(...)` written inside `names`."""
    raised: set[str] = set()
    for name in names:
        for node in ast.walk(functions[name]):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(target, ast.Name):
                raised.add(target.id)
    return raised


def test_the_read_open_call_graph_is_derived_and_not_empty() -> None:
    """The premise, asserted before the population that rests on it.

    A graph that found nothing would report perfect coverage: the empty set is a
    subset of anything. `_prepare` is named because that is where the interesting
    refusals live -- a graph reaching `open_read_connection` but not the function
    it calls would sweep almost nothing while looking healthy.
    """
    functions = _functions_of(_connection_source())
    assert "open_read_connection" in functions, (
        f"the read opener is not in the parsed module, so this file is reading something "
        f"else: {sorted(functions)[:10]}"
    )
    reachable = _reachable_from("open_read_connection", functions)
    assert {"open_read_connection", "_prepare", "_configure", "_assert_schema_version"} <= (
        reachable
    ), f"the call graph from the read opener no longer reaches `_prepare`: {sorted(reachable)}"
    assert _ALREADY_ANSWERED, "`_ALREADY_ANSWERED` is empty, so the subset check below is vacuous"


def test_every_theurian_error_a_read_open_can_raise_is_already_answered() -> None:
    """RED means a refusal `_prepare` classified is re-wrapped as damage one layer up.

    The failure this catches is silent by construction. `_reading()`'s
    `except Exception` converts anything outside `_ALREADY_ANSWERED` into
    `StateDatabaseUnreadableError`, so a new type raised by an opener still
    *reaches* the caller -- carrying the wrong message and the delete-your-state
    cure. Nothing crashes, no test over a healthy database notices, and the
    classification that was just added is worth nothing.

    Measured while writing #530: with `StateDirectoryUnwritableError` left out of
    that tuple, `migrate validate` over an unwritable state directory published
    "Delete `.theurian/state/` and run `theurian migrate apply`" again -- the
    exact cure the classification exists to replace.

    Scoped to `TheurianError` subclasses because those are the ones carrying a
    message and a remedy of their own. `FileNotFoundError` is answered by that
    tuple too and is asserted separately, since resolving builtins through the
    module namespace would quietly admit anything Python defines.
    """
    functions = _functions_of(_connection_source())
    reachable = _reachable_from("open_read_connection", functions)
    raised = _raised_in(reachable, functions)
    assert raised, "no `raise` was found on the read-open path, so this sweep is vacuous"

    ours = {
        name: resolved
        for name in raised
        if isinstance(resolved := getattr(connection_module, name, None), type)
        and issubclass(resolved, TheurianError)
    }
    assert ours, (
        f"none of the raised names resolves to a `TheurianError` in connection.py, so the "
        f"resolution step is broken rather than the population clean: {sorted(raised)}"
    )

    unanswered = sorted(name for name, kind in ours.items() if kind not in _ALREADY_ANSWERED)
    assert not unanswered, (
        f"these types are raised while opening a state database for reading but are not in "
        f"`store.py::_ALREADY_ANSWERED`, so `_reading()` re-wraps each one into "
        f"`StateDatabaseUnreadableError` and publishes the delete-your-state cure over it: "
        f"{unanswered}"
    )


def test_a_missing_database_is_answered_without_being_called_damage() -> None:
    """`FileNotFoundError` is the non-`TheurianError` member of the same tuple.

    `open_read_connection` raises it for a database that was never built, and the
    cure there is `theurian migrate apply` with nothing to delete first. Asserted
    here rather than inside the sweep above, because that sweep resolves names
    against `connection.py`'s namespace where a builtin would resolve by accident
    and admit anything Python defines.
    """
    assert FileNotFoundError in _ALREADY_ANSWERED, (
        "a state database that was never built is reported as a damaged one, whose cure "
        "tells the operator to delete a directory that does not exist"
    )
