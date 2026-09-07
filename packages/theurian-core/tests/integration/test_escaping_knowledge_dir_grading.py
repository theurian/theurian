"""An escaping ``.theurian`` *itself* is graded like every other escape (#550).

The neighbouring class to ``test_contained_path_envelope.py``'s, and keyed
differently on purpose. That file's population is
``ProjectPaths._contained``'s call sites -- a **leaf** under ``.theurian``
delivered as a symbolic link out of the tree. This file's population is
:meth:`ProjectPaths.of`'s own join check: the ``.theurian`` **directory** itself
delivered as such a link, refused a level earlier, before a single helper derives
a path. One root cause, two guards, two keys -- and until this change, two
different sets of exit codes.

**What was measured, and how.** Both faces were run against the real CLI in a
sandbox (macOS 26.6, CPython 3.13.3) at ``8372cc8c``, one fresh repository per
command, with ``HOME`` and ``THEURIAN_DATA_DIR`` redirected:

===================  =======================  ==========================
command              leaf (``.theurian/state``)  directory (``.theurian``)
===================  =======================  ==========================
``migrate status``   4                        1
``migrate validate`` 4                        1
``migrate apply``    4                        1
``index build``      4                        1
``index status``     4                        1
``index gc``         4                        1
``project status``   4                        **0**, degraded payload
===================  =======================  ==========================

Seven commands answered one root cause two ways, and ``project status`` reported
a *registered* project at exit 0 while the whole layout it describes resolved
outside the working tree. On the MCP surface the split was not a code but a
missing cure: the leaf face reached the caller through ``_with_remedy`` carrying
"Remove ``.theurian/state`` …", and the directory face escaped ``_resolve``
un-guarded into ``_forwarding``, which publishes ``str(exc)`` and drops
``.remedy`` by design -- so an agent was told a path escaped and given nothing to
do about it.

**The population is derived from the source, never listed here.**
:func:`project_paths_of_call_sites` reads every ``ProjectPaths.of(...)`` call out
of ``packages/theurian-core/src`` by AST, so a call site added later joins this
file or fails :func:`test_every_project_paths_of_call_site_is_classified` until
someone classifies it. Two classifications exist, and the second is as load-
bearing as the first: a site whose refusal reaches a caller as a **refusal**, and
a site whose caller deliberately absorbs it into a **diagnostic verdict**. The
five ``doctor``/`setup` sites are the second kind -- a probe has to come back
with an answer, and ``test_setup_migrations_checker.py``'s
``test_a_theurian_that_leaves_the_tree_stays_a_conflict_rather_than_an_answer``
holds that decision against a measured counterfactual. Their grade did not move
and this file pins that it did not.

**The commands are derived too**, from the context-resolving call sites in
``theurian.cli`` (:data:`_RESOLVERS`) rather than hand-listed:
:data:`REACHED_BY` maps each caller to the command path that runs it, and
:func:`test_every_context_resolving_caller_maps_to_a_shipped_command` holds the
mapping against the Typer app in both directions. A command that stopped
resolving a project, or a new one that started, changes that set and fails there
rather than quietly leaving this sweep narrower than it reads.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
import typer
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.authorization import (
    DEPLOYMENT_ACL_GROUPS,
    DEPLOYMENT_TENANT,
    AuthorizationGrant,
)
from theurian.application.project_service import (
    KNOWLEDGE_DIR_ESCAPE_REMEDY,
    ProjectRegistry,
)
from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.enums import Sensitivity

pytestmark = pytest.mark.integration

runner = CliRunner()

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

#: ``packages/theurian-core/src/theurian``: this file -> integration -> tests ->
#: theurian-core, then down into the package. Computed rather than written out so
#: a moved test file fails loudly instead of sweeping an empty tree.
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "theurian"

#: The grant every MCP call here runs under: everything this deployment could
#: serve, so a refusal is attributable to the plant and never to a ceiling.
EVERY_SENSITIVITY: Final = frozenset(Sensitivity)


# -- The population, read out of the source ---------------------------------


def _own_calls(node: ast.AST) -> Iterator[ast.Call]:
    """Every ``Call`` in ``node``'s own body, never one belonging to a nested def.

    ``ast.walk`` descends into nested functions, so a call inside a closure is
    attributed to the enclosing function as well as to itself. That is not a
    detail: ``mcp/tools.py`` defines every tool inside ``register``, so the whole
    module's call sites arrived under one name and the classification below could
    not distinguish ``_resolve`` from its twenty siblings.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _own_calls(child)


