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
   has to name the thing to act on *and* a command that acts on it. Every other
   reader compares against the constant as a whole rather than looking inside
   it, so none of them can see it emptied of meaning: ``git grep -n
   SCHEMAS_UNUSABLE -- packages/`` answers 25 lines across four files at this
   commit -- one of them this sentence -- and the only assertions that inspect
   the string's *content* are in the last test here. Measured 2026-09-05 in a
   throwaway clone: replacing the constant with "Something went wrong." left 35
   of the 36 tests in this fix's scope green, and that test was the one failure.

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
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from theurian.application.project_service import ProjectError
from theurian.application.setup_steps import SCHEMAS_UNUSABLE_ACTION, SCHEMAS_UNUSABLE_SUMMARY
from theurian.domain.errors import SchemaUnreadableError, TheurianError

SRC = Path(__file__).resolve().parents[2] / "src" / "theurian"

#: Where ``ProjectError`` is defined. Nothing can raise it without importing
#: this module, which is the whole of claim 3's key.
PROJECT_ERROR_HOME = "theurian.application.project_service"

#: Everything ``_check_migrations``' ``try`` calls, as module entry points. The
#: ``try`` body is ``load_migrations(...)``, ``schema_root()`` and
#: ``run_static_migration_guards(...)``; ``schema_root`` is excluded on purpose
#: -- it is the one call that *does* raise ``ProjectError``, and claim 3 says it
#: is the only one.
CALLEES_THAT_MUST_NOT_RAISE_A_PROJECT_ERROR = (
    "theurian.infrastructure.filesystem.migration_loader",
    "theurian.application.migration_engine",
)


def _module_path(name: str) -> Path | None:
    relative = name.removeprefix("theurian.").replace(".", "/")
    for candidate in (SRC / f"{relative}.py", SRC / relative / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _import_closure(entry: str) -> frozenset[str]:
    """Every first-party module reachable from ``entry`` by following imports.

    ``from x import y`` is followed as both ``x`` and ``x.y``, because the second
    is a module in this tree whenever ``x`` is a package -- and missing that edge
    is how a closure walker reports a clean answer for a graph it never entered.
    """
    seen: set[str] = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_path(name)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(a.name for a in node.names if a.name.startswith("theurian."))
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                if not node.module.startswith("theurian"):
                    continue
                pending.append(node.module)
                pending.extend(
                    f"{node.module}.{a.name}"
                    for a in node.names
                    if _module_path(f"{node.module}.{a.name}") is not None
                )
    return frozenset(seen)


def _handler_types(module: str, function: str) -> tuple[tuple[str, ...], ...]:
    """The exception names each ``except`` clause of ``function`` catches, in order."""
    path = _module_path(module)
    assert path is not None, f"{module} is not a module in this tree"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    ]
    assert len(definitions) == 1, f"{module}.{function}: {len(definitions)} definitions"
    handlers: list[tuple[str, ...]] = []
    for node in ast.walk(definitions[0]):
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


@pytest.mark.parametrize("callee", CALLEES_THAT_MUST_NOT_RAISE_A_PROJECT_ERROR)
def test_nothing_the_checkers_try_calls_can_raise_a_project_error(callee: str) -> None:
    """Claim 3: ``schema_root()`` is the only ``ProjectError`` inside that ``try``.

    Reachability, not a subclass count. ``ProjectError`` lives in
    ``application/project_service.py``, so a module that never reaches it --
    transitively, over first-party imports -- cannot name it and cannot raise
    it. Adding that import to the loader or to the guards would make the first
    handler catch a refusal that is *not* about the installation, and
    ``migrations-valid`` would tell the operator to reinstall Theurian over it.
    """
    assert PROJECT_ERROR_HOME not in _import_closure(callee)


def test_the_install_arm_names_something_the_reader_can_run() -> None:
    """Claim 4: the remedy is a cure, not a truthy string.

    The two sentences the install arm publishes are the only rescue on a shared
    report, and every test that pins them does it by comparing against these
    same constants -- so the whole suite stays green when they are replaced with
    a placeholder. This is the check that does not.

    ``uv tool install`` is asserted whole rather than by the word "reinstall",
    because a remedy that names an action without naming the command that
    performs it sends the reader to look one up.
    """
    assert "`uv tool install --force --python 3.13 'theurian[daemon]'`" in SCHEMAS_UNUSABLE_ACTION
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
    """The positive control the test above is worthless without.

    ``cli/setup_commands`` imports ``ProjectError`` directly, so a walker that
    answered "not reachable" for everything -- a typo in the module name, a
    ``from`` edge never followed -- fails here instead of reporting the clean
    result it never measured.
    """
    assert PROJECT_ERROR_HOME in _import_closure("theurian.cli.setup_commands")
