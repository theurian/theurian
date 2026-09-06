"""What the two readers of a migration set catch, and what that partition rests on.

`doctor`'s ``migrations-valid`` step publishes one of two failure sentences, and
which one it publishes is decided by ``cli/setup_commands._check_migrations``'s
``except`` clauses. Issue #529 is what happens when that decision is absent: a
build that cannot locate or read the JSON Schemas it publishes was reported as
"The migrations in <dir> do not validate.", and under ``doctor --report`` that
sentence was the whole message.

Four claims hold the split up, and this module keys each one to a check that
goes red when it stops being true. They are here rather than beside the
behaviour tests because none of them is about *which* sentence the step
publishes -- the behaviour tests own that -- and each is about the shape of the
thing that publishes it.

1. **The population.** ``_check_migrations`` has exactly two handlers and
   ``probe_migrations`` has none. Enumerated from the AST rather than asserted
   in prose, so a third handler added to either one fails here.
2. **The refusal set did not move.** The union of those two handlers is exactly
   ``TheurianError`` -- the set ``theurian migrate validate`` refuses on, which
   #91 exists to keep the two commands agreeing about. The partition is a
   partition, not a narrowing.
3. **The first handler is exact.** ``except (SchemaUnreadableError,
   ProjectError)`` names the two install-integrity faces *and nothing else*,
   because inside that ``try`` the only ``ProjectError`` raised is
   ``schema_root()``'s.
4. **The arm it unlocks carries a real remedy.** The whole value of splitting
   is that the install faces get a cure the operator can run, so the constant
   has to name the thing to act on *and* a command that acts on it. Every
   reader of the constants themselves compares against them whole rather than
   looking inside, so none can see one emptied of meaning. Measured 2026-09-05
   in a throwaway clone: replacing ``SCHEMAS_UNUSABLE_ACTION`` with "Something
   went wrong." left 35 of the 36 tests in this fix's scope green, and the one
   failure was ``test_the_install_arm_names_something_the_reader_can_run``
   below. Two *other* tests do inspect a remedy's content --
   ``test_cli_commands.py::test_a_build_that_cannot_find_its_schemas_is_diagnosed_by_its_own_remedy``
   and ``test_daemon_extra.py::test_the_reinstall_commands_are_the_install_commands_with_force``
   -- and both were written after that measurement, so neither was in it.

Claim 3 is the one with a wrong key available, and the wrong key was in the
tree: two docstrings recorded "``ProjectError`` has no subclasses anywhere in
this tree". It was true when #519 wrote it and false two days later, which is
what a count-shaped key does -- a change adds a member to the population and
nothing re-checks the universals over it:

.. code-block:: console

   $ git grep -nE '^class [A-Za-z_]+\\(ProjectError\\)' 5157da73 -- packages/theurian-core/src
   $ git grep -nE '^class [A-Za-z_]+\\(ProjectError\\)' 22ce405b -- packages/theurian-core/src
   .../project_service.py:525:class ProjectPathEscapeError(ProjectError):
   .../project_service.py:581:class GitignoreIsASymbolicLinkError(ProjectError):

Counting is the wrong key anyway: what matters is not how many subclasses exist
but whether any can be *raised* inside the ``try``. So the key here is import
reachability. ``ProjectError`` is defined in
``application/project_service.py``; code that never reaches that module cannot
raise it, whatever the class hierarchy does.

**A reachability claim is only as strong as the walker computing it**, and the
first version of this one was weaker than every sentence citing it. It missed
relative imports entirely, never walked the package ``__init__`` modules Python
executes on the way, and had a positive control that found the target one edge
out -- so a walker that stopped after a single hop would have passed it.
:func:`_import_closure` now follows all three edges, and
:func:`test_the_walker_follows_a_planted_relative_import_chain` walks a tree
built for the purpose, with a negative arm, because a control drawn from this
tree can only exercise the shapes this tree happens to contain. Repairing it
grew the two swept closures from 9 and 36 modules to 14 and 39, and the answer
did not change: neither reaches ``project_service``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from theurian.application.project_service import ProjectError
from theurian.application.setup_steps import SCHEMAS_UNUSABLE_ACTION, SCHEMAS_UNUSABLE_SUMMARY
from theurian.domain.errors import SchemaUnreadableError, TheurianError
from theurian.domain.extras import DAEMON_REINSTALL, DAEMON_REINSTALL_COMMANDS

SRC = Path(__file__).resolve().parents[2] / "src" / "theurian"

#: The package every first-party module lives under. Passed to the walker rather
#: than closed over, so a planted tree can be walked with the same code the real
#: claims are measured with -- a control that ran a *second* walker would prove
#: nothing about this one.
ROOT_PACKAGE = "theurian"

#: Where ``ProjectError`` is defined. Nothing can raise it without importing
#: this module, which is the whole of claim 3's key.
PROJECT_ERROR_HOME = "theurian.application.project_service"

#: The one call inside ``_check_migrations``' ``try`` that legitimately raises a
#: ``ProjectError`` -- the whole point of the first handler. Every *other* callee
#: is derived from the try body itself (:func:`_try_body_callee_modules`) rather
#: than listed, so a fourth call added there joins the sweep without anybody
#: remembering to add it.
THE_CALL_THAT_MAY_RAISE_A_PROJECT_ERROR = "schema_root"


def _module_path(name: str, *, root: Path, package: str) -> Path | None:
    relative = name.removeprefix(f"{package}.").replace(".", "/")
    if name == package:
        relative = ""
    for candidate in (root / f"{relative}.py", root / relative / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _is_package(name: str, *, root: Path, package: str) -> bool:
    if name == package:
        return True
    relative = name.removeprefix(f"{package}.").replace(".", "/")
    return (root / relative / "__init__.py").is_file()


def _ancestors(name: str) -> set[str]:
    """Every package Python executes on the way to importing ``name``.

    ``import theurian.a.b`` runs ``theurian/__init__.py`` and
    ``theurian/a/__init__.py`` first, so anything either of those imports is
    reachable from ``b`` at runtime whether or not ``b`` names it. Omitting them
    left 27 of this tree's 28 package ``__init__`` modules outside every closure
    the first version of this walker computed.
    """
    parts = name.split(".")
    return {".".join(parts[:index]) for index in range(1, len(parts))}


def _absolute(node: ast.ImportFrom, *, inside: str, root: Path, package: str) -> str | None:
    """``node``'s target as an absolute module name, resolving ``from .x import y``.

    The version this replaces tested ``node.module.startswith("theurian")``,
    which is ``False`` for every relative import in the language: ``from
    .project_service import ProjectError`` parses as ``module='project_service',
    level=1``. So a relative import of the forbidden module was invisible to the
    walker, and ruff's ``TID252`` does not cover it either -- its default bans
    only *parent* relatives, and a same-package one is exactly the shape that
    would matter here.
    """
    if node.level == 0:
        return node.module
    base = inside if _is_package(inside, root=root, package=package) else inside.rpartition(".")[0]
    for _ in range(node.level - 1):
        base = base.rpartition(".")[0]
    if not base:
        return None
    return f"{base}.{node.module}" if node.module else base


def _import_closure(entry: str, *, root: Path = SRC, package: str = ROOT_PACKAGE) -> frozenset[str]:
    """Every first-party module reachable from ``entry``, by imports and by ancestry.

    Three edges, and the first version had only the third:

    * **Ancestry** -- see :func:`_ancestors`.
    * **Relative imports** -- see :func:`_absolute`.
    * **Absolute imports**, including the ``from x import y`` submodule edge:
      ``y`` is itself a module whenever ``x`` is a package. That edge is a
      measured no-op on this tree (the closure is byte-identical with it
      removed, both entry points), and is kept because ``from theurian.domain
      import errors`` is a form the codebase is free to start using -- not
      because anything here currently needs it.

    ``root``/``package`` are parameters so
    :func:`test_the_walker_follows_a_planted_relative_import_chain` can point
    the same function at a tree it built, which is the only way a control says
    anything about *this* walker.
    """
    seen: set[str] = set()
    pending = [entry, *_ancestors(entry)]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(_ancestors(name) - seen)
        path = _module_path(name, root=root, package=package)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(a.name for a in node.names if a.name.split(".")[0] == package)
            elif isinstance(node, ast.ImportFrom):
                resolved = _absolute(node, inside=name, root=root, package=package)
                if resolved is None or resolved.split(".")[0] != package:
                    continue
                pending.append(resolved)
                pending.extend(
                    f"{resolved}.{alias.name}"
                    for alias in node.names
                    if _module_path(f"{resolved}.{alias.name}", root=root, package=package)
                    is not None
                )
    return frozenset(seen)


def _function(module: str, name: str) -> ast.FunctionDef:
    path = _module_path(module, root=SRC, package=ROOT_PACKAGE)
    assert path is not None, f"{module} is not a module in this tree"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"{module}.{name}: {len(found)} definitions"
    return found[0]


def _imported_from(module: str) -> dict[str, str]:
    """Every name ``module`` imports, mapped to the module it came from."""
    path = _module_path(module, root=SRC, package=ROOT_PACKAGE)
    assert path is not None, f"{module} is not a module in this tree"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    origins: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            resolved = _absolute(node, inside=module, root=SRC, package=ROOT_PACKAGE)
            if resolved is None:
                continue
            for alias in node.names:
                origins[alias.asname or alias.name] = resolved
    return origins


def _try_body_callee_modules(module: str, function: str) -> dict[str, str]:
    """The module behind each function ``function``'s ``try`` body calls.

    Derived rather than listed. A hand-written tuple beside a file this module
    already parses is a second enumeration of the same thing, and the one that
    goes stale is the hand-written one: a fourth call added to that ``try`` would
    silently leave the reachability sweep, which is the sweep that makes the
    typed handler exact.
    """
    tries = [node for node in ast.walk(_function(module, function)) if isinstance(node, ast.Try)]
    assert len(tries) == 1, f"{module}.{function}: {len(tries)} try statements"
    origins = _imported_from(module)
    called: dict[str, str] = {}
    # ``.body`` and not the whole ``Try``: the handlers construct
    # ``MigrationsCheck``, which is imported like everything else and would ride
    # into a sweep that is about what can *raise into* them.
    for statement in tries[0].body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                origin = origins.get(node.func.id)
                if origin is not None:
                    called[node.func.id] = origin
    return called


def _handlers_in(definition: ast.FunctionDef) -> tuple[tuple[str, ...], ...]:
    handlers: list[tuple[str, ...]] = []
    for node in ast.walk(definition):
        if not isinstance(node, ast.ExceptHandler):
            continue
        caught = node.type
        if caught is None:
            handlers.append(("<bare except>",))
        elif isinstance(caught, ast.Tuple):
            handlers.append(tuple(ast.unparse(element) for element in caught.elts))
        else:
            handlers.append((ast.unparse(caught),))
    return tuple(handlers)


def _handler_types(module: str, function: str) -> tuple[tuple[str, ...], ...]:
    """The exception names each ``except`` clause of ``function`` catches, in order."""
    return _handlers_in(_function(module, function))


def _handler_types_at(path: Path, function: str) -> tuple[tuple[str, ...], ...]:
    """The same, for a file outside the package -- the test double lives in ``tests/``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    ]
    assert len(found) == 1, f"{path.name}:{function}: {len(found)} definitions"
    return _handlers_in(found[0])


