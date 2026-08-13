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

Nothing here holds the *weld* between the two halves of redaction; that is
`test_redacting_a_run_that_did_not_withhold_is_refused`. What the sweep asserts
about its own reach is bounded by `_OBSERVED_SEEDS`, which says which seeds are
measurements and which are guards on a step that does not exist yet.
"""

from __future__ import annotations

import json
import plistlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import STEPS, Step
from theurian.cli.setup_commands import _redacted
from theurian.domain.setup import (
    DifferingFields,
    SetupError,
    SetupState,
    SetupStep,
    StepId,
    StepStatus,
)
from theurian.infrastructure.claude.mcp_config import ClaudeCodeMcpConfig, ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.infrastructure.services.launchagent import LABEL, LaunchAgentManager
from theurian.infrastructure.services.runner import CommandResult
from theurian.infrastructure.services.systemd_user import SystemdUserManager
from theurian.security.env_file import env_block
from theurian.security.tokens import TOKEN_ENV_VAR

pytestmark = pytest.mark.integration

PORT = 7419

#: Distinctive enough that no test passes on a coincidental substring, and
#: shaped like the thing it stands for.
LITERAL_TOKEN = "SentinelBearerTokenAAAAAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105
PLIST_TOKEN = "SentinelPlistEnvTokenBBBBBBBBBBBBBBBBBBBBBBB"  # noqa: S105
UNIT_TOKEN = "SentinelUnitExecTokenIIIIIIIIIIIIIIIIIIIIIII"  # noqa: S105
UNIT_ENV_TOKEN = "SentinelUnitEnvTokenJJJJJJJJJJJJJJJJJJJJJJJ"  # noqa: S105
FOREIGN_SCRIPT = "/opt/northwind-acquisition/preflight.sh"
FOREIGN_CLIENT = "northwind-acquisition"
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
    # 0755, not `touch()`. `probe_core` requires the executable bit since #49,
    # and without it `core-present` conflicts, which is one of the two verdicts
    # `_blocking_conflicts` reads -- so every payload below would be a report of
    # an ABORTED run rather than of the plan this module is about. Measured: all
    # eighteen steps are still published either way and the withholding still
    # happens, so nothing here goes red; what changes is the object under test.
    # `test_the_payloads_here_describe_a_plan_and_not_an_aborted_run` pins it.
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

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


def test_the_payloads_here_describe_a_plan_and_not_an_aborted_run(tmp_path: Path) -> None:
    """The fixture's mode, pinned -- because nothing else in this module needs it.

    Every seed below is asserted absent from the published payload, and an
    ABORTED run publishes all eighteen steps too, so each of them stays green
    against a `core-present` that conflicts. Reverting `_context` to `touch()`
    was measured as a SURVIVING mutation at 1731 passed for exactly that reason:
    the module would go on testing withholding, but on a report of a run that
    stopped at step two rather than of the plan its docstring describes.

    That is the difference this asserts. It is not a claim that withholding
    breaks when the run aborts -- measured, it does not.
    """
    report = SetupService(_context(tmp_path), STEPS).run(SetupRequest(dry_run=True))

    assert report.state is SetupState.PLAN_BUILT, (
        "the fixture's executable must satisfy `core-present`, or every payload "
        "in this module describes an aborted run"
    )
    core = report.step(StepId.CORE_PRESENT)
    assert core is not None
    assert core.status is StepStatus.SATISFIED


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
    """Withholding that removed the diagnosis would defeat the purpose.

    `headers` is publishable because *Theurian writes it* -- it is a field of
    `ConnectionSpec.as_entry`, not a string read out of the user's file -- and it
    says which line to open on the terminal.
    """
    detail = _detail(_published(with_a_literal_token), StepId.MCP_CONNECTION)

    assert "headers" in detail
    assert "--report" in detail, "the reader has to be told where the values are"


def test_the_operators_own_terminal_still_shows_the_entry(
    with_a_literal_token: SetupContext,
) -> None:
    """This is the assertion that fails if the difference is simply deleted. The
    person who has to fix the entry is looking at their own screen."""
    assert LITERAL_TOKEN in _on_the_terminal(with_a_literal_token)


def test_a_field_name_someone_else_added_to_the_entry_is_counted_not_named(
    tmp_path: Path,
) -> None:
    """`~/.claude.json` is a hand-editable object in somebody else's state file,
    so a top-level key in it is data as much as a value is."""
    context = _context(tmp_path)
    (context.home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "theurian": {
                        "type": "http",
                        "url": f"http://127.0.0.1:{PORT}/mcp",
                        "headers": {"Authorization": "Bearer ${THEURIAN_MCP_TOKEN}"},
                        f"proxy-for-{FOREIGN_CLIENT}": "https://internal.example",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    published = _published(
        replace(context, mcp_config=ClaudeCodeMcpConfig(home=context.home, runner=StubRunner()))
    )

    assert FOREIGN_CLIENT not in published
    # The count reaches a person, so it is written as English: one field
    # *differs*, several *differ*. Asserted because a sentence nobody reads back
    # drifts, and this one is the reader's only signal that there is more.
    assert "1 further field differs" in _detail(published, StepId.MCP_CONNECTION)


def test_several_withheld_fields_are_counted_in_the_plural(tmp_path: Path) -> None:
    """The other half of the same sentence."""
    context = _context(tmp_path)
    (context.home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "theurian": {
                        "type": "http",
                        "url": f"http://127.0.0.1:{PORT}/mcp",
                        "headers": {"Authorization": "Bearer ${THEURIAN_MCP_TOKEN}"},
                        f"proxy-for-{FOREIGN_CLIENT}": "https://internal.example",
                        "audit-webhook": "https://internal.example/audit",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    published = _published(
        replace(context, mcp_config=ClaudeCodeMcpConfig(home=context.home, runner=StubRunner()))
    )

    assert "2 further fields differ" in _detail(published, StepId.MCP_CONNECTION)


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
    """An adapter can differ with no *field* differing -- a systemd unit whose
    comments were edited. The sentence must not claim to name fields it has none
    of, and must still withhold the values."""
    context = _context(
        tmp_path,
        service=FakeService(
            installed=True,
            difference=f"corrupt: {LITERAL_TOKEN}",
            differing=DifferingFields(),
        ),
    )
    detail = _detail(_published(context), StepId.DAEMON_SERVICE)

    assert LITERAL_TOKEN not in detail
    assert "Fields that differ" not in detail
    assert "withheld" in detail


def test_a_service_definition_that_does_not_parse_says_so_rather_than_withholding(
    tmp_path: Path,
) -> None:
    """Through the real adapter, because the parse-failure branch is the adapter's.

    "The installed values are withheld" was said of a plist whose values had
    never been read -- asserting that something was held back, on the one input
    where nothing was, and dropping the single fact the reader of that issue
    needs. The path is anchored and the words are Theurian's, so there is
    nothing here to withhold.
    """
    context = _context(tmp_path)
    manager = LaunchAgentManager(
        executable=context.executable, home=context.home, runner=StubRunner(), uid=501
    )
    manager.plist_path.write_text("this is not a plist", encoding="utf-8")

    detail = _detail(_published(replace(context, service=manager)), StepId.DAEMON_SERVICE)

    assert "is not a readable plist" in detail
    assert "values are withheld" not in detail, "nothing was read, so nothing was held back"


# -- The same step, through the other service manager -------------------------
#
# `daemon-service` was driven only through LaunchAgent here, and the CRITICAL
# that asymmetry hid was in the systemd adapter alone: its parser derived a
# directive *name* from a continuation line, which is the value of the line
# above it, and published a bearer token as a field name. An adapter-level test
# covered the parser; nothing asserted on the payload it feeds.


@pytest.fixture
def with_a_token_in_the_unit(tmp_path: Path) -> SetupContext:
    """A unit whose `ExecStart` continues onto a second line carrying a token.

    Written by hand rather than by editing `render`'s output, because what is
    being tested is a unit somebody else wrote -- which is the only state that
    makes this step conflict.
    """
    context = _context(tmp_path)
    manager = SystemdUserManager(
        executable=context.executable, home=context.home, runner=StubRunner()
    )
    manager.unit_path.parent.mkdir(parents=True, exist_ok=True)
    manager.unit_path.write_text(
        f"""\
