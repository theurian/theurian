"""The MCP tool surface, called in process (ADR-0013, SEC-13, SEC-15).

The e2e suite proves these tools work over the wire against a real daemon. It
cannot cheaply enumerate their branches, and it runs the daemon in a subprocess
where nothing is measured. These tests go through ``server.call_tool`` -- the
same entry point the transport uses -- against a project built by the real CLI.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian import __protocol_version__, __version__
from theurian.application.project_service import (
    ACTIVE_POINTER_REMEDY,
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
    read_active_state,
)
from theurian.application.retrieval_service import CANDIDATE_DEPTH, FIRST_PASS_DEPTH
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus, may_surface
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.migration import MIGRATION_ENGINE_VERSION
from theurian.domain.ranking import Ranked
from theurian.infrastructure.sqlite import store as store_module
from theurian.infrastructure.sqlite.connection import (
    create_database,
    open_read_connection,
    write_transaction,
)
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter
from theurian.mcp.tools import MAX_PROJECT_ID_CHARS, MAX_RESULTS

pytestmark = pytest.mark.integration

runner = CliRunner()

#: tests/integration/test_mcp_tools.py -> integration -> tests -> theurian-core
#: -> packages -> repo root, matching test_wire_contract.py's own computation.
REPO_ROOT = Path(__file__).resolve().parents[4]


class _NothingWithheld:
    """A `Visibility` that withholds nothing.

    The tests below that use it are asserting a *precondition*: that the index
    still holds the retracted text and the retrievers still match it. That is
    what makes the response-level assertions mean something -- an answer that
    omits a secret because no retriever could find it proves nothing about the
    gate. Ranked through the real canonical store, these queries reach nothing,
    which is the fix; ranked through this, they reach the document, which is the
    threat the fix answers.
    """

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(ranked)

    def at_moment(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """No `asOf` in this file's use of it: nothing is pinned either."""
        return tuple(ranked)


NOTHING_WITHHELD = _NothingWithheld()

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
DRAFT_ID = "01K1BBBBBB01234567890ABCDE"
DRAFT_REVISION_ID = "01K1BBBREV01234567890ABCDE"

BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
DRAFT_BODY = "# Caching draft\n\nA proposal nobody has reviewed.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
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
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

