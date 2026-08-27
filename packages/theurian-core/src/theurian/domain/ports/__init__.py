"""Ports: the contracts the domain requires of the outside world (ADR-0003).

Every port is a :class:`typing.Protocol`, so adapters satisfy it structurally
and never import a domain base class. That is what keeps the dependency arrow
pointing inward.

The port set is closed. Adding one requires an ADR -- the constraint exists to
prevent the "interface for everything" failure mode §34 of the brief rules out.

Every port must ship a deterministic fake. A port without one is not finished,
because it cannot be exercised offline (OSS-15). Fakes land in ``tests/fakes/``
alongside the first real adapter in Milestone 1; until then
``tests/unit/test_ports.py`` checks the properties that are checkable today --
that the set is closed, and that each entry is a genuine Protocol.

**The fake rule above is an intention, not an enforced one, and the gap is
wide.** No test asserts it, and by ``rg <PortName> packages/theurian-core/tests/
fakes/`` only three of the fifteen ports below are named there at all: ``Clock``
(``FrozenClock``), ``IdGenerator`` (``SeededIdGenerator``) and ``DaemonManager``
(``FakeService``). The other twelve are exercised by stand-ins that live beside
the tests that need them. Said plainly here because the sentence above reads as a
guarantee, and a reader who takes it for one concludes that any port they find in
this list can be swapped out offline.
"""

from theurian.domain.ports.authorization import AuthorizationProvider
from theurian.domain.ports.canonical_store import CanonicalStore
from theurian.domain.ports.daemon_manager import (
    DaemonManager,
    ServiceState,
    ServiceStatus,
)
from theurian.domain.ports.determinism import Clock, IdGenerator
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ports.index_store import IndexStore
from theurian.domain.ports.object_store import ObjectStore
from theurian.domain.ports.reranking import RerankingProvider, ScoredCandidate
from theurian.domain.ports.review_finding_source import ReviewFindingSource
from theurian.domain.ports.review_finding_store import ReviewFindingStore
from theurian.domain.ports.review_provider import ReviewProvider
from theurian.domain.ports.secret_store import SecretStore
from theurian.domain.ports.source_parser import NormalizedDocument, SourceParser
from theurian.domain.ports.specification_provider import SpecificationProvider
from theurian.domain.ports.summarization import SummarizationProvider
from theurian.domain.ports.vector_store import VectorMatch, VectorStore

#: The closed port set, used by the test that asserts every port has a fake.
ALL_PORTS: tuple[type, ...] = (
    AuthorizationProvider,
    CanonicalStore,
    Clock,
    DaemonManager,
    EmbeddingProvider,
    IdGenerator,
    IndexStore,
    ObjectStore,
    RerankingProvider,
    ReviewFindingSource,
    ReviewFindingStore,
    ReviewProvider,
    SecretStore,
    SourceParser,
    SpecificationProvider,
    SummarizationProvider,
    VectorStore,
)

__all__ = [
    "ALL_PORTS",
    "AuthorizationProvider",
    "CanonicalStore",
    "Clock",
    "DaemonManager",
    "EmbeddingProvider",
    "IdGenerator",
    "IndexStore",
    "NormalizedDocument",
    "ObjectStore",
    "RerankingProvider",
    "ReviewFindingSource",
    "ReviewFindingStore",
    "ReviewProvider",
    "ScoredCandidate",
    "SecretStore",
    "ServiceState",
    "ServiceStatus",
    "SourceParser",
    "SpecificationProvider",
    "SummarizationProvider",
    "VectorMatch",
    "VectorStore",
]
