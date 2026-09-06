"""Every open of an index database goes through one shape-refusing function (#586).

``index_store.py`` refuses a named pipe, a socket or a device at the index path
*before* the open, because after the open there is nothing left that can bound
one: SQLite retries a call interrupted by a signal, and the driver's ``timeout``
is for locks rather than for opening a file. A guard in that position is worth
exactly its coverage, and coverage here means **no second opener**.

The shipped call sites each probed ``is_file()`` before reaching the store --
``mcp/search.py::_searchable_file``, ``cli/index_status_report.py``,
``cli/index_commands.py``'s gc guard, ``application/withdrawal_purge.py``. That
is a maintained list, the exact shape ``test_connection_faults.py``'s AST form
replaced for the state database, and this module is the same replacement for the
index.
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import socket
from typing import Final

import pytest

from theurian.infrastructure.sqlite import index_purge as index_purge_module
from theurian.infrastructure.sqlite import index_store as index_store_module
from theurian.infrastructure.sqlite.index_purge import IndexPurgeError, _copy
from theurian.infrastructure.sqlite.index_store import (
    IndexBuildError,
    IndexPathNotAFileError,
    _open_read,
)

pytestmark = pytest.mark.unit

#: ``os.mkfifo`` is POSIX-only, and a named pipe is the member whose open is
#: *unbounded* rather than merely wrong.
_CAN_MAKE_A_NAMED_PIPE: Final = hasattr(os, "mkfifo")

#: The one connect this module is allowed to make outside the shared opener: the
#: FTS5 capability probe, which opens no file at all.
_THE_IN_MEMORY_PROBE: Final = ":memory:"


def _opens_a_database(node: ast.Call) -> bool:
    """Whether ``node`` opens a SQLite database, in any spelling this key covers.

    Taken verbatim from ``test_connection_faults.py::_opens_a_database`` and with
    the same recorded bound: the attribute call ``sqlite3.connect(...)``, the bare
    ``connect(...)`` a ``from sqlite3 import connect`` produces, and
    ``sqlite3.Connection(...)``. It does **not** match an aliased module, a name
    bound at runtime, or a connection handed in from elsewhere. Those are covered
    behaviourally, by the FIFO refusals below and by
    ``tests/integration/test_derived_read_bounds.py``; this is the cheap
    structural net for the ordinary way a guard gets bypassed.
    """
    target = node.func
    if isinstance(target, ast.Attribute):
        return target.attr in {"connect", "Connection"} and (
            isinstance(target.value, ast.Name) and target.value.id == "sqlite3"
        )
    return isinstance(target, ast.Name) and target.id in {"connect", "Connection"}


def _opens_only_memory(node: ast.Call) -> bool:
    """Whether the call's first argument is the ``":memory:"`` literal."""
    if not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and first.value == _THE_IN_MEMORY_PROBE


