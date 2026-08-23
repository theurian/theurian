"""Derived state under `.theurian/state/` is served only if this install built it.

The class these tests pin (ADR-0004, SEC-7): everything under `.theurian/state/`
-- the active pointers and the SQLite databases they name -- is derived and
git-ignored, but nothing stops a repository contributor from force-adding a
doctored copy (`git add -f`, past the ignore) and shipping it. A victim who
clones (or downloads a ZIP/tarball) + `project register` + serves over MCP,
*without ever running `migrate apply`*, was then served the attacker's bytes.
`active.json`'s `stateHash` binds the migration set, not the database bytes, and
the database filename is derived from that hash, so a self-consistent doctored
pair -- a `rejected` body relabelled `approved`, every integrity record
recomputed to match -- has no internal inconsistency for the read-back guards to
catch (that face is what `test_revision_id_reuse.py` and the #30 PR2 signal
cover). The only discriminator an author of the repository cannot forge is
whether *this installation* built the artifact, recorded out of the repository
tree in `THEURIAN_DATA_DIR`.

The closure invariant, stated once and checked below: **a checkout shipping
derived state under `.theurian/state/` and a checkout shipping none produce
identical served knowledge -- both refuse until the state is built locally.**
Delivery does not matter: a clone (state tracked) and a repackaged tarball
(state present but untracked) are refused alike, which a `git ls-files` probe
could not do.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import BuildProvenance, ProjectRegistry
from theurian.cli.commands import _discard_untrusted_state
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT_ID = "demo"
APPROVED_ID = "architecture.auth-policy"
REJECTED_ID = "architecture.rejected-approach"

#: A marker that lives only in the rejected body, so its appearance anywhere a
#: caller reads is the disclosure itself -- the same tell the standalone
#: reproduction uses (a fabricated token, not a real credential).
LEAK_MARKER = "kf9-rejected-only-leak-77"
APPROVED_BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
REJECTED_BODY = f"# Rejected approach\n\nthe key {LEAK_MARKER} was pasted into the diff.\n"

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: author@example.com
operations:
  - op: createItem
    itemId: {APPROVED_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {APPROVED_ID}
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(APPROVED_BODY)}
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
  - op: createItem
    itemId: {REJECTED_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {REJECTED_ID}
    revisionId: 01K1CCCREV01234567890ABCDE
    contentFile: ../knowledge/architecture/rejected.md
    contentSha256: {body_pin(REJECTED_BODY)}
    metadata:
      title: Rejected approach
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: inferred
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/rejected.md
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


@contextlib.contextmanager
def _at(root: Path, data_dir: Path) -> Iterator[None]:
    """Run the in-process CLI as if invoked in ``root`` with ``THEURIAN_DATA_DIR``.

    Set together, restored together: `init`, `register`, `migrate apply` and
    `index build` all resolve the project from the working directory and the
    provenance store from the environment, so getting either wrong points the
    build at a different project or a different data directory than the serve
    step then reads.
    """
    prev_cwd = Path.cwd()
    prev_env = os.environ.get("THEURIAN_DATA_DIR")
    os.chdir(root)
    os.environ["THEURIAN_DATA_DIR"] = str(data_dir)
    try:
        yield
    finally:
        os.chdir(prev_cwd)
        if prev_env is None:
            os.environ.pop("THEURIAN_DATA_DIR", None)
        else:
            os.environ["THEURIAN_DATA_DIR"] = prev_env


def _cli(root: Path, data_dir: Path, *args: str) -> None:
    with _at(root, data_dir):
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, f"`theurian {' '.join(args)}` failed: {result.output}"


def _seed_author_repo(repo: Path) -> None:
    """A legitimate project, built by the real CLI, then committed."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "author@example.com")
    _git(repo, "config", "user.name", "Author")

    data = repo.parent / "author-data"
    _cli(repo, data, "init")
    knowledge = repo / ".theurian/knowledge/architecture"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "auth-policy.md").write_text(APPROVED_BODY, encoding="utf-8")
    (knowledge / "rejected.md").write_text(REJECTED_BODY, encoding="utf-8")
    (repo / f".theurian/migrations/{MIGRATION_ID}-seed.yaml").write_text(
        MIGRATION, encoding="utf-8"
    )
    _cli(repo, data, "project", "register")
    _cli(repo, data, "migrate", "apply")
    _cli(repo, data, "index", "build")


