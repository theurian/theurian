"""What `doctor --report` may publish about things Theurian did not write (O-3, SEC-6).

Every assertion here is on a **value**, never on a shape. That is the point of
the module: the redaction that shipped before it substituted the paths the local
`SetupContext` holds, and a test asserting the home directory is gone passed
happily beside a live bearer token -- a string read out of somebody else's file
has no anchor to substitute and left the payload verbatim. A test that checks
the anchors passes equally before and after this module's subject exists; a test
that greps the payload for the literal secret does not.

So each case seeds a distinctive sentinel into a source Theurian reads but does
not own -- Claude Code's config, a LaunchAgent plist, another daemon's `/health`
reply, the project registry, an exception -- and asserts it is absent from the
published payload and still present in the operator's own.

The payload is produced the way `doctor --report` produces it: the real state
machine, then the real redaction, over a context built with `for_publication`
set. Adapters are real wherever they read a file, because the file is what is
being reported on. Only the two seams that would touch the developer's own
machine -- `launchctl` and the `claude` CLI -- are stubbed.
"""

from __future__ import annotations

import json
import plistlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import STEPS, Step
from theurian.cli.setup_commands import _redacted
from theurian.domain.setup import SetupStep, StepId
from theurian.infrastructure.claude.mcp_config import ClaudeCodeMcpConfig, ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.infrastructure.services.launchagent import LABEL, LaunchAgentManager
from theurian.infrastructure.services.runner import CommandResult

pytestmark = pytest.mark.integration

PORT = 7419

#: Distinctive enough that no test passes on a coincidental substring, and
#: shaped like the thing it stands for.
LITERAL_TOKEN = "SentinelBearerTokenAAAAAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105
PLIST_TOKEN = "SentinelPlistEnvTokenBBBBBBBBBBBBBBBBBBBBBBB"  # noqa: S105
FOREIGN_DATA_DIR = "/opt/somebody-elses/private-workspace"
FOREIGN_PROJECT_ID = "acme-unreleased-merger-tooling"
EXCEPTION_MESSAGE = "postgres://sentinel-user:SentinelDbPasswordEEE@db.internal:5432"


class StubRunner:
    """Every external tool is present and every command succeeds.

    `launchctl print` answering with no `pid =` line leaves the service
    INSTALLED_STOPPED, which is what makes `probe_daemon_service` go on to
    compare definitions -- the branch this module is about.
    """

    def run(
        self,
        args: Sequence[str],  # noqa: ARG002 - part of the CommandRunner protocol
        *,
        timeout: float = 20.0,  # noqa: ARG002 - likewise
        env: Mapping[str, str] | None = None,  # noqa: ARG002 - likewise
    ) -> CommandResult:
        return CommandResult(exit_code=0)

    def which(self, executable: str) -> str | None:
        return f"/usr/bin/{executable}"


def _context(tmp_path: Path, *, for_publication: bool = True, **overrides: Any) -> SetupContext:
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True, exist_ok=True)
    data_dir = home / ".theurian"
    data_dir.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "bin" / "theurian"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()

    defaults: dict[str, Any] = {
        "home": home,
        "data_dir": data_dir,
        "port": PORT,
        "project_root": None,
        "connection": ConnectionSpec(port=PORT),
        "mcp_config": FakeMcpConfig(available=False),
        "secrets": FileSecretStore(data_dir),
        "health": lambda: None,
        "service": FakeService(),
        "executable": str(executable),
        "for_publication": for_publication,
    }
    return SetupContext(**{**defaults, **overrides})


def _published(context: SetupContext, steps: Sequence[Step] = STEPS) -> str:
    """The whole `doctor --report --json` payload, as one string to search."""
    report = SetupService(context, steps).run(SetupRequest(dry_run=True))
    return json.dumps(_redacted(report.to_json(), context))


def _on_the_terminal(context: SetupContext, steps: Sequence[Step] = STEPS) -> str:
    """The same payload as plain `theurian doctor` prints, for the same machine."""
    local = replace(context, for_publication=False)
    return json.dumps(SetupService(local, steps).run(SetupRequest(dry_run=True)).to_json())


def _detail(payload: str, step_id: StepId) -> str:
    step = next(s for s in json.loads(payload)["steps"] if s["id"] == step_id.value)
    return str(step["detail"])


