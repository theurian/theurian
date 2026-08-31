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
prongs each blind to what the other catches.

**How the scanned population is assembled matters as much as the scan itself.**
:data:`SERVING_MODULES` is not one hand-written list a human must remember to
extend every time a file is added. ``mcp/``, ``daemon/`` and ``review/`` are
walked wholesale (every ``.py`` file under any of the three, at any depth, via
:func:`_walk_python_modules`) -- none of the three carries the findings write
path, so nothing under any of them needs excluding, and a brand-new file under
any of them is scanned automatically with no list to fall out of date.
``review/`` is walked rather than acknowledged like most of the rest of the
package (see below) because it is *not* an ordinary non-serving package: its
own docstring names it the future home of review-knowledge serving code
(Milestone 7, owned by no open issue since ``#129`` closed on the wording), so a
package-level acknowledgement there would wave
through the exact file this whole test guards against -- measured by a
round-two review, which planted ``review/findings_search.py`` importing the
store and watched it pass the entire suite, because ``review/`` was in neither
the walked set nor any completeness bucket. ``application/`` and ``cli/`` do
carry the write path (``application/findings_builder.py``,
``cli/findings_commands.py``), so those two stay hand-picked instead --
:func:`test_every_shipped_python_module_is_classified` is the completeness
guard for that choice, forcing a NEW ``application/`` or ``cli/`` file into one
of three named buckets (serving, write path, or acknowledged non-serving)
before it can pass, rather than letting it default to "not in any list, so not
scanned, unnoticed". ``infrastructure/sqlite/store.py`` -- the canonical-store
adapter every read tool imports, reached by ``mcp/tools.py`` for
``knowledge.get`` -- is in the hand-picked remainder too, closing the specific
gap a round-one review measured: a store reference added to it evaded every
prior check because it was in neither the walked directories nor the old list.
``cli/main.py`` -- the command-registration root every CLI entry point is
wired through -- is scanned for the same reason, moved here from the acknowledged
non-serving bucket it sat in before; it names no store reference today, so the
scan stays green.

**The completeness guard now covers the whole shipped package, not only
``application/`` and ``cli/``.** A round-two review measured that ``domain/``
(40 files), ``infrastructure/`` (31 files outside the hand-picked five above),
``security/`` (7 files), and nine smaller reserved packages sat outside every
bucket -- 89 of 132 shipped modules, seen by neither the walk, the hand-picked
list, nor any completeness check, at the time of that measurement (@98f11bc,
2026-08-28). :func:`test_every_shipped_python_module_is_classified` closes that
by requiring every ``.py`` file under the shipped package, walked fresh from
disk, to fall into one of: :data:`SERVING_MODULES`, :data:`WRITE_PATH_MODULES`,
the ``application/``/``cli/`` non-serving lists, the file-level
:data:`_INFRASTRUCTURE_NON_SERVING_MODULES` (``infrastructure/`` mixes hand-picked
serving members with the rest, like ``application/``/``cli/``, so it gets the
same per-file treatment), :data:`_TOP_LEVEL_NON_SERVING_MODULES`, or one of
:data:`_ACKNOWLEDGED_SUBTREES`'s whole-package acknowledgements. **The
subtree acknowledgement is a deliberately weaker guarantee than the file-level
lists**: it forces classification of a NEW top-level package, not of a new file
inside one already acknowledged -- ``domain/newthing.py`` passes silently where
``application/newthing.py`` would not. That trade-off is what keeps 132 files
tractable at package granularity instead of demanding a fourth 89-entry hand
list; it is safe here because every acknowledged subtree is either pure value
types with no I/O (``domain/``, most of ``security/``) or a docstring-only
placeholder for a layer that is "not yet implemented" by its own module
docstring -- and it is exactly the gap that made ``review/`` above the one
exception, walked rather than acknowledged.

**Prong (a) -- no serving-layer module imports the store [AST].** Every module in
the assembled :data:`SERVING_MODULES` set is parsed and its imports are scanned;
none may reach the store port, the store adapter, or the standalone builder. This
catches a store reference added anywhere under the walked directories or the
hand-picked remainder, including on a path no test drives -- but it reads
*imports*, so a serving module that reached the store's SQLite *table* directly,
importing nothing, would slip past it. Prong (b) is the half that sees that.

**Prong (b) -- the store's tables never appear in a serving module, and no
registered tool serves a finding [grep + AST].** The distinctive table and
artifact tokens (``findings_metadata``, ``rejected_trailers``, ``theurian-findings``,
the ``findings`` table SQL, matched loosely enough to catch a quoted or
schema-qualified table name) are grepped out of every serving module -- the arm
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

**What neither prong covers, named rather than left implicit.** A reach built by
string concatenation -- a table name or filename assembled at runtime from split
pieces rather than written as one literal, for example ``"find" + "ings"`` -- evades
both the AST import scan and the grep tokens, since neither looks for anything but
a literal match on a real module or table name. Closing that residual needs a
store-level universal invariant over every value kind a disclosure could ride on,
not another string pattern here -- it is owed to the slice-3 review-ingestion
serving lane, not fixed in this file. Separately, the
runtime bytecode arm in ``tests/integration/test_findings_tool_registry.py`` walks
a *tool's own* code object and its nested consts (a comprehension, a closure
defined inline), but not a *named helper function* the tool calls out to: a tool
that called a module-level helper which itself referenced a store symbol would
show only the helper's name in the tool's own ``co_names``, not the symbol the
helper's code references one hop away -- a one-hop transitivity gap, also owed to
the same later lane rather than fixed by walking further here.

