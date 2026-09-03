"""Packaging and accepting change proposals (ADR-0013 §4).

Two operations, and the boundary between them is the product:

``draft`` writes ``.theurian/proposals/<proposal-id>/`` -- a schema-valid,
directly applicable migration, the body in its native format, and the evidence a
reviewer reads. It writes nowhere else. A draft asked for ``local=True`` writes
the same layout under ``.theurian/proposals-local/`` instead, which
``theurian init`` git-ignores: same content, a parent that does not travel
(ADR-0028).

``accept`` moves those files into place, once it has proved the move is safe to
make. **It automates the file moves and not the judgement**: it does not apply
the change, and above all does not approve it. Approval is a human merging a
pull request, and there is no code path here that stands in for one.

What it *does* check is two things, both before it moves anything, and ADR-0027
is where the reasoning lives:

* **That the landed migration set with this proposal in it still survives the
  pipeline ``migrate apply`` runs** (decision 2). If it does not, the acceptance
  is refused and nothing is consumed, so the proposal is still there to correct.
  That is a change from ADR-0013 §4's division, which left every question about
  the migration to ``migrate validate``; it held while ``accept`` was a ``mv``,
  and stopped holding when the same command began deleting its sources (#307).
* **That nothing this acceptance puts into the pull request appears to carry a
  secret** (decision 3, SEC-11, T-15), under the policy ``security.secretScan``
  selects -- ``block`` unless the project says otherwise. It scans each body's
  content, the migration document's own author-written fields (its
  ``contentFile`` among them), the migration file's bytes, its filename, each
  body's landed path (#336, #349) and the evidence record's own text (#361); a
  field reaches every search result while the body is only read on request. The
  evidence record is the one input the command does not *move*: it stays in the
  proposal directory that ``accept``'s own first next step tells the author to
  commit, which puts it in history by a different route. Best effort, and the
  product's published stance that it is not a replacement for a repository
  secret scanner is unchanged by it.

Both are driven by a composition root -- the CLI today, Milestone 7's
write-intent MCP tools next -- which is why the schema check arrives as an
injected callable rather than by importing the loader (ADR-0003).

The two moves ``accept`` performs are deliberately asymmetric, and that
asymmetry is the whole of what #89 measured:

* The **migration** file must never land on an existing name. Its name carries
  its ULID, so a collision means that migration is already in place. When two
  proposals both wrote ``migration.yaml``, the second acceptance replaced the
  first and reported nothing; validation then found one migration and applying
  it applied only that one, with the first change gone from the set and its body
  file left in ``.theurian/knowledge/`` with nothing pointing at it.
* The **body** file may replace what is at its ``contentFile`` path -- but only
  where no migration already in place pins those bytes. A body a migration
  references is immutable: the loader compares it against the pinned
  ``contentSha256`` on every load, so replacing it would make an applied
  migration's pin wrong and take the whole set out of ``migrate validate``. A
  generated proposal never reaches that case, because its ``contentFile`` carries
  a fresh revision id; a hand-authored one that reuses an existing body's path is
  refused, and the honest way to change a body is a new revision at a new path.
"""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn

import yaml

from theurian.application.project_service import ProjectError, ProjectPaths, ensure_gitignore
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.errors import (
    InputTooLargeError,
    IrregularSourceFileError,
    PathEscapeError,
    RevisionConflictError,
    SchemaUnreadableError,
    TheurianError,
)
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, ProposalId, RevisionId
from theurian.domain.knowledge import (
    AUTHORED_ANCHOR_FIELDS,
    AUTHORED_IN_THEURIAN,
    SourceAnchor,
)
from theurian.domain.migration import (
    MIGRATION_API_VERSION,
    CreateItem,
    Migration,
    UpsertRevision,
)
from theurian.domain.ports import Clock, IdGenerator
from theurian.domain.proposal import (
    Evidence,
    body_relative_path,
    is_generated_body_file_name,
    is_migration_file_name,
    kebab_slug,
    migration_file_name,
    require_evidence,
)
from theurian.domain.values import ContentHash, MediaType
from theurian.security.content_secrets import MAX_FINDINGS, SecretFinding, scan_text
from theurian.security.paths import (
    assert_no_symlink_escape,
    read_source_file,
    resolve_within_root,
)
from theurian.security.project_config import SecretScanPolicy, read_secret_scan_policy
from theurian.security.yaml_loading import (
    MAX_RENDERED_SCALAR_CHARS,
    is_bounded_scalar,
    load_yaml_mapping,
)

#: The evidence file's name. Fixed, unlike the migration's: nothing moves it, so
#: it cannot collide with anything, and a reviewer looking for the reasoning
#: behind a proposal should not have to work out what it was called.
EVIDENCE_FILE: Final = "evidence.json"

#: How many proposal-directory names one refusal lists before it stops counting.
#: The directory is committed input, so its file count is the contributor's:
#: 50,000 files produced a 600 KB error string in 1.5 s before this bound.
_MAX_NAMES_LISTED: Final = 5

#: How much of *one* untrusted name a refusal renders before it stops (#360).
#: :data:`_MAX_NAMES_LISTED` bounds how many, and bounded nothing about how long
#: each one is: a ``contentFile`` is a raw YAML scalar the schema has not seen
#: yet, so ``_body_moves``' "not in the proposal directory" refusal echoed the
#: whole of it into a terminal.
#:
#: Reused rather than minted: :data:`~theurian.security.yaml_loading
#: .MAX_RENDERED_SCALAR_CHARS` is this codebase's existing answer to *how much of
#: an untrusted scalar may be interpolated*, and a second number beside it would
#: be two answers to one question.
_MAX_NAME_CHARS: Final = MAX_RENDERED_SCALAR_CHARS

#: The same for another component's *report* -- a parser's or a validator's own
#: message -- which is a different question and needs a different number.
#:
#: A name is a token; a report is prose that has to survive being read. Measured
#: 2026-09-04 across the three accept-path suites, the longest legitimate report
#: this path composes is 779 characters (the rehearsal's revision-conflict
#: diagnosis, which names two revisions, two migrations and two body paths) and
#: the longest schema message 763 (a ``oneOf`` listing every operation shape).
#: :data:`_MAX_NAME_CHARS` cut both mid-sentence, so this bounds the *flood*
#: rather than the sentence: ``jsonschema`` puts ``repr(instance)`` in its
#: message, and an instance from a 4 MiB migration is megabytes.
_MAX_REPORT_CHARS: Final = 2000

#: What follows a string this module cut, so a reader can tell a cut from a name
#: that genuinely ends in an ellipsis. Outside the quotes, deliberately: inside
#: them it would read as part of the name. It publishes no length -- how many
#: characters were dropped is the contributor's number, the same reason
#: :data:`_MAX_NAMES_LISTED`'s tail is suppressed in :meth:`
#: ProposalService._secret_refusal`.
_TRUNCATED: Final = " (truncated)"

#: What a refusal prints in place of a name the detector reports (#360). A fixed
#: literal and never a prefix of the name: a partial echo is the walk-around
#: :class:`~theurian.security.content_secrets.SecretFinding`'s four-character
#: bound exists to prevent, and the same rule
#: :meth:`ProposalService._landed_text` applies to a finding's *location*.
_REDACTED_NAME: Final = "a name that appears to carry a secret"

#: The same, for another component's own words -- a parser's or a validator's
#: message, which quotes the input it refused.
_REDACTED_REPORT: Final = "a report whose own text appears to carry a secret"

#: How many entries ``operations`` may hold before ``accept`` refuses the
#: migration outright, checked against the raw parsed document -- before
#: :meth:`ProposalService._body_moves` reads a single body (#306).
#:
#: **Why a count, not only a size.** The migration file itself is already
#: bounded (``MAX_YAML_BYTES``, 4 MiB), but nothing bounded how many
#: operations that budget can spend. ``_operation_count`` runs on the raw
#: parsed document, before any per-entry shape is checked -- so the cheapest
#: entry that keeps ``operations`` a list is a bare scalar list item, four
#: bytes (``"- x\n"``), and 4 MiB of those is well past a million: the
#: largest such document that still fits the byte cap parses to 1,048,573
#: entries, measured directly rather than estimated from a "typical" entry
#: size a document under this check need not use. A schema-valid
#: ``upsertRevision`` may instead name a *distinct* ``contentFile`` up to
#: :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` (8 MiB) -- a
#: separate file the migration only points at, so the migration's own byte
#: cap says nothing about it -- and :meth:`ProposalService._body_moves`
#: read every one of them into memory before ADR-0027's schema check ever
#: ran (measured on main: a shared 512 KB body held resident once per
#: naming operation, 1120 MB resident at 2,000 operations with no cap in
#: sight). Counting operations is what a byte cap on the migration file
#: cannot do, because the cost this bounds is spent reading *other* files a
#: dozen bytes are enough to name.
#:
#: **Why every operation, not only ``upsertRevision``.** A ``createItem`` or
#: a malformed entry carries no body, but it still costs O(1) work at every
#: later stage this document reaches -- the schema check, the ``migrate
#: apply`` replay, the secret scan's field walk (#336) -- so bounding only
#: the body-bearing subset would leave those stages unbounded by an
#: operations array padded with cheap entries. The cap is on
#: ``len(document["operations"])``, full stop.
#:
#: **Why the bound is two channels, not one.** ``moves = tuple(self._body_moves(...))``
#: holds one resident copy per *distinct* incoming body -- up to
#: ``cap * MAX_SOURCE_FILE_BYTES``. But :meth:`ProposalService._commit`, further
#: down the same call, holds a *second* set simultaneously: for every move that
#: replaces an existing destination, ``restored`` carries the destination's
#: prior bytes -- read through the same size-capped path as every other
#: accept-path read (#400) -- kept resident so a failed write can roll it back.
#: Both lists can be full at once (a migration whose every operation replaces an
#: existing body), so the true worst-case peak is ``2 * cap * MAX_SOURCE_FILE_BYTES``,
#: not the one-channel figure this docstring stated before #306's round-one
#: review measured the gap: 499 replace-mode operations at 1 MiB each peaked at
#: ~1051 MiB against a create-mode run's ~542 MiB for the same count, roughly
#: double, exactly what the two-channel formula predicts.
#:
#: **Why 250, not the sibling precedent's 500 or 5,000.** ``openapi.py``'s
#: ``MAX_OPERATIONS`` bounds records that come from the document already being
#: read, so its worst case is bounded by that same document's own byte cap --
#: it is not the precedent for this bound, whose worst case is spent on
#: *separate* files the document only names. 5,000 would admit
#: ``2 * 5,000 * 8 MiB`` = ~78 GiB; 500 would still admit ~7.8 GiB. 250 keeps
#: the two-channel peak at ``2 * 250 * 8 MiB`` = ~3.9 GiB, a pure memory
#: ceiling this project can defend on its own terms, independent of any other
#: constant's value. It leaves enormous headroom over what this repository's
#: own migrations ever declare: the largest legitimate migration checked in
#: anywhere in this repo is 5 operations (the sample project's
#: ``add-order-cancellation``); every other migration here is 1-2, and
#: ``ProposalService.draft`` always emits exactly two (``createItem`` and
#: ``upsertRevision``). 250 is ~50x that observed maximum and rejects nothing
#: a real migration in this codebase has ever produced.
#:
#: **What this bound does not cover, and what closes it (#400).** This cap
#: bounds the *count* of operations, not the size of any one destination the
#: ``restored`` reads in :meth:`ProposalService._commit` hold resident. Until
#: #400, that read was a raw ``Path.read_bytes()``, bounded by
#: ``MAX_SOURCE_FILE_BYTES`` only for a destination an *earlier accepted
#: proposal* had itself landed through this same size-capped path -- a
#: destination that reached its size some other way (written directly to
#: ``.theurian/knowledge/``, exactly what a ``git clone`` delivers) was read
#: uncapped, so a single replace operation on an oversized committed body held
#: the whole of it resident regardless of this cap. ``_commit`` now reads every
#: replaced destination through
#: :func:`~theurian.security.paths.read_source_file`, the same path every
#: other accept-path read takes, so the two-channel peak this bound assumes is
#: unconditional rather than depending on how each destination came to exist.
MAX_UPSERT_OPERATIONS: Final = 250

#: Where a secret-scan finding sits when the text it was found in is an artifact
#: ``accept`` lands rather than a field of the migration document (#349).
#:
#: **No finding location is ever built from author-controlled or scanned text.**
#: A location assembled from a body's landed path, from the migration's filename,
#: or from the body content itself would be a verbatim second copy of the
#: credential it reports -- in the refusal a terminal prints and in the ``accept
#: --json`` document something logs -- routing straight around the four-character
#: bound :class:`~theurian.security.content_secrets.SecretFinding` refuses to be
#: constructed past. So every channel here names *itself* with a fixed literal,
#: carrying at most an integer index, exactly the discipline
#: :func:`_authored_strings` holds for a document key.
#:
#: Prose rather than a dotted path, so a reader does not go looking for a field
#: of the document by that name: none of these is one. Each is rendered as
#: ``<location>:<line>:<column>``, and the position is real -- into the migration
#: file for its bytes, into a body for its content, and into the name for the two
#: name channels.
_AT_MIGRATION_BYTES: Final = "the migration file as written"
_AT_MIGRATION_NAME: Final = "the migration filename"

#: The landed path of one body, indexed by its position among the bodies the
#: migration lands -- the order its ``contentFile`` operations appear in. Indexed
#: because a migration may land several and two of them may both be secret-shaped;
#: by position rather than by the path, which is the thing being reported.
_AT_BODY_PATH: Final = "the landed path of body"

#: The content of one body the acceptance lands, indexed by the *same* position
#: :data:`_AT_BODY_PATH` uses, so a content finding and a path finding at one
#: index name the same body. A fixed literal rather than that body's landed path,
#: which is where this channel's location sat until the review of #349 found the
#: echo: when the path is itself the credential, locating a body-content finding
#: by it republished the value, walking around the same four-character bound the
#: two name channels do -- and it was the last finding-location channel still
#: built from scanned text. The sibling class of author-supplied names echoed in
#: *refusal messages* elsewhere in this module is closed by :func:`_bounded` and
#: the two renderings over it (#360), whose boundary :func:`_names` records.
_AT_BODY_CONTENT: Final = "the content of body"

#: The evidence record's own text, whole (#361). The one channel here that names
#: something the acceptance does **not** land -- and it is scanned for exactly
#: that reason: ``_remove_proposal_sources`` deletes the migration and every body
#: and leaves ``evidence.json`` in the proposal directory, while accept's own
#: first next step tells the author to open a pull request with that directory in
#: it. So the file travels into Git history by this command's own instruction,
#: which is the outcome T-15 and SEC-11 name, reached through the evidence file
#: rather than through the migration.
#:
#: Unindexed, because there is exactly one: the name is fixed
#: (:data:`EVIDENCE_FILE`) and nothing moves it.
_AT_EVIDENCE: Final = "the evidence record as written"

#: The errnos a ``chmod`` actually cures, for the accept-path read-failure remedy.
#: An ``EISDIR``/``ENOTDIR``/``ENAMETOOLONG``/``ELOOP`` is the proposal's own input
#: at fault, not a permission bit, so prescribing ``chmod`` for it over-claims the
#: cause -- the mistake ``c7cf455`` corrected for :class:`PathEscapeError` (#233).
_PERMISSION_ERRNOS: Final = frozenset({errno.EACCES, errno.EPERM})

#: Checks a built migration document against the published JSON Schema, raising
#: on failure. Supplied by the composition root, because locating and reading
#: ``schemas/`` is an adapter's job (ADR-0003).
MigrationDocumentValidator = Callable[[Mapping[str, object]], None]

#: Returns an item's current revision in approved canonical state, or ``None`` if
#: it does not exist. Injected so the generator can require ``--expected-revision``
#: on a known item without opening the state database: the CLI derives it from
#: the loaded migration set (:func:`current_revision_in`), and Milestone 7's MCP
#: tools supply their own view of the same state.
CurrentRevisionLookup = Callable[[ItemId], RevisionId | None]

#: Returns the migration filed under an id in the project's approved set, or
#: ``None`` if none is. Injected -- ``MigrationSet.get`` from the *same*
#: ``MigrationSet`` ``resolve_context`` already loaded -- so the accept path reads
#: exactly the set ``migrate validate``/``apply`` read (keyed by inner id in
#: ``MigrationSet._by_id``) rather than re-detecting landed migrations from the
#: filesystem. That the loader is not imported here is ADR-0003, the same reason
#: the schema check and the current-revision lookup arrive this way.
LandedMigrationLookup = Callable[[MigrationId], Migration | None]

#: Every migration in the project's approved set. The same ``MigrationSet``
#: :data:`LandedMigrationLookup` is keyed into, handed over whole because the
#: replacement guard's question -- "does *any* migration already in place read
#: the file at this destination?" -- is not keyed by id and cannot be asked one
#: lookup at a time. Injected for the identical reason: it is the loader's set,
#: so the guard and the loader cannot disagree about which bodies are backed
#: (#234), and the loader itself stays an adapter this layer never imports
#: (ADR-0003).
#:
#: The return is a :class:`~collections.abc.Collection`, not a bare
#: ``Iterable``, as a defensive constraint on the callable rather than a
#: description of the current caller. The guard invokes this once and
#: materializes the result with ``tuple(...)`` before scanning that tuple per
#: replaced body, so today it would stay correct even against a single-use
#: generator. ``Collection`` guards a *future* refactor: one that iterated the
#: returned value directly per move -- dropping the ``tuple(...)`` -- would, on a
#: spent iterator, answer the first move and silently wave every later one
#: through, and requiring re-iterability at the type edge forbids that shape from
#: ever type-checking. Both roots return the loaded ``MigrationSet``, which is
#: re-iterable, so the constraint costs nothing.
LandedMigrations = Callable[[], Collection[Migration]]


@dataclass(frozen=True, slots=True)
class CandidateMigrationSet:
    """A migration set to prove applyable, described as files rather than as objects.

    What :data:`MigrationSetRehearsal` is handed. It is files and not a
    ``MigrationSet`` because the pipeline it will be put through *reads a project
    tree*: the loader re-reads each ``upsertRevision``'s body from the path its
    ``contentFile`` resolves to, and an incoming proposal's bodies are not at
    those paths yet. Handing over an already-parsed set would mean the pre-check
    parsed it -- a second loader, which is the shape ADR-0027 decision 2 forbids.

    Every path is project-relative, so a copy laid out the same way resolves
    every ``../knowledge/...`` the same way the original does.
    """

    #: The project every path below is relative to, and the only tree a rehearsal
    #: reads. It writes nothing here.
    root: Path
    #: The knowledge directory's own name -- ``.theurian`` unless this project was
    #: registered with another. The copy is laid out under the same name.
    knowledge_directory: PurePosixPath
    #: The id the replay's throwaway store records its rows under. The real
    #: project's, so the replay exercises the key the real apply would.
    project_id: ProjectId
    #: Project-relative files to copy across: each landed migration's own file and
    #: each body a landed ``upsertRevision`` reads. Sorted and deduplicated, so
    #: the copy is the same on every run and every filesystem.
    landed: tuple[str, ...]
    #: Project-relative destination and bytes for each file the *incoming*
    #: proposal contributes -- its migration under ``migrations/``, its bodies
    #: under ``knowledge/``. Written after ``landed``, so a body this proposal
    #: replaces is replaced in the copy too: the copy is the union, not the two
    #: sets side by side. The bytes are the ones ``accept`` already read through
    #: the security layer, never a second read of the same file.
    incoming: tuple[tuple[str, bytes], ...]


#: Proves a :class:`CandidateMigrationSet` survives the pipeline ``migrate
#: apply`` runs, raising if it does not, and writing nothing outside a throwaway
#: target. Injected, and deliberately **not** a second implementation of that
#: pipeline: the composition root wires the very function ``migrate apply``
#: calls (``cli/migration_pipeline.py``), which is what makes "``accept`` and
#: ``migrate apply`` cannot disagree about whether a set is usable" structural
#: rather than a property two pieces of code happen to share today (ADR-0027
#: decision 2's hard condition).
MigrationSetRehearsal = Callable[[CandidateMigrationSet], None]


class ProposalError(TheurianError):
    """A proposal could not be drafted or accepted.

    Carries ``remedy`` for the same reason :class:`ProjectError` does: a caller
    reporting the failure must not have to infer the cure from the exception's
    type, and these failures have genuinely different cures -- an unknown
    proposal id, a migration already in place, a body file that is not there.
    """

    def __init__(self, message: str, *, remedy: str = "") -> None:
        self.remedy = remedy
        super().__init__(message)


class ChangeAlreadyInPlaceError(ProposalError):
    """``.theurian/migrations/`` already holds the migration this move would make.

    Its own family rather than a message a caller matches on: these are the
    refusals that say something about the project's *knowledge state* rather
    than about the invocation, so a caller reports them under the exit code it
    reserves for that, and a reworded message cannot silently move them.

    The two members differ in what they say about *this* proposal, and their
    remedies differ with them. :class:`ProposalAlreadyAcceptedError` says this
    proposal's own migration has landed, so drafting it again would duplicate a
    change already in history (#89). :class:`MigrationNameTakenError` says the
    *name* is taken, which is that same case seen from the other side or a
    genuinely different change that collided -- so its remedy sends the reader to
    read what is there first, and drafting again is right only in the second
    case. What they share, and what the exit code carries, is that neither is a
    refusal a caller may retry unchanged.
    """


class MigrationNameTakenError(ChangeAlreadyInPlaceError):
    """``.theurian/migrations/`` already holds a file of that name.

    The name carries the migration's id, so a collision means that migration is
    already in place. Reached with the proposal's own migration file still in its
    directory, which is what separates it from
    :class:`ProposalAlreadyAcceptedError`.
    """


