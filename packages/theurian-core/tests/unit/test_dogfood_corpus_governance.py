"""What this repository asserts about the knowledge corpus it commits (Milestone 7).

Milestone 7 seeded Theurian's own ``.theurian/`` with 26 items -- 24 ADRs and 2
work logs -- through the released ``propose`` path, and committed them. The
adversarial review then ran nine mutations over that corpus: **all nine
survived**. Not one test in the suite read a byte of it, so the corpus was
committed data with no owner. This module is what those mutations have to die
on.

**Why the governance triple is the load-bearing one, and why enforcing
sensitivity did not make it less so.** This paragraph used to rest on
``sensitivity`` being a published label that no retrieval predicate read, with
enforcement deferred to
https://github.com/theurian/theurian/issues/119. That is no longer true: a
deployment declares a ceiling, a build writes no row above it, every retriever
filters on it, and ``knowledge.status`` counts only what the ceiling serves
(ADR-0025). The conclusion is unchanged, and the reason it survives is worth
saying rather than assuming.

A ceiling is **a property of the deployment that serves a corpus, not of the
repository that ships one**. Anyone who clones this repository chooses their own
-- the profile file lives in the operator's data directory precisely so that a
contributor cannot author it -- and the shipped default serves more than
``public``. So an ``internal`` item committed here is served by whichever
deployment reads it, and the boundary this module guards is still *whether it is
committed at all*. That boundary is held by ``.git/info/exclude``, which is
machine-local: it exists in one developer's clone, never in CI, and never in a
fresh checkout. A stray ``git add -f`` of a local-only note (an operator handoff
note, a pre-accept draft) therefore has exactly one place left to go RED, and
this is it (FR-K9).

**The population key, so a reader can attack the key and not just the number.**
Every tracked path under the root ``.theurian/`` prefix. Three consequences,
each deliberate:

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
  the key the loader itself enumerates by: ``load_migrations`` lists the
  directory with ``iterdir()`` and keeps every entry ending ``.yaml``, and
  ``_entry_is_migration_file`` then classifies the *entry* rather than the shape
  of its name. A migration hand-renamed off its ULID prefix is therefore still
  loaded and served (measured 2026-08-22: a committed migration copied to
  ``seed-adr-0001.yaml`` loads, while ``is_migration_file_name`` returns
  ``False`` for that name), so anything the loader applies is governed here.
  ``is_migration_file_name`` does require the prefix, but it is a
  *proposal*-directory predicate -- ``accept`` uses it to pick the migration out
  of a directory that also holds bodies -- and it never runs over
  ``.theurian/migrations/``; reading it as the loader's own filter is what makes
  this population look wider than the loader's, and it is not.

**Three sources answer "what is tracked", in order, and only the first is a
definition.** :func:`_index` asks **git**; failing that it reads the
**manifest** ``tools/mutate.py`` records in a copy it made without a ``.git``;
failing that every rule **skips loudly**. This is the contract
``packages/theurian-core/tests/command_population.py`` already holds with the
same harness, and it is here because the harness broke this module outright:
inside a ``tools/mutate.py`` copy, ``git`` exits 128, ``check=True`` turned that
into a ``CalledProcessError``, and 11 of the 13 rules failed on it (measured:
11 failed, 2 passed) -- which takes the *unmutated control* RED and voids every
verdict in the batch, including the verdicts about this file.

The manifest carries paths and nothing else -- it is ``git ls-files --cached
-z`` output verbatim -- so two things degrade on that path and say so in their
own docstrings: :func:`test_no_tracked_corpus_path_is_a_symlink_or_executable`
reads the mode from the working tree instead of the index, and
:func:`test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit`
cannot run at all and skips with that reason. Everything else reads YAML, JSON
or worktree bytes and is unaffected.

**Emptiness is a finding, not a pass.** Almost every rule below is a ``for``
loop, and a loop over nothing asserts nothing -- so :func:`_corpus_paths`,
:func:`_revisions` and :func:`_evidence` *refuse* an empty population rather
than return one. Measured rather than assumed: with no such refusal, ``git rm -r
--cached .theurian`` left 11 of the original 13 tests green, because **two** of
them -- the floor and the index-mode rule -- were the only ones that read a
count. Two guards for eleven, not one for twelve.

**And the refusal does not reach every rule, which is stated rather than
rounded up.** Run against a tree that cannot be asked what it tracks, 23 of the
28 rules here skip and 5 pass (measured 2026-08-31 at c220733, in a ``.git``-
and ``.mutate-population``-less copy): the two managed-``.gitignore`` rules
read ``.gitignore`` and not the corpus,
:func:`test_the_known_families_are_exactly_what_family_can_return` reads this
module's own source, and two rules added since the count above was first
written -- :func:`test_the_evidence_key_rule_admits_the_optional_key_and_nothing_else`
and :func:`test_the_fold_key_orders_by_the_migration_document_id_not_the_file_name`
-- are driven entirely by literal input and never touch the population either.
Those five assert something real about a tree with no corpus in it; the other
23 would not, which is why they refuse instead. This paragraph moves whenever a
rule is added that does or does not read the population, independently of
whether the corpus itself grows -- a different trigger from the floor and the
tracked-path count below, both of which move only when the corpus does.

**A floor, recorded as a lower bound rather than an exact count.**
:data:`MINIMUM_MIGRATIONS` is checked against
:func:`test_the_committed_corpus_is_present_and_has_not_shrunk`'s
``len(_migration_paths())`` -- a count of tracked migration files, not of
live items, and the two are not the same number: a re-seed is a second
migration, and a second revision, over an item that already exists (see
:data:`GOVERNED_OPERATIONS`), so it grows the migration count without
growing the item count. A branch-relative "this branch ships" figure here
re-binds on every read and every branch, so the count is instead a dated
point measurement rather than a live claim: 30 tracked migrations over 26
live items, measured 2026-09-01 at ``2844ea5`` (the ADR-0013 re-seed plus
the three #199 unit C second-wave re-seeds). The constant itself
stays 26 and stays a lower bound: the dogfood corpus is expected to grow,
and every item or revision added is fully governed by the rules below
whether or not anyone raises the number here. What the bound catches is
the direction that is never routine: committed knowledge disappearing.

**What is out of scope, and why.** A pinned body is compared against the blob at
its own ``sourceAnchor.commitSha`` -- never against the *current* ``docs/`` file.
Drift between a corpus item and the document it was seeded from is a real gap and
it belongs to https://github.com/theurian/theurian/issues/263, which is a CI
concern with a different cadence: a live-drift check goes RED when someone edits
an ADR, which is a normal thing to do, and turning that into a test failure here
would make this module the thing people learn to ignore. Also out of scope: the
*contents* of a body or of an ``evidence.json`` ``reasoning`` string. Nothing
here scans free text for secrets, and the evidence rules below close the
*structural* escapes -- an unknown key, a stray file in a proposal directory, an
anchor naming a document no migration names -- not the prose. SEC-11's scanner
runs at ``theurian propose accept`` and nowhere else (#198), so it says nothing
about a body already committed here: every one of these landed before the
control shipped, and the corpus is not re-scanned.

**Not marked ``unit``.** It runs ``git`` in a subprocess, which the ``unit``
marker's own definition excludes. It lives here with the other structural
tests -- ``test_examples.py``, ``test_artifact_integrity_claim.py`` -- that read
the shipped repository rather than construct a fixture, and like them it carries
no marker.
"""

from __future__ import annotations

import ast
import fnmatch
import functools
import hashlib
import inspect
import json
import os
import pathlib
import re
import stat
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Final

import pytest
import yaml
from jsonschema import Draft202012Validator

from theurian.domain.project import (
    GITIGNORE_BLOCK_END,
    GITIGNORE_BLOCK_START,
    GITIGNORE_ENTRIES,
)

#: ``parents[4]`` is ``.../tests/unit/`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages`` -> repo root, the reckoning ``test_examples.py`` uses.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

SCHEMAS: Final = REPO_ROOT / "schemas"

CORPUS_PREFIX: Final = ".theurian/"
MIGRATIONS_PREFIX: Final = ".theurian/migrations/"
KNOWLEDGE_PREFIX: Final = ".theurian/knowledge/"
PROPOSALS_PREFIX: Final = ".theurian/proposals/"

#: The metadata every committed revision must carry, exactly. Not a subset check
#: and not an enum check -- the published schema already allows ``internal`` and
#: ``draft``, and this is the narrower claim the *repository* makes about what it
#: is willing to publish about itself.
GOVERNED_METADATA: Final[Mapping[str, str]] = MappingProxyType(
    {
        "sensitivity": "public",
        "trustLevel": "reviewed",
        "status": "approved",
    }
)