**A static sibling of that same one-hop gap.** Prong (a) reads each
:data:`SERVING_MODULES` member's *own* import statements; it does not follow an
import transitively. A brand-new module under an acknowledged, unscanned area
(``infrastructure/some_new_adapter.py``, say) that itself imports the store, then
imported by a serving module by its own innocuous name
(``from theurian.infrastructure.some_new_adapter import get_finding_count``),
shows the serving module naming only ``some_new_adapter`` and
``get_finding_count`` -- neither a store module component nor a store symbol --
so prong (a) sees nothing to catch, and prong (b)'s grep sees no store table
token in the serving module either, only in the adapter one hop away.
:func:`test_every_shipped_python_module_is_classified` forces the new adapter
itself into a bucket, but an "acknowledged non-serving" entry is a classification,
not a measurement (see :data:`_APPLICATION_NON_SERVING_MODULES`'s own docstring
note) -- it does not scan the adapter's imports either. Closing this needs the
same store-level universal invariant the string-concatenation residual above is
owed to, not a deeper walk here; recorded with the same slice-3 disposition as
its runtime counterpart.

Pure in the sense the T-7 structural arms are, with one named exception: every
*assertion* here parses ``.py`` files as text or as an AST and opens no database,
socket, or long-lived resource. The one guard test that is not pure this way is
:func:`test_the_directory_walk_picks_up_a_new_file_under_a_walked_directory`,
which writes a synthetic tree of throwaway ``.py`` files under ``tmp_path`` to
prove :func:`_walk_python_modules` itself is recursive and re-reads on every
call -- it needs a real filesystem to observe a walk picking up a freshly
written file, which a snippet parsed in memory cannot demonstrate.
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

