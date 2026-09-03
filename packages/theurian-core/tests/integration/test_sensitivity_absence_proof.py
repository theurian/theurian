"""One query against two corpora, on the sensitivity axis (ADR-0025 part 4, #119).

``test_absence_proof.py`` states the property this module states, over the axis
that was enforced first: *no published value varies with content the caller may
not read*. It is a 2-safety property, so no single run can be inspected for it,
and the answer is self-composition -- build the pair, ask the same question,
compare the whole answer.

This is the same property over a **different reason for withholding**, and it is
its own file because ``test_absence_proof.py``'s docstring argues a new axis
should be one (*Why this is one file and not two*). The builder below is a
deliberate *second implementation* rather than an import of that module's: the
two files prove one property over two axes, and keeping their corpus
construction independent means a bug in one cannot mask the same bug in the
other -- the whole value of a 2-safety cross-check. A shared helper *would* be
importable -- this tree runs under ``--import-mode=importlib`` and
``conftest.py`` puts the tests root on ``sys.path``, which is how
``migration_fixtures`` is imported below -- so the separation is a choice, not a
limitation. The cost of the choice is stated where it bites:
:func:`test_the_two_alphabets_cannot_produce_a_shared_token_or_trigram` exists
here as well, because this file has its own copy of the vocabulary and a copy
can drift.

What "withheld" means here
--------------------------
Not a status. A deployment declares one **sensitivity ceiling** in its serving
profile (ADR-0025's entitlement model), and an item above it is one this
*deployment* may not disclose to anyone -- while remaining ordinary, current,
approved knowledge that every count over the store still includes. That is what
makes the axis a different machine from the status axis rather than a
re-parametrisation of it: on the status axis a withheld row has left
``SURFACEABLE_STATUSES``, so five other things stop counting it; here nothing
stops except the disclosure gates.

Three mechanisms, and each is stopped by different code
-------------------------------------------------------
ADR-0025 ships three, in the order they were built, and a suite that exercised
one would report the other two as covered:

:data:`EXCLUDED_AT_BUILD`
    phase 3. The item is above the ceiling *when the index is built*, so
    ``IndexBuilder`` writes no chunk row and the forest, derived over what the
    build wrote, gets no summary node. Nothing is filtered at query time because
    there is nothing in the file to filter -- which is the point: BM25 collection
    statistics are computed over every row an FTS5 table holds, so a row no query
    can return still prices the rows that can (T-17a).
:data:`RECLASSIFIED_NOT_PURGED`
    phase 2. The index was built while the item was *within* the ceiling, so its
    chunks carry a level the ceiling admits and every retriever's ``WHERE``
    returns them. The item is then reclassified upward in the canonical store,
    and :class:`~theurian.application.visibility.CanonicalVisibility`'s re-check
    on the item's **current** level is the only thing between the row and the
    caller. Structurally the strongest arm here, and the one that fails open if
    the gate is deleted.
:data:`RECLASSIFIED_AND_PURGED`
    phase 5. The same reclassification, with ``migrate apply`` purging the
    withdrawn rows out of the published build in the same command. It is not
    generated -- the purge is wired into the real CLI -- so it is one hand-written
    pair at the end of this module, built the way
    ``test_absence_proof.py``'s shipped-close section builds its own.

Two ways for a pair to differ, and one cell that is deliberately absent
-----------------------------------------------------------------------
:data:`ONE_PAYLOAD_APART`
    both projects hold the withheld documents; their bodies are identical but for
    a payload one character apart, drawn from an alphabet no visible row can
    produce. A gate that publishes a withheld row separates the pair, because the
    payload differs. This is the shape that catches *content* reaching a caller.
:data:`PRESENT_IN_ONE_ONLY`
    the probe holds the withheld documents and the control never held them at
    all. This is ADR-0025 part 4's property in its literal words -- "an index
    holding the withheld rows and an index that never held them must return the
    same response" -- and it is the shape that catches a candidate slot, a count,
    a token total or a BM25 statistic spent on a row that never becomes a result.

    **It also closes a blind spot ``test_absence_proof.py`` names as open.** That
    module says *presence is not tested here*: its pairs vary what a withheld
    document says, never whether it is in the corpus at all. On this axis presence
    *is* testable, because two of the three mechanisms keep the row out of the
    index by construction rather than filtering it on the way out.

The third cell of that grid -- :data:`PRESENT_IN_ONE_ONLY` over
:data:`RECLASSIFIED_NOT_PURGED` -- is **excluded by record and not by oversight**,
and :data:`SHAPES` is written as the list of valid cells so that it is absent by
construction rather than filtered away. In that state the published build still
holds the reclassified document's text, so a control that never held it has
different FTS5 collection statistics and the visible rows are scored against a
different ``avgdl`` and different document frequencies. The equality would fail,
honestly, and the honest response to that failure is to weaken it. That is T-17a
on this axis, it is recorded in ADR-0025 part 2 as the one direction the purge
cannot close, and the state itself is reproduced by
``test_mcp_tools.py::test_the_ranked_path_withholds_a_document_reclassified_after_the_build``.

What is compared, and what is held equal
----------------------------------------
Every key of the ``knowledge.search`` response and every key of the
``knowledge.status`` response, with **nothing masked** on the generated pairs.
Three values that a two-project comparison would otherwise have to exclude are
held equal as *inputs*: both projects register under one id in two separate
registries, and :func:`_build_project` declares ``snapshotId`` and
``indexBuildId`` rather than deriving them. So this module says nothing about
those three; ``test_absence_proof.py``'s docstring records what that costs and
``test_the_build_identity_a_search_reports_does_not_vary_with_the_query``
covers the part that matters.

**``knowledge.status`` is compared here and is excluded there, and the difference
is a change in the product rather than a difference of opinion.** That module
excludes it because its two interesting fields, ``stateHash`` and
``appliedMigrations``, are constants of its builder -- which is true of this
builder too. What is *not* a constant is ``itemCount`` and ``itemsByStatus``:
they are a live read, and until #119 phase 6 they counted every surfaceable item
whatever the ceiling, which made them the strongest member of the disclosure
family on this axis -- a published statistic over rows the caller may not see,
reached through a tool nobody checking ``knowledge.search`` would think to call.
Phase 6 narrowed them, and the pairs below are what says so: under
:data:`PRESENT_IN_ONE_ONLY` the probe holds items the control does not, and both
must report the same counts.

Durations are excluded, and by record rather than by measurement
-----------------------------------------------------------------
``test_absence_proof.py``'s duration-exclusion docstring was amended for this
axis in ``e25edb8`` and again when the acceptance was recorded, and this module
adopts that decision rather than re-taking it. In short: on the ceiling axis a
second quantity moves with the withheld count while the pass count is held at one
-- ``SqliteCanonicalStore.list_items_by_status`` spends about 0.20 us and 6.0
SQLite VM steps per above-ceiling row, corpus-bounded because that statement
carries no ``LIMIT`` -- and ``count_surfaceable_by_status`` carries a larger term
of the same shape since phase 6. Both are recorded and accepted residuals with
their measurements at the methods themselves, and flattening them is
https://github.com/theurian/theurian/issues/338.

**This suite deliberately does not measure any of it.** A term of that size is
far below what a pair built out of one process's wall clock could separate from
noise: the threat model puts a real client's end-to-end floor at 1.40 ms (TB-1),
thousands of above-ceiling rows away. A timing assertion here would assert
nothing while reading as though it asserted the family.

Four further things this file does not reach
---------------------------------------------
- **The forest half.** Every pair here builds a chunk-only index, so
  ``nodes_fts`` and ``nodes_trigram`` are not compared. ADR-0025 part 4 is
  explicit that all four scoring surfaces are owed, and the node half is
  delivered by ``test_sensitivity_purge.py::test_the_purged_forest_equals_one_
  built_above_the_ceiling`` (nodes, derivation edges and node vectors between a
  purged build and one built above the ceiling) and by
  ``test_forest_builder.py::test_an_above_ceiling_document_reaches_neither_half_
  of_the_index`` (the build-side exclusion over all four text indexes). What this
  file adds is the leaf-side *response* equality those two do not compare.
- **The unranked fallback path.** Every generated pair here answers from an
  index, because the pointer's recorded flavor matches the grant in force. A
  deployment whose build was made under another ceiling degrades to
  ``search.substring_answer`` with ``serving-profile-mismatch``, and that path
  has its own gate -- the grant handed to ``list_items_by_status`` as a SQL
  predicate. It is pinned by ``test_mcp_tools.py``'s
  ``test_the_unranked_scan_withholds_an_above_ceiling_item`` and
  ``test_a_narrow_ceiling_withholds_the_item_from_search_and_from_get``, not
  here: reaching it from a pair means building the two corpora under a *different*
  ceiling from the one they are served under, which is a second variable in a
  comparison that has one.
- **``rejected`` and other non-surfaceable statuses.** Every document here is
  ``approved``. Mixing the axes would mean a pair whose equality could be
  satisfied by the status gate while the disclosure gate did nothing, which is
  the failure mode ``test_absence_proof.py`` records having had once.
- **Japanese, and every script without word boundaries.** The alphabet split this
  file's disjointness rests on is Latin. ``test_mcp_tools.py``'s ``three_indexes``
  is where the CJK machine lives.

Why the generated corpora are built without the CLI
----------------------------------------------------
A generated test builds a pair per example, and a CLI-built project costs
seconds. The two mechanisms that can be reached without the migration engine are
built through the application layer -- a real SQLite canonical store, a real
index build, a real embedder, and the real MCP tool dispatch -- and the one that
cannot, :data:`RECLASSIFIED_AND_PURGED`, runs the real ``theurian migrate apply``
once at the end of this module.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.authorization import (
    DEPLOYMENT_ACL_GROUPS,
    DEPLOYMENT_TENANT,
    SERVING_PROFILE_FILENAME,
    AuthorizationGrant,
    encode_sensitivities,
)
from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import (
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
)
from theurian.cli.main import app
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
from theurian.security.project_config import SecretScanPolicy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# The deployment, and the levels it does and does not serve
# ---------------------------------------------------------------------------

#: The ceiling every deployment in this file declares, expanded to the set a
#: grant carries. ``internal`` is the level ADR-0025 records as the shipped
#: default the flip in #119 phase 6b will land, so the pairs here are built at the
#: line the product is moving to rather than at an arbitrary one.
CEILING_LEVELS: Final = frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL})

#: What the withheld documents are raised to. ``restricted`` rather than
#: ``confidential`` for no reason but legibility in a failure -- both are outside
#: :data:`CEILING_LEVELS`, which is the only property that matters and which
#: :func:`test_the_withheld_level_is_outside_the_ceiling_this_file_serves` holds.
ABOVE_CEILING_LEVEL: Final = Sensitivity.RESTRICTED

#: The level every *visible* document carries, inside the ceiling.
WITHIN_CEILING_LEVEL: Final = Sensitivity.INTERNAL

CEILING_GRANT: Final = AuthorizationGrant(
    tenant=DEPLOYMENT_TENANT,
    sensitivities=CEILING_LEVELS,
    acl_groups=DEPLOYMENT_ACL_GROUPS,
)

#: Every level, for the reads *below* the gate that establish a pair's
#: preconditions. Spelled out rather than read from
#: ``StaticAuthorizationProvider``'s shipped default, which #119 phase 6b
#: narrows: a helper that inherited it would start withholding this file's own
#: fixtures from its own guards, and the guards would go on passing.
EVERY_SENSITIVITY: Final = frozenset(Sensitivity)


# ---------------------------------------------------------------------------
# The corpus vocabulary, and why the alphabets are split
# ---------------------------------------------------------------------------

#: The visible corpus and every query term are built from these, and the letters
#: are the load-bearing part: **a to o only**.
#:
#: The two indexes in a pair must agree on every FTS5 collection statistic that
#: reaches a *visible* row, or a separation would be BM25 arithmetic rather than
#: a leak (T-17a). Both tokenizers this index uses fold case --
#: ``unicode61 remove_diacritics 2`` for words, ``trigram`` for substrings -- so
#: "disjoint" has to hold after folding, which splitting the alphabet at ``o``
#: gives by construction.
#:
#: A second copy of ``test_absence_proof.py``'s list, and a copy can drift:
#: :func:`test_the_two_alphabets_cannot_produce_a_shared_token_or_trigram` is
#: this file's own guard on this file's own constants.
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
PROJECT_ID: Final = "ceiling-pair"

#: Declared, never derived -- see the module docstring on what that costs.
#: Crockford base32 has no ``I``, ``L``, ``O`` or ``U``, and
#: ``tests/unit/test_test_fixtures.py`` is what catches a readable spelling that
#: forgets.
INDEX_BUILD_ID: Final = "01K1BDAAAA01234567890ABCDE"

#: Two canonical states. A reclassification happens *between* them, so the index
#: is built against :data:`STATE_AT_BUILD` and the pointer then names
#: :data:`STATE_NOW` -- which is the true description of a project whose
#: knowledge moved after its last build, and the only window in which the
#: canonical re-check has anything to do.
STATE_AT_BUILD: Final = StateHash(ContentHash("a" * 64))
STATE_NOW: Final = StateHash(ContentHash("b" * 64))
MIGRATION_ID: Final = MigrationId("01K1MGAAAA01234567890ABCDE")

#: How far the run instant sits from a day boundary of ``created_at``.
#:
#: ``ageDays`` is ``(now - created_at).days`` with ``now`` read per request, so a
#: pair whose two calls straddle a boundary reports two different ages for one
#: document and fails for a reason that is not a leak. Anchoring the corpus half
#: a day off the run instant makes that impossible for any run shorter than
#: twelve hours, rather than improbable.
AGE_OFFSET: Final = timedelta(days=3, hours=12)

#: An id no generated corpus can mint, for the "absent" arm of the
#: ``knowledge.get`` comparison.
NO_SUCH_ITEM: Final = "architecture.no-such-item"


# ---------------------------------------------------------------------------
# The three mechanisms and the two ways a pair can differ
# ---------------------------------------------------------------------------

#: Withheld because the item was **already** above the ceiling when the index was
#: built, so ``IndexBuilder`` wrote no chunk row for it (#119 phase 3, ADR-0025
#: part 1). Nothing in the published file to filter, and therefore nothing in the
#: FTS5 collection statistics either.
EXCLUDED_AT_BUILD: Final = "excluded-at-build"

#: Withheld because the item was reclassified **after** the build and the purge
#: did not reach the published build (#119 phase 2, ADR-0025 part 2's recorded
#: residual). Its chunks carry the level the ceiling admits, so every retriever
#: returns them and the canonical re-check on the item's *current* level is the
#: only gate.
RECLASSIFIED_NOT_PURGED: Final = "reclassified-not-purged"

#: Withheld because ``migrate apply`` purged the reclassified rows out of the
#: published build in the same command (#119 phase 5). Not generated: the purge
#: is wired into the real CLI, so this one is hand-written at the end of this
#: module.
RECLASSIFIED_AND_PURGED: Final = "reclassified-and-purged"

#: Both projects hold the withheld documents, one payload apart.
ONE_PAYLOAD_APART: Final = "one-payload-apart"

#: The probe holds them and the control never did.
PRESENT_IN_ONE_ONLY: Final = "present-in-one-only"

#: The ``(mechanism, difference)`` cells a generated pair may take, written as the
#: valid list rather than as a product with a filter.
#:
#: The missing cell is ``(RECLASSIFIED_NOT_PURGED, PRESENT_IN_ONE_ONLY)`` and it
#: is missing on purpose: there the probe's published build still holds the
#: reclassified document's text, so a control that never held it is scored
#: against a different ``avgdl`` and different per-term document frequencies, and
#: the visible rows can come back in a different order. That is T-17a, recorded
#: in ADR-0025 part 2 as the direction the purge cannot close and not a defect
#: this pair could reveal. Filtering it away with ``assume`` would have left a
#: reader counting four cells and finding three.
SHAPES: Final[tuple[tuple[str, str], ...]] = (
    (EXCLUDED_AT_BUILD, ONE_PAYLOAD_APART),
    (EXCLUDED_AT_BUILD, PRESENT_IN_ONE_ONLY),
    (RECLASSIFIED_NOT_PURGED, ONE_PAYLOAD_APART),
)


@dataclass(frozen=True, slots=True)
class _Document:
    """One knowledge item, as this module writes it."""

    item_id: str
    revision_id: str
    title: str
    body: str
    sensitivity: Sensitivity


@dataclass(frozen=True, slots=True)
class _Case:
    """One generated pair, before either project exists.

    Everything here is shared by the two projects except what :attr:`difference`
    names -- either the withheld documents' payloads, or whether the control
    holds them at all. Both live in items neither caller may read.
    """

    visible: tuple[_Document, ...]
    #: Body of each withheld document, minus its payload. Identical in both
    #: projects wherever both hold them, so every collection statistic it
    #: contributes is identical too.
    withheld_filler: tuple[str, ...]
    withheld_titles: tuple[str, ...]
    #: ``(probe, control)`` per withheld document: equal length, one character
    #: apart, drawn from the alphabet no visible row can produce.
    payloads: tuple[tuple[str, str], ...]
    #: One of :data:`SHAPES`.
    mechanism: str
    difference: str
    query: str
    limit: int
    max_tokens: int
    use_dense: bool

    @property
    def arguments(self) -> dict[str, Any]:
        """The generated parameter triple, asked for by name.

        A property rather than a default inside :func:`_search`, so that an empty
        argument set really does mean *the tool's own defaults* --
        :data:`ARGUMENT_SETS`' first entry, and the defect
        ``test_absence_proof.py`` records having had when a helper seeded the
        dict itself.
        """
        return {"limit": self.limit, "maxTokens": self.max_tokens, "useDense": self.use_dense}

    @property
    def build_sensitivity(self) -> Sensitivity:
        """The level the withheld documents carry **when the index is written**.

        Above the ceiling for :data:`EXCLUDED_AT_BUILD`, so the builder writes no
        row for them; *within* it for :data:`RECLASSIFIED_NOT_PURGED`, so the
        build writes their text stamped with a level the ceiling admits and the
        reclassification below moves the item out from under it afterwards. That
        asymmetry is the whole of the difference between the two mechanisms, and
        it is why one is stopped by the builder and the other by
        :class:`~theurian.application.visibility.CanonicalVisibility`.
        """
        return ABOVE_CEILING_LEVEL if self.mechanism == EXCLUDED_AT_BUILD else WITHIN_CEILING_LEVEL

    @property
    def reclassified(self) -> tuple[str, ...]:
        """Which items are raised above the ceiling after the index is written."""
        if self.mechanism != RECLASSIFIED_NOT_PURGED:
            return ()
        return tuple(document.item_id for document in self.withheld(secret=True))

    def withheld(self, *, secret: bool) -> tuple[_Document, ...]:
        """The withheld documents as one side of the pair writes them.

        ``()`` for the control under :data:`PRESENT_IN_ONE_ONLY`: that side never
        held them, which is the difference the pair is made of.
        """
        if not secret and self.difference == PRESENT_IN_ONE_ONLY:
            return ()
        return tuple(
            _Document(
                item_id=f"architecture.withheld-{index:02d}",
                revision_id=_ulid("WH", index),
                title=self.withheld_titles[index],
                body=f"{filler} {pair[0] if secret else pair[1]}",
                sensitivity=self.build_sensitivity,
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


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

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
    discarded for failing to have one.
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


def _payload_prose() -> st.SearchStrategy[str]:
    """Filler a visible row can share no token and no trigram with."""
    word = st.text(alphabet=PAYLOAD_ALPHABET, min_size=3, max_size=9)
    return st.lists(
        st.lists(word, min_size=4, max_size=14).map(lambda words: " ".join(words) + "."),
        min_size=1,
        max_size=4,
    ).map("\n\n".join)


def _payload_title() -> st.SearchStrategy[str]:
    """A withheld document's title, drawn from the alphabet no visible row uses.

    So that :func:`test_no_above_ceiling_payload_appears_anywhere_a_caller_reads`
    can look for the title as a string. Drawn from :data:`VOCABULARY` it could
    not: a withheld document titled ``cache`` is indistinguishable from a visible
    document titled ``cache``, and the search for it reports the visible
    document's own title as a leak -- the oracle trap in miniature.
    """
    return st.lists(
        st.text(alphabet=PAYLOAD_ALPHABET, min_size=3, max_size=8), min_size=1, max_size=2
    ).map(" ".join)


def _visible_documents(sizes: tuple[int, ...]) -> st.SearchStrategy[tuple[_Document, ...]]:
    """An approved corpus of one of ``sizes`` documents, all within the ceiling.

    A *ladder* rather than a list length, because the boundary it straddles is
    exact. ``CANDIDATE_DEPTH`` is fifty: a pair whose corpora both fit inside one
    retriever's depth cannot tell a depth loop that counts *visible* rows from one
    that counts raw ones, which is the face that recovered a credential at the
    default token budget on the status axis.
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
                    sensitivity=WITHIN_CEILING_LEVEL,
                )
                for index, (title, body) in enumerate(pairs)
            )
        )
    )