#: The operations a committed migration is allowed to declare, in order. Pinned
#: as a sequence rather than a set because the corpus is seeded, never edited:
#: 26 migrations, each ``createItem`` then ``upsertRevision`` and nothing else
#: (measured 2026-08-20).
#:
#: **A governed re-seed does not violate that pin.** It is a second migration
#: over the same item -- ``createItem`` (idempotent under FR-K8 against an item
#: that already exists) then ``upsertRevision`` carrying ``expectedRevision`` --
#: not an edit, so the pinned tuple continues to hold
#: (https://github.com/theurian/theurian/issues/416).
#:
#: **This is what stops an appended operation from moving governance behind the
#: rules' backs.** Every governance rule here reads ``upsertRevision.metadata``;
#: a trailing ``changeSensitivity`` op would reclassify the same item to
#: ``internal`` at apply time while ``upsertRevision`` still reads ``public``,
#: and every rule below would report the corpus compliant. It is also what makes
#: :func:`test_every_committed_migration_declares_a_revision_the_governance_rules_can_read`
#: honest: that rule's diagnosis ("published and ungoverned") is only true
#: because no other operation shape is permitted here.
#:
#: **Retiring or reclassifying an item widens this list.** A ``deprecateItem``
#: or ``changeSensitivity`` migration is a legitimate thing to commit, and the
#: change that adds one edits this tuple *and* says in the same commit which
#: rule now covers the new operation's metadata. The floor below carries the
#: same convention for the same reason: a recorded constant is a decision, and
#: moving it is a decision too.
GOVERNED_OPERATIONS: Final = ("createItem", "upsertRevision")

#: The key set ``propose`` writes into ``.theurian/proposals/<id>/evidence.json``,
#: and the whole of it (measured 2026-08-20 over all 26 committed files).
EVIDENCE_KEYS: Final = frozenset(
    {"proposalId", "agentId", "taskId", "model", "reasoning", "sourceAnchors"}
)

#: Keys a committed ``evidence.json`` **may** carry, named one at a time.
#:
#: ``migrationId`` and ``itemId`` are the two fields of this file that Core reads
#: back: together they let ``propose accept`` answer "has this proposal been
#: accepted?" from the migration set -- a migration with that id, operating on
#: that item, is in ``.theurian/migrations/`` or it is not -- rather than by
#: inferring it from which files are left in the directory, which was wrong in
#: both directions (#253). ``itemId`` is the cross-check that stops a forged
#: ``migrationId`` (pointing at another proposal's landed migration) from reading
#: as accepted. Optional and not required, because the 26 original seed
#: proposals predate both fields, and a required key would take the corpus RED
#: for a field the tool did not write when they were drafted. Every proposal
#: drafted since carries both.
#:
#: This is an allowance for *these* keys and not a relaxation of the rule: the
#: escape the rule below closes is an evidence file carrying a field nothing reads
#: and no schema validates, and a set of two named keys is still exact.
OPTIONAL_EVIDENCE_KEYS: Final = frozenset({"migrationId", "itemId"})

#: The keys a ``sourceAnchor`` carries, in both a migration's revision metadata
#: and an ``evidence.json`` (measured 2026-08-20: one shape, 52 anchors).
ANCHOR_KEYS: Final = frozenset({"provider", "sourceUri", "commitSha", "filePath"})

#: A full, unabbreviated object name. An abbreviated one resolves today and
#: becomes ambiguous as the repository grows, which turns a pin into a lookup
#: that can start failing for reasons that have nothing to do with the corpus.
_FULL_OBJECT_NAME: Final = re.compile(r"\A[0-9a-fA-F]{40}\Z")

#: The corpus holds 27 migrations, 27 bodies, 27 ``evidence.json`` files and 3
#: ``.gitkeep`` placeholders -- 84 tracked paths under the root ``.theurian/``
#: (measured 2026-08-31 at 7e7074c). Recorded as content rather than as a
#: branch SHA: a squash merge destroys the branch commit a reader would go
#: looking for.
#:
#: A lower bound; see the module docstring for why it is not an equality.
MINIMUM_MIGRATIONS: Final = 26

#: Every shape of file this repository knowingly tracks under ``.theurian/``.
#: Adding a family is a decision (a committed ``config.yaml`` publishes settings;
#: a committed ``state/`` publishes a derived artifact), so it is made here in
#: the open rather than absorbed silently by a rule that stopped covering it --
#: which is what :func:`test_the_known_families_are_exactly_what_family_can_return`
#: turns from a convention into a check.
_FAMILIES: Final = ("gitkeep", "migration", "body", "proposal-evidence")


def _frozen(value: Any) -> Any:
    """A YAML or JSON value with every mapping and list made unwriteable.

    :func:`_revisions` is cached, so one :class:`Revision` is handed to every
    rule in this module and to every rerun inside a session. A rule that poked
    at ``metadata`` -- or at a nested ``sourceAnchors`` entry, which is where
    the mutable state actually hides -- would change what its neighbours see,
    and the order tests happen to run in would decide the result.

    Recursive rather than a single :class:`~types.MappingProxyType` at the top,
    because a shallow proxy over a dict of lists of dicts protects exactly one
    level and reads as though it protects all of them. Non-container values are
    returned untouched, including any that are *not* the shape a rule expects:
    a ``sourceAnchors: "oops"`` has to stay visible to
    :func:`test_every_source_anchor_is_a_well_formed_git_pin` rather than be
    normalised away here.
    """
    if isinstance(value, dict):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_frozen(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class Revision:
    """One ``upsertRevision`` operation, as the committed document declares it.

    ``migration`` is the tracked *path*; ``migration_document_id`` is the
    document's own ``id`` field. The two are not the same key: the loader
    orders migrations by the parsed ``id`` (Kahn's algorithm, ULID tie-break,
    see :func:`_application_order`), never by file name, and every committed
    file's name happening to embed its own ``id`` is a convention this module
    does not enforce anywhere else.
    """

    migration: str
    item_id: str
    revision_id: str
    content_file: str
    content_sha256: str
    metadata: Mapping[str, Any]
    expected_revision: str | None
    migration_document_id: str

    @property
    def anchors(self) -> tuple[Any, ...]:
        """``sourceAnchors`` as declared -- entries of any shape, not just mappings.

        A non-mapping entry is a finding for
        :func:`test_every_source_anchor_is_a_well_formed_git_pin` to report, so
        it is passed through rather than filtered out here.
        """
        raw = self.metadata.get("sourceAnchors", ())
        return raw if isinstance(raw, tuple) else ()


@dataclass(frozen=True, slots=True)
class Evidence:
    """One committed ``.theurian/proposals/<proposal-id>/evidence.json``."""

    path: str
    directory: str
    document: Mapping[str, Any]

    @property
    def proposal_id(self) -> str:
        declared = self.document.get("proposalId")
        return declared if isinstance(declared, str) else ""

    @property
    def anchors(self) -> tuple[Any, ...]:
        raw = self.document.get("sourceAnchors", ())
        return raw if isinstance(raw, tuple) else ()


@dataclass(frozen=True, slots=True)
class Index:
    """What Git tracks, and which of the three sources said so.

    ``modes`` is ``None`` on the manifest path and only there: the manifest is
    ``git ls-files --cached -z`` output, which carries paths and no index modes.
    A rule that needs a mode either degrades to the working tree and says so, or
    skips.
    """

    paths: frozenset[str]
    modes: Mapping[str, frozenset[str]] | None
    source: str


# -- Asking git, or not ------------------------------------------------------

#: Environment variables that make git answer for a *different* tree, index or
#: configuration than the one it was handed, dropped before it is asked. The
#: same set ``command_population._INHERITED_GIT_OVERRIDES`` and
#: ``tools/mutate.py`` drop, for the same measured reason: ``GIT_INDEX_FILE``
#: binds the index while ``-C``/``cwd`` binds the working tree, so an inherited
#: one makes ``ls-files --cached`` report somebody else's index -- and here that
#: is not a wrong population but a *silently empty* corpus, which
#: :func:`_corpus_paths` would report as "the committed corpus is gone".
#:
#: Nobody exports these by hand. Git exports them to hooks, so a suite run from
#: ``pre-commit`` or ``post-merge`` inherits them.
_INHERITED_GIT_OVERRIDES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)

#: Long enough that no healthy read reaches it -- this repository's ``ls-files``
#: is 11 ms -- and short enough that a git waiting on an index lock or on
#: credentials ends the run instead of hanging the gate.
_GIT_TIMEOUT_SECONDS: Final = 30

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

#: Where ``tools/mutate.py`` records the source checkout's tracked paths when it
#: copies the tree without a ``.git`` -- see ``_POPULATION_NAME`` there, which is
#: the other half of this contract. The bytes are ``git ls-files --cached -z``
#: output verbatim.
_POPULATION_MANIFEST: Final = ".mutate-population"


