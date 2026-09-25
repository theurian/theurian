"""OKF bundle import (ADR-0037 decisions 5, 6): the gated on-ramp.

**Decision 6 in the ADR's own words**: *"The import's whole output is a
proposal draft through the existing `ProposalService` -- `draft` for a
concept's body and revision, `draft_from_document` for the operations
decision 5 admits."* Decision 5 is entirely about `addRelation`. So one
admitted concept becomes one proposal through :meth:`.draft()
<theurian.application.proposal_service.ProposalService.draft>` -- the same
content path `knowledge.proposeChange` and
:mod:`theurian.application.candidate_generation` already use -- and every
`theurian_relations` entry belonging to a concept that **actually drafted**
becomes one `addRelation` operation in at most one additional proposal
through :meth:`.draft_from_document()
<theurian.application.proposal_service.ProposalService.draft_from_document>`.
`draft_from_document` cannot carry the first kind at all:
`ProposalService._refuse_operations_outside_the_v1_set` refuses
`createItem`/`upsertRevision` unconditionally, redirecting a caller to
`.draft()` -- the control ADR-0027's two-procedures-disagree defect exists to
enforce, and this import is bound by it like every other caller.

**Nothing here reads OKF front matter as governance** (decision 1): `status`,
`theurian_owner`, `theurian_namespace`, `theurian_trust_level` and
`theurian_sensitivity` are never copied onto a drafted proposal.
`ProposalRequest.namespace` stays unset (derived from the item id, never from
`theurian_namespace`'s free text -- `domain/proposal.py::body_relative_path`'s
own reason). `trust_level` is always :attr:`TrustLevel.INFERRED
<theurian.domain.enums.TrustLevel.INFERRED>`, the `KnowledgeCandidate`
precedent (`domain/review.py`) applied to a second on-ramp: no field on
:class:`ImportedConcept` can carry any other value, so no later code path can
raise it.

**Every path a bundle names is resolved and contained before it is read**
(decision 6): the bundle root first (`root.resolve()`, so a bundle unpacked
under a symlinked `/tmp` still passes containment for in-bundle references),
then each discovered concept file and each `theurian_body_file` sidecar
through :func:`theurian.security.paths.read_source_file`. A failing reference
refuses that reference alone -- the bundle's own front-matter key and the
literal string it wrote, never the path it resolved to (T-25) -- and the
import continues with everything else the bundle admits.

**A `sources[]` entry is never followed, fetched, or reachability-checked.**
Whether it becomes an additional :class:`SourceAnchor` is a syntactic test
alone (decision 6): a URI or a relative path, never a scope descriptor
("all queries in BigQuery project X").
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final, final

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_codec import (
    THEURIAN_BODY_FILE,
    ConceptDecodeRefusal,
    DecodedConcept,
    DecodedConceptDocument,
    RelationEntry,
    decode_concept_document,
)
from theurian.application.proposal_service import (
    MAX_UPSERT_OPERATIONS,
    DraftedMigration,
    DraftedProposal,
    ProposalError,
    ProposalRequest,
)
from theurian.domain.enums import KnowledgeKind, TrustLevel
from theurian.domain.errors import (
    DomainError,
    InputTooLargeError,
    InvalidIdentifierError,
    InvariantViolationError,
    IrregularSourceFileError,
    MigrationError,
    PathDepthExceededError,
    PathEscapeError,
    SymlinkBudgetExceededError,
    TheurianError,
    UnanchoredLinkTargetError,
    UnreadableLinkError,
)
from theurian.domain.identifiers import ItemId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.proposal import Evidence
from theurian.domain.values import MARKDOWN, MediaType
from theurian.security.paths import read_source_file

#: `index.md`/`log.md` are OKF's reserved names, at every level (§3.1).
_RESERVED_AT_EVERY_LEVEL: Final = frozenset({"index.md", "log.md"})

#: The manifest's reserved name, at the bundle root only (ADR-0037 decision
#: 2's positional reservation): a nested `architecture/theurian-bundle.md` is
#: an ordinary concept.
_MANIFEST_FILENAME: Final = "theurian-bundle.md"

#: RFC 3986's scheme grammar, prefix only: `scheme = ALPHA *( ALPHA / DIGIT /
#: "+" / "-" / "." )` followed by `:`.
_URI_SCHEME_PATTERN: Final = re.compile(r"\A[A-Za-z][A-Za-z0-9+.\-]*:")

#: What a per-draft refusal is: the documented failure surface of `.draft()`
#: and `.draft_from_document()` alike (a packaging refusal, a schema
#: violation, evidence that evidences nothing). Deliberately **not**
#: `TheurianError`: a `ProjectPathEscapeError` from `.theurian/proposals`
#: itself is a whole-command failure, not a fact about one concept or one
#: relation, and must propagate to the CLI's own handler for it rather than
#: being recorded as if the bundle's content were at fault.
_DRAFT_REFUSAL: Final = (ProposalError, MigrationError, InvariantViolationError)


class OkfImportError(TheurianError):
    """The import as a whole could not proceed. Individual refusals are not this."""

    def __init__(self, message: str, *, remedy: str) -> None:
        self.remedy = remedy
        super().__init__(message)


#: What one :class:`ImportRefusal` is about. `REFERENCE` names a
#: `theurian_body_file`-shaped containment or content-type refusal; `CONCEPT`
#: names a concept that could not be decoded or mapped at all; `DRAFT` names a
#: concept whose own `.draft()` call refused; `RELATIONS` names the aggregated
#: relations document, whether its own draft refused or one edge was dropped
#: because the concept it named as `sourceItemId` never drafted.
KIND_REFERENCE: Final = "reference"
KIND_CONCEPT: Final = "concept"
KIND_DRAFT: Final = "draft"
KIND_RELATIONS: Final = "relations"


@dataclass(frozen=True, slots=True)
class ImportRefusal:
    """One thing the import declined to admit (ADR-0037 decision 6).

    `kind` is one of the `KIND_*` constants above. `key` is the front-matter
    key for a containment refusal, or the concept's own bundle-relative path
    or item id otherwise. `literal` is the bundle's own written value for a
    containment refusal -- literal, never the path it resolved to (T-25) --
    or a short, Theurian-written reason otherwise.
    """

    kind: str
    key: str
    literal: str


@dataclass(frozen=True, slots=True)
class ImportedConcept:
    """One OKF concept mapped onto a proposal request, before drafting.

    `trust_level` is always `INFERRED`: an import is untrusted front matter,
    and a human reviewer is what would raise it -- the `KnowledgeCandidate`
    precedent (`domain/review.py`), applied to this on-ramp. No caller can
    pass a different value; there is no field for one.
    """

    item_id: ItemId
    title: str
    kind: KnowledgeKind
    body: str
    content_type: MediaType
    labels: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    relations: tuple[RelationEntry, ...]
    trust_level: TrustLevel = field(default=TrustLevel.INFERRED, init=False)


@dataclass(frozen=True, slots=True)
class ImportedProposal:
    """One admitted concept's drafted proposal."""

    item_id: ItemId
    proposal: DraftedProposal


