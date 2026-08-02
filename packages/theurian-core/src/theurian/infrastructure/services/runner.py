"""Running an external command, behind a seam (ADR-0003).

``launchctl`` and ``systemctl`` are the two things a service adapter cannot
avoid touching, and they are exactly what a test cannot invoke: the macOS suite
would register a real LaunchAgent in the developer's account, and the Linux suite
needs a session bus that CI does not have.

So the adapters take a runner. The default one really executes; tests supply one
that records and replies. What is being tested is the *decision* — which command,
with which arguments, and what the exit code means — which is where the bugs are.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, final, runtime_checkable

#: Nothing a service adapter runs should ever take this long. A hung
#: ``launchctl`` must not hang a setup run that a person is waiting on.
DEFAULT_TIMEOUT_SECONDS: Final = 20.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one external command."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Both streams, for matching against tool output.

        ``launchctl`` and ``systemctl`` disagree about which stream carries an
        error, and a check that reads only one of them silently misses half the
        failures.
        """
        return f"{self.stdout}\n{self.stderr}".strip()


@runtime_checkable
class CommandRunner(Protocol):
    """Runs a command and reports what happened."""

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Execute ``args``. Never raises for a non-zero exit; that is data.

        ``env`` **overlays** the current environment rather than replacing it,
        so a caller can redirect one variable without having to reconstruct a
        working PATH.
        """
        ...

    def which(self, executable: str) -> str | None:
        """Absolute path to ``executable``, or ``None`` if it is not on PATH."""
        ...


@final
class SubprocessRunner:
    """The real one."""

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run a command, turning every failure mode into a result.

        A service probe runs inside ``SessionStart``. Raising here would turn a
        missing ``systemctl`` into a traceback in front of someone who just
        opened a terminal, so absence, timeout, and non-zero exit all come back
        as data.
        """
        try:
            completed = subprocess.run(  # noqa: S603 - args are adapter-controlled, never user input
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ, **env} if env else None,
            )
        except FileNotFoundError:
            return CommandResult(exit_code=127, stderr=f"{args[0]}: not found")
        except subprocess.TimeoutExpired:
            return CommandResult(exit_code=124, stderr=f"{args[0]}: timed out after {timeout}s")
        except OSError as exc:  # pragma: no cover - defensive
            return CommandResult(exit_code=126, stderr=f"{args[0]}: {exc}")

        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def which(self, executable: str) -> str | None:
        return shutil.which(executable)
