"""``theurian okf export`` end to end, through the real CLI (ADR-0037 slice S2).

In-process through Typer's ``CliRunner``, over a project this module builds with
the product's own commands -- ``init``, then a migration, then ``migrate
apply`` -- so the export reads a state database the real write path produced and
a provenance record the real ``migrate apply`` wrote. Nothing here touches the
developer's own environment: the fixture ``chdir``s into ``tmp_path`` and points
``THEURIAN_DATA_DIR`` at a directory under it, which is the pattern
``test_cli_commands.py`` sets.

What this file is *for* is the wire contract and the composition: the option set,
the payload's keys, and that each refusal reaches a ``--json`` caller as a
document rather than a traceback. The bundle's own bytes are
``test_okf_export.py``'s.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
TITLE: Final = "Authentication policy"

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
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: {TITLE}
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


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A git working tree with an isolated data directory, as the CWD."""
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
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command with ``--json`` appended, and parse the document it wrote.

    stdout and stderr stay apart: this CLI keeps stdout a clean machine channel
    and puts a refusal on stderr, so a test that merged them could not tell a
    published report from a published refusal.
    """
    if args[:2] == ("migrate", "apply"):
        commit_migrations()
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _applied(root: Path) -> None:
    """One approved item in canonical state, through the real write path."""
    assert _invoke("init")[0] == 0
    (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY, encoding="utf-8")
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(
        MIGRATION, encoding="utf-8"
    )
    assert _invoke("migrate", "apply")[0] == 0


def test_the_export_writes_a_bundle_and_reports_what_it_holds(project: Path) -> None:
    _applied(project)
    target = project.parent / "bundle"

    code, payload = _invoke("okf", "export", str(target))

    assert code == 0, payload
    assert payload == {
        "bundlePath": str(target),
        "concepts": 1,
        "sidecars": 0,
        # The root's, and `architecture/`'s: the item id is
        # `architecture.auth-policy`, so its concept sits one directory down and
        # that directory gets an index of its own (ADR-0037 decision 2).
        "indexes": 2,
        "bundleDigest": payload["bundleDigest"],
    }
    assert sorted(
        str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
    ) == [
        "architecture/auth-policy.md",
        "architecture/index.md",
        "index.md",
        "theurian-bundle.md",
    ]
    assert BODY.strip() in (target / "architecture/auth-policy.md").read_text(encoding="utf-8")
    assert payload["bundleDigest"] in (target / "theurian-bundle.md").read_text(encoding="utf-8")


def test_the_payload_carries_no_knowledge_content(project: Path) -> None:
    """A path, three counts and a digest over the bundle's own files.

    The command writes knowledge to disk at a path the operator named; what it
    *publishes* is the report. A title or an item id here would be a second
    channel with its own disclosure question, and there is none.
    """
    _applied(project)

    _, payload = _invoke("okf", "export", str(project.parent / "bundle"))

    rendered = json.dumps(payload)
    assert TITLE not in rendered
    assert "signed token" not in rendered
    assert "auth-policy" not in rendered


def test_the_export_takes_no_option_that_widens_the_population(project: Path) -> None:
    """There is no `--include-unapproved` bundle (ADR-0037 decision 3).

    Asserted over the rendered option list rather than over the source, because
    the claim is about the surface a caller sees: an option added here would be
    the flag the ADR forbids, whatever it was called in Python.
    """
    result = runner.invoke(app, ["okf", "export", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    # The `Options:` section alone: the body above it is prose, and an em dash
    # written as two hyphens there is not an option.
    listed = result.stdout.split("Options:")[1]
    options = [word for word in listed.split() if word.startswith("--")]
    assert sorted(options) == ["--help", "--json"], result.stdout
    # One required positional and nothing else, spelled the way every other
    # argument in this CLI renders (`{proposal_id}`, `{project_id}`).
    assert result.stdout.split("\n")[0].endswith("[OPTIONS] {directory}")


def test_a_second_export_into_the_same_directory_is_refused_as_a_document(
    project: Path,
) -> None:
    """The refusal reaches a `--json` caller as `{error, remedy}`, not a traceback."""
    _applied(project)
    target = project.parent / "bundle"
    assert _invoke("okf", "export", str(target))[0] == 0

    code, payload = _invoke("okf", "export", str(target))

    assert code == 1
    assert "not empty" in payload["error"]
    assert "theurian okf export" in payload["remedy"]
    # The first bundle is untouched: a refusal before the walk writes nothing.
    assert (target / "theurian-bundle.md").exists()


def test_an_unbuilt_project_is_refused_with_the_cure_that_builds_it(project: Path) -> None:
    assert _invoke("init")[0] == 0

    code, payload = _invoke("okf", "export", str(project.parent / "bundle"))

    assert code == 1
    assert "nothing to export" in payload["error"]
    assert "theurian migrate apply" in payload["remedy"]
    assert not (project.parent / "bundle").exists()


def test_an_unloadable_migration_is_reported_as_a_document(project: Path) -> None:
    """#205's obligation for a new ``_require_project`` caller.

    Every command reaching ``resolve_context`` inherits its migration loading, so
    a migration that will not load must arrive as ``{error, remedy}`` rather than
    as a traceback. ``test_resolve_context_call_sites.py`` names this test as the
    discharge for this command's entry in its enumeration.
    """
    assert _invoke("init")[0] == 0
    (project / ".theurian/migrations/0001-broken.yaml").write_text(
        ": not : valid : yaml :\n", encoding="utf-8"
    )

    code, payload = _invoke("okf", "export", str(project.parent / "bundle"))

    assert code != 0
    assert payload["error"]
    assert payload["remedy"]
    assert not (project.parent / "bundle").exists()


def test_state_this_installation_did_not_build_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0004 and SEC-7, one artifact further out than `index build`.

    A `.theurian/state/` force-added past the ignore by a repository contributor
    would otherwise be projected into a bundle that names Theurian as its
    producer and is handed to somebody else. The provenance record lives in the
    data directory, which a repository cannot write to -- so pointing the data
    directory somewhere else is exactly the delivered-state condition.
    """
    _applied(project)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "another-installation"))
    assert _invoke("project", "register")[0] == 0

    code, payload = _invoke("okf", "export", str(project.parent / "bundle"))

    assert code == 1
    assert "not built by this Theurian installation" in payload["error"]
    assert payload["remedy"]
    assert not (project.parent / "bundle").exists()
