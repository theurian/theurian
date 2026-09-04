"""Ports: the contracts the domain requires of the outside world (ADR-0003).

Every port is a :class:`typing.Protocol`, so adapters satisfy it structurally
and never import a domain base class. That is what keeps the dependency arrow
pointing inward.

The port set is closed, and :data:`ALL_PORTS` below **is** that set -- the
register ADR-0003 point 5's Milestone 7 amendment names. Adding an entry
requires an ADR; the constraint exists to prevent the "interface for everything"
failure mode §34 of the brief rules out. A ``Protocol`` declared in this package
but absent from :data:`ALL_PORTS` is outside the closed set *and* outside every
check keyed to it, which is a quieter failure than being listed wrongly. Such
Protocols exist, and the amendment names each with why it is outside -- held to
the live set by ``test_ports.py``'s
``test_adr_0003_names_the_register_and_every_protocol_outside_it``. No count is
given here on purpose: a number in a docstring is a snapshot, and this one had
already moved before the sentence stating it was a day old.

Every port must ship a deterministic fake. A port without one is not finished,
because it cannot be exercised offline (OSS-15). Fakes live in ``tests/fakes/``.

**The fake rule above is an intention, not an enforced one, and the gap is
wide.** No test asserts it. Determined structurally -- ``isinstance`` against
each runtime-checkable port -- the doubles in ``tests/fakes/`` cover these
entries of :data:`ALL_PORTS` and no others: ``Clock`` (``FrozenClock``),
``IdGenerator`` (``SeededIdGenerator``), ``DaemonManager`` (``FakeService``)
and ``ReviewFindingSource`` (``FakeReviewFindingSource``). **Every other entry
has no double**, which is most of them. Said plainly here because the sentence
above reads as a guarantee, and a reader who takes it for one concludes that
any port in this list can be swapped out offline.

Named rather than counted, for the reason the paragraph above gives: a
membership claim stays true when the register grows, while "four of seventeen"
and "the other thirteen" are both falsified by a port that has nothing to do
with fakes.

Do not re-answer that by name search. ``isinstance`` is the key because a name
search is wrong in both directions: it misses ``FrozenClock`` when looking for
``Clock``, and it hits ``fakes/pages.py`` when looking for ``IndexStore``,
which builds ``RetrieverPage`` helpers and defines no ``IndexStore`` double.
Membership re-measured on 2026-09-04, on the branch of
https://github.com/theurian/theurian/pull/534.
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