#: Corpora smaller than one retriever's candidate depth.
BELOW_THE_DEPTH: Final = (2, 5, 12)

#: Corpora at and past it. ``CANDIDATE_DEPTH`` is 50 and a document of this size
#: is one chunk, so 51 documents matching a common term is 51 candidate rows for
#: fifty slots -- which is what makes a displaced row observable at all.
ACROSS_THE_DEPTH: Final = (49, 51, 62)

#: How deep :func:`_offered_by_the_index` asks, so its page is complete.
#:
#: Far past anything this file can build, because it answers "does any retriever
#: hand this row up", which a cut list cannot. Deliberately unrelated to
#: ``MAX_RESULTS``: that is the caller's bound, and borrowing it here asks a
#: different question.
_EXHAUSTIVE_DEPTH: Final = 500


def _cases(
    shape: tuple[str, str],
    sizes: tuple[int, ...] = BELOW_THE_DEPTH + ACROSS_THE_DEPTH,
) -> st.SearchStrategy[_Case]:
    """One generated pair, in the shape the caller names.

    **``shape`` is a parameter and not a draw**, which is the same decision
    :data:`ARGUMENT_SETS` and :func:`_visible_documents` record for their own
    axes: a small, known, load-bearing set is enumerated rather than sampled,
    because sampling buries the case that matters. Measured here before it was
    changed -- with the shape drawn from :data:`SHAPES` at four examples a cell,
    seed 119 produced 16 examples of one cell and 8 of each of the others across
    the equality's eight parametrisations. That is enough to have exercised all
    three, and it is a property of *that seed* rather than of the configuration:
    a strategy added or removed anywhere in this module re-rolls the sequence,
    and the day it drew zero of a mechanism, the suite would report three
    mechanisms and check two, with every test green and every id unchanged.
    Enumerated, a mutation to one gate turns a cell whose id names that
    mechanism RED.

    Built in one ``flatmap`` because two of the guarantees are relational:

    - **the query matches the visible corpus.** Its terms are sampled from the
      words this corpus actually contains, so ``count > 0`` is structural rather
      than hoped for. It is still asserted -- see :func:`_assert_the_pair_bites`.
    - **the withheld documents are reachable by that query.** When the filler
      shares the corpus vocabulary, the query's own terms are appended to it;
      when it does not, the query is made to carry the probe's payload. Either
      way the probe has an above-ceiling candidate for something to withhold.
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
            shape=st.just(shape),
            limit=st.sampled_from((1, 3, 10, MAX_RESULTS)),
            max_tokens=st.sampled_from((2_000, 8_000, MAX_BUDGET_TOKENS)),
            use_dense=st.booleans(),
        )

    return _visible_documents(sizes).flatmap(with_query)


def _assemble(  # noqa: PLR0913 - one parameter per generated knob
    *,
    visible: tuple[_Document, ...],
    terms: list[str],
    fillers: list[tuple[str, str]],
    titles: list[str],
    payloads: list[tuple[str, str]],
    shares_vocabulary: bool,
    names_the_secret: bool,
    shape: tuple[str, str],
    limit: int,
    max_tokens: int,
    use_dense: bool,
) -> _Case:
    """Turn the generated knobs into a pair, resolving the one dependency.

    ``names_the_secret`` is forced true when the filler shares no vocabulary,
    because otherwise the withheld document matches the query in neither project
    and the pair exercises nothing. That is the fourth combination of two
    booleans, and it is removed here rather than filtered away -- a claim about
    this function and not about the run, since hypothesis discards examples for
    reasons no strategy here controls.
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
    mechanism, difference = shape
    return _Case(
        visible=visible,
        withheld_filler=filler,
        withheld_titles=tuple(titles[: len(fillers)]),
        payloads=tuple(chosen),
        mechanism=mechanism,
        difference=difference,
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
            status=KnowledgeStatus.APPROVED,
            trust_level=TrustLevel.REVIEWED,
            sensitivity=document.sensitivity,
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
        status=KnowledgeStatus.APPROVED,
        current_revision_id=RevisionId(document.revision_id),
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=document.sensitivity,
        validity=ValidityPeriod(valid_from=created_at),
    )


