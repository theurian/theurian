"""How many places in the product can hand candidates to the gate (SEC-13, T-17a).

:data:`~theurian.application.retrieval_service.CandidateSource` is
``Callable[[Visibility], SearchOutcome]``, and its own docstring records what
that buys and — twice rewritten, because it was twice written larger — what it
does not. The signature makes the visibility *available* where candidates come
into existence. It cannot make it *used*: a closure may ignore its parameter, and
``admit(request, lambda _visible: precomputed)`` is a conforming source that
publishes a document the canonical store withholds. Not reasoned — measured,
against a real project whose index still held a deprecated document, twice in
Milestone 5 and recorded in two different files.

So what keeps a withheld row out of a response is not the type. It is that
**exactly one call site exists**, and that site is covered by tests which fail
when it stops ranking through its visibility. That count was true and enforced by
nothing, which is the same shape as a comment saying "do not call this twice".

The enumeration is pinned the way ``test_no_registered_tool_can_reach_a_canonical
_write`` pins its own (``tests/integration/test_mcp_tools.py``): against the
shipped source rather than against a naming convention, and with the expected set
spelled out so that adding to it is a decision someone makes rather than a
diff nobody notices.

**What this cannot see.** It reads names, so ``getattr(gate, "admit")``, a
dispatch table, or a second gate class with the same method under another name
all pass. It is a floor on the review a new call site gets, not a proof that one
cannot exist.

Pure in the sense the other structural tests are: it parses the shipped ``.py``
files as text and opens no database, no socket, and no temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import theurian
from theurian.application.retrieval_service import ResultGate
from theurian.domain.enums import may_surface

pytestmark = pytest.mark.unit

#: Repo root, for the security document that lives outside the wheel.
#:
#: ``parents[4]`` is ``.../tests/unit/`` → ``tests`` → ``theurian-core`` →
#: ``packages`` → repo root, the reckoning ``test_schemas.py`` and
#: ``test_install_claims.py`` use for the same reason.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

#: The package as *imported*, not a path relative to this file.
#:
#: So the tree scanned is the tree the suite runs against. A hand-built relative
#: path can drift from the installed package and would then scan a directory with
#: no call sites in it at all.
SRC = pathlib.Path(theurian.__file__).resolve().parent

#: The gate method whose call sites are counted.
ATTRIBUTE = "admit"

#: The one place in the product that may hand candidates to the gate, as
#: ``(module path under theurian/, enclosing function)``.
#:
#: ``hybrid_answer`` is where the retrieval pipeline and the canonical read
#: session meet. What holds *it* to using the visibility it is handed is
#: ``test_a_withheld_document_changes_nothing_a_caller_can_see``
#: (``tests/integration/test_mcp_tools.py``), red in all twenty parametrisations
#: when that function ranks through a visibility that withholds nothing. A second
#: call site inherits none of that.
#:
#: The enclosing function rather than the line number: a line number moves
#: whenever anything above it is edited, and a test that fails on an unrelated
#: edit is a test that gets its expectation updated without being read.
SOLE_CALL_SITE = ("mcp/search.py", "hybrid_answer")


def _attribute_uses(path: pathlib.Path) -> list[tuple[str, str]]:
    """Every ``.admit`` in ``path``, as ``(module path, enclosing function)``.

    Matches the *attribute*, not the call, so binding the method without calling
    it — ``publish = gate.admit`` — counts as the call site it is.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = path.relative_to(SRC).as_posix()
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Attribute) and child.attr == ATTRIBUTE:
                found.append((module, ".".join(scope) or "<module>"))
            visit(child, scope)

    visit(tree, ())
    return found