def test_the_checker_catches_exactly_two_things_and_the_probe_catches_nothing() -> None:
    """Claim 1, the population -- and the positive control for the claim below it.

    ``probe_migrations`` catching nothing is what makes the classification the
    checker's alone: a handler there would be a second reader of the same load,
    reaching its own verdict, which is #91's divergence in a new place. That
    assertion is worthless on its own, though -- a walker that found no handler
    anywhere would pass it -- so it is asserted beside the two the checker does
    have, which the same walker has to find.
    """
    assert _handler_types("theurian.cli.setup_commands", "_check_migrations") == (
        ("SchemaUnreadableError", "ProjectError"),
        ("TheurianError",),
    )
    assert _handler_types("theurian.application.setup_steps", "probe_migrations") == ()


def test_the_double_mirrors_the_checkers_handlers() -> None:
    """``setup_migrations.checked_by_the_loader`` catches what production catches.

    The hazard the double's own docstring names, now checked: it once caught
    ``Exception``, which is wider than production's net, so a family that
    escapes ``_check_migrations`` and reaches an operator as CONFLICTING "Could
    not check migrations-valid" came back through the double as an ordinary
    verdict -- #91's divergence living inside the fixture built to measure it.
    Since #529 the net is a *partition*, and a double that mirrored the net but
    flattened the partition would answer every install fault with the
    migrations-are-broken arm while the suite stayed green.

    Compared clause for clause and in order, because order is what decides which
    arm a ``SchemaUnreadableError`` takes: both types are ``TheurianError``s, so
    a pair swapped end to end catches everything in the first clause.
    """
    double = Path(__file__).resolve().parents[1] / "setup_migrations.py"

    assert _handler_types_at(double, "checked_by_the_loader") == _handler_types(
        "theurian.cli.setup_commands", "_check_migrations"
    )


