"""A planted link at a derived write target destroys nothing (#523, #394, #371).

One root cause with six members: **a link-following, truncating write to a
derived path an attacker can plant**. ``Path.write_text`` and ``open(path, "w")``
follow a symbolic link and then ``O_TRUNC`` whatever it names, so a link a clone
delivers -- force-added past ADR-0004's ignore -- turned a routine command into a
destructive write somewhere else. PR #518 fixed the first member (the write lock,
#481); this file drives the other five.

**Measured on ``1fe3302b`` against the real CLI, every face below exited 0 with a
success report and the victim's body replaced.** That is what the assertions are
shaped around: each one reads the victim's *bytes*, because "the file still
exists" is satisfied by a truncation and "the link is gone" is satisfied by an
unlink-then-write. Both faces of every plant are driven -- a link pointing out of
the working tree, which containment refuses, and one pointing at a tracked file
*inside* it, which containment correctly waves through and ``O_NOFOLLOW`` is the
only thing that refuses.

The mechanism-level tests live in ``tests/unit/test_no_follow_writes.py``,
including the source-level guard that keeps the population of atomic publishers
exhaustive. This file is the end-to-end half: what a caller running the shipped
commands actually receives.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths
from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app
from theurian.domain.errors import SecurityError
from theurian.infrastructure.secrets.file_store import TOKEN_KEY, FileSecretStore

pytestmark = pytest.mark.integration

runner = CliRunner()

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

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

#: What a victim file holds before a command runs. Compared byte for byte
#: afterwards: a truncation to zero and an overwrite are both "the file is still
#: there", and both are the defect.
VICTIM: Final = b"VICTIM BODY THAT MUST SURVIVE\n"


class Ran:
    """One command's result, in the shape the assertions here ask about."""

    def __init__(self, exit_code: int, stdout: str, stderr: str, escaped: str | None) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.escaped = escaped

    @property
    def envelope(self) -> dict[str, Any] | None:
        """The ``{error, remedy}`` document, or ``None`` when stderr held no JSON.

        ``None`` is what an escaped exception leaves behind, which is the CP-2
        failure every assertion below has to be able to tell apart from a refusal.
        """
        if not self.stderr.strip():
            return None
        try:
            parsed = json.loads(self.stderr)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def _run(*args: str) -> Ran:
    result = runner.invoke(app, [*args, "--json"])
    escaped = result.exception
    if isinstance(escaped, SystemExit):
        escaped = None
    return Ran(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr or "",
        escaped=None if escaped is None else type(escaped).__name__,
    )


def _must_succeed(*args: str) -> None:
    ran = _run(*args)
    assert ran.exit_code == 0, f"{args}: {ran.stdout}{ran.stderr}"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A registered project with one applicable migration, nothing built yet.

    ``HOME`` and ``THEURIAN_DATA_DIR`` go through ``monkeypatch`` rather than
    ``os.environ``, and the ``chdir`` is here too: the CLI resolves a project from
    the working directory, so a test that forgot it would run against the
    developer's own checkout.
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
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)

    _must_succeed("init")
    _must_succeed("project", "register")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    return root


@pytest.fixture
def built(project: Path) -> Path:
    """The same project with canonical state applied, so ``index build`` can run."""
    _must_succeed("migrate", "apply")
    return project


def _plant_outside(root: Path, relative: str) -> Path:
    """A link at ``relative`` naming a file genuinely outside the working tree.

    Returns the victim. The link is *relative*, because that is what a clone
    carries, and the target is asserted outside the resolved root -- the property
    every "containment refuses this" claim below turns on, so it is checked rather
    than assumed.
    """
    victim = root.parent / f"victim-{relative.replace('/', '-')}"
    victim.write_bytes(VICTIM)
    link = root / ".theurian" / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    depth = len(link.relative_to(root).parts)
    link.symlink_to(Path(*[".."] * depth) / victim.name)
    assert not victim.resolve().is_relative_to(root.resolve())
    return victim


