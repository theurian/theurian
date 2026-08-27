"""AC-7: the review-finding store is reachable by NO serving path (ADR-0029).

The security boundary of phase-2 slice-2. The store lands parsed findings from a
wholesale rebuild of public git history, and this slice ships **no serving read**:
a findings *search* -- a surface that returns finding content to a caller -- is a
later lane with its own disclosure round. Until that lane is designed, the
property the threat model rests on is not a filter but an **absence**: nothing a
caller can reach hands out a finding, because the serving layer cannot reach the
store at all.

Like ``test_network_call_sites.py`` (T-7), that absence is enforced here by nothing
else, so it is asserted outright -- structurally, over the shipped source, in two
prongs each blind to what the other catches:

**Prong (a) -- no serving-layer module imports the store [AST].** Every module in
the explicitly-named :data:`SERVING_MODULES` set is parsed and its imports are
scanned; none may reach the store port, the store adapter, or the standalone
builder. This catches a store reference added anywhere in the serving layer,
including on a path no test drives -- but it reads *imports*, so a serving module
that reached the store's SQLite *table* directly, importing nothing, would slip
past it. Prong (b) is the half that sees that.

**Prong (b) -- the store's tables never appear in a serving module, and no
registered tool serves a finding [grep + AST].** The distinctive table and
artifact tokens (``findings_metadata``, ``rejected_trailers``, ``theurian-findings``,
the ``findings`` table SQL) are grepped out of every serving module -- the arm
that catches a raw ``SELECT ... FROM findings`` an import scan cannot see. And the
MCP tool registry in ``mcp/tools.py`` is parsed for every ``@server.tool(name=...)``
it registers, pinned to the known read-only set, and asserted to expose no tool
whose name serves a finding. (The runtime companion -- that the *built* server
registers exactly that set and no registered tool reaches a store symbol in its
bytecode -- lives in ``tests/integration/test_findings_tool_registry.py``, because
it constructs a server.)

**The WRITE path is exempt, by design, and that exemption is pinned not assumed.**
``cli/findings_commands.py`` (the ``findings build`` rebuild command) and
``application/findings_builder.py`` are the composition root of the wholesale
rebuild -- a write/maintenance path, like ``index build``, that returns no finding
content. They *legitimately* reach the store, so they are excluded from
:data:`SERVING_MODULES`. :func:`test_the_write_path_modules_do_reach_the_store`
asserts they really do -- which is the guard that proves the import scan fires
against real repository files at all, not only against snippets. The boundary is:
serving RETURNS finding content to a caller; the write path REBUILDS the store and
returns none.

Pure in the sense the T-7 structural arms are: every check parses ``.py`` files as
text or as an AST. Nothing here opens a database, a socket, or a temporary
directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Iterator

import pytest

import theurian

pytestmark = pytest.mark.unit

#: The package as *imported*, not a path relative to this file -- the reckoning
#: ``test_network_call_sites.py`` uses, and for the same reason: a hand-built
#: relative path can drift from the installed package and would then scan a
#: directory the product does not run against, passing whatever the source did.
SRC = pathlib.Path(theurian.__file__).resolve().parent

#: The SERVING layer, named explicitly from the architecture rather than inferred
#: from a directory walk, because the boundary is a judgement about *what returns
#: finding content to a caller*, not about where a file sits. A serving path takes
#: a caller's request and answers it with knowledge; the write/maintenance path
#: rebuilds a derived artifact and answers with counts.
#:
#: - ``mcp/`` -- the whole daemon tool surface a client speaks to.
#: - ``application/retrieval_service.py``, ``application/visibility.py`` -- the
#:   retrieval and gate the tools call.
#: - ``cli/commands.py`` -- the content-returning CLI (the knowledge read/search
#:   commands).
#: - the index read-side -- ``index_store.py``, ``index_query.py``,
#:   ``index_scan.py``, ``index_forest.py`` -- what a search reads its candidates
#:   from.
#:
#: NOT here, deliberately: ``cli/findings_commands.py`` and
#: ``application/findings_builder.py`` are the write path (see the module
#: docstring), and ``application/project_service.py`` owns the *path* helper
#: ``findings_for`` for both build and serve, so it names the artifact without
#: serving it.
SERVING_MODULES: tuple[str, ...] = (
    "mcp/__init__.py",
    "mcp/tools.py",
    "mcp/search.py",
    "mcp/results.py",
    "application/retrieval_service.py",
    "application/visibility.py",
    "cli/commands.py",
    "infrastructure/sqlite/index_store.py",
    "infrastructure/sqlite/index_query.py",
    "infrastructure/sqlite/index_scan.py",
    "infrastructure/sqlite/index_forest.py",
)

#: The write/maintenance path that *legitimately* reaches the store. Excluded from
#: the serving set above; asserted to really reach the store below, so the import
#: scan is proven to fire against real files.
WRITE_PATH_MODULES: tuple[str, ...] = (
    "cli/findings_commands.py",
    "application/findings_builder.py",
)

#: A store *module* is named by any of these as a dotted-path component. The three
#: modules the store lives in: the port, the SQLite adapter, and the builder that
#: is its only writer. Matched on a component boundary, not by substring, so an
#: unrelated ``review_finding`` (the domain *record* module, which is not the
#: store) does not read as a hit.
STORE_MODULE_COMPONENTS = frozenset({"review_finding_store", "findings_store", "findings_builder"})

#: A store *symbol*, named directly in a ``from ... import`` or a bare reference.
STORE_SYMBOLS = frozenset(
    {"ReviewFindingStore", "SqliteReviewFindingStore", "FindingsBuilder", "FindingsBuildRequest"}
)

#: Callables that import by string, where the string is the module that matters --
#: the one-line way a reach hides from an import-statement scan.
_DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module", "importlib.import_module"})


def _dotted(node: ast.AST) -> str | None:
    """The dotted name of a ``Name``/``Attribute`` chain, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _module_hits(dotted: str) -> set[str]:
    """The store-module components ``dotted`` is built from, matched on a boundary."""
    parts = set(dotted.split("."))
    return {component for component in STORE_MODULE_COMPONENTS if component in parts}


