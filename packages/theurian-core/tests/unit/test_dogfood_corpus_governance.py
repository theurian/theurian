"""What this repository asserts about the knowledge corpus it commits (Milestone 7).

Milestone 7 seeded Theurian's own ``.theurian/`` with 26 items -- 24 ADRs and 2
work logs -- through the released ``propose`` path, and committed them. The
adversarial review then ran nine mutations over that corpus: **all nine
survived**. Not one test in the suite read a byte of it, so the corpus was
committed data with no owner. This module is what those mutations have to die
on.

**Why the governance triple is the load-bearing one.** ``sensitivity`` is a
published label, not a serving control: ``docs/architecture/requirements-analysis.md``
records that values are ingested and returned and that no retrieval predicate
reads them, with enforcement deferred to
https://github.com/theurian/theurian/issues/119. So nothing downstream stops an
``internal`` item from being served once it is in the repository -- the boundary
is *whether it is committed at all*. That boundary is currently held by
``.git/info/exclude``, which is machine-local: it exists in one developer's
clone, never in CI, and never in a fresh checkout. A stray ``git add -f`` of a
local-only note (an operator handoff note, a pre-accept draft) therefore has
exactly one place left to go RED, and this is it (FR-K9).

**The population key, so a reader can attack the key and not just the number.**
``git ls-files`` from the repository root, then every path under the root
``.theurian/`` prefix. Three consequences, each deliberate:

- **Tracked only.** An untracked file is invisible here however large the local
  corpus grows. That is the same population rule
  https://github.com/theurian/theurian/issues/262 adopts for the documented-command
  scan, and for the same reason: a developer's local-only knowledge is not the
  repository's, and a suite that reads it is RED on one machine and green on
  every other.
- **The root corpus only.** ``examples/sample-project/.theurian/**`` is excluded
  because it is teaching material governed differently on purpose: both of its
  migrations carry ``sensitivity: internal`` (measured, not assumed -- ``grep
  sensitivity examples/sample-project/.theurian/migrations/*.yaml``). A population
  holding both corpora could not state the rule below at all. The example has its
  own guard in ``test_examples.py``.
- **Every tracked ``*.yaml`` directly under ``.theurian/migrations/``**, which is
  wider than ``is_migration_file_name`` -- the loader's own predicate, which
  requires a ULID-prefixed name. A YAML the loader ignores is still a file this
  repository publishes, so it is governed here rather than skipped.

**Emptiness is a finding, not a pass.** Every rule below is a ``for`` loop, and a
loop over nothing asserts nothing -- so :func:`_corpus_paths` and
:func:`_revisions` *refuse* an empty population rather than return one, and every
rule inherits that refusal. This was measured, not assumed: with one test
standing guard instead, ``git rm -r --cached .theurian`` left 11 of the 13 tests
here green. A corpus that was deleted, moved, or never cherry-picked alongside
this file now takes the whole module RED.

**A floor, recorded as a lower bound rather than an exact count.** 26 is what
6f97770 ships; the dogfood corpus is expected to grow, and every item added is
fully governed by the rules below whether or not anyone updates the number here.
What the bound catches is the direction that is never routine: committed
knowledge disappearing.

**What is out of scope, and why.** A pinned body is compared against the blob at
its own ``sourceAnchor.commitSha`` -- never against the *current* ``docs/`` file.
Drift between a corpus item and the document it was seeded from is a real gap and
it belongs to https://github.com/theurian/theurian/issues/263, which is a CI
concern with a different cadence: a live-drift check goes RED when someone edits
an ADR, which is a normal thing to do, and turning that into a test failure here
would make this module the thing people learn to ignore. Also out of scope: the
*contents* of a body (nothing here scans for secrets) and the prose inside
``.theurian/proposals/*/evidence.json``.

**Not marked ``unit``.** It runs ``git`` in a subprocess, which the ``unit``
marker's own definition excludes. It lives here with the other structural
tests -- ``test_examples.py``, ``test_artifact_integrity_claim.py`` -- that read
the shipped repository rather than construct a fixture, and like them it carries
no marker.
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import pathlib
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final, NoReturn

import pytest
import yaml

from theurian.domain.project import (
    GITIGNORE_BLOCK_END,
    GITIGNORE_BLOCK_START,
    GITIGNORE_ENTRIES,
)

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, the reckoning ``test_examples.py`` uses.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

CORPUS_PREFIX: Final = ".theurian/"
MIGRATIONS_PREFIX: Final = ".theurian/migrations/"
KNOWLEDGE_PREFIX: Final = ".theurian/knowledge/"
PROPOSALS_PREFIX: Final = ".theurian/proposals/"

#: The metadata every committed revision must carry, exactly. Not a subset check
#: and not an enum check -- the published schema already allows ``internal`` and
#: ``draft``, and this is the narrower claim the *repository* makes about what it
#: is willing to publish about itself.
GOVERNED_METADATA: Final[dict[str, str]] = {
    "sensitivity": "public",
    "trustLevel": "reviewed",
    "status": "approved",
}

#: Measured at 6f97770: 26 migrations, 26 bodies, 26 proposal evidence files and
#: 3 ``.gitkeep`` placeholders -- 81 tracked paths under the root ``.theurian/``.
#: A lower bound; see the module docstring for why it is not an equality.
MINIMUM_MIGRATIONS: Final = 26

#: Every shape of file this repository knowingly tracks under ``.theurian/``.
#: Adding a family is a decision (a committed ``config.yaml`` publishes settings;
#: a committed ``state/`` publishes a derived artifact), so it is made here in
#: the open rather than absorbed silently by a rule that stopped covering it.
_FAMILIES: Final = ("gitkeep", "migration", "body", "proposal-evidence")


@dataclass(frozen=True, slots=True)
class Revision:
    """One ``upsertRevision`` operation, as the committed document declares it."""

    migration: str
    item_id: str
    content_file: str
    content_sha256: str
    metadata: dict[str, Any]

    @property
    def anchors(self) -> tuple[dict[str, Any], ...]:
        raw = self.metadata.get("sourceAnchors", [])
        return tuple(raw) if isinstance(raw, list) else ()


#: Every ``git`` this module runs, with the ownership check told about the one
#: repository it is being pointed at.
#:
#: Without it, a suite run by a different uid than the checkout's owner -- a
#: container that mounts the tree and runs as root, a CI step under ``sudo``
#: whose ``SUDO_UID`` heuristic does not apply -- gets ``detected dubious
#: ownership`` and every rule here ERRORs. ``safe.directory`` is honoured from
#: the command scope: git's own documentation calls system, global and command
#: the *protected* scopes, and only refuses the setting from repository config.
#: It grants nothing beyond reading this path; no rule here runs a hook.
_GIT: Final = ("git", "-c", f"safe.directory={REPO_ROOT}")


def _git_text(*arguments: str) -> str:
    """``git`` in the repository root, as text. A non-zero exit raises."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [*_GIT, *arguments],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@functools.cache