def _functions(path: Path) -> Iterator[tuple[str, ast.AST]]:
    """Every function in ``path``, nested ones included, with its own name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name, node


def _module_of(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).with_suffix("").as_posix().replace("/", ".")


def project_paths_of_call_sites() -> frozenset[str]:
    """Every ``ProjectPaths.of(...)`` call in the shipped source, as ``module::function``.

    The key the issue was filed on, re-derived on each run rather than trusted:
    ``git grep -n 'ProjectPaths.of(' -- packages/theurian-core/src`` answers the
    same question but counts its own prose, and two of that command's ten lines
    at ``8372cc8c`` are a docstring and a comment. An AST walk cannot read a
    comment, so what it returns is calls.

    Keyed on the *innermost* enclosing function rather than on file and line: a
    line number moves whenever anything above it does, and the innermost is what
    makes ``mcp.tools::_resolve`` its own member instead of one of ``register``'s.
    """
    return frozenset(
        f"{_module_of(path)}::{name}"
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for name, node in _functions(path)
        for call in _own_calls(node)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "of"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "ProjectPaths"
    )


#: The three spellings of "resolve a project context" in ``theurian.cli``, which
#: are three *handlers* over one ``ProjectPaths.of`` call site and not three call
#: sites: ``resolve_context`` bare (``project status``, which degrades rather than
#: refusing), ``_resolve_or_refuse`` (``init`` and ``project register``, which run
#: before a migration set exists) and ``_require_project`` (everything else). Each
#: definition is excluded from the derived set below, since a function is not its
#: own caller.
_RESOLVERS: Final = frozenset({"resolve_context", "_resolve_or_refuse", "_require_project"})


def context_resolving_callers() -> frozenset[str]:
    """Every ``theurian.cli`` function that resolves a project context.

    All three spellings in :data:`_RESOLVERS`, because a command reaches
    ``cli/context.py::resolve_context`` through exactly one of them and they are
    graded by different handlers. Deriving on the whole set rather than on the
    bare name is what keeps this honest when a resolver is extracted: pulling
    ``init`` and ``project register``'s shared arms into ``_resolve_or_refuse``
    would otherwise have dropped both commands out of the sweep silently.
    """
    return frozenset(
        f"{_module_of(path)}::{name}"
        for path in sorted((SOURCE_ROOT / "cli").rglob("*.py"))
        for name, node in _functions(path)
        if name not in _RESOLVERS
        for call in _own_calls(node)
        if isinstance(call.func, ast.Name) and call.func.id in _RESOLVERS
    )


@dataclass(frozen=True, slots=True)
class Site:
    """One ``ProjectPaths.of`` call site and what its refusal is measured to do."""

    #: ``module::function``, matching :func:`project_paths_of_call_sites`.
    key: str
    #: ``True`` when the refusal reaches a caller as a refusal this file grades;
    #: ``False`` when its caller deliberately absorbs it into a verdict.
    refuses: bool
    #: Why, in the words of the decision. Every site carries one, because "it is
    #: not graded here" and "nobody looked at it" are indistinguishable otherwise.
    because: str


#: Every call site the key derives, each classified. Eight at ``8372cc8c``.
SITES: Final = (
    Site(
        key="cli.context::resolve_context",
        refuses=True,
        because=(
            "the resolve every project-scoped command makes. Reached by four "
            "handlers -- `_require_project`'s chain and the direct calls in "
            "`init_command`, `project_register` and `project_status` -- which is "
            "why the command sweep below is derived from `context_resolving_callers` "
            "rather than from this one entry."
        ),
    ),
    Site(
        key="mcp.tools::_resolve",
        refuses=True,
        because=(
            "every knowledge tool resolves through it (#119 decision 4). A "
            "different envelope contract from the CLI's -- an MCP transport error, "
            "not a `--json` document on stderr -- so it is driven separately, by "
            "`test_the_mcp_surface_publishes_the_cure_for_an_escaping_knowledge_directory`."
        ),
    ),
    Site(
        key="cli.migration_pipeline::rehearse_migration_set",
        refuses=True,
        because=(
            "`propose accept`'s dry replay, over a tree this process just created "
            "under a temporary directory. Its `knowledge_directory` comes from "
            "`ProposalService._knowledge_directory`, which derives it relative to a "
            "root `ProjectPaths.of` has already contained, so no delivered artefact "
            "reaches it -- a contract-guarantee arm, not a branch real data drives. "
            "It is graded rather than excused because `propose accept`'s own "
            "`except ProjectPathEscapeError` arm (propose_commands.py) answers it, "
            "and that arm is the same grading this change gives every other site. "
            "Undriven here, and said so rather than counted as covered."
        ),
    ),
    Site(
        key="cli.setup_commands::_current_state_hash",
        refuses=False,
        because=(
            "the resolve sits outside that function's `try` deliberately: a "
            "containment refusal is not a verdict about a migration set, so it "
            "escapes to `SetupService._probe`'s generic net and reaches the "
            "operator as CONFLICTING, 'Could not check initial-index.'. Held by "
            "`test_setup_migrations_checker.py::"
            "test_a_theurian_that_leaves_the_tree_stays_a_conflict_rather_than_an_answer` "
            "against a measured counterfactual."
        ),
    ),
    Site(
        key="cli.setup_commands::_check_migrations",
        refuses=False,
        because=(
            "the same placement decision one step over: the resolve is outside the "
            "`try`, so the refusal escapes to the same net and `migrations-valid` "
            "reports CONFLICTING rather than a claim about the operator's YAML."
        ),
    ),
    Site(
        key="cli.setup_commands::_published_secret_scan",
        refuses=False,
        because=(
            "guarded on purpose, and the whole call rather than the resolve alone: "
            "a `doctor` that ended in a traceback with empty stdout over a doctored "
            "tree is the shape that measurement closed. The verdict is "
            "NOT_APPLICABLE, which is what a scan that could not run says."
        ),
    ),
    Site(
        key="application.setup_steps::probe_migrations",
        refuses=False,
        because=(
            "the `migrations-valid` step. Its refusal is the checker's to publish, "
            "and it arrives as CONFLICTING through the net above."
        ),
    ),
    Site(
        key="application.setup_steps::probe_initial_index",
        refuses=False,
        because=(
            "the `initial-index` step, second resolve on the same root. Same net, "
            "same CONFLICTING verdict."
        ),
    ),
)

SITE_BY_KEY: Final = {site.key: site for site in SITES}

#: Which command runs each context-resolving caller. Hand-written and checked
#: both ways against the Typer app: a caller that is itself a command callback
#: maps to its own path, and the one that is not (`propose_commands::_draft`,
#: the body `propose_draft` delegates to) names the command that reaches it.
REACHED_BY: Final = {
    "cli.commands::init_command": "init",
    "cli.commands::project_register": "project register",
    "cli.commands::project_status": "project status",
    "cli.commands::migrate_status": "migrate status",
    "cli.commands::migrate_validate": "migrate validate",
    "cli.commands::migrate_apply": "migrate apply",
    "cli.commands::ingest_command": "ingest",
    "cli.index_commands::index_build": "index build",
    "cli.index_commands::index_status": "index status",
    "cli.index_commands::index_gc": "index gc",
    "cli.findings_commands::findings_build": "findings build",
    "cli.propose_commands::propose_accept": "propose accept",
    "cli.propose_commands::_draft": "propose",
}

#: A valid `ProposalId` naming nothing, so `propose accept` gets past its own
#: argument parse and reaches `_require_project`. A malformed one exits 2 there
#: and would measure the parser instead of the resolve.
ABSENT_PROPOSAL: Final = "01J0000000000000000000000A"

#: The argv each swept command needs beyond its path. `propose` is a callback
#: group whose required options are checked before its body runs, so it takes the
#: full set or it never reaches the resolve.
EXTRA_ARGS: Final[dict[str, tuple[str, ...]]] = {
    "propose accept": (ABSENT_PROPOSAL,),
    "propose": (
        "--item-id",
        "architecture.demo",
        "--title",
        "Demo",
        "--kind",
        "architecture",
        "--owner",
        "team",
        "--author",
        "author@example.com",
        "--description",
        "why",
        "--body-file",
        "BODY.md",
        "--authored-here",
        "--agent-id",
        "agent",
        "--task-id",
        "task",
        "--model",
        "model",
        "--reasoning",
        "because",
    ),
}


# -- The corpus and the plants ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """What one command published, as a caller parsing ``--json`` receives it."""

    exit_code: int
    stdout: str
    envelope: dict[str, Any] | None
    escaped: str | None

    @property
    def remedy(self) -> str:
        return "" if self.envelope is None else str(self.envelope.get("remedy", ""))

    @property
    def published_the_escape_cure(self) -> bool:
        return self.remedy == KNOWLEDGE_DIR_ESCAPE_REMEDY


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _build_corpus(tmp_path: Path, patch: pytest.MonkeyPatch) -> Path:
    """A registered, migrated project with ``HOME`` and the data directory moved.

    No ``index build``: nothing in this file reads an index, and the resolve under
    test happens before one is opened. Both redirections go through
    ``monkeypatch`` rather than ``os.environ``, and the ``chdir`` is here because
    the CLI resolves a project from the working directory -- a sweep that forgot
    it would resolve the developer's own checkout.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    patch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    patch.setenv("HOME", str(tmp_path / "home"))
    patch.chdir(root)

    _run("init")
    _run("project", "register")
    _run("migrate", "apply")
    (root / "BODY.md").write_text("# Demo\n\nBody.\n", encoding="utf-8")
    return root