def _plant_inside(root: Path, relative: str) -> Path:
    """A link at ``relative`` naming a tracked file *inside* the working tree.

    The face containment cannot refuse: the link resolves inside the root, so
    ``_contain`` passes it -- correctly, nothing escapes -- and the only thing that
    stops the write is ``O_NOFOLLOW``.
    """
    victim = root / "runbook.md"
    victim.write_bytes(VICTIM)
    link = root / ".theurian" / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    # One fewer `..` than :func:`_plant_outside` uses, because the victim sits at
    # the root rather than beside it. Getting this off by one is not a broken
    # fixture, it is a *different test*: the link resolves above the root, so
    # containment refuses it and the assertion about `O_NOFOLLOW` is made over a
    # refusal `O_NOFOLLOW` never issued. Asserted below rather than trusted.
    depth = len(link.relative_to(root).parts) - 1
    link.symlink_to(Path(*[".."] * depth) / victim.name)
    assert link.resolve() == victim.resolve(), "the in-tree plant does not name the victim"
    assert victim.resolve().is_relative_to(root.resolve())
    return victim


def _assert_refused_cleanly(ran: Ran, *, code: int = EXIT_STATE_ERROR) -> dict[str, Any]:
    """The CP-2 contract: nonzero, one parseable document on stderr, clean stdout.

    Asserted on every refusal this file produces because #549 closed exactly this
    class for the neighbouring commands, and a new raise site that escapes
    ``--json`` reopens it. ``escaped`` is read off the runner rather than off the
    streams: ``CliRunner`` keeps an uncaught exception on ``result.exception``
    instead of letting Typer's Rich handler render it, so the escape would be
    invisible in what was captured.
    """
    assert ran.escaped is None, (
        f"an exception reached the caller instead of a document: {ran.escaped}"
    )
    assert ran.exit_code == code, f"exit {ran.exit_code}, stderr: {ran.stderr}"
    assert ran.stdout == "", f"the machine channel was not clean: {ran.stdout!r}"
    envelope = ran.envelope
    assert envelope is not None, f"stderr held no JSON document: {ran.stderr!r}"
    assert envelope.get("error"), "the envelope carries no message"
    assert envelope.get("remedy"), "the envelope carries no cure"
    return envelope


def _assert_names_a_removable_link(envelope: dict[str, Any], link: Path) -> None:
    """The remedy names the artefact to act on and something the reader can run.

    Not "the remedy is non-empty": a placeholder passes that, and this project has
    shipped one three times. What the **in-tree** refusal knows is exactly which
    file is the link -- ``O_NOFOLLOW`` constrains the final component -- so its cure
    names that path and the act that clears it.
    """
    assert str(link) in envelope["remedy"], (
        f"the cure does not name the file to act on: {envelope['remedy']}"
    )
    assert "Remove the symbolic link" in envelope["remedy"]


def _assert_names_the_derived_directory(
    envelope: dict[str, Any], subdirectory: str, rebuild: str
) -> None:
    """The **out-of-tree** refusal's cure, which names a directory and not a leaf.

    Deliberately a different assertion from the one above, rather than a looser
    one covering both. Containment is a chokepoint over the whole path, and a link
    anywhere between the derived subdirectory and the leaf produces the identical
    refusal -- so naming the leaf there would send a reader to remove a file inside
    the link's target and cure nothing (the reasoning recorded on
    ``derived_escape_remedy``). A test that accepted either wording would let a
    later change swap them and stay green.
    """
    assert f"Remove `{subdirectory}`" in envelope["remedy"], (
        f"the cure does not name the directory to remove: {envelope['remedy']}"
    )
    assert rebuild in envelope["remedy"], (
        f"the cure does not say how to rebuild what it removes: {envelope['remedy']}"
    )


def _tmp_leftovers(root: Path) -> list[str]:
    return sorted(path.name for path in (root / ".theurian/state").glob("*.tmp"))


