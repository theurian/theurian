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
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from migration_fixtures import body_pin
from setup_migrations import state_hash_from_the_loader, unchecked_migrations
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths
from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import probe_token, probe_token_storage
from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app
from theurian.domain.errors import SecurityError
from theurian.domain.setup import StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.secrets.file_store import (
    TOKEN_KEY,
    FileSecretStore,
    InsecureSecretPermissionsError,
    SecretPathIsASymbolicLinkError,
)

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

#: The value the attacker puts in the file their planted link names. Compared
#: rather than merely "the read raised": a read side that unlinked the link and
#: re-minted would also raise nothing, and would have destroyed the evidence.
ATTACKER_TOKEN: Final = "ATTACKER-CHOSEN-TOKEN"  # noqa: S105 - a test fixture, not a credential


def _setup_context(tmp_path: Path, data_dir: Path) -> SetupContext:
    """The context ``probe_token_storage`` needs, in this file's own spelling.

    Duplicated from ``test_probe_storage_claims.py`` rather than imported: that
    file's helper is private to it, and a shared fixture would make the two files
    fail together over a change to either. Only the two fields this probe reads
    carry meaning here.
    """
    return SetupContext(
        home=tmp_path / "home",
        data_dir=data_dir,
        port=7419,
        project_root=None,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable="",
        check_migrations=unchecked_migrations,
        current_state_hash=state_hash_from_the_loader,
    )


@dataclass(frozen=True, slots=True)
class Ran:
    """One command's result, in the shape the assertions here ask about.

    Frozen, like every value in this codebase: an assertion helper that could
    reassign ``exit_code`` on the object it was handed would make a failure
    report describe a run that never happened.
    """

    exit_code: int
    stdout: str
    stderr: str
    escaped: str | None

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


