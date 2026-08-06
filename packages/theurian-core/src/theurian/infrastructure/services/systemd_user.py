"""Linux systemd **user** unit adapter for the daemon service (§24.2, FR-L3).

A user unit under ``~/.config/systemd/user``, driven with ``systemctl --user``.
Never a system unit: those live in ``/etc/systemd/system``, need root to install,
and would run Theurian as a service account with no business reading a
developer's home directory.

The awkward part of this platform is that a user manager exists only while the
user has a session. Without lingering enabled, closing the last session stops
the daemon — so ``install`` asks for lingering and treats a refusal as a warning
rather than a failure, because a daemon that runs whenever the user is logged in
is still a working Theurian.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from theurian.domain.ports.daemon_manager import ServiceState, ServiceStatus
from theurian.domain.setup import DifferingFields, SetupError
from theurian.infrastructure.services.runner import CommandRunner, SubprocessRunner

UNIT_NAME: Final = "theurian.service"

_UNIT_DIRECTORY: Final = ".config/systemd/user"

#: Every directive :meth:`SystemdUserManager.render` writes. A literal, and the
#: only vocabulary ``differing_keys`` may publish.
#:
#: **Stated rather than derived, because deriving it was a disclosure.** It used
#: to be computed by re-parsing ``render()``'s own output, on the reasoning that
#: a name Theurian writes cannot be a value it read. That holds for a plist,
#: where ``plistlib`` escapes what it is given, and for the MCP entry, which is
#: a dict literal. It did not hold here: ``render`` interpolates
#: ``data_directory`` and ``executable`` into text that is then re-parsed *as
#: structure*, so a newline in either produced a directive of the caller's
#: choosing -- and that directive counted as Theurian-authored, so a name
#: present only in the *installed* unit was published in the differing-fields
#: list. Measured through the same path ``doctor --report`` takes.
#:
#: A constant cannot be extended by an input. :func:`_reject_structural_input`
#: closes the same root cause on the write side, where the injected directive
#: was written into the user's unit file three times over.
#:
#: **This layer is deliberately unobservable, and that was measured rather than
#: assumed.** With the rejection in place, no input reaches ``render``'s
#: interpolation points carrying a newline, so deriving the vocabulary from
#: ``render``'s output and reading it from here produce identical answers:
#: reverting to the derived form alone leaves the suite green, while removing the
#: rejection alone turns three tests red. So this is not pinned by a test and
#: cannot be -- it is here for the interpolation point somebody adds later
#: without going through the rejection, which is the shape the defect had.
#: :meth:`DifferingFields.over`'s own behaviour against this constant *is*
#: pinned; what is unobservable is only the wiring.
_RENDERED_DIRECTIVES: Final = frozenset(
    {
        "After",
        "Description",
        "Documentation",
        "Environment",
        "ExecStart",
        "NoNewPrivileges",
        "PrivateTmp",
        "ProtectHome",
        "ProtectSystem",
        "ReadWritePaths",
        "Restart",
        "RestartSec",
        "Type",
        "WantedBy",
        "WorkingDirectory",
    }
)


def _reject_structural_input(**values: str) -> None:
    """Refuse a value that would become unit-file structure rather than data.

    ``render`` is an f-string over a line-oriented format, so a value carrying a
    newline does not land in the field it was meant for: it starts a directive,
    and ``install`` writes that directive into the user's unit file once per
    interpolation point -- three times, for the data directory. The plist adapter
    has no equivalent because ``plistlib`` escapes what it is handed.

    Refused rather than escaped: systemd has no escape that makes a newline part
    of a value here, and a data directory containing one is a mistake worth
    naming rather than quietly rewriting.
    """
    for field, value in values.items():
        if "\n" in value or "\r" in value:
            msg = (
                f"{field} contains a line break, which would become a directive in "
                f"the systemd unit rather than part of the value. Set "
                f"THEURIAN_DATA_DIR to a path without one, or pass --data-dir."
            )
            raise SetupError(msg)


def _logical_lines(unit: str) -> Iterator[str]:
    """A unit's directive and section lines, with continuations joined.

    A line ending in a backslash continues onto the next, and the next line is
    therefore the *value* of the directive above it. Splitting it on its own
    ``=`` invents a directive name out of somebody's value:

        ExecStart=/usr/bin/theurian daemon start \\
            --header "Authorization: Bearer <token>"

    parsed line by line, yielded that header as a directive name. What stops
    that name being *published* is :meth:`DifferingFields.over`, which keeps only
    names Theurian's own renderer produces; this function stops it being derived
    at all, so the count beside the names counts real directives.

    **A comment does not continue.** Dropping comments before joining rather
    than after is a decision, not an ordering accident: joining first lets a
    comment ending in a backslash swallow the directive on the next line, and a
    swallowed directive vanishes from the comparison -- ``ExecStart`` differing
    would be reported as no difference at all. Whether real systemd continues a
    comment has varied across its releases, and this is not a format Theurian
    owns; the choice that cannot hide a difference is the one taken.
    """
    buffered = ""
    for raw in unit.splitlines():
        stripped = raw.strip()
        if not buffered and (not stripped or stripped.startswith(("#", ";"))):
            continue
        line = f"{buffered} {stripped}" if buffered else stripped
        if line.endswith("\\"):
            buffered = line[:-1].rstrip()
            continue
        buffered = ""
        yield line
    if buffered:  # a file whose last line dangles a continuation
        yield buffered


def _directives(unit: str) -> dict[tuple[str, str], tuple[str, ...]]:
    """A unit's ``Name=Value`` lines, keyed by section and name.

    A tuple of values rather than one, because systemd lets a directive repeat
    -- ``Environment=`` most of all -- and collapsing repeats would report two
    units as equal when one of them sets a variable the other does not.

    **Keyed by section as well as name**, because systemd's sections are not
    decoration: ``Environment=`` under ``[Unit]`` does nothing, and comparing on
    the bare name reported a unit with it misplaced there as identical to one
    with it in ``[Service]``. That is a difference the operator has to see, and
    it was being answered with "no directive differs".
    """
    found: dict[tuple[str, str], list[str]] = {}
    section = ""
    for line in _logical_lines(unit):
        if line.startswith("["):
            section = line
            continue
        name, separator, value = line.partition("=")
        if separator:
            found.setdefault((section, name.strip()), []).append(value.strip())
    return {key: tuple(values) for key, values in found.items()}


@final
class SystemdUserManager:
    """Installs and controls the daemon as a systemd user unit."""

    platform_id = "systemd-user"

    def __init__(
        self,
        *,
        executable: str,
        home: Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._executable = executable
        self._home = home or Path.home()
        self._runner = runner or SubprocessRunner()

    @property
    def unit_path(self) -> Path:
        return self._home / _UNIT_DIRECTORY / UNIT_NAME

    def is_supported(self) -> bool:
        """Whether this machine actually has a systemd user manager.

        The binary existing is not enough: a container or a WSL install often
        ships ``systemctl`` with no user manager behind it, and every command
        then fails with "Failed to connect to bus". Asking is cheaper than
        installing a unit that can never start.
        """
        if self._runner.which("systemctl") is None:
            return False
        return self._runner.run(["systemctl", "--user", "is-system-running"]).exit_code != 127  # noqa: PLR2004 - "not found"

    # -- Definition -------------------------------------------------------

    def render(self, *, port: int, data_directory: str) -> str:
        """The unit file.

        ``Type=simple`` with ``--foreground``: systemd supervises the process,
        so a program that daemonises itself would be reported as having exited
        immediately.

        Every directive written here is named in :data:`_RENDERED_DIRECTIVES`,
        which ``differing_keys`` publishes from. Adding one here without adding
        it there makes it report as an unnamed count rather than by name --
        annoying, and the safe direction; the reverse is the one that discloses,
        and a constant cannot be extended by an input.

        Raises:
            SetupError: If an interpolated value carries a line break, which
                would become a directive rather than part of the value.
        """
        _reject_structural_input(data_directory=data_directory, executable=self._executable)
        return f"""\