class ProposalAlreadyAcceptedError(ChangeAlreadyInPlaceError):
    """This proposal's own migration has already been moved out of its directory.

    The evidence-only shape :func:`_no_migration_error` diagnoses: nothing is
    left to move, because a previous ``accept`` moved it.
    """


class ApprovedSetUnusableError(ProposalError):
    """``.theurian/migrations/`` cannot be applied, with or without this proposal.

    Its own type, and not the plain :class:`ProposalError` every other
    pre-check refusal raises, because the two carry opposite instructions and a
    caller acts on the type before it reads the message. A plain
    ``ProposalError`` says *this proposal could not be used as it stands*, whose
    recovery is to correct or re-draft it. That recovery is wrong here: the
    proposal may be perfect, and drafting a second one mints a duplicate for a
    fault it does not have (#89's hazard, arriving through the pre-check ADR-0027
    added). What a caller must do instead is read the project's knowledge state,
    which is what the exit code reserved for that already means.
    """


@dataclass(frozen=True, slots=True)
class ProposalRequest:
    """One proposed change, before any identifier or path has been chosen.

    Deliberately free of ids and paths: those are the service's to mint, so that
    an MCP tool and the CLI cannot disagree about them. ``expected_revision`` is
    the one field that changes the shape of the result -- present, this is an
    update and the migration states which revision it replaces (ADR-0006, #210).
    """

    item_id: ItemId
    title: str
    kind: KnowledgeKind
    owner: str
    author: str
    description: str
    body: str
    content_type: MediaType
    evidence: Evidence
    source_anchors: tuple[SourceAnchor, ...] = ()
    labels: tuple[str, ...] = ()
    scope_paths: tuple[str, ...] = ()
    #: Absent means "not stated": the field is left out of the migration and the
    #: loader applies the schema default. A value is never invented here, because
    #: `unverified`/`internal` written into every draft would assert a judgement
    #: the caller did not make (#249).
    trust_level: TrustLevel | None = None
    sensitivity: Sensitivity | None = None
    namespace: str | None = None
    expected_revision: RevisionId | None = None

    def __post_init__(self) -> None:
        # ADR-0013 point 5, on the generation path itself rather than only on
        # the value it was handed. `Evidence` enforces the same rule, and one of
        # the two is always the redundant one -- which is the point: neither is
        # the place a future caller happens not to go through.
        require_evidence(self.evidence)
        for name, value in (
            ("title", self.title),
            ("owner", self.owner),
            ("author", self.author),
            ("description", self.description),
        ):
            if not value.strip():
                raise ProposalError(
                    f"A proposal needs a non-empty {name}.",
                    remedy=f"Pass --{name}.",
                )
        if not self.body.strip():
            raise ProposalError(
                "A proposal with an empty body proposes nothing.",
                remedy="Write the knowledge into a file and pass it as --body-file.",
            )
        # INV-8, enforced here rather than left to `migrate apply`. A revision
        # with neither is schema-valid and then exits 4 with "has no source
        # anchor" (measured on the shipped sample project, #36) -- after a human
        # has reviewed the proposal and merged the pull request.
        if not self.source_anchors and AUTHORED_IN_THEURIAN not in self.labels:
            raise ProposalError(
                "A revision needs at least one source anchor, or the "
                f"{AUTHORED_IN_THEURIAN!r} label to declare it originates here.",
                remedy=(
                    "Pass --source-uri with where this came from, or --authored-here "
                    "if the knowledge originates in Theurian."
                ),
            )

    @property
    def resolved_namespace(self) -> str:
        """The namespace to record, defaulting to the item id's own."""
        return self.item_id.namespace if self.namespace is None else self.namespace


@dataclass(frozen=True, slots=True)
class DraftedProposal:
    """What one call to :meth:`ProposalService.draft` wrote, and where."""

    proposal_id: ProposalId
    directory: Path
    migration_id: MigrationId
    migration_file: Path
    revision_id: RevisionId
    expected_revision: RevisionId | None
    body_file: Path
    evidence_file: Path
    #: As written into the migration: relative to ``.theurian/migrations/``,
    #: which is where the migration file lives *after* acceptance.
    content_file: str
    content_sha256: ContentHash
    #: Where ``accept`` will put the body. Reported so a caller can say what the
    #: change would touch without reading the migration back.
    body_destination: Path


@dataclass(frozen=True, slots=True)
class MovedFile:
    """One file ``accept`` relocated, and whether it landed on something."""

    source: Path
    destination: Path
    replaced: bool


@dataclass(frozen=True, slots=True)
class ProposalSecretFinding:
    """One secret-shaped string the accept-path scan found, and where it sits.

    ``location`` is one of two kinds, because the scan reads two kinds of input
    (#336, #349):

    * **A field of the migration document**, named by its path inside that
      document -- ``migration.operations[1].metadata.title``, or an
      ``upsertRevision``'s ``migration.operations[1].contentFile``. Every segment
      is a literal this module chose (:func:`_authored_strings`), never a key read
      back out of the document, so an untrusted key cannot ride into the message
      this renders.
    * **An artifact the acceptance lands** -- a body's content or its landed
      path, the migration's own bytes, or its filename -- named by a fixed
      literal for the channel it came from (:data:`_AT_BODY_CONTENT`,
      :data:`_AT_BODY_PATH`, :data:`_AT_MIGRATION_BYTES`,
      :data:`_AT_MIGRATION_NAME`), the two body channels each carrying an integer
      index so two bodies stay tellable apart. None is built from what was
      scanned: on the two name channels, and on a body whose content is itself
      credential-shaped, a location derived from the match would republish it, so
      the literal is what keeps the finding from being a second copy of what it
      reports (#360).

    They are told apart by shape rather than by a flag, because nothing acts on
    the difference: all are printed for a human to go and look at.

    ``finding.line``/``finding.column`` are positions **within the scanned
    text**. For a body's content or the migration's own bytes that is the
    position in the file; for a migration field it is the position in that field's
    own value, because the document is scanned one value at a time (see
    :func:`_document_findings` for why); for a filename or a landed path it is the
    position in that name.
    """

    location: str
    finding: SecretFinding

    def describe(self) -> str:
        """One line naming the location, the position, the family, and nothing else."""
        return self.finding.describe(at=self.location)


@dataclass(frozen=True, slots=True)
class SecretScanResult:
    """What the accept-path secret scan did, and what it found (SEC-11).

    The policy rides along with the findings because an empty list means two
    different things and a caller has to be able to tell them apart: under
    ``warn`` it says the proposal was scanned and is clean, and under ``off`` it
    says nothing was scanned at all. Under ``block`` it is always empty -- a
    finding refuses the acceptance, so a result exists only when there was none.
    """

    policy: SecretScanPolicy
    findings: tuple[ProposalSecretFinding, ...] = ()


@dataclass(frozen=True, slots=True)
class AcceptedProposal:
    """What one call to :meth:`ProposalService.accept` moved."""

    proposal_id: ProposalId
    migration: MovedFile
    bodies: tuple[MovedFile, ...] = ()
    #: Whether the proposal was read from ``.theurian/proposals-local/`` rather
    #: than the tracked location (ADR-0028). Published so a composition root can
    #: say what happens next without comparing paths: the tracked directory
    #: travels in the pull request, and the local one is git-ignored and does
    #: not. Defaulted to the committable case, which is ADR-0013 point 7's.
    local: bool = False
    #: What the SEC-11 scan did. Defaulted to the policy an unconfigured project
    #: gets, so a hand-built result -- a fake in a test, a future caller -- states
    #: the strictest reading rather than an unscanned one it did not check.
    secret_scan: SecretScanResult = SecretScanResult(policy=SecretScanPolicy.BLOCK)
    #: A remedy set only when the move landed but the proposal's own source files
    #: could not then be removed (a read-only proposal directory). The acceptance
    #: succeeded, so this rides on the success result rather than turning it into
    #: a failure -- reporting a non-landing would send the caller to re-draft and
    #: mint a duplicate migration (#89). ``None`` on a clean move.
    cleanup_remedy: str | None = None