def _doctor_state(repo: Path) -> None:
    """Relabel the rejected item `approved` and reconcile the integrity count.

    A self-consistent edit: the read-back guards (#30 PR2) compare the pointer's
    `migrationCount` and the store's live surfaceable count, and both stay
    internally consistent because the attacker authored both sides. Nothing but
    provenance separates this from a state this install built.
    """
    (database,) = (repo / ".theurian/state").glob("theurian-state-*.sqlite")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE knowledge_items SET status='approved' WHERE item_id=?", (REJECTED_ID,)
        )
        live = connection.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE status IN ('approved','draft','proposed')"
        ).fetchone()[0]
        connection.execute("UPDATE project_integrity SET expected_surfaceable_count=?", (live,))
        connection.commit()


def _commit_doctored_state(repo: Path) -> None:
    """Force-add the doctored derived state past its ADR-0004 ignore, and commit."""
    _doctor_state(repo)
    _git(repo, "add", "-f", ".theurian/state")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    tracked = subprocess.run(
        ["git", "ls-files", ".theurian/state"],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert tracked.strip(), "the doctored state must actually be tracked for the clone vector"


def _register(repo: Path, data_dir: Path) -> ProjectRegistry:
    _cli(repo, data_dir, "project", "register")
    return ProjectRegistry.default(data_dir)


def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    async def invoke() -> Any:
        return await build_server(registry).call_tool(tool, arguments)

    result = asyncio.run(invoke())
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(result.content[0].text)
    return loaded


def _refused(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    """Assert a tool refuses, returning the message so its remedy can be checked."""
    with pytest.raises(SdkToolError) as raised:
        _call(registry, tool, **arguments)
    return str(raised.value)


def _serialise(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# clone: the doctored state is tracked and travels with `git clone`
# ---------------------------------------------------------------------------


def test_a_clone_without_apply_refuses_every_read(tmp_path: Path) -> None:
    """The primary vector: clone + register + serve, with NO `migrate apply`.

    Every knowledge tool must refuse rather than serve the attacker's bytes --
    `knowledge.get` on the relabelled item, `knowledge.status`, and both the
    ranked and unranked `knowledge.search` -- because none of `.theurian/state/`
    was built by this installation.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _commit_doctored_state(author)

    victim = tmp_path / "victim" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(victim))
    registry = _register(victim, tmp_path / "victim-data")

    get_message = _refused(registry, "knowledge.get", projectId=PROJECT_ID, itemId=REJECTED_ID)
    assert "migrate apply" in get_message
    assert LEAK_MARKER not in get_message

    _refused(registry, "knowledge.status", projectId=PROJECT_ID)
    _refused(registry, "knowledge.search", projectId=PROJECT_ID, query="token")


def test_the_refusal_names_the_situation_and_the_git_escape(tmp_path: Path) -> None:
    """The message must name what happened and both halves of the cure.

    Not a bare stack trace: it says the state was not built here, and it names the
    two commands that fix it -- rebuild locally, and untrack the committed copy so
    it does not return on the next checkout.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _commit_doctored_state(author)

    victim = tmp_path / "victim" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(victim))
    registry = _register(victim, tmp_path / "victim-data")

    message = _refused(registry, "knowledge.get", projectId=PROJECT_ID, itemId=REJECTED_ID)
    assert "was not built by this" in message
    assert "theurian migrate apply" in message
    assert "git rm --cached -r .theurian/state" in message


# ---------------------------------------------------------------------------
# ZIP / tarball: the same bytes, present but untracked
# ---------------------------------------------------------------------------


def test_a_repackaged_tarball_with_untracked_state_also_refuses(tmp_path: Path) -> None:
    """The face a `git ls-files` probe would miss.

    A ZIP download or a repackaged tarball carries the doctored `.theurian/state/`
    as ordinary files with no Git tracking metadata. Simulated by copying the
    seeded tree and re-initialising Git, so the state files are present but
    untracked. Provenance -- "did this install build it" -- refuses it exactly as
    it refuses the clone, because delivery is not the discriminator.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _doctor_state(author)

    victim = tmp_path / "victim" / "demo"
    shutil.copytree(author, victim)
    shutil.rmtree(victim / ".git")
    _git(victim, "init", "-q", "-b", "main")
    _git(victim, "config", "user.email", "victim@example.com")
    _git(victim, "config", "user.name", "Victim")

    # The state is on disk but Git tracks none of it -- the fresh `git init`
    # re-applies the `.theurian/` ignore, so a `git ls-files` probe sees nothing.
    # This is precisely the delivery a tracking probe would clear and provenance
    # must not.
    assert list((victim / ".theurian/state").glob("theurian-state-*.sqlite"))
    tracked = subprocess.run(
        ["git", "ls-files", ".theurian/state"],  # noqa: S607
        cwd=victim,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert not tracked.strip(), "the repackaged state must be present but untracked"

    registry = _register(victim, tmp_path / "victim-data")
    _refused(registry, "knowledge.get", projectId=PROJECT_ID, itemId=REJECTED_ID)
    _refused(registry, "knowledge.status", projectId=PROJECT_ID)


# ---------------------------------------------------------------------------
# after a local rebuild: the doctored bytes are discarded and reads are correct
# ---------------------------------------------------------------------------


def test_migrate_apply_discards_the_doctored_state_and_reads_are_correct(tmp_path: Path) -> None:
    """`migrate apply` rebuilds locally, records provenance, discards the doctored DB.

    Run against the cloned tree with the doctored state *in place*: because that
    database was not built here, `migrate apply` refuses to adopt it and rebuilds
    from the Git-tracked migrations, then records the rebuild's provenance. Reads
    then return the correct content -- the relabelled item is `rejected` again, so
    `knowledge.get` refuses it as the policy intends and the secret never
    surfaces.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _commit_doctored_state(author)

    victim = tmp_path / "victim" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(victim))
    victim_data = tmp_path / "victim-data"
    registry = _register(victim, victim_data)

    _cli(victim, victim_data, "migrate", "apply")
    _cli(victim, victim_data, "index", "build")

    # The approved item serves, correctly.
    approved = _call(registry, "knowledge.get", projectId=PROJECT_ID, itemId=APPROVED_ID)
    assert LEAK_MARKER not in _serialise(approved)
    assert "signed token" in _serialise(approved)

    # The relabelled item is `rejected` once more, so it is refused by policy and
    # its body -- the secret -- is never served.
    rejected_message = _refused(registry, "knowledge.get", projectId=PROJECT_ID, itemId=REJECTED_ID)
    assert LEAK_MARKER not in rejected_message

    status = _call(registry, "knowledge.status", projectId=PROJECT_ID)
    assert "integrity" not in status, f"a clean rebuild carries no damage signal: {status}"

    search = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="token")
    assert LEAK_MARKER not in _serialise(search)
    assert BuildProvenance.default(victim_data).has_state(victim.resolve(), status["stateHash"])


