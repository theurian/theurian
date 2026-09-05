"""AC-7: the review-finding store is reachable by EXACTLY ONE serving path (ADR-0029).

The security boundary of the findings surface. Slice-2 landed the store and held
this boundary as an **absence**: nothing a caller could reach touched the store,
because no serving module imported it and no registered tool named a finding.
Slice-3 serves findings, so the absence is gone and something narrower has to
replace it -- or the boundary would simply have been deleted.

What replaces it: **exactly one sanctioned reader, named here, and any second one
fails.** The prongs below are the same prongs, inverted rather than relaxed. Each
still walks the whole serving layer; what changed is that a hit in
:data:`SANCTIONED_STORE_READERS` is permitted *and required*, while a hit anywhere
else is the failure it always was. The planted-second-reader scenario this file
was written against -- ``review/findings_search.py``, importing the store and
passing the whole suite in a round-two review -- is still caught, because
``review/`` is walked and that file is not on the list.

Requiring the sanctioned hits is not decoration: a scan that silently stopped
resolving imports used to pass this file forever, and "no module reaches the
store" and "the scanner is broken" are the same observation from the outside. Now
the shipped serving reader must be *seen* reaching it, so a broken scanner fails
loudly instead of certifying an empty search.

Like ``test_network_call_sites.py`` (T-7), the boundary is enforced here by nothing
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
own docstring names it the future home of review-knowledge serving code (owned
by ``#479``; that docstring named ``#129`` until it closed ``COMPLETED`` on the
wording rather than on the code), so a package-level acknowledgement there would
wave through the exact file this whole test guards against -- measured by a
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

**Prong (a) -- only the sanctioned modules import the store [AST].** Every module
in the assembled :data:`SERVING_MODULES` set is parsed and its imports are
scanned. A module outside :data:`SANCTIONED_STORE_READERS` may reach neither the
store port, nor the store adapter, nor the standalone builder; a module inside it
may reach exactly the names recorded there and no others -- so widening the
sanctioned reader's own reach (adding the *builder* to the tool surface, say) is
as visible as adding a second reader. This catches a store reference added
anywhere under the walked directories or the hand-picked remainder, including on
a path no test drives -- but it reads *imports*, so a serving module that reached
the store's SQLite *table* directly, importing nothing, would slip past it. Prong
(b) is the half that sees that.

**Prong (b) -- the store's tables never appear in a serving module, and exactly
one registered tool serves a finding [grep + AST].** The distinctive table and
artifact tokens (``findings_metadata``, ``rejected_trailers``, ``theurian-findings``,
the ``findings`` table SQL, matched loosely enough to catch a quoted or
schema-qualified table name) are grepped out of every serving module -- the arm
that catches a raw ``SELECT ... FROM findings`` an import scan cannot see. **That
arm is unchanged by slice-3 and still admits nothing**: the sanctioned reader
goes through the port, so no serving module names a table, and a hand-written
query in the read path is as much a defect as it ever was. And the MCP tool
registry in ``mcp/tools.py`` is parsed for every tool it registers -- through the
``_tool`` seam or straight onto ``server.tool``, both shapes -- pinned to the
known set, and asserted to expose exactly one tool whose name serves a finding.
(The runtime companion -- that the *built* server registers exactly that set and
that exactly the sanctioned tool reaches a store symbol in its bytecode -- lives
in ``tests/integration/test_findings_tool_registry.py``, because it constructs a
server.)

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
a literal match on a real module or table name. What answers that residual is not
another string pattern here but the store-level invariant slice-3 landed: the
port's one serving read never selects the rejected table, refuses a stale store,
and cannot express an unbounded query, so a module that reached the file by a
concatenated name would still have to reimplement those controls to get anything
out of it -- and reimplementing them is a diff, not an accident. It stays a
residual for the *unsanctioned-reader* question, which is what this file answers.
Separately, the
runtime bytecode arm in ``tests/integration/test_findings_tool_registry.py`` walks
a *tool's own* code object and its nested consts (a comprehension, a closure
defined inline), but not a *named helper function* the tool calls out to: a tool
that called a module-level helper which itself referenced a store symbol would
show only the helper's name in the tool's own ``co_names``, not the symbol the
helper's code references one hop away -- a one-hop transitivity gap. Slice-3
narrows it rather than closing it: the sanctioned tool constructs the store in
its *own* body precisely so that arm sees the symbol there and can now require
it, so a store reach that hid one hop away would have to *also* remove the
sanctioned tool's own reach to go unnoticed, which fails that arm's positive
half. Following the hop is still owed.

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

**One further claim of the same shape, pinned here for the same reason.** The
port records that :meth:`ReviewFindingStore.is_current` is *deliberately* not
what the serving read calls -- the staleness comparison happens inside the
connection the rows come back on, so asking this method first would be a second
open with a rebuild able to land between them. That makes "nothing shipped calls
it" a load-bearing statement rather than an observation about dead code, and
:func:`test_no_shipped_module_asks_the_store_whether_it_is_current` is what
keeps it true: a caller appearing anywhere in the shipped package fails until
either the call is removed or the three docstrings asserting its absence are
re-tensed.

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

#: The **serving** modules sanctioned to reach the store, and exactly what each
#: may reach (ADR-0029 phase-2 slice-3). A module absent from this mapping may
#: reach nothing; a module present may reach these names and no others.
#:
#: Two entries, and the split between them is the reason there are two modules:
#:
#: - ``mcp/tools.py`` is the composition root (ADR-0003). It names the concrete
#:   adapter because it constructs it -- and it constructs it **in the tool's own
#:   body**, deliberately, so the runtime bytecode arm in
#:   ``tests/integration/test_findings_tool_registry.py`` can *require* the
#:   sanctioned tool to reach a store symbol rather than only forbidding others
#:   from doing so;
#: - ``mcp/findings.py`` names the **port module** only, for the query and row
#:   value types it validates into and shapes out of. It constructs no store,
#:   opens no file, and reaches no adapter.
#:
#: The values are what :func:`_store_references` reports, which is module
#: components plus :data:`STORE_SYMBOLS` members -- so ``FindingsStoreError``,
#: ``FindingQuery`` and ``StoredFinding`` do not appear here even though those
#: modules import them: they are error and value types, not the store.
#:
#: **The builder is on neither entry, and that is the point of recording an exact
#: set rather than a list of blessed files.** ``FindingsBuilder`` rebuilds the
#: store from git; a serving module reaching it would be the read path acquiring a
#: write, and it would fail this even though the module is "sanctioned".
SANCTIONED_STORE_READERS: dict[str, frozenset[str]] = {
    "mcp/tools.py": frozenset({"findings_store", "SqliteReviewFindingStore"}),
    "mcp/findings.py": frozenset({"review_finding_store"}),
}

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
        "index_secret_scan.py",
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
        # The `gh` review-ingestion adapter (ADR-0030). It reads GitHub and
        # returns `ReviewEvent`/`ReviewThread` evidence; the findings store is a
        # different arm entirely -- `Review-Finding:` trailers out of local git
        # history (ADR-0029) -- and nothing here reaches it. The two share the
        # FR-V family and the safety triple, not a source.
        "github/__init__.py",
        "github/environment.py",
        "github/gh_cli.py",
        "github/limits.py",
        "github/queries.py",
        "github/review_provider.py",
        "github/transport_guard.py",
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


def test_only_the_sanctioned_serving_modules_import_the_finding_store() -> None:
    """Prong (a): one reader, named, and every other serving module reaches nothing.

    AC-7's import half, inverted for slice-3 rather than relaxed. A finding now
    reaches a caller, so "nothing touches the store" is no longer the property;
    what replaces it is that the set of serving modules touching it is exactly
    :data:`SANCTIONED_STORE_READERS`, each reaching exactly the names recorded
    beside it. Both directions fail:

    * a module outside the mapping with any hit -- the planted
      ``review/findings_search.py`` shape, and any future one;
    * a sanctioned module reaching a name it is not recorded as reaching -- the
      read path acquiring the *builder*, say.

    The write path (:data:`WRITE_PATH_MODULES`) stays outside the serving set and
    is pinned separately to reach the store, so the scan is proven to fire against
    real repository files rather than only against snippets.
    """
    unsanctioned = {
        module: sorted(hits)
        for module in SERVING_MODULES
        if module not in SANCTIONED_STORE_READERS
        and (hits := _store_references(_serving_source(module), module))
    }

    assert not unsanctioned, (
        "a serving-layer module that is not a sanctioned reader imports the "
        "review-finding store:\n"
        + "\n".join(f"  {module} :: {names}" for module, names in sorted(unsanctioned.items()))
        + "\n\nExactly one serving path may reach the store (ADR-0029 phase-2 "
        "slice-3): `mcp/tools.py` constructs the adapter for the `review.findings` "
        "tool, and `mcp/findings.py` names the port for its value types. A second "
        "reader is a second set of serving controls -- the stale-store refusal, the "
        "rejected-trailer exclusion, the bound -- that no review has seen.\n\n"
        "If this is a new serving surface, it lands with its own disclosure round "
        "and its own entry in SANCTIONED_STORE_READERS, not by being added here "
        "quietly. The write/maintenance path (`findings build`) is exempt and lives "
        "in cli/findings_commands.py and application/findings_builder.py, which are "
        "deliberately outside SERVING_MODULES."
    )

    overreaching = {
        module: sorted(hits - permitted)
        for module, permitted in SANCTIONED_STORE_READERS.items()
        if (hits := _store_references(_serving_source(module), module)) - permitted
    }

    assert not overreaching, (
        "a sanctioned reader reaches more of the store than it is recorded as "
        "reaching:\n"
        + "\n".join(f"  {module} :: {names}" for module, names in sorted(overreaching.items()))
        + "\n\nThe entry in SANCTIONED_STORE_READERS is an exact set, not a "
        "permission to touch anything findings-shaped. `FindingsBuilder` in "
        "particular is the rebuild path: a serving module reaching it is the read "
        "surface acquiring a write."
    )


def test_each_sanctioned_reader_really_reaches_the_store() -> None:
    """The positive half, without which prong (a) certifies a broken scanner.

    ``test_only_the_sanctioned_serving_modules_import_the_finding_store`` answers
    "does an unsanctioned module reach the store?" and reports *no* as clean --
    which is also what a scanner that resolved nothing would report, forever. The
    write-path test below has always been this file's answer to that, and this is
    the same guard aimed at the serving side: the modules that are *supposed* to
    reach the store are asserted to be seen reaching it.

    It is also the drift guard on the mapping itself. A sanctioned entry whose
    module stopped importing the store -- renamed, refactored, or removed -- is a
    stale permission sitting in a security-relevant list, and a stale permission
    is how the next module inherits one nobody meant to grant.
    """
    missing = {
        module: sorted(permitted)
        for module, permitted in SANCTIONED_STORE_READERS.items()
        if not _store_references(_serving_source(module), module)
    }

    assert not missing, (
        "a module recorded as a sanctioned store reader imports nothing of the "
        "store:\n"
        + "\n".join(f"  {module} :: expected {names}" for module, names in sorted(missing.items()))
        + "\n\nEither the serving path moved -- in which case update "
        "SANCTIONED_STORE_READERS, and check whether its new home is scanned at all "
        "-- or the import scan has stopped resolving real files, in which case "
        "prong (a) is now vacuous and its green result means nothing."
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


# -- The staleness question is asked inside the read, never before it --------

#: One case per form the reference scan must count, and per form it must not.
#: The prose case is the load-bearing negative: three shipped docstrings explain
#: why nothing calls ``is_current``, and a scan that counted a mention would
#: report those explanations as the very callers they deny.
_REFERENCE_CASES: tuple[tuple[str, str, int], ...] = (
    ("store.is_current()", "is_current", 1),
    ("if not store.is_current():\n    rebuild()", "is_current", 1),
    ("current = store.is_current", "is_current", 1),
    ('"""Nothing shipped calls is_current, and that is deliberate."""', "is_current", 0),
    ("def is_current(self) -> bool:\n    return True", "is_current", 0),
    ("store.is_current_at(moment)", "is_current", 0),
    ("# is_current is the standalone question", "is_current", 0),
)


def _named_references(source: str, module: str, name: str) -> int:
    """How often ``source`` references ``name`` as an attribute or a bare name.

    Definitions and prose are not references: an ``ast.FunctionDef``'s own name
    is neither an ``Attribute`` nor a ``Name`` node, and a docstring is an
    ``ast.Constant``. That distinction is the whole reason this parses rather
    than greps -- the shipped package mentions ``is_current`` in three
    docstrings precisely to say that nothing calls it.
    """
    tree = ast.parse(source, filename=module)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == name)
        or (isinstance(node, ast.Name) and node.id == name)
    )


@pytest.mark.parametrize(
    "source, name, expected",
    _REFERENCE_CASES,
    ids=[case[0].splitlines()[0][:48] for case in _REFERENCE_CASES],
)
def test_the_reference_scan_counts_a_call_and_not_a_mention(
    source: str, name: str, expected: int
) -> None:
    """Guards the claim below, which a scan that resolved nothing would satisfy.

    ``test_no_shipped_module_asks_the_store_whether_it_is_current`` reports
    *zero* as clean, and zero is also what a broken scan reports over every file
    forever. Each form it must see is asserted against a snippet, and each form
    it must let past -- a docstring, a comment, the method's own definition, a
    differently-named method -- is asserted to count nothing.
    """
    assert _named_references(source, "snippet.py", name) == expected


def test_the_reference_scan_finds_a_real_call_in_the_shipped_source() -> None:
    """The positive control on a real file: the scan sees a method call that exists.

    The snippets above prove the scan reads snippets. This proves it reads the
    shipped package, which is what the claim below is asserted over:
    ``mcp/tools.py`` really does call ``serve_findings``, so a scan that found
    nothing there has stopped resolving files and its zero for ``is_current``
    means nothing.
    """
    tools_source = _serving_source("mcp/tools.py")

    assert _named_references(tools_source, "mcp/tools.py", "serve_findings") >= 1, (
        "the reference scan found no `serve_findings` call in mcp/tools.py, which "
        "is the one sanctioned serving read's only caller. The scan is broken, not "
        "the product -- fix it before trusting the zero the next test reports."
    )


def test_no_shipped_module_asks_the_store_whether_it_is_current() -> None:
    """The recorded reason for a dead method, held to being true.

    ``is_current`` is not dead code somebody forgot: the port, the adapter and
    ``findings_schema.py`` all record that the serving read deliberately does
    *not* call it, because the staleness comparison has to happen inside the
    connection the rows come back on. A second open would let a rebuild land
    between the check and the read, leaving the check answering for a file the
    rows did not come from.

    So a caller appearing anywhere in the shipped package is not a small style
    matter -- it is either that split-open shape arriving, or three docstrings
    becoming false. Both need a human, which is what this failure asks for.
    """
    callers = {
        module: hits
        for module in sorted(_all_shipped_modules(SRC))
        if (
            hits := _named_references(
                (SRC / module).read_text(encoding="utf-8"), module, "is_current"
            )
        )
    }

    assert not callers, (
        "a shipped module references the review-finding store's `is_current`:\n"
        + "\n".join(
            f"  {module} :: {hits} reference(s)" for module, hits in sorted(callers.items())
        )
        + "\n\nThe serving read makes the staleness comparison inside the connection "
        "it reads the rows through, on purpose: `is_current()` followed by a query is "
        "two opens, and a rebuild landing between them leaves the check vouching for a "
        "file the rows did not come from. If a diagnostic surface genuinely needs the "
        "standalone question, say so here -- and re-tense the three docstrings "
        "(`ReviewFindingStore.is_current`, `SqliteReviewFindingStore.is_current`, "
        "`findings_schema`) that currently state it has no shipped caller."
    )


# -- Prong (b) AST: the MCP tool registry serves no finding ------------------

#: The read-only tools this build ships. Pinned as a whole set, not a subset, so a
#: *new* ``_tool(server, name=...)`` fails this test until it is classified here --
#: the drift guard that stops a second findings-serving tool from being added
#: silently and read as "not on the list, so fine".
KNOWN_TOOL_NAMES = frozenset(
    {
        "knowledge.search",
        "knowledge.get",
        "knowledge.status",
        "project.list",
        "review.findings",
        "system.capabilities",
    }
)

#: The one tool that serves findings (ADR-0029 phase-2 slice-3). Its disclosure
#: round is what makes it permitted; a second name matching
#: :data:`_FINDING_TOOL_PATTERN` has had no such round.
FINDINGS_TOOL_NAME = "review.findings"

#: A tool name that would serve a finding. Matched case-insensitively so
#: ``knowledge.findings`` and ``review.finding`` both trip it.
_FINDING_TOOL_PATTERN = re.compile(r"finding", re.IGNORECASE)


def _registered_tool_names(tools_source: str) -> Iterator[str]:
    """Every registered tool's ``name="..."`` string literal in ``mcp/tools.py``.

    Reads the registration source rather than a running server, so it stays a pure
    unit check. **Two registration shapes count, and both must**, because since
    issue #491 the five tools go through ``mcp/tools.py``'s own ``_tool`` seam
    rather than straight onto ``server.tool``:

    * ``_tool(server, name=...)`` -- the seam every tool uses today. It wraps the
      body in ``_forwarding`` so a refusal raised below the surface still reaches
      the caller under mcp >= 2.1.
    * ``server.tool(name=...)`` -- the SDK call the seam delegates to. No tool
      uses it directly now, and ``test_tool_error_type_contract.py`` pins that;
      it stays recognised here because a tool added that way is *still a
      registered tool*, and this check must see it in order to make someone
      classify it.

    Recognising only the first would make this arm blind to exactly the bypass
    the other arm calls a defect -- one refactor already reduced this function's
    result to the empty set, which is why the shapes are enumerated rather than
    assumed. A tool registered by some *other* mechanism (a dynamic ``add_tool``)
    would evade this AST arm entirely; the runtime companion in
    ``tests/integration/test_findings_tool_registry.py``, which enumerates the
    built server, is the arm that would catch that.
    """
    tree = ast.parse(tools_source, filename="mcp/tools.py")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        through_the_sdk = isinstance(func, ast.Attribute) and func.attr == "tool"
        through_the_seam = isinstance(func, ast.Name) and func.id == "_tool"
        if not (through_the_sdk or through_the_seam):
            continue
        for keyword in node.keywords:
            value = keyword.value
            if (
                keyword.arg == "name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                yield value.value


def test_the_tool_registry_registers_exactly_the_known_tools() -> None:
    """Guards the finding-tool check: a new tool must be classified, not defaulted in.

    Pinned as a whole-set equality so a new registration reddens this until
    someone adds it to :data:`KNOWN_TOOL_NAMES` -- which is the moment they must
    decide whether it serves a finding. Without the pin, a second findings-serving
    tool whose name did not literally contain "finding" would sail past the check
    below; with it, every new tool is seen.
    """
    tools_source = (SRC / "mcp/tools.py").read_text(encoding="utf-8")

    registered = set(_registered_tool_names(tools_source))

    assert registered == set(KNOWN_TOOL_NAMES), (
        f"mcp/tools.py registers {sorted(registered)}, pinned set is "
        f"{sorted(KNOWN_TOOL_NAMES)}. A tool was added or removed. If you added "
        f"one, classify it: does it return finding content? If so it needs its own "
        f"disclosure round -- `{FINDINGS_TOOL_NAME}` had one (ADR-0029 phase-2 "
        f"slice-3) and a second surface does not inherit it. If not, add its name "
        f"here."
    )


def test_exactly_one_registered_tool_name_serves_a_finding() -> None:
    """Prong (b): the tool surface exposes the sanctioned findings tool and no other.

    Slice-3 makes one tool serve findings, so this is an equality rather than an
    emptiness -- and it fails in both directions. A **second** name matching
    ``finding`` is a serving surface with no disclosure round of its own. **Zero**
    names matching it is the pin having gone vacuous or the tool having been
    renamed out from under the rest of this file, including the runtime arm that
    keys on the same name.

    Read from the registration source; the whole-set pin above is what makes the
    name check meaningful, by forcing every new tool through classification first.
    """
    tools_source = (SRC / "mcp/tools.py").read_text(encoding="utf-8")

    serving = sorted(
        name for name in _registered_tool_names(tools_source) if _FINDING_TOOL_PATTERN.search(name)
    )

    assert serving == [FINDINGS_TOOL_NAME], (
        f"the MCP tool registry exposes finding-serving tool(s) {serving}, and the "
        f"sanctioned set is ['{FINDINGS_TOOL_NAME}']. A tool that returns parsed "
        f"review findings to a caller is a disclosure surface: the sanctioned one "
        f"went through ADR-0029's round with its rejected-trailer exclusion, its "
        f"constant stale-store refusal and its bound, and none of that transfers to "
        f"a second tool by being findings-shaped. An empty list here is the other "
        f"failure: the tool was renamed and this file no longer checks anything."
    )