def _escape_the_knowledge_directory(root: Path) -> Path:
    """Deliver ``.theurian`` as a relative symbolic link that leaves the tree.

    What a clone hands the victim in #237: the layout lives beside the tree and
    ``.theurian`` is a committed relative link into it. The target being genuinely
    outside the clone's *real* tree is the property every assertion turns on, so
    it is checked here rather than assumed.
    """
    outside = root.parent / "outside"
    shutil.move(str(root / ".theurian"), str(outside))
    (root / ".theurian").symlink_to(Path("..") / "outside")
    assert not outside.resolve().is_relative_to(root.resolve()), (
        "the plant must sit genuinely outside the clone's real tree"
    )
    return outside


def _contain_the_knowledge_directory(root: Path) -> None:
    """Deliver ``.theurian`` as a symbolic link that stays *inside* the tree.

    The control this file needs most. A guard keyed on "is it a link" rather than
    on where the link goes would refuse this too, and every refusal assertion
    above would still pass -- so the honest tree is checked with the same
    machinery, not assumed to be uninteresting.
    """
    real = root / ".theurian-real"
    shutil.move(str(root / ".theurian"), str(real))
    (root / ".theurian").symlink_to(Path(".theurian-real"))
    assert real.resolve().is_relative_to(root.resolve())


