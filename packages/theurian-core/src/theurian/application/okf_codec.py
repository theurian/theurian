"""OKF front-matter codec (ADR-0037 decisions 2, 4, 7): the encoder half.

Shared between S2's exporter and S3's importer (a later slice, a different
session): the types below are the seam. This module ships the encoder --
:func:`encode_concept_front_matter`, :func:`encode_root_index_front_matter`,
:func:`encode_manifest_front_matter` and :func:`sidecar_extension` -- and S3
appends the decoder against the same dataclasses. Every field is a plain
string, int or nested value, never a domain enum or a Theurian entity: OKF
`type` values are not centrally registered (§4.1) and a decoded bundle's
`status` need not be Theurian's own vocabulary, so baking either in as an enum
would make the shared type unusable for decoding an arbitrary bundle.

Every value that reaches a YAML front-matter block goes through
:func:`yaml.safe_dump`, which quotes and escapes: a newline embedded in a
title, owner, label or relation note terminates no YAML value and opens no key
(decision 2's escaping rule, the front-matter half). The two Markdown-syntax
escapes at the bottom are decision 2's other half, for the two structural
sites outside front matter: an index entry's link text, and a relation note
rendered as a list line. Rendering those sites into bytes is the exporter's
job (a later assignment); this module only ships the escapes.

Key order in every emitted block is fixed here as a constant of the exporter
version, never a function of the row (decision 2, byte-source family
"Bundle-structural constants").

Pure throughout: no filesystem I/O, no clock, no randomness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import yaml

from theurian.domain.values import MediaType

# ---------------------------------------------------------------------------
# Key vocabulary (ADR-0037 *Neutral*, third bullet; decision 7's table).
# ---------------------------------------------------------------------------

THEURIAN_EXPORT_VERSION: Final = "theurian_export_version"
THEURIAN_BUNDLE_DIGEST: Final = "theurian_bundle_digest"
THEURIAN_ITEM_ID: Final = "theurian_item_id"
THEURIAN_REVISION_ID: Final = "theurian_revision_id"
THEURIAN_STATUS: Final = "theurian_status"
THEURIAN_NAMESPACE: Final = "theurian_namespace"
THEURIAN_OWNER: Final = "theurian_owner"
THEURIAN_TRUST_LEVEL: Final = "theurian_trust_level"
THEURIAN_SENSITIVITY: Final = "theurian_sensitivity"
THEURIAN_CONTENT_TYPE: Final = "theurian_content_type"
THEURIAN_BODY_FILE: Final = "theurian_body_file"
THEURIAN_RELATIONS: Final = "theurian_relations"
THEURIAN_ANCHOR: Final = "theurian_anchor"

#: All thirteen `theurian_*` keys, in the order ADR-0037's *Neutral* bullet
#: spells them.
THEURIAN_KEYS: Final[tuple[str, ...]] = (
    THEURIAN_EXPORT_VERSION,
    THEURIAN_BUNDLE_DIGEST,
    THEURIAN_ITEM_ID,
    THEURIAN_REVISION_ID,
    THEURIAN_STATUS,
    THEURIAN_NAMESPACE,
    THEURIAN_OWNER,
    THEURIAN_TRUST_LEVEL,
    THEURIAN_SENSITIVITY,
    THEURIAN_CONTENT_TYPE,
    THEURIAN_BODY_FILE,
    THEURIAN_RELATIONS,
    THEURIAN_ANCHOR,
)

OKF_TYPE: Final = "type"
OKF_TITLE: Final = "title"
OKF_TAGS: Final = "tags"
OKF_STATUS: Final = "status"
OKF_STALE_AFTER: Final = "stale_after"
OKF_GENERATED: Final = "generated"
OKF_SOURCES: Final = "sources"
OKF_VERSION: Final = "okf_version"

#: The OKF-side keys the export emits, beside the thirteen `theurian_*` keys
#: above (Compliance section's emission-inventory sentence).
OKF_KEYS: Final[tuple[str, ...]] = (
    OKF_TYPE,
    OKF_TITLE,
    OKF_TAGS,
    OKF_STATUS,
    OKF_STALE_AFTER,
    OKF_GENERATED,
    OKF_SOURCES,
    OKF_VERSION,
)

#: §12: the OKF spec version this exporter targets, fixed by decision 2.
OKF_SPEC_VERSION: Final = "0.2"

#: The manifest's fixed `type` (decision 2) -- also the root index heading and
#: the manifest's own index-entry title, both of which live outside this
#: module's front-matter-only scope.
MANIFEST_TYPE: Final = "Theurian Bundle"


# ---------------------------------------------------------------------------
# Shared types (encoder and decoder alike).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedBy:
    """OKF `generated` (§5.2, §7): the actor and instant a concept was produced.

    `at` is a caller-formatted instant string, not a ``datetime``: the encoder
    formats a revision's `created_at`, and the decoder reads a bundle's raw
    text. Neither belongs to a type both directions share.
    """

    by: str
    at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceAnchorProjection:
    """`theurian_anchor`'s exact shape (decision 7): the served anchor fields
    and no others -- `blobSha` and `externalId` have no field to carry them.
    """

    provider: str
    repository: str
    commit_sha: str
    file_path: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceEntry:
    """One OKF `sources[]` entry (§5.1): `resource`, plus decision 7's anchor."""

    resource: str
    anchor: SourceAnchorProjection


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationEntry:
    """One `theurian_relations` entry (decision 4): the typed edge, lossless."""

    type: str
    target: str
    note: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ConceptFrontMatter:
    """A concept document's front matter (decision 7's projection).

    `theurian_relations` is normalized to `(type, target)` order at
    construction (decision 2's ordering rule) regardless of the order the
    caller passes: two callers assembling the same set in different orders
    must still encode to byte-identical output, so the invariant belongs here
    rather than at render time. `sources` and `labels` keep the caller's own
    order (decision 2: a property of the row, not of the query).

    Emission order is :data:`CONCEPT_FRONT_MATTER_KEY_ORDER`, an exporter
    constant -- not this dataclass's field order, which is for readability.
    """

    kind: str
    title: str
    labels: tuple[str, ...] = ()
    status: str
    theurian_status: str
    stale_after: str | None = None
    generated: GeneratedBy
    sources: tuple[SourceEntry, ...] = ()
    theurian_export_version: int
    theurian_item_id: str
    theurian_revision_id: str
    theurian_namespace: str
    theurian_owner: str
    theurian_trust_level: str
    theurian_sensitivity: str
    theurian_content_type: str
    theurian_body_file: str | None = None
    theurian_relations: tuple[RelationEntry, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(self.theurian_relations, key=lambda entry: (entry.type, entry.target))
        )
        object.__setattr__(self, "theurian_relations", ordered)


