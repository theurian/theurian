"""Non-interference by generation, not by enumeration (SEC-13, T-15, T-17, issue #29).

"No published value varies with content the caller may not read" is a statement
about *pairs* of executions -- a 2-safety property, and the standard name for it
is non-interference. No single run can be inspected for it, which is why
Milestone 5 spent eight review rounds discovering one observable at a time: a
field, then a duration, then a statistic computed over rows the gate had removed,
then another tool's response, then an error that fires for one input and not
another.

Round four's answer was **self-composition**: run the pair, and compare. That is
``test_a_withheld_document_changes_nothing_a_caller_can_see`` in
``test_mcp_tools.py``, and it works. It runs against three fixed corpora, so it
covers the shapes someone thought to write -- and three of Milestone 5's
residuals were found only because a reviewer happened to pick a corpus that
exhibited them.

This module generates the pair instead. The corpus, the withheld set, the query
and the caller's parameters are all drawn by ``hypothesis``, the two projects are
built through the real application layer, and the **whole response dict** is
compared. It replaces "did a reviewer think of this field" with "did N generated
pairs separate the responses".

What is compared, and what is held equal
----------------------------------------
Every key of the ``knowledge.search`` response, with **nothing masked**. That is
possible because the three values a two-project comparison would otherwise have
to exclude are held equal as *inputs* rather than excluded as outputs:

``projectId``
    both projects are registered under one id, in two separate registries;
``snapshotId``, ``indexBuildId``
    both are declared by :func:`_build_project` rather than derived from content.

**So this module says nothing about those three.** In the shipped product a state
hash covers the whole working tree (ADR-0016) and therefore does move with
withheld content; what makes that acceptable is that it cannot move with the
*query*, which is
``test_the_build_identity_a_search_reports_does_not_vary_with_the_query``'s job
and not this file's. Stated here because a value held constant looks exactly like
a value that was checked.

The three shapes generated, and why each exists
------------------------------------------------
Every generated pair differs **only** in content the caller may not read: one to
three ``draft`` items, indexed (the index is built with ``include_unapproved``)
and withheld at query time (the search is not). The visible halves are byte
identical, which :func:`test_the_two_projects_differ_only_in_the_withheld_bodies`
asserts rather than assumes.

``shared filler``
    the withheld draft matches the query in *both* projects and differs only in
    its payload. A gate that publishes a withheld row is caught here, because the
    payload differs.
``shared filler, and the query names the secret``
    as above, plus the query carries the probe's payload -- the extraction shape
    Milestone 5 measured at 257, then 203, then 442 calls per credential.
``payload-only filler``
    the withheld draft matches the query in the probe **only**, so the probe's
    retrievers see one more withheld row than the control's. This is the shape
    that catches a candidate slot, a count or a token total spent on a row that
    never becomes a result -- four of the five faces in the table in
    :mod:`theurian.application.retrieval_service`.

The payloads are **one character apart**, which is the attack rather than a
random pair: guess a character, ask, keep it if a number moves.

The blind spot, named
---------------------
**Presence is not tested here, and it is not safe.** These pairs vary a withheld
document's *content* and whether it *matches*; they never vary whether it is in
the index at all, because that is broken today and accepted:
:func:`test_a_withheld_draft_still_changes_which_document_a_caller_is_handed`
pins the breakage, with a corpus this module's own generator found. Read
*Where the equality is conditional* in
:mod:`theurian.application.retrieval_service` for the mechanism, T-17a in the
threat model for the acceptance, and issue #15 for the fix.

Two further things this file does not reach, so nobody has to rediscover them:

- **Durations.** Every published *value* is compared; how long the call took is
  not. See ``FIRST_PASS_DEPTH`` in
  :mod:`theurian.application.retrieval_service`, which records that residual and
  why it follows from the loop's definition. The falsifiable part of it is a pass
  count, and ``test_the_second_pass_arrives_at_fifty_withheld_rows_and_not_before``
  (``tests/unit/test_retrieval_depth.py``) already holds it deterministically.
- **``rejected`` items.** :func:`~theurian.domain.enums.may_surface` refuses them
  under every flag, so :class:`~theurian.application.index_builder.IndexBuilder`
  never writes one and there is no withheld row for a pair to differ by.
  :func:`test_a_rejected_item_is_never_written_into_the_index` asserts that
  premise, because the whole argument rests on it.

Why the corpora are built without the CLI
-----------------------------------------
``test_mcp_tools.py``'s ``three_indexes`` costs 2.9 s per corpus through ``git
init``, ``migrate apply`` and ``index build``. A generated test builds a pair per
example, so it goes through the application layer instead -- a real SQLite
canonical store, a real index build, a real embedder, and the real MCP tool
dispatch. What is skipped is the migration engine and the CLI, neither of which
takes part in answering a query.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError

from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import ProjectPaths, ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRevision,
    RevisionMetadata,
    SourceAnchor,
)
from theurian.domain.project import Project
from theurian.domain.state import ActiveState, StateHash
from theurian.domain.values import MARKDOWN, ContentHash, ValidityPeriod
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.connection import create_database, write_transaction
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter
from theurian.mcp.tools import MAX_BUDGET_TOKENS, MAX_RESULTS

pytestmark = pytest.mark.integration

#: The visible corpus and every query term are built from these, and the letters
#: are the load-bearing part: **a to o only**.
#:
#: The two indexes in a pair must agree on every FTS5 collection statistic that
#: reaches a *visible* row, or a separation would be BM25 arithmetic rather than
#: a leak (T-17a). Both writing systems this product indexes fold case --
#: ``unicode61 remove_diacritics 2`` for words, ``trigram`` for substrings -- so
#: "disjoint" has to hold after folding. Splitting the alphabet at ``o`` gives
#: that by construction: no payload below can produce a token or a trigram that
#: any visible row also carries, whatever either side generates.
#:
#: :func:`test_the_two_alphabets_cannot_produce_a_shared_token_or_trigram` is
#: what holds it, because a single word added here with a ``p``-``z`` letter in
#: it would turn every equality below into a measurement of SQLite's arithmetic.
VOCABULARY: Final = (
    "cache",
    "manifold",
    "headline",
    "beacon",
    "backend",
    "domain",
    "handle",
    "logical",
    "machine",
    "combine",
    "median",
    "nominal",
    "chained",
    "callback",
    "flagged",
    "mileage",
)

#: The other half of the split. Upper case only for legibility in a failure --
#: FTS5 folds it to ``p``-``z``, which is what the disjointness rests on.
PAYLOAD_ALPHABET: Final = "PQRSTUVWXYZ"

#: One id, both projects. Held equal so ``projectId`` needs no mask.
PROJECT_ID: Final = "absence-pair"

#: Declared, never derived -- see the module docstring on what that costs.
#: `BD`, not `BU`: Crockford base32 has no U, and `tests/unit/test_test_fixtures.py`
#: is what catches the readable spelling.
INDEX_BUILD_ID: Final = "01K1BDAAAA01234567890ABCDE"
STATE_HASH: Final = StateHash(ContentHash("a" * 64))
MIGRATION_ID: Final = MigrationId("01K1MGAAAA01234567890ABCDE")

#: How far the run instant sits from a day boundary of ``created_at``.
#:
#: ``ageDays`` is ``(now - created_at).days`` with ``now`` read per request
#: (:func:`theurian.mcp.results.result_payload`), so a pair whose two calls
#: straddle a boundary reports two different ages for one document and fails for
#: a reason that is not a leak. Anchoring the corpus half a day off the run
#: instant makes that impossible for any run shorter than twelve hours, rather
#: than improbable.
AGE_OFFSET: Final = timedelta(days=3, hours=12)

#: An id no generated corpus can mint, for the "absent" arm of the
#: ``knowledge.get`` comparison.
NO_SUCH_ITEM: Final = "architecture.no-such-item"


# ---------------------------------------------------------------------------
# The generated case
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Document:
    """One knowledge item, as this module writes it."""

    item_id: str
    revision_id: str
    title: str
    body: str
    status: KnowledgeStatus


@dataclass(frozen=True, slots=True)
class _Case:
    """One generated pair, before either project exists.

    Everything here is shared by the two projects except :attr:`payloads`, whose
    first element goes to the probe and second to the control. That is the whole
    of the difference between them, and it lives in ``draft`` items neither
    caller may read.
    """

    visible: tuple[_Document, ...]
    #: Body of each withheld draft, minus its payload. Identical in both
    #: projects, so every collection statistic it contributes is identical too.
    withheld_filler: tuple[str, ...]
    withheld_titles: tuple[str, ...]
    #: ``(probe, control)`` per withheld draft: equal length, one character
    #: apart, drawn from the alphabet no visible row can produce.
    payloads: tuple[tuple[str, str], ...]
    query: str
    limit: int
    max_tokens: int
    use_dense: bool

    def withheld(self, *, secret: bool) -> tuple[_Document, ...]:
        """The withheld drafts as one side of the pair writes them."""
        return tuple(
            _Document(
                item_id=f"architecture.withheld-{index:02d}",
                revision_id=_ulid("WH", index),
                title=self.withheld_titles[index],
                body=f"{filler} {pair[0] if secret else pair[1]}",
                status=KnowledgeStatus.DRAFT,
            )
            for index, (filler, pair) in enumerate(
                zip(self.withheld_filler, self.payloads, strict=True)
            )
        )

    def documents(self, *, secret: bool) -> tuple[_Document, ...]:
        return (*self.visible, *self.withheld(secret=secret))

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(pair[0] for pair in self.payloads)


def _ulid(tag: str, number: int) -> str:
    """A deterministic ULID, so both projects mint identical ids.

    Ties in these corpora break on chunk id, and a chunk id is
    ``<revision ULID>#<ordinal>``. Two projects disagreeing about these would
    order identically-scoring rows differently for an honest reason, and every
    equality below would be measuring that instead.

    ``tag`` is Crockford base32: no ``I``, ``L``, ``O`` or ``U``.
    """
    return f"01K1{tag}{number:03d}".ljust(26, "0")[:26]


_WORD = st.sampled_from(VOCABULARY)


def _sentence() -> st.SearchStrategy[str]:
    return st.lists(_WORD, min_size=4, max_size=14).map(lambda words: " ".join(words) + ".")


def _prose() -> st.SearchStrategy[str]:
    return st.lists(_sentence(), min_size=1, max_size=4).map("\n\n".join)


def _payload_pair() -> st.SearchStrategy[tuple[str, str]]:
    """Two payloads of one length, differing in exactly one character.

    One character apart on purpose. A random unrelated pair is the easy case; the
    attack Milestone 5 measured guesses a character at a time and keeps it if a
    published number moves, so the pair that has to be indistinguishable is the
    pair that is nearly the same.

    Never equal, and not by filtering: the replacement is a non-zero rotation
    within the alphabet, so a difference exists by construction and no example is
    ever discarded for failing to have one.
    """

    def apart(parts: tuple[str, int, int]) -> tuple[str, str]:
        secret, position, shift = parts
        replacement = PAYLOAD_ALPHABET[
            (PAYLOAD_ALPHABET.index(secret[position]) + shift) % len(PAYLOAD_ALPHABET)
        ]
        return secret, secret[:position] + replacement + secret[position + 1 :]

    return st.integers(min_value=6, max_value=20).flatmap(
        lambda size: st.tuples(
            st.text(alphabet=PAYLOAD_ALPHABET, min_size=size, max_size=size),
            st.integers(min_value=0, max_value=size - 1),
            st.integers(min_value=1, max_value=len(PAYLOAD_ALPHABET) - 1),
        ).map(apart)
    )


def _visible_documents() -> st.SearchStrategy[tuple[_Document, ...]]:
    """An approved corpus, sized across the candidate-depth boundary.

    Up to sixty documents because :data:`CANDIDATE_DEPTH` is fifty. A pair whose
    corpora both fit inside one retriever's depth cannot tell a depth loop that
    counts visible rows from one that counts raw ones -- which is the fourth face
    in :mod:`theurian.application.retrieval_service`'s table and the one that
    recovered a credential at the default budget.
    """
    document = st.tuples(st.lists(_WORD, min_size=1, max_size=3).map(" ".join), _prose())
    return st.lists(document, min_size=2, max_size=60).map(
        lambda pairs: tuple(
            _Document(
                item_id=f"architecture.visible-{index:02d}",
                revision_id=_ulid("VS", index),
                title=title,
                body=body,
                status=KnowledgeStatus.APPROVED,
            )
            for index, (title, body) in enumerate(pairs)
        )
    )


def _cases() -> st.SearchStrategy[_Case]:
    """One generated pair, in one of the three shapes the module docstring names.

    Built in one ``flatmap`` because two of the guarantees are relational and
    cannot be stated on an independent strategy:

    - **the query matches the visible corpus.** Its terms are sampled from the
      words this corpus actually contains, so ``count > 0`` is structural rather
      than hoped for. It is still asserted -- see :func:`_assert_the_pair_bites`.
    - **the probe's withheld draft is reachable by that query.** When the filler
      shares the corpus vocabulary, the query's own terms are appended to it;
      when it does not, the query is made to carry the probe's payload. Either
      way there is a withheld candidate for the gate to withhold.
    """

    def with_query(visible: tuple[_Document, ...]) -> st.SearchStrategy[_Case]:
        words = sorted({word for doc in visible for word in doc.body.replace(".", "").split()})
        return st.builds(
            _assemble,
            visible=st.just(visible),
            terms=st.lists(st.sampled_from(words), min_size=1, max_size=3),
            fillers=st.lists(st.tuples(_prose(), _payload_prose()), min_size=1, max_size=3),
            titles=st.lists(_payload_title(), min_size=3, max_size=3),
            payloads=st.lists(_payload_pair(), min_size=3, max_size=3),
            shares_vocabulary=st.booleans(),
            names_the_secret=st.booleans(),
            limit=st.sampled_from((1, 3, 10, MAX_RESULTS)),
            max_tokens=st.sampled_from((2_000, 8_000, MAX_BUDGET_TOKENS)),
            use_dense=st.booleans(),
        )

    return _visible_documents().flatmap(with_query)


def _payload_prose() -> st.SearchStrategy[str]:
    """Filler a visible row can share no token and no trigram with."""
    word = st.text(alphabet=PAYLOAD_ALPHABET, min_size=3, max_size=9)
    return st.lists(
        st.lists(word, min_size=4, max_size=14).map(lambda words: " ".join(words) + "."),
        min_size=1,
        max_size=4,
    ).map("\n\n".join)


def _payload_title() -> st.SearchStrategy[str]:
    """A withheld draft's title, drawn from the alphabet no visible row uses.

    Written this way so that
    :func:`test_no_withheld_payload_appears_anywhere_a_caller_reads` can look for
    the title as a string. Drawn from :data:`VOCABULARY` it could not: a withheld
    draft titled ``cache`` is indistinguishable from a visible document titled
    ``cache``, and the search for it reports the visible document's own title as
    a leak. That is the oracle trap in miniature -- a marker the caller is
    entitled to read comes back carrying the marker.

    Identical in both projects, like every other part of a withheld draft except
    its payload, so it moves no collection statistic between them.
    """
    return st.lists(
        st.text(alphabet=PAYLOAD_ALPHABET, min_size=3, max_size=8), min_size=1, max_size=2
    ).map(" ".join)


def _assemble(  # noqa: PLR0913 - one parameter per generated knob
    *,
    visible: tuple[_Document, ...],
    terms: list[str],
    fillers: list[tuple[str, str]],
    titles: list[str],
    payloads: list[tuple[str, str]],
    shares_vocabulary: bool,
    names_the_secret: bool,
    limit: int,
    max_tokens: int,
    use_dense: bool,
) -> _Case:
    """Turn the generated knobs into a pair, resolving the one dependency.

    ``names_the_secret`` is forced true when the filler shares no vocabulary,
    because otherwise the withheld draft matches the query in neither project and
    the pair exercises nothing. That is the fourth combination of two booleans,
    and it is removed here rather than filtered away, so no example is ever
    silently discarded.
    """
    chosen = payloads[: len(fillers)]
    shared_terms = " ".join(terms)
    filler = tuple(
        f"{prose} {shared_terms}." if shares_vocabulary else payload_prose
        for prose, payload_prose in fillers
    )
    query_terms = [*terms]
    if names_the_secret or not shares_vocabulary:
        query_terms.append(chosen[0][0])
    return _Case(
        visible=visible,
        withheld_filler=filler,
        withheld_titles=tuple(titles[: len(fillers)]),
        payloads=tuple(chosen),
        query=" ".join(query_terms),
        limit=limit,
        max_tokens=max_tokens,
        use_dense=use_dense,
    )


# ---------------------------------------------------------------------------
# Building one side of a pair
# ---------------------------------------------------------------------------


def _revision(document: _Document, created_at: datetime) -> KnowledgeRevision:
    return KnowledgeRevision.create(
        revision_id=RevisionId(document.revision_id),
        item_id=ItemId(document.item_id),
        project_id=ProjectId(PROJECT_ID),
        migration_id=MIGRATION_ID,
        title=document.title,
        body=document.body,
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind.ARCHITECTURE,
            namespace="backend",
            status=document.status,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=Sensitivity.INTERNAL,
            owner="platform-team",
        ),
        validity=ValidityPeriod(valid_from=created_at),
        author="engineer@example.com",
        created_at=created_at,
        source_anchors=(
            SourceAnchor(provider="git", source_uri=f"git://demo/{document.item_id}.md"),
        ),
    )


def _item(document: _Document, created_at: datetime) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=ItemId(document.item_id),
        project_id=ProjectId(PROJECT_ID),
        namespace="backend",
        kind=KnowledgeKind.ARCHITECTURE,
        status=document.status,
        current_revision_id=RevisionId(document.revision_id),
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=Sensitivity.INTERNAL,
        validity=ValidityPeriod(valid_from=created_at),
    )


def _build_project(
    root: Path, documents: tuple[_Document, ...], created_at: datetime
) -> ProjectRegistry:
    """One project: a canonical store, an index that holds its drafts, a registry.

    ``include_unapproved`` on the *build* and not on the search is what makes a
    withheld row exist at all: the draft's chunks are in the index file, and
    :class:`~theurian.application.visibility.CanonicalVisibility` withholds them
    from every query that does not ask for drafts.
    """
    paths = ProjectPaths.of(root)
    paths.state.mkdir(parents=True, exist_ok=True)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    database = paths.database_for(STATE_HASH)
    create_database(database, state_hash=str(STATE_HASH), engine_version=1)

    with write_transaction(database, paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        writer.register_project(
            Project(
                project_id=ProjectId(PROJECT_ID),
                root_path=str(root),
                repository_url=None,
                default_branch="main",
                knowledge_directory=PurePosixPath(".theurian"),
                registered_at=created_at,
            )
        )
        for document in documents:
            writer.append_revision(_revision(document, created_at))
            writer.put_item(_item(document, created_at))

    paths.active_pointer.write_text(
        json.dumps(
            ActiveState(
                state_hash=STATE_HASH,
                database_filename=STATE_HASH.database_filename,
                migration_count=1,
                updated_at=created_at.isoformat(),
            ).to_json()
        ),
        encoding="utf-8",
    )
    IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=HashingEmbedding(),
    ).build(
        IndexRequest(
            database=database,
            index_path=paths.index_for(INDEX_BUILD_ID),
            project_id=PROJECT_ID,
            state_hash=str(STATE_HASH),
            index_build_id=INDEX_BUILD_ID,
            include_unapproved=True,
        )
    )
    paths.active_index_pointer.write_text(
        json.dumps(
            {
                "indexBuildId": INDEX_BUILD_ID,
                "stateHash": str(STATE_HASH),
                "projectId": PROJECT_ID,
                "indexesUnapproved": True,
            }
        ),
        encoding="utf-8",
    )

    registry = ProjectRegistry(path=root / "registry" / "projects.json")
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        json.dumps(
            {
                PROJECT_ID: {
                    "projectId": PROJECT_ID,
                    "rootPath": str(root),
                    "knowledgeDirectory": ".theurian",
                    "registeredAt": created_at.isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    return registry


@dataclass(frozen=True, slots=True)
class _Pair:
    """Two projects that differ only in content no caller may read."""

    probe: ProjectRegistry
    control: ProjectRegistry
    case: _Case


def _pair(base: Path, case: _Case) -> _Pair:
    created_at = datetime.now(UTC) - AGE_OFFSET
    return _Pair(
        probe=_build_project(base / "probe", case.documents(secret=True), created_at),
        control=_build_project(base / "control", case.documents(secret=False), created_at),
        case=case,
    )


def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    """Invoke a tool through the same entry point the transport uses."""

    async def invoke() -> Any:
        return await build_server(registry).call_tool(tool, arguments)

    result = asyncio.run(invoke())
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(result.content[0].text)
    return loaded


def _search(registry: ProjectRegistry, case: _Case, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "projectId": PROJECT_ID,
        "query": case.query,
        "limit": case.limit,
        "maxTokens": case.max_tokens,
        "useDense": case.use_dense,
    }
    return _call(registry, "knowledge.search", **{**arguments, **overrides})


def _failing(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    with pytest.raises(SdkToolError) as raised:
        _call(registry, tool, **arguments)
    return str(raised.value)


def _assert_the_pair_bites(pair: _Pair, probe: dict[str, Any]) -> None:
    """Refuse to pass on an example that proved nothing.

    Three ways a generated pair can be green while testing nothing, and each has
    already happened to a hand-written version of this comparison:

    - the answer is empty, so two empty answers are being compared;
    - the withheld draft is not reachable by this query at any depth, so there
      was nothing for the gate to withhold;
    - the payloads are equal, so the two projects are the same project.

    Asserted rather than filtered. ``hypothesis`` will happily generate a corpus
    of one empty document forever, and an example dropped by ``assume`` leaves no
    trace in the run.
    """
    case = pair.case
    assert probe["count"] > 0, "two empty answers prove nothing about withholding"
    assert all(secret != decoy for secret, decoy in case.payloads), (
        "the two projects must actually differ"
    )

    reachable = _search(
        pair.probe,
        case,
        includeUnapproved=True,
        limit=MAX_RESULTS,
        maxTokens=MAX_BUDGET_TOKENS,
    )
    withheld_ids = {document.item_id for document in case.withheld(secret=True)}
    assert withheld_ids & {result["itemId"] for result in reachable["results"]}, (
        "this query must be able to reach a withheld draft when the flag permits "
        "it, or the comparison below is between two answers nothing was hidden from"
    )
    assert not withheld_ids & {result["itemId"] for result in probe["results"]}, (
        "and it must not reach one when the flag does not"
    )


# ---------------------------------------------------------------------------
# The generated equalities
# ---------------------------------------------------------------------------

#: Shared by every generated test here.
#:
#: ``deadline=None`` because one example builds two SQLite databases and two
#: index files; ``derandomize=True`` because a suite that fails on a different
#: example each run cannot be bisected; ``database=None`` because the default
#: example database writes ``.hypothesis/`` into whatever directory pytest was
#: launched from, which for this repository is the repository.
_GENERATED = settings(
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.data_too_large, HealthCheck.too_slow],
)


@settings(_GENERATED, max_examples=25)
@given(case=_cases())
def test_no_published_value_varies_with_a_withheld_document(
    tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    """SEC-13, T-15, FR-R4, FR-R5. The property, over generated pairs.

    One query against two corpora that differ only in ``draft`` bodies the caller
    may not read, and the **entire response** must be equal -- ``count``, every
    field of every hit including which chunk was excerpted, and every key of the
    ``retrieval`` block.

    Nothing is masked, so nothing has to be argued for: the three values a
    two-project comparison would normally exclude are held equal as inputs, and
    the module docstring says what that costs.

    This is the mechanised form of
    ``test_a_withheld_document_changes_nothing_a_caller_can_see``
    (``test_mcp_tools.py``), which asserts the same thing against three fixed
    corpora. The fixed one is not redundant: it runs the real CLI, covers a
    Japanese corpus this generator does not produce, and covers the *stale index*
    shape -- a document approved at build time and retired afterwards -- which
    this file deliberately avoids (see the module docstring on T-17a).
    """
    pair = _pair(tmp_path_factory.mktemp("absence"), case)

    probe = _search(pair.probe, case)
    control = _search(pair.control, case)

    _assert_the_pair_bites(pair, probe)
    assert probe == control, (
        "every published value must equal what the same query returns against a "
        "corpus whose withheld documents say something else"
    )


@settings(_GENERATED, max_examples=25)
@given(case=_cases())
def test_no_withheld_payload_appears_anywhere_a_caller_reads(
    tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    """SEC-13. Equality is not the same claim as absence, and both are wanted.

    Two identical responses can both carry the secret; the comparison above would
    not notice, because it compares the probe against a control that holds a
    *different* secret and would separate on it -- unless the leak is of
    something both projects share. The `title` of a withheld draft is exactly
    that: identical in both projects by construction, so a gate that published it
    would leave the equality green.

    Checked over the serialised response rather than field by field, because a
    field list is the thing this file exists to stop maintaining. ``query`` is
    excluded and only ``query``: a caller who asks for a string is echoed that
    string back, which discloses nothing they did not already hold, and two of
    the three generated shapes deliberately put the probe's payload there.
    """
    pair = _pair(tmp_path_factory.mktemp("absence"), case)

    probe = _search(pair.probe, case)

    _assert_the_pair_bites(pair, probe)
    published = json.dumps({key: value for key, value in probe.items() if key != "query"})
    for secret in case.secrets:
        assert secret not in published, "a withheld payload reached the response"
    for withheld in case.withheld(secret=True):
        assert withheld.title not in published, "so did a withheld document's title"
        assert withheld.item_id not in published, "so did its id"


@settings(_GENERATED, max_examples=15)
@given(case=_cases())
def test_a_withheld_item_is_refused_by_the_same_words_that_refuse_an_absent_one(
    tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    """SEC-13, T-17. The tool that reaches the same content by id.

    Closing every path through ``knowledge.search`` achieves nothing if
    ``knowledge.get`` will hand the document over to anyone who knows its id --
    that is how Milestone 5's fifth face was found, and why
    :func:`theurian.mcp.tools.knowledge_get` answers "withheld" and "absent" with
    one message.

    Generated because "one message" is a claim about *every* id, and a
    hand-written case checks it for the one id someone wrote down. Three arms
    rather than two: the withheld id in the probe, the same id in the control,
    and an id that exists in neither. All three must be the same sentence, so the
    refusal cannot be used to confirm that an item exists.
    """
    pair = _pair(tmp_path_factory.mktemp("absence"), case)
    withheld_id = case.withheld(secret=True)[0].item_id

    from_probe = _failing(pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=withheld_id)
    from_control = _failing(pair.control, "knowledge.get", projectId=PROJECT_ID, itemId=withheld_id)
    absent = _failing(pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=NO_SUCH_ITEM)

    assert from_probe == from_control, "the two corpora must refuse identically"
    assert from_probe == absent.replace(NO_SUCH_ITEM, withheld_id), (
        "and a withheld id must be refused in the words an absent id is refused in"
    )
    assert (
        _call(
            pair.probe,
            "knowledge.get",
            projectId=PROJECT_ID,
            itemId=withheld_id,
            **{"includeUnapproved": True},
        )["itemId"]
        == withheld_id
    ), (
        "the guard on this guard: the id must be fetchable when the flag permits "
        "it, or the three refusals above agree because nothing is there"
    )


# ---------------------------------------------------------------------------
# Guards on the generator itself
# ---------------------------------------------------------------------------


def test_the_two_alphabets_cannot_produce_a_shared_token_or_trigram() -> None:
    """The premise every equality above rests on, checked rather than assumed.

    If a visible row and a withheld payload could share a token or a trigram, a
    generated pair would differ in an FTS5 collection statistic that reaches a
    visible row -- ``nHit``, and through it ``idf`` -- and every separation would
    be T-17a's content channel rather than a gate defect. The tests above would
    then fail for a reason that is not a leak, and the obvious response is to
    relax them.

    Checked after case folding, because both tokenizers this index uses fold:
    ``unicode61 remove_diacritics 2`` and ``trigram`` are both case insensitive,
    so ``Z`` and ``z`` are one token to FTS5.
    """
    visible = {character for word in VOCABULARY for character in word.casefold()}
    payload = set(PAYLOAD_ALPHABET.casefold())

    assert not visible & payload, (
        f"the alphabets overlap on {sorted(visible & payload)}; a payload could "
        f"then change the `nHit` of a term a visible row carries"
    )
    assert len(PAYLOAD_ALPHABET) > 1, "a one-character alphabet cannot make two payloads differ"


def test_a_rejected_item_is_never_written_into_the_index(tmp_path: Path) -> None:
    """Why this file has no ``rejected`` arm, stated as a test rather than a note.

    :func:`~theurian.domain.enums.may_surface` refuses ``rejected`` under every
    flag, so :class:`~theurian.application.index_builder.IndexBuilder` never
    writes one -- which is why a generated pair differing by a rejected item
    would differ in nothing at all, index statistics included, and would pass
    while testing nothing.

    That is a premise of the module docstring's list of blind spots, and a
    premise nothing else in this file could notice breaking. If ``rejected``
    joins ``SURFACEABLE_STATUSES``, or the builder stops consulting
    ``may_surface``, this goes red and the blind spot has to be reopened.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    rejected = _Document(
        item_id="architecture.rejected",
        revision_id=_ulid("RJ", 0),
        title="beacon ledger",
        body="cache ledger kernel beacon backend domain.",
        status=KnowledgeStatus.REJECTED,
    )
    visible = _Document(
        item_id="architecture.visible-00",
        revision_id=_ulid("VS", 0),
        title="handle",
        body="cache ledger kernel beacon backend domain.",
        status=KnowledgeStatus.APPROVED,
    )

    registry = _build_project(tmp_path / "one", (visible, rejected), created_at)

    index = SqliteIndexStore(ProjectPaths.of(tmp_path / "one").index_for(INDEX_BUILD_ID))
    rows = index.search_lexical("ledger", project_id=PROJECT_ID, limit=50, include_unapproved=True)
    assert {row.item_id for row in rows} == {visible.item_id}, (
        "a rejected item must not be in the index under any flag"
    )
    assert (
        _call(
            registry,
            "knowledge.search",
            projectId=PROJECT_ID,
            query="ledger",
            includeUnapproved=True,
        )["count"]
        == 1
    ), "and the tool must not report it either"


