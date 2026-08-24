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
(``cleared = tuple(ranked)``) left all ten tests here green while turning **all
twenty** parametrisations of
``test_a_withheld_document_changes_nothing_a_caller_can_see`` red.
:func:`_assert_the_pair_bites` now reads the index file directly and asserts
which of the two mechanisms is doing the work, per example.

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
**Presence is not tested here.** These pairs vary a withheld document's *content*
and whether it *matches*; they never vary whether it is in the index at all.
Issue #15's trigger closed presence for every *withdrawal* -- a retirement, a
supersede, a reject, and an in-place status change that makes a revision
non-surfaceable **at the published index's own build flavor** (a doc dropped to
``draft`` in a default index is withheld *there* even though ``may_surface``
passes a draft under ``--include-unapproved``). All are purged the moment they
commit (ADR-0024 decision 5), proved in
:func:`test_a_withdrawal_purges_the_published_index_without_a_separate_build`. The
one residual it leaves is not a withdrawal at all:
:func:`test_a_withheld_draft_still_changes_which_document_a_caller_is_handed` pins
a `draft` an operator *chose* to index with ``--include-unapproved`` and which
that build legitimately holds -- surfaceable at its flavor, returned to a caller
who passes the flag, so off by default rather than withheld
(:func:`test_a_draft_in_an_include_unapproved_index_survives_an_unrelated_replay`
holds that the purge does not over-reach and delete it). Read
*Where the equality is conditional* in
:mod:`theurian.application.retrieval_service` for the mechanism and T-17a in the
threat model for the residual that remains.

**Three of the five published tools, and the other two are not oversights.** The
population is ``@server.tool(name=...)`` in :mod:`theurian.mcp.tools` -- five --
and the pairs here compare ``knowledge.search`` and ``knowledge.get``:

``knowledge.status``
    **excluded because a comparison here would grade this file's builder, not
    the product.** Measured against the pairs this module actually builds, which
    is the only way to get it right -- an earlier version of this paragraph
    reasoned from ``tools.py``'s comment instead and had all four fields
    backwards:

    ==================== ===================== ==================
    field                retired (deprecated)  draft
    ==================== ===================== ==================
    ``itemCount``        equal, 1 vs 1         **moves, 3 vs 2**
    ``itemsByStatus``    equal                 **moves**
    ``stateHash``        equal                 equal
    ``appliedMigrations`` equal, 0 vs 0        equal, 0 vs 0
    ==================== ===================== ==================

    Two separate reasons, and neither is the one ``tools.py`` records.

    The counts move for a ``draft`` because ``draft`` is in
    :data:`~theurian.domain.enums.SURFACEABLE_STATUSES` and this tool counts
    exactly that set. **That is not a leak**: a draft is off by default, not
    withheld from every caller -- ``includeUnapproved`` returns it, which is
    precisely why :func:`_retire` uses ``deprecated`` instead when the point is
    to be unreachable. ``tools.py``'s own sentence says "one **rejected** item",
    and ``rejected`` is not surfaceable; generalising it to "one withheld item"
    is what made it false here.

    ``stateHash`` and ``appliedMigrations`` -- the two the tool records as
    genuinely moving, and the reason issue #19 is open -- cannot move in this
    file at all: :func:`_build_project` *declares* the state hash and never runs
    the migration engine, so both are constants of the builder. A pair over this
    tool would therefore pass while saying nothing about the residual it exists
    to have.
``project.list``, ``system.capabilities``
    not project-scoped. Neither reads knowledge, so no corpus difference can
    reach either, and a pair over them would be two identical answers compared.

Named rather than left out, because "this file compares the response" reads as
"the response", and a reader counting tools should not have to.

Four further things this file does not reach, so nobody has to rediscover them:

- **The unranked fallback path.** Every pair here answers from an index, so
  :func:`theurian.mcp.search.substring_answer` -- a second, whole answer path
  with its own gate (:func:`~theurian.domain.enums.may_surface`, applied in
  ``_scan``) -- is never compared. Reaching it from a pair means either breaking
  the index, or passing ``includeUnapproved`` against an index built without
  drafts. The second is the tidy one and it is deliberately not done: for the
  draft shape that flag makes the withheld documents *visible*, so the two
  corpora would differ in a document the caller may read and the equality would
  fail honestly. Making it safe means coupling a parametrised argument to a
  generated corpus property, which is how an example silently stops testing what
  its id says -- the defect this file has already had once. Left for a pair
  built specifically for the fallback.
- **Durations, and this is a decision rather than an omission.** Issue #29 asks
  for a statistical latency test -- ``dudect``-style, or a Welch t-test over
  samples classed by withheld count -- so that a regression in the timing family
  fails a run instead of sitting in prose. It is not built here, and the reason
  is that the quantity underneath it is pinned *exactly*. What varies with the
  withheld count on the *status* axis is the number of SQL round-trips, and that
  is asserted from both sides of its threshold by
  ``test_the_second_pass_arrives_at_fifty_withheld_rows_and_not_before``, its
  geometric step by ``test_each_pass_reaches_twice_as_far_as_the_one_before``
  (both ``tests/unit/test_retrieval_depth.py``), and the corpus scan count by
  ``test_one_search_reads_the_scan_once_however_many_rows_were_withheld``
  (``tests/integration/test_scan_exhaustion.py``). A t-test over wall clock would
  be a noisier measurement of the same variable, on a machine that also runs the
  rest of the suite -- and it would fail intermittently, which is the failure
  mode this repository can least afford in a security assertion. If the pass
  count is right, the latency follows; if it is wrong, a deterministic test says
  so and names the constant.

  **"If the pass count is right, the latency follows" is now too narrow to be the
  whole reason, and saying so is what keeps this an exclusion rather than a false
  closure argument.** It was written when the pass count was the only quantity
  that moved with what was withheld. On the *ceiling* axis (#119) a second one
  moves while the pass count is held at one: the canonical statement
  ``list_items_by_status`` spends about 0.20 us and 6.0 SQLite VM steps per
  above-ceiling row, measured, linear, and bounded by the corpus rather than by
  the caller's ask, because that statement carries no ``LIMIT``. It is a recorded
  and accepted residual, not an open defect -- the measurement, the per-row
  comparison against T-17's accepted 14.7 us per withheld row, and what it would
  take to flatten it are on ``SqliteCanonicalStore.list_items_by_status``, and the
  flattening is owned by https://github.com/theurian/theurian/issues/338.

  **This suite deliberately does not measure it.** Every pair here compares
  *response content*, and a term of that size is far below what a pair built out
  of one process's wall clock could separate from noise -- the threat model puts
  a real client's end-to-end floor at 1.40 ms (TB-1), thousands of above-ceiling
  rows away. Adding a timing assertion here would therefore assert nothing while
  reading as though it asserted the family. What no test here covers is that
  residual and the *constant factor* -- what one pass costs on a large corpus --
  and those numbers live in ``FIRST_PASS_DEPTH``'s docstring and in the note
  named above, measured by hand and not re-measured.
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

Why this is one file and not two
--------------------------------
It is past the 800-line guideline, and the obvious seam is real: the T-17a block
at the end is three hand-written tests that share only :func:`_build_project` and
:class:`_Document` with everything above. It is not split, for one reason.

**The T-17a block is the exception to the property the rest of the file
asserts**, and the two only mean anything read together: above, "no published
value varies with a withheld document"; below, "except in the residual that
remains -- here is the corpus and the operator configuration that reaches it" --
and, in the shipped-close section after it, "and here is the withdrawal that
removes it, purging the published build with no rebuild". Splitting puts the
residual and its close in a file that a reader of the guarantee never opens -- and
Milestone 5's account of T-17a is precisely that of an acceptance drifting away
from the measurement behind it, carried for two rounds in the orchestrator's own
words before anyone re-measured it.

The secondary cost is that the split needs a shared helper module in a test tree
with no package structure (no ``__init__.py``, ``--import-mode=importlib``),
which is a new import mechanism introduced for one file.

Sizes here for calibration rather than as an excuse: ``test_mcp_tools.py`` is
3,757 lines, ``test_retrieval_service.py`` 2,002 and
``test_canonical_store_corruption.py`` 1,829, so this is not the outlier the raw
number suggests. The guideline still bites the day a *fourth* concern lands
here; the two natural next ones -- a fallback-path pair and a Japanese corpus --
should each be their own file rather than a fourth section of this one.
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
from theurian.application.retrieval_service import DEFAULT_BUDGET_TOKENS
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

pytestmark = pytest.mark.integration

#: The disclosure grant every retriever call in this file runs under: all four
#: levels, which is what "this deployment serves everything" means once the
#: retrievers take the axis as a WHERE predicate (#119 phase 4). Spelled out
#: rather than read from ``StaticAuthorizationProvider``'s shipped default, which
#: a later phase narrows -- a file that inherited it would start withholding its
#: own fixtures silently, turning these tests into tests of something else.
EVERY_SENSITIVITY = frozenset(Sensitivity)


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
    #: What this deployment must be serving to see it (#119). ``internal`` for
    #: everything the ceiling shape does not raise, which is what every document
    #: in this file was before the axis existed.
    sensitivity: Sensitivity = Sensitivity.INTERNAL


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

#: Withheld at query time because the item's sensitivity is above the ceiling
#: this deployment serves (#119). Structurally the strongest of the three: the
#: rows are ``approved`` at build *and* at query time, so every retriever's SQL
#: admits them, the index holds their text stamped with a level the ceiling would
#: admit, and nothing but the canonical re-check on the item's *current* level
#: stands between the row and the caller.
#:
#: It is the retired shape's sibling and not its duplicate. There the withheld
#: rows have left ``SURFACEABLE_STATUSES``, so five other things also stop
#: counting them -- the #30 expected count, `itemsByStatus`, the purge. Here they
#: are ordinary approved knowledge that this *deployment* may not disclose, so
#: those quantities still include them and only the disclosure gates may not.
ABOVE_THE_CEILING: Final = "above-the-ceiling"

WITHHOLDING_MECHANISMS: Final = (
    RETIRED_AFTER_BUILD,
    DRAFT_IN_AN_UNAPPROVED_INDEX,
    ABOVE_THE_CEILING,
)

#: The level the ceiling shape raises its withheld documents to, and the grant
#: that then excludes them. Every other document in this file is ``internal``, so
#: the ceiling admits the visible corpus whole and stops exactly at the withheld
#: rows.
ABOVE_CEILING_LEVEL: Final = Sensitivity.RESTRICTED
CEILING_GRANT: Final = AuthorizationGrant(
    tenant=DEPLOYMENT_TENANT,
    sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL}),
    acl_groups=DEPLOYMENT_ACL_GROUPS,
)

