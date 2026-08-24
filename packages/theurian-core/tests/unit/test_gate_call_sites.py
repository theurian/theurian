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
from collections.abc import Iterator

import pytest

import theurian
from theurian.application.retrieval_service import ResultGate
from theurian.domain.enums import may_disclose, may_surface

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


def _iter_nodes_with_scope(tree: ast.AST) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
    """Yield ``(node, scope)`` for every node, ``scope`` the dotted enclosing defs.

    Shared by every scan in this file so they agree on what "enclosing function"
    means. A ``def``/``class`` extends the scope for its body and is not itself
    yielded as a candidate; everything else is yielded once, under the scope it
    sits in.
    """

    def walk(node: ast.AST, scope: tuple[str, ...]) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, (*scope, child.name))
            else:
                yield child, scope
                yield from walk(child, scope)

    yield from walk(tree, ())


def _here(module: str, scope: tuple[str, ...]) -> tuple[str, str]:
    """A scan hit as ``(module path under theurian/, enclosing function)``."""
    return module, ".".join(scope) or "<module>"


def _attribute_uses(path: pathlib.Path) -> list[tuple[str, str]]:
    """Every ``.admit`` in ``path``, as ``(module path, enclosing function)``.

    Matches the *attribute*, not the call, so binding the method without calling
    it — ``publish = gate.admit`` — counts as the call site it is.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = path.relative_to(SRC).as_posix()
    return [
        _here(module, scope)
        for node, scope in _iter_nodes_with_scope(tree)
        if isinstance(node, ast.Attribute) and node.attr == ATTRIBUTE
    ]


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

#: The domain function that decides whether this *deployment* may disclose an
#: item's sensitivity class (SEC-13, #119), read off the symbol for the same
#: reason. The second axis, enumerated the same way as the first, because it fails
#: the same way: a read path that forgets it publishes above-ceiling content, and
#: nothing in the type system says a path was meant to consult it.
DISCLOSURE_GATE = may_disclose.__name__

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
#: The six, by responsibility, and the behavioural test that holds each to
#: gating (so removing a site is caught here *and* the removal turns one red):
#:   - the index builder decides what to write
#:     (``test_index_builder`` withholds unapproved from the index);
#:   - ``knowledge.search`` gates each of its two answer paths — the ranked path
#:     through ``CanonicalVisibility._may_surface`` and the substring fallback
#:     through ``mcp.search._scan``
#:     (``test_a_withheld_document_changes_nothing_a_caller_can_see``);
#:   - ``knowledge.get`` gates the item it hands over by id, and, per edge, each
#:     endpoint of a relation before publishing it
#:     (``test_knowledge_get_will_not_hand_over_what_search_withheld`` and the
#:     relation-visibility tests in ``tests/integration/test_mcp_tools.py``);
#:   - the withdrawal purge decides which revisions a still-published index must
#:     stop holding (ADR-0024 decision 5) — the one *inverse* use, naming what is
#:     non-surfaceable so the purge and the surfacing gate cannot disagree
#:     (``test_a_withdrawal_purges_the_published_index_without_a_separate_build``
#:     and ``test_a_restored_item_survives_the_replay_a_later_apply_forces``).
STATUS_GATE_CALL_SITES = {
    ("application/index_builder.py", "IndexBuilder._build"),
    ("application/migration_engine.py", "revisions_to_purge"),
    ("application/visibility.py", "CanonicalVisibility._may_surface"),
    ("mcp/search.py", "_scan"),
    ("mcp/tools.py", "_relation_is_visible"),
    ("mcp/tools.py", "register.knowledge_get"),
}

#: Every place the product consults the disclosure gate, as
#: ``(module path under theurian/, enclosing function)``.
#:
#: Four: three canonical-side read paths a caller can reach content through
#: (#119 phase 2) and the build side that decides what exists to be reached
#: (#119 phase 3), each with the test that holds it to gating:
#:   - the ranked path's canonical re-check on the item's *current* level
#:     (``test_the_ranked_path_withholds_a_document_reclassified_after_the_build``);
#:   - ``knowledge.get``'s gate on the item it hands over by id, refused in the
#:     words that refuse an absent one
#:     (``test_a_narrow_ceiling_withholds_the_item_from_search_and_from_get`` and
#:     ``test_absence_proof.py``'s generated refusal equality);
#:   - the per-edge gate on each endpoint of a relation, because an edge's target
#:     id and ``note`` are the disclosure whether or not the body is
#:     (``test_a_relation_to_an_above_ceiling_item_is_not_published``);
#:   - the index builder, which decides what is *written* rather than what is
#:     shown, so that an above-ceiling document's text never reaches the FTS5
#:     tables whose collection statistics price every visible row -- the T-17a
#:     mechanism, moved to this axis (ADR-0025 part 1,
#:     ``test_forest_builder.py::test_an_above_ceiling_document_reaches_neither_
#:     half_of_the_index``).
#:
#: **The gates spelled as a predicate are deliberately absent, and they are not
#: further sites.** This axis is enforced in two spellings, and a scan that reads
#: names can only see one of them:
#:   - ``mcp/search.py :: _scan`` hands the grant to the *canonical* store as a
#:     SQL predicate, so an above-ceiling row is never materialised for a Python
#:     check to run on (``test_the_unranked_scan_withholds_an_above_ceiling_item``,
#:     and the cost note on ``list_items_by_status``);
#:   - every retriever hands it to the *index* the same way, through
#:     ``SqliteIndexStore._scope`` and ``._node_scope`` (#119 phase 4), which emit
#:     ``chunks.sensitivity IN (…)`` and ``nodes.sensitivity IN (…)`` in the same
#:     statement as the match.
#: Adding either here would mean deleting the predicate that makes it cheap. What
#: covers the predicate side instead is
#: ``test_the_axes_security_md_publishes_are_the_axes_the_scope_filter_emits``
#: below, which reads ``_scope``'s own clause literals -- so between the two
#: tests, a gate that disappears is caught whichever way it was written.
#:
#: ``cli/index_commands.py :: _indexable_items`` is absent for the reason it is
#: absent from the status set above: it *repeats* the builder's selection rule
#: inline rather than calling either gate, so that "what was there to be indexed"
#: and "what got indexed" stay two derivations that can be caught disagreeing.
DISCLOSURE_GATE_CALL_SITES = {
    ("application/index_builder.py", "IndexBuilder._build"),
    ("application/visibility.py", "CanonicalVisibility._may_surface"),
    ("mcp/tools.py", "_relation_is_visible"),
    ("mcp/tools.py", "register.knowledge_get"),
}


#: The module ``may_surface`` lives in, matched against imports to follow the
#: symbol rather than a spelling.
ENUMS_MODULE = "theurian.domain.enums"


def _dotted(node: ast.AST) -> str | None:
    """The dotted name of a ``Name``/``Attribute`` chain, else ``None``.

    ``theurian.domain.enums`` → ``"theurian.domain.enums"``; a subscript or call
    in the chain → ``None``, because it is not a plain module reference.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _enums_bindings(tree: ast.AST, symbol: str) -> tuple[set[str], set[str]]:
    """Per module: the local names bound to ``symbol``, and to the enums module.

    So the scan follows the import, not a spelling. ``import may_surface as gate``
    binds ``gate`` to the function (a *direct* name); ``import theurian.domain.enums
    as e`` and ``from theurian.domain import enums`` bind a name to the *module*,
    through which the function is reached as ``e.may_surface``. Both are how a
    sixth call site could hide from a scan that only knew the bare name — the
    adversarial review demonstrated both survive a bare-``Name`` scan.

    ``symbol`` is a parameter because there are two gates on this path and they
    fail identically. A second copy of this resolver for ``may_disclose`` would be
    the same rule written twice, which is what the enumeration exists to stop
    happening to the rules themselves.
    """
    direct: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level:
            if node.module == ENUMS_MODULE:
                direct |= {a.asname or a.name for a in node.names if a.name == symbol}
            elif node.module == "theurian.domain":
                module_aliases |= {a.asname or a.name for a in node.names if a.name == "enums"}
        elif isinstance(node, ast.Import):
            module_aliases |= {a.asname or a.name for a in node.names if a.name == ENUMS_MODULE}
    return direct, module_aliases


