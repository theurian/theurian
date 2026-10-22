"""Phase B's exit criterion, run as a test: propose -> PR -> merge -> apply, from a
plain MCP client against a real daemon (roadmap Phase B exit row; ADR-0013,
ADR-0032, ADR-0034).

Ported from the 2026-10-22 demonstration so the claim cannot rot. The "PR" is a
branch push followed by ``merge --no-ff`` into a local bare remote: Theurian
observes nothing of the merge (T-15, ADR-0034), so that local flow is the
human half. Expected values are the ones that run recorded.

Everything stays under ``tmp_path``: ``HOME``, the data directory and the git
ceiling are redirected, inherited ``GIT_*`` variables are stripped by prefix, and
the only remote is a bare repository beside the project.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from daemon_session import McpClient, serve_daemon
from migration_fixtures import body_pin

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
SEED_BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
SEED_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: human@example.com
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
    contentSha256: {body_pin(SEED_BODY)}
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
    "agentId": "plain-mcp-client",
    "taskId": "phase-b-exit-demo",
    "model": "none (hand-driven wire calls)",
    "reasoning": "Phase B exit-criterion demonstration.",
}

GIT_IDENTITY = (
    "-c",
    "user.email=human@example.com",
    "-c",
    "user.name=Human Reviewer",
    "-c",
    "commit.gpgsign=false",
)


@dataclass(frozen=True)
class Project:
    root: Path
    remote: Path
    env: dict[str, str]
    port: int
    token: str

    def cli(self, *args: str) -> dict[str, Any]:
        assert THEURIAN is not None
        done = subprocess.run(  # noqa: S603
            [THEURIAN, *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, (args, done.returncode, done.stdout, done.stderr)
        parsed: dict[str, Any] = json.loads(done.stdout)
        return parsed

    def git(self, *args: str) -> None:
        done = subprocess.run(  # noqa: S603
            ["git", *GIT_IDENTITY, *args],  # noqa: S607
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, (args, done.stdout, done.stderr)

    def merge_through_a_branch(self, proposal_id: str) -> None:
        """The human half: branch, commit, push, merge ``--no-ff`` into main, push."""
        branch = f"proposal/{proposal_id}"
        self.git("checkout", "-q", "-b", branch)
        self.git("add", ".theurian")
        self.git("commit", "-q", "-m", f"knowledge: accept proposal {proposal_id}")
        self.git("push", "-q", "origin", branch)
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", f"Merge {branch}", branch)
        self.git("push", "-q", "origin", "main")

    def human_cycle(self, proposal_id: str) -> None:
        self.cli("propose", "accept", proposal_id, "--json")
        self.merge_through_a_branch(proposal_id)
        self.cli("migrate", "apply", "--json")
        self.cli("index", "build", "--json")


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return {
        **inherited,
        "HOME": str(tmp_path / "home"),
        "THEURIAN_DATA_DIR": str(tmp_path / "data"),
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Project]:
    """One approved item, committed, pushed, applied and built; a daemon serving it."""
    (tmp_path / "home").mkdir()
    root = tmp_path / "demo"
    root.mkdir()
    remote = tmp_path / "remote.git"
    env = _isolated_env(tmp_path)
    stub = Project(root, remote, env, port=0, token="")

    stub.git("init", "-q", "--bare", "-b", "main", str(remote))
    stub.git("init", "-q", "-b", "main")
    stub.git("remote", "add", "origin", str(remote))
    stub.cli("init", "--json")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(SEED_BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(SEED_MIGRATION)
    stub.cli("project", "register", "--json")
    stub.git("add", "-A")
    stub.git("commit", "-q", "-m", "seed: one approved item")
    stub.git("push", "-q", "origin", "main")
    stub.cli("migrate", "apply", "--json")
    stub.cli("index", "build", "--json")

    assert THEURIAN is not None
    with serve_daemon(THEURIAN, root, env, tmp_path / "data", tmp_path / "daemon.log") as (
        port,
        token,
    ):
        yield Project(root, remote, env, port, token)


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    content: dict[str, Any] = result["structuredContent"]
    return content


def _project_id(client: McpClient) -> str:
    projects = _structured(client.call("project.list", {}))["projects"]
    return str(projects[0]["projectId"])


def test_a_proposed_item_is_served_once_a_human_merges_and_applies_it(project: Project) -> None:
    """The proposeChange half of the Phase B exit criterion: the proposal alone is
    not served (`get` is refused and `search` finds nothing), and after the human
    cycle the item is served approved and not stale (ADR-0013, ADR-0034)."""
    with McpClient(project.port, project.token) as client:
        project_id = _project_id(client)
        proposal = _structured(
            client.call(
                "knowledge.proposeChange",
                {
                    "projectId": project_id,
                    "itemId": "architecture.retry-policy",
                    "title": "Retry policy",
                    "kind": "architecture",
                    "owner": "platform-team",
                    "author": "human@example.com",
                    "description": "Record the retry budget.",
                    "body": (
                        "# Retry policy\n\n"
                        "Every outbound call gets three attempts with jittered backoff.\n"
                    ),
                    "contentType": "text/markdown",
                    "evidence": EVIDENCE,
                    "labels": ["authored-in-theurian"],
                },
            )
        )

        item = {"projectId": project_id, "itemId": "architecture.retry-policy"}
        query = {"projectId": project_id, "query": "retry attempts jittered backoff"}
        assert client.call("knowledge.get", item)["isError"] is True
        assert _structured(client.call("knowledge.search", query))["results"] == []

        project.human_cycle(proposal["proposalId"])

        got = _structured(
            client.call(
                "knowledge.get", {"projectId": project_id, "itemId": "architecture.retry-policy"}
            )
        )
        found = _structured(
            client.call(
                "knowledge.search",
                {"projectId": project_id, "query": "retry attempts jittered backoff"},
            )
        )

    assert got["status"] == "approved"
    assert [hit["itemId"] for hit in found["results"]] == ["architecture.retry-policy"]
    assert found["retrieval"]["stale"] is False


def test_a_deprecation_draft_retires_the_item_once_a_human_merges_and_applies_it(
    project: Project,
) -> None:
    """The generateMigrationDraft half, and SEC-13: a retired item is unreachable by
    `get` (with or without `includeUnapproved`) and by `search`, and leaves the count.

    The positive control reads the item through `get` and `search` before the cycle,
    so the refusals afterwards are a change and not a query that never matched."""
    with McpClient(project.port, project.token) as client:
        project_id = _project_id(client)
        item = {"projectId": project_id, "itemId": "architecture.auth-policy"}
        query = {"projectId": project_id, "query": "signed token authentication"}
        assert _structured(client.call("knowledge.get", item))["status"] == "approved"
        before_hits = [
            hit["itemId"] for hit in _structured(client.call("knowledge.search", query))["results"]
        ]
        assert before_hits == ["architecture.auth-policy"]
        before = _structured(client.call("knowledge.status", {"projectId": project_id}))

        proposal = _structured(
            client.call(
                "knowledge.generateMigrationDraft",
                {
                    "projectId": project_id,
                    "document": {
                        "author": "human@example.com",
                        "operations": [
                            {"op": "deprecateItem", "itemId": "architecture.auth-policy"}
                        ],
                    },
                    "evidence": EVIDENCE,
                },
            )
        )

        project.human_cycle(proposal["proposalId"])

        get_plain = client.call("knowledge.get", item)
        get_unapproved = client.call("knowledge.get", {**item, "includeUnapproved": True})
        after_hits = _structured(client.call("knowledge.search", query))["results"]
        after = _structured(client.call("knowledge.status", {"projectId": project_id}))

    assert get_plain["isError"] is True
    assert get_unapproved["isError"] is True
    assert "architecture.auth-policy" not in [hit["itemId"] for hit in after_hits]
    assert after["itemCount"] == before["itemCount"] - 1
