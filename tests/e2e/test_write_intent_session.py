"""Approved knowledge is unchanged after a session that calls every write-intent
tool, against a real daemon (ADR-0013's owed E2E, discharged at slice B4).

ADR-0013's *Still owed* names this exactly -- "an E2E test asserting approved
knowledge is unchanged after a full agent session that calls every write-intent
tool" -- and records that it "still holds vacuously today" because no such tool
is registered. ADR-0032 slice B4 registers ``knowledge.proposeChange`` and
``knowledge.generateMigrationDraft``, so it stops being vacuous here.

The failure this prevents is the whole product's premise: an MCP write tool that
mutated approved knowledge directly, rather than emitting a proposal a human
merges. So a real agent session drives *every* registered write-intent tool
against a live daemon, and afterwards the canonical store and the approved
knowledge bodies are byte-identical -- while the proposal directory has grown by
exactly the calls made.

**"Every" is derived, not committed.** A session that called no write-intent tool
would satisfy "approved knowledge is unchanged" trivially, and a session that
called only the tools somebody remembered to list would satisfy it almost as
cheaply. So the write-intent set is **derived from what the daemon publishes**:
every registered tool whose input schema requires an ``evidence`` object is one
(:func:`_write_intent_tools`), and that set must equal the argument sets this
module carries. A newly registered write-intent tool with no entry in
:data:`WRITE_INTENT_CALLS` therefore reddens this test instead of going silently
unexercised -- which is the residual ADR-0013's *Still owed* recorded against the
committed-set shape this replaces.

**Not vacuous.** The derived set is asserted non-empty before it is compared, so
a build that registered no write-intent tool -- or a schema shape this reader
stopped seeing -- reports itself rather than passing as "nothing to call". Every
member is then called and asserted to have landed a distinct proposal.

**The derivation collected a third tool at slice B5, and the fixture grew to feed
it.** ``review.generateKnowledgeCandidate`` (ADR-0033) joined by registering with
a required ``evidence`` -- nothing here named it -- and the arguments it needed
were the work: it reads a *stored review record* and verifies its ``fixCommit``
against the daemon's **own** git repository, neither of which the B4 fixture had.
So the project now carries review evidence under ``.theurian/review/``, the store
``theurian review build`` derives from it, and a commit that touched the file the
stored thread is anchored to. That commit's sha cannot be written into
:data:`WRITE_INTENT_CALLS`, which is what :data:`FIX_COMMIT` and :func:`_resolved`
are for.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import review_candidate_fixtures as corpus
from migration_fixtures import body_pin

from theurian.application.project_service import ProjectPaths
from theurian.infrastructure.review_evidence import ReviewEvidenceStore

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]

STARTUP_TIMEOUT_SECONDS = 30.0

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

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
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

EVIDENCE = {
    "agentId": "claude-code",
    "taskId": "task-7",
    "model": "claude-opus-5",
    "reasoning": "The write path is exercised end to end.",
}

#: Stands in for the one argument no module-level dict can hold: the sha of a
#: commit this fixture makes at run time.
#:
#: ``review.generateKnowledgeCandidate`` verifies its ``fixCommit`` against the
#: daemon's **own** repository (ADR-0033 decision 3), so the value is not a
#: constant -- it is whatever ``git commit`` produced in this ``tmp_path``. The
#: placeholder keeps :data:`WRITE_INTENT_CALLS` the population key the derivation
#: below compares against, and :func:`_resolved` is what substitutes it. A
#: placeholder that reached the wire unsubstituted would be refused as a commit
#: this repository does not have, which is a failure rather than a silence.
FIX_COMMIT = "<the commit this fixture made>"

#: The arguments each write-intent tool is driven with. Every registered
#: write-intent tool must have an entry here, or the session cannot claim to call
#: "every" one -- and which tools those are is read off the daemon by
#: :func:`_write_intent_tools` rather than repeated here, so this dict is checked
#: against the surface instead of defining it.
WRITE_INTENT_CALLS: dict[str, dict[str, Any]] = {
    "knowledge.proposeChange": {
        "projectId": "demo",
        "itemId": "architecture.retry-policy",
        "title": "Retry policy",
        "kind": "architecture",
        "owner": "platform-team",
        "author": "platform-team@example.com",
        "description": "Record the retry budget.",
        "body": "# Retry policy\n\nThree attempts.\n",
        "contentType": "text/markdown",
        "evidence": EVIDENCE,
        "labels": ["authored-in-theurian"],
    },
    "knowledge.generateMigrationDraft": {
        "projectId": "demo",
        "document": {
            "author": "platform-team@example.com",
            "operations": [{"op": "deprecateItem", "itemId": "architecture.auth-policy"}],
        },
        "evidence": EVIDENCE,
    },
    # ADR-0033. The caller authors the generalization; what the daemon verifies is
    # the promotion gate, recomputed from the stored review record, and the
    # `fixCommit`, checked against its own repository. The record key and the
    # repository name are the corpus fixture's, so this call resolves the same
    # thread `tests/integration/test_candidate_generation_wire.py` drives -- the
    # one that meets all seven signals.
    "review.generateKnowledgeCandidate": {
        "projectId": "demo",
        "repository": corpus.REPOSITORY,
        "recordKey": corpus.THREAD_SATISFYING,
        "fixCommit": FIX_COMMIT,
        "itemId": "reliability.retry-lock-order",
        "title": "Acquire locks after reads in retry-eligible paths",
        "body": "Acquire locks after reads, never before, in retry-eligible paths.\n",
        "kind": "convention",
        "category": "reliability-rule",
        "owner": "platform-team",
        "author": "platform-team@example.com",
        "description": "Generalise the resolved deadlock thread into a locking rule",
        "evidence": EVIDENCE,
        "sourceAnchors": [
            {
                "provider": "github",
                "sourceUri": (
                    f"https://github.com/{corpus.REPOSITORY}/pull/"
                    f"{corpus.PULL_REQUEST_CI_PASSED}#discussion_r1"
                ),
                "repository": corpus.REPOSITORY,
                "filePath": corpus.FILE_PATH,
            }
        ],
    },
}


def _resolved(arguments: dict[str, Any], daemon: Daemon) -> dict[str, Any]:
    """``arguments`` with :data:`FIX_COMMIT` replaced by the sha the fixture made.

    A new dict rather than an edit in place: :data:`WRITE_INTENT_CALLS` is the
    population the derivation below is compared against, and a drive that mutated
    it would leave the second run of this module asserting against arguments the
    first one rewrote.
    """
    return {
        key: daemon.fix_commit if value == FIX_COMMIT else value for key, value in arguments.items()
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass(frozen=True)
class Daemon:
    port: int
    token: str
    root: Path

    #: A commit in :attr:`root` that touched the stored review thread's
    #: ``filePath``. Known only at run time, which is why :data:`FIX_COMMIT` exists.
    fix_commit: str


@pytest.fixture
def running_daemon(tmp_path: Path) -> Iterator[Daemon]:
    """A real daemon serving a project with one approved item and its review evidence.

    The review half is what ``review.generateKnowledgeCandidate`` needs and no
    other write-intent tool does: a record under ``.theurian/review/``, the
    derived search store ``theurian review build`` projects out of it, and a
    commit in this repository that touched the file the stored thread is anchored
    to.

    **The evidence is written in process, and that is the only offline route.**
    ``theurian review ingest`` spawns ``gh`` and contacts GitHub, which an E2E must
    not do; ``.theurian/review/`` is source rather than derived state, so a clone
    lands records there by carrying them, and this fixture is that clone. The
    store it derives is then built by the **real** command, so the provenance
    record a serving surface demands (ADR-0004, SEC-7) is written the way an
    operator writes it rather than forged here.
    """
    assert THEURIAN is not None
    root = tmp_path / "demo"
    root.mkdir()
    data_dir = tmp_path / "datadir"
    env = {**os.environ, "THEURIAN_DATA_DIR": str(data_dir)}

    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    def cli(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [THEURIAN, *args], cwd=root, env=env, check=True, capture_output=True, timeout=60
        )

    cli("init", "--json")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    cli("project", "register", "--json")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)  # noqa: S607
    commit = ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "commit the migration"]
    subprocess.run(commit, cwd=root, check=True, capture_output=True)  # noqa: S603
    cli("migrate", "apply", "--json")

    # Staged alone, and after the migration commit, so this sha means *this commit
    # touched that path*. A sweeping `git add -A` would leave no commit here that
    # is specific to the file the stored thread names, and the candidate tool's
    # check is exactly that specificity.
    (root / corpus.FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / corpus.FILE_PATH).write_text("def retry():\n    pass\n")
    subprocess.run(  # noqa: S603
        ["git", "add", corpus.FILE_PATH],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add retry helper"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )
    fix_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    ReviewEvidenceStore(ProjectPaths.of(root).review).write(
        corpus.evidence_records(), run=corpus.INGESTION_RUN
    )
    cli("review", "build", "--json")

    port = _free_port()
    log = tmp_path / "daemon.log"
    with log.open("wb") as sink:
        process = subprocess.Popen(  # noqa: S603
            [THEURIAN, "daemon", "start", "--foreground", "--port", str(port)],
            cwd=root,
            env=env,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )

    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _health(port) is not None:
                break
            if process.poll() is not None:
                pytest.fail(f"daemon exited: {log.read_text()}")
            time.sleep(0.2)
        else:
            pytest.fail(f"daemon did not become healthy in time: {log.read_text()}")

        token = (data_dir / "auth" / "mcp-token").read_text().strip()
        yield Daemon(port, token, root, fix_commit)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=10)


def _health(port: int) -> dict[str, Any] | None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status != 200:
            return None
        payload: dict[str, Any] = json.loads(response.read().decode())
    except OSError:
        return None
    finally:
        connection.close()
    return payload


class _McpClient:
    """A minimal Streamable HTTP MCP client, owning its connection explicitly so
    no keep-alive socket is left to the garbage collector under ``filterwarnings =
    error``."""

    def __init__(self, port: int, token: str) -> None:
        self._connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self._token = token
        self._session: dict[str, str] = {}

    def __enter__(self) -> _McpClient:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "session", "version": "1"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *_: object) -> None:
        self._connection.close()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._connection.request(
            "POST",
            "/mcp",
            body=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self._session,
            },
        )
        response = self._connection.getresponse()
        if session_id := response.getheader("mcp-session-id"):
            self._session["mcp-session-id"] = session_id
        raw = response.read().decode()
        if not raw.strip():
            return {}
        match = re.search(r"^data: (.*)$", raw, re.MULTILINE)
        parsed: dict[str, Any] = json.loads(match.group(1) if match else raw)
        return parsed

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Every registered tool's published input schema, by name.

        The schema rather than the name alone, because what makes a tool
        write-intent is visible in what it requires of a caller and is not
        recoverable from a list of names -- see :func:`_write_intent_tools`.
        """
        response = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        return {tool["name"]: tool.get("inputSchema", {}) for tool in response["result"]["tools"]}

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        result: dict[str, Any] = response["result"]
        return result

    def approved_view(self) -> dict[str, Any]:
        """What the read tools say about approved knowledge, read through the daemon.

        Deterministic reads of the canonical state -- the status breakdown and the
        approved item's own record. Read through the daemon, so a change a
        write-intent tool buffered in the store's WAL (not yet in the main file)
        would show here even though :func:`_tree_digest` excludes the WAL.
        """
        status = self.call("knowledge.status", {"projectId": "demo"})
        item = self.call(
            "knowledge.get", {"projectId": "demo", "itemId": "architecture.auth-policy"}
        )
        return {"status": status["structuredContent"], "item": item["structuredContent"]}


