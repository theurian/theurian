"""OKF front-matter codec (ADR-0037 decisions 2, 4, 7): the encoder half.

Shared between S2's exporter and S3's importer (a later slice, a different
session): the types below are the seam. This module ships the encoder --
:func:`encode_concept_front_matter`, :func:`encode_root_index_front_matter`,
:func:`encode_manifest_front_matter` and :func:`sidecar_extension` -- and S3
appends the decoder against these dataclasses. Every field is a plain string,
int or nested value, never a domain enum or a Theurian entity: OKF `type` values
are not centrally registered (§4.1) and a decoded bundle's `status` need not be
Theurian's own vocabulary, so baking either in as an enum would make the shared
type unusable for decoding an arbitrary bundle. One field is narrower than that:
`generated.by` carries the export tool's own actor form, checked at construction
(see :class:`GeneratedBy`).

Row text reaches structure through three sites, and :data:`LINE_TERMINATORS` is
what each of them is written against. In a YAML front-matter block, a value
carrying any terminator is emitted double-quoted (:func:`_represent_str`), which
writes each terminator as an escape: the value occupies one physical line, so it
terminates no YAML value, opens no key, and renders no column-0 `---` a consumer
splitting on fences could truncate at. At the two Markdown-syntax sites -- an
index entry's link text and a relation note rendered as a list line -- the text
is folded to one line first and then escaped for that one line's own grammar.
Rendering those two sites into bytes is the exporter's job (a later assignment);
this module only ships the escapes.

Key order in every emitted block is fixed here as a constant of the exporter
version, never a function of the row (decision 2, byte-source family
"Bundle-structural constants"), and so is each value the encoders emit with no
field to carry it: :data:`EXPORT_VERSION`, :data:`MANIFEST_TYPE` and
:data:`OKF_SPEC_VERSION`.

Pure throughout: no filesystem I/O, no clock, no randomness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import yaml

from theurian.domain.errors import InvariantViolationError
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

#: :data:`THEURIAN_EXPORT_VERSION`'s value -- the mapping version of decision
#: 7's table. An exporter constant the encoders emit, never a caller's field:
#: named apart from the key constant the way :data:`OKF_SPEC_VERSION` is named
#: apart from :data:`OKF_VERSION`.
EXPORT_VERSION: Final = 1

#: The manifest's fixed `type` (decision 2) -- also the root index heading and
#: the manifest's own index-entry title, both of which live outside this
#: module's front-matter-only scope.
MANIFEST_TYPE: Final = "Theurian Bundle"

#: Every character a Markdown renderer or a YAML emitter can read as a line
#: break: CommonMark's `\n` and `\r`, plus YAML 1.1's NEL, LINE SEPARATOR and
#: PARAGRAPH SEPARATOR. Row text is handled over this whole set at the YAML
#: block, at :func:`escape_markdown_link_text` and at
#: :func:`escape_markdown_list_line` -- never over `\n` alone: the two escapes
#: are single-line rules anchored at the start of a string, and a second
#: physical line inside one row value carries its own leading construct past
#: them.
LINE_TERMINATORS: Final[frozenset[str]] = frozenset("\n\r\x85\u2028\u2029")

#: `\r\n` first, so the pair folds to one space rather than two. The character
#: class is built from :data:`LINE_TERMINATORS`, so a member added there is
#: folded and quoted here without a second edit.
_LINE_TERMINATOR_RUN: Final = re.compile(
    "\r\n|[" + "".join(re.escape(character) for character in sorted(LINE_TERMINATORS)) + "]"
)


def _single_line(text: str) -> str:
    """*text* with every :data:`LINE_TERMINATORS` member folded to one space.

    Never refuses, for the reason :func:`sidecar_extension` has a fallback arm
    instead of a raise: a gate-cleared row is never refused, so hostile text is
    made inert rather than rejected.
    """
    return _LINE_TERMINATOR_RUN.sub(" ", text)


# ---------------------------------------------------------------------------
# Shared types (encoder and decoder alike).
# ---------------------------------------------------------------------------

#: `generated.by`'s tool form (§7). Matched with :meth:`re.Pattern.fullmatch`
#: and not `^...$`: `$` also matches before a trailing newline, so
#: `^theurian/\S+$` accepts `theurian/0.4.0\n` -- a line terminator inside the
#: value §7 fixes as a constant, which is then not that constant.
_TOOL_ACTOR: Final = re.compile(r"theurian/\S+")


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedBy:
    """OKF `generated` (§5.2, §7): the actor and instant a concept was produced.

    `at` is a caller-formatted instant string: the encoder formats a revision's
    `created_at` and a decoder reads a bundle's raw text, and neither is a
    ``datetime`` a type both directions share could hold.

    `by` is the **export tool's** actor, and §7 fixes it as a constant of the
    exporter version, so it is constrained here at construction. A caller free
    to pass a row string -- an author, an owner, anything a migration can set --
    would turn a byte-source constant into a projected row value. A decoder
    reading an arbitrary bundle's actor is not bound by this: nothing says
    another producer's `generated.by` looks like Theurian's.
    """

    by: str
    at: str

    def __post_init__(self) -> None:
        if not _TOOL_ACTOR.fullmatch(self.by):
            raise InvariantViolationError(
                f"GeneratedBy.by is {self.by!r}; the export actor is the tool form "
                f"`theurian/<version>`, with no whitespace (ADR-0037 §7). Pass "
                f"f'theurian/{{__version__}}' from `theurian/__init__.py` -- "
                f"`theurian --version` prints the value it holds."
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceAnchorProjection:
    """`theurian_anchor`'s exact shape (decision 7): the served anchor fields
    and no others -- `blobSha` and `externalId` have no field to carry them.

    `provider` is required because `domain/knowledge.py::SourceAnchor` refuses an
    empty one; the rest are optional, and an unset one is omitted from
    `theurian_anchor` rather than emitted null. FR-R5's external-URI source is
    the shape that needs it: a source reached by URI has a provider and a
    `resource`, and no repository, commit or line range at all.
    """

    provider: str
    repository: str | None = None
    commit_sha: str | None = None
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None


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


def _relation_key(entry: RelationEntry) -> tuple[str, str, bool, str]:
    """A total order over :class:`RelationEntry`: equal keys are equal entries.

    Totality over the whole field space is what makes the sort independent of
    the caller's argument order. `(type, target)` alone leaves two edges of the
    same type to the same target ordered by whoever built the tuple, and
    `note or ""` alone still collapses `note=None` onto `note=""`; the third
    component separates those two, so the four-tuple determines the entry. Keys:
    `test_two_edges_sharing_a_type_and_target_are_ordered_by_their_notes` and
    `test_an_absent_note_and_an_empty_note_are_two_different_sort_keys`.
    """
    return (entry.type, entry.target, entry.note is None, entry.note or "")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConceptFrontMatter:
    """A concept document's front matter (decision 7's projection).

    `theurian_relations` is normalized at construction by :func:`_relation_key`
    (decision 2's ordering rule) regardless of the order the caller passes: two
    callers assembling the same set in different orders must still encode to
    byte-identical output, so the invariant belongs here rather than at render
    time. `sources` and `labels` keep the caller's own order (decision 2: a
    property of the row, not of the query).

    `theurian_export_version` is deliberately not a field: it is
    :data:`EXPORT_VERSION`, which the encoder emits itself.

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
        ordered = tuple(sorted(self.theurian_relations, key=_relation_key))
        object.__setattr__(self, "theurian_relations", ordered)