DRAFT_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {DRAFT_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.caching-draft
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.caching-draft
    revisionId: {DRAFT_REVISION_ID}
    contentFile: ../knowledge/architecture/caching-draft.md
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
"""

#: A second and third approved item, so a budget has something to drop.
EXTRA_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1{letter}AAAAA01234567890ABCDE
createdAt: 2026-08-02T12:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: 01K1{letter}AAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/{slug}.md
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


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered project with one approved item and one draft."""
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
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(BODY)
    (knowledge / "caching-draft.md").write_text(DRAFT_BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    (root / f".theurian/migrations/{DRAFT_ID}-draft.yaml").write_text(DRAFT_MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _apply_leaving_the_stale_build_published(root: Path) -> None:
    """Apply a written withdrawal migration with the index pointer withheld.

    ADR-0024 decision 5 makes ``theurian migrate apply`` purge the withdrawn
    revisions from whatever build the pointer names, the instant the withdrawal
    commits -- which is the close this milestone lands, proved end to end in
    ``test_absence_proof.py``. These fixtures are for the *residual* that close
    leaves: a request already reading the pre-swap build, or a purge that raised,
    where the published build still holds the withheld rows and the canonical gate
    is the only thing between them and a caller (ADR-0024 decision 5's own words).

    Withholding the pointer across the apply reproduces that residual
    deterministically. With no published build, the purge takes its
    ``no-published-index`` path and removes nothing; the pre-withdrawal build --
    which does hold the withheld revision -- is restored afterward, stale against
    the state the withdrawal advanced to. Every test on these fixtures then reads
    exactly the state it was written for: the gate withholding an offered row.
    """
    pointer = ProjectPaths.of(root).active_index_pointer
    withheld_pointer = pointer.read_text(encoding="utf-8")
    pointer.unlink()
    _run("migrate", "apply")
    pointer.write_text(withheld_pointer, encoding="utf-8")


async def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    """Invoke a tool and return its payload, or ``{"_error": ...}``.

    ``call_tool`` re-raises a failing tool as the SDK's own ``ToolError``; the
    transport is the layer that turns it into ``isError=True`` content. Both
    carry the same message, and the e2e suite checks the wire form. What matters
    here is the *text*, so a message that leaked a path or a stack trace would
    fail a test rather than reach a client.
    """
    result = await build_server(registry).call_tool(tool, arguments)
    content: Any = result.content  # type: ignore[union-attr]
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


async def _call_failing(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    """Invoke a tool that must fail, and return the message a client would see."""
    with pytest.raises(SdkToolError) as raised:
        await _call(registry, tool, **arguments)
    return str(raised.value)


# -- Project resolution ----------------------------------------------------


@pytest.mark.asyncio
async def test_an_unregistered_project_names_what_is_registered(
    registry: ProjectRegistry,
) -> None:
    """SEC-13. The error must not become a probe for other projects' contents,
    but a caller that mistyped an id needs to see the right one."""
    message = await _call_failing(registry, "knowledge.search", projectId="typo", query="token")

    assert "not registered" in message
    assert "demo" in message, "the registered id must be shown"
    assert "project register" in message


@pytest.mark.asyncio
async def test_an_unbuilt_project_says_how_to_build_it(
    registry: ProjectRegistry, tmp_path: Path
) -> None:
    """Registered but never applied is different from "no results", and the
    remedy is a command, not a support request."""
    unbuilt = tmp_path / "unbuilt"
    unbuilt.mkdir()
    (unbuilt / ".theurian/state").mkdir(parents=True)
    _register_extra(registry, "other", unbuilt)

    message = await _call_failing(registry, "knowledge.status", projectId="other")

    assert "no built knowledge state" in message
    assert "migrate apply" in message


@pytest.mark.asyncio
async def test_a_missing_state_database_is_reported_as_rebuildable(
    registry: ProjectRegistry,
) -> None:
    """The canonical store is derived (ADR-0004). Losing it is a rebuild, not
    data loss, and the message has to say so or someone will restore a backup
    over their Git-tracked migrations."""
    root = Path(registry.load()["demo"]["rootPath"])
    databases = list((root / ".theurian/state").glob("*.sqlite"))
    assert databases, "the fixture must have built a state database to remove"
    for database in databases:
        database.unlink()

    message = await _call_failing(registry, "knowledge.search", projectId="demo", query="token")

    assert "missing" in message
    assert "reconstructible from Git-tracked migrations" in message


# -- knowledge.search ------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_an_approved_item(registry: ProjectRegistry) -> None:
    result = await _call(registry, "knowledge.search", projectId="demo", query="signed token")

    assert result["count"] == 1
    assert result["results"][0]["itemId"] == "architecture.auth-policy"


@pytest.mark.asyncio
async def test_search_matches_the_title_as_well_as_the_body(
    registry: ProjectRegistry,
) -> None:
    result = await _call(registry, "knowledge.search", projectId="demo", query="Authentication")

    assert result["count"] == 1


@pytest.mark.asyncio
async def test_search_is_case_insensitive(registry: ProjectRegistry) -> None:
    upper = await _call(registry, "knowledge.search", projectId="demo", query="SIGNED TOKEN")

    assert upper["count"] == 1


@pytest.mark.asyncio
async def test_a_draft_is_withheld_by_default(registry: ProjectRegistry) -> None:
    """The failure this system exists to prevent: an unreviewed draft returned
    alongside a team decision is indistinguishable from one."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="caching")

    assert result["count"] == 0


@pytest.mark.asyncio
async def test_a_draft_is_returned_only_when_asked_for(registry: ProjectRegistry) -> None:
    result = await _call(
        registry, "knowledge.search", projectId="demo", query="caching", includeUnapproved=True
    )

    assert result["count"] == 1
    hit = result["results"][0]
    assert hit["status"] == "draft"
    assert hit["trustLevel"] == "inferred", "the caller must be able to tell them apart"


@pytest.mark.asyncio
async def test_no_match_is_an_empty_result_not_an_error(registry: ProjectRegistry) -> None:
    result = await _call(registry, "knowledge.search", projectId="demo", query="kubernetes")

    assert result["count"] == 0
    assert result["results"] == []


#: `createItem` with no `upsertRevision` beside it. A normal, supported shape:
#: an item may be declared in one migration and given content in a later one,
#: and it sits in the store with `currentRevisionId: null` until then.
PLACEHOLDER_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1HAAAAA01234567890ABCDE
createdAt: 2026-08-02T14:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.reserved-slot
    kind: architecture
    namespace: backend
    owner: platform-team
"""


@pytest.fixture
def with_a_contentless_item(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` project plus one item that has no revision yet."""
    root = Path(registry.load()["demo"]["rootPath"])
    (root / ".theurian/migrations/01K1HAAAAA01234567890ABCDE-reserve.yaml").write_text(
        PLACEHOLDER_MIGRATION
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_an_item_with_no_revision_yet_is_skipped_rather_than_crashing(
    with_a_contentless_item: ProjectRegistry,
) -> None:
    """`createItem` before `upsertRevision` leaves `currentRevisionId` null.

    Every result is built from a revision, so an item that has none has nothing
    to return — but it is still in `list_items`, still `draft`, and therefore
    still walked by the scan whenever a caller passes `includeUnapproved`. The
    guard has to be a skip: dereferencing the null pointer would take the whole
    search down for every caller, over an item nobody asked about.

    `includeUnapproved` is required to reach it at all, because `createItem`
    files the item as a draft.
    """
    result = await _call(
        with_a_contentless_item,
        "knowledge.search",
        projectId="demo",
        query="caching",
        includeUnapproved=True,
    )

    assert result["count"] == 1, "the search still answers"
    assert result["results"][0]["itemId"] == "architecture.caching-draft"


@pytest.mark.asyncio
async def test_the_contentless_item_really_is_in_the_store(
    with_a_contentless_item: ProjectRegistry,
) -> None:
    """Guards the guard. If the migration had not applied, the test above would
    be walking a two-item store and asserting nothing about the branch."""
    result = await _call(with_a_contentless_item, "knowledge.status", projectId="demo")

    assert result["itemCount"] == 3
    assert result["itemsByStatus"]["draft"] == 2, "the reserved slot is one of them"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_an_empty_query_is_refused(registry: ProjectRegistry, query: str) -> None:
    """Without this, a whitespace query matches every document and quietly
    returns the whole knowledge base."""
    message = await _call_failing(registry, "knowledge.search", projectId="demo", query=query)

    assert "must not be empty" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -5, 10_000])
async def test_an_out_of_range_limit_is_clamped_not_refused(
    registry: ProjectRegistry, limit: int
) -> None:
    """A caller passing 10000 wants "everything"; refusing helps nobody, and
    honouring it would blow their context budget."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="token", limit=limit)

    assert result["count"] == 1


# -- knowledge.get ---------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_the_full_body(registry: ProjectRegistry) -> None:
    """Search returns an excerpt; get is what an agent calls once it has decided
    a document matters."""
    result = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert result["body"] == BODY
    assert result["title"] == "Authentication policy"
    assert result["relations"] == []


@pytest.mark.asyncio
async def test_get_on_an_unknown_item_is_an_error_not_an_empty_document(
    registry: ProjectRegistry,
) -> None:
    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    assert "not present" in message


# -- knowledge.status ------------------------------------------------------


@pytest.mark.asyncio
async def test_status_counts_items_by_status(registry: ProjectRegistry) -> None:
    result = await _call(registry, "knowledge.status", projectId="demo")

    assert result["itemCount"] == 2
    assert result["itemsByStatus"] == {"approved": 1, "draft": 1}
    assert result["appliedMigrations"] == 2
    assert result["stateHash"]


#: One item in each status this tool refuses to count, plus the two the
#: `registry` fixture already holds.
#:
#: `knowledge.status` had no retired item anywhere in its fixtures, so the
#: ``status not in SURFACEABLE_STATUSES`` branch never fired and replacing it
#: with ``item.status.value == ""`` passed the whole suite. All three excluded
#: statuses are here rather than one, because they are excluded for one reason
#: -- `knowledge.get` answers "withheld" and "absent" identically (SEC-13), and
#: a count is the same answer as a number -- and a corpus holding one of them
#: could not tell whether the other two were still excluded.
#:
#: Each arrives by the operation that really produces it: `deprecateItem` for
#: `deprecated`, and a revision declaring the status for `superseded` and
#: `rejected`. Writing all three as revision metadata would have left
#: `deprecateItem`'s own path unrepresented.
RETIRED_MIGRATION_ID = "01K1DDDDDD01234567890ABCDE"
RETIRED_BODY = "# Retired\n\nContent nobody outside the team may be told exists.\n"

RETIRED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {RETIRED_MIGRATION_ID}
createdAt: 2026-08-02T16:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.retired-gateway
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.retired-gateway
    revisionId: 01K1DDDGWA01234567890ABCDE
    contentFile: ../knowledge/architecture/retired-gateway.md
    metadata:
      title: Retired gateway
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/retired-gateway.md
  - op: deprecateItem
    itemId: architecture.retired-gateway
    reason: replaced by the edge proxy
  - op: createItem
    itemId: architecture.superseded-sessions
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.superseded-sessions
    revisionId: 01K1DDDSSN01234567890ABCDE
    contentFile: ../knowledge/architecture/superseded-sessions.md
    metadata:
      title: Superseded sessions
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: superseded
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/superseded-sessions.md
  - op: createItem
    itemId: architecture.rejected-store
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.rejected-store
    revisionId: 01K1DDDRST01234567890ABCDE
    contentFile: ../knowledge/architecture/rejected-store.md
    metadata:
      title: Rejected store
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/rejected-store.md
"""


@pytest.fixture
def with_retired_items(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` project, plus one deprecated, one superseded, one rejected."""
    root = Path(registry.load()["demo"]["rootPath"])
    knowledge = root / ".theurian/knowledge/architecture"
    for slug in ("retired-gateway", "superseded-sessions", "rejected-store"):
        (knowledge / f"{slug}.md").write_text(RETIRED_BODY)
    (root / f".theurian/migrations/{RETIRED_MIGRATION_ID}-retire.yaml").write_text(
        RETIRED_MIGRATION
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


def _stored_statuses(registry: ProjectRegistry, project_id: str = "demo") -> dict[str, str]:
    """Every item in the canonical store, mapped to the status it really holds."""
    paths = ProjectPaths.of(Path(registry.load()[project_id]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    context = RequestContext(project_id=ProjectId(project_id))
    with SqliteCanonicalStore(paths.state / active.database_filename) as store:
        return {item.item_id.value: item.status.value for item in store.list_items(context)}


@pytest.mark.asyncio
async def test_the_retired_items_really_reach_the_canonical_store(
    with_retired_items: ProjectRegistry,
) -> None:
    """Guards the guard. Without this, a migration that stopped applying would
    turn the exclusion test below into an assertion about a two-item store --
    which is exactly the state that let the exclusion be deleted unnoticed."""
    stored = _stored_statuses(with_retired_items)

    assert stored == {
        "architecture.auth-policy": "approved",
        "architecture.caching-draft": "draft",
        "architecture.retired-gateway": "deprecated",
        "architecture.superseded-sessions": "superseded",
        "architecture.rejected-store": "rejected",
    }


@pytest.mark.asyncio
async def test_retired_items_are_absent_from_every_published_count(
    with_retired_items: ProjectRegistry,
) -> None:
    """SEC-13, T-17. A count is an answer to the question the error message refuses.

    `knowledge.get` deliberately says the same thing about a withheld id and an
    absent one, so a caller cannot confirm that a retired id exists. Counting the
    retired items here answers that question with a number instead, and does it
    without the caller needing to guess an id at all.

    Asserted as an exact mapping rather than as "deprecated is not a key": a
    breakdown that reported them under a different label, or collapsed them into
    an `other` bucket, would leak the same quantity.
    """
    result = await _call(with_retired_items, "knowledge.status", projectId="demo")

    assert result["itemsByStatus"] == {"approved": 1, "draft": 1}
    assert result["itemCount"] == 2


@pytest.mark.asyncio
async def test_the_item_count_is_the_sum_of_the_published_breakdown(
    with_retired_items: ProjectRegistry,
) -> None:
    """The subtraction the filtered breakdown would otherwise invite.

    Reporting the true total beside a filtered breakdown leaks the withheld count
    exactly, by subtraction -- so `itemCount` is defined as the sum of what is
    published and not as the store's size. Pinned separately from the exclusion
    itself because the two fail independently: this one holds while
    `itemsByStatus` is correct and `itemCount` is `len(items)`.
    """
    result = await _call(with_retired_items, "knowledge.status", projectId="demo")
    stored = _stored_statuses(with_retired_items)

    assert result["itemCount"] == sum(result["itemsByStatus"].values())
    assert result["itemCount"] < len(stored), (
        "the store must be larger than the published count, or this asserts nothing"
    )


# -- knowledge.status: T-17's equality, and the two fields exempt from it ---
#
# The tests above compare one project's report against what its own store holds.
# The pair below is the other shape, and the one T-17 is closed by: two corpora,
# one identical request. #19 measured that difference through the real tool and
# recorded the result in `schemas/mcp/knowledge-status-response.schema.json`;
# this is that measurement, held as a test.

#: Both halves register under this id, in data directories of their own. The
#: request is then identical between them, which is what lets `projectId` be a
#: field the comparison *asserts equal* rather than one it has to exclude --
#: #19's measurement recorded it equal, and a pair registered as two different
#: ids would drop a published field out of the property for a reason that has
#: nothing to do with what was withheld.
WITHHELD_HALF_PROJECT_ID = "status-pair"


@dataclass(frozen=True, slots=True)
class _WithheldHalfPair:
    """Two projects one migration apart, and that migration withholds everything it makes.

    ``absent``  never held an item a caller may not read
    ``present`` holds three, and differs in nothing else
    """

    absent: ProjectRegistry
    present: ProjectRegistry


def _build_withheld_half_project(root: Path, *, holds_the_withheld_half: bool) -> None:
    """One half of the pair, built by the real CLI in ``root``.

    The approved item is written from the same constants the `registry` fixture
    uses and the retired trio from the same migration `with_retired_items`
    applies, so the two halves cannot drift apart in a way that makes their
    reports differ for an honest reason.

    No index build. `knowledge.status` reads the canonical store, and a build
    would put an index identity into a comparison that is about the canonical
    half -- ``stateHash`` excepted, the two are independent (ADR-0016).
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    if holds_the_withheld_half:
        for slug in ("retired-gateway", "superseded-sessions", "rejected-store"):
            (knowledge / f"{slug}.md").write_text(RETIRED_BODY)
        (root / f".theurian/migrations/{RETIRED_MIGRATION_ID}-retire.yaml").write_text(
            RETIRED_MIGRATION
        )
    _run("project", "register", "--project-id", WITHHELD_HALF_PROJECT_ID)
    _run("migrate", "apply")


@pytest.fixture(scope="module")
def one_withheld_migration_apart(
    tmp_path_factory: pytest.TempPathFactory,
) -> _WithheldHalfPair:
    """The pair #19 measured: identical projects, one extra withheld-only migration.

    Two registries rather than two ids in one, because an id is unique per
    registry and `projectId` is a published field. The alternative -- one
    registry, ids `probe` and `control` -- would make that field differ for a
    reason that has nothing to do with the withheld items, and the comparison
    would have to exclude it.

    Module-scoped: two real CLI builds under a `THEURIAN_DATA_DIR` of their own,
    and both tests below ask them the same question.
    """
    base = tmp_path_factory.mktemp("withheld-half")
    monkey = pytest.MonkeyPatch()
    registries: dict[str, ProjectRegistry] = {}
    try:
        for name, holds in (("absent", False), ("present", True)):
            data_dir = base / f"{name}-datadir"
            monkey.setenv("THEURIAN_DATA_DIR", str(data_dir))
            root = base / name
            root.mkdir()
            # `init` and `project register` resolve the project from the working
            # directory and take no argument that says where.
            monkey.chdir(root)
            _build_withheld_half_project(root, holds_the_withheld_half=holds)
            registries[name] = ProjectRegistry.default(data_dir)
    finally:
        monkey.undo()
    return _WithheldHalfPair(absent=registries["absent"], present=registries["present"])


def test_the_pair_differs_by_a_migration_that_creates_only_withheld_items(
    one_withheld_migration_apart: _WithheldHalfPair,
) -> None:
    """Guards the differential below, which an empty migration would satisfy too.

    That comparison is a statement about withheld content only if the extra
    migration really landed and really created nothing a caller may see. A
    migration file that applied and created no item at all moves `stateHash` and
    `appliedMigrations` and leaves every count alone -- which is exactly the
    result the test below is written to observe, with nothing withheld anywhere
    in it. So the two stores are read directly, and both halves are pinned: the
    surfaceable half identical, the extra half retired in all three ways.

    All three retired statuses rather than #19's single `rejected` item. They are
    withheld for one reason (SEC-13, T-17), and a pair differing by one of them
    could not tell whether the other two had started moving a count.
    """
    absent = _stored_statuses(one_withheld_migration_apart.absent, WITHHELD_HALF_PROJECT_ID)
    present = _stored_statuses(one_withheld_migration_apart.present, WITHHELD_HALF_PROJECT_ID)

    assert absent == {"architecture.auth-policy": "approved"}
    assert present == {
        "architecture.auth-policy": "approved",
        "architecture.retired-gateway": "deprecated",
        "architecture.superseded-sessions": "superseded",
        "architecture.rejected-store": "rejected",
    }


@pytest.mark.asyncio
async def test_a_withheld_item_moves_exactly_the_two_fields_the_status_schema_exempts(
    one_withheld_migration_apart: _WithheldHalfPair,
) -> None:
    """SEC-13, T-17, #19. T-17's equality for `knowledge.status`, exception set and all.

    One request against two corpora, which is the form that closes T-17: a
    project holding items the caller may not read must answer the way a project
    that never held them answers. `knowledge.search` holds that outright
    (`test_a_withheld_document_changes_nothing_a_caller_can_see`) and
    `knowledge.get` holds it by refusing to distinguish a withheld id from an
    absent one. This tool holds it for four of its six fields; the other two are
    `stateHash` and `appliedMigrations`, exempt by the recorded decision in
    `schemas/mcp/knowledge-status-response.schema.json` (#19) -- neither carries
    a bit about *what* was withheld, and this tool takes one argument, so there
    is no probe to vary and therefore no extraction oracle.

    **The exempt set is asserted exactly, not as an upper bound.** A subset check
    would also pass a response that had stopped publishing `appliedMigrations`,
    and one whose `stateHash` had become insensitive to canonical state -- both
    are changes to a published contract, and both should be *decided* rather than
    absorbed by a comparison that quietly widens. #19 measured that both move for
    this input, so both are asserted to move. This test is the project's record
    of which fields are exempt, and an exemption nobody notices expiring is not a
    record.

    **Two directories, one hash -- so the pair's difference really is the
    migration.** No path, mtime or hostname participates in a state hash
    (`StateInputs`, in `theurian.domain.state`), which is what makes the two
    halves comparable at all despite being built in different directories.
    Measured rather than reasoned about: with the fixture mutated to give the
    absent half the withheld trio as well, `moved` comes back empty -- the same
    hash from two separate builds in two separate trees.

    **What it goes RED for.** A seventh field priced on the store's true size; a
    breakdown that counted retired items, or bucketed them under another label;
    an `itemCount` taken from `len(items)` rather than from the published
    breakdown; a `schemaVersion` or `projectId` made to vary with what is
    withheld. Each moves a key outside the exempt pair, and the set comparison
    names the key. The response's *shape* is the wire contract's subject
    (`test_wire_contract.py`); what moves between two shapes is this one's.

    The literal counts are what stop this being an equality between two empty
    reports: a tool that answered `{}` and `0` for every project on earth would
    satisfy the set comparison perfectly. `schemaVersion` is held equal by that
    comparison rather than by a literal here, since its value is pinned where the
    schema is checked.
    """
    absent = await _call(
        one_withheld_migration_apart.absent, "knowledge.status", projectId=WITHHELD_HALF_PROJECT_ID
    )
    present = await _call(
        one_withheld_migration_apart.present, "knowledge.status", projectId=WITHHELD_HALF_PROJECT_ID
    )

    assert absent.keys() == present.keys(), (
        "a field one project publishes and the other does not is the whole leak, "
        "not a difference in values"
    )
    moved = {key for key in absent if absent[key] != present[key]}

    assert (
        (absent["projectId"], absent["itemCount"], absent["itemsByStatus"])
        == (
            present["projectId"],
            present["itemCount"],
            present["itemsByStatus"],
        )
        == (WITHHELD_HALF_PROJECT_ID, 1, {"approved": 1})
    ), "both reports must describe the one approved item, or this compares two empty answers"
    assert moved == {"stateHash", "appliedMigrations"}, (
        "exactly the two fields the published schema exempts -- no more, and no fewer"
    )


# -- knowledge.status: the read cost does not carry the withheld count ------
#
# The timing sibling of the equality above, and the channel #19 was opened to
# close. `knowledge.status` used to run `list_items` (a `SELECT *` with no
# status predicate) and drop the retired rows in Python, so it materialised
# every withheld row before discarding it -- and the response *time* then
# carried the withheld count, recoverable by subtracting the published
# `itemCount` (T-17; the `search._scan` sibling of the same channel is #158).
# The fix counts in SQL over the surfaceable statuses alone, so the store never
# hands a withheld row back.
#
# Nothing else in this file can see that regression: the response is
# byte-identical either way, which is exactly why reverting to `list_items`
# passed the whole suite. Only the count of rows the store materialises moves,
# so that is what this measures -- at the connection, where both the SQL path
# and the `list_items` path fetch their rows.

READ_COST_BASELINE_ID = "read-cost-baseline"
READ_COST_HEAVY_ID = "read-cost-heavy"

#: How many withheld (`rejected`) items the heavy corpus holds beyond the three
#: approved items both corpora share. Large enough that a `list_items` path
#: materialising every row hands back an unmistakably different count; the two
#: corpora are otherwise identical, so the three approved rows and the single
#: migration-history row are the only rows either report's SQL returns.
READ_COST_WITHHELD = 25

READ_COST_MIGRATION_ID = "01K1RCST0001234567890ABCDE"


def _read_cost_item_ops(slug: str, revision_id: str, title: str, status: str) -> str:
    return f"""  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: {revision_id}
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""


def _build_read_cost_project(root: Path, project_id: str, *, withheld: int) -> None:
    """Three approved items, plus ``withheld`` rejected ones, in one migration.

    `init` and `project register` resolve the project from the working
    directory and take no argument that says where, so the caller has chdir'd
    into ``root`` already; the file writes below are given it as well so they do
    not depend on that. One migration in both corpora, so `appliedMigrations`
    -- and the single migration-history row it reads -- is identical between
    them and the only difference the measurement sees is the withheld items.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    operations = ""
    for i in range(3):
        slug = f"read-approved-{i}"
        (knowledge / f"{slug}.md").write_text(f"# Approved {i}\n\nApproved body {i}.\n")
        operations += _read_cost_item_ops(
            slug, f"01K1RCAP{i:02d}01234567890ABCDE", f"Approved {i}", "approved"
        )
    for i in range(withheld):
        slug = f"read-withheld-{i:03d}"
        (knowledge / f"{slug}.md").write_text(f"# Withheld {i}\n\nWithheld body {i}.\n")
        operations += _read_cost_item_ops(
            slug, f"01K1RCWH{i:02d}01234567890ABCDE", f"Withheld {i}", "rejected"
        )
    header = (
        "apiVersion: theurian.dev/v1\n"
        f"id: {READ_COST_MIGRATION_ID}\n"
        "createdAt: 2026-08-02T10:00:00+09:00\n"
        "author: engineer@example.com\n"
        "operations:\n"
    )
    (root / f".theurian/migrations/{READ_COST_MIGRATION_ID}-corpus.yaml").write_text(
        header + operations
    )
    _run("project", "register", "--project-id", project_id)
    _run("migrate", "apply")


@dataclass(frozen=True, slots=True)
class _ReadCostCorpora:
    """Two projects sharing three approved items; one also holds withheld ones.

    ``stored`` is the heavy store read directly, so the measurement's guard can
    assert the withheld rows a `list_items` path would fetch really landed --
    an equal read count between two three-item stores would be equal for the
    wrong reason.
    """

    registry: ProjectRegistry
    stored: dict[str, str]


@pytest.fixture(scope="module")
def read_cost_corpora(tmp_path_factory: pytest.TempPathFactory) -> _ReadCostCorpora:
    """One baseline project and one withheld-heavy project, built by the real CLI.

    Both register into one `THEURIAN_DATA_DIR` under distinct ids, so one
    registry answers for both and the comparison is between two calls to the
    same daemon.
    """
    base = tmp_path_factory.mktemp("read-cost")
    data_dir = base / "datadir"
    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(data_dir))
    try:
        for project_id, withheld in (
            (READ_COST_BASELINE_ID, 0),
            (READ_COST_HEAVY_ID, READ_COST_WITHHELD),
        ):
            root = base / project_id
            root.mkdir()
            monkey.chdir(root)
            _build_read_cost_project(root, project_id, withheld=withheld)
        registry = ProjectRegistry.default(data_dir)
        stored = _stored_statuses(registry, READ_COST_HEAVY_ID)
    finally:
        monkey.undo()
    return _ReadCostCorpora(registry=registry, stored=stored)


class _RowMeter:
    """Rows the canonical store materialises, counted at the connection.

    Every row SQLite hands back to this store passes through ``row_factory`` on
    its way to a mapper, so counting the calls counts rows materialised -- the
    quantity the old `list_items` path inflated by fetching every row, withheld
    ones included, and dropping them in Python. Reconstructs a real
    ``sqlite3.Row`` so the store's key access (``row["status"]``) still works.
    """

    def __init__(self) -> None:
        self.rows = 0

    def factory(self, cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> sqlite3.Row:
        self.rows += 1
        return sqlite3.Row(cursor, row)


@contextlib.contextmanager
def _rows_materialized_by_the_canonical_store() -> Iterator[_RowMeter]:
    """Count rows the store fetches, by wrapping the reader it opens.

    Installed on the connection ``open_read_connection`` returns, *after* it
    runs -- so the schema-version `SELECT` inside it is not counted. Both
    corpora run that identically and it is not the read under test. Every read
    in this adapter opens through this one function as things stand, which is
    what makes the count complete: a property of the module today, a
    precondition of the measurement rather than a guarantee of it.
    """
    meter = _RowMeter()
    # The original from where it is defined, not from `store` (which imports it,
    # so `--no-implicit-reexport` will not read it back off that module). The
    # patch below still targets the name `store` bound at import, because that is
    # the reference `SqliteCanonicalStore._conn` actually calls.
    real_open = open_read_connection

    def traced(path: Path) -> sqlite3.Connection:
        connection = real_open(path)
        connection.row_factory = meter.factory
        return connection

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "open_read_connection", traced)
        yield meter


@pytest.mark.asyncio
async def test_status_materializes_the_same_rows_however_many_are_withheld(
    read_cost_corpora: _ReadCostCorpora,
) -> None:
    """SEC-13, T-17, #19. The read cost may not carry the withheld count.

    The equality tests above compare two *responses*; this compares two
    *costs*. A project holding twenty-five items a caller may not read must be
    answered by the same number of materialised rows as one holding none, or
    the response time carries the withheld count and a caller with a stopwatch
    recovers by subtraction exactly what the counts withhold -- the oracle T-17
    exists to close, and the channel #19 opened to shut.

    Measured at the row rather than timed, so it goes RED deterministically:
    reverting `knowledge.status` to the `list_items` + Python-filter path it
    ran before the fix makes the store fetch every withheld row, and the heavy
    corpus's count jumps by twenty-five while the baseline's stays put. The
    response is byte-identical on that path, which is why every other status
    test here stays green through the regression and only this one falls.
    """
    corpora = read_cost_corpora

    # Guard: the heavy store really holds the withheld rows a `list_items` path
    # would materialise. Without it, an equal count below could be two
    # three-item stores agreeing for a reason that has nothing to do with the
    # fix -- the shape that let the exclusion be deleted unnoticed.
    withheld_rows = {
        item: status for item, status in corpora.stored.items() if status == "rejected"
    }
    assert len(withheld_rows) == READ_COST_WITHHELD, (
        f"the heavy corpus must hold {READ_COST_WITHHELD} withheld items for the measurement to "
        f"mean anything; its store holds {len(withheld_rows)}"
    )
    assert len(corpora.stored) == 3 + READ_COST_WITHHELD, (
        "the heavy store must be larger than the published count, or a `list_items` path would "
        "materialise nothing extra and this would measure two equal-sized stores"
    )

    with _rows_materialized_by_the_canonical_store() as meter:
        meter.rows = 0
        baseline = await _call(
            corpora.registry, "knowledge.status", projectId=READ_COST_BASELINE_ID
        )
        baseline_rows = meter.rows
        meter.rows = 0
        heavy = await _call(corpora.registry, "knowledge.status", projectId=READ_COST_HEAVY_ID)
        heavy_rows = meter.rows

    # Both report the three approved items and nothing else -- the byte-identical
    # response that hides the cost regression from every other assertion here.
    assert baseline["itemsByStatus"] == heavy["itemsByStatus"] == {"approved": 3}
    assert baseline["itemCount"] == heavy["itemCount"] == 3

    assert baseline_rows > 0, (
        "the meter counted no rows, so it is watching a connection the tool does not open -- "
        "the measurement is inert and would pass whatever the read cost did"
    )
    assert heavy_rows == baseline_rows, (
        f"knowledge.status materialised {heavy_rows} rows against a store holding "
        f"{READ_COST_WITHHELD} withheld items and {baseline_rows} against one holding none. The "
        f"read cost is carrying the withheld count: the store is fetching rows it then discards, "
        f"so latency reports by subtraction what the counts refuse to (SEC-13, T-17)."
    )


@contextlib.contextmanager
def _statement_the_store_runs() -> Iterator[dict[str, Any]]:
    """Capture the exact SQL a store method hands to its reader, read at runtime.

    The plan assertion below must be checked against the statement the tool
    truly runs. A SQL string copied into the test would drift from
    ``count_surfaceable_by_status`` the first time its predicate changed, and go
    on asserting a covering-index plan for a query nothing runs -- so the
    statement is read off ``_read_all`` as the method builds it, never restated.
    ``count_surfaceable_by_status`` makes exactly one ``_read_all`` call, so the
    captured dict holds that one statement.
    """
    captured: dict[str, Any] = {}
    real_read_all = SqliteCanonicalStore._read_all

    def spy(
        store: SqliteCanonicalStore,
        sql: str,
        parameters: tuple[str, ...],
        mapper: Callable[[sqlite3.Row], Any],
    ) -> tuple[Any, ...]:
        captured["sql"] = sql
        captured["params"] = parameters
        return real_read_all(store, sql, parameters, mapper)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SqliteCanonicalStore, "_read_all", spy)
        yield captured


def _query_plan(db_path: Path, sql: str, params: tuple[str, ...]) -> str:
    """The ``EXPLAIN QUERY PLAN`` detail lines for ``sql``, joined for matching.

    Read against a fresh read-only connection to the same file the store used,
    so the plan is the one SQLite forms over the shipped schema -- indexes and
    all -- not over a hand-built copy of it.
    """
    with contextlib.closing(open_read_connection(db_path)) as conn:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return "\n".join(str(row["detail"]) for row in rows)


@pytest.mark.asyncio
async def test_status_count_is_answered_by_a_covering_index(
    read_cost_corpora: _ReadCostCorpora,
) -> None:
    """SEC-13, T-17, #19. The withheld-count independence rests on this index.

    The row-count pin above proves ``knowledge.status`` no longer materialises
    the withheld rows -- but only against the ``list_items`` path it replaced.
    ``count_surfaceable_by_status`` aggregates in SQL, so its ``GROUP BY`` hands
    back one row per surfaceable status whatever the store holds; the row meter
    therefore *cannot* see SQLite walk every row of the project to build that
    aggregate. That walk is exactly what losing the covering index
    ``idx_items_status(project_id, status)`` restores: the planner falls to the
    namespace index, filters on ``project_id`` alone, and reads each withheld row
    to group it -- reopening the O(withheld) timing channel T-17 closed, while
    the response and the meter stay byte-identical.

    So this pins the mechanism the row meter only proxies: the count is served by
    a covering index that reads none of the withheld rows. Deleting or renaming
    the index, or changing the predicate so the index can no longer cover the
    query, drops the plan to a scan and turns this RED where the row-count pin
    above stays green -- the two catch different regressions, so both are kept.

    **The seek form is asserted, not merely the index name.** ``USING COVERING
    INDEX idx_items_status`` appears in a ``SCAN`` line too, so that substring
    alone was satisfied by a plan that walks the whole index -- measured:
    reversing the declared columns to ``(status, project_id)`` leaves the phrase
    intact while the leading column stops being the project. The three parts
    below -- ``SEARCH``, the index, and the ``(project_id=?`` that opens the
    constraint list -- are what say SQLite seeks into one project's entries; the
    reversal plans ``(status=? AND project_id=?)`` and fails the third.
    """
    corpora = read_cost_corpora

    # Arrange: the same heavy store the row-count pin measures, so the plan is
    # read over a corpus that really holds withheld rows the covering index must
    # not read -- built by the real CLI, so it is the shipped schema under test.
    paths = ProjectPaths.of(Path(corpora.registry.load()[READ_COST_HEAVY_ID]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    db_path = paths.state / active.database_filename
    context = RequestContext(project_id=ProjectId(READ_COST_HEAVY_ID))

    # Act: run the real count and capture the statement it built, then plan it.
    # The store is entered first so its connection opens before the capture is
    # installed -- that open does not go through `_read_all`, so the one call the
    # capture sees is the count's own statement.
    with SqliteCanonicalStore(db_path) as store, _statement_the_store_runs() as captured:
        store.count_surfaceable_by_status(context)

    assert captured, (
        "count_surfaceable_by_status ran no statement through the store reader, so the plan "
        "below would describe a query the tool never runs -- the capture watches the wrong method"
    )
    plan = _query_plan(db_path, captured["sql"], captured["params"])

    # Assert: SQLite *seeks* into the covering index rather than walking it. All
    # three fragments are stable across SQLite versions for this shape, and each
    # fails for a different regression: `SEARCH` for a plan that scans,
    # `idx_items_status` for a dropped or renamed index, `(project_id=?` for an
    # index whose leading column is no longer the project.
    for fragment in ("SEARCH", "USING COVERING INDEX idx_items_status", "(project_id=?"):
        assert fragment in plan, (
            f"knowledge.status is no longer answered by a seek into the covering index "
            f"idx_items_status(project_id, status): {fragment!r} is missing. SQLite planned:\n"
            f"{plan}\n"
            "Without that seek the count reads rows of the project it does not publish, withheld "
            "ones included, and the response time carries the withheld count again -- the "
            "O(withheld) channel T-17 closed and #19 must keep shut (SEC-13)."
        )


# -- knowledge.search substring fallback: the scan's read cost does not carry
#    the withheld count (#158, the `search._scan` sibling of #19 above) --------
#
# `knowledge.search`'s unranked fallback (`search._scan`) used to read every item
# with `list_items` and drop the retired rows in Python, so its response *time*
# carried the withheld count exactly the way `knowledge.status` did before #19 --
# recoverable by subtracting the published `count` (T-17, the oracle #158 closes).
# 5f6ce09 pushed the status gate into SQL (`list_items_by_status`, `status IN
# (...)` forced through `idx_items_status`), so the store never hands the scan a
# withheld row. As with the `knowledge.status` siblings above, no response field
# moves either way -- the response is byte-identical -- so only the plan and the
# rows materialised can see the regression, and each of the three below fails on a
# different mutation the others survive.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("include_unapproved", "expected_status_count"),
    [(False, 1), (True, 3)],
    ids=["default-approved-only", "include-unapproved-three-statuses"],
)
async def test_the_substring_scan_reads_items_through_idx_items_status(
    read_cost_corpora: _ReadCostCorpora,
    include_unapproved: bool,
    expected_status_count: int,
) -> None:
    """SEC-13, T-17, #158. The scan's withheld-count independence rests on this index.

    `search._scan` reads its surfaceable items through
    `SqliteCanonicalStore.list_items_by_status`, and that method's `INDEXED BY
    idx_items_status` hint is the whole of what keeps the read proportional to the
    rows it may return. Drop the hint and SQLite prefers the primary-key autoindex
    -- `ORDER BY item_id` is satisfied there with no temp b-tree -- then reads
    every withheld row to apply `status` as a post-filter (the reviewer measured VM
    steps 75 -> 325 -> 1575 across 0/50/300 withheld), reopening the O(withheld)
    timing channel #158 closed. Delete `CREATE INDEX idx_items_status` and the
    hinted statement raises `no such index` rather than silently scanning.

    Named `USING INDEX`, not `COVERING`: these are `SELECT *` reads, so SQLite
    seeks the index and then fetches each matched row from the table -- the plan is
    `SEARCH ... USING INDEX idx_items_status`, never the `USING COVERING INDEX` that
    `knowledge.status`'s aggregate earns.

    Both gate widths are pinned. `includeUnapproved` unset resolves a one-status
    set (`approved`) and the widened gate resolves three (`approved`, `draft`,
    `proposed`), so the planned statement is `IN (?)` or `IN (?, ?, ?)`
    respectively -- and the widened read must seek through the same index, or a
    caller who opts in pays O(withheld) even though the withheld set is unchanged.

    The row-cost pin below proves the scan no longer materialises the withheld
    rows, but only against the `list_items` path it replaced. This pins the
    mechanism that pin only proxies: the read is *planned* through
    `idx_items_status`, so it can never touch a withheld row. Dropping or renaming
    the index turns this RED where the row-cost pin could still pass on a plan that
    happened to read the same rows, so the two catch different regressions and both
    are kept.
    """
    corpora = read_cost_corpora

    # Arrange: the heavy store the row-cost pin measures, built by the real CLI so
    # the plan is read over the shipped schema and a corpus that truly holds the
    # withheld rows the index must not read.
    paths = ProjectPaths.of(Path(corpora.registry.load()[READ_COST_HEAVY_ID]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    db_path = paths.state / active.database_filename
    context = RequestContext(project_id=ProjectId(READ_COST_HEAVY_ID))

    # The status set `_scan` resolves for this `includeUnapproved`, built the same
    # way here -- so the statement planned below is the one the shipped fallback
    # runs, never a hand-copied stand-in that would drift the first time the
    # surfaceable set changed.
    surfaceable = frozenset(
        s for s in KnowledgeStatus if may_surface(s, include_unapproved=include_unapproved)
    )
    assert len(surfaceable) == expected_status_count, (
        f"the {'widened' if include_unapproved else 'default'} gate must resolve "
        f"{expected_status_count} surfaceable status(es), so the statement planned below is the "
        f"`IN ({', '.join('?' * expected_status_count)})` the tool runs; it resolved "
        f"{len(surfaceable)}. A drift here would pin the plan of a query the tool never issues."
    )

    # Act: run the real read and capture the statement it built, then plan it. The
    # store is entered first so its connection opens before the capture is
    # installed -- that open does not go through `_read_all`, so the one call the
    # capture sees is `list_items_by_status`'s own statement.
    with SqliteCanonicalStore(db_path) as store, _statement_the_store_runs() as captured:
        store.list_items_by_status(context, statuses=surfaceable)

    assert captured, (
        "list_items_by_status ran no statement through the store reader, so the plan below "
        "would describe a query the tool never runs -- the capture watches the wrong method"
    )
    assert captured["sql"].count("?") == 1 + expected_status_count, (
        "the captured statement binds one `project_id` plus one placeholder per surfaceable "
        f"status, so a {expected_status_count}-status gate must show {1 + expected_status_count} "
        f"placeholders; it showed {captured['sql'].count('?')}. The capture watched the wrong "
        "statement, so the plan below would not describe this gate width."
    )
    plan = _query_plan(db_path, captured["sql"], captured["params"])

    # Assert: SQLite seeks through `idx_items_status`. `SEARCH ... USING INDEX
    # idx_items_status` is stable across SQLite versions for this shape; a fall to
    # the primary-key autoindex reads `USING INDEX sqlite_autoindex_knowledge_items`
    # instead and fails here.
    assert "USING INDEX idx_items_status" in plan, (
        "the substring scan's item read is no longer served by idx_items_status(project_id, "
        f"status) for a {expected_status_count}-status gate. SQLite planned:\n{plan}\nWithout the "
        "hint the read seeks on project_id alone and reads every withheld row to post-filter "
        "status, so the scan's response time carries the withheld count again -- the O(withheld) "
        "channel T-17 closed and #158 must keep shut (SEC-13)."
    )


@pytest.mark.asyncio
async def test_the_substring_scan_materializes_the_same_rows_however_many_are_withheld(
    read_cost_corpora: _ReadCostCorpora,
) -> None:
    """SEC-13, T-17, #158. The scan's read cost may not carry the withheld count.

    The plan pin above proves the read *is planned* through `idx_items_status`;
    this proves the effect it exists for, against the `list_items` path #158
    replaced. A project holding twenty-five items a caller may not read must be
    answered by the same number of materialised rows as one holding none, or the
    scan's response time carries the withheld count and a caller with a stopwatch
    recovers by subtraction exactly what `count` withholds (the oracle T-17 closes).

    Measured at the row rather than timed, so it goes RED deterministically:
    reverting `_scan` to the pre-5f6ce09 `list_items` + Python `may_surface` path
    makes the store fetch every withheld row, and the heavy corpus's count jumps by
    twenty-five while the baseline's stays put. Both responses answer the three
    approved items and nothing else -- byte-identical on that path, which is why
    every other substring test here stays green through the regression and only
    this one falls.
    """
    corpora = read_cost_corpora

    # Guard: the heavy store really holds the withheld rows a `list_items` path
    # would materialise. Without it an equal count below could be two three-item
    # stores agreeing for a reason that has nothing to do with the fix.
    withheld_rows = {
        item: status for item, status in corpora.stored.items() if status == "rejected"
    }
    assert len(withheld_rows) == READ_COST_WITHHELD, (
        f"the heavy corpus must hold {READ_COST_WITHHELD} withheld items for the measurement to "
        f"mean anything; its store holds {len(withheld_rows)}"
    )
    assert len(corpora.stored) == 3 + READ_COST_WITHHELD, (
        "the heavy store must be larger than the surfaceable count, or a `list_items` path would "
        "materialise nothing extra and this would measure two equal-sized stores"
    )

    with _rows_materialized_by_the_canonical_store() as meter:
        meter.rows = 0
        baseline = await _call(
            corpora.registry,
            "knowledge.search",
            projectId=READ_COST_BASELINE_ID,
            query="approved",
        )
        baseline_rows = meter.rows
        meter.rows = 0
        heavy = await _call(
            corpora.registry, "knowledge.search", projectId=READ_COST_HEAVY_ID, query="approved"
        )
        heavy_rows = meter.rows

    # Both answer through the unranked scan and return the three approved items --
    # the byte-identical response that hides the cost regression from every other
    # assertion here.
    for response in (baseline, heavy):
        assert response["retrieval"]["mode"] == "substring", (
            "the corpus must answer through the unranked scan, or this does not exercise "
            "`_scan`'s `list_items_by_status` read"
        )
        assert response["retrieval"]["indexed"] is False
    assert baseline["count"] == heavy["count"] == 3, (
        "both corpora hold the same three approved items; a different count means a withheld "
        "row surfaced or an approved one was dropped"
    )

    assert baseline_rows > 0, (
        "the meter counted no rows, so it is watching a connection the tool does not open -- "
        "the measurement is inert and would pass whatever the read cost did"
    )
    assert heavy_rows == baseline_rows, (
        f"the substring scan materialised {heavy_rows} rows against a store holding "
        f"{READ_COST_WITHHELD} withheld items and {baseline_rows} against one holding none. The "
        f"read cost is carrying the withheld count: the store is fetching rows it then discards, "
        f"so latency reports by subtraction what the scan refuses to (SEC-13, T-17, #158)."
    )


@pytest.mark.asyncio
async def test_the_substring_scan_never_surfaces_a_retired_item_even_with_include_unapproved(
    with_retired_items: ProjectRegistry,
) -> None:
    """SEC-13, T-15, #158. The SQL gate drops no visible row and admits no withheld one.

    #158 replaced `_scan`'s `list_items` + Python `may_surface` filter with a SQL
    `status IN (...)` over the set `_scan` resolves from `may_surface`. That
    predicate is now the whole gate on this path, so a status wrongly admitted into
    the surfaceable set -- or the predicate dropped -- would hand a retired revision
    to a caller while the plan and row count stayed innocent. The row-cost pins
    above cannot see that: they move rows, this moves who is behind them.

    `with_retired_items` has no index, so `knowledge.search` answers through
    `substring_answer` -> `_scan`. Its three retired items (deprecated, superseded,
    rejected) share a body no surfaceable item holds, and `includeUnapproved=True`
    is the widest a caller can open the gate -- yet retired knowledge is reachable
    through no flag (`may_surface` withholds it whatever `include_unapproved` says,
    because a rejected revision is where the secret that caused the rejection still
    lives). So a needle that matches only the retired bodies must find nothing.

    Distinct from `test_a_draft_is_withheld_by_default`, which is RED only when the
    gate drops the default `include_unapproved=False` case: this stays RED when the
    flag itself wrongly widens to a never-surfaceable status, which that test, run
    without the flag, cannot reach.
    """
    result = await _call(
        with_retired_items,
        "knowledge.search",
        projectId="demo",
        query="retired",
        includeUnapproved=True,
    )

    assert result["retrieval"]["mode"] == "substring", (
        "the corpus must answer through the unranked scan, or this does not exercise "
        "`_scan`'s `list_items_by_status` gate"
    )
    assert result["count"] == 0, (
        "the substring scan surfaced a retired item: `retired` matches only the deprecated, "
        "superseded and rejected bodies, and none of the three may be surfaced through any flag "
        f"(SEC-13, T-15, #158). Returned statuses: {[hit['status'] for hit in result['results']]}"
    )


# -- project.list ----------------------------------------------------------


@pytest.mark.asyncio
async def test_project_list_reports_the_registered_projects(
    registry: ProjectRegistry,
) -> None:
    result = await _call(registry, "project.list")

    assert result["count"] == 1
    assert result["projects"][0]["projectId"] == "demo"


# -- system.capabilities ---------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_report_what_is_and_is_not_built(registry: ProjectRegistry) -> None:
    """A client that only learns "version 0.1" has to guess. Per-feature flags
    let it degrade one feature at a time.

    This assertion previously claimed `hybridRetrieval is False` and kept
    passing after hybrid retrieval shipped, which is how a capability
    declaration and its implementation drift apart unnoticed.

    The drift runs the other way too, so all seven entries of the capability
    block are pinned to the value they actually hold rather than only the `True`
    ones: `knowledgeSearch` and the six booleans. `reviewIngestion`,
    `traceability` and `knowledgeSearch` were all unpinned until #129 -- mutations
    flipping each boolean to `True` and rewriting `"hybrid"` to `"substring"`
    survived the whole suite, and the first two are what Milestone 7 flips.
    That the block holds *only* those seven is the sibling test below.

    Four fields sit outside the block. Three are now pinned to a value here
    too: `version` and `protocolVersion` against the package's own constants,
    and `note` to the substring that carries its one load-bearing claim
    (ADR-0013's "no write-intent tool exists"). Before this, mutations on all
    three -- including inverting `note`'s meaning -- survived the whole suite,
    which is a wider gap than it looks: `version` and `protocolVersion` are
    not incidental metadata either -- they re-publish the same two process
    constants `theurian compat check` reads directly for CP-6
    (docs/architecture/requirements-analysis.md), so a divergence here is
    invisible to that gate and visible only through this response. Only
    `schemaVersion` stays unpinned to a value by
    design, asserted truthy rather than to a constant, because the schema
    version moves on its own schedule.

    A `milestone` field used to sit beside them, reporting a build progress
    integer that had drifted stale against the README since Milestone 6
    closed, with no test, schema, or doc pinning it -- a mutation to `99`
    survived the whole suite. #206 removed it rather than defining it: it was
    produced in exactly one place and consumed nowhere in this repository.
    The sibling test below pins the response's full top-level key set,
    `milestone`'s absence included.
    """
    result = await _call(registry, "system.capabilities")

    assert result["capabilities"]["knowledgeSearch"] == "hybrid", (
        "this is the only capability that is not a boolean, and it names the "
        "retrieval a client may ask for rather than whether a feature exists: "
        "`hybrid` says lexical and vector results are fused. A response's own "
        "`retrieval.mode` reports what actually ran, which stays `substring` "
        "until a project has an index -- so a build that downgraded this to "
        "`substring` would be indistinguishable, to a client, from every "
        "un-indexed project it has ever seen. A mutation doing exactly that "
        "survived the whole suite (#129)."
    )
    assert result["capabilities"]["writeTools"] is False
    assert result["capabilities"]["hybridRetrieval"] is True
    assert result["capabilities"]["knowledgeGet"] is True
    assert result["capabilities"]["raptor"] is True, (
        "the retrieval CL connects the forest to a response: a summary retriever "
        "traverses `nodes_fts` to leaves and a surfaced leaf carries `raptorPath`, "
        "so this flag now says what a caller can get. A client reading `true` may "
        "ask for the `raptorPath` a ranked hit over a `--raptor` index carries."
    )
    assert result["capabilities"]["reviewIngestion"] is False, (
        "no review history is ingested: `infrastructure/github/` holds no adapter "
        "and `theurian ingest` reads local files only, so a client reading `true` "
        "would offer a feature no code path performs. Flipping it is a "
        "security-relevant change and not a feature flag -- the T-7 entry in "
        "docs/security/threat-model.md cites this `false` as what stands in for "
        "the repository allowlist while SEC-10's reader is owed (#129), so the "
        "change that flips it owes the allowlist as well."
    )
    assert result["capabilities"]["traceability"] is False, (
        "no tool answers FR-T3's questions -- which code implements a spec, which "
        "tests verify one. `CanonicalStore.list_traceability_edges` is declared on "
        "the port and called from nowhere in `src/`, and `knowledge.get`'s "
        "`relations` come from `list_relations`, which walks knowledge-to-knowledge "
        "relations rather than traceability edges. A client reading `true` would "
        "offer a query the server cannot serve, so flip this in the change that "
        "ships the tool, not ahead of it."
    )
    assert result["version"] == __version__, (
        "re-publishes the same `__version__` process constant `theurian "
        "compat check` reads directly for CP-6 "
        "(docs/architecture/requirements-analysis.md) -- that gate never "
        "reads this response, so a mutation here would not make `compat "
        "check` refuse anything. It would only let a client that already "
        "passed the gate see a stale build reported on the MCP face, which "
        "is the drift this pin catches instead."
    )
    assert result["protocolVersion"] == __protocol_version__, (
        "for a client that only calls MCP tools, `system.capabilities` is "
        "the sole place this is readable at all -- `/health` is an HTTP "
        "route outside the MCP tool surface, not a tool in this build. It "
        "re-publishes the same `__protocol_version__` constant "
        "`resolve_compatibility` (theurian.domain.compatibility) reads "
        "directly through `cli/main.py`'s `compat_check`, never through "
        "this response, so a mutation here would not move that gate's "
        "outcome -- it would only make a client that already passed the "
        "gate see a different protocol reported here than the one it was "
        "actually checked against."
    )
    assert "No write-intent tool exists" in result["note"], (
        "the response's only prose statement of ADR-0013 -- ADR-0013 is why "
        "no MCP path reaches approved state. The note's meaning can be "
        "inverted (`No write-intent tool exists` -> `A write-intent tool "
        "exists`) while every other assertion here keeps passing, so this "
        "pins the load-bearing substring rather than the sentence's exact "
        "wording, which is free to change around it."
    )
    assert result["schemaVersion"]


@pytest.mark.asyncio
async def test_the_capability_block_holds_exactly_the_flags_that_are_pinned(
    registry: ProjectRegistry,
) -> None:
    """Pinning every flag is a claim about a population, so the population is pinned too.

    The test above asserts a value per key it knows about, and a parametrized
    assertion over keys nobody enumerated cannot fail for a key that was added
    after it was written. A new capability would ship declared-but-unasserted,
    which is the state `reviewIngestion`, `traceability` and `knowledgeSearch`
    were each found in (#129) -- and a capability flag is a security statement
    when it is `reviewIngestion`, because T-7's threat-model entry cites its
    `false` as what stands in for the repository allowlist.

    So this fails when a flag is added *and* when one is removed, and its message
    says what to do about it. The value of a new flag belongs in the test above;
    this one only insists that a value exists to be argued about.
    """
    result = await _call(registry, "system.capabilities")

    assert set(result["capabilities"]) == {
        "knowledgeSearch",
        "knowledgeGet",
        "hybridRetrieval",
        "raptor",
        "reviewIngestion",
        "traceability",
        "writeTools",
    }, (
        f"system.capabilities declares {sorted(result['capabilities'])}. Each "
        f"entry tells a client what it may rely on, so an unpinned one is a "
        f"promise no test holds the server to: add the new flag to "
        f"`test_capabilities_report_what_is_and_is_not_built` with the value it "
        f"actually holds and the reason it holds it, then add it here."
    )


@pytest.mark.asyncio
async def test_the_system_capabilities_response_holds_exactly_the_keys_that_are_pinned(
    registry: ProjectRegistry,
) -> None:
    """The population one level above the block, which is where `milestone` sat.

    The sibling test above pins the population of the `capabilities` block, but
    `milestone` lived beside it -- a top-level sibling of `version` and
    `schemaVersion`, not an entry inside the block -- so that test could never
    see it. Nothing enumerated the response's own top-level keys, and a
    mutation setting `milestone` to `99` survived the whole suite (#206). This
    is that enumeration, one layer up: it fails when a top-level field is
    added or removed, exactly as the block's own population test does for the
    block.

    `milestone` does not appear here. #206 removed it rather than defining it:
    it was produced in exactly one place and consumed nowhere in this
    repository, `docs/protocol/mcp-tools.md` never defined what it meant, and
    it had drifted stale against the README's own milestone claim since
    Milestone 6 closed. The response's other fields keep their own roles
    instead of filling that gap: `capabilities` (with `knowledgeSearch`) is
    the supported, tested contract a client degrades against; `version` and
    `protocolVersion` re-publish the same two constants `theurian compat
    check` reads directly for CP-6 (docs/architecture/requirements-analysis.md)
    rather than reporting progress -- a milestone number, if a client wants
    one, lives in the README roadmap, not in any wire field.

    This also ties the doc to the pin: the paragraph in
    `docs/protocol/mcp-tools.md` naming each of these fields' roles is read
    below and checked against this same key set, so a field added to one
    without the other fails here too.
    """
    result = await _call(registry, "system.capabilities")

    assert set(result) == {
        "version",
        "protocolVersion",
        "schemaVersion",
        "capabilities",
        "note",
    }, (
        f"system.capabilities returned top-level keys {sorted(result)}. A "
        f"field a client can see is part of the wire contract, so an "
        f"unenumerated one is a promise nothing holds the server to: decide "
        f"what it means, document its role in the `system.capabilities` "
        f'paragraph under "Project and system" in '
        f"docs/protocol/mcp-tools.md, then add it here."
    )

    doc_text = (REPO_ROOT / "docs/protocol/mcp-tools.md").read_text(encoding="utf-8")
    start = doc_text.index("`system.capabilities` exists so a client can degrade")
    end = doc_text.index("### `project.list`", start)
    field_role_paragraph = doc_text[start:end]

    for key in result:
        assert f"`{key}`" in field_role_paragraph, (
            f"docs/protocol/mcp-tools.md's `system.capabilities` paragraph "
            f"does not name `{key}` (backtick-quoted). That paragraph is the "
            f"prose enumeration of this response's top-level fields, so it "
            f"drifts silently from the key set above unless something reads "
            f"both -- name {key}'s role there."
        )


# -- Result shape ----------------------------------------------------------


@pytest.mark.asyncio
async def test_every_result_carries_the_trust_triple_and_an_anchor(
    registry: ProjectRegistry,
) -> None:
    """SEC-15, FR-R5."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="token")
    hit = result["results"][0]

    assert hit["contentClassification"] == "untrusted-knowledge"
    assert hit["mayContainInstructions"] is True
    assert hit["executable"] is False
    assert hit["sourceAnchors"][0]["provider"] == "git"
    assert hit["freshness"]["ageDays"] >= 0
    assert hit["freshness"]["isWithinValidity"] is True


@pytest.mark.asyncio
async def test_a_long_body_is_excerpted_rather_than_returned_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search is a triage tool. Returning full bodies for every hit is how a
    caller's context budget disappears before it reads anything."""
    long_body = "# Long\n\n" + ("sentinel padding " * 200)
    registry = _project_with_body(tmp_path, monkeypatch, long_body)

    result = await _call(registry, "knowledge.search", projectId="demo", query="sentinel")
    excerpt = result["results"][0]["excerpt"]

    assert excerpt.endswith("...")
    assert len(excerpt) <= 283
    assert "\n" not in excerpt


def _register_extra(registry: ProjectRegistry, project_id: str, root: Path) -> None:
    """Add a registry entry directly, for states the CLI will not produce."""
    entries = {**registry.load(), project_id: {"projectId": project_id, "rootPath": str(root)}}
    registry.path.write_text(json.dumps(entries), encoding="utf-8")


def _project_with_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> ProjectRegistry:
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
    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")
    return ProjectRegistry.default(data_dir)


# -- No path from MCP to approved state (ADR-0013) -------------------------

#: The only gateways to a canonical write. ``SqliteWriter`` mutates approved
#: knowledge and can only be built from a connection obtained through
#: ``write_transaction``, so a tool that touches neither name cannot write.
WRITE_GATEWAYS = frozenset({"SqliteWriter", "write_transaction"})


def _mutating_method_names() -> frozenset[str]:
    """Methods that exist only on the writer.

    ``SqliteWriter`` also carries a couple of read helpers so a migration can
    check what it is about to change; those names are shared with the read-only
    store and are not evidence of a write path. The difference between the two
    classes is the surface that mutates approved knowledge.
    """
    from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

    writer = {name for name in vars(SqliteWriter) if not name.startswith("_")}
    reader = {name for name in vars(SqliteCanonicalStore) if not name.startswith("_")}
    return frozenset(writer - reader)


def _referenced_names(function: Any) -> set[str]:
    """Every attribute and global name reachable from a function's code.

    Follows nested code objects, so a write hidden inside a comprehension or an
    inner helper is still visible.
    """
    seen: set[str] = set()
    pending = [function.__code__]
    while pending:
        code = pending.pop()
        seen.update(code.co_names)
        pending.extend(c for c in code.co_consts if hasattr(c, "co_names"))
    return seen


def test_the_write_gateway_still_guards_the_write_surface() -> None:
    """Guards the guard below.

    The structural argument is "no writer, no writes". If the write methods ever
    move onto the read-only store, the test below keeps passing while checking
    nothing.
    """
    mutating = _mutating_method_names()

    assert {"append_revision", "put_item", "record_migration"} <= mutating, (
        "the known write methods must be writer-only; if one moved to the "
        "read-only store, MCP tools can now reach it"
    )


def test_no_registered_tool_can_reach_a_canonical_write(registry: ProjectRegistry) -> None:
    """ADR-0013. The guarantee is structural, not a naming convention.

    A read-only *name* proves nothing: `knowledge.get` could call
    `append_revision` and the tool list would look unchanged. This walks the
    bytecode of every registered tool instead, looking for both the write
    gateways and every individual write method.
    """
    forbidden = WRITE_GATEWAYS | _mutating_method_names()
    server = build_server(registry)
    offenders: dict[str, set[str]] = {}

    # The private manager, deliberately: the public `list_tools` returns wire
    # schemas, and this test needs the callables behind them.
    tools = server._tool_manager.list_tools()
    assert tools, "an empty tool list would pass this test vacuously"

    for tool in tools:
        reached = _referenced_names(tool.fn) & forbidden
        if reached:
            offenders[tool.name] = reached

    assert not offenders, f"tools with a canonical write path: {offenders}"


# -- Cross-project isolation (SEC-13) --------------------------------------


@pytest.fixture
def two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """Two registered projects, each with its own knowledge and its own state.

    One daemon serving many projects is the design (ADR-0002). The property that
    makes it safe is that a call for one cannot observe the other -- and that
    only becomes testable once two really exist.
    """
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))

    for name, secret in (
        ("alpha", "alpha-only-payment-rotation"),
        ("beta", "beta-only-tls-pinning"),
    ):
        root = tmp_path / name
        root.mkdir()
        for args in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

        monkeypatch.chdir(root)
        _run("init")
        (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(
            f"# {name} policy\n\n{secret}\n"
        )
        (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
        _run("project", "register")
        _run("migrate", "apply")

    return ProjectRegistry.default(data_dir)


@pytest.mark.asyncio
async def test_a_query_for_one_project_cannot_observe_the_other(
    two_projects: ProjectRegistry,
) -> None:
    """SEC-13. The isolation is per state database, not a filter someone has to
    remember to apply."""
    alpha = await _call(two_projects, "knowledge.search", projectId="alpha", query="alpha-only")
    leaked = await _call(two_projects, "knowledge.search", projectId="alpha", query="beta-only")

    assert alpha["count"] == 1
    assert leaked["count"] == 0, "alpha must not be able to read beta's knowledge"


@pytest.mark.asyncio
async def test_each_project_sees_its_own_content_under_the_same_item_id(
    two_projects: ProjectRegistry,
) -> None:
    """Both projects use the same itemId. If scoping were wrong anywhere, this
    is where one project's document would surface under the other's name."""
    alpha = await _call(
        two_projects, "knowledge.get", projectId="alpha", itemId="architecture.auth-policy"
    )
    beta = await _call(
        two_projects, "knowledge.get", projectId="beta", itemId="architecture.auth-policy"
    )

    assert "alpha-only-payment-rotation" in alpha["body"]
    assert "beta-only-tls-pinning" in beta["body"]
    assert alpha["body"] != beta["body"]


@pytest.mark.asyncio
async def test_project_list_shows_both_without_revealing_their_knowledge(
    two_projects: ProjectRegistry,
) -> None:
    result = await _call(two_projects, "project.list")

    assert {p["projectId"] for p in result["projects"]} == {"alpha", "beta"}
    assert "alpha-only-payment-rotation" not in json.dumps(result)


# -- Hybrid retrieval through the tool (FR-R2, FR-R4) ------------------------
#
# These exist because a breaking change to this response shape -- replacing the
# flat `note` with a structured `retrieval` block -- passed the entire suite
# without a single failure. Nineteen tests call `knowledge.search`, and every
# one of them went down the substring fallback because no fixture had built an
# index. The ranked path was shipped untested.


@pytest.fixture
def indexed(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` fixture, plus a built retrieval index."""
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("index", "build")
    finally:
        monkey.undo()
    return registry


RECLASSIFY_ID = "01K1RRRRRR01234567890ABCDE"
RECLASSIFY_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {RECLASSIFY_ID}
createdAt: 2026-08-02T13:00:00+09:00
author: engineer@example.com
operations:
  - op: changeSensitivity
    itemId: architecture.auth-policy
    sensitivity: restricted
    reason: Reclassified after review
"""


def _published_index_chunk_sensitivity(root: Path) -> set[str]:
    """The sensitivity the currently published index stamped on the item's chunks."""
    payload = read_active_index_pointer(ProjectPaths.of(root)).payload
    assert payload is not None, "the project must have a published index"
    index = ProjectPaths.of(root).index_for(str(payload["indexBuildId"]))
    with contextlib.closing(sqlite3.connect(index)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT sensitivity FROM chunks WHERE revision_id = ?", (REVISION_ID,)
        ).fetchall()
    return {row["sensitivity"] for row in rows}


@pytest.mark.asyncio
async def test_a_reclassification_shows_in_the_response_before_any_rebuild(
    indexed: ProjectRegistry,
) -> None:
    """After a ``changeSensitivity`` and a ``migrate apply`` -- with NO rebuild --
    a search reports the item's new sensitivity, while the still-published index
    keeps the old label on its chunk rows.

    This is the whole contract for a reclassification, and why it needs no
    auto-rebuild. ``result_payload`` reads the item's current sensitivity, not the
    immutable revision's (SEC-14), so the label a caller sees is correct the
    instant the migration commits -- the assertion on the response's
    ``sensitivity`` holds it. The index column *does* lag: nothing rebuilt it, so
    its chunk still says ``internal``. That lag is asserted to be *present*, not
    absent, because it is harmless: no gate reads a chunk's ``sensitivity`` before
    #119, and an unsigned local index row nothing reads is not a disclosure
    (SEC-7). The column matches canonical again after ``index build`` re-derives
    (``test_forest_builder_scale.py``); until then the response is already right,
    which is what makes the auto-rebuild the migration engine deliberately does
    not do (``test_migration_engine.py``) unnecessary.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    assert _published_index_chunk_sensitivity(root) == {"internal"}, (
        "the fixture's index must be built at the original sensitivity, or the lag below "
        "is not the one this test is about"
    )

    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / f".theurian/migrations/{RECLASSIFY_ID}-reclassify.yaml").write_text(
            RECLASSIFY_MIGRATION
        )
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    hits = [hit for hit in result["results"] if hit["itemId"] == "architecture.auth-policy"]
    assert hits, "the reclassified item must still be found -- it was not withdrawn"
    assert hits[0]["sensitivity"] == "restricted", (
        "the search reported the revision's stale sensitivity, not the item's current "
        "one -- the label decides who may read the content (SEC-14)"
    )
    assert _published_index_chunk_sensitivity(root) == {"internal"}, (
        "the reclassification rebuilt or purged the index -- the point of this test is "
        "that it does neither, and that the response is right anyway"
    )


@pytest.mark.asyncio
async def test_knowledge_get_reports_the_items_current_sensitivity_not_the_revisions(
    registry: ProjectRegistry,
) -> None:
    """SEC-14 on the one call path the ranked-search test above does not reach.

    ``result_payload`` is shared by three call sites, and each threads
    ``item.sensitivity`` -- never ``revision.metadata.sensitivity`` -- into it
    independently: the ranked path (``search.py``'s ``_response``, pinned
    above), ``knowledge.get`` (``tools.py``), and the substring-scan fallback
    (``search.py``'s ``_scan``, pinned below). The test above proves the
    ranked path is item-authoritative; nothing before this one proved
    ``knowledge.get`` is too, so a caller reading back the revision's stale
    label there would have satisfied every test that ran before it. No index
    is needed: ``knowledge.get`` reads the canonical store directly.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    before = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )
    assert before["sensitivity"] == "internal", (
        "the fixture's item must start at the default sensitivity, or the reclassification "
        "below proves nothing"
    )

    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / f".theurian/migrations/{RECLASSIFY_ID}-reclassify.yaml").write_text(
            RECLASSIFY_MIGRATION
        )
        _run("migrate", "apply")
    finally:
        monkey.undo()

    after = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert after["sensitivity"] == "restricted", (
        "knowledge.get reported the revision's stale sensitivity, not the item's current "
        "one -- SEC-14's authority does not hold on this call path"
    )


@pytest.mark.asyncio
async def test_the_substring_scan_reports_the_items_current_sensitivity_not_the_revisions(
    registry: ProjectRegistry,
) -> None:
    """SEC-14 on the unranked fallback, the other call path the ranked-search
    test above does not reach.

    ``registry`` has no index built, so ``knowledge.search`` answers through
    ``substring_answer`` -> ``_scan`` (``search.py``) -- the same fallback
    ``test_search_without_an_index_says_so_rather_than_returning_nothing``
    proves this fixture takes. ``_scan`` threads ``item.sensitivity`` into
    ``result_payload`` independently of the ranked path and of
    ``knowledge.get``; nothing before this test proved it, so a caller reading
    back the revision's stale label there would have satisfied every test that
    ran before it too.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    before = await _call(registry, "knowledge.search", projectId="demo", query="signed token")
    assert before["retrieval"]["mode"] == "substring", (
        "the fixture must answer through the unranked scan, or the reclassification below "
        "does not exercise `_scan`'s call site"
    )
    before_hit = next(
        hit for hit in before["results"] if hit["itemId"] == "architecture.auth-policy"
    )
    assert before_hit["sensitivity"] == "internal", (
        "the fixture's item must start at the default sensitivity, or the reclassification "
        "below proves nothing"
    )

    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / f".theurian/migrations/{RECLASSIFY_ID}-reclassify.yaml").write_text(
            RECLASSIFY_MIGRATION
        )
        _run("migrate", "apply")
    finally:
        monkey.undo()

    after = await _call(registry, "knowledge.search", projectId="demo", query="signed token")

    assert after["retrieval"]["mode"] == "substring"
    hit = next(hit for hit in after["results"] if hit["itemId"] == "architecture.auth-policy")
    assert hit["sensitivity"] == "restricted", (
        "the substring scan reported the revision's stale sensitivity, not the item's "
        "current one -- SEC-14's authority does not hold on this call path"
    )


@pytest.fixture(params=["indexed", "registry"], ids=["ranked", "fallback"])
def either_answer_path(request: pytest.FixtureRequest) -> tuple[ProjectRegistry, bool]:
    """A project with an index, and one without, and which of the two it is.

    A fixture rather than a boolean parameter because `theurian index build`
    embeds through `asyncio.run`, which raises inside an already-running loop —
    so an async test cannot build one in its own body.
    """
    chosen: ProjectRegistry = request.getfixturevalue(request.param)
    return chosen, request.param == "indexed"


@pytest.mark.asyncio
async def test_one_read_of_the_state_pointer_serves_the_whole_request(
    either_answer_path: tuple[ProjectRegistry, bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-R5. `snapshotId` must name the state the results actually came from.

    Every tool used to read `.theurian/state/active.json` twice: once to choose
    the database, and again to report which canonical state answered. `migrate
    apply` swaps that pointer atomically, so a request landing between the two
    read the old database and named the new hash — a false answer to the one
    question the field exists to answer. The second read could also find nothing
    at all, which is why `snapshotId` had to admit `null`.

    Proved by removing the pointer the instant the first read returns, rather
    than by counting calls: a counter only watches the name it was attached to,
    and the read that came back was reached through a different import. Nothing
    can read the file after this, on any path, so a non-empty `snapshotId` means
    the value was carried rather than re-fetched.

    Run on both answer paths. They report the field for the same reason and used
    to fetch it in two different places.
    """
    from theurian.application.project_service import read_active_state
    from theurian.mcp import tools

    registry, built = either_answer_path

    def read_and_remove(paths: Any) -> Any:
        state = read_active_state(paths)
        paths.active_pointer.unlink(missing_ok=True)
        return state

    monkeypatch.setattr(tools, "read_active_state", read_and_remove)

    result = await _call(registry, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["indexed"] is built, "both answer paths must be covered"
    assert result["retrieval"]["snapshotId"], "the state that answered, carried not re-read"


@pytest.mark.asyncio
async def test_search_and_status_name_the_same_canonical_state(
    indexed: ProjectRegistry,
) -> None:
    """The point of publishing `snapshotId` at all.

    A caller holding a `knowledge.status` response must be able to tell whether a
    search answered from that same state without a third call, so the two strings
    have to be the identical value rather than two renderings of one idea.
    """
    status = await _call(indexed, "knowledge.status", projectId="demo")
    search = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert search["retrieval"]["snapshotId"] == status["stateHash"]


@pytest.mark.asyncio
async def test_status_carries_the_state_pointer_rather_than_re_reading_it(
    registry: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-R5. `stateHash` must name the state the counts actually came from.

    `knowledge.status` resolves `.theurian/state/active.json` once, to choose the
    database it counts over. `stateHash` has to be that same resolution and not a
    second read of the pointer: `migrate apply` swaps it atomically, so a request
    that re-read it could count the old database and then name the *new* hash -- a
    false answer to the freshness question the field exists for, and the one
    `knowledge.search` publishes `snapshotId` against
    (`test_search_and_status_name_the_same_canonical_state`).

    Mirrors `test_one_read_of_the_state_pointer_serves_the_whole_request`, which
    holds this for `knowledge.search`: the pointer is removed the instant the
    first read returns, so nothing can read it again on any path. That the
    reported hash is still the right one therefore means it was carried, and
    asserting the exact value rules out a hash that was carried but wrong.
    """
    from theurian.mcp import tools

    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    answered = read_active_state(paths)
    assert answered is not None, "the fixture must have built a canonical state"

    def read_and_remove(request_paths: Any) -> Any:
        state = read_active_state(request_paths)
        request_paths.active_pointer.unlink(missing_ok=True)
        return state

    monkeypatch.setattr(tools, "read_active_state", read_and_remove)

    result = await _call(registry, "knowledge.status", projectId="demo")

    assert result["stateHash"] == str(answered.state_hash), (
        "the pointer a re-read would have found is gone, so a status that still names the "
        "right state carried the resolution its counts came from rather than fetching it again "
        "-- a re-read here would count one database and name another the instant `migrate "
        "apply` lands mid-request (FR-R5)"
    )


@pytest.mark.asyncio
async def test_search_without_an_index_says_so_rather_than_returning_nothing(
    registry: ProjectRegistry,
) -> None:
    """A project that applied migrations but has not indexed yet would otherwise
    answer every query with nothing, which reads as "we have no such decision"
    rather than "ask me again in a moment"."""
    result = await _call(registry, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["indexed"] is False
    assert result["retrieval"]["mode"] == "substring"
    assert "index build" in result["retrieval"]["note"]
    assert result["count"] == 1, "the fallback still answers"


@pytest.mark.asyncio
async def test_search_uses_the_index_once_one_exists(indexed: ProjectRegistry) -> None:
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert result["retrieval"]["indexed"] is True
    assert result["retrieval"]["mode"] in {"hybrid", "lexical", "dense"}
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_a_ranked_hit_says_which_retrievers_found_it(indexed: ProjectRegistry) -> None:
    """A ranking nobody can explain is a ranking nobody can debug."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")
    hit = result["results"][0]

    assert hit["foundBy"], "every hit names the retrievers that surfaced it"
    assert hit["fusedScore"] > 0


@pytest.mark.asyncio
async def test_a_ranked_hit_keeps_the_published_provenance_and_trust_labels(
    indexed: ProjectRegistry,
) -> None:
    """SEC-15, FR-R5. The ranked path resolves hits back through the canonical
    store, so it must not lose the labels the substring path attaches."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")
    hit = result["results"][0]

    assert hit["contentClassification"] == "untrusted-knowledge"
    assert hit["mayContainInstructions"] is True
    assert hit["executable"] is False
    assert hit["sourceAnchors"]
    assert hit["revisionId"]


@pytest.mark.asyncio
async def test_the_index_is_never_the_authority_for_a_result(
    indexed: ProjectRegistry,
) -> None:
    """FR-R5. Results are resolved through the canonical store, so a hit
    reflects the revision as it is now rather than as the index recorded it."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert result["results"][0]["title"] == "Authentication policy"


@pytest.fixture
def indexed_corpus(registry: ProjectRegistry) -> ProjectRegistry:
    """`indexed`, plus two more approved items, then a build.

    A budget test needs something a budget can drop. The `registry` fixture
    holds one approved item, so `count` was always 1 -- which is what made the
    `or count == 1` arm of the assertion below absorb every failure.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        for letter, slug, title in (
            ("C", "retry-policy", "Retry policy"),
            ("D", "quota-policy", "Quota policy"),
        ):
            (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
                f"# {title}\n\nEvery call carries a signed token. This policy explains how "
                f"the token budget is spent, and what the gateway does when it runs out.\n"
            )
            (
                root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{slug}.yaml"
            ).write_text(EXTRA_MIGRATION.format(letter=letter, slug=slug, title=title))
        _run("migrate", "apply")
        _run("index", "build")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_a_token_budget_is_honoured(indexed_corpus: ProjectRegistry) -> None:
    """FR-R4. An agent that receives more context than it asked for has already
    paid for it.

    Asserted as a *difference* between two budgets rather than against a
    constant. `usedTokens <= 40 or count == 1` is satisfied by a `pack` that
    ignores the budget entirely, and by one that reports a used total it never
    enforced -- both of which passed this test.
    """
    generous = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=32_000
    )
    tight = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=40
    )

    assert generous["count"] > 1, "the fixture must offer more than one hit to drop"
    assert generous["retrieval"]["droppedForBudget"] == 0
    assert tight["count"] < generous["count"], "a tight budget must actually drop results"
    assert tight["count"] >= 1, "a budget below the best hit still returns it (pack's floor)"
    # Over budget only via that documented single-result floor.
    if tight["count"] > 1:
        assert tight["retrieval"]["usedTokens"] <= 40


@pytest.mark.asyncio
async def test_the_default_budget_answers_an_ordinary_query_in_full(
    indexed_corpus: ProjectRegistry,
) -> None:
    """FR-R4. The half of `DEFAULT_BUDGET_TOKENS` that bounds it from below.

    A default exists so that omitting the parameter is *safe*, not so that it
    silently truncates. Three short policy documents that all answer the query is
    an ordinary answer to an ordinary question, and a caller who states no budget
    has to receive it whole — otherwise the pipeline's second opinion, which
    `diversify` spends a per-item cap to make room for, is invisible to every
    caller who did not read the parameter list.

    Asserted against the generous answer rather than against a token count, so it
    survives any retuning of `estimate_tokens`: the claim is "the default did not
    cost this caller anything", which is a comparison and not a number.

    `DEFAULT_BUDGET_TOKENS` was moved from 2000 to 200 and the entire suite
    passed. Every other budget test here states its own `maxTokens`, and the
    published default — which is what an agent actually calls with — was checked
    by nothing.
    """
    default = await _call(indexed_corpus, "knowledge.search", projectId="demo", query="token")
    generous = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=32_000
    )

    assert generous["count"] > 1, "the fixture must offer more than one hit to lose"
    assert default["results"] == generous["results"], (
        "a caller who stated no budget must not be answered with less than the corpus holds"
    )
    assert default["retrieval"]["droppedForBudget"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [32_000, 200], ids=["generous", "tight"])
async def test_the_reported_cost_is_measured_on_the_payload_the_caller_receives(
    indexed_corpus: ProjectRegistry, max_tokens: int
) -> None:
    """FR-R4. A budget the caller cannot verify is a number, not a promise.

    The ranked path used to price a *chunk* — the unit it retrieves and pins —
    while sending a full result payload. The excerpt is capped at 280
    characters; provenance, the trust triple, `sourceAnchors` and SAFETY are
    not, and none of them was counted. Measured on a ten-document project,
    `maxTokens=500` reported 486 and sent 1,953: 9.2x over at the tightest
    budget. Every existing budget assertion compared the implementation's number
    against itself, so all of them held.

    Two assertions, deliberately different in kind. The first pins *what* is
    priced: the serialised result, exactly as the caller receives it. The second
    holds whatever `estimate_tokens` is later retuned to — a caller charged N
    tokens must not be sent more than `CHARS_PER_TOKEN * N` characters — so this
    test cannot be satisfied by changing the estimator to agree with a wrong
    total.

    Run at both ends of the budget because the tight case is the one that
    matters and the one that was worst: it is where truncation silently eats the
    caller's own instructions.
    """
    from theurian.domain.ranking import CHARS_PER_TOKEN, estimate_tokens

    result = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=max_tokens
    )
    sent = [json.dumps(hit, ensure_ascii=False) for hit in result["results"]]

    assert result["retrieval"]["indexed"] is True, "this must exercise the ranked path"
    assert sent, "a response with no results would satisfy any accounting"
    assert result["retrieval"]["usedTokens"] == sum(estimate_tokens(one) for one in sent)
    assert result["retrieval"]["usedTokens"] * CHARS_PER_TOKEN >= sum(len(one) for one in sent), (
        "the caller was charged for less than was sent to them"
    )


@pytest.mark.asyncio
async def test_the_budget_covers_the_whole_message_and_not_only_the_results(
    indexed_corpus: ProjectRegistry,
) -> None:
    """FR-R4. What arrives in the caller's window is the response, not the hits.

    `projectId`, the echoed `query`, `count` and the whole `retrieval` block —
    the `note` above all, which is a paragraph of prose — travel with every
    answer and were charged to nobody. Measured at 138 to 171 tokens of fixed
    overhead, so `maxTokens=100` sent 302 to 352.

    The budget is calibrated from the run itself rather than written as a
    constant: `maxTokens` is set to exactly what the results alone cost, which is
    the budget the previous accounting accepted in full while sending the
    envelope on top of it. A response that still fits that number is one that
    charged for the envelope.
    """
    from theurian.domain.ranking import estimate_tokens

    generous = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=32_000
    )
    results_alone = int(generous["retrieval"]["usedTokens"])
    tight = await _call(
        indexed_corpus,
        "knowledge.search",
        projectId="demo",
        query="token",
        maxTokens=results_alone,
    )

    assert generous["count"] > 1, "the fixture must offer more than one hit to drop"
    assert tight["retrieval"]["droppedForBudget"] >= 1, (
        "the results alone exactly filled this budget, so the envelope must displace one"
    )
    assert estimate_tokens(json.dumps(tight, ensure_ascii=False)) <= results_alone


@pytest.mark.asyncio
async def test_the_envelope_never_starves_the_answer_completely(
    indexed_corpus: ProjectRegistry,
) -> None:
    """The floor `take_within_budget` promises, held through the new reservation.

    A budget smaller than the envelope leaves nothing to spend, and the naive
    subtraction returns zero or negative — which would turn "your budget is
    small" into "we have no such decision", the failure the whole fallback exists
    to prevent. One over-long answer a caller can truncate beats an empty one
    they cannot act on.
    """
    starved = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token", maxTokens=1
    )

    assert starved["count"] == 1
    assert starved["retrieval"]["usedTokens"] > 0


