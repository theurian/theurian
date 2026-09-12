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
from migration_fixtures import body_pin
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
from theurian.mcp.tools import PATH_ESCAPE_REFUSAL

pytestmark = pytest.mark.integration

runner = CliRunner()

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

#: ``packages/theurian-core/src/theurian``: this file -> integration -> tests ->
#: theurian-core, then down into the package. Computed rather than written out so
#: a moved test file fails loudly instead of sweeping an empty tree.
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2] / "src" / "theurian"

#: A sentence out of :data:`~theurian.mcp.tools.PATH_ESCAPE_REFUSAL`, written out
#: rather than sliced off the constant.
#:
#: ``PATH_ESCAPE_REFUSAL in message`` is true of **every** message once the
#: constant is ``""``, and round one measured exactly that: emptied, the suite
#: stayed green while every caller who met a containment refusal was handed a
#: message with no words in it. This is the assertion that fails then -- measured
#: 2026-09-11 on this branch, emptying the constant turns this face, the face
#: beside it and the seam's unit case RED together.
#:
#: Written out in each of those three files rather than shared: three pins reading
#: from one place are one edit away from being no pin at all.
PATH_ESCAPE_SENTENCE: Final = "does not resolve to a location inside the project root"

_MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
_REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
_BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

#: A real, applied migration is written into every corpus, so the
#: escaping-``.theurian/migrations`` plant refuses a directory a build genuinely
#: read from -- not an empty one whose refusal a reader could dismiss as vacuous.
_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {_REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(_BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

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


def _scoped_calls(node: ast.AST, scope: str = "<module>") -> Iterator[tuple[str, ast.Call]]:
    """Every ``Call`` under ``node``, tagged with the innermost function it sits in.

    Attributes each call to the nearest enclosing ``def`` (re-scoping at each
    nested one, which is what keeps ``mcp/tools.py``'s tools out of
    ``register``); a call at module scope, in a class body, or inside a ``lambda``
    keeps its enclosing scope rather than vanishing. That closes the three
    evasions a function-only walk had: a ``ProjectPaths.of`` written at module
    scope, in a class body, or in a lambda was invisible to the population key and
    so needed no classification. Measured 2026-09-07: each of the three,
    planted, now arrives at the classifier as ``<module>`` (or the enclosing def)
    and fails the exact-set guard. The residual evasions -- an *aliased* import
    or a module-attribute spelling of the same call -- are not caught here and are
    forbidden instead by
    :func:`test_the_population_keys_are_not_evaded_by_an_import_alias`.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield from _scoped_calls(child, child.name)
            continue
        if isinstance(child, ast.Call):
            yield scope, child
        yield from _scoped_calls(child, scope)


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

    Bounded by spelling, and the bound is stated rather than implied: this matches
    the literal ``ProjectPaths.of`` attribute call, in any scope
    (:func:`_scoped_calls`). It does *not* resolve ``ProjectPaths`` imported under
    an alias, nor a module-attribute spelling (``ps.ProjectPaths.of``) --
    :func:`test_the_population_keys_are_not_evaded_by_an_import_alias` forbids both
    so the direct spelling is the only one, which is what makes this key complete.
    """
    return frozenset(
        f"{_module_of(path)}::{scope}"
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for scope, call in _scoped_calls(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "of"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "ProjectPaths"
    )


#: The three spellings of "resolve a project context" in ``theurian.cli``, which
#: are three *handlers* over one ``ProjectPaths.of`` call site and not three call
#: sites: ``resolve_context`` bare (``project status``, which degrades on a
#: non-escape failure but refuses an escape at ``EXIT_STATE_ERROR`` since #550),
#: ``_resolve_or_refuse`` (``init`` and ``project register``, which run before a
#: migration set exists) and ``_require_project`` (everything else). Each
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

    Same bound as the ``ProjectPaths.of`` key: any scope
    (:func:`_scoped_calls`), a bare-name call, and no alias --
    :func:`test_the_population_keys_are_not_evaded_by_an_import_alias` forbids an
    aliased import of any resolver so the bare name is the only spelling. A
    resolver's own definition is excluded, since a function is not its own caller.
    """
    return frozenset(
        f"{_module_of(path)}::{scope}"
        for path in sorted((SOURCE_ROOT / "cli").rglob("*.py"))
        for scope, call in _scoped_calls(ast.parse(path.read_text(encoding="utf-8")))
        if scope not in _RESOLVERS
        and isinstance(call.func, ast.Name)
        and call.func.id in _RESOLVERS
    )


# -- The handler-set authority key ------------------------------------------
#
# "Thirteen commands grade a doctored `.theurian` uniformly" is a claim about
# every resolver, and enumerating them by eye is how the round-one gate slipped:
# a second escape type (`PathEscapeError`, from the loader over an escaping
# `.theurian/migrations`) reached `project_status` and `_resolve_or_refuse` with
# no arm, so they graded it 0 and 1 while `_require_project` graded it 4. The key
# below makes uniformity a *measured* property: it reads every resolver's
# ``except`` arms from the AST and asserts each grades the same set of escape
# types the same way. A third escape type, or a resolver that drops an arm, fails
# it rather than shipping.


def escape_arm_types() -> frozenset[str]:
    """The escape-exception classes a resolver must arm against, derived from source.

    An escape class is one whose name ends in ``EscapeError``; the *arm* set is
    the classes among them that no other escape class subclasses. A subclass is
    caught by its parent's ``except`` (``PathDepthExceededError`` under
    ``PathEscapeError``), so it needs no arm of its own -- naming it would be a
    second key that could drift from the first. At ``8372cc8c`` this returns
    ``{PathEscapeError, ProjectPathEscapeError}``; a sibling added later (a
    ``SymlinkEscapeError`` next to them) enters here and widens every assertion
    below with no second edit.
    """
    escape_classes: dict[str, tuple[str, ...]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("EscapeError"):
                bases = tuple(b.id for b in node.bases if isinstance(b, ast.Name))
                escape_classes[node.name] = bases
    return frozenset(
        name
        for name, bases in escape_classes.items()
        if not any(base in escape_classes for base in bases)
    )


def _grades_state_error(handler: ast.ExceptHandler) -> bool:
    """Whether an ``except`` body grades ``EXIT_STATE_ERROR``.

    Two spellings reach that grade: ``_fail(..., code=EXIT_STATE_ERROR)`` and
    ``_fail_a_path_escape(...)``, whose own body is that call (pinned by
    :func:`test_the_path_escape_helper_grades_the_state_error`). Anything else --
    ``_emit(_unresolved_status(...))`` at exit 0, ``_fail(..., code=1)`` -- is not
    that grade, which is exactly what the survivors did.
    """
    for call in ast.walk(handler):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id == "_fail_a_path_escape":
            return True
        if call.func.id == "_fail" and any(
            kw.arg == "code"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "EXIT_STATE_ERROR"
            for kw in call.keywords
        ):
            return True
    return False


def _resolver_try(function: ast.AST) -> ast.Try | None:
    """The ``Try`` in ``function`` whose body calls ``resolve_context`` bare.

    The resolver's *own* guard, never a later ``try`` in the same body:
    ``project_status`` opens a second one around the pointer read, and a scan that
    took any ``try`` would grade the resolver on the wrong handlers.
    """
    for node in ast.walk(function):
        if isinstance(node, ast.Try) and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "resolve_context"
            for stmt in node.body
            for call in ast.walk(stmt)
        ):
            return node
    return None


def cli_resolver_functions() -> frozenset[str]:
    """Every ``theurian.cli`` function whose own ``try`` wraps ``resolve_context``.

    Derived, so the audit ranges over what exists rather than what was
    remembered: at ``8372cc8c`` this is exactly ``_require_project``,
    ``_resolve_or_refuse`` and ``project_status``, and every one of the thirteen
    commands routes through one of them
    (:func:`test_every_context_resolving_caller_maps_to_a_shipped_command`).
    """
    found: set[str] = set()
    for path in sorted((SOURCE_ROOT / "cli").rglob("*.py")):
        for name, node in _functions(path):
            if _resolver_try(node) is not None:
                found.add(f"{_module_of(path)}::{name}")
    return frozenset(found)


def resolver_escape_grades() -> dict[str, frozenset[str]]:
    """Per resolver, the escape types its own ``try`` grades ``EXIT_STATE_ERROR``.

    Only the arms whose handler reaches that grade count, and only the escape
    types among the names they catch: an arm that named ``PathEscapeError`` but
    graded it exit 1 would not appear here, which is the point -- naming a type is
    not grading it.
    """
    arms = escape_arm_types()
    grades: dict[str, frozenset[str]] = {}
    for path in sorted((SOURCE_ROOT / "cli").rglob("*.py")):
        for name, node in _functions(path):
            guard = _resolver_try(node)
            if guard is None:
                continue
            graded: set[str] = set()
            for handler in guard.handlers:
                if handler.type is None or not _grades_state_error(handler):
                    continue
                names = (
                    [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
                    if isinstance(handler.type, ast.Tuple)
                    else [handler.type.id]
                    if isinstance(handler.type, ast.Name)
                    else []
                )
                graded.update(n for n in names if n in arms)
            grades[f"{_module_of(path)}::{name}"] = frozenset(graded)
    return grades


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
    #: For a ``refuses`` site, whether an end-to-end test in this file drives it.
    #: ``False`` marks a contract-guarantee arm no delivered artefact can reach
    #: (``rehearse_migration_set`` resolves a tree it just created), which is
    #: graded-if-it-fires but not driven -- and
    #: :func:`test_every_refusing_site_is_driven_or_a_recorded_contract_guarantee`
    #: reads this field so it cannot be set without earning it.
    driven_here: bool = True


#: Every call site the key derives, each classified. Eight at ``8372cc8c``.
SITES: Final = (
    Site(
        key="cli.context::resolve_context",
        refuses=True,
        because=(
            "the resolve every project-scoped command makes. Reached by three "
            "handlers -- `_require_project`, `_resolve_or_refuse` (which "
            "`init_command` and `project_register` call) and `project_status`'s "
            "own direct call -- which is why the command sweep below is derived "
            "from `context_resolving_callers` rather than from this one entry."
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
        driven_here=False,
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
    "cli.review_commands::review_ingest": "review ingest",
    "cli.review_commands::review_build": "review build",
}

#: A valid `ProposalId` naming nothing, so `propose accept` gets past its own
#: argument parse and reaches `_require_project`. A malformed one exits 2 there
#: and would measure the parser instead of the resolve.
ABSENT_PROPOSAL: Final = "01J0000000000000000000000A"

#: A repository no allowlist here names, so `review ingest` gets past its own
#: required argument and reaches `_require_project`. It never reaches the
#: allowlist, let alone a spawn: the context resolve is the first thing the
#: command does, and over a doctored tree it refuses there.
UNLISTED_REPOSITORY: Final = "acme/order-service"

#: The argv each swept command needs beyond its path. `propose` is a callback
#: group whose required options are checked before its body runs, so it takes the
#: full set or it never reaches the resolve.
EXTRA_ARGS: Final[dict[str, tuple[str, ...]]] = {
    "propose accept": (ABSENT_PROPOSAL,),
    "review ingest": (UNLISTED_REPOSITORY,),
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

    A real migration is written and applied, so the escaping-``migrations`` plant
    below refuses a directory a build actually read. No ``index build``: nothing
    in this file reads an index, and the resolve under test happens before one is
    opened. Both redirections go through ``monkeypatch`` rather than
    ``os.environ``, and the ``chdir`` is here because the CLI resolves a project
    from the working directory -- a sweep that forgot it would resolve the
    developer's own checkout.
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
    (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(_BODY, encoding="utf-8")
    (root / f".theurian/migrations/{_MIGRATION_ID}-auth.yaml").write_text(
        _MIGRATION, encoding="utf-8"
    )
    _run("migrate", "apply")
    # `propose`'s required `--body-file`, a file the escape refuses before it is read.
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


def _escape_the_migrations_directory(root: Path) -> Path:
    """Deliver ``.theurian/migrations`` as a link that leaves the tree.

    The sibling face of the whole-``.theurian`` plant, and it travels through a
    *different guard*. ``.theurian`` stays an honest directory, so
    ``ProjectPaths.of``'s join check waves it through; the loader is what refuses
    the escaping ``migrations`` directory, raising ``PathEscapeError`` (the
    domain type, from the migration loader, #233) rather than the
    ``ProjectPathEscapeError`` ``ProjectPaths.of`` raises. A resolver that armed
    only the second type grades this one through whatever its generic branch
    assigns -- which is the asymmetry #550's round-one gate found.
    """
    outside = root.parent / "outside_migrations"
    shutil.move(str(root / ".theurian" / "migrations"), str(outside))
    (root / ".theurian" / "migrations").symlink_to(Path("..") / ".." / "outside_migrations")
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
def escaping_migrations(tmp_path_factory: pytest.TempPathFactory) -> Matrix:
    """Every swept command over an escaping ``.theurian/migrations``."""
    return _sweep(tmp_path_factory, _escape_the_migrations_directory)


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


#: The names both population keys match by spelling. An alias or a
#: module-attribute access of any of them is the one evasion :func:`_scoped_calls`
#: does not close, so the guard below forbids it -- turning "the direct spelling
#: is the only spelling" from an assumption into a checked property.
_KEYED_NAMES: Final = frozenset(
    {"ProjectPaths", "resolve_context", "_require_project", "_resolve_or_refuse"}
)


def test_the_population_keys_are_not_evaded_by_an_import_alias() -> None:
    """The residual bound both keys carry, made a checked property.

    ``_scoped_calls`` catches a call in any scope, but both keys still match by
    *name*: ``ProjectPaths`` in ``ProjectPaths.of``, and the bare resolver names.
    Two spellings would slip past -- ``from … import ProjectPaths as PP`` then
    ``PP.of(...)``, or ``import … as m`` then ``m.ProjectPaths.of(...)``. Neither
    is used, and this asserts it across the whole source, so the keys' completeness
    claim rests on a forbidden alternative rather than on trust.

    Measured 2026-09-07: aliasing ``ProjectPaths`` to ``PP`` at one import and
    calling ``PP.of`` fails here by name, before the population key ever reads
    the call.
    """
    aliased: dict[str, list[str]] = {}
    attributed: dict[str, list[str]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        module = _module_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in _KEYED_NAMES and alias.asname is not None:
                        aliased.setdefault(module, []).append(f"{alias.name} as {alias.asname}")
            elif isinstance(node, ast.Attribute) and node.attr in _KEYED_NAMES:
                # `X.ProjectPaths` / `X._require_project`: the name reached as an
                # attribute of something, which neither key can see.
                attributed.setdefault(module, []).append(node.attr)

    assert not aliased, (
        "a keyed name is imported under an alias, so the population keys cannot "
        f"see calls through it: {aliased}"
    )
    assert not attributed, (
        "a keyed name is reached as a module attribute (`m.Name`), which the "
        f"population keys match by bare name cannot see: {attributed}"
    )


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


# -- The handler-set authority key -------------------------------------------


def test_the_path_escape_helper_grades_the_state_error() -> None:
    """``_fail_a_path_escape`` grades ``EXIT_STATE_ERROR``, which the AST key trusts.

    :func:`_grades_state_error` treats a call to ``_fail_a_path_escape`` as that
    grade without reading its body, so this pins the fact that call stands in for.
    A refactor that made the helper grade exit 1 would pass every resolver's key
    while silently regrading three escape arms; this is what stops that.
    """
    helper = next(
        node
        for _name, node in _functions(SOURCE_ROOT / "cli" / "commands.py")
        if _name == "_fail_a_path_escape"
    )
    calls_state_error_fail = any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_fail"
        and any(
            kw.arg == "code"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "EXIT_STATE_ERROR"
            for kw in call.keywords
        )
        for call in ast.walk(helper)
    )
    assert calls_state_error_fail, (
        "`_fail_a_path_escape` no longer grades EXIT_STATE_ERROR, so the handler-set "
        "key below trusts a call that no longer means what it assumes"
    )


def test_every_cli_resolver_grades_the_same_escape_types() -> None:
    """The authority key. RED at ``dbad3898`` before the loader-escape arm landed.

    "Thirteen commands grade a doctored ``.theurian`` uniformly" is a claim about
    the resolvers they route through, and the round-one gate found it false by a
    type the eye had not enumerated: an escaping ``.theurian/migrations`` raises
    the loader's ``PathEscapeError``, not ``ProjectPaths.of``'s
    ``ProjectPathEscapeError``, and ``project_status`` and ``_resolve_or_refuse``
    armed only the second -- grading the first ``0`` and ``1`` where
    ``_require_project`` graded it ``4``.

    So this does not enumerate. It derives the escape-arm universe
    (:func:`escape_arm_types`), derives the resolvers
    (:func:`cli_resolver_functions`), reads each resolver's ``EXIT_STATE_ERROR``
    arms from the AST (:func:`resolver_escape_grades`), and asserts every resolver
    grades the whole universe. Ranging over the three handlers rather than the
    thirteen commands is sound because
    :func:`test_every_context_resolving_caller_maps_to_a_shipped_command` proves
    each command routes through one of them.

    RED measured at ``dbad3898`` (this branch's first fix commit, before the
    loader arm): ``resolver_escape_grades`` returned ``{'_require_project':
    {PathEscapeError, ProjectPathEscapeError}, '_resolve_or_refuse':
    {ProjectPathEscapeError}, 'project_status': {ProjectPathEscapeError}}`` -- two
    resolvers short one arm.
    """
    universe = escape_arm_types()
    resolvers = cli_resolver_functions()
    grades = resolver_escape_grades()

    assert universe, "no escape-arm classes were derived, so this asserts nothing"
    assert resolvers == frozenset(grades), (
        "a resolver was derived without a grade set, or vice versa; the two "
        f"derivations disagree: resolvers={sorted(resolvers)}, graded={sorted(grades)}"
    )

    short = {
        resolver: sorted(universe - graded)
        for resolver, graded in grades.items()
        if graded != universe
    }
    assert not short, (
        "these resolvers do not grade every escape type as EXIT_STATE_ERROR, so "
        "one root cause answers different codes depending on which type escaped "
        f"(resolver -> unarmed escape types): {short}"
    )


@_NEEDS_SYMLINKS
def test_every_command_grades_an_escaping_migrations_directory_as_a_state_error(
    escaping_migrations: Matrix,
) -> None:
    """The loader-escape face, driven end to end. RED at ``dbad3898``.

    ``.theurian`` is honest and ``migrations`` under it is the escaping link, so
    ``ProjectPaths.of`` passes and the loader refuses with ``PathEscapeError``.
    Measured at ``dbad3898`` across the sweep: ``init`` and ``project register``
    answered ``1`` and ``project status`` answered ``0`` with a payload calling
    the project registered, while the ten ``_require_project`` commands answered
    ``4``. This holds the whole sweep at ``EXIT_STATE_ERROR``.

    The envelope is a clean ``{error, remedy}`` naming ``.theurian/migrations`` --
    the loader's own culprit-naming remedy (#233), *not*
    :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`, because a different guard refused a
    different path. So this asserts the grade and the envelope's cleanliness,
    never the knowledge-directory cure.
    """
    graded = {
        command: seen.exit_code
        for command, seen in escaping_migrations.items()
        if seen.exit_code != EXIT_STATE_ERROR
    }
    assert not graded, (
        f"an escaping `.theurian/migrations` was graded something other than "
        f"{EXIT_STATE_ERROR}: {dict(sorted(graded.items()))}"
    )

    wrong = {
        command: {"escaped": seen.escaped, "stdout": seen.stdout[:120], "envelope": seen.envelope}
        for command, seen in escaping_migrations.items()
        if seen.escaped is not None
        or seen.stdout != ""
        or seen.envelope is None
        or not seen.envelope.get("error")
        or not seen.envelope.get("remedy")
        or ".theurian/migrations" not in str(seen.envelope.get("remedy"))
    }
    assert not wrong, f"refusals that are not one clean envelope naming the culprit: {wrong}"


@_NEEDS_SYMLINKS
def test_every_refusing_site_is_driven_or_a_recorded_contract_guarantee(
    escaping: Matrix, escaping_migrations: Matrix
) -> None:
    """``Site.refuses`` is load-bearing: every ``True`` site names how it is proved.

    Without this the field is decorative -- nothing reads it, so a site could
    claim to refuse and never be exercised (adversarial M-4). The refusing set is
    pinned to its exact three members, so a new one forces an entry here rather
    than inheriting silent coverage, and each of the three carries its own proof:

    - ``cli.context::resolve_context`` -- the thirteen-command sweeps above, both
      uniform ``EXIT_STATE_ERROR``; asserted here again so the field points at a
      live matrix, not a memory.
    - ``mcp.tools::_resolve`` -- driven by the async MCP test; its grading path
      (``ProjectPaths.of`` wrapped in a ``try``) is asserted structurally so this
      does not rest on that test alone.
    - ``cli.migration_pipeline::rehearse_migration_set`` -- ``driven_here=False``,
      a contract-guarantee arm over a tree the process just created. Its grade, if
      it ever fired, is ``propose accept``'s ``except ProjectPathEscapeError`` arm,
      asserted to exist so "graded rather than excused" is checked, not claimed.
    """
    refusing = {s.key for s in SITES if s.refuses}
    assert refusing == {
        "cli.context::resolve_context",
        "mcp.tools::_resolve",
        "cli.migration_pipeline::rehearse_migration_set",
    }, f"the refusing set moved; each new member needs a driving proof here: {sorted(refusing)}"

    # resolve_context: the live matrices, both uniform.
    assert escaping and escaping_migrations, "a sweep produced no rows, so this proves nothing"
    off = {
        f"{shape} {command}": seen.exit_code
        for shape, matrix in (("escaping", escaping), ("migrations", escaping_migrations))
        for command, seen in matrix.items()
        if seen.exit_code != EXIT_STATE_ERROR
    }
    assert not off, f"a driven CLI resolve site did not grade EXIT_STATE_ERROR: {off}"

    # _resolve: its grading path exists (a `try` guarding the `ProjectPaths.of`).
    resolve = next(
        node for name, node in _functions(SOURCE_ROOT / "mcp" / "tools.py") if name == "_resolve"
    )
    assert any(
        isinstance(node, ast.Try)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "of"
            for stmt in node.body
            for call in ast.walk(stmt)
        )
        for node in ast.walk(resolve)
    ), "`mcp.tools::_resolve` no longer guards its `ProjectPaths.of`, so the MCP site is undriven"

    # rehearse_migration_set's grade, if it fired: propose_accept's escape arm.
    accept = next(
        node
        for name, node in _functions(SOURCE_ROOT / "cli" / "propose_commands.py")
        if name == "propose_accept"
    )
    assert any(
        isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "ProjectPathEscapeError"
        for node in ast.walk(accept)
    ), "`propose_accept` lost its `except ProjectPathEscapeError` arm; rehearse's grade is unbacked"

    for site in SITES:
        if site.refuses and not site.driven_here:
            assert "contract-guarantee" in site.because, (
                f"{site.key} is refuses=True, driven_here=False, but does not record why "
                f"it cannot be driven"
            )


# -- The MCP surface ---------------------------------------------------------


#: The knowledge tools that resolve through ``_resolve`` and their minimal args.
#: All three, not just ``search``: the docstring's claim is "every knowledge tool
#: resolves through it", so driving one would leave the universal unproven for the
#: other two -- ``knowledge.get`` and ``knowledge.status`` reach the same
#: ``_resolve`` and must publish the same cure.
_KNOWLEDGE_TOOLS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    ("knowledge.search", {"query": "token"}),
    ("knowledge.get", {"itemId": "architecture.auth-policy"}),
    ("knowledge.status", {}),
)


@pytest.mark.asyncio
@_NEEDS_SYMLINKS
@pytest.mark.parametrize(
    "tool, extra", _KNOWLEDGE_TOOLS, ids=lambda v: v if isinstance(v, str) else ""
)
async def test_the_mcp_surface_publishes_the_cure_for_an_escaping_knowledge_directory(
    tool: str, extra: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mcp.tools::_resolve``'s half: the cure crosses, the layout does not.

    The ``ProjectPaths.of`` arm of the containment envelope, driven over every
    knowledge tool. Two things are pinned, and each was false at a different
    commit:

    **The cure travels.** RED at ``8372cc8c``, where the resolve sat outside every
    ``try`` in ``_resolve``, so its ``ProjectError`` escaped to ``_forwarding`` --
    which republishes ``str(exc)`` and drops ``.remedy`` deliberately, because its
    whole justification is changing nothing about what the wire carries. The agent
    was told a path escaped and given no next action, while the
    ``read_active_state`` call one line below reached the same caller through
    ``_with_remedy`` carrying "Remove ``.theurian/state`` …" for the leaf face of
    the identical root cause.

    **And it travels alone.** RED at ``68d8ee19``, where the message half that
    then crossed was :meth:`ProjectPaths.of`'s own -- *"{directory} resolves
    outside the project root {resolved}, so every file …"*, whose second
    interpolation is the root after ``.resolve()``. That is the operator's
    **resolved** filesystem layout, published to an MCP caller by a refusal (the
    GHSA-97q9 class). ``_with_remedy`` now substitutes
    :data:`~theurian.mcp.tools.PATH_ESCAPE_REFUSAL` for that message wherever a
    ``ProjectPathEscapeError`` crosses the tool boundary, so what this test asserts
    is the constant, byte-identical, and the **absence of any absolute path**.

    The cure is layout-free by construction rather than by luck:
    :meth:`ProjectPaths.of` keys both of its raises to
    :data:`KNOWLEDGE_DIR_ESCAPE_REMEDY`, a module constant that interpolates
    nothing at all, so dropping the message costs the caller nothing actionable.

    **The absence assertion is over the whole absolute-path population**, not over
    the pair the old message happened to name: ``str(tmp_path.resolve())`` is a
    prefix of every path this corpus can build -- the checkout, the plant's target,
    ``HOME`` and the data directory alike -- so it fires for a disclosure through
    any substring at all, and the named candidates beside it say *which* one
    leaked.

    **This corpus registers ``demo`` by its real path**, so the registered spelling
    and the resolved form coincide here: :func:`_build_corpus` ``mkdir``s a true
    directory under ``tmp_path``, and pytest's temporary root is itself already
    resolved (measured 2026-09-11, Darwin 25.6). The assertion below therefore
    covers the registered-spelling-equals-resolved case; the differential where the
    two differ -- a root registered through a symbolic link, where the resolved
    form is a string the registry does not hold -- is pinned by the face beside
    this one, in ``test_review_findings_tool.py``:
    ``test_an_escaping_state_directory_is_refused_without_naming_the_resolved_layout``.

    Driven through ``server.call_tool`` -- the entry point the transport uses --
    rather than by calling ``_resolve``: what is under test is what crosses the
    tool boundary, and the boundary is where both the remedy was being lost and the
    layout was being published. Over all three knowledge tools, because
    ``_resolve`` runs on every one of them and the docstring says so.
    """
    with pytest.MonkeyPatch.context() as patch:
        root = _build_corpus(tmp_path, patch)
        data_dir = tmp_path / "datadir"
        outside = _escape_the_knowledge_directory(root)
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
        await server.call_tool(tool, {"projectId": "demo", **extra})

    message = str(excinfo.value)
    assert PATH_ESCAPE_REFUSAL in message, (
        f"{tool}: the refusal did not survive the tool boundary as the one constant "
        f"`_with_remedy` substitutes for a `ProjectPathEscapeError`'s own message: {message}"
    )
    assert PATH_ESCAPE_SENTENCE in message, (
        f"{tool}: the constant crossed the boundary without saying what went wrong, so "
        f"the assertion above is satisfied by a refusal carrying no words at all: {message}"
    )
    assert KNOWLEDGE_DIR_ESCAPE_REMEDY in message, (
        f"{tool}: the refusal crossed the tool boundary without its cure, so an "
        f"agent is told a path escaped and given nothing to do about it: {message}"
    )
    published = {
        name: value
        for name, value in (
            ("the resolved project root", str(root.resolve())),
            ("the registered spelling of the root", str(root)),
            ("the directory the knowledge link escaped to", str(outside.resolve())),
            ("the temporary tree this run was given", str(tmp_path.resolve())),
        )
        if value in message
    }
    assert not published, (
        f"{tool}: the refusal published the operator's resolved filesystem layout to an "
        f"MCP caller (GHSA-97q9): {published}\n{message}"
    )


def test_the_mcp_resolver_cannot_reach_the_loaders_escape_type() -> None:
    """The MCP face audited against the same escape-type key, by reachability.

    ``mcp.tools::_resolve`` is a resolver too, but not a ``resolve_context`` one:
    the daemon never loads migrations, so the loader's ``PathEscapeError`` cannot
    arise there. That is what makes ``ProjectPathEscapeError`` its *only* reachable
    escape type -- and the one it guards, through ``_with_remedy`` (the test above
    proves the cure travels). Asserting the reachability rather than adding a
    ``PathEscapeError`` arm is the honest audit: an arm for a type that cannot be
    raised is dead code that reads as coverage.

    Two AST facts settle it: ``_resolve`` calls neither ``load_migrations`` nor
    ``resolve_context`` (the only routes to the loader), and it appears in no
    ``resolver_escape_grades`` entry, because it wraps ``ProjectPaths.of`` in its
    own ``try`` rather than ``resolve_context``.
    """
    resolve = next(
        node for name, node in _functions(SOURCE_ROOT / "mcp" / "tools.py") if name == "_resolve"
    )
    reaches_loader = {
        call.func.id
        for call in _own_calls(resolve)
        if isinstance(call.func, ast.Name)
        and call.func.id in {"load_migrations", "resolve_context"}
    }
    assert not reaches_loader, (
        "`mcp.tools::_resolve` now reaches the migration loader "
        f"({sorted(reaches_loader)}), so `PathEscapeError` is reachable there and "
        "the daemon needs the same arm the CLI resolvers carry -- this reachability "
        "audit no longer covers it"
    )
    assert "mcp.tools::_resolve" not in resolver_escape_grades(), (
        "`mcp.tools::_resolve` now guards a `resolve_context` call, so it belongs "
        "to the CLI handler-set key and must grade every escape type there"
    )