#: SQLite's read-time sidecars. A read in WAL mode creates an (empty) ``-wal`` and
#: a ``-shm`` beside the store, so a fingerprint that counted them would report a
#: *read* as a change to approved knowledge. They are excluded, and the behavioural
#: read below is what catches a change buffered in the WAL rather than committed to
#: the main file.
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _tree_digest(*roots: Path) -> str:
    """A digest of every file under *roots*, by relative path and content, with
    SQLite's read-time sidecars excluded.

    This is the on-disk "approved knowledge" fingerprint: the canonical store's
    main database file and the approved knowledge bodies. A proposal a write-intent
    tool lands sits under a different directory, so it does not enter here -- which
    is the property under test.
    """
    digest = hashlib.sha256()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not any(path.name.endswith(s) for s in _SQLITE_SIDECARS):
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
    return digest.hexdigest()


#: What marks a registered tool as write-intent, over the wire.
#:
#: ADR-0032 decision 4: ``agentId``, ``taskId``, ``model`` and ``reasoning`` are
#: **required on every write-intent call**, and the object that carries them is
#: ``evidence``. A tool that drafts a proposal cannot package one without it --
#: ``require_evidence`` refuses twice, once in ``Evidence.__post_init__`` and again
#: in ``ProposalRequest.__post_init__``, so that "rejected at generation" is a
#: property of the generation path rather than of one constructor. No read tool
#: asks for it, because a read records no provenance.
#:
#: It is the *registration* that answers this, and slice B5 is where that was
#: collected rather than asserted: ADR-0033's `review.generateKnowledgeCandidate`
#: builds its `proposal.Evidence` from the tool's own ``evidence`` input, exactly
#: as ADR-0032's tools take them, so it **joined this set by registering** -- this
#: key was not touched, and what had to be added was its arguments below.
WRITE_INTENT_INPUT_KEY = "evidence"