def test_the_two_handlers_partition_the_refusal_set_rather_than_narrowing_it() -> None:
    """Claim 2: the union is still exactly ``TheurianError``.

    Asked of Python rather than read off the class statements, because a base
    class named through an alias or moved between modules still answers this
    correctly. If either type stopped being a ``TheurianError``, the checker
    would let it escape to ``SetupService._probe``'s generic net -- CONFLICTING
    "Could not check migrations-valid", which stops setup for consent -- on a
    tree ``theurian migrate validate`` simply refuses. That is #91, and it has
    already happened twice.
    """
    for narrower in (SchemaUnreadableError, ProjectError):
        assert issubclass(narrower, TheurianError), (
            f"{narrower.__name__} is caught before the TheurianError clause, so a "
            f"{narrower.__name__} that is not a TheurianError silently widens the set"
        )


def test_the_try_bodys_calls_are_the_two_derived_ones_and_schema_root() -> None:
    """Claim 3's population, taken from the ``try`` rather than written beside it.

    The check below sweeps whatever this returns, so this is where a fourth call
    appearing in that ``try`` becomes visible instead of quietly widening what
    the first handler catches. Asserting the exact mapping also pins the one
    deliberate exclusion: ``schema_root`` is the call the handler exists *for*.
    """
    assert _try_body_callee_modules("theurian.cli.setup_commands", "_check_migrations") == {
        "load_migrations": "theurian.infrastructure.filesystem.migration_loader",
        "schema_root": "theurian.cli.context",
        "run_static_migration_guards": "theurian.application.migration_engine",
    }