def _tracked_paths() -> frozenset[str]:
    """Every path Git tracks, repository-relative, NUL-separated so a name cannot lie.

    ``-z`` matters: without it Git quotes and escapes any path holding a
    non-ASCII byte, and a corpus seeded from documents with CJK titles is exactly
    where such a name appears.
    """
    return frozenset(
        entry for entry in _git_text("ls-files", "-z", "--full-name").split("\0") if entry
    )


@functools.cache
def _corpus_paths() -> tuple[str, ...]:
    """The tracked root corpus, sorted. ``examples/`` cannot reach this prefix.

    Refuses an empty answer here rather than returning one, so that no rule in
    this module can iterate over nothing and report safety. Measured: untracking
    the whole corpus (``git rm -r --cached .theurian``) left 11 of the 13 tests
    green when this was a plain filter, with one test standing guard for twelve.
    """
    paths = tuple(sorted(path for path in _tracked_paths() if path.startswith(CORPUS_PREFIX)))
    assert paths, (
        "git tracks nothing under .theurian/. The committed corpus is gone -- which is a "
        "finding, not a reason for these rules to pass."
    )
    return paths


@functools.cache
def _migration_paths() -> tuple[str, ...]:
    """Tracked ``*.yaml`` directly under ``.theurian/migrations/``."""
    return tuple(path for path in _corpus_paths() if _family(path) == "migration")