#: Directories walked wholesale rather than named file-by-file. None of
#: ``mcp/``, ``daemon/`` or ``review/`` carries the findings write path -- that
#: lives only in ``application/findings_builder.py`` and
#: ``cli/findings_commands.py`` (see :data:`WRITE_PATH_MODULES`) -- so nothing
#: under any of the three needs excluding, and a NEW file placed under any of
#: them is scanned with no list a human must remember to extend. ``review/`` is
#: walked rather than acknowledged like the rest of the package precisely
#: because it is the one package whose own docstring names it the future home
#: of review-knowledge serving code -- see the module docstring's "How the
#: scanned population is assembled" section for the measured reason.
#: :func:`test_the_directory_walk_picks_up_a_new_file_under_a_walked_directory`
#: guards the walk mechanism itself against silently stopping to see a new
#: file, on a synthetic tree; :func:`test_the_walked_serving_directories_exist_and_are_non_empty`
#: guards the real directories these three names point at against being
#: renamed or removed, which ``Path.rglob`` would otherwise tolerate in
#: silence.
_WALKED_SERVING_DIRS: tuple[str, ...] = ("mcp", "daemon", "review")


def _walk_python_modules(root: pathlib.Path, directory: str) -> tuple[str, ...]:
    """Every ``.py`` file under ``root/directory``, as a ``directory/name.py`` string.

    Sorted so the result -- and so :data:`SERVING_MODULES` -- is deterministic
    across filesystems and Python versions that iterate :meth:`pathlib.Path.rglob`
    in different orders. ``root`` is a parameter rather than a closure over
    :data:`SRC` directly, so a test can point this at a synthetic tree and prove a
    freshly added file is picked up, instead of trusting the walk by inspection.
    """
    base = root / directory
    return tuple(str(path.relative_to(root)) for path in sorted(base.rglob("*.py")))


#: The SERVING layer -- a judgement about *what returns finding content to a
#: caller*, not about where a file sits. A serving path takes a caller's request
#: and answers it with knowledge; the write/maintenance path rebuilds a derived
#: artifact and answers with counts.
#:
#: - ``mcp/``, ``daemon/``, ``review/`` -- walked wholesale (see
#:   :data:`_WALKED_SERVING_DIRS`): the whole daemon tool surface a client
#:   speaks to, the process that builds and runs it, and the review-knowledge
#:   package that is the one docstring-named future home of findings-serving
#:   code.
#: - ``application/retrieval_service.py``, ``application/visibility.py`` -- the
#:   retrieval and gate the tools call.
#: - ``cli/commands.py`` -- the content-returning CLI (the knowledge read/search
#:   commands).
#: - ``cli/main.py`` -- the command-registration root every CLI entry point,
#:   including ``commands.py``'s, is wired through.
#: - the index read-side -- ``index_store.py``, ``index_query.py``,
#:   ``index_scan.py``, ``index_forest.py`` -- what a search reads its candidates
#:   from.
#: - ``store.py`` -- the canonical-store adapter every read tool imports
#:   (``mcp/tools.py`` reaches ``SqliteCanonicalStore`` through it for
#:   ``knowledge.get``); not itself under ``mcp/``/``daemon/``, so the walk above
#:   does not reach it, and not one of the four ``index_*`` modules either.
#:
#: ``application/`` and ``cli/`` are hand-picked rather than walked, unlike
#: ``mcp/``/``daemon/``/``review/``: the findings write path lives in both
#: (``application/findings_builder.py``, ``cli/findings_commands.py``), so a walk
#: would need to exclude exactly those two anyway, at which point it buys nothing
#: over naming the serving members directly. The cost of hand-picking is that a
#: NEW file in either directory is invisible to this set until someone adds it --
#: :func:`test_every_shipped_python_module_is_classified` is the completeness
#: guard for that gap, and for the whole rest of the package alongside it: it
#: fails the moment a new ``application/`` or ``cli/`` file is neither here, nor
#: in :data:`WRITE_PATH_MODULES`, nor in :data:`_APPLICATION_NON_SERVING_MODULES`
#: / :data:`_CLI_NON_SERVING_MODULES`, forcing a human classification rather than
#: a silent default to "not scanned". ``application/project_service.py`` owns the
#: *path* helper ``findings_for`` for both build and serve, so it names the
#: artifact without serving it -- it is one of the acknowledged non-serving
#: members, not measured to be unreachable.
SERVING_MODULES: tuple[str, ...] = (
    *_walk_python_modules(SRC, "mcp"),
    *_walk_python_modules(SRC, "daemon"),
    *_walk_python_modules(SRC, "review"),
    "application/retrieval_service.py",
    "application/visibility.py",
    "cli/commands.py",
    "cli/main.py",
    "infrastructure/sqlite/index_store.py",
    "infrastructure/sqlite/index_query.py",
    "infrastructure/sqlite/index_scan.py",
    "infrastructure/sqlite/index_forest.py",
    "infrastructure/sqlite/store.py",
)

