"""GHSA-3f65: served text that drifts under an unchanged revision id must not be served.

The serve gate re-checks the canonical store for what is approved *now* before it
shows a retrieved row (:class:`~theurian.application.visibility.CanonicalVisibility`).
Until this fix it compared only the revision id, trusting that one id names one
immutable served text. But the state database is derived, unsigned and git-ignored
(ADR-0004, SEC-7), and the index serves a revision's *title prepended to its body*
-- so the served text has two faces that can drift under an unchanged revision id:

* **the body**: edit an approved body, re-pin the applied migration's
  ``contentSha256``, delete ``.theurian/state/active.json`` so ``_verify_history``
  early-returns, and re-apply -- canonical adopts the new body under the same id.
* **the title** (the cheaper face, round-1 CRITICAL of the fix): a title is
  migration metadata that no ``contentSha256`` pins, so editing it drifts the
  served text while the revision id *and the body hash* both hold. The first fix
  keyed the gate on the body-only hash and so passed this face; the class-complete
  fix keys it on ``served_content_hash(title, body)`` -- the exact text an excerpt
  is cut from.

Either way the revision-id check passed on both sides, so the stale index's
excerpt reached the caller: a new face of the derived-state-trust class
(GHSA-266v), closed on the index side by a per-chunk served-content hash the gate
checks.

**Constructed directly rather than through the CLI attack.** The drift's end state
is canonical holding one served text for a revision while the published index
holds another, and a direct ``UPDATE`` reaches exactly that state without a rebuild
that would erase it -- which is what makes the content-identity check the thing
under test rather than the schema bump. The reference reproductions that drove the
whole attack through the real ``migrate apply`` and MCP ``knowledge.search`` are
``scratchpad/adv-390/repro_high1.sh`` (body) and ``scratchpad/title_drift_repro.py``
(title).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import ProjectPaths, ProjectRegistry, read_active_state
from theurian.application.retrieval_service import RetrievalService, SearchOutcome, SearchRequest
from theurian.application.visibility import CanonicalVisibility
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.enums import Sensitivity
from theurian.domain.identifiers import ProjectId
from theurian.infrastructure.sqlite.index_schema import INDEX_SCHEMA_VERSION
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

pytestmark = pytest.mark.integration

runner = CliRunner()

EVERY_SENSITIVITY = frozenset(Sensitivity)
CONTEXT = RequestContext(project_id=ProjectId("demo"))

#: The line that identifies text as having come out of the *stale index* and
#: nowhere canonical still holds it. A word nothing else in this file writes, so a
#: fragment of it in a served passage came from body A after canonical dropped it.
SENTINEL = "zephyrsecret"

AUTH_REVISION = "01K1AAAREV01234567890ABCDE"

#: Body A: the approved text at build time. Its distinctive token is what a
#: post-build edit removes; the published index keeps it.
BODY_A = f"""# Authentication policy

Every call carries a signed token {SENTINEL}.
"""

#: Body B: the same revision after the author edits it and re-pins the migration.
#: Same query terms (so the *stale* index still ranks the old chunk), the secret
#: gone from what canonical now holds.
BODY_B = """# Authentication policy

Every call carries a signed token from the vault.
"""

#: A second document, never drifted, so the probe can prove the fix withholds the
#: drifted row without withholding a legitimate current hit beside it.
CACHE_BODY = """# Caching policy

Read-through cache with a two-minute TTL. Stale entries are evicted lazily.
"""

#: The title face's sentinel. It lives *only* in a title -- migration metadata no
#: ``contentSha256`` pins -- so a title-only drift removes it while the revision id
#: and the body hash both hold. A token nothing else in this file writes.
TITLE_SENTINEL = "zephyrtitle"

DEPLOY_REVISION = "01K1CCCREV01234567890ABCDE"

#: The title at build time, secret in it; ``DEPLOY_TITLE_B`` is the retracted title
#: the drift installs. The index prepends the title to the body, so the sentinel
#: rides into the excerpt -- which is exactly what a body-only hash could not see.
DEPLOY_TITLE_A = f"Deployment policy {TITLE_SENTINEL} runbook"
DEPLOY_TITLE_B = "Deployment policy runbook"

#: The body carries the query term (``rollout``) so the document ranks, and never
#: the sentinel -- so a sentinel in a served passage came from the stale title, not
#: from the body the query matched.
DEPLOY_BODY = """# Deployment policy