class ProposalService:
    """Writes proposal directories, and moves accepted ones into place."""

    def __init__(  # noqa: PLR0913 -- injected dependencies, all keyword-only (ADR-0003)
        self,
        *,
        paths: ProjectPaths,
        project_id: ProjectId,
        clock: Clock,
        ids: IdGenerator,
        validate: MigrationDocumentValidator,
        current_revision: CurrentRevisionLookup,
        landed_migration: LandedMigrationLookup,
        landed_migrations: LandedMigrations,
        rehearse: MigrationSetRehearsal,
    ) -> None:
        self._paths = paths
        self._project_id = project_id
        self._clock = clock
        self._ids = ids
        self._validate = validate
        self._current_revision = current_revision
        self._landed_migration = landed_migration
        self._landed_migrations = landed_migrations
        self._rehearse = rehearse

    # -- generation --------------------------------------------------------

    def draft(self, request: ProposalRequest, *, local: bool = False) -> DraftedProposal:
        """Write one proposal directory, and -- for ``--local`` -- the ignore rule it needs.

        ``local`` picks the parent (ADR-0028): the layout inside the directory is
        identical, and the migration's ``contentFile`` is unaffected because it is
        relative to ``.theurian/migrations/``, which is where the migration lands
        from either location. It is a parameter of the *act* rather than a field
        of :class:`ProposalRequest`, which is what becomes the migration document
        -- where the draft is written is not something the reviewed text says.

        A ``local=True`` draft also brings the managed ``.gitignore`` block current
        before it writes, because ``--local``'s confidentiality *is* that ignore
        rule (:meth:`_ensure_local_is_ignored`); it is refused rather than written
        if the rule cannot be established. An ordinary draft writes only its own
        proposal directory and touches nothing outside it.

        It defaults to the committable location, and that direction is
        deliberate: a caller who forgets it gets a proposal that shows up in
        ``git status``, which is visible, rather than one that silently does
        not.

        Every identifier is fresh: the proposal, the migration, and the
        revision. A revision id names one item for the life of a project, and
        reusing an applied one is accepted by ``migrate validate`` and refused by
        ``migrate apply`` -- so a generator that reused one would produce a
        proposal ``accept`` refuses, on the replay, having consumed nothing.

        The body path carries the revision id for a reason measured on this
        branch (see :func:`body_relative_path`): one path per item made the
        second accepted proposal invalidate the first migration's pinned digest,
        and the project stopped validating entirely.

        An update states which revision it replaces, or it is refused here --
        at the point the author can still act on it, rather than at ``accept``
        with the work already done (#210). The
        generator does not have to be told the item already exists: it derives
        the item's current revision from the approved migration set (which is the
        canonical state), so ``--expected-revision`` is required exactly when the
        item is real and forbidden when it is not.

        Raises:
            ProposalError: If the request cannot be packaged, if an update omits
                or misplaces its ``expectedRevision``, if the built migration does
                not satisfy the published schema, or if a ``--local`` draft cannot
                make ``.theurian/proposals-local/`` git-ignored. No proposal
                directory is written in any case.
        """
        self._check_expected_revision(request)
        proposal_id = ProposalId(self._ids.new_ulid().value)
        migration_id = MigrationId(self._ids.new_ulid().value)
        revision_id = RevisionId(self._ids.new_ulid().value)

        relative_body = body_relative_path(request.item_id, revision_id, request.content_type)
        # `..` and the sibling's name rather than `os.path.relpath`: both
        # directories are children of `.theurian/`, so the relative path is
        # exact by construction and stays POSIX-shaped on any platform.
        content_file = PurePosixPath("..", self._paths.knowledge.name, relative_body)
        body_bytes = request.body.encode("utf-8")
        digest = ContentHash.of_bytes(body_bytes)

        document = _migration_document(
            request,
            migration_id=migration_id,
            revision_id=revision_id,
            created_at=self._clock.now().replace(microsecond=0).isoformat(),
            content_file=content_file.as_posix(),
            digest=digest,
        )
        self._validate(document)

        if local:
            # Before a single byte is written: `--local`'s confidentiality rests
            # entirely on `.theurian/proposals-local/` being git-ignored, and that
            # is only true if the managed block carries the ADR-0028 entry (see
            # `_ensure_local_is_ignored`). Refused here, nothing is written.
            self._ensure_local_is_ignored()

        parent = self._paths.proposals_local if local else self._paths.proposals
        directory = parent / proposal_id.value
        directory.mkdir(parents=True)
        # The body mirrors the sub-path it will occupy under `knowledge/`, not a
        # flat leaf name. Two content files that differ only in namespace --
        # `../knowledge/alpha/notes.md` and `../knowledge/beta/notes.md` -- share
        # the leaf `notes.md`, and a flat layout made `accept` find one file for
        # both: it consumed the first and raised a bare `FileNotFoundError` on
        # the second, leaving an orphan in `knowledge/`. Mirroring the sub-path
        # here is what lets `accept` find each body by its full relative path.
        body_file = directory / relative_body
        body_file.parent.mkdir(parents=True, exist_ok=True)
        body_file.write_bytes(body_bytes)
        evidence_file = directory / EVIDENCE_FILE
        evidence_file.write_text(
            json.dumps(
                _evidence_document(request.evidence, proposal_id, migration_id, request.item_id),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        migration_file = directory / migration_file_name(
            migration_id, kebab_slug(request.title, fallback=_last_segment(request.item_id))
        )
        migration_file.write_text(_to_yaml(document), encoding="utf-8")

        return DraftedProposal(
            proposal_id=proposal_id,
            directory=directory,
            migration_id=migration_id,
            migration_file=migration_file,
            revision_id=revision_id,
            expected_revision=request.expected_revision,
            body_file=body_file,
            evidence_file=evidence_file,
            content_file=content_file.as_posix(),
            content_sha256=digest,
            body_destination=self._paths.knowledge / relative_body,
        )

    def _ensure_local_is_ignored(self) -> None:
        """Bring the managed ``.gitignore`` block current before a ``--local`` draft writes.

        ``--local``'s whole promise is confidentiality: the body does not appear
        in ``git status`` and does not travel to a clone or a pull request. That
        rests entirely on ``.theurian/proposals-local/`` being git-ignored, which
        is true only when the managed block carries the ADR-0028 entry. A project
        initialised before ADR-0028 -- every shipped ``0.1.0.dev9`` project -- has
        a block that does not, so a ``--local`` draft there would write a private
        body to a directory Git tracks while the command claimed it was ignored
        (HIGH-2, reproduced with a stale block).

        :func:`~theurian.application.project_service.ensure_gitignore` is
        idempotent and is the same re-run path ``theurian init`` uses, so a stale
        block is brought current and a current one is left untouched. If the block
        cannot be made current -- a ``.gitignore`` the filesystem refuses to write
        or read, or markers only the operator can repair -- the draft is *refused*
        rather than written and then falsely reported as ignored. The refusal
        never interpolates the absolute project path, which would carry the
        developer's home directory into an ``accept --json`` document (the
        discipline :meth:`_unreadable` records).
        """
        try:
            ensure_gitignore(self._paths.root)
        except ProjectError as exc:
            raise ProposalError(
                "A --local draft needs .theurian/proposals-local/ to be git-ignored, but the "
                "project's .gitignore has a Theurian block that cannot be rewritten safely. "
                "Nothing was written.",
                remedy=exc.remedy
                or "Repair the Theurian block in .gitignore by hand, then run `theurian init`.",
            ) from exc
        except (OSError, UnicodeDecodeError) as exc:
            reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else "it is unusable"
            raise ProposalError(
                f"A --local draft needs .theurian/proposals-local/ to be git-ignored, but the "
                f"project's .gitignore could not be updated ({reason}). Nothing was written.",
                remedy="Make .gitignore readable and writable, or run `theurian init`, then "
                "draft again. To draft without the ignore guarantee, drop --local -- but the "
                "proposal will then show up in git status.",
            ) from exc

    def _check_expected_revision(self, request: ProposalRequest) -> None:
        """Refuse an update with no guard, and a first revision with a stale one.

        ``expectedRevision`` is optimistic concurrency (ADR-0006): present, it
        must equal the item's current revision; absent, the revision is the
        item's first. Both are checkable at generation from the approved set,
        and checking here is what stops #210's unguarded update -- a second
        proposal for an existing item with no ``--expected-revision`` -- from
        being written at all. ``accept`` would refuse it on the replay
        (ADR-0027 decision 2) and consume nothing, so the cost of dropping this
        check would be a wasted draft rather than a broken set; refusing at
        generation is still where the author can act on it soonest.
        """
        current = self._current_revision(request.item_id)
        expected = request.expected_revision
        if current is None:
            if expected is not None:
                raise ProposalError(
                    f"{request.item_id.value} does not exist yet, so its first revision "
                    f"cannot replace {expected.value}.",
                    remedy="Drop --expected-revision to create the item, or correct --item-id.",
                )
            return
        if expected is None:
            raise ProposalError(
                f"{request.item_id.value} already exists at revision {current.value}; an "
                "update must state which revision it replaces, or accepting it would be "
                "refused for conflicting with the revision already in place.",
                remedy=f"Pass --expected-revision {current.value} to update it, or a new "
                "--item-id to create a different item.",
            )
        if expected != current:
            raise ProposalError(
                f"{request.item_id.value} is at revision {current.value}, but "
                f"--expected-revision names {expected.value}; the update would conflict at "
                "apply.",
                remedy=f"Pass --expected-revision {current.value}.",
            )

    # -- acceptance --------------------------------------------------------

    def accept(self, proposal_id: ProposalId) -> AcceptedProposal:
        """Prove the project still applies with this proposal in it, then move it.

        **The proposal is looked up in both locations** -- the tracked
        ``.theurian/proposals/`` and the git-ignored ``.theurian/proposals-local/``
        -- through one lookup, one symlink refusal, one containment check and one
        size cap. ADR-0028's hard condition: a second location must not become a
        second reader, because SEC-7 is held by one implementation or by none.
        An id present in *both* is refused naming both paths, never resolved by
        precedence: the two directories can hold different bytes, and choosing
        silently is the ambiguity class this project has already paid for.

        **The operation count is checked before a single body is read** (#306,
        SEC-8): a migration past :data:`MAX_UPSERT_OPERATIONS` is refused on
        the raw parsed document, ahead of ``moves = tuple(self._body_moves(...))``
        below. A schema check placed after that line would still be correct
        about *whether* to accept, but the memory this bounds is spent reading
        bodies, and that spend happens the moment ``_body_moves`` runs -- a
        refusal that arrives after it is too late to have prevented it. Two
        operations that name the *same* ``contentFile`` also read it at most
        once, in :meth:`_body_moves` itself, so resident memory tracks distinct
        bodies rather than operations naming them.

        **Before anything moves, the union of the landed migration set and this
        proposal is put through the pipeline ``migrate apply`` runs. If it does
        not survive, ``accept`` refuses and consumes nothing** (ADR-0027 decision
        2). The proposal directory is untouched on every refusal path, which is
        the property #307 asked for: a proposal survives its own rejection, so
        the author still has the sources to correct.

        That is a change of contract, not a new guard rail. ``accept`` used to
        move files and leave every question about the migration to ``migrate
        validate`` and ``migrate apply`` (ADR-0013 §4), which was defensible
        while ``accept`` was a ``mv`` -- and stopped being defensible once it
        also *deleted* its sources, because a check that runs after the input is
        destroyed cannot be acted on.

        The pre-check runs after the structural checks below, so what an already
        accepted proposal answers is unchanged, and it is described where it
        lives: :meth:`_refuse_unless_the_union_applies`.

        **Everything this acceptance puts into the pull request is scanned for
        secrets first** (SEC-11, ADR-0027 decision 3, #336, #349, #361) -- each
        body's content, the migration document's own author-written fields (its
        ``contentFile`` included), the migration file's bytes, its filename, each
        body's landed path and the evidence record's own text -- under the policy
        ``security.secretScan`` selects and ``block`` by default. It sits between
        the structural checks and the pre-check on purpose: the pre-check stages
        the bodies into a throwaway tree, and a body that is going to be refused
        should not be written anywhere at all. It also sits ahead of every word
        this command says about what to do next, so a refusal arrives before the
        author is ever told to commit the directory the finding is in.
        :meth:`_scan_for_secrets` has the reasoning.

        Every file this reads is proved to be a regular file inside the project
        with no symlink anywhere in its chain, and every file it writes lands
        inside ``.theurian/knowledge/`` (a body) or ``.theurian/migrations/``
        (the migration). A proposal directory is committed and so arrives
        through a contributor's pull request (ADR-0013 point 7) -- it is
        untrusted input, and a hand-authored ``contentFile`` or a symlinked
        body would otherwise make ``accept`` read a file outside the project or
        write one outside ``knowledge/``. The reads route through
        :func:`read_source_file` (SEC-7, T-5, and the size cap SEC-8); the
        writes use ``O_NOFOLLOW`` with an explicit mode, so no source bit and no
        planted destination symlink survives the move.

        The bytes that land are bounded by what a *committed* proposal can carry.
        A hardlink is the one channel this does not close -- ``O_NOFOLLOW`` does
        not see one -- but Git cannot commit a live hardlink (a fresh clone gets
        a distinct inode holding the committed blob), so the documented channel
        cannot deliver one; reaching it needs local write access at accept time,
        where the secret is already readable. Recorded as an accepted residual
        under the local-write boundary rather than closed with an ``st_nlink``
        check, which would refuse legitimate files.

        **CP-2 invariant: no accept-path filesystem or path fault escapes
        ``accept`` untranslated.** A fault that must abort ``accept`` is turned
        into an error carrying a ``remedy`` at one of six *translation* sites:
        the examination phase's ``except OSError`` in this method,
        :meth:`_refuse_unless_the_union_applies`, :meth:`_commit`'s own clause,
        :meth:`_destination_of`, which catches its ``resolve()``
        ``ValueError`` -- not an ``OSError``, so the examination clause never sees
        it -- in place,
        :func:`~theurian.security.project_config.read_secret_scan_policy`, which
        translates every way ``.theurian/config.yaml`` can fail before it
        returns, and :meth:`_evidence_text`, which reads the evidence record
        inside the scan and so sits outside the examination clause for the same
        reason the rest of the scan does (#361). The examination and commit
        clauses are deliberately separate,
        because a failed *write* must roll the destinations back before it
        reports, and one clause spanning both would describe a half-written tree
        as an unreadable proposal. The pre-check and the secret scan sit outside
        the examination clause for the same reason in the other direction: they
        read the whole project and its configuration, so a fault in a *landed*
        file or in ``config.yaml`` reaching the examination clause would be
        reported as an unreadable proposal with a ``chmod`` remedy for the wrong
        file. Two further sites catch ``OSError`` on the accept path but
        deliberately do *not* translate: :meth:`_remove_proposal_sources`
        degrades a post-landing cleanup failure to a remedy and still returns
        success, and :func:`_roll_back` stays silent so a raise cannot mask the
        error already propagating. An editor adding a filesystem call that must
        abort ``accept`` has to land it under one of the six translation sites,
        or add a seventh -- a raw escape publishes no ``{error, remedy}`` under
        ``--json`` (#227).

        Raises:
            ProposalAlreadyAcceptedError: If this proposal's own migration is
                already in ``.theurian/migrations/``. A composition root reports
                this and :class:`MigrationNameTakenError` under the exit code it
                reserves for knowledge state, so both are named here rather than
                left inside "a migration already in place".
            MigrationNameTakenError: If the migration's name is taken, whether by
                a previous acceptance of this proposal or by a different change
                that collided.
            ProposalError: If the proposal is unknown, ambiguous -- two migration
                files inside it, or the same id present in both proposal
                locations -- incomplete,
                could not be fully examined -- including a directory or a file
                in it the filesystem refuses to list, stat or read -- names a
                file the security layer refuses, declares more operations than
                :data:`MAX_UPSERT_OPERATIONS`, would land anything that appears
                to contain a secret -- a body, a migration field, the migration's
                own bytes, its filename or a body's path -- while
                ``security.secretScan`` is ``block``, or
                would leave the project's migration set unable to apply. Both
                types above are subclasses, so a caller that catches only this
                still catches everything.
            ProjectConfigError: If ``.theurian/config.yaml`` exists and cannot be
                read or states a ``security.secretScan`` value the build does not
                recognise. Not a :class:`ProposalError`, because the proposal has
                nothing wrong with it and its author has nothing to correct.
            PathEscapeError: If a ``contentFile`` resolves outside
                ``.theurian/knowledge/``.
            InputTooLargeError: If a file the accept path reads exceeds SEC-8's
                size cap.
        """
        try:
            location = self._require_directory(proposal_id)
            directory = location.directory
            migration_file = self._require_migration(location, proposal_id)
            migration_bytes = self._read_within_project(migration_file)
            document = _parse_migration(migration_bytes, migration_file)
            destination = self._paths.migrations / migration_file.name
            # "Already in place" is the harder stop and is reported first -- both
            # by the destination *name* and by the migration *id* the loaded set
            # already holds; the filename/id agreement is checked next, on a name
            # nothing holds.
            self._refuse_if_migration_present(destination, document)
            _require_filename_matches_id(migration_file, document)
            _refuse_past_the_operation_cap(document)

            moves = tuple(self._body_moves(directory, document))
            self._refuse_if_a_replacement_breaks_an_existing_pin(moves)
        except OSError as exc:
            # Every line above probes or reads a directory whose permissions are
            # a contributor's, through raw `is_symlink`/`is_file`/`exists`/`stat`
            # and read calls, and not one of them translated its own `OSError`:
            # the failure left `accept` as a bare `PermissionError`, so `--json`
            # published no `{error, remedy}` document at all (CP-2, #227). Which
            # call fires is an accident of the mode -- `0o000` on the directory
            # reaches the evidence probe, `0o444` the `is_symlink` above it, and
            # an unreadable migration file the read inside the security layer --
            # so the clause spans the examination phase rather than any one of
            # them.
            #
            # It stops there deliberately. `_commit` below keeps its own
            # `except OSError`, because a failed write must roll the
            # destinations back before it reports; a clause spanning both would
            # swallow that and describe a half-written tree as an unreadable
            # proposal.
            raise self._unreadable(proposal_id, exc) from exc

        # Outside the clause above for the same reason the pre-check is: this
        # reads `.theurian/config.yaml`, whose faults are the project's and not
        # this proposal's, and it raises its own translated errors (CP-2, the
        # fifth site).
        #
        # Before the pre-check, not after, and that ordering is load-bearing:
        # the rehearsal stages the incoming bodies into a throwaway tree, so a
        # body carrying a secret would be written somewhere -- briefly, but
        # written -- before anything had decided to refuse it. Scanning first
        # means a blocked body's bytes reach no filesystem at all. It is also
        # the cheaper order: the scan is a regex pass over bytes already in
        # hand, and the rehearsal copies and replays the project's whole
        # migration set.
        secret_scan = self._scan_for_secrets(
            location, migration_file, migration_bytes, document, moves
        )

        # Outside the clause above, deliberately: the pre-check reads every
        # landed migration and body, so its faults are not this proposal's and
        # must not be reported as "the proposal could not be examined". It
        # translates its own (CP-2, the fourth site).
        self._refuse_unless_the_union_applies(
            location, migration_file, migration_bytes, document, moves
        )

        accepted = self._commit(proposal_id, moves, migration_file, migration_bytes, destination)
        return replace(accepted, secret_scan=secret_scan, local=location.local)

    # -- the secret scan (SEC-11, ADR-0027 decision 3) ---------------------

    def _scan_for_secrets(
        self,
        location: _ProposalLocation,
        migration_file: Path,
        migration_bytes: bytes,
        document: Mapping[str, object],
        moves: tuple[_BodyMove, ...],
    ) -> SecretScanResult:
        """Scan everything the acceptance would land, refusing on the policy.

        The control T-15 names, at the point SEC-11 names it: ``accept`` is the
        last place a proposal can be stopped before a human merges it and
        ``migrate apply`` makes it canonical. The detector is best effort by
        recorded design (:mod:`theurian.security.content_secrets`), and the
        product's published stance -- *Theurian is not a repository secret
        scanner and is not a replacement for one* -- is unchanged by its
        existence.

        **The population is what an acceptance puts into the pull request, not
        what it parses** (#349, #361). ``_commit`` writes bytes to paths; a parse
        is something this method does on the way. Six inputs, in the order they
        are scanned:

        * **each body's bytes**, the channel since #198 -- the artifact a
          reviewer actually opens in a pull request;
        * **the migration document's author-written field values** (#336),
          skimmed rather than read, and ``title`` and an anchor's ``sourceUri``
          are published on every ``knowledge.search`` and ``knowledge.get``
          result, so a credential in one reaches an agent that never opens a
          body. An ``upsertRevision``'s ``contentFile`` is one of them: its
          *parsed* value is the one channel that catches a credential both in a
          ``..``-removed path segment *and* spelled with YAML escapes -- the
          migration bytes below catch a plainly-spelled traversal, the landed
          path catches an escaped credential in a segment that survives
          resolution, and only the parsed value sees the two at once (#349).
          :func:`_authored_strings` is the population and why it is that one;
        * **the migration file's own bytes**, which is what lands in
          ``.theurian/migrations/`` verbatim. It covers what no parse survives --
          a YAML comment holding the rotation note that names the retired value,
          and every field's spelling *as written*;
        * **the migration's filename**, whose slug after the ULID prefix is the
          contributor's on a hand-authored proposal and appears nowhere in the
          bytes; and
        * **each body's landed path** relative to ``.theurian/knowledge/``,
          directory components included -- ``_commit`` calls
          ``destination.parent.mkdir(parents=True)``, so every component becomes
          a real directory in the tree; and
        * **the evidence record's own text**, which this command lands nowhere
          and which travels into the repository all the same (#361). It is the
          one input here that is not an artifact of the move, and
          :meth:`_evidence_text` is where that difference is argued.

        **No channel subsumes the parsed field values.** A double-quoted YAML
        scalar spells any character as ``\\xNN``, so a token can sit in a parsed
        value -- a metadata field, or a ``contentFile`` -- while the bytes hold
        only escape runs no family matches. The landed-path channel and the
        parsed ``contentFile`` are *complementary* rather than one a subset of
        the other: ``..`` resolution can drop the very segment a credential sits
        in, while ``.resolve()`` following an in-tree symlink can equally
        substitute a component the authored string never spelled, so each scans
        text the other can miss. In the ordinary case their detection overlaps,
        and the landed-path channel is kept for its distinct *location* -- the
        resolved tree path a reviewer opens -- rather than for a detection unique
        to it. Both are pinned by their own tests.

        **One token may be reported by more than one channel, and that is the
        choice made here.** A ``contentFile`` naming a credential is reported
        against its parsed field value, against the migration's bytes where its
        as-written spelling matches, and against the landed path where the
        segment survives resolution; a field value spelled plainly is reported
        against the bytes and against the field. Nothing is deduplicated: the
        channels answer
        different questions -- what the file says, what its fields mean, what the
        tree ends up holding -- and each location sends a reviewer somewhere
        different. Suppressing the duplicate would need a key over the
        match, and a finding quotes at most
        :data:`~theurian.security.content_secrets.REDACTED_PREFIX_CHARS`
        characters of it: two *different* credentials of one family would collide
        on that key and one would be dropped. A security control that hides a
        real finding to tidy a report is the wrong trade, and de-duplicating
        after the fact would spend budget on findings it then discarded, so the
        ceiling below would stop meaning "we stopped looking here".

        **The cost is one more pass over the migration.** The bytes are already
        in hand -- no file is re-read -- but the regex pass over them is paid
        again, bounded by SEC-8's size cap on that one file, on top of the
        per-field passes. The two name channels are a few dozen characters each.
        :mod:`theurian.security.content_secrets` prices the per-input scan and
        records that the accept-path total is the sum over these inputs.

        **The policy is read here rather than injected**, unlike every adapter
        this service takes. ADR-0003's reason for injection is that locating
        schemas and opening databases is an adapter's job; ``security/`` is a
        shared primitive that this layer already calls directly for path
        containment and YAML loading. The deciding argument is what the two
        shapes fail like: an injected policy is one a composition root can
        forget to wire, and Milestone 7's write-intent MCP tools are a second
        root arriving. A security control that a caller can omit by omission is
        not a control.

        The configuration is read on every acceptance, including one whose
        migration carries no body at all. The policy governs the accept path
        rather than this particular proposal, so a configuration file that
        states something the build cannot act on is a refusal whatever is being
        accepted -- which surfaces the typo on the first acceptance instead of
        on the first one that happens to carry a body.

        **The policy is read before any input is touched**, which is what makes
        ``off`` mean what it says. There is no per-finding suppression, so a
        project that hits a false positive in a title -- or in a filename, where
        every artifact Theurian generates is built around a high-entropy ULID --
        has exactly one move; a scan wired ahead of the policy read would leave
        that project unable to accept anything while reporting the policy as
        ``off``.

        Returns:
            The policy that was in force and the findings to report on the
            success result. Findings are non-empty only under ``warn``: ``off``
            scans nothing and ``block`` raises rather than returning. They arrive
            in the order the inputs are listed above -- least skimmed first, so a
            reviewer meets the body before the artifact nobody reads -- and they
            are one list under one budget: :meth:`_secret_refusal`'s listing
            bound applies to the whole of it rather than to each channel, and a
            channel that would take the total past
            :data:`~theurian.security.content_secrets.MAX_FINDINGS` is truncated
            silently rather than given a ceiling of its own.

        Raises:
            ProposalError: If the policy is ``block`` and anything this
                acceptance would put into the pull request appears to carry a
                secret, or if it is ``block`` and the evidence record is present
                but cannot be read (:meth:`_evidence_text`). Raised before
                anything has been written, so the proposal directory is intact
                and the change can be corrected rather than re-drafted.
            ProjectConfigError: If ``.theurian/config.yaml`` exists and cannot be
                read, or states a ``security.secretScan`` value that is not one
                of the three. Deliberately not translated into a
                :class:`ProposalError`: the fault is in the project's
                configuration and the author has nothing to correct in the
                proposal, so re-labelling it would send them to edit a migration
                that is right -- the same reasoning that keeps
                :class:`~theurian.domain.errors.SchemaUnreadableError` distinct
                on this path.
        """
        policy = read_secret_scan_policy(self._paths.root, self._paths.config)
        if policy is SecretScanPolicy.OFF:
            return SecretScanResult(policy=policy)

        # Chained rather than appended, so the laziness `_findings_in` relies on
        # survives: a run whose budget fills in the bodies never opens
        # `evidence.json` at all, and the read that channel needs is not paid by
        # an acceptance that was going to be refused anyway.
        findings = _findings_in(
            chain(
                self._landed_text(migration_file, migration_bytes, document, moves),
                self._evidence_text(location, policy),
            )
        )
        if policy is SecretScanPolicy.BLOCK and findings:
            raise self._secret_refusal(location, findings)
        return SecretScanResult(policy=policy, findings=findings)

    def _landed_text(
        self,
        migration_file: Path,
        migration_bytes: bytes,
        document: Mapping[str, object],
        moves: tuple[_BodyMove, ...],
    ) -> Iterable[tuple[str, str]]:
        """Every text this acceptance would land, each with where it sits.

        The five channels :meth:`_scan_for_secrets` enumerates, in the order it
        states. Lazy, so a run that fills the budget in the first channel never
        decodes the migration a second time.

        **Every location is a fixed literal of this module's own, never the text
        that was scanned.** A body's *content* and a body's *path* are two
        channels over the same body, told apart by literal and shared by index
        (:data:`_AT_BODY_CONTENT`, :data:`_AT_BODY_PATH`), so two dirty bodies
        stay tellable apart and a content finding and a path finding at one index
        name the same body. The content channel located itself by that landed
        path until the review of #349 found the echo: when the path is *itself*
        the credential, the location republished it, walking around the
        four-character bound :class:`~theurian.security.content_secrets
        .SecretFinding` holds on the match -- the same reason the two name
        channels never name what they found. Bringing it under the literal rule
        is what makes *no* finding location author-controlled or scanned text.

        The sibling class -- author-supplied names echoed in refusal *messages*
        elsewhere in this module, several of them fired *before* this scan runs --
        is closed by :func:`_bounded` and the two renderings over it (#360). Its
        boundary is this module: :func:`_names` records the population that lies
        outside it and is not closed.
        """
        knowledge = self._paths.knowledge.resolve()
        landed = tuple(move.destination.relative_to(knowledge).as_posix() for move in moves)
        for index, move in enumerate(moves):
            # `errors="replace"` rather than a refusal on undecodable bytes. A
            # body that is not UTF-8 is a fault the rehearsal's loader reports
            # with a better message than this scan could, and refusing here
            # would make the secret scan the thing that reports it. Replacement
            # leaves every ASCII run intact, which is what a credential is; the
            # residual is a secret deliberately split by an undecodable byte,
            # which a best-effort detector does not claim to catch.
            yield f"{_AT_BODY_CONTENT}[{index}]", move.data.decode("utf-8", errors="replace")
        yield from _authored_strings(document)
        # `replace` again, though nothing can reach it today: `_parse_migration`
        # decodes these same bytes strictly and refuses before the scan runs. It
        # is written this way so the *scan* is never the thing that reports an
        # encoding fault -- the caller's message is the better one -- whatever a
        # later parse decides to tolerate.
        yield _AT_MIGRATION_BYTES, migration_bytes.decode("utf-8", errors="replace")
        yield _AT_MIGRATION_NAME, migration_file.name
        for index, at in enumerate(landed):
            yield f"{_AT_BODY_PATH}[{index}]", at

    def _evidence_text(
        self, location: _ProposalLocation, policy: SecretScanPolicy
    ) -> Iterable[tuple[str, str]]:
        """The evidence record's own text, which travels without being landed (#361).

        **The rationale this replaces was half right.** ``accept`` moves neither
        ``evidence.json`` nor the proposal directory's name, and the conclusion
        drawn from that -- neither is an artifact this scan can be about -- does
        not follow for the first of the two. Three facts of this command put the
        file into Git history without moving it:
        :meth:`_remove_proposal_sources` deletes the migration and every body and
        leaves the evidence behind; ``_ACCEPT_STEPS[0]`` tells the author to open
        a pull request *with the proposal directory in it*, because the merge is
        the approval; and ``.theurian/proposals/`` is not git-ignored. So an
        agent's free-text ``reasoning`` carrying a credential becomes a commit,
        which is the outcome T-15 and SEC-11 name -- reached through the evidence
        file rather than through the migration.

        **The whole text, never a field walk.** The migration document has an
        allowlist because what lands there is a *parsed* value that the loader
        reads and the index publishes; what lands here is the file, byte for
        byte, so the bytes are the artifact and the bytes are what is scanned.
        A field enumeration would also be the thing that drifts: ``reasoning`` is
        free text under no schema constraint, and a record gaining a field would
        gain an unscanned channel with it.

        The residual, stated: a credential spelled with JSON ``\\uNNNN`` escapes
        sits in the parsed value and not in the bytes, so this misses it. It is
        not reachable through the record ``draft`` writes -- ``json.dumps``
        escapes non-ASCII and never ASCII, and a credential is ASCII -- so
        reaching it takes a hand-edited evidence file written to hide one, which
        is the adversary :mod:`theurian.security.content_secrets` already
        disclaims completeness against.

        **A present record that cannot be read refuses under ``block`` and is
        skipped under ``warn``**, which is the one place the two postures differ
        here. ``block`` promises that nothing it cannot clear gets past, and a
        channel it cannot read is a channel it cannot clear -- a 9 MiB or
        symlinked ``evidence.json`` would otherwise be a one-line bypass of the
        control. ``warn`` proceeds even when a credential *is* found, so
        proceeding when one merely could not be looked for takes nothing away
        that ``warn`` was offering. ``off`` never reaches this method.

        **A ``--local`` proposal is scanned the same way, though its record
        travels nowhere** (ADR-0028: the directory is git-ignored, and
        ``_LOCAL_ACCEPT_FIRST_STEP`` says so). Git-ignored keeps the bytes out of
        a *commit* and not off the disk -- which is exactly why
        :meth:`_secret_refusal`'s rotation advice is already unconditional -- and
        a control whose population depends on a flag is a second path to get
        wrong for a case where the value is live either way.

        Absent is not a failure: ``draft`` writes the body, then the evidence,
        then the migration, so an interrupted draft legitimately has none
        (:meth:`_read_evidence_record` records that split).
        """
        evidence = location.directory / EVIDENCE_FILE
        if not evidence.exists() and not evidence.is_symlink():
            return
        try:
            data = self._read_within_project(evidence)
        except (OSError, TheurianError) as exc:
            # CP-2's sixth translation site. This method runs outside `accept`'s
            # examination clause -- the scan deliberately sits there, so a fault
            # in `config.yaml` is not reported as an unreadable proposal -- and an
            # untranslated `OSError` from here would escape `accept` raw and
            # publish no `{error, remedy}` under `--json` (#227).
            if policy is SecretScanPolicy.BLOCK:
                raise self._evidence_unscannable(location, exc) from exc
            return
        # `errors="replace"`, the same reasoning as the body channel: an
        # undecodable evidence file is a fault `_read_evidence_record` reports
        # with a better message than this scan could, and replacement leaves
        # every ASCII run -- which is what a credential is -- intact.
        yield _AT_EVIDENCE, data.decode("utf-8", errors="replace")

    def _evidence_unscannable(
        self, location: _ProposalLocation, error: BaseException
    ) -> ProposalError:
        """``block`` could not read the evidence record, so it cannot clear it.

        The reason is derived from the *type* of the failure and never from
        ``str(exc)``, whose text carries the absolute filename and with it the
        machine's home directory -- the discipline :meth:`_evidence_indeterminate`
        records, and the table it reads is shared with it so a new failure mode
        cannot fall through to an answer in one and not the other.
        """
        return ProposalError(
            f"{location.relative}/{EVIDENCE_FILE} is present but could not be read "
            f"({_evidence_failure_reason(error)}), so the secret scan cannot clear it -- and "
            "accepting this would tell you to commit that directory.",
            remedy=(
                f"Make {location.relative}/{EVIDENCE_FILE} readable and no larger than the "
                "source-file cap, then accept it again. If the record is not recoverable, "
                "delete it -- an absent one is a legacy or interrupted draft and is allowed. "
                "To accept without scanning it, set security.secretScan to warn or off in "
                ".theurian/config.yaml (block, warn, off; block is what an absent key selects)."
            ),
        )

    def _secret_refusal(
        self, location: _ProposalLocation, findings: tuple[ProposalSecretFinding, ...]
    ) -> ProposalError:
        """The refusal for a proposal that appears to carry a secret.

        The listing is bounded by :data:`_MAX_NAMES_LISTED` for the reason that
        constant already records: the count is the contributor's, not ours. The
        detector bounds it again at its own :data:`~theurian.security
        .content_secrets.MAX_FINDINGS`, so this is the second of two ceilings
        rather than the only one. **``findings`` is every channel's together** --
        the bodies, the document's fields, the migration's bytes, its filename,
        each landed path and the evidence record -- so the cap and its deliberate
        silence about what it dropped apply once, to the whole list. Capped per
        channel it would publish six times the cap and say which channels the
        proposal leaked on, which is the contributor's count again in a different
        shape.

        **The sentence names no destination**, because the six channels no longer
        share one. Five are artifacts the acceptance lands; the evidence record is
        one it leaves for the pull request instead (#361), and for a ``--local``
        proposal that record travels nowhere at all. "In what it would land" was
        true of five of six and is the kind of sentence a reader checks against
        the finding's own location, which says which channel it was.

        **The remedy says to rotate before it says how to proceed.** A secret
        that reached a proposal directory is in a Git working tree and, if the
        proposal was committed, in history -- so telling the author to delete
        the line and carry on would be advice that leaves the credential live.
        That holds for a ``--local`` proposal too: the directory is git-ignored,
        which keeps the bytes out of a *commit* and not off the disk, so the
        rotation advice is unconditional and only the path named changes.
        The escape hatch for a false positive is named second and names the key,
        because ``block`` is the default and a false positive is otherwise a
        dead end.
        """
        listed = [finding.describe() for finding in findings[:_MAX_NAMES_LISTED]]
        return ProposalError(
            f"This proposal appears to carry a secret: {_names(listed)}. Nothing has moved.",
            remedy=(
                "Treat the value as exposed and rotate it -- it is already in a working tree, "
                "and in Git history if the proposal has been committed. Then remove it from the "
                f"proposal in {location.relative}/ and accept it "
                "again. If it is not a secret, set security.secretScan to warn or off in "
                ".theurian/config.yaml (block, warn, off; block is what an absent key selects)."
            ),
        )

    # -- the pre-check (ADR-0027 decision 2) -------------------------------

    def _refuse_unless_the_union_applies(
        self,
        location: _ProposalLocation,
        migration_file: Path,
        migration_bytes: bytes,
        document: Mapping[str, object],
        moves: tuple[_BodyMove, ...],
    ) -> None:
        """Refuse unless the landed set *with this proposal in it* still applies.

        The four stages ADR-0027 decision 2 names, in its order:

        1. **Schema and document limits**, against the proposal's own document --
           the same :data:`MigrationDocumentValidator` ``draft`` calls, so a
           proposal this build generated cannot fail it and a hand-authored one
           is refused naming its own file. It runs here rather than only inside
           the replay because the replay's loader would name the copy's path.
        2. **Self-consistency of the incoming proposal** -- the digest
           verification the loader performs when it re-reads a referenced body,
           and the body-sharing guard over the incoming operations together with
           the landed set.
        3. **The whole-set guards** ``migrate validate`` runs.
        4. **A dry replay** of the landed set and the proposal together, against
           a throwaway target.

        Stages 2 to 4 are the injected :data:`MigrationSetRehearsal`, and they
        are not run here in three steps because they are not three passes: the
        rehearsal loads the copy once, which *is* the digest verification, and
        hands the one loaded set to the guards and then to the engine. Splitting
        them apart here would mean parsing the set a second time -- the second
        implementation the ADR's hard condition forbids.

        **Why stage 4 is not optional**, measured on #316: two proposals drafted
        before either acceptance both claim their item's first revision, and the
        pair is schema-valid, passes every statically decidable guard, exits 0 on
        ``migrate validate`` -- and can never be applied. Stages 1 to 3 pass it.
        The replay is what refuses it, and it refuses the invariants nobody has
        written a guard for yet by construction rather than one at a time.

        Nothing is consumed on any path through this method: it reads the
        project and writes only inside the rehearsal's own throwaway target.

        Raises:
            ProposalError: If any stage refuses. The remedy separates the two
                fault directions -- this proposal's own migration, or a landed
                one that was already broken before it -- because they need
                different actions from the reader and only one of them is the
                author's to fix.
        """
        self._refuse_a_document_the_schema_rejects(migration_file, document)

        try:
            landed = self._landed_files()
        except (ProposalError, SchemaUnreadableError):
            raise
        except (TheurianError, OSError) as exc:
            # The approved set cannot even be enumerated, which is a fault in
            # `.theurian/migrations/` and never in a proposal that has not moved.
            # It does not reach here from the CLI -- resolving the project loads
            # the same set and fails first -- but a composition root that
            # enumerates lazily can, and "unreadable landed migration" must not
            # arrive as "your proposal is broken" (#227).
            raise self._landed_set_refusal(exc) from exc

        candidate = self._candidate(landed, migration_file, migration_bytes, moves)
        try:
            self._rehearse(candidate)
        except SchemaUnreadableError:
            # The installation's schema, not this project's content: it says
            # "reinstall theurian", and re-labelling it as a fault in the
            # proposal would send the author to edit a file that is correct.
            raise
        except (TheurianError, OSError) as exc:
            raise self._union_refusal(location, migration_file, landed, exc) from exc

    def _refuse_a_document_the_schema_rejects(
        self, migration_file: Path, document: Mapping[str, object]
    ) -> None:
        """Stage 1: the published schema, then the version this build understands.

        The ``apiVersion`` check is not the schema's ``const`` written twice. The
        schema is a *file*, located at runtime (``cli/context.py::schema_root``,
        which prefers the packaged copy but falls back to a source checkout's),
        while :data:`MIGRATION_API_VERSION` is compiled into this build. They can
        disagree, and when they do it is the build's answer that decides what the
        loader will accept -- which is why the loader checks both too, and why
        the same pair is checked here rather than assuming the file agrees.
        """
        try:
            self._validate(document)
        except SchemaUnreadableError:
            raise
        except TheurianError as exc:
            raise ProposalError(
                f"{_names([migration_file.name])} is not a valid migration: {_their_words(exc)}",
                remedy=(
                    "Correct the migration in the proposal directory, then accept it again. "
                    "Nothing has moved."
                ),
            ) from exc
        if document.get("apiVersion") != MIGRATION_API_VERSION:
            raise ProposalError(
                f"{_names([migration_file.name])} declares an apiVersion this build does not "
                f"understand; it reads {MIGRATION_API_VERSION!r}.",
                remedy=(
                    "Correct apiVersion in the migration, or install the Theurian build that "
                    "wrote it. Nothing has moved."
                ),
            )

    def _landed_files(self) -> tuple[str, ...]:
        """Every project-relative file the approved migration set was read from.

        Each landed migration's own file, and the body each ``upsertRevision``
        resolved to -- the paths the *loader* recorded, so the copy the replay
        reads holds exactly the files the loader would read here, not whatever a
        second enumeration of ``.theurian/migrations/`` would find (the
        disagreement #234 measured on the sibling guard).

        Sorted and deduplicated: two operations legitimately naming one body
        contribute one file, and a total order keeps the copy identical run to
        run.
        """
        files: set[str] = set()
        for migration in self._landed_migrations():
            files.add(self._staged_path(migration.source_path))
            for operation in migration.operations:
                if isinstance(operation, UpsertRevision):
                    files.add(self._staged_path(operation.resolved_content_path))
        return tuple(sorted(files))

    def _staged_path(self, recorded: str | None) -> str:
        """One loader-recorded project-relative path, refusing an absent one.

        ``None`` is the in-memory case the loader never produces (it records
        ``source_path`` for every migration it reads and ``resolved_content_path``
        for every body it resolves). Refused rather than skipped: a skip would
        leave that migration or body out of the copy, and the replay would then
        answer "it applies" about a set that is not the one on disk.
        """
        if recorded is None:  # pragma: no cover - the loader records both
            raise ProposalError(
                "The approved migration set holds a migration with no recorded source file, "
                "so whether this proposal can be accepted cannot be decided.",
                remedy="Run theurian migrate validate --json and fix what it reports.",
            )
        return recorded

    def _candidate(
        self,
        landed: tuple[str, ...],
        migration_file: Path,
        migration_bytes: bytes,
        moves: tuple[_BodyMove, ...],
    ) -> CandidateMigrationSet:
        """The union to rehearse: the landed files, plus this proposal's own.

        The proposal's files are described by the destinations they *would*
        occupy, not by where they sit now, because that is the tree the pre-check
        is asking about. Their bytes are the ones :meth:`accept` already read
        through the security layer -- re-reading them here would ask a second
        question about a file that has already answered.
        """
        destination = self._paths.migrations / migration_file.name
        incoming = [(self._within_project(destination), migration_bytes)]
        incoming.extend((self._within_project(move.destination), move.data) for move in moves)
        return CandidateMigrationSet(
            root=self._paths.root,
            knowledge_directory=self._knowledge_directory(),
            project_id=self._project_id,
            landed=landed,
            incoming=tuple(incoming),
        )

    def _knowledge_directory(self) -> PurePosixPath:
        """Where ``.theurian/`` sits, relative to the project root.

        The whole relative path and not ``knowledge_dir.name``: a project
        registered with a *nested* knowledge directory would lay its files out
        under both segments, and a copy that kept only the leaf would put the
        migrations somewhere the loader then reports as an empty set -- a replay
        that passes because it read nothing.
        """
        return PurePosixPath(self._within_project(self._paths.knowledge_dir))

    def _within_project(self, path: Path) -> str:
        """``path`` as a project-relative POSIX string, for the copy's layout."""
        try:
            return path.relative_to(self._paths.root).as_posix()
        except ValueError as exc:  # pragma: no cover - every path here was contained above
            raise PathEscapeError(str(path), str(self._paths.root)) from exc

    def _union_refusal(
        self,
        location: _ProposalLocation,
        migration_file: Path,
        landed: tuple[str, ...],
        error: BaseException,
    ) -> ProposalError:
        """Turn a refused rehearsal into a refusal a reader can act on.

        Two directions, and which one it is decides the whole remedy. If the
        landed set fails the same replay *without* this proposal in it, the
        project was already broken and the author has nothing to correct; if it
        passes, this proposal is what breaks it. The discriminator is a second
        rehearsal of the landed set alone, and it costs nothing on the path that
        matters: it runs only when the acceptance is already being refused.
        """
        landed_error = self._landed_set_alone_fails(landed)
        if landed_error is not None:
            return self._landed_set_refusal(landed_error)
        message = (
            f"Accepting this proposal would leave .theurian/migrations/ unable to apply: "
            f"{_their_words(error)}"
        )
        if isinstance(error, RevisionConflictError):
            # The racing face (#307): this proposal claimed a revision that was
            # free when it was drafted and is not free now. Re-drafting is the
            # honest cure *here* and nowhere else on this path -- nothing of this
            # proposal was consumed, so a second draft duplicates nothing (#89).
            return ProposalError(
                message,
                remedy=(
                    "Another change to this item landed first. Nothing has moved, so either "
                    "give the proposal's own migration the expectedRevision the message "
                    "reports, or draft the change again with --expected-revision and delete "
                    f"{location.relative}/."
                ),
            )
        return ProposalError(
            message,
            remedy=(
                f"Correct {_names([migration_file.name])} in {location.relative}/, then accept "
                "it again. Nothing has moved and the proposal's own files are intact."
            ),
        )

    def _landed_set_refusal(self, error: BaseException) -> ApprovedSetUnusableError:
        """The refusal for a fault that predates this proposal.

        Direction (b): the project's own ``.theurian/migrations/`` is what
        cannot be applied, and the author of this proposal has nothing to
        correct in it. The remedy therefore names neither the proposal's file
        nor a re-draft -- both would send the reader to change something that is
        not wrong -- and points at the directory the fault is in.

        It points at the *directory* and not at a file, which is weaker than it
        looks like it should be and is the strongest thing that is true: what
        the underlying message identifies depends on which stage refused. The
        loader names a migration file; a revision conflict names an *item* and
        two revision ids, because the engine does not know which of the two
        migrations claiming that item is the wrong one. "Read what the message
        names, in ``.theurian/migrations/``" holds for both; "fix the migration
        the message names" was false for the second, and a remedy that is false
        for a case is worse than one that is general.
        """
        return ApprovedSetUnusableError(
            f"The project's migration set cannot be applied as it stands, with or without "
            f"this proposal: {_their_words(error)}",
            remedy=(
                "This proposal is not the cause: nothing has moved and its directory is "
                "intact. The fault is in .theurian/migrations/ -- read what the message "
                "names there and correct it, then accept this proposal again. "
                "theurian migrate apply runs the same pipeline over the same set."
            ),
        )

    def _landed_set_alone_fails(self, landed: tuple[str, ...]) -> BaseException | None:
        """The fault the landed set carries on its own, or ``None`` if it carries none.

        The same rehearsal, over the same files, with the incoming proposal left
        out -- so the two answers cannot differ for any reason except the
        proposal itself. An empty set is not rehearsed: there is nothing for it
        to fail at, and creating a store to prove it would be work spent on a
        foregone answer.
        """
        if not landed:
            return None
        try:
            self._rehearse(
                CandidateMigrationSet(
                    root=self._paths.root,
                    knowledge_directory=self._knowledge_directory(),
                    project_id=self._project_id,
                    landed=landed,
                    incoming=(),
                )
            )
        except SchemaUnreadableError:
            raise
        except (TheurianError, OSError) as exc:
            return exc
        return None

    def _require_directory(self, proposal_id: ProposalId) -> _ProposalLocation:
        """Find the one directory this id names, across both locations (ADR-0028).

        The two candidates are probed for *presence* first and graded second,
        and that order is what makes the ambiguity refusal real. Grading one
        location and falling through to the other on failure would resolve by
        precedence -- a symlinked tracked proposal would be answered by a local
        one of the same id, which is exactly the silent choice between two
        directories that may hold different bytes the ADR refuses.

        Presence is ``lexists``: anything at the name, a broken symlink and a
        regular file included. A candidate that is present and not a real
        directory is *refused*, never skipped, for the same reason -- the reader
        is told what is in the way rather than sent to look for a proposal the
        lookup silently declined to read.
        """
        # Built from a validated ULID, so no caller-supplied text reaches either
        # path: `ProposalId` cannot spell a separator, let alone a traversal.
        # But the name being safe says nothing about what it resolves *to*: a
        # committed proposal directory that is itself a symlink to somewhere out
        # of the project would pull that target's `*.yaml` into the accept path.
        # `is_dir()` follows the link, so it is checked separately, below.
        parents = ((self._paths.proposals, False), (self._paths.proposals_local, True))
        candidates = tuple(
            _ProposalLocation(
                directory=parent / proposal_id.value,
                relative=self._within_project(parent / proposal_id.value),
                local=local,
            )
            for parent, local in parents
        )
        present = [
            candidate
            for candidate in candidates
            # `exists(follow_symlinks=False)`, so a dangling symlink counts as
            # present: it is something in the way of this id, and answering "no
            # such proposal" for it would send the author to draft a duplicate.
            if candidate.directory.exists(follow_symlinks=False)
        ]
        if len(present) > 1:
            raise self._ambiguous_locations(proposal_id, present)
        if not present:
            # "or" states where it was not found; "and" is what the reader has to
            # do about it. A remedy saying "list A or B" is an instruction that
            # can be followed correctly and still miss the proposal, because
            # which of the two holds it is exactly what is not known here.
            searched = [f"{self._within_project(parent)}/" for parent, _ in parents]
            raise ProposalError(
                f"No proposal {proposal_id.value} under {' or '.join(searched)}.",
                remedy=f"List {' and '.join(searched)} to see which proposals are waiting.",
            )
        location = present[0]
        if location.directory.is_symlink():
            raise ProposalError(
                f"Proposal {proposal_id.value} is a symlink, not a directory.",
                remedy=f"A proposal is a real directory at {location.relative}. "
                "Remove the link and put the directory itself there.",
            )
        if not location.directory.is_dir():
            raise ProposalError(
                f"{location.relative} is not a directory, so it cannot be a proposal.",
                remedy=f"Remove what is at {location.relative} and put the proposal directory "
                "itself there.",
            )
        return location

    def _ambiguous_locations(
        self, proposal_id: ProposalId, present: Sequence[_ProposalLocation]
    ) -> ProposalError:
        """Refuse an id that names a directory in both locations (ADR-0028).

        Never resolved by precedence. The two directories are independent trees
        that can hold different migrations for the same proposal id, and a
        silent pick would accept one of them while the author was looking at the
        other -- with the loser's body left behind and nothing said. Both paths
        are named so the reader can compare them before deleting either.
        """
        named = " and ".join(f"{location.relative}/" for location in present)
        return ProposalError(
            f"Proposal {proposal_id.value} is in two places at once: {named}. They can hold "
            "different changes, so Theurian will not choose between them.",
            remedy=f"Compare {named}, delete the one that is not the change you mean to accept, "
            "then run theurian propose accept again.",
        )

    def _require_migration(self, location: _ProposalLocation, proposal_id: ProposalId) -> Path:
        # The migration is identified by its `<ulid>-<slug>.yaml` name, not by
        # any `*.yaml`: a YAML or YML *body* is a `*.yaml` too, so matching the
        # suffix counted the body as a second migration and a YAML-bodied
        # proposal could never be accepted ("holds two or more migration
        # files"). Body files also mirror their `knowledge/` sub-path and so sit
        # in a subdirectory, while the migration is always at the top level --
        # another reason a top-level, name-matched lookup finds exactly the
        # migration. The name pattern is anchored on a ULID, so listing the
        # directory and filtering by name selects exactly what `glob("*.yaml")`
        # used to.
        #
        # `iterdir()` and not `glob()`: `Path.glob` runs its `scandir` inside a
        # `try` that swallows every `OSError`, so a proposal directory this
        # process cannot read yielded *nothing* and the migration sitting in it
        # read as absent -- the silent false negative #214 fixed on the loader's
        # own enumeration, here handing an unreadable directory to the
        # already-accepted diagnosis below. `iterdir()` raises instead, and
        # `accept` translates it (#227).
        directory = location.directory
        entries = sorted(path for path in directory.iterdir() if is_migration_file_name(path.name))
        # `is_file()` follows a symlink, so a name-matching link to an
        # out-of-project file would otherwise count as the migration and have its
        # target's bytes read into a tracked file. It is rejected by name rather
        # than filtered, so the reader is told about the link, not sent to draft
        # again over a "missing" migration.
        symlinked = [path.name for path in entries if path.is_symlink()]
        if symlinked:
            raise ProposalError(
                f"Proposal {proposal_id.value} holds a symlinked migration file: "
                f"{_names(symlinked)}.",
                remedy="A proposal's migration is a real file. Remove the link and put "
                "the migration itself there.",
            )
        candidates = [path for path in entries if path.is_file()]
        if not candidates:
            raise self._no_migration_error(location, proposal_id)
        if len(candidates) > 1:
            raise ProposalError(
                f"Proposal {proposal_id.value} holds two or more migration files: "
                f"{_names([path.name for path in candidates])}.",
                remedy="One proposal is one change. Split them, or delete the extra file.",
            )
        return candidates[0]

    def _no_migration_error(
        self, location: _ProposalLocation, proposal_id: ProposalId
    ) -> ProposalError:
        """Diagnose a proposal directory that holds no migration file.

        The question is whether this proposal has already been accepted, and the
        two answers have opposite remedies: re-drafting an accepted proposal
        mints a second migration for a change already in history (#89), while
        telling the author of an interrupted draft that no action is needed
        discards work that exists nowhere else (#253).

        **This is a best-effort diagnosis over untrusted input, not a
        tamper-proof fact.** A proposal directory is committed and arrives through
        a contributor's pull request (ADR-0013 point 7), so everything read here
        -- ``evidence.json`` included -- is contributor-controlled. A migration id
        recorded in ``evidence.json`` is a *claim*, not proof: removing it moves
        the answer, and it can name another proposal's landed migration (a forge).
        Two things keep the diagnosis safe despite that:

        * **A recorded id is cross-checked by item id.** The landed migration it
          names must also operate on the item this proposal's own record declares
          (:meth:`_landed_migration_matching`), which reduces the forge to landing
          a migration for the same item -- indistinguishable from a genuine
          acceptance.
        * **Safety is by remedy, not by detection.** No branch here concludes with
          an unconditional "no action is needed" or "draft it again". Every answer
          that could be wrong sends the reader to ``.theurian/migrations/`` first,
          and no remedy instructs discarding work that might exist.

        A read failure is answered as *indeterminate*, never as an answer: a
        ``evidence.json`` present but unreadable is a different fact from an absent
        one, and concluding "accepted" or "draft again" from "could not read it"
        is how a permission slip or a tamper becomes a wrong verdict.
        """
        document = self._read_evidence_record(location, proposal_id)
        if document is None:
            # No evidence.json at all: a legacy proposal (the 26 committed before
            # the record existed), an accepted one whose evidence was removed, an
            # interrupted draft that never reached its evidence write, or a bare
            # directory. None carries a claim to check, so the answer is inferred
            # and points at the migration set first.
            return self._inferred_answer(location.directory, proposal_id)
        recorded = _migration_id_or_none(document.get("migrationId"))
        if recorded is None:
            # Present, readable evidence, but no usable migration id: a legacy
            # file (no such key) or a malformed value. No claim to check, so it is
            # inferred exactly like an absent record.
            return self._inferred_answer(location.directory, proposal_id)
        landed = self._landed_state(recorded, document)
        if landed is not None and landed.confirmed:
            return ProposalAlreadyAcceptedError(
                f"Proposal {proposal_id.value} appears to have been accepted: the migration it "
                f"records, {_names([landed.name])}, is in .theurian/migrations/ and "
                "operates on the item this proposal names.",
                remedy="Confirm that is the change you intended, then review it and open a pull "
                "request. If it is not, the record is stale -- read what is in "
                ".theurian/migrations/ before drafting again.",
            )
        if landed is not None:
            # The migration IS in place under the recorded id, so nothing here is
            # "nothing landed" -- returning exit 1 (whose contract is "re-draft")
            # would tell the author to mint a duplicate of a change on disk (#89,
            # adversarial M1). The item could not be cross-checked -- the record
            # names none, or names one this migration does not operate on -- so
            # this is read-before-acting, not an assertion that it is this change.
            return ProposalAlreadyAcceptedError(
                f"Proposal {proposal_id.value} records migration {recorded.value}, and a "
                f"migration with that id, {_names([landed.name])}, is in "
                ".theurian/migrations/ -- but this proposal's record does not name an item that "
                "migration operates on, so it cannot confirm that is the same change.",
                remedy="Read the migration under that id in .theurian/migrations/. If it is this "
                "change, review it and open a pull request. If it is not, this proposal's record "
                "is wrong -- correct evidence.json rather than re-drafting, which would mint a "
                "second migration.",
            )
        return ProposalError(
            f"Proposal {proposal_id.value} records migration {recorded.value}, but no migration "
            "with that id is in .theurian/migrations/, and no file named "
            f"{recorded.value}-<slug>.yaml is in this proposal directory. Nothing it drafted has "
            "been accepted.",
            remedy="Look in .theurian/migrations/ for the migration this proposal drafted -- it "
            "may be present under a different name. If it is genuinely gone, draft the change "
            "again with theurian propose.",
        )

    def _inferred_answer(self, directory: Path, proposal_id: ProposalId) -> ProposalError:
        """The answer when no usable migration id is recorded.

        Reached for a proposal that records no ``migrationId`` -- the 26 committed
        before the field existed (2026-08-20 at ``9873272``), one whose
        ``evidence.json`` is absent, or one whose recorded id is malformed. There
        is no claim to check, so the answer is inferred from the directory and
        both branches point at the migration set first.

        The inference reads only files of the shape ``draft`` writes
        (:func:`is_generated_body_file_name`), which is what keeps a ``Thumbs.db``
        or a reviewer's notes from deciding it. It is best-effort over a
        contributor-controlled directory, wrong in *both* directions and safe only
        by remedy:

        * a generated-shape body hand-planted in an accepted directory reads as
          unfinished; and
        * an interrupted draft whose body was hand-authored under a non-generated
          name -- a hand-written ``contentFile`` bypasses ``body_relative_path``
          -- leaves no generated-shape body and reads as accepted, though nothing
          landed.

        Neither misreads into a lost change: the unfinished branch checks the
        migration set before re-drafting, and the accepted branch never says an
        unconditional "no action is needed".
        """
        unmoved = _unmoved_generated_bodies(directory, proposal_id)
        if unmoved:
            return ProposalError(
                f"Proposal {proposal_id.value} looks unfinished: it still holds the body file "
                f"{_names(unmoved)} but no migration file, and records no migration id to check "
                "against .theurian/migrations/.",
                remedy="Look in .theurian/migrations/ for a migration naming that body. If it is "
                "there, this proposal was accepted -- review it and open a pull request. If it is "
                "not, nothing landed: draft the change again with theurian propose.",
            )
        return ProposalAlreadyAcceptedError(
            f"Proposal {proposal_id.value} appears to have been accepted, but this is inferred, "
            f"not proven: no drafted body file is left beside its {EVIDENCE_FILE} and it records "
            "no migration id, so the directory cannot say for certain.",
            remedy="Confirm the migration this proposal drafted is in .theurian/migrations/. If "
            "it is, no further action is needed beyond reviewing it and opening a pull request. "
            "If it is not, nothing landed -- draft the change again with theurian propose.",
        )

    def _read_evidence_record(
        self, location: _ProposalLocation, proposal_id: ProposalId
    ) -> Mapping[str, object] | None:
        """Parse ``evidence.json``, or ``None`` if it is genuinely absent.

        The split that matters, and the one #253's third round named: *absent* and
        *present-but-unreadable* are different facts. An absent record is a legacy
        or interrupted proposal and is answered by inference; a present record
        that cannot be read is answered as indeterminate, because concluding
        anything from "could not read it" is exactly the collapse that let a
        permission slip read as an acceptance. ``draft`` writes the body, then the
        evidence, then the migration, so an interruption can leave no evidence at
        all -- the absent case is real.

        Every failure ``_read_within_project`` and ``json.loads`` can raise is
        caught and turned into indeterminate: an ``OSError`` (a directory named
        ``evidence.json``, an unreadable one), a decode or JSON error, a
        ``RecursionError`` from a deeply nested document, and the ``TheurianError``
        family the security layer raises -- a symlinked ``evidence.json`` (T-5),
        one over SEC-8's size cap, one whose path escapes the root, and one that
        is not a regular file at all: a FIFO, a socket or a device, whose
        ``st_size`` bounds nothing (#215). Catching them is only half of it --
        each also needs its own row in :data:`_EVIDENCE_FAILURE_REASONS`, whose
        security-layer rows track ``read_source_file``'s ``Raises`` one for one,
        because that table's fallthrough asserts the document parsed. None may
        fall through to an answer.
        """
        evidence = location.directory / EVIDENCE_FILE
        if not evidence.exists() and not evidence.is_symlink():
            return None
        try:
            document = json.loads(self._read_within_project(evidence))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError, TheurianError) as exc:
            raise self._evidence_indeterminate(proposal_id, location, exc) from exc
        if isinstance(document, Mapping):
            return document
        # Present and parseable, but not an object: it records no fields at all,
        # so it cannot prove acceptance. Indeterminate rather than "no record",
        # which would drop to inference and could conclude accepted.
        raise self._evidence_indeterminate(proposal_id, location, None)

    def _evidence_indeterminate(
        self, proposal_id: ProposalId, location: _ProposalLocation, error: BaseException | None
    ) -> ProposalError:
        """An indeterminate verdict: the record is present but could not be read.

        The reason is derived from the *type* of failure, never from ``str(exc)``:
        an ``OSError``'s text carries the absolute path, which is the machine's
        home directory and not the proposal's, and interpolating it here would
        leak it (the same reason :func:`_within` exists). A category reason says
        enough without that.
        """
        return ProposalError(
            f"Proposal {proposal_id.value} could not be examined: its {EVIDENCE_FILE} is present "
            f"but could not be read ({_evidence_failure_reason(error)}), so whether it has been "
            "accepted cannot be answered.",
            remedy=f"Make {location.relative}/{EVIDENCE_FILE} readable and "
            "well-formed, then run theurian propose accept again. If it cannot be recovered, look "
            "in .theurian/migrations/ for the migration this proposal drafted before re-drafting.",
        )

    def _unreadable(self, proposal_id: ProposalId, error: OSError) -> ProposalError:
        """The answer when the filesystem refuses a probe or a read on the accept path.

        Raised from the single clause in :meth:`accept` that spans the
        examination phase, so *which* raw call the mode happens to select does
        not change the answer the caller gets.

        The reason is ``strerror`` -- the OS's own category for the failure --
        and never ``str(exc)``, whose text carries the absolute filename and with
        it the developer's home directory. :meth:`_evidence_indeterminate` records
        that discipline; :func:`_project_relative` is what names the file without
        it, and :func:`_names` quotes the result, because a proposal directory's
        filenames are the contributor's (ADR-0013 point 7).

        **The remedy is chosen by errno, never a blanket ``chmod``.** The failure
        the examination phase reports is not always a permission one, and not
        every permission one is cured on the path the ``OSError`` names -- the
        same over-claim ``c7cf455`` corrected for :class:`PathEscapeError`,
        reopened here:

        * On ``EACCES``/``EPERM`` the cure is a permission change, but a
          ``stat``/``open`` refused for a *child* is the parent lacking its search
          bit, not the child lacking read -- ``chmod u+rX`` on the child would be a
          cure for the wrong file. :meth:`_permission_remedy` points ``chmod u+x``
          at the unsearchable directory when the parent is the one refused, and
          ``chmod u+rX`` at the named path otherwise.
        * On any other errno -- ``EISDIR`` (a ``contentFile`` naming a directory),
          ``ENOTDIR``, ``ENAMETOOLONG``, ``ELOOP`` -- no ``chmod`` cures it: the
          cause is the proposal's own input, so the remedy names the
          ``contentFile`` to correct and says nothing about permissions.

        **The remedy never sends the reader to draft again.** A read the
        filesystem refused says nothing about whether this proposal's migration
        has already landed -- the two facts are unrelated -- and re-drafting an
        accepted proposal mints a second migration for a change already in
        history (#89). So both branches point at ``.theurian/migrations/`` first,
        exactly as the indeterminate-evidence remedy above does. What they *can*
        state outright is that nothing moved: every write on this path is in
        :meth:`_commit`, which the clause in :meth:`accept` deliberately excludes.
        """
        named = _names([_project_relative(error.filename, self._paths.root)])
        return ProposalError(
            f"Proposal {proposal_id.value} could not be examined: "
            f"{error.strerror or 'it could not be read'} at {named}. Nothing has been "
            "moved, and whether it can be accepted cannot be answered without reading it.",
            remedy=self._read_failure_remedy(error, named),
        )

    #: What every read-failure remedy ends with, whatever cured the read: the
    #: refused read is not evidence that nothing landed, so the reader is sent to
    #: ``.theurian/migrations/`` before any re-draft (#89), never told to draft
    #: again outright.
    _MIGRATIONS_TAIL: Final = (
        " If it cannot be recovered, look in .theurian/migrations/ for the migration this "
        "proposal drafted before re-drafting: a refused read is not evidence that nothing landed."
    )

    def _read_failure_remedy(self, error: OSError, named: str) -> str:
        """The cure for one accept-path read failure, chosen by its errno.

        Permission failures earn a ``chmod``; everything else earns a neutral
        remedy that names the input to correct, because ``chmod`` cures none of
        ``EISDIR``/``ENOTDIR``/``ENAMETOOLONG``/``ELOOP``. A ``None`` errno is
        treated as non-permission: a ``chmod`` prescribed for an unknown cause is
        the over-claim this method exists to avoid.
        """
        if error.errno in _PERMISSION_ERRNOS:
            return f"{self._permission_remedy(error, named)}{self._MIGRATIONS_TAIL}"
        return (
            f"The migration names a contentFile the filesystem cannot read as a file ({named}); "
            "no permission change cures that. Correct the contentFile the migration names, then "
            f"run theurian propose accept again.{self._MIGRATIONS_TAIL}"
        )

    def _permission_remedy(self, error: OSError, named: str) -> str:
        """The ``chmod`` for a permission failure, pointed at the path truly at fault.

        A refused ``stat``/``open`` of a *child* is the parent directory lacking
        its search (execute) bit, not the child lacking read: the child is
        unreachable, so ``chmod u+rX`` on it is a cure for a file the reader
        cannot even name yet. When the named path's parent is the unsearchable
        one, the cure is ``chmod u+x`` on that directory; otherwise the named path
        itself is read-refused and takes ``chmod u+rX``.
        """
        filename = error.filename
        if isinstance(filename, str):
            parent = Path(filename).parent
            if parent != Path(filename) and not os.access(parent, os.X_OK):
                named_parent = _names([_project_relative(str(parent), self._paths.root)])
                return (
                    f"Make {named_parent} searchable -- chmod u+x on it -- then run theurian "
                    "propose accept again."
                )
        return (
            f"Make {named} readable -- chmod u+rX on it -- then run theurian propose accept again."
        )

    def _landed_state(
        self, migration_id: MigrationId, evidence: Mapping[str, object]
    ) -> _LandedMigration | None:
        """What the project's approved migration set holds under the recorded id.

        **Closure (the class this closes).** This reads the single ``MigrationSet``
        ``resolve_context`` already loaded -- the same set ``migrate validate`` and
        ``migrate apply`` read, keyed by *inner* id in ``MigrationSet._by_id`` --
        through the injected :data:`LandedMigrationLookup`. So it cannot disagree
        with the loader about whether a migration with the recorded inner id is in
        place: ``absent`` is returned exactly when ``_by_id`` holds no migration
        with that id, and no filename shape, ULID prefix, or symlink can make one
        reader see landed while the other sees absent. A previous version had its
        *own* landed-detector -- a ``self._paths.migrations`` filename glob keyed on
        the id's ULID *prefix*, plus a symlink skip -- and it disagreed with the
        loader twice: a symlinked landed migration read as absent (round five), and
        a landed migration renamed off its ULID prefix read as absent (round six),
        each an exit-1 "nothing landed" over a change on disk, each a duplicate-mint
        (#89).

        **The class is the accept-path procedures that judge whether a migration
        is landed, and all three now consult the loaded set.**
        :meth:`_destination_backs_a_landed_revision` was the second: it
        re-detected landed migrations from the filesystem (a ``glob("*.yaml")``
        with a symlink skip), and reproduced round five's disagreement on the
        replacement guard rather than on this diagnosis: a landed migration behind
        a symlink held a body the guard could not see, so a replacement was
        allowed and the set stopped loading (#234). It reads the same loaded set
        through :data:`LandedMigrations` as of that fix, and keys the comparison
        on the loader's ``content_identity`` -- ``(st_dev, st_ino)`` -- so no
        spelling of a body path, case or NFC/NFD included, can make the guard and
        the loader disagree about which file a revision reads (#210, #227).
        :meth:`_refuse_if_migration_present` was the third and last: it keyed the
        "already in place" refusal on the destination *filename* alone, so a
        same-id different-slug proposal collided on the loader's inner id while
        its name was free and landed a duplicate migration id (p15). It consults
        the loaded set through :data:`LandedMigrationLookup` as of this fix. No
        method on the accept path enumerates ``.theurian/migrations/`` any more.

        ``None`` when nothing is filed under the id. Otherwise a
        :class:`_LandedMigration` whose ``confirmed`` says whether it also operates
        on the item this proposal's record names -- the cross-check that turns "a
        migration with this id exists" into "*this proposal's* change is in place",
        because a recorded id alone is a contributor claim (``evidence.json`` is
        committed input, ADR-0013 point 7). The items are read from the *loaded*
        migration's typed operations, which the loader already parsed through the
        symlink-escape guard, so no link is followed here; a landed migration of a
        shape a generated proposal does not produce degrades to ``present``
        ("cannot confirm"), which is safe -- never ``absent``.
        """
        migration = self._landed_migration(migration_id)
        if migration is None:
            return None
        name = Path(migration.source_path).name if migration.source_path else migration_id.value
        claimed = _evidence_item_ids(evidence)
        return _LandedMigration(name=name, confirmed=bool(claimed & _migration_item_ids(migration)))

    def _refuse_if_migration_present(
        self, destination: Path, document: Mapping[str, object]
    ) -> None:
        """Refuse a migration whose name *or* id the project already holds.

        **Closure (the class this closes).** The migration set keys by *inner*
        ``id`` (``MigrationSet._by_id``), so "already in place" has two faces and
        a filename check sees only one:

        * The **name** is taken: ``<id>-<slug>.yaml`` already exists in
          ``.theurian/migrations/`` (or is a symlink there). The name carries the
          id, so that very migration is in place under this name.
        * The **id** is landed under a *different* name: a hand-authored proposal
          named ``<id>-other-slug.yaml`` carrying ``id: <id>`` collides on the
          inner id while the destination name is free, so the filename check waved
          it through and ``accept`` landed a duplicate id -- ``migrate
          validate``/``status``/``apply`` then all exit 4 on "duplicate migration
          id" (reproduced end to end, p15).

        The id face is answered against the *loaded* ``MigrationSet`` through
        :data:`LandedMigrationLookup` -- the same set the loader,
        ``migrate validate`` and ``migrate apply`` read, keyed the same way -- so
        this cannot disagree with them about which ids are landed. This is the
        third and last accept-path procedure to be moved off a filesystem
        heuristic and onto the loaded set (#234/#253/#254 converted the sibling
        two, :meth:`_landed_state` and :meth:`_destination_backs_a_landed_revision`);
        the population is the accept-path procedures that judge whether a migration
        is landed, and all three now consult the loaded set. A malformed or absent
        inner ``id`` yields no lookup and falls to the name check plus
        :func:`_require_filename_matches_id`, which run either side of this.
        """
        if destination.exists() or destination.is_symlink():
            raise MigrationNameTakenError(
                f"{_names([destination.name])} is already in .theurian/migrations/. The name "
                "carries the migration's id, so that migration is already in place.",
                remedy=(
                    "Read the migration that is already there. If this proposal is a "
                    "different change, draft it again to mint a new migration id; if it "
                    "is the same one, delete the proposal directory."
                ),
            )
        migration_id = _migration_id_or_none(document.get("id"))
        if migration_id is None:
            return
        landed = self._landed_migration(migration_id)
        if landed is not None:
            landed_name = (
                Path(landed.source_path).name if landed.source_path else migration_id.value
            )
            raise MigrationNameTakenError(
                f"A migration with id {migration_id.value} is already in .theurian/migrations/ "
                f"as {_names([landed_name])}; that id is already in place, so accepting this "
                "would land a duplicate migration id the whole set then fails to validate on.",
                remedy=(
                    "Read the migration already filed under that id. If this proposal is a "
                    "different change, draft it again to mint a new migration id; if it is the "
                    "same one, delete the proposal directory."
                ),
            )

    def _body_moves(self, directory: Path, document: Mapping[str, object]) -> Iterable[_BodyMove]:
        """Pair each ``contentFile`` the migration names with the file to move.

        The destination is resolved against ``.theurian/migrations/`` because
        that is where the migration file will be once this call finishes -- the
        same resolution the loader performs -- and it is confined to
        ``.theurian/knowledge/``. The body is then found in the proposal
        directory at the **same sub-path** it will occupy under ``knowledge/``,
        not by its leaf name: two content files that differ only in namespace
        share a leaf, and a leaf lookup found one file for both.

        **A source is read through the security layer at most once, however
        many operations name it** (#306, SEC-8). Two operations that declare
        the same ``contentFile`` -- an in-place re-declare (ADR-0024 decision
        5) or a cross-item reuse that :meth:`_refuse_if_a_replacement_breaks_an_existing_pin`
        or the schema-and-replay check later refuses -- resolve to the same
        ``source`` file, so the second and every later one reuse the bytes the
        first read rather than reading them again: resident memory tracks the
        number of *distinct* bodies a proposal names, not the number of
        operations that name them. Every ``_BodyMove`` this yields still
        carries its own operation's ``revision_id``/``item_id``, because the
        guards downstream judge each declaration on its own, byte-shared or
        not.

        **Keyed on the source's ``(st_dev, st_ino)``, never the resolved path
        string.** A case-insensitive, case-preserving filesystem (APFS, NTFS)
        and Unicode normalization (NFC vs NFD) both reach one physical file by
        more than one spelling, and ``Path`` equality is a string comparison
        that does not fold either: two ``contentFile`` entries differing only
        in case (``X.md`` vs ``x.md``) resolve to two distinct path strings
        naming the *same* inode, so a string-keyed cache read it twice and
        held two resident copies of one file -- the identical class
        :meth:`_refuse_if_a_replacement_breaks_an_existing_pin` already keys
        on inode identity to avoid, for the same reason (verified on Darwin).
        ``source.stat()`` is called once per operation -- one extra syscall
        beyond the existence check above it -- and its result supplies both
        halves of the identity key, so no second ``stat`` is needed to read
        ``st_ino`` after ``st_dev``.
        """
        knowledge = self._paths.knowledge.resolve()
        read: dict[tuple[int, int], bytes] = {}
        for content_file, revision_id, item_id in _upsert_bodies(document):
            destination = self._destination_of(content_file)
            tail = destination.relative_to(knowledge)
            source = directory / tail
            if not source.exists():
                raise ProposalError(
                    f"The migration names {_names([content_file])}, but "
                    f"{_names([tail.as_posix()])} is not in the proposal directory.",
                    remedy="Restore the body file, or draft the proposal again.",
                )
            info = source.stat()
            identity = (info.st_dev, info.st_ino)
            data = read.get(identity)
            if data is None:
                data = self._read_within_project(source)
                read[identity] = data
            yield _BodyMove(
                source=source,
                destination=destination,
                data=data,
                replaced=destination.exists(),
                revision_id=revision_id,
                item_id=item_id,
            )

    def _read_within_project(self, path: Path) -> bytes:
        """Read one accept-path file, or refuse it.

        A regular file, inside the project root, under SEC-8's size cap, with no
        symlink *anywhere* in its chain below the root. :func:`read_source_file`
        enforces the size cap and rejects a symlink that escapes the root, but it
        *follows* an intermediate symlink that stays in-project -- and a proposal
        directory is a contributor's, so a namespaced body reached through an
        in-project directory symlink would read a file the proposal never
        authored. The chain is therefore walked and every symlink component is
        refused, so 'no symlink anywhere in its chain' is literally true, the
        same stance :meth:`_require_directory` takes on the proposal directory.

        The irregular-file refusal is re-raised with a ``referrer`` for the same
        reason ``migration_loader.py::_parse_upsert`` attaches one:
        :func:`read_source_file` names nothing, because its argument is the
        author's own ``contentFile`` string, so without this the user is told
        that *a* file is a FIFO and never which one. Reproduced through the real
        CLI as a ``propose accept --json`` payload naming no path at all.

        What is attached is ``path`` made project-relative -- and ``path`` is
        Theurian's own construction, never an authored string: a ULID proposal
        directory joined with either a name ``iterdir()`` returned, the constant
        ``evidence.json``, or the normalized ``knowledge/`` tail
        :meth:`_body_moves` obtained from :meth:`_destination_of`, which resolved
        it and proved containment first. It is the identical string
        :meth:`_reject_symlink_in_chain` already prints for this same file, so it
        opens no echo the accept path did not already have.
        """
        self._reject_symlink_in_chain(path)
        relative = PurePosixPath(path.relative_to(self._paths.root))
        try:
            return read_source_file(self._paths.root, relative)
        except IrregularSourceFileError as exc:
            raise IrregularSourceFileError(exc.shape, referrer=relative.as_posix()) from exc

    def _reject_symlink_in_chain(self, path: Path) -> None:
        """Refuse ``path`` if any component below the project root is a symlink.

        Only the portion below the root is walked: what sits above it (a
        ``/tmp`` that is itself a link on macOS, say) is the environment's, not
        the proposal's. A symlink component *inside* the proposal is never
        legitimate -- a committed proposal is real files and directories.
        """
        root = self._paths.root
        try:
            relative = path.relative_to(root)
        except ValueError:  # pragma: no cover - accept paths are built under the root
            raise PathEscapeError(str(path), str(root)) from None
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ProposalError(
                    f"{_names([current.relative_to(root).as_posix()])} is a symlink; a "
                    "proposal's files and directories must all be real.",
                    remedy="Remove the link and commit the real file or directory.",
                )

    def _refuse_if_a_replacement_breaks_an_existing_pin(self, moves: Iterable[_BodyMove]) -> None:
        """Refuse a body replacement that would break an existing landed pin.

        This guard holds one narrow invariant, not a global one: a replacement
        never breaks a pin **already landed** in the approved set. It is *not*
        the claim that ``accept`` leaves the set able to apply -- that claim is
        :meth:`_refuse_unless_the_union_applies`'s, which runs after this one and
        is what refuses a self-contained breakage in one proposal (two operations
        naming one ``contentFile``, a self-inconsistent pin, an empty
        ``contentFile``). Until ADR-0027 decision 2 nothing refused those at all:
        they landed here and were caught by ``migrate validate`` in CI, after the
        proposal that produced them had been consumed. What *this* method refuses
        is the one fault it can judge from the *landed* set alone, and it stays
        because it judges it more precisely than a replay can -- *the destination
        is a body a landed revision already reads*:

        * If that landed revision **pinned** the body, the loader re-reads it and
          finds bytes the pin no longer matches: *"hashes to abc7cdb70713 but the
          migration pins 4f9c5503e198"*, exit 4 with no undo.
        * Whether pinned or not, the destination now backs **two** distinct
          revisions -- the landed one and this proposal's -- and
          ``refuse_duplicate_content_files`` refuses one body file behind two
          revisions for the whole project (also exit 4).

        **Keyed on the destination's ``(st_dev, st_ino)``, never a path string.**
        A case-insensitive filesystem (APFS, NTFS) reaches one inode by many
        spellings, and ``Path.resolve()`` folds ``.``/``..``/symlinks but not case
        or NFC/NFD -- so a hand-authored ``contentFile`` differing only in case
        (``RETRY-POLICY...`` vs the landed ``retry-policy...``) resolved to a
        *different string* while landing on the *same file*, and a string key
        waved it through (the disclosure this re-key closes; the identical fix
        ``refuse_duplicate_content_files`` took for #210). ``move.replaced`` was
        set from ``destination.exists()``, so the file is there and the ``stat``
        resolves; a ``stat`` that fails anyway is an accept-path FS fault, left to
        :meth:`accept`'s examination-phase ``except OSError`` (this method runs
        inside it) rather than swallowed here.

        A generated proposal never reaches a refusal: its ``contentFile`` carries
        a fresh revision id, so it lands on no existing file. What reaches it is a
        hand-authored ``contentFile`` reusing an existing body's path, and
        refusing there breaks nothing legitimate -- the honest way to change a
        body is a new revision at a new path. The one landed reference that is
        *not* a break is this proposal's own revision re-declared **byte for
        byte** against its own body **on the same item** (an in-place status
        change, ADR-0024 decision 5): the item and revision ids do not move and,
        because a revision's content is immutable, the bytes do not either, so
        nothing changes. All three conjuncts are required. A re-declare that keeps
        the revision id but supplies *different* bytes overwrites the immutable
        body that id already froze; one that keeps the id and bytes but names a
        *different* item is a cross-item revision reuse ``migrate apply`` refuses
        -- so the skip is keyed on the quadruple (identity, equal item id, equal
        revision id, equal bytes), not on the id alone.
        """
        replaced = [move for move in moves if move.replaced]
        if not replaced:
            # Load the approved set only when a replacement is actually in hand:
            # a generated proposal lands on no existing file, and reading the set
            # for it would refuse an otherwise-valid accept whenever the set does
            # not load -- the O_EXCL race path, which writes a non-migration file
            # to the destination on purpose, is exactly that.
            return
        landed = tuple(self._landed_migrations())
        for move in replaced:
            stat = move.destination.stat()
            identity = (stat.st_dev, stat.st_ino)
            if not self._destination_backs_a_landed_revision(landed, identity, move):
                continue
            relative = move.destination.relative_to(self._paths.knowledge.resolve())
            raise ProposalError(
                f"Accepting this would overwrite {_names([relative.as_posix()])}, a body "
                "already backing a landed revision; the whole set would then fail to validate.",
                remedy="Draft this as a new revision -- a fresh contentFile -- rather than "
                "a replacement of an existing body.",
            )

    def _destination_backs_a_landed_revision(
        self,
        landed: Iterable[Migration],
        identity: tuple[int, int],
        move: _BodyMove,
    ) -> bool:
        """Does a landed ``upsertRevision`` already read ``identity`` in a way this move breaks?

        **Closure (the class this closes).** The migrations come from the
        project's *loaded* set, through the injected :data:`LandedMigrations` --
        the same ``MigrationSet`` ``migrate validate`` and ``migrate apply`` read,
        and the same one :meth:`_landed_state` is keyed into. So the guard and the
        loader cannot disagree about *which migrations are enumerated*: a landed
        migration relocated behind a symlink is followed by both, where a previous
        version globbed ``.theurian/migrations/*.yaml`` itself and skipped the
        link -- a pin it could not see, ``accept`` allowed the replacement, and
        the set stopped loading (#234, reproduced end to end). Enumerating the
        loaded set is the whole benefit; how each operation's body path is spelled
        is not, which is why the comparison is the loader's own
        ``content_identity`` and not a path string.

        The one landed reference that is *not* a break is this proposal's own
        revision re-declared **byte for byte** against its own body **on the same
        item** -- an in-place status change (ADR-0024 decision 5), which carries
        identical content because a revision's content is immutable. The skip
        therefore has three conjuncts, not one: the landed operation's item id
        equals this move's, its revision id equals this move's, *and* this move's
        bytes hash to what that operation reads.

        Each conjunct closes a demonstrated face. Keying on the id alone let a
        hand-authored proposal reuse a landed revision id while supplying
        *different* bytes, overwriting the pinned body that id already froze and
        leaving the set at exit 4 with no undo. Adding byte-identity closed that
        but not the *cross-item* face: a byte-identical body re-declared under a
        *different* item's id matches on revision id and bytes, so the two-conjunct
        skip fired and ``accept`` let it land -- ``migrate validate`` did not catch
        it (it does not check cross-item revision ownership) and ``migrate apply``
        then refused the whole set at exit 4 ("a revision id belongs to one item",
        INV-1/SEC-13) after the pull request had merged, the proposal already
        consumed. The item conjunct moves that refusal to the accept door. It does
        not create the disclosure protection -- ``store.py``'s
        ``_refuse_unless_it_is_the_same_revision`` refuses a cross-item revision-id
        reuse before it reads content, so no rejected-item body is ever disclosed,
        which is why this face is HIGH and not CRITICAL; the accept-side conjunct
        only spares the operator a consumed proposal with no undo.

        Everything else on the same inode -- a different revision, the same
        revision with different bytes, or the same revision on a different item --
        is a break.
        """
        incoming = ContentHash.of_bytes(move.data)
        for migration in landed:
            for operation in migration.operations:
                if not isinstance(operation, UpsertRevision):
                    continue
                if not self._operation_reads(operation, identity, move.destination):
                    continue
                # A landed revision already reads the body this move would
                # overwrite. That is a break unless it is this proposal's own
                # revision re-declared byte-for-byte on the same item -- the sole
                # in-place re-declare ADR-0024 decision 5 admits, and the only
                # case in which overwriting the body changes nothing the set has
                # pinned. The item conjunct is load-bearing: a byte-identical body
                # re-declared under a *different* item's id matches on id and
                # bytes but is a cross-item revision reuse, which `migrate apply`
                # refuses (INV-1/SEC-13) after the pull request has merged.
                if (
                    operation.item_id.value == move.item_id
                    and operation.revision_id.value == move.revision_id
                    and self._reads_identical_bytes(operation, incoming, move.destination)
                ):
                    continue
                return True
        return False

    def _reads_identical_bytes(
        self, operation: UpsertRevision, incoming: ContentHash, destination: Path
    ) -> bool:
        """Whether ``operation`` reads exactly ``incoming``'s bytes.

        The loader records every operation's body hash in ``content_sha256`` --
        the declared pin, or the body's hash as it read it, but always one
        (:class:`UpsertRevision`), and this guard only ever iterates the loaded
        set. ``None`` is therefore the in-memory case the loader never produces;
        for it the bytes now at ``destination`` -- the file the operation was
        already matched to read (:meth:`_operation_reads`) -- are hashed instead,
        through :func:`~theurian.security.paths.read_source_file` rather than a
        raw read, so this fallback stays bounded by SEC-8's cap the same way
        every other accept-path read is (#400). That read sits inside
        :meth:`accept`'s examination-phase ``except OSError``, so a filesystem
        refusal to read it becomes a CP-2 ``ProposalError``, not a raw escape --
        an oversized ``destination`` is the one fault that clause does not
        translate, because :class:`~theurian.domain.errors.InputTooLargeError`
        is not an ``OSError``; it propagates as itself, the same choice made for
        every other size-cap refusal on this path.
        """
        landed = operation.content_sha256
        if landed is None:  # pragma: no cover - loader always sets it
            relative = PurePosixPath(destination.relative_to(self._paths.root))
            landed = ContentHash.of_bytes(read_source_file(self._paths.root, relative))
        return incoming.value == landed.value

    def _operation_reads(
        self, operation: UpsertRevision, identity: tuple[int, int], destination: Path
    ) -> bool:
        """Whether a loaded ``upsertRevision`` reads the body now at ``destination``.

        The loader takes ``content_identity`` -- ``(st_dev, st_ino)`` -- from the
        same ``stat`` that read the body, so a case or NFC/NFD variant of one file
        compares equal here where its path *string* does not (#210). Every
        operation a gate ever sees carries it, because the loader is the sole
        production constructor of :class:`UpsertRevision`.

        ``None`` only for an operation built in memory, which has no file on disk;
        for that defensive case the fallback is a path comparison against
        :meth:`_pinned_body_path` -- a spelling-sensitive key, but the only one
        available without an inode, and unreachable from any loaded set.
        """
        if operation.content_identity is None:  # pragma: no cover - loader always sets it
            return self._pinned_body_path(operation) == destination.resolve()
        return operation.content_identity == identity

    def _pinned_body_path(self, operation: UpsertRevision) -> Path:
        """Where a loaded ``upsertRevision``'s body sits, fully resolved.

        The path fallback for :meth:`_operation_reads` when an operation carries
        no ``content_identity`` -- an in-memory operation the loader never
        produces. ``resolved_content_path`` is the loader's own resolution of the
        ``contentFile`` (against ``.theurian/migrations/``, ``..`` collapsed and
        symlinks followed, expressed relative to the resolved root); reusing it
        keeps the fallback spelling the path the same way the loader did. It is
        ``None`` for the same in-memory case, whose declared path is resolved here
        against ``.theurian/migrations/`` -- the base the loader itself uses.
        """
        resolved = operation.resolved_content_path
        if resolved is None:
            return (self._paths.migrations / operation.content_file_path).resolve()
        return self._paths.root.resolve() / resolved

    def _commit(
        self,
        proposal_id: ProposalId,
        moves: tuple[_BodyMove, ...],
        migration_file: Path,
        migration_bytes: bytes,
        migration_destination: Path,
    ) -> AcceptedProposal:
        """Write every body and the migration, or leave the tree as it was.

        Atomic in the sense that matters here: either the migration and all its
        bodies land, or ``.theurian/migrations/`` and ``.theurian/knowledge/``
        are exactly as they were. The move is a copy-then-delete -- every
        destination is written first, and the proposal's own files are removed
        only once the migration has landed, so a failure part way through rolls
        the destinations back and leaves the proposal directory whole. The
        migration is written last and with ``O_EXCL``: if its name was taken in
        the window since the check, the bodies already written are rolled back
        rather than left as orphans a previous version stranded in
        ``knowledge/``.

        Every write and directory creation is inside the guard, including the
        opening ``mkdir`` of ``.theurian/migrations/``: its ``OSError`` used to
        escape ``accept`` raw (``.theurian/`` unwritable and the directory
        absent), and the examination clause in :meth:`accept` deliberately does
        not span this method, so nothing translated it (CP-2). The rollback set
        is empty when the ``mkdir`` runs, so folding it in changes no rollback.

        **"Exactly as they were" is a claim about bytes, not about the
        directories that hold them.** :func:`_roll_back` restores every
        written destination's content byte-identically -- unlinking what it
        created, rewriting what it replaced -- but it never removes a
        directory ``mkdir(parents=True, exist_ok=True)`` created for a
        destination's parent. A refusal on the first operation to name a new
        namespace therefore leaves that namespace's now-empty directory behind
        in ``.theurian/knowledge/``, even though no file it would have held
        survives. Harmless and pre-existing -- the same residue the
        ``OSError`` and :class:`MigrationNameTakenError` rollbacks already
        leave, not new with the ``InputTooLargeError``/
        :class:`~theurian.domain.errors.IrregularSourceFileError`/
        :class:`~theurian.domain.errors.PathEscapeError` clause below -- and
        not closed here.

        **The restored-body read is size-capped, not raw** (#400, the per-entry
        face of #306's class). A destination this replaces may have reached its
        size some way other than through this service -- a body committed
        directly to ``.theurian/knowledge/``, which a ``git clone`` delivers
        exactly as large as the repository holds it, with no cap of
        :func:`~theurian.security.paths.read_source_file`'s ever having run
        over it. A raw ``Path.read_bytes()`` here would hold that whole
        replaced destination resident to build the rollback snapshot, whatever
        its size -- a channel :data:`MAX_UPSERT_OPERATIONS` does not bound,
        because that cap counts operations and this cost is spent by *one*.
        Routing the read through :func:`~theurian.security.paths.read_source_file`
        closes it the same way every other accept-path read is bounded: a
        destination over :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES`
        is refused before its bytes are read at all, so the replacement is
        never accepted rather than accepted and then held resident. The raise
        is left untranslated -- :class:`~theurian.domain.errors.InputTooLargeError`
        already carries its own remedy, the same choice :meth:`accept` makes
        for the identical raise on the *incoming* body -- but it still needs
        this method's own rollback first, because a destination read this deep
        in the loop can follow writes already made for earlier moves in the
        same call; the writes route through the same failure branch as an
        ``OSError`` would, without being one.

        **The restored read attaches a referrer, the same as every other
        caller that can reach this refusal** (see
        :class:`~theurian.domain.errors.IrregularSourceFileError`'s own
        enumeration). Left bare, a replaced destination swapped for a FIFO
        between the size-cap fix landing and this one raised
        :class:`~theurian.domain.errors.IrregularSourceFileError` with no
        ``referrer`` at all, so ``accept`` published "The referenced file is a
        named pipe (FIFO), not a regular file" naming no path -- the identical
        CP-2 shape :meth:`_read_within_project` was fixed to stop. ``relative``
        is safe to attach: it is Theurian's own project-relative construction
        from ``move.destination``, resolved and proved contained by
        :meth:`_destination_of` before ``_commit`` ever runs, never an
        author-controlled string. :class:`~theurian.domain.errors.InputTooLargeError`
        needs no matching clause -- its constructor takes no path at all, the
        same as every other size-cap raise on this path -- and an unattached
        :class:`~theurian.domain.errors.PathEscapeError` here stays generic
        rather than wrong, the same choice :meth:`_read_within_project` already
        makes for it.
        """
        created: list[Path] = []
        restored: list[tuple[Path, bytes]] = []
        try:
            self._paths.migrations.mkdir(parents=True, exist_ok=True)
            for move in moves:
                move.destination.parent.mkdir(parents=True, exist_ok=True)
                if move.replaced:
                    relative = PurePosixPath(move.destination.relative_to(self._paths.root))
                    try:
                        restored_bytes = read_source_file(self._paths.root, relative)
                    except IrregularSourceFileError as exc:
                        raise IrregularSourceFileError(
                            exc.shape, referrer=relative.as_posix()
                        ) from exc
                    restored.append((move.destination, restored_bytes))
                else:
                    created.append(move.destination)
                _write_file(move.destination, move.data, exclusive=False)
            try:
                _write_file(migration_destination, migration_bytes, exclusive=True)
            except FileExistsError as exc:
                raise MigrationNameTakenError(
                    f"{_names([migration_destination.name])} appeared in .theurian/migrations/ "
                    "while this proposal was being accepted, so accepting it would overwrite "
                    "that migration.",
                    remedy="Read what is there, then draft this proposal again for a new id.",
                ) from exc
            created.append(migration_destination)
        except MigrationNameTakenError:
            _roll_back(created, restored)
            raise
        except (InputTooLargeError, IrregularSourceFileError, PathEscapeError):
            # Not an `OSError`: these are `SecurityError`s (CP-2's third
            # category alongside the translated `OSError` and the reraised
            # `MigrationNameTakenError`), so they need their own clause to
            # reach the rollback -- without one they would propagate past it
            # and leave whatever this loop had already written in place, the
            # exact half-written tree this method's docstring promises never
            # happens. Left untranslated on purpose: see the size-cap note
            # above.
            _roll_back(created, restored)
            raise
        except OSError as exc:
            _roll_back(created, restored)
            # `strerror` and a project-relative name, never `str(exc)`: an
            # OSError's text carries the absolute filename, which is the
            # machine's home directory (the discipline `_unreadable` records).
            raise ProposalError(
                f"accept could not write "
                f"{_names([_project_relative(exc.filename, self._paths.root)])}: "
                f"{exc.strerror or 'the write failed'}.",
                remedy="Check the contentFile the migration names, then accept it again.",
            ) from exc

        return AcceptedProposal(
            proposal_id=proposal_id,
            migration=MovedFile(
                source=migration_file, destination=migration_destination, replaced=False
            ),
            bodies=tuple(
                MovedFile(source=m.source, destination=m.destination, replaced=m.replaced)
                for m in moves
            ),
            cleanup_remedy=self._remove_proposal_sources(moves, migration_file),
        )

    def _remove_proposal_sources(
        self, moves: tuple[_BodyMove, ...], migration_file: Path
    ) -> str | None:
        """Delete the proposal's now-copied files; report, don't raise, on failure.

        **The evidence record is deliberately not among them**, and that is why
        it is scanned rather than removed (#361). ``_read_evidence_record`` reads
        it to answer whether a proposal has already been accepted -- the question
        whose wrong answer mints a duplicate migration (#89) -- so deleting it
        here would destroy the diagnosis a re-accept depends on. It therefore
        stays in a directory ``accept`` tells the author to commit, which makes
        it an input to :meth:`_scan_for_secrets` even though this method moves it
        nowhere.

        Runs only after the migration and every body have landed, so the move is
        already a success: a failure here is a cleanup that could not finish, not
        a non-landing. Reporting it as a failure would exit 1 -- whose contract is
        "nothing landed" -- and send the caller to re-draft, minting a duplicate
        migration (#89). So a refused ``unlink`` (a read-only proposal directory,
        ``0o555``) is degraded to a remedy naming the leftover for a human to
        remove, and ``accept`` still returns success.
        """
        try:
            for move in moves:
                move.source.unlink(missing_ok=True)
            migration_file.unlink(missing_ok=True)
        except OSError:
            leftover = _names([_project_relative(str(migration_file.parent), self._paths.root)])
            return (
                f"The migration and its bodies landed; the proposal's own files in {leftover} "
                "could not be removed. Delete that directory by hand once it is writable -- the "
                "acceptance is complete and does not need running again."
            )
        return None

    def _destination_of(self, content_file: str) -> Path:
        """Where one ``contentFile`` points, proved to be inside ``knowledge/``.

        The ``..`` in ``../knowledge/x.md`` is legitimate and load-bearing, so
        containment cannot be a check on the string: the path is resolved first
        -- symlinks and all -- and the resolved result is what must stay inside
        ``.theurian/knowledge/`` (SEC-7, T-4, T-5). The boundary is
        ``knowledge/`` and not the project root, because a body has no
        legitimate destination outside it: a generated ``contentFile`` is always
        ``../knowledge/...``, and confining to the root instead would let a
        hand-authored ``../../.git/hooks/pre-commit`` write an executable git
        hook that runs on the maintainer's next commit.

        ``resolve()`` is the one call here that can raise before any check runs,
        and on a caller-influenced value. Measured on CPython 3.13,
        ``resolve(strict=False)`` swallows ELOOP and ENAMETOOLONG, so the only
        fault that reaches this clause is a ``ValueError``: on an embedded NUL,
        or -- as ``UnicodeEncodeError``, a ``ValueError`` -- on an unpaired
        surrogate. Neither is a ``TheurianError`` nor an ``OSError`` the
        examination clause catches, so an untranslated one would escape ``accept``
        raw and ``--json`` would publish zero bytes (CP-2). The ``OSError`` half of
        the catch is defensive: the loader guards its own ``resolve()`` with the
        same ``(ValueError, OSError)`` (``_parse_upsert``) and this matches it, so
        a filesystem fault on some other platform is translated rather than
        escaping raw. Neither the author's ``content_file`` (SEC-7 forbids
        reflecting it, #233) nor the ``OSError``'s absolute filename is echoed.
        """
        knowledge = self._paths.knowledge.resolve()
        try:
            resolved = (self._paths.migrations / content_file).resolve()
        except (ValueError, OSError) as exc:
            raise ProposalError(
                "The migration names a contentFile whose path the filesystem cannot "
                "resolve -- it contains a NUL byte or an unpaired surrogate.",
                remedy="Correct the contentFile the migration names, then accept it again.",
            ) from exc
        try:
            relative = resolved.relative_to(knowledge)
        except ValueError as exc:
            raise PathEscapeError(content_file, str(knowledge)) from exc
        destination = resolve_within_root(self._paths.knowledge, PurePosixPath(relative))
        assert_no_symlink_escape(self._paths.knowledge, destination)
        return destination


@dataclass(frozen=True, slots=True)
class _ProposalLocation:
    """Which of the two proposal directories one acceptance is reading (ADR-0028).

    ``relative`` is carried beside the absolute path rather than recomputed at
    each message, because that is where the two locations would otherwise
    diverge: a refusal built from the literal ``.theurian/proposals/`` reads
    correctly for a tracked proposal and sends the author of a ``--local`` one to
    a directory that does not exist. It is project-relative for the reason
    :func:`_project_relative` records -- an absolute path carries the developer's
    home directory into a message.

    ``local`` is the same fact as ``relative``, reduced to what a composition
    root branches on: whether the directory travels in the pull request.
    """

    directory: Path
    relative: str
    local: bool


@dataclass(frozen=True, slots=True)
class _BodyMove:
    """One body the accept path will write, with the bytes it already checked."""

    source: Path
    destination: Path
    data: bytes
    replaced: bool
    #: The ``revisionId`` the proposal's own migration declares at this body, or
    #: ``None`` when the operation names none. The replacement guard compares it
    #: with the landed revision already reading the destination: an equal id is
    #: the legitimate in-place re-declare (ADR-0024 decision 5), and only a
    #: *different* id would put two revisions on one file.
    revision_id: str | None
    #: The ``itemId`` the proposal's own migration declares at this body, or
    #: ``None`` when the operation names none. The in-place re-declare skip
    #: requires it to equal the landed revision's item: a body re-declared under a
    #: *different* item's id is a cross-item revision reuse, which ``migrate
    #: apply`` refuses (INV-1/SEC-13) after the pull request has merged.
    item_id: str | None


@dataclass(frozen=True, slots=True)
class _LandedMigration:
    """A migration filed under a recorded id, and whether its item cross-checks.

    ``confirmed`` is the difference between "this proposal's change is in place"
    and "a migration with this id is in place but may be someone else's" -- the
    two carry different messages and different remedies, but neither is "nothing
    landed", so both exit on the knowledge-state code rather than the re-draft one.
    ``name`` is the loaded migration's filename (from ``Migration.source_path``),
    for the message, never a value the diagnosis reasons *over*.
    """

    name: str
    confirmed: bool


def _last_segment(item_id: ItemId) -> str:
    return item_id.value.rpartition(".")[2]


def _unmoved_generated_bodies(directory: Path, proposal_id: ProposalId) -> tuple[str, ...]:
    """Body files of the generator's own shape still in a proposal directory.

    Relative POSIX paths, sorted, so a message naming them reads the same on
    every run and every platform. A symlink counts without being followed: what
    a name-shaped entry *is* does not change the fact that the generator's file
    has not been moved out, and a symlinked body is refused by name later.

    Deliberately not "every file that is not the evidence". ``accept`` removes
    only the bodies its migration names, so anything else a proposal directory
    carries -- a reviewer's notes, an editor's backup, ``Thumbs.db`` -- survives
    a *successful* acceptance and is committed with it (ADR-0013 point 7).
    Counting those made an accepted proposal read as unfinished, whose remedy
    mints a duplicate migration.

    Raises:
        ProposalError: If any part of the directory cannot be read. An
            unreadable subdirectory is the one case where "no body is left" and
            "no body could be seen" are different facts, and concluding the
            first from the second is how an untouched draft gets reported as
            accepted. ``rglob`` swallows that error silently, which is why this
            walks with an error callback instead.
    """

    def refuse(error: OSError) -> NoReturn:
        # `strerror` with a *literal* fallback, never `error` itself: an
        # `OSError`'s own text carries the absolute filename, which is the
        # machine's home directory (the discipline `_unreadable` and
        # `_evidence_indeterminate` both record, and which this one call had
        # walked around through its `or`).
        raise ProposalError(
            f"Proposal {proposal_id.value} could not be examined: "
            f"{error.strerror or 'it could not be read'} at "
            f"{_names([_within(error.filename, directory)])}. Whether it has been accepted "
            "cannot be answered without reading it.",
            remedy="Make the path above readable -- chmod u+rx on the directory -- then run "
            "theurian propose accept again.",
        )

    unmoved: list[str] = []
    for parent, _directories, files in directory.walk(on_error=refuse):
        for name in files:
            if is_generated_body_file_name(name):
                unmoved.append((parent / name).relative_to(directory).as_posix())
    return tuple(sorted(unmoved))


def _within(filename: object, directory: Path) -> str:
    """``filename`` from an ``OSError``, relative to the proposal directory.

    The absolute path is the machine's and not the proposal's: it carries the
    developer's home directory into a message. Falls back to whatever the error
    carried when the path is not under ``directory``, because a message naming
    nothing is worse than one naming an odd path.
    """
    if not isinstance(filename, str):
        return "an unreadable path"
    try:
        return Path(filename).relative_to(directory).as_posix() or "."
    except ValueError:
        return filename


def _project_relative(filename: object, root: Path) -> str:
    """``filename`` from an ``OSError``, relative to the project root.

    :func:`_within`'s sibling, for the accept path, where the refused call can be
    anywhere the command reached rather than only inside one proposal directory:
    a probe under either proposal directory, a body under ``.theurian/knowledge/``,
    the migration destination. The absolute path is never returned, for the reason
    :func:`_within` records -- it is the machine's home directory, not the
    proposal's.

    Both spellings of the root are tried because this path produces both. A probe
    built as ``self._paths.root / ...`` carries the root as the project was
    configured, while a read through :func:`read_source_file` carries the
    *resolved* one, and the two differ wherever the root is reached through a
    symlink (``/var`` against ``/private/var`` on macOS). The suite does not
    exercise that difference -- pytest's ``tmp_path`` is already the resolved
    ``/private/var/...``, so both spellings coincide here; the two branches earn
    their keep on a real project configured under an unresolved root (the shape
    the orchestrator's ``probes/e7`` shows). A path under neither is named by a
    phrase and not by its own text: that is where this is deliberately stricter
    than :func:`_within`, whose odd path is at least known to be inside a
    committed proposal directory.
    """
    if not isinstance(filename, str):
        return "an unreadable path"
    candidate = Path(filename)
    for base in (root, root.resolve()):
        try:
            return candidate.relative_to(base).as_posix() or "."
        except ValueError:
            continue
    return "a path outside the project"


def _bounded(text: str, limit: int) -> str | None:
    """``text`` cut to what a refusal may print, or ``None`` if it may not print it.

    The one gate every author-derived string in this module passes through before
    it reaches a message (#360), and the reason it is one gate rather than a
    judgement per site: which producer is trustworthy is exactly the enumeration
    that drifts. A caller renders what comes back; ``None`` means the detector
    reported the cut text and the caller must name it by a literal of its own
    instead.

    ``limit`` is the caller's, because a name and a report are different
    questions with different answers (:data:`_MAX_NAME_CHARS`,
    :data:`_MAX_REPORT_CHARS`). What is *not* the caller's is whether to scan:
    there is no argument for that, so no site can opt out of the redaction while
    keeping the cut.

    **The cut happens first, and the scan runs on what will actually be
    printed.** Scanning the whole and printing a prefix keys the guard on a
    superset of what it protects, which is how a truncated run that scans as a
    credential on its own gets past a scan of the untruncated string
    (GHSA-3f65's shape: the gate hashed the body while the index served title
    plus body).

    **A dirty string is dropped whole, never partially echoed.** ``scan_text``
    reports a finding's line, column and family but not the match's *length*, so
    substituting only the matched span is not reconstructible from what the
    detector returns -- and a "clean" remainder around a redacted span is a
    partial copy of the credential besides, which is the walk-around
    :class:`~theurian.security.content_secrets.SecretFinding`'s four-character
    bound exists to prevent.

    **This gate's reach is the detector's reach, and that is a bound worth
    stating rather than one to read past.** What it promises is that a string
    the detector reports is not printed; it does not promise that no credential
    is. :mod:`theurian.security.content_secrets` is best effort by recorded
    design, and one shape of the residual is visible right here, measured
    2026-09-04 through the real CLI: PyYAML's ``Mark.get_snippet`` cuts the front
    off a long line, so a 43-character ``sk-`` token in a malformed migration
    reached :func:`_their_words` with its prefix already gone, leaving 32
    lower-case hex characters that no family matches -- no upper-case letter for
    the generic family's class gate, no ``sk-`` for the specific one -- and they
    were printed. The fragment is what a *third party's* truncation left, not
    something this module chose to quote, and it is the same residual every
    caller of this detector carries.
    """
    head = text[:limit]
    return None if scan_text(head, max_findings=1) else head


def _one_name(name: str) -> str:
    """One untrusted name, quoted, bounded and redacted, for an *error message*.

    ``repr`` and never the raw name. A proposal directory is committed input
    (ADR-0013 point 7), so its filenames are the contributor's: one carrying
    ``ESC [ 2 K CR`` erases the line a terminal has already drawn and prints its
    own in place of it -- T-3's injection at the CLI edge rather than in indexed
    content. This quotes such a name into readable escapes.

    **Control characters are the class the quoting closes, and they were never
    the whole of it** (#360). A name is also where a credential sits: a
    ``contentFile`` and a migration filename are both author-written, both reach
    refusals that fire *before* the secret scan runs, and both were echoed at
    full length under the shipped ``block`` default -- so the same string the
    scan would have redacted to four characters was printed whole by the refusal
    that beat it there. :func:`_bounded` is what settles both halves.

    The terminal-rewriting half is still closed at the *output sink* as well:
    ``cli.commands._render`` and ``_fail`` escape control characters in every
    value they print, so a name that skips this helper (the exit-0 success
    payload did, before #233's round three) still cannot rewrite a line.
    """
    head = _bounded(name, _MAX_NAME_CHARS)
    if head is None:
        return _REDACTED_NAME
    return repr(head) if len(head) == len(name) else f"{head!r}{_TRUNCATED}"


def _their_words(reported: object) -> str:
    """Another component's own message, bounded and redacted, for a refusal.

    A parser's and a validator's errors quote the input they refused, so they
    carry author text as surely as a name does -- and one of the two does it
    *before* the secret scan runs. Measured 2026-09-04 on this build:

    * ``yaml.YAMLError`` in :func:`_parse_migration` prints the offending source
      line through PyYAML's ``Mark.get_snippet``, which is bounded to a window
      around the mark -- a 43-character token was cut, a 23-character ``sk-``
      token at that family's floor was echoed **whole**, and this site runs on
      the raw bytes before anything is scanned;
    * ``jsonschema``'s message in
      :meth:`ProposalService._refuse_a_document_the_schema_rejects` quotes the
      offending instance in full and is bounded by nothing.

    Rendered unquoted, unlike :func:`_one_name`: these are sentences a reader
    finishes rather than names a reader copies, and ``repr`` on a multi-line
    parser error is unreadable. Bounded at :data:`_MAX_REPORT_CHARS` and not at
    the name bound, which cut two legitimate diagnoses mid-sentence when both
    shared one number.
    """
    text = str(reported)
    head = _bounded(text, _MAX_REPORT_CHARS)
    if head is None:
        return _REDACTED_REPORT
    return head if len(head) == len(text) else f"{head}{_TRUNCATED}"


def _rendered_scalar(value: object) -> str:
    """One untrusted *parsed* scalar -- a raw YAML value -- for a refusal.

    A string goes through :func:`_one_name`, which is where the credential and
    the terminal-control questions are answered. Anything else has already been
    proved renderable by :func:`~theurian.security.yaml_loading.is_bounded_scalar`
    -- a bool, a number, ``None`` or a timestamp -- and none of those can spell a
    credential, so ``repr`` is the whole of it.
    """
    return _one_name(value) if isinstance(value, str) else repr(value)


def _names(names: Sequence[str]) -> str:
    """Untrusted names, each bounded by :func:`_one_name`, and how many at most.

    Two ceilings, and they answer different questions. This one bounds *how
    many*: 50,000 files in a committed proposal directory produced a 600 KB error
    string in 1.5 s before :data:`_MAX_NAMES_LISTED` capped the list.
    :func:`_one_name` bounds *how much of each*, which the cap never did (#360).

    The remaining interpolations in this module are constrained rather than
    routed here, and each is constrained by construction rather than by trust:

    * identifiers -- ``ProposalId``, ``MigrationId``, ``ItemId``, ``RevisionId``
      -- validated on construction against anchored patterns, and every message
      built from one (``location.relative``, the searched-directory lists) with
      them;
    * ``OSError.strerror``, which is the OS's own category for a failure and
      carries none of the path (:meth:`ProposalService._unreadable` records why
      ``str(exc)`` is never used in its place);
    * :func:`_evidence_failure_reason`, a fixed table keyed on the exception's
      *type*;
    * this module's own literals -- ``AUTHORED_IN_THEURIAN``,
      ``MIGRATION_API_VERSION``, ``MAX_UPSERT_OPERATIONS``, the field names in
      :meth:`ProposalRequest.__post_init__` -- and integer counts.

    **What lies outside this population, and is not closed.** The gate's boundary
    is this module, and one *other* module reached on the accept path holds the
    same shape:
    ``infrastructure/filesystem/migration_loader.py`` prefixes ``{path.name}`` --
    a landed migration's filename -- onto every ``MigrationError`` it raises, and
    the CLI loads the migration set during context resolution, so that message
    arrives *before* ``accept`` runs at all. Measured 2026-09-04 through the real
    CLI: a landed migration named for a credential printed it at full length in
    an ``accept --json`` payload while every refusal in this module withheld the
    same string. It is a different producer with a different population and four
    other commands reading it (``migrate validate``/``apply``/``status``,
    ``index build``), so it is recorded here rather than closed from here.
    """
    shown = ", ".join(_one_name(name) for name in names[:_MAX_NAMES_LISTED])
    remaining = len(names) - _MAX_NAMES_LISTED
    return shown if remaining <= 0 else f"{shown}, and {remaining} more"


def _require_filename_matches_id(migration_file: Path, document: Mapping[str, object]) -> None:
    """Refuse a migration whose filename ULID is not its own ``id``.

    ``.theurian/migrations/`` names files ``<id>-<slug>.yaml`` and the ULID is
    authoritative (``migrations.md``), but the loader keys migrations by the
    *inner* ``id``. A file named for one ULID carrying another lands here as its
    filename and is then read by the loader as its inner id -- so the "already in
    place" check (which sees the filename) misses a real collision on the inner
    id, and the set fails downstream with a duplicate-id error. The two must
    agree, and ``_require_migration`` has already proved the name is
    ``<ulid>-<slug>.yaml``.
    """
    inner = document.get("id")
    prefix = migration_file.name.split("-", 1)[0]
    if isinstance(inner, str) and inner == prefix:
        return
    if not is_bounded_scalar(inner):
        # This runs *before* stage-1 schema validation, so `inner` is still raw
        # YAML: `id: *anchor` pointing at an alias graph is a container whose
        # `{inner!r}` re-expands to gigabytes from a few hundred bytes (T-6). A
        # migration id is a ULID, so anything but a short scalar is a mistake and
        # is refused without being rendered -- the filename ULID is the diagnosis.
        raise ProposalError(
            f"The migration file is named for {_names([prefix])} but its id is not a simple "
            "value; the filename ULID must equal the migration id.",
            remedy="Rename the file to <id>-<slug>.yaml, or correct the id inside it.",
        )
    # Both halves are routed even though only one of them is reachable as author
    # text today: `prefix` comes off a name `_require_migration` already matched
    # against `_MIGRATION_FILE_NAME`, so it is a ULID. Routing it anyway is the
    # uniform rule #360 asked for -- a per-site trust argument is what has to be
    # re-derived by whoever next calls this function from somewhere else.
    raise ProposalError(
        f"The migration file is named for {_names([prefix])} but its id is "
        f"{_rendered_scalar(inner)}; the filename ULID must equal the migration id.",
        remedy="Rename the file to <id>-<slug>.yaml, or correct the id inside it.",
    )


def _operation_count(document: Mapping[str, object]) -> int:
    """How many entries ``operations`` holds, or 0 when the field is not a list.

    A document whose ``operations`` is missing or the wrong shape is left for
    the schema check that runs later (:meth:`ProposalService._refuse_unless_the_union_applies`)
    to refuse on its own terms; this only has to answer the counting question
    without raising on a shape the rest of the pipeline already rejects.
    """
    operations = document.get("operations")
    return len(operations) if isinstance(operations, list) else 0


def _refuse_past_the_operation_cap(document: Mapping[str, object]) -> None:
    """Refuse a migration declaring more than :data:`MAX_UPSERT_OPERATIONS`.

    Called on the raw parsed document, before :meth:`ProposalService._body_moves`
    reads anything (#306, SEC-8): the cost this bounds is spent reading bodies,
    one per operation naming a ``contentFile``, and that spend happens the
    instant ``_body_moves`` runs -- so the count has to be checked before that
    call, not after. See :data:`MAX_UPSERT_OPERATIONS` for why the count is
    checked at all and why 250 is where it sits.
    """
    count = _operation_count(document)
    if count <= MAX_UPSERT_OPERATIONS:
        return
    raise ProposalError(
        f"This migration declares {count} operations, more than the "
        f"{MAX_UPSERT_OPERATIONS} `accept` will examine in one proposal.",
        remedy=(
            f"Split the proposal into migrations of {MAX_UPSERT_OPERATIONS} operations or fewer."
        ),
    )


def _parse_migration(data: bytes, path: Path) -> Mapping[str, object]:
    """Parse an accepted proposal's migration into the mapping the accept path reads.

    Syntax only, and deliberately: it answers *is this YAML, and is it a
    mapping*, so the structural checks that follow have something to key on --
    the migration's id, its filename agreement, the ``contentFile`` of each
    operation. Whether the document is a valid migration, and whether the set it
    joins still applies, are settled afterwards by
    :meth:`ProposalService._refuse_unless_the_union_applies`, which puts it
    through the published schema and then through ``migrate apply``'s own
    pipeline (ADR-0027 decision 2).

    That division used to run the other way: ``accept`` moved files and left
    both questions to ``migrate validate`` and ``migrate apply`` (ADR-0013 §4).
    It was defensible while ``accept`` was a ``mv``, and stopped being
    defensible once the command also deleted its sources -- a check that runs
    after the input is destroyed cannot be acted on (#307).
    """
    try:
        return load_yaml_mapping(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise ProposalError(
            f"{_names([path.name])} could not be read as a migration: {_their_words(exc)}",
            remedy="Fix the migration file in the proposal directory, then accept it again.",
        ) from exc


def _migration_id_or_none(value: object) -> MigrationId | None:
    """Parse a recorded ``migrationId`` as a ULID, or ``None`` for anything else.

    ``evidence.json`` is untrusted (ADR-0013 point 7), so a ``migrationId`` that
    is not a string, or a string that is not a ULID, is treated as *no usable id*
    rather than as a value or a crash: the diagnosis falls to inference, which is
    safe. This is also what keeps caller text out of the glob a matched id feeds
    -- a ULID cannot spell a metacharacter.
    """
    if not isinstance(value, str):
        return None
    try:
        return MigrationId.parse(value)
    except TheurianError:
        return None


def _evidence_item_ids(evidence: Mapping[str, object]) -> frozenset[str]:
    """Item ids this proposal's own record claims, for the acceptance cross-check.

    ``draft`` records a single ``itemId`` string; a hand-written ``evidence.json``
    may carry a list. Both are read as a set. A value of any other shape yields
    the empty set, which the cross-check reads as "cannot confirm" -- so a record
    that names no item never confirms an acceptance, which is the safe default.
    """
    value = evidence.get("itemId")
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, list):
        return frozenset(item for item in value if isinstance(item, str))
    return frozenset()


def _migration_item_ids(migration: Migration) -> frozenset[str]:
    """The item ids a *loaded* migration's operations name, for the cross-check.

    Read from the loader's typed operations, not a re-parse of the file: the
    loader already read the id and operations through ``read_source_file``'s
    symlink-escape guard, so nothing here follows a link. Only ``createItem`` and
    ``upsertRevision`` are consulted -- the two operations a generated proposal
    produces, and the pair whose ``item_id`` names the change this proposal made.
    A landed migration of any other shape contributes no id, so the cross-check
    reads it as ``present`` ("cannot confirm") rather than ``confirmed``, which is
    the safe direction: it never turns a landed migration into ``absent``.
    """
    return frozenset(
        op.item_id.value
        for op in migration.operations
        if isinstance(op, CreateItem | UpsertRevision)
    )


#: A path-free, control-free reason per read-failure class. By exception type
#: rather than ``str(exc)``: an ``OSError``'s text carries the absolute filename
#: (a home-directory leak) and a decode error's echoes bytes the file chose, so a
#: fixed phrase per class leaks neither. ``UnicodeDecodeError`` precedes
#: ``ValueError`` because it is one -- and it is reachable, not dead:
#: ``_read_within_project`` returns *bytes*, and ``json.loads`` on non-UTF-8
#: bytes raises ``UnicodeDecodeError`` before it reaches JSON syntax, so it must
#: come first to be reported as UTF-8 rather than as JSON (pinned by
#: ``test_a_non_utf8_evidence_file_is_indeterminate_as_bad_utf8``).
#: ``InputTooLargeError``/``PathEscapeError`` precede ``ProposalError`` because
#: ``TheurianError`` is their common base but only the symlink refusal is a plain
#: ``ProposalError``.
#:
#: **The table is closed, and its fallthrough is a verdict rather than an
#: "unknown".** A type with no entry here is reported as "it is not a JSON
#: object", which claims the document parsed and held the wrong shape -- so the
#: security-layer rows must track :func:`~theurian.security.paths.read_source_file`'s
#: own ``Raises``, one row each. ``IrregularSourceFileError`` (#215) arrived a
#: ``TheurianError``, was therefore already caught and already answered
#: indeterminate, and still reported a FIFO as a parsed non-object until it got
#: this row. ``test_every_read_failure_the_evidence_read_can_raise_has_its_own_reason``
#: drives the correspondence directly, because no filesystem produces all of
#: these on demand.
_EVIDENCE_FAILURE_REASONS: Final = (
    (InputTooLargeError, "it is larger than the size cap"),
    (IrregularSourceFileError, "it is not a regular file"),
    (PathEscapeError, "its path escapes the project"),
    (ProposalError, "it is, or is reached through, a symlink"),
    (RecursionError, "it is nested too deeply to parse"),
    (UnicodeDecodeError, "it is not valid UTF-8"),
    (ValueError, "it is not valid JSON"),
)


def _evidence_failure_reason(error: BaseException | None) -> str:
    """A path-free, control-free reason an ``evidence.json`` could not be read."""
    for kind, reason in _EVIDENCE_FAILURE_REASONS:
        if isinstance(error, kind):
            return reason
    if isinstance(error, OSError):
        return (error.strerror or "it could not be read").lower()
    return "it is not a JSON object"


def _upsert_bodies(document: Mapping[str, object]) -> Iterable[tuple[str, str | None, str | None]]:
    """Each ``contentFile`` a migration names, with its ``revisionId`` and ``itemId``.

    Both identifiers travel with the body because the replacement guard needs all
    three: whether the destination is already read by a landed revision, and
    whether *this* proposal re-declares that revision **on the same item** (the
    in-place re-declare, ADR-0024 decision 5) or claims it for a different one.
    The item id is what separates the legitimate re-declare from a cross-item
    reuse: a body re-declared under a *different* item's id passes an id-and-bytes
    skip while ``migrate apply`` still refuses it (INV-1/SEC-13, a revision id
    belongs to one item), so the skip carries the item conjunct too. ``None`` for
    either when the operation names none -- a malformed hand-authored migration --
    which the guard reads as "cannot be the in-place case" and so refuses
    conservatively.
    """
    operations = document.get("operations")
    if not isinstance(operations, list):
        return
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        content_file = operation.get("contentFile")
        if not (isinstance(content_file, str) and content_file):
            continue
        revision = operation.get("revisionId")
        item = operation.get("itemId")
        yield (
            content_file,
            revision if isinstance(revision, str) else None,
            item if isinstance(item, str) else None,
        )


#: The migration document's author-written string fields, level by level (#336).
#:
#: **An allowlist keyed to the schema's string fields, not the generator's.**
#: Every level of ``schemas/migrations/migration.schema.json`` declares
#: ``additionalProperties: false``, so the string fields it names are the ones a
#: document that can be accepted may carry, and
#: ``test_the_allowlist_covers_every_string_field_the_schema_declares`` reddens
#: if the schema grows a string field this set neither scans nor excludes. What
#: is covered is the author-written field *values*: a credential in a YAML
#: comment, or in a name nothing in the document spells, is read by the
#: artifact-level channels instead (#349, :meth:`ProposalService._landed_text`),
#: and the two are not each other's superset -- an escaped ``\\xNN`` scalar hides
#: a token from the bytes while this walk sees the value it parses to. Written by
#: level and not per operation
#: type: ``op`` is untrusted until stage-1 validation, which runs *after* this
#: scan, so branching on it would let a mislabelled operation pick which fields
#: get looked at. Reading the union at every operation costs a handful of
#: absent-key lookups and cannot be steered.
#:
#: A field is left out only where a *mechanism* bars a reported secret from it,
#: never because "an author cannot write it" -- which is false for a free-form
#: string like a path or a timestamp. The mechanism is per field:
#:
#: * ``id``, ``revisionId``, ``expectedRevision`` and ``dependsOn`` are
#:   ``$defs/ulid`` -- upper-case Crockford base32, carrying no lower-case
#:   letter -- and ``contentSha256`` is ``^[0-9a-f]{64}$`` -- lower-case hex,
#:   carrying no upper-case letter. The generic family's class gate needs an
#:   upper-case letter, a lower-case letter *and* a digit
#:   (``_looks_like_a_secret``), so it fires on neither: the ULID lacks the
#:   lower-case letter -- and is subtracted to nothing above the candidate floor
#:   besides -- and the hex lacks the upper-case letter. No prefix family fires
#:   either: their literals (``sk-``, ``ghp_``, ``AKIA``, ``xox``, ``AIza``) each
#:   need a character one alphabet or the other cannot spell. Scanning them would
#:   only spend the detector's ULID subtraction on strings that exist to be
#:   identifiers.
#: * ``op``, ``apiVersion`` and every enum (``kind``, ``status``,
#:   ``sensitivity``, ``trustLevel``, ``relationType``) admit only a fixed
#:   vocabulary, none of whose members the detector reports.
#:
#: ``contentFile`` is *scanned*, not excluded, and its parsed value is why: the
#: review of #349 found that neither artifact channel beside it covers what that
#: value does. ``..`` resolution can drop the very path segment a credential sits
#: in, so the resolved landed path is not a superset of what the author wrote --
#: nor strictly a subset of it, since ``.resolve()`` on an in-tree symlink can
#: substitute a component the string never spelled; and a double-quoted YAML
#: scalar spells any character as ``\\xNN``, so the migration's raw bytes carry
#: only escape runs no family matches while the loader parses out the decoded
#: credential. The parsed value is the one place both are visible, so
#: ``contentFile`` sits in
#: :data:`_AUTHORED_OPERATION_FIELDS`. A secret-shaped path that backs no file
#: never reaches acceptance: ``_body_moves`` refuses it first.
#:
#: ``createdAt`` and the date-time metadata fields (``validFrom``, ``validTo``)
#: are *scanned*, not excluded: their schema ``format: date-time`` is not
#: enforced by this pre-validation scan, so an author can write an arbitrary
#: string into them -- and ``contentType`` likewise, which lands and is published
#: on every ``knowledge.search``/``knowledge.get`` result (#336).
_AUTHORED_MIGRATION_FIELDS: Final = ("author", "createdAt", "description")

#: Every operation's author-written strings, unioned across the schema's
#: operation types: the item, alias and specification names an author chooses,
#: the free-text ``reason``/``note``/``description``, the ``sourceUri`` and
#: ``format`` a specification or an evidence removal records, ``namespace``/
#: ``owner``, and an ``upsertRevision``'s ``contentFile`` -- the parsed value,
#: which carries what its as-written bytes and its resolved landed path can each
#: miss (the ``contentFile`` note above).
_AUTHORED_OPERATION_FIELDS: Final = (
    "alias",
    "contentFile",
    "description",
    "format",
    "itemId",
    "namespace",
    "note",
    "owner",
    "reason",
    "sourceItemId",
    "sourceUri",
    "specId",
    "supersededBy",
    "targetItemId",
)

#: A revision's own metadata strings. ``title`` is the sharpest of them after the
#: source anchors: it is published on every ``knowledge.search`` and
#: ``knowledge.get`` result, so a credential there is disclosed to an agent that
#: never opens the body. ``labels``, ``scope.paths`` and ``sourceAnchors`` are
#: handled structurally by :func:`_metadata_strings` because they are lists.
_AUTHORED_METADATA_FIELDS: Final = (
    "aclGroup",
    "contentType",
    "namespace",
    "owner",
    "tenantId",
    "title",
    "validFrom",
    "validTo",
)

#: Every string a source anchor declares, and the one allowlist this module does
#: **not** own: :data:`~theurian.domain.knowledge.AUTHORED_ANCHOR_FIELDS` is
#: shared with ``index build``'s scan of the same anchors, read back off the
#: canonical store rather than off a parsed migration (#329 round 1). The names
#: are identical because the wire format is; the two controls differ only in where
#: they find the values.
#:
#: The scan runs *before* schema validation, so the schema's ``^[0-9a-f]{7,64}$``
#: pattern is not what stops a secret in ``commitSha``/``blobSha`` -- the
#: detector's class gate is: it cannot fire on lower-case hex, the generic family
#: needing an upper-case letter and every prefix family (``sk-``, ``ghp_``,
#: ``AKIA``, ``xox``, ``AIza``) needing a character hex cannot spell.
_AUTHORED_ANCHOR_FIELDS: Final = AUTHORED_ANCHOR_FIELDS


def _document_findings(document: Mapping[str, object]) -> tuple[ProposalSecretFinding, ...]:
    """Every secret-shaped string in the migration document's own fields (#336).

    **One value at a time, never the concatenation.** Joining the fields and
    scanning once is both less precise and wrong: with no delimiter between
    them, two clean values fuse into a token neither carries. A clean
    migration's ``contentSha256`` run straight into a ``title`` whose first word
    holds an upper-case letter -- ``Configuring the service``, say -- is reported
    as one high-entropy candidate (verified 2026-08-24), because the hex brings
    the length, the digits and the lower-case letters while the ``title`` brings
    the upper-case letter, and neither field clears the detector's class gate on
    its own. Per value, a clean document scans empty, and a finding names the
    field it is in rather than an offset into a string nobody can see.

    The total is bounded by the detector's own :data:`~theurian.security
    .content_secrets.MAX_FINDINGS` across *all* fields, not per field: the field
    count is the document's, so a per-field ceiling is no ceiling. Truncation is
    silent for the reason ``scan_text`` records -- the refusal is actionable on
    the first finding, and a count past the cap is the input's number to publish,
    not ours.

    The budget is :func:`_findings_in`'s, and this is one *caller* of it rather
    than the only one: the accept path scans the document's fields and the
    artifacts it lands under a single budget (#349). What this function keeps is
    the narrower question -- what the parsed document alone carries -- which is
    what the allowlist tests ask it.
    """
    return _findings_in(_authored_strings(document))


def _findings_in(scanned: Iterable[tuple[str, str]]) -> tuple[ProposalSecretFinding, ...]:
    """Scan each ``(location, text)`` in turn, under one budget for the whole run.

    **One ceiling over every input, never one each.** How many inputs there are
    is the document's to choose -- a field per operation, a body per
    ``contentFile``, a path per body -- so a per-input ceiling is no ceiling at
    all: :data:`~theurian.security.content_secrets.MAX_FINDINGS` bounds what one
    call publishes into an error message and an ``accept --json`` document, and
    that bound only means something if it is the sum. The inputs are consumed
    lazily and in order, so a caller whose budget fills early never computes the
    text of the channels it would have truncated.
    """
    findings: list[ProposalSecretFinding] = []
    for at, value in scanned:
        # Read before the extend rather than inside it. The budget is a function
        # of a list this statement is about to append to, so computing it in the
        # generator expression would tie its value to when that expression is
        # first advanced. It is never zero, because the loop breaks at the
        # ceiling; `scan_text` refuses a spent budget on its own besides, so the
        # break is what keeps this loop from walking channels it cannot pay for
        # rather than what keeps the ceiling honest.
        remaining = MAX_FINDINGS - len(findings)
        findings.extend(
            ProposalSecretFinding(location=at, finding=finding)
            for finding in scan_text(value, max_findings=remaining)
        )
        if len(findings) >= MAX_FINDINGS:
            break
    return tuple(findings)


def _authored_strings(document: Mapping[str, object]) -> Iterable[tuple[str, str]]:
    """Each author-written string in a parsed migration, with where it sits.

    The document reaching here is raw YAML: it has been proved to be a mapping
    and nothing else, because stage-1 schema validation runs *after* the scan
    (:meth:`ProposalService._scan_for_secrets` says why). So every level is
    shape-checked before it is read and anything of the wrong shape is skipped
    rather than refused -- a malformed migration is the schema's refusal to
    report, with a message this scan could not improve on.

    Locations are built from the literals in this module, never from a key read
    out of the document. A contributor's own key would otherwise reach an error
    message and an ``accept --json`` payload, and an unbounded one would carry
    megabytes into a terminal (the class :func:`_names` bounds for filenames).

    **Every value is visited once, by object identity.** PyYAML collapses an
    alias to the *same* object, so a 4 MiB document can nest ``operations:
    [*op, *op, ...]`` inside which ``labels: [*s, *s, ...]`` -- a few million
    references each, and a walk that expanded both would scan hundreds of
    gigabytes for a file that fits in the YAML size cap (T-6). Skipping a
    repeat cannot hide a secret: a skipped occurrence is the *same string
    object* as one already scanned, so its characters have been judged and
    reported at the first place they appeared. ``id()`` is safe as the key here
    and only here -- every object is reachable from ``document``, which the
    caller holds for the whole walk, so none can be collected and have its
    address reused mid-scan.
    """
    seen: set[int] = set()
    yield from _fields(document, _AUTHORED_MIGRATION_FIELDS, "migration", seen)
    for index, operation in _mappings_in(document.get("operations"), seen):
        at = f"migration.operations[{index}]"
        yield from _fields(operation, _AUTHORED_OPERATION_FIELDS, at, seen)
        # `addEvidence` carries a single anchor rather than a list of them.
        anchor = operation.get("anchor")
        if isinstance(anchor, Mapping) and _unvisited(anchor, seen):
            yield from _fields(anchor, _AUTHORED_ANCHOR_FIELDS, f"{at}.anchor", seen)
        metadata = operation.get("metadata")
        if isinstance(metadata, Mapping) and _unvisited(metadata, seen):
            yield from _metadata_strings(metadata, f"{at}.metadata", seen)


def _metadata_strings(
    metadata: Mapping[str, object], at: str, seen: set[int]
) -> Iterable[tuple[str, str]]:
    """A revision metadata block's author-written strings, scalars and lists alike."""
    yield from _fields(metadata, _AUTHORED_METADATA_FIELDS, at, seen)
    yield from _list_strings(metadata.get("labels"), f"{at}.labels", seen)
    scope = metadata.get("scope")
    if isinstance(scope, Mapping) and _unvisited(scope, seen):
        yield from _list_strings(scope.get("paths"), f"{at}.scope.paths", seen)
    for index, anchor in _mappings_in(metadata.get("sourceAnchors"), seen):
        yield from _fields(anchor, _AUTHORED_ANCHOR_FIELDS, f"{at}.sourceAnchors[{index}]", seen)


def _fields(
    mapping: Mapping[str, object], names: tuple[str, ...], at: str, seen: set[int]
) -> Iterable[tuple[str, str]]:
    """The string values ``names`` holds in ``mapping``, each located under ``at``."""
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str) and _unvisited(value, seen):
            yield f"{at}.{name}", value


