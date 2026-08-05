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

import pytest

import theurian
from theurian.application.retrieval_service import ResultGate

pytestmark = pytest.mark.unit

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
