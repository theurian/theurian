"""Theurian CLI entry point.

A composition root: this is one of the three places allowed to name concrete
adapters (ADR-0003). Milestone 0 ships only the version surface, because that is
what the plugin's compatibility gate depends on -- everything else lands in
Milestones 1 through 8.

Every command supports ``--json``. The JSON shape is a published contract
validated against ``schemas/cli/`` by ``tests/contract/``; the plugin depends on
it, so it changes only with a protocol version bump.
"""

from __future__ import annotations

import json
import platform
import sys
from typing import Annotated

import typer

from theurian import __protocol_version__, __version__
from theurian.cli import commands
from theurian.domain.compatibility import (
    CompatibilityDeclaration,
    CompatibilityOutcome,
    Version,
    resolve_compatibility,
)
from theurian.domain.errors import DomainError

app = typer.Typer(
    name="theurian",
    help="Git-native engineering knowledge for AI agents.",
    no_args_is_help=True,
    add_completion=False,
)

compat_app = typer.Typer(help="Inspect Core/plugin compatibility.", no_args_is_help=True)
app.add_typer(compat_app, name="compat")
app.add_typer(commands.project_app, name="project")
app.add_typer(commands.migrate_app, name="migrate")
app.command("init")(commands.init_command)
app.command("ingest")(commands.ingest_command)

#: Exit code for a compatibility mismatch. Distinct from 1 so a caller can tell
#: "incompatible" apart from "the command itself failed".
EXIT_INCOMPATIBLE = 3


def _emit(payload: dict[str, object], *, as_json: bool) -> None:
    """Write a result to stdout in the requested format.

    Machine output goes to stdout and nothing else does, so a caller can pipe
    stdout into a JSON parser without filtering log lines out of it.
    """
    if as_json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return
    for key, value in payload.items():
        sys.stdout.write(f"{key}: {value}\n")


def _version_payload() -> dict[str, object]:
    return {
        "name": "theurian",
        "version": __version__,
        "protocolVersion": __protocol_version__,
        "python": platform.python_version(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
    }


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    show_version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Print the Theurian version and exit."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Theurian command-line interface."""
    if show_version:
        _emit(_version_payload(), as_json=as_json)
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        sys.stdout.write(ctx.get_help() + "\n")
        raise typer.Exit(0)


@app.command("version")
def version_command(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Report the Theurian Core and protocol versions.

    The plugin calls this to resolve compatibility before doing anything else
    (ADR-0001, §30). Its output shape is part of the published CLI contract.
    """
    _emit(_version_payload(), as_json=as_json)


@compat_app.command("check")
def compat_check(
    plugin_version: Annotated[
        str, typer.Option("--plugin-version", help="Version of the calling plugin.")
    ],
    core_minimum: Annotated[
        str, typer.Option("--core-minimum", help="Oldest Core version the plugin supports.")
    ],
    core_maximum_exclusive: Annotated[
        str,
        typer.Option(
            "--core-maximum-exclusive",
            help="First Core version the plugin does not support.",
        ),
    ],
    protocol_version: Annotated[
        str, typer.Option("--protocol-version", help="Wire protocol the plugin speaks.")
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Resolve whether a plugin may operate against this Core.

    Core performs the comparison so that no client reimplements SemVer ordering
    or the PEP 440 translation (§34: no duplicated Core logic in a plugin). A
    client passes its declaration and reads the verdict.

    Exit codes: 0 compatible, 3 incompatible, 2 malformed input.
    """
    try:
        declaration = CompatibilityDeclaration(
            plugin_version=Version.parse(plugin_version),
            core_minimum=Version.parse(core_minimum),
            core_maximum_exclusive=Version.parse(core_maximum_exclusive),
            protocol_version=protocol_version,
        )
    except DomainError as exc:
        _emit({"outcome": "invalid-declaration", "error": str(exc)}, as_json=as_json)
        raise typer.Exit(2) from exc

    verdict = resolve_compatibility(
        declaration,
        Version.parse_python(__version__),
        __protocol_version__,
    )

    _emit(
        {
            "outcome": verdict.outcome.value,
            "compatible": verdict.is_compatible,
            "message": verdict.message,
            "remedy": verdict.remedy,
            "pluginVersion": str(verdict.plugin_version),
            "coreVersion": str(verdict.core_version) if verdict.core_version else None,
            "protocolVersion": verdict.protocol_version,
        },
        as_json=as_json,
    )

    if verdict.outcome is not CompatibilityOutcome.COMPATIBLE:
        raise typer.Exit(EXIT_INCOMPATIBLE)


if __name__ == "__main__":  # pragma: no cover
    app()