def _write_intent_tools(schemas: dict[str, dict[str, Any]]) -> set[str]:
    """Which registered tools are write-intent, derived rather than listed.

    Keyed on the published input schema's ``required`` list, so the answer comes
    from the daemon's own surface. A hand-written list here would reintroduce
    exactly the drift this replaces: it would be checked against itself.
    """
    return {
        name
        for name, schema in schemas.items()
        if WRITE_INTENT_INPUT_KEY in schema.get("required", ())
    }


def _proposal_count(root: Path) -> int:
    proposals = root / ".theurian/proposals"
    if not proposals.exists():
        return 0
    return sum(1 for entry in proposals.iterdir() if entry.is_dir())


def test_a_session_calling_every_write_intent_tool_leaves_approved_knowledge_unchanged(
    running_daemon: Daemon,
) -> None:
    """ADR-0013's owed E2E, no longer vacuous.

    The canonical store (``.theurian/state``) and the approved knowledge bodies
    (``.theurian/knowledge``) are fingerprinted before and after a session that
    calls every registered write-intent tool. They are byte-identical afterward --
    no MCP tool wrote approved knowledge -- while the proposal directory has grown
    by exactly the calls made, which is where "AI proposes" lands.

    The session's coverage is the control, and it is **derived from the daemon**:
    the write-intent tools are the registered ones whose published input schema
    requires ``evidence`` (ADR-0032 decision 4), that set must equal the argument
    sets this module carries, and every member is called and asserted to have
    returned a distinct proposal id. A session that reached none of them would
    pass the unchanged-knowledge assertion for the wrong reason; a session that
    reached all the ones somebody listed while a tenth registered unlisted would
    pass it for a subtler one, and the equality is what forbids both.
    """
    root = running_daemon.root
    approved = (root / ".theurian/state", root / ".theurian/knowledge")

    before_digest = _tree_digest(*approved)
    before_proposals = _proposal_count(root)

    with _McpClient(running_daemon.port, running_daemon.token) as client:
        schemas = client.tool_schemas()
        registered_write_intent = _write_intent_tools(schemas)

        assert registered_write_intent, (
            f"no registered tool publishes a required `{WRITE_INTENT_INPUT_KEY}` input, so "
            f"this session has nothing to drive and every assertion below would hold "
            f"vacuously. Either the build registers no write-intent tool, or the published "
            f"input schemas stopped carrying evidence the way ADR-0032 decision 4 requires: "
            f"registered={sorted(schemas)}"
        )
        driven = set(WRITE_INTENT_CALLS)
        assert registered_write_intent == driven, (
            f"the registered write-intent tools and the argument sets this session carries "
            f"are not the same set, so it cannot claim to call every one.\n"
            f"  registered but undriven: {sorted(registered_write_intent - driven)}\n"
            f"  driven but unregistered: {sorted(driven - registered_write_intent)}\n"
            f"A registered tool with no entry above is a write path this test claims to "
            f"exercise and does not (ADR-0013's owed E2E); add its arguments in the change "
            f"that registers it."
        )

        before_view = client.approved_view()

        landed: dict[str, str] = {}
        for tool in sorted(registered_write_intent):
            result = client.call(tool, _resolved(WRITE_INTENT_CALLS[tool], running_daemon))
            assert result["isError"] is False, (tool, result)
            proposal_id = result["structuredContent"]["proposalId"]
            assert proposal_id, (tool, result)
            landed[tool] = proposal_id

        after_view = client.approved_view()

    # Every write-intent tool actually did work: a distinct proposal each.
    assert set(landed) == set(WRITE_INTENT_CALLS)
    assert len(set(landed.values())) == len(WRITE_INTENT_CALLS), landed

    # Behavioural: the read tools report the same approved knowledge -- this reads
    # the live WAL-merged state, so it catches a change the on-disk digest cannot.
    assert after_view == before_view, (
        "a read tool reported different approved knowledge after the write-intent "
        "session -- a write reached the canonical store (ADR-0013)"
    )

    # On disk: the canonical store's main file and the approved bodies are
    # byte-identical (SQLite's read-time WAL/SHM sidecars excluded).
    after_digest = _tree_digest(*approved)
    after_proposals = _proposal_count(root)

    assert after_digest == before_digest, (
        "a write-intent tool changed approved knowledge -- the canonical store's main "
        "file or an approved body moved. No MCP tool may write approved state (ADR-0013)"
    )
    assert after_proposals == before_proposals + len(WRITE_INTENT_CALLS), (
        f"the session did not land one proposal per write-intent tool: "
        f"{before_proposals} -> {after_proposals}"
    )