def test_discarding_untrusted_state_takes_the_wal_and_shm_sidecars(tmp_path: Path) -> None:
    """The discard removes a WAL-mode database's sidecars, not only the main file.

    A WAL-mode database keeps committed data in a `-wal` sidecar; deleting the
    main file alone could leave a poisoned WAL to be replayed against whatever is
    rebuilt in its place -- the replay the read side opens `mode=ro` to avoid.

    Pinned at the function level rather than through `migrate apply`, because a
    clean rebuild checkpoints and removes even a *surviving* sidecar on close, so
    the end state after an apply does not distinguish a discard that took the
    sidecars from one that left them -- while the discard step itself does.
    """
    database = tmp_path / "theurian-state-abc.sqlite"
    database.write_bytes(b"main-db-bytes")
    wal = database.with_name(f"{database.name}-wal")
    shm = database.with_name(f"{database.name}-shm")
    wal.write_bytes(b"committed-but-poisoned-wal")
    shm.write_bytes(b"shared-memory-index")

    _discard_untrusted_state(database)

    assert not database.exists()
    assert not wal.exists(), "a surviving -wal could be replayed against the rebuild"
    assert not shm.exists(), "a surviving -shm indexes the poisoned wal"


# ---------------------------------------------------------------------------
# the index sibling: a doctored index is stood aside, not served
# ---------------------------------------------------------------------------


