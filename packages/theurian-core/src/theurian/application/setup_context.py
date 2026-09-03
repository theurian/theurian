"""What every setup step is allowed to touch (§6, ADR-0003).

One context object rather than a pile of parameters, so that a step cannot
quietly reach for something the plan did not account for: everything a step may
read or write is on this record, and everything on this record is either a path
or an injected adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from theurian.domain.ports.daemon_manager import DaemonManager
from theurian.domain.ports.mcp_client_config import McpClientConfig
from theurian.domain.ports.secret_store import SecretStore
from theurian.domain.state import StateHash


@dataclass(frozen=True, slots=True)
class MigrationsCheck:
    """What a project's migration set turned out to be, once something read it.

    Two fields rather than a raised exception, because the step reporting this
    has to publish a verdict for a refusal as well as for a healthy set: a probe
    that let the failure escape would reach the reader as
    ``SetupService._probe``'s generic "Could not check migrations-valid" and lose
    the sentence written for it.
    """

    #: How many migrations were loaded. The loader's own enumeration, never a
    #: second count taken beside it -- `doctor` and `theurian migrate validate`
    #: reporting different numbers for the same directory is the defect #91 is
    #: about.
    count: int
    #: ``None`` when the set loads and passes the static guards `theurian
    #: migrate validate` runs. Otherwise whatever refused it, so the step can
    #: publish the type and withhold the message (:func:`failure_detail`).
    failure: Exception | None


@dataclass(frozen=True, slots=True)
class SetupContext:
    """Everything the steps operate on."""

    home: Path
    data_dir: Path
    port: int
    #: The repository setup was invoked in, or ``None`` when it was not invoked
    #: inside one. Project steps report themselves not-applicable rather than
    #: failing: installing the machine-wide parts outside a repository is a
    #: perfectly reasonable thing to do.
    project_root: Path | None
    #: The connection the MCP client should hold. Opaque here: its shape is the
    #: client adapter's business, and the steps only pass it through.
    connection: Any
    mcp_config: McpClientConfig
    secrets: SecretStore
    #: Asks whatever is on the port whether it is a healthy Theurian. Injected
    #: rather than imported so the application layer keeps depending on
    #: behaviour instead of on the daemon's HTTP client (ADR-0003).
    health: Callable[[], dict[str, object] | None]
    #: ``None`` when this platform has no user-scoped service manager. Supported
    #: rather than fatal — the daemon can be started by hand.
    service: DaemonManager | None
    #: Absolute path to the ``theurian`` executable, as the service unit will
    #: invoke it. A relative name would resolve against launchd's PATH, which is
    #: not the user's.
    executable: str
    #: Loads and validates a project's migration set, given the repository root.
    #: Injected from the CLI composition root because the application layer must
    #: not import the infrastructure loader (ADR-0003) -- the same reasoning as
    #: :attr:`health`, and for the same reason it is **not defaulted**: a probe
    #: falling back to "nothing to check" would report ``satisfied`` on every
    #: machine whose composition root forgot to wire it, which is #91 wearing a
    #: different hat.
    check_migrations: Callable[[Path], MigrationsCheck]
    #: The state hash the migration set on disk resolves to (ADR-0016), given the
    #: repository root -- the value ``theurian project status`` publishes as
    #: ``stateHash``, and the one that names the database file for the state a
    #: project is *at*.
    #:
    #: ``None`` means **no hash could be produced for this tree**, and the three
    #: ways that happens fail at three different points: the published JSON
    #: Schemas cannot be located, so the load never starts; a schema is found and
    #: cannot be read, so it stops before a migration is parsed; or a migration is
    #: read and refused. "The load was attempted and refused" covers only the last
    #: two, which is why the step publishing this says the set *could not be
    #: read* rather than anything about when the reading stopped.
    #:
    #: It is also narrower than "something went wrong". The composition root's
    #: implementation resolves the project's paths *before* the load, so a
    #: ``.theurian`` that escapes the working tree raises out of this call rather
    #: than answering ``None`` (#237, T-5; measured -- the step is reported
    #: ``conflicting``, "Could not check initial-index."). A caller that reads
    #: ``None`` as "every failure is contained here" is reading more than this
    #: returns.
    #:
    #: Injected from the CLI composition root for the same reason as
    #: :attr:`check_migrations`: resolving it means loading YAML off disk against
    #: the published JSON Schemas, which the application layer does not reach for
    #: (ADR-0003).
    #:
    #: **Separate from :attr:`check_migrations` rather than folded into
    #: :class:`MigrationsCheck`, because the two questions separate in practice.**
    #: "Does this set validate?" and "which state is this project at?" are
    #: answered by the same load, but a test whose subject is some other step
    #: wants a checker that reads nothing *and* a state hash that is real -- the
    #: two cannot ride on one record without the record lying about one of them.
    #:
    #: **Not defaulted**, for the reason spelled out on :attr:`check_migrations`:
    #: a probe falling back to "no state hash" would publish "cannot tell whether
    #: the knowledge state is built" on every machine whose composition root
    #: forgot to wire it, and a fallback answering with the *active pointer*
    #: instead is #451 -- `doctor` reporting "Knowledge state is built." for a
    #: project ``theurian project status`` reports ``stateBuilt: false`` for.
    current_state_hash: Callable[[Path], StateHash | None]
    #: True when this run's output is bound for somewhere the operator does not
    #: control: ``doctor --report``, which exists to be pasted into a public
    #: issue.
    #:
    #: **A probe that reads a value Theurian did not author must withhold it
    #: when this is set.** Another process's reply, another user's configuration
    #: file, an exception raised by a library -- none of it is Theurian's to
    #: publish. ``cli/setup_commands._redacted`` cannot help there: it
    #: substitutes the paths this context holds, and a string the local context
    #: never held has no anchor to substitute, so it goes out verbatim. That is
    #: how a literal ``Authorization: Bearer <token>`` in someone's
    #: ``~/.claude.json`` reached a redacted report.
    #:
    #: A field *name* read out of such a place is one of those values. It is
    #: whatever string sat in key position in somebody else's file, and a systemd
    #: continuation line -- which is the value of the directive above it -- parsed
    #: alone as a name carrying a token.
    #:
    #: **This flag is a requirement on each probe, not a mechanism that enforces
    #: one.** Nothing stops a new ``detail=`` from ignoring it; that was measured
    #: by adding one, and the suite stayed green. Two things catch it:
    #: :func:`cli.setup_commands._redacted` refuses a context that did not ask
    #: for publication, and
    #: ``tests/integration/test_setup_report_withholding.py`` sweeps every step
    #: in ``STEPS`` with a seeded sentinel. A step reading a source that sweep
    #: does not seed is a step nothing checks -- add it there.
    #:
    #: The flag lives here rather than as a probe parameter because the context
    #: is the only channel every step already has, and because the composition
    #: root -- which is the layer that knows where the output is going -- is
    #: what builds it.
    for_publication: bool = False

    @property
    def auth_dir(self) -> Path:
        return self.data_dir / "auth"

    @property
    def env_file(self) -> Path:
        return self.data_dir / "env"

    @property
    def theurian_dir(self) -> Path | None:
        return self.project_root / ".theurian" if self.project_root else None