The blue-green rollout drains connections before cutover.
"""


def _migration(  # noqa: PLR0913 - a createItem+upsertRevision fixture names this many fields
    *, letter: str, item: str, revision: str, filename: str, title: str, body: str
) -> str:
    return f"""apiVersion: theurian.dev/v1
id: 01K1{letter}AAAAA01234567890ABCDE
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: {revision}
    contentFile: ../knowledge/architecture/{filename}
    contentSha256: {body_pin(body)}
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
          sourceUri: git://demo/{filename}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A registered project holding body A (with the sentinel) and a control doc."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)

    assert runner.invoke(app, ["init", "--json"]).exit_code == 0
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth.md").write_text(BODY_A)
    (knowledge / "cache.md").write_text(CACHE_BODY)
    (knowledge / "deploy.md").write_text(DEPLOY_BODY)
    (root / ".theurian/migrations/01K1AAAAAA01234567890ABCDE-auth.yaml").write_text(
        _migration(
            letter="A",
            item="architecture.auth",
            revision=AUTH_REVISION,
            filename="auth.md",
            title="Authentication policy",
            body=BODY_A,
        )
    )
    (root / ".theurian/migrations/01K1BAAAAA01234567890ABCDE-cache.yaml").write_text(
        _migration(
            letter="B",
            item="architecture.cache",
            revision="01K1BBBREV01234567890ABCDE",
            filename="cache.md",
            title="Caching policy",
            body=CACHE_BODY,
        )
    )
    (root / ".theurian/migrations/01K1CAAAAA01234567890ABCDE-deploy.yaml").write_text(
        _migration(
            letter="C",
            item="architecture.deploy",
            revision=DEPLOY_REVISION,
            filename="deploy.md",
            title=DEPLOY_TITLE_A,
            body=DEPLOY_BODY,
        )
    )
    assert runner.invoke(app, ["project", "register", "--json"]).exit_code == 0
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0
    yield root


def _database(project: Path) -> Path:
    """The canonical state the pointer names, resolved the way production does."""
    paths = ProjectPaths.of(project)
    active = read_active_state(paths)
    assert active is not None, "the fixture must have published a canonical state"
    return paths.state / active.database_filename


def _build_index(project: Path) -> Path:
    index_path = project / ".theurian/state/theurian-index-01K1DXAAAA.sqlite"
    IndexBuilder(store_factory=SqliteCanonicalStore, index_factory=SqliteIndexStore).build(
        IndexRequest(
            database=_database(project),
            index_path=index_path,
            project_id="demo",
            state_hash="test-state",
            index_build_id="01K1DXAAAA01234567890ABCDE",
            visible_sensitivities=EVERY_SENSITIVITY,
        )
    )
    return index_path


def _drift_the_body(project: Path) -> None:
    """Make canonical hold body B for ``AUTH_REVISION`` while the index keeps body A.

    The end state of the CLI attack, reached directly: the same revision id now
    hashes to body B (``contentSha256`` re-pinned), and no rebuild has touched the
    published index, so the index's chunk still carries body A's hash.
    """
    with closing(sqlite3.connect(_database(project))) as connection, connection:
        connection.execute(
            "UPDATE knowledge_revisions SET body = ?, content_sha256 = ? WHERE revision_id = ?",
            (BODY_B, body_pin(BODY_B), AUTH_REVISION),
        )


def _drift_the_title(project: Path) -> None:
    """Make canonical hold ``DEPLOY_TITLE_B`` while the index keeps ``DEPLOY_TITLE_A``.

    The *title* face of the same class. Only ``title`` moves: the revision id and
    ``content_sha256`` (the body hash) are untouched, because no ``contentSha256``
    pins a title. So the first fix -- which keyed the gate on the body-only hash --
    saw nothing move and served the old title; the class-complete fix keys on
    ``served_content_hash(title, body)``, which does move, so the row is withheld.

    Reaching the end state directly, exactly as ``_drift_the_body`` does: in the CLI
    attack an author edits the title metadata and re-applies after removing
    ``active.json``; the ``UPDATE`` is that end state without a rebuild that would
    re-stamp the index. Only the title is a valid `KnowledgeRevision` after the
    change (``DEPLOY_TITLE_B`` is non-empty and the body still hashes to
    ``content_sha256``), so canonical reads it back cleanly and the drift is a
    served-text drift, not a corruption.
    """
    with closing(sqlite3.connect(_database(project))) as connection, connection:
        connection.execute(
            "UPDATE knowledge_revisions SET title = ? WHERE revision_id = ?",
            (DEPLOY_TITLE_B, DEPLOY_REVISION),
        )