def test_index_build_refuses_canonical_state_this_install_did_not_build(tmp_path: Path) -> None:
    """`index build` refuses to derive an index from an unprovenanced canonical state.

    Without the refusal a doctored `.theurian/state/` shipped in a repository
    launders through the build: `index build` reads its rows into a fresh index
    and records *that* index as this install's, so the serve-side index gate would
    then vouch for it. The canonical state must be one this install built (ADR-0004,
    SEC-7), so a clone that registers but never runs `migrate apply` is refused
    before anything is derived -- exit non-zero, and nothing published to serve.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _commit_doctored_state(author)

    victim = tmp_path / "victim" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(victim))
    victim_data = tmp_path / "victim-data"
    _register(victim, victim_data)

    with _at(victim, victim_data):
        result = runner.invoke(app, ["index", "build", "--json"], catch_exceptions=False)
    assert result.exit_code != 0, result.output
    assert "not built by this Theurian installation" in result.output
    assert LEAK_MARKER not in result.output


def test_ranked_search_refuses_the_index_of_an_unbuilt_clone(tmp_path: Path) -> None:
    """A doctored committed index, clone + no apply: ranked search must refuse.

    The index database carries titles, bodies and excerpts, so it is a disclosure
    vector of its own. In a clone with nothing built here, the canonical gate
    refuses `knowledge.search` before the index is even consulted -- the same
    single refusal as every other read.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _commit_doctored_state(author)

    victim = tmp_path / "victim" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(victim))
    registry = _register(victim, tmp_path / "victim-data")

    _refused(registry, "knowledge.search", projectId=PROJECT_ID, query="token")


def test_an_unprovenanced_index_stands_aside_to_the_trusted_canonical(tmp_path: Path) -> None:
    """Isolate the index gate: canonical trusted, index not built here.

    A repository whose canonical state this install *did* build, whose index
    pointer is then repointed at a build id this install did not build (an index
    dropped in after the fact). The ranked path must stand aside with
    `fallbackReason: index-unbuilt` and answer from the trusted canonical store
    rather than read the untrusted index -- so the doctored index never serves its
    bytes, while a search on the legitimately-built project still answers.
    """
    repo = tmp_path / "demo"
    _seed_author_repo(repo)
    data = repo.parent / "author-data"
    registry = ProjectRegistry.default(data)

    # Baseline: the legitimately-built index answers the ranked path.
    ranked = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="token")
    assert ranked["retrieval"]["fallbackReason"] is None

    # Repoint the pointer at a build id this installation never recorded, backed
    # by the real index file copied under that name -- structurally valid,
    # searchable, and untrusted.
    state = repo / ".theurian/state"
    (built,) = state.glob("theurian-index-*.sqlite")
    forged_id = "01K1F0RGED01234567890ABCDE"
    shutil.copy(built, state / f"theurian-index-{forged_id}.sqlite")
    pointer = state / "active-index.json"
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    pointer.write_text(json.dumps({**payload, "indexBuildId": forged_id}), encoding="utf-8")

    stood_aside = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="token")
    assert stood_aside["retrieval"]["fallbackReason"] == "index-unbuilt"
    assert LEAK_MARKER not in _serialise(stood_aside)


# ---------------------------------------------------------------------------
# the ranked path's real revision guard: `_may_surface`, not the get read-back
#   (T-18 -- the ranked path is defended by the current-revision-id match)
# ---------------------------------------------------------------------------

#: Two approved, indexed items sharing the query terms so both rank, with A
#: carrying a marker only it holds. Repointing A's canonical current-revision at
#: B's revision must drop A from the ranked answer.
SIBLING_A_ID = "architecture.sibling-a"
SIBLING_B_ID = "architecture.sibling-b"
SIBLING_A_REV = "01K1EAAREV01234567890ABCDE"
SIBLING_B_REV = "01K1EBBREV01234567890ABCDE"
SIBLING_A_MARKER = "sibling-a-only-marker-77"
SIBLING_A_BODY = f"# Deploy runbook\n\nDeploy rollout steps runbook: the {SIBLING_A_MARKER} path.\n"
SIBLING_B_BODY = "# Deploy checklist\n\nDeploy rollout steps checklist for staging and review.\n"