def _callees_that_must_not_raise_a_project_error() -> list[str]:
    called = _try_body_callee_modules("theurian.cli.setup_commands", "_check_migrations")
    excluded = called.pop(THE_CALL_THAT_MAY_RAISE_A_PROJECT_ERROR, None)
    assert excluded is not None, (
        f"{THE_CALL_THAT_MAY_RAISE_A_PROJECT_ERROR} is no longer called in that try; "
        f"the exclusion below is now silently sweeping nothing it was meant to skip"
    )
    modules = sorted(set(called.values()))
    # An empty derivation is the failure mode a derived parameter list invites,
    # and pytest does not report it as one: zero parameters is a *skip*, so the
    # sweep would go quiet with the suite still reading green. Measured -- a
    # perturbation that emptied this returned "176 passed, 1 skipped".
    assert modules, (
        "the reachability sweep derived no callees from `_check_migrations`' try "
        "body; a parametrization with no cases skips rather than fails, so this "
        "assertion is what keeps an empty sweep from reading as a clean one"
    )
    return modules


@pytest.mark.parametrize("callee", _callees_that_must_not_raise_a_project_error())
def test_nothing_the_checkers_try_calls_can_raise_a_project_error(callee: str) -> None:
    """Claim 3: ``schema_root()`` is the only ``ProjectError`` inside that ``try``.

    Reachability, not a subclass count. ``ProjectError`` lives in
    ``application/project_service.py``, so a module that never reaches it --
    transitively, over first-party imports and through the packages Python
    executes on the way -- cannot name it and cannot raise it. Adding that
    import to the loader or to the guards would make the first handler catch a
    refusal that is *not* about the installation, and ``migrations-valid`` would
    tell the operator to reinstall Theurian over it.

    The parameters are derived from the ``try`` body, minus ``schema_root``.
    """
    assert PROJECT_ERROR_HOME not in _import_closure(callee)


def test_the_install_arm_names_something_the_reader_can_run() -> None:
    """Claim 4: the remedy is a cure, not a truthy string.

    The two sentences the install arm publishes are the only rescue on a shared
    report, and every test that pins them does it by comparing against these
    same constants -- so the whole suite stays green when they are replaced with
    a placeholder. This is the check that does not.

    **The command is asserted against
    :data:`~theurian.domain.extras.DAEMON_REINSTALL_COMMANDS`, never as a
    literal.** Spelling ``--python 3.13`` here would put a fourth copy of the
    ``requires-python`` floor outside the population
    ``test_daemon_extra.py::test_the_install_commands_pin_the_python_core_requires``
    sweeps, and would then *hold the arm to it*: a floor raise that correctly
    updated ``domain/extras.py`` would fail here and read as this test catching
    a regression, when what it caught was its own stale copy. A test that
    entrenches the drift it exists to prevent is worse than no test.
    """
    assert SCHEMAS_UNUSABLE_ACTION.startswith(DAEMON_REINSTALL), (
        "the cure is the shared one, so the floor and both installers reach this arm"
    )
    for installer in DAEMON_REINSTALL_COMMANDS:
        assert installer in SCHEMAS_UNUSABLE_ACTION, (
            "uv and pipx: every other remedy in the tree offers both, and a pipx "
            "user handed only the uv form has none"
        )
        assert "--force" in installer, (
            "a plain re-install of an already-installed requirement is a no-op that "
            "exits 0, so this remedy would report success and leave the build broken"
        )
    assert "`.theurian/migrations`" in SCHEMAS_UNUSABLE_ACTION, (
        "the arm exists to say which files are *not* the thing to edit; naming "
        "them is how it says it"
    )
    assert "installation" in SCHEMAS_UNUSABLE_SUMMARY, "the summary must name the culprit"
    assert "could not be validated" in SCHEMAS_UNUSABLE_SUMMARY, (
        "the summary names the operation that failed, which is the half that is "
        "true whichever of the two faces refused"
    )
    assert "do not validate" not in SCHEMAS_UNUSABLE_SUMMARY, (
        "that is the other arm's sentence, and publishing it here is #529 itself"
    )


