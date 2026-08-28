"""A damaged canonical state database must not answer with its own bytes.

The canonical store holds *every* revision a project ever recorded -- `draft`,
`rejected` and `deprecated` alongside `approved` (ADR-0006) -- and the retrieval
gate is what keeps the withheld ones out of a response. An exception raised while
a row is being interpreted goes around that gate entirely: `datetime.
fromisoformat` quotes the string it would not parse, every enum quotes the member
it could not find, and each domain value object renders its argument with
``!r``. Under corruption those strings are whatever bytes happened to be on the
page, so the message a caller receives had become a function of the cell.

Measured on `67a792c` through ``build_server(registry).call_tool``: overwriting
one cell published it verbatim to an MCP client from **60** (column, tool)
positions -- among them `knowledge.get: Invalid isoformat string: '<the cell>'`.
Issue #18, and the last named member of the class
:data:`~theurian.infrastructure.sqlite.index_store._UNREADABLE_VALUES` closes on
the index side.

**Two properties, held separately because they fail separately.** The enum face
leaked a cell *and* named a remedy; the `json.loads` face named no remedy and
leaked nothing (`Expecting value: line 1 column 1 (char 0)` says nothing about
its input). A test that checked only one of them would have called each face
clean in turn.

**Written against the published surface.** Nothing here asserts an exception
type or a remedy's wording -- both were being chosen while this file was
written. What is asserted is what a caller receives: the text of a refusal, and
whether it names something the caller can run.

**The population is read out of the live schema, not listed here.** A column
added in a later milestone is swept the moment a migration writes to it, and
:func:`test_every_table_the_schema_declares_holds_a_row_to_corrupt` fails if the
corpus stops covering a table -- so neither the column list nor the corpus can
quietly fall behind the DDL.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
import shutil
import sqlite3
import subprocess
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, override

import pytest
import typer.main
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeRevision, RevisionMetadata, SourceAnchor
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite import store as sqlite_store
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
    write_transaction,
)
from theurian.infrastructure.sqlite.schema import DDL, SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The cell every corruption writes. Nothing in it is a word this codebase uses,
#: so a fragment of it appearing anywhere in a published message came out of the
#: database file and nowhere else.
SENTINEL: Final = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"

#: How much of :data:`SENTINEL` has to survive into a message before it counts as
#: disclosed. Twelve characters is short enough to catch a truncated echo and
#: long enough that no English sentence produces one by accident -- and checking
#: *windows* rather than the whole string is what stops this file pinning the
#: sample: an implementation that echoed the first half of the cell would satisfy
#: ``SENTINEL not in message`` and fail here.
LEAK_WINDOW: Final = 12

#: :data:`SENTINEL` reduced to what SQLite accepts as an identifier, for the one
#: damage this file writes into the schema text rather than into a row. Spaces
#: and `=` would leave DDL that does not parse, which is a *different* failure.
#:
#: Still caught by :func:`leaked_fragments`: `9f2a7c41d8e3`, `payroll` and
#: `240000` survive the substitution unchanged, so a message that quotes this
#: identifier is recognised as a disclosure exactly as a quoted cell is.
SCHEMA_SENTINEL: Final = re.sub(r"[^0-9A-Za-z]", "_", SENTINEL)

#: `knowledge.get`'s two id-resolution refusals, which name no remedy on purpose.
#:
#: Both fire *before* any cell is interpreted -- the corrupted column is part of
#: the item -> revision pointer chain, so the lookup misses and no value is
#: converted. Neither message contains anything but the caller's own arguments,
#: which is why they are exempt from the remedy property rather than from the
#: disclosure one. Their reach is pinned exactly by
#: :data:`REFUSALS_WITHOUT_A_REMEDY`, so this exemption cannot silently absorb a
#: converter that starts refusing without a remedy.
#:
#: That they report damage as absence is a real gap and not a design decision:
#: a caller is told the item does not exist when its row is in fact unreadable.
#: Recorded here rather than asserted, because closing it is a change to
#: `knowledge.get`, not to this store.
#:
#: **A fourth position left this tuple's reach in #30 PR2.** A sentinel in
#: `knowledge_items.project_id` takes every item out of the project's surfaceable
#: count, which the PR2 detector compares against the count `migrate apply`
#: recorded -- so `knowledge.get` now refuses that cell with "could not be fully
#: read", naming the rebuild, instead of reporting it as absence. The three that
#: remain move neither count: a corrupt `item_id` keeps the row inside both the
#: project and the status scope, and a corrupt `knowledge_revisions.revision_id`
#: or `project_id` leaves `knowledge_items` untouched.
#:
#: **This tuple, and the exact set below, say nothing about a tool that does not
#: refuse at all.** Both are read only where ``answer.refused`` holds, so the
#: worse face of the same gap is invisible to them: `knowledge.search` answers
#: ``{"count": 0, "results": []}`` over a corrupt `knowledge_items.item_id` -- a
#: successful, false statement to an agent, and not a refusal.
#: :data:`UNDETECTED_UNDERREPORT` is where that class is stated; framing it here
#: would have required a set of refusals to hold something that never refuses.
_ID_RESOLUTION_REFUSALS: Final = (
    "is not present in project",
    "points at a missing revision",
)

#: Every (tool, table, column) whose refusal names no remedy, stated exactly.
#:
#: An exact set rather than an allowance: ``names_a_remedy(msg) or is_exempt(msg)``
#: passes for any implementation that stops naming remedies altogether, and this
#: does not. A column added in a later milestone that refuses without a remedy
#: appears here as a failure.
REFUSALS_WITHOUT_A_REMEDY: Final = frozenset(
    {
        ("knowledge.get", "knowledge_items", "item_id"),
        ("knowledge.get", "knowledge_revisions", "revision_id"),
        ("knowledge.get", "knowledge_revisions", "project_id"),
    }
)

#: The one (tool, table, column) where a damaged cell makes a tool answer
#: **successfully with less than the database holds and say nothing**, exactly.
#:
#: #30's recorded residual, and the honest half of what closing the issue means.
#: The detector takes two measurements -- the live `migration_history` row count
#: against the pointer's `migrationCount`, and the live surfaceable-item count
#: against the one `migrate apply` recorded in `project_integrity` -- and a
#: sentinel here moves **neither**. The row keeps its `project_id` and its
#: `status`, so it stays inside both scopes and is still counted; what breaks is
#: the item -> revision pointer chain that `knowledge.search` walks to build a
#: result. So the tool answers ``{"count": 0, "results": [], "retrieval":
#: {"stale": false}}`` -- one result fewer than the file holds, no ``integrity``
#: key, and nothing a caller can tell apart from a project that genuinely holds
#: nothing. A count is not a checksum, and this is the shape a count cannot see.
#:
#: **A new member appearing here is a failure, not an expectation to update.**
#: That is the whole guard, and it is the one the deleted `SILENTLY_EMPTIED`
#: bought: the reach of the silent class may not grow without someone saying so.
#: A member *leaving* is the improvement -- it means the position started
#: disclosing, in which case it appears in :data:`DISCLOSED_AS_INTEGRITY` and
#: that set fails too until it is moved by hand. Nothing else in this file can
#: see either direction: every other property here is read over
#: ``answer.refused`` or over the text of a message, and this position produces
#: neither.
#:
#: `knowledge.get` is absent because it does not answer successfully at all over
#: this cell -- it refuses, in :data:`REFUSALS_WITHOUT_A_REMEDY`, reporting the
#: damage as absence. That is a different recorded gap on a different surface.
#:
#: **One position was measured joining this set and was kept out by a change to
#: the store instead** (#119 phase 6), recorded because "a new member is a
#: failure" is only a real rule if the failure is acted on. Narrowing
#: `knowledge.status`'s published counts by the deployment's sensitivity ceiling
#: was first written as a second SQL predicate, `sensitivity IN (?, ?)`, beside
#: the status one. A corrupted `knowledge_items.sensitivity` then drops out of
#: that `IN` list exactly as an above-ceiling row does, and the sweep measured
#: `('knowledge.status', 'knowledge_items', 'sensitivity')` answering
#: ``itemCount: 2 -> 0`` with no refusal and no `integrity` -- while
#: `knowledge.search` and `knowledge.get` both *refuse* the same cell, because
#: `_item_from_row` interprets it. `count_surfaceable_by_status` now aggregates
#: in SQL and admits in Python through `Sensitivity(...)`, so the cell refuses
#: there too and this set is unchanged. Restoring the predicate form turns this
#: test RED, which is what makes it the guard on that decision.
#:
#: **Four positions joined this set with GHSA-3f65 and were reasoned about here
#: rather than kept out**, because keeping them out would mean abandoning the fix.
#: The serve gate now checks that indexed text still matches canonical's
#: *current-revision served content* -- its title-plus-body, the text an excerpt
#: is cut from -- by joining `knowledge_revisions` on `(project_id, revision_id)`
#: and recomputing `served_content_hash(title, body)`
#: (`store._ITEM_WITH_CURRENT_CONTENT_SQL`). Two of the four break the *join*: a
#: sentinel in `knowledge_revisions.revision_id` or `knowledge_revisions.project_id`
#: leaves no matching current revision, so the recomputed hash is absent (NULL
#: title and body). The other two break the *hash*: a sentinel in
#: `knowledge_revisions.title` or `knowledge_revisions.body` still matches the
#: join but changes the recomputed served hash, so it no longer equals the one the
#: index recorded. All four end the same way -- `CanonicalVisibility._may_surface`
#: withholds the row and `knowledge.search` answers ``count: 0`` with no
#: `integrity` key. This is the *same class* as `knowledge_items.item_id` above --
#: a broken item -> revision chain the count-based #30 detector cannot see, now
#: reaching one table further because the gate reads one table further -- and it
#: fails in the safe direction: a revision row a bit-flip has damaged, in its
#: identity or in its served text, cannot vouch for the derived index text keyed to
#: it, so the text is withheld rather than served unverified (ADR-0004: the index
#: is never authoritative). Before the fix these cells answered cleanly, because
#: the ranked path read title and body from the index and never touched
#: `knowledge_revisions`. Disclosing revision-row damage as `integrity` rather than
#: answering it silently is the #30 detector's job, and its scope does not yet
#: reach `knowledge_revisions`; that is a separate gap, not this fix's to close.
UNDETECTED_UNDERREPORT: Final = frozenset(
    {
        ("knowledge.search", "knowledge_items", "item_id"),
        ("knowledge.search", "knowledge_revisions", "revision_id"),
        ("knowledge.search", "knowledge_revisions", "project_id"),
        ("knowledge.search", "knowledge_revisions", "title"),
        ("knowledge.search", "knowledge_revisions", "body"),
    }
)

#: Every (tool, table, column) in the migration history where an MCP read tool
#: answers **cleanly** -- no refusal, no shrunken count, no ``integrity`` -- over
#: a cell the CLI treats as tampering, stated exactly.
#:
#: The third face of the same question, and the one neither set above can hold.
#: :data:`REFUSALS_WITHOUT_A_REMEDY` is read over refusals and
#: :data:`UNDETECTED_UNDERREPORT` over shrinking integers; these positions produce
#: neither, because what changed is *upstream* of the answer: `knowledge.status`
#: used to reach the migration history through ``applied_migrations``, which
#: parses every row into a ``MigrationId`` and a ``ContentHash``, so a damaged
#: `migration_id` or `checksum` made it refuse. #30 PR1 replaced that with a bare
#: ``COUNT(*)`` that interprets no cell -- which is what keeps the integrity
#: check itself unable to refuse or quote on the damage it exists to report
#: (#18) -- and the two positions became clean answers.
#:
#: **What it guards is that this stays a trade and not a loss.** The measurement
#: below pairs each position with the CLI: `migrate status` and `migrate apply`
#: exit 4 on exactly these two cells, so the tamper detection the read tools gave
#: up still exists on the surface a user runs to check a project. A position
#: leaving this set means a read tool started refusing again (which would put the
#: `COUNT` back on a parsed row); a position joining it means a cell the CLI calls
#: tampering became invisible to a read tool that used to notice it.
#:
#: All three tools, not `knowledge.status` alone: `knowledge.search` and
#: `knowledge.get` call the same ``COUNT`` on every request, so they answer over
#: these cells identically, and a set naming only the tool whose behaviour
#: changed would leave the other two free to start refusing without a word.
ANSWERED_CLEAN_OVER_A_DAMAGED_CELL: Final = frozenset(
    {
        ("knowledge.search", "migration_history", "migration_id"),
        ("knowledge.get", "migration_history", "migration_id"),
        ("knowledge.status", "migration_history", "migration_id"),
        ("knowledge.search", "migration_history", "checksum"),
        ("knowledge.get", "migration_history", "checksum"),
        ("knowledge.status", "migration_history", "checksum"),
    }
)

#: Every (tool, table, column) in the **whole schema** where a damaged cell is
#: disclosed to a successful caller through the present-only ``integrity``
#: object (#30), exactly.
#:
#: The counterpart of :data:`UNDETECTED_UNDERREPORT` and the reason that set can
#: be one position long: between them they partition every successful answer a
#: damaged cell produces into *disclosed* and *silent*, so a position moving from
#: one to the other fails both equalities rather than sliding across quietly.
#:
#: It is also the guard that stops :data:`ANSWERED_CLEAN_OVER_A_DAMAGED_CELL`
#: being vacuous: a build where the detector never fires would make *every*
#: position "clean", and that set would be a larger frozenset nobody read as a
#: failure.
#:
#: **Keyed on the key's presence and on nothing else** -- deliberately not on
#: "shrinks a count *and* discloses". Six of the nine publish `integrity` while
#: every integer in the response stays where it was, which is the detector's own
#: shape: a lost migration row or a lost `project_integrity` record damages the
#: state a response was assembled from without changing anything the response
#: says. A set keyed on the conjunction would have held three positions and
#: called the other six a matter for no test.
#:
#: The nine, by what reaches the detector, all measured by the sweep below:
#:
#: - `migration_history.project_id` on all three tools (#30 PR1) -- the sentinel
#:   drops every row out of the ``WHERE``, so the live migration count falls to
#:   zero against a pointer recording one;
#: - `project_integrity.project_id` on all three tools (#30 PR2) -- the same
#:   mechanism against the other record: this project's row is no longer this
#:   project's, `expected_surfaceable_count` reads ``None``, and a readable
#:   database with no record has lost one;
#: - `knowledge_items.project_id` on `knowledge.search` and `knowledge.status`,
#:   and `knowledge_items.status` on `knowledge.status` (#30 PR2) -- the live
#:   surfaceable-item count moves away from the recorded one. These three are the
#:   members that also shrink a published integer, which is
#:   :data:`DISCLOSED_BESIDE_A_SHRUNKEN_COUNT`.
#:
#: `knowledge.get` is absent from the last group because it never reaches a
#: successful answer over those cells: it refuses, with the damage message and
#: the rebuild remedy, and a refusal carries no field. That refusal is held by
#: :func:`test_every_refusal_over_a_damaged_database_names_a_remedy` and, for its
#: wording, by `test_mcp_tools.py`'s `GET_DAMAGE_PHRASE` pins.
DISCLOSED_AS_INTEGRITY: Final = frozenset(
    {
        ("knowledge.search", "migration_history", "project_id"),
        ("knowledge.get", "migration_history", "project_id"),
        ("knowledge.status", "migration_history", "project_id"),
        ("knowledge.search", "project_integrity", "project_id"),
        ("knowledge.get", "project_integrity", "project_id"),
        ("knowledge.status", "project_integrity", "project_id"),
        ("knowledge.search", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "status"),
    }
)

#: The members of :data:`DISCLOSED_AS_INTEGRITY` that **also** answer with a
#: smaller published integer than the intact database produced, exactly.
#:
#: Stated so that the two sets above have no seam between them. Each is keyed on
#: one thing -- "the key is present", "a count shrank and the key is not" -- and
#: a position already disclosing that *started* silently shrinking a count would
#: move neither. Here it does: the sweep measures which disclosed positions
#: shrink, and this is the answer, so the whole shrinking class is
#: ``DISCLOSED_BESIDE_A_SHRUNKEN_COUNT | UNDETECTED_UNDERREPORT`` and nothing
#: falls between them.
#:
#: These three are what #30 PR2 bought over PR1's state, where all three answered
#: ``count: 0`` or ``itemCount: 0`` with no signal at all: the number is still
#: wrong -- neither tool can repair a row it cannot read -- but it no longer
#: arrives as a fact about the project.
DISCLOSED_BESIDE_A_SHRUNKEN_COUNT: Final = frozenset(
    {
        ("knowledge.search", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "status"),
    }
)

#: Every (tool, table, column) where a damaged cell refuses the *whole* response on
#: all three read tools, over the one integrity-record cell the detector reads back.
#:
#: `project_integrity.expected_surfaceable_count` is the first post-check cell the
#: #30 PR2 detector *interprets*: every tool reads it on every request through
#: `_measure_integrity`, and `int()` over a non-numeric cell refuses through
#: `_reading` with a remedy rather than being read as a count. So a single damaged
#: cell here turns all three tools' answers into a refusal -- distinct from
#: `project_integrity.project_id`, whose damage the detector *discloses* through
#: `integrity` (:data:`DISCLOSED_AS_INTEGRITY`), because a moved project id reads
#: back as "no record", a damage the tool answers around rather than refusing over.
#:
#: An exact set, so it fails in both directions: a tool that stopped refusing here
#: -- reading the cell as 0, which fabricates or hides a damage report depending on
#: the live count -- drops its position, and a cell that started refusing all three
#: joins it.
REFUSES_THE_WHOLE_RESPONSE: Final = frozenset(
    {
        ("knowledge.search", "project_integrity", "expected_surfaceable_count"),
        ("knowledge.get", "project_integrity", "expected_surfaceable_count"),
        ("knowledge.status", "project_integrity", "expected_surfaceable_count"),
    }
)

#: Every (tool, table, column) where a non-ISO `valid_to` refuses, stated exactly.
#:
#: `valid_to` is optional, so it is read by `_opt_dt` rather than `_dt`, and a
#: tolerant `_opt_dt` -- one that swallowed `datetime.fromisoformat`'s `ValueError`
#: and read a corrupt window as open-ended -- would slide these positions from
#: refused to a clean serve. No other property here would fail: the disclosure and
#: remedy sweeps read only over refusals, the clean-answer set is `migration_history`
#: only, and `CONVERTER_FAMILIES` reaches `datetime.fromisoformat` through
#: `knowledge_revisions.created_at` (a `_dt` read the mutation leaves refusing). So
#: this pins the `_opt_dt` refusals directly; the set is measured, not asserted.
#:
#: Four positions, not two: over the indexed corpus `knowledge.search` reads
#: canonical items and revisions on its ranked path to gate them, so it interprets
#: both `valid_to` columns exactly as `knowledge.get` does. `knowledge.status`
#: counts in SQL and builds neither, so it is absent.
REFUSED_OVER_A_NON_ISO_VALID_TO: Final = frozenset(
    {
        ("knowledge.get", "knowledge_items", "valid_to"),
        ("knowledge.get", "knowledge_revisions", "valid_to"),
        ("knowledge.search", "knowledge_items", "valid_to"),
        ("knowledge.search", "knowledge_revisions", "valid_to"),
    }
)

#: Declared by the DDL, written by no migration operation and read by no store
#: method. Excluded from the corpus-coverage guard with its reason, so the guard
#: stays a real check on every other table.
UNPOPULATED_TABLES: Final = frozenset({"traceability_edges"})

#: How many columns a single ``UPDATE`` can put a string into, across the whole
#: populated schema. Arithmetic over the DDL, table by table, ``INTEGER PRIMARY
#: KEY`` rowids and `traceability_edges` excluded: 4 + 8 + 24 + 13 + 4 + 6 + 11
#: + 13 + 11 + 5 + 2.
#:
#: The last term is `project_integrity`, added with schema version 3 (#30 PR2).
#:
#: An exact number rather than a floor. ``len(columns) > 90`` -- what stood here
#: -- let nine columns vanish from the sweep without a word, and a sweep that
#: has quietly stopped covering a column asserts nothing about it while still
#: reporting green.
CORRUPTIBLE_COLUMN_COUNT: Final = 101

BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
DRAFT_BODY: Final = "# Caching draft\n\nA proposal nobody has reviewed.\n"

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"

#: One migration that reaches every table a migration can reach. The narrow
#: fixtures elsewhere in this suite leave `knowledge_relations`,
#: `knowledge_aliases`, `knowledge_evidence` and `specifications` empty, and a
#: sweep over an empty table asserts nothing about the converters that read it.
MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      labels: [security]
      scope:
        paths: ["services/api"]
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
  - op: createItem
    itemId: architecture.caching-draft
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.caching-draft
    revisionId: 01K1BBBREV01234567890ABCDE
    contentFile: ../knowledge/architecture/caching-draft.md
    contentSha256: {body_pin(DRAFT_BODY)}
    metadata:
      title: Caching draft
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: draft
      owner: platform-team
      trustLevel: inferred
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/caching-draft.md
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: depends_on
    targetItemId: architecture.caching-draft
    note: the token cache
  - op: addAlias
    alias: architecture.auth
    itemId: architecture.auth-policy
  - op: addEvidence
    itemId: architecture.auth-policy
    anchor:
      provider: git
      sourceUri: git://demo/evidence.md
    description: the RFC that decided it
    confidence: 0.75
  - op: registerSpecification
    specId: spec.auth
    itemId: architecture.auth-policy
    sourceUri: git://demo/spec.yaml
    format: application/yaml
    status: active
"""


