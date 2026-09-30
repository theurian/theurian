"""The reads each gate makes, derived from `src`, and the records of the session behind them (#832).

`test_adr_0038_gate_read_records.py` holds prose to the sets this module derives
and pins. Three groups:

- **The derivation itself** (`gate_read_facts`): each rule is driven over a
  snippet that must be read as a body read and one that must not, because a walk
  that finds nothing and a tree that holds nothing look alike.
- **The gates' reads**: `knowledge.get` reads a body through `current_revision`;
  search through `_served_item`'s `get_item_exact`, for every row that clears, and
  `_surfaced`'s `get_revision`, for the first `limit`; `_relation_is_visible`
  through none. Behaviour is driven in `test_result_gate_session.py`.
- **`CanonicalReadSession`'s standing** (ADR-0003, #865): the facts the open
  question rests on, and the two records that say it is open.

**Keys, each with its weak half.** `gate_read_facts` states the derivation keys.
The caller scan for the session's `get_item` is: an attribute call `.get_item(...)`
in `src` whose first argument is spelled with `context`. The writer's
`get_item(project_id, item_id)` is a different method and is excluded by that key;
a session call whose first argument is not spelled with `context` is outside it.
The gate-path reads are the session members called by `_relation_is_visible`,
every method of `CanonicalVisibility` and every method of `ResultGate` (the scopes
`gates()` walks); a read reached through another function is outside it. The
session-factory annotation scan is a `Callable[...]` subscript naming
`CanonicalReadSession`; a string annotation or an alias is outside it.
"""

from __future__ import annotations

import ast
import textwrap
from typing import Final

import pytest
from adr_0038_support import REPO_ROOT, SRC, THREAT_MODEL, _sites, _trees
from gate_read_facts import (
    READ_NAMES,
    SESSION_MEMBERS,
    STORE_FILE,
    TOOLS_FILE,
    VISIBILITY_FILE,
    body_readers,
    callees,
    function,
    gates,
    joined_readers,
    klass,
    methods,
    reads_body,
    self_reach,
    tree,
)
from gate_read_rules import _block
from record_sentences import (
    sentence_containing,
)

pytestmark = pytest.mark.unit

_PRE_GATE_TESTS: Final = REPO_ROOT / (
    "packages/theurian-core/tests/integration/test_pre_gate_body_materialization.py"
)
_NUMBER_WORDS: Final = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}

# -- The derivation, driven over snippets -----------------------------------------

_SNIPPET: Final = textwrap.dedent(
    '''
    _BODY_SQL = "SELECT body FROM knowledge_revisions WHERE x = ?"
    _POINTER_SQL = "SELECT item_id FROM knowledge_items WHERE x = ?"
    _MIXED_CASE_SQL = "SELECT body FROM Knowledge_Revisions WHERE x = ?"

    class Store:
        def direct(self):
            return self._read("SELECT * FROM knowledge_revisions")

        def through_a_constant(self):
            return self._read(_BODY_SQL)

        def through_a_self_call(self):
            self.through_a_constant()
            return self._read(_POINTER_SQL)

        def through_two_self_calls(self):
            return self.through_a_self_call()

        def direct_upper_case(self):
            return self._read("SELECT * FROM KNOWLEDGE_REVISIONS")

        def direct_mixed_case(self):
            return self._read("SELECT r.body FROM Knowledge_Items i JOIN Knowledge_Revisions r")

        def through_a_mixed_case_constant(self):
            return self._read(_MIXED_CASE_SQL)

        def pointer_only(self):
            return self._read(_POINTER_SQL)

        def names_the_table_in_its_docstring(self):
            """Not `knowledge_revisions`: this is the pointer row alone."""
            return self._read(_POINTER_SQL)

        def hands_a_reader_on(self):
            return self._read(_POINTER_SQL, mapper=self.through_a_constant)

        def calls_another_object(self):
            return self.other.through_a_constant()

        def _read(self, sql, mapper=None):
            return sql
    '''
)