@settings(_GENERATED, max_examples=10)
@given(case=_cases())
def test_the_two_projects_differ_only_in_the_withheld_bodies(case: _Case) -> None:
    """The other half of the guard: a separation must be attributable.

    ``probe == control`` is only evidence about withholding if the two corpora
    are identical everywhere a caller may look. Asserted over the generated case
    rather than over the built projects, because this is a property of the
    generator and it is the generator that would drift: an id, a title or a
    length that varied between the two sides would make the equalities above fail
    for an honest reason, and the honest response to that failure is to weaken
    them.

    Byte identity of the visible half, and *shape* identity of the withheld half
    -- same ids, same titles, same fillers, same lengths. The payloads differ,
    and they are the only thing that does.
    """
    probe = case.documents(secret=True)
    control = case.documents(secret=False)
    visible_count = len(case.visible)

    assert probe[:visible_count] == control[:visible_count], "the visible corpora must be identical"
    assert [(d.item_id, d.title, len(d.body)) for d in probe[visible_count:]] == [
        (d.item_id, d.title, len(d.body)) for d in control[visible_count:]
    ], "and the withheld ones must differ in nothing a collection statistic can see"
    assert [d.body for d in probe[visible_count:]] != [d.body for d in control[visible_count:]], (
        "while differing in what they say"
    )
    assert all(d.status is KnowledgeStatus.DRAFT for d in probe[visible_count:])


