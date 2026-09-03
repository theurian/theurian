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

**Three sentences are pinned individually; the class is closed by the sweep at
the bottom of this file.** The sweep's key is what makes it worth anything: it
asks every daemon-mentioning *field* of the plan to name the port it was drawn
from, rather than asking no step to contain one phrasing of the old mistake. A
key that named the phrasing was the first thing written here, and "No daemon
serves this data directory" would have walked straight past it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Final, NamedTuple

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import state_hash_from_the_loader, unchecked_migrations

from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupService
from theurian.application.setup_steps import (
    probe_daemon_running,
    probe_mcp_health,
    probe_single_instance,
)
from theurian.domain.setup import SetupStep, StepId, StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import FileSecretStore

pytestmark = pytest.mark.integration

#: Not the default. A hardcoded 7419 in any sentence below fails here.
PORT = 7420

#: The claim this cluster exists to delete, in the form the three original
#: sentences took. Kept as a *second* layer under the positive predicate below,
#: never as the key: a symptom string only recognises the mistakes someone has
#: already made. Matched case-insensitively so "The daemon is running" is caught
#: as well as the original "No daemon is running".
_UNSCOPED_CLAIM = "daemon is running"

#: The population predicate for the class sweep: a published field is asked to
#: scope its claim when it mentions a daemon **at all**.
#:
#: The widest available word, on purpose. Anything narrower is a list of
#: phrasings, and the class is not a list of phrasings -- it is "a port-scoped
#: observation published as a fact about more than that port", which the next
#: author can express in words nobody here thought of. ``step_id`` is not part
#: of the text that is searched, so ``daemon-running`` is in the population
#: because of what its sentences say and never because of its name.
_MENTIONS_A_DAEMON: Final = "daemon"


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
        "current_state_hash": state_hash_from_the_loader,
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


