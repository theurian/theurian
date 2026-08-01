"""Ports: the contracts the domain requires of the outside world (ADR-0003).

Every port is a :class:`typing.Protocol`, so adapters satisfy it structurally
and never import a domain base class. That is what keeps the dependency arrow
pointing inward.

The port set is closed. Adding one requires an ADR -- the constraint exists to
prevent the "interface for everything" failure mode §34 of the brief rules out.

Every port has a deterministic fake in ``tests/fakes/``. A port without a fake is
not finished, because it cannot be exercised offline (OSS-15).
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
from theurian.domain.ports.object_store import ObjectStore
from theurian.domain.ports.reranking import RerankingProvider, ScoredCandidate
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
    ObjectStore,
    RerankingProvider,
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
    "NormalizedDocument",
    "ObjectStore",
    "RerankingProvider",
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