@pytest.mark.parametrize(
    ("method", "selects_a_body"),
    [
        ("direct", True),
        ("direct_upper_case", True),
        ("direct_mixed_case", True),
        ("through_a_mixed_case_constant", True),
        ("through_a_constant", True),
        ("through_a_self_call", True),
        ("through_two_self_calls", True),
        ("pointer_only", False),
        ("names_the_table_in_its_docstring", False),
        ("hands_a_reader_on", False),
        ("calls_another_object", False),
    ],
)
def test_a_method_reads_a_body_when_its_self_call_reach_names_the_revisions_table(
    method: str, selects_a_body: bool
) -> None:
    """RED means the body-selecting key follows too little or too much of a method's reach.

    `through_a_self_call` is adversarial N6a: a body read inserted into the
    body-free `get_item_exact_metadata` through `self.get_item(...)` left every
    pin green while the constant-name key read the method as body-free. The last
    two rows are the key's stated weak half (a method handed on by reference, a
    call on another object), asserted so that widening the key is a decision.
    The upper- and mixed-case rows are adversarial M1: SQLite resolves a table
    name in any case, so a body read spelled `KNOWLEDGE_REVISIONS` or
    `Knowledge_Revisions` left every pin green while a case-sensitive key read
    the method as body-free.
    """
    module = ast.parse(_SNIPPET)
    owner = klass(module, "Store")

    assert reads_body(module, owner, method) is selects_a_body, (
        f"`{method}` reaches {sorted(self_reach(owner, method))}, and the key reads it as "
        f"{'not ' if selects_a_body else ''}selecting a body"
    )


def test_a_method_the_class_does_not_define_is_not_followed() -> None:
    """The reach stops at the class: `self.x()` with no `x` in it reads nothing."""
    owner = klass(ast.parse(_SNIPPET), "Store")

    assert self_reach(owner, "calls_another_object") == {"calls_another_object"}, (
        "the reach followed a call on another object"
    )
    assert self_reach(owner, "absent") == set(), "a name the class does not define has no reach"


# -- What the store reads: the derived sets ---------------------------------------


def test_the_body_readers_are_the_joined_reads_the_revision_read_and_the_adapters_dereference() -> (
    None
):
    """RED means the set every post-gate record is held to has moved (#832).

    The session's `get_revision` is a member `_ITEM_READS` (the `get_item*`
    members) leaves out, and `current_revision` is the adapter's own; both read a
    body and both are called after a gate. The four are a positive control on the
    derivation: a moved store changes the records' obligations, and this says so.
    """
    assert body_readers() == {"get_item", "get_item_exact", "get_revision", "current_revision"}, (
        f"the readers that select a body are {sorted(body_readers())}"
    )
    assert joined_readers() == {"get_item", "get_item_exact"}, (
        f"the readers through the served-content join are {sorted(joined_readers())}"
    )
    assert body_readers() <= READ_NAMES, "a body reader outside the names the records are read for"


def test_the_relation_gates_read_selects_no_body() -> None:
    """RED means the read `_relation_is_visible` decides on selects a body, by any self-call (T-26).

    Keyed on the SQL the read reaches and not on the name of a constant it uses, so
    a body read inserted in the store method itself, or in one it calls, redden it.
    """
    module = tree(STORE_FILE)
    owner = klass(module, "SqliteCanonicalStore")
    (read,) = gates()["relation"].read

    assert not reads_body(module, owner, read), (
        f"`{read}` reaches {sorted(self_reach(owner, read))} and selects a body"
    )
    assert reads_body(module, owner, "get_item_exact"), (
        "control: the joined read must be read as selecting a body, or the check above is vacuous"
    )


# -- Each gate's reads ------------------------------------------------------------