# -- Reading a published message ------------------------------------------


def leaked_fragments(text: str) -> tuple[str, ...]:
    """Every recognisable piece of :data:`SENTINEL` present in ``text``.

    Two overlapping tests, because a message can echo a cell either whole or in
    pieces: contiguous windows of :data:`LEAK_WINDOW` characters, and the rare
    whitespace-separated tokens on their own. ``sk-live-9f2a7c41d8e3`` alone is
    the whole disclosure even if the rest of the cell never appears.
    """
    windows = {SENTINEL[i : i + LEAK_WINDOW] for i in range(len(SENTINEL) - LEAK_WINDOW + 1)}
    tokens = {token for token in SENTINEL.split() if len(token) >= 6}
    return tuple(sorted(piece for piece in windows | tokens if piece in text))


def _command_paths() -> frozenset[str]:
    """Every command path the shipped CLI accepts, read off the Typer app.

    A remedy is checked against the real command set rather than against a list
    written here, so a remedy naming a command that was renamed or removed stops
    counting as a remedy.
    """

    def walk(command: Any, prefix: tuple[str, ...] = ()) -> Iterator[str]:
        children = getattr(command, "commands", None)
        # A group that runs its own callback is a command as well as a parent.
        # `theurian propose` is one (#212): reading "has children" as "not a
        # command" hid it from the partition below entirely, which is the one
        # failure this file exists to make impossible.
        if prefix and (not children or getattr(command, "invoke_without_command", False)):
            yield " ".join(prefix)
        if children:
            for name, child in children.items():
                yield from walk(child, (*prefix, name))

    return frozenset(walk(typer.main.get_command(app)))


def _tool_names(registry: ProjectRegistry) -> frozenset[str]:
    """Every MCP tool the server registers. A remedy may name one of these."""
    return frozenset(tool.name for tool in build_server(registry)._tool_manager.list_tools())


def names_a_remedy(text: str, *, commands: frozenset[str], tools: frozenset[str]) -> bool:
    """Whether ``text`` names something the caller can actually run.

    Matched only in the forms a published remedy really uses -- ``theurian
    <path>``, or the name inside backquotes -- because bare names are ordinary
    English and would make almost anything look actionable. Three concrete false
    positives this shape closes, all of them measured rather than imagined:

    - ``version`` occurs in "schema version" and ``init`` in "initialised";
    - the SDK prefixes every failure with ``Error executing tool
      knowledge.get:``, so a bare tool-name test calls *every* refusal a remedy.
      That one silently emptied this check: `'X' is not present in project 'Y'.`
      counted as actionable because of the prefix the SDK had added to it.
    """
    return any(f"theurian {path}" in text or f"`{path}`" in text for path in commands) or any(
        f"`{name}`" in text for name in tools
    )


# -- The CLI population ----------------------------------------------------

