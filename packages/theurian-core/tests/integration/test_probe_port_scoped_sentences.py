"""A silent port is not an absent daemon (#93).

``context.health()`` asks one address -- ``127.0.0.1:<port>`` -- and answers
``None`` when nothing there says it is a healthy Theurian. Three steps turned
that into "No daemon is running", which is a claim about the whole machine made
from one probe of one port.

It is wrong in the state operators actually hit: a daemon serving this data
directory on *another* port. ``daemon.lock`` is held, ``theurian daemon start``
refuses as a duplicate, and `doctor` says nothing is running -- so the reader is
sent to start a second one, and the two surfaces disagree about the same
machine with no way to tell which is right.

The correction is not a nicer wording: each sentence now names the address that
was probed, and ``single-instance`` says outright that a daemon on another port
is outside what this check can see. That last one matters because
``single-instance``'s job *is* the duplicate-daemon question, and it was the
step reporting the answer with the widest silence.

The port used here is 7420 rather than the 7419 default, so a sentence that
hardcodes the number fails instead of agreeing with the fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import unchecked_migrations

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupService
from theurian.application.setup_steps import (
    probe_daemon_running,
    probe_mcp_health,
    probe_single_instance,
)
from theurian.domain.setup import StepId, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

pytestmark = pytest.mark.integration

#: Not the default. A hardcoded 7419 in any sentence below fails here.
PORT = 7420

#: The claim this cluster exists to delete, in the form any rewording would
#: still take. Matched case-insensitively so "The daemon is running" is caught
#: as well as the original "No daemon is running".
_UNSCOPED_CLAIM = "daemon is running"


def _context(tmp_path: Path, **overrides: object) -> SetupContext:
    """A machine whose port is silent, with a service manager unless told otherwise."""
    data_dir = tmp_path / "home" / ".theurian"
    data_dir.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, object] = {
        "home": tmp_path / "home",
        "data_dir": data_dir,
        "port": PORT,
        "project_root": None,
        "connection": ConnectionSpec(port=PORT),
        "mcp_config": FakeMcpConfig(),
        "secrets": FileSecretStore(data_dir),
        "health": lambda: None,
        "service": FakeService(),
        "executable": "",
        "check_migrations": unchecked_migrations,
    }
    return SetupContext(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_a_silent_port_with_no_service_manager_names_the_address_it_probed(
    tmp_path: Path,
) -> None:
    """``daemon-running``'s not-applicable arm, where nothing else can be said.

    This arm is reached only when the platform has no service manager, so there
    is no service to ask and the port really is the whole of the evidence. The
    sentence therefore has to be about the port.
    """
    step = probe_daemon_running(_context(tmp_path, service=None))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == (
        f"Nothing is answering on 127.0.0.1:{PORT}, and this platform has no service manager."
    )
    assert step.detail == "Start it with `theurian daemon start --foreground`."


def test_single_instance_says_a_daemon_on_another_port_is_outside_what_it_checked(
    tmp_path: Path,
) -> None:
    """The one that misleads hardest, because duplicates are its whole subject.

    "No daemon is running, so there is nothing to be duplicated" is the exact
    conclusion the evidence does not support: a second daemon on another port
    holds this data directory's lock, and this check never looked. Saying so is
    what stops a reader from treating a silent port as proof of single
    instance.
    """
    step = probe_single_instance(_context(tmp_path))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == (
        f"Nothing is answering on 127.0.0.1:{PORT}, so single-instance cannot be "
        f"assessed from here. A daemon serving this data directory on another port "
        f"would not be seen by this check."
    )


def test_mcp_health_names_the_address_it_could_not_reach(tmp_path: Path) -> None:
    """The third member. Same evidence, same scope, so the same sentence shape."""
    step = probe_mcp_health(_context(tmp_path))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == (
        f"Nothing is answering on 127.0.0.1:{PORT}, so the MCP endpoint cannot be checked."
    )


def test_no_step_in_the_plan_reports_an_absent_daemon_from_one_silent_port(
    tmp_path: Path,
) -> None:
    """The class predicate, over the whole plan rather than the three known members.

    Three steps drew the same wrong conclusion from one call to
    ``context.health()``, and they were written at different times by different
    hands. What keeps the class closed is not three corrected sentences but a
    check over every step in ``STEPS``: nothing a run publishes may say a daemon
    is running -- or is not -- on the strength of one port answering nothing.

    ``service=None`` so the not-applicable arm of ``daemon-running`` is walked
    too; with a service manager present that step takes its ``missing`` arm,
    which already named the port and is not what this is about.
    """
    plan = SetupService(_context(tmp_path, service=None)).plan()

    offenders = [
        step.step_id.value
        for step in plan.steps
        if _UNSCOPED_CLAIM in f"{step.summary} {step.detail}".lower()
    ]

    assert offenders == [], f"these steps report on daemons the probe never looked for: {offenders}"
    # The positive control, without which deleting all three sentences passes:
    # the steps that speak about the silent port have to still speak about it,
    # and to name which port it was.
    spoke = {
        step.step_id
        for step in plan.steps
        if f"127.0.0.1:{PORT}" in f"{step.summary} {step.detail}"
    }
    assert spoke >= {StepId.DAEMON_RUNNING, StepId.SINGLE_INSTANCE, StepId.MCP_HEALTH}