# ---------------------------------------------------------------------------
# What generation reaches that the equality cannot: T-17a, at the tool surface
# ---------------------------------------------------------------------------

#: A corpus this module's own generator produced, recorded verbatim.
#:
#: Two approved documents and one withheld ``draft``. It is here because it is
#: the thing issue #29 asked generation to find: a residual that a fixed corpus
#: does not contain, and one that is **fragile in exactly the way T-17a
#: predicts**. Measured while reducing it -- shortening the withheld body to one
#: paragraph, or to a single line of the same words, stops it reproducing, and so
#: does deleting the trailing payload (which shortens it). It survives replacing
#: every query term inside the withheld body with a word the query does not use,
#: which is what says the channel here is the length norm and not ``idf``.
#:
#: Kept as data rather than regenerated, because a test whose corpus is redrawn
#: each run reports a different failure each run.
_T17A_LONG_VISIBLE: Final = (
    "chained domain beacon logical callback flagged.\n\n"
    "flagged beacon backend nominal median.\n\n"
    "backend machine beacon flagged logical handle flagged."
)
_T17A_SHORT_VISIBLE: Final = "logical ledger beacon kernel backend machine."
_T17A_WITHHELD: Final = (
    "chained logical combine ledger.\n\n"
    "beacon flagged nominal chained ledger backend combine callback backend iceberg domain.\n\n"
    "chained combine machine callback handle handle combine callback logical combine machine.\n\n"
    "callback nominal iceberg logical median nominal combine domain flagged cache flagged "
    "logical. ZTXVSQVVUURXWZ"
)