def _search(project: Path, index_path: Path, query: str) -> SearchOutcome:
    """One search over the real (possibly stale) index and the real canonical store.

    A fresh store session per call, so a drift committed between two searches is
    read on the second -- the gate's one-session-per-request rule (SEC-13) is what
    makes that the honest thing to model.
    """
    service = RetrievalService(SqliteIndexStore(index_path))
    with SqliteCanonicalStore(_database(project)) as store:
        visibility = CanonicalVisibility(
            store, CONTEXT, include_unapproved=False, visible_sensitivities=EVERY_SENSITIVITY
        )
        return service.search(
            SearchRequest(query=query, project_id="demo", visible_sensitivities=EVERY_SENSITIVITY),
            visibility,
        )


def test_a_body_that_drifted_under_an_unchanged_revision_is_not_served(project: Path) -> None:
    """The CRITICAL, as one query against the same index before and after the drift.

    The index never changes between the two searches. What changes is that
    canonical stops holding the sentinel under this revision id -- and the fix is
    what makes the second search stop serving it, because the index's build-time
    content hash no longer matches canonical's current-revision hash (GHSA-3f65).

    Without the content-identity check the two searches are identical: the
    revision id still matches, so the stale chunk clears the gate and its passage
    -- the old body, sentinel and all -- reaches the caller. That is the RED state
    the guard closes; neutering the check in `_may_surface` reproduces it.
    """
    index_path = _build_index(project)

    honest = _search(project, index_path, "signed token")
    assert any(candidate.item_id == "architecture.auth" for candidate in honest.candidates), (
        "precondition: the approved document must surface before the drift, or "
        "withholding it after proves nothing"
    )
    assert any(SENTINEL in passage for passage in honest.passages.values()), (
        "precondition: the index must hold the sentinel text, so the leak has something to leak"
    )

    _drift_the_body(project)

    probe = _search(project, index_path, "signed token")
    assert all(candidate.item_id != "architecture.auth" for candidate in probe.candidates), (
        "the drifted revision must be withheld: its build-time content hash no "
        "longer matches canonical's current-revision hash"
    )
    assert not any(SENTINEL in passage for passage in probe.passages.values()), (
        "and so the retracted body must not reach the caller through any excerpt"
    )


def test_a_document_beside_the_drifted_one_still_surfaces(project: Path) -> None:
    """The fix withholds on content drift, not on everything -- no over-withholding.

    The control document never drifted, so its build-time hash still matches
    canonical's, and a query for it answers after the drift exactly as before. A
    guard that dropped every row would pass the CRITICAL test above and fail this
    one, which is why the honest path is asserted separately.
    """
    index_path = _build_index(project)
    _drift_the_body(project)

    surviving = _search(project, index_path, "cache")
    assert any(candidate.item_id == "architecture.cache" for candidate in surviving.candidates), (
        "a document whose body did not drift must still surface after another one's did"
    )


def test_a_title_that_drifted_under_an_unchanged_revision_is_not_served(project: Path) -> None:
    """The title face, the round-1 CRITICAL of the fix, as one query across the drift.

    The secret is in the *title* this time, and only the title drifts -- the body
    and its ``content_sha256`` are untouched. This is what the first fix could not
    catch: it keyed the gate on the body-only hash, which does not move when a title
    changes, so the stale index's title reached the caller inside the excerpt. The
    class-complete fix keys on ``served_content_hash(title, body)``, which does
    move, so the row is withheld.

    Pins the served-hash *source* as much as the gate: the canonical side recomputes
    the served hash from the current revision's ``title`` and ``body``
    (``store._ITEM_WITH_CURRENT_CONTENT_SQL``). Revert that recomputation to the
    stored body-only ``content_sha256`` and this goes RED, because a body-only hash
    is invariant under a title drift -- which is precisely the bug.
    """
    index_path = _build_index(project)

    honest = _search(project, index_path, "rollout")
    assert any(candidate.item_id == "architecture.deploy" for candidate in honest.candidates), (
        "precondition: the document must surface before the drift, or withholding it "
        "after proves nothing"
    )
    assert any(TITLE_SENTINEL in passage for passage in honest.passages.values()), (
        "precondition: the index must serve the sentinel title inside the excerpt, so "
        "the leak has something to leak"
    )

    _drift_the_title(project)

    probe = _search(project, index_path, "rollout")
    assert all(candidate.item_id != "architecture.deploy" for candidate in probe.candidates), (
        "the drifted revision must be withheld: its build-time served hash "
        "(title-plus-body) no longer matches canonical's current-revision served hash"
    )
    assert not any(TITLE_SENTINEL in passage for passage in probe.passages.values()), (
        "and so the retracted title must not reach the caller through any excerpt"
    )