def _list_strings(value: object, at: str, seen: set[int]) -> Iterable[tuple[str, str]]:
    """The string elements of ``value``, if it is a list, each located by index."""
    if not isinstance(value, list) or not _unvisited(value, seen):
        return
    for index, element in enumerate(value):
        if isinstance(element, str) and _unvisited(element, seen):
            yield f"{at}[{index}]", element


def _mappings_in(value: object, seen: set[int]) -> Iterable[tuple[int, Mapping[str, object]]]:
    """The mapping elements of ``value``, if it is a list, with their indices.

    The index is the element's position in the list as written, so a list mixing
    mappings with anything else still locates each mapping where a reader will
    find it rather than by its position among the mappings.
    """
    if not isinstance(value, list) or not _unvisited(value, seen):
        return
    for index, element in enumerate(value):
        if isinstance(element, Mapping) and _unvisited(element, seen):
            yield index, element


def _unvisited(value: object, seen: set[int]) -> bool:
    """Whether ``value`` is being reached for the first time, recording that it was.

    Keyed on ``id`` rather than on the value, because the containers this
    dedupes are unhashable and the strings can be megabytes. :func:`
    _authored_strings` records why that is sound and why it is not a determinism
    hazard: the walk's order is the document's, and which objects are shared is
    a property of the document rather than of the run.
    """
    if id(value) in seen:
        return False
    seen.add(id(value))
    return True