def _git_environment() -> dict[str, str]:
    return {
        name: value for name, value in os.environ.items() if name not in _INHERITED_GIT_OVERRIDES
    }


def _git_run(*arguments: str) -> subprocess.CompletedProcess[bytes] | None:
    """One read-only git command in the repository root, or ``None`` if it cannot run.

    ``None`` covers the two ways the *question* fails rather than the answer:
    no git on this machine, and a git that did not finish inside
    :data:`_GIT_TIMEOUT_SECONDS`. A non-zero exit is *not* ``None`` -- the
    caller wants to see the stderr, because ``not a git repository`` and a
    ``safe.directory`` refusal need different answers.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
            [*_GIT, *arguments],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            env=_git_environment(),
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_index() -> Index | None:
    """Paths and index modes from ``git``, or ``None`` if git cannot answer here.

    ``-s`` and ``-z`` together: ``-s`` carries the mode, which is the only place
    "this is a symlink" survives a clone, and ``-z`` matters because without it
    Git quotes and escapes any path holding a non-ASCII byte -- and a corpus
    seeded from documents with CJK titles is exactly where such a name appears.

    A path is recorded once, its modes as a set: the index holds up to three
    entries for a path left unmerged by a conflict, and a merge in progress is a
    legitimate local state that must not read as a second corpus.
    """
    completed = _git_run("ls-files", "-s", "-z", "--full-name")
    if completed is None or completed.returncode != 0:
        return None

    modes: dict[str, set[str]] = {}
    for entry in completed.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not entry or "\t" not in entry:
            continue
        prefix, path = entry.split("\t", 1)
        modes.setdefault(path, set()).add(prefix.split(" ", 1)[0])
    if not modes:
        return None
    return Index(
        paths=frozenset(modes),
        modes=MappingProxyType({path: frozenset(seen) for path, seen in modes.items()}),
        source="git",
    )


def _manifest_index() -> Index | None:
    """The population ``tools/mutate.py`` recorded for this copy, if it did.

    ``None`` when the file is absent, which is every ordinary run; when it
    cannot be read; and -- the two that are not obvious -- when it is **empty**
    or **truncated**. ``ls-files -z`` terminates every entry including the last,
    so a manifest that does not end in a NUL was cut short, and adopting it
    would silently drop whatever was being written when the write stopped.

    ``modes`` is ``None``: the manifest is a path list and carries no index
    mode. That is a real loss and it is declared here rather than papered over,
    because a caller that assumed ``100644`` would be asserting nothing.
    """
    manifest = REPO_ROOT / _POPULATION_MANIFEST
    try:
        listing = manifest.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if not listing.endswith("\0"):
        return None
    paths = frozenset(entry for entry in listing.split("\0") if entry)
    if not paths:
        return None
    return Index(paths=paths, modes=None, source="manifest")


@functools.cache
def _index() -> Index | None:
    """What is tracked, from the best source available: git, then the manifest."""
    return _git_index() or _manifest_index()


def _tracked() -> Index:
    """The index, or a skip that names both sources and why each did not answer.

    Loud rather than silent, and a skip rather than a failure: a tree with
    neither a ``.git`` nor a manifest is not a repository making a false claim,
    it is a tree that cannot be asked the question. What must never happen is
    the third thing -- returning an empty population and letting every ``for``
    loop below report safety.
    """
    index = _index()
    if index is None:
        pytest.skip(
            f"nothing here can say what {REPO_ROOT} tracks: `git ls-files` did not answer "
            f"(no git, a timeout, or not a working copy) and no {_POPULATION_MANIFEST} came "
            f"with the tree. Every rule in this module reads the tracked corpus, so they "
            f"would each iterate over nothing and report safety. In a `tools/mutate.py` "
            f"copy this means the manifest was not recorded; in a checkout it means git "
            f"could not be run."
        )
    return index


def _requires_git_objects(what: str) -> Index:
    """The index, or a skip, for a rule that needs blobs and not just path names."""
    index = _tracked()
    if index.source != "git":
        pytest.skip(
            f"{what} needs git objects, and the population came from "
            f"{_POPULATION_MANIFEST} -- a path list with no repository behind it. This is "
            f"a `tools/mutate.py` copy, which is built without a `.git` on purpose."
        )
    return index


# -- The population ----------------------------------------------------------

#: The one *live* record of the corpus's population -- the rest of this
#: module's prose (the module docstring's "30 tracked migrations over 26 live
#: items", the comment above :data:`MINIMUM_MIGRATIONS`, :data:`EVIDENCE_KEYS`
#: and :data:`GOVERNED_OPERATIONS`) narrates the same numbers as dated
#: measurements that intentionally freeze at the commit named beside them --
#: the anchor-counts convention -- and none of those sites is checked against
#: the tree. This mapping is checked, by
#: :func:`test_the_corpus_population_matches_its_recorded_expectation`, which
#: recomputes every key from the same population helpers every rule in this
#: module already reads: :func:`_migration_paths`, :func:`_corpus_paths`,
#: :func:`_evidence_paths`, :func:`_family`, :func:`_revisions`. No new I/O
#: path is opened for it.
#:
#: The stale-count class this closes opened three times across the #440 and
#: #471 review rounds -- the same seven numbers, hand-carried across roughly a
#: dozen prose sites, went stale in both directions of hand maintenance: stale
#: when written, and mis-copied when re-scoped
#: (https://github.com/theurian/theurian/issues/458). A future re-seed or a
#: new item moves at least one of these numbers; the fix from here on is a
#: one-line, deliberate update to this mapping in the same change that moves
#: the corpus, not a sweep across however many places happened to narrate it.
#: Moved on 2026-09-06 by the ADR-0004 re-seed
#: (https://github.com/theurian/theurian/issues/579): one migration, one body and
#: one evidence file joined, so ``tracked_paths`` moved by three and the two
#: item-level counts did not move at all -- the re-seed supersedes a revision of an
#: item the corpus already held, and that item already had more than one.
EXPECTED_CORPUS_POPULATION: Final[Mapping[str, int]] = MappingProxyType(
    {
        "tracked_migrations": 49,
        "bodies": 49,
        "evidence_files": 49,
        "gitkeep_placeholders": 3,
        "tracked_paths": 150,
        "distinct_items": 26,
        "multi_revision_items": 16,
    }
)


@functools.cache
def _corpus_paths() -> tuple[str, ...]:
    """The tracked root corpus, sorted. ``examples/`` cannot reach this prefix.

    Refuses an empty answer here rather than returning one, so that no rule in
    this module can iterate over nothing and report safety. Measured: untracking
    the whole corpus (``git rm -r --cached .theurian``) left 11 of the 13
    original tests green when this was a plain filter -- two of them read a
    count, eleven read a loop.
    """
    paths = tuple(sorted(path for path in _tracked().paths if path.startswith(CORPUS_PREFIX)))
    assert paths, (
        "git tracks nothing under .theurian/. The committed corpus is gone -- which is a "
        "finding, not a reason for these rules to pass."
    )
    return paths


@functools.cache
def _migration_paths() -> tuple[str, ...]:
    """Tracked ``*.yaml`` directly under ``.theurian/migrations/``."""
    return tuple(path for path in _corpus_paths() if _family(path) == "migration")


@functools.cache
def _evidence_paths() -> tuple[str, ...]:
    """Tracked ``evidence.json`` files under ``.theurian/proposals/``."""
    return tuple(path for path in _corpus_paths() if _family(path) == "proposal-evidence")


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
        document = _document(path)
        operations = document.get("operations", [])
        assert isinstance(operations, list), f"{path} declares no `operations` list"
        document_id = document.get("id")
        for operation in operations:
            if not isinstance(operation, dict) or operation.get("op") != "upsertRevision":
                continue
            metadata = operation.get("metadata")
            expected = operation.get("expectedRevision")
            found.append(
                Revision(
                    migration=path,
                    item_id=str(operation.get("itemId", "")),
                    revision_id=str(operation.get("revisionId", "")),
                    content_file=str(operation.get("contentFile", "")),
                    content_sha256=str(operation.get("contentSha256", "")),
                    metadata=_frozen(metadata)
                    if isinstance(metadata, dict)
                    else MappingProxyType({}),
                    expected_revision=expected if isinstance(expected, str) else None,
                    migration_document_id=str(document_id) if isinstance(document_id, str) else "",
                )
            )
    assert found, (
        "no committed migration declares an upsertRevision, so every governance rule "
        "below would inspect nothing. See _corpus_paths for why this refuses rather "
        "than returns empty."
    )
    return tuple(found)


@functools.cache
def _evidence() -> tuple[Evidence, ...]:
    """Every committed ``evidence.json``, parsed, in path order.

    Refuses an empty answer for the same reason :func:`_revisions` does: the
    evidence rules are loops, and the corpus ships one evidence file per
    migration.
    """
    found = tuple(
        Evidence(
            path=path,
            directory=path.removeprefix(PROPOSALS_PREFIX).rsplit("/", 1)[0],
            document=_frozen(json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))),
        )
        for path in _evidence_paths()
    )
    assert found, (
        "the committed corpus holds no proposal evidence, so every evidence rule below "
        "would inspect nothing. See _corpus_paths for why this refuses rather than "
        "returns empty."
    )
    return found


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


def _anchor_faults(anchor: Any) -> tuple[str, ...]:
    """Everything wrong with one ``sourceAnchor``, as sentences, or ``()``.

    Every clause here is what makes ``git show <commitSha>:<filePath>`` mean the
    thing its rule's docstring says it means:

    - **A missing or empty ``commitSha`` compares against the index.** ``git
      show :docs/adr/0001-....md`` -- the empty revision -- is stage 0 of the
      *current index*, which is exactly the live comparison the byte-identity
      rule forswears, and it succeeds, so the shallow-clone skip never fires
      either. That is a pin which passes by naming nothing.
    - **An abbreviated ``commitSha``** resolves today and becomes ambiguous as
      the repository grows.
    - **A ``filePath`` under ``.theurian/``** would let a body be pinned to a
      copy of itself, which is a check that cannot fail.
    - **A ``provider`` other than ``git``** means the anchor is not the thing
      the rule knows how to verify, and would otherwise be verified anyway.
    """
    if not isinstance(anchor, Mapping):
        return (f"is a {type(anchor).__name__}, not a mapping",)

    faults: list[str] = []
    if set(anchor) != ANCHOR_KEYS:
        faults.append(f"declares keys {sorted(anchor)}, not {sorted(ANCHOR_KEYS)}")
    provider = anchor.get("provider")
    if provider != "git":
        faults.append(f"names provider {provider!r}, and only 'git' can be verified here")
    commit = anchor.get("commitSha")
    if not isinstance(commit, str) or not _FULL_OBJECT_NAME.match(commit):
        faults.append(
            f"names commitSha {commit!r}, which is not a full 40-character object name -- "
            f"an empty one makes `git show :<path>` read the current index instead"
        )
    file_path = anchor.get("filePath")
    if not isinstance(file_path, str) or not file_path:
        faults.append(f"names filePath {file_path!r}, so there is no document to compare against")
    elif file_path.startswith(CORPUS_PREFIX):
        faults.append(
            f"names filePath {file_path!r}, inside the corpus itself -- a body pinned to a "
            f"copy of itself is a check that cannot fail"
        )
    return tuple(faults)


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


def test_the_corpus_population_matches_its_recorded_expectation() -> None:
    """The corpus's population is derived here once, not narrated by hand a dozen times.

    Companion to :func:`test_the_committed_corpus_is_present_and_has_not_shrunk`: that rule
    is a floor (``>=``), because the corpus is expected to grow between reads of this
    module's own prose. This rule is the exact count (``==``) that prose kept trying and
    failing to state by hand -- #458, split from #440's re-confirmation round, after the
    stale-count class had produced roughly a dozen hand-fixed prose sites across the #440
    and #471 rounds, wrong in both directions: stale when written, mis-copied when
    re-scoped (the seed-pair figure "2 crossings / holds for 24" was actually 1 / 25 the
    day it was written).

    Every number below is computed from the same population helpers every other rule in
    this module reads -- :func:`_migration_paths`, :func:`_corpus_paths`,
    :func:`_evidence_paths`, :func:`_family`, :func:`_revisions` -- and checked against
    :data:`EXPECTED_CORPUS_POPULATION`, the one mapping this module asks a human to keep
    current. Everywhere else a count appears in this module's prose, it is a dated
    measurement that intentionally freezes (the anchor-counts convention) and is not
    re-checked here; this is the one place a divergence goes RED instead of stale.
    """
    corpus = _corpus_paths()
    revisions_by_item: dict[str, list[Revision]] = {}
    for revision in _revisions():
        revisions_by_item.setdefault(revision.item_id, []).append(revision)

    measured: dict[str, int] = {
        "tracked_migrations": len(_migration_paths()),
        "bodies": sum(1 for path in corpus if _family(path) == "body"),
        "evidence_files": len(_evidence_paths()),
        "gitkeep_placeholders": sum(1 for path in corpus if _family(path) == "gitkeep"),
        "tracked_paths": len(corpus),
        "distinct_items": len(revisions_by_item),
        "multi_revision_items": sum(
            1 for revisions in revisions_by_item.values() if len(revisions) > 1
        ),
    }

    for key, expected in EXPECTED_CORPUS_POPULATION.items():
        assert measured[key] == expected, (
            f"{key} measured {measured[key]}, EXPECTED_CORPUS_POPULATION records {expected} -- "
            f"a re-seed or new item moved the population -- update the EXPECTED mapping in "
            f"the same change, deliberately."
        )


def test_every_tracked_corpus_path_belongs_to_a_family_this_module_governs() -> None:
    """A stranger under ``.theurian/`` is a publication nothing below inspects.

    The rules that follow read migrations, bodies, evidence and the managed
    ignore block. A tracked file of any other shape -- a stray note at
    ``.theurian/handoff.md``, a ``.yml`` beside the migrations, a committed
    ``state/`` database -- passes every one of them by never being looked at.
    This is the seam closed.
    """
    strangers = [path for path in _corpus_paths() if _family(path) is None]

    assert not strangers, (
        f"tracked under .theurian/ but of no shape this module governs: {strangers}. "
        f"Known families: {list(_FAMILIES)}. Widen the population key deliberately, or "
        f"untrack the file -- do not leave it published and unchecked."
    )


def test_the_known_families_are_exactly_what_family_can_return() -> None:
    """A family with no rule behind it is the seam above reopened from the inside.

    :func:`test_every_tracked_corpus_path_belongs_to_a_family_this_module_governs`
    only reports what :func:`_family` calls a stranger. Teaching ``_family`` to
    recognise ``config.yaml`` therefore *silences* that rule for the new shape,
    and nothing else here would read it -- the file becomes governed by having
    been named, which is the opposite of what naming it is for.

    Read out of :func:`_family`'s own source rather than restated, so the check
    cannot drift from the thing it checks: every string a ``return`` statement
    in that function can produce must appear in :data:`_FAMILIES`, and every
    entry of :data:`_FAMILIES` must be reachable from one.
    """
    tree = ast.parse(inspect.getsource(_family).strip())
    returned = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }

    assert returned == set(_FAMILIES), (
        f"`_family` can return {sorted(returned)}, but _FAMILIES records "
        f"{sorted(_FAMILIES)}. A family `_family` recognises and _FAMILIES does not name is "
        f"a shape that stopped being a stranger without any rule starting to read it; the "
        f"other direction is a rule guarding a family nothing can produce."
    )


def test_every_committed_migration_declares_a_revision_the_governance_rules_can_read() -> None:
    """Soundness for every rule below: they read ``upsertRevision`` operations.

    A migration carrying none -- a rename, a hand-written document, an operation
    spelled differently -- would be scanned and found compliant without a single
    field being compared. Measured 2026-08-20: 26 migrations, one revision each.

    The diagnosis this prints ("published and ungoverned") is only honest
    because :data:`GOVERNED_OPERATIONS` refuses every other operation shape. A
    corpus that were allowed to carry a ``deprecateItem``-only migration would
    fail here while being perfectly governed, and the message would be wrong.
    """
    governed = {revision.migration for revision in _revisions()}
    silent = [path for path in _migration_paths() if path not in governed]

    assert not silent, (
        f"committed migrations declaring no upsertRevision: {silent}. Every governance "
        f"rule in this module reads that operation, so these files are published and "
        f"ungoverned."
    )


def test_every_committed_migration_declares_exactly_the_governed_operations() -> None:
    """An appended operation moves governance behind every other rule's back.

    Each rule here reads ``upsertRevision.metadata``. A migration that declares
    ``createItem``, ``upsertRevision`` **and then** ``changeSensitivity`` applies
    an item that is ``internal`` in the store while the metadata these rules read
    still says ``public`` -- so the corpus reports compliant and serves the other
    thing. Measured: appending such an operation leaves every governance rule in
    this module green.

    Pinned as an ordered sequence, not a set: the corpus is seeded and never
    edited, and ``upsertRevision`` before ``createItem`` is not a migration this
    repository writes. See :data:`GOVERNED_OPERATIONS` for what widening this
    list costs the change that widens it.
    """
    declared = {
        path: tuple(
            operation.get("op") if isinstance(operation, dict) else operation
            for operation in _document(path).get("operations", [])
        )
        for path in _migration_paths()
    }
    unexpected = {path: ops for path, ops in declared.items() if ops != GOVERNED_OPERATIONS}

    assert not unexpected, (
        f"committed migrations whose operations are not {list(GOVERNED_OPERATIONS)}: "
        f"{unexpected}. Only `upsertRevision` carries the metadata this module reads, so "
        f"any other operation changes what gets applied without changing what gets checked."
    )


def test_every_committed_migration_matches_the_published_migration_schema() -> None:
    """The corpus has to satisfy the contract the product publishes for it.

    ``test_examples.py`` already holds this for ``examples/sample-project``; the
    root corpus is the one a maintainer edits by hand, and it had no such guard.
    Measured by the adversarial review: four corpora the production loader
    refuses -- a missing ``apiVersion``, a non-ULID ``id``, a naive
    ``createdAt``, an unknown top-level key -- passed every rule in this module.

    Reported together rather than one raise per file, so a hand-edit that breaks
    several migrations names all of them in one run.
    """
    validator = Draft202012Validator(
        json.loads((SCHEMAS / "migrations" / "migration.schema.json").read_text(encoding="utf-8"))
    )
    invalid = [
        f"{path}: {error.json_path} {error.message}"
        for path in _migration_paths()
        for error in sorted(validator.iter_errors(_document(path)), key=str)
    ]

    assert not invalid, (
        f"committed migrations that do not satisfy schemas/migrations/migration.schema.json: "
        f"{invalid}. The product would refuse to apply these, so the repository is "
        f"publishing knowledge it cannot itself ingest."
    )


def test_every_committed_revision_id_is_unique_across_the_corpus() -> None:
    """A revision id is an identity, and two of them make one of the two unreachable.

    ADR-0006 makes a revision immutable and addressable by its id. Duplicating
    one -- a migration copied as a starting point and edited without renumbering,
    which is exactly how these were seeded -- means the second apply either
    collides or silently wins, and a reader who follows the id gets whichever
    the store kept.
    """
    seen: dict[str, list[str]] = {}
    for revision in _revisions():
        seen.setdefault(revision.revision_id, []).append(revision.migration)
    duplicated = {identifier: paths for identifier, paths in seen.items() if len(paths) > 1}

    assert not duplicated, (
        f"revision ids declared by more than one committed migration: {duplicated}. A "
        f"revision id is an identity; two migrations claiming one means a reader following "
        f"it reaches whichever the store kept."
    )


def _revisions_in_application_order() -> tuple[Revision, ...]:
    """:func:`_revisions`, reordered to the loader's real application order.

    **Corrected from a false equivalence.** This module first folded by
    :func:`_migration_paths`'s file-name sort, on the claim that it was "the
    same order ``load_migrations`` applies in". It is not.
    :meth:`~theurian.domain.migration.MigrationSet._topological_order` runs
    Kahn's algorithm over ``dependsOn``, with each round's ready set broken by
    the migration document's own ``id`` -- never by file name -- and the
    orchestrator reproduced the miss on this branch: renaming the re-seed
    migration's inner ``id`` to sort before the seed's, file name left
    untouched, left every rule in this module green while ``migrate apply``
    would apply the re-seed first and refuse it (its ``expectedRevision``
    would then name a revision that does not exist yet).

    No committed migration declares ``dependsOn`` today (checked below, and
    skipped loudly rather than assumed), so every migration is "ready" in
    Kahn's first and only round and the whole ordering collapses to one sort
    key: the document's own ``id``, ascending. That every committed file's
    *name* happens to embed the same ``id`` is a convention this module does
    not enforce anywhere -- it is not what the loader reads.

    A stable sort over :func:`_revisions`' existing sequence is sufficient
    to derive that order without reproducing the loader: :func:`_revisions`
    already emits one migration's operations contiguously before the next
    (it walks :func:`_migration_paths` outer, ``operations`` inner), so
    sorting by ``migration_document_id`` regroups those blocks by the correct
    key while stability leaves each migration's own operations in their
    original, correct, document order. The sort itself is
    :func:`_sorted_by_application_order`, pulled out so it can be pinned
    directly against a hand-built clash between the two keys
    (ADV-RC MEDIUM-2) rather than only through the committed corpus, where
    every file name happens to embed its own id and the two keys never
    disagree.
    """
    for path in _migration_paths():
        depends_on = _document(path).get("dependsOn")
        if depends_on:
            pytest.skip(
                f"{path} declares dependsOn ({depends_on!r}). Reproducing the loader's "
                f"Kahn ordering under a real dependency graph is out of this rule's scope; "
                f"it only re-derives the id-tie-break case the corpus has exercised so far "
                f"(zero dependsOn declarations). Widen this helper before trusting the "
                f"chain rule again once one is committed."
            )
    return _sorted_by_application_order(_revisions())


def _sorted_by_application_order(revisions: Iterable[Revision]) -> tuple[Revision, ...]:
    """The pure fold key: a stable sort on ``migration_document_id``, never on
    ``migration`` (the tracked file path).

    Split out of :func:`_revisions_in_application_order` so the key itself is
    directly testable against hand-built input, independent of the real
    corpus's ``dependsOn`` check and its ``_revisions()`` read -- on the
    committed corpus the two keys never disagree (every file name embeds its
    own id), so a test that only ever exercises this through the corpus
    cannot tell a correct fold from one that quietly reverted to the file
    path, which is exactly what happened once (see the module docstring's
    "Corrected from a false equivalence").
    """
    return tuple(sorted(revisions, key=lambda revision: revision.migration_document_id))


def test_the_fold_key_orders_by_the_migration_document_id_not_the_file_name() -> None:
    """The corrected fold key (cdef404), pinned against a clash the real corpus cannot pose.

    ADV-RC MEDIUM-2: on the committed corpus every migration's file name
    embeds its own inner id, so reverting :func:`_sorted_by_application_order`
    back to ``key=lambda revision: revision.migration`` -- the mistake this
    module shipped once -- survives the whole suite; the two keys never
    disagree there. Smallest honest pin: two hand-built :class:`Revision`
    objects whose file-name order is the *opposite* of their
    ``migration_document_id`` order, fed straight into the pure sort rather
    than through the real corpus, its ``dependsOn`` check or its
    ``_migration_paths()`` population.
    """
    sorts_last_by_name_first_by_id = Revision(
        migration="zzz-sorts-last-by-file-name.yaml",
        item_id="test.fold-key-item",
        revision_id="rev-applied-first",
        content_file="a.md",
        content_sha256="a" * 64,
        metadata=MappingProxyType({}),
        expected_revision=None,
        migration_document_id="01-sorts-first-by-id",
    )
    sorts_first_by_name_second_by_id = Revision(
        migration="aaa-sorts-first-by-file-name.yaml",
        item_id="test.fold-key-item",
        revision_id="rev-applied-second",
        content_file="a.md",
        content_sha256="a" * 64,
        metadata=MappingProxyType({}),
        expected_revision="rev-applied-first",
        migration_document_id="02-sorts-second-by-id",
    )

    ordered = _sorted_by_application_order(
        (sorts_first_by_name_second_by_id, sorts_last_by_name_first_by_id)
    )

    assert [revision.revision_id for revision in ordered] == [
        "rev-applied-first",
        "rev-applied-second",
    ], (
        f"sorted by application order: {[r.revision_id for r in ordered]}. Folding by "
        f"revision.migration (file name) would give ['rev-applied-second', "
        f"'rev-applied-first'] instead, because 'aaa-...' sorts before 'zzz-...'; the "
        f"corrected key sorts by migration_document_id, which orders '01-...' before "
        f"'02-...' regardless of file name."
    )


def test_every_expected_revision_names_the_chain_the_migrations_construct() -> None:
    """``expectedRevision`` is the corpus's first optimistic-concurrency pin, and
    nothing static held it before this rule.

    Reproduced by the orchestrator (code-review MEDIUM): mutate a committed
    migration's ``expectedRevision`` to a well-formed ULID no revision holds,
    and the whole static surface stays green -- this module (32 tests, before
    this rule existed) and ``theurian migrate validate`` both pass, because
    neither reconstructs the chain the field claims to extend; the schema only
    checks the *shape* of the value (a well-formed ULID, or ``null``), never
    what it names. Only ``theurian migrate apply`` refuses, with a revision
    conflict, on whichever machine applies the corpus next -- the run where a
    wrong pin is most expensive, same reasoning as
    :func:`test_every_committed_revision_id_is_unique_across_the_corpus` just
    above.

    Application order is :func:`_revisions_in_application_order`'s -- the
    loader's own Kahn-ordered sequence, folded by the migration document's
    ``id``, not by file name; see that function's docstring for why the two
    are not the same key and for the adversarial finding (ADV MEDIUM) that
    caught this rule folding by the wrong one. Folding ``upsertRevision`` by
    ``itemId`` in that order, "current" is last-upsert-wins: whichever
    revision the most recent prior ``upsertRevision`` for an item declared is
    what the *next* one for that item has to name.

    Two branches, and they are exhaustive over this corpus: a revision naming
    ``expectedRevision`` has to match what the fold above left current for its
    item, and one naming none has to be that item's *first* ``upsertRevision``
    -- optimistic concurrency has nothing to check against before an item holds
    a revision at all, which is why ``expectedRevision`` is absent from all 26
    seed migrations by design. The published schema also allows a third
    absence -- FR-K8's idempotent re-run of an operation whose own
    ``revisionId`` the item already holds -- but that requires two migrations
    naming the same ``revisionId``, which
    :func:`test_every_committed_revision_id_is_unique_across_the_corpus` above
    already forbids across this corpus, so it cannot arise while walking the
    committed migrations once from empty.

    This is the static half. The applicability test in
    ``tests/integration/test_root_corpus_applies.py`` catches the same face
    dynamically -- the real loader and engine, not a reconstruction of them --
    so a defect that escapes one of these mechanisms still meets the other.
    """
    current: dict[str, str] = {}
    wrong_pin: list[str] = []
    not_first: list[str] = []
    for revision in _revisions_in_application_order():
        preceding = current.get(revision.item_id)
        if revision.expected_revision is None:
            if preceding is not None:
                not_first.append(
                    f"{revision.migration}: upsertRevision {revision.revision_id} on "
                    f"{revision.item_id} carries no expectedRevision, but the preceding "
                    f"migrations already left {preceding} current for that item"
                )
        elif revision.expected_revision != preceding:
            wrong_pin.append(
                f"{revision.migration}: upsertRevision {revision.revision_id} on "
                f"{revision.item_id} names expectedRevision {revision.expected_revision!r}, "
                f"but the preceding migrations left {preceding!r} current for that item"
            )
        current[revision.item_id] = revision.revision_id

    assert not wrong_pin, (
        f"upsertRevision operations whose expectedRevision does not name the revision the "
        f"preceding migrations left current for that item: {wrong_pin}. `migrate apply` "
        f"refuses every one of these with a revision conflict; nothing static did before "
        f"this rule."
    )
    assert not not_first, (
        f"upsertRevision operations with no expectedRevision that are not their item's "
        f"first revision: {not_first}. Optimistic concurrency has nothing to check against "
        f"on a first revision, but a later one silently skipping the field means "
        f"`migrate apply` accepts whatever happens to be current with no confirmation at "
        f"all."
    )


def test_no_two_committed_migrations_pin_the_same_body() -> None:
    """Two revisions over one file make the sha256 rule check one thing twice.

    Every pin rule below is keyed on the body a migration names. If two
    migrations name the same ``contentFile``, one body satisfies both -- so an
    item can be published with a body that was written for a different item, and
    the hash check agrees, because it is comparing the file against whichever
    declaration was copied along with it.
    """
    seen: dict[str, list[str]] = {}
    for revision in _revisions():
        seen.setdefault(_body_path(revision) or revision.content_file, []).append(
            revision.migration
        )
    shared = {body: paths for body, paths in seen.items() if len(paths) > 1}

    assert not shared, (
        f"bodies pinned by more than one committed migration: {shared}. One body cannot "
        f"carry two revisions' governance metadata, and the sha256 rule cannot tell which "
        f"of them it just confirmed."
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
        f"{dict(GOVERNED_METADATA)}: {offenders}. A local-only note reached the repository, "
        f"or a published item was reclassified without anyone deciding to publish the change."
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


def test_every_source_anchor_is_a_well_formed_git_pin() -> None:
    """A malformed anchor is a pin that passes by naming nothing.

    The one that is not obvious, and the one the adversarial review measured: an
    **empty or missing ``commitSha``** turns ``git show <commitSha>:<filePath>``
    into ``git show :<filePath>``, which is git's syntax for *stage 0 of the
    current index*. The byte-identity rule then compares the committed body
    against the live file it explicitly refuses to compare against, succeeds,
    and never reaches the shallow-clone skip. The anchor names no commit at all
    and the corpus reports verified.

    Checked here on its own, and checked again inside the byte-identity rule
    before it runs ``git cat-file``, so the malformed case is reported as what
    it is rather than as a byte mismatch.
    """
    faults = [
        f"{revision.migration} anchor #{position}: {fault}"
        for revision in _revisions()
        for position, anchor in enumerate(revision.anchors)
        for fault in _anchor_faults(anchor)
    ]

    assert not faults, (
        f"source anchors that cannot be verified as written: {faults}. Each one is a "
        f"provenance claim that reads as checked and is not."
    )


# -- Pin integrity -----------------------------------------------------------


def test_every_pinned_body_is_a_tracked_file_inside_the_corpus() -> None:
    """A pin pointing at something Git does not ship is a pin that proves nothing.

    Two failure modes in one rule, because they are one claim: a ``contentFile``
    that climbs out of ``.theurian/`` (``../../etc/passwd``), and one naming a
    file that exists on the author's disk but was never committed -- which is
    precisely what an untracked, local-only body looks like from CI.
    """
    tracked = _tracked().paths
    unreachable = [
        (revision.migration, revision.content_file, _body_path(revision))
        for revision in _revisions()
        if (resolved := _body_path(revision)) is None
        or not resolved.startswith(CORPUS_PREFIX)
        or resolved not in tracked
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

    The failure names the **body** file, not only the migration: the migration is
    where the declaration lives and the body is the file somebody edited, and a
    report that names only the first sends the reader to the wrong file.
    """
    drifted = [
        (revision.migration, _body_path(revision), revision.content_sha256, digest)
        for revision in _revisions()
        if (digest := hashlib.sha256(_body_bytes(revision)).hexdigest()) != revision.content_sha256
    ]

    assert not drifted, (
        f"bodies whose bytes no longer hash to their declared pin "
        f"(migration, body, declared, actual): {drifted}. The body is the file that was "
        f"edited; re-pin it in its migration or restore the bytes."
    )