def test_a_document_whose_title_did_not_drift_still_surfaces(project: Path) -> None:
    """The honest title path: an untouched title-plus-body still serves after a drift.

    The auth document's title never drifts here, so its served hash still matches
    canonical's and it answers after the deploy title drifted exactly as before. A
    guard that withheld on the served-hash axis for every row would pass the CRITICAL
    above and fail this, which is why the honest path is asserted separately.
    """
    index_path = _build_index(project)
    _drift_the_title(project)

    surviving = _search(project, index_path, "signed token")
    assert any(candidate.item_id == "architecture.auth" for candidate in surviving.candidates), (
        "a document whose served text did not drift must still surface after another's did"
    )


# -- Durable guards: the surfaces that are safe today *by construction* ----------
#
# The tests above prove the gate withholds a drifted row. The three below pin the
# reasons a drift cannot reach a caller by another route, so a future change that
# rewires one of those routes fails here rather than silently reopening the class:
#
# (a) `knowledge.get` reads canonical, never the index -- so it hands back the
#     redacted current body, not the stale indexed one.
# (b) a build from before the fix (schema v6, no served-content column) is refused
#     *wholesale* by the schema gate, never queried row-by-row without the check.
# (c) a search racing a rebuild resolves the atomic pointer to exactly one build,
#     and both possible builds refuse the drifted sentinel -- the old by the schema
#     gate, the new by the content gate -- so there is no interleaving that serves
#     v6 rows unchecked (ADR-0022).
#
# These go through the real MCP tools (`build_server(...).call_tool`) rather than
# `RetrievalService`, because the schema gate and the pointer resolution live on
# that path, and `knowledge.get` exists only there.


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    """The registry the MCP server resolves ``demo`` through, over the fixture's data dir."""
    return ProjectRegistry.default(tmp_path / "datadir")


@pytest.fixture
def published_index(project: Path) -> Path:
    """Build and *publish* an index through the CLI, so the active pointer names it.

    A **synchronous** fixture on purpose. ``theurian index build`` embeds chunks
    through ``asyncio.run``, which raises inside an already-running loop, so the
    build cannot happen in the body of the async tests below (the same constraint
    ``test_index_fallback``'s ``broken`` fixture documents). Unlike ``_build_index``,
    which writes a fixed path the direct-call tests hand to ``SqliteIndexStore``:
    the MCP search path resolves ``active-index.json``, which only
    ``theurian index build`` writes. The fixture has already ``chdir``-ed into the
    project, so this runs there.
    """
    assert runner.invoke(app, ["index", "build", "--json"]).exit_code == 0
    return _published_index_file(project)


def _published_index_file(project: Path) -> Path:
    built = list((project / ".theurian/state").glob("theurian-index-*.sqlite"))
    assert len(built) == 1, f"expected exactly one published index, found {built}"
    return built[0]


def _make_the_published_build_look_pre_fix(project: Path) -> None:
    """Stamp the published index with the previous schema version (v6, GHSA-3f65's before).

    A genuine pre-fix build has no ``served_content_sha256`` column at all; what
    matters for the schema gate is the recorded version, which ``is_searchable``
    compares against ``INDEX_SCHEMA_VERSION``. Setting it to ``INDEX_SCHEMA_VERSION
    - 1`` reaches the same refusal a real v6 file would, without keeping a whole v6
    schema alive in the tests.
    """
    with closing(sqlite3.connect(_published_index_file(project))) as connection, connection:
        connection.execute(
            "UPDATE index_metadata SET index_schema_version = ?", (INDEX_SCHEMA_VERSION - 1,)
        )