def _roll_back(created: Iterable[Path], restored: Iterable[tuple[Path, bytes]]) -> None:
    """Undo a partial commit: remove what was created, restore what was replaced.

    Best effort, and deliberately silent on its own failures: it runs while an
    exception is already propagating, and a raise here would mask the failure
    the caller is trying to report.
    """
    for path in created:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - defensive; the file was just written
            continue
    for path, data in restored:
        try:
            _write_file(path, data, exclusive=False)
        except OSError:  # pragma: no cover - defensive; the file was just read
            continue


def _write_file(destination: Path, data: bytes, *, exclusive: bool) -> None:
    """Write ``data`` to ``destination`` with an explicit mode, never following a link.

    ``O_NOFOLLOW`` on the final component: a destination that is a symlink is
    refused rather than written through, so a body cannot be redirected out of
    ``knowledge/`` by planting a link at its path. The write carries an explicit
    ``0o644`` and never a rename, so a body chmod 0755 in the proposal directory
    does not land executable in ``knowledge/``. ``O_EXCL`` additionally refuses a
    destination that exists at all, for the migration, whose name must never land
    on another file.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    handle = os.open(destination, flags, 0o644)
    with os.fdopen(handle, "wb") as opened:
        opened.write(data)


def _migration_document(  # noqa: PLR0913 -- the fields a migration has; all keyword-only
    request: ProposalRequest,
    *,
    migration_id: MigrationId,
    revision_id: RevisionId,
    created_at: str,
    content_file: str,
    digest: ContentHash,
) -> dict[str, object]:
    """Build the migration a human will review and then apply.

    ``status: approved`` is right even though nobody has approved it yet: the
    file applies only once a human has merged it, and ``draft`` would land
    knowledge that ``theurian index build`` leaves out. ``trustLevel`` and
    ``sensitivity`` are written only when the caller set them (``--trust-level``,
    ``--sensitivity``): left unset they are absent from the file and the loader
    applies its defaults, ``unverified`` and ``internal``. Stamping those
    defaults in unasked-for would assert a judgement the caller never made, so
    the omission is surfaced in the CLI's next steps instead of written here
    (#249).
    """
    metadata: dict[str, object] = {
        "title": request.title,
        "contentType": request.content_type.value,
        "kind": request.kind.value,
        "namespace": request.resolved_namespace,
        "status": KnowledgeStatus.APPROVED.value,
        "owner": request.owner,
    }
    if request.trust_level is not None:
        metadata["trustLevel"] = request.trust_level.value
    if request.sensitivity is not None:
        metadata["sensitivity"] = request.sensitivity.value
    if request.labels:
        metadata["labels"] = list(request.labels)
    if request.scope_paths:
        metadata["scope"] = {"paths": list(request.scope_paths)}
    if request.source_anchors:
        metadata["sourceAnchors"] = [_anchor_document(a) for a in request.source_anchors]

    upsert: dict[str, object] = {
        "op": "upsertRevision",
        "itemId": request.item_id.value,
        "revisionId": revision_id.value,
    }
    # Absent means "this creates the first revision", so an update states which
    # revision it replaces or `migrate apply` reports a conflict (#210).
    if request.expected_revision is not None:
        upsert["expectedRevision"] = request.expected_revision.value
    upsert["contentFile"] = content_file
    upsert["contentSha256"] = digest.value
    upsert["metadata"] = metadata

    return {
        "apiVersion": MIGRATION_API_VERSION,
        "id": migration_id.value,
        "createdAt": created_at,
        "author": request.author,
        "description": request.description,
        "operations": [
            {
                "op": "createItem",
                "itemId": request.item_id.value,
                "kind": request.kind.value,
                "namespace": request.resolved_namespace,
                "owner": request.owner,
            },
            upsert,
        ],
    }


def _anchor_document(anchor: SourceAnchor) -> dict[str, object]:
    """An anchor as the schema spells it, with absent fields left out."""
    fields = {
        "provider": anchor.provider,
        "sourceUri": anchor.source_uri,
        "repository": anchor.repository,
        "commitSha": anchor.commit_sha,
        "blobSha": anchor.blob_sha,
        "filePath": anchor.file_path,
        "lineStart": anchor.line_start,
        "lineEnd": anchor.line_end,
        "externalId": anchor.external_id,
    }
    return {name: value for name, value in fields.items() if value is not None}


def _evidence_document(
    evidence: Evidence,
    proposal_id: ProposalId,
    migration_id: MigrationId,
    item_id: ItemId,
) -> dict[str, object]:
    """The origin record ADR-0013 point 5 requires, for a human to read.

    Written for a human, with two fields ``propose accept`` reads back:
    ``migrationId`` and ``itemId``. Together they let it ask "has this proposal
    been accepted?" -- a migration with that id, operating on that item, is in
    ``.theurian/migrations/`` or it is not -- rather than inferring the answer
    from which files are left in the directory, which got #253 wrong in both
    directions.

    **Neither field is authority; both are a contributor's claim.** The proposal
    directory is committed and arrives through a pull request (ADR-0013 point 7),
    so this file is untrusted input like any other in it. ``migrationId`` alone
    would let a never-accepted proposal name another proposal's landed migration
    and read as accepted; ``itemId`` is what the accept path cross-checks against
    that landed migration's operations, reducing the forge to landing a migration
    for the same item. See :meth:`ProposalService._landed_migration_matching`.

    The anchors here are *not* the ones ``migrate apply`` enforces -- those are
    ``metadata.sourceAnchors`` on the revision (INV-8) -- and neither list
    substitutes for the other.
    """
    return {
        "proposalId": proposal_id.value,
        "migrationId": migration_id.value,
        "itemId": item_id.value,
        "agentId": evidence.agent_id.value,
        "taskId": evidence.task_id.value,
        "model": evidence.model,
        "reasoning": evidence.reasoning,
        "sourceAnchors": [_anchor_document(anchor) for anchor in evidence.anchors],
    }


def _to_yaml(document: Mapping[str, object]) -> str:
    """Serialise a migration, in the order it was built.

    ``sort_keys=False`` because the built order is the order a reviewer reads:
    what this is, who owns it, why, then what it does. ``allow_unicode`` so a
    Japanese title stays legible rather than becoming escape sequences.
    """
    return str(yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True, width=100))