#: One document long enough to split into several chunks, every one of which
#: contains `gateway`. Without it the per-item cap cannot be observed at this
#: layer: every other document here yields exactly one chunk, so "no item appears
#: twice" is true whether or not the cap runs.
LONG_BODY = (
    "# Gateway runbook\n\nThis runbook records what the gateway does under load.\n\n"
) + "".join(
    f"## {section}\n\nThe gateway {section.lower()} procedure is rehearsed each quarter. "
    f"It states what the gateway requires, who owns the gateway path, and when the "
    f"gateway behaviour is next revisited. Nothing here changes without a migration.\n\n"
    for section in ("Escalation", "Retention", "Exceptions", "Review cadence")
)


@pytest.fixture
def indexed_long_document(registry: ProjectRegistry) -> ProjectRegistry:
    """`registry`, plus one multi-chunk document, then a build."""
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / ".theurian/knowledge/architecture/gateway-runbook.md").write_text(LONG_BODY)
        (root / ".theurian/migrations/01K1EAAAAA01234567890ABCDE-gateway.yaml").write_text(
            EXTRA_MIGRATION.format(letter="E", slug="gateway-runbook", title="Gateway runbook")
        )
        _run("migrate", "apply")
        _run("index", "build")
    finally:
        monkey.undo()
    return registry


@pytest.mark.asyncio
async def test_one_result_per_document_however_many_chunks_matched(
    indexed_long_document: ProjectRegistry,
) -> None:
    """`knowledge.search` returns documents, so a document appears once.

    The cap lives in the `per_item=1` this tool passes to the ranking, and
    nothing at this layer noticed when it moved: raising it to 2 left 96 tests
    green while a second chunk of the same document appeared in `results` — two
    hits with one `itemId`, two shares of the caller's budget, and a `count` that
    no longer counts documents.
    """
    result = await _call(
        indexed_long_document, "knowledge.search", projectId="demo", query="gateway", limit=10
    )
    items = [hit["itemId"] for hit in result["results"]]

    assert "architecture.gateway-runbook" in items, "the multi-chunk document must be found"
    assert items.count("architecture.gateway-runbook") == 1, (
        "one result per document, not one per chunk"
    )
    assert len(items) == len(set(items))