def _assert_the_error_names(envelope: dict[str, Any], *, artifact: str, link: Path) -> None:
    """The ``error`` field says *which* artefact, by its leaf, with no absolute path.

    Round one, adversarial M-1: only ``envelope["error"]`` being truthy was
    asserted anywhere, and four mutations lived in that gap --

    * the whole message replaced by ``"a write was refused"``, the exact string
      :func:`~theurian.cli.commands._symbolic_link_write_refusal`'s own docstring
      calls useless, since the reader is left to guess which file to look at;
    * the message replaced by ``f"{path} could not be written"``, which puts the
      operator's absolute path in the field this module keeps them out of;
    * ``index build`` naming "The ingestion manifest" for the *index pointer's*
      temporary, and again for the *scan record* -- one command sending a reader
      to a different command's file.

    Three assertions, because the mutations fail three different ways: the phrase
    that says which artefact, the leaf that says which file, and the absence that
    keeps the absolute path out. The leaf is asserted rather than the whole path
    for the reason the production docstring gives -- the ``error`` field is what
    gets quoted into a bug report, and the ``remedy`` beside it carries the full
    path because a cure has to be typeable.
    """
    message = envelope["error"]
    assert artifact in message, f"the error does not say which artefact: {message}"
    assert link.name in message, f"the error does not name the leaf: {message}"
    assert str(link) not in message, (
        f"the error carries the operator's absolute path, which belongs in the "
        f"remedy and not here: {message}"
    )
    # Round two, adversarial M-1: the pins above say which *file* the message is
    # about and nothing about what it says HAPPENED, so inverting the sentence to
    # "followed it and replaced" survived the whole suite
    # (`R2-MISLEAD-message-says-it-wrote-through`). A refusal that describes
    # itself as a completed write is worse than no message: it sends an operator
    # to restore a file nothing touched, and it is the product asserting the
    # opposite of its own security behaviour.
    assert "refused" in message, (
        f"the message does not say the write was refused, so it does not "
        f"distinguish a refusal from a write that went through: {message}"
    )
    for claim in ("followed it", "replaced whatever it names.", "was written"):
        assert claim not in message, (
            f"the message claims the write happened ({claim!r}), which is the "
            f"inverse of what this path does: {message}"
        )


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

    link = project / ".theurian/state/active.json.tmp"
    _assert_names_a_removable_link(envelope, link)
    _assert_the_error_names(
        envelope, artifact="The active-state pointer's temporary file", link=link
    )


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
def test_the_apply_refusal_names_the_file_that_actually_refused(
    project: Path, tmp_path: Path
) -> None:
    """Round one, adversarial L-2: the envelope blamed a file that does not exist.

    Two writes run inside ``migrate apply``'s section-B ``try``:
    ``provenance.record_state``, which writes ``<data_dir>/provenance.json.tmp``,
    and ``write_active_state``. The handler named ``active.json.tmp``
    unconditionally, so a symbolic-link loop at the *provenance* temporary
    produced exit 4 with a remedy telling the operator to remove a path that was
    never created -- measured against the real CLI.

    The plant is a **self-referential link**, which is the shape that reaches
    ``ELOOP`` at a write this command does not guard: the provenance write is
    ``Path.write_text``, and a *non-loop* link there is followed at exit 0 with
    the victim outside the data directory overwritten -- measured 2026-09-05, and
    the reason the message below must not claim a refusal. That path is the
    per-user data directory and is outside this class (#523 records it as no
    capability increase); what is fixed is the *reporting*, and this pins all four
    of its claims.
    """
    data_dir = tmp_path / "datadir"
    data_dir.mkdir(parents=True, exist_ok=True)
    loop = data_dir / "provenance.json.tmp"
    loop.unlink(missing_ok=True)
    loop.symlink_to("provenance.json.tmp")

    ran = _run("migrate", "apply")

    envelope = _assert_refused_cleanly(ran)
    assert str(loop) in envelope["remedy"], (
        f"the cure names a file other than the one that refused: {envelope['remedy']}"
    )
    assert "active.json.tmp" not in envelope["remedy"], (
        "the cure still blames the pointer's temporary, which was never created"
    )
    assert not (project / ".theurian/state/active.json.tmp").exists()

    # Round two, H-D and adversarial `R2-L2-fallthrough-artifact-lies`: the
    # *message* was never read here, so collapsing the artefact ternary to the
    # pointer phrase survived. Two clauses, and both were false for this culprit.
    assert loop.name in envelope["error"], (
        f"the message does not name the file that failed: {envelope['error']}"
    )
    assert "active-state pointer" not in envelope["error"], (
        "the message calls a per-user provenance file the active-state pointer, "
        "which is the mutation this assertion exists to kill"
    )
    assert "refused" not in envelope["error"], (
        "the message claims Theurian refused this write. Nothing on this path "
        "refuses: `BuildProvenance._write` is a bare `write_text`, and a non-loop "
        "link there is followed at exit 0 (measured). Claiming a guard that does "
        "not exist is the product asserting something false about its own "
        "security behaviour"
    )
    assert "repository" not in envelope["remedy"], (
        "the cure blames a repository for a link in the per-user data directory, "
        "which no clone can reach"
    )


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

    link = built / ".theurian/state/active-index.json.tmp"
    _assert_names_a_removable_link(envelope, link)
    _assert_the_error_names(envelope, artifact="The index pointer's temporary file", link=link)


@_NEEDS_SYMLINKS
@pytest.mark.parametrize("plant", [_plant_outside, _plant_inside], ids=["out-of-tree", "in-tree"])
def test_index_build_refuses_a_link_at_the_scan_record_temporary(built: Path, plant: Any) -> None:
    """AC2, the secret-scan record's temporary leaf.

    Refused *before* anything is built, by ``_the_scan_record_paths_are_usable``, so
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

    An in-tree link is contained, so ``_the_scan_record_paths_are_usable``'s
    ``lstat`` is what sees it, and the cure it publishes names the leaf. That probe
    decides only *when* the refusal is reported -- what makes the write safe is
    ``O_NOFOLLOW`` inside it, which has no window between deciding and acting.
    """
    _plant_inside(built, "state/index-secret-scan.json.tmp")

    envelope = _assert_refused_cleanly(_run("index", "build"))

    link = built / ".theurian/state/index-secret-scan.json.tmp"
    _assert_names_a_removable_link(envelope, link)
    _assert_the_error_names(envelope, artifact="The secret-scan record's temporary file", link=link)