def test_the_scanner_looks_for_a_method_the_gate_actually_has() -> None:
    """Guards the guard below, which reads a name and cannot resolve a type.

    If :meth:`ResultGate.admit` is renamed, the scan below starts looking for a
    name nothing in the product has. It would still fail — the expected set is
    non-empty — but it would fail saying "no call sites found", which reads as a
    broken test rather than as the rename it is. This says which it was.
    """
    assert hasattr(ResultGate, ATTRIBUTE), (
        f"`ResultGate.{ATTRIBUTE}` no longer exists, so the call-site count below "
        f"is counting a name the product has stopped using. Rename ATTRIBUTE and "
        f"SOLE_CALL_SITE together, or the enumeration silently protects nothing"
    )


def test_exactly_one_place_in_the_product_hands_candidates_to_the_gate() -> None:
    """A second ``.admit`` call site is a new publication path, and must be argued for.

    The safety of :data:`~theurian.application.retrieval_service.CandidateSource`
    rests on this count and on nothing in the type system: the parameter can be
    ignored, and a source that ignores it publishes what the canonical store
    withheld. That was measured, not inferred.

    The assertion is an equality against the whole enumeration rather than a
    length, so it fails in both directions — a call site added, and the known one
    moved or deleted — and its failure names the sites it found.
    """
    sites = sorted({site for path in sorted(SRC.rglob("*.py")) for site in _attribute_uses(path)})

    assert sites == [SOLE_CALL_SITE], (
        f"`ResultGate.{ATTRIBUTE}` is reachable from {len(sites)} place(s) in the "
        f"shipped source, expected exactly one:\n"
        + "\n".join(f"  {module} :: {function}" for module, function in sites)
        + f"\n\nExpected only: {SOLE_CALL_SITE[0]} :: {SOLE_CALL_SITE[1]}\n\n"
        f"`CandidateSource` is `Callable[[Visibility], SearchOutcome]` and a "
        f"closure may ignore its parameter: `{ATTRIBUTE}(request, lambda _visible: "
        f"precomputed)` published a deprecated document's credential against a "
        f"real project, where the shipped path answered `count: 0`. Nothing in "
        f"the signature rejects that, so the property is held by the call sites "
        f"themselves.\n\n"
        f"A second call site is permitted and is not free. Before widening "
        f"SOLE_CALL_SITE, establish for the new site:\n"
        f"  1. its source ranks through the `Visibility` it is handed, rather "
        f"than closing over candidates ranked somewhere else;\n"
        f"  2. one query against two corpora -- an index holding withheld "
        f"documents and an index that never did -- returns the identical "
        f"response through it, which is the closure argument this milestone "
        f"settled on after four review rounds; and\n"
        f"  3. a test that goes red when (1) stops holding, confirmed by "
        f"breaking it.\n"
        f"Then list the site here, with the test from (3) named beside it."
    )


# -- The status gate: every place the product consults may_surface ------------

#: The domain function that decides *whether* a status may be shown (SEC-13,
#: T-15). Read off the symbol so a rename breaks the import above — loudly — long
#: before this scan quietly finds zero sites for a name nothing has.
STATUS_GATE = may_surface.__name__

#: Every place the product consults the status gate, as
#: ``(module path under theurian/, enclosing function)``.
#:
#: The set the enums.py and mcp/results.py docstrings describe in prose, and the
#: set they got wrong: both said "three layers" and the module docstring said
#: *four* callers while the tree held five — the fifth,
#: ``mcp/tools.py :: _relation_is_visible``, gates each relation endpoint on
#: ``knowledge.get`` and was added after the count was written. A count in a
#: docstring is enforced by nothing; this set is.
#:
#: The five, by responsibility:
#:   - the index builder decides what to write;
#:   - ``knowledge.search`` gates each of its two answer paths — the ranked path
#:     through ``CanonicalVisibility._may_surface`` and the substring fallback
#:     through ``mcp.search._scan``;
#:   - ``knowledge.get`` gates the item it hands over by id, and, per edge,
#:     each endpoint of a relation before publishing it.
STATUS_GATE_CALL_SITES = {
    ("application/index_builder.py", "IndexBuilder._build"),
    ("application/visibility.py", "CanonicalVisibility._may_surface"),
    ("mcp/search.py", "_scan"),
    ("mcp/tools.py", "_relation_is_visible"),
    ("mcp/tools.py", "register.knowledge_get"),
}


