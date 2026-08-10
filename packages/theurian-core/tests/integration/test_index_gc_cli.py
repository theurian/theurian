"""`theurian index gc` through the real CLI (ADR-0024 point 6).

**Written because a mutation run found six survivors here and nothing else.**
`gc` had no end-to-end test at all: its behaviour was correct and every part of
it was deletable. Each test below names the mutation it kills, because a test
that cannot say what it would catch is how the six got in.

The priority is the glob's prefix. `theurian-state-<hash>.sqlite` -- the
canonical store, which is neither derived nor disposable -- lives in the same
directory as the index builds. Dropping `theurian-index-` from the glob makes
`gc` delete the project's knowledge, and the whole suite stayed green. ADR-0022
point 2 records the same hazard one layer up: "a glob that could not tell them
apart would hand a retrieval index to the canonical store".
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1AAAAAA01234567890ABCDE
createdAt: 2026-08-03T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth
    revisionId: 01K1AREVAA01234567890ABCDE
    contentFile: ../knowledge/architecture/auth.md
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
          sourceUri: git://demo/auth.md
"""


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout or result.stderr or ""
    return result.exit_code, json.loads(stream) if stream.strip() else {}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
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
    (root / ".theurian/knowledge/architecture/auth.md").write_text(
        "# Authentication policy\n\nEvery call carries a signed token.\n"
    )
    (root / ".theurian/migrations/01K1AAAAAA01234567890ABCDE-auth.yaml").write_text(MIGRATION)
    assert runner.invoke(app, ["project", "register", "--json"]).exit_code == 0
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0
    yield root


def _builds(project: Path) -> list[str]:
    return sorted(p.name for p in (project / ".theurian/state").glob("theurian-index-*.sqlite"))


def test_publishing_a_build_no_longer_reclaims_the_one_it_replaced(project: Path) -> None:
    """ADR-0024 point 6, and #103 item 3, which nothing pinned.

    Kills: reinstating the post-publish reap loop, which survived 1,844 tests.
    """
    assert _invoke("index", "build")[0] == 0
    first = _builds(project)
    assert len(first) == 1

    assert _invoke("index", "build")[0] == 0

    after = _builds(project)
    assert len(after) == 2, (
        f"publishing must leave the previous build on disk for `gc` to reclaim; found {after}"
    )
    assert first[0] in after, "the build that was replaced is the one that disappeared"


def test_a_build_is_written_under_a_name_gc_will_not_reclaim(project: Path) -> None:
    """The `.building` discipline, at the builder rather than in a hand-made file.

    Kills: making `index_build` write directly under the final name, which
    survived because the only test of `.building` constructed the file itself and
    never checked that a real build produces one.

    Observed by watching the directory during the build: the completed name must
    not appear until the temporary one is gone.
    """
    seen: list[tuple[str, ...]] = []
    state = project / ".theurian/state"
    original = os.replace

    def watching(src: Any, dst: Any) -> Any:
        seen.append(tuple(sorted(p.name for p in state.glob("theurian-index-*"))))
        return original(src, dst)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "replace", watching)
        assert _invoke("index", "build")[0] == 0

    assert seen, "no rename happened, so the build never went through a temporary name"
    during = seen[0]
    assert any(name.endswith(".building") for name in during), (
        f"a build must be written under a `.building` name; during the build the state "
        f"directory held {during}"
    )
    assert not any(name.endswith(".sqlite") for name in during), (
        f"the completed name appeared before the rename, so `gc` could reclaim a build that "
        f"was still being written; during the build the directory held {during}"
    )


def test_gc_never_matches_a_file_without_the_index_prefix(project: Path) -> None:
    """The canonical store lives in the same directory, and it is not disposable.

    Kills: dropping `theurian-index-` from the glob, which deletes
    `theurian-state-<hash>.sqlite` -- the project's knowledge -- and stayed green
    across the whole suite.

    Asserted on the *names* rather than only on survival, so that a `gc` which
    reclaims the state database and happens to leave a copy behind still fails.
    """
    assert _invoke("index", "build")[0] == 0
    assert _invoke("index", "build")[0] == 0
    state = project / ".theurian/state"
    canonical = sorted(p.name for p in state.glob("theurian-state-*.sqlite"))
    assert canonical, "the fixture must have a canonical state database beside the builds"

    code, payload = _invoke("index", "gc")

    assert code == 0
    assert all(name.startswith("theurian-index-") for name in payload["reclaimed"]), (
        f"`gc` reclaimed something that is not an index build: {payload['reclaimed']}"
    )
    assert sorted(p.name for p in state.glob("theurian-state-*.sqlite")) == canonical, (
        "the canonical state database was reclaimed; it is not derived and not disposable"
    )


def test_a_dry_run_reports_a_plan_and_deletes_nothing(project: Path) -> None:
    """Kills: `--dry-run` performing the deletion anyway."""
    assert _invoke("index", "build")[0] == 0
    assert _invoke("index", "build")[0] == 0
    before = _builds(project)
    assert len(before) == 2

    code, payload = _invoke("index", "gc", "--dry-run")

    assert code == 0
    assert payload["dryRun"] is True
    assert payload["reclaimed"], "a dry run must still report what it would reclaim"
    assert _builds(project) == before, "a dry run deleted files"


