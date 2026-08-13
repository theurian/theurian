"""``theurian auth`` — explicit token rotation (ADR-0011, SEC-4).

Rotation is a deliberate act and never a side effect. Setup mints a token only
when there is none, and nothing regenerates one silently, because replacing a
token breaks every configured client at once with no explanation.

The awkward part is that rotation has *three* participants, and getting two of
them right is worse than getting none: the token file, the running daemon (which
read the old token into memory at startup), and every shell that already exported
the old value. Writing a new file alone leaves clients reading a token the daemon
will reject — a 401 whose cause is invisible. So this restarts the daemon when it
can, and says plainly what the user must do when it cannot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from theurian.cli.setup_commands import _executable
from theurian.daemon.instance import DEFAULT_PORT, probe_health
from theurian.domain.ports.daemon_manager import ServiceState
from theurian.infrastructure.secrets.file_store import (
    TOKEN_KEY,
    FileSecretStore,
    default_data_dir,
)
from theurian.infrastructure.services import detect_manager
from theurian.security.env_file import MalformedEnvBlockError, merge_env_file
from theurian.security.tokens import TOKEN_ENV_VAR, describe, generate_token

auth_app = typer.Typer(help="Manage the local access token.", no_args_is_help=True)


@auth_app.command("rotate")
def auth_rotate(
    port: Annotated[int, typer.Option("--port")] = DEFAULT_PORT,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Replace the local access token.

    Run this after a token has been exposed — a world-readable file, a token
    pasted into an issue, a shared machine. Tightening a file's mode is not
    enough once its contents have been readable by someone else.
    """
    from theurian.cli.commands import _emit  # noqa: PLC0415 - avoids a circular import

    data_dir = default_data_dir()
    store = FileSecretStore(data_dir)

    token = generate_token()
    asyncio.run(store.set(TOKEN_KEY, token))

    # Rewritten too: it names the token's location, and a rotation that left a
    # stale env file pointing somewhere else would be a 401 with no visible cause.
    env_remedy = _refresh_env_file(data_dir)

    restarted, remedy = _restart_daemon(port=port)

    _emit(
        {
            "rotated": True,
            # Never the token itself. This is enough to confirm a change without
            # putting the new credential in a terminal's scrollback (SEC-6).
            "token": describe(token),
            "tokenFile": str(data_dir / "auth" / TOKEN_KEY),
            "daemonRestarted": restarted,
            "nextSteps": env_remedy + remedy,
        },
        as_json=as_json,
    )


def _refresh_env_file(data_dir: Path) -> list[str]:
    """Bring the env file's Theurian block up to date, keeping the rest.

    The same managed block ``theurian setup`` writes, through the same merge:
    this command used to render the whole file and truncate whatever else was
    in it, so a rotation destroyed the lines its own header invites people to
    add (#128). Rotation is usually run *because* a credential has been
    exposed, which is the worst moment to take something away silently.

    Returns:
        Lines to prepend to ``nextSteps``, empty when the file was updated.

    Markers that do not delimit one block leave the file untouched and put the
    repair in the output instead. The alternative orderings are both worse:
    refusing to rotate leaves the exposed token in place over a comment marker,
    and rewriting anyway is the data loss this function exists to end. The
    token has already been replaced by the time this runs, so the shell that
    sources this file needs the block fixed before it stops seeing 401s.
    """
    env_path = data_dir / "env"
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else None
    try:
        merged = merge_env_file(existing, data_dir)
    except MalformedEnvBlockError as exc:
        return [f"{env_path} was left untouched: {exc}"]

    env_path.write_text(merged, encoding="utf-8")
    env_path.chmod(0o600)
    return []


def _restart_daemon(*, port: int) -> tuple[bool, list[str]]:
    """Restart the daemon so it picks up the new token.

    The daemon reads its token once, at startup. Until it restarts it keeps
    checking against the old one, so every client that correctly re-reads the
    file gets a 401 — the exact failure rotation is supposed to prevent.
    """
    reload_shell = (
        f"Open a new shell, or re-source your profile, so ${TOKEN_ENV_VAR} picks up the new value."
    )

    service = detect_manager(executable=_executable())
    if service is not None:
        status = asyncio.run(service.status())
        if status.state is not ServiceState.NOT_INSTALLED:
            asyncio.run(service.restart())
            return True, [reload_shell]

    if probe_health(port=port) is not None:
        return False, [
            "Restart the daemon: it read the old token at startup and will "
            "reject the new one until it does.",
            reload_shell,
        ]

    return False, [reload_shell]
