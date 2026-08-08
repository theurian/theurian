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

This module generates the pair instead. The corpus, the withheld set, the way
each is withheld and the query are drawn by ``hypothesis``; the two projects are
built through the real application layer; and the **whole response dict** is
compared. It replaces "did a reviewer think of this field" with "did N generated
pairs separate the responses".

Two inputs are deliberately **not** generated, because both are exact boundaries
that sampling buries: the corpus size (:func:`_visible_documents`) and the
caller's own parameters (:data:`ARGUMENT_SETS`). Both are measured decisions,
recorded where they are made.

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

Two ways of being withheld, and which code each one exercises
-------------------------------------------------------------
Every generated pair differs **only** in content the caller may not read: one to
three documents the search does not return. The visible halves are byte
identical, which :func:`test_the_two_projects_differ_only_in_the_withheld_bodies`
asserts rather than assumes.

*How* they are withheld is generated, and it is not a detail. The two states are
stopped by different code:

:data:`RETIRED_AFTER_BUILD`
    approved when the index was written, ``deprecated`` afterwards. Its chunks
    carry ``status = 'approved'``, every retriever's ``WHERE`` returns them, and
    :class:`~theurian.application.visibility.CanonicalVisibility` is the only
    thing between the row and the caller. **This is the shape Milestone 5's five
    faces lived in.**
:data:`DRAFT_IN_AN_UNAPPROVED_INDEX`
    a ``draft`` in an index built with ``--include-unapproved``, searched without
    it. Its chunks carry ``status = 'draft'``, so the retrievers' own ``WHERE``
    refuses them and the canonical gate is never asked about them.

The first version of this file built only the second, and it looked identical
from the outside. It is not: deleting the canonical gate outright
(``cleared = tuple(ranked)``) left all ten tests here green while turning 38
parametrisations of ``test_a_withheld_document_changes_nothing_a_caller_can_see``
red. :func:`_assert_the_pair_bites` now reads the index file directly and asserts
which of the two is doing the work, per example.

Across that, three ways for the two corpora to differ:

``shared filler``
    the withheld document matches the query in *both* projects and differs only
    in its payload. A gate that publishes a withheld row is caught here, because
    the payload differs.
``shared filler, and the query names the secret``
    as above, plus the query carries the probe's payload -- the extraction shape
    Milestone 5 measured at 257, then 203, then 442 calls per credential.
``payload-only filler``
    the withheld document matches the query in the probe **only**, so the probe's
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

Three further things this file does not reach, so nobody has to rediscover them:

- **Durations, and this is a decision rather than an omission.** Issue #29 asks
  for a statistical latency test -- ``dudect``-style, or a Welch t-test over
  samples classed by withheld count -- so that a regression in the timing family
  fails a run instead of sitting in prose. It is not built here, and the reason
  is that the quantity underneath it is already pinned *exactly*. What varies
  with the withheld count is the number of SQL round-trips, and that is asserted
  from both sides of its threshold by
  ``test_the_second_pass_arrives_at_fifty_withheld_rows_and_not_before``, its
  geometric step by ``test_each_pass_reaches_twice_as_far_as_the_one_before``
  (both ``tests/unit/test_retrieval_depth.py``), and the corpus scan count by
  ``test_one_search_scans_the_corpus_once_however_many_rows_were_withheld``
  (``tests/integration/test_scan_cache.py``). A t-test over wall clock would be a
  noisier measurement of the same variable, on a machine that also runs the rest
  of the suite -- and it would fail intermittently, which is the failure mode
  this repository can least afford in a security assertion. If the pass count is
  right, the latency follows; if it is wrong, a deterministic test says so and
  names the constant. What no test here covers is the *constant factor* -- what
  one pass costs on a large corpus -- and those numbers live in
  ``FIRST_PASS_DEPTH``'s docstring, measured by hand and not re-measured.
- **``rejected`` items.** :func:`~theurian.domain.enums.may_surface` refuses them
  under every flag, so :class:`~theurian.application.index_builder.IndexBuilder`
  never writes one and there is no withheld row for a pair to differ by.
  :func:`test_a_rejected_item_is_never_written_into_the_index` asserts that
  premise, because the whole argument rests on it.
