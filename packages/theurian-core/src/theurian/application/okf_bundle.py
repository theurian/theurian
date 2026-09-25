"""Every byte of an OKF bundle, as a function of the rows it holds (ADR-0037).

Split from :mod:`theurian.application.okf_export`, which owns the *population*:
the gates, the one snapshot, and the walk. This module owns the **bytes** --
decision 2's seven byte-source families -- and the seam is the one the ADR itself
draws. Nothing here reads a store, a clock or a filesystem: it takes the walked
rows and returns `relative POSIX path -> contents`, so decision 2's invariant
(*every byte is a function of the exported population and the exporter's own
version, and of nothing else*) is a property of a pure function rather than of a
procedure.

Every structural spelling here -- the reserved names, the escape suffix, the
headings, the list markers, the sidecar wording, the manifest notice, the front
matter's key order (in :mod:`theurian.application.okf_codec`) -- is a constant of
the exporter version: fixed before any row is read, so no member of that family
can vary with a corpus or with a run.

The orderings are decision 2's four, and they are what make "a function of a set"
into a sequence: the digest's paths and the index entries' paths bytewise,
`(relation type, target)` for the relation channels (see :func:`relation_order`
for where the front matter's key goes further), and in-row order for `sources[]`
and `tags`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import PurePosixPath
from typing import Final

from theurian import __version__
from theurian.application.okf_codec import (
    MANIFEST_TYPE,
    ConceptFrontMatter,
    GeneratedBy,
    ManifestFrontMatter,
    RelationEntry,
    SourceAnchorProjection,
    SourceEntry,
    encode_concept_front_matter,
    encode_manifest_front_matter,
    encode_root_index_front_matter,
    escape_markdown_link_text,
    escape_markdown_list_line,
    sidecar_extension,
)
from theurian.domain.identifiers import ItemId
from theurian.domain.knowledge import (
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeRevision,
    SourceAnchor,
)
from theurian.domain.values import MARKDOWN

#: §7's *tool* actor form, `<producer>/<version>`: the export is a tool a person
#: runs, so `generated.by` names the exporter and never an author (decision 2).
GENERATED_BY: Final = f"theurian/{__version__}"

#: The only OKF `status` this export can emit. Decision 3's population holds
#: `approved` alone, and decision 7 maps it to §5.4's `stable`; `deprecated` and
#: `superseded` are outside `SURFACEABLE_STATUSES` entirely, so a `deprecated`
#: concept in a bundle would be a disclosure defect rather than a mapping gap.
OKF_STABLE: Final = "stable"

#: The manifest concept, at the bundle root (decision 2).
MANIFEST_NAME: Final = "theurian-bundle.md"

#: One of these in every directory the bundle holds (decision 2).
INDEX_NAME: Final = "index.md"

#: OKF reserves `index.md` and `log.md` at *any* level of the hierarchy (§3.1);
#: `theurian-bundle.md` is this ADR's and is reserved at the bundle root only,
#: where the manifest it would displace actually sits (decision 7). Spelled as
#: stems because that is what a derived leaf is compared against.
_RESERVED_AT_EVERY_LEVEL: Final = frozenset({"index", "log"})
_RESERVED_AT_THE_ROOT: Final = frozenset({"theurian-bundle"})

#: What a colliding stem takes instead. It cannot itself collide, and that is a
#: property of the id grammar rather than a hope: `ItemId('index_item')` raises
#: `InvalidIdentifierError`, because the underscore is outside the id alphabet.
_ESCAPE_SUFFIX: Final = "_item"

_RELATIONS_HEADING: Final = "## Relations"

#: The bundle root, as every relative path inside it spells its own parent.
_ROOT: Final = PurePosixPath(".")

#: The fixed wording the sidecar link sits in (decision 2's seventh byte-source
#: family). It names the filename twice -- the link's text *and* its target --
#: which is `theurian_body_file`'s own derivation and not a second one.
_SIDECAR_PARAGRAPH: Final = (
    "This concept's body is not Markdown, so it is preserved beside this document, "
    "byte for byte, at [{name}]({name})."
)

#: The holder notice, mandatory rather than courteous (decision 2). Fixed text,
#: naming no deployment: a project id is neither served row data nor a constant
#: of the exporter, and whoever holds the bundle knows where they got it. It is
#: the one control in the purge residual that travels *with* the artifact.
MANIFEST_NOTICE: Final = f"""# {MANIFEST_TYPE}

