"""What a bare install cannot do, and what Theurian says about it (#78, ADR-0014).

``uv tool install theurian`` — the command every install surface names — installs
a Theurian whose daemon cannot start, because ``uvicorn`` lives in the ``daemon``
extra. The packaging split is deliberate; the defect is that Python answered for
it, with a traceback naming a package the user never asked for.

Two claims are held here, and the first is the one that rots:

- :data:`~theurian.domain.extras.DAEMON_MODULES` is **derived from the source**,
  not read back. A hand-written list of third-party imports is correct on the day
  it is written and silently wrong the first time ``daemon/server.py`` grows an
  import — and the symptom of being wrong is the raw traceback coming back.
- The guard fires on the extra's modules and on nothing else. A ``theurian``
  submodule that fails to import is a bug in Theurian, and answering it with
  "install the daemon extra" would send the user to reinstall a package that
  already contains the broken file.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from typing import Final, cast

import pytest

from theurian.domain.extras import (
    DAEMON_EXTRA,
    DAEMON_EXTRA_REMEDY,
    DAEMON_INSTALLERS,
    DAEMON_MODULES,
    DAEMON_REINSTALL,
    DAEMON_REINSTALL_COMMANDS,
    provided_by_daemon_extra,
)

PACKAGE_ROOT: Final = pathlib.Path(__file__).resolve().parents[2]
SRC: Final = PACKAGE_ROOT / "src" / "theurian"
PYPROJECT: Final = PACKAGE_ROOT / "pyproject.toml"

#: ``requires-python``, in the only shape this repository writes it. Matched
#: whole rather than searched, because a spec that grew a second clause is a
#: question about which bound the install commands should name -- and answering
#: it by taking the first number found is how the wrong one gets shipped.
_REQUIRES_PYTHON: Final = re.compile(r">=\s*(?P<floor>\d+\.\d+)")

#: The interpreter an install command pins, whatever flags surround it.
_PINNED_PYTHON: Final = re.compile(r"--python (?P<version>\S+)")

#: The distribution name at the head of a requirement string, before any
#: version pin, extra or marker.
_DISTRIBUTION: Final = re.compile(r"^[A-Za-z0-9._-]+")

#: The packages that only run once the ``daemon`` extra is installed. Not a
#: guess: these are the three modules that import ``uvicorn``, ``mcp`` or
#: ``starlette`` at module scope, and
#: :func:`test_no_other_package_imports_the_daemon_extra_at_module_scope` holds
#: the tree to it.
DAEMON_PACKAGES: Final = ("daemon", "mcp")


def _top_level_imports(path: pathlib.Path) -> set[str]:
    """Every top-level module name ``path`` imports, at any nesting.

    ``ast.walk`` rather than ``tree.body`` on purpose. ``cli/commands.py`` keeps
    its daemon imports inside the functions that need them -- measured at 170 ms
    versus 600 ms for ``theurian --version`` -- so a scan that read only
    module-scope statements would report the CLI as free of the extra while it
    is exactly where the failure surfaces.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module.split(".", 1)[0])
    return names


def _requires_python_floor() -> str:
    """The lowest Python ``pyproject.toml`` says the distribution accepts.

    Read rather than remembered, which is the whole point of the assertion it
    feeds: nothing in the build derives ``domain/extras.py``'s ``3.13`` from this
    declaration, and every production caller interpolates those constants instead
    of repeating the number, so this is where the family is held or nowhere.
    """
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    spec = cast(str, cast(dict[str, object], metadata["project"])["requires-python"])

    match = _REQUIRES_PYTHON.fullmatch(spec.strip())
    assert match is not None, (
        f"`requires-python` is now `{spec}`, which is not a bare `>=X.Y`. Decide "
        f"which bound the install commands should name and teach this test the "
        f"new shape -- theurian/domain/extras.py holds the literals."
    )
    return match.group("floor")


def _third_party(names: set[str]) -> set[str]:
    """Whatever is neither Theurian nor the standard library.

    ``sys.stdlib_module_names`` is the interpreter's own answer rather than a
    list maintained here, so a module that moves into or out of the standard
    library does not need an edit in this file to stay classified correctly.
    """
    return {
        name
        for name in names
        if name != "theurian" and name not in sys.stdlib_module_names and not name.startswith("_")
    }


def _normalized(requirement: str) -> str:
    """A requirement string reduced to its PEP 503 normalized distribution name."""
    match = _DISTRIBUTION.match(requirement.strip())
    name = match.group(0) if match else requirement.strip()
    return re.sub(r"[-_.]+", "-", name).lower()


