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
from typing import Final, cast

import pytest

from theurian.domain.extras import (
    DAEMON_EXTRA,
    DAEMON_EXTRA_REMEDY,
    DAEMON_INSTALLERS,
    DAEMON_MODULES,
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


def test_every_third_party_import_of_the_daemon_is_named_here() -> None:
    """:data:`DAEMON_MODULES` is the source's answer, not a remembered one.

    Adding an import to ``daemon/runner.py`` without adding it here is what puts
    ``ModuleNotFoundError`` back in front of a user: the CLI guard re-raises
    anything it does not recognise, so an unlisted module produces exactly the
    traceback this whole change exists to remove.
    """
    imported: set[str] = set()
    for package in DAEMON_PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            imported |= _third_party(_top_level_imports(path))

    assert imported == set(DAEMON_MODULES), (
        f"{DAEMON_PACKAGES} import {sorted(imported)}; DAEMON_MODULES says "
        f"{sorted(DAEMON_MODULES)}. Update theurian/domain/extras.py."
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

    Three literals: both :data:`DAEMON_INSTALLERS` entries and the pipx
    ``--force`` form inside :data:`DAEMON_EXTRA_REMEDY`. ``requires-python`` is
    where the floor is actually declared; these only repeat it, and nothing in
    the build derives one from the other. Raising the floor to 3.14 without
    touching them therefore ships ``uv tool install --python 3.13
    'theurian[daemon]'`` as the command Theurian prints when the daemon will not
    start -- a remedy that cannot resolve, because the only wheel it may install
    excludes the interpreter it just asked for.

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

    for command in (*DAEMON_INSTALLERS, DAEMON_EXTRA_REMEDY):
        pinned = set(_PINNED_PYTHON.findall(command))
        assert pinned == {floor}, (
            f"`requires-python` says >={floor}, but this pins {sorted(pinned) or 'nothing'}: "
            f"{command}"
        )