This bundle is a point-in-time copy of approved knowledge, derived from a
Theurian deployment. It is an Index-class artifact: never a record of truth,
never to be cited as team knowledge, and losable without loss.

Nothing that happens in the deployment it came from reaches it. A withdrawal, a
correction, or the removal of a secret applies there and to the indexes built
there; it does not propagate to a copy already distributed, and no part of this
bundle can be updated in place.

So do not read it as current. Regenerate the bundle from the deployment it came
from and compare `theurian_bundle_digest`, rather than trusting what is written
here."""


@dataclass(frozen=True, slots=True)
class Concept:
    """One exported row: the item, its current revision, and its visible edges."""

    item: KnowledgeItem
    revision: KnowledgeRevision
    relations: tuple[KnowledgeRelation, ...]

    @property
    def stem(self) -> str:
        return _stem(self.item.item_id)

    @property
    def path(self) -> PurePosixPath:
        return concept_path(self.item.item_id)


@dataclass(frozen=True, slots=True)
class Bundle:
    """Every file the export will write, with the counts and digest it reports."""

    files: Mapping[str, str]
    #: A digest over every file above **except** the manifest, which is where it
    #: sits (decision 2).
    digest: str
    concepts: int
    sidecars: int
    indexes: int


def relation_order(relation: KnowledgeRelation) -> tuple[str, str]:
    """Decision 2's ordering for the body's relation channel: `(type, target)`.

    Not a stored order -- ``list_relations`` answers a query -- and applied by
    the walk, so the tuple a :class:`Concept` carries is already in it.

    The front-matter channel re-sorts by the codec's own key, this one extended
    with the note. The two agree wherever `(type, target)` tells two edges apart;
    where it does not -- an invertible pair stored in both directions arrives as
    two entries of one type and target, each with its own note -- the codec's key
    is the total one and this channel falls back to ``list_relations``'s
    `ORDER BY`. Both are deterministic; they are not the same order.
    """
    return (relation.relation_type.value, relation.target_item_id.value)


# ---------------------------------------------------------------------------
# Paths and names (decision 2's first byte-source family).
# ---------------------------------------------------------------------------


def _stem(item_id: ItemId) -> str:
    """The concept document's stem, from the item id's own last segment.

    Derived from the **item id**, never from the `namespace` field:
    ``domain.proposal.body_relative_path`` already refuses that field for this,
    and its reason transfers to a bundle unchanged -- `namespace` is free text
    where `../` is spellable, while an `ItemId` is dotted lowercase kebab-case
    and cannot express a traversal at all. Unlike a body file's name this
    carries no revision id: a bundle holds one revision per item.

    A derived leaf equal to a reserved name takes :data:`_ESCAPE_SUFFIX` instead
    of being refused or silently overwriting (decision 7): one item called
    `index` would otherwise deny the whole corpus its bundle.
    ``theurian_item_id`` still names the unescaped id, so nothing is lost.
    """
    leaf = item_id.value.rpartition(".")[2]
    reserved = _RESERVED_AT_EVERY_LEVEL
    if not item_id.namespace:
        reserved = reserved | _RESERVED_AT_THE_ROOT
    return f"{leaf}{_ESCAPE_SUFFIX}" if leaf in reserved else leaf


def concept_path(item_id: ItemId) -> PurePosixPath:
    """Where ``item_id``'s concept document sits, relative to the bundle root.

    Public because a relation's link target is the *far* item's path, and one
    derivation has to answer for both ends -- an edge pointing at an item named
    `index` must link to the escaped name the bundle actually wrote.
    """
    directories = item_id.namespace.split(".") if item_id.namespace else []
    return PurePosixPath(*directories, f"{_stem(item_id)}.md")


def _sidecar_path(concept: Concept) -> PurePosixPath:
    """Where a non-markdown body goes: the concept's own stem, the body's extension.

    The extension is :func:`~theurian.application.okf_codec.sidecar_extension`'s
    total function of `contentType` alone, whose range is `{.json, .yaml, .txt}`
    and never `.md` -- so a sidecar can never be written at a concept document's
    own path, and a gate-cleared row with an unmapped media type is still
    exported rather than refused.
    """
    extension = sidecar_extension(concept.revision.content_type)
    return concept.path.parent / f"{concept.stem}{extension}"


# ---------------------------------------------------------------------------
# Documents.
# ---------------------------------------------------------------------------


def _document(*blocks: str) -> str:
    """Blocks separated by one blank line, with exactly one trailing newline.

    **The embedded markdown body keeps every byte but its surrounding blank
    lines, which these separators replace.** A recorded decision rather than an
    oversight: the document's frame is then a constant of the exporter version,
    which is what decision 2's determinism pin holds, instead of varying with
    how many newlines an author left at the end of a file. Byte-for-byte
    preservation is claimed for a *sidecar* (decision 7) and is what makes a
    structured body still parse; a round trip is not identity in any case
    (ADR-0037, *What this does not close* item 2).
    """
    return "\n\n".join(block.strip("\n") for block in blocks) + "\n"


def _link(text: str, target: str) -> str:
    """One Markdown link, with the label escaped whatever it came from.

    Escaped **inside the renderer** rather than at each site that supplies a
    label: a title is unbounded author text, an item id and a path component are
    grammar-bounded, and a site-by-site rule is one a new site inherits
    incorrectly. Unescaped, a label carrying `]` immediately followed by `(`
    closes the link early and opens a second, attacker-chosen one -- decision
    2's Markdown half.
    """
    return f"[{escape_markdown_link_text(text)}]({target})"


def _relations_section(relations: Sequence[KnowledgeRelation]) -> str:
    """The generated `## Relations` section (decision 4).

    Renders the served triple `{type, target, note}` and nothing else: the link
    text is the target's item id, so the section draws on no second row's
    fields, and the type is conveyed in prose by the heading it sits under,
    which is what §6.1 asks for. Links are bundle-absolute, §6.1's recommended
    form.

    Emitted even when there are no relations, so *this heading's presence* is a
    constant of the exporter rather than a bit about the graph. The document's
    shape as a whole is not: the block between the front matter and this section
    is the authored body, preserved byte for byte (ADR-0010 rule 5), and only the
    frame around it -- the fences, the key order, this heading, the sidecar
    paragraph's fixed wording -- is decision 2's constant family.

    ``groupby`` rather than a sort of its own: the tuple arrives in
    :func:`relation_order`, so the groups are the types in order.
    """
    lines = [_RELATIONS_HEADING]
    for relation_type, group in groupby(relations, key=lambda each: each.relation_type.value):
        lines.extend(("", f"### {relation_type}", ""))
        for relation in group:
            target = relation.target_item_id.value
            lines.append(f"* {_link(target, f'/{concept_path(relation.target_item_id)}')}")
            lines.extend(_note_lines(relation.note))
    return "\n".join(lines)


def _note_lines(note: str | None) -> tuple[str, ...]:
    """A relation `note` as its own list line under the edge it explains.

    Included because `knowledge.get` already serves it verbatim to this same
    population, so withholding it would protect nothing while dropping the
    sentence that says why the edge exists (decision 4).

    The schema bounds a note by length alone -- 1000 characters, no
    control-character exclusion -- so a newline is spellable in it, and this
    renderer keeps the note's own newlines as the item's continuation lines.
    Each of those is a line start, and ``escape_markdown_list_line``'s
    line-start rule is anchored at the start of the string it is handed, so the
    escape is applied per line rather than to the value. The terminators this
    split does *not* cover -- `\\r` and YAML's three -- the escape folds to a
    space itself.
    """
    if note is None:
        return ()
    escaped = [escape_markdown_list_line(line) for line in note.split("\n")]
    continuation = tuple(f"    {line}" if line else "" for line in escaped[1:])
    return (f"  * {escaped[0]}", *continuation)


def _instant(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _source_entry(anchor: SourceAnchor) -> SourceEntry:
    """One OKF `sources[]` entry: `sourceUri` as `resource`, the rest as the anchor.

    `blobSha` and `externalId` have no field to carry them: neither served
    payload publishes either, so exporting them would put provenance in a
    portable artifact that the serve path withholds from the same rows
    (decision 7).
    """
    return SourceEntry(
        resource=anchor.source_uri,
        anchor=SourceAnchorProjection(
            provider=anchor.provider,
            repository=anchor.repository,
            commit_sha=anchor.commit_sha,
            file_path=anchor.file_path,
            line_start=anchor.line_start,
            line_end=anchor.line_end,
        ),
    )


def _front_matter(concept: Concept, *, body_file: str | None) -> ConceptFrontMatter:
    """Decision 7's projection for one row.

    ``status`` and ``sensitivity`` come off the **item** and everything else off
    the revision, which is the split `mcp/results.py::result_payload` already
    makes and for its reason: a deprecation moves the item's status and a
    `changeSensitivity` moves its classification, each without writing a new
    revision, so a projection echoing `revision.metadata` would label the
    document with what it was authored under rather than what now decides who
    may read it.
    """
    revision = concept.revision
    return ConceptFrontMatter(
        kind=revision.metadata.kind.value,
        title=revision.title,
        labels=revision.metadata.labels,
        status=OKF_STABLE,
        theurian_status=concept.item.status.value,
        # Only when the revision records one (§5.5 has no opening twin).
        stale_after=_instant(revision.validity.valid_to),
        generated=GeneratedBy(by=GENERATED_BY, at=revision.created_at.isoformat()),
        sources=tuple(_source_entry(anchor) for anchor in revision.source_anchors),
        theurian_item_id=concept.item.item_id.value,
        theurian_revision_id=revision.revision_id.value,
        theurian_namespace=revision.metadata.namespace,
        theurian_owner=revision.metadata.owner,
        theurian_trust_level=revision.metadata.trust_level.value,
        theurian_sensitivity=concept.item.sensitivity.value,
        theurian_content_type=str(revision.content_type),
        theurian_body_file=body_file,
        theurian_relations=tuple(
            RelationEntry(
                type=relation.relation_type.value,
                target=relation.target_item_id.value,
                note=relation.note,
            )
            for relation in concept.relations
        ),
    )


def _concept_files(concept: Concept) -> dict[str, str]:
    """One concept document, and a sidecar when the body is not markdown.

    A markdown body embeds; anything else is written beside the document and
    linked from it, because ADR-0010 rule 5 and the schema's own `contentType`
    description say a structured body is *preserved* rather than converted
    (decision 7). The link and `theurian_body_file` carry the same derived
    filename, which is one derivation rather than two.
    """
    if concept.revision.content_type == MARKDOWN:
        return {
            str(concept.path): _document(
                encode_concept_front_matter(_front_matter(concept, body_file=None)),
                concept.revision.body,
                _relations_section(concept.relations),
            )
        }

    sidecar = _sidecar_path(concept)
    return {
        str(concept.path): _document(
            encode_concept_front_matter(_front_matter(concept, body_file=sidecar.name)),
            _SIDECAR_PARAGRAPH.format(name=sidecar.name),
            _relations_section(concept.relations),
        ),
        # The snapshot's `body` column, byte for byte. The writer adds nothing:
        # it encodes UTF-8 with `newline=""`.
        str(sidecar): concept.revision.body,
    }


# ---------------------------------------------------------------------------
# Index files and the manifest (decision 2).
# ---------------------------------------------------------------------------


def _index_files(concepts: Sequence[Concept]) -> dict[str, str]:
    """One `index.md` in every directory the bundle holds (decision 2).

    Every directory, not only those holding a concept: an item id of
    `architecture.auth.session.policy` leaves `architecture/` holding nothing but
    a subdirectory, and skipping its index leaves everything beneath it
    unreachable by following indexes -- which is the progressive disclosure §8
    is for.
    """
    directories = {_ROOT}
    for concept in concepts:
        directories.update(concept.path.parents)

    entries: dict[PurePosixPath, list[tuple[str, str]]] = {each: [] for each in directories}
    members = [(concept.path, concept.revision.title) for concept in concepts]
    # The manifest is an entry of the root's index, titled by the fixed string
    # the root heading uses: it projects no row, so it carries no `title` for an
    # entry to draw on, and leaving it unsaid is a byte nobody bounded.
    members.append((PurePosixPath(MANIFEST_NAME), MANIFEST_TYPE))
    for path, title in members:
        entries[path.parent].append((f"/{path}", title))
    for directory in directories - {_ROOT}:
        # One entry per *immediate* subdirectory, linked with the trailing slash
        # §8's own example uses, and titled by its path component -- which is
        # derived from item ids rather than from any walked `title`.
        entries[directory.parent].append((f"/{directory}/", directory.name))

    return {
        _index_path(directory): _index_document(directory, listing)
        for directory, listing in entries.items()
    }


def _index_path(directory: PurePosixPath) -> str:
    return INDEX_NAME if directory == _ROOT else f"{directory}/{INDEX_NAME}"


def _index_document(directory: PurePosixPath, entries: Sequence[tuple[str, str]]) -> str:
    """One index file: a single section, one list, no descriptions.

    The heading is the directory's own path component; the root has none, so its
    heading is the exporter constant `# Theurian Bundle`, matching the
    manifest's `type`. Entries are ordered **bytewise by their bundle-absolute
    path**, one total order over concept documents and subdirectories alike,
    which is what decision 2's determinism pin needs. Entries carry no
    description: §8 says they SHOULD, and Theurian holds no counterpart.

    Only the bundle root's index carries front matter, and only `okf_version`
    (§8, §12).
    """
    heading = f"# {MANIFEST_TYPE}" if directory == _ROOT else f"# {directory.name}"
    lines = [
        f"* {_link(title, path)}"
        for path, title in sorted(entries, key=lambda entry: entry[0].encode("utf-8"))
    ]
    body = "\n".join([heading, "", *lines])
    if directory != _ROOT:
        return _document(body)
    return _document(encode_root_index_front_matter(), body)


def render(concepts: Sequence[Concept]) -> Bundle:
    """The whole bundle as `relative POSIX path -> contents`, manifest included.

    Rendered before anything is written, so a walk that raises leaves the target
    exactly as it found it and no digest can name a file that is not there.

    The digest is taken while the manifest is still absent -- it is where the
    digest sits, so including it would be a fixed point -- and carried out beside
    the files so the value the report publishes is the value the manifest holds
    rather than a second computation that has to agree with it.
    """
    files: dict[str, str] = {}
    for concept in concepts:
        files.update(_concept_files(concept))
    files.update(_index_files(concepts))
    digest = _digest_of(files)
    files[MANIFEST_NAME] = _document(
        encode_manifest_front_matter(ManifestFrontMatter(theurian_bundle_digest=digest)),
        MANIFEST_NOTICE,
    )
    return Bundle(
        files=files,
        digest=digest,
        concepts=len(concepts),
        sidecars=sum(1 for concept in concepts if concept.revision.content_type != MARKDOWN),
        indexes=sum(1 for path in files if PurePosixPath(path).name == INDEX_NAME),
    )


def _digest_of(files: Mapping[str, str]) -> str:
    """`theurian_bundle_digest`: sha256 over the bundle's own files, and nothing else.

    `(relative POSIX path, sha256 of the file)` pairs sorted **bytewise** by
    path (decision 2). Sorted on the encoded bytes rather than on the string, so
    the order is the one the ADR names rather than one that happens to agree with
    it for ASCII names.

    Its inputs are the bundle and nothing else, which is the property that makes
    it publishable -- and the reason the whole-tree `stateHash` is not here: that
    value covers every migration in the working tree, so it moves when a
    `rejected`, `draft` or above-ceiling row moves.
    """
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda name: name.encode("utf-8")):
        body = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        digest.update(f"{path}\n{body}\n".encode())
    return digest.hexdigest()


__all__ = [
    "GENERATED_BY",
    "INDEX_NAME",
    "MANIFEST_NAME",
    "MANIFEST_NOTICE",
    "OKF_STABLE",
    "Bundle",
    "Concept",
    "concept_path",
    "relation_order",
    "render",
]