# -- The MCP entry: a literal credential in someone else's config file --------


@pytest.fixture
def with_a_literal_token(tmp_path: Path) -> SetupContext:
    """The state that makes this step conflict, and gives someone a reason to run
    `doctor --report`: an entry with the token pasted in rather than referenced.
    Theurian never writes that (SEC-5). It is what it finds."""
    context = _context(tmp_path)
    (context.home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "theurian": {
                        "type": "http",
                        "url": f"http://127.0.0.1:{PORT}/mcp",
                        "headers": {"Authorization": f"Bearer {LITERAL_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return replace(context, mcp_config=ClaudeCodeMcpConfig(home=context.home, runner=StubRunner()))


def test_a_bearer_token_in_the_installed_entry_never_reaches_a_report(
    with_a_literal_token: SetupContext,
) -> None:
    """SEC-6. The step renders the installed entry so the user can decide whether
    the run proceeds around it, and the installed entry is not Theurian's."""
    assert LITERAL_TOKEN not in _published(with_a_literal_token)


def test_the_report_still_names_the_field_that_differs(
    with_a_literal_token: SetupContext,
) -> None:
    """Withholding that removed the diagnosis would defeat the purpose. A field
    name is schema, and it says which line to open on the terminal."""
    detail = _detail(_published(with_a_literal_token), StepId.MCP_CONNECTION)

    assert "headers" in detail
    assert "--report" in detail, "the reader has to be told where the values are"


def test_the_operators_own_terminal_still_shows_the_entry(
    with_a_literal_token: SetupContext,
) -> None:
    """This is the assertion that fails if the difference is simply deleted. The
    person who has to fix the entry is looking at their own screen."""
    assert LITERAL_TOKEN in _on_the_terminal(with_a_literal_token)


# -- The service definition: a credential in a plist somebody hand edited ------


@pytest.fixture
def with_a_token_in_the_plist(tmp_path: Path) -> SetupContext:
    context = _context(tmp_path)
    manager = LaunchAgentManager(
        executable=context.executable, home=context.home, runner=StubRunner(), uid=501
    )
    manager.plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": [context.executable, "daemon", "start"],
                "EnvironmentVariables": {
                    "THEURIAN_DATA_DIR": str(context.data_dir),
                    "THEURIAN_MCP_TOKEN": PLIST_TOKEN,
                },
            },
            sort_keys=True,
        )
    )
    return replace(context, service=manager)


def test_a_token_in_the_installed_plist_never_reaches_a_report(
    with_a_token_in_the_plist: SetupContext,
) -> None:
    """`EnvironmentVariables` is a dictionary a person may put anything into, and
    the conflict report printed it whole."""
    assert PLIST_TOKEN not in _published(with_a_token_in_the_plist)


def test_the_report_names_the_plist_key_that_differs(
    with_a_token_in_the_plist: SetupContext,
) -> None:
    detail = _detail(_published(with_a_token_in_the_plist), StepId.DAEMON_SERVICE)

    assert "EnvironmentVariables" in detail
    assert "THEURIAN_MCP_TOKEN" not in detail, "a variable's name is content too"


def test_the_operators_own_terminal_still_shows_the_plist_difference(
    with_a_token_in_the_plist: SetupContext,
) -> None:
    assert PLIST_TOKEN in _on_the_terminal(with_a_token_in_the_plist)


def test_a_service_difference_with_no_nameable_field_is_withheld_whole(
    tmp_path: Path,
) -> None:
    """An adapter can differ with no *field* differing: a systemd unit whose
    comments were edited, a plist too damaged to parse. The sentence must not
    claim to name fields it has none of, and must still withhold the values."""
    context = _context(
        tmp_path,
        service=FakeService(installed=True, difference=f"corrupt: {LITERAL_TOKEN}", differing=()),
    )
    detail = _detail(_published(context), StepId.DAEMON_SERVICE)

    assert LITERAL_TOKEN not in detail
    assert "Fields that differ" not in detail
    assert "withheld" in detail


# -- Single instance: a path off the wire from a process this one does not own -


@pytest.fixture
def with_a_foreign_daemon(tmp_path: Path) -> SetupContext:
    return _context(tmp_path, health=lambda: {"status": "ok", "dataDir": FOREIGN_DATA_DIR})


