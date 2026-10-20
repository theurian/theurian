"""Measure the refusal-duration channel of ADR-0032 decision 6 (issue #702).

Command line, from the repository root::

    PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
        tools/measure/decision6_refusal_timing.py [--n-lookup 2000] [--n-wire 300]

Instrument: ``time.perf_counter_ns``, on both sides of every comparison. The
corpus is the one ``test_write_intent_disclosure_oracle.py`` builds (an in-view
item, a ``rejected`` item, a ``confidential`` item above the default ceiling),
built through the real CLI in a temporary project with ``HOME`` and
``THEURIAN_DATA_DIR`` redirected to temporary directories.

Two channels, each comparing a withheld id (rejected, confidential) with an
absent id, with the two sides interleaved call by call, the order alternating, so that drift and
position hit both:

* ``lookup``: the caller-scoped ``current_item`` lookup in isolation. The
  closure is local to ``theurian.mcp.tools``, so this reproduces its body (open
  the store, ``get_item_metadata``, ``may_surface``, ``may_disclose``); it is a
  copy, not the shipped closure, and tracks it only as long as that body does.
* ``wire``: a ``knowledge.proposeChange`` call carrying a stale
  ``expectedRevision``, through the MCP transport (``mcp_session``), timed
  around the whole ``tools/call`` round trip.

Per channel it prints N per side, the median of each side, the difference of
medians (withheld minus absent), the median absolute deviation (MAD) of each
side, and |difference| divided by the larger MAD. The numbers are printed as
measured; this script asserts nothing about them.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Final

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages/theurian-core/tests"))  # the test helpers live there

from git_harness import commit_migrations  # noqa: E402
from mcp_wire_session import mcp_session  # noqa: E402
from migration_fixtures import body_pin  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from theurian.application.authorization import StaticAuthorizationProvider  # noqa: E402
from theurian.application.project_service import (  # noqa: E402
    ProjectPaths,
    ProjectRegistry,
    read_active_state,
)
from theurian.cli.main import app  # noqa: E402
from theurian.daemon.runner import build_server  # noqa: E402
from theurian.domain.context import RequestContext  # noqa: E402
from theurian.domain.enums import may_disclose, may_surface  # noqa: E402
from theurian.domain.identifiers import ItemId, ProjectId  # noqa: E402
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore  # noqa: E402, TID251

IN_VIEW: Final = "architecture.auth-policy"
REJECTED: Final = "architecture.gamma-record"
CONFIDENTIAL: Final = "architecture.delta-record"
ABSENT: Final = "architecture.never-existed"
MIGRATION_ID: Final = "01K1EEEEEE01234567890ABCDE"
BODY: Final = "# Body\n\nContent nobody outside the team may be told exists.\n"
STALE_REVISION: Final = "01K1BBBREV01234567890ABCDE"
WARMUP: Final = 20


def _migration(items: tuple[tuple[str, str, str, str], ...]) -> str:

    head = (
        f"apiVersion: theurian.dev/v1\nid: {MIGRATION_ID}\n"
        "createdAt: 2026-08-02T16:00:00+09:00\nauthor: engineer@example.com\noperations:\n"
    )
    parts = []
    for item_id, revision, status, sensitivity in items:
        parts.append(f"""  - op: createItem
    itemId: {item_id}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item_id}
    revisionId: {revision}
    contentFile: ../knowledge/architecture/{item_id.split(".", 1)[1]}.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: {item_id}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      sensitivity: {sensitivity}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{item_id}.md