def _store_references(source: str, module: str) -> set[str]:
    """Every store module-component or symbol ``source`` imports, as strings.

    Three arms, each a form the ones before it miss:

    - ``import a.b.findings_store`` / ``import ... as fs`` -- resolved to the
      module reached, not to a spelling;
    - ``from a.b.findings_store import SqliteReviewFindingStore`` -- both the
      module *and* the imported symbol count, so aliasing the symbol
      (``import ... as Store``) still names the module;
    - ``importlib.import_module("a.b.findings_store")`` -- a string import, which
      is how a reach hides from an ``import``-statement scan in one line.

    Relative imports (``from . import x``) are skipped: they reach into this
    package by a name this scan cannot resolve, and the store is always imported
    by its absolute dotted path.
    """
    tree = ast.parse(source, filename=module)
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                reached |= _module_hits(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            reached |= _module_hits(node.module)
            for alias in node.names:
                reached |= _module_hits(f"{node.module}.{alias.name}")
                if alias.name in STORE_SYMBOLS:
                    reached.add(alias.name)
        elif isinstance(node, ast.Call) and _dotted(node.func) in _DYNAMIC_IMPORTERS:
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    reached |= _module_hits(argument.value)
    return reached


#: One case per form the scan claims to see, and per form it claims to let past --
#: the guard T-7 keeps for the same reason: without it the enumeration below could
#: go green while the scanner resolved nothing, and a broken extractor reads,
#: from the outside, exactly like a serving layer that never touched the store.
#:
#: The negative cases are as load-bearing as the positives. ``review_finding`` (the
#: domain *record*) and ``retrieval_service`` (a serving collaborator) must NOT
#: read as the store, or every serving module would false-positive and the pin
#: would be noise.
_SCANNER_CASES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore",
        frozenset({"findings_store", "SqliteReviewFindingStore"}),
    ),
    (
        "from theurian.domain.ports.review_finding_store import ReviewFindingStore",
        frozenset({"review_finding_store", "ReviewFindingStore"}),
    ),
    (
        "from theurian.application.findings_builder import FindingsBuilder, FindingsBuildRequest",
        frozenset({"findings_builder", "FindingsBuilder", "FindingsBuildRequest"}),
    ),
    ("import theurian.infrastructure.sqlite.findings_store", frozenset({"findings_store"})),
    ("import theurian.infrastructure.sqlite.findings_store as fs", frozenset({"findings_store"})),
    (
        "from theurian.infrastructure.sqlite.findings_store import (\n"
        "    SqliteReviewFindingStore as Store,\n)",
        # `alias.name` is the *original* name, not the `as Store` binding, so the
        # module and the imported symbol are both seen through the rename.
        frozenset({"findings_store", "SqliteReviewFindingStore"}),
    ),
    (
        "importlib.import_module('theurian.infrastructure.sqlite.findings_store')",
        frozenset({"findings_store"}),
    ),
    ('__import__("theurian.application.findings_builder")', frozenset({"findings_builder"})),
    # Negatives: the record module, a serving collaborator, the index store, a
    # comment, and a relative import must all read as nothing.
    ("from theurian.domain.review_finding import ReviewFinding, FindingLoad", frozenset()),
    ("from theurian.application.retrieval_service import RetrievalService", frozenset()),
    ("import theurian.infrastructure.sqlite.index_store", frozenset()),
    ("# a CRITICAL finding recorded in review round 1 of PR #112", frozenset()),
    ("from . import findings_store", frozenset()),
)