def test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit() -> None:
    """Each item claims to be a verbatim copy of a document at a named commit.

    Compared against ``git cat-file blob <commitSha>:<filePath>`` -- the blob as
    it was at the anchor, which is a fixed object and stays fixed. **Not**
    against the current ``docs/`` file: an ADR is edited in the normal course of
    work, and live drift is https://github.com/theurian/theurian/issues/263's
    concern, on CI's cadence rather than this suite's. ``cat-file blob`` rather
    than ``show`` so that an anchor naming a *directory* fails as a bad pin
    rather than as a mysterious byte mismatch.

    **Skips per revision, not for the whole rule.** A shallow clone that is
    missing one anchor commit used to abandon the other twenty-five, which is
    the wrong trade in the environment it was written for: ``actions/checkout``
    defaults to ``fetch-depth: 1``, so the first unreachable anchor hid every
    remaining comparison. Now every revision is compared, the unreadable ones
    are collected, and the rule reports the mismatches first and the skip only
    if there was nothing to report.

    In any complete clone a missing anchor is a failure, and the message says
    **which** kind: a commit this repository does not contain, or a path that
    does not exist at a commit it does.

    Needs git objects, so it is the one rule that cannot run on the
    ``.mutate-population`` path -- a ``tools/mutate.py`` copy has no repository
    to read a blob from, and there is nothing a working-tree read could
    substitute: the whole claim is about a blob that is *not* in the working
    tree.
    """
    _requires_git_objects("comparing a body against the blob at its anchor commit")

    mismatched: list[str] = []
    unreadable: list[str] = []
    for revision in _revisions():
        body = _body_bytes(revision)
        for anchor in revision.anchors:
            faults = _anchor_faults(anchor)
            assert not faults, (
                f"{revision.migration} carries an anchor this rule cannot verify as written: "
                f"{faults}. Fix the anchor; comparing against it would assert nothing."
            )
            commit, file_path = anchor["commitSha"], anchor["filePath"]
            shown = _git_run("cat-file", "blob", f"{commit}:{file_path}")
            if shown is None or shown.returncode != 0:
                unreadable.append(_unreadable_anchor(revision, commit, file_path))
                continue
            if shown.stdout != body:
                mismatched.append(
                    f"{revision.migration} pins {_body_path(revision)} as a verbatim copy of "
                    f"{file_path} at {commit}, but the committed body differs "
                    f"({len(body)} bytes here, {len(shown.stdout)} at the anchor)"
                )

    assert not mismatched, f"bodies that are not verbatim copies of their anchors: {mismatched}"
    if unreadable:
        pytest.skip(
            f"{len(unreadable)} anchor(s) could not be read, so those comparisons did not "
            f"run: {unreadable}"
        )