def test_each_gate_reads_a_body_only_at_the_call_sites_derived_for_it() -> None:
    """RED means a gate reads a body through a reader, or from a place, no record names (H-A).

    `knowledge.get` calls `current_revision`; search calls `get_item_exact` in
    `_served_item` and `get_revision` in `_surfaced`; `_relation_is_visible` calls
    none. Derived over every method of `CanonicalVisibility` and `ResultGate`, so a
    third call site in either class is a new obligation for every record.
    """
    found = {
        name: (set(gate.read), {site: set(readers) for site, readers in gate.after.items()})
        for name, gate in gates().items()
    }

    assert found == {
        "knowledge.get": ({"get_item_metadata"}, {"knowledge_get": {"current_revision"}}),
        "search": (
            {"get_item_metadata"},
            {"_served_item": {"get_item_exact"}, "_surfaced": {"get_revision"}},
        ),
        "relation": ({"get_item_exact_metadata"}, {}),
    }, f"the gates' reads are {found}"


def test_knowledge_get_reads_neither_full_read() -> None:
    """RED means `knowledge.get` calls `get_item` or `get_item_exact`, which its records deny."""
    called = callees(function(tree(TOOLS_FILE), "knowledge_get"))

    assert called & {"get_item", "get_item_exact"} == set(), (
        f"knowledge.get calls {sorted(called & {'get_item', 'get_item_exact'})}"
    )
    assert "get_item_metadata" in called and "current_revision" in called, (
        "knowledge.get no longer decides on the pointer row and reads through the revision"
    )


def test_the_only_caller_of_get_item_exact_in_src_is_served_item() -> None:
    """RED means a second caller reads the joined form, or the content check moved off it.

    `get_item_exact` joins the body, so each caller is a body read that must sit
    after a gate. The population is every attribute or bare-name call in `src`.
    """

    def calls(node: ast.AST) -> str | None:
        match node:
            case ast.Call(
                func=ast.Attribute(attr="get_item_exact") | ast.Name(id="get_item_exact")
            ):
                return "get_item_exact"
        return None

    callers = _sites(_trees(SRC), calls)

    assert callers == {(SRC + "application/visibility.py", "_served_item", "get_item_exact")}, (
        f"callers of get_item_exact in src: {sorted(callers)}"
    )


def test_the_lookup_reads_the_pointer_row_and_nothing_calls_the_sessions_get_item() -> None:
    """RED means the gate's item read is another read, or the session's `get_item` gained a caller.

    Records say the narrowed `get_item` "has no caller in `src/`" and that the
    reachability read is `get_item_metadata`. The key for a session call is the
    first argument spelled with `context`; the writer's `get_item(project_id, ...)`
    is what it excludes, asserted as a control.
    """

    def session_call(node: ast.AST) -> str | None:
        match node:
            case ast.Call(func=ast.Attribute(attr="get_item"), args=[first, *_]) if (
                "context" in ast.unparse(first)
            ):
                return ast.unparse(node)
        return None

    def writer_call(node: ast.AST) -> str | None:
        match node:
            case ast.Call(func=ast.Attribute(attr="get_item"), args=[first, *_]) if (
                "project_id" in ast.unparse(first)
            ):
                return ast.unparse(node)
        return None

    lookup = methods(klass(tree(VISIBILITY_FILE), "CanonicalVisibility"))["_lookup"]

    assert callees(lookup) & SESSION_MEMBERS == {"get_item_metadata"}, (
        f"`_lookup` reads through {sorted(callees(lookup) & SESSION_MEMBERS)}"
    )
    assert _sites(_trees(SRC), session_call) == set(), (
        f"the session's get_item has callers: {sorted(_sites(_trees(SRC), session_call))}"
    )
    assert _sites(_trees(SRC), writer_call), (
        "control: the key that excludes the writer's get_item found no writer call to exclude"
    )
    assert _sites({"<snippet>": ast.parse("store.get_item(context, item_id)")}, session_call), (
        "control: the session key does not see a session call"
    )


