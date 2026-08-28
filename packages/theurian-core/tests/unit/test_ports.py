"""The port set (ADR-0003).

Ports are the substitution points that make ADR-0003, ADR-0009, and the
cloud-ready design in ADR-0014 possible. A port that quietly acquires a concrete
implementation, or a base class an adapter must inherit, silently inverts the
dependency the whole layering exists to protect.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
from fakes import FakeReviewFindingSource

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


def test_port_set_is_closed() -> None:
    """Adding a port is an architecture decision, not a refactor.

    If this fails, either an ADR authorised the change and this list should be
    updated, or someone added an abstraction the design did not ask for.
    """
    assert {p.__name__ for p in ports.ALL_PORTS} == EXPECTED_PORTS


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