def test_another_daemons_data_directory_never_reaches_a_report(
    with_a_foreign_daemon: SetupContext,
) -> None:
    """It came off the wire from a process this one does not own, so it is a path
    the local context never held and no anchor can substitute."""
    assert FOREIGN_DATA_DIR not in _published(with_a_foreign_daemon)


def test_the_report_still_says_the_port_is_held_by_a_different_directory(
    with_a_foreign_daemon: SetupContext,
) -> None:
    detail = _detail(_published(with_a_foreign_daemon), StepId.SINGLE_INSTANCE)

    assert "<another data directory>" in detail
    assert str(with_a_foreign_daemon.port) in detail


def test_the_operators_own_terminal_still_names_the_other_directory(
    with_a_foreign_daemon: SetupContext,
) -> None:
    """The remedy is to go and stop that daemon, which needs its directory."""
    assert FOREIGN_DATA_DIR in _on_the_terminal(with_a_foreign_daemon)


# -- The registry: the names of other repositories on this machine -------------


@pytest.fixture
def with_an_unreadable_registration(tmp_path: Path) -> SetupContext:
    """A project id is derived from a repository's directory name, so an id in
    this file names somebody's other work -- and a bare name is not a path."""
    root = tmp_path / "this-repository"
    (root / ".git").mkdir(parents=True)
    context = _context(tmp_path, project_root=root)
    (context.data_dir / "projects.json").write_text(
        json.dumps({FOREIGN_PROJECT_ID: {"noRootPath": True}}), encoding="utf-8"
    )
    return context


def test_other_repositories_ids_never_reach_a_report(
    with_an_unreadable_registration: SetupContext,
) -> None:
    assert FOREIGN_PROJECT_ID not in _published(with_an_unreadable_registration)


def test_the_report_says_how_many_entries_cannot_be_read(
    with_an_unreadable_registration: SetupContext,
) -> None:
    """A count is what a reader of a public issue acts on: one hand edit, or a
    file that is gone."""
    detail = _detail(_published(with_an_unreadable_registration), StepId.PROJECT_REGISTERED)

    assert "1 registry entry cannot be read" in detail


def test_the_operators_own_terminal_still_names_the_id_to_unregister(
    with_an_unreadable_registration: SetupContext,
) -> None:
    """The id is the argument `theurian project unregister` takes. A remedy
    naming an id no surface prints is not a remedy, so it stays where it is
    typed rather than being deleted."""
    assert FOREIGN_PROJECT_ID in _on_the_terminal(with_an_unreadable_registration)


def test_a_registry_that_cannot_be_parsed_at_all_is_withheld_whole(tmp_path: Path) -> None:
    """Counting needs the file too. When it cannot be had, the sentence says so
    rather than inventing a number."""
    root = tmp_path / "this-repository"
    (root / ".git").mkdir(parents=True)
    context = _context(tmp_path, project_root=root)
    (context.data_dir / "projects.json").write_text(f'["{FOREIGN_PROJECT_ID}"]', encoding="utf-8")

    published = _published(context)

    assert FOREIGN_PROJECT_ID not in published
    assert "cannot be read at all" in _detail(published, StepId.PROJECT_REGISTERED)


# -- A probe that raises: whatever the exception happens to carry --------------


def _explode(_: SetupContext) -> SetupStep:
    raise RuntimeError(EXCEPTION_MESSAGE)


def _nothing(_: SetupContext) -> None: ...


_EXPLODING = (Step(StepId.MIGRATIONS_VALID, _explode, _nothing, critical=False),)


def test_an_exception_message_never_reaches_a_report(tmp_path: Path) -> None:
    """Every probe reads something Theurian did not write, so nothing bounds what
    an exception from one carries. Decided: the type is published, the message is
    not."""
    published = _published(_context(tmp_path), _EXPLODING)

    assert EXCEPTION_MESSAGE not in published
    assert "RuntimeError" in _detail(published, StepId.MIGRATIONS_VALID)


def test_the_operators_own_terminal_still_shows_the_exception_message(tmp_path: Path) -> None:
    assert EXCEPTION_MESSAGE in _on_the_terminal(_context(tmp_path), _EXPLODING)
