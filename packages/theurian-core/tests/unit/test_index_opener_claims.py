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
import json
import os
import pathlib
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Final, cast

import pytest
from ast_keys import opens_a_database, opens_inside

from theurian.application.project_service import ProjectPaths
from theurian.infrastructure.sqlite import index_purge as index_purge_module
from theurian.infrastructure.sqlite import index_store as index_store_module
from theurian.infrastructure.sqlite.index_purge import IndexPurgeError, _copy
from theurian.infrastructure.sqlite.index_store import (
    IndexPathNotAFileError,
    SqliteIndexStore,
    _open_read,
)
from theurian.mcp.search import Fallback, _searchable_file

pytestmark = pytest.mark.unit

#: ``os.mkfifo`` is POSIX-only, and a named pipe is the member whose open is
#: *unbounded* rather than merely wrong.
_CAN_MAKE_A_NAMED_PIPE: Final = hasattr(os, "mkfifo")

#: The one connect this module is allowed to make outside the shared opener: the
#: FTS5 capability probe, which opens no file at all.
_THE_IN_MEMORY_PROBE: Final = ":memory:"


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
    opens = {
        id(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and opens_a_database(node) and not _opens_only_memory(node)
    }
    probes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and opens_a_database(node) and _opens_only_memory(node)
    ]

    assert opens, (
        "no `sqlite3.connect` over a path was found in index_store.py at all, so this "
        "containment claim is about a module the walk did not read"
    )
    assert probes, (
        "the `:memory:` capability probe is gone, so the exclusion below is carrying a "
        "case that no longer exists and would hide a real opener spelled the same way"
    )

    inside = opens_inside(tree, "_connect_to", opens)
    assert inside == opens, (
        f"{len(opens - inside)} of {len(opens)} index-database opens are outside "
        f"`_connect_to`. Every open of one has to go through it, because it refuses a path "
        f"that is not a regular file before the open -- a second call site opens whatever "
        f"is there and waits on a named pipe with nothing left to bound it (#586)"
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


#: The child that opens a planted index path and reports what it got.
#:
#: **A child, because this refusal's absence is a hang** (#586 round two, M-3).
#: This test called ``_open_read`` in-process, so the mutation that deletes the
#: shape check did not turn it red -- it parked the whole suite inside
#: ``sqlite3.connect`` for 2460 seconds before the run was killed by hand. The
#: suite's ``hang_guard`` does not reach this open either: SQLite retries a call
#: interrupted by a signal, measured on #585's branch as a ``SIGALRM`` at 3 s
#: leaving the process inside ``__open`` at 150 s. The kill in
#: :func:`_refusal_in_a_child` is the only bound, which is the rule both sibling
#: files (``test_state_database_faults.py``, ``test_derived_read_bounds.py``)
#: already follow for the same reason.
_CHILD: Final = (
    "import json, sys\n"
    "from theurian.infrastructure.sqlite.index_store import _open_read\n"
    "try:\n"
    "    _open_read(__import__('pathlib').Path(sys.argv[1])).close()\n"
    "except BaseException as exc:\n"
    "    print(json.dumps({\n"
    "        'type': type(exc).__name__,\n"
    "        'shape': getattr(exc, 'shape', None),\n"
    "        'remedy': getattr(exc, 'remedy', ''),\n"
    "        'is_build_error': isinstance(exc, __import__(\n"
    "            'theurian.infrastructure.sqlite.index_store', fromlist=['x']\n"
    "        ).IndexBuildError),\n"
    "    }))\n"
    "else:\n"
    "    print(json.dumps({'type': None}))\n"
)

#: How long the child gets before it is killed and the test fails. Generous next
#: to an open that refuses without touching the file, short next to a CI job.
_CHILD_TIMEOUT_SECONDS: Final = 30.0


def _refusal_in_a_child(path: pathlib.Path) -> dict[str, object]:
    """Open ``path`` through ``_open_read`` in a child, killed if it does not return."""
    try:
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-c", _CHILD, str(path)],
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`_open_read` did not return within {_CHILD_TIMEOUT_SECONDS}s against {path.name} "
            f"and the child was killed: the open is unbounded again, and an in-process "
            f"version of this test would have hung the suite instead of failing"
        )
    lines = done.stdout.strip().splitlines()
    assert lines, f"the child printed no report; it exited {done.returncode}: {done.stderr!r}"
    report: dict[str, object] = json.loads(lines[-1])
    return report