def _observe(command: str) -> Observation:
    """Run one swept command and record what reached the caller.

    ``CliRunner`` keeps an uncaught exception on ``result.exception`` rather than
    letting Typer's Rich handler render it, so an escape is invisible in the
    captured streams and has to be read off the result.
    """
    argv = [*command.split(" "), *EXTRA_ARGS.get(command, ()), "--json"]
    result = runner.invoke(app, argv)
    escaped = result.exception
    if isinstance(escaped, SystemExit):
        escaped = None
    stderr = (result.stderr or "").strip()
    envelope: dict[str, Any] | None = None
    if stderr:
        try:
            parsed = json.loads(stderr)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            envelope = parsed
    return Observation(
        exit_code=result.exit_code,
        stdout=result.stdout,
        envelope=envelope,
        escaped=None if escaped is None else type(escaped).__name__,
    )


Matrix = dict[str, Observation]


def _sweep(
    tmp_path_factory: pytest.TempPathFactory,
    plant: Any,
) -> Matrix:
    """One fresh corpus per command, so a mutating command cannot colour the next.

    A shared corpus would be sound for the escaping plant, where every command
    refuses before it writes -- and unsound for the controls, where ``migrate
    apply`` and ``propose`` write for real. One rule for both keeps the two
    matrices comparable, which is the whole point of running them.
    """
    observed: Matrix = {}
    for command in sorted(REACHED_BY.values()):
        with pytest.MonkeyPatch.context() as patch:
            slot = tmp_path_factory.mktemp(command.replace(" ", "-"))
            root = _build_corpus(slot, patch)
            if plant is not None:
                plant(root)
            observed[command] = _observe(command)
    return observed