def test_the_reachability_walker_finds_project_service_where_it_is_reachable() -> None:
    """The positive control the sweep above is worthless without -- and it is weak.

    ``cli/setup_commands`` imports ``ProjectError`` directly, so this catches a
    walker that answers "not reachable" for everything: a typo in the module
    name, a ``from`` edge never followed.

    **It catches nothing about depth**, and saying so is the point. The target
    sits one edge from this entry, and measured at 22ce405b no module in the
    tree reaches it at depth three or more -- so a walker that stopped after one
    hop would pass here while reporting a clean sweep it never performed. The
    planted control below is what covers that, and the relative-import edge with
    it.
    """
    assert PROJECT_ERROR_HOME in _import_closure("theurian.cli.setup_commands")


def _plant(root: Path, modules: dict[str, str]) -> None:
    for name, source in modules.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_the_walker_follows_a_planted_relative_import_chain(tmp_path: Path) -> None:
    """A tree built here, walked by the same function the real claims use.

    Two things this proves that the real tree cannot, because the real tree does
    not currently contain either shape:

    * **Relative imports are followed.** ``from .forbidden import Boom`` parses
      as ``module='forbidden', level=1``, and the walker this replaces tested
      ``node.module.startswith("theurian")`` -- ``False`` for every relative
      import in the language. Ruff's ``TID252`` does not cover the gap: its
      default bans parent-relative imports only, so a same-package relative is
      both lint-clean and, before this, invisible.
    * **Ancestor packages are walked.** ``nested/__init__.py`` is imported by
      nothing; it is reached only because Python executes it on the way to
      ``nested.leaf``, and it is what imports ``forbidden``. Dropping that edge
      changed no test outcome when the planted tree was flat, which is why the
      tree is not flat.
    * **Depth is unbounded.** ``forbidden`` is three edges from ``entry``, so a
      walker that stopped early fails here -- which the real positive control
      above cannot detect, since nothing in the real tree is that far from the
      target.

    The negative arm is not decoration. Without it a walker that simply returned
    every module under the root would pass the positive arm, and every "does not
    reach ``project_service``" assertion in this file would be reporting on a
    walker that cannot say no.
    """
    root = tmp_path / "planted"
    _plant(
        root,
        {
            "__init__.py": "",
            "entry.py": "from .middle import helper\n",
            "middle.py": "from .nested.leaf import thing\n\n\ndef helper() -> None: ...\n",
            # Reachable *only* as an ancestor of `planted.nested.leaf`: nothing
            # imports this package by name, and Python runs it on the way in.
            "nested/__init__.py": "from ..forbidden import Boom\n",
            "nested/leaf.py": "thing = 1\n",
            "forbidden.py": "class Boom(Exception): ...\n",
            "unrelated.py": "value = 1\n",
        },
    )
    reached = _import_closure("planted.entry", root=root, package="planted")

    assert "planted.nested" in reached, (
        "nothing names this package; it is reached only because Python executes a "
        "package's __init__ before the submodule under it"
    )
    assert "planted.forbidden" in reached, (
        "three edges out, through a package __init__ nobody imports, by a "
        "``from ..x import Y`` relative -- none of which the first version of this "
        "walker could follow"
    )
    assert "planted.middle" in reached
    assert "planted" in reached
    assert "planted.unrelated" not in reached, (
        "nothing imports it; a walker that returns every module under the root "
        "cannot answer the question this file asks"
    )