[Unit]
Description=Theurian knowledge daemon

[Service]
Type=simple
ExecStart={context.executable} daemon start --foreground \\
    --header "Authorization: Bearer {UNIT_TOKEN}" --port=7419
Environment=THEURIAN_DATA_DIR={context.data_dir} \\
    THEURIAN_MCP_TOKEN={UNIT_ENV_TOKEN}
ExecStartPre={FOREIGN_SCRIPT}

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    return replace(context, service=manager)


def test_a_token_on_a_unit_continuation_line_never_reaches_a_report(
    with_a_token_in_the_unit: SetupContext,
) -> None:
    """It arrived as a field *name*, not a value -- which is why "we publish only
    names" was not by itself a safety argument."""
    published = _published(with_a_token_in_the_unit)

    assert UNIT_TOKEN not in published
    assert UNIT_ENV_TOKEN not in published


def test_a_directive_theurian_never_writes_never_reaches_a_report(
    with_a_token_in_the_unit: SetupContext,
) -> None:
    """`ExecStartPre` names a third-party absolute path, and no anchor reaches
    it. The name is Theurian's to publish only if Theurian writes it."""
    published = _published(with_a_token_in_the_unit)

    assert FOREIGN_SCRIPT not in published
    assert "ExecStartPre" not in published