@pytest.fixture
def sweep_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A working directory whose own path cannot contain the word being searched for.

    ``tmp_path`` is named after the test that asks for it, and every test below
    is named after daemons -- so ``data-directory``, ``serving-profile`` and
    ``env-reference``, which quote their paths, all matched
    :data:`_MENTIONS_A_DAEMON` and reported themselves as offenders. That was
    the sweep working: it reads substituted values as well as authored words,
    and it cannot tell them apart. ``tmp_path_factory.mktemp`` names the
    directory rather than the test, which takes the fixture out of the corpus
    without narrowing what the predicate looks at.
    """
    return tmp_path_factory.mktemp("port-scoping")


#: The fields a reader of a plan sees, and the unit the class sweep works in.
#:
#: **Per field rather than per step**, which is not a detail. A step-wide check
#: was written first and it survived the mutation that matters: leave
#: ``single-instance``'s scoped summary alone and add
#: ``detail="No daemon serves this data directory."`` beside it, and the step as
#: a whole still contains ``127.0.0.1:7420``, so the sweep passed a step that had
#: just published exactly the claim #93 is about. A field is the smallest thing
#: a reader is shown on its own, so it is the unit that has to stand up alone.
_PUBLISHED_FIELDS: Final = ("summary", "detail", "action")


def _published(step: SetupStep) -> str:
    """Everything a reader of a plan sees from one step, as one string.

    For the two whole-step checks only -- the positive control, which asks
    whether the step named the port *anywhere*, and the symptom layer. The class
    sweep uses :data:`_PUBLISHED_FIELDS` instead, for the reason recorded there.
    """
    return " ".join(getattr(step, field) for field in _PUBLISHED_FIELDS)


def _scopes_to_the_probed_port(text: str) -> bool:
    """Whether a sentence names the one address this run asked about.

    Two written forms exist in the plan, and both name the port rather than
    generalising past it, so both count:

    - ``127.0.0.1:7420`` -- the three #93 members, and the MCP URL that
      ``mcp-health`` reports on its satisfied arm.
    - ``port 7420`` -- ``single-instance``'s conflicting arm, whose summary says
      "The daemon on this port" and whose detail opens "Port 7420 is held by".

    A bare ``7420`` is deliberately not enough: the number appears in paths and
    URLs belonging to steps that observed nothing, and accepting it would let a
    sentence that generalises past the probe pass on someone else's evidence.
    """
    return f"127.0.0.1:{PORT}" in text or f"port {PORT}" in text.lower()


#: The published fields that mention a daemon and are *not* required to name a
#: port, each with the substring that has to still be present for the exemption
#: to apply.
#:
#: Keyed by ``(step_id, status, field)`` and witnessed by a phrase, so the
#: exemption is fail-closed three times over: another arm of the same step is
#: not covered, another field of the same arm is not covered, and a rewrite of
#: the exempt sentence drops out of the witness and back into the population,
#: where it needs a fresh decision rather than inheriting this one.
#:
#: Enumerated from what ``SetupService.plan()`` produces across the states in
#: :func:`_states`, not from reading the probes.
#: :func:`test_every_recorded_scoping_exemption_is_still_reached_by_the_sweep`
#: holds the list to that.
_EXEMPT_FROM_SCOPING: Final[dict[tuple[StepId, StepStatus, str], str]] = {
    # Talks about the *service definition*, not about a daemon answering: a
    # service manager starts with its own PATH, so the unit has to name an
    # absolute executable. Derived from `shutil.which` and this interpreter's
    # imports; it never calls `context.health()` and asserts nothing about what
    # is running anywhere. The remaining "daemon"s are inside the two install
    # commands it recommends.
    (StepId.CORE_PRESENT, StepStatus.CONFLICTING, "detail"): (
        "must invoke Theurian by absolute path"
    ),
    # A remedy, not an observation. The step reports that this *platform* has no
    # user-scoped service manager -- a property of the operating system, not of
    # a port -- and its "daemon" is in the command it tells the reader to run.
    (StepId.DAEMON_SERVICE, StepStatus.NOT_APPLICABLE, "detail"): (
        "start the daemon with `theurian daemon start --foreground`"
    ),
    # The same remedy under the step that did observe the port. Its summary
    # beside this one carries the address, and the positive control pins that.
    (StepId.DAEMON_RUNNING, StepStatus.NOT_APPLICABLE, "detail"): (
        "Start it with `theurian daemon start --foreground`."
    ),
    # Scoped in prose rather than in digits: "on this port" is the port that was
    # probed, and the detail beside it opens "Port 7420 is held by". The
    # positive control requires that detail to keep naming it.
    (StepId.SINGLE_INSTANCE, StepStatus.CONFLICTING, "summary"): (
        "The daemon on this port serves a different data directory."
    ),
    # Read out of Claude Code's own config file, not off the wire. "this daemon"
    # is the `ConnectionSpec` this run holds, which is the port under discussion
    # by construction, so there is no second port the sentence could be confused
    # about.
    (StepId.MCP_CONNECTION, StepStatus.SATISFIED, "summary"): (
        "Claude Code points at this daemon."
    ),
    # **A recorded decision, not an oversight.** This sentence really does draw
    # an unscoped conclusion from one port's answer: a second daemon serving the
    # same data directory on another port would leave it saying "exactly one".
    # That is issue #88's subject -- what `single-instance` can honestly conclude
    # when the port *does* answer -- rather than #93's, which is what it may
    # conclude when the port is silent. Named here so the gap is a decision on
    # the record; delete this entry when #88 lands.
    (StepId.SINGLE_INSTANCE, StepStatus.SATISFIED, "summary"): (
        "Exactly one daemon serves this data directory."
    ),
}


class _State(NamedTuple):
    """A machine state, and which health-derived steps must name the port in it."""

    label: str
    context: SetupContext
    #: The positive control. Steps that reported on `context.health()` here and
    #: are therefore required to say which address it asked, whether or not
    #: their sentence happens to contain the word "daemon" -- `mcp-health`'s
    #: silent-port sentence does not, and would otherwise be swept by nothing.
    must_scope: frozenset[StepId]


def _states(tmp_path: Path) -> tuple[_State, ...]:
    """Every state in which a step of the plan speaks about a daemon.

    Enumerated from what ``SetupService.plan()`` actually produces rather than
    from what the steps look like they produce, and it is the enumeration that
    does the work here. The silent-port-with-no-service-manager state alone --
    which is all this sweep used to run -- reaches no satisfied arm at all, so
    "A daemon is answering", "Exactly one daemon serves this data directory" and
    "The daemon answers on ..." were outside anything it could have caught; and
    it skips ``daemon-running``'s ``missing`` arm, which is the state an ordinary
    machine is in halfway through its first ``theurian setup``.

    ``dataDir`` is the only key of the health payload any setup step reads
    (``probe_single_instance``); the other two only ask whether it is ``None``.
    """
    silent = _context(tmp_path / "silent", service=None)
    mid_setup = _context(tmp_path / "mid-setup")

    same_dir = tmp_path / "healthy" / "home" / ".theurian"
    same_dir.mkdir(parents=True, exist_ok=True)
    healthy = _context(
        tmp_path / "healthy",
        health=lambda: {"dataDir": str(same_dir)},
        service=FakeService(installed=True),
        mcp_config=FakeMcpConfig(entry=ConnectionSpec(port=PORT).as_entry()),
    )

    foreign = tmp_path / "duplicate" / "elsewhere"
    foreign.mkdir(parents=True, exist_ok=True)
    duplicate_kwargs: dict[str, object] = {
        "health": lambda: {"dataDir": str(foreign)},
        "service": FakeService(installed=True),
        "mcp_config": FakeMcpConfig(entry=ConnectionSpec(port=PORT).as_entry()),
    }

    return (
        # The #93 state: nothing answers, and there is no service manager to ask
        # instead, so the port is the whole of the evidence.
        _State(
            "silent port, no service manager",
            silent,
            frozenset({StepId.DAEMON_RUNNING, StepId.SINGLE_INSTANCE, StepId.MCP_HEALTH}),
        ),
        # The same silence with a service manager present, which is where an
        # ordinary machine sits between `daemon-service` being registered and
        # the daemon answering. `daemon-running` takes its `missing` arm here and
        # in no other state below.
        _State(
            "silent port, with a service manager",
            mid_setup,
            frozenset({StepId.DAEMON_RUNNING, StepId.SINGLE_INSTANCE, StepId.MCP_HEALTH}),
        ),
        # The satisfied side. `single-instance` is absent from `must_scope` here
        # and exempt above, for #88's reason and no other.
        _State(
            "a healthy daemon serving this data directory",
            healthy,
            frozenset({StepId.DAEMON_RUNNING, StepId.MCP_HEALTH}),
        ),
        # The conflict `single-instance` exists to report, which is where its
        # sentences are least free to generalise.
        _State(
            "a daemon on this port serving another data directory",
            _context(tmp_path / "duplicate", **duplicate_kwargs),
            frozenset({StepId.DAEMON_RUNNING, StepId.SINGLE_INSTANCE, StepId.MCP_HEALTH}),
        ),
        # The same conflict as `doctor --report` renders it. A withheld value is
        # still a published sentence, and the withholding substitutes the data
        # directory, never the port.
        _State(
            "the same conflict, rendered for publication",
            _context(tmp_path / "report", for_publication=True, **duplicate_kwargs),
            frozenset({StepId.DAEMON_RUNNING, StepId.SINGLE_INSTANCE, StepId.MCP_HEALTH}),
        ),
    )


def _unscoped_daemon_fields(
    state: _State,
) -> Iterator[tuple[tuple[StepId, StepStatus, str], str]]:
    """Every published field of this state's plan that speaks of a daemon and names no port.

    The population, generated once and shared by the sweep and by the
    exemption-hygiene test below. Two traversals written separately would be two
    definitions of the population, free to drift apart until the hygiene test
    certifies an exemption list the sweep no longer uses.
    """
    for step in SetupService(state.context).plan().steps:
        for field in _PUBLISHED_FIELDS:
            text: str = getattr(step, field)
            if _MENTIONS_A_DAEMON not in text.lower() or _scopes_to_the_probed_port(text):
                continue
            yield (step.step_id, step.status, field), text


def test_every_daemon_sentence_in_the_plan_names_the_port_it_was_drawn_from(
    sweep_root: Path,
) -> None:
    """The class predicate, stated positively, over every step in ``STEPS``.

    Three steps drew the same wrong conclusion from one call to
    ``context.health()``, written at different times by different hands. What
    keeps that class closed cannot be the three corrected sentences, and cannot
    be a search for the wording they used to have: the next author will phrase
    it their own way, and "No daemon serves this data directory" contains no
    substring of "No daemon is running".

    So the requirement is turned around and made universal. Every published
    field that mentions a daemon must say **which port it was drawn from**, or
    appear in :data:`_EXEMPT_FROM_SCOPING` with a defence and a witness phrase.
    A new unscoped sentence fails by default; nobody has to have predicted its
    wording.
    """
    offenders: dict[str, list[str]] = {}
    for state in _states(sweep_root):
        for key, text in _unscoped_daemon_fields(state):
            witness = _EXEMPT_FROM_SCOPING.get(key)
            if witness is not None and witness in text:
                continue
            step_id, status, field = key
            offenders.setdefault(state.label, []).append(
                f"{step_id.value}/{status.value}/{field}: {text}"
            )

    assert offenders == {}, (
        f"these fields report on daemons beyond the port they were drawn from: {offenders}"
    )


def test_the_steps_that_probed_the_port_still_say_which_port_it_was(
    sweep_root: Path,
) -> None:
    """The positive control, without which deleting the sentences passes.

    The sweep above constrains only steps that *mention* a daemon, and one
    health-derived sentence does not: "Nothing is answering on 127.0.0.1:7420,
    so the MCP endpoint cannot be checked." Stripping its address would leave
    the sweep green, so the steps that called ``context.health()`` are named
    here and required to name the address back.
    """
    for state in _states(sweep_root):
        spoke = {
            step.step_id
            for step in SetupService(state.context).plan().steps
            if _scopes_to_the_probed_port(_published(step))
        }

        assert spoke >= state.must_scope, (
            f"in the state '{state.label}', these steps stopped naming the port "
            f"they probed: {sorted(step.value for step in state.must_scope - spoke)}"
        )


def test_no_step_in_the_plan_still_carries_the_original_unscoped_claim(
    sweep_root: Path,
) -> None:
    """The second layer: the exact mistake #93 was filed for, in any casing.

    Subsumed by the sweep above for every arm the sweep reaches -- "a daemon is
    running" names no port, so it is already an offender. Kept because it is
    cheap, because it holds even for an arm whose state nobody has enumerated
    here yet, and because a reader who greps for #93's original wording should
    find a test that mentions it.
    """
    for state in _states(sweep_root):
        offenders = [
            step.step_id.value
            for step in SetupService(state.context).plan().steps
            if _UNSCOPED_CLAIM in _published(step).lower()
        ]

        assert offenders == [], (
            f"in the state '{state.label}', these steps report a daemon the probe "
            f"never looked for: {offenders}"
        )


def test_every_recorded_scoping_exemption_is_still_reached_by_the_sweep(
    sweep_root: Path,
) -> None:
    """An exemption nothing reaches is a hole nobody is watching.

    The exemption list is the one place this file can be weakened without a
    failing test: a step added to it stops being asked to scope its claim. This
    pins it to what the plan produces, so a sentence that was rewritten, an arm
    that stopped being reachable, or an entry added "just in case" shows up as a
    failure instead of quietly widening the sweep's blind spot.
    """
    reached: set[tuple[StepId, StepStatus, str]] = set()
    for state in _states(sweep_root):
        for key, text in _unscoped_daemon_fields(state):
            witness = _EXEMPT_FROM_SCOPING.get(key)
            if witness is not None and witness in text:
                reached.add(key)

    unreached = sorted(
        (step_id.value, status.value, field)
        for step_id, status, field in set(_EXEMPT_FROM_SCOPING) - reached
    )
    assert unreached == [], (
        f"these exemptions are not produced by any state swept here: {unreached}"
    )