@dataclass(frozen=True, slots=True, kw_only=True)
class ManifestFrontMatter:
    """`theurian-bundle.md`'s front matter (decision 2): one field, two constants.

    `type` and `theurian_export_version` are exporter constants the encoder
    emits; the digest is the one value that is a function of the bundle's own
    files. No `generated` block (the manifest has no revision to date) and no
    `theurian_content_type` (it projects no body) -- decision 2 forbids both,
    and this type carries no field for either.
    """

    theurian_bundle_digest: str


#: The fixed key order :func:`encode_concept_front_matter` writes in -- a
#: constant of the exporter version (decision 2, byte-source family
#: "Bundle-structural constants"), never a function of the row. The encoder
#: reads its mapping *through* this constant, so it decides both which keys
#: appear and in what order.
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

#: `theurian_anchor`'s own key order, nested inside each `sources[]` entry --
#: read through by :func:`_anchor_mapping` the same way.
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


class _FrontMatterDumper(yaml.SafeDumper):
    """`SafeDumper` with one override, registered on the subclass and not the class.

    On `yaml.SafeDumper` itself the representer below would change every
    `yaml.safe_dump` in the process. Key:
    `test_the_string_representer_reaches_no_dumper_but_the_codecs_own`.
    """


def _represent_str(dumper: _FrontMatterDumper, value: str) -> yaml.ScalarNode:
    r"""Double-quoted style whenever *value* carries a line terminator.

    Double-quoted style writes each terminator as an escape (`\r`, `\n`, `\N`,
    `\L`, `\P`), which keeps the whole value on one physical line and
    round-trips it byte-for-byte. PyYAML's own style choice does neither for
    U+0085: it emits the character raw inside single quotes, and `safe_load`
    reads it back as a space. Key to both halves:
    `test_a_forged_terminator_stays_inside_its_own_value_at_every_position`.
    """
    style = '"' if _LINE_TERMINATOR_RUN.search(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_FrontMatterDumper.add_representer(str, _represent_str)


def _dump_yaml(mapping: dict[str, object]) -> str:
    """Key order preserved verbatim; every value quoted and escaped.

    `sort_keys=False` is load-bearing: without it PyYAML alphabetizes the
    mapping, destroying the fixed order decision 2 requires.
    """
    return str(
        yaml.dump(
            mapping,
            Dumper=_FrontMatterDumper,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=100,
        )
    )


def _front_matter_block(mapping: dict[str, object]) -> str:
    return f"{_FRONT_MATTER_FENCE}{_dump_yaml(mapping)}{_FRONT_MATTER_FENCE}"


def _present(values: dict[str, object], order: tuple[str, ...]) -> dict[str, object]:
    """*values* re-read through *order*, dropping the absent ones.

    Absence is `None` and is emitted as omission, never as null -- this
    module's one absence convention. An empty list is not an absence: decision 7
    treats `tags`, `sources` and `theurian_relations` as always-present
    projections of a field the revision always carries, sometimes empty.
    """
    return {key: value for key in order if (value := values[key]) is not None}


def _anchor_mapping(anchor: SourceAnchorProjection) -> dict[str, object]:
    return _present(
        {
            "provider": anchor.provider,
            "repository": anchor.repository,
            "commit_sha": anchor.commit_sha,
            "file_path": anchor.file_path,
            "line_start": anchor.line_start,
            "line_end": anchor.line_end,
        },
        SOURCE_ANCHOR_KEY_ORDER,
    )


def _source_entry_mapping(entry: SourceEntry) -> dict[str, object]:
    return {"resource": entry.resource, THEURIAN_ANCHOR: _anchor_mapping(entry.anchor)}


_RELATION_KEY_ORDER: Final[tuple[str, ...]] = ("type", "target", "note")


def _relation_entry_mapping(entry: RelationEntry) -> dict[str, object]:
    return _present(
        {"type": entry.type, "target": entry.target, "note": entry.note},
        _RELATION_KEY_ORDER,
    )


def encode_concept_front_matter(front_matter: ConceptFrontMatter) -> str:
    """The front-matter block for one concept document (decision 7's projection).

    `theurian_export_version` is :data:`EXPORT_VERSION` and not a projected
    field; the rest is the row, ordered and filtered by
    :data:`CONCEPT_FRONT_MATTER_KEY_ORDER` through :func:`_present`.
    """
    values: dict[str, object] = {
        OKF_TYPE: front_matter.kind,
        OKF_TITLE: front_matter.title,
        OKF_TAGS: list(front_matter.labels),
        OKF_STATUS: front_matter.status,
        OKF_STALE_AFTER: front_matter.stale_after,
        OKF_GENERATED: {"by": front_matter.generated.by, "at": front_matter.generated.at},
        OKF_SOURCES: [_source_entry_mapping(entry) for entry in front_matter.sources],
        THEURIAN_EXPORT_VERSION: EXPORT_VERSION,
        THEURIAN_ITEM_ID: front_matter.theurian_item_id,
        THEURIAN_REVISION_ID: front_matter.theurian_revision_id,
        THEURIAN_STATUS: front_matter.theurian_status,
        THEURIAN_NAMESPACE: front_matter.theurian_namespace,
        THEURIAN_OWNER: front_matter.theurian_owner,
        THEURIAN_TRUST_LEVEL: front_matter.theurian_trust_level,
        THEURIAN_SENSITIVITY: front_matter.theurian_sensitivity,
        THEURIAN_CONTENT_TYPE: front_matter.theurian_content_type,
        THEURIAN_BODY_FILE: front_matter.theurian_body_file,
        THEURIAN_RELATIONS: [
            _relation_entry_mapping(entry) for entry in front_matter.theurian_relations
        ],
    }
    return _front_matter_block(_present(values, CONCEPT_FRONT_MATTER_KEY_ORDER))


def encode_root_index_front_matter() -> str:
    """The bundle-root `index.md`'s front matter: `okf_version` and nothing else.

    §8 permits an index file no front matter at all except this one key
    (decision 2); a non-root index carries none, which is why this function
    takes no argument.
    """
    return _front_matter_block({OKF_VERSION: OKF_SPEC_VERSION})


def encode_manifest_front_matter(manifest: ManifestFrontMatter) -> str:
    """`theurian-bundle.md`'s front matter: fixed text, a constant, and the digest."""
    return _front_matter_block(
        {
            OKF_TYPE: MANIFEST_TYPE,
            THEURIAN_EXPORT_VERSION: EXPORT_VERSION,
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

#: The inline constructs a label can reopen, escaped after the backslash run is
#: doubled. `]` immediately followed by `(` closes the link early and opens a
#: second, attacker-chosen one -- the failure decision 2 names. `<` is that same
#: failure through a different door: `<https://...>` is a CommonMark autolink
#: and nests a second anchor inside the entry's own, and `<img ...>` is raw
#: inline HTML. `(` on its own, with no preceding unescaped `]`, carries no
#: special meaning here and is left alone.
_LINK_TEXT_ESCAPED: Final = ("[", "]", "<", ">")


def escape_markdown_link_text(text: str) -> str:
    """Escape *text* for a Markdown link's bracketed label (index entries).

    Folded to one line first, which is the precondition the rest of this rule is
    valid under: every construct it escapes is an inline one, and a second
    physical line inside a label ends the paragraph the link sits in -- which
    orphans the real link and lets the next line open any block construct at
    all.

    On that one line the escaped set is :data:`_LINK_TEXT_ESCAPED`, preceded by
    doubling the caller's own backslashes so that none of them can pair with an
    escape this function inserts and cancel it out.
    """
    escaped = _single_line(text).replace("\\", "\\\\")
    for character in _LINK_TEXT_ESCAPED:
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


#: CommonMark's ATX-heading rule allows up to three leading spaces before `#`.
_ATX_HEADING_PREFIX: Final = re.compile(r"^( {0,3})(#+)")


def escape_markdown_list_line(text: str) -> str:
    """Escape *text* for a relation note rendered as its own list line.

    Folded to one line first: the rule below is anchored at the start of the
    string, so it is valid only where the string *is* the line. A note carrying
    a terminator otherwise renders its second line as a block of its own -- a
    `## Relations` sub-heading, an index-shaped list entry, a fence -- past an
    escape that only ever looked at the first.

    On that one line the escaped construct is a leading run of `#`, optionally
    indented as CommonMark's ATX-heading rule allows: unescaped, it reads as a
    heading and splits the enclosing `## Relations` section (decision 2). One
    inserted backslash defeats the whole run, since the line no longer opens
    with `#` at all.
    """
    line = _single_line(text)
    match = _ATX_HEADING_PREFIX.match(line)
    if match is None:
        return line
    indent, hashes = match.group(1), match.group(2)
    return f"{indent}\\{hashes}{line[match.end() :]}"


__all__ = [
    "CONCEPT_FRONT_MATTER_KEY_ORDER",
    "EXPORT_VERSION",
    "LINE_TERMINATORS",
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