def test_a_withheld_draft_still_changes_which_document_a_caller_is_handed(
    tmp_path: Path,
) -> None:
    """T-17a, issue #15. **This test asserts that a leak is present.**

    Two projects with the same two approved documents. One of them also holds a
    ``draft`` that neither caller may read, and that is the only difference. A
    one-word query at ``limit=1`` returns *a different approved document* from
    each -- different id, different title, different excerpt, different
    provenance -- and reports a different ``usedTokens``.

    The gate is not at fault and nothing withheld is published: both answers hold
    one approved document and no draft. What moved is BM25's length
    normalisation, ``k1 * (1 - b + b * D / avgdl)``, whose ``avgdl`` is taken over
    every row in the index including the ones the query never returns. There is
    nothing for a :class:`~theurian.application.visibility.Visibility` to
    intercept, because the arithmetic happens inside SQLite.

    **What this adds to the two tests that already pin T-17a.**
    ``test_a_withheld_document_can_still_reorder_the_visible_ones`` and
    ``test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones``
    (``test_retrieval_service.py``) assert reordering below the tool surface,
    through ``ResultGate`` directly. The first of them says in prose that the
    difference is "reachable through `knowledge.search` with no parameters" and
    nothing measured it. This does: the whole published response, through
    ``server.call_tool``, on a query of one ordinary word. It also shows the
    reach is not confined to *order* -- with ``limit=1`` there is no order to
    permute, and the caller is simply handed a different document.

    Like its two siblings, this goes red when Milestone 6 closes the stale window
    (ADR-0022, issue #15), and it is meant to: whoever makes it stop reproducing
    is the person who should be rewriting the T-17a acceptance in the threat
    model in the same change.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    visible = (
        _Document(
            "architecture.visible-00",
            _ulid("VS", 0),
            "beacon ledger",
            _T17A_LONG_VISIBLE,
            KnowledgeStatus.APPROVED,
        ),
        _Document(
            "architecture.visible-01",
            _ulid("VS", 1),
            "handle",
            _T17A_SHORT_VISIBLE,
            KnowledgeStatus.APPROVED,
        ),
    )
    withheld = _Document(
        "architecture.withheld-00",
        _ulid("WH", 0),
        "domain domain",
        _T17A_WITHHELD,
        KnowledgeStatus.DRAFT,
    )
    holds_it = _build_project(tmp_path / "holds-it", (*visible, withheld), created_at)
    never_did = _build_project(tmp_path / "never-did", visible, created_at)

    from_probe = _call(
        never_did, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=1
    )
    from_control = _call(
        holds_it, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=1
    )

    assert from_probe["count"] == from_control["count"] == 1, "one result each, or this is not it"
    assert {result["status"] for result in from_probe["results"]} == {"approved"}, (
        "the gate must still be withholding the draft from both"
    )
    assert {result["status"] for result in from_control["results"]} == {"approved"}
    assert from_probe["results"][0]["itemId"] != from_control["results"][0]["itemId"], (
        "a withheld draft the caller cannot read decides which approved document "
        "they are handed; if this no longer reproduces, T-17a's acceptance in the "
        "threat model is out of date and should be deleted rather than this test"
    )
    assert from_probe["retrieval"]["usedTokens"] != from_control["retrieval"]["usedTokens"], (
        "and the published cost moves with it"
    )


def test_the_t17a_corpus_still_has_something_to_withhold(tmp_path: Path) -> None:
    """Guards the test above, whose whole meaning is in its corpus.

    "The two answers name different documents" is satisfiable by a corpus that
    has stopped withholding anything at all -- a builder that indexed the draft
    and a gate that published it would produce two different answers too, and
    that is a defect rather than this channel. So the preconditions are asserted:
    the draft is in the probe's index, is reachable by the query, and is in
    neither answer.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    visible = (
        _Document(
            "architecture.visible-00",
            _ulid("VS", 0),
            "beacon ledger",
            _T17A_LONG_VISIBLE,
            KnowledgeStatus.APPROVED,
        ),
        _Document(
            "architecture.visible-01",
            _ulid("VS", 1),
            "handle",
            _T17A_SHORT_VISIBLE,
            KnowledgeStatus.APPROVED,
        ),
    )
    withheld = _Document(
        "architecture.withheld-00",
        _ulid("WH", 0),
        "domain domain",
        _T17A_WITHHELD,
        KnowledgeStatus.DRAFT,
    )
    registry = _build_project(tmp_path / "holds-it", (*visible, withheld), created_at)

    index = SqliteIndexStore(ProjectPaths.of(tmp_path / "holds-it").index_for(INDEX_BUILD_ID))
    indexed = index.search_lexical(
        "backend", project_id=PROJECT_ID, limit=50, include_unapproved=True
    )
    default = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=10)
    with_flag = _call(
        registry,
        "knowledge.search",
        projectId=PROJECT_ID,
        query="backend",
        limit=10,
        includeUnapproved=True,
    )

    assert withheld.item_id in {row.item_id for row in indexed}, (
        "the draft's chunks must be in the index, or there is no withheld row"
    )
    assert withheld.item_id in {result["itemId"] for result in with_flag["results"]}, (
        "and this query must reach it when the flag permits it"
    )
    assert withheld.item_id not in {result["itemId"] for result in default["results"]}, (
        "while the default answer withholds it, which is what makes the flip a "
        "statistics channel rather than a gate failure"
    )


