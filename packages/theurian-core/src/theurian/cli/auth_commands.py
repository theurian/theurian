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
from typing import Annotated, Final

import typer

from theurian.cli.setup_commands import _executable
from theurian.daemon.instance import DEFAULT_PORT, probe_health
from theurian.domain.errors import SecurityError
from theurian.domain.ports.daemon_manager import ServiceState
from theurian.infrastructure.secrets.file_store import (
    TOKEN_KEY,
    FileSecretStore,
    default_data_dir,
)
from theurian.infrastructure.services import detect_manager
from theurian.security.env_file import MalformedEnvBlockError, merge_env_file
from theurian.security.no_follow import (
    is_a_symbolic_link_refusal,
    write_text_without_following_a_link,
)
from theurian.security.regular_file import (
    IrregularArtefactError,
    read_text_from_a_regular_file,
)
from theurian.security.tokens import TOKEN_ENV_VAR, describe, generate_token

#: The mode `<data_dir>/env` is created with and re-asserted at, matching
#: `setup_steps._SECRET_FILE_MODE`: this command and `theurian setup` write the
#: same file, and a mode that differed between them would depend on which ran
#: last.
_ENV_FILE_MODE: Final = 0o600

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
    from theurian.cli.commands import _emit, _fail  # noqa: PLC0415 - avoids a circular import

    data_dir = default_data_dir()
    store = FileSecretStore(data_dir)

    token = generate_token()
    try:
        asyncio.run(store.set(TOKEN_KEY, token))
    except SecurityError as exc:
        # The one command that reaches `FileSecretStore.set` with no handler
        # above it (#371). `setup` runs it through `SetupService._apply`, whose
        # `except Exception` records a FAILED step, and `daemon start
        # --foreground` has an `except TheurianError` that publishes `exc.remedy`
        # -- so this is where the refusal would otherwise have become a Rich
        # traceback with an empty machine channel under `--json` (CP-2).
        #
        # **`SecurityError` and not the link class it named until #586.** The
        # store refuses a plant at the secret's path with a *family* of errors --
        # a symbolic link, and now a named pipe, socket, device or directory --
        # and naming one of them made this handler the maintained-list shape the
        # same issue is about elsewhere: measured 2026-09-06, a 0600 named pipe at
        # `<data_dir>/auth/mcp-token` escaped here as
        # `SecretPathIsNotAFileError` at exit 1 with **both channels empty**,
        # while the link at the same path published its document. Every member
        # carries its own message and its own cure, so one arm serves them all.
        #
        # Exit 1 rather than a state code: nothing about the caller's knowledge
        # state is wrong, and this command has no `EXIT_STATE_ERROR` vocabulary of
        # its own -- the data directory is the thing to repair.
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=1)
        return
    except OSError as exc:
        # **#572, closed here.** `SecurityError` covers every fault the store
        # *classifies*; it does not cover the ones it merely propagates. A token
        # file the owner cannot rewrite -- another account's, a read-only mount,
        # ENOSPC, or a macOS immutable flag (`chflags uchg`, which is what drove
        # this arm RED) -- reaches the `os.open` inside `set` as a plain
        # `OSError` and escaped `--json` as a Rich traceback with empty stdout.
        # That is the CP-2 shape on a command whose contract is a parseable
        # `{error, remedy}` document.
        #
        # The *cause* and not `str(exc)`: an `OSError`'s `str` appends the
        # filename, which is the operator's absolute path, and the path already
        # travels in the remedy where a reader acts on it (GHSA-923w-f36f-jcfq). The
        # remedy names the artefact, the two commands that inspect it, and the
        # fact that the old token is still in place -- because it is: `set`
        # failed, so nothing was rotated and the caller's clients still work.
        token_file = data_dir / "auth" / TOKEN_KEY
        _fail(
            f"The local access token could not be written ({type(exc).__name__}).",
            remedy=(
                f"Theurian could not replace {token_file}, so the old token is still in "
                f"place and nothing was rotated. Check that the file and "
                f"{data_dir / 'auth'} are yours to write and not read-only: "
                f"`ls -ld {token_file} {data_dir / 'auth'}` shows the owner and the mode, "
                f"and on macOS `ls -ldO` also shows an immutable flag that `chflags "
                f"nouchg {token_file}` clears. Then run `theurian auth rotate` again."
            ),
            as_json=as_json,
            code=1,
        )
        return

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

    ``newline=""`` on both sides and the creation mode on the write, for the
    reasons :func:`~theurian.application.setup_steps.apply_env_reference` states:
    this is the second writer of the same file and the two must not differ. Both
    go through ``no_follow``'s writer and the bounded reader since #586 -- the
    byte-identical pair of ``open`` calls they had before carried neither
    ``O_NOFOLLOW`` nor ``O_NONBLOCK``, so a symbolic link at ``<data_dir>/env``
    was followed out of the data directory and its victim overwritten at exit 0,
    and a named pipe there held this command with both channels empty until it
    was killed.
    """
    env_path = data_dir / "env"
    try:
        # `exists()` rather than `is_file()`: the latter answers `False` for a
        # named pipe, which would read as "no file yet" and send a planted
        # artefact into the write below rather than to the refusal.
        existing = (
            read_text_from_a_regular_file(env_path, newline="") if env_path.exists() else None
        )
        merged = merge_env_file(existing, data_dir)
        write_text_without_following_a_link(env_path, merged, mode=_ENV_FILE_MODE)
        # Re-asserted, for the same two reasons the setup step re-asserts it:
        # the creation mode is ANDed with the umask, and a file an earlier
        # version created keeps whatever mode it was given.
        env_path.chmod(_ENV_FILE_MODE)
    except MalformedEnvBlockError as exc:
        return [f"{env_path} was left untouched: {exc}"]
    except IrregularArtefactError as exc:
        # Ahead of the `OSError` arm, whose sentence is about a file that was
        # opened and half-written. Nothing was opened here, and what the reader
        # needs is what is at the path -- the generic text would send them to
        # repair a block that is not there (#586).
        return [
            f"{env_path} was left untouched: it is {exc.shape}, not the env file Theurian "
            f"writes. `ls -l {env_path}` shows what is at the path now. Remove it and run "
            f"`theurian setup` to rewrite the block; the new token is already in place."
        ]
    except OSError as exc:
        if is_a_symbolic_link_refusal(exc):
            # The link is refused rather than followed, so nothing was written
            # through it -- and saying so is the point: an operator who did not
            # create it needs to know something with write access to their data
            # directory did.
            return [
                f"{env_path} was left untouched: it is a symbolic link, and Theurian will "
                f"not write the block through one -- the lines would land wherever it "
                f"points. Remove it and run `theurian setup`; something with write access "
                f"to {data_dir} put it there, so check that directory's permissions. The "
                f"new token is already in place."
            ]
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