def test_the_report_names_the_unit_directives_theurian_does_write(
    with_a_token_in_the_unit: SetupContext,
) -> None:
    """Withholding that removed the diagnosis would defeat the purpose, and the
    count is how the reader learns there is more on their terminal."""
    detail = _detail(_published(with_a_token_in_the_unit), StepId.DAEMON_SERVICE)

    assert "ExecStart" in detail
    assert "further field" in detail


def test_the_operators_own_terminal_still_shows_the_unit_difference(
    with_a_token_in_the_unit: SetupContext,
) -> None:
    assert UNIT_TOKEN in _on_the_terminal(with_a_token_in_the_unit)


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


def test_an_exception_from_an_apply_is_withheld_like_one_from_a_probe(tmp_path: Path) -> None:
    """The sibling channel, asserted rather than reasoned about.

    `doctor --report` is a dry run, so it never reaches `_apply` -- which is why
    that path kept a bare f-string beside a withholding one for a round. Nothing
    stops a caller running a real setup on a context built for publication, and
    an apply raises for the same reasons a probe does.
    """

    def _explode_on_apply(_: SetupContext) -> None:
        raise RuntimeError(EXCEPTION_MESSAGE)

    def _missing(_: SetupContext) -> SetupStep:
        return SetupStep(
            step_id=StepId.MIGRATIONS_VALID,
            status=StepStatus.MISSING,
            summary="Something is missing.",
            action="Create it.",
        )

    steps = (Step(StepId.MIGRATIONS_VALID, _missing, _explode_on_apply, critical=False),)
    report = SetupService(_context(tmp_path), steps).run(SetupRequest())
    payload = json.dumps(report.to_json())

    assert EXCEPTION_MESSAGE not in payload
    assert "RuntimeError" in payload


# -- The two halves cannot be used apart ---------------------------------------


def test_redacting_a_run_that_did_not_withhold_is_refused(
    with_a_literal_token: SetupContext,
) -> None:
    """Substitution alone reproduces the defect this PR closes.

    Hand `_redacted` a payload from a context built without `for_publication`
    and it stamps `"redacted": true` on output that still carries whatever the
    steps read -- exactly the pre-PR behaviour, reachable by calling one half of
    a two-half control. `doctor_command` is the only caller today; the guard is
    for the one that does not exist yet.
    """
    local = replace(with_a_literal_token, for_publication=False)
    payload = SetupService(local).run(SetupRequest(dry_run=True)).to_json()
    assert LITERAL_TOKEN in json.dumps(payload), "the operator's own run must carry it"

    with pytest.raises(SetupError, match="for_publication"):
        _redacted(payload, local)