def test_gc_reports_what_it_reclaimed_and_how_much(project: Path) -> None:
    """Kills: forcing `reclaimed` to `[]`, and forcing `bytesReclaimed` to 0.

    Both mutations leave the deletion working and the output lying, which is the
    shape that survives a test asserting only that the files are gone.
    """
    assert _invoke("index", "build")[0] == 0
    superseded = _builds(project)[0]
    size = (project / ".theurian/state" / superseded).stat().st_size
    assert _invoke("index", "build")[0] == 0

    code, payload = _invoke("index", "gc")

    assert code == 0
    assert payload["reclaimed"] == [superseded]
    assert payload["bytesReclaimed"] == size, (
        f"the reported size must be the bytes actually freed; got {payload['bytesReclaimed']} "
        f"for a {size}-byte build"
    )
    assert superseded not in _builds(project)


def test_gc_refuses_an_unreadable_pointer_and_says_so(project: Path) -> None:
    """Kills: removing the refusal, and changing its exit code from 1 to 0.

    A pointer this command cannot parse is not evidence that any particular build
    is unreferenced, so the answer is a refusal with a remedy rather than a
    reclaim of everything.
    """
    assert _invoke("index", "build")[0] == 0
    before = _builds(project)
    (project / ".theurian/state/active-index.json").write_text("{not json")

    code, payload = _invoke("index", "gc")

    assert code == 1, "a refusal must exit non-zero, or a script cannot tell it happened"
    assert set(payload) == {"error", "remedy"}
    assert _builds(project) == before, "a run that refused still deleted builds"


def test_gc_refuses_when_the_published_build_is_missing(project: Path) -> None:
    """A pointer aimed at nothing must not make every real build unreferenced.

    The worst possible reading of a broken pointer is "none of these is the
    published build, so all of them may go".
    """
    assert _invoke("index", "build")[0] == 0
    assert _invoke("index", "build")[0] == 0
    pointer = json.loads((project / ".theurian/state/active-index.json").read_text())
    (project / ".theurian/state" / f"theurian-index-{pointer['indexBuildId']}.sqlite").unlink()
    before = _builds(project)

    code, payload = _invoke("index", "gc")

    assert code == 1
    assert set(payload) == {"error", "remedy"}
    assert "theurian index build" in payload["remedy"]
    assert _builds(project) == before


def test_gc_reports_stranded_building_files_without_deleting_them(project: Path) -> None:
    """A crashed writer's leftovers are named, not reclaimed.

    `gc` cannot tell a live writer from a crash, so deleting would destroy work
    in progress. Reporting is the honest half; reclaiming needs an age or
    liveness heuristic that does not exist yet.
    """
    assert _invoke("index", "build")[0] == 0
    stranded = project / ".theurian/state/theurian-index-01K1CRASHED.sqlite.building"
    stranded.write_bytes(b"leftovers of a killed indexer")

    code, payload = _invoke("index", "gc")

    assert code == 0
    assert payload["strandedBuilding"] == [stranded.name]
    assert stranded.is_file(), "a `.building` file was deleted; it may be a live writer"


def test_gc_converts_an_unwritable_state_directory_into_its_own_contract(
    project: Path,
) -> None:
    """Kills the CP-2 escape: a bare `PermissionError` through Typer.

    Measured before the guard: a Rich traceback, exit 1, and **empty stdout under
    `--json`** -- for a condition whose remedy is one `chmod`. Every command
    promises `{error, remedy}`; an unguarded `unlink` loop does not.
    """
    assert _invoke("index", "build")[0] == 0
    assert _invoke("index", "build")[0] == 0
    state = project / ".theurian/state"
    before = _builds(project)
    state.chmod(0o500)
    try:
        code, payload = _invoke("index", "gc")
    finally:
        state.chmod(0o700)

    assert code == 1
    assert set(payload) == {"error", "remedy"}, (
        f"an OS refusal must arrive as this command's contract, not as a traceback; got "
        f"{sorted(payload)}"
    )
    assert "chmod" in payload["remedy"] or "writable" in payload["remedy"]
    assert _builds(project) == before


def test_gc_is_idempotent(project: Path) -> None:
    """A second run reclaims nothing and still succeeds."""
    assert _invoke("index", "build")[0] == 0
    assert _invoke("index", "build")[0] == 0
    assert _invoke("index", "gc")[0] == 0

    code, payload = _invoke("index", "gc")

    assert code == 0
    assert payload["reclaimed"] == []
    assert payload["bytesReclaimed"] == 0


def test_gc_keeps_a_finished_build_that_has_not_published_yet(project: Path) -> None:
    """The window `.building` cannot see: renamed into place, pointer not yet written.

    `index build` renames and *then* publishes, and a `gc` landing between the
    two reclaimed the file -- after which the pointer named nothing. Reproduced
    against the real CLI at 8 of 12 runs before the ULID rule was restored beside
    the `.building` one.
    """
    assert _invoke("index", "build")[0] == 0
    published = json.loads((project / ".theurian/state/active-index.json").read_text())
    later = ProjectPaths.of(project).index_for("01ZZZZZZZZZZZZZZZZZZZZZZZZ")
    later.write_bytes(b"a build that finished and has not published yet")

    code, payload = _invoke("index", "gc")

    assert code == 0
    assert later.name not in payload["reclaimed"], (
        f"`gc` reclaimed a build whose id sorts above the published "
        f"{published['indexBuildId']}, which is a build that started later and may be about "
        f"to publish"
    )
    assert later.is_file()