def _name_uses(path: pathlib.Path, name: str) -> list[tuple[str, str]]:
    """Every bare-``name`` reference in ``path``, as ``(module path, enclosing function)``.

    ``may_surface`` is a module-level function reached by name, so its uses are
    :class:`ast.Name` nodes. ``from ... import may_surface`` is an
    :class:`ast.alias`, and its ``def`` is a :class:`ast.FunctionDef`; neither is
    a :class:`ast.Name`, so the import lines and the definition are not counted —
    only the places that actually reach the function are. Matches the reference
    rather than the call, so binding it without calling — ``gate = may_surface``
    — counts as the site it is, the same way the ``.admit`` scan above does.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = path.relative_to(SRC).as_posix()
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Name) and child.id == name:
                found.append((module, ".".join(scope) or "<module>"))
            visit(child, scope)

    visit(tree, ())
    return found


def test_every_place_the_product_consults_the_status_gate_is_enumerated() -> None:
    """A new ``may_surface`` call site is a new place a withheld status can leak past.

    ``may_surface`` (SEC-13, T-15) is the single rule for whether a status may be
    shown at all: retired knowledge — deprecated, superseded, rejected — is
    reachable through no flag, because a rejected revision is where the secret
    that caused the rejection still lives. Every path that hands content to a
    caller has to consult it, and ``knowledge.get`` having *no* copy is precisely
    how a caller who could not search for a withheld item could still fetch it
    (``test_knowledge_get_will_not_hand_over_what_search_withheld``).

    This is the control that would have caught the docstring drift #63 phase 0
    fixed: enums.py claimed *four* callers in three layers while the tree held
    five. A count in prose is enforced by nothing. The assertion is an equality
    against the whole spelled-out set, so it fails in both directions — a site
    added, and a known one moved or deleted — and names the sites it found. A new
    site is a decision someone records here, not a diff nobody notices.

    Like the ``.admit`` scan above, this reads names: ``getattr``, a dispatch
    table, or a second status rule under another name all pass it. It is a floor
    on the review a new call site gets, not a proof that an ungated path cannot
    exist.
    """
    sites = sorted(
        {site for path in sorted(SRC.rglob("*.py")) for site in _name_uses(path, STATUS_GATE)}
    )

    assert sites == sorted(STATUS_GATE_CALL_SITES), (
        f"`{STATUS_GATE}` is consulted from {len(sites)} place(s) in the shipped "
        f"source, and the pinned set has {len(STATUS_GATE_CALL_SITES)}:\n"
        + "\n".join(f"  {module} :: {function}" for module, function in sites)
        + "\n\nExpected exactly:\n"
        + "\n".join(
            f"  {module} :: {function}" for module, function in sorted(STATUS_GATE_CALL_SITES)
        )
        + f"\n\n`{STATUS_GATE}` is the one rule for whether a status may surface "
        f"(SEC-13, T-15), and its enums.py / mcp/results.py docstrings state this "
        f"count in prose. Prose enforces nothing: the count read 'four' while the "
        f"tree held five until #63 phase 0. If you added a call site, it is a new "
        f"path a withheld status can leave by — establish that it gates through "
        f"`{STATUS_GATE}` before returning content, add a test that goes red when "
        f"it stops, and only then add it here with that test named beside it. If "
        f"you removed or moved one, amend this set and both docstrings together."
    )


# -- The scope filter: its axes are the axes SECURITY.md publishes ------------

#: The shipped WHERE-clause filter (#63) and the security document that claims to
#: derive its authorization-axis list from it.
INDEX_STORE = SRC / "infrastructure" / "sqlite" / "index_store.py"
SECURITY_MD = REPO_ROOT / "SECURITY.md"

#: The block in SECURITY.md whose ``chunks.<column>`` tokens must be exactly the
#: axes ``_scope`` emits. SECURITY.md's derivation table sources this list to
#: "the predicates the retrieval path actually emits"; these markers make that
#: claim checkable instead of a promise a reader has to take on trust.
ENFORCED_AXES_BEGIN = "enforced-axes:begin"
ENFORCED_AXES_END = "enforced-axes:end"


def _scope_where_axes() -> set[str]:
    """The ``chunks.<column>`` axes ``_scope``'s WHERE clause names, read from source.

    Parses the shipped ``index_store.py``, walks the ``_scope`` function for its
    string literals, and pulls the column out of each ``chunks.<column> = ?``
    predicate. Reads the source rather than calling ``_scope``, so a clause the
    runtime happens not to take on a given branch — the ``status`` predicate is
    added only when ``include_unapproved`` is false — still counts as an axis the
    filter can emit. The docstring beside the clauses mentions ``chunks`` columns
    in prose but writes no ``chunks.<column> = ?``, so it does not match.
    """
    tree = ast.parse(INDEX_STORE.read_text(encoding="utf-8"), filename=str(INDEX_STORE))
    scope = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_scope"
    )
    literals = " ".join(
        node.value
        for node in ast.walk(scope)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return set(re.findall(r"chunks\.(\w+)\s*=\s*\?", literals))


def _security_md_published_axes() -> set[str]:
    """The ``chunks.<column>`` axes SECURITY.md publishes as enforced, from its marked block."""
    text = SECURITY_MD.read_text(encoding="utf-8")
    begin = text.index(ENFORCED_AXES_BEGIN)
    end = text.index(ENFORCED_AXES_END)
    return set(re.findall(r"chunks\.(\w+)", text[begin:end]))


def test_the_axes_security_md_publishes_are_the_axes_the_scope_filter_emits() -> None:
    """Hold SECURITY.md to its own claim: the axes it publishes are the ones ``_scope`` emits.

    SECURITY.md tells a reader that project isolation and status withholding are
    the two authorization axes enforced on retrieval today (T-11, SEC-13), and its
    "derivation, list by list" table sources that list to ``_scope``. That is a
    claim about the shipped filter, and a document naming a control that has
    drifted from — or never matched — the code is exactly the compliance-claims
    defect #115 tracks.

    This reads both sides off source: the ``chunks.<column>`` axes ``_scope``'s
    WHERE clause emits, and the ``chunks.<column>`` tokens SECURITY.md publishes
    in its marked enforced-axes block. Give ``_scope`` a ``chunks.tenant``
    predicate without amending SECURITY.md, or trim a predicate the document still
    advertises, and the two diverge here. The empty-set guard stops the two
    extractors agreeing vacuously if a marker moves or the clause shape changes.
    """
    scope_axes = _scope_where_axes()
    published_axes = _security_md_published_axes()

    assert scope_axes, (
        f"Found no `chunks.<column> = ?` predicate in `_scope` at {INDEX_STORE}. "
        f"The reader broke, not the filter — comparing two empty sets would assert "
        f"nothing. Fix the extractor before trusting a green result here."
    )
    assert published_axes == scope_axes, (
        f"SECURITY.md's enforced-axes block publishes {sorted(published_axes)}, "
        f"but `_scope` emits {sorted(scope_axes)}.\n\n"
        f"SECURITY.md derives its authorization-axis list from `_scope` (its "
        f"'derivation, list by list' table: 'the predicates the retrieval path "
        f"actually emits'). The two have diverged. If `_scope` gained or dropped "
        f"a predicate, re-derive the block delimited by `{ENFORCED_AXES_BEGIN}` "
        f"and `{ENFORCED_AXES_END}` in SECURITY.md to match — and the FR-R1 "
        f"disposition table in docs/architecture/requirements-analysis.md with "
        f"it. A published axis that no predicate emits, or a predicate no document "
        f"names, is the compliance-claims defect #115 tracks."
    )