# -- AC1: the active-state pointer, through `migrate apply` ------------------


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", [_plant_outside, _plant_inside], ids=["out-of-tree", "in-tree"])
def test_migrate_apply_refuses_a_link_at_the_active_pointer_temporary(
    project: Path, plant: Any
) -> None:
    """AC1. Both faces of the plant #523 filed, driven through the shipped command.

    On ``1fe3302b`` this exited 0 with a full apply report while the victim's body
    became the active-state JSON, and ``os.replace`` then renamed the link itself
    over ``active.json`` -- so the next command met a pointer resolving outside the
    tree and the operator's file was already gone.

    The two faces share this contract and not their wording: the out-of-tree one is
    refused by containment, whose cure names the directory, and the in-tree one by
    the open, whose cure names the leaf. Each is asserted in its own test below,
    because a single assertion loose enough to accept both would accept the wrong
    one.
    """
    victim = plant(project, "state/active.json.tmp")

    ran = _run("migrate", "apply")

    _assert_refused_cleanly(ran)
    assert victim.read_bytes() == VICTIM
    assert (project / ".theurian/state/active.json.tmp").is_symlink(), (
        "the refusal removed the evidence"
    )


@_NEEDS_SYMLINKS
def test_the_in_tree_apply_refusal_names_the_temporary_to_remove(project: Path) -> None:
    """The face only ``O_NOFOLLOW`` refuses, and the wording it earns.

    Containment passes this link -- its target is inside the working tree, so
    nothing escapes -- and the open is what declines it. Because that decision is
    made at the final component, the cure can name the leaf rather than the
    directory holding it, which is the difference from the escape below.
    """
    _plant_inside(project, "state/active.json.tmp")

    envelope = _assert_refused_cleanly(_run("migrate", "apply"))

    _assert_names_a_removable_link(envelope, project / ".theurian/state/active.json.tmp")


@_NEEDS_SYMLINKS
def test_the_out_of_tree_apply_refusal_names_the_state_directory(project: Path) -> None:
    """The containment face: the culprit can sit anywhere on the chain.

    ``_contain`` resolves the whole path, so a link at ``state`` and a link at the
    leaf produce the identical refusal and it cannot tell them apart. The cure
    therefore names the derived subdirectory and how to rebuild it.
    """
    _plant_outside(project, "state/active.json.tmp")

    envelope = _assert_refused_cleanly(_run("migrate", "apply"))

    _assert_names_the_derived_directory(envelope, ".theurian/state", "theurian migrate apply")


@_NEEDS_SYMLINKS
def test_a_refused_apply_publishes_no_active_pointer(project: Path) -> None:
    """The refusal's other half: it is a claim about what did not happen.

    An envelope that names a symbolic link while the pointer was published anyway
    is the shape #525's round-one CRITICAL took one command over, so the pointer is
    read rather than inferred from the exit code.
    """
    _plant_outside(project, "state/active.json.tmp")

    _run("migrate", "apply")

    assert not (project / ".theurian/state/active.json").exists()


# -- AC2: the index pointer and the secret-scan record, through `index build` -


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", [_plant_outside, _plant_inside], ids=["out-of-tree", "in-tree"])
def test_index_build_refuses_a_link_at_the_index_pointer_temporary(built: Path, plant: Any) -> None:
    """AC2, the index pointer. Measured exit 0 with the victim overwritten before.

    The published pointer is read on both sides, not inferred from the exit code:
    an envelope naming a symbolic link while the build published anyway is the
    exact shape #525's round-one CRITICAL took one artefact over.
    """
    victim = plant(built, "state/active-index.json.tmp")
    before = (built / ".theurian/state/active-index.json").exists()

    ran = _run("index", "build")

    _assert_refused_cleanly(ran)
    assert victim.read_bytes() == VICTIM
    assert (built / ".theurian/state/active-index.json").exists() is before, (
        "the refusal published a build while reporting that it had not"
    )