#: Every command path this file corrupts the database underneath.
#:
#: Chosen by what is *safe to run against one corpus a few hundred times*, not
#: by what is believed to read the canonical store -- believing it is how this
#: sweep came to be one command wide while two HIGH findings walked out through
#: `migrate status` and `migrate apply`. What the population actually reaches is
#: measured rather than asserted here, by
#: :func:`test_exactly_these_commands_notice_a_single_damaged_cell`.
CLI_SWEEP: Final = (
    ("index", "build"),
    # Swept rather than excluded, though it never opens the canonical store: it
    # resolves the project through the registry and prints *filenames*, and the
    # sweep's question is what reaches a caller's output, not which file the
    # damage was in. A `gc` that echoed a resolved path or a pointer fragment
    # would be caught here and nowhere else.
    ("index", "gc"),
    ("index", "status"),
    ("migrate", "status"),
    ("migrate", "validate"),
    ("migrate", "apply"),
    ("project", "list"),
    ("project", "status"),
    ("version",),
)

#: Every remaining command path, with the reason it cannot be swept. Held as an
#: exact partition of the real Typer app by
#: :func:`test_every_shipped_command_is_swept_or_excluded_with_a_reason`, so a
#: command added in a later milestone has to be classified rather than
#: forgotten.
CLI_NOT_SWEPT: Final = {
    "auth rotate": "rotates a stored token, so the corpus stops being the same corpus",
    "compat check": "requires --core-version and friends; resolves no project",
    "daemon start": "spawns a process and binds a port",
    "daemon status": "probes for a daemon this suite must not have running",
    "daemon stop": "signals a process this suite must not have running",
    "doctor": "a health report, not a command over this project's state: it exits "
    "non-zero on a healthy corpus because the fixture installs no Claude Code",
    "findings build": "never opens the canonical store: it reads git history "
    "(refs/remotes/origin/main) and writes a separate derived findings store, so a "
    "corrupted canonical cell cannot reach its output -- and it needs a fetched "
    "public ref this corpus has no reason to carry",
    "ingest": "writes migration files, which moves the state hash and so the database",
    "init": "writes .theurian/ and appends to .gitignore in the working directory",
    "project register": "rewrites the registry the corpus was built from",
    "project unregister": "deletes the registration every other command resolves",
    "propose": "writes a fresh proposal directory on every invocation, and needs eleven "
    "options to reach the point where it would write anything",
    "propose accept": "moves a migration file into .theurian/migrations/, which moves the "
    "state hash and so the database -- the same reason as `ingest`",
    "setup": "writes ~/.claude.json and a LaunchAgent on the developer's own machine",
    "uninstall": "removes what `setup` installed, on the developer's own machine",
}

#: The commands a single damaged cell can actually make fail, stated exactly.
#:
#: The vacuity guard for :data:`CLI_SWEEP`. "No swept command leaked" is
#: satisfied perfectly by a sweep whose commands never open the database at all,
#: and five of the eight above are exactly that today -- they resolve the
#: project, read the pointer and answer from files this corruption never
#: touches. Stating which three do the work means a change that stops one of
#: them reaching the store fails here rather than quietly hollowing out both
#: properties below.
COMMANDS_THAT_NOTICE_A_DAMAGED_CELL: Final = frozenset(
    {"index build", "migrate status", "migrate apply"}
)


# -- The corpus ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Corpus:
    """A registered, migrated, indexed project and a pristine copy of its state."""

    registry: ProjectRegistry
    root: Path
    database: Path
    pristine: Path


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _rendered_traceback(exc: BaseException) -> str:
    """Every exception a Rich traceback would print for ``exc``, message included.

    **The chain, not the exception.** ``rich.traceback`` follows ``__cause__``
    and ``__context__`` and renders each link with its own message, so `raise
    ... from exc` publishes the cause to whoever reads the terminal. That is the
    whole disclosure surface of an uncaught failure here:
    `StateDatabaseUnreadableError` withholds the cell from its own message and
    keeps the real exception on ``__cause__`` for whoever holds the traceback --
    which, for a CLI command, is the operator.

    Measured against the real `theurian migrate status` with
    `migration_history.checksum` overwritten: exit 1, empty stdout, and the last
    line of the boxed traceback reading ``DomainError: ContentHash must be 64
    lowercase hex characters, got '<the cell>'``. Appending only
    ``type(exc).__name__`` and ``str(exc)`` -- what stood here -- reproduced the
    *withheld* half and made this sweep blind to the half that is published.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        following = current.__cause__
        if following is None and not current.__suppress_context__:
            following = current.__context__
        current = following
    return "\n".join(parts)


def _invoke(*args: str) -> tuple[int, str]:
    """Run a CLI command, returning its exit code and everything it printed.

    An uncaught exception *does* reach a terminal as text: Typer installs a Rich
    traceback and renders it to stderr. ``CliRunner`` swallows it onto
    ``result.exception`` instead, so :func:`_rendered_traceback` is what keeps
    this sweep looking at what an operator sees rather than at what the runner
    happened to keep.
    """
    result = runner.invoke(app, [*args, "--json"])
    text = (result.stdout or "") + (result.stderr or "")
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += _rendered_traceback(result.exception)
    return result.exit_code, text


def _build_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    """Build the corpus under ``tmp_path``, with `HOME` and the data directory moved.

    Both redirections are made through ``monkeypatch`` and never through
    ``os.environ``, so a corpus built for a module-scoped fixture leaves nothing
    behind when its context closes. ``chdir`` is here too: the CLI resolves a
    project from the working directory, so a sweep that forgot it would resolve
    the developer's own checkout.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(BODY)
    (knowledge / "caching-draft.md").write_text(DRAFT_BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")
    _run("index", "build")

    (database,) = (root / ".theurian/state").glob("theurian-state-*.sqlite")
    pristine = tmp_path / "pristine-state.sqlite"
    shutil.copy2(database, pristine)
    return Corpus(ProjectRegistry.default(data_dir), root, database, pristine)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    """One project holding a row in every table a migration can write.

    Indexed as well as migrated, so `knowledge.search` answers through
    ``ResultGate`` -- the canonical read site reached by
    ``store_factory=SqliteCanonicalStore`` rather than by a direct construction,
    and therefore the one a search for ``SqliteCanonicalStore(`` does not find.
    """
    return _build_corpus(tmp_path, monkeypatch)


# -- Corrupting one cell ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Column:
    table: str
    name: str

    @override
    def __str__(self) -> str:
        return f"{self.table}.{self.name}"


def corruptible_columns(database: Path) -> tuple[Column, ...]:
    """Every column an on-disk bit flip could turn into a string, from the schema.

    Read out of the live database rather than listed, so the population follows
    the DDL. Two exclusions, both structural rather than editorial:

    - a table with no rows has no cell to corrupt;
    - an ``INTEGER PRIMARY KEY`` is SQLite's rowid and refuses a text value, so
      no ``UPDATE`` can put one there.

    Both are asserted elsewhere in this file rather than trusted: the first by
    :func:`test_every_table_the_schema_declares_holds_a_row_to_corrupt`, the
    second by :func:`test_every_column_outside_a_rowid_really_took_the_cell`.
    """
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        columns: list[Column] = []
        for table in tables:
            rows = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            if rows == 0:
                continue
            for _cid, name, declared, _notnull, _default, pk in connection.execute(
                f"PRAGMA table_info({table})"
            ):
                if pk == 1 and declared.upper() == "INTEGER":
                    continue
                columns.append(Column(table, name))
        return tuple(columns)
    finally:
        connection.close()


#: A ``CREATE TABLE`` body, up to the closing paren that sits alone on a line.
_TABLE_BLOCK: Final = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.DOTALL)