def test_the_cap_is_the_only_reason_the_long_document_appears_once(
    indexed_long_document: ProjectRegistry,
) -> None:
    """Guards the guard above.

    "It appears once" is satisfied by a corpus that could only ever have produced
    one chunk of it. Asked for two per item, the same query against the same
    index must return the same document twice, or the assertion above is about
    the fixture rather than about the cap.

    **Synchronous**, and at the ranking layer: this is the state the tool must
    not publish, so it is measured one layer below the tool.
    """
    from theurian.application.retrieval_service import RetrievalService, SearchRequest
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(indexed_long_document.load()["demo"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    outcome = RetrievalService(SqliteIndexStore(built)).search(
        SearchRequest(query="gateway", project_id="demo", per_item=2), NOTHING_WITHHELD
    )

    items = [candidate.item_id for candidate in outcome.candidates]
    assert items.count("architecture.gateway-runbook") == 2, (
        "the index must hold a second matching chunk for the cap to have work to do"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["token\x00policy", "token\ud800policy"])
async def test_a_query_containing_an_untransportable_character_does_not_raise(
    indexed: ProjectRegistry, query: str
) -> None:
    """A search box must not raise at the user.

    `_to_match_expression` quotes every term, so the inputs that still reach the
    driver malformed are the ones that cannot become a NUL-terminated UTF-8
    string: a NUL, which ends the C string early and makes FTS5 report
    `unterminated string`; and a lone surrogate, which cannot be encoded at all
    and fails as a `UnicodeEncodeError` before SQLite is called. Both used to
    reach the client as a tool failure. JSON-RPC can carry either.
    """
    result = await _call(indexed, "knowledge.search", projectId="demo", query=query)

    assert "results" in result


#: A document whose answer is nowhere near its head.
#:
#: The first section is long enough to fill an excerpt on its own and never says
#: `quarantine`; the last section is the only place that word appears. That
#: separation is the whole point: with a corpus of short documents the head of
#: the body *is* the matched passage, so an excerpt taken from either place
#: reads the same and the replacement below cannot be observed.
HEAD_MARKER = "every inbound call carries a signed token"
TAIL_MARKER = "the gateway quarantines the tenant"
LAYERED_BODY = (
    f"# Authentication policy\n\nThe gateway is the only trust boundary, so "
    f"{HEAD_MARKER} and the signature is verified before any handler runs. "
    + (
        "This paragraph exists to fill the first passage with prose that does "
        "not answer the question being asked. " * 6
    )
    + f"\n\n## Compromise handling\n\nWhen a signing key leaks, {TAIL_MARKER} "
    f"and every credential minted under that key is revoked immediately.\n"
)


@pytest.fixture
def layered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """One document whose answer is in its last section, and a built index.

    **Synchronous**, and that is not a style choice. `theurian index build`
    embeds through `asyncio.run`, which raises inside an already-running loop,
    so an async test cannot build an index in its own body.
    """
    registry = _project_with_body(tmp_path, monkeypatch, LAYERED_BODY)
    _build_index(registry)
    return registry


@pytest.mark.asyncio
async def test_the_excerpt_is_the_passage_that_matched_not_the_head_of_the_document(
    layered: ProjectRegistry,
) -> None:
    """Chunking buys ranking precision; this is what delivers it to the caller.

    Retrieval ranks *chunks*, so a hit already knows which paragraph answered
    the query. Returning the head of the document instead throws that away: the
    caller sees an introduction, cannot tell why the document was returned, and
    pays a `knowledge.get` to find out — which is the whole cost chunking was
    supposed to avoid.

    Both halves are asserted. "The matched passage is present" alone is
    satisfied by returning the entire body; "the head is absent" alone is
    satisfied by returning nothing at all.
    """
    result = await _call(layered, "knowledge.search", projectId="demo", query="quarantines")
    excerpt = result["results"][0]["excerpt"]

    assert TAIL_MARKER in excerpt, "the caller must see the paragraph that matched"
    assert HEAD_MARKER not in excerpt, "not the head of the document it came from"


@pytest.mark.asyncio
async def test_the_unranked_fallback_still_excerpts_from_the_head(
    layered: ProjectRegistry,
) -> None:
    """The control, and a published difference between the two answer paths.

    Without an index there are no chunks, so the fallback has nothing but the
    document to excerpt from and says the head. That is what makes the ranked
    path's excerpt a real replacement rather than a coincidence of this corpus:
    the same document, the same query, a different excerpt.
    """
    root = Path(layered.load()["demo"]["rootPath"])
    (root / ".theurian/state/active-index.json").unlink()

    result = await _call(layered, "knowledge.search", projectId="demo", query="quarantines")
    excerpt = result["results"][0]["excerpt"]

    assert result["retrieval"]["indexed"] is False
    assert HEAD_MARKER in excerpt, "with no chunk to point at, the head is all there is"


def _build_index(registry: ProjectRegistry) -> None:
    """Run `theurian index build` inside a registered project's root."""
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("index", "build")
    finally:
        monkey.undo()


@pytest.mark.asyncio
async def test_drafts_stay_out_of_ranked_results_by_default(
    indexed: ProjectRegistry,
) -> None:
    result = await _call(indexed, "knowledge.search", projectId="demo", query="caching")

    assert all(hit["status"] == "approved" for hit in result["results"])


@pytest.mark.asyncio
async def test_a_pointer_to_a_missing_index_falls_back_instead_of_failing(
    indexed: ProjectRegistry,
) -> None:
    """The index is derived. A pointer that outlived its file is a missing
    optimisation, never a reason to refuse to answer."""
    root = Path(indexed.load()["demo"]["rootPath"])
    for built in (root / ".theurian/state").glob("theurian-index-*.sqlite"):
        built.unlink()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["indexed"] is False
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_a_deprecated_item_stops_being_returned_even_from_a_stale_index(
    indexed: ProjectRegistry,
) -> None:
    """The index's status is a build-time snapshot; the canonical store is the
    authority for what is approved *now*.

    Without a live re-check, deprecating a decision leaves it retrievable until
    someone remembers to rebuild — which is the system returning knowledge the
    team has explicitly retired.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    (root / ".theurian/migrations/01K1CAAAAA01234567890ABCDE-deprecate.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1CAAAAA01234567890ABCDE
createdAt: 2026-08-03T12:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.auth-policy
    reason: superseded by the new gateway design
"""
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert all(hit["status"] == "approved" for hit in result["results"])


@pytest.mark.asyncio
async def test_a_stale_index_says_so_in_the_response(indexed: ProjectRegistry) -> None:
    """Only the CLI knew about staleness, and the MCP client is the one acting
    on the answer.

    Applying a migration is what makes the index stale: editing a knowledge file
    alone leaves the *database* equally out of date, so the index still matches
    what the database holds.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    (root / ".theurian/knowledge/architecture/caching-draft.md").write_text(
        DRAFT_BODY + "\n\nAn additional paragraph that changes the state hash.\n"
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert result["retrieval"]["stale"] is True
    assert "index build" in result["retrieval"]["note"]


@pytest.mark.asyncio
async def test_include_unapproved_falls_back_when_the_index_holds_no_drafts(
    indexed: ProjectRegistry,
) -> None:
    """An index built without `--include-unapproved` cannot answer this.

    Indexing everything and filtering at query time was tried and reverted: it
    made one boolean reach content the team decided must not be followed, and it
    removed the operator's ability to guarantee no draft is in the file. Falling
    back answers the question honestly instead of returning approved-only
    results under a parameter that asked for more.
    """
    result = await _call(
        indexed,
        "knowledge.search",
        projectId="demo",
        query="caching",
        includeUnapproved=True,
    )

    assert result["retrieval"]["indexed"] is False
    assert any(hit["status"] == "draft" for hit in result["results"])


@pytest.mark.asyncio
async def test_the_response_says_whether_the_index_holds_unapproved_content(
    indexed: ProjectRegistry,
) -> None:
    """So an empty answer to `includeUnapproved=True` is distinguishable from
    "there are no drafts"."""
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert result["retrieval"]["indexesUnapproved"] is False


@pytest.mark.asyncio
async def test_an_absurd_token_budget_is_clamped_rather_than_raised(
    indexed: ProjectRegistry,
) -> None:
    """A caller asking for a million tokens means "as much as you have".
    Answering with an exception that names an internal parameter helps nobody.

    **The second assertion is weaker than it reads, and is left as a floor
    rather than mistaken for a bound.** This corpus answers in 609 tokens, so
    `usedTokens <= 32_000` holds at any `MAX_BUDGET_TOKENS` above that — and
    holds with the clamp deleted outright. Measured: the constant was raised to
    10,000,000 and all 1,261 tests passed. Nothing in this suite can bind it,
    because `MAX_RESULTS` caps a response at fifty payloads of roughly 200
    tokens — an excerpt is capped at `results.EXCERPT_CHARS` — which is a third
    of the cap. Reaching it needs a corpus of fifty CJK documents, whose
    280-character excerpts cost about 420 tokens each; until one exists, the
    real bound on a response is `MAX_RESULTS`, which is pinned by
    `test_a_caller_may_have_the_fifty_results_the_tool_promises`.
    """
    zero = await _call(indexed, "knowledge.search", projectId="demo", query="token", maxTokens=0)
    huge = await _call(
        indexed, "knowledge.search", projectId="demo", query="token", maxTokens=99_000_000
    )

    assert zero["count"] >= 1
    assert huge["retrieval"]["usedTokens"] <= 32_000


# -- What a caller's own query may cost them (SEC-8, FR-R4) ------------------
#
# `mcp.tools.MAX_QUERY_CHARS` is a published contract value, not a knob: the
# response schema states `maxLength: 2000` on `query` with the reason, and
# `test_a_real_search_response_validates_against_its_published_schema
# [unranked-overlong-query]` fails if the constant moves. So the *number* is
# pinned, and nothing below restates it.
#
# What the schema cannot hold is the two claims that make the number safe, and
# neither was tested. Both were confirmed reachable by mutation:
#
#   - `infrastructure.sqlite.index_query.MAX_QUERY_CHARS` lowered to 500: a
#     query whose question sat 1,400 characters in came back `count: 0` while
#     the response echoed all 1,907 characters of it. The schema calls that
#     field "the string that was actually searched for"; it would have been a
#     string containing a term nobody searched for. 1,260 tests passed.
#   - `mcp.tools.MAX_QUERY_CHARS` raised to 8,000: at the default budget the
#     caller's own query displaced two of their three results, because the
#     echoed query is charged to them through `_envelope_tokens`.


@pytest.mark.asyncio
async def test_a_term_the_response_echoes_is_a_term_that_was_searched_for(
    indexed: ProjectRegistry,
) -> None:
    """SEC-8. Two bounds, one string — the invariant that keeps the echo honest.

    The query is truncated twice by two constants that happen to be equal: once
    at the tool boundary, which decides what is *echoed*, and again in
    `_query_terms`, which decides what is *searched*. If the retrieval one were
    ever the smaller, every response would keep saying "query" of a string it
    had only partly read, and a caller debugging a missing result would be
    reading the wrong evidence.

    **The probe is placed from the observed boundary, not from either
    constant.** One call with an absurd query reveals where the echo is cut;
    the real probe then puts its question just inside that point. That is what
    makes it catch the divergence from *either* side, verified both ways: the
    retrieval bound lowered to 500, and the tool bound raised to 8,000 — which
    moves the observed cut, so the probe follows it out past the retrieval one.

    The shape it cannot see is the two moving together. Both at 8,000 leaves
    this green, because the echo and the search still agree; what that breaks is
    the caller's budget, which is the test below, and the published `maxLength`,
    which is the wire-contract suite.
    """
    absurd = await _call(indexed, "knowledge.search", projectId="demo", query="z" * 50_000)
    cut = len(absurd["query"])
    probe = "z" * (cut - 20) + " signed"

    answer = await _call(indexed, "knowledge.search", projectId="demo", query=probe)

    assert cut < 50_000, "the boundary must truncate at all, or the probe measures nothing"
    assert answer["query"] == probe, "the whole probe must be echoed, including its question"
    assert [hit["itemId"] for hit in answer["results"]] == ["architecture.auth-policy"], (
        "a term the caller can read back in `query` must be one the search actually spent"
    )


@pytest.mark.asyncio
async def test_a_query_at_the_boundary_does_not_cost_the_caller_their_answer(
    indexed_corpus: ProjectRegistry,
) -> None:
    """FR-R4. The caller's own words are charged to the caller's own budget.

    `_envelope_tokens` prices the echoed query and reserves it from `maxTokens`,
    which is right — it is sent to them — but it means the cap on a query is
    also a cap on how much of a defaulting caller's answer their question may
    eat. At 2,000 characters that is about 500 tokens of the 2,000-token
    default, and all three results still fit. Measured: 4,000 still fits this
    corpus, 6,000 does not, and at 8,000 the same query returns one result and
    reports two dropped. So it is a band, and a corpus with more to say would
    narrow it — a caller's question is charged against their whole answer, not
    against a fixed share of it.

    So this is the constraint the schema's `maxLength` does not express — the
    query cap and `DEFAULT_BUDGET_TOKENS` are not independent, and raising the
    first is a change to what the second delivers. No `maxTokens` is passed,
    because the caller who is hurt is the one who never thought about it.

    The query is sent absurdly long rather than at the published length, so the
    boundary clamps it and the test needs no constant to know where that is.
    """
    short = await _call(indexed_corpus, "knowledge.search", projectId="demo", query="token")
    maximal = await _call(
        indexed_corpus, "knowledge.search", projectId="demo", query="token " + "z" * 50_000
    )

    assert short["count"] > 1, "the fixture must have an answer long enough to be cut into"
    assert len(maximal["query"]) < 50_000, "the boundary must clamp, or this measures nothing"
    assert [hit["itemId"] for hit in maximal["results"]] == [
        hit["itemId"] for hit in short["results"]
    ], "a question at the published maximum must not displace the answer to it"
    assert maximal["retrieval"]["droppedForBudget"] == 0


@pytest.mark.asyncio
async def test_a_superseded_revision_is_not_served_from_a_stale_index(
    indexed: ProjectRegistry,
) -> None:
    """The leak this guard exists for.

    Replacing a revision is *how* a secret gets removed from approved
    knowledge. The index pins a revision id at build time, so without this
    check a stale index keeps answering with the very text the team just
    retracted — and labels it with the new revision's `approved` status.

    A stale index therefore returns fewer results rather than wrong ones.
    """
    root = Path(indexed.load()["demo"]["rootPath"])
    (root / ".theurian/knowledge/architecture/auth-v2.md").write_text(
        "# Authentication policy\n\nThe key now lives in the secret store.\n"
    )
    (root / ".theurian/migrations/01K1EAAAAA01234567890ABCDE-replace.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1EAAAAA01234567890ABCDE
createdAt: 2026-08-03T14:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1EREVAA01234567890ABCDE
    expectedRevision: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-v2.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-v2.md
"""
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert all(hit["revisionId"] != "01K1AAAREV01234567890ABCDE" for hit in result["results"]), (
        "the retracted revision must not be served"
    )
    assert result["retrieval"]["stale"] is True


@pytest.mark.asyncio
async def test_what_a_stale_index_withheld_is_not_reported(indexed: ProjectRegistry) -> None:
    """`withheldSuperseded` was a per-query count of documents that matched the
    caller's terms and that the caller was not allowed to read.

    It was added so zero results would not read as "we have no such decision".
    It bought that at the price of a truth oracle, and the trigram retriever
    matches any substring of three characters, so the oracle extracts rather
    than merely detects. `stale` says the same useful thing — your index is
    behind, expect fewer results — and says it identically for every query.
    """
    result = await _call(indexed, "knowledge.search", projectId="demo", query="signed token")

    assert "withheldSuperseded" not in result["retrieval"]
    assert result["retrieval"]["stale"] is False


# -- The extraction oracle, closed and kept closed (SEC-13, T-15, T-17) -------
#
# T-17 in `docs/security/threat-model.md` is the threat these tests discharge,
# and it is its own entry rather than a note on T-15 for a reason worth knowing
# while reading them: T-15 covers a secret being committed *into* approved
# knowledge, and this attacks T-15's own remediation. Superseding a revision is
# the documented way to get a secret out, and the window right after performing
# that redaction was the window the plaintext could be read back — through a
# different tool call, with no flag.
#
# T-17 records both extraction paths, re-measured against the pre-fix code:
# 257 ordinary `knowledge.search` calls recovered a 20-character credential
# through a superseding revision, and 215 recovered a 13-character one through
# `deprecateItem` — where `withheldSuperseded` never moved and `usedTokens` was
# the only channel. Both paths are parametrized below for exactly that reason:
# closing one says nothing about the other.
#
# The invariant asserted here is deliberately stronger than "extraction fails",
# which is a property of one attack strategy and can hold of a channel that is
# merely narrow. It is: **a query matching only withheld content must be
# indistinguishable, in the content of the response, from a query matching
# nothing at all.** The two `retrieval` blocks are compared whole, so every
# field is covered by construction and a field added later is covered the day it
# is added.
#
# **In content, and only in content.** T-17 records a residual these tests do
# not close and must not be read as closing: timing. A withheld candidate still
# costs a canonical-store lookup before it is dropped, so latency stays weakly
# correlated with whether a query matched withheld content. That is inherent to
# re-checking against the canonical store on every call — the same re-check that
# closes T-15's window — rather than something this fix chose to leave.

#: A string that appears in exactly one document and in no English word list,
#: so a hit is unambiguous and a miss is not a tokenizer accident.
LEAKED_CREDENTIAL = "ZQXJVBWKPLMNTRD9"

#: A query that matches nothing, in the same shape as `LEAKED_CREDENTIAL`. The comparison
#: is only meaningful if the control query is equally exotic -- a common word
#: would differ in `retrieval` for honest reasons.
NO_SUCH_TERM = "QQZZXNOSUCHVALUE7"

#: The dense retriever cannot score `LEAKED_CREDENTIAL`. Measured: the bundled embedder is
#: a hashed character n-gram vectoriser, and a random sixteen-character token
#: shares almost no n-gram mass with prose, so it sits below the 0.25 similarity
#: floor and the dense retriever contributes nothing at all. A probe that never
#: reaches the retriever cannot demonstrate anything about the field that
#: retriever reports, so the dense case gets a query the retriever can actually
#: score -- measured at 0.773 against the withheld chunk and below the floor for
#: everything else in the corpus.
DENSE_PROBE = "tenant quarantine playbook rehearsal"
#: Unrelated to every document here, on every retriever. The control has to be
#: as scoreable as the probe and match nothing, or the comparison is between two
#: different kinds of query rather than between two answers.
DENSE_CONTROL = "kubernetes scheduler bin packing"

#: ``(id, probe, control, useDense)``. The probe matches only the withheld
#: document; the control matches nothing.
PROBES = (
    ("the-secret-itself", LEAKED_CREDENTIAL, NO_SUCH_TERM, False),
    ("a-phrase-the-vector-retriever-can-score", DENSE_PROBE, DENSE_CONTROL, True),
)

RUNBOOK_BODY = (
    f"# Tenant quarantine playbook\n\nWhen the shared gateway credential "
    f"{LEAKED_CREDENTIAL} leaks, the responder rehearses this quarantine playbook, "
    f"isolates the affected tenant, and records the rehearsal in the "
    f"quarantine ledger before escalating.\n"
)

RUNBOOK_TITLE = "Tenant quarantine playbook"

#: The one document both withholding stories retire, so both write the same
#: migration. ``title`` is the only hole in it, and it is a hole because the
#: title is prepended to the body before chunking: an English title on the
#: Japanese depth corpus would hand the word index tokens no other document in
#: that corpus has, and the word index having nothing to say is the point of
#: that corpus.
RUNBOOK_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1FAAAAA01234567890ABCDE
createdAt: 2026-08-02T13:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.runbook
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.runbook
    revisionId: 01K1FAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/runbook.md
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
          sourceUri: git://demo/runbook.md
"""

#: The remediation itself: a new revision with the credential removed. The
#: window this opens is the ordinary state between `migrate apply` and `index
#: build` -- performing the fix is what makes the old text readable from the
#: index, which is why the oracle mattered.
REDACTION_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1GAAAAA01234567890ABCDE
createdAt: 2026-08-03T15:00:00+09:00
author: engineer@example.com
operations:
  - op: upsertRevision
    itemId: architecture.runbook
    revisionId: 01K1GREVAA01234567890ABCDE
    expectedRevision: 01K1FAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/runbook-v2.md
    metadata:
      title: Tenant quarantine playbook
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/runbook-v2.md
"""

#: The other way content leaves approved knowledge. Covered alongside the
#: redaction because they take different branches out of `ResultGate` -- one
#: fails the status gate, the other the current-revision check -- and closing one
#: says nothing about the other.
DEPRECATION_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1GAAAAA01234567890ABCDE
createdAt: 2026-08-03T15:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.runbook
    reason: the credential was pasted into it
"""


def _supersede(root: Path) -> None:
    (root / ".theurian/knowledge/architecture/runbook-v2.md").write_text(
        "# Tenant quarantine playbook\n\nThe gateway credential now lives in the secret store.\n"
    )
    (root / ".theurian/migrations/01K1GAAAAA01234567890ABCDE-redact.yaml").write_text(
        REDACTION_MIGRATION
    )


def _deprecate(root: Path) -> None:
    (root / ".theurian/migrations/01K1GAAAAA01234567890ABCDE-deprecate.yaml").write_text(
        DEPRECATION_MIGRATION
    )


@pytest.fixture(params=[_supersede, _deprecate], ids=["superseded", "deprecated"])
def withheld(registry: ProjectRegistry, request: pytest.FixtureRequest) -> ProjectRegistry:
    """A secret indexed while approved, then withheld, with the index left stale.

    **Synchronous**, because `theurian index build` embeds through
    `asyncio.run`, which raises inside an already-running loop.

    The index is deliberately *not* rebuilt, and after issue #15 that is the
    *residual* state rather than the steady one: a withdrawal now purges the
    published build (ADR-0024 decision 5), so the window in which it still holds
    the withheld rows is a request already in flight or a purge that failed --
    exactly where the canonical gate is the only defense. The withdrawal is
    applied with the pointer withheld so the purge acts on nothing, reproducing
    that residual (`_apply_leaving_the_stale_build_published`); the shipped close
    of the non-residual case is proved in `test_absence_proof.py`.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / ".theurian/knowledge/architecture/runbook.md").write_text(RUNBOOK_BODY)
        (root / ".theurian/migrations/01K1FAAAAA01234567890ABCDE-runbook.yaml").write_text(
            RUNBOOK_MIGRATION.format(title=RUNBOOK_TITLE)
        )
        _run("migrate", "apply")
        _run("index", "build")

        request.param(root)
        _apply_leaving_the_stale_build_published(root)
    finally:
        monkey.undo()
    return registry


@pytest.mark.parametrize(
    ("probe", "control", "use_dense"), [p[1:] for p in PROBES], ids=[p[0] for p in PROBES]
)
def test_the_stale_index_still_ranks_the_withheld_document(
    withheld: ProjectRegistry, probe: str, control: str, use_dense: bool
) -> None:
    """Guards the guard: proves each probe reaches the branch under test.

    If the index no longer ranked the withheld document, "a query for it looks
    like a query for nothing" would be true because there was nothing to
    withhold — the fixture, not the fix, would be doing the work. That is not
    hypothetical here: the dense retriever cannot score `LEAKED_CREDENTIAL` at all, so the
    `useDense` case was silently vacuous until this fixed it.

    Asserted against the ranked candidate list, one layer below the canonical
    store that does the withholding, and with the same `useDense` the response
    test uses.
    """
    from theurian.application.retrieval_service import RetrievalService, SearchRequest
    from theurian.infrastructure.embedding import HashingEmbedding
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(withheld.load()["demo"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    service = RetrievalService(SqliteIndexStore(built), HashingEmbedding())

    found = service.search(
        SearchRequest(query=probe, project_id="demo", use_dense=use_dense), NOTHING_WITHHELD
    )
    nothing = service.search(
        SearchRequest(query=control, project_id="demo", use_dense=use_dense), NOTHING_WITHHELD
    )

    assert [c.item_id for c in found.candidates] == ["architecture.runbook"], (
        "the probe must still rank the retracted document, and only it"
    )
    assert nothing.candidates == (), "and the control must reach no candidate at all"
    if use_dense:
        assert "dense" in found.candidates[0].found_by, (
            "the dense retriever must be one of the retrievers that surfaced it, "
            "or `embeddingModel` is not a channel this probe can open"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe", "control", "use_dense"), [p[1:] for p in PROBES], ids=[p[0] for p in PROBES]
)
async def test_a_query_matching_only_withheld_content_is_indistinguishable_from_no_match(
    withheld: ProjectRegistry, probe: str, control: str, use_dense: bool
) -> None:
    """SEC-13, T-15, T-17. The whole `retrieval` block, compared field by field.

    Not "the secret is absent from the response" — it was already absent, and
    the leak was never the text. It was that ``count: 0, results: [],
    usedTokens: 46`` *states* that something matched and may not be read, which
    is the truth oracle sequential extraction needs.

    ``useDense=True`` is covered because ``embeddingModel`` was a fourth channel
    on top of the count, the token total and the mode: it used to be reported
    empty when the dense retriever found nothing, so it flipped on a withheld
    dense hit — one bit per query, and no flag required to read it.

    The claim is about the *content* of the response and nothing else. T-17
    records timing as a standing residual: a withheld candidate still costs a
    canonical-store lookup, so latency remains weakly correlated. Nothing here
    measures or asserts anything about how long the two calls took, and a reader
    must not take equality of these two blocks as a claim that they are
    indistinguishable in every respect.
    """
    withheld_only = await _call(
        withheld, "knowledge.search", projectId="demo", query=probe, useDense=use_dense
    )
    no_match = await _call(
        withheld, "knowledge.search", projectId="demo", query=control, useDense=use_dense
    )

    assert withheld_only["count"] == 0
    assert withheld_only["results"] == []
    assert withheld_only["retrieval"] == no_match["retrieval"], (
        "every field that differs here is a channel an attacker reads one bit at a time"
    )


@pytest.mark.asyncio
async def test_nothing_derived_from_the_withheld_document_is_reported(
    withheld: ProjectRegistry,
) -> None:
    """SEC-13, T-17, stated positively so a reader knows what the fields may say.

    Every number in `retrieval` is computed from the results the caller actually
    receives; everything else is a property of the index or of the caller's own
    parameters. `stale` is the deliberate exception and the reason
    `withheldSuperseded` could be deleted rather than merely hidden: it says
    "your index is behind, expect fewer results" identically for every query.

    `droppedForBudget` is asserted at zero for its own reason. A withheld hit is
    not "dropped for budget" — counting it there would move the leak one field
    to the left rather than close it.
    """
    result = await _call(withheld, "knowledge.search", projectId="demo", query=LEAKED_CREDENTIAL)
    retrieval = result["retrieval"]

    assert retrieval["usedTokens"] == 0, "nothing was sent, so nothing was charged"
    assert retrieval["droppedForBudget"] == 0, "a withheld hit is not 'dropped for budget'"
    assert retrieval["mode"] == "none", "the mode names the retrievers behind the *results*"
    assert "withheldSuperseded" not in retrieval
    assert retrieval["stale"] is True, "the query-independent half is still told"


# -- The same oracle, in the input space the probes above cannot reach --------
#
# Every probe above matches the withheld document *and nothing else*, so both
# sides of the comparison answer `count: 0` and every field agrees — which stayed
# true while the caller's `limit` was still being applied to *candidates*, and a
# withheld candidate was still taking a result slot from a visible one.
#
# The channel needs both halves: a query matching withheld content **and** at
# least `limit` documents the caller may see. Then the withheld hit displaces the
# last visible one, `count` drops by one, and the oracle is open again — measured
# at 203 ordinary `knowledge.search` calls to recover a sixteen-character
# credential, cheaper than the 257 the probes above were written against.
#
# The invariant is unchanged and the assertion is stronger: the **whole
# response** must be identical, not merely the `retrieval` block. `fusedScore`
# lives in `results`, and it was a second oracle of exactly the same shape — RRF
# scores `1 / (k + rank)`, so a withheld chunk ranked above a visible one pushed
# that visible one's published score from 0.032787 to 0.032258. Four visible
# hits, four numbers, all moving together: a finer read than `count`, because it
# also says which rank the withheld document took.

#: A control the same length as the probe. `retrieval` no longer depends on what
#: a query *matched*, but the response does echo the query and the caller is
#: charged for the envelope that carries it, so a control of a different length
#: would differ from the probe for an honest reason and blunt the comparison.
CROWD_CONTROL = "QQZZXNOSUCHVAL7X"

#: How many approved documents the crowding probe also matches. Enough that the
#: displacement is visible at several `limit` values rather than at one.
CROWD = 4


def _without_query(payload: dict[str, Any]) -> dict[str, Any]:
    """Everything a caller receives except the string they sent."""
    return {key: value for key, value in payload.items() if key != "query"}


@pytest.fixture(params=[_supersede, _deprecate], ids=["superseded", "deprecated"])
def crowded(registry: ProjectRegistry, request: pytest.FixtureRequest) -> ProjectRegistry:
    """`withheld`, plus enough approved documents to fill the caller's `limit`.

    Same shape as `withheld` and deliberately not layered on it: the crowd has to
    be in the index *before* the redaction, and rebuilding afterwards would
    remove the withheld revision from the index and leave nothing to withhold.

    Like `withheld`, the withdrawal is applied with the index pointer withheld so
    the issue #15 purge (ADR-0024 decision 5) acts on nothing and the stale build
    -- which holds the withheld revision among the crowd -- is what the caller
    reads. That is the residual the canonical gate defends; the shipped close is
    proved in `test_absence_proof.py`.

    **Synchronous**, because `theurian index build` embeds through `asyncio.run`,
    which raises inside an already-running loop.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        (root / ".theurian/knowledge/architecture/runbook.md").write_text(RUNBOOK_BODY)
        (root / ".theurian/migrations/01K1FAAAAA01234567890ABCDE-runbook.yaml").write_text(
            RUNBOOK_MIGRATION.format(title=RUNBOOK_TITLE)
        )
        for index, letter in enumerate("HJKM"[:CROWD]):
            slug = f"gateway-note-{index}"
            (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
                f"# Gateway note {index}\n\nThe shared gateway meters every request for "
                f"tenant {index}, and the gateway rejects an unsigned one.\n"
            )
            (
                root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{slug}.yaml"
            ).write_text(
                EXTRA_MIGRATION.format(letter=letter, slug=slug, title=f"Gateway note {index}")
            )
        _run("migrate", "apply")
        _run("index", "build")

        request.param(root)
        _apply_leaving_the_stale_build_published(root)
    finally:
        monkey.undo()
    return registry


def test_the_crowding_probe_puts_the_withheld_document_among_visible_ones(
    crowded: ProjectRegistry,
) -> None:
    """Guards the guard: proves this fixture can violate the invariant.

    Two preconditions, both necessary. The withheld document must still be ranked
    — otherwise there is nothing to withhold and the fixture, not the fix, keeps
    the two responses equal. And it must rank *above* at least one visible
    document, or removing it changes nothing about which visible ones fit inside
    a small `limit`.

    Asserted one layer below the canonical store that does the withholding, with
    the same `per_item` the tool passes.
    """
    from theurian.application.retrieval_service import RetrievalService, SearchRequest
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(crowded.load()["demo"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    outcome = RetrievalService(SqliteIndexStore(built)).search(
        SearchRequest(query=f"gateway {LEAKED_CREDENTIAL}", project_id="demo", per_item=1),
        NOTHING_WITHHELD,
    )
    items = [candidate.item_id for candidate in outcome.candidates]

    assert "architecture.runbook" in items, "the probe must still reach the retracted document"
    assert items.index("architecture.runbook") < len(items) - 1, (
        "it must outrank at least one visible document, or its removal displaces nothing"
    )
    assert len([item for item in items if item != "architecture.runbook"]) >= CROWD, (
        "and the caller must have enough visible documents to fill a `limit`"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", range(1, CROWD + 2))
async def test_a_withheld_hit_never_costs_a_visible_one_its_place(
    crowded: ProjectRegistry, limit: int
) -> None:
    """SEC-13, T-15, T-17. The whole response, compared field by field.

    Run across every `limit` from one to one past the crowd, because the leak is
    a boundary effect: it appears exactly when the withheld candidate falls
    inside `limit` and disappears once `limit` exceeds everything that matched.
    A single `limit` would have been the one that passed.

    `maxTokens` is generous on purpose. This test is about the gate, not about
    the budget, and a budget tight enough to drop results would make the two
    calls differ over which of them the caller could afford rather than over what
    they were allowed to see.
    """
    probe = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {LEAKED_CREDENTIAL}",
        limit=limit,
        maxTokens=32_000,
    )
    control = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {CROWD_CONTROL}",
        limit=limit,
        maxTokens=32_000,
    )

    assert probe["count"] == min(limit, CROWD), "the caller gets every document they may read"
    assert _without_query(probe) == _without_query(control), (
        "anything that differs here is a bit an attacker reads per call"
    )


@pytest.mark.asyncio
async def test_a_withheld_hit_does_not_move_the_scores_of_the_visible_ones(
    crowded: ProjectRegistry,
) -> None:
    """The same oracle one field to the right, stated on its own so it stays shut.

    `count` is a single number and saturates: once `limit` is smaller than the
    number of visible matches it stops moving. `fusedScore` does not. It is
    published per hit to six decimal places and every one of them shifts by a
    rank when a withheld chunk ranks above it, so a saturated `count` and a full
    result set still carried the signal.

    The fix was first to re-fuse over what survived the gate, and is now to fuse
    only what the gate ever admitted — the retrievers are read through a
    `Visibility`, so the ranks that reach RRF are already the ranks an index
    without the withheld document would have produced. That is not an
    approximation of the number it replaces; it is the number.
    """
    probe = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {LEAKED_CREDENTIAL}",
        maxTokens=32_000,
    )
    control = await _call(
        crowded,
        "knowledge.search",
        projectId="demo",
        query=f"gateway {CROWD_CONTROL}",
        maxTokens=32_000,
    )

    assert [hit["fusedScore"] for hit in probe["results"]] == [
        hit["fusedScore"] for hit in control["results"]
    ]
    assert [hit["itemId"] for hit in probe["results"]] == [
        hit["itemId"] for hit in control["results"]
    ], "nor may the order move, which is the same read one step less directly"


# -- One status authority, reached from every tool ---------------------------


@pytest.mark.asyncio
async def test_knowledge_get_will_not_hand_over_what_search_withheld(
    registry: ProjectRegistry,
) -> None:
    """The bypass that survived three separate fixes to `knowledge.search`.

    Closing every path through search achieved nothing while `get` had no gate:
    a caller reads an approved item, takes the `targetItemId` off its relation,
    and fetches the withheld body in one more call. No flag, no guessing.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()

    withheld = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.caching-draft"
    )

    assert "not present" in withheld


@pytest.mark.asyncio
async def test_the_withheld_message_does_not_confirm_the_item_exists(
    registry: ProjectRegistry,
) -> None:
    """SEC-13. A distinct message would tell a caller that an id they may not
    read is nonetheless a real id — the inference the isolation exists to
    prevent."""
    absent = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.no-such-thing"
    )
    withheld = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.caching-draft"
    )

    assert absent.replace("no-such-thing", "X") == withheld.replace("caching-draft", "X")


@pytest.mark.asyncio
async def test_asking_for_unapproved_reaches_a_draft_through_get(
    registry: ProjectRegistry,
) -> None:
    """The flag widens which statuses are allowed. It is not a bypass, and it is
    also not a lock: a draft is work in progress an author has reason to read."""
    result = await _call(
        registry,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.caching-draft",
        includeUnapproved=True,
    )

    assert result["status"] == "draft"


#: A rejected item, an approved one, and one relation from `auth-policy` to each.
#:
#: The relation gate at `knowledge.get` had no corpus at all: no fixture in the
#: repository issued `addRelation`, so the test that named the gate asserted a
#: `not in` over an empty list and passed with the gate replaced by
#: `target.item_id is not None`. Both edges are needed, and for different
#: reasons -- the rejected one is the leak, and the approved one is what keeps
#: the assertion from going vacuous again if a later fixture change loses the
#: relations. `related_to` rather than a second `rejects` because it has no
#: inverse either, so both edges reach `knowledge.get` in the direction they
#: were written.
RELATION_MIGRATION_ID = "01K1RNNNNN01234567890ABCDE"
REJECTED_ID = "01K1RRRRRR01234567890ABCDE"
REJECTED_REVISION_ID = "01K1RRRREV01234567890ABCDE"
ROTATION_ID = "01K1TTTTTT01234567890ABCDE"
ROTATION_REVISION_ID = "01K1TTTREV01234567890ABCDE"

#: The note on the rejected edge. Published beside `targetItemId` by the same
#: comprehension, so it is a second field to check rather than decoration -- an
#: implementation that blanked the id and kept the note would leak the reason a
#: revision was rejected, which is the content SEC-13 withholds the body for.
REJECTED_RELATION_NOTE = "superseded by the signed-token design"

REJECTED_BODY = "# Plaintext token store\n\nTokens were kept unhashed in `sessions.token`.\n"
ROTATION_BODY = "# Token rotation\n\nRotating a credential invalidates the previous one.\n"

RELATION_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {RELATION_MIGRATION_ID}
createdAt: 2026-08-02T15:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.plaintext-token-store
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.plaintext-token-store
    revisionId: {REJECTED_REVISION_ID}
    contentFile: ../knowledge/architecture/plaintext-token-store.md
    metadata:
      title: Plaintext token store
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/plaintext-token-store.md
  - op: createItem
    itemId: architecture.token-rotation
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.token-rotation
    revisionId: {ROTATION_REVISION_ID}
    contentFile: ../knowledge/architecture/token-rotation.md
    metadata:
      title: Token rotation
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/token-rotation.md
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: rejects
    targetItemId: architecture.plaintext-token-store
    note: {REJECTED_RELATION_NOTE}
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: related_to
    targetItemId: architecture.token-rotation
    note: the rotation procedure this policy assumes
"""


@pytest.fixture
def with_a_rejected_relation(registry: ProjectRegistry) -> ProjectRegistry:
    """The `registry` project, plus the two edges above off `architecture.auth-policy`."""
    root = Path(registry.load()["demo"]["rootPath"])
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "plaintext-token-store.md").write_text(REJECTED_BODY)
    (knowledge / "token-rotation.md").write_text(ROTATION_BODY)
    (root / f".theurian/migrations/{RELATION_MIGRATION_ID}-relations.yaml").write_text(
        RELATION_MIGRATION
    )
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("migrate", "apply")
    finally:
        monkey.undo()
    return registry


def _stored_relations(registry: ProjectRegistry, item_id: str) -> tuple[str, ...]:
    """The relation targets the canonical store actually holds for ``item_id``.

    Resolved through the active-state pointer, not by globbing: `migrate apply`
    leaves the previous state database beside the new one, so a glob picks one of
    two and the test would depend on filesystem ordering. This is the same
    database the tool under test reads.
    """
    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    context = RequestContext(project_id=ProjectId("demo"))
    with SqliteCanonicalStore(paths.state / active.database_filename) as store:
        return tuple(
            relation.target_item_id.value
            for relation in store.list_relations(context, ItemId(item_id))
        )


@pytest.mark.asyncio
async def test_the_rejected_relation_really_reaches_the_canonical_store(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """Guards the guard below, which is the whole reason this round exists.

    The gate can only be observed while there is something for it to withhold.
    With no `addRelation` anywhere in the suite, `knowledge.get` published an
    empty list and the assertion that named the gate held over nothing. This
    reads the store directly, so a migration that silently stopped applying
    fails here rather than turning the next test green for the wrong reason.
    """
    stored = _stored_relations(with_a_rejected_relation, "architecture.auth-policy")

    assert stored == (
        "architecture.plaintext-token-store",
        "architecture.token-rotation",
    ), "both edges are recorded, and the gate is the only thing that removes one"


@pytest.mark.asyncio
async def test_relations_to_withheld_items_are_not_published(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """Withholding a body while publishing the id it lives at withholds nothing
    that matters — the id is how the body was found in the first place.

    Asserted as the exact published list rather than as an absence, because an
    absence over an empty list is what this test used to be. The approved edge
    has to survive: a gate that published nothing would satisfy "the rejected id
    is gone" and break `knowledge.get` for every honest caller.
    """
    result = await _call(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.auth-policy",
    )

    assert [relation["targetItemId"] for relation in result["relations"]] == [
        "architecture.token-rotation"
    ]
    assert [relation["relationType"] for relation in result["relations"]] == ["related_to"]


@pytest.mark.asyncio
async def test_no_field_of_a_withheld_relation_survives_into_the_payload(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """`targetItemId` is not the only thing the comprehension publishes.

    `note` says *why* a revision was rejected, which is the same class of content
    as the body the id would have been used to fetch. Asserted over the whole
    serialised payload so a field added to the relation shape later is covered
    without this test being edited (SEC-13).
    """
    result = await _call(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.auth-policy",
    )

    serialised = json.dumps(result)
    assert result["relations"], "the visible edge must still be there, or this asserts nothing"
    assert "plaintext-token-store" not in serialised
    assert REJECTED_RELATION_NOTE not in serialised
    assert "rejects" not in serialised


@pytest.mark.asyncio
async def test_the_rejected_item_is_unreachable_through_the_flag_that_reaches_a_draft(
    with_a_rejected_relation: ProjectRegistry,
) -> None:
    """`includeUnapproved` widens the allowed statuses; it does not disable them.

    A caller who has the rejected id from somewhere else must not be able to turn
    the relation gate's own opt-in into a bypass — `rejected` is outside
    `SURFACEABLE_STATUSES` and no flag adds it back.
    """
    result = await _call(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.auth-policy",
        includeUnapproved=True,
    )
    message = await _call_failing(
        with_a_rejected_relation,
        "knowledge.get",
        projectId="demo",
        itemId="architecture.plaintext-token-store",
        includeUnapproved=True,
    )

    assert [relation["targetItemId"] for relation in result["relations"]] == [
        "architecture.token-rotation"
    ]
    assert "not present" in message


# -- The same gate, in the direction nothing in this repository ever built ---
#
# `list_relations` returns every edge touching an item in *both* directions, and
# mirrors only the four types in `INVERSE_RELATIONS` on the way out. For every
# other type -- `rejects`, `related_to`, `contradicts`, `depends_on` -- an
# *incoming* edge comes back in the orientation it was stored in, so
# `target_item_id` is the item being fetched. The gate asked whether the target
# could be surfaced, which on those rows asks about the item the caller is
# already holding, and published the edge together with the note its withheld
# source had written. Measured on a real project before the fix:
#
#     {"relationType": "contradicts", "targetItemId": "architecture.auth-policy",
#      "note": "REJECTED BECAUSE sessions.token held raw bearer tokens until 2026-07"}
#
# `with_a_rejected_relation` above cannot show that, and neither can anything
# else here: both of its edges are outgoing, and no other fixture in the
# repository issues an `addRelation` at all. So the review round that went
# looking for this defect ran a suite in which every relation assertion agreed
# with it, and the fix landed with nothing going red.
#
# The corpus below is built so that cannot recur: every no-inverse type gets its
# own fetched item and its own incoming edge, and a second project holds the same
# visible content with the withheld item and its edges never written.

GATE_PROBE = "relations-probe"
GATE_ABSENT = "relations-absent"

#: The four relation types absent from `INVERSE_RELATIONS`, and therefore the
#: four whose incoming edges reach a reader unmirrored. Written out rather than
#: derived from the mapping, so this is a claim about which types matter rather
#: than a restatement of whatever the mapping currently holds. The premise --
#: that none of the four has an inverse -- is pinned in
#: `tests/unit/test_domain_invariants.py`, by
#: `test_the_four_types_that_reach_a_reader_unmirrored_have_no_inverse`.
NO_INVERSE_TYPES = ("rejects", "related_to", "contradicts", "depends_on")

#: The `rejected` item every withheld edge below hangs off.
WITHHELD_SOURCE = "architecture.plaintext-token-store"
#: A `draft` item. `rejected` is reachable through no flag, so a rejected source
#: cannot show that the gate *widens* correctly; a draft one can.
DRAFT_SOURCE = "architecture.session-cookies"
#: The approved item every fetched item keeps one publishable edge to, so no
#: assertion below is satisfied by an implementation that publishes nothing.
VISIBLE_PEER = "architecture.token-rotation"
#: The fetched item for the inverse-bearing type, reached from three directions.
INVERSE_TARGET = "architecture.gate-implements"
#: The fetched item the draft points at.
DRAFT_TARGET = "architecture.gate-draft-source"


def _gate_target(relation_type: str) -> str:
    """The approved item that receives an incoming edge of ``relation_type``.

    One fetched item per type rather than one item with four edges: with a single
    item every parameter would assert the same list, so a gate that handled
    `contradicts` and leaked `depends_on` would fail all four identically and
    name none of them.
    """
    return f"architecture.gate-{relation_type.replace('_', '-')}"


def _withheld_note(marker: str) -> str:
    """The note on an edge whose far end the caller may not see.

    A rejection rationale is where the secret that caused the rejection lives, so
    `note` is the field this defect actually leaked -- the withheld *id* never
    appeared. Tagged by the edge that carries it so a leak names its own route
    instead of only proving that some route is open.
    """
    return f"REJECTED BECAUSE sessions.token held raw bearer tokens ({marker})"


@dataclass(frozen=True, slots=True)
class _GateItem:
    """One item in the gate corpus. ``revision_id`` is fixed so both projects mint
    the same one -- the differential compares whole responses, and `revisionId`
    is published."""

    slug: str
    status: str
    revision_id: str

    @property
    def item_id(self) -> str:
        return f"architecture.{self.slug}"

    @property
    def title(self) -> str:
        return self.slug.replace("-", " ").capitalize()


#: Present in both projects.
GATE_SHARED_ITEMS = (
    _GateItem("token-rotation", "approved", "01K1SREV0101234567890ABCDE"),
    _GateItem("session-cookies", "draft", "01K1SREV0201234567890ABCDE"),
    _GateItem("gate-rejects", "approved", "01K1SREV0401234567890ABCDE"),
    _GateItem("gate-related-to", "approved", "01K1SREV0501234567890ABCDE"),
    _GateItem("gate-contradicts", "approved", "01K1SREV0601234567890ABCDE"),
    _GateItem("gate-depends-on", "approved", "01K1SREV0701234567890ABCDE"),
    _GateItem("gate-implements", "approved", "01K1SREV0801234567890ABCDE"),
    _GateItem("gate-draft-source", "approved", "01K1SREV0901234567890ABCDE"),
)

#: Written only into `relations-probe`.
GATE_WITHHELD_ITEM = _GateItem("plaintext-token-store", "rejected", "01K1SREV0301234567890ABCDE")

#: Edges both projects hold, byte for byte. Every one of them must be published.
GATE_VISIBLE_EDGES: tuple[tuple[str, str, str, str], ...] = (
    *(
        (_gate_target(relation_type), "related_to", VISIBLE_PEER, f"the peer for {relation_type}")
        for relation_type in NO_INVERSE_TYPES
    ),
    (INVERSE_TARGET, "related_to", VISIBLE_PEER, "the peer for implements"),
    (DRAFT_TARGET, "related_to", VISIBLE_PEER, "the peer for the draft source"),
    # A type *with* an inverse, both ways round. The store mirrors the incoming
    # one, which is a different code path from everything above and was pinned in
    # neither direction.
    (VISIBLE_PEER, "implements", INVERSE_TARGET, "incoming from a visible item, mirrored"),
    (INVERSE_TARGET, "implements", VISIBLE_PEER, "outgoing, a type that has an inverse"),
    # Incoming from a draft: withheld by default, published when widened.
    (DRAFT_SOURCE, "contradicts", DRAFT_TARGET, "incoming from a draft"),
)

#: Edges only `relations-probe` holds. None may be published under any flag.
GATE_WITHHELD_EDGES: tuple[tuple[str, str, str, str], ...] = (
    *(
        (WITHHELD_SOURCE, relation_type, _gate_target(relation_type), _withheld_note(relation_type))
        for relation_type in NO_INVERSE_TYPES
    ),
    (WITHHELD_SOURCE, "implements", INVERSE_TARGET, _withheld_note("implements")),
    # Outgoing to the withheld item: the direction the old gate did catch, kept
    # so the differential covers it too.
    (_gate_target("depends_on"), "related_to", WITHHELD_SOURCE, _withheld_note("outgoing")),
)

#: The items the two projects genuinely disagree about, and therefore the only
#: ones the differential can say anything with. `gate-draft-source` is left out
#: deliberately: the draft and its edge exist in *both* projects, so comparing
#: that item would compare two identical corpora and hold whatever the gate did.
GATE_DIFFERENTIAL_ITEMS = (
    *(_gate_target(relation_type) for relation_type in NO_INVERSE_TYPES),
    INVERSE_TARGET,
)

GATE_BASE_MIGRATION_ID = "01K1SAAAAA01234567890ABCDE"
GATE_WITHHELD_MIGRATION_ID = "01K1SBBBBB01234567890ABCDE"

GATE_ITEM_OPERATIONS = """  - op: createItem
    itemId: {item_id}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item_id}
    revisionId: {revision_id}
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://gate/{slug}.md
"""


def _gate_migration(
    migration_id: str,
    created_at: str,
    items: Sequence[_GateItem],
    edges: Sequence[tuple[str, str, str, str]],
) -> str:
    operations = "".join(
        GATE_ITEM_OPERATIONS.format(
            item_id=item.item_id,
            revision_id=item.revision_id,
            slug=item.slug,
            title=item.title,
            status=item.status,
        )
        for item in items
    ) + "".join(
        f"  - op: addRelation\n"
        f"    sourceItemId: {source}\n"
        f"    relationType: {relation_type}\n"
        f"    targetItemId: {target}\n"
        f"    note: {note}\n"
        for source, relation_type, target, note in edges
    )
    return (
        f"apiVersion: theurian.dev/v1\n"
        f"id: {migration_id}\n"
        f"createdAt: {created_at}\n"
        f"author: engineer@example.com\n"
        f"operations:\n"
        f"{operations}"
    )


def _build_gate_project(root: Path, *, holds_the_withheld_source: bool) -> None:
    """One project of the pair, differing only in whether the withheld item exists.

    The shared content lives in one migration written identically into both, and
    the withheld item and its six edges live in a second migration only the probe
    receives. Applied in id order, so the edges land after both endpoints exist.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _run("init")
    _run("project", "register")
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"

    for item in GATE_SHARED_ITEMS:
        (knowledge / f"{item.slug}.md").write_text(f"# {item.title}\n\nBody for {item.item_id}.\n")
    (migrations / f"{GATE_BASE_MIGRATION_ID}-shared.yaml").write_text(
        _gate_migration(
            GATE_BASE_MIGRATION_ID,
            "2026-08-03T10:00:00+09:00",
            GATE_SHARED_ITEMS,
            GATE_VISIBLE_EDGES,
        )
    )

    if holds_the_withheld_source:
        item = GATE_WITHHELD_ITEM
        (knowledge / f"{item.slug}.md").write_text(f"# {item.title}\n\nBody for {item.item_id}.\n")
        (migrations / f"{GATE_WITHHELD_MIGRATION_ID}-withheld.yaml").write_text(
            _gate_migration(
                GATE_WITHHELD_MIGRATION_ID,
                "2026-08-03T11:00:00+09:00",
                (item,),
                GATE_WITHHELD_EDGES,
            )
        )

    _run("migrate", "apply")