# -- Every step, not the five that were known to be broken ---------------------


#: Seeds a step publishes today when nothing withholds, so their absence from a
#: report is a measurement rather than a coincidence.
#:
#: The remaining seeds -- the `.gitignore`, the env file, the token file, a
#: migration's contents, and a foreign server's name in `claude.json` -- are read
#: by a step that publishes only a path, a count, or a boolean about them.
#: Swapping one of those for another string is undetectable, **and that is what
#: they are for**: they guard a step that does not exist yet, on the files a
#: future step is most likely to start quoting.
#:
#: The split is measured, not asserted: `test_every_observable_seed_reaches_the
#: _operators_own_output` fails if a seed listed here is not published unredacted,
#: which is how `.gitignore` moved out of it. A sweep whose coverage is
#: overstated is worse than a smaller one.
_OBSERVED_SEEDS: Final = (
    "claude.json theurian entry",
    "launchagent plist",
    "another daemon's /health",
    "project registry",
)


def _leaked(published: str, seeds: dict[str, str]) -> list[str]:
    """The sources whose seeded value reached the payload. The sweep's predicate."""
    return sorted(source for source, value in seeds.items() if value in published)


def _seed_every_external_source(context: SetupContext) -> tuple[SetupContext, dict[str, str]]:
    """Put a distinctive string into everything the plan reads and does not own.

    Keyed by the source, so a failure names the file rather than only the
    sentinel. Each is placed the way a real machine would carry it: a
    hand-edited config, a reply from another process, a file the user wrote.
    """
    home, data_dir = context.home, context.data_dir
    root = context.project_root
    assert root is not None

    seeds = {
        "claude.json theurian entry": "SweepClaudeEntryKKKK",
        "claude.json foreign server": "SweepForeignServerLLLL",
        "launchagent plist": "SweepPlistMMMM",
        "another daemon's /health": "SweepForeignDataDirNNNN",
        "project registry": "sweep-foreign-project-oooo",
        ".gitignore": "SweepGitignorePPPP",
        "env file": "SweepEnvFileQQQQ",
        "token file": "SweepTokenFileRRRR",
        "migration file": "SweepMigrationSSSS",
    }

    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "theurian": {
                        "type": "http",
                        "url": f"http://127.0.0.1:{PORT}/mcp",
                        "headers": {
                            "Authorization": f"Bearer {seeds['claude.json theurian entry']}"
                        },
                    },
                    seeds["claude.json foreign server"]: {"type": "stdio"},
                }
            }
        ),
        encoding="utf-8",
    )
    manager = LaunchAgentManager(
        executable=context.executable, home=home, runner=StubRunner(), uid=501
    )
    manager.plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": LABEL,
                "EnvironmentVariables": {"THEURIAN_MCP_TOKEN": seeds["launchagent plist"]},
            },
            sort_keys=True,
        )
    )
    (data_dir / "projects.json").write_text(
        json.dumps({seeds["project registry"]: {"noRootPath": True}}), encoding="utf-8"
    )
    (root / ".gitignore").write_text(f"{seeds['.gitignore']}\n", encoding="utf-8")
    # A *current* block with a line under it assigning the same variable, rather
    # than a file with no markers at all. Both are read by `probe_env_reference`,
    # but only this one reaches the arm that reports a SATISFIED step carrying a
    # detail (#128) -- and a detail on a satisfied step is a publishing channel
    # that was added after this sweep was written. The seed is the value on that
    # line, which is what a detail built from what the probe read would carry.
    (data_dir / "env").write_text(
        f"{env_block(data_dir)}\nexport {TOKEN_ENV_VAR}={seeds['env file']}\n",
        encoding="utf-8",
    )
    (data_dir / "auth").mkdir(parents=True, exist_ok=True)
    (data_dir / "auth" / "mcp-token").write_text(seeds["token file"], encoding="utf-8")

    migrations = root / ".theurian" / "migrations"
    migrations.mkdir(parents=True, exist_ok=True)
    (migrations / "0001-note.yaml").write_text(
        f"note: {seeds['migration file']}\n", encoding="utf-8"
    )

    foreign_directory = f"/opt/{seeds["another daemon's /health"]}"
    seeded = replace(
        context,
        mcp_config=ClaudeCodeMcpConfig(home=home, runner=StubRunner()),
        service=manager,
        health=lambda: {"status": "ok", "dataDir": foreign_directory},
    )
    return seeded, seeds