def test_this_module_opens_an_index_database_in_exactly_one_place() -> None:
    """RED means an index database is opened past ``_connect_to``'s shape refusal.

    The premise is asserted first, because a walk that found no ``connect`` at all
    would report perfect containment over a module it had failed to read. The
    functions come from *this* parse rather than a second one: two parses give two
    sets of nodes, and the membership test is identity, so a second parse silently
    answers "no call is inside any function" and passes.
    """
    source = pathlib.Path(inspect.getfile(index_store_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    opens = {
        id(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _opens_a_database(node) and not _opens_only_memory(node)
    }
    probes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _opens_a_database(node) and _opens_only_memory(node)
    ]

    assert opens, (
        "no `sqlite3.connect` over a path was found in index_store.py at all, so this "
        "containment claim is about a module the walk did not read"
    )
    assert probes, (
        "the `:memory:` capability probe is gone, so the exclusion below is carrying a "
        "case that no longer exists and would hide a real opener spelled the same way"
    )

    enclosing = {
        name
        for name, function in functions.items()
        for node in ast.walk(function)
        if id(node) in opens
    }
    assert enclosing == {"_connect_to"}, (
        f"an index database is opened from {sorted(enclosing)}. Every open of one has to go "
        f"through `_connect_to`, which refuses a path that is not a regular file before the "
        f"open -- a second call site opens whatever is there and waits on a named pipe with "
        f"nothing left to bound it (#586)"
    )


def test_the_purge_asks_the_source_shape_before_it_opens_it() -> None:
    """``index_purge`` is the other module that opens a *published* index path.

    Its target is the ``.building`` name ``purge_into`` has already refused to
    reuse, so only the source can hold a plant. Asserted structurally as well as
    behaviourally below, because the behavioural test can only reach ``_copy``
    directly -- ``withdrawal_purge``'s own ``is_file()`` probe stands in front of
    it on every shipped path.
    """
    source = pathlib.Path(inspect.getfile(index_purge_module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    copy_function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_copy"
    )
    asks = [
        node
        for node in ast.walk(copy_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "irregular_shape_at"
    ]
    assert asks, (
        "`_copy` no longer asks what is at the source path before opening it, so a named "
        "pipe there holds `migrate apply` -- and the write lock it has taken -- with no bound"
    )


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_at_the_index_path_is_refused_rather_than_waited_on(
    tmp_path: pathlib.Path,
) -> None:
    """The member the guard exists for, driven at the opener.

    Measured before the guard, in a killed child: ``_open_read`` against a named
    pipe was still inside the call at 12 seconds. It is driven here rather than
    through a shipped command because every shipped caller probes ``is_file()``
    first -- which is exactly why the guard had to become structural.
    """
    pipe = tmp_path / "theurian-index-01ABCDEF.sqlite"
    os.mkfifo(pipe)
    with pytest.raises(IndexPathNotAFileError) as raised:
        _open_read(pipe)
    assert raised.value.shape == "a named pipe (FIFO)"
    assert isinstance(raised.value, IndexBuildError), (
        "the refusal is no longer an `IndexBuildError`, so `mcp/search.py`'s fallback to "
        "the substring scan stops covering it and it reaches an agent as a tool failure"
    )
    assert str(pipe) in raised.value.remedy, (
        f"the remedy does not name the artefact to clear: {raised.value.remedy!r}"
    )
    assert "`theurian index build`" in raised.value.remedy, (
        f"the remedy names nothing the reader can run: {raised.value.remedy!r}"
    )


def test_a_socket_at_the_index_path_is_refused_by_shape(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket, so the guard's population is *not a regular file* rather than *a pipe*.

    Bound through a **relative** name from inside ``tmp_path``: an ``AF_UNIX``
    address is capped at about a hundred bytes and a pytest temporary directory
    already spends most of that, so binding the absolute path raises ``AF_UNIX
    path too long`` and the test would skip on the platform it is meant to run on.
    """
    monkeypatch.chdir(tmp_path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind("theurian-index-01ABCDEF.sqlite")
        with pytest.raises(IndexPathNotAFileError) as raised:
            _open_read(tmp_path / "theurian-index-01ABCDEF.sqlite")
    assert raised.value.shape == "a socket"


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_at_the_purge_source_is_refused_with_a_cure(
    tmp_path: pathlib.Path,
) -> None:
    """``_copy``'s refusal keeps its caller's contract: an ``IndexPurgeError`` with a cure.

    Not a new exception type, because ``migrate apply``'s withdrawal purge grades
    ``IndexPurgeError`` and a different type would reach a ``--json`` caller as a
    traceback.
    """
    pipe = tmp_path / "theurian-index-01ABCDEF.sqlite"
    os.mkfifo(pipe)
    with pytest.raises(IndexPurgeError) as raised:
        _copy(pipe, tmp_path / "target.sqlite.building")
    assert "a named pipe (FIFO)" in str(raised.value)
    assert "`theurian index build`" in str(raised.value), (
        f"the refusal names nothing the reader can run: {str(raised.value)!r}"
    )
    assert not (tmp_path / "target.sqlite.building").exists(), (
        "the refusal ran after the target had been created, so a refused purge leaves a "
        "stranded file behind"
    )