@_NEEDS_SYMLINKS
def test_a_link_at_the_published_scan_record_is_consumed_rather_than_refused(built: Path) -> None:
    """The published name is *not* write-guarded, and the reason is what writes to it.

    No write opens this path -- only ``os.replace`` names it, and ``rename(2)``
    operates on the link rather than through it: measured standalone, replacing
    over a linked record left the link's target byte-identical and turned the
    record into a regular file. So there is nothing here for ``O_NOFOLLOW`` to
    back on the write side, and the build proceeds.

    It **is** read through a link, by ``_read_record``'s ``read_text`` on both of
    its callers, and that is a recorded decision rather than a gap: both gate on
    the build id before the contents mean anything, so a planted link can make a
    verdict unavailable but not flip one, and a local attacker who can plant it
    can rewrite the record directly for less work. The reasoning lives on
    ``_the_scan_record_paths_are_usable`` (security round two, H-C).

    **This test replaces one that asserted the opposite** (round one, code review
    H-1). The earlier precondition refused on this path with text claiming
    "writing through it would replace whatever it names" -- false of a
    ``rename(2)`` destination -- and blocked ``index build`` at exit 4 with
    nothing built, while the identical plant at ``active-index.json`` was consumed
    at exit 0 by ``_publish``. Two paths written the same way, answered two
    different ways, on a mechanism that was not real.

    What is asserted instead is the behaviour that *is* real, and it is asserted
    in three parts because two of them alone would pass over a build that never
    ran: the command succeeds, the link is gone, and the record is a regular file
    holding this build's own id.
    """
    victim = _plant_inside(built, "state/index-secret-scan.json")
    record = built / ".theurian/state/index-secret-scan.json"

    ran = _run("index", "build")

    assert ran.exit_code == 0, f"the build was refused: {ran.stderr}"
    assert victim.read_bytes() == VICTIM, "the replace wrote through the link"
    assert not record.is_symlink(), "the link survived the replace"
    assert json.loads(record.read_text(encoding="utf-8"))["indexBuildId"], (
        "the record is not this build's own"
    )


@_NEEDS_SYMLINKS
def test_an_escaping_published_scan_record_is_still_refused(built: Path) -> None:
    """Dropping the symlink guard did not drop the containment one.

    The published name keeps its ``_contained`` check, which is the half that
    answers "this path leaves the working tree" -- a question ``rename(2)``'s
    behaviour says nothing about. Held here as well as in
    ``test_contained_path_envelope.py``'s ``index_secret_scan`` plant, because the
    change above is exactly the kind that takes a neighbouring guard with it.
    """
    victim = _plant_outside(built, "state/index-secret-scan.json")

    ran = _run("index", "build")

    _assert_refused_cleanly(ran)
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

    link = project / ".theurian/cache/ingestion.json"
    _assert_names_a_removable_link(envelope, link)
    _assert_the_error_names(envelope, artifact="The ingestion manifest", link=link)


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


# -- The `mkdir` beside that write, which is not a link at all ---------------


def test_ingest_answers_a_regular_file_where_the_cache_directory_belongs(project: Path) -> None:
    """Round one, code review H-2: the `mkdir` sat outside the handler's ``try``.

    ``init`` creates ``.theurian/cache`` as a directory, so this plant has to
    *replace* it -- an earlier attempt wrote into it and measured nothing, which
    is the shape a fixture failure takes when it looks like a passing test.

    Measured on the branch before the fix: exit 1, **zero bytes on stdout**, and a
    Rich traceback naming ``commands.py``'s ``mkdir`` line. ``exist_ok=True``
    suppresses the error only when what is already there is a directory, so a
    regular file raises ``FileExistsError`` one line above the ``except`` that
    owed the caller a document.
    """
    cache = project / ".theurian/cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.write_text("not a directory\n", encoding="utf-8")

    envelope = _assert_refused_cleanly(_run("ingest"))

    assert ".theurian/cache" in envelope["remedy"], (
        f"the cure does not name the directory in the way: {envelope['remedy']}"
    )