[Unit]
Description=Theurian knowledge daemon
Documentation=https://github.com/theurian/theurian
After=network.target

[Service]
Type=simple
ExecStart={self._executable} daemon start --foreground --port {port}
Environment=THEURIAN_DATA_DIR={data_directory}
WorkingDirectory={data_directory}
Restart=on-failure
RestartSec=5

# The daemon needs the user's home directory and nothing else. These are cheap
# and they bound what a compromised parser can reach (SEC-9).
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-write
ReadWritePaths={data_directory}

[Install]
WantedBy=default.target
"""

    def _installed_unit(self) -> str | None:
        """The installed unit's text, or ``None`` if it cannot be read as text.

        The plist adapter has had this since a directory where the plist should
        be escaped its parser's ``except`` as an ``OSError``; the unit adapter
        had no equivalent, so a unit holding invalid UTF-8 raised
        ``UnicodeDecodeError`` out of both comparison methods and the report
        degraded to "could not check daemon-service" -- true, and two steps from
        "your unit file is not text". ``exists()`` is a race, not a guarantee,
        which is why ``OSError`` is caught here rather than assumed away.
        """
        try:
            return self.unit_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None

    def differs_from_installed(self, *, port: int, data_directory: str) -> str:
        """Empty when the installed unit already matches, else a diff.

        Reads the unit file and nothing else. **Drop-ins are not consulted**: a
        unit overridden by ``~/.config/systemd/user/theurian.service.d/*.conf``
        reports as satisfied while systemd runs something else entirely, which
        is the supported way to change a unit and so not an exotic case. Not
        fixed here because it is a change to what this method *means* -- the
        effective definition rather than the file Theurian wrote -- and that
        wants its own decision about what ``install`` should then do with an
        override it did not create (SEC-18). Filed as
        https://github.com/theurian/theurian/issues/52.
        """
        if not self.unit_path.exists():
            return ""

        wanted = self.render(port=port, data_directory=data_directory)
        installed = self._installed_unit()
        if installed is None:
            return f"{self.unit_path} is not a readable unit file."
        if installed == wanted:
            return ""

        diff = difflib.unified_diff(
            installed.splitlines(),
            wanted.splitlines(),
            fromfile=f"{self.unit_path} (installed)",
            tofile="Theurian would install",
            lineterm="",
        )
        return "\n".join(diff)

    def differing_keys(self, *, port: int, data_directory: str) -> DifferingFields:
        """Which unit directives differ, without a word about their values.

        The diff :meth:`differs_from_installed` produces is whole lines of a
        file Theurian did not write, and those lines carry
        ``Environment=THEURIAN_MCP_TOKEN=...`` and paths outside the roots a
        shared report substitutes.

        Only the directives in :data:`_RENDERED_DIRECTIVES` are named; the rest
        are counted. A unit file is somebody else's text in a format Theurian
        does not own, so a name read out of it is data -- see
        :class:`DifferingFields`, which is where that argument lives, and the
        constant, which is where the argument stopped holding when the
        vocabulary was derived from ``render``'s output instead of stated.

        Only directives are compared, so a unit differing solely in its comments
        names nothing and counts nothing -- the caller's sentence then says the
        difference is withheld rather than naming fields, which stays true either
        way, and the full diff is one ``theurian doctor`` away.
        """
        if not self.unit_path.exists():
            return DifferingFields()

        text = self._installed_unit()
        if text is None:
            return DifferingFields(unreadable="is not a readable unit file")

        wanted = _directives(self.render(port=port, data_directory=data_directory))
        installed = _directives(text)
        differing = {
            key for key in set(installed) | set(wanted) if installed.get(key) != wanted.get(key)
        }
        # Compared by (section, name) and published by name alone: the section a
        # directive sits in decides whether it does anything, but naming it says
        # nothing more than the directive already does.
        #
        # The cost is a bare name that can point at the wrong line: an
        # `Environment=` misplaced under `[Unit]`, with `[Service]` matching
        # exactly, publishes `Fields that differ: Environment` and sends the
        # reader to a `[Service] Environment` that is identical. Accepted -- the
        # conflict is never hidden, since the byte difference is what raised it,
        # and plain `theurian doctor` shows the unified diff with the sections
        # in it. Publishing the section instead would name a part of the file
        # this method has no vocabulary for.
        #
        # `authored` is the constant, never `wanted`. Deriving it from what
        # `render` just produced let an injected newline add a name to the
        # vocabulary, and that name was then published off the *installed* unit.
        return DifferingFields.over({name for _, name in differing}, authored=_RENDERED_DIRECTIVES)

    # -- Lifecycle --------------------------------------------------------

    async def status(self) -> ServiceStatus:
        if not self.unit_path.exists():
            return ServiceStatus(
                state=ServiceState.NOT_INSTALLED,
                service_identifier=UNIT_NAME,
                detail=f"No unit at {self.unit_path}.",
            )

        active = self._runner.run(["systemctl", "--user", "is-active", UNIT_NAME])
        if active.output.strip() != "active":
            return ServiceStatus(
                state=ServiceState.INSTALLED_STOPPED,
                service_identifier=UNIT_NAME,
                detail=f"systemctl reports {active.output.strip() or 'unknown'}.",
            )

        shown = self._runner.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value", UNIT_NAME]
        )
        pid = int(shown.stdout.strip()) if shown.stdout.strip().isdigit() else None
        return ServiceStatus(
            state=ServiceState.RUNNING,
            pid=pid or None,
            listening=True,
            service_identifier=UNIT_NAME,
        )

    async def install(self, *, port: int, data_directory: str) -> None:
        """Write the unit, reload, enable, and start. Idempotent.

        Only ever reached from an explicit user action (FR-L3).
        """
        wanted = self.render(port=port, data_directory=data_directory)
        self.unit_path.parent.mkdir(parents=True, exist_ok=True)

        if self.unit_path.exists() and self.unit_path.read_text(encoding="utf-8") == wanted:
            await self.start()
            return

        if self.unit_path.exists():
            self.back_up()

        temporary = self.unit_path.with_suffix(".service.tmp")
        temporary.write_text(wanted, encoding="utf-8")
        temporary.replace(self.unit_path)

        self._runner.run(["systemctl", "--user", "daemon-reload"])
        self._runner.run(["systemctl", "--user", "enable", UNIT_NAME])
        await self.start()

    def enable_lingering(self) -> str:
        """Ask for the daemon to survive logout. Returns a warning, or empty.

        Failure is not fatal. ``loginctl enable-linger`` is often refused by
        policy, and a Theurian that runs whenever the user is logged in is still
        a working Theurian -- so this degrades rather than aborts (§6.1).
        """
        if self._runner.which("loginctl") is None:
            return "loginctl is not available, so the daemon will stop when you log out."

        result = self._runner.run(["loginctl", "enable-linger"])
        if result.ok:
            return ""
        return (
            "Could not enable lingering, so the daemon will stop when you log out. "
            "Ask an administrator for `loginctl enable-linger $USER` to keep it running."
        )

    def back_up(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = self.unit_path.with_name(f"{UNIT_NAME}.{stamp}.backup")
        backup.write_text(self.unit_path.read_text(encoding="utf-8"), encoding="utf-8")
        return backup

    async def start(self) -> None:
        self._runner.run(["systemctl", "--user", "start", UNIT_NAME])

    async def stop(self) -> None:
        self._runner.run(["systemctl", "--user", "stop", UNIT_NAME])

    async def restart(self) -> None:
        self._runner.run(["systemctl", "--user", "restart", UNIT_NAME])

    async def uninstall(self) -> None:
        """Deregister the service and remove its unit. Data is untouched (FR-L5)."""
        self._runner.run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
        self.unit_path.unlink(missing_ok=True)
        self._runner.run(["systemctl", "--user", "daemon-reload"])
