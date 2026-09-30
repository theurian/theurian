"""The records that name the relation gate's read and the post-gate body read (#832).

**The class.** Prose names a read by symbol, and nothing derived that prose from
the call site. `_relation_is_visible` moved from `get_item_exact` to
`get_item_exact_metadata` in 0.2.3 (T-26); the records kept saying the old
one, and `get_item`, for the body read that `CanonicalVisibility._served_item`
makes through `get_item_exact`. Here both reads are *derived* from the code, and
each record is held to the derived name; no expected read is spelled in this file.

- **R**, the gate's read: the one `CanonicalReadSession` `get_item*` member
  `mcp/tools.py::_relation_is_visible` calls.
- **S**, the post-gate body read: the one such member
  `application/visibility.py::CanonicalVisibility._served_item` calls, and the
  only caller of `get_item_exact` in `src`.

**The key, and its known-weak half.** A record is located by a claim-free anchor
(the subject it is about, never the read it names) and held by one of four rules
over the sentence that starts at the anchor and ends at the next full stop:
``first`` (the first `get_item*` name after the anchor is the derived read),
``last`` (the last one is, where a sentence narrates the move oldest-first),
``exactly`` (the sentence names that read and no other) and ``paragraph`` (the
whole block names it and never bare `get_item`). The names are the members of
`CanonicalReadSession` spelled in the text. A record that fails to locate says
"record not found", never "claim false".

What the rules cannot see: a stale name later in the same sentence under
``first``, a stale claim that names no read (the Tests row says "body-free", so
it is held to that word, derived from R's SQL), and any record not in the tables
below. `test_adr_0038_joined_read_population.py` is the ratchet over the rest of
the tree, and it is line-keyed, so it does not see a face whose symbol is off
its line; the anchored rules here are what cover that shape (the port
docstring's "through this exact read").

Not held: that each record's *reasoning* is right, only which read it names. The
Control, part A paragraph of T-21 is the 0.1.0.dev6 record and is expected to
name `get_item_exact`; only its `Amended in #832` block is held.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import pytest
from adr_0038_support import (
    PORT,
    REPO_ROOT,
    ROADMAP,
    SRC,
    STORE,
    THREAT_MODEL,
    TOOLS,
    _callees,
    _function,
    _sites,
    _trees,
)

from theurian.domain.ports.canonical_store import CanonicalReadSession

pytestmark = pytest.mark.unit

VISIBILITY: Final = SRC + "application/visibility.py"
INDEX_BUILDER: Final = SRC + "application/index_builder.py"
ALIAS_GUARDS: Final = SRC + "application/migration_alias_guards.py"

#: `CanonicalReadSession`'s `get_item*` members, from the class.
_ITEM_READS: Final = frozenset(
    name for name in dir(CanonicalReadSession) if name.startswith("get_item")
)
_NAME: Final = re.compile(r"(?<![A-Za-z0-9_])get_item[a-z_]*")
_MARKERS: Final = re.compile(r"(?m)^\s*(?:>|#:|#)\s?")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKERS.sub("", text)).strip()


def _names(text: str) -> list[str]:
    return [name for name in _NAME.findall(text) if name in _ITEM_READS]


def _one_read(path: str, function: str) -> str:
    called = _callees(_function(path, function)) & _ITEM_READS
    assert len(called) == 1, f"{path}::{function} makes {sorted(called)}, not exactly one item read"
    return next(iter(called))


def _body_free(read: str) -> bool:
    used = {node.id for node in ast.walk(_function(STORE, read)) if isinstance(node, ast.Name)} & {
        "_ITEM_METADATA_SQL",
        "_ITEM_WITH_CURRENT_CONTENT_SQL",
    }
    assert len(used) == 1, f"SqliteCanonicalStore.{read} reads through {sorted(used)}"
    return used == {"_ITEM_METADATA_SQL"}


def _gate_read() -> str:
    return _one_read(TOOLS, "_relation_is_visible")


def _served_read() -> str:
    return _one_read(VISIBILITY, "_served_item")


# -- Locators: each returns the record's text or fails with "record not found" ----


def _not_found(what: str) -> None:
    pytest.fail(f"record not found: {what}", pytrace=False)


def _doc(path: str, function: str, cls: str | None = None) -> str:
    if cls is None:
        return ast.get_docstring(_function(path, function)) or ""
    owner = next(
        node
        for node in ast.walk(_trees()[path])
        if isinstance(node, ast.ClassDef) and node.name == cls
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function
    )
    return ast.get_docstring(method) or ""


def _module_doc(path: str) -> str:
    return ast.get_docstring(_trees()[path]) or ""


def _blocks(text: str) -> list[str]:
    return [block for block in re.split(r"\n[ \t]*\n", text) if block.strip()]


def _block(path_text: str, needle: str) -> str:
    found = [block for block in _blocks(path_text) if needle in block]
    if len(found) != 1:
        _not_found(f"{len(found)} blocks carry {needle!r}")
    return found[0]


def _row(text: str, prefix: str, needle: str) -> str:
    found = [line for line in text.splitlines() if line.startswith(prefix) and needle in line]
    if len(found) != 1:
        _not_found(f"{len(found)} rows start {prefix!r} and carry {needle!r}")
    return found[0]


def _comment(path: str, needle: str) -> str:
    """The one run of consecutive comment lines carrying *needle*."""
    runs: list[list[str]] = [[]]
    for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            runs[-1].append(line)
        elif runs[-1]:
            runs.append([])
    found = ["\n".join(run) for run in runs if needle in "\n".join(run)]
    if len(found) != 1:
        _not_found(f"{len(found)} comment runs in {path} carry {needle!r}")
    return found[0]


def _comment_above(path: str, statement: str) -> str:
    """The `#:` run directly above the line that starts with *statement*."""
    lines = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(statement)]
    if len(starts) != 1:
        _not_found(f"{len(starts)} lines in {path} start {statement!r}")
    above: list[str] = []
    for line in reversed(lines[: starts[0]]):
        if not line.startswith("#:"):
            break
        above.insert(0, line)
    return "\n".join(above)


# -- Rules ------------------------------------------------------------------------


def _sentence(text: str, anchor: str) -> list[str]:
    flat = _flat(text)
    if flat.count(anchor) != 1:
        _not_found(f"{flat.count(anchor)} occurrences of the anchor {anchor!r}")
    start = flat.index(anchor)
    stop = re.compile(r"\.(?=\s|$)").search(flat, start)
    return _names(flat[start : stop.start() if stop else len(flat)])


def _first(text: str, anchor: str, read: str) -> str | None:
    names = _sentence(text, anchor)
    return None if names and names[0] == read else f"first read after {anchor!r} is {names[:1]}"


def _last(text: str, anchor: str, read: str) -> str | None:
    names = _sentence(text, anchor)
    return None if names and names[-1] == read else f"last read after {anchor!r} is {names[-1:]}"


def _exactly(text: str, anchor: str, read: str) -> str | None:
    names = set(_sentence(text, anchor))
    return None if names == {read} else f"the sentence at {anchor!r} names {sorted(names)}"


def _paragraph(text: str, anchor: str, read: str) -> str | None:
    names = set(_names(_flat(text)))
    if read not in names or "get_item" in names:
        return f"the paragraph names {sorted(names)}, and must name {read} and never bare get_item"
    return None


@dataclass(frozen=True)
class Record:
    label: str
    text: Callable[[], str]
    anchor: str
    rule: Callable[[str, str, str], str | None]


def _roadmap() -> str:
    return ROADMAP.read_text(encoding="utf-8")


def _threat_model() -> str:
    return THREAT_MODEL.read_text(encoding="utf-8")


_GATE: Final = (
    Record(
        "roadmap Phase C Security row",
        lambda: _row(_roadmap(), "| **Security** |", "A graph response is a new disclosure family"),
        "the visibility decision on each hop's endpoint",
        _first,
    ),
    Record(
        "roadmap section 1 T-21 bullet",
        lambda: _block(_roadmap(), "T-21 was closed by two fixes"),
        "each endpoint is",
        _last,
    ),
    Record(
        "threat model T-21 Amended in #832 block",
        lambda: _block(_threat_model(), "> **Amended in [#832]"),
        "`_relation_is_visible`",
        _first,
    ),
    Record(
        "CanonicalReadSession.get_item_exact docstring",
        lambda: _doc(PORT, "get_item_exact", "CanonicalReadSession"),
        "_relation_is_visible",
        _first,
    ),
    Record(
        "SqliteCanonicalStore.get_item_exact docstring",
        lambda: _doc(STORE, "get_item_exact", "SqliteCanonicalStore"),
        "`_relation_is_visible`",
        _first,
    ),
    Record(
        "migration_alias_guards module docstring",
        lambda: _module_doc(ALIAS_GUARDS),
        "``_relation_is_visible``",
        _first,
    ),
    Record(
        "index_builder visible-set comment",
        lambda: _comment(INDEX_BUILDER, "_relation_is_visible"),
        "_relation_is_visible",
        _first,
    ),
    Record(
        "index_builder relation-count docstring",
        lambda: _doc(INDEX_BUILDER, "_relation_secrets"),
        "the same answer",
        _first,
    ),
)

_SERVED: Final = (
    Record(
        "CanonicalReadSession.get_item_metadata docstring",
        lambda: _doc(PORT, "get_item_metadata", "CanonicalReadSession"),
        "body is read",
        _exactly,
    ),
    Record(
        "SqliteCanonicalStore.get_item_metadata docstring",
        lambda: _doc(STORE, "get_item_metadata", "SqliteCanonicalStore"),
        "body is read",
        _exactly,
    ),
    Record(
        "_ITEM_WITH_CURRENT_CONTENT_SQL comment",
        lambda: _comment_above(STORE, "_ITEM_WITH_CURRENT_CONTENT_SQL"),
        "CanonicalVisibility",
        _first,
    ),
    Record(
        "CanonicalVisibility.cleared docstring, the read",
        lambda: _doc(VISIBILITY, "cleared", "CanonicalVisibility"),
        "the body is read",
        _first,
    ),
    Record(
        "CanonicalVisibility.cleared docstring, the count",
        lambda: _doc(VISIBILITY, "cleared", "CanonicalVisibility"),
        "(The body-carrying",
        _first,
    ),
    Record(
        "threat model T-26 fix paragraph",
        lambda: _block(_threat_model(), "**The fix: gate on metadata"),
        "",
        _paragraph,
    ),
)


def _check(record: Record, read: str) -> None:
    text = record.text()
    assert text.strip(), f"record not found: {record.label} is empty"
    failure = record.rule(text, record.anchor, read)
    assert failure is None, f"{record.label} does not name {read}: {failure}"


@pytest.mark.parametrize("record", _GATE, ids=[r.label for r in _GATE])
def test_a_record_names_the_read_the_relation_gate_makes(record: Record) -> None:
    """RED means a record names another read as the one `_relation_is_visible` makes (#832).

    The expected read is derived from the call site, so moving the gate to
    another read reddens every record here at once, not only the ones a reviewer
    remembers.
    """
    _check(record, _gate_read())


@pytest.mark.parametrize("record", _SERVED, ids=[r.label for r in _SERVED])
def test_a_record_names_the_read_the_content_check_makes_after_the_gate(record: Record) -> None:
    """RED means a record names another read as the post-gate body read (#832).

    This is the WatchDog condition on #832: the PR may not present an absence
    grep for bare `get_item` as this check. The expected read is derived from
    `_served_item`, and the sentences held name it as the body read.
    """
    _check(record, _served_read())


def test_the_roadmap_tests_row_says_body_free_exactly_when_the_gate_read_is() -> None:
    """RED means the Tests row promises a body-free hop read the gate does not make."""
    row = _row(_roadmap(), "| **Tests** |", "Equality extension")

    assert _body_free(_gate_read()) is True
    assert ("body-free" in row) is _body_free(_gate_read()), (
        "the Phase C Tests row and the gate's read disagree on whether the hop read is body-free"
    )


def test_the_post_gate_body_read_is_the_joined_read_and_its_only_caller_is_served_item() -> None:
    """RED means a second caller reads the joined form, or the content check moved off it.

    `get_item_exact` joins the body, so every caller of it is a body read that
    must sit after a gate. The population is derived from the AST of `src`:
    a call through an attribute or bare name.
    """
    served = _served_read()

    def calls(node: ast.AST) -> str | None:
        match node:
            case ast.Call(
                func=ast.Attribute(attr="get_item_exact") | ast.Name(id="get_item_exact")
            ):
                return "get_item_exact"
        return None

    callers = _sites(_trees(SRC), calls)

    assert served == "get_item_exact"
    assert not _body_free(served)
    assert callers == {(VISIBILITY, "_served_item", "get_item_exact")}


def test_knowledge_get_reads_its_body_through_the_revision_and_neither_full_read() -> None:
    """RED means `knowledge.get` calls `get_item` or `get_item_exact`, which the docstrings deny."""
    called = _callees(_function(TOOLS, "knowledge_get"))

    assert called & _ITEM_READS == {"get_item_metadata"}
    assert "current_revision" in called