@_NEEDS_SYMLINKS
def test_ingest_answers_a_committed_link_to_nowhere_at_the_cache_directory(project: Path) -> None:
    """The same `mkdir`, reached by the artefact a clone actually delivers.

    A regular file at ``.theurian/cache`` is something a person put there; a
    symbolic link naming a path that does not exist is something a repository
    carries, force-added past ADR-0004's ignore, and it is the shape #394 is
    about. Both raise ``FileExistsError`` (errno 17, measured) through the same
    call: ``exist_ok=True`` suppresses ``EEXIST`` only once ``is_dir()`` agrees,
    and it follows the link to answer. Measured before the fix, this produced the
    identical exit 1 with an empty machine channel.
    """
    cache = project / ".theurian/cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.symlink_to(Path("..") / "nowhere-at-all")

    envelope = _assert_refused_cleanly(_run("ingest"))

    assert ".theurian/cache" in envelope["remedy"]


@_NEEDS_SYMLINKS
def test_ingest_still_writes_through_a_contained_link_at_the_cache_directory(project: Path) -> None:
    """The narrowness control: a *contained* directory link is not an escape.

    ``.theurian/cache -> ../elsewhere`` resolves inside the working tree, so
    containment passes it and the manifest lands at the link's target. Nothing
    leaves the tree, so refusing it would be a fix reaching past its own class --
    and without this row, tightening the two refusals above into "any link at
    `.theurian/cache`" would read as green.

    ``O_NOFOLLOW`` does not see this one either: it constrains the manifest's own
    final component, and the link here is a *prefix* directory. That is the bound
    ``no_follow``'s module docstring records, driven rather than asserted. What
    that bound costs when the target is not an empty scratch directory is the
    separate fact-pin below.
    """
    (project / "elsewhere").mkdir()
    cache = project / ".theurian/cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.symlink_to(Path("..") / "elsewhere", target_is_directory=True)

    ran = _run("ingest")

    assert ran.exit_code == 0, f"a contained directory link was refused: {ran.stderr}"
    assert json.loads((project / "elsewhere/ingestion.json").read_text(encoding="utf-8"))


@_NEEDS_SYMLINKS
def test_a_contained_directory_link_still_relocates_the_manifest(project: Path) -> None:
    """The recorded bound, pinned as a **fact** rather than described in prose (#577).

    This test asserts today's behaviour and today's behaviour is a gap: a clone
    carrying ``.theurian/cache -> ../docs`` writes the ingestion manifest onto a
    *tracked* ``docs/ingestion.json``, at exit 0, and nothing refuses it.
    Containment is satisfied -- the target resolves inside the working tree -- and
    ``O_NOFOLLOW`` constrains the manifest's own final component, not the
    directory above it. Measured 2026-09-05 against the real CLI (round one,
    adversarial M-2).

    **It is written to go RED the day the bound is closed**, which is the point of
    pinning a gap rather than a guarantee. Three sentences in the shipped source
    claimed the in-tree-link shape was refused without saying "at the final
    component"; each is now qualified, and each points here. If prefix hardening
    lands (``openat`` against a directory descriptor at every level) this test
    fails, and whoever lands it deletes it along with those qualifiers -- rather
    than discovering that three docstrings had quietly become true and one
    describes a world that no longer exists.

    The victim's prior bytes are asserted gone, not merely that a file exists at
    the target: "the manifest was written somewhere" is satisfied by the honest
    path too, and what makes this a gap is that it landed on content somebody
    authored.
    """
    tracked = project / "docs" / "ingestion.json"
    tracked.parent.mkdir()
    tracked.write_bytes(VICTIM)
    cache = project / ".theurian/cache"
    shutil.rmtree(cache, ignore_errors=True)
    cache.symlink_to(Path("..") / "docs", target_is_directory=True)

    ran = _run("ingest")

    assert ran.exit_code == 0, (
        f"the prefix-link bound may have been closed -- if so, delete this test and "
        f"the qualifiers that point at it (#577): {ran.stderr}"
    )
    assert tracked.read_bytes() != VICTIM, (
        "the tracked file survived, so this test no longer pins the gap it was written for"
    )
    assert json.loads(tracked.read_text(encoding="utf-8")), (
        "the manifest did not land here, so the relocation this pins did not happen"
    )


def test_ingest_answers_a_directory_where_the_manifest_belongs(project: Path) -> None:
    """The third artefact this arm's remedy names, and the one that already worked.

    Pinned beside the two the H-2 fix added because the remedy now names three
    causes in one sentence, and a cure that lists a cause nothing produces is the
    same defect as one that omits a cause that happens.
    """
    manifest = project / ".theurian/cache/ingestion.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.mkdir()

    envelope = _assert_refused_cleanly(_run("ingest"))

    assert "IsADirectoryError" in envelope["error"]
    assert ".theurian/cache/ingestion.json" in envelope["remedy"]