def test_the_t26_proof_names_a_test_that_drives_cleared_alone_and_counts_one_of_each_read() -> None:
    """RED means the test the proof bullet cites does not drive what the bullet says it does.

    The bullet says `cleared`, driven over one surfaceable row, makes one metadata
    read and one body read, and that the test does not drive `ResultGate._surfaced`.
    Held here: the test exists, asserts `metadata_reads == 1` and `body_reads == 1`
    (the count words the prose spells are derived from them), and its module never
    names `ResultGate`. Not held: that the counter counts what its name says.
    """
    name = "test_may_surface_reads_the_body_of_a_surfaceable_row"
    module = ast.parse(_PRE_GATE_TESTS.read_text(encoding="utf-8"))
    test = function(module, name)
    counted: dict[str, int] = {}
    for node in ast.walk(test):
        match node:
            case ast.Compare(
                left=ast.Attribute(attr=counter),
                ops=[ast.Eq()],
                comparators=[ast.Constant(value=int(times))],
            ):
                counted[counter] = times
    spelled = sentence_containing(_block(THREAT_MODEL.read_text(encoding="utf-8"), name), name)
    named = {n.id for n in ast.walk(module) if isinstance(n, ast.Name)}

    assert counted == {"metadata_reads": 1, "body_reads": 1}, f"the test counts {counted}"
    assert f"{_NUMBER_WORDS[counted['metadata_reads']]} metadata read and " in spelled, (
        "the proof bullet spells another count of metadata reads than the test asserts"
    )
    assert f"{_NUMBER_WORDS[counted['body_reads']]} body read" in spelled, (
        "the proof bullet spells another count of body reads than the test asserts"
    )
    assert "ResultGate" not in named, (
        "the test module drives `ResultGate`; the bullet says it does not"
    )
    assert "it does not drive `ResultGate._surfaced`" in spelled, (
        "the bullet no longer says the test leaves `_surfaced` undriven"
    )


def test_the_only_session_factory_annotation_in_src_is_result_gates_store_factory() -> None:
    """RED means a second `Callable[..., CanonicalReadSession]` seam exists, or the first moved.

    The records call `ResultGate`'s `store_factory` the gate path's one injected
    session seam. Key: a `Callable[...]` subscript whose text names
    `CanonicalReadSession`; `IndexBuildSession`'s factory is the other annotation and
    is not one. Not seen: a string annotation, or an alias of the session.
    """

    def factory(node: ast.AST) -> str | None:
        match node:
            case ast.Subscript(value=ast.Name(id="Callable") | ast.Attribute(attr="Callable")):
                text = ast.unparse(node)
                return text if "CanonicalReadSession" in text else None
        return None

    found = _sites(_trees(SRC), factory)

    assert {(path, label) for path, _, label in found} == {
        (SRC + "application/retrieval_service.py", "Callable[[Path], CanonicalReadSession]")
    }, f"session-factory annotations in src: {sorted(found)}"
    assert _sites({"<snippet>": ast.parse("x: Callable[[Path], CanonicalReadSession]")}, factory), (
        "control: the key does not see the annotation it is written for"
    )


def test_the_relation_gate_has_one_call_site_and_it_is_in_knowledge_get() -> None:
    """RED means `_relation_is_visible` gained a caller, or moved out of `knowledge_get`.

    The records say shipped wiring always hands it the concrete store that
    `knowledge_get` opens; that holds only while it has no other caller.
    """

    def call(node: ast.AST) -> str | None:
        match node:
            case ast.Call(
                func=ast.Name(id="_relation_is_visible")
                | ast.Attribute(attr="_relation_is_visible")
            ):
                return "_relation_is_visible"
        return None

    assert _sites(_trees(SRC), call) == {
        (SRC + "mcp/tools.py", "knowledge_get", "_relation_is_visible")
    }, f"callers of `_relation_is_visible`: {sorted(_sites(_trees(SRC), call))}"