@dataclass(frozen=True, slots=True)
class OkfImportRequest:
    """One import run's inputs: the bundle, and the values every drafted proposal shares."""

    root: Path
    owner: str
    author: str
    evidence: Evidence
    item_filter: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class OkfImportResult:
    """What one bundle import produced."""

    concepts_admitted: tuple[ImportedProposal, ...]
    relations_proposal: DraftedMigration | None
    refusals: tuple[ImportRefusal, ...]


def _unreadable_directory_refusal(root: Path, exc: OSError) -> ImportRefusal:
    """A directory `os.walk` could not list, named by its own bundle-relative path.

    `exc.filename` is the directory `os.walk` was trying to read when it
    failed, which is an absolute, resolved path -- never rendered (T-25); only
    its position relative to the bundle root is.
    """
    directory = Path(exc.filename) if exc.filename else root
    try:
        relative = directory.relative_to(root).as_posix()
    except ValueError:
        relative = "."
    return ImportRefusal(kind=KIND_CONCEPT, key=relative, literal=_read_failure_reason(exc))


def _walk_concept_paths(root: Path) -> tuple[tuple[PurePosixPath, ...], tuple[ImportRefusal, ...]]:
    """Every candidate concept file's bundle-relative path, sorted bytewise,
    and a refusal for each subtree `os.walk` could not list.

    `Path.rglob` is not what does the walking here, because its own directory
    scan silently drops a `PermissionError`: a concept sitting inside a
    mode-000 directory simply vanished from the walk, with no refusal and no
    trace. `os.walk`'s `onerror` callback is what closes that gap -- it is
    called for the directory the walk could not list, and the walk continues
    with whatever else it can see.

    Reserved names are excluded here (decision 2): `index.md`/`log.md` at any
    level, and the manifest at the bundle root only. This only lists names;
    :func:`_map_concept` is what proves each one is contained before its bytes
    are read, so a symlinked `.md` planted inside the bundle and pointing
    outside it is refused there, however it was discovered.

    A directory whose own name ends `.md` is a candidate too, matched from
    `dirnames` alongside `filenames` -- `Path.rglob("*.md")`'s glob is
    name-only, blind to entry type, and `read_source_file` is what turns such
    a directory into a refusal (`unbounded_shape` never excludes one).
    `os.walk` still recurses into it regardless, the same as any other
    subdirectory.
    """
    candidates: list[PurePosixPath] = []
    unreadable: list[ImportRefusal] = []
    for dirpath, dirnames, filenames in os.walk(
        root, onerror=lambda exc: unreadable.append(_unreadable_directory_refusal(root, exc))
    ):
        directory = Path(dirpath)
        for name in (*filenames, *dirnames):
            if not name.endswith(".md"):
                continue
            relative = PurePosixPath((directory / name).relative_to(root).as_posix())
            if relative.name in _RESERVED_AT_EVERY_LEVEL:
                continue
            if relative == PurePosixPath(_MANIFEST_FILENAME):
                continue
            candidates.append(relative)
    return tuple(sorted(candidates, key=lambda item: item.as_posix())), tuple(unreadable)