#: The write/maintenance path that *legitimately* reaches the store. Excluded from
#: the serving set above; asserted to really reach the store below, so the import
#: scan is proven to fire against real files.
WRITE_PATH_MODULES: tuple[str, ...] = (
    "cli/findings_commands.py",
    "application/findings_builder.py",
)

#: Every other ``.py`` file directly under ``application/``: neither serving
#: content the way ``retrieval_service.py``/``visibility.py`` do, nor the
#: findings write path. Named explicitly, alongside :data:`SERVING_MODULES`'s own
#: hand-picked entries for this directory, so the completeness test below
#: (``test_every_shipped_python_module_is_classified``) can assert the three
#: buckets are exhaustive over the real directory listing. Membership here is an
#: acknowledgement, not a measurement: it is not a claim that any of these files
#: were checked for a store reference, only that a human has sorted them into
#: "not serving, not the write path" and a future addition must be sorted the
#: same way before this test passes again.
_APPLICATION_NON_SERVING_MODULES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "authorization.py",
        "forest_builder.py",
        "index_builder.py",
        "ingestion_service.py",
        "migration_alias_guards.py",
        "migration_body_guards.py",
        "migration_engine.py",
        "project_service.py",
        "proposal_service.py",
        "setup_context.py",
        "setup_service.py",
        "setup_steps.py",
        "setup_withholding.py",
        "withdrawal_purge.py",
    }
)

#: The ``cli/`` twin of :data:`_APPLICATION_NON_SERVING_MODULES`. ``main.py`` is
#: NOT here -- it moved into :data:`SERVING_MODULES` (the command-registration
#: root), so it is scanned rather than merely acknowledged.
_CLI_NON_SERVING_MODULES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "auth_commands.py",
        "context.py",
        "index_commands.py",
        "index_status_report.py",
        "migration_pipeline.py",
        "output.py",
        "propose_commands.py",
        "setup_commands.py",
    }
)

#: Every other ``.py`` file directly under ``infrastructure/``: neither serving
#: content the way the hand-picked five in :data:`SERVING_MODULES` do, nor the
#: findings write path (which does not touch ``infrastructure/`` at all --
#: ``WRITE_PATH_MODULES`` is entirely ``application/``/``cli/``). Paths are
#: relative to ``infrastructure/`` itself, not bare filenames, because unlike
#: ``application/``/``cli/`` this directory has real subpackages
#: (``sqlite/``, ``claude/``, ``services/``, ...). Membership here is an
#: acknowledgement, not a measurement, in the same sense
#: :data:`_APPLICATION_NON_SERVING_MODULES` already documents: a human has
#: sorted these into "not serving, not the write path", not scanned each one
#: for a store reference.
_INFRASTRUCTURE_NON_SERVING_MODULES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "determinism.py",
        "claude/__init__.py",
        "claude/mcp_config.py",
        "embedding/__init__.py",
        "embedding/hashing.py",
        "filesystem/__init__.py",
        "filesystem/migration_loader.py",
        "filesystem/parsers/markdown.py",
        "filesystem/parsers/openapi.py",
        "filesystem/parsers/registry.py",
        "filesystem/parsers/structured.py",
        "git/__init__.py",
        "git/trailer_source.py",
        "github/__init__.py",
        "raptor/__init__.py",
        "raptor/extractive.py",
        "secrets/__init__.py",
        "secrets/file_store.py",
        "services/__init__.py",
        "services/launchagent.py",
        "services/runner.py",
        "services/systemd_user.py",
        "sqlite/__init__.py",
        "sqlite/connection.py",
        "sqlite/findings_schema.py",
        "sqlite/findings_store.py",
        "sqlite/index_purge.py",
        "sqlite/index_schema.py",
        "sqlite/schema.py",
        "vector/__init__.py",
    }
)