@_NEEDS_SYMLINKS
def test_the_in_tree_index_pointer_refusal_names_the_temporary_to_remove(built: Path) -> None:
    """The open's face for the index pointer, with the cure that names the leaf."""
    _plant_inside(built, "state/active-index.json.tmp")

    envelope = _assert_refused_cleanly(_run("index", "build"))

    _assert_names_a_removable_link(envelope, built / ".theurian/state/active-index.json.tmp")


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", [_plant_outside, _plant_inside], ids=["out-of-tree", "in-tree"])
def test_index_build_refuses_a_link_at_the_scan_record_temporary(built: Path, plant: Any) -> None:
    """AC2, the secret-scan record's temporary leaf.

    Refused *before* anything is built, by ``_the_scan_record_can_be_written``, so
    the exit code means what the three plugin documents say it means: nothing was
    published. Reaching this at write time instead would degrade it to a
    ``recordWarning`` beside a build that had already published -- which is why the
    published pointer is read below as well as the victim.
    """
    victim = plant(built, "state/index-secret-scan.json.tmp")
    before = (built / ".theurian/state/active-index.json").exists()

    ran = _run("index", "build")

    _assert_refused_cleanly(ran)
    assert victim.read_bytes() == VICTIM
    assert (built / ".theurian/state/active-index.json").exists() is before, (
        "the refusal published a build while reporting that it had not"
    )


@_NEEDS_SYMLINKS
def test_the_in_tree_scan_record_refusal_names_the_temporary_to_remove(built: Path) -> None:
    """The precondition's own wording, which the containment arm cannot supply.

    An in-tree link is contained, so ``_the_scan_record_can_be_written``'s
    ``lstat`` is what sees it, and the cure it publishes names the leaf. That probe
    decides only *when* the refusal is reported -- what makes the write safe is
    ``O_NOFOLLOW`` inside it, which has no window between deciding and acting.
    """
    _plant_inside(built, "state/index-secret-scan.json.tmp")

    envelope = _assert_refused_cleanly(_run("index", "build"))

    _assert_names_a_removable_link(envelope, built / ".theurian/state/index-secret-scan.json.tmp")


@_NEEDS_SYMLINKS
def test_index_build_refuses_a_link_at_the_scan_record_itself(built: Path) -> None:
    """The published name, not only the temporary, on the in-tree face.

    ``os.replace`` renames the temporary over this path and ``rename(2)`` never
    follows a link, so nothing is written *through* one here -- but the swap would
    silently consume the link and leave the record where the attacker chose to
    have the *next* reader look. Refused with the temporary, in one precondition.

    The out-of-tree face of this same path is already swept by
    ``test_contained_path_envelope.py``'s ``index_secret_scan`` plant; what is new
    here is the contained link, which that sweep's containment key cannot see.
    """
    victim = _plant_inside(built, "state/index-secret-scan.json")

    ran = _run("index", "build")

    envelope = _assert_refused_cleanly(ran)
    _assert_names_a_removable_link(envelope, built / ".theurian/state/index-secret-scan.json")
    assert victim.read_bytes() == VICTIM


# -- AC3: the ingestion manifest, through `ingest` ---------------------------


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", [_plant_outside, _plant_inside], ids=["out-of-tree", "in-tree"])
def test_ingest_refuses_a_link_at_its_cache_manifest(project: Path, plant: Any) -> None:
    """AC3, #394. Measured exit 0 with the manifest landing on the victim before.

    The out-of-tree face is refused by containment before a single source is
    parsed -- ``.theurian/cache/ingestion.json`` was joined from ``knowledge_dir``
    directly, the one route around the chokepoint -- and the in-tree face by the
    open. The two therefore publish different remedies, which is why only the
    shared contract is asserted here and the wording is asserted per face below.
    """
    victim = plant(project, "cache/ingestion.json")

    ran = _run("ingest")

    _assert_refused_cleanly(ran)
    assert victim.read_bytes() == VICTIM