- **Japanese, and every script without word boundaries.** The alphabet split this
  file's disjointness rests on is Latin, and ``unicode61`` cannot segment CJK --
  which makes the trigram retriever's fifty slots the entire candidate list and a
  materially different machine. ``test_mcp_tools.py``'s ``three_indexes``
  parametrises over both writing systems and is where that case lives.

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
from dataclasses import dataclass, replace
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

#: Two canonical states, because the interesting withholding happens *between*
#: them: the index is built against :data:`STATE_AT_BUILD` and the pointer then
#: names :data:`STATE_NOW`, so a search reports ``stale: true`` -- which is the
#: true description of a project whose knowledge moved after its last build, and
#: the only window in which :class:`~theurian.application.visibility.
#: CanonicalVisibility` has anything to do.
STATE_AT_BUILD: Final = StateHash(ContentHash("a" * 64))
STATE_NOW: Final = StateHash(ContentHash("b" * 64))
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


#: Withheld at query time because the *canonical store* retired it after the
#: index was built. Its chunks carry ``status = 'approved'``, so every retriever's
#: SQL admits them and
#: :class:`~theurian.application.visibility.CanonicalVisibility` is the only thing
#: between the row and the caller. This is the shape Milestone 5's five faces
#: lived in.
RETIRED_AFTER_BUILD: Final = "retired-after-build"

#: Withheld at query time because the row is a ``draft`` and the caller did not
#: ask for drafts. Its chunks carry ``status = 'draft'``, so the retrievers' own
#: ``WHERE`` refuses them and the canonical gate is never consulted about them.
#:
#: A weaker shape, kept because it is a real product state -- an index built with
#: ``--include-unapproved`` and a search that did not ask -- and because a
#: separation here would be a defect in a different gate.
DRAFT_IN_AN_UNAPPROVED_INDEX: Final = "draft-in-an-unapproved-index"

WITHHOLDING_MECHANISMS: Final = (RETIRED_AFTER_BUILD, DRAFT_IN_AN_UNAPPROVED_INDEX)