#: The one file directly at the package root, ``theurian/__init__.py``: package
#: metadata (``__version__``, ``__protocol_version__``) and the public-surface
#: docstring, no I/O.
_TOP_LEVEL_NON_SERVING_MODULES: frozenset[str] = frozenset({"__init__.py"})

#: Whole-subtree acknowledgements: every ``.py`` file under each prefix, however
#: many and whatever their name, is classified non-serving in one entry rather
#: than a per-file list. **A deliberately weaker guarantee than the file-level
#: lists above**: a NEW top-level package needs an entry here before
#: :func:`test_every_shipped_python_module_is_classified` passes, but a NEW
#: file inside an already-acknowledged subtree does not -- ``domain/newthing.py``
#: passes silently where ``application/newthing.py`` would not. That trade-off
#: is what keeps a 132-file package tractable without a fourth ~90-entry hand
#: list, and it is sound for exactly the packages listed: each is either pure
#: value types and ports with no I/O, or a package whose own module docstring
#: says its layer is "not yet implemented" and names where the real code
#: currently lives instead. ``review/`` is deliberately NOT here -- see
#: :data:`_WALKED_SERVING_DIRS`'s own docstring for why that one package gets
#: the stronger, walked guarantee instead.
_ACKNOWLEDGED_SUBTREES: tuple[tuple[str, str], ...] = (
    (
        "domain/",
        "pure value types, entities and ports (including the review-finding "
        "port and record) -- no I/O, so nothing here opens a database itself; "
        "a serving module importing a store symbol *from* here is still "
        "caught, because the import shows up in the serving module's own scan",
    ),
    (
        "security/",
        "path, secret-file, env-file and project-config guards -- no store or findings reference",
    ),
    (
        "retrieval/",
        "docstring-only placeholder; the real retrieval code is "
        "application/retrieval_service.py, already in SERVING_MODULES",
    ),
    (
        "indexing/",
        "docstring-only placeholder; the real index code is under application/ "
        "and infrastructure/sqlite/, both already accounted for",
    ),
    ("ingestion/", "docstring-only placeholder for the ingestion layer, not yet implemented"),
    (
        "normalization/",
        "a mechanical, pure source-to-canonical text projection; no I/O beyond "
        "the bytes it is given",
    ),
    (
        "observability/",
        "docstring-only placeholder for opt-in tracing/metrics, not yet implemented",
    ),
    (
        "specification/",
        "docstring-only placeholder for specification handling, not yet implemented",
    ),
    ("traceability/", "docstring-only placeholder for the traceability graph, not yet implemented"),
    (
        "migrations/",
        "SQLite schema-migration docstring package; the migration code itself "
        "is under infrastructure/sqlite/",
    ),
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


def test_the_directory_walk_picks_up_a_new_file_under_a_walked_directory(
    tmp_path: pathlib.Path,
) -> None:
    """Guards :func:`_walk_python_modules`: the reason the walked dirs need no list.

    A round-one review found the previous, hand-written :data:`SERVING_MODULES`
    missed ``daemon/`` entirely: a new ``Route`` serving a finding, added to
    ``daemon/server.py``, passed the whole suite because nothing scanned that
    directory. Deriving the population by walk instead of by list is the fix; this
    is the guard the guard needs -- the same reason :data:`_SCANNER_CASES` exists
    for :func:`_store_references` -- because a walk that silently stopped
    descending, or silently stopped seeing a freshly written file, would make
    :data:`SERVING_MODULES` shrink with no test noticing until a real evasion
    landed. Built against a synthetic tree rather than :data:`SRC`, so a file can
    actually be added and observed, including one nested a directory deep --
    proving the walk is recursive, not only one level, in case any walked
    directory ever grows a subpackage.
    """
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "existing.py").write_text("x = 1\n")
    (tmp_path / "mcp" / "nested").mkdir()
    (tmp_path / "mcp" / "nested" / "deeper.py").write_text("y = 2\n")
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "server.py").write_text("z = 3\n")

    found_mcp = _walk_python_modules(tmp_path, "mcp")
    found_daemon = _walk_python_modules(tmp_path, "daemon")

    assert found_mcp == ("mcp/existing.py", "mcp/nested/deeper.py")
    assert found_daemon == ("daemon/server.py",)

    # A file added *after* the first walk is still picked up by a fresh walk --
    # the property SERVING_MODULES relies on: nothing here is cached across a
    # source edit, because the module executes this walk again on every import.
    (tmp_path / "mcp" / "just_added.py").write_text("w = 4\n")
    assert _walk_python_modules(tmp_path, "mcp") == (
        "mcp/existing.py",
        "mcp/just_added.py",
        "mcp/nested/deeper.py",
    )