@_NEEDS_SYMLINKS
def test_the_in_tree_ingest_refusal_names_the_link_to_remove(project: Path) -> None:
    """The face whose cure is the link itself, kept apart from the escape's cure.

    An escaping ``.theurian/cache`` can carry its culprit anywhere between the
    derived subdirectory and the leaf, so that refusal names the *directory*
    (``derived_escape_remedy``). This one knows the leaf, because ``O_NOFOLLOW``
    constrains the final component -- and telling this reader to remove
    ``.theurian/cache`` would be more destruction than the fault needs.
    """
    _plant_inside(project, "cache/ingestion.json")

    envelope = _assert_refused_cleanly(_run("ingest"))

    _assert_names_a_removable_link(envelope, project / ".theurian/cache/ingestion.json")


@_NEEDS_SYMLINKS
def test_the_out_of_tree_ingest_refusal_names_the_cache_directory(project: Path) -> None:
    """The containment face, and the remedy the ``cache`` tail was added for.

    ``.theurian/cache`` had no ``ProjectPaths`` helper under it until #394, so no
    containment refusal could ever be published for it and
    ``_REBUILD_AFTER_REMOVING`` carried no entry. With one, a remedy that stopped
    at "remove ``.theurian/cache``" would leave the reader with no way back.
    """
    _plant_outside(project, "cache/ingestion.json")

    envelope = _assert_refused_cleanly(_run("ingest"))

    _assert_names_the_derived_directory(envelope, ".theurian/cache", "theurian ingest")


# -- AC4: the local access token ---------------------------------------------


@_NEEDS_SYMLINKS
def test_the_secret_store_refuses_to_write_a_token_through_a_link(tmp_path: Path) -> None:
    """AC4, #371. ``TOKEN_KEY`` itself, never a literal.

    The key is the constant because the first reproduction of this missed on the
    wrong name: the token is stored under ``mcp-token``, and a test spelling
    ``token`` would plant a link nothing ever opens and report GREEN over an
    unrefused write.

    The plant is *dangling*, which is the shape ``setup`` met: with ``O_CREAT``
    following the link, the open creates the attacker's file with the 0600 this
    store chose -- so ``theurian doctor`` afterwards reported ``satisfied`` about a
    file in somebody else's directory.
    """
    data_dir = tmp_path / "data"
    loot = tmp_path / "attacker" / "loot"
    loot.parent.mkdir(parents=True)
    (data_dir / "auth").mkdir(parents=True)
    (data_dir / "auth" / TOKEN_KEY).symlink_to(loot)

    store = FileSecretStore(data_dir)
    with pytest.raises(SecurityError) as excinfo:
        asyncio.run(store.set(TOKEN_KEY, "s3cret-token-value"))

    assert not loot.exists(), "the token was written into the attacker's path"
    assert (data_dir / "auth" / TOKEN_KEY).is_symlink()
    assert str(data_dir / "auth" / TOKEN_KEY) in excinfo.value.remedy
    assert "theurian auth rotate" in excinfo.value.remedy


@_NEEDS_SYMLINKS
def test_the_secret_store_refuses_to_overwrite_a_file_a_link_names(tmp_path: Path) -> None:
    """The second face: the link names a file that already exists.

    Distinct from the dangling case because the harm is different -- there the
    attacker *receives* the token, here an operator's own file is truncated and
    replaced by it. Both are the same open, and the victim's bytes are what says
    so.
    """
    data_dir = tmp_path / "data"
    victim = tmp_path / "notes.txt"
    victim.write_bytes(VICTIM)
    (data_dir / "auth").mkdir(parents=True)
    (data_dir / "auth" / TOKEN_KEY).symlink_to(victim)

    store = FileSecretStore(data_dir)
    with pytest.raises(SecurityError):
        asyncio.run(store.set(TOKEN_KEY, "s3cret-token-value"))

    assert victim.read_bytes() == VICTIM


