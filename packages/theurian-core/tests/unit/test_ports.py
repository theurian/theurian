"""The port set (ADR-0003).

Ports are the substitution points that make ADR-0003, ADR-0009, and the
cloud-ready design in ADR-0014 possible. A port that quietly acquires a concrete
implementation, or a base class an adapter must inherit, silently inverts the
dependency the whole layering exists to protect.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
import textwrap
from pathlib import Path

import pytest
from fakes import FakeReviewFindingSource
from write_lock_claims import REPO_ROOT, collapsed

from theurian.domain import ports
from theurian.domain.ports import ReviewFindingSource
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource

#: The closed set. Growing it requires an ADR, so this list is the enforcement.
#:
#: ``IndexStore`` was added in Milestone 6, and it is a *registration* rather
#: than a new abstraction: the port has existed since Milestone 5 under ADR-0003
#: and ADR-0022, and its contract is now governed by ADR-0024. It had simply
#: never been listed, so every parametrised test below -- Protocol, runtime
#: checkable, documented, annotated, no method bodies, not instantiable -- had
#: never run against it. Measured before adding it: ``IndexStore in ALL_PORTS``
#: was ``False`` with fourteen entries, while the port carried nine methods and a
#: breaking change to three of them.
#:
#: That is the failure mode this list exists to prevent, arriving from the
#: opposite direction to the one it was written for. The set being *closed*
#: stops a port being added without an ADR; it does nothing about a port that
#: exists, is imported by the application layer, and is absent from the list --
#: for which every check here is silently vacuous rather than failing.
#:
#: ``ReviewFindingSource`` was added by ADR-0029: the FR-S1 Git-commit-metadata
#: arm reads ``Review-Finding:`` trailers into canonical records. It is a genuine
#: new abstraction rather than a registration -- the ``SourceParser`` port maps
#: one file to one document by media type and cannot express a ``git log`` read
#: that yields many findings across many commits -- so it lands with its driving
#: ADR, exactly the deliberate-decision path this list gates.
#:
#: ``ReviewFindingStore`` is the other half of that same ADR-0029 arm: where the
#: source *reads* findings out of git, the store *lands* them in a wholesale-rebuilt
#: Canonical-layer projection. It is a genuine new abstraction rather than a
#: registration -- no existing port expresses a rebuild-from-git artifact carrying a
#: parser stamp and exposing a verification dump but no serving read -- so it too
#: lands with ADR-0029.
EXPECTED_PORTS = frozenset(
    {
        "AuthorizationProvider",
        "CanonicalStore",
        "Clock",
        "DaemonManager",
        "EmbeddingProvider",
        "IdGenerator",
        "IndexStore",
        "ObjectStore",
        "RerankingProvider",
        "ReviewFindingSource",
        "ReviewFindingStore",
        "ReviewProvider",
        "SecretStore",
        "SourceParser",
        "SpecificationProvider",
        "SummarizationProvider",
        "VectorStore",
    }
)


#: ADR-0003, whose point 5 the pins below hold. Read as a record rather than
#: imported, so the two halves stay independently written: the ADR states the
#: register in prose and this module recomputes it from the code.
ADR_0003 = REPO_ROOT / "docs" / "adr" / "0003-ports-and-adapters.md"

#: Every ``typing.Protocol`` declared under ``domain/ports/`` that is deliberately
#: **outside** :data:`ports.ALL_PORTS`, and therefore outside every check keyed to
#: it. :func:`test_port_set_is_closed` cannot see these: it draws its names *from*
#: ``ALL_PORTS``, so a Protocol that never reaches the tuple is not compared
#: against anything. This set is that test's complement, and the two together are
#: what make "the port set is closed" a checkable sentence rather than an
#: intention.
#:
#: Membership, not size. A new Protocol landing outside the register fails naming
#: itself, and a member removed from here fails too -- the second direction is
#: what stops the set being widened to whatever the walk happens to find.
EXPECTED_OUTSIDE_THE_REGISTER = frozenset(
    {
        # A narrowing of `CanonicalStore` with an explicit handle lifetime.
        # Injected as a `store_factory: Callable[[Path], CanonicalReadSession]`,
        # so what an operator substitutes is still a `CanonicalStore` adapter and
        # no boundary opens that `ALL_PORTS` does not already govern.
        "CanonicalReadSession",
        # A widening of `CanonicalReadSession` by `list_relations`, for SEC-11's
        # build-time scan of a relation's `note` (#329). Same standing as the
        # Protocol it widens, and the live demonstration of why this pin exists:
        # it landed outside the register with nothing to go RED.
        "IndexBuildSession",
        # An open question rather than a decision. It has a port's shape --
        # `SetupContext.mcp_config` is constructor-injected and
        # `cli/setup_commands.py` names `ClaudeCodeMcpConfig` as its adapter --
        # but whether it *joins* the register is itself an ADR decision (#140),
        # and moving it here would take that decision by edit.
        "McpClientConfig",
    }
)

#: The sentence ADR-0003's point 5 amendment must keep carrying: the register
#: itself. Lowercase because it is matched against :func:`collapsed` output.
#:
#: This one key does double duty, which is why the negative it guards is not
#: written as a second pattern. The amendment exists because point 5 gave a bare
#: count and named no register; a rewrite that reverted to a bare count would
#: take this sentence with it, and the pin then fails at the locator with
#: "found 0" rather than passing over a record that had lost its point.
_REGISTER_SENTENCE = "**the register is `all_ports`, in `domain/ports/__init__.py`**"

#: One data row of the amendment's outside-the-register table, keyed on a first
#: cell that is *only* a code span. The header's first cell reads
#: ``Outside `ALL_PORTS` `` -- a word before the backtick -- and the separator row
#: carries no code span at all, so neither is counted as a Protocol.
_OUTSIDE_TABLE_ROW = re.compile(r"^\s*>\s*\|\s*`(\w+)`\s*\|")


def _protocols_declared_under_ports() -> frozenset[str]:
    """Every ``typing.Protocol`` declared in the ``domain.ports`` package, by name.

    Walked at runtime rather than parsed, and keyed on ``_is_protocol`` -- the
    same attribute :func:`test_port_is_a_protocol` reads. A source scan for
    ``class X(Protocol)`` answers a question about spelling: ``from typing import
    Protocol as P`` declares a Protocol that no such scan sees, and the whole
    point of this walk is to find the declaration nobody registered.

    Each class is attributed to the module that *defines* it, so ``__init__.py``
    re-exporting the ports does not double-count them, and ``Protocol`` itself --
    imported into every one of these modules -- is not counted at all.
    """
    package_modules = [ports]
    for info in pkgutil.iter_modules(ports.__path__):
        assert not info.ispkg, (
            f"`domain/ports/` now contains the subpackage `{info.name}`, which this "
            f"walk does not descend into; a Protocol declared inside it would be "
            f"outside the register and outside this pin at the same time"
        )
        package_modules.append(importlib.import_module(f"{ports.__name__}.{info.name}"))

    return frozenset(
        member.__name__
        for module in package_modules
        for member in vars(module).values()
        if inspect.isclass(member)
        and getattr(member, "_is_protocol", False)
        and member.__module__ == module.__name__
    )


def _the_amendment_block() -> str:
    """ADR-0003's point 5 amendment, raw, asserted to be exactly one block.

    Raw rather than collapsed because the caller reads table *rows*, and
    collapsing flattens the newlines that separate them into a single line.

    Zero and many fail differently on purpose. Zero means the register sentence
    is gone -- the amendment was reverted or rewritten past its own subject --
    and every assertion downstream would be passing over nothing. Many means the
    key stopped identifying one block, so the rows read would be whichever copy
    came first.
    """
    assert _REGISTER_SENTENCE.lower() == _REGISTER_SENTENCE, (
        "the register key carries a capital and is matched against lowercased "
        "prose, so it can never match however intact the ADR is"
    )
    blocks = [
        block
        for block in re.split(r"\n[ \t]*\n", ADR_0003.read_text(encoding="utf-8"))
        if _REGISTER_SENTENCE in collapsed(block)
    ]

    assert len(blocks) == 1, (
        f"ADR-0003 does not carry {_REGISTER_SENTENCE!r} in exactly one block: found "
        f"{len(blocks)}. Point 5's amendment is what names the collection the closed "
        f"port set is closed *over*; without it the point is a bare count again, which "
        f"is the drift #140 was filed for."
    )
    return blocks[0]


def test_port_set_is_closed() -> None:
    """Adding a port is an architecture decision, not a refactor.

    If this fails, either an ADR authorised the change and this list should be
    updated, or someone added an abstraction the design did not ask for.
    """
    assert {p.__name__ for p in ports.ALL_PORTS} == EXPECTED_PORTS


def test_every_protocol_under_ports_is_registered_or_recorded_as_outside_it() -> None:
    """A Protocol can be declared here, injected, and reach no check at all (#140).

    :func:`test_port_set_is_closed` compares names drawn *from* ``ALL_PORTS``, so
    it is structurally blind to a Protocol that never reaches the tuple: the
    register is the population it iterates, not the population it checks. This is
    its complement, and the pair is what makes ADR-0003 point 5's "the port set is
    closed" enforceable.

    The gap is measured, not hypothetical. ``McpClientConfig`` sat outside the
    register unnoticed; ``IndexStore`` reached it only in Milestone 6, before
    which every parametrised check here had been silently vacuous for it; and
    ``IndexBuildSession`` landed outside it in #329 while #140's own amendment was
    being written, taking the declared count from 19 to 20 with nothing to fail.

    **Both figures are recomputed, and the assertion is on membership.** A pin on
    the two sizes would go green for the wrong tree the moment one Protocol joined
    the register as another left it. The difference is asserted by *name*, so
    ``EXPECTED_OUTSIDE_THE_REGISTER`` has to be edited -- and its per-member
    reason written -- before a new declaration passes.
    """
    declared = _protocols_declared_under_ports()
    registered = frozenset(port.__name__ for port in ports.ALL_PORTS)

    assert registered <= declared, (
        f"the Protocol walk did not find {sorted(registered - declared)}, which "
        f"`ALL_PORTS` holds. The walk is the population every assertion below is a "
        f"difference against, so a narrowed one reports ports as absent and hides "
        f"whatever is genuinely outside the register."
    )
    assert declared - registered == EXPECTED_OUTSIDE_THE_REGISTER, (
        f"the Protocols declared under `domain/ports/` but absent from `ALL_PORTS` are "
        f"{sorted(declared - registered)}, and this pin records "
        f"{sorted(EXPECTED_OUTSIDE_THE_REGISTER)} "
        f"({len(declared)} declared, {len(registered)} registered). A Protocol here is "
        f"outside every check keyed to the register, so it is either a port -- add it to "
        f"`ALL_PORTS` with the ADR point 5 requires -- or it is deliberately outside, in "
        f"which case record why, both here and in ADR-0003's point 5 table."
    )


def test_adr_0003_names_the_register_and_every_protocol_outside_it() -> None:
    """The record has to move when the register does, or it silently goes false.

    The prose half of the pin above, and the direction that matters for a reader:
    point 5's amendment is where someone learns *which collection* the closed port
    set is closed over, and which Protocols sit outside it on purpose. A
    declaration added under ``domain/ports/`` and left out of that table leaves
    this ADR asserting a membership the code no longer has -- exactly how point 5
    came to say "fourteen" while the register held seventeen.

    Two things are held, and neither is the block byte-for-byte: a pin on wording
    fails on a comma and gets updated without being read.

    1. The **register sentence** survives. Located by it rather than merely
       searched for, so a revert to a bare count fails at the locator naming what
       is missing.
    2. The table names **exactly** the live outside-the-register set, recomputed
       rather than restated. A member missing means the ADR under-reports; an
       extra means it names a Protocol that has since joined the register or been
       deleted, and a reader would go looking for it.

    The count sentence above the table is deliberately *not* pinned. It is written
    as a dated measurement at a named commit, so it stays true as history; pinning
    it would demand rewriting a record rather than correcting a claim.
    """
    declared = _protocols_declared_under_ports()
    outside = declared - frozenset(port.__name__ for port in ports.ALL_PORTS)

    named = frozenset(
        match.group(1)
        for line in _the_amendment_block().splitlines()
        if (match := _OUTSIDE_TABLE_ROW.match(line)) is not None
    )

    assert named == outside, (
        f"ADR-0003's point 5 amendment names {sorted(named)} as the Protocols outside "
        f"`ALL_PORTS`, and the code declares {sorted(outside)}. The amendment is the "
        f"record of which Protocols are deliberately outside the register and why, so "
        f"a row is owed for each addition -- with its standing -- and a row that no "
        f"longer matches a declaration sends a reader after something that is not there."
    )


def test_all_ports_is_exported_and_consistent() -> None:
    assert len(ports.ALL_PORTS) == len(EXPECTED_PORTS), "duplicate entry in ALL_PORTS"
    for port in ports.ALL_PORTS:
        assert port.__name__ in ports.__all__


@pytest.mark.parametrize("port", ports.ALL_PORTS, ids=lambda p: p.__name__)
def test_port_is_a_protocol(port: type) -> None:
    """A port must be a Protocol, never an ABC or a concrete class.

    An ABC forces adapters to inherit from a domain class, which points the
    dependency arrow the wrong way -- the exact inversion ADR-0003 exists to
    prevent.
    """
    assert getattr(port, "_is_protocol", False), (
        f"{port.__name__} is not a Protocol. Adapters must satisfy ports "
        "structurally, never by inheritance."
    )


@pytest.mark.parametrize("port", ports.ALL_PORTS, ids=lambda p: p.__name__)
def test_port_is_runtime_checkable(port: type) -> None:
    """Composition roots verify an adapter satisfies its port at wiring time."""
    assert getattr(port, "_is_runtime_protocol", False), f"{port.__name__} needs @runtime_checkable"


@pytest.mark.parametrize("port", ports.ALL_PORTS, ids=lambda p: p.__name__)
def test_port_documents_itself(port: type) -> None:
    """A port is a contract someone else implements from the outside.

    Its docstring is the specification an adapter author works from, so an
    undocumented port is an unspecified contract.
    """
    assert port.__doc__ and port.__doc__.strip(), f"{port.__name__} has no docstring"


@pytest.mark.parametrize("port", ports.ALL_PORTS, ids=lambda p: p.__name__)
def test_port_declares_at_least_one_member(port: type) -> None:
    members = [name for name in vars(port) if not name.startswith("_")]
    assert members, f"{port.__name__} declares no members"


@pytest.mark.parametrize("port", ports.ALL_PORTS, ids=lambda p: p.__name__)
def test_port_methods_are_annotated(port: type) -> None:
    """Unannotated parameters mean mypy cannot verify an adapter conforms.

    Type checking is the mechanism that keeps adapters and ports in sync; a
    missing annotation is a hole in it.
    """
    unannotated: list[str] = []
    for name, member in vars(port).items():
        if name.startswith("_") or not callable(member):
            continue
        signature = inspect.signature(member)
        for parameter in signature.parameters.values():
            if parameter.name in {"self", "cls"}:
                continue
            if parameter.annotation is inspect.Parameter.empty:
                unannotated.append(f"{name}({parameter.name})")
        if signature.return_annotation is inspect.Signature.empty:
            unannotated.append(f"{name} -> ?")

    assert not unannotated, f"{port.__name__} has unannotated members: {unannotated}"


@pytest.mark.parametrize("port", ports.ALL_PORTS, ids=lambda p: p.__name__)
def test_port_has_no_implementation(port: type) -> None:
    """A Protocol method body is `...`.

    A port carrying real logic is a base class wearing a Protocol's name, and
    every adapter would inherit behaviour the domain did not intend to specify.
    """
    with_bodies: list[str] = []
    for name, member in vars(port).items():
        if name.startswith("_") or not callable(member):
            continue
        try:
            source = textwrap.dedent(inspect.getsource(member))
        except (OSError, TypeError):  # pragma: no cover - not source-backed
            continue

        # Parsed rather than string-split: a signature contains colons and
        # newlines of its own, so any lexical shortcut here reads part of the
        # signature as the body.
        definition = ast.parse(source).body[0]
        assert isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef)

        statements = [
            node
            for node in definition.body
            # A docstring is a bare string expression; `...` is a bare constant.
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        if statements:
            with_bodies.append(name)

    assert not with_bodies, (
        f"{port.__name__} has method bodies: {with_bodies}. A port declares a "
        "contract; it does not implement one."
    )


def test_determinism_ports_are_present() -> None:
    """`Clock` and `IdGenerator` are ports for a specific reason.

    Time and ULIDs are inputs to the state hash. Without controlling them,
    "the same migrations produce the same canonical state" (ADR-0007) cannot be
    asserted in a test, which would make that ADR unverifiable rather than
    merely untested.
    """
    assert ports.Clock in ports.ALL_PORTS
    assert ports.IdGenerator in ports.ALL_PORTS


def test_ports_do_not_import_infrastructure() -> None:
    """The dependency rule, checked at the module level for this package."""
    module_names = [name for name in dir(ports) if not name.startswith("_")]
    for name in module_names:
        obj = getattr(ports, name)
        module = getattr(obj, "__module__", "")
        assert not module.startswith("theurian.infrastructure"), (
            f"{name} is defined in infrastructure, not in the domain"
        )


def test_protocols_are_not_instantiable() -> None:
    """Guards against a Protocol accidentally becoming concrete."""
    for port in ports.ALL_PORTS:
        with pytest.raises(TypeError):
            port()


def test_the_git_trailer_source_satisfies_the_review_finding_source_port() -> None:
    """The concrete git adapter satisfies :class:`ReviewFindingSource` structurally.

    The port is ``@runtime_checkable``, and composition roots verify an adapter at
    wiring time. This asserts the check passes for the real adapter, so a rename of
    its ``load_findings`` -- which mypy and the adapter's own tests would not catch,
    because nothing else calls it by name yet -- fails here instead of silently at a
    future injection site.
    """
    assert isinstance(GitTrailerFindingSource(Path("/nonexistent")), ReviewFindingSource)


def test_the_fake_review_finding_source_satisfies_its_port() -> None:
    """The fake satisfies the same port -- making ``fakes.__init__``'s claim true.

    ``fakes/__init__.py`` states "a conformance test asserts it" for every fake.
    Until now no test exercised ``FakeReviewFindingSource``, so a renamed
    ``load_findings`` on the fake passed both mypy and pytest -- the fake could
    drift from the port it stands in for. This is that conformance check: the fake
    must satisfy :class:`ReviewFindingSource` structurally, empty and populated.
    """
    assert isinstance(FakeReviewFindingSource(), ReviewFindingSource)


def test_typing_protocol_is_the_base() -> None:
    """Every port derives from `typing.Protocol`, not a local base class.

    A shared local base would be an ABC in disguise: adapters would have to
    inherit it, which is the dependency inversion ADR-0003 exists to prevent.
    """
    for port in ports.ALL_PORTS:
        bases = [base.__name__ for base in port.__mro__]
        assert "Protocol" in bases, f"{port.__name__} is not a typing.Protocol"
        assert "ABC" not in bases, f"{port.__name__} is an ABC, not a Protocol"