def _gate_uses(path: pathlib.Path, symbol: str) -> list[tuple[str, str]]:
    """Every place ``path`` reaches ``symbol``, as ``(module path, enclosing function)``.

    Resolves imports rather than matching a spelling, so all reaching forms count:
    the bare name it is imported under (``may_surface`` or an ``as`` alias) and an
    attribute on the imported module (``enums.may_surface``). Matches the reference,
    not the call, so binding without calling counts. The ``def`` and the ``import``
    lines are a ``FunctionDef``/``ast.alias`` and are matched by neither arm.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = path.relative_to(SRC).as_posix()
    direct, module_aliases = _enums_bindings(tree, symbol)
    found: list[tuple[str, str]] = []
    for node, scope in _iter_nodes_with_scope(tree):
        by_bare_name = isinstance(node, ast.Name) and node.id in direct
        by_module_attr = (
            isinstance(node, ast.Attribute)
            and node.attr == symbol
            and _dotted(node.value) in module_aliases
        )
        if by_bare_name or by_module_attr:
            found.append(_here(module, scope))
    return found


def _sites_reaching(symbol: str) -> list[tuple[str, str]]:
    """The whole shipped tree's call sites for ``symbol``, sorted and deduplicated."""
    return sorted({site for path in sorted(SRC.rglob("*.py")) for site in _gate_uses(path, symbol)})


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

    The scan follows the import, so the three reaching forms all count: the bare
    name, an ``as`` alias (``import may_surface as gate; gate(...)``), and an
    attribute on the imported module (``enums.may_surface(...)``). The adversarial
    review showed the last two survive a scan that only knew the bare name; both
    are now killed, which is what makes enums.py's "a sixth cannot land unnoticed"
    true rather than aspirational. What it still cannot see is a name it cannot
    resolve statically — ``getattr``, a dispatch table, a re-export under a third
    name — so it is a floor on the review a new call site gets, not a proof that
    an ungated path cannot exist.
    """
    sites = _sites_reaching(STATUS_GATE)

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


def test_every_place_the_product_consults_the_disclosure_gate_is_enumerated() -> None:
    """A new ``may_disclose`` call site is a new place a deployment ceiling can be forgotten.

    The sibling of the status assertion above, and it exists for the reason that
    one turned out to be needed: a second gate on the same paths, consulted from
    several places, whose omission from any one of them is invisible until
    somebody reaches content through it. ``knowledge.get`` having no copy of the
    *status* gate is how a caller who could not search for a withheld item could
    still fetch it; the same shape on this axis would be a caller who could not
    search for an above-ceiling item fetching it by id, or reading its id and its
    ``note`` off a relation.

    Equality against the whole spelled-out set, so it fails in both directions.
    A site added is a new disclosure path to argue for; a site removed or moved is
    a gate that has quietly stopped being consulted, and either way the failure
    names what it found.

    The scan is the same resolver the status assertion uses, so all three reaching
    forms count here too -- bare name, ``as`` alias, and module attribute.
    """
    sites = _sites_reaching(DISCLOSURE_GATE)

    assert sites == sorted(DISCLOSURE_GATE_CALL_SITES), (
        f"`{DISCLOSURE_GATE}` is consulted from {len(sites)} place(s) in the shipped "
        f"source, and the pinned set has {len(DISCLOSURE_GATE_CALL_SITES)}:\n"
        + "\n".join(f"  {module} :: {function}" for module, function in sites)
        + "\n\nExpected exactly:\n"
        + "\n".join(
            f"  {module} :: {function}" for module, function in sorted(DISCLOSURE_GATE_CALL_SITES)
        )
        + f"\n\n`{DISCLOSURE_GATE}` is the one rule for whether this deployment may "
        f"disclose an item's sensitivity class (SEC-13, #119). If you added a call "
        f"site, it is a new path above-ceiling content can leave by -- establish that "
        f"it gates before returning content, add a test that goes red when it stops, "
        f"and only then add it here with that test named beside it. If you removed "
        f"one, say which query or which build-side exclusion now enforces that path "
        f"instead, the way `mcp/search.py :: _scan` is accounted for above."
    )


# -- The scope filter: its axes are the axes the documents publish ------------

#: The shipped WHERE-clause filter (#63) and the two documents that claim to
#: derive their authorization-axis list from it.
INDEX_STORE = SRC / "infrastructure" / "sqlite" / "index_store.py"
SECURITY_MD = REPO_ROOT / "SECURITY.md"
REGISTER_MD = REPO_ROOT / "docs" / "architecture" / "requirements-analysis.md"

#: Every document that publishes the enforced-axis list, each pinned to ``_scope``.
#: A third copy of ``{project_id, status}`` that no test read is how a count
#: drifts, so both copies are checked against the one source.
PINNED_AXIS_DOCS = (
    ("SECURITY.md", SECURITY_MD),
    ("requirements-analysis.md", REGISTER_MD),
)

#: The block, in each of those documents, whose ``chunks.<column>`` tokens *and*
#: spelled count must both match ``_scope``. The markers name the pinning test
#: *file* (stable), not a function, so renaming a test does not silently orphan a
#: document from its check.
ENFORCED_AXES_BEGIN = "enforced-axes:begin"
ENFORCED_AXES_END = "enforced-axes:end"

#: Spelled cardinals, so "**two** axes" in a document is pinned to the number of
#: axes listed beside it — the count-in-prose drift ``may_surface`` had, kept off
#: the document side too.
_CARDINALS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}


def _scope_where_axes() -> set[str]:
    """The ``chunks.<column>`` axes ``_scope``'s WHERE clause names, read from source.

    Parses the shipped ``index_store.py`` and pulls the column out of every
    ``chunks.<column>`` that a comparison operator follows, inside ``_scope``'s
    clause literals. Three deliberate choices:

    - **Any comparison operator, not only ``= ?``.** The next axis is as likely to
      arrive as ``chunks.sensitivity <= ?`` or ``chunks.acl_group IN (?)`` as an
      equality; a regex pinned to ``= ?`` would silently miss it.
    - **The docstring is excluded.** ``ast.walk`` would otherwise read the prose
      beside the clauses, where a future sentence naming ``chunks.foo`` would add
      a phantom axis that this test's own SECURITY.md marker could then be edited
      to "match" — the #115 class, reproduced inside the test.
    - **Source, not a call.** A clause the runtime takes on only one branch — the
      ``status`` predicate, added only when ``include_unapproved`` is false —
      still counts as an axis the filter can emit.
    """
    tree = ast.parse(INDEX_STORE.read_text(encoding="utf-8"), filename=str(INDEX_STORE))
    scope = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_scope"),
        None,
    )
    assert scope is not None, (
        f"No function named `_scope` in {INDEX_STORE}; it was renamed or moved, and "
        f"this test is now deriving axes from nothing. Point it at the new name — "
        f"the watcher needs a watcher, the way `ResultGate.admit` has one above."
    )

    doc = ast.get_docstring(scope, clean=False)
    literals = " ".join(
        node.value
        for node in ast.walk(scope)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != doc
    )
    return set(
        re.findall(
            r"chunks\.(\w+)(?=\s*(?:[=<>!]|\b(?:IN|IS|LIKE|NOT)\b))",
            literals,
            re.IGNORECASE,
        )
    )


def _published_axes_and_count(path: pathlib.Path) -> tuple[set[str], list[int]]:
    """A document's marked block: its ``chunks.<column>`` axes and every spelled count in it."""
    text = path.read_text(encoding="utf-8")
    begin = text.index(ENFORCED_AXES_BEGIN)
    end = text.index(ENFORCED_AXES_END)
    block = text[begin:end]
    axes = set(re.findall(r"chunks\.(\w+)", block))
    counts = [_CARDINALS[w] for w in re.findall(r"[a-z]+", block.lower()) if w in _CARDINALS]
    return axes, counts


