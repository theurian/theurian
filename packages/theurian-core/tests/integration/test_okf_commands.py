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
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The cell the damaged-state case writes. Nothing in it is a word this codebase
#: uses, so a fragment appearing in a published document came out of the database
#: file -- ``test_canonical_store_corruption.py``'s own sentinel and window, so the
#: two files are measuring disclosure on one scale.
SENTINEL: Final = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"
LEAK_WINDOW: Final = 12

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


#: A second item, `confidential` and above the default `internal` ceiling, plus an
#: edge to it from the approved one. Every string it owns is a marker: an export
#: that expanded the operator's ceiling instead of reading it would put these in a
#: file somebody else holds.
CONFIDENTIAL_ID: Final = "architecture.payroll-bands"
CONFIDENTIAL_REVISION: Final = "01K1CNFREV01234567890ABCDE"
CONFIDENTIAL_TITLE: Final = "withheld-payroll-title-6a2f"
CONFIDENTIAL_BODY: Final = "# Bands\n\nwithheld-payroll-body-4c8e\n"
CONFIDENTIAL_NOTE: Final = "withheld-payroll-note-1d7b"
CONFIDENTIAL_MIGRATION_ID: Final = "01K1BBBBBB01234567890ABCDE"

CONFIDENTIAL_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {CONFIDENTIAL_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {CONFIDENTIAL_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {CONFIDENTIAL_ID}
    revisionId: {CONFIDENTIAL_REVISION}
    contentFile: ../knowledge/architecture/payroll-bands.md
    contentSha256: {body_pin(CONFIDENTIAL_BODY)}
    metadata:
      title: {CONFIDENTIAL_TITLE}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      sensitivity: confidential
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/payroll-bands.md
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: depends_on
    targetItemId: {CONFIDENTIAL_ID}
    note: {CONFIDENTIAL_NOTE}
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


def _applied_with_a_confidential_row(root: Path) -> None:
    """The corpus above, plus one `confidential` row and an edge to it.

    Written as a second migration through the real ``migrate apply``, so the
    ceiling the export reads is the one ``load_serving_profile`` resolves from the
    operator's data directory rather than a value a test passed in.
    """
    _applied(root)
    (root / ".theurian/knowledge/architecture/payroll-bands.md").write_text(
        CONFIDENTIAL_BODY, encoding="utf-8"
    )
    (root / f".theurian/migrations/{CONFIDENTIAL_MIGRATION_ID}-add-payroll.yaml").write_text(
        CONFIDENTIAL_MIGRATION, encoding="utf-8"
    )
    assert _invoke("migrate", "apply")[0] == 0


def test_the_default_ceiling_keeps_a_confidential_row_and_its_edge_out_of_the_bundle(
    project: Path,
) -> None:
    """The deployment's declared ceiling decides the bundle, at the command (ADR-0037 decision 3).

    ``test_okf_export.py`` drives the sensitivity axis by passing
    ``visible_sensitivities`` directly; nothing above it measured the value the
    *command* resolves. So an exporter handed every :class:`Sensitivity` instead of
    ``grant.sensitivities`` produced a byte-identical bundle for every corpus this
    file held -- none of them varied a row's sensitivity -- and the substitution
    survived. Here it cannot: the corpus holds one `confidential` row above the
    default `internal` ceiling and one edge into it, and both the counts and the
    bytes are asserted.

    The edge matters as much as the row: a relation publishes the far end's id and
    its `note` whether or not the body goes with it, which is the pair T-21 and
    #119 were both measured leaking.
    """
    _applied_with_a_confidential_row(project)
    target = project.parent / "bundle"

    code, payload = _invoke("okf", "export", str(target))

    assert code == 0, payload
    assert payload["concepts"] == 1
    written = sorted(str(path.relative_to(target)) for path in target.rglob("*") if path.is_file())
    assert written == [
        "architecture/auth-policy.md",
        "architecture/index.md",
        "index.md",
        "theurian-bundle.md",
    ]
    whole = b"".join(path.read_bytes() for path in sorted(target.rglob("*")) if path.is_file())
    for marker in (
        CONFIDENTIAL_ID,
        CONFIDENTIAL_TITLE,
        "withheld-payroll-body-4c8e",
        CONFIDENTIAL_NOTE,
    ):
        assert marker.encode("utf-8") not in whole, marker
    # The corpus really holds the withheld row, so the sweep above ran over a
    # bundle that had something to leak.
    assert BODY.strip() in (target / "architecture/auth-policy.md").read_text(encoding="utf-8")


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


def test_a_target_with_two_absent_parent_levels_is_created_and_filled(project: Path) -> None:
    """`--help` promises "Created if absent" for the whole path, not just the leaf.

    The round-one symlink fix only creates components at or under the bundle
    root, so a target naming two missing parent levels -- `exports/2026-09-26`,
    neither of which exists yet -- raised `FileNotFoundError` instead of
    building the tree (round two, adversarial HIGH).
    """
    _applied(project)
    target = project.parent / "exports" / "2026-09-26" / "bundle"

    code, payload = _invoke("okf", "export", str(target))

    assert code == 0, payload
    assert (target / "theurian-bundle.md").exists()


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


def test_a_dotdot_target_past_an_absent_component_is_refused_rather_than_merged(
    project: Path,
) -> None:
    """`absent/../out` names `out` once `absent` exists, and not before.

    Every guard `lstat`s the exact path it is given, so a not-yet-existing
    component made all three read as absent regardless of what the same
    relative path names once that component is real -- and the ancestor
    `mkdir` that used to run afterward, inside the write, then materialised the
    missing component and made the walk merge into `out`, a separately
    populated prior bundle, without ever refusing (round three, adversarial
    HIGH). Materialising ancestors before every guard is what makes this
    refuse instead.
    """
    _applied(project)
    out = project.parent / "out"
    assert _invoke("okf", "export", str(out))[0] == 0
    (out / "withdrawn.md").write_text("a stale member from an earlier export", encoding="utf-8")
    before = sorted(str(path.relative_to(out)) for path in out.rglob("*"))

    code, payload = _invoke("okf", "export", str(project.parent / "absent" / ".." / "out"))

    assert code == 1
    assert "not empty" in payload["error"]
    after = sorted(str(path.relative_to(out)) for path in out.rglob("*"))
    assert after == before, "the second export merged into the populated prior bundle"


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


def test_an_ancestor_that_is_a_regular_file_is_refused_as_a_document(project: Path) -> None:
    """A cure names a listing only where there is something to list.

    Before the materialise-then-guard reorder, this scenario's ancestor `mkdir`
    ran inside the write, after the walk, and its bare `OSError` reached the
    CLI's generic catch-all -- "nothing was left at {target}" was that arm's own
    cure. This round's fix moves the same `mkdir` ahead of every guard and wraps
    it in a typed `OkfExportError` that walks up from the target to name the
    file actually blocking it, so the reorder changes which arm fires here: the
    generic `OSError` catch never runs, and the cure now points `ls -l` at
    `blocking`, a path that exists, rather than at `target`, which never will.
    """
    _applied(project)
    blocking = project.parent / "a-file"
    blocking.write_text("not a directory", encoding="utf-8")
    target = blocking / "bundle"

    code, payload = _invoke("okf", "export", str(target))

    assert code == 1
    assert str(blocking) in payload["error"]
    assert "Move or rename" in payload["remedy"]
    assert f"ls -l {blocking}" in payload["remedy"]
    assert f"ls -la {target}" not in payload["remedy"]
    assert blocking.read_text(encoding="utf-8") == "not a directory"
    assert not target.exists()


def test_a_damaged_cell_is_reported_as_a_document_and_quoted_nowhere(project: Path) -> None:
    """The command `test_canonical_store_corruption.py` excludes, at its own file.

    That sweep corrupts one cell and drives every command it can safely run a few
    hundred times; `okf export` is excluded from it because it needs a fresh target
    directory per invocation and its `_invoke` passes no arguments. The exclusion is
    the right call there and leaves this command unswept, so the one case is driven
    here: a `knowledge_items` cell the store must interpret, damaged the sweep's own
    way, reaching a `--json` caller as `{error, remedy}` at `EXIT_STATE_ERROR`
    rather than as a traceback carrying the cell.

    Windows of the sentinel are checked rather than the whole string, the sweep's
    own technique: an implementation that echoed half the cell would satisfy
    `SENTINEL not in published`.
    """
    _applied(project)
    database = next((project / ".theurian/state").glob("theurian-state-*.sqlite"))
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE knowledge_items SET valid_from = ?", (SENTINEL,))
        connection.commit()
    finally:
        connection.close()
    target = project.parent / "bundle"

    code, payload = _invoke("okf", "export", str(target))

    assert code == EXIT_STATE_ERROR
    assert payload["error"]
    assert payload["remedy"]
    published = json.dumps(payload)
    windows = [SENTINEL[at : at + LEAK_WINDOW] for at in range(len(SENTINEL) - LEAK_WINDOW + 1)]
    assert [window for window in windows if window in published] == []
    # `exists()` alone follows a link and answers about its target; a dangling
    # or self-referential one is still at the path and still `is_symlink()`.
    assert not target.exists(), "a refused walk left a partial bundle behind"
    assert not target.is_symlink(), "a refused walk left a link standing in for the target"


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