def _item_id_from_path(relative: PurePosixPath) -> str:
    """`architecture/auth/policy.md` -> `architecture.auth.policy`.

    OKF's own concept-id convention (§2): a vanilla bundle carries no
    `theurian_item_id`, so the concept's own bundle path is its identity.
    """
    return ".".join((*relative.parts[:-1], relative.stem))


def _resolve_item_id(concept: DecodedConcept, relative: PurePosixPath) -> ItemId | None:
    candidate = concept.theurian_item_id or _item_id_from_path(relative)
    try:
        return ItemId(candidate)
    except InvalidIdentifierError:
        return None


def _resolve_kind(concept: DecodedConcept) -> KnowledgeKind | None:
    try:
        return KnowledgeKind(concept.kind)
    except ValueError:
        return None


#: Every read-failure shape this module classifies, checked in order: a
#: subclass must precede its own base or its more specific reason is never
#: reached. `PathDepthExceededError`, `SymlinkBudgetExceededError`,
#: `UnreadableLinkError` and `UnanchoredLinkTargetError` all extend
#: `PathEscapeError`, and none of them is an escape -- each has its own
#: reason for the same one issue #233 gave `PathEscapeError` an `entry` to
#: name a location without claiming the path left the root: a link chain can
#: cross the depth or hop budget, or fail to read, or land on an unanchored
#: absolute target, while never once resolving outside it. `FileNotFoundError`
#: is also an `OSError`, but never the other way round; no other pair here
#: shares a subclass relationship, so their relative order does not matter.
#: The fallback below covers the rest: a bare `OSError` (an over-length name,
#: `ENAMETOOLONG`) and a `ValueError` (an embedded NUL byte).
_READ_FAILURE_REASONS: Final[tuple[tuple[type[Exception], str], ...]] = (
    (PathDepthExceededError, "nests too deep below the bundle root"),
    (SymlinkBudgetExceededError, "reached through too many symbolic links"),
    (UnreadableLinkError, "a symbolic link on the path could not be read"),
    (UnanchoredLinkTargetError, "a symbolic link's target could not be anchored inside the root"),
    (PathEscapeError, "escapes the bundle root"),
    (IrregularSourceFileError, "not a regular file"),
    (InputTooLargeError, "too large"),
    (IsADirectoryError, "a directory, not a file"),
    (NotADirectoryError, "reached through a path segment that is a file, not a directory"),
    (PermissionError, "not readable"),
    (FileNotFoundError, "does not exist"),
)


def _read_failure_reason(exc: Exception) -> str:
    """A Theurian-written classification of a failed read -- never `str(exc)`.

    `OSError.__str__` carries the path it failed on, resolved (T-25): a bundle
    crafted to fail in a chosen way could otherwise walk the operator's own
    filesystem layout into a refusal record that lands in a proposal a human
    reviews on a public pull request. Named by the exception's class, which
    the bundle's content never controls.
    """
    return next(
        (reason for exc_type, reason in _READ_FAILURE_REASONS if isinstance(exc, exc_type)),
        "not a valid path",
    )