#: Lines inside a table body that declare a constraint rather than a column.
#:
#: ``REFERENCES`` is here for the *continuation* of a multi-line table-level
#: ``FOREIGN KEY`` clause, which `knowledge_items` gained with the composite
#: pointer key (#24). Without it that line parses as a column named `REFERENCES`,
#: and the two derivations of the population disagree over a column that does not
#: exist. A column-level ``REFERENCES`` never starts a line, so nothing real is
#: dropped -- and the same set equality this protects is what would report it if
#: one ever were.
_CONSTRAINT_HEADS: Final = frozenset(
    {"CHECK", "PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "REFERENCES"}
)


def declared_corruptible_columns() -> frozenset[Column]:
    """The same population, parsed out of :data:`DDL` instead of the live file.

    Deliberately a second, independent derivation. :func:`corruptible_columns`
    asks SQLite through ``PRAGMA table_info``; this reads the source text. A
    change that narrows one -- widening the rowid exclusion, say, so real
    columns stop being swept -- moves the two apart, and
    :func:`test_every_column_outside_a_rowid_really_took_the_cell` names exactly
    which columns went missing rather than reporting a count that got smaller.
    """
    declared: set[Column] = set()
    for table, body in _TABLE_BLOCK.findall(DDL):
        if table in UNPOPULATED_TABLES:
            continue
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line:
                continue
            name, _, rest = line.partition(" ")
            if name.upper() in _CONSTRAINT_HEADS:
                continue
            if "INTEGER PRIMARY KEY" in " ".join(rest.split()).upper():
                continue
            declared.add(Column(table, name))
    return frozenset(declared)


def corrupt(database: Path, column: Column) -> bool:
    """Write :data:`SENTINEL` into ``column``, returning whether anything landed.

    ``PRAGMA ignore_check_constraints`` because real corruption is a bit flip on
    a page and never passes through a constraint. Without it `confidence`, whose
    ``CHECK`` bounds it to [0, 1], could not be given a string at all -- and
    `float()` over a cell that is not a number is one of the two families the
    index store records a guard having missed.

    Every row takes the cell where the column allows it, so a corrupted value in
    a *withheld* row is swept as well as one in a visible row. Where that
    collides with a primary key, the first row alone is corrupted.
    """
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA ignore_check_constraints = ON")
    try:
        try:
            connection.execute(
                f"UPDATE {column.table} SET {column.name} = ?",  # noqa: S608 - schema-derived
                (SENTINEL,),
            )
            connection.commit()
            return True
        except sqlite3.Error:
            connection.rollback()
        try:
            connection.execute(
                f"UPDATE {column.table} SET {column.name} = ? "  # noqa: S608 - schema-derived
                f"WHERE rowid = (SELECT MIN(rowid) FROM {column.table})",
                (SENTINEL,),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            return False
        return True
    finally:
        connection.close()


def restore(corpus: Corpus) -> None:
    """Put the pristine state database back, WAL sidecars included."""
    for suffix in ("-wal", "-shm"):
        Path(str(corpus.database) + suffix).unlink(missing_ok=True)
    shutil.copy2(corpus.pristine, corpus.database)


def holds_sentinel(database: Path, column: Column) -> bool:
    """Whether the cell really carries :data:`SENTINEL` after a corruption."""
    connection = sqlite3.connect(database)
    try:
        found = connection.execute(
            f"SELECT COUNT(*) FROM {column.table} WHERE {column.name} = ?",  # noqa: S608
            (SENTINEL,),
        ).fetchone()[0]
        return bool(found)
    finally:
        connection.close()


# -- Calling the published surfaces ---------------------------------------

#: Each MCP tool with an argument set that reaches the canonical store.
TOOL_CALLS: Final = (
    ("knowledge.search", {"projectId": "demo", "query": "token"}),
    ("knowledge.get", {"projectId": "demo", "itemId": "architecture.auth-policy"}),
    ("knowledge.status", {"projectId": "demo"}),
)


@dataclass(frozen=True, slots=True)
class Answer:
    """What one call gave back: either a refusal's text, or a payload."""

    refused: bool
    text: str


async def call_tool(server: Any, tool: str, arguments: dict[str, Any]) -> Answer:
    """Invoke one tool and capture what a client would receive.

    ``call_tool`` re-raises a failing tool as the SDK's own ``ToolError``; the
    transport turns it into ``isError=True`` content carrying the same message.
    Either way the *text* is what reaches the caller, which is what this file is
    about.
    """
    try:
        result = await server.call_tool(tool, arguments)
    except SdkToolError as exc:
        return Answer(refused=True, text=str(exc))
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return Answer(refused=False, text=json.dumps(structured, ensure_ascii=False))
    return Answer(refused=False, text=result.content[0].text)


async def sweep(corpus: Corpus) -> dict[tuple[str, str, str], Answer]:
    """Corrupt every column in turn and record what each tool answered.

    One server for the whole sweep, as a running daemon would have.
    """
    server = build_server(corpus.registry)
    observed: dict[tuple[str, str, str], Answer] = {}
    for column in corruptible_columns(corpus.database):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            for tool, arguments in TOOL_CALLS:
                observed[tool, column.table, column.name] = await call_tool(server, tool, arguments)
        finally:
            restore(corpus)
    return observed


def cli_sweep(corpus: Corpus) -> dict[tuple[str, str, str], tuple[int, str]]:
    """The same sweep over :data:`CLI_SWEEP`, one command per corruption.

    The database is restored between *commands*, not merely between columns.
    ``migrate apply`` opens a write transaction and upserts the project row, so
    a shared corruption would be a different corruption by the time the next
    command ran, and an observation attributed to the wrong cell is worse than
    no observation.
    """
    observed: dict[tuple[str, str, str], tuple[int, str]] = {}
    for column in corruptible_columns(corpus.database):
        for command in CLI_SWEEP:
            assert corrupt(corpus.database, column), f"{column} took no value"
            try:
                observed[" ".join(command), column.table, column.name] = _invoke(*command)
            finally:
                restore(corpus)
    return observed


@pytest.fixture(scope="module")
def cli_observations(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[tuple[str, str, str], tuple[int, str]]:
    """One CLI sweep -- :data:`CORRUPTIBLE_COLUMN_COUNT` columns by eight commands.

    Read by three properties.

    Module-scoped over its own corpus, never over the function-scoped one. The
    sweep is corpus-neutral by construction (every corruption is restored before
    the next command runs), so sharing the *result* is safe; sharing the corpus
    with tests that damage a schema or open a write transaction would not be,
    which is why this builds its own.

    Shared because it is the expensive thing in this file. Recomputing it per
    property meant roughly 2,400 CLI invocations for three assertions over the
    same 792 observations.
    """
    with pytest.MonkeyPatch.context() as patch:
        corpus = _build_corpus(tmp_path_factory.mktemp("cli-sweep"), patch)
        return cli_sweep(corpus)


# -- The corpus really covers the schema ----------------------------------


def _declared_tables() -> frozenset[str]:
    return frozenset(re.findall(r"CREATE TABLE (\w+)", DDL))


def test_every_table_the_schema_declares_holds_a_row_to_corrupt(corpus: Corpus) -> None:
    """Guards every sweep below. An empty table is a silently skipped population.

    The sweep reads its columns from the live database, so a table the corpus
    never populates disappears from it without a word -- which is how a
    converter family comes to be "covered" by a fixture that never reaches it.
    Compared against the DDL rather than against a list, so a table added in a
    later milestone fails here until the migration above writes to it.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        populated = {
            table
            for table in _declared_tables()
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        }
    finally:
        connection.close()

    assert populated == _declared_tables() - UNPOPULATED_TABLES, (
        "the corpus no longer covers every canonical table; a sweep over the "
        "missing one asserts nothing"
    )


def test_every_column_outside_a_rowid_really_took_the_cell(corpus: Corpus) -> None:
    """Guards the population against a corruption that silently does nothing.

    ``corrupt`` returns True on a committed ``UPDATE``, which an ``UPDATE`` that
    matched no row also does. This reads the cell back: a column that reports
    success without holding the sentinel would put an untested column in the
    sweep and make every assertion about it vacuous.

    The population is pinned two ways before that runs, because a sweep can also
    be hollowed out by never reaching a column at all. Set equality against
    :func:`declared_corruptible_columns` names any column that stopped being
    swept; :data:`CORRUPTIBLE_COLUMN_COUNT` catches the case set equality cannot
    -- a column added to the DDL, which the live database and the parsed DDL
    both grow at once and neither notices.
    """
    columns = corruptible_columns(corpus.database)
    declared = declared_corruptible_columns()

    assert set(columns) == declared, (
        "the swept columns and the DDL's own columns have moved apart; "
        f"missing from the sweep: {sorted(map(str, declared - set(columns)))}, "
        f"swept but undeclared: {sorted(map(str, set(columns) - declared))}"
    )
    assert len(columns) == CORRUPTIBLE_COLUMN_COUNT, (
        f"the schema now offers {len(columns)} corruptible columns rather than "
        f"{CORRUPTIBLE_COLUMN_COUNT}; every exact set in this file was measured "
        f"against the old population"
    )

    silent: list[str] = []
    for column in columns:
        corrupt(corpus.database, column)
        if not holds_sentinel(corpus.database, column):
            silent.append(str(column))
        restore(corpus)

    assert not silent, f"corrupted without effect: {silent}"


# -- The control ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_intact_state_database_answers_every_tool(corpus: Corpus) -> None:
    """The control. Without it nothing below is attributable to the corruption.

    Every refusal the sweeps observe has to be caused by the damaged cell, and
    the only way to know that is that the same call against the same corpus
    succeeds when the cell is intact.
    """
    server = build_server(corpus.registry)

    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    assert [tool for tool, answer in answers.items() if answer.refused] == [], (
        f"a tool refused an undamaged database: {answers}"
    )


def test_an_intact_state_database_answers_the_cli(corpus: Corpus) -> None:
    """The control for the CLI half, over the whole swept population.

    Every non-zero exit the CLI sweeps observe has to be caused by the damaged
    cell. Asserted for all of :data:`CLI_SWEEP` rather than for the two commands
    that used to be swept, because a command that already fails on a healthy
    corpus contributes only noise to the remedy property below -- which is why
    `doctor`, whose non-zero exit means "problems found", is excluded from the
    population rather than tolerated inside it.
    """
    codes = {" ".join(cmd): _invoke(*cmd) for cmd in CLI_SWEEP}

    assert [name for name, (code, _) in codes.items() if code != 0] == [], (
        f"a command failed against an undamaged database: {codes}"
    )


def test_every_shipped_command_is_swept_or_excluded_with_a_reason() -> None:
    """The CLI population is a partition of the real app, not a list someone kept.

    This sweep was one command wide -- `index build` -- while the shipped CLI had
    twenty, and the two findings that walked out of `migrate status` and
    `migrate apply` were invisible to every property in this file. A list of
    commands to sweep cannot fail; a partition of the command set can, and a
    command added in a later milestone fails here until someone says which half
    it belongs in.
    """
    swept = frozenset(" ".join(command) for command in CLI_SWEEP)
    excluded = frozenset(CLI_NOT_SWEPT)

    assert swept & excluded == frozenset(), (
        f"a command is both swept and excluded: {sorted(swept & excluded)}"
    )
    assert swept | excluded == _command_paths(), (
        f"unclassified commands: {sorted(_command_paths() - swept - excluded)}; "
        f"classified but no longer shipped: {sorted((swept | excluded) - _command_paths())}"
    )
    assert all(CLI_NOT_SWEPT.values()), "an exclusion without a reason is a command someone forgot"


def test_exactly_these_commands_notice_a_single_damaged_cell(
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """The vacuity guard for the CLI sweeps. Measured, and stated exactly.

    Both CLI properties below are quantified over :data:`CLI_SWEEP`, and both are
    satisfied trivially by a command that never opens the state database. Five of
    the eight are in that position today -- they answer from the registry, the
    active pointer and the migration files, none of which this corruption
    touches -- so without this the sweep could lose `migrate apply` to a
    refactor and keep reporting green over seven commands that assert nothing.

    An exact set rather than "at least one": a command that *starts* reading the
    canonical store is a new surface for the same class, and it should arrive as
    a failure here rather than as a leak found in a review round.
    """
    noticed = {
        command for (command, _table, _name), (code, _text) in cli_observations.items() if code != 0
    }

    assert noticed == COMMANDS_THAT_NOTICE_A_DAMAGED_CELL, (
        "the set of commands a damaged cell reaches has moved; "
        f"newly reaching it: {sorted(noticed - COMMANDS_THAT_NOTICE_A_DAMAGED_CELL)}, "
        f"no longer reaching it: {sorted(COMMANDS_THAT_NOTICE_A_DAMAGED_CELL - noticed)}"
    )


# -- The disclosure property ----------------------------------------------


@pytest.mark.asyncio
async def test_no_tool_refusal_repeats_a_byte_of_the_state_database(corpus: Corpus) -> None:
    """SEC-13, issue #18. The property the whole file exists for.

    Asserted over refusals only, and that is deliberate: a *successful*
    `knowledge.get` returns the corrupted `title` and `sourceAnchors` in its
    payload, which is the caller's own content answered correctly. Six of the
    sixty-six positions where the sentinel surfaces on `67a792c` are exactly
    that, and treating them as leaks would make this test assert that the store
    stops answering.

    Reported as the whole set rather than at the first failure, because the
    defect is a class: the four faces the reproduction found were four converter
    families, and a test that stopped at `created_at` would have sent someone to
    fix one of them.
    """
    observed = await sweep(corpus)

    leaked = {
        position: leaked_fragments(answer.text)
        for position, answer in observed.items()
        if answer.refused and leaked_fragments(answer.text)
    }

    assert not leaked, f"{len(leaked)} refusals published the corrupted cell: {leaked}"


@pytest.mark.asyncio
async def test_a_damaged_row_the_caller_may_not_read_discloses_nothing(
    corpus: Corpus,
) -> None:
    """SEC-13. The sharpest face: the cell belongs to a document that is withheld.

    `architecture.caching-draft` is a `draft`, so a default `knowledge.search`
    withholds it and `knowledge.get` answers "not present". Every row is still
    walked before the gate runs, so damage in the draft's row raises during
    conversion -- and on `67a792c` that exception carried the draft's bytes to a
    caller who had just been refused the draft.

    Only the draft's row is corrupted here, unlike the sweep, so a message
    carrying the sentinel can have come from nowhere else.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        changed = connection.execute(
            "UPDATE knowledge_items SET valid_from = ? WHERE item_id = ?",
            (SENTINEL, "architecture.caching-draft"),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "the draft row must exist, or this corrupts nothing"

    server = build_server(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    leaked = {
        tool: leaked_fragments(answer.text)
        for tool, answer in answers.items()
        if leaked_fragments(answer.text)
    }

    assert [tool for tool, answer in answers.items() if answer.refused], (
        "the draft's row must be interpreted by some tool, or this asserts nothing"
    )
    assert not leaked, f"a withheld document's cell reached the caller: {answers}"


#: A window that closes before it opens, and a timestamp no corpus produces.
#: Both halves are *valid* values -- a parseable ISO-8601 string in a column
#: whose type it fits -- so what fails is the domain invariant and not a
#: converter. That distinction is the whole point of the test below.
_IMPOSSIBLE_VALID_TO: Final = "1999-01-02T03:04:05.678901+00:00"


@pytest.mark.asyncio
async def test_a_broken_invariant_over_a_withheld_row_publishes_neither_operand(
    corpus: Corpus,
) -> None:
    """The case that decides whether the guard may carve out invariant violations.

    An `InvariantViolationError` looks like the one exception worth letting
    through: it is the domain reporting a real integrity failure -- INV-3's
    content-hash check is how a tampered stored hash is caught on read -- and
    wrapping it as "this database cannot be read" reads like losing that signal.

    It is not, and this is the measurement. `ValidityPeriod.__post_init__`
    renders **both timestamps verbatim** into its message, and `_item_from_row`
    builds one for every item the store hands back, including the ones the gate
    is about to withhold. So a caller who is refused `architecture.caching-draft`
    as "not present" would, under a carve-out, be told in the same breath exactly
    when that document's validity window opens and closes.

    The values injected here are individually valid -- a parseable ISO-8601
    string in a TEXT column -- so `datetime.fromisoformat` succeeds and the only
    thing that fails is the invariant. A guard that excluded invariant
    violations would therefore let this one through while still catching every
    converter, which is precisely why it cannot be tested by the sweep above.

    What the wrapping does *not* cost: the published message still names
    `InvariantViolationError`, and the original travels on `__cause__`. The
    integrity signal survives; only its operands are withheld.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        # A `CHECK` refuses this window, and real corruption never passes
        # through one -- see `corrupt`.
        connection.execute("PRAGMA ignore_check_constraints = ON")
        real_valid_from = connection.execute(
            "SELECT valid_from FROM knowledge_items WHERE item_id = ?",
            ("architecture.caching-draft",),
        ).fetchone()[0]
        changed = connection.execute(
            "UPDATE knowledge_items SET valid_to = ? WHERE item_id = ?",
            (_IMPOSSIBLE_VALID_TO, "architecture.caching-draft"),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "the draft row must exist, or this breaks no invariant"

    server = build_server(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}
    published = {tool: answer.text for tool, answer in answers.items()}

    assert [tool for tool, answer in answers.items() if answer.refused], (
        "no tool built a ValidityPeriod from the draft's row; nothing here is tested"
    )
    assert [tool for tool, text in published.items() if _IMPOSSIBLE_VALID_TO in text] == [], (
        f"the withheld document's validity window reached the caller: {published}"
    )
    assert [tool for tool, text in published.items() if real_valid_from in text] == [], (
        f"the withheld document's start of validity reached the caller: {published}"
    )


def test_no_cli_output_repeats_a_byte_of_the_state_database(
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """The same property on the CLI half of the class.

    `theurian index build` reads every project, item and revision through the
    canonical store, so it reaches converters no MCP tool does -- `projects.
    registered_at` among them. Asserted over *all* of its output rather than only
    over failures, because unlike `knowledge.get` this command publishes counts
    and paths and never a document's content: a cell in its output came from a
    converter's complaint whatever the exit code.

    Over the whole population rather than over the command that reaches the
    store widest. `index build` walks every table and so looked like the strong
    case, but breadth is not reach: `migration_history.checksum` is a column it
    exits 0 over and all three MCP tools stay silent about, and it published
    that cell verbatim through both `migrate status` and `migrate apply` while
    this file reported green over 297 positions.
    """
    leaked = {
        position: max(fragments, key=len)
        for position, (_code, text) in cli_observations.items()
        if (fragments := leaked_fragments(text))
    }

    assert not leaked, (
        f"{len(leaked)} command outputs published the corrupted cell "
        f"(longest fragment each): {leaked}"
    )


# -- The remedy property --------------------------------------------------


@pytest.mark.asyncio
async def test_every_refusal_over_a_damaged_database_names_a_remedy(corpus: Corpus) -> None:
    """A refusal a caller cannot act on repeats forever.

    The state database is derived and git-ignored (ADR-0004), so the remedy is
    always cheap and always the same -- but an agent that receives `Expecting
    value: line 1 column 1 (char 0)` has no way to know that, and will re-issue
    the identical query. This is the face the disclosure test cannot see: the
    `json.loads` family leaked nothing and named nothing.

    The exemption is an **exact set**, not an allowance. Written as
    ``names_a_remedy(...) or is_exempt(...)`` this test would pass for an
    implementation that stopped naming remedies entirely; written as an equality
    over positions it fails the moment a new converter refuses silently.
    """
    commands, tools = _command_paths(), _tool_names(corpus.registry)
    observed = await sweep(corpus)

    silent = {
        position
        for position, answer in observed.items()
        if answer.refused and not names_a_remedy(answer.text, commands=commands, tools=tools)
    }

    assert all(any(f in observed[p].text for f in _ID_RESOLUTION_REFUSALS) for p in silent), (
        f"a refusal named neither a remedy nor an id it could not resolve: "
        f"{ {p: observed[p].text for p in silent} }"
    )
    assert silent == REFUSALS_WITHOUT_A_REMEDY, (
        "the set of refusals that name no remedy has moved; each one is a caller "
        "left with no next action"
    )


def _published_remedy(text: str) -> str:
    """The ``remedy`` field of a ``--json`` failure, or the empty string."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("remedy", "")) if isinstance(payload, dict) else ""


def test_every_cli_failure_over_a_damaged_database_carries_a_remedy(
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """The CLI publishes `remedy` as a field, so the check is the field itself.

    `--json` output is a contract: a failure is `{"error": ..., "remedy": ...}`.
    A command that failed with an empty or absent `remedy` has broken that
    contract, and no amount of prose in `error` substitutes for it -- including
    prose in an uncaught exception's message, which is where this property is
    hardest and where a one-command sweep could not look. An exception that
    escapes a `--json` command prints a Rich traceback and *nothing* on stdout,
    so a caller parsing the contract gets an empty document and no field at all.
    """
    without = {
        position: text
        for position, (code, text) in cli_observations.items()
        if code != 0 and not _published_remedy(text)
    }

    assert not without, f"{len(without)} command failures carried no remedy: {without}"


# -- The sweep really reaches each converter family ------------------------

#: One column per converter family this store reads, with the family named.
#:
#: The families come from the index store's own key -- **does this line
#: interpret bytes that came out of this file?** -- applied to the canonical
#: store: `datetime.fromisoformat`, `int`, `json.loads`, the six enums, and the
#: domain value objects, which raise `DomainError` rather than `ValueError` and
#: so escape any guard written over the latter.
#:
#: `float()` is absent, and that is a stated gap rather than an oversight: its
#: only canonical home is `knowledge_evidence.confidence`, and no MCP tool or CLI
#: command reads `list_evidence`. It is swept -- the population is the whole
#: schema -- but nothing observes it, so it cannot appear here.
CONVERTER_FAMILIES: Final = (
    ("datetime.fromisoformat", "knowledge_revisions", "created_at"),
    ("int", "schema_metadata", "schema_version"),
    ("json.loads", "knowledge_revisions", "scope_paths"),
    ("KnowledgeStatus", "knowledge_items", "status"),
    ("MediaType", "knowledge_revisions", "content_type"),
    ("RelationType", "knowledge_relations", "relation_type"),
    ("ContentHash / INV-3", "knowledge_revisions", "body"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("family", "table", "name"), CONVERTER_FAMILIES)
async def test_the_corpus_reaches_each_converter_family(
    corpus: Corpus, family: str, table: str, name: str
) -> None:
    """Guards the sweeps against being vacuous, one family at a time.

    "No refusal leaked" is satisfied perfectly by a corpus that produces no
    refusals -- which is what a fixture with an empty `knowledge_relations` does
    for `RelationType`, and what a project with no index does for the ranked
    path. This asserts that the damaged cell *is* interpreted: some published
    tool must refuse the same call it answered against the intact database.

    A refusal rather than merely a different answer, and that is the stronger
    claim on purpose. A store that read a corrupt canonical cell and answered
    anyway would be answering from state it could not interpret, which is the
    failure this product exists to prevent -- worse than refusing, not better.
    """
    column = Column(table, name)
    assert corrupt(corpus.database, column), f"{column} took no value"
    assert holds_sentinel(corpus.database, column)

    server = build_server(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    assert [tool for tool, answer in answers.items() if answer.refused], (
        f"no tool interpreted the {family} cell in {column}; every assertion "
        f"about this family is vacuous. Answers: {answers}"
    )


# -- Where the guard sits, on the writer -----------------------------------
#
# `SqliteWriter` reads six times and is guarded four times, and until now
# nothing anywhere held either half. Deleting any of the four guards left the
# suite green while the corrupted cell walked out of `theurian migrate status`
# -- so the placement was correct and unproven, which is the state a later edit
# removes without noticing. The remaining two reads are unguarded on purpose:
# `append_revision`'s `content_sha256`, whose absence
# `test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state`
# below holds, and `_refuse_pointer_to_another_items_revision`'s `item_id`
# lookup, recorded with its reason in `WRITER_READS_NOT_GUARDED`.
#
# Reached through the writer directly rather than through the CLI, and that is
# forced rather than convenient. `record_migration`, `get_item`,
# `list_revision_ids` and `append_revision` run only for a *pending* migration,
# and adding one to the corpus changes the migration set, which changes the state
# hash, which sends the next command to a different -- empty, undamaged --
# database file (ADR-0016). There is no CLI invocation that reaches them over a
# damaged state. `applied_migrations` is the exception and is swept through
# `migrate status` as well.

PROJECT_ID: Final = ProjectId("demo")
ITEM_ID: Final = ItemId("architecture.auth-policy")
REVISION_ID: Final = RevisionId("01K1AAAREV01234567890ABCDE")
APPLIED_AT: Final = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

#: Four of the six reads :class:`SqliteWriter` performs, each over a cell whose
#: converter quotes what it would not accept. Every one of these four sits behind
#: a guard today. The other two -- `append_revision`'s `SELECT item_id,
#: content_sha256` and `_refuse_pointer_to_another_items_revision`'s `SELECT
#: item_id` -- deliberately do not, and are recorded with their reasons in
#: :data:`WRITER_READS_NOT_GUARDED` rather than swept here.
WRITER_READS: Final = (
    (
        "get_item",
        "knowledge_items",
        "status",
        lambda writer: writer.get_item(PROJECT_ID, ITEM_ID),
    ),
    (
        # A retirement asks this for the revisions a still-published index must
        # stop holding (ADR-0024 decision 5), and it builds a `RevisionId` per
        # row -- so a corrupt `revision_id` reaches `theurian migrate apply`'s
        # transaction exactly as `get_item`'s corrupt `status` does.
        "list_revision_ids",
        "knowledge_revisions",
        "revision_id",
        lambda writer: writer.list_revision_ids(PROJECT_ID, ITEM_ID),
    ),
    (
        "applied_migrations",
        "migration_history",
        "migration_id",
        lambda writer: writer.applied_migrations(PROJECT_ID),
    ),
    (
        "record_migration",
        "migration_history",
        "sequence",
        lambda writer: writer.record_migration(
            PROJECT_ID, MigrationId(MIGRATION_ID), "c" * 64, APPLIED_AT
        ),
    ),
)

#: The other half of the partition: every read :class:`SqliteWriter` performs that
#: is deliberately *outside* a ``_reading()`` block, with the reason it interprets
#: nothing.
#:
#: Held against the shipped source together with :data:`WRITER_READS` by
#: :func:`test_every_read_the_writer_performs_is_guarded_or_excluded_with_a_reason`,
#: so a read added in a later milestone has to be classified rather than
#: forgotten. Keyed by ``(method, table)`` -- the table the ``SELECT`` names, not
#: the column, because one read may interpret several cells.
WRITER_READS_NOT_GUARDED: Final = {
    ("append_revision", "knowledge_revisions"): (
        "hands both cells it reads to `_refuse_unless_it_is_the_same_revision`, "
        "which compares `item_id` against `item_id` and `content_sha256` against "
        "`content_sha256` and interprets neither, so no converter can put a "
        "stored cell into a message. Each mismatch branch *does* interpret, and "
        "those two lines -- `ItemId(stored_item)` and `ContentHash(stored)` -- "
        "are guarded on their own. Guarding the read itself would answer a "
        "conflicting write with a remedy that deletes the state, which is what "
        "`test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state` "
        "holds."
    ),
    ("_refuse_pointer_to_another_items_revision", "knowledge_revisions"): (
        "fetches the `item_id` that owns the revision a `put_item` pointer names "
        "and compares it, opaque, against `item.item_id.value`; the `SELECT` "
        "interprets nothing, so no converter can put a stored cell into the "
        "mismatch message. The one branch that does interpret, "
        "`ItemId(stored_item)`, is guarded on its own line -- the same shape as "
        "`append_revision`'s two mismatch branches. Guarding the read itself "
        "would answer a cross-item pointer with the delete-the-state remedy "
        "`test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state` "
        "holds against."
    ),
}

#: The context manager that marks a read as guarded, by name.
#:
#: Matched lexically, so :func:`_writer_reads` reports a read as guarded only when
#: the read sits *inside* the block -- which is the distinction the whole
#: partition rests on. `append_revision` calls `_reading()` and is still an
#: unguarded read: its guard wraps the interpretation three lines further in, not
#: the ``SELECT``. A scan keyed on "this method mentions `_reading`" would call it
#: guarded and pass while the guard moved off the read entirely.
_GUARD: Final = "_reading"

#: What the scan counts as a read: the SQL verb that pulls bytes off the page, and
#: the cursor methods that carry rows away from one.
_READ_VERB: Final = "SELECT"
_FETCH_METHODS: Final = frozenset({"fetchone", "fetchall", "fetchmany"})
_TABLE_IN_SQL: Final = re.compile(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

#: Stands in for a table this scan could not read out of the SQL, so that such a
#: read joins the population as an unclassifiable member rather than being
#: dropped. A read the scanner cannot name must fail the partition, not vanish
#: from it -- silently skipping the reads it does not understand is the one way a
#: derived population degrades back into a list.
_UNRESOLVED: Final = "<table not derivable from the SQL literal>"


@dataclass(frozen=True)
class ReadSite:
    """One place :class:`SqliteWriter` pulls bytes out of the state database."""

    method: str
    table: str
    guarded: bool


def _select_table(node: ast.Call) -> str | None:
    """The table an ``.execute("SELECT ...")`` reads, or ``None`` if not a read."""
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
        return None
    if not (node.args and isinstance(node.args[0], ast.Constant)):
        return None

    sql = node.args[0].value
    if not isinstance(sql, str) or not sql.lstrip().upper().startswith(_READ_VERB):
        return None

    found = _TABLE_IN_SQL.search(sql)
    return found.group(1) if found else _UNRESOLVED


def _unattributed_fetch(node: ast.Call) -> str | None:
    """A ``fetch*`` whose rows came from a statement this scan could not read.

    A ``fetch*`` chained straight onto a ``SELECT`` literal is the same read
    :func:`_select_table` already counted, so it is not counted twice. One reached
    through a variable -- ``cursor = conn.execute(sql); cursor.fetchall()`` -- is a
    read whose SQL is not in the tree, and it enters the population unresolved.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr in _FETCH_METHODS):
        return None
    receiver = node.func.value
    if isinstance(receiver, ast.Call) and _select_table(receiver) is not None:
        return None
    return _UNRESOLVED


def _opens_guard(node: ast.With | ast.AsyncWith) -> bool:
    """Whether this ``with`` statement is the ``_reading()`` guard."""
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Name)
        and item.context_expr.func.id == _GUARD
        for item in node.items
    )


def _writer_reads() -> frozenset[ReadSite]:
    """Every read in :class:`SqliteWriter`, read out of the shipped source.

    Parsed from the source of the class *as imported*, so the tree scanned is the
    tree the suite runs against rather than a path assembled relative to this
    file, which can drift from the installed package.
    """
    found: set[ReadSite] = set()

    def visit(node: ast.AST, method: str | None, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, method or child.name, guarded)
                continue
            if isinstance(child, ast.With | ast.AsyncWith):
                visit(child, method, guarded or _opens_guard(child))
                continue
            if isinstance(child, ast.Call) and method is not None:
                table = _select_table(child) or _unattributed_fetch(child)
                if table is not None:
                    found.add(ReadSite(method, table, guarded))
            visit(child, method, guarded)

    visit(ast.parse(textwrap.dedent(inspect.getsource(SqliteWriter))), None, False)
    return frozenset(found)


def _write_lock(corpus: Corpus) -> Path:
    """The project's real write lock, so this writer serialises like any other."""
    return corpus.root / ".theurian/runtime/write.lock"


@pytest.mark.parametrize(("method", "table", "name", "call"), WRITER_READS)
def test_a_writers_read_of_a_damaged_cell_answers_without_quoting_it(
    corpus: Corpus,
    method: str,
    table: str,
    name: str,
    call: Callable[[SqliteWriter], object],
) -> None:
    """SEC-13 on the write path. The same property, three reads nothing held.

    A write transaction is not a private context: `theurian migrate status`
    opens one, and everything raised inside it reaches an operator through
    Typer's Rich traceback. Measured with `migration_history.migration_id`
    overwritten and the guard removed, `migrate status` printed
    ``InvalidIdentifierError: MigrationId must be a 26-character ... got
    'ROTATE-ME sk-live-...'`` -- the cell, verbatim, from a command that reports
    on migrations and has no business publishing a stored value at all.

    Asserted over what the caller receives rather than over an exception type,
    like everything else in this file: no fragment of the cell, and a remedy the
    caller can run. The second half is what separates this from "it raised
    something": an unguarded `ValueError` names no next action, and an agent
    that receives one re-issues the identical command.
    """
    column = Column(table, name)
    assert corrupt(corpus.database, column), f"{column} took no value"
    assert holds_sentinel(corpus.database, column)

    with (
        pytest.raises(Exception) as caught,
        write_transaction(corpus.database, _write_lock(corpus)) as connection,
    ):
        call(SqliteWriter(connection))

    published = str(caught.value)
    assert leaked_fragments(published) == (), (
        f"`SqliteWriter.{method}` published the corrupted {column}: {published}"
    )
    assert names_a_remedy(published, commands=_command_paths(), tools=frozenset()), (
        f"`SqliteWriter.{method}` refused {column} with no next action: {published}"
    )


def test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state(
    corpus: Corpus,
) -> None:
    """The inverse, and the reason the writer is guarded four times and not five.

    `append_revision` reads a stored `content_sha256` and is deliberately *not*
    guarded, because past ``BEGIN IMMEDIATE`` a failure is the caller's statement
    against the caller's data. Answering one of those with "delete
    `.theurian/state/` and run `theurian migrate apply`" would hand an operator a
    destructive remedy for a write that simply did not apply -- and, worse, would
    make a conflicting write indistinguishable from a damaged file.

    The absence of a guard is as much a decision as its presence and was as
    unheld: wrapping that one read left the whole suite green. The two arms here
    are the boundary itself. The same damage -- a schema whose
    `knowledge_revisions` no longer declares the column both sides ask for, which
    is what a rewritten `sqlite_master.sql` cell looks like -- is a damaged
    database on the read side and the caller's problem on the write side.

    Neither arm may publish the cell, which is why the sentinel is written into
    the schema text rather than into a row: a `sqlite3` complaint about a broken
    schema quotes names it read out of the file.
    """
    # Built before the damage and before either `pytest.raises`, so a domain
    # object this test failed to construct is an error here rather than a pass
    # in the write arm -- which is what the first draft of this test did.
    revision = _a_revision_the_store_already_holds(corpus)

    connection = sqlite3.connect(corpus.database, isolation_level=None)
    try:
        connection.execute("PRAGMA writable_schema = ON")
        changed = connection.execute(
            "UPDATE sqlite_master SET sql = replace(sql, 'content_sha256', ?) "
            "WHERE type = 'table' AND name = 'knowledge_revisions'",
            (SCHEMA_SENTINEL,),
        ).rowcount
    finally:
        connection.close()
    assert changed == 1, "the schema row must exist, or this damages nothing"

    commands = _command_paths()
    context = RequestContext(project_id=PROJECT_ID)

    with pytest.raises(Exception) as reading, SqliteCanonicalStore(corpus.database) as store:
        store.list_revisions(context, ITEM_ID)

    with (
        pytest.raises(Exception) as writing,
        write_transaction(corpus.database, _write_lock(corpus)) as connection,
    ):
        SqliteWriter(connection).append_revision(revision)

    assert names_a_remedy(str(reading.value), commands=commands, tools=frozenset()), (
        f"a read over a damaged schema named no remedy: {reading.value}"
    )
    assert not names_a_remedy(str(writing.value), commands=commands, tools=frozenset()), (
        f"a write inside an open transaction was answered with a remedy that deletes "
        f"the state: {writing.value}"
    )
    assert (leaked_fragments(str(reading.value)), leaked_fragments(str(writing.value))) == (
        (),
        (),
    ), f"the damaged schema reached a caller: read={reading.value} write={writing.value}"


def _a_revision_the_store_already_holds(corpus: Corpus) -> KnowledgeRevision:
    """The corpus's own approved revision, rebuilt with different content.

    Read through a plain `sqlite3` connection rather than through the store,
    because the store is what the surrounding test is measuring. Read from the
    *pristine* copy, so the caller may damage the live database first.
    """
    connection = sqlite3.connect(corpus.pristine)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM knowledge_revisions WHERE revision_id = ?",
            (REVISION_ID.value,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None, "the corpus must hold this revision, or nothing is appended"

    return KnowledgeRevision.create(
        revision_id=REVISION_ID,
        item_id=ITEM_ID,
        project_id=PROJECT_ID,
        migration_id=MigrationId(MIGRATION_ID),
        title=row["title"],
        # Different content under an existing id, so an intact schema answers
        # this with the immutability invariant rather than with silence.
        body="Rewritten by a migration nobody approved.\n",
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind(row["kind"]),
            namespace=row["namespace"],
            status=KnowledgeStatus(row["status"]),
            trust_level=TrustLevel(row["trust_level"]),
            sensitivity=Sensitivity(row["sensitivity"]),
            owner=row["owner"],
        ),
        validity=ValidityPeriod(valid_from=datetime.fromisoformat(row["valid_from"])),
        author=row["author"],
        created_at=datetime.fromisoformat(row["created_at"]),
        # INV-4: a revision Theurian did not author needs somewhere it came
        # from, and `KnowledgeRevision.create` refuses one without it.
        source_anchors=(SourceAnchor(provider="git", source_uri="git://demo/rewritten.md"),),
    )


def test_the_scan_looks_for_a_guard_the_store_actually_defines() -> None:
    """Guards the partition below, which matches a name and cannot resolve a type.

    If ``_reading`` is renamed, every read in the writer reads as *unguarded*, and
    the partition fails saying that three guarded reads disappeared and three
    unclassified ones arrived. That is a rename reported as a wholesale loss of
    protection, which is the kind of failure whose expectation gets updated
    instead of read. This says which it was.
    """
    assert hasattr(sqlite_store, _GUARD), (
        f"`{_GUARD}` no longer exists in the store module, so the scan below is "
        f"looking for a guard the product has stopped using and will report every "
        f"read as unguarded. Rename _GUARD with it"
    )


def test_every_read_the_writer_performs_is_guarded_or_excluded_with_a_reason() -> None:
    """The writer's reads are a partition of the source, not a list someone kept.

    :data:`WRITER_READS` was a `parametrize` list and nothing else: a fifth read
    added to :class:`SqliteWriter` without a guard failed no test in this suite.
    The same shape twice cost this file real accuracy -- two comments here said
    the writer reads three times while a test in this same file said "guarded
    three times *and not four*", and the prose was wrong for as long as nothing
    checked it against the source.

    **The key is reads, not interpretations, and that is the decision.**
    ``_reading()`` exists to catch interpretations -- its own key is *does this
    line interpret bytes that came out of this file?* -- and that question is not
    decidable from a syntax tree. ``int(row["s"])`` interprets; ``stored !=
    revision.content_sha256.value`` does not; both are expressions over a fetched
    row. What the tree *does* decide is where bytes enter the writer: a ``SELECT``
    handed to ``execute``, and any ``fetch*`` carrying rows away from one.

    Reads are the safe side of that difference, because every interpretation is a
    read. Quantifying over reads over-approximates the population the guard cares
    about and therefore cannot miss one; quantifying over interpretations would
    require the scan to infer which lines convert, and a scanner that infers that
    wrongly says nothing and passes. So the semantic judgement is not made here at
    all -- *this read interprets nothing* is written down per read, with its
    reason, in :data:`WRITER_READS_NOT_GUARDED`.

    That is what a future reader needs from a red run: the two repairs are
    opposite. A new read that interprets a stored cell belongs in
    :data:`WRITER_READS`, behind a guard, where the sweep above corrupts its table
    and proves it refuses without quoting the cell. A new read that only moves
    opaque bytes belongs in :data:`WRITER_READS_NOT_GUARDED` with the argument for
    why -- and guarding it anyway is not the safe default, because past
    ``BEGIN IMMEDIATE`` a guard offers to delete the operator's state database in
    answer to a write that merely conflicted.
    """
    reads = _writer_reads()
    guarded = frozenset((site.method, site.table) for site in reads if site.guarded)
    unguarded = frozenset((site.method, site.table) for site in reads if not site.guarded)
    swept = frozenset((method, table) for method, table, _name, _call in WRITER_READS)
    excluded = frozenset(WRITER_READS_NOT_GUARDED)

    assert reads, (
        "no reads found in `SqliteWriter` at all -- the scan is looking at the "
        "wrong tree, or `SELECT` stopped being written as a literal, and every "
        "assertion below is vacuous"
    )
    assert guarded == swept, (
        f"guarded reads in the source and the swept set disagree.\n"
        f"  guarded in source but not swept by WRITER_READS: {sorted(guarded - swept)}\n"
        f"  swept by WRITER_READS but not guarded in source: {sorted(swept - guarded)}\n"
        f"A guarded read that WRITER_READS does not carry is never corrupted by "
        f"the sweep above, so nothing shows that it refuses without quoting the "
        f"cell. A swept entry the source no longer guards is a guard someone "
        f"removed."
    )
    assert unguarded == excluded, (
        f"unguarded reads in the source and the recorded exclusions disagree.\n"
        f"  unguarded in source, unclassified: {sorted(unguarded - excluded)}\n"
        f"  recorded as excluded but no longer present: {sorted(excluded - unguarded)}\n"
        f"Every read in `SqliteWriter` is either guarded and swept -- add it to "
        f"WRITER_READS with the column whose converter quotes what it would not "
        f"accept -- or unguarded on purpose, in which case record in "
        f"WRITER_READS_NOT_GUARDED why it interprets nothing. `{_UNRESOLVED}` "
        f"means the scan could not read the table out of the SQL, not that the "
        f"read is exempt."
    )
    assert all(WRITER_READS_NOT_GUARDED.values()), (
        "an exclusion without a reason is a read someone forgot to guard"
    )


# -- What the report says, and what it does not ----------------------------


def test_a_damaged_database_report_names_the_converter_that_failed(corpus: Corpus) -> None:
    """The detail is the failing exception's type, and nothing else can be.

    It is the whole of what this report carries beyond a fixed sentence, and the
    only thing that tells an operator holding two of them apart: a
    `ValueError` from a timestamp and an `InvalidIdentifierError` from a revision
    pointer are different repairs. Replacing it with an empty string leaves a
    grammatical, remedy-naming, entirely uninformative message -- and left the
    whole suite green.

    Two columns rather than one, because "the message contains the cause's type
    name" is satisfied by an implementation that hard-codes any single name.
    What is asserted is that the two reports *differ*, and that each names its
    own cause.
    """
    context = RequestContext(project_id=PROJECT_ID)
    reports: dict[str, str] = {}

    # An enum, which raises `ValueError`, and a domain value object, which raises
    # `InvalidIdentifierError` -- the two families whose failure to share a base
    # class is why the guard is written over the boundary and not the hierarchy.
    for column in (
        Column("knowledge_items", "status"),
        Column("knowledge_items", "current_revision_id"),
    ):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            with (
                pytest.raises(StateDatabaseUnreadableError) as caught,
                SqliteCanonicalStore(corpus.database) as store,
            ):
                store.get_item(context, ITEM_ID)
        finally:
            restore(corpus)

        cause = caught.value.__cause__
        assert cause is not None, f"{column}: the real exception did not travel on __cause__"
        assert type(cause).__name__ in str(caught.value), (
            f"{column}: the report does not say what failed. cause={type(cause).__name__}, "
            f"report={caught.value}"
        )
        reports[str(column)] = str(caught.value)

    _first, _second = reports.values()
    assert _first != _second, (
        f"two different converter failures produced the same report, so the detail "
        f"distinguishes nothing: {reports}"
    )


def test_a_nested_read_reports_the_converter_that_failed_not_the_wrapper(
    corpus: Corpus,
) -> None:
    """Reads nest, and a nested read must not answer with the answer's own name.

    `get_revision` maps its row by calling `_anchors_for`, which is a guarded
    read inside a guarded read. Without `StateDatabaseUnreadableError` in the
    already-answered set the outer guard wraps the inner one, and the detail --
    the only part of the report that carries information -- becomes the string
    ``StateDatabaseUnreadableError``, which says that a state database was
    unreadable to someone reading a message that already says so.

    `source_anchors.line_start` is the cell: `SourceAnchor` takes it as an int
    and the corrupted text reaches its comparison, so the failure happens in the
    *inner* read and the outer guard is what decides how it is reported.
    """
    column = Column("source_anchors", "line_start")
    assert corrupt(corpus.database, column), f"{column} took no value"
    assert holds_sentinel(corpus.database, column)
    context = RequestContext(project_id=PROJECT_ID)

    with (
        pytest.raises(StateDatabaseUnreadableError) as caught,
        SqliteCanonicalStore(corpus.database) as store,
    ):
        store.get_revision(context, REVISION_ID)

    chain: list[BaseException] = []
    current: BaseException | None = caught.value
    while current is not None:
        chain.append(current)
        current = current.__cause__

    wraps = [item for item in chain if isinstance(item, StateDatabaseUnreadableError)]
    assert len(wraps) == 1, (
        f"the nested read was answered {len(wraps)} times over: "
        f"{[type(item).__name__ for item in chain]}"
    )
    assert type(chain[-1]).__name__ in str(caught.value), (
        f"the report names the wrapper rather than the converter that failed: "
        f"chain={[type(item).__name__ for item in chain]}, report={caught.value}"
    )


def test_an_unsupported_schema_version_is_reported_as_a_version_not_as_damage(
    corpus: Corpus,
) -> None:
    """A header this build read successfully is not a damaged file.

    `schema_version` is the one cell whose *failure to be interpreted* and whose
    *successful interpretation* both stop a read, and they need different
    answers. A number this build does not support was read correctly: the file
    is intact, the build is the wrong one, and the caller needs the two version
    numbers to know that. Wrapping it discards both and asserts damage that is
    not there -- and the sweep cannot catch it, because the sweep writes text
    into that column and text is the *other* case.

    ADR-0017: state databases are rebuilt rather than migrated, so no
    compatibility window makes this unreachable.
    """
    unsupported = SCHEMA_VERSION + 1000
    connection = sqlite3.connect(corpus.database)
    try:
        changed = connection.execute(
            "UPDATE schema_metadata SET schema_version = ? WHERE id = 1", (unsupported,)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "schema_metadata must hold its single row, or this changes nothing"

    with pytest.raises(SchemaVersionMismatchError) as caught, SqliteCanonicalStore(corpus.database):
        pass

    assert (caught.value.found, caught.value.expected) == (unsupported, SCHEMA_VERSION), (
        f"the mismatch does not say which two versions disagree: {caught.value!r}"
    )


#: Every schema version that predates the `project_integrity` table (#30 PR2),
#: fixed by history rather than derived from `SCHEMA_VERSION`.
#:
#: `range(1, SCHEMA_VERSION)` used to equal this set, but only by coincidence:
#: it held while `SCHEMA_VERSION` was 3, the version `project_integrity` itself
#: shipped in. #117 is the case that broke the coincidence -- it bumps
#: `SCHEMA_VERSION` to 4 for a reason that has nothing to do with
#: `project_integrity` (dropping a `CHECK` on `valid_from`/`valid_to`), and
#: `range(1, 4)` now includes 3, a version that has held `project_integrity`
#: since it shipped. Sweeping it into this population would assert something
#: false about it: 3 *is* refused (`is_supported` is exact-match, ADR-0017),
#: but not because it predates the integrity table.
PRE_INTEGRITY_SCHEMA_VERSIONS: Final = (1, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("stamped_version", PRE_INTEGRITY_SCHEMA_VERSIONS)
async def test_a_pre_integrity_database_is_refused_unread_by_every_tool(
    corpus: Corpus, stamped_version: int
) -> None:
    """#30 PR2. The premise the "no record is damage" inference rests on.

    Every version in :data:`PRE_INTEGRITY_SCHEMA_VERSIONS` held no
    `project_integrity` table -- neither version 1 nor the version 2 that
    `0.1.0.dev3` shipped. The detector reads a missing record as *damage* rather
    than as "not recorded", and that is only sound while no such file can be
    opened at all -- otherwise "no record" means either "this state lost one" or
    "this state predates the table", and the detector cannot tell which.
    `is_supported` is exact-match for that reason (ADR-0017: state databases are
    rebuilt, never migrated), so the ambiguity is unreachable rather than merely
    unlikely.

    Parametrised over the *whole* fixed set rather than over the first alone,
    because the inference needs the whole range closed and a compatibility
    window would most plausibly be opened for the version immediately behind --
    the one a released build actually wrote.

    So this asserts the premise on the surface where it would be violated: not
    one tool but all three, because `_resolve` is shared and a compatibility
    window opened for one would open it for the others. Driven by stamping
    `schema_metadata` rather than by building with an old build, so the file this
    refuses is otherwise a perfectly readable current database -- which is what
    makes the refusal attributable to the version and to nothing else. Confirmed
    against a database `0.1.0.dev3` really wrote, read by the build that shipped
    `SCHEMA_VERSION` 3, before #117 bumped it to 4: all three tools refuse it
    with "… was written at schema version 2, but this build uses 3".

    Two assertions, and they fail separately. Every tool must refuse -- RED the
    moment `is_supported` accepts anything in this set -- and every refusal must
    name a remedy the caller can run.

    There is deliberately no third assertion that the refusals carry no
    `integrity` key. A refused tool publishes no field at all, so "an old file is
    not reported as damaged" is already contained in "an old file is refused":
    asserting it separately reads a payload that, given the first assertion holds,
    cannot exist -- a check that can never fail. Were `is_supported` to accept an
    old version, the tool would answer and the first assertion is what catches it;
    a separate `integrity` check would only report whether that (already failing)
    answer also carried the key, so dropping it loses nothing. The population this
    parametrization sweeps is pinned by
    :func:`test_the_pre_integrity_schema_versions_are_exactly_one_and_two`, so an
    edit that shrinks or widens :data:`PRE_INTEGRITY_SCHEMA_VERSIONS` without cause
    fails there rather than silently changing this sweep.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        changed = connection.execute(
            "UPDATE schema_metadata SET schema_version = ? WHERE id = 1", (stamped_version,)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "schema_metadata must hold its single row, or nothing was stamped"

    server = build_server(corpus.registry)
    commands, tools = _command_paths(), _tool_names(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    assert [tool for tool, answer in answers.items() if not answer.refused] == [], (
        f"a tool answered from a database written at a schema version this build does not "
        f"support, so 'no integrity record' can no longer mean damage: {answers}"
    )
    assert [
        tool
        for tool, answer in answers.items()
        if not names_a_remedy(answer.text, commands=commands, tools=tools)
    ] == [], f"a version refusal named nothing the caller can run: {answers}"


def test_the_pre_integrity_schema_versions_are_exactly_one_and_two() -> None:
    """#30 PR2. The population the refusal sweep above is parametrized over.

    :data:`PRE_INTEGRITY_SCHEMA_VERSIONS` is a fixed historical fact -- exactly
    versions 1 and 2 precede the `project_integrity` table -- and this pins it to
    the literal tuple rather than trusting the constant's own definition to stay
    correct. It was derived from `range(1, SCHEMA_VERSION)` until #117: that
    coupling failed in *both* directions the underlying pin already worried
    about, and one it had not yet met. A `SCHEMA_VERSION` fall still silently
    drops a case (`3 -> 2` loses the `0.1.0.dev3` version from the sweep); #117's
    rise to 4 for a reason unrelated to `project_integrity` is the new one --
    `range(1, 4)` gained 3, a version that has held the table since it shipped,
    and swept it into a population whose whole premise is "held no such table".
    A reversion of either kind, or an edit to the constant itself, is RED here.
    """
    assert PRE_INTEGRITY_SCHEMA_VERSIONS == (1, 2), (
        f"PRE_INTEGRITY_SCHEMA_VERSIONS is now {PRE_INTEGRITY_SCHEMA_VERSIONS}, so the refusal "
        f"sweep no longer covers what it claims to -- exactly the versions that predate "
        f"`project_integrity` (#30 PR2)"
    )


# -- Answering successfully with less than the file holds -------------------


def _published_integers(text: str) -> dict[str, int]:
    """Every integer a payload publishes, keyed by its path through the JSON.

    Derived from the payload rather than from a list of field names, so a count
    added to a response in a later milestone is compared without anyone
    remembering to add it here. Booleans are excluded: `True` is an `int` in
    Python and ``stale: false -> true`` is not a shrinking count.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}

    found: dict[str, int] = {}

    def walk(node: object, path: str) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found[path] = node
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            found[f"{path}[]"] = len(node)
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, "")
    return found


@dataclass(frozen=True, slots=True)
class DamagedCellAnswer:
    """One tool's answer over one damaged cell, classified into four outcomes.

    A position falls in exactly one of them: it refused, it disclosed the damage
    through the present-only ``integrity`` object (#30), it answered successfully
    with a smaller integer and said nothing, or it answered clean.

    ``integrity`` and ``shrunk`` are recorded separately rather than folded
    together because the interesting position is the one that has ``shrunk``
    without ``integrity`` -- the caller is handed a false number as a fact. That
    is :data:`UNDETECTED_UNDERREPORT`; the two together are
    :data:`DISCLOSED_BESIDE_A_SHRUNKEN_COUNT`.
    """

    refused: bool
    integrity: bool
    shrunk: dict[str, str]

    @property
    def clean(self) -> bool:
        return not self.refused and not self.integrity and not self.shrunk

    @property
    def silently_underreports(self) -> bool:
        return not self.refused and not self.integrity and bool(self.shrunk)


def _integrity_reported(text: str) -> bool:
    """Whether a successful payload carries the present-only ``integrity`` key."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and "integrity" in payload


@pytest.fixture(scope="module")
def damaged_cell_answers(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[tuple[str, str, str], DamagedCellAnswer]:
    """Every tool's answer over every damaged cell in the schema, classified, once.

    Its own corpus, for the reason :func:`cli_observations` builds one: the sweep
    restores the database between columns, so the *result* is safe to share while
    the corpus is not.

    **Over the whole schema and not over `migration_history` alone.** It was
    scoped to that table while the #30 PR1 detector read nothing else. PR2's
    second comparison reads `knowledge_items` and `project_integrity`, and the
    positions that matter most to :data:`UNDETECTED_UNDERREPORT` are in
    `knowledge_items` -- a narrower sweep would have stated the silent class over
    a population that cannot contain it.

    The intact answers are captured before any corruption, so a shrinking count
    is measured against the same corpus one moment earlier and cannot be a
    property of the corpus or of the clock.
    """
    with pytest.MonkeyPatch.context() as patch:
        corpus = _build_corpus(tmp_path_factory.mktemp("damaged-cell-sweep"), patch)
        server = build_server(corpus.registry)
        intact = {
            tool: _published_integers(asyncio.run(call_tool(server, tool, args)).text)
            for tool, args in TOOL_CALLS
        }
        assert all(intact.values()), f"a tool published no integer to compare against: {intact}"

        observed: dict[tuple[str, str, str], DamagedCellAnswer] = {}
        columns = corruptible_columns(corpus.database)
        assert columns, "the corpus holds no row to damage"
        for column in columns:
            assert corrupt(corpus.database, column), f"{column} took no value"
            try:
                for tool, args in TOOL_CALLS:
                    answer = asyncio.run(call_tool(server, tool, args))
                    published = _published_integers(answer.text)
                    observed[tool, column.table, column.name] = DamagedCellAnswer(
                        refused=answer.refused,
                        integrity=_integrity_reported(answer.text),
                        shrunk={
                            field: f"{before} -> {published[field]}"
                            for field, before in intact[tool].items()
                            if field in published and published[field] < before
                        },
                    )
            finally:
                restore(corpus)
        return observed


def test_exactly_one_position_answers_with_less_than_the_file_holds_and_says_nothing(
    damaged_cell_answers: dict[tuple[str, str, str], DamagedCellAnswer],
) -> None:
    """#30's recorded residual, stated as an exact set. The reach cannot grow silently.

    The face no property framed around refusals can see. Both refusal sweeps in
    this file read ``answer.refused`` before they assert anything, so a tool that
    answers *successfully* and wrongly is structurally invisible to them -- and
    that is the worse outcome, not the milder one. `knowledge.search` replying
    ``{"count": 0, "results": [], "retrieval": {"stale": false}}`` over a damaged
    `knowledge_items.item_id` tells an agent that the index is fresh and this
    project holds no answer, which is a false statement it will act on; a refusal
    at least stops it.

    Only shrinking is read: a corrupted `title` changes what `knowledge.get`
    returns and that is the caller's own content answered correctly, which is why
    this is not "the answer changed".

    Two equalities, because the pair of sets has to have no seam. The first is
    the residual itself. The second states the *whole* shrinking class as
    ``DISCLOSED_BESIDE_A_SHRUNKEN_COUNT | UNDETECTED_UNDERREPORT``, so a position
    that is already disclosing and starts shrinking a count as well -- which
    moves neither set on its own -- fails here.
    """
    silent = {
        position
        for position, answer in damaged_cell_answers.items()
        if answer.silently_underreports
    }
    shrinking = {
        position
        for position, answer in damaged_cell_answers.items()
        if not answer.refused and answer.shrunk
    }

    assert silent == UNDETECTED_UNDERREPORT, (
        f"the set of positions where a tool answers successfully with less than it holds and "
        f"discloses nothing has moved. Newly silent: "
        f"{ {p: damaged_cell_answers[p].shrunk for p in silent - UNDETECTED_UNDERREPORT} }; "
        f"no longer silent: {sorted(UNDETECTED_UNDERREPORT - silent)} -- if one of those "
        f"started disclosing, move it into DISCLOSED_BESIDE_A_SHRUNKEN_COUNT rather than "
        f"deleting it"
    )
    assert shrinking == DISCLOSED_BESIDE_A_SHRUNKEN_COUNT | UNDETECTED_UNDERREPORT, (
        f"the set of positions that answer with a smaller integer has moved, in a way neither "
        f"exact set above catches on its own. Newly shrinking: "
        f"{sorted(shrinking - DISCLOSED_BESIDE_A_SHRUNKEN_COUNT - UNDETECTED_UNDERREPORT)}; "
        f"no longer shrinking: "
        f"{sorted((DISCLOSED_BESIDE_A_SHRUNKEN_COUNT | UNDETECTED_UNDERREPORT) - shrinking)}"
    )


def test_exactly_these_positions_disclose_damage_as_integrity(
    damaged_cell_answers: dict[tuple[str, str, str], DamagedCellAnswer],
) -> None:
    """#30. The detector fires, on exactly these positions, over the whole schema.

    The guard for the clean-answer set below, which a build with the detector
    unplugged would satisfy with a *larger* clean set that nobody reads as a
    failure -- every position would be "clean", and only this test says which of
    them must not be. It is equally the
    guard for the set above: a detector that stopped firing on
    `knowledge_items.project_id` would move that position into the silent class
    and fail there, and a detector that stopped firing on a position that shrinks
    nothing would fail only here.

    Both comparisons are represented, so a mutation that drops either one is RED:
    the migration-row count against the pointer (PR1) is the
    `migration_history.project_id` trio, and the surfaceable-item count against
    what `migrate apply` recorded (PR2) is the `project_integrity` and
    `knowledge_items` members. See :data:`DISCLOSED_AS_INTEGRITY` for what
    reaches the detector at each one.
    """
    disclosed = {position for position, answer in damaged_cell_answers.items() if answer.integrity}
    disclosed_and_shrunken = {
        position
        for position, answer in damaged_cell_answers.items()
        if answer.integrity and answer.shrunk
    }

    assert disclosed == DISCLOSED_AS_INTEGRITY, (
        f"the set of positions disclosed through `integrity` has moved. Newly disclosing: "
        f"{sorted(disclosed - DISCLOSED_AS_INTEGRITY)}; no longer disclosing: "
        f"{sorted(DISCLOSED_AS_INTEGRITY - disclosed)}"
    )
    assert disclosed_and_shrunken == DISCLOSED_BESIDE_A_SHRUNKEN_COUNT, (
        f"which disclosed positions also publish a smaller integer has moved. Newly shrinking: "
        f"{sorted(disclosed_and_shrunken - DISCLOSED_BESIDE_A_SHRUNKEN_COUNT)}; no longer "
        f"shrinking: {sorted(DISCLOSED_BESIDE_A_SHRUNKEN_COUNT - disclosed_and_shrunken)}"
    )


# -- Answering cleanly over a cell the tool stopped reading -----------------


def test_exactly_these_positions_answer_cleanly_over_a_cell_the_cli_calls_tampering(
    damaged_cell_answers: dict[tuple[str, str, str], DamagedCellAnswer],
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """#30 PR1. The cells the read tools stopped interpreting, stated exactly.

    A tool that answers cleanly over a damaged cell is not automatically wrong --
    it is wrong when nothing else notices. So the population here is not "cells a
    tool ignores" but "cells a tool ignores *and the CLI refuses*": the two
    surfaces are read together, and the set is what remains.

    Restricted to `migration_history` on both sides, which is where the trade was
    made: `knowledge.status` used to reach that table through
    ``applied_migrations`` and refuse on a damaged `migration_id` or `checksum`,
    and PR1's bare ``COUNT(*)`` interprets neither. Every other table the sweep
    now covers is a different question.

    Both halves are measured rather than assumed. The CLI half comes from the
    same sweep :func:`test_exactly_these_commands_notice_a_single_damaged_cell`
    reads, so a `migrate status` that stopped exiting 4 on a tampered checksum
    empties the population and this fails -- which is the outcome that matters,
    because the read tools' silence is only acceptable while that exit exists.

    An exact set, so it fails in both directions. A read tool that starts
    refusing again -- the shape that returns if the integrity `COUNT` is put back
    on a parsed row -- drops its position and fails here; a cell the CLI calls
    tampering that a tool used to notice and no longer does joins the set and
    fails here too.
    """
    tampering = {
        (table, column)
        for (_command, table, column), (code, _text) in cli_observations.items()
        if code != 0 and table == "migration_history"
    }
    clean_over_tampering = {
        position
        for position, answer in damaged_cell_answers.items()
        if answer.clean and (position[1], position[2]) in tampering
    }

    assert clean_over_tampering == ANSWERED_CLEAN_OVER_A_DAMAGED_CELL, (
        f"the set of positions answering cleanly over a cell the CLI calls tampering has moved. "
        f"Newly clean: {sorted(clean_over_tampering - ANSWERED_CLEAN_OVER_A_DAMAGED_CELL)}; "
        f"no longer clean: {sorted(ANSWERED_CLEAN_OVER_A_DAMAGED_CELL - clean_over_tampering)}. "
        f"The cells `migrate status` and `migrate apply` refuse are {sorted(tampering)}"
    )


# -- Refusing the whole response over a cell the detector interprets ----------


def test_the_integrity_record_cell_refuses_the_whole_response_on_every_tool(
    damaged_cell_answers: dict[tuple[str, str, str], DamagedCellAnswer],
) -> None:
    """#30 PR2. A non-numeric `expected_surfaceable_count` refuses, on all three tools.

    This is the first post-check cell the detector *interprets*: `_measure_integrity`
    reads `project_integrity.expected_surfaceable_count` on every request, and
    `int()` over a cell that is not a number refuses through `_reading` -- because
    reading it as 0 would fabricate a damage report, or hide one, depending on what
    the live count happens to be. So every tool refuses the whole response, and the
    refusal names a remedy (held by
    :func:`test_every_refusal_over_a_damaged_database_names_a_remedy`); this pins
    *which* positions refuse, which nothing else did.

    An exact set, so it fails in both directions: a tool that stopped refusing --
    the tolerant read that turns the refusal into a fabricated or hidden signal --
    drops its position and fails here, and a cell that started refusing all three
    joins it.
    """
    refused = {
        position
        for position, answer in damaged_cell_answers.items()
        if answer.refused
        and (position[1], position[2]) == ("project_integrity", "expected_surfaceable_count")
    }

    assert refused == REFUSES_THE_WHOLE_RESPONSE, (
        "which tools refuse over a non-numeric integrity-record cell has moved. "
        f"Newly refusing: {sorted(refused - REFUSES_THE_WHOLE_RESPONSE)}; "
        f"no longer refusing: {sorted(REFUSES_THE_WHOLE_RESPONSE - refused)} -- a tool that "
        "stopped refusing reads the cell as a count and fabricates or hides a damage report"
    )


def test_a_non_iso_valid_to_refuses_rather_than_being_read_as_open_ended(
    damaged_cell_answers: dict[tuple[str, str, str], DamagedCellAnswer],
) -> None:
    """#18, SEC-13. A corrupt optional timestamp refuses, it is not swallowed.

    `valid_to` is optional, so `_opt_dt` reads it rather than `_dt`. A tolerant
    `_opt_dt` -- one that caught `datetime.fromisoformat`'s `ValueError` and read a
    corrupt window as open-ended -- would answer over a cell it could not interpret,
    the failure this whole file exists to prevent, and *no other property here would
    fail*: the disclosure and remedy sweeps read only over refusals, the clean-answer
    set is `migration_history` only, and `CONVERTER_FAMILIES` reaches
    `datetime.fromisoformat` through `knowledge_revisions.created_at`, a `_dt` read
    the mutation leaves refusing. So this pins the `_opt_dt` refusals directly.

    An exact set: the tolerant slide drops every position to a clean serve and fails
    here, and a `valid_to` read that stopped refusing on any surface fails here too.
    """
    refused = {
        position
        for position, answer in damaged_cell_answers.items()
        if answer.refused and position[2] == "valid_to"
    }

    assert refused == REFUSED_OVER_A_NON_ISO_VALID_TO, (
        "which positions refuse a non-ISO `valid_to` has moved. "
        f"Newly refusing: {sorted(refused - REFUSED_OVER_A_NON_ISO_VALID_TO)}; "
        f"no longer refusing: {sorted(REFUSED_OVER_A_NON_ISO_VALID_TO - refused)} -- a position "
        "that stopped refusing is reading a corrupt validity window as open-ended (#18)"
    )