def test_the_walked_serving_directories_exist_and_are_non_empty() -> None:
    """Guards the real walk against a renamed or vanished directory -- silently on both sides.

    :func:`_walk_python_modules` is built on :meth:`pathlib.Path.rglob`, and
    ``rglob`` on a directory that does not exist returns an empty iterator with
    no exception. A renamed ``daemon/`` (or ``mcp/``, or ``review/``) would
    therefore make :data:`SERVING_MODULES` quietly lose every file that
    directory used to contribute, with nothing failing at import time -- the
    same silent-shrink failure
    :func:`test_the_directory_walk_picks_up_a_new_file_under_a_walked_directory`
    guards for a synthetic tree, but that test never touches the real package,
    so it cannot catch this. Each configured directory is asserted to exist
    under the shipped package, and walking it is asserted to find at least one
    file -- an empty walk over a directory that exists (all three currently
    carry at least an ``__init__.py``) is as indistinguishable from a rename as
    a missing directory is, and this treats both the same way.
    """
    for directory in _WALKED_SERVING_DIRS:
        path = SRC / directory
        assert path.is_dir(), (
            f"{directory}/ is in _WALKED_SERVING_DIRS but does not exist under {SRC} -- "
            f"renamed or removed? SERVING_MODULES would otherwise shrink silently, since "
            f"Path.rglob on a missing directory returns nothing rather than raising."
        )
        assert _walk_python_modules(SRC, directory), (
            f"walking {directory}/ found no .py file -- an empty walk is indistinguishable "
            f"from a renamed or emptied directory and would leave SERVING_MODULES silently "
            f"missing this directory's contents."
        )