@pytest.fixture(scope="module")
def gate_projects(tmp_path_factory: pytest.TempPathFactory) -> ProjectRegistry:
    """Two projects in one registry, differing in one thing.

    ``relations-probe``  holds the rejected item and the six edges touching it
    ``relations-absent`` was never told that item exists

    Everything else is held equal on purpose -- the item ids, the revision ids,
    the bodies, the notes and the visible edges -- because the differential below
    compares whole responses, and any honest difference between the two projects
    would have to be excluded from that comparison and would take a real leak
    with it.

    Module-scoped: two real projects through the real CLI, and every test here
    asks the same pair the same questions.
    """
    base = tmp_path_factory.mktemp("relation-gate")
    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(base / "datadir"))
    try:
        for project_id, holds in ((GATE_PROBE, True), (GATE_ABSENT, False)):
            root = base / project_id
            root.mkdir()
            monkey.chdir(root)
            _build_gate_project(root, holds_the_withheld_source=holds)
    finally:
        monkey.undo()
    return ProjectRegistry.default(base / "datadir")


def _published(result: dict[str, Any]) -> tuple[tuple[str, str, str | None], ...]:
    """The relations a caller receives, in wire order.

    Order is not incidental: `list_relations` sorts by source, type and target in
    SQL, so a published list is deterministic and an assertion may state it. A
    `set` here would hide a gate that published the right rows in an order driven
    by which ones were filtered out.
    """
    return tuple(
        (relation["relationType"], relation["targetItemId"], relation["note"])
        for relation in result["relations"]
    )


def _gate_relation_rows(
    registry: ProjectRegistry, project_id: str
) -> tuple[tuple[str, str, str], ...]:
    """Every edge in ``project_id``'s canonical store, read with SQL.

    Deliberately not through `list_relations`: that is the call the gate filters,
    and a corpus guard that used it could not tell "the gate withheld this row"
    from "this row was never stored". Counting rows in the table is the only
    reading that distinguishes them -- the original relation test evaluated
    `all(...)` over an empty list and passed for exactly that reason.

    Resolved through the active-state pointer rather than a glob, because
    `migrate apply` leaves the previous database beside the new one.
    """
    paths = ProjectPaths.of(Path(registry.load()[project_id]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, f"{project_id} must have built a canonical state"
    with contextlib.closing(sqlite3.connect(paths.state / active.database_filename)) as connection:
        rows = connection.execute(
            "SELECT source_item_id, relation_type, target_item_id FROM knowledge_relations "
            "WHERE project_id = ? ORDER BY source_item_id, relation_type, target_item_id",
            (project_id,),
        ).fetchall()
    return tuple((str(a), str(b), str(c)) for a, b, c in rows)


def _authored(edges: Sequence[tuple[str, str, str, str]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted((source, kind, target) for source, kind, target, _ in edges))


def test_the_incoming_edges_really_reach_the_canonical_store(
    gate_projects: ProjectRegistry,
) -> None:
    """Guards the guard. The gate can only be observed while it holds something.

    Every assertion below is an absence, and an absence over a corpus that was
    never written is free. This counts the rows in `knowledge_relations`
    directly, so a migration that silently stopped applying -- a renamed
    operation, a rejected id, an edge dropped for referencing an item that does
    not exist yet -- fails here instead of turning six tests green for the wrong
    reason.

    The five incoming edges are named individually rather than counted, because
    a count cannot say which direction they were stored in, and direction is the
    whole defect.
    """
    rows = _gate_relation_rows(gate_projects, GATE_PROBE)

    assert rows == _authored(GATE_VISIBLE_EDGES + GATE_WITHHELD_EDGES)
    assert tuple(row for row in rows if row[0] == WITHHELD_SOURCE) == (
        (WITHHELD_SOURCE, "contradicts", "architecture.gate-contradicts"),
        (WITHHELD_SOURCE, "depends_on", "architecture.gate-depends-on"),
        (WITHHELD_SOURCE, "implements", INVERSE_TARGET),
        (WITHHELD_SOURCE, "rejects", "architecture.gate-rejects"),
        (WITHHELD_SOURCE, "related_to", "architecture.gate-related-to"),
    ), "the withheld item must be the *source* of these, or nothing incoming is being tested"


def test_the_control_project_holds_the_visible_edges_and_nothing_else(
    gate_projects: ProjectRegistry,
) -> None:
    """Guards the differential's other half: `relations-absent` is a control, not
    an empty project.

    If it lost the visible edges too, the comparison below would hold between two
    answers that publish nothing, and a gate that withheld everything would pass.
    If it gained the withheld ones, there would be no difference left to detect.
    Both are stated as one exact row set.
    """
    rows = _gate_relation_rows(gate_projects, GATE_ABSENT)

    assert rows == _authored(GATE_VISIBLE_EDGES)
    assert not any(WITHHELD_SOURCE in row for row in rows), (
        "the control must never have heard of the withheld item"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_unapproved", [False, True], ids=["default", "widened"])
@pytest.mark.parametrize("item_id", GATE_DIFFERENTIAL_ITEMS)
async def test_a_withheld_relation_endpoint_changes_nothing_a_caller_can_see(
    gate_projects: ProjectRegistry, item_id: str, include_unapproved: bool
) -> None:
    """SEC-13. Milestone 5's closure argument, applied to `knowledge.get`.

    One request against two corpora: one whose canonical state holds an item the
    caller may not read together with every edge touching it, one that was never
    told the item exists. The **whole response** must be identical -- all sixteen
    published fields, `body`, `excerpt`, `revisionId`, `freshness`,
    `sourceAnchors` and the three trust labels among them, plus any field added
    to this payload after today.

    Field-by-field is what the previous round did, and it is how this defect
    survived: `targetItemId` was checked, and the leak was in `note`. A whole-
    response comparison also covers the family that is not a field value at all
    -- *which rows*, or which part of a row, reached the caller -- since a
    displaced or reordered relation changes the list without changing any field's
    domain.

    Nothing is excluded from the comparison, which is worth saying because the
    equivalent test for `knowledge.search` has to exclude two build identities.
    `knowledge.get` publishes no build identity and no count, so the two
    responses are required to be equal outright.

    Asserted under both flags. `includeUnapproved=True` is the one that widens
    which statuses may surface, and `rejected` is outside `SURFACEABLE_STATUSES`
    under every flag -- so the equality is required to survive the widening, not
    to depend on it.

    One field is wall-clock-derived and shared rather than pinned:
    `freshness.ageDays` is `now - revisionCreatedAt` in whole days, and the two
    calls below are milliseconds apart, so they agree except across the instant
    the day count ticks. If this ever fails on `ageDays` alone and on nothing
    else, that is the cause and not a leak -- `revisionCreatedAt` itself comes
    from the migration and is identical in both projects by construction.
    """
    arguments = {"itemId": item_id, "includeUnapproved": include_unapproved}
    probe = await _call(gate_projects, "knowledge.get", projectId=GATE_PROBE, **arguments)
    absent = await _call(gate_projects, "knowledge.get", projectId=GATE_ABSENT, **arguments)

    assert probe["relations"], "two empty relation lists would be equal and prove nothing"
    assert probe == absent


@pytest.mark.asyncio
@pytest.mark.parametrize("relation_type", NO_INVERSE_TYPES)
async def test_an_incoming_edge_from_a_withheld_item_is_not_published(
    gate_projects: ProjectRegistry, relation_type: str
) -> None:
    """SEC-13. The four types whose incoming edges reach a reader unmirrored.

    On each of these an incoming edge arrives with `target_item_id` set to the
    item being fetched, so a gate that asks only about the target asks whether
    the caller may see what they are already holding. It answered yes for all
    four and published the source's note.

    One fetched item per type, so a gate that closed `contradicts` and left
    `depends_on` open fails at the parameter that names it. Asserted as the exact
    published list rather than as the absence of a string: an absence is what the
    round-six test asserted, and the visible edge has to survive or a gate that
    withheld every relation would satisfy it.
    """
    result = await _call(
        gate_projects, "knowledge.get", projectId=GATE_PROBE, itemId=_gate_target(relation_type)
    )

    assert _published(result) == (("related_to", VISIBLE_PEER, f"the peer for {relation_type}"),)
    assert _withheld_note(relation_type) not in json.dumps(result), (
        "the note is the field that leaked; the withheld id never appeared"
    )


@pytest.mark.asyncio
async def test_a_type_with_an_inverse_is_gated_in_both_directions(
    gate_projects: ProjectRegistry,
) -> None:
    """`implements` reaches this item three ways, and only two may be published.

    The store mirrors an incoming edge of a type that has an inverse, which is a
    different code path from every type above and was pinned in neither
    direction. All three are asserted at once because the mirroring is what makes
    them hard to tell apart on the wire: the withheld source's edge and the
    visible peer's edge are the same shape until the gate runs, and the published
    `implemented_by` row proves the mirror still happens for the one that clears
    it. A gate that suppressed both would look identical to a correct one if only
    the withheld direction were checked.

    Order is the store's: source id, then type, then target.
    """
    result = await _call(
        gate_projects, "knowledge.get", projectId=GATE_PROBE, itemId=INVERSE_TARGET
    )

    assert _published(result) == (
        ("implements", VISIBLE_PEER, "outgoing, a type that has an inverse"),
        ("related_to", VISIBLE_PEER, "the peer for implements"),
        ("implemented_by", VISIBLE_PEER, "incoming from a visible item, mirrored"),
    )


@pytest.mark.asyncio
async def test_an_incoming_edge_from_a_draft_reappears_when_the_flag_widens(
    gate_projects: ProjectRegistry,
) -> None:
    """Over-blocking is a defect too, and this is the case that distinguishes them.

    A gate that withheld every incoming edge would satisfy every assertion above.
    `includeUnapproved` widens which statuses may surface, and a draft is work in
    progress its author has reason to read -- so the same edge that is correctly
    absent by default has to come back, note included.

    It comes back naming the *fetched* item as its `targetItemId`, because an
    incoming edge of a type with no inverse is published in the orientation it
    was stored in. That is the shape, not a leak: the gate has cleared both ends
    before this row is published. It is pinned here so a later change to the wire
    shape is a decision rather than an accident.
    """
    default = await _call(gate_projects, "knowledge.get", projectId=GATE_PROBE, itemId=DRAFT_TARGET)
    widened = await _call(
        gate_projects,
        "knowledge.get",
        projectId=GATE_PROBE,
        itemId=DRAFT_TARGET,
        includeUnapproved=True,
    )

    assert _published(default) == (("related_to", VISIBLE_PEER, "the peer for the draft source"),)
    assert _published(widened) == (
        ("related_to", VISIBLE_PEER, "the peer for the draft source"),
        ("contradicts", DRAFT_TARGET, "incoming from a draft"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_unapproved", [False, True], ids=["default", "widened"])
async def test_an_incoming_edge_from_a_rejected_item_survives_no_flag(
    gate_projects: ProjectRegistry, include_unapproved: bool
) -> None:
    """`includeUnapproved` widens the allowed statuses; `rejected` is not among
    them under either setting.

    Stated as the same exact list under both flags, and separately from the draft
    above, because the two are the pair that has to be told apart: a caller who
    can widen their way to a draft's note must not widen their way to a rejected
    revision's, which is where the reason for the rejection is written.
    """
    result = await _call(
        gate_projects,
        "knowledge.get",
        projectId=GATE_PROBE,
        itemId=_gate_target("contradicts"),
        includeUnapproved=include_unapproved,
    )

    assert _published(result) == (("related_to", VISIBLE_PEER, "the peer for contradicts"),)


# -- `itemId` that is not an id at all ---------------------------------------


@pytest.mark.asyncio
async def test_a_malformed_item_id_names_the_tool_that_finds_a_real_one(
    registry: ProjectRegistry,
) -> None:
    """FR-R6. An error without a next action is a support request.

    `InvalidIdentifierError` carries a format rule and no remedy, and the SDK
    re-raises whatever escapes a tool as `Error executing tool …: {exc}` --
    keeping `str(exc)` and dropping everything else. So a caller who passed a
    title where an id belongs was told what an id looks like and not how to find
    one.

    Naming `knowledge.search` discloses nothing SEC-13 protects: every stored id
    passed this same validation, so a string that fails it names no item,
    withheld or otherwise.
    """
    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="Authentication Policy"
    )

    assert "knowledge.search" in message, "the remedy must name the tool that returns real ids"
    assert "demo" in message, "and the project to run it against"


@pytest.mark.asyncio
async def test_an_over_long_item_id_is_not_echoed_back(registry: ProjectRegistry) -> None:
    """The failure `MAX_QUERY_CHARS` closes for `query`, closed here for `itemId`.

    A message built from an unbounded input is an amplifier: whatever reads the
    error -- a log line, an agent's context window, a bug report -- receives the
    whole of what the caller sent. `ItemId` checks length before it quotes the
    value, so the message reports the length and never the string.

    Bounded at 500 rather than pinned at the 183 characters measured today: the
    property is that the message does not grow with its input, and 183 is one
    wording of it.

    **`projectId` was the third member of this class, and it is now closed.**
    `mcp/tools.py`'s `_unresolvable` used to interpolate it with no bound:
    measured through this same entry point, 2,000,000 characters in produced a
    2,000,141 character message, against 2,000 for `query` and 185 for `itemId`.
    `db36089` (#17) bounded it to the same discipline -- an over-long
    unregistered id is reported by its length, never echoed -- so the class
    carries no unnamed or unpinned member. The sibling pins are
    `test_an_over_long_project_id_is_reported_by_length_not_echoed` and
    `test_the_project_id_echo_is_named_up_to_the_id_ceiling_then_by_length`.
    """
    message = await _call_failing(registry, "knowledge.get", projectId="demo", itemId="a" * 20_000)

    assert "aaaa" not in message, "not even a truncated prefix of the identifier"
    assert "20000" in message, "the length is what the caller needs to see"
    assert len(message) < 500, f"the message grew with its input ({len(message)} characters)"


@pytest.mark.asyncio
async def test_an_over_long_project_id_is_reported_by_length_not_echoed(
    registry: ProjectRegistry,
) -> None:
    """#17. The `projectId` echo, the third member of the amplifier class above.

    `_resolve` runs before any `ProjectId` bounds the caller's string, so an
    unregistered id reaches `_unresolvable` as raw bytes. Interpolated verbatim,
    the error mirrored the whole of what the caller sent -- an ~1x amplifier of
    an unbounded input, the same failure `MAX_QUERY_CHARS` closes for `query`
    and `ItemId` closes for `itemId`. `db36089` reports an over-long id by its
    length instead, so the message cannot grow with its input (SEC-15).

    Asserting the length *value* appears, not merely that some number does: a
    message that dropped the count would leave a caller unable to tell that the
    id was too long at all, only that it was unregistered.
    """
    over_long = "a" * 50_000

    message = await _call_failing(registry, "knowledge.status", projectId=over_long)

    assert "aaaa" not in message, "not even a truncated prefix of the id may be echoed"
    assert "50000" in message, "the length is what the caller needs to see instead"
    assert len(message) < 500, f"the message grew with its input ({len(message)} characters)"


@pytest.mark.asyncio
async def test_the_project_id_echo_is_named_up_to_the_id_ceiling_then_by_length(
    registry: ProjectRegistry,
) -> None:
    """#17. The boundary between "named so a typo is visible" and "by length".

    A registered id is a `ProjectId`, which is at most `MAX_PROJECT_ID_CHARS`
    (defined as the domain ceiling `MAX_IDENTIFIER_LENGTH`). An unregistered id
    within that ceiling could be a typo of a real one, so it is named back; an
    id past it could not be any project id, so echoing it would only reflect the
    caller's own bytes and is replaced by the length.

    The boundary is derived from the constant rather than pinned at 200, so it
    tracks the ceiling if it moves and it catches an off-by-one in the check:
    an id of exactly `MAX_PROJECT_ID_CHARS` must be named (the check is `>`, not
    `>=`), one character over must not.
    """
    normal = "mistyped-project"
    at_ceiling = "b" * MAX_PROJECT_ID_CHARS
    just_over = "c" * (MAX_PROJECT_ID_CHARS + 1)

    named_normal = await _call_failing(registry, "knowledge.status", projectId=normal)
    named_ceiling = await _call_failing(registry, "knowledge.status", projectId=at_ceiling)
    by_length = await _call_failing(registry, "knowledge.status", projectId=just_over)

    assert normal in named_normal, "a normal unregistered id is named so a typo stays visible"
    assert at_ceiling in named_ceiling, "an id no longer than a project id can be is named back"
    assert "longer than any project id can be" not in named_ceiling, "so it is not by length"

    assert just_over not in by_length, "one character over the ceiling is not echoed"
    assert str(MAX_PROJECT_ID_CHARS + 1) in by_length, "it is reported by its length instead"


# -- The candidate depth itself, and the equality that closes the family ------
#
# The three sections above each closed one number by moving it past the gate:
# `usedTokens`, then `count`, then `fusedScore`. Two more were left, and neither
# is a number:
#
# - `CANDIDATE_DEPTH` rows were read from each retriever *before* anything asked
#   who may see them, so a withheld row took one of the fifty and the fiftieth
#   visible row fell off the end. Measured on the shipped code: 442 ordinary
#   `knowledge.search` calls recovered a sixteen-character credential, at the
#   **default** token budget, because `droppedForBudget` publishes the size of
#   the gate-cleared set. On a Japanese corpus the precondition is automatic —
#   `unicode61` cannot segment CJK, so the trigram retriever's fifty slots *are*
#   the candidate list.
# - `diversify(per_item=...)` also ran before the gate, so *which chunk* of a
#   document survived was decided by a ranking that included withheld rows.
#   Re-fusing afterwards cannot undo that: the discarded chunk is gone. Measured
#   over 20,000 random rank arrangements, chunk identity moved 9.1% of the time,
#   visible item order 3.4%, published `fusedScore` 3.6% — a different `excerpt`
#   for the same document, from two queries differing only in a token no visible
#   document contains.
#
# Both are the same defect, and it is not one a comparison of two *queries* can
# state fully: `count` saturates, `fusedScore` needs a tie, and under
# `useDense=True` two different query strings legitimately produce different
# dense ranks for visible documents, so a whole-response comparison would fail
# for an honest reason.
#
# So the property is stated against two *corpora* and one query: the response
# must equal what an index that never held the withheld document would return.
# Nothing about the query varies, which is what makes the comparison total —
# every field, `useDense` included.

#: Enough approved documents to fill every retriever's candidate depth. Smaller
#: and the boundary is unreachable: the withheld document has to take a slot a
#: visible one would otherwise have had, which cannot happen until the slots run
#: out.
#:
#: **An absolute number, not `CANDIDATE_DEPTH + 5`.** Written as an offset from
#: the constant, the crowd resized itself whenever the constant moved, so the
#: fixture saturated the pipeline at any depth and pinned none: `CANDIDATE_DEPTH`
#: was set to 5 and to 200 and this whole module passed. Fifty-five is fifty —
#: the depth the T-17 measurements were taken at — plus the five that make the
#: fiftieth visible row displaceable. If the pipeline's depth ever exceeds this,
#: `test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth`
#: fails, which is the correct outcome: the fixture would no longer reach the
#: boundary it exists to sit on.
DEPTH_CROWD = 55

#: The number of visible rows the pipeline is documented to read per retriever,
#: and the depth every T-17 and SEC-13 measurement in the threat model was taken
#: at. Stated here in its own right so the fixture describes a *corpus* rather
#: than restating whatever the implementation currently holds.
DOCUMENTED_DEPTH = 50

#: The two fields excluded from the comparisons below, and the only two.
#:
#: They name *which* build and *which* canonical state answered, and two
#: separately created projects cannot share either: `indexBuildId` is a fresh
#: ULID per build and `snapshotId` is a hash over content that differs by the
#: withheld document itself. Excluding a field is how a comparison quietly gets
#: narrower than the property it claims, so both are covered instead by
#: `test_the_build_identity_a_search_reports_does_not_vary_with_the_query`,
#: which shows they are the same for every query against one project and
#: therefore cannot answer "did this query match something withheld?".
BUILD_IDENTITY = ("indexBuildId", "snapshotId")

#: One template for the whole crowd. `EXTRA_MIGRATION` above encodes its ids as
#: a single letter, which runs out well before `CANDIDATE_DEPTH` documents.
DEPTH_NOTE_MIGRATION = """apiVersion: theurian.dev/v1
id: {mid}
createdAt: 2026-08-02T12:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: {rid}
    contentFile: ../knowledge/architecture/{slug}.md
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

DEPTH_RUNBOOK_ID = "01K1FAAAAA01234567890ABCDE"
DEPTH_RETIRE_ID = "01K1GAAAAA01234567890ABCDE"
DEPTH_LATE_ID = "01K1HAAAAA01234567890ABCDE"


def _depth_ulid(tag: str, number: int) -> str:
    """A deterministic ULID for the crowd, so every project mints the same ones.

    Chunk ids are `<revision ULID>#<ordinal>` and every tie in this corpus of
    near-identical notes breaks on chunk id, so two projects that disagree about
    these ids would produce two different orders for honest reasons and the
    comparison would prove nothing.

    ``tag`` is Crockford base32, which has no ``I``, ``L``, ``O`` or ``U``.
    """
    return f"01K1{tag}{number:03d}".ljust(26, "0")[:26]


@dataclass(frozen=True, slots=True)
class _DepthCorpus:
    """One writing system's version of the crowd, and how retrieval behaves on it.

    The equality property was pinned against English only, and English is the
    easy case: `unicode61` splits it on word boundaries, so the word index ranks
    the whole crowd and the trigram index is a second opinion. Japanese is the
    case the trigram index exists for (ADR-0023), and the situation there is
    materially different — and worse. `unicode61` cannot segment CJK, so a run of
    Japanese between two punctuation marks is *one* token: the word index cannot
    match `ゲートウェイ` inside it and has nothing to contribute, which makes the
    trigram retriever's fifty candidate slots the entire candidate list. That is
    why the extraction attack needed no filler trick on a Japanese corpus — and
    why a property held only against English was held against the writing system
    that needs it least.

    Held equal across both: the ids, the crowd size, the query's shape (a common
    term the crowd matches plus the credential only the withheld document holds),
    and the staleness of every index.
    """

    #: The pytest parameter id, so a failure names the writing system.
    id: str
    #: Matches the crowd through its common term and the withheld document
    #: through the credential — the two halves the channel needs.
    query: str
    #: The crowd note body and title, by ordinal.
    note: str
    note_title: str
    #: The document that gets withheld, with and without the thing worth
    #: stealing. Both bodies match the crowd's own term, so in both projects the
    #: withheld document is a real candidate the retrievers rank. Only one of
    #: them can be reached by a query for the credential, which is the single
    #: difference the response is required not to notice.
    runbook: str
    runbook_secret_tail: str
    runbook_clean_tail: str
    runbook_title: str
    #: The unrelated item created after the build in the project that never held
    #: the runbook, so that project is left exactly as stale as the other two.
    unrelated_title: str
    unrelated_body: str
    #: How many rows the *word* index can offer for `query` at
    #: :data:`DOCUMENTED_DEPTH`, measured rather than reasoned about. This is the
    #: number that says the two corpora are different machines: fifty in English,
    #: and in Japanese exactly one — the withheld document, reached through the
    #: credential, which is the only part of the query `unicode61` can tokenize
    #: out of that corpus.
    word_index_rows: int

    def body(self, number: int) -> str:
        return self.note.format(number=number)

    def title(self, number: int) -> str:
        return self.note_title.format(number=number)

    def runbook_body(self, *, secret: bool) -> str:
        tail = self.runbook_secret_tail if secret else self.runbook_clean_tail
        return self.runbook.format(tail=tail)


ENGLISH_DEPTH = _DepthCorpus(
    id="english",
    query=f"gateway {LEAKED_CREDENTIAL}",
    note=(
        "# Gateway note {number}\n\nThe shared gateway meters every request for "
        "tenant {number}. The gateway rejects an unsigned request and records it "
        "in the ledger.\n"
    ),
    note_title="Gateway note {number}",
    runbook=(
        "# Tenant quarantine playbook\n\nWhen the shared gateway vault {tail}, the "
        "responder isolates the affected tenant and records the rehearsal in the ledger.\n"
    ),
    runbook_secret_tail=f"credential {LEAKED_CREDENTIAL} leaks",
    runbook_clean_tail="credential is rotated on schedule",
    runbook_title=RUNBOOK_TITLE,
    unrelated_title="Ledger retention",
    unrelated_body="# Ledger retention\n\nRecords are kept for seven years.\n",
    word_index_rows=DOCUMENTED_DEPTH,
)

JAPANESE_DEPTH = _DepthCorpus(
    id="japanese",
    query=f"ゲートウェイ {LEAKED_CREDENTIAL}",
    note=(
        "# ゲートウェイ運用メモ {number}\n\n共有ゲートウェイはテナント {number} の全リクエストを"
        "計測する。ゲートウェイは署名のないリクエストを拒否し、その記録を台帳に残す。\n"
    ),
    note_title="ゲートウェイ運用メモ {number}",
    runbook=(
        "# テナント隔離手順書\n\n共有ゲートウェイの金庫で {tail} 、対応者は影響を受けた"
        "テナントを隔離し、予行演習の結果を台帳に記録する。\n"
    ),
    runbook_secret_tail=f"認証情報 {LEAKED_CREDENTIAL} が漏洩した場合",
    runbook_clean_tail="認証情報が定期的に更新される場合",
    runbook_title="テナント隔離手順書",
    unrelated_title="台帳の保存期間",
    unrelated_body="# 台帳の保存期間\n\n記録は七年間保存する。\n",
    # Measured, not assumed: the word index returns the withheld runbook and
    # nothing else, because the credential is ASCII and the fifty-five Japanese
    # notes are one token each.
    word_index_rows=1,
)

DEPTH_CORPORA = (ENGLISH_DEPTH, JAPANESE_DEPTH)


def _build_depth_project(
    root: Path, corpus: _DepthCorpus, *, secret: bool, holds_the_document: bool
) -> None:
    """One project: a crowd, optionally a withheld document, and a stale index.

    The three projects differ in exactly one thing each, and everything else is
    held equal on purpose — the ids, the bodies of the crowd, and the *freshness*
    of the index. The last one matters more than it looks: `stale` changes the
    `note`, the `note` is priced into the envelope, and the envelope decides how
    many results fit a default budget. A control whose index was merely fresh
    differed from the probe by two results for that reason alone, which is an
    honest difference and a useless comparison. So the project that never held
    the document gets an unrelated item created after its build, and is left
    exactly as far behind as the other two.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    _run("init")
    _run("project", "register")
    knowledge = root / ".theurian/knowledge/architecture"
    migrations = root / ".theurian/migrations"

    for number in range(DEPTH_CROWD):
        slug = f"gateway-note-{number:02d}"
        (knowledge / f"{slug}.md").write_text(corpus.body(number))
        (migrations / f"{_depth_ulid('N', number)}-{slug}.yaml").write_text(
            DEPTH_NOTE_MIGRATION.format(
                mid=_depth_ulid("N", number),
                rid=_depth_ulid("R", number),
                slug=slug,
                title=corpus.title(number),
            )
        )
    if holds_the_document:
        (knowledge / "runbook.md").write_text(corpus.runbook_body(secret=secret))
        (migrations / f"{DEPTH_RUNBOOK_ID}-runbook.yaml").write_text(
            RUNBOOK_MIGRATION.format(title=corpus.runbook_title)
        )

    _run("migrate", "apply")
    _run("index", "build")

    if holds_the_document:
        # The withdrawal, applied with the pointer withheld so issue #15's purge
        # (ADR-0024 decision 5) acts on nothing and the stale build still holds
        # the withheld runbook -- the residual the depth probes below exercise.
        # The shipped close of the non-residual case is `test_absence_proof.py`.
        (migrations / f"{DEPTH_RETIRE_ID}-deprecate.yaml").write_text(DEPRECATION_MIGRATION)
        _apply_leaving_the_stale_build_published(root)
    else:
        (knowledge / "ledger-retention.md").write_text(corpus.unrelated_body)
        (migrations / f"{DEPTH_LATE_ID}-ledger.yaml").write_text(
            DEPTH_NOTE_MIGRATION.format(
                mid=DEPTH_LATE_ID,
                rid=_depth_ulid("P", 0),
                slug="ledger-retention",
                title=corpus.unrelated_title,
            )
        )
        _run("migrate", "apply")


@dataclass(frozen=True, slots=True)
class _DepthProjects:
    """Three projects built from one corpus, and the corpus that describes them."""

    registry: ProjectRegistry
    corpus: _DepthCorpus


@pytest.fixture(scope="module", params=DEPTH_CORPORA, ids=[c.id for c in DEPTH_CORPORA])
def three_indexes(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> _DepthProjects:
    """The same crowd, indexed three ways, in each writing system.

    ``depth-probe``      the withheld document holds the credential
    ``depth-same-shape`` the withheld document holds nothing worth stealing
    ``depth-absent``     the document was never written at all

    Each writing system gets its own data directory, so the three project ids
    are the same in both and every test below can name them without knowing
    which corpus it was handed.

    Module-scoped because it builds three real projects through the real CLI and
    every test below asks it the same question. Measured at 2.9 s per corpus.
    """
    corpus: _DepthCorpus = request.param
    base = tmp_path_factory.mktemp(f"depth-{corpus.id}")
    monkey = pytest.MonkeyPatch()
    monkey.setenv("THEURIAN_DATA_DIR", str(base / "datadir"))
    try:
        for name, secret, holds in (
            ("depth-probe", True, True),
            ("depth-same-shape", False, True),
            ("depth-absent", False, False),
        ):
            root = base / name
            root.mkdir()
            monkey.chdir(root)
            _build_depth_project(root, corpus, secret=secret, holds_the_document=holds)
    finally:
        monkey.undo()
    return _DepthProjects(registry=ProjectRegistry.default(base / "datadir"), corpus=corpus)


def test_the_depth_probe_reaches_the_withheld_document_inside_the_candidate_depth(
    three_indexes: _DepthProjects,
) -> None:
    """Guards the guard: proves this fixture can violate the invariant.

    Three preconditions. The withheld document must still be in the index and
    still be matched — otherwise the equality below holds because there is
    nothing to withhold. It must rank inside the depth the pipeline reads, or it
    takes no slot from anyone. And the crowd must be able to fill that depth, or
    the fiftieth visible row does not exist to be displaced.

    **The rows are counted at :data:`DOCUMENTED_DEPTH`, not at
    ``CANDIDATE_DEPTH``.** Asked at the constant's own value, every count here
    moved with it and the assertion held at a depth of 5 and of 200 — a guard
    that resizes itself guards nothing. Counting at fifty makes this a claim
    about the corpus: fifty-five near-identical notes really do offer fifty rows
    to the retriever that carries them.

    **What this corpus reaches, and what it does not.** The last two assertions
    are read off the implementation, and they say opposite things on purpose. The
    crowd must offer more visible rows than :data:`CANDIDATE_DEPTH` publishes, or
    no fiftieth visible row exists to be displaced and every equality below is
    vacuous. It must *not* fill :data:`FIRST_PASS_DEPTH`, and it does not: fifty-six
    chunks against a first pass of a hundred, so both retrievers are exhausted on
    that pass and ``cleared[:CANDIDATE_DEPTH]`` trims the two corpora identically.
    This fixture therefore cannot fail when that cut is deleted — measured,
    ``return cleared`` passed every one of the 1,407 tests that existed before
    ``tests/unit/test_candidate_cut.py`` was written — and the corpus that can
    fail is there, where the matching rows outnumber a first pass. An earlier
    wording of this paragraph called the asserted constant "the depth the pipeline
    actually reads", which is :data:`FIRST_PASS_DEPTH` and was not the constant
    asserted; that mismatch is where the gap lived.

    It also records *which* retriever is doing the work, because that is the
    whole difference between the two corpora and it is not visible from the
    response. English fills both retrievers. Japanese fills only the trigram
    one: `unicode61` treats an unbroken run of CJK as a single token, so the
    fifty-five notes are one token each and the word index cannot match
    `ゲートウェイ` inside any of them — the single row it does return is the
    withheld document, reached through the ASCII credential. Fifty trigram slots
    are therefore the entire candidate list, which is why the attack needed no
    filler trick there.

    Ranked through the index directly, one layer below the canonical store that
    does the withholding — which is what the retrievers saw before this
    milestone.
    """
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    corpus = three_indexes.corpus
    root = Path(three_indexes.registry.load()["depth-probe"]["rootPath"])
    (built,) = (root / ".theurian/state").glob("theurian-index-*.sqlite")
    index = SqliteIndexStore(built)

    words = index.search_lexical(
        corpus.query, project_id="depth-probe", limit=DOCUMENTED_DEPTH
    ).rows
    trigrams = index.search_substring(
        corpus.query, project_id="depth-probe", limit=DOCUMENTED_DEPTH
    ).rows

    assert (len(words), len(trigrams)) == (corpus.word_index_rows, DOCUMENTED_DEPTH), (
        "the crowd must fill the depth it is there to fill, on the retriever that carries it"
    )
    assert "architecture.runbook" in [row.item_id for row in trigrams], (
        "the withheld document must take one of the slots, or nothing is displaced"
    )
    assert "architecture.runbook" in [row.item_id for row in words], (
        "and it must be reachable through the word index too, or `dense` and "
        "`lexical` below prove nothing about it"
    )
    crowd = index.search_substring(
        corpus.query, project_id="depth-probe", limit=FIRST_PASS_DEPTH
    ).rows

    assert len(crowd) > CANDIDATE_DEPTH, (
        "the pipeline publishes every row this crowd can offer, so no visible row "
        "is displaced and every equality asserted below holds vacuously"
    )
    assert len(crowd) < FIRST_PASS_DEPTH, (
        "this corpus now outlasts a first pass, so it no longer describes what the "
        "comment above says it describes: re-read `tests/unit/test_candidate_cut.py`, "
        "which exists because this one is exhausted before the candidate cut bites"
    )


@pytest.mark.asyncio
async def test_the_three_projects_are_equally_behind_their_own_knowledge(
    three_indexes: _DepthProjects,
) -> None:
    """Guards the guard: the confound that makes an honest difference look like a leak.

    `stale` decides the `note`, the `note` is priced into the envelope, and the
    envelope decides how many results fit a default budget. A control whose index
    was merely *fresh* differed from the probe by two results for that reason
    alone — a real difference, nothing to do with the withheld document, and
    indistinguishable in the comparison below from the leak it is looking for.

    So `depth-absent` is given an unrelated item after its build, purely to leave
    it as far behind as the two that retired the runbook. That is a fixture
    detail nothing else would notice breaking: remove it and every equality
    assertion below starts failing for a reason that is not a security defect,
    and the obvious response is to relax the assertion. This says out loud which
    of the two it is.
    """
    projects = ("depth-probe", "depth-same-shape", "depth-absent")

    answers = [
        await _call(
            three_indexes.registry,
            "knowledge.search",
            projectId=project,
            query=three_indexes.corpus.query,
        )
        for project in projects
    ]

    assert [answer["retrieval"]["stale"] for answer in answers] == [True, True, True]


@pytest.mark.asyncio
async def test_a_caller_who_asks_for_the_published_maximum_receives_it(
    three_indexes: _DepthProjects,
) -> None:
    """FR-R4. What `CANDIDATE_DEPTH` owes the published contract.

    `MAX_RESULTS` is a number this product states: a caller may ask for fifty
    results and the tool clamps anything larger to it. A candidate depth below
    that quietly makes the published maximum unreachable — the retrievers are cut
    to their depth *before* fusion, so the answer runs out of candidates before
    it runs out of limit, and a caller asking for fifty over a corpus of
    fifty-six documents gets however many the depth happened to allow.

    Nothing said so. `CANDIDATE_DEPTH` was set to 5 and every test in this module
    passed, including the equality comparisons above — they compare two projects
    against each other, so a depth that truncates both truncates them equally.

    Asked against `depth-absent`, which withholds nothing: this is a claim about
    reach, and mixing it with a corpus that has something to hide would put two
    effects on one measurement.
    """
    answer = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-absent",
        query=three_indexes.corpus.query,
        limit=MAX_RESULTS,
        maxTokens=32_000,
    )

    assert answer["retrieval"]["indexed"] is True, "this must be the ranked path"
    assert answer["count"] == MAX_RESULTS, (
        "the corpus holds more matching documents than the published cap, so a "
        "caller who asks for the cap must receive it"
    )


@pytest.mark.asyncio
async def test_the_default_budget_does_not_hand_back_the_whole_ranking(
    three_indexes: _DepthProjects,
) -> None:
    """FR-R4. The half of `DEFAULT_BUDGET_TOKENS` that bounds it from above.

    "Small enough that a caller who forgot the parameter is not handed their
    whole window back" is the constant's own promise, and the caller it is
    written for is exactly this one: they set `limit` to the published maximum
    and left `maxTokens` alone. Fifty results is several thousand tokens of
    payload; a default that let all of them through would spend a context window
    the caller never agreed to spend.

    Paired with `test_the_default_budget_answers_an_ordinary_query_in_full`,
    which bounds it from below. Neither alone is a bound: the default was set to
    200 and the whole suite passed, because every budget assertion in this module
    states its own `maxTokens` and the one that does not was too small to notice.

    **A band, not a value.** The pair holds the default between roughly 800 and
    8,000 tokens — measured by mutation: 700 fails below, 12,000 fails above on
    the English corpus and 16,000 on both. 2,000 sits inside it, and nothing in
    the requirements fixes it more tightly than that, so nothing here pretends to.
    """
    answer = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-absent",
        query=three_indexes.corpus.query,
        limit=MAX_RESULTS,
    )

    assert answer["retrieval"]["droppedForBudget"] >= 1, (
        "fifty results must not fit a budget the caller never chose"
    )
    assert answer["count"] < MAX_RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["depth-same-shape", "depth-absent"])
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"limit": MAX_RESULTS},
        {"limit": MAX_RESULTS - 1, "maxTokens": 32_000},
        {"limit": MAX_RESULTS, "maxTokens": 32_000},
        {"limit": MAX_RESULTS, "maxTokens": 32_000, "useDense": True},
    ],
    ids=["defaults", "at-the-depth", "one-below", "generous", "dense"],
)
async def test_a_withheld_document_changes_nothing_a_caller_can_see(
    three_indexes: _DepthProjects, control: str, arguments: dict[str, Any]
) -> None:
    """SEC-13, T-15. The property, by construction rather than by comparison.

    One query, two corpora: one whose index holds a document the caller may not
    read, one whose does not. Every published value must be equal — `count`,
    `usedTokens`, `droppedForBudget`, every hit's `fusedScore`, `foundBy`,
    `excerpt` and position, and the whole `retrieval` block bar the two build
    identities named in `BUILD_IDENTITY`.

    Three earlier rounds compared a probe query against a control query and
    passed while a sibling channel stayed open, because a comparison of two
    queries is only as wide as the fields those two queries happen to move. This
    one has no such gap: the query is *identical*, so anything that differs
    differs because of the withheld document and nothing else.

    It is also the only shape in which the dense retriever can be asserted at
    all. Two different query strings legitimately produce different dense ranks
    for documents the caller *can* see, so a two-query comparison under
    `useDense=True` fails for an honest reason; one query against two corpora
    does not.

    **Both writing systems, and the Japanese one is not a formality.** Measured
    again, per parameter set, against a mutation that takes the depth loop off
    the trigram retriever alone:

    - English notices only at `maxTokens=32_000` — `generous` and `dense` — where
      the whole ranking is published and `usedTokens` moves.
    - Japanese notices at `at-the-depth`, which is `limit=50` at the **default**
      token budget: `droppedForBudget` reads 43 against the control's 44. That is
      the field and the budget the shipped extraction attack used, and the word
      index cannot cover for it — `unicode61` cannot segment CJK, so the trigram
      retriever's fifty slots are the entire candidate list.
    - Japanese also notices at `generous` and `dense`, through `count` and
      `usedTokens`.

    **`defaults` and `one-below` stay green under that mutation, in both writing
    systems, and that is not a gap in them.** With no parameters at all `limit`
    is 10 and the budget admits 6, so a row displaced at candidate position 50 is
    nowhere near the answer; `one-below` asks for 49 of a ranking that has 50, so
    the fiftieth is the only one that moves and it was not published either way.
    They are in the list for the *other* leaks — `defaults` is there because the
    version this test closes leaked with no parameters set at all, and a
    parameter set that no current mutation moves is still the one a future
    regression may.

    An earlier wording of this paragraph said "Japanese notices at the default
    budget" without naming the parameter set, which reads as `defaults` — the one
    case that does not. Stated by id here for that reason.
    """
    query = three_indexes.corpus.query
    probe = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-probe",
        query=query,
        **arguments,
    )
    other = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId=control,
        query=query,
        **arguments,
    )

    assert probe["count"] > 0, "a comparison of two empty answers proves nothing"
    assert probe["results"] == other["results"], (
        "every field of every hit, including which chunk of a document was excerpted"
    )
    assert probe["count"] == other["count"]
    assert {k: v for k, v in probe["retrieval"].items() if k not in BUILD_IDENTITY} == {
        k: v for k, v in other["retrieval"].items() if k not in BUILD_IDENTITY
    }