def _core_dependency_modules() -> set[str]:
    """The top-level modules a *bare* install already carries.

    Derived, not listed: a distribution name is not an import name (``pyyaml``
    provides ``yaml``, ``python-ulid`` provides ``ulid``), so the declared
    requirements are mapped through the installed metadata rather than guessed
    at from their spelling.

    The sweep below needs this because the two questions stopped coinciding.
    ``DAEMON_MODULES`` answers *which missing import the daemon extra would
    supply*; walking ``theurian.mcp`` answers *which third-party modules it
    imports*, and since ``mcp/validation.py`` those include ``jsonschema`` and
    ``referencing`` -- core runtime dependencies a bare install already has.
    Listing them as daemon modules would answer a broken *core* install with
    "install the daemon extra", a remedy ``domain/extras.py`` calls worse than
    none because a user can follow it to completion and stay broken.
    """
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = cast(dict[str, object], metadata["project"])
    declared = {_normalized(spec) for spec in cast(list[str], project["dependencies"])}
    return {
        module
        for module, distributions in packages_distributions().items()
        if any(_normalized(name) in declared for name in distributions)
    }


def test_every_third_party_import_of_the_daemon_is_named_here() -> None:
    """:data:`DAEMON_MODULES` is the source's answer, not a remembered one.

    Adding an import to ``daemon/runner.py`` without adding it here is what puts
    ``ModuleNotFoundError`` back in front of a user: the CLI guard re-raises
    anything it does not recognise, so an unlisted module produces exactly the
    traceback this whole change exists to remove.

    What the sweep subtracts is the core requirement set
    (:func:`_core_dependency_modules`), because an import a bare install already
    satisfies is not one the extra answers for. The disjointness assertion is
    the control on that subtraction: a derivation that swallowed ``mcp`` or
    ``uvicorn`` would hide the very imports this test exists to catch, and would
    do it silently.
    """
    imported: set[str] = set()
    for package in DAEMON_PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            imported |= _third_party(_top_level_imports(path))

    core = _core_dependency_modules()
    assert core.isdisjoint(DAEMON_MODULES), (
        f"{sorted(core & set(DAEMON_MODULES))} is being read as a core requirement, "
        f"which would hide it from this sweep. Check pyproject.toml's dependencies."
    )
    assert imported - core == set(DAEMON_MODULES), (
        f"{DAEMON_PACKAGES} import {sorted(imported - core)} beyond the core "
        f"requirements; DAEMON_MODULES says {sorted(DAEMON_MODULES)}. "
        f"Update theurian/domain/extras.py."
    )