def _resolve_body(
    root: Path, concept: DecodedConcept, inline_body: str
) -> tuple[str, MediaType] | ImportRefusal:
    """The concept's body, and its media type.

    A markdown body embeds in the concept document (the common case); a
    non-markdown body lives in the sidecar `theurian_body_file` names,
    preserved byte for byte on export, so it is read the same way here.
    """
    if concept.theurian_body_file is None:
        return inline_body, MARKDOWN
    try:
        sidecar_bytes = read_source_file(root, concept.theurian_body_file)
    except (TheurianError, OSError, ValueError):
        return ImportRefusal(
            kind=KIND_REFERENCE, key=THEURIAN_BODY_FILE, literal=concept.theurian_body_file
        )
    try:
        sidecar_text = sidecar_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ImportRefusal(
            kind=KIND_REFERENCE, key=THEURIAN_BODY_FILE, literal=concept.theurian_body_file
        )
    if not concept.theurian_content_type:
        return ImportRefusal(kind=KIND_REFERENCE, key="theurian_content_type", literal="")
    try:
        content_type = MediaType(concept.theurian_content_type)
    except DomainError:
        return ImportRefusal(
            kind=KIND_REFERENCE,
            key="theurian_content_type",
            literal=concept.theurian_content_type,
        )
    return sidecar_text, content_type


def _is_uri_or_relative_path(resource: str) -> bool:
    """Decision 6's syntactic test, and nothing more: no follow, no fetch, no
    reachability check.

    The whitespace check runs *before* the scheme check and dominates it: a
    scope descriptor can itself open with a colon-terminated word that
    satisfies RFC 3986's scheme grammar ("BigQuery: all queries in project
    X" -- "BigQuery" is all letters, a legal scheme), so testing the scheme
    first admitted any such descriptor as if it were a URI. No legitimate URI
    or relative path carries whitespace, so checking for it first is never a
    false exclusion.

    **The scheme grammar admits `javascript:`, `data:` and `file:`, and this
    function does not narrow it.** That is deliberate, not an oversight: this
    module never follows, fetches or renders a `sources[]` resource -- it
    becomes a `SourceAnchor.source_uri` string on a migration a human reviews
    as text, and nothing here treats it as a link. Narrowing the grammar would
    protect against a rendering context this pipeline does not have; a future
    consumer that *does* render these as clickable links owns that
    allowlisting itself, the same way it would for any other free-text field
    reaching it (`no_fetch` is pinned for this whole module by
    `test_okf_import_no_fetch.py`).
    """
    if not resource or any(c.isspace() for c in resource):
        return False
    if _URI_SCHEME_PATTERN.match(resource):
        return True
    return not resource.startswith("/")


def _bundle_identity_anchor(relative: PurePosixPath) -> SourceAnchor:
    """INV-8's always-present anchor: the bundle's own identity.

    Named by the concept's path *within* the bundle, never the operator's
    absolute bundle-root path -- the same T-25 reason a refusal never names a
    resolved path either.
    """
    return SourceAnchor(
        provider="okf-bundle",
        source_uri=f"okf-bundle:{relative.as_posix()}",
        file_path=relative.as_posix(),
    )


def _source_anchors(relative: PurePosixPath, concept: DecodedConcept) -> tuple[SourceAnchor, ...]:
    anchors = [_bundle_identity_anchor(relative)]
    anchors.extend(
        SourceAnchor(provider="okf-source", source_uri=entry.resource)
        for entry in concept.sources
        if _is_uri_or_relative_path(entry.resource)
    )
    return tuple(anchors)