@pytest.mark.asyncio
async def test_the_build_identity_a_search_reports_does_not_vary_with_the_query(
    three_indexes: _DepthProjects,
) -> None:
    """What justifies excluding `indexBuildId` and `snapshotId` above.

    A field left out of a comparison is a field nothing checks, so the two that
    are left out are checked here instead, and by the only test that means
    anything for them: they must be the same for *every* query against one
    project. A value that cannot vary with the query cannot answer a question
    about what the query matched.
    """
    matching = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-probe",
        query=three_indexes.corpus.query,
    )
    unrelated = await _call(
        three_indexes.registry, "knowledge.search", projectId="depth-probe", query=NO_SUCH_TERM
    )

    assert {k: matching["retrieval"][k] for k in BUILD_IDENTITY} == {
        k: unrelated["retrieval"][k] for k in BUILD_IDENTITY
    }
    assert all(matching["retrieval"][key] for key in BUILD_IDENTITY), "and neither is empty"


@pytest.mark.asyncio
async def test_a_stale_index_still_names_the_canonical_state_that_answered(
    three_indexes: _DepthProjects,
) -> None:
    """FR-R5. `snapshotId` is the canonical state's hash, never the index's.

    The two are equal on a fresh index, so the test that pairs `snapshotId` with
    `knowledge.status` proved nothing about which of them is being reported —
    replacing the canonical hash with the index's build-time hash passed the
    whole suite. They differ exactly when the index is behind, which is the state
    this fixture is in and the state a caller most needs the answer for: an index
    build id is not a canonical state, and a caller comparing the two strings to
    decide whether their `knowledge.status` snapshot is still current would be
    told "yes" by an index that had not been rebuilt since.
    """
    status = await _call(three_indexes.registry, "knowledge.status", projectId="depth-probe")
    search = await _call(
        three_indexes.registry,
        "knowledge.search",
        projectId="depth-probe",
        query=three_indexes.corpus.query,
    )

    assert search["retrieval"]["stale"] is True, "an index that is not behind cannot show this"
    assert search["retrieval"]["snapshotId"] == status["stateHash"]