def _unreadable_anchor(revision: Revision, commit: str, file_path: str) -> str:
    """Why one anchor could not be read: a shallow clone, a missing commit, or a missing path.

    The three are different findings and used to arrive as one message. A
    shallow clone is the environment's fault (``fetch-depth: 1``); a commit this
    repository does not contain is a pin nobody can ever check; a path absent at
    a commit that *is* present is a pin naming a document that did not exist
    yet, which is a corpus defect and not a clone depth problem.
    """
    shallow = _git_run("rev-parse", "--is-shallow-repository")
    if shallow is not None and shallow.stdout.decode("utf-8", "replace").strip() == "true":
        return (
            f"{revision.migration}: shallow clone, so {commit} is absent and its pin to "
            f"{file_path} cannot be read. Give the job `fetch-depth: 0` to run this rule"
        )
    present = _git_run("cat-file", "-e", f"{commit}^{{commit}}")
    if present is None or present.returncode != 0:
        raise AssertionError(
            f"{revision.migration} anchors {file_path} to {commit}, which this complete "
            f"clone does not contain. The pin names a source nobody can verify against."
        )
    raise AssertionError(
        f"{revision.migration} anchors {file_path} to {commit}. That commit is present, "
        f"and it holds no blob at that path -- the pin names a document that did not exist "
        f"there, so it has never been compared against anything."
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


# -- The families no rule used to read ---------------------------------------


def test_every_tracked_gitkeep_is_empty() -> None:
    """``.gitkeep`` was an accepted family with no rule behind it.

    :func:`_family` calls any file named ``.gitkeep`` a known shape, anywhere
    under ``.theurian/``, and no rule read one. So a note written to
    ``.theurian/knowledge/architecture/.gitkeep`` -- or to a directory invented
    for the purpose -- was a *fully governed-looking* publication of arbitrary
    text: the family rule passed it, the body rules never saw it (it is not
    ``*.md``), and the work log's claim that "a stray ``git add -f`` goes RED in
    CI" was false for it.

    A placeholder's whole job is to exist, so zero bytes is the entire contract
    and anything else is content. The size is reported, never the bytes: a
    failure message that printed them would republish whatever was hidden there
    into CI logs.
    """
    carrying = [
        (path, len(payload))
        for path in _corpus_paths()
        if _family(path) == "gitkeep" and (payload := (REPO_ROOT / path).read_bytes())
    ]

    assert not carrying, (
        f"tracked .gitkeep files that are not empty (path, bytes): {carrying}. A placeholder "
        f"exists to hold a directory open; bytes inside one are content that no rule in this "
        f"module reads and no reviewer looks at."
    )


def test_every_tracked_proposal_path_is_an_evidence_file_in_its_own_directory() -> None:
    """The proposal tree's shape, so nothing can be parked beside the evidence.

    ``propose`` writes ``.theurian/proposals/<proposal-id>/`` holding a
    migration, a body and ``evidence.json``, and ``propose accept`` moves the
    first two out. What is committed is therefore the residue: one
    ``evidence.json`` per directory, plus the placeholder that holds
    ``proposals/`` itself open.

    Without this, a second file in a proposal directory is governed by nothing:
    ``.theurian/proposals/<id>/.gitkeep`` is an accepted family (now empty, by
    the rule above) and ``.theurian/proposals/<id>/notes.md`` is a stranger the
    family rule catches -- but ``.theurian/proposals/<id>/evidence.json`` in a
    directory that is *not* a proposal id would be neither.
    """
    expected = {f"{PROPOSALS_PREFIX}.gitkeep"}
    misplaced = [
        path
        for path in _corpus_paths()
        if path.startswith(PROPOSALS_PREFIX)
        and path not in expected
        and not re.fullmatch(r"[^/]+/evidence\.json", path.removeprefix(PROPOSALS_PREFIX))
    ]
    misnamed = [
        evidence.path
        for evidence in _evidence()
        if evidence.directory != evidence.proposal_id or "/" in evidence.directory
    ]

    assert not misplaced, (
        f"tracked under .theurian/proposals/ but not `<proposal-id>/evidence.json`: "
        f"{misplaced}. Only the evidence file survives `propose accept`; anything else there "
        f"is a publication with no migration and no governance metadata."
    )
    assert not misnamed, (
        f"evidence files whose directory is not their own proposalId: {misnamed}. The "
        f"directory name is the only index into the proposal, so a disagreement makes the "
        f"evidence unreachable from the id it claims."
    )


def test_every_committed_evidence_file_declares_exactly_the_evidence_keys() -> None:
    """``evidence.json`` was the second accepted family no rule read.

    The adversarial review's shape: an ``evidence.json`` carrying an extra key
    -- a ``notes`` or a ``handoff`` field holding embargoed reasoning -- was
    committed, passed the family rule, and was read by nothing else in this
    module. Exact keys rather than a required subset, because the escape is an
    addition and a subset check is blind to exactly that.

    What this does **not** close, said plainly: the *values*. ``reasoning`` is
    free text and ``sourceAnchors`` is checked for shape below, not for whether
    the prose inside is publishable. Content scanning is #198's, and the module
    docstring says so.

    Exactness survives :data:`OPTIONAL_EVIDENCE_KEYS`. A key is admitted here by
    being named in that set, one at a time and with the reason recorded beside
    it; what stays refused is the unnamed key, which is the whole of the escape.
    """
    unexpected = [
        (evidence.path, _evidence_key_difference(evidence.document))
        for evidence in _evidence()
        if _evidence_key_difference(evidence.document)
    ]

    assert not unexpected, (
        f"evidence files whose keys are not {sorted(EVIDENCE_KEYS)} plus at most "
        f"{sorted(OPTIONAL_EVIDENCE_KEYS)} (path, difference): {unexpected}. A key "
        f"`propose` does not write is a field this module does not read and no schema "
        f"validates; a missing required one is provenance the corpus does not carry."
    )


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (EVIDENCE_KEYS, []),
        (EVIDENCE_KEYS | {"migrationId"}, []),
        (EVIDENCE_KEYS | {"migrationId", "itemId"}, []),
        (EVIDENCE_KEYS | {"itemId"}, []),
        (EVIDENCE_KEYS | {"notes"}, ["notes"]),
        (EVIDENCE_KEYS | {"migrationId", "handoff"}, ["handoff"]),
        (EVIDENCE_KEYS | {"itemId", "handoff"}, ["handoff"]),
        (EVIDENCE_KEYS - {"reasoning"}, ["reasoning"]),
    ],
)
def test_the_evidence_key_rule_admits_the_optional_key_and_nothing_else(
    keys: frozenset[str], expected: list[str]
) -> None:
    """The rule above, driven by input rather than by the corpus it reads.

    The 26 original seed files predate ``migrationId`` and ``itemId``; the 27th,
    re-seeded through ``propose``/``accept`` in
    https://github.com/theurian/theurian/issues/416, carries both -- the first
    real exercise of the *admitting* half of the allowance. What landing more
    proposals never exercises is the *refusal* half: `propose` does not write a
    stray key like ``notes`` or ``handoff``, so an allowance wrongly widened to
    admit any key would stay green however large the corpus grows. These eight
    cases are the ones the allowance has to separate.
    """
    assert _evidence_key_difference(dict.fromkeys(keys, "value")) == expected