@pytest.mark.skipif(not _CAN_MAKE_A_NAMED_PIPE, reason="os.mkfifo is POSIX-only")
def test_a_named_pipe_at_the_index_path_is_refused_rather_than_waited_on(
    tmp_path: pathlib.Path,
) -> None:
    """The member the guard exists for, driven at the opener in a killed child.

    Measured before the guard: ``_open_read`` against a named pipe was still
    inside the call at 12 seconds. It is driven at the opener rather than through
    a shipped command because every shipped caller probes ``is_file()`` first --
    which is exactly why the guard had to become structural.
    """
    pipe = tmp_path / "theurian-index-01ABCDEF.sqlite"
    os.mkfifo(pipe)
    report = _refusal_in_a_child(pipe)
    assert report["type"] == "IndexPathNotAFileError", f"the open did not refuse by shape: {report}"
    assert report["shape"] == "a named pipe (FIFO)"
    assert report["is_build_error"] is True, (
        "the refusal is no longer an `IndexBuildError`, so `mcp/search.py`'s fallback to "
        "the substring scan stops covering it and it reaches an agent as a tool failure"
    )
    remedy = str(report["remedy"])
    assert str(pipe) in remedy, f"the remedy does not name the artefact to clear: {remedy!r}"
    assert "`theurian index build`" in remedy, (
        f"the remedy names nothing the reader can run: {remedy!r}"
    )


def test_a_swapped_path_at_the_searchable_probe_falls_back_rather_than_raising(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#586 round two, M-1. The probe that opens the file one call before the query.

    ``_searchable_file`` answers ``is_file()`` and then opens the file through
    ``is_searchable()``. A **static** plant never gets past the first -- measured,
    ``is_file()`` is ``False`` for a named pipe and the request falls back as a
    missing file. What reaches the second is the *interleaving*: a co-resident
    process swapping the path between them, which makes this probe the opener.

    Driven by substituting the refusal at the store rather than by racing a real
    swap, because the race is the one #585 records as winnable and not reliable;
    what is under test is the handler, and the handler cannot tell how the
    refusal arrived. RED before it: the error escaped ``_searchable_file``, past
    the caller's ``except IndexBuildError`` -- which wraps the query, not this
    probe -- and reached the agent as a ``ToolError``.
    """
    index = tmp_path / "theurian-index-01ABCDEF.sqlite"
    index.write_text("not really a database", encoding="utf-8")

    def refuse(self: SqliteIndexStore) -> bool:
        raise IndexPathNotAFileError(index, "a named pipe (FIFO)")

    monkeypatch.setattr(SqliteIndexStore, "is_searchable", refuse)
    # `cast` and not a real `ProjectPaths`: building one needs a project tree,
    # and `_searchable_file` uses exactly the one method the stand-in supplies.
    answer = _searchable_file(cast("ProjectPaths", _PathsNaming(index)), "01ABCDEF")
    assert isinstance(answer, Fallback), (
        f"the probe's refusal escaped `_searchable_file` instead of becoming a fallback, "
        f"so `knowledge.search` answers an agent with a tool failure: {answer!r}"
    )


@dataclass(frozen=True, slots=True)
class _PathsNaming:
    """The one thing ``_searchable_file`` asks of ``ProjectPaths``.

    A stand-in rather than a real ``ProjectPaths``, because building one needs a
    project tree and the function under test uses exactly this method.
    """

    index: pathlib.Path

    def index_for(self, build_id: str) -> pathlib.Path:  # noqa: ARG002 - the signature is the seam
        return self.index


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