@pytest.fixture(scope="module")
def escaping(tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    """Every swept command over an escaping ``.theurian``."""
    return _sweep(tmp_path_factory, _escape_the_knowledge_directory)


@pytest.fixture(scope="module")
def contained_link(tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    """The same commands over a ``.theurian`` link that stays inside the tree."""
    return _sweep(tmp_path_factory, _contain_the_knowledge_directory)


@pytest.fixture(scope="module")
def undoctored(tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    """The same commands over an ordinary ``.theurian`` directory."""
    return _sweep(tmp_path_factory, None)


# -- The population is the class, not a list --------------------------------


def test_every_project_paths_of_call_site_is_classified() -> None:
    """A call site added later joins this file or fails here. The closure argument.

    Set equality both ways, so neither direction can rot silently: a new
    ``ProjectPaths.of`` call with no classification fails, and a classification
    naming a call site that no longer exists fails too. Without it the sweep
    below is a list, and a list cannot report what it is missing -- which is
    exactly how the directory face survived #525's round: that closure's key was
    ``_contained``'s call sites, and this one is not in it.

    **The key is demonstrably able to hit.** Run 2026-09-07 in a throwaway copy
    of the tree with a ninth call site planted -- a module-level
    ``ninth_call_site`` in ``project_service.py`` returning ``ProjectPaths.of(root)``
    -- this reported *"the classified set and the ``ProjectPaths.of`` call sites
    have moved apart; unclassified:
    ['application.project_service::ninth_call_site']"*. A clean set-equality
    assertion that has never been shown to fail is not a closure argument.
    """
    derived = project_paths_of_call_sites()
    classified = frozenset(SITE_BY_KEY)

    assert classified == derived, (
        "the classified set and the `ProjectPaths.of` call sites have moved "
        f"apart; unclassified: {sorted(derived - classified)}, "
        f"classified but no longer present: {sorted(classified - derived)}"
    )
    assert all(site.because for site in SITES), (
        "a call site classified without a reason is one somebody forgot"
    )
    assert any(site.refuses for site in SITES), (
        "no site grades a refusal, so every property below is vacuous"
    )


def test_every_context_resolving_caller_maps_to_a_shipped_command() -> None:
    """The command sweep is derived, and the Typer app is what settles it.

    Three failures this catches that a hand-written list cannot: a command that
    starts resolving a project and is never swept; a swept entry naming a command
    the app no longer ships; and a caller renamed out from under its mapping.

    **Measured against a planted caller**, in the same throwaway copy and the
    same run as the guard above: a ``fourteenth_resolver_caller`` in
    ``cli/commands.py`` calling ``_resolve_or_refuse`` reported *"unmapped:
    ['cli.commands::fourteenth_resolver_caller']"*.
    """

    def walk(a: typer.Typer, prefix: tuple[str, ...] = ()) -> Iterator[str]:
        for info in a.registered_commands:
            name = info.name or (info.callback.__name__ if info.callback else "?")
            yield " ".join((*prefix, name))
        for group in a.registered_groups:
            sub = group.typer_instance
            if sub is None:
                continue
            name = group.name or "?"
            if sub.registered_callback is not None:
                yield " ".join((*prefix, name))
            yield from walk(sub, (*prefix, name))

    shipped = frozenset(walk(app))
    callers = context_resolving_callers()

    assert frozenset(REACHED_BY) == callers, (
        "the mapped callers and the context-resolving call sites have moved "
        f"apart; unmapped: {sorted(callers - frozenset(REACHED_BY))}, "
        f"mapped but no longer resolving: {sorted(frozenset(REACHED_BY) - callers)}"
    )
    unshipped = sorted(set(REACHED_BY.values()) - shipped)
    assert not unshipped, f"swept commands the app no longer ships: {unshipped}"


# -- The controls ------------------------------------------------------------


@_NEEDS_SYMLINKS
def test_no_swept_command_publishes_the_escape_cure_over_an_honest_tree(
    undoctored: Matrix,
) -> None:
    """Without this, no refusal below is attributable to the plant.

    Not "every command exits 0": ``propose accept`` is handed a well-formed id
    naming no proposal and refuses correctly, and ``findings build`` needs a
    fetched ``refs/remotes/origin/main`` it does not have here. What the control
    has to establish is narrower and is the thing that could be false -- that
    none of them answers with the containment cure when nothing has escaped.
    """
    wrong = {
        command: seen.remedy
        for command, seen in undoctored.items()
        if seen.published_the_escape_cure
    }

    assert not wrong, f"commands published the escape cure over an honest tree: {wrong}"


@_NEEDS_SYMLINKS
def test_a_contained_symbolic_link_is_not_refused(
    contained_link: Matrix, undoctored: Matrix
) -> None:
    """AC3. A link is refused only when it *escapes*, and this is what says so.

    Compared against the undoctored matrix command by command rather than against
    exit 0, for the reason the control above states: two of these commands refuse
    on an honest tree for reasons of their own, and a guard that started refusing
    every symbolic link would be invisible to an assertion that only demanded
    non-zero. The pair has to *agree*.
    """
    diverged = {
        command: (undoctored[command].exit_code, seen.exit_code, seen.remedy)
        for command, seen in contained_link.items()
        if seen.exit_code != undoctored[command].exit_code or seen.published_the_escape_cure
    }

    assert not diverged, (
        "a `.theurian` symbolic link that stays inside the tree changed a "
        f"command's answer (undoctored, contained, remedy): {diverged}"
    )


# -- The grading unification -------------------------------------------------


@_NEEDS_SYMLINKS
def test_every_command_grades_an_escaping_knowledge_directory_as_a_state_error(
    escaping: Matrix,
) -> None:
    """The unification #550 asks for, keyed on the directory rather than a leaf.

    RED at ``8372cc8c``: twelve of the thirteen swept commands answered ``1`` and
    ``project status`` answered ``0`` with a degraded payload, while the *leaf*
    face of the same root cause answered ``EXIT_STATE_ERROR`` from all seven
    commands measured beside it. ``EXIT_STATE_ERROR`` is the code to keep for the
    reason ``_fail_a_path_escape`` records: a working tree carrying a symbolic
    link force-added past ADR-0004's ignore is a knowledge-state problem the user
    must repair, not a command that could not run here.

    Reported as the whole set rather than at the first failure, because this is a
    class: a test that stopped at the first mis-grade would send someone to fix
    one handler while three others still disagreed.
    """
    graded = {
        command: seen.exit_code
        for command, seen in escaping.items()
        if seen.exit_code != EXIT_STATE_ERROR
    }

    assert not graded, (
        f"an escaping `.theurian` was graded something other than "
        f"{EXIT_STATE_ERROR}: {dict(sorted(graded.items()))}"
    )


@_NEEDS_SYMLINKS
def test_every_refusal_is_one_clean_envelope_naming_the_knowledge_directory_cure(
    escaping: Matrix,
) -> None:
    """The other half of the grade: a caller can read it, and it says what to do.

    Three properties that fail separately, so they are asserted separately from
    the exit code above: no exception reaches the terminal as a Rich traceback,
    the machine channel stays empty, and the remedy is the one keyed on the
    refused path -- :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`, whose subject is the
    knowledge directory itself and not a derived leaf under it.
    """
    wrong = {
        command: {
            "escaped": seen.escaped,
            "stdout": seen.stdout[:120],
            "envelope": seen.envelope,
        }
        for command, seen in escaping.items()
        if seen.escaped is not None
        or seen.stdout != ""
        or seen.envelope is None
        or not seen.envelope.get("error")
        or not seen.published_the_escape_cure
    }

    assert not wrong, f"refusals that are not one clean envelope with the cure: {wrong}"


# -- The MCP surface ---------------------------------------------------------


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
async def test_the_mcp_surface_publishes_the_cure_for_an_escaping_knowledge_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mcp.tools::_resolve``'s half, and it is a dropped cure rather than a code.

    RED at ``8372cc8c``: the resolve sat outside every ``try`` in ``_resolve``, so
    its ``ProjectError`` escaped to ``_forwarding`` -- which republishes
    ``str(exc)`` and drops ``.remedy`` deliberately, because its whole
    justification is changing nothing about what the wire carries. The agent
    received *".theurian resolves outside the project root …"* and no next action,
    while the ``read_active_state`` call one line below reached the same caller
    through ``_with_remedy`` carrying "Remove ``.theurian/state`` …" for the leaf
    face of the identical root cause.

    Driven through ``server.call_tool`` -- the entry point the transport uses --
    rather than by calling ``_resolve``: what is under test is what crosses the
    tool boundary, and the boundary is where the remedy was being lost.
    """
    with pytest.MonkeyPatch.context() as patch:
        root = _build_corpus(tmp_path, patch)
        data_dir = tmp_path / "datadir"
        _escape_the_knowledge_directory(root)
        registry = ProjectRegistry.default(data_dir)

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    server = build_server(
        registry,
        AuthorizationGrant(
            tenant=DEPLOYMENT_TENANT,
            sensitivities=EVERY_SENSITIVITY,
            acl_groups=DEPLOYMENT_ACL_GROUPS,
        ),
    )

    # `SdkToolError` and not this module's `ToolError`: `call_tool` re-raises a
    # failing tool as the SDK's own type, which is the layer the transport turns
    # into `isError=True` content. Catching the narrower one would pass for the
    # wrong reason on a refusal that never reached the boundary.
    with pytest.raises(SdkToolError) as excinfo:
        await server.call_tool("knowledge.search", {"projectId": "demo", "query": "token"})

    message = str(excinfo.value)
    assert "resolves outside the project root" in message, (
        f"the refusal did not survive the tool boundary at all: {message}"
    )
    assert KNOWLEDGE_DIR_ESCAPE_REMEDY in message, (
        f"the refusal crossed the tool boundary without its cure, so an agent is "
        f"told a path escaped and given nothing to do about it: {message}"
    )