@pytest.mark.parametrize(
    "source, expected",
    _SCANNER_CASES,
    ids=[case[0].splitlines()[0][:60] for case in _SCANNER_CASES],
)
def test_the_store_import_scan_sees_each_reaching_form_and_no_other(
    source: str, expected: frozenset[str]
) -> None:
    """Guards the guard below, which is worthless the moment its scanner stops seeing.

    A structural pin fails in a way nobody notices: if ``_store_references`` stops
    resolving imports, the serving-module scan keeps passing forever, and the
    silence looks exactly like a serving layer that never reached the store. So
    each form the scan claims to catch is asserted against a snippet here, and each
    form it claims to let past is asserted to produce nothing.

    The ``from ... import`` and aliased forms are not decoration: aliasing the
    symbol hides it from a name-only scan, and ``import_module("...findings_store")``
    is the one-line way around an ``import``-statement scan.
    """
    found = _store_references(source, "snippet.py")

    assert found == set(expected), (
        f"the store scan read `{source}` as {sorted(found)}, expected "
        f"{sorted(expected)}. The scanner is broken, not the product: fix "
        f"`_store_references` before trusting a green result from "
        f"`test_no_serving_module_imports_the_finding_store`, which would keep "
        f"passing with a scanner that resolves nothing."
    )


def _serving_source(module: str) -> str:
    """The text of a serving module, or a hard failure if the path drifted.

    A moved or renamed serving module would make the scan read an empty string and
    pass vacuously -- the exact failure the parser-registry drift guard exists to
    stop -- so a missing path is a loud error, not a skip.
    """
    path = SRC / module
    assert path.is_file(), (
        f"the serving module {module} is not at {path}. SERVING_MODULES has "
        f"drifted from the shipped package; a scan over a path that does not exist "
        f"reads nothing and would pass this boundary vacuously. Fix the list."
    )
    return path.read_text(encoding="utf-8")


def test_no_serving_module_imports_the_finding_store() -> None:
    """Prong (a): the serving layer cannot reach the store, so it cannot serve one.

    AC-7, the import half. A findings *search* is a later lane with its own
    disclosure round; until it exists, the property the threat model rests on is
    that no path a caller reaches can hand out a finding. That holds structurally
    if -- and only if -- the serving layer imports neither the store port, the
    store adapter, nor the standalone builder: with no import, there is no
    transitive call, on any path, driven or not.

    The one exemption is the write path (:data:`WRITE_PATH_MODULES`), which is not
    in the serving set and is pinned to reach the store by the test below -- so
    this assertion is the *absence* of a store reference in the *serving* set,
    proven against every serving module, not a subset that happens to be driven.
    """
    offenders = {
        module: sorted(hits)
        for module in SERVING_MODULES
        if (hits := _store_references(_serving_source(module), module))
    }

    assert not offenders, (
        "a serving-layer module imports the review-finding store:\n"
        + "\n".join(f"  {module} :: {names}" for module, names in sorted(offenders.items()))
        + "\n\nThe store lands parsed findings and ships NO serving read in this "
        "slice (ADR-0029 phase-2 slice-2): a findings search is a later lane with "
        "its own disclosure round. What stands in for that gate until it is "
        "designed is that nothing a caller reaches can touch the store. A serving "
        "module importing the store port, adapter, or builder is a path to a "
        "finding that the disclosure round has not reviewed.\n\n"
        "If this is the serving lane landing, it does not land here: it lands with "
        "the disclosure round ADR-0029 owes it. The write/maintenance path "
        "(`findings build`) is exempt and lives in cli/findings_commands.py and "
        "application/findings_builder.py, which are deliberately outside "
        "SERVING_MODULES."
    )