def test_no_step_publishes_a_value_it_only_read(tmp_path: Path) -> None:
    """The enumeration, rather than one test per route already known to break.

    Per-route memory is what this class is made of: the routes fixed here were
    separate discoveries, and another arrives the moment someone adds a
    ``detail=`` to a step nobody thought about -- one line publishing a
    ``.gitignore``'s contents passed the whole suite. This sweep covers every
    step in ``STEPS`` at once, so the next one is caught by a test that already
    exists rather than by the next reviewer.

    It asserts the payload *and* that the run reached the steps: a sweep over a
    plan whose steps all reported NOT_APPLICABLE would pass by not looking. That
    the steps *conflict* is not the same as the seeds being what made them
    conflict, though, so :data:`_OBSERVED_SEEDS` is checked against the
    operator's own output as well -- see that constant for what the rest of the
    seeds do and do not prove.
    """
    root = tmp_path / "this-repository"
    (root / ".git").mkdir(parents=True)
    context, seeds = _seed_every_external_source(_context(tmp_path, project_root=root))

    published = _published(context)

    assert not _leaked(published, seeds), (
        f"published values Theurian did not write: {sorted(_leaked(published, seeds))}"
    )
    # The redaction half of `_published`, which nothing else in this file needs:
    # without it the sweep would pass with `_redacted` removed, while the module
    # docstring claims to run it.
    assert str(context.home) not in published

    statuses = {step["id"]: step["status"] for step in json.loads(published)["steps"]}
    assert len(statuses) == len(StepId), "the sweep must cover every step"
    for reached in ("mcp-connection", "daemon-service", "single-instance", "project-registered"):
        assert statuses[reached] == "conflicting", f"{reached} never read what was seeded"


def test_every_observable_seed_reaches_the_operators_own_output(tmp_path: Path) -> None:
    """The positive control the sweep needs to mean anything.

    A seed proves something only if the payload would carry it when nothing
    withholds. Without this, swapping an observed seed for another string leaves
    the sweep green -- it would be asserting the absence of a value that was
    never going to appear.

    Runs the same fixture with ``for_publication`` off, and requires every seed
    in :data:`_OBSERVED_SEEDS` to be there.
    """
    root = tmp_path / "this-repository"
    (root / ".git").mkdir(parents=True)
    context, seeds = _seed_every_external_source(_context(tmp_path, project_root=root))

    local = _on_the_terminal(context)

    missing = sorted(source for source in _OBSERVED_SEEDS if seeds[source] not in local)
    assert not missing, f"seeds that no step publishes even unredacted: {missing}"


def test_the_sweep_rings_for_a_step_that_forgets_to_withhold(tmp_path: Path) -> None:
    """The alarm's own test, wired to the line the next author might write.

    A guard nobody has watched fail is a guard nobody knows the shape of. This
    is the exact one-line addition that reopened the class during review -- a
    step publishing a file's contents -- and it asserts the sweep sees it.
    """
    root = tmp_path / "this-repository"
    (root / ".git").mkdir(parents=True)
    context, seeds = _seed_every_external_source(_context(tmp_path, project_root=root))

    def _leaky(_: SetupContext) -> SetupStep:
        return SetupStep(
            step_id=StepId.GITIGNORE,
            status=StepStatus.CONFLICTING,
            summary="Derived Theurian artifacts are not ignored by Git.",
            detail=f"The file currently holds:\n{(root / '.gitignore').read_text()}",
        )

    published = _published(context, (Step(StepId.GITIGNORE, _leaky, _nothing, critical=False),))

    assert _leaked(published, seeds) == [".gitignore"], "the sweep's alarm must be able to ring"