def _evidence_key_difference(document: Mapping[str, object]) -> list[str]:
    """Keys this evidence file has and should not, or lacks and should have.

    Sorted, and symmetric on purpose: an extra key is a publication nothing
    validates, a missing one is provenance the corpus does not carry, and a rule
    that names only the first reports the second as an empty list.
    """
    return sorted((set(document) - OPTIONAL_EVIDENCE_KEYS) ^ EVIDENCE_KEYS)


def test_the_committed_corpus_holds_one_evidence_file_per_migration() -> None:
    """Provenance for every item, and no proposal directory left over.

    The corpus is one proposal per migration by construction: 27 and 27 (dated
    2026-08-31). That is no longer the same claim as one item per proposal: the
    ADR-0013 re-seed (https://github.com/theurian/theurian/issues/416) landed a
    27th proposal and a 27th migration whose ``upsertRevision`` names an
    *existing* item (``architecture.ai-writes-produce-proposals``) rather than a
    new one, so an item can hold more than one revision while this file still
    holds exactly one evidence record per migration. The proposal id is *not*
    derivable from the migration id -- the seed generated the first 26
    monotonically, and index-pairing the 26 seed proposal ids against the 26
    seed migration ids, sorted ascending (identical whether the migration is
    keyed by its parsed ``id`` or by its filename's ULID prefix -- measured
    2026-08-31, the two never disagree across this corpus), only **1** of the
    26 pairs crosses a millisecond boundary; ``proposalId + 1 ==
    migration.id`` holds for **25** of the 26, not 24, and fails on exactly
    the pair that crosses (both figures measured 2026-08-31, at b7bb4cd). Not
    a relation this can assert regardless: even one
    failing pair means it is not universal, and it is not the seed's only
    boundary crossing that will ever exist -- the next correctly-seeded item
    can cross one too. Counts and uniqueness are what the data actually
    supports, and asserting the false relation would be a rule that goes RED
    on the next correctly-seeded item.

    An extra evidence directory is a proposal whose migration was never
    committed -- reasoning published for a decision the repository does not
    hold. A missing one is an item with no record of who proposed it.
    """
    identifiers = [evidence.proposal_id for evidence in _evidence()]
    duplicated = sorted({name for name in identifiers if identifiers.count(name) > 1})

    assert len(_evidence()) == len(_migration_paths()), (
        f"the corpus holds {len(_evidence())} evidence files and "
        f"{len(_migration_paths())} migrations. Every committed item carries the record of "
        f"the proposal that produced it, and every committed proposal produced an item."
    )
    assert not duplicated, (
        f"proposalIds claimed by more than one evidence file: {duplicated}. The id is the "
        f"only key into a proposal, so two files claiming one makes the second invisible."
    )