@pytest.mark.asyncio
async def test_a_gc_unlink_between_a_requests_reads_does_not_tear_it(
    indexed: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#103 item 4, pinned through `hybrid_answer` rather than through the store.

    A search makes several index reads -- `embedding_model`, the retrieval, then
    `chunk_texts`. `hybrid_answer` opens one read connection for the request
    (`SqliteIndexStore.session`), so a `theurian index gc` that unlinks the build
    between two of those reads does not tear the request: on POSIX the held
    descriptor keeps the inode readable after the name is gone.

    **This is the last load-bearing line of the PR that nothing pinned.** Making
    `session()` a no-op -- each read opening its own connection again -- left the
    whole suite green, because every other test constructs a store and reads it
    once. Here the second read, after the unlink, opens a path that is gone and
    the whole request degrades to the substring-scan fallback.

    The unlink is deterministic rather than timed: `metadata()` is the first
    index read inside the session, reached through `embedding_model` -- which is
    why `useDense=True`, the parameter that makes `embedding_model` consult the
    stored model. Wrapping it to unlink after it returns puts the unlink between
    the first read and every one that follows.
    """
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(indexed.load()["demo"]["rootPath"])
    build = next((root / ".theurian/state").glob("theurian-index-*.sqlite"))

    real_metadata = SqliteIndexStore.metadata
    fired = 0

    def unlink_after_the_first_read(self: SqliteIndexStore) -> dict[str, object]:
        nonlocal fired
        result = real_metadata(self)
        if fired == 0:
            build.unlink()
        fired += 1
        return result

    monkeypatch.setattr(SqliteIndexStore, "metadata", unlink_after_the_first_read)

    result = await _call(
        indexed, "knowledge.search", projectId="demo", query="token", useDense=True
    )

    assert fired >= 1, (
        "the hook never fired, so the unlink did not land mid-request and this test proved "
        "nothing -- `embedding_model` no longer reads `metadata()`, or `useDense` stopped "
        "reaching it"
    )
    assert result["retrieval"]["indexed"] is True, (
        "the request tore on a `gc` unlink between two of its reads. Without the held session "
        "connection the second read opened a path that was gone, and the whole request fell "
        "back to the substring scan -- `retrieval.fallbackReason` is "
        f"{result['retrieval'].get('fallbackReason')!r}"
    )
    assert result["count"] >= 1, "the index answered, so it must have returned the matching hit"
    assert not build.exists(), (
        "a read recreated the reaped build: `mode=ro` is what stops `sqlite3.connect` from "
        "conjuring an empty database at the deleted path"
    )


@pytest.mark.asyncio
async def test_a_gc_unlink_in_the_acquisition_window_degrades_rather_than_raising(
    indexed: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-1's fix, which no other test holds (ADR-0024 point 7).

    There are two windows a `theurian index gc` unlink can land in, and they
    need different code and different tests:

    - *between two reads inside the session* -- the held descriptor survives it,
      pinned by `test_a_gc_unlink_between_a_requests_reads_does_not_tear_it`;
    - *before the session is acquired* -- between `_searchable_file`'s
      `is_searchable()` returning True and `hybrid_answer` opening the session.
      Acquiring the session opens the file, which is now gone, so acquisition
      itself raises `IndexUnreadableError`.

    The fix is that the `with index.session()` sits **inside** the `try`, so an
    acquisition failure is caught by `except IndexBuildError` and the request
    degrades to the substring scan. Move the `with` outside the `try` and that
    error escapes to the agent as a tool error -- and no test noticed, which is
    what this closes.

    The unlink is placed in the window deterministically: `is_searchable()` is
    the last thing that touches the file before acquisition, so this wraps it to
    unlink the build after it returns True.
    """
    from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

    root = Path(indexed.load()["demo"]["rootPath"])
    build = next((root / ".theurian/state").glob("theurian-index-*.sqlite"))

    real_is_searchable = SqliteIndexStore.is_searchable
    fired = False

    def unlink_in_the_acquisition_window(self: SqliteIndexStore) -> bool:
        nonlocal fired
        usable = real_is_searchable(self)
        if usable and not fired:
            build.unlink()
            fired = True
        return usable

    monkeypatch.setattr(SqliteIndexStore, "is_searchable", unlink_in_the_acquisition_window)

    result = await _call(indexed, "knowledge.search", projectId="demo", query="token")

    assert fired, (
        "the hook never fired, so the unlink did not land in the acquisition window -- "
        "`_searchable_file` no longer gates on `is_searchable()` before the session opens"
    )
    retrieval = result["retrieval"]
    assert retrieval["indexed"] is False, (
        "a `gc` unlink between the searchability check and session acquisition must degrade "
        "to the substring scan, not raise: with `with index.session()` outside the `try`, the "
        "acquisition `IndexUnreadableError` reaches the agent as a tool error"
    )
    assert retrieval["mode"] == "substring"
    assert retrieval["fallbackReason"] == "index-unreadable"
    assert result["count"] >= 1, "the substring scan still answers from the canonical store"
    assert not build.exists(), "a read recreated the reaped build; `mode=ro` must prevent it"


# -- `asOf`: a refinement, not a default filter (FR-R1, #63 phase 2) ---------
#
# Three items sharing one query term, distinguished only by their validity
# window: one open-ended since 2020, one expired at the end of 2021, one not
# valid until 2031. A moment pinned to mid-2020 must include the first two and
# exclude the third -- and, for the second, `freshness.isWithinValidity` must
# read `true` at that pinned moment even though the same field on the very
# same revision reads `false` against real time, which is what proves the
# field is computed against `asOf` and not against `datetime.now()`.

AS_OF_QUERY = "governance decision"

AS_OF_ALWAYS_MIGRATION_ID = "01K1QAAAAA01234567890ABCDE"
AS_OF_ALWAYS_REVISION_ID = "01K1QAAREV01234567890ABCDE"
AS_OF_LATER_MIGRATION_ID = "01K1RAAAAA01234567890ABCDE"
AS_OF_LATER_REVISION_ID = "01K1RAAREV01234567890ABCDE"
AS_OF_EXPIRED_MIGRATION_ID = "01K1SAAAAA01234567890ABCDE"
AS_OF_EXPIRED_REVISION_ID = "01K1SAAREV01234567890ABCDE"

AS_OF_ALWAYS_BODY = "# Governance always current\n\nA governance decision with no expiry.\n"
AS_OF_LATER_BODY = "# Governance starting later\n\nA governance decision that starts later.\n"
AS_OF_EXPIRED_BODY = "# Governance already expired\n\nA governance decision retired long ago.\n"

AS_OF_ALWAYS_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {AS_OF_ALWAYS_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.governance-always
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.governance-always
    revisionId: {AS_OF_ALWAYS_REVISION_ID}
    contentFile: ../knowledge/architecture/governance-always.md
    metadata:
      title: Governance always current
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2020-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://as-of-demo/governance-always.md
"""

AS_OF_LATER_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {AS_OF_LATER_MIGRATION_ID}
createdAt: 2026-08-02T10:05:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.governance-later
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.governance-later
    revisionId: {AS_OF_LATER_REVISION_ID}
    contentFile: ../knowledge/architecture/governance-later.md
    metadata:
      title: Governance starting later
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2031-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://as-of-demo/governance-later.md
"""

AS_OF_EXPIRED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {AS_OF_EXPIRED_MIGRATION_ID}
createdAt: 2026-08-02T10:10:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.governance-expired
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.governance-expired
    revisionId: {AS_OF_EXPIRED_REVISION_ID}
    contentFile: ../knowledge/architecture/governance-expired.md
    metadata:
      title: Governance already expired
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2020-01-01T00:00:00+09:00
      validTo: 2021-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://as-of-demo/governance-expired.md
"""

#: Strictly between `governance-always`/`governance-expired`'s `validFrom` and
#: `validTo`/`governance-later`'s `validFrom`. RFC 3339, offset required, the
#: same format `asOf` publishes.
AS_OF_PINNED_MOMENT = "2020-06-01T00:00:00+09:00"


@pytest.fixture
def as_of_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered project holding the three items described above."""
    root = tmp_path / "as-of-demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "as-of-datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "governance-always.md").write_text(AS_OF_ALWAYS_BODY)
    (knowledge / "governance-later.md").write_text(AS_OF_LATER_BODY)
    (knowledge / "governance-expired.md").write_text(AS_OF_EXPIRED_BODY)
    (root / f".theurian/migrations/{AS_OF_ALWAYS_MIGRATION_ID}-always.yaml").write_text(
        AS_OF_ALWAYS_MIGRATION
    )
    (root / f".theurian/migrations/{AS_OF_LATER_MIGRATION_ID}-later.yaml").write_text(
        AS_OF_LATER_MIGRATION
    )
    (root / f".theurian/migrations/{AS_OF_EXPIRED_MIGRATION_ID}-expired.yaml").write_text(
        AS_OF_EXPIRED_MIGRATION
    )
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


@pytest.fixture
def as_of_indexed(as_of_registry: ProjectRegistry) -> ProjectRegistry:
    """`as_of_registry`, plus a built retrieval index -- the ranked answer path."""
    root = Path(as_of_registry.load()["as-of-demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        _run("index", "build")
    finally:
        monkey.undo()
    return as_of_registry


@pytest.fixture(params=["as_of_indexed", "as_of_registry"], ids=["ranked", "fallback"])
def as_of_either_answer_path(request: pytest.FixtureRequest) -> ProjectRegistry:
    """`as_of_registry`, ranked and unranked.

    The unranked scan is a named blind spot of the whole absence-proof suite in
    this module -- `test_a_withheld_document_changes_nothing_a_caller_can_see`
    only ever ranks -- so `asOf` has to be reached by name on both paths rather
    than trusted to generalise from one of them.
    """
    chosen: ProjectRegistry = request.getfixturevalue(request.param)
    return chosen


@pytest.mark.asyncio
async def test_a_search_pinned_to_a_moment_returns_only_knowledge_valid_then(
    as_of_either_answer_path: ProjectRegistry,
) -> None:
    """FR-R1, #63 phase 2. `asOf` pins the *validity window*, not the query:
    all three items match `AS_OF_QUERY`, so what changes between the two calls
    below is which items are inside their declared window at the pinned
    moment, checked through the identical `ValidityPeriod.contains` on both
    the ranked path (`CanonicalVisibility.at_moment`) and the unranked
    fallback (`_scan`, in Python -- not through a SQL `current_at` filter,
    which this project's own `SqliteCanonicalStore` no longer has after
    review round 1 of PR #112 found it comparing timestamps as SQLite TEXT).

    Every fixture here uses a single UTC offset (`+09:00`) throughout, so it
    cannot exercise the offset-mismatch defect that filter had; that case is
    `test_a_mixed_utc_offset_does_not_change_which_items_are_in_the_window`.

    Also the test for the parameter's second published effect: a hit's
    `freshness.isWithinValidity` **and** `freshness.ageDays` must be computed
    against the pinned moment, not against real time. `governance-expired` is
    chosen for the `isWithinValidity` assertion specifically because its
    real-time answer and its pinned-moment answer disagree -- proving the
    field moved with `asOf` rather than merely happening to agree with it.
    `governance-always`, created at `2026-08-02T10:00:00+09:00` and pinned at
    `AS_OF_PINNED_MOMENT` (`2020-06-01T00:00:00+09:00`, six years earlier),
    is what proves `ageDays` moved too: computed against real time it can only
    grow from today; computed against the pinned moment it is clamped to zero,
    which is the floor `mcp.results.result_payload` applies to a negative age
    -- unreachable unless the moment behind it is the pinned one.
    """
    unpinned = await _call(
        as_of_either_answer_path, "knowledge.search", projectId="as-of-demo", query=AS_OF_QUERY
    )
    pinned = await _call(
        as_of_either_answer_path,
        "knowledge.search",
        projectId="as-of-demo",
        query=AS_OF_QUERY,
        asOf=AS_OF_PINNED_MOMENT,
    )

    assert {r["itemId"] for r in unpinned["results"]} == {
        "architecture.governance-always",
        "architecture.governance-later",
        "architecture.governance-expired",
    }, "without `asOf` nothing is filtered by validity, exactly as before this parameter existed"
    unpinned_expired = next(
        r for r in unpinned["results"] if r["itemId"] == "architecture.governance-expired"
    )
    assert unpinned_expired["freshness"]["isWithinValidity"] is False, (
        "expired against real time -- the precondition that makes the pinned "
        "assertion below mean something"
    )
    unpinned_always = next(
        r for r in unpinned["results"] if r["itemId"] == "architecture.governance-always"
    )
    assert unpinned_always["freshness"]["ageDays"] > 0, (
        "against real time, a revision created in the past has a positive age"
    )

    assert {r["itemId"] for r in pinned["results"]} == {
        "architecture.governance-always",
        "architecture.governance-expired",
    }, "the item not yet valid at the pinned moment must be excluded"
    pinned_expired = next(
        r for r in pinned["results"] if r["itemId"] == "architecture.governance-expired"
    )
    assert pinned_expired["freshness"]["isWithinValidity"] is True, (
        "computed against the pinned moment, not against real time"
    )
    pinned_always = next(
        r for r in pinned["results"] if r["itemId"] == "architecture.governance-always"
    )
    assert pinned_always["freshness"]["ageDays"] == 0, (
        "computed against the pinned moment, six years before the revision was "
        "created -- clamped to zero rather than negative, and only reachable "
        "at all if `ageDays` moved with `asOf` rather than staying pinned to "
        "real time (MEDIUM-3, review round 1 of PR #112)"
    )


@pytest.mark.asyncio
async def test_everything_as_of_excludes_is_returned_by_the_same_query_without_it(
    as_of_either_answer_path: ProjectRegistry,
) -> None:
    """The recorded closure argument for `asOf`, as a test rather than as prose.

    `asOf` is not a withholding: everything it excludes is returned to the
    same caller by the same query with the parameter omitted, so no observable
    here can carry a bit the caller could not obtain directly, and the
    disclosure-family checklist SEC-13/T-15 opens for a document a caller may
    not read does not apply. Filtering by default was rejected for the
    corresponding reason a *permanent* filter would reopen it: it would make
    `freshness.isWithinValidity` constant-`true` on a fresh index and give the
    ranked path a stale-index statistics residual with no way to turn off, the
    shape T-17a already carries for a different cause
    (`theurian.application.retrieval_service`).

    Checked two ways, not one: the excluded item is still present, whole, in
    the identical `knowledge.search` call with `asOf` omitted, *and*
    independently confirmed through `knowledge.get` -- a different tool,
    reached by id rather than by query -- so this is not merely "a shorter
    excerpt survives", it is "the item was never inaccessible".

    **This does not assert `pinned ⊆ unpinned`, and an earlier version did.**
    That is false in general: when `limit`/`maxTokens` truncate the unpinned
    answer, excluding a top candidate via `asOf` can *promote* a
    lower-ranked one that the untruncated unpinned ranking also contains but
    the truncated unpinned *response* does not display -- reviewer-measured
    on a 150-document corpus (MEDIUM-1, review round 1 of PR #112). It
    happens not to be reachable through this three-item fixture, which is
    exactly why it read as a general property here;
    `test_asof_can_promote_an_item_the_unpinned_response_truncated_away`
    demonstrates it directly and pins the property that *is* true instead.
    """
    unpinned = await _call(
        as_of_either_answer_path, "knowledge.search", projectId="as-of-demo", query=AS_OF_QUERY
    )
    pinned = await _call(
        as_of_either_answer_path,
        "knowledge.search",
        projectId="as-of-demo",
        query=AS_OF_QUERY,
        asOf=AS_OF_PINNED_MOMENT,
    )

    unpinned_by_id = {r["itemId"]: r for r in unpinned["results"]}
    pinned_ids = {r["itemId"] for r in pinned["results"]}
    excluded = set(unpinned_by_id) - pinned_ids

    assert excluded == {"architecture.governance-later"}, "the pin must exclude something real"

    excluded_hit = unpinned_by_id["architecture.governance-later"]
    assert excluded_hit["title"] == "Governance starting later"
    assert excluded_hit["sourceAnchors"], "a whole, ordinary result, not a redacted one"

    fetched = await _call(
        as_of_either_answer_path,
        "knowledge.get",
        projectId="as-of-demo",
        itemId="architecture.governance-later",
    )
    assert fetched["body"] == AS_OF_LATER_BODY, (
        "reachable through a second, independent tool too -- `asOf` narrows one "
        "query, it does not narrow what this caller may read"
    )


# -- `asOf` can promote a candidate the unpinned response truncated away -----
#
# MEDIUM-1, review round 1 of PR #112. The closure test above used to assert
# `pinned_ids <= set(unpinned_by_id)`. That is not a general property: when
# `limit` truncates the unpinned answer, excluding a candidate via `asOf`
# changes which items fill the remaining slots, so a pinned response can
# contain an id the truncated unpinned one does not display. It read as a
# general property only because this fixture's three items never fill
# `limit=10` -- promotion needs a `limit` the untruncated candidate set
# actually exceeds, which this fixture below is built to do at `limit=2`.

PROMOTION_QUERY = "promotion ordering decision"

PROMOTION_EXCLUDED_ID = "architecture.aaa-not-yet-valid"
PROMOTION_KEPT_ID = "architecture.bbb-always-valid"
PROMOTION_PROMOTED_ID = "architecture.ccc-always-valid"

#: Before every item's `validFrom` below except the one it must exclude.
PROMOTION_AS_OF = "2026-01-01T00:00:00+09:00"

PROMOTION_EXCLUDED_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1WAAAAA01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.aaa-not-yet-valid
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.aaa-not-yet-valid
    revisionId: 01K1WAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/aaa-not-yet-valid.md
    metadata:
      title: Not yet valid, alphabetically first
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2031-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://promotion-demo/aaa-not-yet-valid.md
"""

PROMOTION_KEPT_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1XAAAAA01234567890ABCDE
createdAt: 2026-08-02T10:05:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.bbb-always-valid
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.bbb-always-valid
    revisionId: 01K1XAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/bbb-always-valid.md
    metadata:
      title: Always valid, alphabetically second
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2020-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://promotion-demo/bbb-always-valid.md
"""

PROMOTION_PROMOTED_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1YAAAAA01234567890ABCDE
createdAt: 2026-08-02T10:10:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.ccc-always-valid
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.ccc-always-valid
    revisionId: 01K1YAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/ccc-always-valid.md
    metadata:
      title: Always valid, alphabetically third
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2020-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://promotion-demo/ccc-always-valid.md
"""

PROMOTION_BODY = "A promotion ordering decision distinguished only by its item id.\n"


@pytest.fixture
def promotion_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ProjectRegistry]:
    """Three items, alphabetically `aaa` < `bbb` < `ccc`, never indexed --
    the fallback path orders by item id, which is what makes the promotion
    at `limit=2` deterministic: `aaa` is excluded by `PROMOTION_AS_OF`, so its
    slot goes to `ccc` rather than staying empty.
    """
    root = tmp_path / "promotion-demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "promotion-datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "aaa-not-yet-valid.md").write_text(PROMOTION_BODY)
    (knowledge / "bbb-always-valid.md").write_text(PROMOTION_BODY)
    (knowledge / "ccc-always-valid.md").write_text(PROMOTION_BODY)
    (root / ".theurian/migrations/01K1WAAAAA01234567890ABCDE-aaa.yaml").write_text(
        PROMOTION_EXCLUDED_MIGRATION
    )
    (root / ".theurian/migrations/01K1XAAAAA01234567890ABCDE-bbb.yaml").write_text(
        PROMOTION_KEPT_MIGRATION
    )
    (root / ".theurian/migrations/01K1YAAAAA01234567890ABCDE-ccc.yaml").write_text(
        PROMOTION_PROMOTED_MIGRATION
    )
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


@pytest.mark.asyncio
async def test_asof_can_promote_an_item_the_unpinned_response_truncated_away(
    promotion_registry: ProjectRegistry,
) -> None:
    """The property that replaced `pinned ⊆ unpinned` (MEDIUM-1, PR #112
    review round 1): a promoted item is absent from the truncated unpinned
    slice, not absent from what this caller may read. It is independently
    reachable both through `knowledge.get` and through the identical unpinned
    query once its own `limit` is generous enough not to cut it.
    """
    unpinned = await _call(
        promotion_registry,
        "knowledge.search",
        projectId="promotion-demo",
        query=PROMOTION_QUERY,
        limit=2,
    )
    pinned = await _call(
        promotion_registry,
        "knowledge.search",
        projectId="promotion-demo",
        query=PROMOTION_QUERY,
        limit=2,
        asOf=PROMOTION_AS_OF,
    )

    unpinned_ids = {r["itemId"] for r in unpinned["results"]}
    pinned_ids = {r["itemId"] for r in pinned["results"]}

    assert unpinned_ids == {PROMOTION_EXCLUDED_ID, PROMOTION_KEPT_ID}, (
        "unranked order is by item id: the two alphabetically earliest fill `limit=2`"
    )
    assert pinned_ids == {PROMOTION_KEPT_ID, PROMOTION_PROMOTED_ID}, (
        "excluding the alphabetically earliest promotes the third into the same `limit=2`"
    )
    assert not pinned_ids <= unpinned_ids, (
        f"{PROMOTION_PROMOTED_ID!r} is in the pinned answer and not in the truncated "
        "unpinned one -- demonstrating why that subset property does not hold in general"
    )

    fetched = await _call(
        promotion_registry,
        "knowledge.get",
        projectId="promotion-demo",
        itemId=PROMOTION_PROMOTED_ID,
    )
    assert fetched["itemId"] == PROMOTION_PROMOTED_ID, "reachable directly, by id"

    unpinned_untruncated = await _call(
        promotion_registry,
        "knowledge.search",
        projectId="promotion-demo",
        query=PROMOTION_QUERY,
        limit=3,
    )
    assert PROMOTION_PROMOTED_ID in {r["itemId"] for r in unpinned_untruncated["results"]}, (
        "and reachable through the identical unpinned search once `limit` does not "
        "truncate it -- it was never withheld, only outside a small slice"
    )


# -- A mixed UTC offset must not change which items are in the window --------
#
# Found in round 1 of PR #112's review: `SqliteCanonicalStore.list_items(
# current_at=...)` compared a stored `validFrom`/`validTo` against `asOf` as
# SQLite TEXT -- a lexicographic ordering of the ISO-8601 string, never the
# absolute instant it names. `'2020-01-01T00:00:00+09:00'` sorts *after*
# `'2019-12-31T16:00:00Z'` even though the first names the earlier absolute
# moment: `'2'` outranks `'1'` at the fourth character, and nothing downstream
# of that comparison ever looks further. The ranked path never had this bug --
# `CanonicalVisibility` always compared through `ValidityPeriod.contains`,
# which parses both sides into timezone-aware `datetime` objects first -- so
# every fixture above that used one offset throughout (`+09:00` for
# `validFrom`/`validTo` and for `asOf`) could not have found it: the
# lexicographic order and the absolute order agree whenever every timestamp
# compared shares a common prefix length and offset.
#
# Closed by deleting `current_at` and its SQL branch outright rather than
# normalizing it: both paths now build their moment from `ValidityPeriod.
# contains`, so there is exactly one comparison to get right instead of two
# that have to be kept in agreement.

MIXED_OFFSET_QUERY = "timezone boundary decision"

MIXED_OFFSET_INSIDE_ID = "architecture.boundary-inside-window"
MIXED_OFFSET_EXPIRED_ID = "architecture.boundary-already-expired"

MIXED_OFFSET_INSIDE_MIGRATION_ID = "01K1TAAAAA01234567890ABCDE"
MIXED_OFFSET_INSIDE_REVISION_ID = "01K1TAAREV01234567890ABCDE"
MIXED_OFFSET_EXPIRED_MIGRATION_ID = "01K1VAAAAA01234567890ABCDE"
MIXED_OFFSET_EXPIRED_REVISION_ID = "01K1VAAREV01234567890ABCDE"

MIXED_OFFSET_INSIDE_BODY = (
    "# Timezone boundary, inside the window\n\n"
    "A timezone boundary decision valid from a JST midnight with no expiry.\n"
)
MIXED_OFFSET_EXPIRED_BODY = (
    "# Timezone boundary, already expired\n\n"
    "A timezone boundary decision that expired at a JST midnight.\n"
)

#: `validFrom` is `2020-01-01T00:00:00+09:00`, i.e. `2019-12-31T15:00:00Z`.
#: `MIXED_OFFSET_AS_OF` below is one absolute hour after that, so this item is
#: *inside* its open-ended window at the pinned moment -- the item review
#: round 1 found the fallback path wrongly excluding.
MIXED_OFFSET_INSIDE_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIXED_OFFSET_INSIDE_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {MIXED_OFFSET_INSIDE_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {MIXED_OFFSET_INSIDE_ID}
    revisionId: {MIXED_OFFSET_INSIDE_REVISION_ID}
    contentFile: ../knowledge/architecture/boundary-inside-window.md
    metadata:
      title: Timezone boundary, inside the window
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2020-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://mixed-offset-demo/boundary-inside-window.md
"""

#: `validTo` is the identical absolute instant as `validFrom` above,
#: `2019-12-31T15:00:00Z`, so this item is *expired* at `MIXED_OFFSET_AS_OF`
#: (one absolute hour later) -- the item review round 1 found the fallback
#: path wrongly including, with its own `freshness.isWithinValidity: false`
#: printed on the same payload.
MIXED_OFFSET_EXPIRED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIXED_OFFSET_EXPIRED_MIGRATION_ID}
createdAt: 2026-08-02T10:05:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {MIXED_OFFSET_EXPIRED_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {MIXED_OFFSET_EXPIRED_ID}
    revisionId: {MIXED_OFFSET_EXPIRED_REVISION_ID}
    contentFile: ../knowledge/architecture/boundary-already-expired.md
    metadata:
      title: Timezone boundary, already expired
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      validFrom: 2010-01-01T00:00:00+09:00
      validTo: 2020-01-01T00:00:00+09:00
      sourceAnchors:
        - provider: git
          sourceUri: git://mixed-offset-demo/boundary-already-expired.md
"""

#: One absolute hour after both migrations' JST-authored boundary
#: (`2019-12-31T15:00:00Z`), given in `Z` rather than `+09:00` -- the mixed
#: offset itself. Inside `MIXED_OFFSET_INSIDE_ID`'s open-ended window and past
#: `MIXED_OFFSET_EXPIRED_ID`'s `validTo`, in absolute time.
MIXED_OFFSET_AS_OF = "2019-12-31T16:00:00Z"


@pytest.fixture
def mixed_offset_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """The mixed-offset corpus above, registered twice under one registry:
    once left unindexed, so the unranked fallback answers, and once indexed,
    so the ranked path answers -- so one test can compare both answers to the
    identical query against the identical corpus, which a parametrised
    ranked-or-fallback fixture cannot give it.
    """
    data_dir = tmp_path / "mixed-offset-datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))

    for name, build_index in (("mixed-offset-fallback", False), ("mixed-offset-ranked", True)):
        root = tmp_path / name
        root.mkdir()
        for args in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

        monkeypatch.chdir(root)
        _run("init")
        knowledge = root / ".theurian/knowledge/architecture"
        (knowledge / "boundary-inside-window.md").write_text(MIXED_OFFSET_INSIDE_BODY)
        (knowledge / "boundary-already-expired.md").write_text(MIXED_OFFSET_EXPIRED_BODY)
        (root / f".theurian/migrations/{MIXED_OFFSET_INSIDE_MIGRATION_ID}-inside.yaml").write_text(
            MIXED_OFFSET_INSIDE_MIGRATION
        )
        (
            root / f".theurian/migrations/{MIXED_OFFSET_EXPIRED_MIGRATION_ID}-expired.yaml"
        ).write_text(MIXED_OFFSET_EXPIRED_MIGRATION)
        _run("project", "register")
        _run("migrate", "apply")
        if build_index:
            _run("index", "build")

    return ProjectRegistry.default(data_dir)


@pytest.mark.asyncio
async def test_a_mixed_utc_offset_does_not_change_which_items_are_in_the_window(
    mixed_offset_projects: ProjectRegistry,
) -> None:
    """The closure condition for HIGH-1, review round 1 of PR #112.

    Same corpus, same query, same pinned moment, two retrievers. If the two
    disagree, at least one of them is wrong, and this project has no way to
    tell a caller which -- so the only correct answer is that they cannot
    disagree. Before the fix they did, on both items at once: the fallback
    path reported ``{MIXED_OFFSET_EXPIRED_ID}`` -- excluding the item that is
    actually inside its window and including the one that has actually
    expired, the two faces review round 1 measured through the real MCP
    surface. This also stands in for "the fallback path applies
    ``ValidityPeriod.contains`` in Python": no implementation of ``_scan``
    that instead re-introduced a SQL-side comparison of the raw strings could
    pass this alongside the ranked path, whatever mechanism it used.

    This is also the only test in this module that runs the ranked and the
    unranked answer through the same assertions in the same test, rather than
    through `as_of_either_answer_path`'s parametrisation -- deliberately: the
    property under test is an equality *between* the two paths, which a
    fixture that hands back only one of them at a time cannot state.
    """
    fallback = await _call(
        mixed_offset_projects,
        "knowledge.search",
        projectId="mixed-offset-fallback",
        query=MIXED_OFFSET_QUERY,
        asOf=MIXED_OFFSET_AS_OF,
    )
    ranked = await _call(
        mixed_offset_projects,
        "knowledge.search",
        projectId="mixed-offset-ranked",
        query=MIXED_OFFSET_QUERY,
        asOf=MIXED_OFFSET_AS_OF,
    )

    assert fallback["retrieval"]["indexed"] is False, "must actually exercise the fallback path"
    assert ranked["retrieval"]["indexed"] is True, "must actually exercise the ranked path"

    fallback_ids = {r["itemId"] for r in fallback["results"]}
    ranked_ids = {r["itemId"] for r in ranked["results"]}

    assert ranked_ids == {MIXED_OFFSET_INSIDE_ID}, (
        "the correct answer, in absolute time: the open-ended item is valid "
        "at the pinned moment, the already-expired one is not"
    )
    assert fallback_ids == ranked_ids, (
        "same corpus, same query, same pinned moment -- which retriever "
        "answered must not change which items are in the window"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("not-a-timestamp", "RFC 3339"),
        ("2026-08-01T00:00:00", "UTC offset"),
        ("2026-13-40T00:00:00Z", "RFC 3339"),
        ("2026-08-01T00:00:00Z" + "0" * 200, "characters long"),
    ],
    ids=["garbage", "no-offset", "invalid-calendar-date", "absurdly-long"],
)
async def test_an_unparseable_as_of_is_a_clean_tool_error(
    as_of_registry: ProjectRegistry, raw: str, expected: str
) -> None:
    """Validated the way the surface validates everything else: a boundary
    check in `_parse_as_of` that raises `ToolError` with a remedy, before the
    value can reach `ValidityPeriod.contains` -- which refuses a naive moment
    with a bare `DomainError` carrying no remedy at all, raised from inside a
    canonical read session rather than from the tool surface. Mirrors
    `test_an_empty_query_is_refused` and
    `test_a_malformed_item_id_names_the_tool_that_finds_a_real_one`: a clean
    `ToolError`, never a traceback, and never an unbounded echo of the input.
    """
    message = await _call_failing(
        as_of_registry, "knowledge.search", projectId="as-of-demo", query=AS_OF_QUERY, asOf=raw
    )

    assert "asOf" in message
    assert expected in message
    assert len(message) < 500, f"the message must not grow with its input ({len(message)} chars)"


# -- #30 PR1: the present-only `integrity` damage signal --------------------
#
# The canonical store is derived and immutable once built (ADR-0004), so the
# active pointer that chose it records how many migrations it was built from --
# `ActiveState.migration_count` -- and a healthy project's live
# `migration_history` row count equals that number. PR1 discloses a difference
# through a present-only `integrity` object on all three read tools: present
# *only* when `live != expected`, absent otherwise. Absence asserts nothing --
# there is deliberately no `damageDetected: false` form -- because the check is
# incomplete by design (a migration-count mismatch, and nothing finer), and a
# `false` token would read as "checked and clean" over a detector that is not.
#
# The tests below hold four things, each failing independently:
#   * the detector actually *runs* -- a real mismatch surfaces `integrity` from
#     each of search / status / get (RED the moment that tool's emission is
#     unplugged, which is the whole point of PR1 over merely shaping the field);
#   * a healthy build emits nothing, single-project and with a sibling's rows in
#     the same file (the "absence" side of the present-only contract);
#   * the signal carries no bit about withheld content (the closure argument);
#   * the added `COUNT(*)` stays on its covering index, off the O(withheld)
#     timing channel #158 and #19 closed.


#: The action every `integrity` remedy names. Matched as a substring rather than
#: pinned whole, so this reads the published surface and not the wording, which
#: the mcp agent may still revise -- but `migrate apply` is the rebuild, and a
#: remedy that stopped naming a runnable rebuild would leave a caller stuck.
INTEGRITY_REMEDY_ACTION = "migrate apply"

#: The two further things the remedy has to name, in this order.
#:
#: The remedy used to be that one command and nothing else, and measurement found
#: it cannot cure every shape it is emitted for: against a *surplus*
#: `migration_history` row there is nothing pending, so `migrate apply` exits 0
#: reporting `applied: [], changed: false` and the signal is still there
#: afterwards. A remedy a caller can run three times to no effect is worse than
#: no remedy, because it reads as "already fixed".
#:
#: So the string prescribes a fallback, and the third step is load-bearing rather
#: than decorative: deleting the derived state deletes the published index with
#: it, so a caller who stops after the rebuild has a project that answers but no
#: longer ranks. Order is asserted as well as presence, because "rebuild the
#: index" before "delete the state" cures nothing.
INTEGRITY_REMEDY_FALLBACK = (".theurian/state/", "theurian index build")


def _state_database(registry: ProjectRegistry, project_id: str = "demo") -> tuple[Path, Any]:
    """The state database file and the active pointer a tool would resolve.

    Read the same way `_resolve` reads them, so a test damages exactly the file
    the tool reads and compares against exactly the pointer the tool trusts.
    """
    paths = ProjectPaths.of(Path(registry.load()[project_id]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state to damage"
    return paths.state / active.database_filename, active


def _drop_one_migration_history_row(database: Path) -> int:
    """Delete the draft migration's history row, so ``live == expected - 1``.

    A single lost row is the minimal, unambiguous mismatch: `live` drops by
    exactly one while the pointer's `migration_count` is untouched, so the
    detector's `live != expected` is the only reason a signal appears. The
    knowledge items are left intact, so `knowledge.get` still reads its item and
    exercises the *success*-path emission rather than a refusal.
    """
    connection = sqlite3.connect(database)
    try:
        changed = connection.execute(
            "DELETE FROM migration_history WHERE migration_id = ?", (DRAFT_ID,)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return changed


@pytest.mark.asyncio
async def test_a_lost_migration_row_surfaces_integrity_from_knowledge_search(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. The detector runs on `knowledge.search`, not just the field's shape.

    What the detector measures is one number against one number: this project's
    live `migration_history` row count, against the `migrationCount` the active
    pointer that chose the state database records. A lost row moves the first and
    not the second, and the field discloses the difference.

    **The search still answers, and that is the point** -- `count: 1` below, not
    `count: 0`. Losing a migration-history row does not empty a response: the
    `knowledge_items` rows are untouched, so the retrievers find what they always
    found, and nothing in the answer itself says the state that produced it is
    damaged. The `integrity` key is the only thing that does.

    This test measures the migration comparison alone. PR2 added a second one --
    the live surfaceable-item count against the count `migrate apply` recorded --
    which is what now sets the key for a sentinel in `knowledge_items.project_id`,
    the corruption that answers `count: 0, results: []` with `stale: false`. A
    sentinel in `item_id` moves neither count and still leaves the key absent
    (#30).

    RED the moment search's `integrity` emission is unplugged (return `answer`
    unconditionally, or force the detector to `None`), which is what proves PR1
    wired the detector into this path and did not merely define the object.
    """
    database, _ = _state_database(registry)
    assert _drop_one_migration_history_row(database) == 1, (
        "the draft migration's history row must exist, or nothing is damaged and this is vacuous"
    )

    result = await _call(registry, "knowledge.search", projectId="demo", query="token")

    assert result["count"] == 1, "the search still answers its approved item"
    assert result["integrity"]["damageDetected"] is True
    assert INTEGRITY_REMEDY_ACTION in result["integrity"]["remedy"]


@pytest.mark.asyncio
async def test_a_lost_migration_row_surfaces_integrity_from_knowledge_status(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. The detector runs on `knowledge.status`, and `appliedMigrations`
    holds the pointer's count rather than the shrunken live read.

    RED the moment status's `integrity` emission is unplugged. The
    `appliedMigrations` assertion is the second half of the same fix: it is
    published from `active.migration_count`, so it reports two even while the
    live migration-row count has fallen to one -- the honest number, disclosed
    beside a signal that says the two disagree, not the silent under-report PR1
    removes.
    """
    database, active = _state_database(registry)
    assert active.migration_count == 2, "the fixture applies two migrations"
    assert _drop_one_migration_history_row(database) == 1, "nothing damaged; the test is vacuous"

    result = await _call(registry, "knowledge.status", projectId="demo")

    assert result["integrity"]["damageDetected"] is True
    assert INTEGRITY_REMEDY_ACTION in result["integrity"]["remedy"]
    assert result["appliedMigrations"] == 2, (
        "appliedMigrations is the pointer's own count and must not shrink with the lost row"
    )


@pytest.mark.asyncio
async def test_a_lost_migration_row_surfaces_integrity_from_knowledge_get(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. The detector runs on `knowledge.get`'s success path.

    The item is read -- its body comes back intact -- and the signal still
    applies, because damage elsewhere in the migration history means the
    response was assembled from a state holding less than its pointer records.
    RED the moment get's success-path `integrity` emission is unplugged.
    """
    database, _ = _state_database(registry)
    assert _drop_one_migration_history_row(database) == 1, "nothing damaged; the test is vacuous"

    result = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert result["body"] == BODY, "the item itself still reads, so this is the success path"
    assert result["integrity"]["damageDetected"] is True
    assert INTEGRITY_REMEDY_ACTION in result["integrity"]["remedy"]


@pytest.mark.asyncio
async def test_a_healthy_build_emits_no_integrity_field_from_any_tool(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1, M1. The "absence" side of the present-only contract.

    A healthy project has `live == expected`, so the key is *genuinely absent*
    from every tool -- not `damageDetected: false`, which would assert a clean
    bill the detector cannot honestly give. RED the moment the detector is made
    to emit on a match (always-present), which is the mistake this pins against.

    The guard is what stops the absence being vacuous: the build really applied
    two migrations and really holds two live rows, so the detector evaluated
    `2 == 2` and *chose* to stay silent -- the same code path the mismatch tests
    above prove will speak.
    """
    database, active = _state_database(registry)
    connection = sqlite3.connect(database)
    try:
        live = connection.execute(
            "SELECT COUNT(*) FROM migration_history WHERE project_id = ?", ("demo",)
        ).fetchone()[0]
    finally:
        connection.close()
    assert live == active.migration_count == 2, (
        "the healthy build must hold as many live rows as its pointer records, or its silence "
        "is an accident rather than the detector choosing absence on a match"
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert "integrity" not in search, "a healthy search must not report damage"
    assert "integrity" not in status, "a healthy status must not report damage"
    assert "integrity" not in got, "a healthy get must not report damage"


@pytest.mark.asyncio
async def test_a_sibling_projects_rows_in_the_same_file_forge_no_mismatch(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1, M1 (multi-project). The COUNT is scoped, so another project's rows
    do not inflate `live` into a false mismatch.

    The detector treats `live > expected` as damage too (`!=`, not `<`), so a
    count that forgot its `WHERE project_id = ?` would read every project's rows
    and forge a signal on a healthy project the moment a second project's
    migration history shared the file. Five foreign rows are written in beside
    demo's two; a scoped count still answers two, so the healthy project stays
    silent. Drop the `project_id` predicate and this goes RED.
    """
    database, active = _state_database(registry)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for seq in range(5):
            connection.execute(
                "INSERT INTO migration_history "
                "(migration_id, project_id, checksum, applied_at, sequence) VALUES (?, ?, ?, ?, ?)",
                (f"sibling-migration-{seq}", "sibling", "c" * 64, "2026-08-02T10:00:00+00:00", seq),
            )
        connection.commit()
        total = connection.execute("SELECT COUNT(*) FROM migration_history").fetchone()[0]
    finally:
        connection.close()
    assert total == active.migration_count + 5, (
        "the sibling project's rows must really land in the shared file, or a scoped and an "
        "unscoped count would agree for the wrong reason"
    )

    status = await _call(registry, "knowledge.status", projectId="demo")
    search = await _call(registry, "knowledge.search", projectId="demo", query="token")

    assert "integrity" not in status, "a sibling project's rows must not forge damage on demo"
    assert "integrity" not in search, "a sibling project's rows must not forge damage on demo"
    assert status["appliedMigrations"] == active.migration_count


def _corrupt_migration_project_id(database: Path, sentinel: str = "not-a-project") -> int:
    """Overwrite every `migration_history.project_id`, dropping demo's rows out of
    the `WHERE project_id = 'demo'`.

    This is SILENTLY_EMPTIED member #5 as it was: a sentinel here made the tool
    answer `appliedMigrations: 0` against a project that had applied several.
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        changed = connection.execute(
            "UPDATE migration_history SET project_id = ?", (sentinel,)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return changed


@pytest.mark.asyncio
async def test_a_corrupt_migration_project_id_is_disclosed_not_silently_emptied(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. The position that left SILENTLY_EMPTIED: member #5 is now caught.

    Corrupting `migration_history.project_id` drops every row out of demo's
    `WHERE`, so `live` reads zero. Before PR1 `knowledge.status` published that
    zero as `appliedMigrations: 0` -- a successful, false statement to an agent
    that the project had applied nothing. Now the tool reports `appliedMigrations`
    from the pointer's own count (so it does not shrink) and emits `integrity`
    because the live count disagrees. This is RED both if the emission is
    unplugged and if `appliedMigrations` reverts to the live read.
    """
    database, active = _state_database(registry)
    assert active.migration_count == 2, "the fixture applies two migrations"
    assert _corrupt_migration_project_id(database) == 2, (
        "both migration rows must take the sentinel, or nothing is emptied and this is vacuous"
    )

    result = await _call(registry, "knowledge.status", projectId="demo")

    assert result["integrity"]["damageDetected"] is True
    assert INTEGRITY_REMEDY_ACTION in result["integrity"]["remedy"]
    assert result["appliedMigrations"] == 2, (
        "appliedMigrations must report the pointer's two, not the silently-emptied live zero -- "
        "the member #5 under-report PR1 removed"
    )


@pytest.mark.asyncio
async def test_the_integrity_signal_is_identical_across_a_withheld_only_difference(
    read_cost_corpora: _ReadCostCorpora,
) -> None:
    """#30. The closure argument: `integrity` carries no bit about withheld content.

    The two corpora hold the *same* one migration and the same three approved
    items, and differ only in the twenty-five `rejected` items the heavy one also
    holds -- items a caller may not read. So whether the key appears must be
    identical between them: absent in both. One query against two corpora, which
    is the form of argument that closes a disclosure class rather than a field.

    **Both comparisons are blind to a retired row, and for two different
    reasons.** The migration comparison (PR1) reads `migration_history` against
    the pointer's `migrationCount`, and neither knows an item exists. The
    surfaceable comparison (PR2) *does* count `knowledge_items` -- so the
    blindness there is not structural but a property of the predicate, and it has
    to be the same predicate twice: `record_expected_surfaceable_count` counts
    `SURFACEABLE_STATUSES` alone inside `migrate apply`'s transaction, and
    `count_surfaceable_items` counts `SURFACEABLE_STATUSES` alone on the request.
    A `rejected` row is on neither side. Measured on these two corpora: recorded
    3 and live 3 in both, over stores holding 3 and 28 rows.

    **So the mutation that would break it is a count over every row rather than
    the surfaceable ones, and only on one side.** Widen both sides together and
    the corpora stay healthy and the key stays absent; widen the *reader* alone
    and the heavy corpus reads 28 against a record of 3 while the baseline reads
    3 against 3 -- the key appears on exactly the project that holds withheld
    content, which is the leak. Measured, on the two independent readers the
    three surfaces use:

    - dropping the status predicate from `count_surfaceable_items` -- the read
      `knowledge.search` and `knowledge.get` share -- fails the equality below on
      `knowledge.search`;
    - dropping it from `count_surfaceable_by_status`, whose sum
      `knowledge.status` passes in instead, fails the same equality on
      `knowledge.status` with `itemsByStatus` carrying `rejected: 25`.

    Both assertions are needed and they fail differently. The first is the
    property; the second exists because a build where *both* corpora reported
    damage would satisfy an equality of presence while saying nothing. Mirrors
    the #19 status differential.
    """
    corpora = read_cost_corpora

    # Guard: the pair really differs only by withheld content, so an equal signal
    # below is equal *because* the signal ignores it -- not because the corpora
    # are the same size.
    withheld = {item for item, status in corpora.stored.items() if status == "rejected"}
    assert len(withheld) == READ_COST_WITHHELD, (
        f"the heavy corpus must hold {READ_COST_WITHHELD} withheld items for this to test "
        f"withheld-independence; it holds {len(withheld)}"
    )
    assert len(corpora.stored) == 3 + READ_COST_WITHHELD, (
        "the heavy corpus must be larger than the baseline, or this compares two equal corpora"
    )

    calls: tuple[tuple[str, dict[str, Any]], ...] = (
        ("knowledge.search", {"query": "approved"}),
        ("knowledge.status", {}),
        ("knowledge.get", {"itemId": "architecture.read-approved-0"}),
    )
    for tool, extra in calls:
        baseline = await _call(corpora.registry, tool, projectId=READ_COST_BASELINE_ID, **extra)
        heavy = await _call(corpora.registry, tool, projectId=READ_COST_HEAVY_ID, **extra)

        assert ("integrity" in baseline) == ("integrity" in heavy), (
            f"{tool}: whether `integrity` appears changed with withheld content alone -- the "
            f"signal is carrying a bit about what the caller may not read (SEC-13)"
        )
        assert "integrity" not in baseline and "integrity" not in heavy, (
            f"{tool}: both corpora are healthy, so neither may report damage -- an equal signal "
            f"that was present in both would pass the line above for the wrong reason"
        )


@contextlib.contextmanager
def _one_row_statement_the_store_runs() -> Iterator[dict[str, Any]]:
    """Capture the SQL a single-row store method hands to its reader, at runtime.

    The sibling of `_statement_the_store_runs` for `_read_one`, which
    `count_migration_history` uses. Read off the reader as the method builds it,
    never restated, so the plan below is checked against the statement the tool
    truly runs rather than a copy that would drift.
    """
    captured: dict[str, Any] = {}
    real_read_one = SqliteCanonicalStore._read_one

    def spy(
        store: SqliteCanonicalStore,
        sql: str,
        parameters: tuple[str, ...],
        mapper: Callable[[sqlite3.Row], Any],
    ) -> Any:
        captured["sql"] = sql
        captured["params"] = parameters
        return real_read_one(store, sql, parameters, mapper)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SqliteCanonicalStore, "_read_one", spy)
        yield captured


@pytest.mark.asyncio
async def test_the_search_integrity_count_is_answered_by_a_covering_index(
    read_cost_corpora: _ReadCostCorpora,
) -> None:
    """#30 PR1, M2. The added `COUNT(*)` does not reopen the O(withheld) timing channel.

    `knowledge.search` reads `count_migration_history` on every request. Its cost
    stays off the corpus because it is served by
    `idx_migration_history_sequence(project_id, sequence)` as a covering-index
    scan of one project's migration rows -- O(migrations), never a table scan and
    never `knowledge_items`, so it cannot carry the withheld count the way the
    ranked and scan reads once did (#158, #19). Mirrors
    `test_status_count_is_answered_by_a_covering_index`.

    Two halves, because two mutations must each turn it RED:

    * the captured statement carries `INDEXED BY idx_migration_history_sequence`.
      Dropping the hint is RED here even though SQLite would pick the same index
      on its own -- the hint is what makes a *lost* index fail loudly rather than
      fall to a silent table scan (the store's own reasoning);
    * SQLite *seeks* into that covering index. Dropping or renaming the index
      makes the hinted read raise `no such index`, so the store call below raises
      rather than reaching the assertion; reversing its declared columns to
      ``(sequence, project_id)`` keeps the index and the hint and turns the seek
      into a full walk of every project's migration entries -- measured at 172x
      the work, and `USING COVERING INDEX idx_migration_history_sequence` is in
      that plan too, which is why the index name alone was not enough to assert.
      The seek form is: ``SEARCH`` + the index + the ``(project_id=?`` that opens
      the constraint list. The reversal plans ``SCAN migration_history USING
      COVERING INDEX idx_migration_history_sequence`` and fails two of the three.
    """
    corpora = read_cost_corpora
    paths = ProjectPaths.of(Path(corpora.registry.load()[READ_COST_HEAVY_ID]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    db_path = paths.state / active.database_filename

    # The store is entered first so its connection opens before the capture is
    # installed -- that open does not go through `_read_one`, so the one call the
    # capture sees is `count_migration_history`'s own statement.
    with SqliteCanonicalStore(db_path) as store, _one_row_statement_the_store_runs() as captured:
        store.count_migration_history(ProjectId(READ_COST_HEAVY_ID))

    assert captured, (
        "count_migration_history ran no statement through _read_one, so the plan below would "
        "describe a query the tool never runs -- the capture watches the wrong reader"
    )
    assert "INDEXED BY idx_migration_history_sequence" in captured["sql"], (
        "the migration COUNT dropped its INDEXED BY hint; without it a dropped or renamed index "
        "falls to a silent table scan rather than failing loudly, and the O(migrations) bound "
        f"stops being structural. Statement:\n{captured['sql']}"
    )
    plan = _query_plan(db_path, captured["sql"], captured["params"])

    for fragment in (
        "SEARCH",
        "USING COVERING INDEX idx_migration_history_sequence",
        "(project_id=?",
    ):
        assert fragment in plan, (
            f"the search integrity COUNT is no longer a seek into the covering index "
            f"idx_migration_history_sequence(project_id, sequence): {fragment!r} is missing. "
            f"SQLite planned:\n{plan}\n"
            "Without that seek the read walks every project's migration entries -- a cost the "
            "corpus can move, reopening the O(withheld) channel #158 and #19 closed (SEC-13)."
        )


@pytest.mark.asyncio
async def test_the_surfaceable_integrity_count_is_answered_by_a_covering_index(
    read_cost_corpora: _ReadCostCorpora,
) -> None:
    """#30 PR2, SEC-13, T-17. The surfaceable COUNT does not read the withheld rows.

    `knowledge.search` and `knowledge.get` compare a live surfaceable-item count
    against `project_integrity` on every request (#30 PR2), and that count is read
    by `count_surfaceable_items` -- the one integrity reader the two covering-index
    tests above do not cover. Its cost must stay off the withheld rows: forced
    through `idx_items_status(project_id, status)` as a covering-index *seek*, so a
    retired row is never read and the response time cannot carry the withheld count
    (#158, #19). Mirrors `test_status_count_is_answered_by_a_covering_index`.

    Two halves, each RED on its own mutation:

    * the captured statement carries `INDEXED BY idx_items_status`. Dropping the
      hint is RED here even though SQLite picks the same index unaided (the store's
      own measurement, 133 -> 134 -> 134): the hint is what makes a lost or renamed
      index fail loudly rather than fall to a scan that reads the withheld rows;
    * SQLite *seeks* into that covering index. Dropping or renaming the index makes
      the hinted read raise `no such index`, so the store call below raises rather
      than reaching the assertion; reversing the declared columns to
      ``(status, project_id)`` keeps the phrase but plans ``(status=? AND
      project_id=?)`` and fails the third fragment.
    """
    corpora = read_cost_corpora
    paths = ProjectPaths.of(Path(corpora.registry.load()[READ_COST_HEAVY_ID]["rootPath"]))
    active = read_active_state(paths)
    assert active is not None, "the fixture must have built a canonical state"
    db_path = paths.state / active.database_filename
    context = RequestContext(project_id=ProjectId(READ_COST_HEAVY_ID))

    # The store is entered first so its connection opens before the capture is
    # installed -- that open does not go through `_read_one`, so the one call the
    # capture sees is `count_surfaceable_items`'s own statement.
    with SqliteCanonicalStore(db_path) as store, _one_row_statement_the_store_runs() as captured:
        store.count_surfaceable_items(context)

    assert captured, (
        "count_surfaceable_items ran no statement through _read_one, so the plan below would "
        "describe a query the tool never runs -- the capture watches the wrong reader"
    )
    assert "INDEXED BY idx_items_status" in captured["sql"], (
        "the surfaceable COUNT dropped its INDEXED BY hint; without it a dropped or renamed index "
        "falls to a scan that reads the withheld rows rather than failing loudly, and the "
        f"O(surfaceable) bound stops being structural. Statement:\n{captured['sql']}"
    )
    plan = _query_plan(db_path, captured["sql"], captured["params"])

    for fragment in ("SEARCH", "USING COVERING INDEX idx_items_status", "(project_id=?"):
        assert fragment in plan, (
            f"count_surfaceable_items is no longer a seek into the covering index "
            f"idx_items_status(project_id, status): {fragment!r} is missing. SQLite planned:\n"
            f"{plan}\n"
            "Without that seek the surfaceable count reads the project's withheld rows, and the "
            "search / get response time carries the withheld count again (SEC-13, T-17, #158)."
        )


def test_the_recorded_surfaceable_count_is_written_through_the_covering_index(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2, SEC-13, T-17. The writer records the count through the covering index.

    The writer half of the pair the readers above pin. `migrate apply` records the
    surfaceable count with an `INSERT ... SELECT COUNT(*)`, and that `COUNT` runs
    over `idx_items_status` for the same reason the readers' does: the force is
    structural, so a dropped or renamed index fails the build loudly rather than
    silently counting through a scan.

    `record_expected_surfaceable_count` runs its statement straight on the
    connection, not through `_read_one`/`_read_all`, so the reader spies cannot see
    it. The statement is read off the connection's trace callback as it executes --
    never restated -- and planned on that same connection, so the plan is the one
    SQLite forms over the shipped schema.

    Two halves, each RED on its own mutation, as in the reader siblings: the
    captured statement carries `INDEXED BY idx_items_status`, and SQLite seeks into
    the covering index (`SEARCH` + the index + `(project_id=?`). Reversing the
    declared columns keeps the phrase but plans ``(status=? AND project_id=?)``.
    """
    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    database, _ = _state_database(registry)

    captured: list[str] = []
    with write_transaction(database, paths.write_lock) as connection:
        # Installed after the connection is open and past its `BEGIN`, so the only
        # statement it sees is the writer's own INSERT; unset before the plan below
        # so the `EXPLAIN QUERY PLAN` probe is not captured back into it.
        connection.set_trace_callback(captured.append)
        SqliteWriter(connection).record_expected_surfaceable_count(ProjectId("demo"))
        connection.set_trace_callback(None)
        insert = next(
            (
                sql
                for sql in captured
                if "project_integrity" in sql and sql.lstrip().upper().startswith("INSERT")
            ),
            None,
        )
        assert insert is not None, (
            f"record_expected_surfaceable_count ran no INSERT the trace could see: {captured}"
        )
        plan = "\n".join(str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN " + insert))

    assert "INDEXED BY idx_items_status" in insert, (
        "the recording COUNT dropped its INDEXED BY hint; a dropped or renamed index then counts "
        f"through a scan at build time rather than failing loudly. Statement:\n{insert}"
    )
    for fragment in ("SEARCH", "USING COVERING INDEX idx_items_status", "(project_id=?"):
        assert fragment in plan, (
            f"record_expected_surfaceable_count no longer records through a seek into the covering "
            f"index idx_items_status(project_id, status): {fragment!r} is missing. SQLite "
            f"planned:\n{plan}"
        )


# -- #30 PR1: what `knowledge.get` says when the damage *is* the absence -----
#
# `knowledge.get` publishes no field on a refusal, so its half of the integrity
# signal is a different *message*: "could not be fully read", naming the rebuild,
# instead of the "is not present" that a genuinely absent id earns. Two things
# have to hold and they fail separately -- the branch has to be reached, and it
# has to say something other than absence -- which is why both are asserted here
# rather than one standing in for the other.


#: The phrase that distinguishes `get`'s damage refusal from its absence
#: refusal. Matched as a substring of the published message, because `get` has
#: no `integrity` field to read: the message *is* the signal.
#:
#: Face-independent since PR2 (#30). The detector now takes two measurements --
#: migration rows against the pointer, surfaceable items against what the writer
#: recorded -- and the message names neither, because a message that said which
#: one fired would answer, over a damaged database, a question about what the
#: state holds that this tool refuses to answer over a healthy one.
GET_DAMAGE_PHRASE = "disagrees with its own records about what it holds"

#: What `get` says for an id that is simply not there -- and, by SEC-13, for one
#: it is withholding. Pinned here so the damage message can be asserted *unequal*
#: to it: a damage branch that reached for this text would be reporting damage as
#: absence, which is exactly the #30 under-report on the `get` surface.
GET_ABSENCE_PHRASE = "is not present in project"


@pytest.mark.asyncio
async def test_an_absent_item_over_a_damaged_state_is_refused_as_damage_not_absence(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. `knowledge.get` must not report damage as absence.

    "Not present" is a claim about a store somebody could read in full. When the
    live `migration_history` count disagrees with the pointer's, nobody can: an
    item present in the canonical migrations may be missing from the derived
    state, so the honest answer is "could not be fully read", with the rebuild
    named. Answering "is not present" instead tells an agent that a decision does
    not exist when the truth is that the state holding it is damaged -- the same
    silent under-report `appliedMigrations: 0` was on `knowledge.status`.

    The item asked for really is absent from the store, so the *absence* branch
    is the one the tool would otherwise take -- which is what makes this a test
    of the damage branch and not of the corruption. Two mutations turn it RED for
    two different reasons, and neither assertion catches both:

    * forcing the damage branch off (`if False:`) drops through to the absence
      refusal, so the damage phrase is missing;
    * swapping the damage message for the absence text keeps the branch and
      loses the distinction, so the absence phrase is present.
    """
    database, active = _state_database(registry)
    assert active.migration_count == 2, "the fixture applies two migrations"
    assert _corrupt_migration_project_id(database) == 2, (
        "both migration rows must take the sentinel, or the state is undamaged and the tool "
        "would refuse with absence for the honest reason"
    )

    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    assert GET_DAMAGE_PHRASE in message, (
        f"`knowledge.get` answered for a damaged state without saying so: {message!r}"
    )
    assert GET_ABSENCE_PHRASE not in message, (
        f"`knowledge.get` reported damage as absence, which is the #30 under-report on this "
        f"surface: {message!r}"
    )
    assert INTEGRITY_REMEDY_ACTION in message, "a damage refusal must name the rebuild"


@pytest.mark.asyncio
async def test_an_absent_item_over_a_healthy_state_is_refused_as_absence(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1, the other side. The damage message is reserved for damage.

    Without this, the test above is satisfied by a `get` that answers every
    unknown id with "could not be fully read" -- a tool that cries damage over a
    healthy project, whose refusals then say nothing at all. The two together are
    what make the message a signal: absence here, damage there, on the same call
    against the same fixture with one row count changed.
    """
    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    assert GET_ABSENCE_PHRASE in message, (
        f"a healthy project must refuse an unknown id as absent: {message!r}"
    )
    assert GET_DAMAGE_PHRASE not in message, f"a healthy project reported damage: {message!r}"


# -- #30 PR1: a surplus row is damage too (the `!=` more-side) ---------------


def _add_a_foreign_migration_history_row(database: Path, project_id: str = "demo") -> None:
    """Write one extra `migration_history` row *for this project*, so live > expected.

    The direction no fixture reached. Every other damage in this file removes a
    row or drops it out of the `WHERE`, so `live < expected` was the only side
    measured -- and `>=` in place of `!=` survived the whole suite because of it.
    This is the shape the store's own docstring names: another project's rows
    reaching this one, here written directly rather than by corrupting a
    `project_id`, so `expected` is untouched and the surplus is the only change.
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO migration_history "
            "(migration_id, project_id, checksum, applied_at, sequence) VALUES (?, ?, ?, ?, ?)",
            ("01K1ZZZZZZ01234567890ABCDE", project_id, "d" * 64, "2026-08-02T13:00:00+00:00", 99),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_a_surplus_migration_row_is_damage_on_every_read_tool(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. The detector compares with `!=`, so *more* rows are damage as well.

    The state database is immutable once built, so a live count above the
    pointer's is not a project that got ahead -- it is rows that were never
    applied here, and an answer assembled from them is an answer from a state
    nobody recorded. `<` or `>=` in place of `!=` calls that healthy, and no
    fixture in this file could tell: every other damage it writes *removes*
    reach, so `live > expected` had never been built at all.

    All three surfaces are asserted in one test because the claim is one claim --
    the direction of a single comparison -- and it reaches a caller three
    different ways: a field on `search`, a field on `status`, and a *message* on
    `get`, which publishes no field on a refusal.
    """
    database, active = _state_database(registry)
    _add_a_foreign_migration_history_row(database)
    connection = sqlite3.connect(database)
    try:
        live = connection.execute(
            "SELECT COUNT(*) FROM migration_history WHERE project_id = ?", ("demo",)
        ).fetchone()[0]
    finally:
        connection.close()
    assert live == active.migration_count + 1 == 3, (
        f"the surplus row must really land in this project's history: live={live}, "
        f"pointer={active.migration_count}. Without it this measures a healthy project."
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    assert search["integrity"]["damageDetected"] is True, (
        "knowledge.search called a surplus migration row healthy; the comparison has become a "
        "shortfall test and rows that were never applied here are answering queries"
    )
    assert status["integrity"]["damageDetected"] is True, (
        "knowledge.status called a surplus migration row healthy"
    )
    assert status["appliedMigrations"] == 2, (
        "appliedMigrations is the pointer's own count and must not follow the surplus row"
    )
    assert GET_DAMAGE_PHRASE in message, (
        f"knowledge.get called a surplus migration row healthy: {message!r}"
    )


# -- #30 PR1: why the remedy has a second sentence --------------------------


def _apply_returning_its_report(root: Path) -> dict[str, Any]:
    """Run the remedy's first command against ``root``, and return what it printed.

    The working directory is set *in the same call* that runs the CLI rather than
    inherited from an earlier one: `migrate apply` resolves the project from
    ``Path.cwd()`` and takes no argument that says where, so a test leaning on a
    fixture's `chdir` is one refactor away from applying against this checkout.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(root)
        result = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    report: dict[str, Any] = json.loads(result.stdout)
    return report


@pytest.mark.asyncio
async def test_a_plain_apply_does_not_cure_a_surplus_migration_row(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. The measured fact the remedy's fallback sentence rests on.

    `theurian migrate apply` reconciles the migration *files* against the history
    it finds, so it cures a shortfall -- a lost row is pending again, and the
    apply writes it back. It cannot cure a surplus: the row is already recorded,
    nothing is pending, and the command exits 0 reporting `applied: []` and
    `changed: false` while the signal it was named as the cure for is still
    there. A caller who runs it and re-reads the response is told the same thing
    it was told before, by a command that reported success.

    That is why the remedy gained a second sentence, and why this is pinned
    rather than left as a measurement in a document: the sentence is *predicated*
    on this behaviour. If a future change made a plain apply silently reconcile a
    surplus by deleting rows the pointer does not account for, this goes RED --
    and that would be worth noticing on its own, because deleting canonical
    history to make a signal go away is a larger decision than a remedy's
    wording.

    Three consecutive applies, which is what was measured: one apply proves the
    command does not cure it, and three prove the caller cannot get there by
    repeating it.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    database, _ = _state_database(registry)
    _add_a_foreign_migration_history_row(database)

    before = await _call(registry, "knowledge.status", projectId="demo")
    assert "integrity" in before, (
        "the surplus row did not produce a signal, so there is nothing for an apply to fail to "
        "cure and this test would pass over a healthy project"
    )

    for attempt in range(1, 4):
        report = _apply_returning_its_report(root)

        assert report["applied"] == [], (
            f"apply #{attempt} applied {report['applied']}; a surplus row is not a pending "
            f"migration, so an apply that has something to do here is reconciling the history "
            f"against the pointer -- a different behaviour than the remedy is written for"
        )
        assert report["changed"] is False, f"apply #{attempt} reported a change: {report}"
        after = await _call(registry, "knowledge.status", projectId="demo")
        assert after.get("integrity") == before["integrity"], (
            f"the integrity signal moved after apply #{attempt}: {before.get('integrity')} -> "
            f"{after.get('integrity')}. Either a plain apply now cures a surplus row -- in which "
            f"case the remedy's fallback sentence is describing a case that no longer exists -- "
            f"or it changed the signal without curing it"
        )


@pytest.mark.asyncio
async def test_the_published_remedy_names_the_fallback_and_the_index_rebuild(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1. A remedy that cannot cure the shape it is emitted for is not one.

    Asserted over the string a *caller receives*, on both surfaces that carry it
    -- the `integrity` object's `remedy` field and `knowledge.get`'s refusal
    message -- rather than over the module constant, so a tool that stopped
    publishing the constant would fail here too.

    Three fragments, each failing for its own regression: the first command
    (already pinned elsewhere, restated here because a fallback with no first
    step is not a remedy either), the state directory the fallback deletes, and
    the index rebuild that follows it. The order between the last two is
    asserted because it is the whole content of the third step: deleting the
    derived state deletes the published index with it, measured as
    `indexed: false` / `no-index` afterwards, and a caller told to rebuild the
    index *before* deleting the state rebuilds the one it is about to lose.

    RED against the single-command string this replaced.
    """
    database, _ = _state_database(registry)
    assert _drop_one_migration_history_row(database) == 1, "nothing damaged; the test is vacuous"

    published = await _call(registry, "knowledge.status", projectId="demo")
    refusal = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    for surface, remedy in (
        ("knowledge.status integrity.remedy", published["integrity"]["remedy"]),
        ("knowledge.get refusal", refusal),
    ):
        assert INTEGRITY_REMEDY_ACTION in remedy, f"{surface} names no rebuild command: {remedy!r}"
        for fragment in INTEGRITY_REMEDY_FALLBACK:
            assert fragment in remedy, (
                f"{surface} names no fallback for the shape a plain apply cannot cure "
                f"({fragment!r} is missing): {remedy!r}"
            )
        assert remedy.index(INTEGRITY_REMEDY_FALLBACK[1]) > remedy.index(
            INTEGRITY_REMEDY_FALLBACK[0]
        ), (
            f"{surface} tells a caller to rebuild the index before deleting the state that "
            f"holds it, so the rebuilt index is the one the next step throws away: {remedy!r}"
        )


# -- #30 PR1: the cells this tool stopped reading ---------------------------
#
# `knowledge.status` used to build its count through `applied_migrations`, which
# parses every migration row into a `MigrationId` and a `ContentHash` -- so a
# damaged `migration_id` or `checksum` made the tool *refuse*. It now runs a bare
# `COUNT(*)` that interprets no cell, so those two positions answer cleanly.
#
# That is a deliberate trade and not an oversight: tamper detection over the
# migration history is `theurian migrate status`'s job, which still exits 4 on
# both cells (measured; pinned in `test_canonical_store_corruption.py`). What
# must not happen is the read tools quietly answering with *less* than the
# database holds, and they do not: `appliedMigrations` comes from the pointer.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "rows"),
    [
        # The composite primary key is (project_id, migration_id), so both rows
        # cannot take the same sentinel id; one row is corrupted instead. One is
        # enough -- a single unreadable id used to refuse the whole call.
        ("migration_id", 1),
        ("checksum", 2),
    ],
)
async def test_status_answers_cleanly_over_a_migration_cell_it_no_longer_reads(
    registry: ProjectRegistry, column: str, rows: int
) -> None:
    """#30 PR1. A cell the count does not interpret cannot make this tool refuse.

    The bare `COUNT(*)` is what keeps the integrity check itself safe: a detector
    that parsed the rows it counted could be made to refuse -- or to quote a cell
    -- by the very damage it exists to report (#18). The cost is that these two
    cells no longer reach `knowledge.status` at all, which is why this pins the
    *clean* answer explicitly rather than leaving it as whatever falls out.

    Three assertions, because "did not refuse" alone would be satisfied by a tool
    that answered zeroes: the counts must be the true ones, `appliedMigrations`
    must be the pointer's two, and `integrity` must be absent -- the detector
    saw no discrepancy, because there is none in the row *count*.
    """
    database, active = _state_database(registry)
    assert active.migration_count == 2, "the fixture applies two migrations"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        changed = connection.execute(
            f"UPDATE migration_history SET {column} = ? "  # noqa: S608 - parametrized column name
            f"WHERE rowid IN (SELECT rowid FROM migration_history ORDER BY rowid LIMIT {rows})",
            ("ROTATE-ME sk-live-9f2a7c41d8e3",),
        ).rowcount
        connection.commit()
        held = connection.execute(
            f"SELECT COUNT(*) FROM migration_history WHERE {column} = ?",  # noqa: S608
            ("ROTATE-ME sk-live-9f2a7c41d8e3",),
        ).fetchone()[0]
    finally:
        connection.close()
    assert changed == held == rows, (
        f"migration_history.{column} did not take the sentinel ({changed} updated, {held} hold "
        f"it), so this call is being answered over an undamaged database"
    )

    result = await _call(registry, "knowledge.status", projectId="demo")

    assert result["itemCount"] == 2, "the item counts do not come from the migration history"
    assert result["appliedMigrations"] == 2, (
        "appliedMigrations must stay the pointer's own count over a damaged migration cell"
    )
    assert "integrity" not in result, (
        f"a damaged migration_history.{column} moved no row count, so the detector has nothing "
        f"to report -- an `integrity` key here means it is firing on something else: {result}"
    )


# -- #30 PR1: the healthy invariant survives more applies -------------------


def _live_and_expected(registry: ProjectRegistry, project_id: str = "demo") -> tuple[int, int]:
    """The detector's two operands, read the way it reads them.

    ``live`` straight from SQLite rather than through the store, so a store
    method that started answering from the pointer could not make the two agree
    by construction; ``expected`` from the pointer the tools resolve.
    """
    database, active = _state_database(registry, project_id)
    connection = sqlite3.connect(database)
    try:
        live = connection.execute(
            "SELECT COUNT(*) FROM migration_history WHERE project_id = ?", (project_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    return int(live), active.migration_count


@pytest.mark.asyncio
async def test_a_re_apply_and_a_third_migration_leave_every_tool_silent(
    registry: ProjectRegistry,
) -> None:
    """#30 PR1, M1. `live == expected` is an invariant of `migrate apply`, not of two.

    The absence tests above measure one build of two migrations. That leaves the
    invariant pinned at a single point, where an off-by-one in either operand --
    a pointer written before the last row lands, an idempotent re-apply that
    bumps `migrationCount` without writing a row -- would fire `integrity` on a
    project nobody damaged and turn the signal into noise a caller learns to
    ignore. So the pointer and the rows are compared again after a re-apply that
    changes nothing, and again after a third migration that changes both.

    The guard is the row count read straight from SQLite beside the pointer's:
    silence proves the detector chose it only if the two operands really are
    equal and really did move.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        # An apply with nothing pending: the pointer must not count what it did
        # not write.
        _run("migrate", "apply")
        after_reapply = _live_and_expected(registry)

        slug, title, letter = "retry-policy", "Retry policy", "C"
        (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
            f"# {title}\n\nEvery call carries a signed token, and retries reuse it.\n"
        )
        (root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{slug}.yaml").write_text(
            EXTRA_MIGRATION.format(letter=letter, slug=slug, title=title)
        )
        _run("migrate", "apply")
        after_third = _live_and_expected(registry)
    finally:
        monkey.undo()

    assert after_reapply == (2, 2), (
        f"an idempotent re-apply moved the operands to {after_reapply}; the detector would now "
        f"report damage on a project nobody touched"
    )
    assert after_third == (3, 3), (
        f"a third migration left the operands at {after_third}, so the build below either did "
        f"not happen or left the pointer disagreeing with its own rows"
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.retry-policy"
    )

    assert "integrity" not in search, "a healthy three-migration search must not report damage"
    assert "integrity" not in status, "a healthy three-migration status must not report damage"
    assert "integrity" not in got, "a healthy three-migration get must not report damage"
    assert status["appliedMigrations"] == 3, "the pointer must have counted the third migration"


# -- #30 PR1: a pointer that cannot be true is refused, not published --------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("knowledge.search", {"query": "token"}),
        ("knowledge.get", {"itemId": "architecture.auth-policy"}),
        ("knowledge.status", {}),
    ],
)
async def test_a_negative_migration_count_is_refused_by_every_read_tool(
    registry: ProjectRegistry, tool: str, arguments: dict[str, Any]
) -> None:
    """#30 PR1. `appliedMigrations` cannot be published below its own `minimum: 0`.

    `active.json` is a file on disk, and `migrationCount` was parsed as any
    integer -- so a hand edit to `-5` reached the wire verbatim as
    `appliedMigrations: -5`, in violation of the schema `knowledge.status`
    publishes. A strict client rejects that response *whole*, which throws away
    the `integrity` key riding on it: the one field that says the state is
    damaged is discarded by the damage.

    Refused at parse time instead, in the family a corrupt pointer already
    produces, so every tool that resolves the project refuses with the pointer's
    own remedy -- and the negative number reaches no successful response at all,
    because there is no successful response. All three are covered because
    `_resolve` is shared and a fix applied at one call site would leave the other
    two publishing it.

    RED the moment the range check leaves `ActiveState.from_json`: the pointer
    parses again, the tool answers, and `_call_failing` fails on DID NOT RAISE.
    """
    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    pointer = json.loads(paths.active_pointer.read_text(encoding="utf-8"))
    paths.active_pointer.write_text(json.dumps({**pointer, "migrationCount": -5}), encoding="utf-8")

    message = await _call_failing(registry, tool, projectId="demo", **arguments)

    assert "migrationCount" in message, (
        f"the refusal must name the field a user has to fix: {message!r}"
    )
    assert ACTIVE_POINTER_REMEDY in message, (
        f"{tool} refused a malformed pointer without the remedy that rebuilds it: {message!r}"
    )


# -- #30 PR2: the surfaceable-item comparison and the record it reads --------
#
# PR1 compared one number against one number. PR2 adds a second: the live count
# of `knowledge_items` whose status is surfaceable, against the count `migrate
# apply` wrote into `project_integrity` inside its own write transaction. Damage
# is either comparison differing, in either direction, and a project with no
# record at all is damage too.
#
# Every test below is written so that **only the new comparison can fire**: the
# migration operands are asserted equal first, so a signal that appeared for
# PR1's reason would be a failure of the guard rather than a pass. Four claims,
# each failing on its own mutation:
#
#   * the second comparison runs (a lost surfaceable item is disclosed on all
#     three tools while the migration count is untouched);
#   * a missing record is damage rather than "not recorded", which is what makes
#     the schema-version bump load-bearing;
#   * an apply that changes the store records the new count, so a healthy
#     rebuild is silent;
#   * an apply with nothing pending does *not* re-record, so the remedy's first
#     step cannot manufacture the all-clear it was run to earn.


def _expected_surfaceable_count(database: Path, project_id: str = "demo") -> int | None:
    """The writer's own record, read straight from SQLite.

    Never through `SqliteCanonicalStore.expected_surfaceable_count`, for the
    reason `_live_and_expected` reads its row count with a bare connection: a
    store method that started answering from the live count could otherwise make
    the detector's two operands agree by construction, and every assertion below
    would hold over a build that had stopped comparing anything.

    ``None`` means the row is absent, which is the state the detector reads as
    damage rather than as "not recorded".
    """
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT expected_surfaceable_count FROM project_integrity WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        connection.close()
    return None if row is None else int(row[0])


def _live_surfaceable_count(database: Path, project_id: str = "demo") -> int:
    """How many items a reader should be able to see, counted now.

    The other operand, read the same independent way. `draft` and `approved` are
    both surfaceable, so the fixture's two items are the two this counts -- which
    is asserted rather than assumed wherever it matters below.
    """
    connection = sqlite3.connect(database)
    try:
        statuses = tuple(sorted(status.value for status in SURFACEABLE_STATUSES))
        placeholders = ", ".join("?" for _ in statuses)
        count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_items "  # noqa: S608 - placeholders only
            f"WHERE project_id = ? AND status IN ({placeholders})",
            (project_id, *statuses),
        ).fetchone()[0]
    finally:
        connection.close()
    return int(count)


def _delete_the_draft_item(database: Path) -> int:
    """Take one surfaceable item out of the store and change nothing else.

    The minimal, unambiguous shape for the *second* comparison, and the mirror of
    `_drop_one_migration_history_row` for the first: `migration_history` is
    untouched, so PR1's operands stay equal and a signal can only have come from
    the item count. The **draft** rather than the approved item, so
    `knowledge.search`'s own `count` does not move either -- the response is
    byte-identical to the healthy one but for the `integrity` key, which is the
    sharpest form of "the answer looks fine and the state it came from is not".
    """
    connection = sqlite3.connect(database)
    try:
        changed = connection.execute(
            "DELETE FROM knowledge_items WHERE project_id = ? AND item_id = ?",
            ("demo", "architecture.caching-draft"),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return changed


@pytest.mark.asyncio
async def test_a_lost_surfaceable_item_is_damage_on_every_read_tool(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The second comparison runs, and it is the only one that can fire.

    PR1's detector reads `migration_history` and the pointer, and neither moves
    here: the guard asserts they are still equal before the call, so a build that
    kept only PR1's comparison answers all three of these calls clean and this
    goes RED. That is the mutation this test exists for -- deleting the
    `live_surfaceable == expected_surfaceable` term from `_integrity_signal`.

    `knowledge.search` still answers `count: 1`, because the item that vanished
    is the draft and no default search was ever going to return it. So the whole
    of what distinguishes this response from a healthy one is the `integrity`
    key: an agent reading the numbers alone has no way to know that the state it
    is reading lost a row. `knowledge.status` does publish a smaller `itemCount`,
    disclosed beside the signal rather than as a fact about the project.
    """
    database, active = _state_database(registry)
    assert _live_and_expected(registry) == (2, 2), (
        "the migration operands must be equal, or PR1's comparison could be what fires below "
        "and this says nothing about PR2's"
    )
    assert _expected_surfaceable_count(database) == 2, (
        "`migrate apply` must have recorded the fixture's two surfaceable items, or there is "
        "no expectation for the live count to disagree with"
    )
    assert _delete_the_draft_item(database) == 1, "nothing was lost; the test is vacuous"
    assert _live_surfaceable_count(database) == 1, "the deletion must move the live count"

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert search["integrity"]["damageDetected"] is True, (
        "knowledge.search answered over a state holding fewer items than its own record says, "
        "and said nothing -- the surfaceable comparison is not running"
    )
    assert search["count"] == 1, (
        "the search answer itself must be unchanged, or the signal is riding on a response that "
        "already looks wrong and this measures the wrong thing"
    )
    assert status["integrity"]["damageDetected"] is True, "knowledge.status disclosed nothing"
    assert status["itemCount"] == 1, "the live count is what it is; the signal is what discloses it"
    assert status["appliedMigrations"] == active.migration_count, (
        "the migration count must not move, or the guard above stopped holding mid-test"
    )
    assert got["integrity"]["damageDetected"] is True, (
        "knowledge.get's success path disclosed nothing"
    )
    for surface, published in (("search", search), ("status", status), ("get", got)):
        assert INTEGRITY_REMEDY_ACTION in published["integrity"]["remedy"], (
            f"knowledge.{surface} reported damage with no rebuild to run"
        )


@pytest.mark.asyncio
async def test_a_missing_integrity_record_is_damage_and_not_silence(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. "No record" is damage, which is what the schema bump buys.

    `expected_surfaceable_count` returning `None` could mean two things -- this
    database was written before the record existed, or it has lost one -- and the
    detector may only treat it as the second because `is_supported` refuses every
    older file outright -- exact match, so versions 1 and 2 alike (ADR-0017,
    `SCHEMA_VERSION` 3). Reading `None` as healthy is the tempting mutation, and
    it is what this goes RED against.

    Nothing else is touched: the migration operands are equal, the live
    surfaceable count still equals the number that was recorded, and every
    published integer is the one a healthy build produces. The record's *absence*
    is the entire difference between this response and a clean one.
    """
    database, _ = _state_database(registry)
    assert _live_and_expected(registry) == (2, 2), "the migration comparison must stay healthy"
    recorded = _expected_surfaceable_count(database)
    assert recorded == _live_surfaceable_count(database) == 2, (
        f"the record and the live count must agree before the record is removed, or the signal "
        f"below could be the ordinary count mismatch: recorded={recorded}"
    )

    connection = sqlite3.connect(database)
    try:
        removed = connection.execute(
            "DELETE FROM project_integrity WHERE project_id = ?", ("demo",)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert removed == 1, (
        "the record must exist to be removed, or this measures a database from before the table"
    )
    assert _expected_surfaceable_count(database) is None, "and it must really be gone"

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    for surface, published in (("search", search), ("status", status), ("get", got)):
        assert published["integrity"]["damageDetected"] is True, (
            f"knowledge.{surface} read a database with no integrity record and called it healthy; "
            f"a readable database this build can open was written by a build that records"
        )
    assert (search["count"], status["itemCount"], got["itemId"]) == (
        1,
        2,
        "architecture.auth-policy",
    ), (
        "every other published value must be the healthy one, or the signal above is riding on "
        "damage this test did not write"
    )


@pytest.mark.asyncio
async def test_a_lost_surfaceable_item_makes_get_refuse_an_absent_id_as_damage(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The second comparison reaches `get`'s refusal branch, not only its
    success path.

    `knowledge.get` publishes no field on a refusal, so its half of the signal is
    a different *message*, and the branch that chooses it is a separate call to
    the detector from the one on the success path -- a fix applied to one would
    leave the other reporting damage as absence. PR1 pinned that branch against a
    lost migration row; this is the same branch reached by the comparison PR2
    added, with the migration operands asserted equal so only the new one can
    fire.

    The distinction is the whole content: an agent told `'architecture.absent' is
    not present in project 'demo'` concludes the decision does not exist, when
    the truth is that the state which would hold it has lost a row. Both phrases
    are asserted, because the two mutations fail differently -- forcing the
    damage branch off drops through to absence, and reusing the absence text
    keeps the branch and loses the signal.
    """
    database, _ = _state_database(registry)
    assert _live_and_expected(registry) == (2, 2), (
        "the migration operands must be equal, or PR1's comparison is what reaches this branch"
    )
    assert _delete_the_draft_item(database) == 1, "nothing was lost; the test is vacuous"

    message = await _call_failing(
        registry, "knowledge.get", projectId="demo", itemId="architecture.absent"
    )

    assert GET_DAMAGE_PHRASE in message, (
        f"`knowledge.get` refused over a state holding fewer items than its own record says, "
        f"without saying so: {message!r}"
    )
    assert GET_ABSENCE_PHRASE not in message, (
        f"`knowledge.get` reported the lost row as the absence of the id that was asked for, "
        f"which is the #30 under-report on this surface: {message!r}"
    )
    assert INTEGRITY_REMEDY_ACTION in message, "a damage refusal must name the rebuild"


@pytest.mark.asyncio
async def test_an_apply_that_changes_the_store_records_the_new_count(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The recording half of `created or report.changed`, and its silence.

    A third migration adds a third surfaceable item, and the apply that writes it
    records three inside the same transaction -- into the *new* state database,
    because the migration set moved and so did the state hash (ADR-0016). So the
    detector's two operands are equal again and every tool is silent.

    RED if the recording call is removed: the new database carries no record,
    `expected_surfaceable_count` reads `None`, and all three tools report damage
    on a project nobody damaged. That is the mutation this pins, and it is the
    one a reader would otherwise call harmless -- the count is written by a
    command nothing else asserts about.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    before, _ = _state_database(registry)
    assert _expected_surfaceable_count(before) == 2, "the fixture must start with its two recorded"

    slug, title, letter = "retry-policy", "Retry policy", "C"
    (root / f".theurian/knowledge/architecture/{slug}.md").write_text(
        f"# {title}\n\nEvery call carries a signed token, and retries reuse it.\n"
    )
    (root / f".theurian/migrations/01K1{letter}AAAAA01234567890ABCDE-{slug}.yaml").write_text(
        EXTRA_MIGRATION.format(letter=letter, slug=slug, title=title)
    )
    report = _apply_returning_its_report(root)
    assert report["changed"] is True, f"the apply must have had something to do: {report}"

    after, _ = _state_database(registry)
    assert _live_surfaceable_count(after) == 3, "the third item must really be in the new store"
    assert _expected_surfaceable_count(after) == 3, (
        "the apply that wrote the third item did not record what a reader should now see, so "
        "every read of this fresh, healthy build reports damage"
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.retry-policy"
    )

    assert "integrity" not in search, "a freshly rebuilt project must not report damage"
    assert "integrity" not in status, "a freshly rebuilt project must not report damage"
    assert "integrity" not in got, "a freshly rebuilt project must not report damage"
    assert status["itemCount"] == 3, "and the count it publishes is the one that was recorded"


@pytest.mark.asyncio
async def test_a_pending_free_apply_does_not_re_record_over_a_damaged_state(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The *not*-recording half, which is the load-bearing one.

    `migrate apply` is step one of the remedy the signal publishes. An apply with
    nothing pending writes nothing, so re-recording there would count the rows
    the damaged state currently holds and store that as the new expectation --
    the signal would clear while the damage stood, and the remedy would
    manufacture the all-clear it was run to earn. Only the second step of the
    remedy (delete `.theurian/state/`, apply again) rebuilds the file, and that
    is the step that actually cures it.

    So the recording is conditioned on `created or report.changed`, and this is
    what goes RED when that condition is removed: the record moves from 2 to 1
    and all three tools fall silent over a store that is still missing a row.

    Three applies rather than one, for the reason
    `test_a_plain_apply_does_not_cure_a_surplus_migration_row` runs three: one
    shows the command does not clear the signal, three show a caller cannot get
    there by repeating it.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    database, _ = _state_database(registry)
    assert _expected_surfaceable_count(database) == 2, (
        "the fixture must start with its two recorded"
    )
    assert _delete_the_draft_item(database) == 1, "nothing was lost; the test is vacuous"

    before = await _call(registry, "knowledge.status", projectId="demo")
    assert "integrity" in before, (
        "the lost item produced no signal, so there is nothing for an apply to fail to clear and "
        "this test would pass over a healthy project"
    )

    for attempt in range(1, 4):
        report = _apply_returning_its_report(root)

        assert (report["applied"], report["changed"]) == ([], False), (
            f"apply #{attempt} had something to do ({report['applied']}, "
            f"changed={report['changed']}); a lost row is not a pending migration, so an apply "
            f"that changes here is reconciling the store against its record -- a different "
            f"behaviour than the condition is written for"
        )
        assert _expected_surfaceable_count(database) == 2, (
            f"apply #{attempt} re-recorded the expectation from the damaged state: the record is "
            f"now {_expected_surfaceable_count(database)}, taken from a store that is still "
            f"missing a row, and the signal a caller ran this command to cure has been cleared "
            f"rather than fixed"
        )
        after = await _call(registry, "knowledge.status", projectId="demo")
        assert after.get("integrity") == before["integrity"], (
            f"the integrity signal moved after apply #{attempt}: {before.get('integrity')} -> "
            f"{after.get('integrity')}, over a store that still holds "
            f"{_live_surfaceable_count(database)} of its 2 recorded items"
        )


# -- #30 PR2: an apply into an existing file records, and a surplus is damage ----


def _reset_state_to_an_uncommitted_apply(database: Path, state_hash: str) -> None:
    """Leave the state file present but empty of migrations, as an apply whose
    schema was created and whose migration transaction never committed would.

    `migrate apply` runs `create_database` *outside* its write transaction (#63),
    so an apply interrupted before that transaction commits leaves a file at the
    state hash's name holding the schema and nothing else -- no migration history
    and no `project_integrity` record. The active pointer and this installation's
    provenance for the hash are untouched, so the next apply finds the file present
    and trusted (`created` is False) and applies the pending migrations into it
    (`report.changed` is True): the arm of `created or report.changed` no other
    fixture reaches, because a changing apply otherwise moves the state hash and so
    writes a *new* file (`created` True).
    """
    for suffix in ("", "-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    create_database(database, state_hash, MIGRATION_ENGINE_VERSION)


@pytest.mark.asyncio
async def test_an_apply_into_an_existing_empty_database_re_records_the_count(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The `report.changed` half of `created or report.changed`.

    The recording condition has two arms and only `created` was ever exercised:
    every apply that changes the store also moves the state hash, so it writes a
    *new* database (`created` True) and the `report.changed` arm never fires. So
    `if created or report.changed:` -> `if created:` survives the whole suite, and
    a future change that let an apply reach an existing file with pending
    migrations would silently stop recording the count.

    This builds that shape directly: the state file is reset to a created-but-
    uncommitted apply -- schema present, no migration history, no
    `project_integrity` -- with the pointer and provenance for the hash left in
    place. The next `migrate apply` therefore reports `databaseCreated: false`,
    `changed: true`, and must re-record the count, or the freshly, correctly
    rebuilt project reports damage to every read tool.

    RED under `if created:`: `created` is False, so the record is never written,
    `expected_surfaceable_count` reads `None`, and all three tools report damage on
    a healthy project.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    database, active = _state_database(registry)
    assert _expected_surfaceable_count(database) == 2, (
        "the fixture must start with its two recorded"
    )

    _reset_state_to_an_uncommitted_apply(database, str(active.state_hash))
    assert _expected_surfaceable_count(database) is None, (
        "the reset must leave no record, or this measures an apply that had one to read"
    )

    report = _apply_returning_its_report(root)
    assert (report["databaseCreated"], report["changed"]) == (False, True), (
        f"the apply must reach the `report.changed` arm -- an existing file with pending "
        f"migrations -- or it exercises the `created` arm this test is not about: {report}"
    )

    after, _ = _state_database(registry)
    assert _live_surfaceable_count(after) == 2, "the two items must be back in the rebuilt store"
    assert _expected_surfaceable_count(after) == 2, (
        "the apply that applied a migration into the existing file did not record the count, so "
        "every read of this correctly rebuilt project reports damage"
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )
    assert "integrity" not in search, "a correctly rebuilt project must not report damage"
    assert "integrity" not in status, "a correctly rebuilt project must not report damage"
    assert "integrity" not in got, "a correctly rebuilt project must not report damage"


def _add_a_surplus_surfaceable_item(database: Path, project_id: str = "demo") -> int:
    """Add one surfaceable `knowledge_items` row, so `live > expected`.

    The surfaceable mirror of `_add_a_foreign_migration_history_row`: the direction
    no other fixture reaches. Every other surfaceable-count damage here *removes* a
    row (`live < expected`), so `==` -> `>=` on the surfaceable comparison survives
    the whole suite -- a surplus is called healthy. Copies an existing approved
    row's enum-valued columns so every value is one the schema accepts, with a
    fresh id and a NULL `current_revision_id` (a composite child key with a NULL
    component imposes no constraint), so the row is counted but names no revision
    and cannot itself be surfaced as a result.
    """
    connection = sqlite3.connect(database)
    try:
        changed = connection.execute(
            "INSERT INTO knowledge_items "
            "(item_id, project_id, namespace, kind, status, current_revision_id, owner, "
            " trust_level, sensitivity, tenant_id, acl_group, valid_from, valid_to) "
            "SELECT 'architecture.surplus', project_id, namespace, kind, status, NULL, owner, "
            " trust_level, sensitivity, tenant_id, acl_group, valid_from, valid_to "
            "FROM knowledge_items WHERE project_id = ? AND status = 'approved' LIMIT 1",
            (project_id,),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return changed


@pytest.mark.asyncio
async def test_a_surplus_surfaceable_item_is_damage_on_every_read_tool(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The surfaceable comparison is `!=`, so *more* items is damage too.

    The state database is immutable once built, so a live surfaceable count above
    what `migrate apply` recorded is not a project that grew -- it is a row that was
    never applied here, and an answer assembled from it comes from a state nobody
    recorded. `==` -> `>=` (or `<`) on the surfaceable term calls that healthy, and
    no other fixture could tell: every other surfaceable damage in this suite
    *removes* reach, so `live > expected` had never been built.

    All three surfaces in one test, because the claim is one -- the direction of a
    single comparison -- reaching a caller three ways: a field on `search` and
    `status`, and `get`'s success path, which the surplus leaves intact (the
    fetched item is still readable) so it publishes the field rather than a message.
    """
    database, _ = _state_database(registry)
    assert _live_and_expected(registry) == (2, 2), (
        "the migration operands must be equal, or PR1's comparison is what fires below"
    )
    assert _expected_surfaceable_count(database) == 2, "the fixture must start with two recorded"
    assert _add_a_surplus_surfaceable_item(database) == 1, (
        "the surplus row must land, or this is vacuous"
    )
    assert _live_surfaceable_count(database) == 3, (
        "the surplus must move the live count above the record"
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert search["integrity"]["damageDetected"] is True, (
        "knowledge.search called a surplus surfaceable row healthy; the surfaceable comparison "
        "has become a shortfall test and a row never applied here is answering queries"
    )
    assert status["integrity"]["damageDetected"] is True, (
        "knowledge.status called a surplus healthy"
    )
    assert got["integrity"]["damageDetected"] is True, (
        "knowledge.get's success path called a surplus healthy"
    )
    for surface, published in (("search", search), ("status", status), ("get", got)):
        assert INTEGRITY_REMEDY_ACTION in published["integrity"]["remedy"], (
            f"knowledge.{surface} reported damage with no rebuild to run"
        )


def _move_the_draft_to_approved(database: Path, project_id: str = "demo") -> int:
    """Move the draft item to `approved`, a status still in `SURFACEABLE_STATUSES`.

    A within-set status move: both `draft` and `approved` are surfaceable, so the
    count `count_surfaceable_items` takes is unchanged (2 -> 2). It is the residual
    the store's DDL comment and `_integrity_signal`'s docstring record as a class --
    a type-valid, in-scope corruption the count cannot see -- and the direction they
    separate from a status that *leaves* the set, which the count does see (the
    sentinel a corruption sweep writes, disclosed as `integrity`).
    """
    connection = sqlite3.connect(database)
    try:
        changed = connection.execute(
            "UPDATE knowledge_items SET status = 'approved' "
            "WHERE project_id = ? AND status = 'draft'",
            (project_id,),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return changed


@pytest.mark.asyncio
async def test_a_within_surfaceable_status_move_moves_no_count_and_is_silent(
    registry: ProjectRegistry,
) -> None:
    """#30 PR2. The silent direction of a `status` corruption, stated as a property.

    A `knowledge_items.status` cell moves the surfaceable count only when the new
    value *leaves* `SURFACEABLE_STATUSES`. A value that stays inside it (draft ->
    approved) is counted either way, so the count -- which is not a checksum --
    cannot see the move, and the `integrity` key stays absent while the response
    reflects the new status. This is the residual `_integrity_signal`'s docstring
    and the schema DDL comment record; without it that "silent" direction is a
    claim no test holds, while the corruption sweep only ever covers the *leaving*
    direction (its sentinel is not a surfaceable status).

    The guard is `itemsByStatus`: it proves the move happened -- both items now
    `approved` -- so the silence below is over a state that really changed, not a
    corpus that never moved.
    """
    database, _ = _state_database(registry)
    assert _live_and_expected(registry) == (2, 2), "the migration operands must stay equal"
    assert _expected_surfaceable_count(database) == 2, "the fixture records two surfaceable items"
    assert _move_the_draft_to_approved(database) == 1, "the draft must move, or this is vacuous"
    assert _live_surfaceable_count(database) == 2, (
        "a within-set move must not change the surfaceable count, or this is not the residual case"
    )

    search = await _call(registry, "knowledge.search", projectId="demo", query="token")
    status = await _call(registry, "knowledge.status", projectId="demo")
    got = await _call(
        registry, "knowledge.get", projectId="demo", itemId="architecture.auth-policy"
    )

    assert status["itemsByStatus"] == {"approved": 2}, (
        f"the move did not take effect, so the silence below is over an unchanged corpus: "
        f"{status['itemsByStatus']}"
    )
    assert "integrity" not in search, "a within-set status move moved no count and must be silent"
    assert "integrity" not in status, "a within-set status move moved no count and must be silent"
    assert "integrity" not in got, "a within-set status move moved no count and must be silent"