@_NEEDS_SYMLINKS
def test_a_contained_state_link_destroys_a_colliding_name(project: Path) -> None:
    """#577's ``state`` face, pinned as a fact beside the ``cache`` one.

    ``.theurian/state -> ../build`` resolves inside the working tree, so
    containment passes it and ``O_NOFOLLOW`` never sees it -- the link is a prefix
    directory, not the final component of any write. The whole state directory
    relocates into ``build/`` at exit 0.

    **What that costs is a colliding name, and the mechanism matters**: the
    pointer write truncates ``build/active.json.tmp`` and the ``os.replace`` that
    publishes over it then *unlinks* it. Measured 2026-09-05.

    A tracked ``build/active.json`` gives exit 1 instead, and that is an accident
    rather than a guard -- the pointer read fails to parse it and aborts before
    any write. Naming only that case, as an earlier note did, would read as the
    plant being harmless; it is harmless for exactly one filename.

    Written to go RED the day prefix hardening lands, like its ``cache`` sibling.
    """
    build = project / "build"
    build.mkdir()
    colliding = build / "active.json.tmp"
    colliding.write_bytes(VICTIM)
    state = project / ".theurian/state"
    shutil.rmtree(state, ignore_errors=True)
    state.symlink_to(Path("..") / "build", target_is_directory=True)

    ran = _run("migrate", "apply")

    assert ran.exit_code == 0, (
        f"the prefix-link bound may have been closed -- if so, delete this test "
        f"and the qualifiers that point at it (#577): {ran.stderr}"
    )
    assert not colliding.exists(), (
        "the colliding name survived, so this test no longer pins the loss it was written for"
    )
    assert json.loads((build / "active.json").read_text(encoding="utf-8"))["stateHash"], (
        "state did not relocate here, so the relocation this pins did not happen"
    )


# -- AC4: the local access token ---------------------------------------------


def _planted_token_link(tmp_path: Path) -> tuple[Path, Path]:
    """An ``auth/mcp-token`` link to a token file the attacker owns and chose.

    The whole shape, not a fragment of it: ``auth`` at 0700 and the loot at 0600,
    so every mode-keyed probe in the product answers *satisfied* about it. A plant
    with a loose mode would be refused by a check that has nothing to do with the
    link, and the test would pass for the wrong reason.

    Returns ``(data_dir, loot)``.
    """
    data_dir = tmp_path / "data"
    loot = tmp_path / "attacker" / "loot"
    loot.parent.mkdir(parents=True)
    loot.write_text(ATTACKER_TOKEN, encoding="utf-8")
    loot.chmod(0o600)
    (data_dir / "auth").mkdir(parents=True, mode=0o700)
    (data_dir / "auth" / TOKEN_KEY).symlink_to(loot)
    return data_dir, loot