@_NEEDS_SYMLINKS
def test_auth_rotate_publishes_the_refusal_as_a_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-2 for the one command that reached the store with no handler above it.

    ``setup`` records a failed step through ``SetupService._apply``'s ``except
    Exception`` and ``daemon start --foreground`` has an ``except TheurianError``
    that publishes ``exc.remedy``; ``auth rotate`` had neither, so the refusal this
    branch adds would have ended a ``--json`` run in a Rich traceback with an empty
    machine channel.
    """
    home = tmp_path / "home"
    home.mkdir()
    data_dir = home / ".theurian"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(tmp_path)
    loot = tmp_path / "loot"
    (data_dir / "auth").mkdir(parents=True)
    (data_dir / "auth" / TOKEN_KEY).symlink_to(loot)

    ran = _run("auth", "rotate")

    envelope = _assert_refused_cleanly(ran, code=1)
    assert str(data_dir / "auth" / TOKEN_KEY) in envelope["remedy"]
    assert not loot.exists()


# -- AC5's other half: the honest run is unchanged ---------------------------


def test_an_unplanted_project_still_applies_builds_and_ingests(project: Path) -> None:
    """The control, and the only reason any refusal above is attributable.

    Every artefact the converted writers produce is read back, and the temporary
    leaves are asserted *gone*: a publisher that stopped renaming, or one that left
    its ``.json.tmp`` behind on the happy path, would satisfy every refusal test in
    this file.
    """
    _must_succeed("migrate", "apply")
    _must_succeed("index", "build")
    _must_succeed("ingest")

    state = project / ".theurian/state"
    assert json.loads((state / "active.json").read_text(encoding="utf-8"))["stateHash"]
    assert json.loads((state / "active-index.json").read_text(encoding="utf-8"))["indexBuildId"]
    assert json.loads((state / "index-secret-scan.json").read_text(encoding="utf-8"))["policy"]
    assert json.loads((project / ".theurian/cache/ingestion.json").read_text(encoding="utf-8")), (
        "the manifest is empty, so `ingest` recorded nothing"
    )
    assert _tmp_leftovers(project) == [], "a temporary survived a successful publish"


@_NEEDS_SYMLINKS
def test_a_refusal_leaves_no_half_written_temporary_behind(project: Path) -> None:
    """The state/lifecycle family: a refused write must not become residue.

    ``O_NOFOLLOW`` refuses at the open, so nothing is created -- but a fix that
    probed and then wrote, or one that unlinked before opening, would leave either
    a stray file or a removed link. Read as a set so a *new* temporary name shows
    up here rather than accumulating unseen.
    """
    _plant_outside(project, "state/active.json.tmp")

    _run("migrate", "apply")

    assert _tmp_leftovers(project) == ["active.json.tmp"], (
        "the refusal created or removed a temporary; only the plant should be here"
    )


def test_the_temporary_helpers_name_the_leaves_the_publishers_use(project: Path) -> None:
    """The plants above are aimed by hand, and this is what keeps them aimed.

    A plant at a path no writer touches is the quietest way for this file to
    assert nothing: every command answers normally, every property passes, and the
    writer it was meant to cover was never reached. So the spellings are written
    out above and compared against production here -- a helper that started
    deriving a different name fails here rather than dragging the plants along.
    """
    paths = ProjectPaths.of(project)

    assert paths.active_pointer_temporary == Path(f"{paths.active_pointer}.tmp")
    assert paths.active_index_pointer_temporary == Path(f"{paths.active_index_pointer}.tmp")
    assert paths.index_secret_scan_temporary == Path(f"{paths.index_secret_scan}.tmp")
    assert paths.ingestion_manifest == project / ".theurian/cache/ingestion.json"
    assert os.fspath(paths.active_pointer_temporary).endswith("state/active.json.tmp")