#: What every other shape runs under: the four levels the shipped default serves.
#: Spelled out rather than read from `StaticAuthorizationProvider`, because a
#: later phase narrows that default and this file's other two mechanisms must go
#: on being about status.
ALLOW_ALL_LEVELS: Final = frozenset(Sensitivity)
ALLOW_ALL_GRANT: Final = AuthorizationGrant(
    tenant=DEPLOYMENT_TENANT,
    sensitivities=ALLOW_ALL_LEVELS,
    acl_groups=DEPLOYMENT_ACL_GROUPS,
)


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
    #: The caller's parameters, for the tests that want them *varied* rather than
    #: enumerated -- see :attr:`arguments`.
    limit: int
    max_tokens: int
    use_dense: bool

    @property
    def arguments(self) -> dict[str, Any]:
        """The generated parameter triple, asked for by name.

        Only :func:`test_no_withheld_payload_appears_anywhere_a_caller_reads`
        uses it. That test asks whether a payload can appear *anywhere*, which is
        a question worth spreading across parameters rather than pinning to four
        of them; the equality test asks a question whose observability depends on
        an exact ``limit`` and an exact budget, and enumerates instead
        (:data:`ARGUMENT_SETS`).

        A property rather than a default inside :func:`_search`, because a
        default there is what made ``ARGUMENT_SETS``' ``defaults`` entry a no-op
        for twenty-four calls without anything saying so.
        """
        return {"limit": self.limit, "maxTokens": self.max_tokens, "useDense": self.use_dense}

    @property
    def build_status(self) -> KnowledgeStatus:
        """What the withheld documents are when the index is written.

        ``approved`` for the retired and ceiling shapes: the row has to be in the
        index as one a retriever will return, or the canonical gate never sees it
        and the pair proves nothing about the gate. That mistake was made in this
        file's first version -- every withheld document was a ``draft``, so the
        retrievers' own ``WHERE`` removed it and deleting the canonical gate
        outright left all ten tests here green.
        """
        return (
            KnowledgeStatus.DRAFT
            if self.withheld_by == DRAFT_IN_AN_UNAPPROVED_INDEX
            else KnowledgeStatus.APPROVED
        )

    @property
    def build_sensitivity(self) -> Sensitivity:
        """What the withheld documents' *level* is, in the store and in the index.

        Raised for the ceiling shape alone, and raised **before** the build. Until
        #119 phase 3 that made the index stamp its chunks with the very level the
        ceiling excludes -- the harder case, and the only one available while the
        builder ignored the ceiling. The builder no longer does, so on this shape
        the level now decides that the withheld documents are never written, and
        the pair's index-side difference is gone by construction rather than
        filtered.

        **What that costs, said plainly:** the ranked path's canonical re-check on
        this axis is no longer exercised from here, because the rows it would
        re-check are not in either index. It is exercised by
        ``test_mcp_tools.py::test_the_ranked_path_withholds_a_document_
        reclassified_after_the_build``, which is now the only place a *ranked*
        answer meets an above-ceiling row -- reachable only through
        reclassification after a build, since nothing else puts one in a file this
        deployment will read. A pair shaped like that (build at ``internal``,
        reclassify to ``restricted``, serve at ``internal``) would restore the
        index-side difference on this axis and is owed by ADR-0025 part 4, whose
        owner is #119.
        """
        return (
            ABOVE_CEILING_LEVEL if self.withheld_by == ABOVE_THE_CEILING else Sensitivity.INTERNAL
        )

    @property
    def grant(self) -> AuthorizationGrant:
        """The deployment grant the caller's server is built with (#119).

        Narrowed for the ceiling shape and allow-all for the other two, so each
        mechanism is exercised by the thing that actually stops it and the pair's
        equality is never satisfied by a second gate withholding the same row.
        """
        return CEILING_GRANT if self.withheld_by == ABOVE_THE_CEILING else ALLOW_ALL_GRANT

    @property
    def indexes_unapproved(self) -> bool:
        """Whether the index is built with drafts in it.

        Only the draft shape needs them, and only it gets them: an index built
        with ``--include-unapproved`` is a different published field
        (``indexesUnapproved``) and a different fallback vocabulary, so switching
        it on for shapes that hold no draft would test the tool's less common
        configuration everywhere and its shipped one nowhere.
        """
        return self.withheld_by == DRAFT_IN_AN_UNAPPROVED_INDEX

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
    ever discarded *for failing to have one*. Discards from other causes are
    hypothesis's business and are not zero -- see :data:`_GENERATED`.
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
    boundary it straddles is exact. :data:`~theurian.application.
    retrieval_service.CANDIDATE_DEPTH` is fifty: a pair whose corpora both fit
    inside one retriever's depth cannot tell a depth loop that counts *visible*
    rows from one that counts raw ones -- the fourth face in
    :mod:`theurian.application.retrieval_service`'s table, and the one that
    recovered a credential at the default token budget.

    Measured, not reasoned about: with the size drawn as an ordinary list length,
    the mutation replacing the depth loop with a single fifty-row fetch survived
    twenty-five generated examples of every test in this file. What that says is
    that twenty-five draws did not land on the case; it is not a claim about the
    distribution, which was not measured.
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

#: How deep :func:`_offered_by_the_index` asks, so its page is complete.
#:
#: Far past anything this file can build -- 62 visible documents plus 3 withheld,
#: each short enough for a single chunk -- because it answers "does any retriever
#: hand this row up", which a cut list cannot. Deliberately unrelated to
#: ``MAX_RESULTS``: that is the caller's bound, and borrowing it here is what made
#: the completeness assertion fire on every ``across-the-depth`` cell.
_EXHAUSTIVE_DEPTH: Final = 500


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
    and it is removed here rather than filtered away.

    **That is a claim about this function, not about the run**, and it used to be
    written as the wider one -- "so no example is ever silently discarded".
    Hypothesis discards examples for reasons no strategy here controls: an
    overrun is dropped and re-drawn, and the health check that would report it
    only fires above a threshold these generators sit far below. Measured over
    seven seeds, two of them lost examples that way (see :data:`_GENERATED`).
    What holds here is narrower and still worth having: **no example is
    discarded for a condition this module could have arranged instead**, which
    is the class of loss a reader can do something about.
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
        status=document.status,
        current_revision_id=RevisionId(document.revision_id),
        owner="platform-team",
        trust_level=TrustLevel.REVIEWED,
        sensitivity=document.sensitivity,
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

    ``migration_count`` is **0** because this builder runs no migration engine
    and writes no `migration_history` row. It read 1 until #30 PR2 measured it:
    the integrity detector compares the pointer's count against the store's live
    row count, so a pointer claiming one applied migration over a store holding
    none made every response in this suite carry ``integrity`` -- leaving the
    equalities below comparing two damaged responses, which they do perfectly
    and pointlessly. Present since PR1 (`e62de35`); the value was chosen before
    any pointer field was read back.
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
    """The canonical write the index never saw.

    Re-records the expected surfaceable count for the reason the first write
    records it at all (see :func:`_build_project`): a retirement takes rows out
    of that count, and a real one arrives through ``migrate apply``, which
    re-records inside the same transaction. Without this the pair would answer
    every call with ``integrity`` present, and this suite would be comparing two
    damage reports rather than two healthy responses.
    """
    with write_transaction(database, paths.write_lock) as connection:
        writer = SqliteWriter(connection)
        for item in items:
            writer.put_item(item)
        writer.record_expected_surfaceable_count(ProjectId(PROJECT_ID))