def test_the_state_the_pair_builder_declares_is_the_state_a_search_reports(
    tmp_path: Path,
) -> None:
    """Why ``snapshotId`` and ``indexBuildId`` can be held equal rather than masked.

    The equalities above compare the whole response with nothing excluded, and
    that is only honest if these two really are inputs this file sets. A builder
    that quietly derived either from content would turn "held equal" into "equal
    by accident", and the day it stopped being accidental every generated test
    would fail for a reason that is not a leak.

    Asserted against a single project, because the claim is about the builder and
    not about a pair.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    document = _Document(
        "architecture.visible-00",
        _ulid("VS", 0),
        "handle",
        "cache ledger kernel beacon.",
        KnowledgeStatus.APPROVED,
    )

    registry = _build_project(tmp_path / "one", (document,), created_at)
    answer = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="ledger")

    assert answer["retrieval"]["snapshotId"] == str(STATE_HASH)
    assert answer["retrieval"]["indexBuildId"] == INDEX_BUILD_ID
    assert answer["retrieval"]["indexed"] is True, "the ranked path, not the fallback"
    assert answer["retrieval"]["stale"] is False, (
        "a stale index is the T-17a shape, and these pairs must not be in it"
    )
    assert answer["projectId"] == PROJECT_ID


def test_the_pair_builder_writes_a_canonical_store_the_gate_actually_reads(
    tmp_path: Path,
) -> None:
    """The last way the generated tests could be green while testing nothing.

    Every equality above depends on the canonical store being the authority the
    gate consults. A builder that wrote items with the wrong project id, or left
    ``current_revision_id`` unset, would produce two projects that answer nothing
    for every query -- and ``count > 0`` would catch that, while a builder that
    wrote the *draft* as approved would not: the answers would still be equal,
    because both projects would publish it.

    So the statuses are read back out of the store rather than trusted from the
    generator.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    approved = _Document(
        "architecture.visible-00",
        _ulid("VS", 0),
        "handle",
        "cache ledger kernel.",
        KnowledgeStatus.APPROVED,
    )
    draft = _Document(
        "architecture.withheld-00",
        _ulid("WH", 0),
        "beacon",
        "cache ledger machine.",
        KnowledgeStatus.DRAFT,
    )

    _build_project(tmp_path / "one", (approved, draft), created_at)

    paths = ProjectPaths.of(tmp_path / "one")
    with SqliteCanonicalStore(paths.database_for(STATE_HASH)) as store:
        items = store.list_items(RequestContext(project_id=ProjectId(PROJECT_ID)))
    by_id = {item.item_id.value: item for item in items}

    assert by_id[approved.item_id].status is KnowledgeStatus.APPROVED
    assert by_id[draft.item_id].status is KnowledgeStatus.DRAFT, (
        "the withheld half must really be unapproved in the store the gate asks"
    )
    assert by_id[draft.item_id].current_revision_id is not None, (
        "an item with no current revision is withheld for the wrong reason"
    )