def test_every_evidence_anchor_is_one_a_committed_migration_also_names() -> None:
    """Evidence cannot cite a document the corpus does not pin.

    The measured escape: an ``evidence.json`` whose ``sourceAnchors`` names a
    file from outside the repository -- a vault note, an operator handoff -- and
    the filename itself is the disclosure, before anyone reads a byte of it.
    Shape alone does not catch it: ``provider: git`` with a 40-hex sha and a
    plausible ``filePath`` is well-formed.

    So the rule is relational rather than syntactic: **every anchor an evidence
    file cites must be an anchor some committed migration cites too.** The
    migration's anchors are already verified byte-for-byte against the blob at
    that commit, so an anchor that survives this is one the repository has
    actually read. Measured 2026-08-20: the two multisets are identical, 26 and
    26.
    """
    published = {
        tuple(sorted((key, str(value)) for key, value in anchor.items()))
        for revision in _revisions()
        for anchor in revision.anchors
        if isinstance(anchor, Mapping)
    }
    uncited = [
        (evidence.path, dict(anchor) if isinstance(anchor, Mapping) else anchor)
        for evidence in _evidence()
        for anchor in evidence.anchors
        if not isinstance(anchor, Mapping)
        or tuple(sorted((key, str(value)) for key, value in anchor.items())) not in published
    ]

    assert not uncited, (
        f"evidence anchors no committed migration names: {uncited}. Only the migrations' "
        f"anchors are verified against a blob, so an anchor that appears only here has been "
        f"published and never checked -- and a path is a disclosure before it is a pin."
    )