def _write_active_state(paths: ProjectPaths, state: StateHash, updated_at: datetime) -> None:
    """Publish which canonical state this project is serving.

    Written by hand rather than through ``write_active_state`` because the
    filename must stay :data:`STATE_NOW`'s throughout: this builder writes one
    database and moves the pointer's *hash* across it, where ``migrate apply``
    would write a second file.

    ``migration_count`` is **0** because this builder runs no migration engine
    and writes no ``migration_history`` row. A pointer claiming otherwise makes
    every response in this suite carry ``integrity``, and the equalities below
    would then compare two damaged responses -- which they would do perfectly and
    pointlessly. :func:`_assert_the_pair_bites` is what refuses that.
    """
    paths.active_pointer.write_text(
        json.dumps(
            ActiveState(
                state_hash=state,
                database_filename=STATE_NOW.database_filename,
                migration_count=0,
                updated_at=updated_at.isoformat(),
            ).to_json()
        ),
        encoding="utf-8",
    )


def _reclassify_in_the_store(
    database: Path, paths: ProjectPaths, items: tuple[KnowledgeItem, ...]
) -> None:
    """The canonical write the index never saw: a ``changeSensitivity``, applied.

    ``put_item`` upserts, so this leaves the revision -- and every chunk built
    from it -- exactly where it was, which is the state
    :data:`RECLASSIFIED_NOT_PURGED` names.

    The expected surfaceable count is re-recorded for the reason the first write
    records it at all: a real reclassification arrives through ``migrate apply``,
    which re-records inside the same transaction. It does not *move* here -- a
    reclassified item is still surfaceable, and the record is written
    ceiling-blind (#119 phase 6) -- and it is written anyway, so that this
    builder does what the engine does rather than what this file happens to need.
    """
    with write_transaction(database, paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        for item in items:
            writer.put_item(item)
        writer.record_expected_surfaceable_count(ProjectId(PROJECT_ID))


def _build_project(
    root: Path,
    documents: tuple[_Document, ...],
    created_at: datetime,
    reclassified: tuple[str, ...] = (),
) -> ProjectRegistry:
    """One project, built and served under :data:`CEILING_GRANT`.

    Two canonical writes with an index build between them, because *when* the
    index was written relative to the canonical state is what decides which gate
    stops a row:

    1. every document is written at its build-time level and the index is built
       under this deployment's ceiling, so a document above that ceiling gets no
       chunk row (#119 phase 3) while one within it does;
    2. the items named in ``reclassified`` are raised above the ceiling, and the
       active pointer moves to :data:`STATE_NOW` while the index keeps
       :data:`STATE_AT_BUILD` -- which is what makes ``stale`` true and what
       leaves the canonical re-check as the only thing between that row and the
       caller.

    **The build's ceiling and the serving grant are one value, deliberately.**
    A build records the flavor it ran under in the pointer's
    ``indexedSensitivities``, and ``mcp.search._published_index`` stands aside a
    build whose flavor differs from the grant in force
    (``serving-profile-mismatch``, degrading to the canonical scan). Building
    under one ceiling and serving under another would therefore compare two
    *fallbacks* and hold over a path this file is not about -- the mistake
    ``test_the_ranked_path_withholds_a_document_reclassified_after_the_build``
    records having been written with once.

    ``include_unapproved`` is false, the shipped default: every document here is
    ``approved``, so nothing is off by default and ``indexesUnapproved`` carries
    the value ``theurian index build`` really produces.
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
        # What `theurian migrate apply` records at the end of its own write
        # transaction (#30 PR2), and this builder writes a store by hand instead
        # of running it. Omitting it does not fail loudly: three tools read the
        # record on every call and treat its absence as damage, so the whole pair
        # would answer with `integrity` present and every equality below would
        # compare two damaged responses and still pass.
        writer.record_expected_surfaceable_count(ProjectId(PROJECT_ID))

    built_from = STATE_AT_BUILD if reclassified else STATE_NOW
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
            visible_sensitivities=CEILING_LEVELS,
            secret_scan=SecretScanPolicy.BLOCK,
            include_unapproved=False,
        )
    )
    # `indexedSensitivities` goes through the shipped encoder rather than being
    # spelled out here: the serve path decodes it, and a second encoding of one
    # wire field is free to drift from the one under test.
    paths.active_index_pointer.write_text(
        json.dumps(
            {
                "indexBuildId": INDEX_BUILD_ID,
                "stateHash": str(built_from),
                "projectId": PROJECT_ID,
                "indexesUnapproved": False,
                "indexedSensitivities": encode_sensitivities(CEILING_LEVELS),
            }
        ),
        encoding="utf-8",
    )

    if reclassified:
        by_id = {document.item_id: document for document in documents}
        _reclassify_in_the_store(
            database,
            paths,
            tuple(
                replace(_item(by_id[item_id], created_at), sensitivity=ABOVE_CEILING_LEVEL)
                for item_id in reclassified
            ),
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
    # This builder stands in for `migrate apply` + `index build`, so it records
    # what those record: that this installation built the served state and index
    # (ADR-0004, SEC-7). Without it the serve path refuses both.
    provenance = BuildProvenance.for_registry(registry)
    provenance.record_state(paths.root, str(STATE_NOW))
    provenance.record_index(paths.root, INDEX_BUILD_ID)
    return registry


@dataclass(frozen=True, slots=True)
class _Pair:
    """Two projects that differ only in content no caller may read."""

    probe: ProjectRegistry
    control: ProjectRegistry
    probe_root: Path
    control_root: Path
    case: _Case


def _pair(base: Path, case: _Case) -> _Pair:
    created_at = datetime.now(UTC) - AGE_OFFSET
    probe_root = base / "probe"
    control_root = base / "control"
    return _Pair(
        probe=_build_project(
            probe_root, case.documents(secret=True), created_at, case.reclassified
        ),
        control=_build_project(
            control_root, case.documents(secret=False), created_at, case.reclassified
        ),
        probe_root=probe_root,
        control_root=control_root,
        case=case,
    )


# ---------------------------------------------------------------------------
# Asking, the way the transport asks
# ---------------------------------------------------------------------------


def _call(
    registry: ProjectRegistry,
    tool: str,
    grant: AuthorizationGrant = CEILING_GRANT,
    **arguments: Any,
) -> dict[str, Any]:
    """Invoke a tool through the same entry point the transport uses.

    ``grant`` defaults to :data:`CEILING_GRANT` rather than to
    ``build_server``'s own default, because every withholding in this file *is*
    the ceiling: a call issued under the shipped default would return the
    withheld rows, and the equalities would compare two answers that both hold
    the payload -- green, and measuring the opposite of what they claim.
    """

    async def invoke() -> Any:
        return await build_server(registry, grant).call_tool(tool, arguments)

    result = asyncio.run(invoke())
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(result.content[0].text)
    return loaded


def _search(registry: ProjectRegistry, case: _Case, **arguments: Any) -> dict[str, Any]:
    """One search, carrying the project and the query and **nothing else**.

    Every other parameter comes from the caller, so an empty ``arguments`` really
    does mean *the tool's own defaults* -- the property
    :func:`test_the_defaults_argument_set_really_sends_no_parameters` holds, and
    the defect ``test_absence_proof.py`` records having had when its own helper
    seeded the dict from the generated case.
    """
    return _call(
        registry,
        "knowledge.search",
        CEILING_GRANT,
        projectId=PROJECT_ID,
        query=case.query,
        **arguments,
    )


def _failing(
    registry: ProjectRegistry,
    tool: str,
    **arguments: Any,
) -> str:
    with pytest.raises(SdkToolError) as raised:
        _call(registry, tool, CEILING_GRANT, **arguments)
    return str(raised.value)


# ---------------------------------------------------------------------------
# Reading below every gate, to establish what a pair proves
# ---------------------------------------------------------------------------


def _offered_by_the_index(root: Path, case: _Case) -> set[str]:
    """Which item ids this query's retrievers hand up out of the index file.

    Read straight off :class:`SqliteIndexStore` **at every sensitivity**, below
    every gate, because that is the only place a pair's precondition can be
    established: a response that omits a withheld document proves nothing if no
    retriever ever offered it.

    ``visible_sensitivities`` is deliberately :data:`EVERY_SENSITIVITY` and not
    the deployment's grant. The question is *what the file holds and hands up*,
    and asking under the grant would filter the answer with the very predicate
    the guard exists to be independent of -- so a build that had stopped
    excluding above-ceiling rows would look exactly like one that had not.

    Both scored retrievers are asked. The trigram one is not decoration: it is
    the only one that can match a payload with no word boundary in it.

    **Both pages must be exhausted**, because every caller of this function uses
    the result to argue an *absence*, and an absence read off a truncated page
    says nothing -- the row could be one position below the cut.
    """
    index = SqliteIndexStore(ProjectPaths.of(root).index_for(INDEX_BUILD_ID))
    pages = (
        index.search_lexical(
            case.query,
            project_id=PROJECT_ID,
            limit=_EXHAUSTIVE_DEPTH,
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        ),
        index.search_substring(
            case.query,
            project_id=PROJECT_ID,
            limit=_EXHAUSTIVE_DEPTH,
            include_unapproved=False,
            visible_sensitivities=EVERY_SENSITIVITY,
        ),
    )
    assert all(page.exhausted for page in pages), (
        f"a truncated page cannot support an absence: every caller of this reads "
        f"'not offered' off the result, and a row below the cut is also not "
        f"offered. This corpus has outgrown _EXHAUSTIVE_DEPTH={_EXHAUSTIVE_DEPTH}"
    )
    return {row.item_id for page in pages for row in page.rows}


def _indexed_text(root: Path) -> list[tuple[str, str]]:
    """Every ``(item id, chunk text)`` this project's published build holds.

    Read with plain SQL rather than through a retriever, because the claim it
    supports is about the *file* and not about what a query can reach: an FTS5
    external-content table scores what it returns against ``N``, ``avgdl`` and
    the per-term document frequencies computed over every row in ``chunks``, so a
    row no query can return still moves the score of one that can.

    Ordered, so two builds are compared as sequences and a duplicated row is a
    difference rather than a set collapsing onto itself.
    """
    path = ProjectPaths.of(root).index_for(INDEX_BUILD_ID)
    with closing(sqlite3.connect(path)) as connection:
        return [
            (str(item_id), str(text))
            for item_id, text in connection.execute(
                "SELECT item_id, text FROM chunks ORDER BY item_id, ordinal"
            )
        ]


def _levels_in_the_store(root: Path, item_ids: set[str]) -> set[Sensitivity]:
    """The levels the canonical store currently records for ``item_ids``.

    Read from the store rather than from the ``_Case`` that asked for them,
    because the ``_Case`` is the thing under suspicion: it decides the level, and
    a generator that stopped raising it would report its own intention back to
    the guard. The store is where the gate reads from.
    """
    paths = ProjectPaths.of(root)
    context = RequestContext(project_id=ProjectId(PROJECT_ID))
    with SqliteCanonicalStore(paths.database_for(STATE_NOW)) as store:
        items = [store.get_item(context, ItemId(item_id)) for item_id in sorted(item_ids)]
    assert all(item is not None for item in items), (
        f"the store must hold every withheld item, or the level below is read off nothing: "
        f"{sorted(item_ids)}"
    )
    return {item.sensitivity for item in items if item is not None}


def _assert_the_pair_bites(pair: _Pair, probe: dict[str, Any]) -> None:
    """Refuse to pass on an example that proved nothing.

    Six ways a generated pair can be green while testing nothing, and each has
    happened to a suite of this shape:

    - the answer is empty, so two empty answers are being compared;
    - the payloads are equal, so the two projects are the same project;
    - the store does not actually hold the withheld items above the ceiling, so
      nothing withholds them and every equality holds vacuously;
    - **the mechanism is not the one the case names** -- the index either offers
      the withheld rows or does not, and which of the two says whether the
      builder or the canonical re-check is doing the work. ``test_absence_proof.py``
      records what skipping this costs: with every withheld document stopped by
      a retriever's own ``WHERE``, deleting the canonical gate outright left ten
      generated tests green;
    - the withheld row is in the answer, which is a leak rather than a bad pair;
    - **the pair is answering as a damaged project**, so every equality is
      comparing two damage reports. Measured on the status axis rather than
      imagined: two separate builder defects put ``integrity`` on every response
      in that suite and nothing failed.
    """
    case = pair.case
    root = pair.probe_root
    withheld_ids = {document.item_id for document in case.withheld(secret=True)}

    assert probe["count"] > 0, "two empty answers prove nothing about withholding"
    assert probe["retrieval"]["indexed"] is True, (
        f"the probe answered from the canonical scan rather than its published build "
        f"({probe['retrieval']}), so this pair says nothing about the index half of the axis. "
        f"The build's recorded flavor and the serving grant must be the same value."
    )
    assert not probe["retrieval"]["fallbackReason"], (
        f"the ranked path stood the build aside ({probe['retrieval']['fallbackReason']})"
    )
    assert "integrity" not in probe, (
        f"the pair answers as a damaged project ({probe['integrity']}), so every equality in "
        f"this file is comparing two damage reports rather than two healthy responses"
    )
    assert all(secret != decoy for secret, decoy in case.payloads), (
        "the two projects must actually differ"
    )
    assert _levels_in_the_store(root, withheld_ids) == {ABOVE_CEILING_LEVEL}, (
        f"the store must hold every withheld item above this deployment's ceiling, or it is "
        f"withheld by nothing: {sorted(withheld_ids)}"
    )

    offered = _offered_by_the_index(root, case)
    if case.mechanism == EXCLUDED_AT_BUILD:
        assert not (withheld_ids & offered), (
            f"a build under this deployment's ceiling still offers "
            f"{sorted(withheld_ids & offered)} to the retrievers, so the withheld text is in "
            f"the file and in the FTS5 collection statistics every visible row is scored "
            f"against (#119 phase 3, ADR-0025 part 1)"
        )
        assert _indexed_text(root) == _indexed_text(pair.control_root), (
            "the two builds do not hold the same text. That is what carries this mechanism: "
            "the corpora differ only inside documents neither build was allowed to write, so "
            "a difference here is withheld content reaching the file -- and from there the "
            "collection statistics -- without being returned by anything"
        )
    else:
        assert withheld_ids & offered, (
            "a document reclassified after the build keeps chunks stamped with the level the "
            "ceiling admits, so a retriever must offer them on the caller's own flags -- if it "
            "does not, the canonical re-check is never consulted and this pair says nothing "
            "about it"
        )
        assert _visible_text(pair.probe_root) == _visible_text(pair.control_root), (
            "the two builds disagree about the text of a document both callers may read, so a "
            "separation below would be BM25 arithmetic over the visible corpus rather than a "
            "gate defect"
        )

    assert not withheld_ids & {result["itemId"] for result in probe["results"]}, (
        "and no withheld document may be in the answer"
    )


def _visible_text(root: Path) -> list[tuple[str, str]]:
    """:func:`_indexed_text`, restricted to the documents both callers may read."""
    return [row for row in _indexed_text(root) if not row[0].startswith("architecture.withheld-")]


# ---------------------------------------------------------------------------
# The generated equalities
# ---------------------------------------------------------------------------

#: Deadline off because one example builds two SQLite databases and two index
#: files; ``database=None`` because the default example database writes
#: ``.hypothesis/`` into whatever directory pytest was launched from, which for
#: this repository is the repository.
#:
#: ``derandomize`` and an explicit :data:`EXAMPLE_SEED` together, for the reason
#: ``test_absence_proof.py`` measured: ``derandomize`` alone derives its seed
#: from ``inspect.getsource`` of the test, so a prose-only docstring edit
#: re-rolls every example and silently changes what was checked. The seed freezes
#: the sequence against source edits; ``derandomize`` stays because it is what
#: keeps a future test here reproducible without one.
_GENERATED = settings(
    deadline=None,
    derandomize=True,
    database=None,
)

#: Fixed so a docstring edit cannot silently change what was checked. Any
#: constant would do; this one is the issue number.
EXAMPLE_SEED: Final = 119

#: The caller's own parameters, enumerated rather than generated.
#:
#: A small, known, load-bearing set, and sampling them buries the case that
#: matters: whether a displaced candidate is *observable* needs ``limit`` at the
#: published maximum **and** a budget that lets fifty results through, and two
#: independent draws land on that pair about one example in twelve.
#:
#: ``defaults`` is ``{}`` and has to stay ``{}``: it means *the tool's own
#: defaults*, not a restatement of them, so it goes on testing whatever
#: ``knowledge_search`` defaults to rather than what this file thought it
#: defaulted to when the line was written.
ARGUMENT_SETS: Final[tuple[tuple[dict[str, Any], str], ...]] = (
    ({}, "defaults"),
    ({"limit": MAX_RESULTS}, "at-the-depth"),
    ({"limit": MAX_RESULTS, "maxTokens": MAX_BUDGET_TOKENS}, "generous"),
    ({"limit": MAX_RESULTS, "maxTokens": MAX_BUDGET_TOKENS, "useDense": True}, "dense"),
)


#: The published values this axis was measured moving on the status axis, named
#: so that a failure says *which* family member separated rather than printing a
#: whole-response diff. Each is a member of the enumeration in
#: ``retrieval_service``'s table: a count computed before a gate, a token total
#: priced on candidates, and a score computed against collection statistics the
#: withheld rows contributed to.
#:
#: They are diagnostics and not the property. The property is the whole-response
#: equality that follows them, and deleting these three would weaken nothing --
#: which is exactly why they are asserted first rather than instead.
def _published_family(answer: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": answer["count"],
        "usedTokens": answer["retrieval"]["usedTokens"],
        "fusedScore": [result.get("fusedScore") for result in answer["results"]],
    }


#: ``SHAPES`` as pytest ids, so a cell says which mechanism it exercised and
#: which way its two corpora differ. A failure that names
#: ``reclassified-not-purged-one-payload-apart`` locates the gate; one that named
#: an example number would not.
SHAPE_IDS: Final = tuple(f"{mechanism}-{difference}" for mechanism, difference in SHAPES)


@pytest.mark.parametrize(
    "arguments", [pair[0] for pair in ARGUMENT_SETS], ids=[pair[1] for pair in ARGUMENT_SETS]
)
@pytest.mark.parametrize("shape", SHAPES, ids=SHAPE_IDS)
@pytest.mark.parametrize(
    "sizes", (BELOW_THE_DEPTH, ACROSS_THE_DEPTH), ids=("below-the-depth", "across-the-depth")
)
@seed(EXAMPLE_SEED)
@settings(_GENERATED, max_examples=3)
@given(data=st.data())
def test_no_published_value_varies_with_an_above_ceiling_document(
    tmp_path_factory: pytest.TempPathFactory,
    sizes: tuple[int, ...],
    shape: tuple[str, str],
    arguments: dict[str, Any],
    data: st.DataObject,
) -> None:
    """ADR-0025 part 4, SEC-13, T-17, FR-R4. The property, over generated pairs.

    One query against two corpora that differ only in documents this deployment
    may not disclose, and the **entire response** must be equal -- ``count``,
    every field of every hit including which chunk was excerpted and what it
    scored, and every key of the ``retrieval`` block.

    Nothing is masked, so nothing has to be argued for: the three values a
    two-project comparison would normally exclude are held equal as inputs, and
    the module docstring says what that costs.

    **Three axes are enumerated and not generated**, each for its own measured
    reason: the corpus size, because the candidate depth is an exact boundary and
    a corpus below it cannot show a displaced row at all; the caller's parameter
    set, because whether a displaced candidate is observable depends on an exact
    ``limit`` and an exact budget; and the shape, so that a mutation to one gate
    fails a cell whose id names that gate (see :func:`_cases`). What is generated
    is everything a person would otherwise have had to think of: what the
    documents say, what the withheld ones say, which of them the query reaches,
    and how long each is.

    RED with the disclosure gate removed at either end: deleting the build-side
    exclusion puts the withheld text back in the probe's index, where the FTS5
    collection statistics price the visible rows against it; deleting the
    canonical re-check publishes the reclassified document itself.
    """
    case = data.draw(_cases(shape, sizes))
    pair = _pair(tmp_path_factory.mktemp("ceiling"), case)

    probe = _search(pair.probe, case, **arguments)
    control = _search(pair.control, case, **arguments)

    _assert_the_pair_bites(pair, probe)
    assert _published_family(probe) == _published_family(control), (
        "a published count, cost or score varies with a document above this deployment's "
        "ceiling -- the three members of the disclosure family measured moving on the status "
        "axis, named here so the whole-response diff below is not the first thing a reader sees"
    )
    assert probe == control, (
        "every published value must equal what the same query returns against a corpus whose "
        "above-ceiling documents say something else, or hold none of them at all"
    )


@pytest.mark.parametrize("shape", SHAPES, ids=SHAPE_IDS)
@seed(EXAMPLE_SEED)
@settings(_GENERATED, max_examples=4)
@given(data=st.data())
def test_the_status_tool_counts_the_same_over_both_corpora(
    tmp_path_factory: pytest.TempPathFactory,
    shape: tuple[str, str],
    data: st.DataObject,
) -> None:
    """#119 phase 6, SEC-13, T-17. The other tool, and the statistic it publishes.

    ``knowledge.search`` and ``knowledge.get`` are not the whole surface. A
    caller who cannot read a document can still ask how many documents this
    project holds, and until phase 6 the answer counted every surfaceable item
    whatever the ceiling -- *a statistic over rows the caller may not see*, which
    is a member of the disclosure family in its own right and the one a reader
    checking the retrieval path would never open.

    The pair is the same pair, so under :data:`PRESENT_IN_ONE_ONLY` the probe's
    store holds up to three items the control's does not, and both must publish
    the same ``itemCount`` and the same ``itemsByStatus``.

    **Every key is compared, including the two that are constants of this
    builder.** ``stateHash`` and ``appliedMigrations`` are declared rather than
    derived here, so their equality says nothing -- stated because a value held
    constant looks exactly like a value that was checked. ``itemCount`` and
    ``itemsByStatus`` are a live read of the store, and they are what this test
    is for.

    The absence of ``integrity`` is asserted rather than assumed: it is what says
    the #30 comparison is still running on the ungated population at both ends.
    A deployment that reported damage because of its own ceiling would satisfy an
    equality of counts while making a false security claim.

    The corpus size is *not* enumerated here, and that is the one axis this test
    can leave to the generator: a count over the store has no candidate depth to
    straddle, so the ladder that makes the retrieval equality able to fail buys
    this nothing but time.

    RED with the ceiling dropped from ``count_surfaceable_by_status``: the probe
    reports its above-ceiling items and the control has none to report.
    """
    case = data.draw(_cases(shape))
    pair = _pair(tmp_path_factory.mktemp("ceiling"), case)

    probe = _call(pair.probe, "knowledge.status", projectId=PROJECT_ID)
    control = _call(pair.control, "knowledge.status", projectId=PROJECT_ID)

    _assert_the_pair_bites(pair, _search(pair.probe, case, **case.arguments))
    assert probe["itemCount"] == len(case.visible), (
        f"the counts must be the visible corpus and nothing else: {probe['itemCount']} published "
        f"over {len(case.visible)} documents this deployment may disclose and "
        f"{len(case.withheld(secret=True))} it may not"
    )
    assert "integrity" not in probe and "integrity" not in control, (
        f"a healthy project reported damage ({probe.get('integrity')}, {control.get('integrity')})"
        f" -- the #30 comparison must stay on the ungated population at both ends, or every "
        f"restricted deployment reports a corrupt database"
    )
    assert probe == control, (
        "knowledge.status published a different answer over a corpus holding above-ceiling "
        "documents than over one that does not hold them"
    )


@pytest.mark.parametrize("shape", SHAPES, ids=SHAPE_IDS)
@seed(EXAMPLE_SEED)
@settings(_GENERATED, max_examples=6)
@given(data=st.data())
def test_no_above_ceiling_payload_appears_anywhere_a_caller_reads(
    tmp_path_factory: pytest.TempPathFactory, shape: tuple[str, str], data: st.DataObject
) -> None:
    """SEC-13. Equality is not the same claim as absence, and both are wanted.

    Two identical responses can both carry the secret. The equality above would
    not notice a leak of something both projects *share* -- and under
    :data:`ONE_PAYLOAD_APART` the withheld documents' titles and ids are shared
    by construction, so a gate that published either would leave it green.

    Checked over the serialised response rather than field by field, because a
    field list is the thing this file exists to stop maintaining. ``query`` is
    excluded and only ``query``: a caller who asks for a string is echoed that
    string back, which discloses nothing they did not already hold, and the
    generator deliberately puts the probe's payload there in two of its shapes.
    """
    case = data.draw(_cases(shape))
    pair = _pair(tmp_path_factory.mktemp("ceiling"), case)

    probe = _search(pair.probe, case, **case.arguments)

    _assert_the_pair_bites(pair, probe)
    published = json.dumps({key: value for key, value in probe.items() if key != "query"})
    for secret in case.secrets:
        assert secret not in published, "an above-ceiling payload reached the response"
    for withheld in case.withheld(secret=True):
        assert withheld.title not in published, "so did an above-ceiling document's title"
        assert withheld.item_id not in published, "so did its id"


#: One more visible document than a retriever's candidate depth, so the fifty
#: slots are contested and a row spent on a withheld candidate is a row a visible
#: one does not get.
_ONE_PAST_THE_DEPTH: Final = 51

#: The two payloads the deterministic pair below is built from: equal length, one
#: character apart, drawn from the alphabet no visible row can produce. Written
#: down rather than generated, because the test that uses them asserts a *shape*
#: -- one corpus matching the query on a term the other does not carry -- and a
#: shape a generator happens to produce some of the time is not one a suite can
#: report as covered.
_PROBE_PAYLOAD: Final = "PQRSTUVWXYZPQRST"
_CONTROL_PAYLOAD: Final = "PQRSTUVWXYZPQRSV"


def _a_pair_that_contests_the_candidate_depth(base: Path) -> tuple[_Pair, _Case]:
    """A corpus built so that a withheld row *displaces* a visible one.

    Fifty-one visible documents all matching one ordinary word, so a retriever's
    fifty slots are already oversubscribed, plus three documents this deployment
    may not disclose whose bodies carry nothing but a payload. The query names
    the word **and the probe's payload**, so the probe's three withheld documents
    match on the trigram retriever -- ``to_trigram_expression`` emits the payload
    as a phrase, and the control's payload differs from it, so no row in the
    control matches it at all.

    That asymmetry is the point. The probe's retriever hands up three rows the
    gate then removes; the control's hands up none. If the depth loop counted raw
    rows rather than cleared ones, the probe would answer with three visible
    documents fewer than the control -- a caller measuring the difference reads
    off how many withheld rows their query reached, which is the fourth face in
    ``retrieval_service``'s table and the one that recovered a credential at the
    default budget on the status axis.

    Both bodies are the same length and the two alphabets are disjoint, so the
    two indexes agree on every collection statistic a *visible* row is scored
    against: ``N``, ``avgdl`` and the document frequency of every ``a``-``o``
    term are identical. A separation here is therefore the gate and not BM25.
    """
    visible = tuple(
        _Document(
            item_id=f"architecture.visible-{index:02d}",
            revision_id=_ulid("VS", index),
            title="handle",
            body=f"cache manifold beacon domain machine combine median nominal {index}.",
            sensitivity=WITHIN_CEILING_LEVEL,
        )
        for index in range(_ONE_PAST_THE_DEPTH)
    )
    case = _Case(
        visible=visible,
        # Long enough that the payload dominates this document's trigram score,
        # and identical in length between the two projects.
        withheld_filler=tuple(f"{_PROBE_PAYLOAD[:6]} " * 8 for _ in range(3)),
        withheld_titles=("QQRRSS", "TTUUVV", "WWXXYY"),
        payloads=((_PROBE_PAYLOAD, _CONTROL_PAYLOAD),) * 3,
        mechanism=RECLASSIFIED_NOT_PURGED,
        difference=ONE_PAYLOAD_APART,
        query=f"manifold {_PROBE_PAYLOAD}",
        limit=MAX_RESULTS,
        max_tokens=MAX_BUDGET_TOKENS,
        use_dense=False,
    )
    return _pair(base, case), case


def test_a_withheld_candidate_does_not_cost_a_visible_document_its_slot(
    tmp_path: Path,
) -> None:
    """SEC-13, T-17, ADR-0025 part 4. Candidate displacement, built rather than drawn.

    The generated equality above covers this family when the draw happens to land
    on it, and **it was measured not landing on it**: the mutation that replaces
    the depth loop with a single ``CANDIDATE_DEPTH`` fetch survived every
    parametrisation of ``test_no_published_value_varies_with_an_above_ceiling_
    document`` at three examples a cell. That is the same mutation
    ``test_absence_proof.py`` records surviving twenty-five generated examples of
    its own equality until both the corpus size and the caller's parameters were
    enumerated. Here the *corpus shape* has to be enumerated as well, because the
    face needs three things at once: more visible matches than the depth, a
    withheld row ranked inside it, and a control in which the same row does not
    match at all.

    So this builds that corpus by hand and asserts each precondition before the
    equality, in the order that makes a failure readable:

    - fifty-one visible documents match the query's ordinary term, so the fifty
      slots are contested;
    - the probe's retriever really offers the withheld rows **within** those
      slots, read below every gate -- otherwise nothing is displaced and the
      equality holds for any implementation;
    - the control's retriever offers none of them, so the two pages differ by
      exactly the withheld rows;
    - and the two responses are equal anyway.

    RED under the depth-loop mutation, which is what the generated cells could
    not say.
    """
    pair, case = _a_pair_that_contests_the_candidate_depth(tmp_path)
    withheld_ids = {document.item_id for document in case.withheld(secret=True)}

    probe = _search(pair.probe, case, **case.arguments)
    control = _search(pair.control, case, **case.arguments)

    probe_slots = _offered_within_the_depth(pair.probe_root, case)
    control_slots = _offered_within_the_depth(pair.control_root, case)

    assert len(case.visible) > MAX_RESULTS, (
        f"the visible corpus must outnumber a retriever's {MAX_RESULTS} slots, or no visible "
        f"document can be displaced by anything"
    )
    assert withheld_ids <= probe_slots, (
        f"the probe's trigram retriever must offer every withheld row inside its first "
        f"{MAX_RESULTS} slots, or the gate removes nothing that a visible row wanted: it "
        f"offered {sorted(probe_slots & withheld_ids)}"
    )
    assert not (withheld_ids & control_slots), (
        "and the control's must offer none of them -- the query carries the probe's payload "
        "as a trigram phrase, and the control's payload differs from it -- or the two pages "
        "lose the same rows and there is no asymmetry to detect"
    )
    _assert_the_pair_bites(pair, probe)
    assert _published_family(probe) == _published_family(control), (
        "a withheld candidate cost a visible document its slot: the probe's count, cost or "
        "scores differ from a corpus whose withheld rows this query does not reach"
    )
    assert probe == control, (
        "and the whole response must be equal, not only the three fields named above"
    )


def _offered_within_the_depth(root: Path, case: _Case) -> set[str]:
    """The item ids the trigram retriever hands up in its first ``MAX_RESULTS`` rows.

    Deliberately *cut* rather than exhausted, which is the opposite of
    :func:`_offered_by_the_index`'s contract and is right here: the claim this
    supports is that a withheld row occupies one of a fixed number of slots, so
    the page has to be the fixed-size one a single-fetch implementation would
    take. Read at every sensitivity, below the gate, for the reason that function
    records.
    """
    index = SqliteIndexStore(ProjectPaths.of(root).index_for(INDEX_BUILD_ID))
    page = index.search_substring(
        case.query,
        project_id=PROJECT_ID,
        limit=MAX_RESULTS,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    return {row.item_id for row in page.rows}


#: The one shape that can pose the question below: the control has to be a corpus
#: that genuinely never held the id.
_NEVER_HELD_IT: Final = (EXCLUDED_AT_BUILD, PRESENT_IN_ONE_ONLY)


@seed(EXAMPLE_SEED)
@settings(_GENERATED, max_examples=8)
@given(case=_cases(_NEVER_HELD_IT))
def test_an_above_ceiling_id_is_refused_in_the_words_that_refuse_one_that_never_existed(
    tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    """SEC-13, T-17. The refusal, compared **across the pair** and not within one.

    ``test_absence_proof.py`` already asks whether a withheld id and an absent id
    are refused in one message, and it already parametrizes that over the
    ceiling. This is the two-corpora form of the same question, which that test
    cannot ask: the id is refused *because it is above the ceiling* in the probe
    and refused *because it does not exist* in the control, and the two refusals
    have to be one sentence. An implementation that told the truth -- "you are
    not cleared for this item" -- would satisfy every within-corpus comparison
    and confirm both that the item exists and what class it is in.

    :data:`_NEVER_HELD_IT` is the only shape that can pose it, and it is fixed
    here rather than parametrized: the other shape's control holds the same item
    at the same level, which is the question the other module already answers.
    ``knowledge.get`` reads canonical state and never the index, so the two
    *mechanisms* are one code path from here -- what varies is only whether the
    control has a row at all.

    Four arms: the id in the probe, the same id in the control that never had it,
    an id neither corpus ever minted, and -- as the guard on the guard -- a
    *visible* id, which must come back rather than be refused. Without the last
    one, three identical refusals would be satisfied by a daemon that refuses
    everything.
    """
    pair = _pair(tmp_path_factory.mktemp("ceiling"), case)
    withheld_id = case.withheld(secret=True)[0].item_id
    visible_id = case.visible[0].item_id

    above_the_ceiling = _failing(
        pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=withheld_id
    )
    never_held = _failing(pair.control, "knowledge.get", projectId=PROJECT_ID, itemId=withheld_id)
    never_minted = _failing(pair.probe, "knowledge.get", projectId=PROJECT_ID, itemId=NO_SUCH_ITEM)
    present = _call(
        pair.probe, "knowledge.get", CEILING_GRANT, projectId=PROJECT_ID, itemId=visible_id
    )

    assert case.withheld(secret=False) == (), (
        "the control must never have held the id, or this compares two corpora that both do"
    )
    assert above_the_ceiling == never_held, (
        "an id this deployment may not disclose must be refused in the words a corpus that "
        "never held it refuses -- otherwise the refusal itself says the item exists"
    )
    assert above_the_ceiling == never_minted.replace(NO_SUCH_ITEM, withheld_id), (
        "and in the words an id no corpus ever minted is refused in"
    )
    assert present["itemId"] == visible_id, (
        "the guard on this guard: an id the caller may read must come back, or the three "
        "refusals above agree because this daemon serves nothing"
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
    *visible* row -- ``nHit``, and through it ``idf`` -- and every separation
    would be T-17a's content channel rather than a gate defect. The tests above
    would then fail for a reason that is not a leak, and the obvious response to
    that failure is to relax them.

    This file has its own copy of :data:`VOCABULARY`, so it needs its own copy of
    this guard: the module it was copied from cannot notice a ``p``-``z`` letter
    added here.

    Checked after case folding, because both tokenizers this index uses fold.
    """
    visible = {character for word in VOCABULARY for character in word.casefold()}
    payload = set(PAYLOAD_ALPHABET.casefold())

    assert not visible & payload, (
        f"the alphabets overlap on {sorted(visible & payload)}; a payload could then change "
        f"the `nHit` of a term a visible row carries"
    )
    assert len(PAYLOAD_ALPHABET) > 1, "a one-character alphabet cannot make two payloads differ"


def test_the_withheld_level_is_outside_the_ceiling_this_file_serves() -> None:
    """The other premise, and the one a single constant edit would break silently.

    Every assertion in this module reads "the withheld documents are absent", and
    a document that was never above the ceiling is absent in a way that proves
    nothing. Three constants have to stay in the right relation to each other,
    and nothing else here would notice if they stopped.
    """
    assert ABOVE_CEILING_LEVEL not in CEILING_LEVELS, (
        "the level this file raises its withheld documents to is one this deployment serves, "
        "so nothing below withholds anything"
    )
    assert WITHIN_CEILING_LEVEL in CEILING_LEVELS, (
        "and the visible corpus must be inside the ceiling, or the pairs answer nothing at all"
    )
    assert CEILING_GRANT.sensitivities == CEILING_LEVELS, (
        "the grant every call runs under must be the ceiling this file names"
    )


def test_the_shape_grid_names_the_cell_it_leaves_out() -> None:
    """That the missing cell is missing on purpose, asserted rather than commented.

    :data:`SHAPES` is written as a list of valid cells so that the T-17a cell --
    a reclassified, unpurged build against a corpus that never held the row -- is
    absent by construction. A reader counting three where the two axes offer four
    needs the omission to be checkable, and a future edit that "completes the
    grid" should fail here and read ADR-0025 part 2 before deleting this test.
    """
    mechanisms = {EXCLUDED_AT_BUILD, RECLASSIFIED_NOT_PURGED}
    differences = {ONE_PAYLOAD_APART, PRESENT_IN_ONE_ONLY}

    assert set(SHAPES) == {
        (mechanism, difference) for mechanism in mechanisms for difference in differences
    } - {(RECLASSIFIED_NOT_PURGED, PRESENT_IN_ONE_ONLY)}, (
        "the shape grid has changed. The one cell this file must not generate is a stale "
        "published build that still holds the reclassified rows, compared against a corpus "
        "that never held them: there the two index files genuinely differ, the visible rows "
        "are scored against different collection statistics, and the equality fails honestly "
        "(T-17a; ADR-0025 part 2 records it as the direction the purge cannot close)"
    )


def _a_corpus_the_parameters_move(tmp_path: Path) -> tuple[ProjectRegistry, _Case]:
    """Twenty matching documents, and a ``_Case`` whose triple is not the defaults.

    Shared by the two guards below, which are mirror images: one asks whether an
    *empty* parameter set reaches the tool empty, the other whether a *stated*
    one reaches it at all. Both need the same precondition -- a corpus on which
    the parameters change the answer -- and each asserts it rather than assuming
    the other did.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    documents = tuple(
        _Document(
            item_id=f"architecture.visible-{index:02d}",
            revision_id=_ulid("VS", index),
            title="handle",
            body=f"cache manifold beacon domain machine combine median nominal {index}.",
            sensitivity=WITHIN_CEILING_LEVEL,
        )
        for index in range(20)
    )
    registry = _build_project(tmp_path / "one", documents, created_at)
    case = _Case(
        visible=documents,
        withheld_filler=(),
        withheld_titles=(),
        payloads=(),
        mechanism=EXCLUDED_AT_BUILD,
        difference=ONE_PAYLOAD_APART,
        query="manifold",
        limit=MAX_RESULTS,
        max_tokens=MAX_BUDGET_TOKENS,
        use_dense=True,
    )
    return registry, case


def test_the_parameters_a_case_carries_reach_the_tool(tmp_path: Path) -> None:
    """That :attr:`_Case.arguments` is not quietly empty.

    It is the parameter source for the two generated tests that want the drawn
    triple rather than an enumerated set. Nothing else here would hold it:
    ``test_absence_proof.py`` measured the mutation ``return {}`` on its own
    version of this property coming back **SURVIVED** against the whole suite,
    which would have collapsed eighteen examples onto the tool defaults with
    every test still green.

    The second assertion is the anti-vacuity one and it is not the same fact as
    the first: a corpus where the parameters change nothing would satisfy "the
    triple reaches the tool" with any implementation at all.
    """
    registry, case = _a_corpus_the_parameters_move(tmp_path)

    as_the_case_asks = _search(registry, case, **case.arguments)
    tool_defaults = _search(registry, case)

    assert case.arguments == {
        "limit": case.limit,
        "maxTokens": case.max_tokens,
        "useDense": case.use_dense,
    }, "the triple must be the case's own, not a fixed one"
    assert as_the_case_asks != tool_defaults, (
        "a case's parameters must reach the tool, or the sweeps that use them run at the "
        "defaults and nothing says so"
    )


def test_the_defaults_argument_set_really_sends_no_parameters(tmp_path: Path) -> None:
    """That ``ARGUMENT_SETS``' first entry is not quietly a fifth generated draw.

    In the module this builder is modelled on it was: the search helper seeded
    its dict from the generated case and let the caller override, so ``{}``
    overrode nothing and ``defaults`` ran generated triples instead of the tool's
    own -- across 24 calls, the tool-default triple appeared zero times, and
    every one of those examples passed.

    Asserted behaviourally rather than by inspecting what :func:`_search` builds,
    because the defect is not that a dict has extra keys -- it is that a named
    argument set does not test what its name says.
    """
    registry, case = _a_corpus_the_parameters_move(tmp_path)

    nothing_stated = _search(registry, case)
    generous = _search(registry, case, **dict(ARGUMENT_SETS[2][0]))

    assert nothing_stated["count"] <= 10, (
        "an empty argument set must reach the tool as no parameters at all, so the tool's own "
        "`limit` default of 10 bounds the answer -- `defaults` is otherwise testing whatever "
        "the generator drew"
    )
    assert nothing_stated != generous, (
        "and this corpus must be one the parameters actually move, or the assertion above "
        "holds for any implementation"
    )


# ---------------------------------------------------------------------------
# The third mechanism: a reclassification purged by `migrate apply` (phase 5)
# ---------------------------------------------------------------------------
#
# Everything above is built through the application layer, because a generated
# test builds a pair per example. This mechanism cannot be: the purge is wired
# into `theurian migrate apply`, and the property is precisely that **no second
# `index build` is needed** — one apply publishes a build a search can go on
# using, with the reclassified rows gone from it (ADR-0024 decision 5, extended
# to `changeSensitivity` by ADR-0025 part 2).
#
# `test_sensitivity_purge.py` asserts that over the *file* — no chunk row, no
# summary node, no `fts5vocab` term — and compares the purged *forest* against
# one built above the ceiling. What it does not compare is the leaf-side
# response, which is what ADR-0025 part 4 records as owed and what this section
# is.

#: Both shipped projects register under this id, in separate registries, so
#: `projectId` is equal as an input rather than compared as an output.
SHIPPED_PROJECT_ID: Final = "ceiling-pair"

#: Two phrases, because the channel is a *reweighting between* them: one query
#: term scaled by the withheld document's presence, one carried only by the
#: visible pair. A single-phrase query scales every row by the same factor and
#: preserves order.
SHIPPED_QUERY: Final = "quarantine ledger"

#: A marker no other body contains, so finding a fragment of it in a response is
#: proof it came out of the reclassified document and nowhere else.
SHIPPED_SECRET_MARKER: Final = "ROTATEME7SECRET"  # noqa: S105 - a test marker, not a credential

_SHIPPED_DOC_MIGRATION: Final = """apiVersion: theurian.dev/v1
id: {mid}
createdAt: 2026-08-05T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: {rid}
    contentFile: ../knowledge/architecture/{slug}.md
    contentSha256: {pin}
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      sensitivity: internal
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""

#: The reclassification. Its id sorts after every create migration, so a replay
#: applies it last whatever order the loader reads the directory in.
_SHIPPED_RECLASSIFY_MIGRATION: Final = """apiVersion: theurian.dev/v1
id: 01K1WRECAA01234567890ABCDE
createdAt: 2026-08-05T11:00:00+09:00
author: engineer@example.com
operations:
  - op: changeSensitivity
    itemId: architecture.secret
    sensitivity: restricted
    reason: reclassified after the index was built
"""

#: The reclassified document, tuned to flip the visible pair. Long and dense in
#: `quarantine`, so its presence moves both BM25's `avgdl` and the `idf` of the
#: phrase the two visible rows share -- enough to reorder them. The marker is
#: appended once, a rare token the query never names, so it changes only the
#: length. Without the purge the probe's stale build ranks the visible pair the
#: other way from `never-held-it`, which is what makes the equality able to fail.
_SHIPPED_SECRET_BODY: Final = (
    "# Payment tenant incident\n\n"
    + "".join(
        f"## {section}\n\nThe quarantine rehearsal for the payment tenant is recorded here. " * 6
        + "\n\n"
        for section in ("Rehearsal", "Finding")
    )
    + f"\n{SHIPPED_SECRET_MARKER}\n"
)

#: The visible pair, deliberately close and opposite: one leans on `quarantine`,
#: the other on `ledger`, each naming the other's term once, so a shift in the
#: reclassified document's weight moves them against each other rather than
#: together.
_SHIPPED_VISIBLE_BODY: Final = {
    "isolation": (
        "# Tenant isolation\n\n"
        + "The quarantine step isolates one tenant. " * 3
        + "The ledger records that it happened.\n"
    ),
    "retention": (
        "# Records retention\n\n"
        + "The ledger keeps records for seven years. " * 3
        + "The quarantine names it.\n"
    ),
}

#: Filler carrying neither query term: it lifts N without touching `avgdl` or the
#: phrases' `nHit`, so `idf` is not degenerate on a three-document corpus and the
#: flip is a reweighting rather than a tie-break.
_SHIPPED_NOISE = 6

#: ``(itemId, migrationId, revisionId, slug, title)``. Every id is Crockford
#: base32 -- no I, L, O or U -- because `MigrationId`/`RevisionId` reject the
#: rest, and the migration id is also the file's name prefix, which the loader
#: pins.
_SHIPPED_VISIBLE: Final = (
    (
        "architecture.isolation",
        "01K1VSAAAA01234567890ABCDE",
        "01K1VSREVA01234567890ABCDE",
        "isolation",
        "Tenant isolation",
    ),
    (
        "architecture.retention",
        "01K1VTAAAA01234567890ABCDE",
        "01K1VTREVA01234567890ABCDE",
        "retention",
        "Records retention",
    ),
)

_SHIPPED_CLI = CliRunner()


def _shipped_cli(
    root: Path, data_dir: Path, home: Path, monkeypatch: pytest.MonkeyPatch, *args: str
) -> dict[str, Any]:
    """Run the real CLI inside ``root`` with its environment redirected.

    ``HOME`` is redirected beside ``THEURIAN_DATA_DIR`` even though nothing here
    reads it: the fixture shells out to ``git``, and a test that reaches the
    developer's real home directory is a defect that surfaces somewhere else
    entirely. Both are set in the same call that changes directory, never in an
    earlier one -- and nothing in this file runs ``setup``, ``uninstall`` or a
    detached ``daemon start``, which are what register with a service manager no
    environment variable redirects.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    result = _SHIPPED_CLI.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, f"{' '.join(args)}: {result.output}"
    payload: dict[str, Any] = json.loads(result.output)
    return payload


def _declare_a_ceiling(data_dir: Path) -> None:
    """Write the serving profile the CLI and the daemon both read.

    Mode 0600 is not tidiness. ``load_serving_profile`` refuses a profile other
    local users can reach, so a test that skipped this would exercise the refusal
    rather than the ceiling -- and would say "the build failed" while looking
    like a withholding.
    """
    auth = data_dir / "auth"
    # 0700 on the directory as well as 0600 on the file. `load_serving_profile`
    # refuses both, because a directory's write bit governs *replacing* an entry
    # in it -- and a bare `mkdir` under the usual umask leaves 0755, which is the
    # shape `FileSecretStore.set` never creates and this refusal exists for.
    auth.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth.chmod(0o700)
    profile = auth / SERVING_PROFILE_FILENAME
    profile.write_text(f"{Sensitivity.INTERNAL.value}\n", encoding="utf-8")
    profile.chmod(0o600)


def _write_shipped_corpus(root: Path) -> None:
    """The corpus both projects apply, bodies and migrations written together.

    Every body is named once, in a local, and handed to both the file write and
    ``body_pin`` -- never re-spelled for the digest. ``contentSha256`` is required
    on every ``upsertRevision`` since #342 (ADR-0027 decision 1) and the loader
    re-hashes the file on each load, so a pin computed from anything but the bytes
    actually written fails the migration rather than the assertion under test.
    """
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"
    for item, mid, rid, slug, title in _SHIPPED_VISIBLE:
        body = _SHIPPED_VISIBLE_BODY[slug]
        (knowledge / f"{slug}.md").write_text(body)
        (migrations / f"{mid}-{slug}.yaml").write_text(
            _SHIPPED_DOC_MIGRATION.format(
                mid=mid, item=item, rid=rid, slug=slug, title=title, pin=body_pin(body)
            )
        )
    for number in range(_SHIPPED_NOISE):
        slug = f"window-{number}"
        body = (
            f"# Deployment window {number}\n\nRelease {number} goes out on Thursday after the "
            f"staging soak has run for a day.\n"
        )
        (knowledge / f"{slug}.md").write_text(body)
        (migrations / f"01K1NZ{number}AAA01234567890ABCDE-{slug}.yaml").write_text(
            _SHIPPED_DOC_MIGRATION.format(
                mid=f"01K1NZ{number}AAA01234567890ABCDE",
                item=f"architecture.{slug}",
                rid=f"01K1NZ{number}REV01234567890ABCDE",
                slug=slug,
                title=f"Deployment window {number}",
                pin=body_pin(body),
            )
        )
    (knowledge / "secret.md").write_text(_SHIPPED_SECRET_BODY)
    (migrations / "01K1SCRTAA01234567890ABCDE-secret.yaml").write_text(
        _SHIPPED_DOC_MIGRATION.format(
            mid="01K1SCRTAA01234567890ABCDE",
            item="architecture.secret",
            rid="01K1SCRTV101234567890ABCDE",
            slug="secret",
            title="Runbook",
            pin=body_pin(_SHIPPED_SECRET_BODY),
        )
    )


def _shipped_project(
    base: Path, name: str, monkeypatch: pytest.MonkeyPatch, *, build_before: bool
) -> tuple[ProjectRegistry, Path]:
    """One real CLI project that reclassifies a document through ``migrate apply``.

    ``build_before`` is the whole of the difference between the pair:

    - ``holds-it`` (``True``) builds and publishes the index while the document is
      ``internal`` -- so it is in the file -- and *then* reclassifies it, which is
      the apply that fires the purge;
    - ``never-held-it`` (``False``) reclassifies first and builds afterward, so
      the build was never allowed to write the row at all (#119 phase 3).

    Both apply the **identical** migration set and therefore reach the identical
    canonical state and report the identical ``snapshotId``. Neither runs a
    second ``index build`` after the reclassification: that a purged build needs
    no rebuild is the property under test.
    """
    root = base / name
    data_dir = base / f"{name}-data"
    home = base / f"{name}-home"
    for directory in (root, data_dir, home):
        directory.mkdir(parents=True)
    for git in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(git, cwd=root, check=True, capture_output=True)  # noqa: S603

    _declare_a_ceiling(data_dir)
    _shipped_cli(root, data_dir, home, monkeypatch, "init")
    _shipped_cli(
        root,
        data_dir,
        home,
        monkeypatch,
        "project",
        "register",
        "--project-id",
        SHIPPED_PROJECT_ID,
    )
    _write_shipped_corpus(root)
    _shipped_cli(root, data_dir, home, monkeypatch, "migrate", "apply")
    if build_before:
        _shipped_cli(root, data_dir, home, monkeypatch, "index", "build")
    (root / ".theurian/migrations/01K1WRECAA01234567890ABCDE-reclassify.yaml").write_text(
        _SHIPPED_RECLASSIFY_MIGRATION
    )
    _shipped_cli(root, data_dir, home, monkeypatch, "migrate", "apply")
    if not build_before:
        _shipped_cli(root, data_dir, home, monkeypatch, "index", "build")
    return ProjectRegistry.default(data_dir), root


def _published_offers(root: Path, query: str) -> set[str]:
    """The item ids the *published* build offers for a query, below every gate.

    Resolves the build the pointer names rather than a fixed id, so it reads the
    purged build after a reclassification and the original before one. Asked at
    every sensitivity for the reason :func:`_offered_by_the_index` is: the
    question is what the file holds, not what the deployment's own predicate
    would let through.
    """
    payload = read_active_index_pointer(ProjectPaths.of(root)).payload
    assert payload is not None, "the project must have a published index"
    index = SqliteIndexStore(ProjectPaths.of(root).index_for(str(payload["indexBuildId"])))
    page = index.search_lexical(
        query,
        project_id=SHIPPED_PROJECT_ID,
        limit=_EXHAUSTIVE_DEPTH,
        include_unapproved=True,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    assert page.exhausted, "the page must be complete for an absence to mean anything"
    return {row.item_id for row in page.rows}


def _masked(response: dict[str, Any]) -> dict[str, Any]:
    """The response minus the fields that describe the *build*, not the answer.

    ``indexBuildId``, ``stale`` and ``note`` differ by construction between a
    purged build and a fresh one: the purge preserves the source build's state
    hash (a removal is not a rebuild), so ``holds-it`` reports ``stale: true``
    and ``never-held-it`` ``stale: false``, and the ``note`` follows ``stale``.
    None of the three is what T-17a moves -- that is the ranking, which is
    compared unmasked -- and every other field, ``snapshotId`` included, must be
    equal.
    """
    masked = dict(response)
    retrieval = dict(masked["retrieval"])
    for field in ("indexBuildId", "stale", "note"):
        retrieval.pop(field, None)
    masked["retrieval"] = retrieval
    return masked


def _visible_order(build: Path) -> list[str]:
    """The visible item ids one build's word retriever returns, in its own order."""
    page = SqliteIndexStore(build).search_lexical(
        SHIPPED_QUERY,
        project_id=SHIPPED_PROJECT_ID,
        limit=MAX_RESULTS,
        include_unapproved=True,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    seen: list[str] = []
    for row in page.rows:
        if row.item_id != "architecture.secret" and row.item_id not in seen:
            seen.append(row.item_id)
    return seen


def _orders_before_and_after_the_purge(probe_root: Path) -> tuple[list[str], list[str]]:
    """The probe's visible order from its pre-purge build and from its purged one.

    Publishing never deletes (ADR-0024 point 6), so the build ``index build``
    wrote while the document was ``internal`` is still on disk beside the purged
    successor the pointer now names. Reading both directly, at the same retriever
    the tool's first pass uses, is the exact before/after the purge produced --
    without rebuilding. Their orders differing is what makes the response
    equality a real test rather than one satisfied by any two corpora that happen
    to agree.
    """
    paths = ProjectPaths.of(probe_root)
    published = read_active_index_pointer(paths).payload
    assert published is not None
    published_id = str(published["indexBuildId"])
    prefix, suffix = "theurian-index-", ".sqlite"

    purged = _visible_order(paths.index_for(published_id))
    for build in sorted(paths.state.glob(f"{prefix}*{suffix}")):
        if build.name[len(prefix) : -len(suffix)] == published_id:
            continue
        holds_it = any(
            row.item_id == "architecture.secret"
            for row in SqliteIndexStore(build)
            .search_lexical(
                SHIPPED_QUERY,
                project_id=SHIPPED_PROJECT_ID,
                limit=MAX_RESULTS,
                include_unapproved=True,
                visible_sensitivities=EVERY_SENSITIVITY,
            )
            .rows
        )
        if holds_it:
            return _visible_order(build), purged
    raise AssertionError(
        "no *unpublished* build holding the reclassified document was found on disk. Either the "
        "purge published nothing -- in which case the build that holds it is the one the pointer "
        "still names, and the assertion above this call should have said so -- or `index build` "
        "wrote only one file and this fixture never had a before to compare against"
    )


def test_a_purged_build_answers_as_one_that_was_never_allowed_to_hold_the_row(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0025 parts 2 and 4, T-17a. The third mechanism, through the real CLI.

    Two real projects under one declared ``internal`` ceiling, applying the
    identical migration set and differing only in *when* the index was built:

    - ``holds-it`` indexed the document while it was ``internal``, then
      reclassified it to ``restricted`` -- the apply that fires the purge;
    - ``never-held-it`` reclassified first and indexed afterward, so the builder
      was never allowed to write the row (#119 phase 3).

    **No ``index build`` runs after the reclassification on either side.** That a
    purge leaves a published build a search can go on using is the property, the
    same one ADR-0024 decision 5 states for a withdrawal, and it is why this
    cannot be a store-level comparison.

    Three assertions, and the corpus is tuned so each can fail:

    - the published build no longer offers the reclassified item at all, read
      below every gate and at every sensitivity, so the absence is the purge and
      not a predicate;
    - ``holds-it`` answers the query **identically** to ``never-held-it`` over the
      whole response but for the three build-identity fields :func:`_masked`
      names. This can only fail on a corpus where the withheld document
      *reorders* the visible pair, which is why the secret is long and dense in
      one query term;
    - the equality's non-vacuity is pinned directly: the probe's own pre-purge
      build, still on disk, ranks the visible pair the *other* way from its purged
      successor. Without that, the equality above would hold for any
      implementation.

    ``test_sensitivity_purge.py`` asserts the same trigger over the *file* and
    over the forest. This is the leaf-side response equality ADR-0025 part 4
    records as the piece those do not cover.

    **The mutation that turns this RED is the purge's sensitivity *flavor* axis,
    not ``changeSensitivity``'s membership of the withdrawal candidate set**, and
    the difference was measured rather than assumed. Removing ``ChangeSensitivity``
    from ``_withdrawal_affected_item`` leaves this green: ``migrate apply``
    replays the whole migration set whenever the state hash shifts (ADR-0016),
    and the reclassified item's own ``upsertRevision`` is in that set, so it is
    gathered as a candidate anyway and the flavor reduction still purges it.
    Deleting ``may_disclose(candidate.sensitivity, visible=indexed_sensitivities)``
    from ``revisions_to_purge`` is what makes the probe's build keep the row --
    measured, and it fails here on the very first assertion.
    """
    base = tmp_path_factory.mktemp("purged")
    probe, probe_root = _shipped_project(base, "holds-it", monkeypatch, build_before=True)
    control, _ = _shipped_project(base, "never-held-it", monkeypatch, build_before=False)

    offered = _published_offers(probe_root, SHIPPED_QUERY)
    from_probe = _call(probe, "knowledge.search", projectId=SHIPPED_PROJECT_ID, query=SHIPPED_QUERY)
    from_control = _call(
        control, "knowledge.search", projectId=SHIPPED_PROJECT_ID, query=SHIPPED_QUERY
    )

    assert "architecture.secret" not in offered, (
        f"the reclassification must have purged the document from the published build, so no "
        f"retriever offers it at any sensitivity: {sorted(offered)}"
    )
    assert from_probe["count"] == from_control["count"] > 0, (
        "both must answer the visible corpus, or two empty answers prove nothing"
    )
    assert from_probe["retrieval"]["indexed"] is True, (
        f"the probe must answer from its purged build rather than the canonical scan, or this "
        f"measures a fallback: {from_probe['retrieval']}"
    )
    assert "architecture.secret" not in {r["itemId"] for r in from_probe["results"]}, (
        "and the reclassified item must not be in the answer"
    )
    assert SHIPPED_SECRET_MARKER not in json.dumps(from_probe), "nor its payload anywhere in it"
    # Read after the assertion above rather than beside the two calls, because it
    # can only be answered once a purge has published a *second* build: with the
    # purge neutralised there is one file on disk and it is the one the pointer
    # names, so this helper would raise about a missing before-image where the
    # assertion above says plainly that the document is still offered.
    stale_order, purged_order = _orders_before_and_after_the_purge(probe_root)
    assert stale_order != purged_order, (
        f"the corpus must be one whose visible order the withheld document flips: the probe's "
        f"pre-purge build ranks the visible pair {stale_order} and its purged successor "
        f"{purged_order}. Equal, and the equality below holds for any implementation."
    )
    assert _masked(from_probe) == _masked(from_control), (
        "a build that held the reclassified rows and had them purged must answer identically "
        "to one that was never allowed to hold them -- ranking, chunk ids and scores"
    )
