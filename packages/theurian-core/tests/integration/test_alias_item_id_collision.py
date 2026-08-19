"""An alias key colliding with a live item id, end to end (SEC-13, T-21).

The shared resource is neither a body file (T-20) nor a revision id (T-18): it is
an *item id*, used by one operation as a live -- and non-surfaceable -- item and
by another as an ``addAlias`` key pointing at an approved item.
``SqliteCanonicalStore.get_item`` resolves the alias *before* the status lookup
(``store.py`` ``_resolve_alias``), and ``_relation_is_visible`` gates each end of
an edge through that resolving ``get_item``. So a ``rejected`` item ``W`` that is
also an ``addAlias`` key for an approved ``P`` clears the gate as ``P``: the edge
that ``W`` authored -- its rejection ``note``, where the secret that caused the
rejection lives -- is published on ``P``'s response. The withheld id never
appears; only the note leaks, so a per-field assertion keyed on the id asked for
misses it exactly as it did for T-18. Measured on the branch point before the
fix, ``migrate apply`` accepted the collision with exit 0 and ``knowledge.get``
for the approved id served the withheld note.

The fix has two independent parts, and each is pinned by its own behaviour so the
assertions stay correct whichever layer implements them:

* **Read-side (Part A).** No serve path runs the migration guards, so a database
  built by a release that predates the write guard already holds the collision
  and must stop leaking at *read* time. Pinned by constructing the poisoned state
  and inserting the alias row directly, so the assertion never depends on the
  write guard existing.
* **Write-side (Part B).** ``migrate validate`` and ``migrate apply`` refuse a
  set that introduces an alias key colliding with an item id -- both commands
  (issue #36 parity), both directions, and across an earlier applied migration.

Both halves are asserted the T-18 way: the refusal *and* the withheld content
reaching nothing, over the whole response string rather than a chosen field. A
test that asserted only the refusal would pass on a build that refuses for the
wrong reason and leaks anyway; a test that asserted only the absence would pass
on a build whose retrievers reach nothing at all, which is why every poisoned
fixture below first proves the withheld note is present, matchable, and withheld
by status alone.

Integration-level by necessity, not preference: ``tests/fakes/store.py``'s
``get_item`` does not resolve aliases, so a unit test against the fake cannot see
this defect. Every test here drives the real ``SqliteCanonicalStore`` through
``migrate apply`` and reads through ``build_server(registry).call_tool``.
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
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, ProjectRegistry, read_active_state
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

#: Exit code Core uses for a knowledge-state problem the user must resolve.
EXIT_STATE_ERROR = 4

WITHHELD_ITEM = "architecture.withheld-credentials"
PUBLISHED_ITEM = "architecture.public-note"
OLD_ITEM = "architecture.old-name"
NEW_ITEM = "architecture.new-name"
#: An alias key that names no item, spent later (test 5) as a fresh item id.
GHOST_KEY = "architecture.ghost-name"
#: An alias key that never names an item -- the ordinary rename (test 6).
FORMER_KEY = "architecture.former-name"

#: A digest to no reader; a fragment of it in a response came out of the note.
SENTINEL = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"
#: A phrase only ``new``'s body carries, so reaching it proves the rename resolved.
RENAME_MARKER = "the renamed document body reached the caller"

WITHHELD_BODY = f"# Withheld credentials\n\nThe production key is {SENTINEL}.\n"
PUBLISHED_BODY = "# Public note\n\nNothing secret here.\n"
OLD_BODY = "# Old name\n\nThis document has been renamed.\n"
NEW_BODY = f"# New name\n\n{RENAME_MARKER}\n"

# Two migration ids and four revision ids are enough: every test runs in its own
# ``tmp_path``/``THEURIAN_DATA_DIR``, so ids only need to be distinct *within* a
# single applied set. Valid 26-character Crockford base32 (no I, L, O, U).
MIG_A = "01K1AAAAAA01234567890ABCDE"
MIG_B = "01K1BBBBBB01234567890ABCDE"
REV_A = "01K2AAAAAA01234567890ABCDE"
REV_B = "01K2BBBBBB01234567890ABCDE"
REV_C = "01K2CCCCCC01234567890ABCDE"
REV_D = "01K2DDDDDD01234567890ABCDE"


# -- YAML builders ---------------------------------------------------------
#
# Assembled from blocks rather than pasted per test: the operation set differs
# between the read leak, the rename, and each write-guard direction, and a shared
# builder keeps the metadata identical so a diff between two tests is only the
# operations that differ.


def _create_item(item_id: str) -> str:
    return (
        f"  - op: createItem\n"
        f"    itemId: {item_id}\n"
        f"    kind: architecture\n"
        f"    namespace: backend\n"
        f"    owner: platform-team\n"
    )


def _upsert(  # noqa: PLR0913 -- one metadata block, keyword-only so a call cannot mis-order it
    *,
    item_id: str,
    revision_id: str,
    content_file: str,
    status: str,
    sensitivity: str,
    trust_level: str,
    title: str,
) -> str:
    return (
        f"  - op: upsertRevision\n"
        f"    itemId: {item_id}\n"
        f"    revisionId: {revision_id}\n"
        f"    contentFile: ../knowledge/{content_file}\n"
        f"    metadata:\n"
        f"      title: {title}\n"
        f"      contentType: text/markdown\n"
        f"      kind: architecture\n"
        f"      namespace: backend\n"
        f"      status: {status}\n"
        f"      owner: platform-team\n"
        f"      trustLevel: {trust_level}\n"
        f"      sensitivity: {sensitivity}\n"
        f"      sourceAnchors:\n"
        f"        - provider: git\n"
        f"          sourceUri: git://demo/{content_file}\n"
    )


def _add_relation(*, source: str, target: str, note: str) -> str:
    return (
        f"  - op: addRelation\n"
        f"    sourceItemId: {source}\n"
        f"    relationType: contradicts\n"
        f"    targetItemId: {target}\n"
        f'    note: "{note}"\n'
    )


def _add_alias(*, alias: str, item_id: str) -> str:
    return f"  - op: addAlias\n    alias: {alias}\n    itemId: {item_id}\n"


def _deprecate(item_id: str) -> str:
    return f"  - op: deprecateItem\n    itemId: {item_id}\n"


def _migration(migration_id: str, *ops: str, created_at: str = "2026-08-02T10:00:00+09:00") -> str:
    return (
        f"apiVersion: theurian.dev/v1\n"
        f"id: {migration_id}\n"
        f"createdAt: {created_at}\n"
        f"author: engineer@example.com\n"
        f"operations:\n" + "".join(ops)
    )


def _withheld_upsert() -> str:
    return _upsert(
        item_id=WITHHELD_ITEM,
        revision_id=REV_A,
        content_file="architecture/withheld-credentials.md",
        status="rejected",
        sensitivity="restricted",
        trust_level="inferred",
        title="Withheld credentials",
    )


def _published_upsert(revision_id: str = REV_B) -> str:
    return _upsert(
        item_id=PUBLISHED_ITEM,
        revision_id=revision_id,
        content_file="architecture/public-note.md",
        status="approved",
        sensitivity="public",
        trust_level="reviewed",
        title="Public note",
    )


# -- Fixtures --------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A registered project with no migration applied yet.

    Deliberately bare, unlike the T-18 fixture: these tests apply very different
    sets, and several apply two sets in sequence to reach the cross-set path, so
    each writes and applies its own migrations.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)

    _run("init")
    _run("project", "register")
    yield root


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry.default(tmp_path / "datadir")


# -- CLI helpers -----------------------------------------------------------


def _run(*args: str) -> None:
    """A command that must succeed. Its exit 0 is the assertion (test 2, 6)."""
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _run_failing(*args: str) -> dict[str, str]:
    """A command that must refuse; returns the ``{error, remedy}`` a user reads."""
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == EXIT_STATE_ERROR, (
        f"`theurian {' '.join(args)}` exited {result.exit_code}: {result.stdout}"
    )
    report: dict[str, str] = json.loads(result.stderr)
    return report


def _json(*args: str) -> dict[str, Any]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def _write_migration(root: Path, migration_id: str, body: str) -> None:
    (root / f".theurian/migrations/{migration_id}-m.yaml").write_text(body)


def _write_body(root: Path, relpath: str, text: str) -> None:
    path = root / ".theurian/knowledge" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# -- MCP helpers -----------------------------------------------------------


async def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    """Everything one tool call puts in front of an agent, as one string.

    A refusal is an outcome here, not an error: ``knowledge.get`` for a withheld
    or absent item raises, and the only question this file asks of the string is
    whether the withheld note is in it.
    """
    try:
        result = await build_server(registry).call_tool(tool, arguments)
    except Exception as exc:
        return str(exc)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return json.dumps([block.text for block in result.content], ensure_ascii=False)  # type: ignore[union-attr]


async def _payload(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    """The same call as :func:`_call`, for the tests that read specific fields."""
    result = await build_server(registry).call_tool(tool, arguments)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return dict(structured)
    loaded: dict[str, Any] = json.loads(result.content[0].text)  # type: ignore[union-attr]
    return loaded


async def _everything(registry: ProjectRegistry) -> str:
    """Every response the note could travel through, concatenated.

    One string on purpose: the collision hides the withheld id behind the
    approved one, so a per-response assertion keyed on the id asked for is the
    reasoning that missed the leak. Both ``get`` ids and both search paths.
    """
    return "\n".join(
        [
            await _call(registry, "knowledge.get", projectId="demo", itemId=PUBLISHED_ITEM),
            await _call(registry, "knowledge.get", projectId="demo", itemId=WITHHELD_ITEM),
            await _call(registry, "knowledge.search", projectId="demo", query="public note"),
            await _call(registry, "knowledge.search", projectId="demo", query="credentials"),
        ]
    )


# -- SQLite helpers --------------------------------------------------------


def _active_database(root: Path) -> Path:
    active = read_active_state(ProjectPaths.of(root))
    assert active is not None, "no active state pointer"
    return root / ".theurian/state" / active.database_filename


def _state_databases(root: Path) -> tuple[Path, ...]:
    return tuple(sorted((root / ".theurian/state").glob("*.sqlite")))


def _stored_project_id(database: Path) -> str:
    with closing(sqlite3.connect(database)) as raw:
        row = raw.execute("SELECT project_id FROM knowledge_items LIMIT 1").fetchone()
    assert row is not None, "the fixture wrote no items to read a project id from"
    return str(row[0])


def _insert_alias(database: Path, *, alias: str, item_id: str) -> None:
    """Insert one alias row directly, the way a release before the write guard did.

    Sidesteps the (to-be-added) write guard on purpose: Part A must hold for a
    database this process did not build, and going through ``migrate apply`` would
    couple this read-side assertion to the write guard's existence.
    """
    project_id = _stored_project_id(database)
    with closing(sqlite3.connect(database)) as raw, raw:
        raw.execute(
            "INSERT INTO knowledge_aliases (alias, item_id, project_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (alias, item_id, project_id, "2026-08-02T10:00:00+09:00"),
        )


# -- Part A: read-side, serve-time repair ----------------------------------


@pytest.mark.asyncio
async def test_a_poisoned_db_does_not_leak_the_note_through_get(
    project: Path, registry: ProjectRegistry
) -> None:
    """The disclosure itself: an aliased ``rejected`` endpoint must not clear the gate.

    The withheld note lives on an edge ``W --contradicts--> P``. With the alias
    ``W -> P`` in ``knowledge_aliases``, the store resolves the edge's ``W``
    endpoint to the approved ``P`` and publishes the note on ``P``'s response.
    Built without the alias in the migration, then the alias inserted directly,
    so this reproduces a database a pre-guard release shipped and does not depend
    on the write guard. RED on HEAD: the note reaches the caller.
    """
    _write_body(project, "architecture/withheld-credentials.md", WITHHELD_BODY)
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(WITHHELD_ITEM),
            _withheld_upsert(),
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_relation(source=WITHHELD_ITEM, target=PUBLISHED_ITEM, note=f"REJECTED {SENTINEL}"),
        ),
    )
    _run("migrate", "apply")

    # Precondition, without which the absence below proves nothing: the note is
    # stored against the rejected item, and until the alias exists the gate
    # already withholds it -- so the fixture is not passing by writing nothing.
    database = _active_database(project)
    with closing(sqlite3.connect(database)) as raw:
        note = raw.execute(
            "SELECT note FROM knowledge_relations WHERE source_item_id = ?", (WITHHELD_ITEM,)
        ).fetchone()
        status = raw.execute(
            "SELECT status FROM knowledge_items WHERE item_id = ?", (WITHHELD_ITEM,)
        ).fetchone()
    assert note is not None and SENTINEL in note[0], "the withheld note is not in the store"
    assert status == ("rejected",), "the item that must not surface is not rejected"
    assert SENTINEL not in await _everything(registry), "the gate must withhold it before the alias"

    _insert_alias(database, alias=WITHHELD_ITEM, item_id=PUBLISHED_ITEM)

    reachable = await _everything(registry)

    assert SENTINEL not in reachable, "the rejected item's note reached a caller through the alias"


@pytest.mark.asyncio
async def test_a_legitimate_rename_alias_still_resolves(
    project: Path, registry: ProjectRegistry
) -> None:
    """The only expressible rename must keep working; the fix must not break it to close the leak.

    ``old`` is retired with ``deprecateItem`` and pointed at an approved ``new``.
    ``knowledge.get(old)`` must still resolve through the alias to ``new``'s
    content. GREEN on HEAD and required to stay green: a read-side fix that
    stopped ``get`` resolving aliases would close the leak and this at once, and a
    write guard that refused every alias-over-an-item id would refuse this apply.
    """
    _write_body(project, "architecture/old-name.md", OLD_BODY)
    _write_body(project, "architecture/new-name.md", NEW_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(OLD_ITEM),
            _upsert(
                item_id=OLD_ITEM,
                revision_id=REV_C,
                content_file="architecture/old-name.md",
                status="approved",
                sensitivity="internal",
                trust_level="reviewed",
                title="Old name",
            ),
            _create_item(NEW_ITEM),
            _upsert(
                item_id=NEW_ITEM,
                revision_id=REV_D,
                content_file="architecture/new-name.md",
                status="approved",
                sensitivity="public",
                trust_level="reviewed",
                title="New name",
            ),
            _deprecate(OLD_ITEM),
            _add_alias(alias=OLD_ITEM, item_id=NEW_ITEM),
        ),
    )

    _run("migrate", "apply")
    resolved = await _payload(registry, "knowledge.get", projectId="demo", itemId=OLD_ITEM)

    assert resolved["itemId"] == NEW_ITEM, "the alias no longer resolves the renamed item"
    assert resolved["title"] == "New name", "and it must answer with the successor's metadata"
    assert RENAME_MARKER in resolved["body"], "and the successor's body"


# -- Part B: write-side prevention -----------------------------------------


def test_apply_refuses_an_alias_over_a_live_item_id(project: Path) -> None:
    """``migrate apply`` must refuse the collision, and refuse it before any write.

    The full attack in one set: ``W`` rejected, ``P`` approved, the edge with its
    note, and ``addAlias W -> P``. RED on HEAD, where apply exits 0. The refusal
    names both ids so the author can find the ``addAlias`` to delete, quotes no
    part of the note, and -- because it is a pre-write refusal like the scope and
    body-file guards -- leaves no state database behind.
    """
    _write_body(project, "architecture/withheld-credentials.md", WITHHELD_BODY)
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(WITHHELD_ITEM),
            _withheld_upsert(),
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_relation(source=WITHHELD_ITEM, target=PUBLISHED_ITEM, note=f"REJECTED {SENTINEL}"),
            _add_alias(alias=WITHHELD_ITEM, item_id=PUBLISHED_ITEM),
        ),
    )

    report = _run_failing("migrate", "apply")

    assert WITHHELD_ITEM in report["error"], "the id used as both an item and an alias key"
    assert PUBLISHED_ITEM in report["error"], "and the item the alias points at"
    assert SENTINEL not in json.dumps(report), "a refusal is not a place to quote the note"
    assert _state_databases(project) == (), "a refused apply must leave no state database"


def test_validate_refuses_an_alias_over_a_live_item_id(project: Path) -> None:
    """``migrate validate`` must refuse the same set as apply (issue #36 parity).

    A statically decidable rule one command enforces and the other does not is the
    #36 class: a document that passes validate and fails apply, or vice versa.
    RED on HEAD, where validate reports ``valid: true``.
    """
    _write_body(project, "architecture/withheld-credentials.md", WITHHELD_BODY)
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(WITHHELD_ITEM),
            _withheld_upsert(),
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_alias(alias=WITHHELD_ITEM, item_id=PUBLISHED_ITEM),
        ),
    )

    report = _run_failing("migrate", "validate")

    assert WITHHELD_ITEM in report["error"]
    assert PUBLISHED_ITEM in report["error"]


def test_status_reports_the_alias_collision_under_refused_ids(project: Path) -> None:
    """``migrate status`` names the colliding migration under ``refusedIds`` (#63, #210).

    ``status`` observes, it does not gate, so it keeps exit 0 -- but the same
    statically decidable property ``validate``/``apply`` refuse must be visible
    here, exactly as the tenant/ACL rule (#63) and the one-body-one-revision rule
    (#210) already are. Otherwise ``status`` reports ``refusedIds: []`` for a set
    the gating commands exit 4 on, the #210 gap applied to a new rule.

    Untested until now, and the gap has teeth: the sibling of this file's
    ``test_apply_refuses_an_alias_over_a_live_item_id`` pins the *throwing*
    ``refuse_alias_item_id_collision``, but nothing pinned the non-throwing
    ``alias_item_collision_violations`` that feeds ``status``. Mutating that
    enumerator to ``return ()`` drops the alias rule from
    ``_refused_migration_ids`` and leaves the whole suite green -- the adversarial
    reviewer's finding. This test kills that mutation: the same live-item
    collision as the apply/validate tests, observed rather than gated.
    """
    _write_body(project, "architecture/withheld-credentials.md", WITHHELD_BODY)
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(WITHHELD_ITEM),
            _withheld_upsert(),
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_alias(alias=WITHHELD_ITEM, item_id=PUBLISHED_ITEM),
        ),
    )

    status = _json("migrate", "status")

    assert MIG_A in status["refusedIds"], (
        "the migration whose addAlias collides with a live item id"
    )
    assert MIG_A in status["pendingIds"], "and it is still pending, since status observes not gates"


def test_apply_refuses_an_alias_over_an_item_from_an_earlier_applied_migration(
    project: Path,
) -> None:
    """The collision must be caught across sets, not only within one.

    Set one creates ``W`` (rejected) and ``P`` (approved) and applies clean. Set
    two adds ``addAlias W -> P``. Applying the second must refuse: the alias key
    ``W`` collides with the item ``W`` an earlier migration already wrote. RED on
    HEAD, where the second apply exits 0.
    """
    _write_body(project, "architecture/withheld-credentials.md", WITHHELD_BODY)
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(WITHHELD_ITEM),
            _withheld_upsert(),
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
        ),
    )
    _run("migrate", "apply")

    _write_migration(
        project,
        MIG_B,
        _migration(
            MIG_B,
            _add_alias(alias=WITHHELD_ITEM, item_id=PUBLISHED_ITEM),
            created_at="2026-08-02T11:00:00+09:00",
        ),
    )

    report = _run_failing("migrate", "apply")

    assert WITHHELD_ITEM in report["error"], "the id an earlier migration already made an item"


def test_apply_refuses_creating_an_item_whose_id_is_an_existing_alias_key(
    project: Path,
) -> None:
    """The reverse direction: an item id must not collide with an existing alias key.

    Set one aliases a name that is *not* an item (``ghost -> P``) and applies
    clean -- that is the ordinary rename shape. Set two creates an item at that
    same ``ghost`` id, which would make one string both an item and an alias key.
    Applying the second must refuse. RED on HEAD.
    """
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_alias(alias=GHOST_KEY, item_id=PUBLISHED_ITEM),
        ),
    )
    _run("migrate", "apply")

    _write_migration(
        project,
        MIG_B,
        _migration(MIG_B, _create_item(GHOST_KEY), created_at="2026-08-02T11:00:00+09:00"),
    )

    report = _run_failing("migrate", "apply")

    assert GHOST_KEY in report["error"], "the id used as both a new item and an existing alias key"


def test_apply_refuses_an_alias_and_a_colliding_item_in_one_set(project: Path) -> None:
    """The reverse collision inside a single set, alias before the item.

    ``addAlias ghost -> P`` then ``createItem ghost`` in one migration is the
    within-set form of the case above, and the guard must not depend on which
    order the two operations arrive in. RED on HEAD.
    """
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_alias(alias=GHOST_KEY, item_id=PUBLISHED_ITEM),
            _create_item(GHOST_KEY),
        ),
    )

    report = _run_failing("migrate", "apply")

    assert GHOST_KEY in report["error"]
    assert _state_databases(project) == (), "a refused apply must leave no state database"


@pytest.mark.asyncio
async def test_a_normal_alias_to_a_nonexistent_old_id_is_accepted(
    project: Path, registry: ProjectRegistry
) -> None:
    """The happy path must stay green: the guard must refuse collisions, not aliases.

    ``addAlias former-name -> P`` where ``former-name`` is no item id is the
    ordinary rename the alias table exists for. It must apply with exit 0 and
    resolve. GREEN on HEAD; pinned so the write guard cannot pass its tests by
    refusing every ``addAlias``.
    """
    _write_body(project, "architecture/public-note.md", PUBLISHED_BODY)
    _write_migration(
        project,
        MIG_A,
        _migration(
            MIG_A,
            _create_item(PUBLISHED_ITEM),
            _published_upsert(),
            _add_alias(alias=FORMER_KEY, item_id=PUBLISHED_ITEM),
        ),
    )

    report = _json("migrate", "apply")
    resolved = await _payload(registry, "knowledge.get", projectId="demo", itemId=FORMER_KEY)

    assert MIG_A in report["applied"], "the ordinary rename alias must apply"
    assert resolved["itemId"] == PUBLISHED_ITEM, "and resolve to the item it names"