def test_the_write_path_modules_do_reach_the_store() -> None:
    """The write path really reaches the store -- so the scan is proven to fire.

    The mirror of the prong-(a) assertion, and the reason it is not vacuous. If
    ``_store_references`` silently resolved nothing against a real repository file,
    the serving-module scan would pass no matter what the source did. These two
    files are the composition root of the wholesale rebuild and *must* name the
    store; asserting they do is what demonstrates the scanner sees a real import,
    not only the snippets in :data:`_SCANNER_CASES`.

    It also pins the exemption as deliberate: the write path is where a store
    reference is correct, so a reviewer reading this list knows the boundary was
    drawn on purpose, not by omission.
    """
    reached = {
        module: _store_references(_serving_source(module), module) for module in WRITE_PATH_MODULES
    }

    assert "findings_store" in reached["cli/findings_commands.py"], (
        "cli/findings_commands.py no longer imports the store adapter; either the "
        "write path moved (update WRITE_PATH_MODULES) or the import scan stopped "
        "resolving real files -- in which case prong (a) is now vacuous."
    )
    assert "findings_builder" in reached["cli/findings_commands.py"]
    assert "review_finding_store" in reached["application/findings_builder.py"], (
        "application/findings_builder.py no longer imports the store port; the "
        "import scan may have stopped resolving real files, making prong (a) "
        "vacuous."
    )


# -- Prong (b) grep: the tables an import scan cannot see --------------------

#: The store's distinctive artifact tokens: the two side tables, the file-name
#: prefix, none of which has any innocent use in a serving module. The bare word
#: ``findings`` is deliberately NOT here -- it appears in serving *comments* about
#: review-round findings -- so only the compound, unambiguous tokens are grepped.
STORE_ARTIFACT_TOKENS: tuple[str, ...] = (
    "findings_metadata",
    "rejected_trailers",
    "theurian-findings",
)

#: The ``findings`` table reached by SQL. Case-sensitive on the uppercase SQL
#: keyword so it matches ``FROM findings`` / ``INSERT INTO findings`` / ``CREATE
#: TABLE findings`` and does not fire on lowercase prose like "...history into
#: findings." in a docstring.
_FINDINGS_TABLE_SQL = re.compile(r"(?:FROM|INTO|UPDATE|JOIN)\s+findings\b|CREATE TABLE findings\b")


def _store_table_hits(source: str) -> list[str]:
    """Every store table/artifact token or ``findings``-table SQL match in ``source``."""
    hits = [token for token in STORE_ARTIFACT_TOKENS if token in source]
    hits.extend(_FINDINGS_TABLE_SQL.findall(source))
    return hits


def test_no_serving_module_reaches_the_store_tables_by_sql_or_name() -> None:
    """Prong (b): a serving path could reach the TABLE directly, importing nothing.

    Prong (a) reads imports, so a serving module that opened the store's SQLite
    file and issued ``SELECT ... FROM findings`` -- naming no store class -- would
    pass it. This is the arm that catches that: the store's table names and its
    file-name prefix have no innocent use in a serving module, so any occurrence is
    a serving path reaching into the findings artifact by hand.
    """
    offenders = {
        module: hits
        for module in SERVING_MODULES
        if (hits := _store_table_hits(_serving_source(module)))
    }

    assert not offenders, (
        "a serving-layer module names the review-finding store's tables:\n"
        + "\n".join(f"  {module} :: {hits}" for module, hits in sorted(offenders.items()))
        + "\n\nThe store's tables (findings, rejected_trailers, findings_metadata) "
        "carry parsed findings that this slice ships no reviewed way to serve. A "
        "serving module reaching them by raw SQL is a disclosure path that skips "
        "the port -- and the import scan (prong a), which reads names, cannot see "
        "it. The findings serving lane lands with its disclosure round, not by a "
        "hand-written query in the read path."
    )