def _family(path: str) -> str | None:
    """Which known shape a tracked corpus path has, or ``None`` for a stranger."""
    name = path.rsplit("/", 1)[-1]
    remainder = path.removeprefix(MIGRATIONS_PREFIX)
    if name == ".gitkeep":
        return "gitkeep"
    if path.startswith(MIGRATIONS_PREFIX) and name.endswith(".yaml") and "/" not in remainder:
        return "migration"
    if path.startswith(KNOWLEDGE_PREFIX) and name.endswith(".md"):
        return "body"
    if path.startswith(PROPOSALS_PREFIX) and name == "evidence.json":
        return "proposal-evidence"
    return None


def _document(path: str) -> dict[str, Any]:
    """A migration document, parsed independently of the production loader.

    ``yaml.safe_load`` rather than ``theurian.security.load_yaml_mapping``: this
    module is a guard *over* committed data, so it should not inherit whatever
    the loader currently accepts or coerces. Every field read below is a string
    or a list, none of which the two parsers disagree about.
    """
    loaded = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path} is not a YAML mapping"
    return loaded


@functools.cache
def _revisions() -> tuple[Revision, ...]:
    """Every ``upsertRevision`` the committed corpus declares, in path order."""
    found: list[Revision] = []
    for path in _migration_paths():
        operations = _document(path).get("operations", [])
        assert isinstance(operations, list), f"{path} declares no `operations` list"
        for operation in operations:
            if not isinstance(operation, dict) or operation.get("op") != "upsertRevision":
                continue
            metadata = operation.get("metadata")
            found.append(
                Revision(
                    migration=path,
                    item_id=str(operation.get("itemId", "")),
                    content_file=str(operation.get("contentFile", "")),
                    content_sha256=str(operation.get("contentSha256", "")),
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
    assert found, (
        "no committed migration declares an upsertRevision, so every governance rule "
        "below would inspect nothing. See _corpus_paths for why this refuses rather "
        "than returns empty."
    )
    return tuple(found)


def _body_path(revision: Revision) -> str | None:
    """``contentFile`` resolved against the migrations directory, or ``None``.

    ``None`` means the reference leaves the repository, by climbing above the
    root or by being absolute in the first place -- ``PurePosixPath.__truediv__``
    *discards* the left side when the right is absolute, so ``contentFile:
    /etc/passwd`` would otherwise resolve, and :func:`_body_bytes` would read it.
    Pure path arithmetic throughout, never a filesystem call, so a symlink cannot
    decide the answer.
    """
    joined = PurePosixPath(revision.migration).parent / revision.content_file
    if joined.is_absolute():
        return None

    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def _body_bytes(revision: Revision) -> bytes:
    """The bytes of a pinned body, from the working tree.

    The working tree rather than the index, deliberately: an uncommitted edit to
    a pinned body is then RED *before* it can be committed, which is where the
    author can still fix it. In CI the two are the same bytes.
    """
    relative = _body_path(revision)
    assert relative is not None, f"{revision.migration} points outside the repository"
    body = REPO_ROOT / relative
    assert body.is_file(), f"{revision.migration} points at {relative}, which is not a file"
    return body.read_bytes()


def _matches_managed_pattern(path: str) -> bool:
    """Whether a repository-relative path is one the managed block ignores.

    Two pattern shapes, which is all ``GITIGNORE_ENTRIES`` contains: a directory
    entry carrying a slash, anchored at the repository root the way Git anchors
    it, and a bare ``*.sqlite``-style glob matched against the file name.
    """
    name = path.rsplit("/", 1)[-1]
    return any(
        path.startswith(entry) if entry.endswith("/") else fnmatch.fnmatchcase(name, entry)
        for entry in GITIGNORE_ENTRIES
    )


def _gitignore_lines() -> tuple[str, ...]:
    return tuple((REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())


def _managed_block() -> tuple[str, ...]:
    """The lines between the managed markers, comments and blanks removed."""
    lines = _gitignore_lines()
    assert GITIGNORE_BLOCK_START in lines, "the tracked .gitignore has no managed block start"
    assert GITIGNORE_BLOCK_END in lines, "the tracked .gitignore has no managed block end"
    start = lines.index(GITIGNORE_BLOCK_START)
    end = lines.index(GITIGNORE_BLOCK_END)
    return tuple(
        stripped
        for line in lines[start + 1 : end]
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


# -- The population itself ---------------------------------------------------


def test_the_committed_corpus_is_present_and_has_not_shrunk() -> None:
    """The floor under the population every other rule here iterates over.

    Emptiness is refused in :func:`_corpus_paths` and :func:`_revisions`, so this
    is the narrower claim: not merely that *something* is committed, but that no
    part of what was committed has quietly gone. A corpus can shrink one item at
    a time without any single deletion looking like a decision.
    """
    migrations = _migration_paths()

    assert len(migrations) >= MINIMUM_MIGRATIONS, (
        f"the committed dogfood corpus holds {len(migrations)} migrations, fewer than the "
        f"{MINIMUM_MIGRATIONS} it shipped with. Committed knowledge does not go missing "
        f"routinely: either restore it, or lower this bound in the same change that says why."
    )


def test_every_tracked_corpus_path_belongs_to_a_family_this_module_governs() -> None:
    """A stranger under ``.theurian/`` is a publication nothing below inspects.

    The rules that follow read migrations, bodies and the managed ignore block. A
    tracked file of any other shape -- a stray note at ``.theurian/handoff.md``, a
    ``.yml`` beside the migrations, a committed ``state/`` database -- passes
    every one of them by never being looked at. This is the seam closed.
    """
    strangers = [path for path in _corpus_paths() if _family(path) is None]

    assert not strangers, (
        f"tracked under .theurian/ but of no shape this module governs: {strangers}. "
        f"Known families: {list(_FAMILIES)}. Widen the population key deliberately, or "
        f"untrack the file -- do not leave it published and unchecked."
    )


def test_every_committed_migration_declares_a_revision_the_governance_rules_can_read() -> None:
    """Soundness for every rule below: they read ``upsertRevision`` operations.

    A migration carrying none -- a rename, a hand-written document, an operation
    spelled differently -- would be scanned and found compliant without a single
    field being compared. Measured at 6f97770: 26 migrations, one revision each.
    """
    governed = {revision.migration for revision in _revisions()}
    silent = [path for path in _migration_paths() if path not in governed]

    assert not silent, (
        f"committed migrations declaring no upsertRevision: {silent}. Every governance "
        f"rule in this module reads that operation, so these files are published and "
        f"ungoverned."
    )


# -- Governance: the private/public boundary ---------------------------------


def test_every_committed_revision_is_public_reviewed_and_approved() -> None:
    """The repository-side half of the private/public boundary (FR-K9).

    ``sensitivity`` is a label and not a serving control (#119), so an
    ``internal`` item that reaches this repository is an item that gets served.
    The only barrier left is the commit, and the barrier that guards the commit
    on a developer's own machine -- ``.git/info/exclude`` -- does not exist in
    CI or in a fresh clone. Exact values, not an allowed set: the schema already
    permits ``internal`` and ``draft``, and this is the stricter thing the
    repository promises about its own corpus.
    """
    offenders = [
        (revision.migration, key, revision.metadata.get(key))
        for revision in _revisions()
        for key, required in GOVERNED_METADATA.items()
        if revision.metadata.get(key) != required
    ]

    assert not offenders, (
        f"committed revisions whose governance metadata is not "
        f"{GOVERNED_METADATA}: {offenders}. A local-only note reached the repository, or a "
        f"published item was reclassified without anyone deciding to publish the change."
    )


def test_every_committed_revision_names_a_git_source_anchor() -> None:
    """Provenance is what makes the pin check below able to run at all (FR-K9).

    A revision with no anchor is a corpus item nobody can trace to a source, and
    it would slip past
    :func:`test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit`
    silently -- that rule loops over anchors.
    """
    anchorless = [revision.migration for revision in _revisions() if not revision.anchors]

    assert not anchorless, (
        f"committed revisions with no sourceAnchors: {anchorless}. Nothing then says which "
        f"document this item is a copy of, and the byte-identity rule below skips it."
    )


# -- Pin integrity -----------------------------------------------------------


def test_every_pinned_body_is_a_tracked_file_inside_the_corpus() -> None:
    """A pin pointing at something Git does not ship is a pin that proves nothing.

    Two failure modes in one rule, because they are one claim: a ``contentFile``
    that climbs out of ``.theurian/`` (``../../etc/passwd``), and one naming a
    file that exists on the author's disk but was never committed -- which is
    precisely what an untracked, local-only body looks like from CI.
    """
    unreachable = [
        (revision.migration, revision.content_file, _body_path(revision))
        for revision in _revisions()
        if (resolved := _body_path(revision)) is None
        or not resolved.startswith(CORPUS_PREFIX)
        or resolved not in _tracked_paths()
    ]

    assert not unreachable, (
        f"pins that do not resolve to a tracked file inside .theurian/: {unreachable}"
    )


def test_every_pinned_body_hashes_to_the_content_sha256_its_migration_declares() -> None:
    """The pin, checked rather than trusted (ADR-0006's immutable revisions).

    ``contentSha256`` is what the canonical store verifies a body against, so a
    body edited in place without re-pinning is a revision whose identity has
    silently changed. Nothing else in the suite reads the committed corpus, so
    editing one of these bodies was free.
    """
    hashed = [
        (revision, hashlib.sha256(_body_bytes(revision)).hexdigest()) for revision in _revisions()
    ]
    drifted = [
        (revision.migration, revision.content_sha256, digest)
        for revision, digest in hashed
        if digest != revision.content_sha256
    ]

    assert not drifted, f"bodies whose bytes no longer hash to their declared pin: {drifted}"


def test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit() -> None:
    """Each item claims to be a verbatim copy of a document at a named commit.

    Compared against ``git show <commitSha>:<filePath>`` -- the blob as it was at
    the anchor, which is a fixed object and stays fixed. **Not** against the
    current ``docs/`` file: an ADR is edited in the normal course of work, and
    live drift is https://github.com/theurian/theurian/issues/263's concern, on
    CI's cadence rather than this suite's.

    Skips, loudly and only, when the clone is shallow and therefore does not hold
    the anchor commit -- ``actions/checkout`` defaults to ``fetch-depth: 1``. In
    any complete clone a missing anchor is a failure, because a pin naming a
    commit the repository does not contain is a pin no one can ever check.
    """
    for revision in _revisions():
        body = _body_bytes(revision)
        for anchor in revision.anchors:
            commit, file_path = anchor.get("commitSha", ""), anchor.get("filePath", "")
            shown = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [*_GIT, "show", f"{commit}:{file_path}"],
                cwd=REPO_ROOT,
                capture_output=True,
                check=False,
            )
            if shown.returncode != 0:
                _refuse_missing_anchor(revision, commit, file_path)
            assert shown.stdout == body, (
                f"{revision.migration} claims a verbatim copy of {file_path} at {commit}, "
                f"but the committed body differs ({len(body)} bytes here, "
                f"{len(shown.stdout)} at the anchor)."
            )


def _refuse_missing_anchor(revision: Revision, commit: str, file_path: str) -> NoReturn:
    """A missing anchor object: a skip in a shallow clone, a failure in a full one."""
    if _git_text("rev-parse", "--is-shallow-repository").strip() == "true":
        pytest.skip(
            f"shallow clone: {commit} is absent, so {revision.migration}'s pin to "
            f"{file_path} cannot be read. Give the job `fetch-depth: 0` to run this rule."
        )
    raise AssertionError(
        f"{revision.migration} anchors {file_path} to {commit}, which this complete clone "
        f"does not contain. The pin names a source nobody can verify against."
    )


def test_no_tracked_body_is_unreferenced_by_a_committed_migration() -> None:
    """The other direction of the pin, and the second face of the boundary.

    A ``git add -f`` that publishes only a body -- no migration, no metadata --
    leaves nothing for the governance rule to read, because that rule walks
    migrations. Here the walk starts from what Git ships and asks which migration
    claims it.
    """
    referenced = {resolved for revision in _revisions() if (resolved := _body_path(revision))}
    orphans = [
        path for path in _corpus_paths() if _family(path) == "body" and path not in referenced
    ]

    assert not orphans, (
        f"tracked bodies no committed migration references: {orphans}. A body with no "
        f"migration carries no governance metadata at all, so nothing else here inspects it."
    )


# -- The managed ignore block ------------------------------------------------


def test_the_managed_gitignore_block_appears_exactly_once() -> None:
    """``init`` rewrites what is between the markers; a second pair splits that.

    A duplicated or half-removed block leaves one copy that ``init`` maintains
    and one that nobody does, and the stale copy is the one a reader believes.
    """
    lines = _gitignore_lines()

    assert lines.count(GITIGNORE_BLOCK_START) == 1, (
        f"the tracked .gitignore holds {lines.count(GITIGNORE_BLOCK_START)} start markers"
    )
    assert lines.count(GITIGNORE_BLOCK_END) == 1, (
        f"the tracked .gitignore holds {lines.count(GITIGNORE_BLOCK_END)} end markers"
    )
    assert lines.index(GITIGNORE_BLOCK_START) < lines.index(GITIGNORE_BLOCK_END), (
        "the managed .gitignore block's end marker precedes its start marker"
    )


def test_the_managed_gitignore_block_lists_exactly_the_derived_patterns() -> None:
    """The block this repository committed is the block ``init`` writes (ADR-0004).

    Order included, so the comparison is exact and deterministic rather than a
    set membership that a reordering could satisfy differently on a rerun. A
    missing pattern is how a derived artifact -- an index database, a state
    file -- becomes committable without anyone deciding it should be; removing
    one was a mutation that survived.
    """
    assert _managed_block() == GITIGNORE_ENTRIES, (
        f"the tracked .gitignore's managed block is {_managed_block()}, not the "
        f"{GITIGNORE_ENTRIES} that `theurian init` writes"
    )


def test_nothing_the_managed_block_ignores_is_tracked_under_the_corpus() -> None:
    """The patterns above are worth exactly what is not committed despite them.

    ``git add -f`` overrides an ignore rule without a word, and a tracked
    ``.theurian/state/*.sqlite`` is a derived artifact published as if it were
    source -- the thing ADR-0004 exists to prevent. The rule is read from
    ``GITIGNORE_ENTRIES`` rather than restated, so it cannot drift from the block
    the test above pins.
    """
    tracked_yet_ignored = [path for path in _corpus_paths() if _matches_managed_pattern(path)]

    assert not tracked_yet_ignored, (
        f"derived artifacts tracked despite the managed ignore block: {tracked_yet_ignored}"
    )


def test_no_tracked_corpus_path_is_a_symlink_or_executable() -> None:
    """Every rule here reads a path's bytes; a symlink makes that mean something else.

    A committed symlink under ``.theurian/`` resolves against whatever the reader
    has on disk, so the body a pin is checked against on one machine is not the
    body checked on another -- and it reaches out of the repository by design.
    Git records the distinction in the index mode, which is the only place it
    survives a clone.
    """
    entries = _git_text("ls-files", "-s", "--full-name", "--", CORPUS_PREFIX).splitlines()
    unexpected = [entry for entry in entries if not entry.startswith("100644 ")]

    assert entries, "git tracks nothing under .theurian/; the corpus is gone"
    assert not unexpected, (
        f"tracked corpus paths that are not regular, non-executable files: {unexpected}"
    )