""")
    return head + "".join(parts)


def _build_corpus(root: Path) -> None:

    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    def run(*args: str) -> None:
        if args[:2] == ("migrate", "apply"):
            commit_migrations()
        result = CliRunner().invoke(app, [*args, "--json"], catch_exceptions=False)
        if result.exit_code != 0:
            raise SystemExit(f"`theurian {' '.join(args)}` failed: {result.stdout}")

    previous = Path.cwd()
    os.chdir(root)  # the CLI resolves the project from the working directory
    try:
        run("init")
        for item in (IN_VIEW, REJECTED, CONFIDENTIAL):
            (root / f".theurian/knowledge/architecture/{item.split('.', 1)[1]}.md").write_text(BODY)
        migration = _migration(
            (
                (IN_VIEW, "01K1AAAREV01234567890ABCDE", "approved", "internal"),
                (REJECTED, "01K1DDDRST01234567890ABCDE", "rejected", "internal"),
                (CONFIDENTIAL, "01K1CCCFDN01234567890ABCDE", "approved", "confidential"),
            )
        )
        (root / f".theurian/migrations/{MIGRATION_ID}-corpus.yaml").write_text(migration)
        run("project", "register")
        run("migrate", "apply")
    finally:
        os.chdir(previous)


def _propose_args(item_id: str) -> dict[str, Any]:
    return {
        "projectId": "demo",
        "itemId": item_id,
        "title": "Retry policy",
        "kind": "architecture",
        "owner": "platform-team",
        "author": "platform-team@example.com",
        "description": "Record the retry budget.",
        "body": "# Retry policy\n\nThree attempts.\n",
        "contentType": "text/markdown",
        "evidence": {
            "agentId": "claude-code",
            "taskId": "task-7",
            "model": "claude-opus-5",
            "reasoning": "Decision-6 refusal timing measurement.",
        },
        "labels": ["authored-in-theurian"],
        "expectedRevision": STALE_REVISION,
    }


def _lookup_channel(registry: Any, withheld: str, n: int) -> tuple[list[int], list[int]]:

    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    active = read_active_state(paths)
    if active is None:
        raise SystemExit("no built canonical state")
    database = paths.state / active.database_filename
    context = RequestContext(project_id=ProjectId("demo"))
    grant = StaticAuthorizationProvider().deployment_grant()

    def current_item(item_id: ItemId) -> object | None:
        with SqliteCanonicalStore(database) as store:
            item = store.get_item_metadata(context, item_id)
        if item is None:
            return None
        if not may_surface(item.status, include_unapproved=True) or not may_disclose(
            item.sensitivity, visible=grant.sensitivities
        ):
            return None
        return item

    if current_item(ItemId(withheld)) is not None or current_item(ItemId(ABSENT)) is not None:
        raise SystemExit("the lookup must answer None for both ids")
    withheld_id, absent_id = ItemId(withheld), ItemId(ABSENT)
    withheld_ns: list[int] = []
    absent_ns: list[int] = []
    for i in range(n + WARMUP):
        order = (withheld_id, absent_id) if i % 2 == 0 else (absent_id, withheld_id)
        took = {}
        for item_id in order:
            start = time.perf_counter_ns()
            current_item(item_id)
            took[item_id] = time.perf_counter_ns() - start
        if i >= WARMUP:
            withheld_ns.append(took[withheld_id])
            absent_ns.append(took[absent_id])
    return withheld_ns, absent_ns


def _wire_channel(
    registry: Any, data_dir: Path, withheld: str, n: int
) -> tuple[list[int], list[int]]:

    withheld_ns: list[int] = []
    absent_ns: list[int] = []
    with mcp_session(build_server(registry), data_dir / "wire") as call:
        for i in range(n + WARMUP):
            order = (withheld, ABSENT) if i % 2 == 0 else (ABSENT, withheld)
            took = {}
            for item_id in order:
                start = time.perf_counter_ns()
                answer = call("knowledge.proposeChange", _propose_args(item_id))
                took[item_id] = time.perf_counter_ns() - start
                if not answer["result"]["isError"]:
                    raise SystemExit(f"expected a refusal: {answer}")
            if i >= WARMUP:
                withheld_ns.append(took[withheld])
                absent_ns.append(took[ABSENT])
    return withheld_ns, absent_ns


def _mad(values: list[int]) -> float:
    median = statistics.median(values)
    return statistics.median(abs(v - median) for v in values)


def _report(channel: str, label: str, withheld: list[int], absent: list[int]) -> None:
    w, a = statistics.median(withheld), statistics.median(absent)
    delta = w - a
    noise = max(_mad(withheld), _mad(absent))
    ratio = abs(delta) / noise if noise else float("inf")
    print(
        f"{channel:7} {label:13} N={len(withheld)}/{len(absent)}  "
        f"median withheld={w / 1e3:10.1f} us  absent={a / 1e3:10.1f} us  "
        f"delta={delta / 1e3:+9.1f} us  "
        f"MAD={_mad(withheld) / 1e3:.1f}/{_mad(absent) / 1e3:.1f} us  "
        f"|delta|/MAD={ratio:.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--n-lookup", type=int, default=2000)
    parser.add_argument("--n-wire", type=int, default=300)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # per-call log output would sit inside the timed region

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(f"commit {commit}  instrument time.perf_counter_ns  warmup {WARMUP} calls per side")

    with tempfile.TemporaryDirectory(prefix="decision6-") as scratch:
        home = Path(scratch) / "home"
        root = Path(scratch) / "demo"
        data_dir = Path(scratch) / "datadir"
        home.mkdir()
        root.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["THEURIAN_DATA_DIR"] = str(data_dir)
        _build_corpus(root)

        registry = ProjectRegistry.default(data_dir)
        for label, item in (("rejected", REJECTED), ("confidential", CONFIDENTIAL)):
            _report("lookup", label, *_lookup_channel(registry, item, args.n_lookup))
        for label, item in (("rejected", REJECTED), ("confidential", CONFIDENTIAL)):
            _report("wire", label, *_wire_channel(registry, Path(scratch), item, args.n_wire))


if __name__ == "__main__":
    main()
