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
import os
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

    # Brought up to date too. Not because rotation moves anything it names --
    # the block references the token by *path*, and rotation changes the value
    # in that file and not the path to it -- but because this is the moment the
    # user is about to re-source the file: a machine whose block is absent,
    # stale or pre-marker exports nothing or exports the wrong path, and the
    # 401 that follows would look like the rotation's fault.
    #
    # Those three shapes and no others. A line *below* the block that assigns
    # the token again survives this and goes unmentioned: rotation writes the
    # block, the shell keeps that later line, and the 401 arrives anyway.
    # `probe_env_reference` is what reports it, so the sentence a person needs
    # comes from `theurian doctor` rather than from here.
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

    Nothing here is allowed to fail the rotation, and the reason is the ordering
    the caller already committed to: the token has been replaced by the time
    this runs. Markers that do not delimit one block leave the file untouched;
    an OS-level refusal -- a read-only ``HOME``, a full disk, a file another
    account owns -- leaves it in whatever state the write reached. Both put the
    repair in ``nextSteps`` rather than raising, because the alternatives are
    worse in both directions: refusing to rotate leaves an exposed credential in
    place over a comment marker or a permission bit, and an exception here ends
    the command with a fresh token on disk, a daemon never restarted, and a
    traceback where the remedy should be.

    ``newline=""`` on both sides and the creation mode on the ``open``, for the
    reasons :func:`~theurian.application.setup_steps.apply_env_reference` states:
    this is the second writer of the same file and the two must not differ.
    """
    env_path = data_dir / "env"
    try:
        existing = env_path.read_text(encoding="utf-8", newline="") if env_path.is_file() else None
        merged = merge_env_file(existing, data_dir)
        with open(
            env_path,
            "w",
            encoding="utf-8",
            newline="",
            opener=lambda file, flags: os.open(file, flags, 0o600),
        ) as handle:
            handle.write(merged)
        # Re-asserted, for the same two reasons the setup step re-asserts it:
        # the creation mode is ANDed with the umask, and a file an earlier
        # version created keeps whatever mode it was given.
        env_path.chmod(0o600)
    except MalformedEnvBlockError as exc:
        return [f"{env_path} was left untouched: {exc}"]
    except OSError as exc:
        # The type and the path, never the message: an OSError carries whatever
        # the OS put in it, and this line is printed beside a rotation somebody
        # may well paste into a bug report.
        return [
            f"{env_path} could not be updated ({type(exc).__name__}): it may still name an "
            f"older block, hold part of one, be empty -- the open that truncates it comes "
            f"before the write that failed -- or be readable by other accounts. The new "
            f"token is already in place; repair that file, then run `theurian setup` to "
            f"rewrite the block."
        ]

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