@_NEEDS_SYMLINKS
def test_a_planted_token_link_is_refused_on_the_read_side_too(tmp_path: Path) -> None:
    """Security round one, HIGH-1. Three observables, pinned together.

    The write side refusing is not enough, and closing only one of these leaves
    the credential in play. Measured against this branch's head **before** the
    read side was converted, with the plant this fixture builds:

    1. ``FileSecretStore.get(TOKEN_KEY)`` returned ``'ATTACKER-CHOSEN-TOKEN'``;
    2. ``daemon.runner.ensure_token`` therefore never re-minted -- it re-mints
       only when there is no token -- so the daemon would have served that value
       as its bearer token;
    3. ``probe_token_storage`` reported **satisfied**: ``is_file()`` follows the
       link, the target is 0600, and ``auth`` is 0700, so all three of its
       predicates pass.
    4. ``probe_token`` reported **satisfied** too, and kept doing so after the
       first three were closed (security round two, H-A -- three reviewers
       converged on it). ``is_file()`` and ``read_text()`` both follow, so a link
       to an attacker-owned file of at least ``MIN_TOKEN_LENGTH`` characters
       reads as a healthy token; a shorter one reaches the "too short" arm, which
       is the same read-through wearing a different verdict.

    **Why four and not three.** The population is
    ``git grep -n "TOKEN_KEY" -- packages/theurian-core/src``, filtered to the
    production sites that *access* the file rather than name it: ``set``, ``get``,
    ``probe_token_storage`` and ``probe_token``. Round one closed three of them
    and this docstring said the class was closed -- the exact failure mode the
    paragraph below warns about, committed one round after writing it. Both
    probes are driven here now, in a loop over the pair, so a fifth access site
    joins by being added to that loop rather than by being remembered.

    And the write-side refusal this branch added *pins the plant in place*:
    ``auth rotate`` now declines the link rather than replacing it, so the state
    persists until an operator removes the link by hand. That is why the read
    conversion is part of the same class rather than a follow-up.

    Asserted as one test rather than three, because the finding is the
    conjunction: a fix that made ``get`` raise while ``doctor`` still said
    satisfied would leave an operator with no way to learn what happened.
    """
    data_dir, loot = _planted_token_link(tmp_path)
    store = FileSecretStore(data_dir)

    with pytest.raises(SecretPathIsASymbolicLinkError) as excinfo:
        asyncio.run(store.get(TOKEN_KEY))

    link = data_dir / "auth" / TOKEN_KEY
    assert str(link) in excinfo.value.remedy
    # The fifth converted site, given the same message treatment as the four in
    # the project tree (round two, LOW). It is an exception rather than an
    # envelope -- no CLI command reaches it without its own handler -- so the
    # assertions are made against `str(exc)` instead of `envelope["error"]`.
    assert link.name in str(excinfo.value)
    assert str(link) not in str(excinfo.value), (
        "the message carries the absolute path, which belongs in the remedy"
    )
    assert "refuses" in str(excinfo.value), (
        "the message does not say the access was refused, so it does not "
        "distinguish a refusal from a read that went through"
    )

    from theurian.daemon.runner import ensure_token

    with pytest.raises(SecretPathIsASymbolicLinkError):
        asyncio.run(ensure_token(data_dir))

    context = _setup_context(tmp_path, data_dir)
    for probe in (probe_token, probe_token_storage):
        step = probe(context)
        assert step.status is not StepStatus.SATISFIED, (
            f"doctor's {probe.__name__} reports {step.status.value} about a token file "
            f"that is a link to {loot}, which the attacker wrote"
        )
    assert loot.read_text(encoding="utf-8") == ATTACKER_TOKEN, (
        "the read path rewrote the attacker's file instead of refusing it"
    )


@_NEEDS_SYMLINKS
def test_a_world_readable_target_is_refused_by_the_mode_check_first(tmp_path: Path) -> None:
    """The ordering the ``Raises`` contract now records (round two, security M-5).

    ``is_world_accessible`` runs before the open and follows the link, so a link
    naming a **0644** target produces ``InsecureSecretPermissionsError`` rather
    than the symbolic-link refusal -- and that error reports the *target's* mode as
    though it were the token's.

    Pinned rather than corrected: refusing a secret other accounts can already
    read, whatever else is wrong with the path, is the safer of the two orders.
    What is not safe is the contract being silent about which refusal a given
    plant produces, because a caller branching on the type would meet the other
    one. The token's own name is asserted absent from nothing here -- what is
    asserted is only that the mode check won, which is the fact the docstring
    claims.
    """
    data_dir = tmp_path / "data"
    loose = tmp_path / "attacker" / "loose"
    loose.parent.mkdir(parents=True)
    loose.write_text(ATTACKER_TOKEN, encoding="utf-8")
    loose.chmod(0o644)
    (data_dir / "auth").mkdir(parents=True, mode=0o700)
    (data_dir / "auth" / TOKEN_KEY).symlink_to(loose)

    with pytest.raises(InsecureSecretPermissionsError) as excinfo:
        asyncio.run(FileSecretStore(data_dir).get(TOKEN_KEY))

    assert excinfo.value.mode == 0o644, (
        "the refusal reports a mode other than the target's, so the ordering this "
        "test pins is not the one that ran"
    )
    assert loose.read_text(encoding="utf-8") == ATTACKER_TOKEN


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