def _decode_concept_file(
    root: Path, relative: PurePosixPath
) -> DecodedConceptDocument | ImportRefusal:
    path_text = relative.as_posix()
    try:
        raw = read_source_file(root, relative)
    except (TheurianError, OSError, ValueError) as exc:
        return ImportRefusal(kind=KIND_CONCEPT, key=path_text, literal=_read_failure_reason(exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ImportRefusal(kind=KIND_CONCEPT, key=path_text, literal="not valid UTF-8")

    decoded = decode_concept_document(text)
    if isinstance(decoded, ConceptDecodeRefusal):
        return ImportRefusal(kind=KIND_CONCEPT, key=path_text, literal=decoded.reason)
    return decoded


def _map_concept(root: Path, relative: PurePosixPath) -> ImportedConcept | ImportRefusal:
    decoded = _decode_concept_file(root, relative)
    if isinstance(decoded, ImportRefusal):
        return decoded
    concept = decoded.front_matter

    item_id = _resolve_item_id(concept, relative)
    if item_id is None:
        literal = concept.theurian_item_id or _item_id_from_path(relative)
        return ImportRefusal(
            kind=KIND_CONCEPT,
            key=relative.as_posix(),
            literal=f"not a valid item id: {literal!r}",
        )

    kind = _resolve_kind(concept)
    if kind is None:
        return ImportRefusal(
            kind=KIND_CONCEPT,
            key=relative.as_posix(),
            literal=f"unrecognized type: {concept.kind!r}",
        )

    body_outcome = _resolve_body(root, concept, decoded.body)
    if isinstance(body_outcome, ImportRefusal):
        return body_outcome
    body, content_type = body_outcome

    return ImportedConcept(
        item_id=item_id,
        title=concept.title,
        kind=kind,
        body=body,
        content_type=content_type,
        labels=concept.labels,
        source_anchors=_source_anchors(relative, concept),
        relations=concept.theurian_relations,
    )


def _proposal_request(request: OkfImportRequest, concept: ImportedConcept) -> ProposalRequest:
    return ProposalRequest(
        item_id=concept.item_id,
        title=concept.title,
        kind=concept.kind,
        owner=request.owner,
        author=request.author,
        description=f"Imported from OKF bundle concept {concept.item_id.value}.",
        body=concept.body,
        content_type=concept.content_type,
        evidence=request.evidence,
        source_anchors=concept.source_anchors,
        labels=concept.labels,
        trust_level=concept.trust_level,
    )


def _relation_operation(source_item_id: ItemId, entry: RelationEntry) -> dict[str, object]:
    operation: dict[str, object] = {
        "op": "addRelation",
        "sourceItemId": source_item_id.value,
        "relationType": entry.type,
        "targetItemId": entry.target,
    }
    if entry.note is not None:
        operation["note"] = entry.note
    return operation


def _dropped_relation_refusal(concept: ImportedConcept, entry: RelationEntry) -> ImportRefusal:
    return ImportRefusal(
        kind=KIND_RELATIONS,
        key=concept.item_id.value,
        literal=f"{entry.type} -> {entry.target}: its own concept did not draft",
    )


def _collect_relation_operations(
    admitted: list[ImportedConcept], drafted_ids: frozenset[str]
) -> tuple[list[dict[str, object]], list[ImportRefusal]]:
    """Every `addRelation` operation a concept that actually drafted contributes.

    A concept whose own `.draft()` refused never reaches here with its edges:
    an edge naming a `sourceItemId` nobody proposed is unreviewable provenance
    -- the reviewer sees a relation with no accompanying content proposal, and
    the concept's own refusal is invisible from the relations document alone.
    Each dropped edge gets its own refusal record rather than one per concept,
    since "an edge" is decision 5's own unit.
    """
    operations: list[dict[str, object]] = []
    dropped: list[ImportRefusal] = []
    for concept in admitted:
        if concept.item_id.value not in drafted_ids:
            dropped.extend(_dropped_relation_refusal(concept, entry) for entry in concept.relations)
            continue
        operations.extend(
            _relation_operation(concept.item_id, entry) for entry in concept.relations
        )
    return operations, dropped


def _operation_cap_exceeded(count: int, concepts: int) -> OkfImportError:
    """`count` is the running total at the crossing, not the bundle's total.

    The walk stops there (see `_admit_concepts`'s docstring), so nothing here
    has read the rest of the bundle to know what it would have admitted --
    the message says what was actually computed: this many operations within
    this many concepts, not "this bundle admits".
    """
    return OkfImportError(
        f"This bundle passes {count} operations within its first {concepts} concepts, "
        f"more than the {MAX_UPSERT_OPERATIONS} a single import will draft.",
        remedy=(
            f"Split the import with `--item <id>`, selecting {MAX_UPSERT_OPERATIONS} "
            f"operations' worth of concepts or fewer per run."
        ),
    )


def _duplicate_item_id_refusal(relative: PurePosixPath, item_id: str) -> ImportRefusal:
    return ImportRefusal(
        kind=KIND_CONCEPT,
        key=relative.as_posix(),
        literal=f"duplicate item id {item_id!r}: another concept already claimed it",
    )


def _unmatched_item_filter_refusals(
    item_filter: frozenset[str], matched_ids: frozenset[str]
) -> list[ImportRefusal]:
    """One refusal per `--item` value no concept was ever matched to.

    "Matched to" is narrower than "carried by": a concept whose own
    `theurian_item_id` overrides its path-derived id is only ever read when
    *that path* is one of the requested values (`_admit_concepts`'s early
    filter), so a value naming only the override, never any path in the
    bundle, is reported unmatched here even though the concept exists --
    the honest claim is that this spelling did not reach it, not that no
    concept anywhere carries it.

    Sorted, so the report is deterministic regardless of set iteration order.
    """
    return [
        ImportRefusal(
            kind=KIND_CONCEPT,
            key=item_id,
            literal="no concept in this bundle is reachable by this item id",
        )
        for item_id in sorted(item_filter - matched_ids)
    ]


def _admit_concepts(
    root: Path, concept_paths: tuple[PurePosixPath, ...], item_filter: frozenset[str]
) -> tuple[list[ImportedConcept], list[ImportRefusal]]:
    """Map every walked path to a concept, filtering by item id as early as possible.

    `--item` gates *reads*, not admission: a concept is read only when its
    path-derived id is one of the requested values, before anything is read,
    so a concept outside the filter costs nothing and emits no refusal. A
    concept that clears that gate is never re-excluded by comparing its own
    decoded id back against the filter -- `theurian_item_id` can override
    what the path alone would derive, and the gate having already matched is
    what the operator asked for, regardless of which id the concept turns
    out to declare. Previously, a divergent override made such a concept
    unreachable by *either* spelling -- refused via the path id here, and
    again via the decoded id -- while the requested-but-absent report falsely
    claimed no concept in the bundle carried it at all. Both the path-derived
    id and the decoded id are recorded as matched, so passing both spellings
    together reports neither as unmatched even though only one triggered the
    read.

    Every `--item` value no concept was ever matched to -- by path or by a
    decoded id a path match happened to surface -- becomes its own refusal;
    see `_unmatched_item_filter_refusals` for what that does and does not
    claim.

    Two concepts resolving to one item id -- one `theurian_item_id`
    overriding its path to collide with another's, most concretely --
    would otherwise both draft: `.draft()`'s own `_check_expected_revision`
    sees only the *landed* migration set, which neither proposal is yet, so
    it refuses neither. `concept_paths` is already sorted bytewise, so
    walking it in order and keeping the first admission per id is what makes
    the refusal land on the second sighting deterministically.

    `MAX_UPSERT_OPERATIONS` is checked here, after each concept is admitted,
    rather than once after the whole bundle has been read: a bundle far past
    the cap would otherwise be read and held in memory in full -- every
    concept's body text alive at once -- before the cap ever had a chance to
    fire (measured: a 209 MB bundle whose 251st concept already crosses the
    cap peaked at 211 MB reading the other several hundred anyway). The walk
    stops at the crossing instead.
    """
    refusals: list[ImportRefusal] = []
    admitted: list[ImportedConcept] = []
    matched_ids: set[str] = set()
    admitted_ids: set[str] = set()
    total_operations = 0
    for relative in concept_paths:
        path_id = _item_id_from_path(relative)
        if item_filter and path_id not in item_filter:
            continue
        matched_ids.add(path_id)
        outcome = _map_concept(root, relative)
        if isinstance(outcome, ImportRefusal):
            refusals.append(outcome)
            continue
        matched_ids.add(outcome.item_id.value)
        if outcome.item_id.value in admitted_ids:
            refusals.append(_duplicate_item_id_refusal(relative, outcome.item_id.value))
            continue
        total_operations += 2 + len(outcome.relations)
        if total_operations > MAX_UPSERT_OPERATIONS:
            raise _operation_cap_exceeded(total_operations, len(admitted) + 1)
        admitted_ids.add(outcome.item_id.value)
        admitted.append(outcome)
    refusals.extend(_unmatched_item_filter_refusals(item_filter, frozenset(matched_ids)))
    return admitted, refusals


def _draft_refusal_literal(exc: BaseException) -> str:
    """Never `str(exc)`: closes three risks at once.

    `MigrationError`'s message comes from the injected schema validator's own
    formatting, which can embed the offending value verbatim and with no
    length bound -- an unproven T-25 channel (nothing here shows it can carry
    a resolved path, but nothing bounds it either) and, separately, an
    unbounded echo of untrusted bundle text into a record a human reviews and
    the CLI publishes. Free-form exception text is also a shape a bundle
    could try to forge to resemble a different refusal. The exception's own
    class name is bounded, entirely Theurian's to choose from, and still says
    which of `.draft()`'s three failure kinds happened.
    """
    return f"the proposal service refused it: {type(exc).__name__}"


def _draft_concepts(
    request: OkfImportRequest, admitted: list[ImportedConcept], drafts: DraftOnlyProposals
) -> tuple[list[ImportedProposal], frozenset[str], list[ImportRefusal]]:
    """Draft every admitted concept, and say which item ids actually landed.

    The returned id set is what :func:`_collect_relation_operations` filters
    against: a concept's relations travel only with concepts that drafted.
    """
    proposals: list[ImportedProposal] = []
    drafted_ids: set[str] = set()
    refusals: list[ImportRefusal] = []
    for concept in admitted:
        try:
            drafted = drafts.draft(_proposal_request(request, concept), local=False)
        except _DRAFT_REFUSAL as exc:
            refusals.append(
                ImportRefusal(
                    kind=KIND_DRAFT,
                    key=concept.item_id.value,
                    literal=_draft_refusal_literal(exc),
                )
            )
            continue
        proposals.append(ImportedProposal(item_id=concept.item_id, proposal=drafted))
        drafted_ids.add(concept.item_id.value)
    return proposals, frozenset(drafted_ids), refusals


@final
class OkfImportService:
    """The gated OKF import: decode, contain, draft. See the module docstring."""

    def __init__(self, *, drafts: DraftOnlyProposals) -> None:
        self._drafts = drafts

    def import_bundle(self, request: OkfImportRequest) -> OkfImportResult:
        root = request.root.resolve()
        if not root.is_dir():
            raise OkfImportError(
                f"{request.root} is not a directory.",
                remedy="Pass the path to an unpacked OKF bundle directory.",
            )

        # The MAX_UPSERT_OPERATIONS cap is enforced inside _admit_concepts,
        # incrementally, so the walk stops at the crossing rather than
        # reading a bundle far past the cap in full before checking it.
        concept_paths, unreadable_directories = _walk_concept_paths(root)
        admitted, mapping_refusals = _admit_concepts(root, concept_paths, request.item_filter)
        refusals: list[ImportRefusal] = [*unreadable_directories, *mapping_refusals]

        proposals, drafted_ids, draft_refusals = _draft_concepts(request, admitted, self._drafts)
        refusals.extend(draft_refusals)

        relation_operations, dropped_relations = _collect_relation_operations(admitted, drafted_ids)
        refusals.extend(dropped_relations)

        relations_proposal = self._draft_relations(request, relation_operations, refusals)

        return OkfImportResult(
            concepts_admitted=tuple(proposals),
            relations_proposal=relations_proposal,
            refusals=tuple(refusals),
        )

    def _draft_relations(
        self,
        request: OkfImportRequest,
        operations: list[dict[str, object]],
        refusals: list[ImportRefusal],
    ) -> DraftedMigration | None:
        if not operations:
            return None
        document = {"author": request.author, "operations": operations}
        try:
            return self._drafts.draft_from_document(
                document, evidence=request.evidence, local=False
            )
        except _DRAFT_REFUSAL as exc:
            # "addRelation" is not a spellable ItemId (the grammar is
            # lowercase-only; the capital R disqualifies it), so this key can
            # never collide with a real concept's own item id -- a bundle
            # cannot forge a concept whose kind=draft refusal reads as the
            # relations document's own kind=relations one.
            refusals.append(
                ImportRefusal(
                    kind=KIND_RELATIONS, key="addRelation", literal=_draft_refusal_literal(exc)
                )
            )
            return None


__all__ = [
    "ImportRefusal",
    "ImportedConcept",
    "ImportedProposal",
    "OkfImportError",
    "OkfImportRequest",
    "OkfImportResult",
    "OkfImportService",
]