@dataclass(frozen=True, slots=True, kw_only=True)
class ManifestFrontMatter:
    """`theurian-bundle.md`'s front matter (decision 2): two constants, nothing else.

    No `generated` block (the manifest has no revision to date) and no
    `theurian_content_type` (it projects no body) -- decision 2 forbids both,
    and this type carries no field for either.
    """

    theurian_export_version: int
    theurian_bundle_digest: str


#: The fixed key order :func:`encode_concept_front_matter` writes in -- a
#: constant of the exporter version (decision 2, byte-source family
#: "Bundle-structural constants"), never a function of the row.
CONCEPT_FRONT_MATTER_KEY_ORDER: Final[tuple[str, ...]] = (
    OKF_TYPE,
    OKF_TITLE,
    OKF_TAGS,
    OKF_STATUS,
    OKF_STALE_AFTER,
    OKF_GENERATED,
    OKF_SOURCES,
    THEURIAN_EXPORT_VERSION,
    THEURIAN_ITEM_ID,
    THEURIAN_REVISION_ID,
    THEURIAN_STATUS,
    THEURIAN_NAMESPACE,
    THEURIAN_OWNER,
    THEURIAN_TRUST_LEVEL,
    THEURIAN_SENSITIVITY,
    THEURIAN_CONTENT_TYPE,
    THEURIAN_BODY_FILE,
    THEURIAN_RELATIONS,
)

#: `theurian_anchor`'s own key order, nested inside each `sources[]` entry.
SOURCE_ANCHOR_KEY_ORDER: Final[tuple[str, ...]] = (
    "provider",
    "repository",
    "commit_sha",
    "file_path",
    "line_start",
    "line_end",
)


# ---------------------------------------------------------------------------
# YAML emission.
# ---------------------------------------------------------------------------