async def _mcp(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    """Call one MCP tool the way the transport does, and return its JSON payload."""
    result = await build_server(registry).call_tool(tool, {"projectId": "demo", **arguments})
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


@pytest.mark.asyncio
async def test_knowledge_get_serves_the_current_body_never_the_stale_index(
    project: Path, published_index: Path, registry: ProjectRegistry
) -> None:
    """(a) `knowledge.get` reads canonical, so a drift redacts it -- the index is moot.

    The stale index still holds body A (sentinel and all), but ``knowledge.get``
    fetches the item's *current revision* from the canonical store, which now holds
    body B. If someone ever wired ``knowledge.get`` to read the index -- the T-16
    "another tool reaches the same content" family -- this goes RED: the sentinel
    would ride back in the ``body`` field.
    """
    _drift_the_body(project)

    reply = await _mcp(registry, "knowledge.get", itemId="architecture.auth")

    blob = json.dumps(reply)
    assert SENTINEL not in blob, (
        "knowledge.get handed back the retracted body -- it read the stale index "
        "rather than canonical's current revision"
    )
    assert "from the vault" in reply["body"], (
        "knowledge.get must return canonical's current (redacted) body, which is body B"
    )


@pytest.mark.asyncio
async def test_a_pre_fix_index_is_refused_wholesale_not_served_row_by_row(
    project: Path, published_index: Path, registry: ProjectRegistry
) -> None:
    """(b) A v6 build cannot prove content identity, so the schema gate refuses it whole.

    A build from before the fix has no served-content hash, so its rows cannot be
    content-checked at all. The schema bump is the forcing function: such a build
    reports ``schema-mismatch`` and the search degrades to the canonical scan --
    which, over the drifted state, holds body B. Remove the schema gate and a v6
    build would be queried row by row with no check, serving the sentinel; this goes
    RED at ``indexed`` first.
    """
    _drift_the_body(project)
    _make_the_published_build_look_pre_fix(project)

    reply = await _mcp(registry, "knowledge.search", query="signed token")

    assert reply["retrieval"]["indexed"] is False, (
        "a pre-fix index must not be reported as a usable one -- the schema gate is gone"
    )
    assert reply["retrieval"]["fallbackReason"] == "index-schema-mismatch", (
        "the refusal must name the schema mismatch, so a client knows a rebuild is the remedy"
    )
    assert SENTINEL not in json.dumps(reply), (
        "the canonical scan the refusal degrades to must hold body B, not the stale index's body A"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caught_the_old_build", "make_pre_fix"),
    [(True, True), (False, False)],
    ids=["pointer-caught-v6", "pointer-caught-v7"],
)
async def test_a_search_racing_a_rebuild_never_serves_the_drifted_sentinel(
    project: Path,
    published_index: Path,
    registry: ProjectRegistry,
    caught_the_old_build: bool,
    make_pre_fix: bool,
) -> None:
    """(c) Whichever build the atomic pointer resolves to, the drifted sentinel is withheld.

    A rebuild swaps ``active-index.json`` atomically (ADR-0022), so a concurrent
    search reads exactly one build -- either the old one still pointed at, or the new
    one. Modelled deterministically, as the GC race is: the two outcomes are forced
    rather than timed. The old build is refused by the *schema* gate (it predates the
    served-content column); the new build is content-checked by the *serve* gate (its
    served hash no longer matches drifted canonical). There is no third outcome that
    serves the old build's rows unchecked, so the sentinel never reaches the caller.
    Remove either gate and one arm goes RED.
    """
    _drift_the_body(project)
    if make_pre_fix:
        _make_the_published_build_look_pre_fix(project)

    reply = await _mcp(registry, "knowledge.search", query="signed token")

    assert SENTINEL not in json.dumps(reply), (
        "the drifted sentinel reached the caller: the build the pointer resolved to was "
        "served without the check that build's gate is supposed to apply"
    )
    assert reply["retrieval"]["indexed"] is not caught_the_old_build, (
        "the old build must be refused (indexed False) and the new build used (indexed True) -- "
        "if this flips, the arm under test is not exercising the gate it names"
    )