def test_every_evidence_migration_claim_resolves_against_a_committed_migration() -> None:
    """The corpus half of #253's own cross-check, unchecked until now (ADV-RC MEDIUM-3).

    ``propose accept`` treats a committed ``evidence.json``'s optional
    ``migrationId``/``itemId`` as a *claim*, not as ground truth: it looks the
    named migration up in the loaded set and confirms the migration's own
    operations -- ``createItem`` and ``upsertRevision``, the two
    :data:`GOVERNED_OPERATIONS` permit, matching ``ProposalService``'s
    ``_migration_item_ids`` exactly -- actually name the claimed item before
    treating the proposal as accepted (``_landed_state``). Nothing here
    re-derived that on the *committed* data: a hand-edited ``evidence.json``
    could claim a ``migrationId`` naming nothing, or a real migration that
    operates on a different item, and every rule in this module stayed
    green -- the same shape as the anchor cross-check just above, applied to
    the other cross-check field pair.

    ``migrationId`` gates the check; ``itemId`` is checked only when also
    present. Both fields are independently optional
    (:data:`OPTIONAL_EVIDENCE_KEYS`, admitted one at a time), and a record
    with neither makes no claim for this rule to resolve.
    """
    item_ids_by_migration_id: dict[str, frozenset[str]] = {}
    for path in _migration_paths():
        document = _document(path)
        migration_id = document.get("id")
        if not isinstance(migration_id, str):
            continue
        operations = document.get("operations", [])
        item_ids_by_migration_id[migration_id] = frozenset(
            str(operation["itemId"])
            for operation in operations
            if isinstance(operation, Mapping)
            and operation.get("op") in {"createItem", "upsertRevision"}
            and isinstance(operation.get("itemId"), str)
        )

    unresolved = []
    for evidence in _evidence():
        migration_id = evidence.document.get("migrationId")
        if migration_id is None:
            continue
        if not isinstance(migration_id, str) or migration_id not in item_ids_by_migration_id:
            unresolved.append(
                (evidence.path, "migrationId", migration_id, "names no committed migration")
            )
            continue
        item_id = evidence.document.get("itemId")
        if item_id is None:
            continue
        if not isinstance(item_id, str) or item_id not in item_ids_by_migration_id[migration_id]:
            unresolved.append(
                (
                    evidence.path,
                    "itemId",
                    item_id,
                    f"is not an item migration {migration_id} operates on",
                )
            )

    assert not unresolved, (
        f"evidence records whose migrationId/itemId cross-check does not resolve against the "
        f"committed migrations: {unresolved}. `propose accept` confirms these fields against "
        f"the loaded migration set before treating a proposal as accepted; a claim nothing "
        f"here checks could be wrong in the committed corpus and nothing would notice."
    )


# -- The managed ignore block ------------------------------------------------


def test_the_managed_gitignore_block_appears_exactly_once() -> None:
    """``init`` rewrites what is between the markers; a second pair splits that.

    A duplicated or half-removed block leaves one copy that ``init`` maintains
    and one that nobody does, and the stale copy is the one a reader believes.

    One of the two rules in this module that read no corpus population at all,
    so the emptiness refusal in :func:`_corpus_paths` does not reach it.
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


def test_the_managed_gitignore_block_lists_exactly_the_patterns_init_writes() -> None:
    """The block this repository committed is the block ``init`` writes (ADR-0004).

    **Not "the derived patterns", which is what this rule was called until
    ADR-0028.** The block carries two categories now: derived artifacts, which
    ADR-0004 governs, and ``.theurian/proposals-local/``, which is authored
    content kept out of Git on purpose and which nothing rebuilds. The claim
    that survives both is the one made here -- the committed file and
    ``GITIGNORE_ENTRIES`` are the same list, in the same order -- and it is the
    claim that was doing the work all along.

    Order included, so the comparison is exact and deterministic rather than a
    set membership that a reordering could satisfy differently on a rerun. A
    missing pattern is how a derived artifact -- an index database, a state
    file -- becomes committable without anyone deciding it should be; removing
    one was a mutation that survived. For the new entry the failure runs the
    other way: a missing pattern is how a proposal whose bytes must not leave
    the machine reaches ``git add -A``.

    The other rule here that reads no corpus population.
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

    **Two sources, and the weaker one is named rather than hidden.** With git,
    the index mode is read and must be ``100644``, which is the claim that
    travels. On the ``.mutate-population`` path there are no modes -- the
    manifest is a path list -- so the mode is read from the *working tree* with
    :func:`os.lstat` instead. That is a real degradation and it is worth what it
    is worth: ``tools/mutate.py`` copies the checkout with ``symlinks=True`` and
    ``copytree``'s default ``copy2``, so a symlink stays a symlink and the
    executable bit survives, which makes the working-tree answer faithful *for
    that copy* -- but it is the disk's answer, not the index's, and a machine
    whose checkout was mangled would be believed. The rule that decides anything
    runs where git answers.
    """
    index = _tracked()
    corpus = _corpus_paths()

    if index.modes is not None:
        unexpected = [
            (path, sorted(index.modes[path])) for path in corpus if index.modes[path] != {"100644"}
        ]
        source = "the index"
    else:
        unexpected = [
            (path, [_worktree_mode(path)])
            for path in corpus
            if _worktree_mode(path) != "regular, non-executable"
        ]
        source = f"the working tree ({_POPULATION_MANIFEST} carries no index modes)"

    assert not unexpected, (
        f"tracked corpus paths that are not regular, non-executable files, according to "
        f"{source}: {unexpected}"
    )


def _worktree_mode(path: str) -> str:
    """How the working tree describes one tracked path, for the manifest fallback."""
    try:
        mode = os.lstat(REPO_ROOT / path).st_mode
    except OSError as error:
        return f"unreadable ({error.strerror})"
    if stat.S_ISLNK(mode):
        return "symlink"
    if not stat.S_ISREG(mode):
        return "not a regular file"
    if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        return "executable"
    return "regular, non-executable"