_FRONT_MATTER_FENCE: Final = "---\n"


def _dump_yaml(mapping: dict[str, object]) -> str:
    """Every value quoted and escaped by PyYAML; key order preserved verbatim.

    `sort_keys=False` is load-bearing: without it PyYAML alphabetizes the
    mapping, destroying the fixed order decision 2 requires.
    """
    return str(
        yaml.safe_dump(
            mapping,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=100,
        )
    )


def _front_matter_block(mapping: dict[str, object]) -> str:
    return f"{_FRONT_MATTER_FENCE}{_dump_yaml(mapping)}{_FRONT_MATTER_FENCE}"


def _anchor_mapping(anchor: SourceAnchorProjection) -> dict[str, object]:
    return {
        "provider": anchor.provider,
        "repository": anchor.repository,
        "commit_sha": anchor.commit_sha,
        "file_path": anchor.file_path,
        "line_start": anchor.line_start,
        "line_end": anchor.line_end,
    }


def _source_entry_mapping(entry: SourceEntry) -> dict[str, object]:
    return {"resource": entry.resource, THEURIAN_ANCHOR: _anchor_mapping(entry.anchor)}


def _relation_entry_mapping(entry: RelationEntry) -> dict[str, object]:
    mapping: dict[str, object] = {"type": entry.type, "target": entry.target}
    if entry.note is not None:
        mapping["note"] = entry.note
    return mapping


def encode_concept_front_matter(front_matter: ConceptFrontMatter) -> str:
    """The front-matter block for one concept document (decision 7's projection).

    `stale_after` and `theurian_body_file` are omitted when absent; every
    other key is emitted even when its value is an empty sequence, because
    decision 7 treats `tags`, `sources` and `theurian_relations` as
    always-present projections of a field the revision always carries, just
    sometimes empty.
    """
    mapping: dict[str, object] = {
        OKF_TYPE: front_matter.kind,
        OKF_TITLE: front_matter.title,
        OKF_TAGS: list(front_matter.labels),
        OKF_STATUS: front_matter.status,
    }
    if front_matter.stale_after is not None:
        mapping[OKF_STALE_AFTER] = front_matter.stale_after
    mapping[OKF_GENERATED] = {"by": front_matter.generated.by, "at": front_matter.generated.at}
    mapping[OKF_SOURCES] = [_source_entry_mapping(entry) for entry in front_matter.sources]
    mapping[THEURIAN_EXPORT_VERSION] = front_matter.theurian_export_version
    mapping[THEURIAN_ITEM_ID] = front_matter.theurian_item_id
    mapping[THEURIAN_REVISION_ID] = front_matter.theurian_revision_id
    mapping[THEURIAN_STATUS] = front_matter.theurian_status
    mapping[THEURIAN_NAMESPACE] = front_matter.theurian_namespace
    mapping[THEURIAN_OWNER] = front_matter.theurian_owner
    mapping[THEURIAN_TRUST_LEVEL] = front_matter.theurian_trust_level
    mapping[THEURIAN_SENSITIVITY] = front_matter.theurian_sensitivity
    mapping[THEURIAN_CONTENT_TYPE] = front_matter.theurian_content_type
    if front_matter.theurian_body_file is not None:
        mapping[THEURIAN_BODY_FILE] = front_matter.theurian_body_file
    mapping[THEURIAN_RELATIONS] = [
        _relation_entry_mapping(entry) for entry in front_matter.theurian_relations
    ]
    return _front_matter_block(mapping)


def encode_root_index_front_matter() -> str:
    """The bundle-root `index.md`'s front matter: `okf_version` and nothing else.

    §8 permits an index file no front matter at all except this one key
    (decision 2); a non-root index carries none, which is why this function
    takes no argument.
    """
    return _front_matter_block({OKF_VERSION: OKF_SPEC_VERSION})


def encode_manifest_front_matter(manifest: ManifestFrontMatter) -> str:
    """`theurian-bundle.md`'s front matter: fixed text and two constants."""
    return _front_matter_block(
        {
            OKF_TYPE: MANIFEST_TYPE,
            THEURIAN_EXPORT_VERSION: manifest.theurian_export_version,
            THEURIAN_BUNDLE_DIGEST: manifest.theurian_bundle_digest,
        }
    )