@pytest.mark.parametrize("label, path", PINNED_AXIS_DOCS, ids=[doc[0] for doc in PINNED_AXIS_DOCS])
def test_the_axes_security_md_publishes_are_the_axes_the_scope_filter_emits(
    label: str, path: pathlib.Path
) -> None:
    """Hold each document to its own claim: the axes it publishes are the ones ``_scope`` emits.

    SECURITY.md tells a reader which authorization axes are enforced on retrieval
    today -- project isolation, status withholding and, since #119 phase 4, the
    deployment's disclosure class (T-11, SEC-13) -- and the FR-R1 register repeats
    the list; both source it to ``_scope``. Neither the axes nor the count is
    spelled here, deliberately: this test derives both from the shipped source, so
    the next axis needs no edit to it. A document
    naming a control that has drifted from — or never matched — the code is exactly
    the compliance-claims defect #115 tracks, and a *third* copy nothing reads is
    how the count drifts, so both documents are pinned here against one source.

    Fails three ways, each demonstrated by mutation: give ``_scope`` a
    ``chunks.tenant`` (or ``chunks.sensitivity <=``) predicate a document does not
    publish; drop a predicate a document still advertises; or add an axis to a
    document's block without correcting its spelled count. The empty-set guard
    stops the two extractors agreeing vacuously if a marker moves.
    """
    scope_axes = _scope_where_axes()

    assert scope_axes, (
        f"Found no `chunks.<column>` predicate in `_scope` at {INDEX_STORE}. The "
        f"reader broke, not the filter — comparing two empty sets would assert "
        f"nothing. Fix the extractor before trusting a green result here."
    )

    published_axes, published_counts = _published_axes_and_count(path)

    assert published_axes == scope_axes, (
        f"{label}'s enforced-axes block publishes {sorted(published_axes)}, but "
        f"`_scope` emits {sorted(scope_axes)}.\n\n"
        f"{label} derives its authorization-axis list from `_scope`. The two have "
        f"diverged. If `_scope` gained or dropped a predicate, re-derive the block "
        f"delimited by `{ENFORCED_AXES_BEGIN}`/`{ENFORCED_AXES_END}` in both "
        f"SECURITY.md and docs/architecture/requirements-analysis.md to match. A "
        f"published axis that no predicate emits, or a predicate no document names, "
        f"is the compliance-claims defect #115 tracks."
    )

    assert set(published_counts) == {len(scope_axes)}, (
        f"{label}'s enforced-axes block spells its count as {published_counts} but "
        f"lists {len(scope_axes)} axis/axes ({sorted(scope_axes)}). Correct the "
        f"spelled number inside the markers so a third axis cannot land while the "
        f"prose still says 'two' — the count-in-prose drift `may_surface` had, kept "
        f"off the document side."
    )