def test_no_other_package_imports_the_daemon_extra_at_module_scope() -> None:
    """The extra stays behind a lazy import everywhere else.

    ``theurian --version`` and ``theurian daemon status`` run on a bare install,
    and they only keep running while nothing on their import path names the
    extra. A module-scope ``import uvicorn`` in ``cli/`` would break every
    command at once, including the one the SessionStart hook runs on every
    session.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.relative_to(SRC).parts[0] in DAEMON_PACKAGES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
                names = {node.module.split(".", 1)[0]}
            else:
                continue
            offenders.extend(
                f"{path.relative_to(SRC)} imports {name} at module scope"
                for name in sorted(names & set(DAEMON_MODULES))
            )

    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "name",
    [
        "uvicorn",
        "mcp",
        "mcp.server",
        "starlette",
        "starlette.applications",
    ],
)
def test_the_guard_claims_what_the_extra_supplies(name: str) -> None:
    assert provided_by_daemon_extra(name)


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "theurian",
        "theurian.daemon.runner",
        "pydantic",
        # A prefix match would accept these: the split is on the dot, not on the
        # string, so a distribution that merely begins with a listed name is not
        # answered with "install the daemon extra".
        "mcpx",
        "uvicorn_worker",
    ],
)
def test_the_guard_disclaims_everything_else(name: str | None) -> None:
    assert not provided_by_daemon_extra(name)


def test_the_remedy_names_a_command_for_each_installer_the_surfaces_offer() -> None:
    """Every surface tells the user to install with uv *or* pipx, so both need one.

    Measured against pipx 1.16.6: ``pipx install 'theurian[daemon]'`` over an
    existing bare install changes nothing and exits 0. A remedy that named only
    the uv form would leave every pipx user without one; a remedy that named the
    plain pipx form would leave them following it and still broken.

    **The literal asserted below carries ``--python 3.13``; the measurement did
    not.** The flag arrived with :data:`DAEMON_INSTALLERS`, and it is pinned here
    so the remedy cannot drift away from the commands beside it -- not because
    anyone ran the flagged form against an existing install. What the run
    settled is ``--force``, and that is present in both spellings.
    """
    assert "uv tool install" in DAEMON_EXTRA_REMEDY
    assert f"pipx install --force --python 3.13 'theurian[{DAEMON_EXTRA}]'" in DAEMON_EXTRA_REMEDY
    for installer in DAEMON_INSTALLERS:
        assert f"theurian[{DAEMON_EXTRA}]" in installer


def test_the_install_commands_pin_the_python_core_requires() -> None:
    """The bare ``3.13`` in ``domain/extras.py``, held to the metadata that decides it.

    Three literals -- both :data:`DAEMON_INSTALLERS` entries and the pipx
    ``--force`` form inside :data:`DAEMON_EXTRA_REMEDY` -- plus
    :data:`DAEMON_REINSTALL`, which is *derived* from the first pair and is swept
    here anyway: derivation is a claim, and this is the check that it holds
    rather than an assumption that it does. ``requires-python`` is
    where the floor is actually declared; these only repeat it, and nothing in
    the build derives one from the other. Raising the floor to 3.14 without
    touching them therefore ships ``uv tool install --python 3.13
    'theurian[daemon]'`` as the command Theurian prints when the daemon will not
    start -- a remedy that cannot resolve, because the only wheel it may install
    excludes the interpreter it just asked for.

    **A remedy outside this sweep is the defect this exists to prevent, and one
    shipped.** ``migrations-valid``'s reinstall arm was written with a fourth
    ``--python 3.13`` literal of its own; it now composes
    :data:`DAEMON_REINSTALL` instead, so the floor reaches it from here.

    Every ``--python`` in each string is checked rather than the first: the
    remedy names two commands, and a fix that updated one of them is exactly the
    half-edit this exists to catch.

    **Nothing else in the suite catches the raise.** Several modules pin the
    literal ``--python 3.13`` -- ``test_compatibility``, ``test_bare_install``,
    ``test_cli_help_without_rich``, ``test_setup_claims`` -- so they redden when
    the *constants* move and stay green when the *floor* does. Measured: with
    ``requires-python`` at ``>=3.14`` and every literal left at 3.13, this is the
    only failure in 3205 tests.
    """
    floor = _requires_python_floor()

    for command in (*DAEMON_INSTALLERS, DAEMON_EXTRA_REMEDY, DAEMON_REINSTALL):
        pinned = set(_PINNED_PYTHON.findall(command))
        assert pinned == {floor}, (
            f"`requires-python` says >={floor}, but this pins {sorted(pinned) or 'nothing'}: "
            f"{command}"
        )


def test_the_reinstall_commands_are_the_install_commands_with_force() -> None:
    """The derivation :data:`DAEMON_REINSTALL_COMMANDS` performs, checked not assumed.

    It inserts ``--force`` after the installer's ``install`` verb, which is a
    string edit and therefore something that can silently stop matching: a
    :data:`DAEMON_INSTALLERS` entry rephrased so that `` install `` no longer
    appears would yield a "reinstall" command with no ``--force`` in it, and the
    only symptom would be an operator following the remedy to completion and
    staying broken. That is the failure ``--force`` is here to prevent, so the
    edit is held rather than trusted.

    The pipx entry is compared against the spelling :data:`DAEMON_EXTRA_REMEDY`
    already writes out, which pins the two remedies to one command instead of
    letting them drift into two answers for the same installer.
    """
    assert len(DAEMON_REINSTALL_COMMANDS) == len(DAEMON_INSTALLERS)

    for original, forced in zip(DAEMON_INSTALLERS, DAEMON_REINSTALL_COMMANDS, strict=True):
        assert forced != original, "the derivation produced the install command unchanged"
        assert "--force" in forced
        assert forced.split()[0] == original.split()[0], "the installer's own program name"
        assert f"theurian[{DAEMON_EXTRA}]" in forced

    assert DAEMON_REINSTALL_COMMANDS[1] in DAEMON_EXTRA_REMEDY, (
        "the pipx repair command has one spelling in this module, not two"
    )
    for command in DAEMON_REINSTALL_COMMANDS:
        assert command in DAEMON_REINSTALL, "the sentence names both installers"