# ---------------------------------------------------------------------------
# The sidecar extension rule (decision 7): total over every media type.
# ---------------------------------------------------------------------------

_JSON_MEDIA_TYPE: Final = "application/json"
_YAML_MEDIA_TYPES: Final = frozenset({"application/yaml", "text/x-yaml"})


def sidecar_extension(content_type: MediaType) -> str:
    """The filename extension a non-markdown sidecar takes (decision 7).

    Total: never raises, and never returns `.md` -- a markdown body embeds in
    its concept document and never reaches this rule. Distinct from
    :func:`theurian.domain.proposal.body_extension`, which maps a *proposal*
    body's three formats and raises on the rest; a gate-cleared knowledge row
    must never be refused, so this rule has a fallback arm instead of a raise.
    """
    value = content_type.value
    if value == _JSON_MEDIA_TYPE or value.endswith("+json"):
        return ".json"
    if value in _YAML_MEDIA_TYPES or value.endswith("+yaml"):
        return ".yaml"
    return ".txt"


# ---------------------------------------------------------------------------
# Markdown-syntax escapes (decision 2): the two structural sites outside YAML.
# ---------------------------------------------------------------------------


def escape_markdown_link_text(text: str) -> str:
    """Escape *text* for a Markdown link's bracketed label (index entries).

    Covers exactly the punctuation that can reopen link syntax inside a
    label: a backslash (doubled first, so a caller's own backslash cannot
    pair with an escape this function inserts and cancel it out), then `[`
    and `]`. Unescaped, a label carrying `]` immediately followed by `(`
    closes the link early and opens a second, attacker-chosen one -- the
    failure ADR-0037 decision 2 names. `(` itself is left alone: on its own,
    with no preceding unescaped `]`, it carries no special meaning here.
    """
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace("[", "\\[")
    return escaped.replace("]", "\\]")


#: CommonMark's ATX-heading rule allows up to three leading spaces before `#`.
_ATX_HEADING_PREFIX: Final = re.compile(r"^( {0,3})(#+)")


def escape_markdown_list_line(text: str) -> str:
    """Escape *text* for a relation note rendered as its own list line.

    Covers exactly a leading run of `#`, optionally indented as CommonMark's
    ATX-heading rule allows: unescaped, it reads as a heading and splits the
    enclosing `## Relations` section (decision 2). One inserted backslash
    defeats the whole run, since the line no longer opens with `#` at all.
    Nothing later in the note needs escaping for this rule.
    """
    match = _ATX_HEADING_PREFIX.match(text)
    if match is None:
        return text
    indent, hashes = match.group(1), match.group(2)
    return f"{indent}\\{hashes}{text[match.end() :]}"


__all__ = [
    "CONCEPT_FRONT_MATTER_KEY_ORDER",
    "MANIFEST_TYPE",
    "OKF_GENERATED",
    "OKF_KEYS",
    "OKF_SOURCES",
    "OKF_SPEC_VERSION",
    "OKF_STALE_AFTER",
    "OKF_STATUS",
    "OKF_TAGS",
    "OKF_TITLE",
    "OKF_TYPE",
    "OKF_VERSION",
    "SOURCE_ANCHOR_KEY_ORDER",
    "THEURIAN_ANCHOR",
    "THEURIAN_BODY_FILE",
    "THEURIAN_BUNDLE_DIGEST",
    "THEURIAN_CONTENT_TYPE",
    "THEURIAN_EXPORT_VERSION",
    "THEURIAN_ITEM_ID",
    "THEURIAN_KEYS",
    "THEURIAN_NAMESPACE",
    "THEURIAN_OWNER",
    "THEURIAN_RELATIONS",
    "THEURIAN_REVISION_ID",
    "THEURIAN_SENSITIVITY",
    "THEURIAN_STATUS",
    "THEURIAN_TRUST_LEVEL",
    "ConceptFrontMatter",
    "GeneratedBy",
    "ManifestFrontMatter",
    "RelationEntry",
    "SourceAnchorProjection",
    "SourceEntry",
    "encode_concept_front_matter",
    "encode_manifest_front_matter",
    "encode_root_index_front_matter",
    "escape_markdown_link_text",
    "escape_markdown_list_line",
    "sidecar_extension",
]