def _build_project(  # noqa: PLR0913 - one per axis a generated shape varies
    root: Path,
    documents: tuple[_Document, ...],
    created_at: datetime,
    retired: tuple[str, ...] = (),
    *,
    indexes_unapproved: bool | None = None,
    visible_sensitivities: frozenset[Sensitivity] = ALLOW_ALL_LEVELS,
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

    **``include_unapproved`` follows the shape rather than being on always.** It
    was on always, which cost two things at once. ``indexesUnapproved`` is a
    published field on every response this file compares, and it never once
    carried the value the shipped ``theurian index build`` produces -- an
    equality that has only ever seen one value of a field is not an equality
    anyone has checked. And an index that holds no drafts is the precondition for
    :data:`~theurian.mcp.search.UNAPPROVED_NOT_INDEXED`, so the whole fallback
    vocabulary was unreachable from every pair built here.

    The retired shape needs nothing unapproved in the index: its withheld
    documents are ``approved`` when the build runs. So it builds at the default
    and the draft shape does not, and the two now differ in that published field
    as well.

    ``indexes_unapproved`` says which of the two this is. It used to be inferred
    as ``not retired``, and the inference stopped holding the moment a third
    withholding mechanism arrived that retires nothing and holds no draft either
    (:data:`ABOVE_THE_CEILING`) -- it would have built that shape's index with
    drafts it does not have, publishing a field value the shipped ``theurian
    index build`` does not produce. ``None`` keeps the old reading for the
    hand-written callers below, which have no ``_Case`` to ask.

    **``visible_sensitivities`` is the grant this project's index is built under,
    and it is the same grant the pair is then queried through** (#119 phase 3).
    Since the builder excludes an above-ceiling item, "which ceiling was in force
    at build time" decides which rows the file holds -- so the serve path stands a
    build made under another one aside entirely
    (``mcp.search._published_index``, ``serving-profile-mismatch``). Building the
    ceiling shape allow-all and querying it narrow would therefore not test a
    narrow deployment at all: both sides would fall back to the unranked scan and
    the equality would hold over a path neither this file nor its docstrings are
    about. It defaults to every level because every other shape here is queried
    through :data:`ALLOW_ALL_GRANT`.
    """
    if indexes_unapproved is None:
        indexes_unapproved = not retired
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
            visible_sensitivities=visible_sensitivities,
            include_unapproved=indexes_unapproved,
        )
    )
    # Written by hand rather than through `write_active_index_pointer`, which is
    # what `theurian index build` publishes with, because this fixture stands in
    # for the CLI. `indexedSensitivities` is encoded through the shipped helper
    # even so: the serve path decodes it, and a hand-spelled list here would be a
    # second encoding of one wire field, free to drift from the one under test.
    paths.active_index_pointer.write_text(
        json.dumps(
            {
                "indexBuildId": INDEX_BUILD_ID,
                "stateHash": str(built_from),
                "projectId": PROJECT_ID,
                "indexesUnapproved": indexes_unapproved,
                "indexedSensitivities": encode_sensitivities(visible_sensitivities),
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
    # This builder stands in for `migrate apply` + `index build`, so it records
    # what those record: that this installation built the served state and index
    # (ADR-0004, SEC-7). Without it the serve path refuses both -- the pointer's
    # final hash is always `STATE_NOW`, and the one index is `INDEX_BUILD_ID`.
    provenance = BuildProvenance.for_registry(registry)
    provenance.record_state(paths.root, str(STATE_NOW))
    provenance.record_index(paths.root, INDEX_BUILD_ID)
    return registry


@dataclass(frozen=True, slots=True)
class _Pair:
    """Two projects that differ only in content no caller may read."""

    probe: ProjectRegistry
    control: ProjectRegistry
    #: The probe's project root, so a guard can read its index file directly.
    probe_root: Path
    #: The control's, for the one guard that compares the two *files* rather than
    #: the two responses -- see `_indexed_text`.
    control_root: Path
    case: _Case


def _pair(base: Path, case: _Case) -> _Pair:
    created_at = datetime.now(UTC) - AGE_OFFSET
    probe_root = base / "probe"
    control_root = base / "control"
    return _Pair(
        probe=_build_project(
            probe_root,
            case.documents(secret=True),
            created_at,
            case.retired,
            indexes_unapproved=case.indexes_unapproved,
            visible_sensitivities=case.grant.sensitivities,
        ),
        control=_build_project(
            control_root,
            case.documents(secret=False),
            created_at,
            case.retired,
            indexes_unapproved=case.indexes_unapproved,
            visible_sensitivities=case.grant.sensitivities,
        ),
        probe_root=probe_root,
        control_root=control_root,
        case=case,
    )


def _call(
    registry: ProjectRegistry,
    tool: str,
    grant: AuthorizationGrant = ALLOW_ALL_GRANT,
    **arguments: Any,
) -> dict[str, Any]:
    """Invoke a tool through the same entry point the transport uses.

    ``grant`` is what the daemon was started with (#119). Defaulted to allow-all
    so that every caller written before the axis existed keeps asking the
    question it was written to ask -- and stated explicitly by
    :meth:`_Case.grant` for the ceiling shape, which is the only one whose
    withholding depends on it. It is *not* defaulted to `build_server`'s own
    default: a later phase narrows that, and this file's other two mechanisms
    must go on being about status.
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
    does mean *the tool's own defaults*. This function used to seed the dict with
    ``case.limit``, ``case.max_tokens`` and ``case.use_dense`` and then let the
    caller override -- which silently emptied :data:`ARGUMENT_SETS`' ``defaults``
    entry, because ``{}`` overrides nothing. Measured over the 24 calls that
    entry issued: the tool-default triple ``(10, 2000, False)`` appeared **zero**
    times, and with ``derandomize`` that is a fixed example set rather than bad
    luck. ``test_mcp_tools.py``'s ``defaults`` is the shape this now matches, and
    that module records why it exists: the leak it closes was reachable with no
    parameters set at all.

    A caller that wants the generated triple asks for it by name --
    :attr:`_Case.arguments` -- so which of the two is in force is visible at the
    call site rather than decided here.

    The grant comes from the case, not from the caller. The ceiling shape's
    withholding *is* the grant, so a search issued against the default one would
    return the withheld rows and this file's equalities would compare two answers
    that both hold the payload -- green, and measuring the opposite of what they
    claim.
    """
    return _call(
        registry,
        "knowledge.search",
        case.grant,
        projectId=PROJECT_ID,
        query=case.query,
        **arguments,
    )


def _failing(
    registry: ProjectRegistry,
    tool: str,
    grant: AuthorizationGrant = ALLOW_ALL_GRANT,
    **arguments: Any,
) -> str:
    with pytest.raises(SdkToolError) as raised:
        _call(registry, tool, grant, **arguments)
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

    **Both pages must be exhausted, and that is a real assertion rather than
    bookkeeping.** Every caller of this function uses its result to argue an
    *absence* -- a draft's chunks are refused, a withheld id is not offered -- and
    an absence read off a truncated page says nothing: the row could be one
    position below the cut. Before :class:`~theurian.domain.ranking.RetrieverPage`
    that could only be inferred from a row count, which is exactly the inference
    that type exists to remove.

    **Asked at :data:`_EXHAUSTIVE_DEPTH` rather than at the depth a search uses**,
    and the difference matters: the question here is *does any retriever hand
    this row up at all*, not *what does the caller see*. Asking at
    ``MAX_RESULTS`` answered a different question and got it wrong -- the
    ``across-the-depth`` corpora hold more matching chunks than fifty, so the
    page came back truncated and the assertion below fired on four cells the
    moment it was written. That is the assertion working: the old code inferred
    completeness from a row count and would have gone on reading "not offered"
    off a cut list.
    """
    index = SqliteIndexStore(ProjectPaths.of(root).index_for(INDEX_BUILD_ID))
    pages = (
        index.search_lexical(
            case.query,
            project_id=PROJECT_ID,
            limit=_EXHAUSTIVE_DEPTH,
            include_unapproved=include_unapproved,
            visible_sensitivities=EVERY_SENSITIVITY,
        ),
        index.search_substring(
            case.query,
            project_id=PROJECT_ID,
            limit=_EXHAUSTIVE_DEPTH,
            include_unapproved=include_unapproved,
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


def _above_the_ceiling_in_the_store(root: Path, item_ids: set[str]) -> bool:
    """Whether every id in ``item_ids`` is at a level :data:`CEILING_GRANT` excludes.

    Read from the canonical store rather than from the ``_Case`` that asked for
    it, because the ``_Case`` is the thing under suspicion: it is what decides the
    level, and a generator that stopped raising it would report its own intention
    back to the guard. The store is where the gate reads from.
    """
    paths = ProjectPaths.of(root)
    context = RequestContext(project_id=ProjectId(PROJECT_ID))
    with SqliteCanonicalStore(paths.database_for(STATE_NOW)) as store:
        items = [store.get_item(context, ItemId(item_id)) for item_id in sorted(item_ids)]
    return bool(items) and all(
        item is not None and item.sensitivity not in CEILING_GRANT.sensitivities for item in items
    )


def _assert_the_pair_bites(pair: _Pair, probe: dict[str, Any]) -> None:
    """Refuse to pass on an example that proved nothing.

    Five ways a generated pair can be green while testing nothing. The third is
    not hypothetical: this file's first version made every withheld document a
    ``draft``, whose chunks the retrievers' own ``WHERE`` refuses, so the
    canonical gate was never asked about them -- and deleting that gate outright
    (``cleared = tuple(ranked)``) left all ten tests here green while turning all
    twenty parametrisations of
    ``test_mcp_tools.py::test_a_withheld_document_changes_nothing_a_caller_can_see``
    red.

    - the answer is empty, so two empty answers are being compared;
    - the payloads are equal, so the two projects are the same project;
    - **no retriever offers the withheld row**, so nothing downstream had a
      chance to leak it;
    - the withheld row is in the answer, which is a leak rather than a bad pair;
    - **the pair is answering as a damaged project**, so every equality below is
      comparing two damage reports.

    The last one is measured rather than imagined. Both #30 detectors read
    records this builder writes by hand -- the active pointer's
    ``migrationCount`` and `project_integrity`'s expected count -- and this file
    got each of them wrong in turn: a pointer claiming one migration over a store
    holding none (PR1), and no `project_integrity` row at all (PR2). Neither
    failed anything. Every response simply carried ``integrity``, in both
    corpora, and the equalities held perfectly over two damaged answers. Nothing
    in this file reads that key, which is exactly why the guard has to.

    Asserted rather than filtered. ``hypothesis`` will happily generate a corpus
    of one empty document forever, and an example dropped by ``assume`` leaves no
    trace in the run.
    """
    case = pair.case
    root = pair.probe_root
    withheld_ids = {document.item_id for document in case.withheld(secret=True)}

    assert probe["count"] > 0, "two empty answers prove nothing about withholding"
    assert "integrity" not in probe, (
        f"the pair answers as a damaged project ({probe['integrity']}), so every equality in "
        f"this file is comparing two damage reports rather than two healthy responses. The "
        f"builder writes the records both #30 comparisons read -- the pointer's "
        f"`migrationCount` and `project_integrity`'s expected surfaceable count -- and one of "
        f"them has drifted from what `theurian migrate apply` would have written"
    )
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
    elif case.withheld_by == ABOVE_THE_CEILING:
        # Inverted by #119 phase 3, and the inversion is the finding rather than
        # a weakening. This branch used to require the withheld rows to be
        # *offered*, because the builder ignored the ceiling and the canonical
        # re-check was the only thing between them and the caller. The builder no
        # longer writes them, so requiring them to be offered would now be
        # requiring the exclusion to have failed.
        assert not (withheld_ids & offered), (
            f"a build under this deployment's ceiling still offers "
            f"{sorted(withheld_ids & offered)} to the retrievers, so the withheld text is in "
            f"the index and in the FTS5 "
            f"collection statistics every visible row is scored against (#119 phase 3, "
            f"ADR-0025 part 1)"
        )
        assert _above_the_ceiling_in_the_store(root, withheld_ids), (
            "and the store must actually hold them above the ceiling: a shape "
            "whose withheld rows are `internal` like everything else is withheld "
            "by nothing, and every equality below would hold vacuously"
        )
        assert _indexed_text(root) == _indexed_text(pair.control_root), (
            "the two builds do not hold the same text. That is what carries this shape now: "
            "the pair's corpora differ only inside documents neither build was allowed to "
            "write, so a difference here is the withheld content reaching the file -- and "
            "from there the collection statistics -- without being returned by anything"
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

#: A fixed draw sequence, and why one is not enough on its own.
#:
#: ``derandomize=True`` reproduces a failure across runs, and that is where its
#: guarantee stops: the seed it derives comes from ``function_digest``, which
#: hashes ``inspect.getsource`` of the test. **A prose-only docstring edit
#: therefore re-rolls every example.** Measured on the
#: ``below-the-depth-defaults`` cell, by inserting one sentence into the test's
#: docstring and nothing else:
#:
#: ===================  =====================================================
#: before               ``cache PPPPPP`` / ``median backend QPSSVUQVRVPWXTRV``
#: after one prose line ``cache PPPPPP`` / ``handle QPYVVXQSSXVQTR``
#: ===================  =====================================================
#:
#: One of three queries survived. That is the same hazard the exact pin on
#: ``hypothesis`` in ``pyproject.toml`` exists for -- a generator whose
#: distribution shifts changes which shapes were checked, and nothing says so --
#: arriving through a channel the pin does not cover. This branch edited
#: docstrings in three separate commits.
#:
#: So the seed is stated rather than derived. :data:`EXAMPLE_SEED` freezes the
#: sequence against source edits; ``derandomize`` stays because it is what makes
#: the *absence* of an explicit seed on any future test here still reproducible.
#: Checked by repeating the measurement above with the seed in place: the same
#: inserted sentence now leaves all three queries unchanged.
#:
#: **What the seed costs, because it is not free.** Hypothesis's pytest plugin
#: normally mixes ``item.nodeid`` into the derived seed, so parametrised cells
#: draw *different* sequences; an explicit ``@seed`` short-circuits that, and all
#: eight cells of the equality now draw the same corpora. So the example count
#: and the corpus count are different numbers, and only the second one says how
#: much was explored. Measured, by digesting every generated ``_Case`` -- the key
#: is ``sha256`` over each document's ``(item_id, title, body, status)`` plus the
#: query and the withholding mechanism:
#:
#: ==================================  ========  =========
#: entry                               examples  corpora
#: ==================================  ========  =========
#: the equality, all 8 cells together        48         12
#: the payload sweep                         18         16
#: ``knowledge.get``                         15         15
#: the generator guard                       10         10
#: **the file**                              **91**     **27**
#: ==================================  ========  =========
#:
#: **Detection went up, not down**, which is why the trade was taken. Counted on
#: the mutation that replaces the depth loop with a single fifty-row fetch, with
#: shrinking suppressed so every cell reports: **3 of the 8 cells red with the
#: seed, 1 of 8 without.** Twelve corpora seen by four argument sets each beat
#: forty-two seen once, because what makes a displaced candidate observable is
#: the *parameters*, not the corpus.
#:
#: ``deadline=None`` because one example builds two SQLite databases and two
#: index files. ``database=None`` because the default example database writes
#: ``.hypothesis/`` into whatever directory pytest was launched from, which for
#: this repository is the repository.
#:
#: **No ``suppress_health_check``, and what removing it did and did not buy.**
#: It carried ``data_too_large`` and ``too_slow``; measured, the file is green
#: without either. What the removal did *not* do is stop examples being
#: discarded silently, and this comment previously implied it had. The health
#: check fires only above a threshold -- twenty overruns before ten valid
#: examples -- and these generators sit far below it, so an overrun is still
#: dropped and re-drawn with nothing said. Measured across seven seeds: 3, 7
#: produced 1 and 3 silent ``invalid`` cases respectively; 29, 11, 17, 23 and 41
#: produced none, all seven green. **Zero invalid is a property of seed 29, not
#: of the configuration.** What the removal bought is narrower and still worth
#: having: a permission that was doing nothing is gone, so if these generators
#: ever do start overrunning in bulk the run fails instead of quietly shrinking
#: its own coverage.
#:
#: What the budget buys, read off ``--hypothesis-show-statistics`` rather than
#: counted by hand: 6 examples in each of the equality's eight cells, 18 for the
#: payload sweep, 15 for ``knowledge.get`` and 10 for the generator guard --
#: **91 examples over eleven entries, every entry stopping on ``max_examples``
#: with 0 failing and 0 invalid**, and the whole file green in about 10 s.
#:
#: **A red run is far slower, and that is shrinking rather than a hang.** The
#: first person to see this file fail should know before they reach for the
#: interrupt. Measured on ``across-the-depth-generous`` under the depth-loop
#: mutation: **251 s to report, against 4 s with shrinking suppressed** and 5 s
#: for the same cell green -- roughly sixty times, because every shrink attempt
#: re-pays two canonical stores and two index builds. Several cells failing at
#: once multiply it, and CI has no job timeout (issue #104).
#:
#: Shrinking is kept anyway, and that is a decision rather than an oversight: a
#: minimised corpus is exactly what this file is for. The T-17a corpus recorded
#: at the end of this module is a shrunk counterexample, and it was reducible by
#: hand only *because* hypothesis had already cut it down.
_GENERATED = settings(
    deadline=None,
    derandomize=True,
    database=None,
)

#: Fixed so a docstring edit cannot silently change what was checked -- see
#: :data:`_GENERATED`. Any constant would do; this one is the issue number.
EXAMPLE_SEED: Final = 29


#: The caller's own parameters, enumerated rather than generated.
#:
#: They are a small, known, load-bearing set, and sampling them buries the case
#: that matters: whether a displaced candidate is *observable* needs ``limit`` at
#: the published maximum **and** a budget that lets fifty results through, and
#: two independent draws from the sets this file used land on that pair about one
#: example in twelve.
#:
#: Measured. The mutation replacing the depth loop with a single fifty-row fetch
#: survived twenty-five generated examples with these sampled -- twice, once with
#: the corpus size drawn as a list length and once with :func:`_visible_documents`
#: already laddered. With both enumerated it dies, in ``across-the-depth`` /
#: ``generous``, on the equality itself.
#:
#: The same sets ``test_mcp_tools.py`` enumerates, minus its ``one-below``, which
#: it keeps for a leak this file's corpora cannot produce.
#:
#: ``defaults`` is ``{}`` and has to stay ``{}``: it means *the tool's own
#: defaults*, not a restatement of them, so it keeps testing whatever
#: :func:`theurian.mcp.tools.knowledge_search` defaults to rather than what this
#: file thought it defaulted to when the line was written. That only works while
#: :func:`_search` adds nothing of its own, which is what
#: :func:`test_the_defaults_argument_set_really_sends_no_parameters` holds.
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
@seed(EXAMPLE_SEED)
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


@seed(EXAMPLE_SEED)
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

    probe = _search(pair.probe, case, **case.arguments)

    _assert_the_pair_bites(pair, probe)
    published = json.dumps({key: value for key, value in probe.items() if key != "query"})
    for secret in case.secrets:
        assert secret not in published, "a withheld payload reached the response"
    for withheld in case.withheld(secret=True):
        assert withheld.title not in published, "so did a withheld document's title"
        assert withheld.item_id not in published, "so did its id"


@seed(EXAMPLE_SEED)
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

    **Three withholding mechanisms reach these four arms, not two** (#119). The
    third is :data:`ABOVE_THE_CEILING`, where the row is ``approved`` and current
    and the *deployment* may not disclose its level -- so the refusal has to be
    identical across a boundary the other two never cross. It is the case a
    hand-written implementation gets wrong in the most tempting way: "you are not
    cleared for this item" is a helpful message, and it confirms both that the
    item exists and what class it is in.
    """
    pair = _pair(tmp_path_factory.mktemp("absence"), case)
    withheld_id = case.withheld(secret=True)[0].item_id
    visible_id = case.visible[0].item_id
    grant = case.grant

    from_probe = _failing(
        pair.probe, "knowledge.get", grant, projectId=PROJECT_ID, itemId=withheld_id
    )
    from_control = _failing(
        pair.control, "knowledge.get", grant, projectId=PROJECT_ID, itemId=withheld_id
    )
    absent = _failing(pair.probe, "knowledge.get", grant, projectId=PROJECT_ID, itemId=NO_SUCH_ITEM)
    present = _call(pair.probe, "knowledge.get", grant, projectId=PROJECT_ID, itemId=visible_id)

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


def _a_corpus_the_parameters_move(tmp_path: Path) -> tuple[ProjectRegistry, _Case]:
    """Twenty matching documents, and a ``_Case`` whose triple is not the defaults.

    Shared by the two guards below, which are mirror images: one asks whether an
    *empty* parameter set reaches the tool empty, the other whether a *stated*
    one reaches it at all. Both need the same precondition -- a corpus on which
    the parameters change the answer -- and each asserts it rather than assuming
    the other did.

    The triple is the opposite of the tool's defaults on all three axes, so
    either failure shows up as the answer landing on the wrong side.

    **Their coverage is asymmetric, and it is worth knowing which way round.**
    Measured on the verdict path, then attributed with fail-fast off:

    ============================================ ================ ==============
    mutation                                     mirror guard     defaults guard
    ============================================ ================ ==============
    ``_search`` re-injects ``case.arguments``     RED              RED
    ``_Case.arguments`` returns ``{}``            RED              green
    ============================================ ================ ==============

    So the mirror guard catches both and the other catches one -- they do *not*
    cover each other, and a note saying they did would be wrong in the direction
    that matters. Neither is redundant: they pin different properties, and the
    ``defaults`` guard is what fails with the message naming ``ARGUMENT_SETS``
    when the H-1 defect returns. The mirror guard's failure under that mutation
    is a side effect -- re-injection stops ``tool_defaults`` being the tool's
    defaults -- and a side effect is not a reason to delete the test that says
    what actually broke.
    """
    created_at = datetime.now(UTC) - AGE_OFFSET
    documents = tuple(
        _Document(
            item_id=f"architecture.visible-{index:02d}",
            revision_id=_ulid("VS", index),
            title="handle",
            body=f"cache manifold beacon domain machine combine median nominal {index}.",
            status=KnowledgeStatus.APPROVED,
        )
        for index in range(20)
    )
    registry = _build_project(tmp_path / "one", documents, created_at)
    case = _Case(
        visible=documents,
        withheld_filler=(),
        withheld_titles=(),
        payloads=(),
        withheld_by=RETIRED_AFTER_BUILD,
        query="manifold",
        limit=MAX_RESULTS,
        max_tokens=MAX_BUDGET_TOKENS,
        use_dense=True,
    )
    return registry, case


def test_the_parameters_a_case_carries_reach_the_tool(tmp_path: Path) -> None:
    """The mirror of the guard below, and it was missing until a mutation said so.

    :attr:`_Case.arguments` is the *other* parameter source in this file: the one
    test that wants the generated triple rather than an enumerated set asks for
    it by name. Nothing held it. Measured on the verdict path --
    ``_Case.arguments`` replaced by ``return {}`` came back **SURVIVED** against
    the whole suite, so eighteen examples of
    :func:`test_no_withheld_payload_appears_anywhere_a_caller_reads` would have
    silently collapsed onto the tool defaults with every test still green.

    That is exactly the defect ``defaults`` had, arriving from the opposite
    direction: there a set that should have been empty was full, here a set that
    should be full can be emptied. One guard cannot cover both -- the guard below
    passes unchanged when ``arguments`` returns ``{}``, because it never calls it.

    The second assertion is the anti-vacuity one, and it is not the same fact as
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
        "a case's parameters must reach the tool, or the sweep that uses them "
        "runs eighteen examples at the defaults and nothing says so"
    )


def test_the_defaults_argument_set_really_sends_no_parameters(tmp_path: Path) -> None:
    """That ``ARGUMENT_SETS``' first entry is not quietly a fifth generated draw.

    It was. :func:`_search` seeded its dict from the generated ``_Case`` and let
    the caller override, so ``{}`` overrode nothing and ``defaults`` ran twelve
    generated parameter triples instead of the tool's own -- across the 24 calls
    it issued, ``(10, 2000, False)`` appeared zero times. Nothing said so: the
    comment above :data:`ARGUMENT_SETS` claimed the opposite, and every one of
    those twelve examples passed.

    Asserted behaviourally rather than by inspecting what :func:`_search` builds,
    because the defect is not that a dict has extra keys -- it is that a named
    argument set does not test what its name says. The ``_Case`` here carries the
    *furthest* possible parameters from the defaults, so an implementation that
    re-injects them lands on the ``generous`` answer instead and the first
    assertion fails.

    The second assertion is what stops the first holding vacuously: on a corpus
    where the defaults and ``generous`` produce the same answer, "no arguments
    equals the documented defaults" is true of any implementation, including the
    broken one.
    """
    registry, case = _a_corpus_the_parameters_move(tmp_path)

    nothing_stated = _search(registry, case)
    # 10 is `knowledge_search`'s own `limit` default; there is no constant for it
    # to import, so a change to that signature fails here and names this line.
    spelled_out = _search(registry, case, limit=10, maxTokens=DEFAULT_BUDGET_TOKENS, useDense=False)
    generous = _search(registry, case, **dict(ARGUMENT_SETS[2][0]))

    assert nothing_stated == spelled_out, (
        "an empty argument set must reach the tool as no parameters at all, or "
        "`defaults` is testing whatever the generator drew"
    )
    assert nothing_stated != generous, (
        "and this corpus must be one the parameters actually move, or the "
        "assertion above holds for any implementation"
    )


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
    page = index.search_lexical(
        "ledger",
        project_id=PROJECT_ID,
        limit=MAX_RESULTS,
        include_unapproved=True,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    assert page.exhausted, "or the rejected item is merely below the cut"
    assert {row.item_id for row in page.rows} == {visible.item_id}, (
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


@seed(EXAMPLE_SEED)
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

    **This exact shape is *not* closed by issue #15's trigger, and the distinction
    is the r3 flavor fix.** The draft here is one an operator *chose* to index with
    ``--include-unapproved``; ``may_surface`` passes it under that flag, so the
    purge's flavor-aware reduction (`revisions_to_purge`, built with this index's
    own ``indexesUnapproved=True``) judges it surfaceable *there* and keeps it --
    which is correct, because a caller who passes the flag is handed it, so it is
    off by default rather than withheld
    (`test_a_draft_in_an_include_unapproved_index_survives_an_unrelated_replay`
    holds the purge does not over-reach). What the trigger *does* close is the
    other draft shape -- a doc dropped to ``draft`` **in a default index**, where
    it is non-surfaceable at that build's flavor and is purged
    (`test_a_withdrawal_purges_the_published_index_without_a_separate_build`'s
    ``inplace-draft`` face). This test is built by hand at flavor ``True``, so it
    pins the narrowed residual the threat model still records for the
    ``--include-unapproved`` configuration.
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
        "backend",
        project_id=PROJECT_ID,
        limit=MAX_RESULTS,
        include_unapproved=True,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    as_the_search_asks = index.search_lexical(
        "backend",
        project_id=PROJECT_ID,
        limit=MAX_RESULTS,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    answer = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="backend", limit=10)

    assert as_the_search_asks.exhausted, (
        "the refusal below has to be the WHERE and not the cut, and since "
        "`RetrieverPage` this is answerable rather than inferable"
    )
    assert withheld_id in {row.item_id for row in with_the_flag.rows}, (
        "the draft's chunks must be in the index file, or there is no withheld row"
    )
    assert withheld_id not in {row.item_id for row in as_the_search_asks.rows}, (
        "and the retriever must refuse them on the search's own flags, so the "
        "reordering is a collection statistic and not a returned row"
    )
    assert withheld_id not in {result["itemId"] for result in answer["results"]}, (
        "nor may the answer carry it"
    )
    assert len(as_the_search_asks.rows) > 1, (
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

    Both shapes, because they publish different values and a builder that
    produced one when asked for the other would be invisible otherwise:

    - ``stale`` -- the retired shape leaves the index behind the store, the draft
      shape changes nothing after the build;
    - ``indexesUnapproved`` -- the retired shape builds at the shipped default
      and the draft shape does not, which is the only reason the ``false`` value
      of that published field appears in any response this file compares.
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
    assert from_behind["retrieval"]["indexesUnapproved"] is False, (
        "the retired shape must build at the shipped default, or no compared "
        "response ever carries that value of this published field"
    )
    assert from_level["retrieval"]["indexesUnapproved"] is True, (
        "and the draft shape must build with the flag, or its drafts are not in "
        "the index and there is nothing withheld"
    )
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
        "manifold",
        project_id=PROJECT_ID,
        limit=MAX_RESULTS,
        include_unapproved=False,
        visible_sensitivities=EVERY_SENSITIVITY,
    )

    assert by_id[approved.item_id].status is KnowledgeStatus.APPROVED
    assert by_id[retired.item_id].status is KnowledgeStatus.DEPRECATED, (
        "the second write must really have retired it in the store the gate asks"
    )
    assert by_id[retired.item_id].current_revision_id is not None, (
        "an item with no current revision is withheld for the wrong reason"
    )
    assert retired.item_id in {row.item_id for row in offered.rows}, (
        "and the index must still offer it on the caller's own flags, or nothing reaches the gate"
    )


# ---------------------------------------------------------------------------
# The shipped close: a withdrawal purges the published index, no rebuild (issue #15)
# ---------------------------------------------------------------------------
#
# Everything above builds its corpora through the application layer and stops a
# withheld row with the *canonical gate* -- the defense that holds while a stale
# published index still contains the row (the in-flight / purge-failure residual,
# ADR-0024 decision 5). This section is the other half: the shipped
# `theurian migrate apply` now *removes* the withdrawn rows from the published
# index the instant the withdrawal commits, so the residual is bounded to one
# command rather than "until someone runs a rebuild".
#
# It has to run the real CLI -- `migrate apply` is where the purge is wired, and
# the property is that no second `index build` is needed. So it builds two real
# projects, `holds-it` (indexed while the secret was approved, then withdrawn ->
# purged) and `never-did` (withdrawn first, then indexed), and asserts they answer
# the same query identically. That is ADR-0024's acceptance property in the flesh:
# "an index that held the withdrawn rows and had them purged answers identically
# to an index that never held them" -- the ranking, chunk ids and scores, which is
# what T-17a moves. Build identity and freshness metadata (`indexBuildId`,
# `stale`, `note`) differ by construction between a purged build and a fresh one
# and are not part of that property; they are masked, named, below.

#: Both shipped projects register under this id, in separate registries, so
#: `projectId` is equal as an input rather than compared as an output -- the same
#: device the generated pairs use.
SHIPPED_PROJECT_ID: Final = "absence-pair"

#: Two phrases, because the channel is a *reweighting between* them: one query
#: term scaled by the withheld document's presence, one carried only by the
#: visible pair. A single-phrase query scales every row by the same factor and
#: preserves order -- see `test_the_bm25_probe_corpus_can_still_flip` in
#: `test_retrieval_service.py`, whose corpus this mirrors.
SHIPPED_QUERY: Final = "quarantine ledger"

#: A marker no other body contains, so finding a fragment of it in a response is
#: proof it came out of the withheld document and nowhere else.
SHIPPED_SECRET_MARKER: Final = "ROTATEME7SECRET"  # noqa: S105 - a test marker, not a credential

#: The withheld document, tuned to flip the visible pair. Long and dense in
#: `quarantine`, so its presence moves both BM25's `avgdl` (length) and the `idf`
#: of the phrase the two visible rows share -- enough to reorder them. This is the
#: `INCIDENT_BODY` shape from `test_retrieval_service.py`, which a sweep proved
#: flips; the marker is appended once, a rare token the query never names, so it
#: changes only the length. Without the purge the probe's stale index ranks the
#: visible pair the other way from `never-did`, which is what makes the equality
#: below able to fail.
_SHIPPED_SECRET_BODY: Final = (
    "# Payment tenant incident\n\n"
    + "".join(
        f"## {section}\n\nThe quarantine rehearsal for the payment tenant is recorded here. " * 6
        + "\n\n"
        for section in ("Rehearsal", "Finding")
    )
    + f"\n{SHIPPED_SECRET_MARKER}\n"
)

_SHIPPED_DOC_MIGRATION: Final = """apiVersion: theurian.dev/v1
id: {mid}
createdAt: 2026-08-03T10:00:00+09:00
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
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""

#: Retirement: the whole item is deprecated, so a published index built while it
#: was approved must stop holding it.
_SHIPPED_DEPRECATE_MIGRATION: Final = """apiVersion: theurian.dev/v1
id: 01K1WDEPAA01234567890ABCDE
createdAt: 2026-08-03T11:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.secret
    reason: pulled after the index was built
"""

#: The undo of a deprecation. Sorts after the deprecation (WR > WD), so a replay
#: applies it second and the item's final status is ``approved``.
_SHIPPED_RESTORE_MIGRATION: Final = """apiVersion: theurian.dev/v1
id: 01K1WRESAA01234567890ABCDE
createdAt: 2026-08-03T12:00:00+09:00
author: engineer@example.com
operations:
  - op: restoreItem
    itemId: architecture.secret
"""

#: An unrelated later change, whose only job is to shift the state hash so the
#: next ``migrate apply`` rebuilds the canonical database and replays the whole
#: set (ADR-0016) -- the moment an operation-log withdrawal set re-purged the
#: since-restored item.
_SHIPPED_EXTRA_BODY: Final = "# Extra note\n\nThe quarantine ledger has an extra note.\n"

_SHIPPED_UNRELATED_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: 01K1XADDAA01234567890ABCDE
createdAt: 2026-08-03T13:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.extra
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.extra
    revisionId: 01K1XADDV101234567890ABCDE
    contentFile: ../knowledge/architecture/extra.md
    contentSha256: {body_pin(_SHIPPED_EXTRA_BODY)}
    metadata:
      title: Extra note
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/extra.md
"""

#: Redaction: a new revision supersedes the approved one and moves
#: ``currentRevisionId`` forward. It is `rejected` -- non-surfaceable under every
#: flag -- so `never-did` never indexes it and the two builds agree on the item
#: being absent; the point the purge has to make is that the *previous* revision's
#: chunks, which hold the pre-redaction text, leave the published index.
_SHIPPED_REDACTED_BODY: Final = "# Runbook\n\nThe credential now lives in the secret store.\n"

_SHIPPED_SUPERSEDE_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: 01K1WDEPAA01234567890ABCDE
createdAt: 2026-08-03T11:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.secret
    revisionId: 01K1SCRTV201234567890ABCDE
    expectedRevision: 01K1SCRTV101234567890ABCDE
    contentFile: ../knowledge/architecture/secret-redacted.md
    contentSha256: {body_pin(_SHIPPED_REDACTED_BODY)}
    metadata:
      title: Runbook
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/secret-redacted.md
"""

#: Reject/draft in place -- the third verb and its flavor face (ADR-0024 decision
#: 5). An ``upsertRevision`` that reuses the item's *current* revision id and its
#: body, changing only ``status``: the revision id never moves, so an op-log
#: withdrawal set misses it, while the item's surfaceability changes. The same
#: content file keeps ``append_revision`` a no-op (FR-K8). ``reject`` is withheld
#: at every flavor; ``draft`` is withheld from a **default** index only -- which
#: these projects build -- and its chunks moving visible-row rankings there is the
#: security face a uniform ``include_unapproved=True`` reduction leaves open.
_SHIPPED_INPLACE_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: 01K1WDEPAA01234567890ABCDE
createdAt: 2026-08-03T11:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.secret
    revisionId: 01K1SCRTV101234567890ABCDE
    contentFile: ../knowledge/architecture/secret.md
    contentSha256: {body_pin(_SHIPPED_SECRET_BODY)}
    metadata:
      title: Runbook
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {{status}}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/secret.md
"""

#: The visible pair, deliberately close and opposite: one leans on `quarantine`,
#: the other on `ledger`, each naming the other's term once, so a shift in the
#: withheld document's weight moves them against each other rather than together.
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
#: base32 -- no I, L, O or U -- because `MigrationId`/`RevisionId` reject the rest,
#: and the migration id is also the file's name prefix, which the loader pins.
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
    root: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch, *args: str
) -> dict[str, Any]:
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    result = _SHIPPED_CLI.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, f"{' '.join(args)}: {result.output}"
    payload: dict[str, Any] = json.loads(result.output)
    return payload


_SHIPPED_WITHDRAWAL: Final = {
    "deprecate": _SHIPPED_DEPRECATE_MIGRATION,
    "supersede": _SHIPPED_SUPERSEDE_MIGRATION,
    "reject": _SHIPPED_INPLACE_MIGRATION.format(status="rejected"),
    "inplace-draft": _SHIPPED_INPLACE_MIGRATION.format(status="draft"),
}


def _write_shipped_corpus(root: Path, *, face: str) -> None:
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"
    for item, mid, rid, slug, title in _SHIPPED_VISIBLE:
        (knowledge / f"{slug}.md").write_text(_SHIPPED_VISIBLE_BODY[slug])
        (migrations / f"{mid}-{slug}.yaml").write_text(
            _SHIPPED_DOC_MIGRATION.format(
                mid=mid,
                item=item,
                rid=rid,
                slug=slug,
                title=title,
                pin=body_pin(_SHIPPED_VISIBLE_BODY[slug]),
            )
        )
    for number in range(_SHIPPED_NOISE):
        slug = f"window-{number}"
        filler = (
            f"# Deployment window {number}\n\nRelease {number} goes out on Thursday after the "
            f"staging soak has run for a day.\n"
        )
        (knowledge / f"{slug}.md").write_text(filler)
        (migrations / f"01K1NZ{number}AAA01234567890ABCDE-{slug}.yaml").write_text(
            _SHIPPED_DOC_MIGRATION.format(
                mid=f"01K1NZ{number}AAA01234567890ABCDE",
                item=f"architecture.{slug}",
                rid=f"01K1NZ{number}REV01234567890ABCDE",
                slug=slug,
                title=f"Deployment window {number}",
                pin=body_pin(filler),
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
    if face == "supersede":
        (knowledge / "secret-redacted.md").write_text(_SHIPPED_REDACTED_BODY)


def _write_shipped_withdrawal(root: Path, *, face: str) -> None:
    (root / ".theurian/migrations/01K1WDEPAA01234567890ABCDE-withdraw.yaml").write_text(
        _SHIPPED_WITHDRAWAL[face]
    )


def _declare_every_level(data_dir: Path) -> None:
    """Declare a ``restricted`` ceiling, the way an operator entitled to the whole
    corpus does.

    Mode 0600 because ``load_serving_profile`` refuses a profile other local users
    can reach, and ``write_text`` under the usual umask leaves 0644 -- a caller
    that skipped this would exercise that refusal and read as "the build failed".
    """
    auth = data_dir / "auth"
    # 0700 on the directory as well as 0600 on the file. `load_serving_profile`
    # refuses both, because a directory's write bit governs *replacing* an entry
    # in it -- and a bare `mkdir` under the usual umask leaves 0755, which is the
    # shape `FileSecretStore.set` never creates and this refusal exists for.
    auth.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth.chmod(0o700)
    profile = auth / SERVING_PROFILE_FILENAME
    profile.write_text(f"{Sensitivity.RESTRICTED.value}\n", encoding="utf-8")
    profile.chmod(0o600)


def _shipped_project(
    root: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch, *, face: str, build_before: bool
) -> ProjectRegistry:
    """One real CLI project that withdraws a secret through `migrate apply`.

    ``build_before`` is the whole of the difference between the pair:

    - ``holds-it`` (``True``) builds and publishes the index while the secret is
      approved -- so it is in the file -- and *then* withdraws it, which is the
      apply that fires the purge (ADR-0024 decision 5);
    - ``never-did`` (``False``) withdraws first and builds afterward, so the
      secret's surfaceable revision is never indexed.

    Both apply the identical migration set, so they reach the identical canonical
    state and report the identical ``snapshotId``. Neither runs a second
    ``index build`` after the withdrawal: that a purged build needs no rebuild is
    the property under test.

    **The ``restricted`` ceiling is declared, and it is what keeps this about the
    status axis.** ``_call`` answers under :data:`ALLOW_ALL_GRANT`, and a build is
    specific to the ceiling it ran under (#119 phase 3): once the shipped default
    became ``internal``, a build made under it recorded a *narrower* flavor than
    that grant, ``_published_index`` stood aside, and both sides of the equality
    answered from the substring scan with nothing to compare. Declaring the
    ceiling makes the build's flavor and the serving grant agree, which is the
    state this pair was written in and the only one in which its equality is
    about a withdrawal.
    """
    root.mkdir(parents=True)
    for git in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(git, cwd=root, check=True, capture_output=True)  # noqa: S603

    _declare_every_level(data_dir)
    _shipped_cli(root, data_dir, monkeypatch, "init")
    _shipped_cli(
        root, data_dir, monkeypatch, "project", "register", "--project-id", SHIPPED_PROJECT_ID
    )
    _write_shipped_corpus(root, face=face)
    _shipped_cli(root, data_dir, monkeypatch, "migrate", "apply")
    if build_before:
        _shipped_cli(root, data_dir, monkeypatch, "index", "build")
    _write_shipped_withdrawal(root, face=face)
    _shipped_cli(root, data_dir, monkeypatch, "migrate", "apply")
    if not build_before:
        _shipped_cli(root, data_dir, monkeypatch, "index", "build")
    return ProjectRegistry.default(data_dir)


def _published_offers(root: Path, query: str, *, include_unapproved: bool) -> set[str]:
    """The item ids the *published* build offers for a query, below every gate.

    Resolves the build the pointer names rather than a fixed id, so it reads the
    purged build after a withdrawal and the original before one.
    """
    payload = read_active_index_pointer(ProjectPaths.of(root)).payload
    assert payload is not None, "the project must have a published index"
    index = SqliteIndexStore(ProjectPaths.of(root).index_for(str(payload["indexBuildId"])))
    page = index.search_lexical(
        query,
        project_id=SHIPPED_PROJECT_ID,
        limit=500,
        include_unapproved=include_unapproved,
        visible_sensitivities=EVERY_SENSITIVITY,
    )
    assert page.exhausted, "the page must be complete for an absence to mean anything"
    return {row.item_id for row in page.rows}


def _masked(response: dict[str, Any]) -> dict[str, Any]:
    """The response minus the fields that describe the *build*, not the answer.

    ``indexBuildId``, ``stale`` and ``note`` differ by construction between a
    purged build and a fresh one: the purge preserves the source build's state
    hash (a removal is not a rebuild), so ``holds-it`` reports ``stale: true`` and
    ``never-did`` ``stale: false``, and the ``note`` follows ``stale``. None of
    the three is what T-17a moves -- that is the ranking, which is compared
    unmasked -- and every other field, ``snapshotId`` included, must be equal.
    """
    masked = dict(response)
    retrieval = dict(masked["retrieval"])
    for field in ("indexBuildId", "stale", "note"):
        retrieval.pop(field, None)
    masked["retrieval"] = retrieval
    return masked


@pytest.mark.parametrize("face", ["deprecate", "supersede", "reject", "inplace-draft"])
def test_a_withdrawal_purges_the_published_index_without_a_separate_build(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, face: str
) -> None:
    """T-17a, issue #15. Every withdrawal verb closes the window, no rebuild.

    The verbs ADR-0024 decision 5 names: a retirement (``deprecateItem``); a
    redaction (``upsertRevision`` moving ``currentRevisionId`` past the revision
    whose chunks hold the pre-redaction text); a reject in place (an
    ``upsertRevision`` reusing the current revision id and only changing status,
    the one an operation-log set misses because the revision id never moves); and
    a **draft in place** -- the same shape, status ``draft``, which is withheld
    from the *default* index these projects build even though ``may_surface``
    passes a draft under ``--include-unapproved``. The last is the flavor face: a
    uniform ``include_unapproved=True`` reduction leaves its now-draft chunk in the
    default file, moving visible-row rankings (T-17a) in the shipped default. All
    driven through the real ``theurian migrate apply`` with **no ``index build``
    after the withdrawal**, because the property is that the purge leaves a
    published build a search can use.

    Three assertions, and the corpus is tuned so each can fail:

    - the published build no longer offers the withheld item at all -- the purge
      removed its chunks, so no retriever ranks it;
    - ``holds-it`` (built holding the secret, then purged) answers the query
      **identically** to ``never-did`` (which never held it), over the whole
      response but for the three build-identity fields the module masks. This is
      ADR-0024's acceptance property, and it can only fail on a corpus where the
      withheld document *reorders* the visible pair -- which is why the secret is
      long and dense in one query term (`_SHIPPED_SECRET_BODY`). Neutralize the
      purge and this separates, not just the presence assertion;
    - the equality's non-vacuity is pinned directly: the probe's own stale build,
      read before the purge would have run, ranks the visible pair the *other* way
      from its post-purge answer. If it did not, the equality above would hold for
      any implementation.

    RED on the pre-fix wiring: `deprecate`/`supersede` keep the secret's chunk
    (op-log worked there); `reject` keeps it because the op-log never saw the
    reject; `inplace-draft` keeps it because a uniform-``True`` reduction judges a
    draft surfaceable even for a default index.
    """
    base = tmp_path_factory.mktemp("shipped")
    probe_root = base / "holds-it"
    probe = _shipped_project(
        probe_root, base / "holds-it-data", monkeypatch, face=face, build_before=True
    )
    control = _shipped_project(
        base / "never-did", base / "never-did-data", monkeypatch, face=face, build_before=False
    )

    offered = _published_offers(probe_root, SHIPPED_QUERY, include_unapproved=True)
    assert "architecture.secret" not in offered, (
        "the withdrawal must have purged the secret from the published build, so "
        "no retriever offers it even under includeUnapproved"
    )

    from_probe = _call(probe, "knowledge.search", projectId=SHIPPED_PROJECT_ID, query=SHIPPED_QUERY)
    from_control = _call(
        control, "knowledge.search", projectId=SHIPPED_PROJECT_ID, query=SHIPPED_QUERY
    )

    assert from_probe["count"] == from_control["count"] > 0, (
        "both must answer the visible corpus, or two empty answers prove nothing"
    )
    assert "architecture.secret" not in {r["itemId"] for r in from_probe["results"]}, (
        "and the withheld item must not be in the answer"
    )
    assert SHIPPED_SECRET_MARKER not in json.dumps(from_probe), "nor its payload anywhere in it"
    assert from_probe["retrieval"]["snapshotId"] == from_control["retrieval"]["snapshotId"], (
        "the two apply the same migrations, so they answer from the same canonical state"
    )
    stale_order, purged_order = _visible_orders_before_and_after_the_purge(probe_root)
    assert stale_order != purged_order, (
        "the corpus must be one whose visible order the withheld document flips: "
        "the probe's pre-purge build must rank the visible pair the other way from "
        "its purged one, or the equality below holds for any implementation"
    )
    assert _masked(from_probe) == _masked(from_control), (
        "a build that held the withdrawn rows and had them purged must answer "
        "identically to one that never held them -- ranking, chunk ids and scores"
    )


def _visible_orders_before_and_after_the_purge(probe_root: Path) -> tuple[list[str], list[str]]:
    """The probe's visible lexical order from its pre-purge build and its purged one.

    Publishing never deletes (ADR-0024 point 6), so the build `index build` wrote
    while the secret was approved is still on disk beside the purged successor the
    pointer now names. Reading both directly, at the same retriever the tool's
    first pass uses, is the exact before/after the purge produced -- without
    rebuilding. Their visible orders differing is what makes the response equality
    a real test rather than one satisfied by any two corpora that happen to agree.
    """
    paths = ProjectPaths.of(probe_root)
    published = read_active_index_pointer(paths).payload
    assert published is not None
    published_id = str(published["indexBuildId"])
    prefix, suffix = "theurian-index-", ".sqlite"

    def visible_order(build: Path) -> list[str]:
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

    purged = visible_order(paths.index_for(published_id))
    for build in sorted(paths.state.glob(f"{prefix}*{suffix}")):
        if build.name[len(prefix) : -len(suffix)] == published_id:
            continue
        offers_secret = any(
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
        if offers_secret:  # the pre-purge build that still holds the withheld row
            return visible_order(build), purged
    raise AssertionError("no pre-purge build holding the secret was found on disk")


def test_a_restored_item_survives_the_replay_a_later_apply_forces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HIGH-2, issue #15. A deprecated-then-restored item is not deleted by a replay.

    ``migrate apply`` rebuilds the canonical database and re-applies the whole set
    whenever the state hash shifts (ADR-0016), which any later knowledge change
    does. An operation-log withdrawal set re-added every past deprecation's
    revision on that replay and never cancelled it, so a since-restored -- and now
    ``approved`` -- item was purged from the published index on an unrelated apply,
    brought back by a rebuild, and purged again: visible content deleted for good.

    The final-state computation reads the item's *current* status instead, so a
    restore cancels its deprecation and the replay withdraws nothing. Driven
    through the real CLI end to end: deprecate (purge fires), restore, rebuild,
    then an unrelated add whose only effect is to force the replay.

    RED on the pre-fix wiring: the unrelated apply's op-log re-purges the restored
    secret, and the final assertion -- the item is still offered -- fails.
    """
    data = tmp_path / "data"
    root = tmp_path / "repo"
    root.mkdir()
    for git in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(git, cwd=root, check=True, capture_output=True)  # noqa: S603

    _shipped_cli(root, data, monkeypatch, "init")
    _shipped_cli(root, data, monkeypatch, "project", "register", "--project-id", SHIPPED_PROJECT_ID)
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"
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
    _shipped_cli(root, data, monkeypatch, "migrate", "apply")
    _shipped_cli(root, data, monkeypatch, "index", "build")

    (migrations / "01K1WDEPAA01234567890ABCDE-deprecate.yaml").write_text(
        _SHIPPED_DEPRECATE_MIGRATION
    )
    _shipped_cli(root, data, monkeypatch, "migrate", "apply")
    assert "architecture.secret" not in _published_offers(
        root, "quarantine", include_unapproved=True
    ), "the deprecation must have purged the secret, or the restore below proves nothing"

    (migrations / "01K1WRESAA01234567890ABCDE-restore.yaml").write_text(_SHIPPED_RESTORE_MIGRATION)
    _shipped_cli(root, data, monkeypatch, "migrate", "apply")
    _shipped_cli(root, data, monkeypatch, "index", "build")
    assert "architecture.secret" in _published_offers(
        root, "quarantine", include_unapproved=False
    ), "the rebuild after the restore must index the now-approved secret again"

    # The unrelated add forces the replay that an op-log set re-purged the secret on.
    (knowledge / "extra.md").write_text(_SHIPPED_EXTRA_BODY)
    (migrations / "01K1XADDAA01234567890ABCDE-extra.yaml").write_text(_SHIPPED_UNRELATED_MIGRATION)
    _shipped_cli(root, data, monkeypatch, "migrate", "apply")

    assert "architecture.secret" in _published_offers(
        root, "quarantine", include_unapproved=False
    ), (
        "a restored item must survive an unrelated later apply -- the replay must "
        "not re-purge it from the published index"
    )
    registry = ProjectRegistry.default(data)
    answer = _call(registry, "knowledge.search", projectId=SHIPPED_PROJECT_ID, query="quarantine")
    assert "architecture.secret" in {result["itemId"] for result in answer["results"]}, (
        "and a caller must still be handed it"
    )


_DRAFT_BODY: Final = (
    "# Caching policy draft\n\nThe caching policy draft is under review by the team.\n"
)

_DRAFT_DOC_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: 01K1PDAAAA01234567890ABCDE
createdAt: 2026-08-03T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.policy-draft
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.policy-draft
    revisionId: 01K1PDREVA01234567890ABCDE
    contentFile: ../knowledge/architecture/policy-draft.md
    contentSha256: {body_pin(_DRAFT_BODY)}
    metadata:
      title: Caching policy draft
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: draft
      owner: platform-team
      trustLevel: inferred
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/policy-draft.md
"""

_DEPRECATE_GATEWAY_MIGRATION: Final = """apiVersion: theurian.dev/v1
id: 01K1WGATEA01234567890ABCDE
createdAt: 2026-08-03T11:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.gateway
    reason: retired, and unrelated to the draft
"""


def test_a_draft_in_an_include_unapproved_index_survives_an_unrelated_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adversarial guard (ADR-0024 decision 5, r3): a legitimately-indexed draft survives.

    A draft an ``--include-unapproved`` build was told to hold is surfaceable *at
    that index's flavor*, so an unrelated withdrawal's replay must not delete it.
    Build such an index, then deprecate a *different* item -- which replays the
    whole set (ADR-0016), touching the draft's own upsert -- and confirm the draft
    is still in the published index.

    RED under a uniform ``include_unapproved=False`` reduction: the replay would
    judge the draft non-surfaceable and purge it from the index that legitimately
    holds it -- the case the security fix must not over-reach into.
    """
    data = tmp_path / "data"
    root = tmp_path / "repo"
    root.mkdir()
    for git in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(git, cwd=root, check=True, capture_output=True)  # noqa: S603

    _shipped_cli(root, data, monkeypatch, "init")
    _shipped_cli(root, data, monkeypatch, "project", "register", "--project-id", SHIPPED_PROJECT_ID)
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"
    (knowledge / "policy-draft.md").write_text(_DRAFT_BODY)
    (migrations / "01K1PDAAAA01234567890ABCDE-draft.yaml").write_text(_DRAFT_DOC_MIGRATION)
    gateway = "# Gateway\n\nThe gateway meters every request.\n"
    (knowledge / "gateway.md").write_text(gateway)
    (migrations / "01K1GAAAAA01234567890ABCDE-gateway.yaml").write_text(
        _SHIPPED_DOC_MIGRATION.format(
            mid="01K1GAAAAA01234567890ABCDE",
            item="architecture.gateway",
            rid="01K1GAREVA01234567890ABCDE",
            slug="gateway",
            title="Gateway",
            pin=body_pin(gateway),
        )
    )
    _shipped_cli(root, data, monkeypatch, "migrate", "apply")
    _shipped_cli(root, data, monkeypatch, "index", "build", "--include-unapproved")
    assert "architecture.policy-draft" in _published_offers(
        root, "caching", include_unapproved=True
    ), "the --include-unapproved build must index the draft, or the guard proves nothing"

    (migrations / "01K1WGATEA01234567890ABCDE-deprecate-gateway.yaml").write_text(
        _DEPRECATE_GATEWAY_MIGRATION
    )
    withdrawn = _shipped_cli(root, data, monkeypatch, "migrate", "apply")

    assert "architecture.gateway" not in _published_offers(
        root, "gateway", include_unapproved=True
    ), "the unrelated deprecation must have purged its own item"
    assert "architecture.policy-draft" in _published_offers(
        root, "caching", include_unapproved=True
    ), (
        "but the draft an --include-unapproved index was told to hold must survive "
        "the replay -- a uniform False reduction would wrongly delete it"
    )
    assert withdrawn["indexPurge"]["published"] is True, (
        "the purge did publish -- it removed the deprecated gateway -- so this is a "
        "real purge that kept the draft, not a skip that touched nothing"
    )


def test_migrate_apply_reports_the_index_purge_it_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `indexPurge` block on `migrate apply --json`, pinned field by field.

    It is the operator's only signal that a withdrawal's purge fired -- or, when
    ``failed`` is true, that a stale build still holds the withdrawn rows. Nothing
    read it, so every field was hardcodable. Two applies: the one that adds and
    approves the secret reports the no-op state; the one that deprecates it reports
    a published purge that removed something and did not fail.
    """
    data = tmp_path / "data"
    root = tmp_path / "repo"
    root.mkdir()
    for git in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(git, cwd=root, check=True, capture_output=True)  # noqa: S603

    _shipped_cli(root, data, monkeypatch, "init")
    _shipped_cli(root, data, monkeypatch, "project", "register", "--project-id", SHIPPED_PROJECT_ID)
    (root / ".theurian/knowledge/architecture/secret.md").write_text(_SHIPPED_SECRET_BODY)
    (root / ".theurian/migrations/01K1SCRTAA01234567890ABCDE-secret.yaml").write_text(
        _SHIPPED_DOC_MIGRATION.format(
            mid="01K1SCRTAA01234567890ABCDE",
            item="architecture.secret",
            rid="01K1SCRTV101234567890ABCDE",
            slug="secret",
            title="Runbook",
            pin=body_pin(_SHIPPED_SECRET_BODY),
        )
    )
    created = _shipped_cli(root, data, monkeypatch, "migrate", "apply")
    assert created["indexPurge"] == {
        "published": False,
        "indexBuildId": None,
        "removed": 0,
        # This apply touched an item (the approved secret), so the engine reports a
        # candidate -- but there is no index yet, so there is nothing to purge.
        "reason": "no-published-index",
        "failed": False,
        "remedy": "",
    }, "an apply before any index build reports the no-op state"

    _shipped_cli(root, data, monkeypatch, "index", "build")
    (root / ".theurian/migrations/01K1WDEPAA01234567890ABCDE-deprecate.yaml").write_text(
        _SHIPPED_DEPRECATE_MIGRATION
    )
    withdrawn = _shipped_cli(root, data, monkeypatch, "migrate", "apply")
    purge = withdrawn["indexPurge"]

    assert purge["published"] is True, "the withdrawal must have published a purged build"
    assert purge["failed"] is False
    assert purge["removed"] > 0, "and it must have removed the secret's chunks"
    assert isinstance(purge["indexBuildId"], str) and purge["indexBuildId"], (
        "the purged build is named"
    )
    assert purge["reason"] == "" and purge["remedy"] == "", "no reason or remedy on success"