#: Every ``.py`` file under ``root``, as a path string relative to ``root`` --
#: the population :func:`test_every_shipped_python_module_is_classified` checks
#: every named bucket and every :data:`_ACKNOWLEDGED_SUBTREES` prefix against,
#: read fresh from disk rather than hand-counted. ``__pycache__`` is excluded;
#: nothing else is, so a new subpackage anywhere under ``root`` is included
#: automatically and must be classified before this test passes.
def _all_shipped_modules(root: pathlib.Path) -> frozenset[str]:
    return frozenset(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


#: Every ``.py`` file directly under ``application/`` or ``cli/`` in the shipped
#: package -- both directories are flat (no subpackage today), so a bare
#: filename is a legitimate relative path; kept for the two directories whose
#: completeness check pre-dates the whole-package walk above.
def _directory_module_names(directory: str) -> set[str]:
    return {path.name for path in (SRC / directory).glob("*.py")}


def test_every_shipped_python_module_is_classified() -> None:
    """Completeness guard, extended from ``application/``/``cli/`` to the whole shipped package.

    A round-two review measured that the completeness guard this test replaces
    covered only two of theurian's sixteen top-level packages: ``domain/`` (40
    files), 31 of ``infrastructure/``'s 36, ``security/`` (7), and nine smaller
    reserved packages -- 89 of 132 shipped modules (@98f11bc, 2026-08-28) -- sat
    outside every bucket, unscanned, unacknowledged, and unnoticed by any test.
    This asserts the union of every named bucket -- :data:`SERVING_MODULES`,
    :data:`WRITE_PATH_MODULES`, the ``application/``/``cli/``/``infrastructure/``
    non-serving lists, :data:`_TOP_LEVEL_NON_SERVING_MODULES`, and every
    :data:`_ACKNOWLEDGED_SUBTREES` prefix -- equals :func:`_all_shipped_modules`'s
    walk of the real package. A file in neither is unclassified and fails this
    test; a file in two is a bug in the bucket data, not a classification gap,
    and is reported by name rather than merely failing the equality.

    This equality is a completeness check on classification, not a claim that
    every acknowledged member was scanned for a store reference -- only
    :data:`SERVING_MODULES` and :data:`WRITE_PATH_MODULES` are (see prong (a)
    and prong (b) below). :data:`_ACKNOWLEDGED_SUBTREES` is a strictly weaker
    guarantee than the three file-level lists: see its own docstring for why
    that trade-off is deliberate, and why ``review/`` is walked instead of
    acknowledged rather than accepting it here.
    """

    def _relative(prefix: str, modules: tuple[str, ...]) -> set[str]:
        return {module[len(prefix) :] for module in modules if module.startswith(prefix)}

    serving_application = _relative("application/", SERVING_MODULES)
    serving_cli = _relative("cli/", SERVING_MODULES)
    write_application = _relative("application/", WRITE_PATH_MODULES)
    write_cli = _relative("cli/", WRITE_PATH_MODULES)

    application_accounted = {
        f"application/{name}"
        for name in serving_application | write_application | _APPLICATION_NON_SERVING_MODULES
    }
    cli_accounted = {f"cli/{name}" for name in serving_cli | write_cli | _CLI_NON_SERVING_MODULES}
    infrastructure_accounted = {
        f"infrastructure/{name}" for name in _INFRASTRUCTURE_NON_SERVING_MODULES
    }
    top_level_accounted = set(_TOP_LEVEL_NON_SERVING_MODULES)

    # The two directories with their own pre-existing per-file completeness
    # story are still checked exactly as before -- a stronger guarantee (every
    # file individually named) than the whole-package check below gives them,
    # since the whole-package check alone cannot tell "unclassified" apart from
    # "classified into the wrong one of these two directories' own buckets".
    assert application_accounted == {
        f"application/{name}" for name in _directory_module_names("application")
    }, (
        "a file under application/ is neither serving, write-path, nor named "
        "non-serving -- classify it (does it return finding content?) before this "
        "can pass"
    )
    assert cli_accounted == {f"cli/{name}" for name in _directory_module_names("cli")}, (
        "a file under cli/ is neither serving, write-path, nor named non-serving "
        "-- classify it (does it return finding content?) before this can pass"
    )

    named_accounted = (
        set(SERVING_MODULES)
        | set(WRITE_PATH_MODULES)
        | application_accounted
        | cli_accounted
        | infrastructure_accounted
        | top_level_accounted
    )
    every_module = _all_shipped_modules(SRC)
    subtree_accounted = {
        module
        for module in every_module
        if any(module.startswith(prefix) for prefix, _reason in _ACKNOWLEDGED_SUBTREES)
    }
    accounted = named_accounted | subtree_accounted
    unaccounted = every_module - accounted

    assert not unaccounted, (
        "a shipped .py file is neither a named serving module, a named write-path "
        "module, a named non-serving module, nor under an acknowledged subtree in "
        "_ACKNOWLEDGED_SUBTREES -- classify it (does it return finding content?) "
        "before this can pass:\n" + "\n".join(f"  {module}" for module in sorted(unaccounted))
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
#: findings." in a docstring. The table name may be double-quoted, single-quoted,
#: bracket-quoted, or schema-qualified (``main.findings``) -- all valid SQLite
#: spellings of the same table that a bare ``findings\b`` match misses. Measured:
#: a round-one review found ``FROM "findings"`` and ``FROM main.findings`` both
#: slipped past the earlier, unquoted-only pattern.
_FINDINGS_TABLE_SQL = re.compile(
    r'(?:FROM|INTO|UPDATE|JOIN)\s+["\'\[]?(?:\w+\.)?findings\b'
    r'|CREATE TABLE ["\'\[]?(?:\w+\.)?findings\b'
)


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


#: Forms the SQL pattern must catch, each a real SQLite spelling of the
#: ``findings`` table a hand-written query could use instead of the bare,
#: unquoted form the earlier pattern only saw. ``FROM "findings"`` and ``FROM
#: main.findings`` are the two a round-one review measured as misses.
_SQL_TABLE_FORMS_THAT_MUST_MATCH: tuple[str, ...] = (
    "SELECT finding_text FROM findings",
    'SELECT finding_text FROM "findings"',
    "SELECT finding_text FROM 'findings'",
    "SELECT finding_text FROM [findings]",
    "SELECT finding_text FROM main.findings",
    'INSERT INTO "findings" (commit_sha) VALUES (?)',
    "UPDATE main.findings SET finding_text = ?",
    "CREATE TABLE main.findings (commit_sha TEXT)",
)

#: Forms the pattern must NOT match -- the false-positive guard for the
#: broadened pattern, mirroring the negative half of :data:`_SCANNER_CASES` for
#: the import scanner. ``findings_metadata`` is a distinct token (caught by
#: :data:`STORE_ARTIFACT_TOKENS` instead), not a spelling of the ``findings``
#: table this pattern targets.
_SQL_TABLE_FORMS_THAT_MUST_NOT_MATCH: tuple[str, ...] = (
    "...history into findings.",
    "# a CRITICAL finding recorded in review round 1",
    "FROM the findings table",
    "SELECT * FROM findings_metadata",
)


@pytest.mark.parametrize("snippet", _SQL_TABLE_FORMS_THAT_MUST_MATCH)
def test_the_sql_table_pattern_matches_quoted_and_schema_qualified_forms(snippet: str) -> None:
    """Guards the broadened prong-(b) pattern against the two measured misses.

    A round-one review found the earlier, unquoted-only pattern let a serving
    module reach the store through ``FROM "findings"`` or ``FROM main.findings``
    -- both valid SQLite, neither containing the bare ``findings`` token the old
    pattern required immediately after the keyword. Quoted (double, single,
    bracket) and schema-qualified forms are asserted here so a future edit that
    narrows the pattern back down is caught before it ships, not discovered by
    the next mutation run.
    """
    assert _FINDINGS_TABLE_SQL.search(snippet), f"the SQL table pattern did not match {snippet!r}"


@pytest.mark.parametrize("snippet", _SQL_TABLE_FORMS_THAT_MUST_NOT_MATCH)
def test_the_sql_table_pattern_does_not_false_positive_on_prose_or_other_tables(
    snippet: str,
) -> None:
    """The broadened pattern stays as narrow as the one it replaces.

    Widening the pattern to catch quoted and qualified forms must not also start
    matching prose about review findings, or a different table whose name merely
    starts with ``findings`` -- either would make the serving scan noisy, forcing
    a reviewer to explain away a false positive instead of trusting the scan.
    """
    assert not _FINDINGS_TABLE_SQL.search(snippet), (
        f"the SQL table pattern false-positived on {snippet!r}"
    )


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