def test_the_table_grep_detects_the_store_tables_in_the_adapter() -> None:
    """Guards prong (b): the grep is proven to fire against the real store files.

    A grep that matched nothing would pass the serving-module scan for a serving
    module that *did* reach the table. So the store's own adapter and schema are
    asserted to contain exactly the tokens the serving scan forbids -- if this goes
    red, the grep has stopped seeing the tables and the serving scan above is
    vacuous, whatever it reports.
    """
    adapter = (SRC / "infrastructure/sqlite/findings_store.py").read_text(encoding="utf-8")
    schema = (SRC / "infrastructure/sqlite/findings_schema.py").read_text(encoding="utf-8")

    adapter_hits = set(_store_table_hits(adapter))
    assert {"findings_metadata", "rejected_trailers"} <= adapter_hits, (
        "the table grep no longer finds the store's tables in its own adapter; "
        "prong (b)'s serving scan is now vacuous. Fix STORE_ARTIFACT_TOKENS / the "
        "SQL pattern before trusting a green serving scan."
    )
    assert _FINDINGS_TABLE_SQL.search(adapter), "the findings-table SQL pattern no longer matches"
    assert "theurian-findings" in adapter, "the store file-name prefix token is no longer detected"
    assert "CREATE TABLE findings" in " ".join(_store_table_hits(schema)) or bool(
        _FINDINGS_TABLE_SQL.search(schema)
    ), "the findings-table DDL is no longer detected in the schema"


# -- Prong (b) AST: the MCP tool registry serves no finding ------------------

#: The read-only tools this slice ships (Milestone 3's surface). Pinned as a whole
#: set, not a subset, so a *new* ``@server.tool(name=...)`` fails this test until
#: it is classified here -- the drift guard that stops a findings-serving tool from
#: being added silently and read as "not on the list, so fine".
KNOWN_TOOL_NAMES = frozenset(
    {
        "knowledge.search",
        "knowledge.get",
        "knowledge.status",
        "project.list",
        "system.capabilities",
    }
)

#: A tool name that would serve a finding. Matched case-insensitively so
#: ``knowledge.findings`` and ``review.finding`` both trip it.
_FINDING_TOOL_PATTERN = re.compile(r"finding", re.IGNORECASE)


def _registered_tool_names(tools_source: str) -> Iterator[str]:
    """Every ``@server.tool(name="...")`` string literal in ``mcp/tools.py``.

    Reads the registration source rather than a running server, so it stays a pure
    unit check. It finds a ``.tool(...)`` call carrying a ``name=<str constant>``
    keyword -- the one mechanism all five tools register through. A tool registered
    by some *other* mechanism (a dynamic ``add_tool``) would evade this AST arm;
    the runtime companion in ``tests/integration/test_findings_tool_registry.py``,
    which enumerates the built server, is the arm that would catch that.
    """
    tree = ast.parse(tools_source, filename="mcp/tools.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "tool"):
            continue
        for keyword in node.keywords:
            value = keyword.value
            if (
                keyword.arg == "name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                yield value.value


def test_the_tool_registry_registers_exactly_the_known_read_tools() -> None:
    """Guards the finding-tool check: a new tool must be classified, not defaulted in.

    Pinned as a whole-set equality so a new ``@server.tool`` reddens this until
    someone adds it to :data:`KNOWN_TOOL_NAMES` -- which is the moment they must
    decide whether it serves a finding. Without the pin, a findings-serving tool
    whose name did not literally contain "finding" would sail past the check below;
    with it, every new tool is seen.
    """
    tools_source = (SRC / "mcp/tools.py").read_text(encoding="utf-8")

    registered = set(_registered_tool_names(tools_source))

    assert registered == set(KNOWN_TOOL_NAMES), (
        f"mcp/tools.py registers {sorted(registered)}, pinned set is "
        f"{sorted(KNOWN_TOOL_NAMES)}. A tool was added or removed. If you added "
        f"one, classify it: does it return finding content? If so it needs the "
        f"disclosure round ADR-0029 owes the findings serving lane, not a place on "
        f"this list. If not, add its name here."
    )


def test_no_registered_tool_name_serves_a_finding() -> None:
    """Prong (b): the daemon tool surface exposes no finding-serving tool (AC-7).

    The store holds parsed findings and this slice ships no reviewed way to serve
    them, so the daemon -- the surface a caller actually speaks to -- must register
    no tool that hands one out. Read from the registration source; the whole-set
    pin above is what makes "no name says finding" meaningful, by forcing every new
    tool through classification first.
    """
    tools_source = (SRC / "mcp/tools.py").read_text(encoding="utf-8")

    serving = sorted(
        name for name in _registered_tool_names(tools_source) if _FINDING_TOOL_PATTERN.search(name)
    )

    assert not serving, (
        f"the MCP tool registry exposes finding-serving tool(s): {serving}. A "
        f"findings search returns parsed review findings to a caller, which is the "
        f"disclosure surface ADR-0029 defers to a later lane with its own review "
        f"round. It does not land as a tool here."
    )