@dataclass(frozen=True, slots=True)
class _Case:
    """One generated pair, before either project exists.

    Everything here is shared by the two projects except :attr:`payloads`, whose
    first element goes to the probe and second to the control. That is the whole
    of the difference between them, and it lives in items neither caller may read.
    """

    visible: tuple[_Document, ...]
    #: Body of each withheld document, minus its payload. Identical in both
    #: projects, so every collection statistic it contributes is identical too.
    withheld_filler: tuple[str, ...]
    withheld_titles: tuple[str, ...]
    #: ``(probe, control)`` per withheld document: equal length, one character
    #: apart, drawn from the alphabet no visible row can produce.
    payloads: tuple[tuple[str, str], ...]
    #: One of :data:`WITHHOLDING_MECHANISMS`. Generated rather than fixed because
    #: the two are stopped by different code, and a suite that only ever built one
    #: of them would report the other as covered.
    withheld_by: str
    query: str
    limit: int
    max_tokens: int
    use_dense: bool

    @property
    def build_status(self) -> KnowledgeStatus:
        """What the withheld documents are when the index is written.

        ``approved`` for the retired shape: it has to be in the index as a row a
        retriever will return, or the canonical gate never sees it and the pair
        proves nothing about the gate. That mistake was made in this file's first
        version -- every withheld document was a ``draft``, so the retrievers'
        own ``WHERE`` removed it and deleting the canonical gate outright left
        all ten tests here green.
        """
        return (
            KnowledgeStatus.APPROVED
            if self.withheld_by == RETIRED_AFTER_BUILD
            else KnowledgeStatus.DRAFT
        )

    @property
    def retired(self) -> tuple[str, ...]:
        """Which items are moved to ``deprecated`` after the index is written."""
        if self.withheld_by != RETIRED_AFTER_BUILD:
            return ()
        return tuple(document.item_id for document in self.withheld(secret=True))

    def withheld(self, *, secret: bool) -> tuple[_Document, ...]:
        """The withheld documents as one side of the pair writes them."""
        return tuple(
            _Document(
                item_id=f"architecture.withheld-{index:02d}",
                revision_id=_ulid("WH", index),
                title=self.withheld_titles[index],
                body=f"{filler} {pair[0] if secret else pair[1]}",
                status=self.build_status,
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


def _visible_documents(sizes: tuple[int, ...]) -> st.SearchStrategy[tuple[_Document, ...]]:
    """An approved corpus of one of ``sizes`` documents.

    A *ladder* rather than ``st.lists(min_size=2, max_size=60)``, because the
    interesting sizes are rare under the natural distribution and the boundary
    they straddle is exact. :data:`~theurian.application.retrieval_service.
    CANDIDATE_DEPTH` is fifty: a pair whose corpora both fit inside one
    retriever's depth cannot tell a depth loop that counts *visible* rows from
    one that counts raw ones -- the fourth face in
    :mod:`theurian.application.retrieval_service`'s table, and the one that
    recovered a credential at the default token budget.

    Measured: with the size drawn as an ordinary list length, twenty-five
    examples produced nothing above the boundary and the mutation replacing the
    depth loop with a single fifty-row fetch survived every test in this file.
    """
    document = st.tuples(st.lists(_WORD, min_size=1, max_size=3).map(" ".join), _prose())
    return (
        st.sampled_from(sizes)
        .flatmap(lambda size: st.lists(document, min_size=size, max_size=size))
        .map(
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
    )


#: Corpora smaller than one retriever's candidate depth.
BELOW_THE_DEPTH: Final = (2, 5, 12)

#: Corpora at and past it. ``CANDIDATE_DEPTH`` is 50 and a document of this size
#: is one chunk, so 62 documents matching a common term is 62 candidate rows for
#: fifty slots -- which is what makes a displaced row observable at all.
ACROSS_THE_DEPTH: Final = (49, 51, 62)


def _cases(sizes: tuple[int, ...] = BELOW_THE_DEPTH + ACROSS_THE_DEPTH) -> st.SearchStrategy[_Case]:
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
            withheld_by=st.sampled_from(WITHHOLDING_MECHANISMS),
            limit=st.sampled_from((1, 3, 10, MAX_RESULTS)),
            max_tokens=st.sampled_from((2_000, 8_000, MAX_BUDGET_TOKENS)),
            use_dense=st.booleans(),
        )

    return _visible_documents(sizes).flatmap(with_query)


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
    withheld_by: str,
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
        withheld_by=withheld_by,
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


def _write_active_state(paths: ProjectPaths, state: StateHash, updated_at: datetime) -> None:
    """Publish which canonical state this project is serving.

    Written by hand rather than through
    :func:`~theurian.application.project_service.write_active_state` because the
    filename must stay :data:`STATE_NOW`'s throughout: this builder writes one
    database and moves the pointer's *hash* across it, where ``migrate apply``
    would write a second file. What a search reads off the pointer -- the hash it
    reports as ``snapshotId``, and the one it compares the index's against -- is
    identical either way.
    """
    paths.active_pointer.write_text(
        json.dumps(
            ActiveState(
                state_hash=state,
                database_filename=STATE_NOW.database_filename,
                migration_count=1,
                updated_at=updated_at.isoformat(),
            ).to_json()
        ),
        encoding="utf-8",
    )


def _retire(item: KnowledgeItem) -> KnowledgeItem:
    """The same item, ``deprecated``.

    ``deprecated`` rather than ``draft`` because
    :func:`~theurian.domain.enums.may_surface` refuses it under *every* flag, so
    a caller cannot reach the row by passing ``includeUnapproved`` -- which is
    what makes it withheld rather than merely off by default.
    """
    return replace(item, status=KnowledgeStatus.DEPRECATED)


def _retire_in_the_store(
    database: Path, paths: ProjectPaths, items: tuple[KnowledgeItem, ...]
) -> None:
    """The canonical write the index never saw."""
    with write_transaction(database, paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        for item in items:
            writer.put_item(item)


def _build_project(
    root: Path,
    documents: tuple[_Document, ...],
    created_at: datetime,
    retired: tuple[str, ...] = (),
) -> ProjectRegistry:
    """One project, built the way the withholding it is meant to exercise needs.

    Two canonical writes with an index build between them, because *when* the
    index was written relative to the canonical state is the whole of what
    decides which gate stops a row:

    1. every document is written at its build-time status and the index is built,
       so a document named in ``retired`` is in the index as ``approved`` and
       every retriever's ``WHERE`` will return it;
    2. the documents in ``retired`` are moved to ``deprecated``, and the active
       pointer moves to :data:`STATE_NOW` while the index keeps
       :data:`STATE_AT_BUILD` -- which is what makes ``stale`` true and what
       leaves :class:`~theurian.application.visibility.CanonicalVisibility` as
       the only thing between that row and the caller.

    With ``retired`` empty this builds the other shape: the index holds drafts
    because it was built with ``include_unapproved``, and a search that does not
    ask for drafts never gets them past the retrievers' own SQL.
    """
    paths = ProjectPaths.of(root)
    paths.state.mkdir(parents=True, exist_ok=True)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    database = paths.database_for(STATE_NOW)
    create_database(database, state_hash=str(STATE_NOW), engine_version=1)

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

    # The state this build sees. With nothing to retire it is already the final
    # state, so a search reports `stale: false`; with a retirement to come it is
    # the earlier one, and the pointer moves past it below. Getting this wrong is
    # not cosmetic -- `stale` decides `retrieval.note`, the note is priced into
    # the envelope, and the envelope decides how many results fit a budget.
    built_from = STATE_AT_BUILD if retired else STATE_NOW
    _write_active_state(paths, built_from, created_at)
    IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=HashingEmbedding(),
    ).build(
        IndexRequest(
            database=database,
            index_path=paths.index_for(INDEX_BUILD_ID),
            project_id=PROJECT_ID,
            state_hash=str(built_from),
            index_build_id=INDEX_BUILD_ID,
            include_unapproved=True,
        )
    )
    paths.active_index_pointer.write_text(
        json.dumps(
            {
                "indexBuildId": INDEX_BUILD_ID,
                "stateHash": str(built_from),
                "projectId": PROJECT_ID,
                "indexesUnapproved": True,
            }
        ),
        encoding="utf-8",
    )

    if retired:
        # The retirement the index never saw. `put_item` upserts, so this leaves
        # the revision -- and every chunk built from it -- exactly where it was.
        by_id = {document.item_id: document for document in documents}
        _retire_in_the_store(
            database,
            paths,
            tuple(_retire(_item(by_id[item_id], created_at)) for item_id in retired),
        )
        _write_active_state(paths, STATE_NOW, created_at)

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
    #: The probe's project root, so a guard can read its index file directly.
    probe_root: Path
    case: _Case


def _pair(base: Path, case: _Case) -> _Pair:
    created_at = datetime.now(UTC) - AGE_OFFSET
    probe_root = base / "probe"
    return _Pair(
        probe=_build_project(probe_root, case.documents(secret=True), created_at, case.retired),
        control=_build_project(
            base / "control", case.documents(secret=False), created_at, case.retired
        ),
        probe_root=probe_root,
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


def _offered_by_the_index(root: Path, case: _Case, *, include_unapproved: bool) -> set[str]:
    """Which item ids this query's retrievers hand up out of the index file.

    Read straight off :class:`~theurian.infrastructure.sqlite.index_store.
    SqliteIndexStore`, below every gate, because that is the only place the
    precondition can be established: a response that omits a withheld document
    proves nothing if no retriever ever offered it.

    Both scored retrievers are asked. The trigram one is not decoration here --
    it is the only one that can match a payload with no word boundary in it, and
    it is the one Milestone 5's extraction attack ran through.
    """
    index = SqliteIndexStore(ProjectPaths.of(root).index_for(INDEX_BUILD_ID))
    rows = [
        *index.search_lexical(
            case.query,
            project_id=PROJECT_ID,
            limit=MAX_RESULTS,
            include_unapproved=include_unapproved,
        ),
        *index.search_substring(
            case.query,
            project_id=PROJECT_ID,
            limit=MAX_RESULTS,
            include_unapproved=include_unapproved,
        ),
    ]
    return {row.item_id for row in rows}


def _assert_the_pair_bites(pair: _Pair, probe: dict[str, Any]) -> None:
    """Refuse to pass on an example that proved nothing.

    Four ways a generated pair can be green while testing nothing. The third is
    not hypothetical: this file's first version made every withheld document a
    ``draft``, whose chunks the retrievers' own ``WHERE`` refuses, so the
    canonical gate was never asked about them -- and deleting that gate outright
    (``cleared = tuple(ranked)``) left all ten tests here green while turning 38
    parametrisations of ``test_mcp_tools.py`` red.

    - the answer is empty, so two empty answers are being compared;
    - the payloads are equal, so the two projects are the same project;
    - **no retriever offers the withheld row**, so nothing downstream had a
      chance to leak it;
    - the withheld row is in the answer, which is a leak rather than a bad pair.

    Asserted rather than filtered. ``hypothesis`` will happily generate a corpus
    of one empty document forever, and an example dropped by ``assume`` leaves no
    trace in the run.
    """
    case = pair.case
    root = pair.probe_root
    withheld_ids = {document.item_id for document in case.withheld(secret=True)}

    assert probe["count"] > 0, "two empty answers prove nothing about withholding"
    assert all(secret != decoy for secret, decoy in case.payloads), (
        "the two projects must actually differ"
    )

    offered = _offered_by_the_index(root, case, include_unapproved=False)
    if case.withheld_by == RETIRED_AFTER_BUILD:
        assert withheld_ids & offered, (
            "a retired document's chunks are still stamped `approved` in the "
            "index, so a retriever must offer them on the caller's own flags -- "
            "if it does not, the canonical gate is never consulted and this pair "
            "says nothing about it"
        )
    else:
        assert not withheld_ids & offered, (
            "a draft's chunks are stamped `draft`, so the retrievers' own WHERE "
            "must refuse them before any gate is asked"
        )
        assert withheld_ids & _offered_by_the_index(root, case, include_unapproved=True), (
            "while the index must still hold them, or this pair differs in nothing"
        )

    assert not withheld_ids & {result["itemId"] for result in probe["results"]}, (
        "and no withheld document may be in the answer"
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


#: The caller's own parameters, enumerated rather than generated.
#:
#: They are a small, known, load-bearing set, and sampling them buries the cases
#: that matter: whether a displaced candidate is *observable* needs ``limit`` at
#: the published maximum **and** a budget that lets fifty results through, and
#: three independent draws land on that pair about one example in twelve.
#: Measured -- with all three sampled, the mutation replacing the depth loop with
#: a single fifty-row fetch survived twenty-five generated examples twice over.
#: The same five sets ``test_mcp_tools.py`` enumerates, minus its ``one-below``.
ARGUMENT_SETS: Final[tuple[tuple[dict[str, Any], str], ...]] = (
    ({}, "defaults"),
    ({"limit": MAX_RESULTS}, "at-the-depth"),
    ({"limit": MAX_RESULTS, "maxTokens": MAX_BUDGET_TOKENS}, "generous"),
    (
        {"limit": MAX_RESULTS, "maxTokens": MAX_BUDGET_TOKENS, "useDense": True},
        "dense",
    ),
)


@pytest.mark.parametrize(
    "arguments", [pair[0] for pair in ARGUMENT_SETS], ids=[pair[1] for pair in ARGUMENT_SETS]
)
@pytest.mark.parametrize(
    "sizes", (BELOW_THE_DEPTH, ACROSS_THE_DEPTH), ids=("below-the-depth", "across-the-depth")
)
@settings(_GENERATED, max_examples=6)
@given(data=st.data())
def test_no_published_value_varies_with_a_withheld_document(
    tmp_path_factory: pytest.TempPathFactory,
    sizes: tuple[int, ...],
    arguments: dict[str, Any],
    data: st.DataObject,
) -> None:
    """SEC-13, T-15, FR-R4, FR-R5. The property, over generated pairs.

    One query against two corpora that differ only in bodies the caller may not
    read, and the **entire response** must be equal -- ``count``, every field of
    every hit including which chunk was excerpted, and every key of the
    ``retrieval`` block.

    Nothing is masked, so nothing has to be argued for: the three values a
    two-project comparison would normally exclude are held equal as inputs, and
    the module docstring says what that costs.

    **Corpus size is enumerated, not generated**, for the reason
    :func:`_visible_documents` records: the candidate depth is an exact boundary
    and a corpus below it cannot show a displaced row at all. So is the caller's
    parameter set -- see :data:`ARGUMENT_SETS`. What is generated is everything
    a person would otherwise have had to think of: what the documents say, what
    the withheld ones say, which of them the query reaches, and how the two
    corpora differ.

    This is the mechanised form of
    ``test_a_withheld_document_changes_nothing_a_caller_can_see``
    (``test_mcp_tools.py``), which asserts the same thing against three fixed
    corpora. The fixed one is not redundant: it runs the real CLI and covers a
    Japanese corpus, where ``unicode61`` cannot segment the text and the trigram
    retriever's fifty slots are the whole candidate list -- a materially
    different machine that this generator does not build.
    """
    case = data.draw(_cases(sizes))
    pair = _pair(tmp_path_factory.mktemp("absence"), case)

    probe = _search(pair.probe, case, **arguments)
    control = _search(pair.control, case, **arguments)

    _assert_the_pair_bites(pair, probe)
    assert probe == control, (
        "every published value must equal what the same query returns against a "
        "corpus whose withheld documents say something else"
    )


@settings(_GENERATED, max_examples=18)
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
    hand-written case checks it for the one id someone wrote down. Four arms
    rather than two: the withheld id in the probe, the same id in the control, an
    id that exists in neither, and -- as the guard on the guard -- a *visible* id,
    which must come back rather than be refused. Without the last one, three
    identical refusals would be satisfied by a project that holds nothing.
    """
    pair = _pair(tmp_path_factory.mktemp("absence"), case)
    withheld_id = case.withheld(secret=True)[0].item_id
    visible_id = case.visible[0].item_id

    from_probe = _failing(pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=withheld_id)
    from_control = _failing(pair.control, "knowledge.get", projectId=PROJECT_ID, itemId=withheld_id)
    absent = _failing(pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=NO_SUCH_ITEM)
    present = _call(pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=visible_id)

    assert from_probe == from_control, "the two corpora must refuse identically"
    assert from_probe == absent.replace(NO_SUCH_ITEM, withheld_id), (
        "and a withheld id must be refused in the words an absent id is refused in"
    )
    assert present["itemId"] == visible_id, (
        "the guard on this guard: an id the caller may read must come back, or "
        "the three refusals above agree because this project holds nothing"
    )
    assert case.secrets[0] not in json.dumps(present), (
        "and the item that does come back must not carry a withheld payload"
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
    assert all(d.status is case.build_status for d in probe[visible_count:])
    assert (case.retired != ()) is (case.withheld_by == RETIRED_AFTER_BUILD), (
        "the retired shape must retire something and the draft shape must not, or "
        "one of the two mechanisms is being built as the other"
    )


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


def _t17a_projects(tmp_path: Path) -> tuple[ProjectRegistry, ProjectRegistry]:
    """The recorded corpus, built twice: with the withheld draft and without it.

    One builder for the pin and its guard, so the guard cannot end up describing a
    corpus the pin no longer uses.
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
    return (
        _build_project(tmp_path / "holds-it", (*visible, withheld), created_at),
        _build_project(tmp_path / "never-did", visible, created_at),
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

    Nothing withheld is published and no gate is at fault: both answers hold one
    approved document and no draft. What moved is BM25's length normalisation,
    ``k1 * (1 - b + b * D / avgdl)``, whose ``avgdl`` is taken over every row in
    the ``chunks_fts`` table. There is nothing for a
    :class:`~theurian.application.visibility.Visibility` to intercept, because the
    arithmetic happens inside SQLite.

    **A draft, which makes the statement stronger than a retired document would.**
    A draft's chunks carry ``status = 'draft'``, so the retrievers' own ``WHERE``
    refuses them and this query's result set never contains that row at all -- yet
    the ranking of the rows it *does* contain still moves. FTS5's collection
    statistics are a property of the virtual table, not of the rows a statement
    selects, so filtering in the outer query does not exclude a row from them.
    :func:`test_the_t17a_corpus_still_has_something_to_withhold` asserts that the
    retriever really does refuse it, because that is the whole of what makes this
    the sharper form.

    **What this adds to the two tests that already pin T-17a.**
    ``test_a_withheld_document_can_still_reorder_the_visible_ones`` and
    ``test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones``
    (``test_retrieval_service.py``) assert reordering below the tool surface,
    through ``ResultGate`` directly. The first of them says in prose that the
    difference is "reachable through `knowledge.search` with no parameters" and
    nothing measured it. This does: the whole published response, through
    ``server.call_tool``, on a query of one ordinary word. It also shows the reach
    is not confined to *order* -- at ``limit=1`` there is no order to permute, and
    the caller is simply handed a different document, with a different excerpt and
    different provenance.

    Like its two siblings, this goes red when Milestone 6 closes the stale window
    (ADR-0022, issue #15), and it is meant to: whoever makes it stop reproducing
    is the person who should be rewriting the T-17a acceptance in the threat
    model in the same change.
    """
    holds_it, never_did = _t17a_projects(tmp_path)

    from_the_one_that_holds_it = _call(
        holds_it, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=1
    )
    from_the_one_that_never_did = _call(
        never_did, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=1
    )

    assert from_the_one_that_holds_it["count"] == 1, "one result each, or this is not it"
    assert from_the_one_that_never_did["count"] == 1
    assert {r["status"] for r in from_the_one_that_holds_it["results"]} == {"approved"}, (
        "nothing withheld may be published, or this is a gate defect and not T-17a"
    )
    assert {r["status"] for r in from_the_one_that_never_did["results"]} == {"approved"}
    assert (
        from_the_one_that_holds_it["results"][0]["itemId"]
        != from_the_one_that_never_did["results"][0]["itemId"]
    ), (
        "a withheld draft the caller cannot read decides which approved document "
        "they are handed; if this no longer reproduces, T-17a's acceptance in the "
        "threat model is out of date and should be deleted rather than this test"
    )
    assert (
        from_the_one_that_holds_it["retrieval"]["usedTokens"]
        != from_the_one_that_never_did["retrieval"]["usedTokens"]
    ), "and the published cost moves with it"


def test_the_t17a_corpus_still_has_something_to_withhold(tmp_path: Path) -> None:
    """Guards the test above, whose whole meaning is in its corpus.

    "The two answers name different documents" is satisfiable by a corpus that has
    stopped withholding anything at all -- a builder that indexed the draft and a
    gate that published it would produce two different answers too, and that is a
    defect rather than this channel.

    The third assertion is the one that makes the claim above the *sharper* form:
    the retriever, asked exactly as the search asks it, does not return the
    withheld row. So its chunks are outside the result set of every statement this
    query runs, and the ranking of the rows inside that set still moves. Without
    it the reader is left to assume FTS5's statistics ignore the outer ``WHERE``,
    which is true and is exactly the sort of assumption this repository has been
    wrong about.
    """
    withheld_id = "architecture.withheld-00"
    registry, _ = _t17a_projects(tmp_path)
    index = SqliteIndexStore(ProjectPaths.of(tmp_path / "holds-it").index_for(INDEX_BUILD_ID))

    with_the_flag = index.search_lexical(
        "backend", project_id=PROJECT_ID, limit=MAX_RESULTS, include_unapproved=True
    )
    as_the_search_asks = index.search_lexical(
        "backend", project_id=PROJECT_ID, limit=MAX_RESULTS, include_unapproved=False
    )
    answer = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=10)

    assert withheld_id in {row.item_id for row in with_the_flag}, (
        "the draft's chunks must be in the index file, or there is no withheld row"
    )
    assert withheld_id not in {row.item_id for row in as_the_search_asks}, (
        "and the retriever must refuse them on the search's own flags, so the "
        "reordering is a collection statistic and not a returned row"
    )
    assert withheld_id not in {result["itemId"] for result in answer["results"]}, (
        "nor may the answer carry it"
    )
    assert len(as_the_search_asks) > 1, (
        "at least two visible rows must match, or there is no order to move"
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

    Both shapes, because they publish different staleness and a builder that
    produced one when asked for the other would be invisible otherwise: the
    retired shape leaves the index behind the store and must report ``stale:
    true``, and the draft shape changes nothing after the build and must report
    ``stale: false``.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    visible = _Document(
        "architecture.visible-00",
        _ulid("VS", 0),
        "handle",
        "cache manifold headline beacon.",
        KnowledgeStatus.APPROVED,
    )
    retired = _Document(
        "architecture.withheld-00",
        _ulid("WH", 0),
        "beacon",
        "cache manifold machine.",
        KnowledgeStatus.APPROVED,
    )

    behind = _build_project(tmp_path / "behind", (visible, retired), created_at, (retired.item_id,))
    level = _build_project(tmp_path / "level", (visible,), created_at)
    from_behind = _call(behind, "knowledge.search", projectId=PROJECT_ID, query="manifold")
    from_level = _call(level, "knowledge.search", projectId=PROJECT_ID, query="manifold")

    assert from_behind["retrieval"]["snapshotId"] == str(STATE_NOW), (
        "the state a search reports must be the one the pointer names, not the "
        "one the index was built from"
    )
    assert from_level["retrieval"]["snapshotId"] == str(STATE_NOW)
    assert from_behind["retrieval"]["indexBuildId"] == INDEX_BUILD_ID
    assert from_behind["retrieval"]["indexed"] is True, "the ranked path, not the fallback"
    assert from_behind["retrieval"]["stale"] is True, "the retired shape leaves the index behind"
    assert from_level["retrieval"]["stale"] is False, "and the draft shape does not"
    assert from_behind["projectId"] == PROJECT_ID


def test_the_pair_builder_writes_a_canonical_store_the_gate_actually_reads(
    tmp_path: Path,
) -> None:
    """The last way the generated tests could be green while testing nothing.

    Every equality above depends on the canonical store being the authority the
    gate consults, and on the retirement really landing in it. A builder whose
    second write silently did nothing would leave the withheld document
    ``approved`` in the store as well as in the index -- both projects would then
    publish it, the responses would still be equal, and every test above would
    stay green while measuring nothing at all.

    So the status is read back out of the store, and the *index* is read back
    too: the retirement must not have touched it, or the row the gate is supposed
    to stop would never have been offered.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    approved = _Document(
        "architecture.visible-00",
        _ulid("VS", 0),
        "handle",
        "cache manifold headline.",
        KnowledgeStatus.APPROVED,
    )
    retired = _Document(
        "architecture.withheld-00",
        _ulid("WH", 0),
        "beacon",
        "cache manifold machine.",
        KnowledgeStatus.APPROVED,
    )

    _build_project(tmp_path / "one", (approved, retired), created_at, (retired.item_id,))

    paths = ProjectPaths.of(tmp_path / "one")
    with SqliteCanonicalStore(paths.database_for(STATE_NOW)) as store:
        items = store.list_items(RequestContext(project_id=ProjectId(PROJECT_ID)))
    by_id = {item.item_id.value: item for item in items}
    offered = SqliteIndexStore(paths.index_for(INDEX_BUILD_ID)).search_lexical(
        "manifold", project_id=PROJECT_ID, limit=MAX_RESULTS, include_unapproved=False
    )

    assert by_id[approved.item_id].status is KnowledgeStatus.APPROVED
    assert by_id[retired.item_id].status is KnowledgeStatus.DEPRECATED, (
        "the second write must really have retired it in the store the gate asks"
    )
    assert by_id[retired.item_id].current_revision_id is not None, (
        "an item with no current revision is withheld for the wrong reason"
    )
    assert retired.item_id in {row.item_id for row in offered}, (
        "and the index must still offer it on the caller's own flags, or nothing reaches the gate"
    )
