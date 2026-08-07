"""The optional dependency groups, and what a bare install is missing (ADR-0014).

`pip install theurian` deliberately installs the CLI and the migration engine and
nothing else, so a CI image or a pre-commit hook does not carry a web server it
never starts (``pyproject.toml``). **The split is sound; what it costs is that
the next step of the documented flow needs the extra.** A user who follows
``uv tool install theurian`` and then runs ``theurian daemon start`` meets
``ModuleNotFoundError: No module named 'uvicorn'`` -- Python's answer, not
Theurian's, naming a package the user never asked for and no command to fix it.

This module is the one place the words are chosen, because the two sides of that
sentence live in different layers: the CLI catches the import failure and the
setup step probes for it before anything is registered, and a remedy that
disagrees between them is worse than one that is merely terse.

**Nothing here inspects the environment.** Deciding whether a module is present
is I/O against ``sys.path`` and belongs to whoever is asking; this module only
says which modules would answer for the ``daemon`` extra and what to run when
they do not.
"""

from __future__ import annotations

from typing import Final

#: The extra that carries the MCP daemon, spelled as ``pyproject.toml`` spells it.
DAEMON_EXTRA: Final = "daemon"

#: Third-party top-level modules that ``theurian.daemon`` and ``theurian.mcp``
#: import and a bare install does not have.
#:
#: ``mcp`` and ``uvicorn`` are named directly by the ``daemon`` extra;
#: ``starlette`` arrives through ``mcp`` and is listed anyway, because what
#: matters here is which import failure the extra answers for, not which line of
#: ``pyproject.toml`` put the wheel on disk. ``watchfiles`` is in the extra and is
#: **not** listed: nothing in ``src/`` imports it, so its absence cannot be the
#: cause of a ``ModuleNotFoundError`` anyone will see.
#:
#: Held to the source by
#: ``tests/unit/test_daemon_extra.py::test_every_third_party_import_of_the_daemon_is_named_here``,
#: which walks the two packages rather than trusting this list -- adding an
#: import to ``daemon/server.py`` is exactly how a user gets the raw traceback
#: back.
DAEMON_MODULES: Final = ("mcp", "starlette", "uvicorn")

#: What a user with no Theurian at all runs. Both work on a machine that has
#: neither; see :data:`DAEMON_EXTRA_REMEDY` for why repairing an install that
#: already exists is not the same command.
DAEMON_INSTALLERS: Final = (
    "uv tool install 'theurian[daemon]'",
    "pipx install 'theurian[daemon]'",
)

#: What to say to someone who already has a bare Theurian.
#:
#: **The pipx form is not the same as the one in :data:`DAEMON_INSTALLERS`, and
#: that difference is measured rather than defensive.** Against pipx 1.16.6 and
#: `theurian` 0.1.0.dev0, ``pipx install 'theurian[daemon]'`` over an existing
#: bare install prints "'theurian' (0.1.0.dev0) already seems to be installed.
#: Not modifying existing installation" and exits 0 -- the extra is not added,
#: and the user's next ``theurian daemon start`` fails exactly as before.
#: ``--force`` installs into the existing venv and does add it. ``uv tool
#: install`` needs no such flag: it re-resolves and adds the extra in place.
#: A remedy a user can follow to completion and still be broken is worse than no
#: remedy, because it moves the blame onto them.
DAEMON_EXTRA_REMEDY: Final = (
    f"Install it with `{DAEMON_INSTALLERS[0]}`. With pipx, run "
    f"`pipx install --force 'theurian[{DAEMON_EXTRA}]'` -- a plain `pipx install` "
    f"leaves an existing installation untouched and would report success."
)


def provided_by_daemon_extra(module: str | None) -> bool:
    """Whether a failed import names something the ``daemon`` extra would supply.

    Takes the top-level package, because ``ModuleNotFoundError.name`` is the
    first name that could not be resolved and that is dotted whenever a package
    is present but a submodule is not. Measured: with ``mcp`` absent,
    ``from mcp.server import MCPServer`` reports ``mcp``; with ``mcp`` present
    and ``mcp.server`` gone it would report ``mcp.server``, and the answer wanted
    is the same in both cases -- a reinstall of the extra.

    ``None`` is false rather than an error. ``ModuleNotFoundError`` allows a
    missing ``name`` and a guard is the wrong place to discover it: attributing
    an unnamed import failure to the extra would put this remedy in front of a
    user whose problem is something else entirely.
    """
    if not module:
        return False
    return module.split(".", 1)[0] in DAEMON_MODULES