SIBLING_SEED_ID = "01K1EAAAAA01234567890ABCDE"
SIBLING_SEED = f"""apiVersion: theurian.dev/v1
id: {SIBLING_SEED_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: author@example.com
operations:
  - op: createItem
    itemId: {SIBLING_A_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {SIBLING_A_ID}
    revisionId: {SIBLING_A_REV}
    contentFile: ../knowledge/architecture/sibling-a.md
    contentSha256: {body_pin(SIBLING_A_BODY)}
    metadata:
      title: Deploy runbook
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/sibling-a.md
  - op: createItem
    itemId: {SIBLING_B_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {SIBLING_B_ID}
    revisionId: {SIBLING_B_REV}
    contentFile: ../knowledge/architecture/sibling-b.md
    contentSha256: {body_pin(SIBLING_B_BODY)}
    metadata:
      title: Deploy checklist
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/sibling-b.md
"""


def _seed_sibling_repo(repo: Path) -> ProjectRegistry:
    """Two approved, indexed items built here, returning the serve-side registry."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "author@example.com")
    _git(repo, "config", "user.name", "Author")

    data = repo.parent / "sibling-data"
    _cli(repo, data, "init")
    knowledge = repo / ".theurian/knowledge/architecture"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "sibling-a.md").write_text(SIBLING_A_BODY, encoding="utf-8")
    (knowledge / "sibling-b.md").write_text(SIBLING_B_BODY, encoding="utf-8")
    (repo / f".theurian/migrations/{SIBLING_SEED_ID}-seed.yaml").write_text(
        SIBLING_SEED, encoding="utf-8"
    )
    _cli(repo, data, "project", "register")
    _cli(repo, data, "migrate", "apply")
    _cli(repo, data, "index", "build")
    return ProjectRegistry.default(data)


def _repoint_current_revision(repo: Path, item_id: str, revision_id: str) -> None:
    """Repoint one item's canonical current-revision at ``revision_id``, on disk.

    A local write to a derived, unsigned state database (ADR-0004, SEC-7) -- the
    residual the provenance record does not close, since it vouches for a hash
    rather than the bytes. Checkpointed so a `mode=ro` serve reader sees the write
    without depending on WAL visibility.
    """
    (database,) = (repo / ".theurian/state").glob("theurian-state-*.sqlite")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        updated = connection.execute(
            "UPDATE knowledge_items SET current_revision_id=? WHERE item_id=?",
            (revision_id, item_id),
        ).rowcount
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert updated == 1, "the repoint must hit exactly the target item"


def test_ranked_search_drops_a_chunk_whose_canonical_revision_was_repointed(
    tmp_path: Path,
) -> None:
    """The ranked path's real revision guard is `_may_surface`, not the get read-back.

    The 61747b3 read-back guard defends `knowledge.get`; the *ranked* path is
    defended instead by `_may_surface` (visibility.py) matching each index row's
    `revision_id` against the item's current revision in the canonical store --
    the ranked path reads the item without dereferencing its pointer, so the
    canonical store's own foreign-pointer refusal (`current_revision`) never runs
    here. Pin that match directly: after a local `index build`, repoint item A's
    canonical `current_revision_id` at a *sibling* item's revision -- a tamper that
    is type-valid, satisfies the composite foreign key, and moves no #30 integrity
    count, so no other guard fires. Ranked search must drop A's chunk (its index
    revision no longer equals A's current one) while sibling B, untouched, still
    surfaces. A future change that weakens the revision-id match serves A's marker
    and goes RED.
    """
    repo = tmp_path / "demo"
    registry = _seed_sibling_repo(repo)

    # Baseline: both siblings rank from the provenanced index, A's marker included.
    baseline = _call(
        registry, "knowledge.search", projectId=PROJECT_ID, query="deploy rollout steps"
    )
    assert baseline["retrieval"]["fallbackReason"] is None, baseline
    assert baseline["count"] >= 2, baseline
    assert SIBLING_A_MARKER in _serialise(baseline), baseline

    _repoint_current_revision(repo, SIBLING_A_ID, SIBLING_B_REV)

    tampered = _call(
        registry, "knowledge.search", projectId=PROJECT_ID, query="deploy rollout steps"
    )
    # Still the ranked path (the index is provenanced and valid), and B still
    # surfaces -- the search served results, it did not fall back or refuse.
    assert tampered["retrieval"]["fallbackReason"] is None, tampered
    assert tampered["count"] >= 1, tampered
    # A's chunk is dropped: its index revision no longer matches A's current one.
    assert SIBLING_A_MARKER not in _serialise(tampered), (
        f"the repointed item's chunk was served despite the revision mismatch: {tampered}"
    )


# ---------------------------------------------------------------------------
# the launder sibling: a withdrawal purge must not copy an unprovenanced index
#   forward into a provenanced build (ADR-0024 decision 5, ADR-0004, SEC-7)
# ---------------------------------------------------------------------------

#: Two approved, indexed items. The keep item is doctored to carry the leak and
#: is searched; the drop item is withdrawn by an *unapplied* migration, so a
#: victim's `migrate apply` fires the withdrawal purge over the doctored index.
LAUNDER_KEEP_ID = "architecture.launder-keep"
LAUNDER_DROP_ID = "architecture.launder-drop"
LAUNDER_LEAK = "injected-launder-leak-akia7qx9"
LAUNDER_KEEP_BODY = (
    "# Gateway auth\n\nEvery request carries a signed bearer token for the gateway.\n"
)
LAUNDER_DROP_BODY = "# Caching\n\nA note about the cache layer and its eviction policy.\n"

LAUNDER_SEED_ID = "01K1DAAAAA01234567890ABCDE"
LAUNDER_WITHDRAW_ID = "01K1DBBBBB01234567890ABCDE"

LAUNDER_SEED = f"""apiVersion: theurian.dev/v1
id: {LAUNDER_SEED_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: author@example.com
operations:
  - op: createItem
    itemId: {LAUNDER_KEEP_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {LAUNDER_KEEP_ID}
    revisionId: 01K1DAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/launder-keep.md
    contentSha256: {body_pin(LAUNDER_KEEP_BODY)}
    metadata:
      title: Gateway auth
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/launder-keep.md
  - op: createItem
    itemId: {LAUNDER_DROP_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {LAUNDER_DROP_ID}
    revisionId: 01K1DBBREV01234567890ABCDE
    contentFile: ../knowledge/architecture/launder-drop.md
    contentSha256: {body_pin(LAUNDER_DROP_BODY)}
    metadata:
      title: Caching
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/launder-drop.md
"""

LAUNDER_WITHDRAW = f"""apiVersion: theurian.dev/v1
id: {LAUNDER_WITHDRAW_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: author@example.com
dependsOn:
  - {LAUNDER_SEED_ID}
operations:
  - op: deprecateItem
    itemId: {LAUNDER_DROP_ID}
    reason: retired
"""


def _seed_launder_repo(repo: Path) -> None:
    """A legitimate two-item project built + indexed here, then doctored + armed.

    The keep item's built chunk is doctored to carry `LAUNDER_LEAK`, and an
    *unapplied* withdrawal of the drop item is committed alongside the doctored
    index. On the victim, applying that withdrawal drives the purge over the
    doctored index -- the copy-forward that the launder attack rides.
    """
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "author@example.com")
    _git(repo, "config", "user.name", "Author")

    data = repo.parent / "launder-author-data"
    _cli(repo, data, "init")
    knowledge = repo / ".theurian/knowledge/architecture"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "launder-keep.md").write_text(LAUNDER_KEEP_BODY, encoding="utf-8")
    (knowledge / "launder-drop.md").write_text(LAUNDER_DROP_BODY, encoding="utf-8")
    (repo / f".theurian/migrations/{LAUNDER_SEED_ID}-seed.yaml").write_text(
        LAUNDER_SEED, encoding="utf-8"
    )
    _cli(repo, data, "project", "register")
    _cli(repo, data, "migrate", "apply")
    _cli(repo, data, "index", "build")

    (index,) = (repo / ".theurian/state").glob("theurian-index-*.sqlite")
    with contextlib.closing(sqlite3.connect(index)) as connection:
        connection.execute(
            "UPDATE chunks SET text = text || ' ' || ? WHERE item_id = ?",
            (LAUNDER_LEAK, LAUNDER_KEEP_ID),
        )
        injected = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE text LIKE ?", (f"%{LAUNDER_LEAK}%",)
        ).fetchone()[0]
        connection.commit()
    assert injected >= 1, "the doctored index must actually carry the leak"

    (repo / f".theurian/migrations/{LAUNDER_WITHDRAW_ID}-withdraw.yaml").write_text(
        LAUNDER_WITHDRAW, encoding="utf-8"
    )
    _git(repo, "add", "-f", ".theurian/state")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")


def test_migrate_apply_does_not_launder_a_doctored_index_via_withdrawal_purge(
    tmp_path: Path,
) -> None:
    """A withdrawal purge must not copy a committed, unprovenanced index forward.

    The purge (ADR-0024 decision 5) page-copies the currently published index,
    deletes the withdrawn revision's rows, and `migrate apply` records the copy as
    this install's build. On a fresh clone the published build is the attacker's
    committed, doctored `theurian-index-*.sqlite`, so an unguarded purge would
    launder its surviving injected chunk into a *provenanced* build that ranked
    search then trusts -- the serve-side `has_index` gate no longer standing it
    aside. A victim clones + registers + `migrate apply` (NO `index build`); the
    purge must decline the unprovenanced source, and ranked `knowledge.search`
    must fall aside to the canonical scan rather than serve the injected passage.
    """
    author = tmp_path / "author" / "demo"
    _seed_launder_repo(author)

    victim = tmp_path / "victim" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(victim))
    victim_data = tmp_path / "victim-data"
    registry = _register(victim, victim_data)

    with _at(victim, victim_data):
        applied = runner.invoke(app, ["migrate", "apply", "--json"], catch_exceptions=False)
    assert applied.exit_code == 0, applied.output
    purge = json.loads(applied.output)["indexPurge"]
    # The purge declined the committed, unprovenanced source (pre-fix: published
    # True with an empty reason), so no laundered build was recorded.
    assert purge["published"] is False, purge
    assert purge["reason"] == "untrusted-source-index", purge

    # No `index build` ran here, so the only index on disk is the doctored,
    # unprovenanced one. Ranked search must stand aside to the canonical store this
    # apply rebuilt and provenanced, never serve the laundered passage.
    search = _call(
        registry, "knowledge.search", projectId=PROJECT_ID, query="signed bearer token gateway"
    )
    assert LAUNDER_LEAK not in _serialise(search), (
        f"the doctored index passage was laundered into a served build: {search}"
    )
    assert search["retrieval"]["fallbackReason"] == "index-unbuilt", search


# ---------------------------------------------------------------------------
# no regression, and the closure invariant
# ---------------------------------------------------------------------------


def test_a_locally_built_project_serves_exactly_as_before(tmp_path: Path) -> None:
    """The control: a normal project built here serves without interference."""
    repo = tmp_path / "demo"
    _seed_author_repo(repo)
    registry = ProjectRegistry.default(repo.parent / "author-data")

    approved = _call(registry, "knowledge.get", projectId=PROJECT_ID, itemId=APPROVED_ID)
    assert "signed token" in _serialise(approved)
    status = _call(registry, "knowledge.status", projectId=PROJECT_ID)
    assert "integrity" not in status
    search = _call(registry, "knowledge.search", projectId=PROJECT_ID, query="token")
    assert search["retrieval"]["fallbackReason"] is None
    # The rejected item is withheld by policy, not disclosed.
    assert LEAK_MARKER not in _serialise(search)


def test_shipping_state_and_shipping_none_serve_identically(tmp_path: Path) -> None:
    """The closure invariant, one query against two checkouts.

    A checkout that ships derived state under `.theurian/state/` (the doctored
    clone) and one that ships none (the same tree with `.theurian/state`
    stripped) must produce identical served knowledge: both refuse, because
    neither was built by this installation. If shipping state changed the answer,
    a repository could choose what a victim is served -- which is the class.
    """
    author = tmp_path / "author" / "demo"
    _seed_author_repo(author)
    _commit_doctored_state(author)

    with_state = tmp_path / "with-state" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(with_state))
    with_registry = _register(with_state, tmp_path / "with-data")

    without_state = tmp_path / "without-state" / "demo"
    _git(tmp_path, "clone", "-q", str(author), str(without_state))
    shutil.rmtree(without_state / ".theurian/state")
    without_registry = _register(without_state, tmp_path / "without-data")

    with_message = _refused(
        with_registry, "knowledge.get", projectId=PROJECT_ID, itemId=REJECTED_ID
    )
    without_message = _refused(
        without_registry, "knowledge.get", projectId=PROJECT_ID, itemId=REJECTED_ID
    )
    # Both refuse. The one shipping state is refused for provenance; the one
    # shipping none is refused for having no state at all -- the caller learns the
    # same thing either way (run `migrate apply`), and neither is served content.
    assert "migrate apply" in with_message
    assert "migrate apply" in without_message
    assert LEAK_MARKER not in with_message
    assert LEAK_MARKER not in without_message
