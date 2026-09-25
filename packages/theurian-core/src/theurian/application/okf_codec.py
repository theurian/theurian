"""OKF front-matter codec (ADR-0037 decisions 2, 4, 7): the encoder and decoder.

Shared between the exporter and the importer: the types below are the seam.
This module ships the encoder -- :func:`encode_concept_front_matter`,
:func:`encode_root_index_front_matter`, :func:`encode_manifest_front_matter`
and :func:`sidecar_extension` -- and the decoder, :func:`decode_concept_document`
and :func:`decode_manifest_front_matter`. Every field is a plain string, int or
nested value, never a domain enum or a Theurian entity: OKF `type` values are
not centrally registered (§4.1) and a decoded bundle's `status` need not be
Theurian's own vocabulary, so baking either in as an enum would make the shared
type unusable for decoding an arbitrary bundle. One field is narrower than
that: `generated.by` carries the export tool's own actor form, checked at
construction (see :class:`GeneratedBy`) -- which is exactly why the decoder
never reuses it for `generated` (see :class:`DecodedGeneratedBy` below).

Row text reaches structure through three sites, and :data:`LINE_TERMINATORS` is
the population all three are written against -- the two Markdown sites over that
set and the tab besides. In a YAML front-matter block, a value
carrying any terminator is emitted double-quoted (:func:`_represent_str`), which
writes every terminator as an *escape sequence* rather than as a byte -- so a
row terminator opens no key and renders no column-0 `---` a consumer splitting
on fences could truncate at. PyYAML still breaks a long value across physical
lines at ``width=100``, and that fold is why the property is stated this way and
not as *one physical line*: what carries the no-column-0 property, on every
style PyYAML picks, is the indent alone -- each continuation lands at the block
indent whichever style wrote it. Only the double-quoted style also trails each
continuation with a backslash; a single-quoted or plain fold has none, and the
property holds there too
(`test_a_terminator_free_forgery_folds_with_no_backslash_and_stays_off_column_zero`).
Key: `test_a_forged_terminator_stays_inside_its_own_value_at_every_position`,
whose payloads include one long enough to fold
(`test_the_folding_payload_folds_at_every_position`).

At the two Markdown-syntax sites -- an index entry's link text and a relation
note rendered as a list line -- whitespace is normalized first
(:func:`_normalized`: every terminator and every tab to one space) and then that
site's whole grammar is escaped: :data:`_INLINE` at both, and at the list line,
which owns a line start, the line-start constructs of :data:`_BLOCK_STARTERS`
too. Rendering those two sites into bytes is the exporter's job (a later
assignment); this module only ships the escapes.

Key order in every emitted block is fixed here as a constant of the exporter
version, never a function of the row (decision 2, byte-source family
"Bundle-structural constants"), and so is each value the encoders emit with no
field to carry it: :data:`EXPORT_VERSION`, :data:`MANIFEST_TYPE` and
:data:`OKF_SPEC_VERSION`.

**The decoder never reuses :class:`ConceptFrontMatter`.** That type is the
export *projection*: every `theurian_*` field is required, because the
exporter always writes all thirteen. A bundle the importer reads may be
vanilla OKF carrying none of them (decision 1: import never reads OKF front
matter as governance, so nothing here treats a decoded value as anything but
untrusted data for a caller to map, at its own layer, onto a proposal).
:class:`DecodedConcept` is the importer's own type, with every `theurian_*`
field optional. It reuses :class:`RelationEntry` and :class:`SourceAnchorProjection`
as-is -- both already optional in the right places for a vanilla bundle -- and
adds :class:`DecodedGeneratedBy`, since :class:`GeneratedBy` constrains `by` to
the export tool's own actor form, and :class:`DecodedSourceEntry`, since
:class:`SourceEntry` requires an anchor no vanilla `sources[]` entry carries.

A malformed concept decodes to :class:`ConceptDecodeRefusal` rather than
raising: one bad file must never abort a bundle a caller is walking (ADR-0037
decision 6's "one bad path still yields a proposal for everything else",
applied one level up to a whole concept).

Untrusted front matter is parsed through
:func:`theurian.security.yaml_loading.load_yaml_mapping` (SEC-8): a safe
loader, size-bounded, with no implicit timestamp coercion -- a bundle's
`stale_after` stays the string it was written as.

Pure throughout: no filesystem I/O, no clock, no randomness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Final

import yaml

from theurian.domain.errors import InputTooLargeError, InvariantViolationError
from theurian.domain.values import MediaType
from theurian.security.yaml_loading import load_yaml_mapping

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
#: :func:`escape_markdown_list_line` -- never over `\n` alone: the list-line
#: rule is anchored at the start of a string, and a second physical line inside
#: one row value carries its own leading construct past it.
LINE_TERMINATORS: Final[frozenset[str]] = frozenset("\n\r\x85\u2028\u2029")


def _run_pattern(characters: frozenset[str]) -> re.Pattern[str]:
    r"""One alternation over *characters*, with `\r\n` first so the pair is one run.

    Without that alternative first, a `\r\n` pair matches twice and folds to two
    spaces rather than one.
    """
    return re.compile(
        "\r\n|[" + "".join(re.escape(character) for character in sorted(characters)) + "]"
    )


#: Built from :data:`LINE_TERMINATORS`, so a member added there is quoted by
#: :func:`_represent_str` without a second edit.
_LINE_TERMINATOR_RUN: Final = _run_pattern(LINE_TERMINATORS)


# ---------------------------------------------------------------------------
# Shared types (encoder and decoder alike).
# ---------------------------------------------------------------------------

#: `generated.by`'s tool form (§7). Matched with :meth:`re.Pattern.fullmatch`
#: and not `^...$`: `$` also matches before a trailing newline, so
#: `^theurian/\S+$` accepts `theurian/0.4.0\n` -- a line terminator inside the
#: value §7 fixes as a constant, which is then not that constant. `\S` bounds
#: whitespace and nothing else: a NUL, a zero-width space and a path-shaped
#: `theurian/../x` all still match, which is a decoder's and a path builder's
#: concern rather than this value's -- nothing here builds a path, and the value
#: lands in a YAML scalar like any other.
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
            # Echoing the rejected value is safe here: `by` comes from the
            # exporter's own constant, so a value reaching this branch is a
            # programming mistake rather than row text.
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

    Double-quoted style writes each terminator as an escape sequence (`\r`, `\n`,
    `\N`, `\L`, `\P`) rather than as the byte itself, so no row terminator
    becomes a line break, and it round-trips the value byte-for-byte. PyYAML's
    own style choice does neither for U+0085: it emits the character raw inside
    single quotes, and `safe_load` reads it back as a space.

    It does **not** keep the value on one physical line: PyYAML folds a value
    past ``width=100``
    (`test_a_long_value_folds_at_the_width_dump_yaml_is_given`). What the fold
    preserves is the property the front matter needs -- each continuation line
    is indented to the block indent, behind a predecessor ending in a
    backslash, so no line a value occupies begins at column 0, where a key or a
    `---` fence would have to start. Key to all three:
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


def _present(
    values: dict[str, object], order: tuple[str, ...], *, constant: str
) -> dict[str, object]:
    """*values* re-read through *order*, dropping the absent ones.

    Absence is `None` and is emitted as omission, never as null -- this
    module's one absence convention. An empty list is not an absence: decision 7
    treats `tags`, `sources` and `theurian_relations` as always-present
    projections of a field the revision always carries, sometimes empty.

    *order* decides which keys appear as well as their order, so the two sides
    must be one population: a key built here and unnamed there would be dropped
    from the block without a word. *constant* is *order*'s own name, so the
    refusal can say which one to amend.
    """
    if set(values) != set(order):
        raise InvariantViolationError(
            f"{constant} and the mapping the encoder built are different populations: "
            f"built but unnamed (so dropped from the block) {sorted(set(values) - set(order))}; "
            f"named but unbuilt (so nothing fills it) {sorted(set(order) - set(values))}. "
            f"Amend {constant} in `application/okf_codec.py` to name every key the encoder "
            f"builds, in the order decision 2 fixes, then run `uv run pytest "
            f"packages/theurian-core/tests/unit/test_okf_codec.py`."
        )
    return {key: value for key in order if (value := values[key]) is not None}


def _anchor_mapping(anchor: SourceAnchorProjection) -> dict[str, object]:
    """Every field of *anchor*, emitted through :data:`SOURCE_ANCHOR_KEY_ORDER`.

    Reflected rather than listed so each key string is spelled once, in the
    constant -- and so the population is the projection's own fields: a field
    added to :class:`SourceAnchorProjection` and not named in the constant
    refuses here, instead of being silently unemitted, and a constant member
    that is not a field refuses too. Key:
    `test_the_anchor_key_order_names_exactly_the_projections_own_fields`.
    """
    values: dict[str, object] = {
        field.name: getattr(anchor, field.name) for field in fields(anchor)
    }
    return _present(values, SOURCE_ANCHOR_KEY_ORDER, constant="SOURCE_ANCHOR_KEY_ORDER")


def _source_entry_mapping(entry: SourceEntry) -> dict[str, object]:
    return {"resource": entry.resource, THEURIAN_ANCHOR: _anchor_mapping(entry.anchor)}


_RELATION_KEY_ORDER: Final[tuple[str, ...]] = ("type", "target", "note")


def _relation_entry_mapping(entry: RelationEntry) -> dict[str, object]:
    return _present(
        {"type": entry.type, "target": entry.target, "note": entry.note},
        _RELATION_KEY_ORDER,
        constant="_RELATION_KEY_ORDER",
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
    return _front_matter_block(
        _present(values, CONCEPT_FRONT_MATTER_KEY_ORDER, constant="CONCEPT_FRONT_MATTER_KEY_ORDER")
    )


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
#
# Each site's escape is that site's *whole* grammar: whitespace normalized
# ahead of both, then the shared inline set at both, then -- only at the list
# line, which owns a line start -- the constructs a line start opens.
# ---------------------------------------------------------------------------

#: Folded to one space ahead of both escapes: every :data:`LINE_TERMINATORS`
#: member, and the tab.
_MARKDOWN_WHITESPACE_RUN: Final = _run_pattern(LINE_TERMINATORS | frozenset("\t"))


def _normalized(text: str) -> str:
    """*text* with every line terminator and every tab folded to one space.

    The terminators because both escapes are single-line rules: a second
    physical line inside one row value carries its own leading construct past a
    rule that only ever looked at the first. The tab because CommonMark counts
    indentation in columns after tab expansion, so `\\t#` is an indented ATX
    heading that a rule written in literal spaces cannot see at all -- and after
    this fold there are no tabs left for such a rule to miss.

    Never refuses, for the reason :func:`sidecar_extension` has a fallback arm
    instead of a raise: a gate-cleared row is never refused, so hostile text is
    made inert rather than rejected.
    """
    return _MARKDOWN_WHITESPACE_RUN.sub(" ", text)


#: The inline constructs escaped at **both** Markdown sites, each member named
#: by what it opens:
#:
#: * `[` -- a link label, which nests a second link inside the entry's own.
#: * `]` -- closes a label early; followed by `(` it opens an attacker-chosen
#:   destination, the failure decision 2 names.
#: * `<` -- a CommonMark autolink (`<https://evil>`) and raw inline HTML
#:   (`<img src=x>`); at a line start the same character opens an HTML *block*,
#:   so escaping it here shuts that door for the list line too.
#: * `>` -- closes both of those.
#: * a backtick -- a code span, which runs to the next backtick on the rendered
#:   line and swallows everything between, an index entry's own
#:   `](destination)` included.
#:
#: `(` is not a member: with every `]` escaped it opens no link destination.
_INLINE: Final[tuple[str, ...]] = ("[", "]", "<", ">", "`")


def _escape_inline(text: str) -> str:
    """Every :data:`_INLINE` member backslash-escaped, backslash runs doubled first.

    The doubling is what stops a caller's own backslash from pairing with an
    escape inserted here and cancelling it -- which would resurrect the very
    construct being closed.
    """
    escaped = text.replace("\\", "\\\\")
    for character in _INLINE:
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


#: What the list-line site has and the link-text site does not: CommonMark's
#: §4 *leaf* block openers this codec's inline pass does not already reach --
#: thematic break (4.1), ATX heading (4.2), setext heading (4.3), fenced code
#: (4.5) -- together with its §5 *container* block openers, block quote and
#: list item, which open a structure *around* the note rather than a leaf
#: inside it.
#:
#: * `#` -- an ATX heading (4.2), which splits the enclosing `## Relations`
#:   section.
#: * a backtick, `~` -- a fenced code block (4.5).
#: * `>` -- a block quote (5.1).
#: * `-`, `+`, `*` -- a list marker (5.2), so a forged relation row; `-`, `*`
#:   and `_` also a thematic break (4.1), which is not a list item at all and
#:   so deletes the note's own row from the rendered list.
#: * `=` -- a setext heading underline (4.3) for the line before it. Escaped
#:   wherever it opens the line, which is broader than the construct (that
#:   needs the whole line), because the line the note is rendered behind is
#:   the exporter's.
#:
#: Five §4 subsections have no character here, not three:
#:
#: * indented code (4.4) -- an indentation, not a character; the block still
#:   *forms*, inside the note's own list item -- the adversarial review
#:   measured it: the item's own `li` count never moves and it gains one
#:   `code_block` -- so :func:`_normalized` plus the every-indent rule below
#:   leave it inert rather than cover it.
#: * the HTML block (4.6) -- opens with `<`, escaped by :data:`_INLINE` before
#:   this rule runs.
#: * the link reference definition (4.7) -- opens with `[`, escaped by
#:   :data:`_INLINE` before this rule runs, exactly the HTML block's pattern
#:   (`test_a_link_reference_definition_note_keeps_its_own_row`).
#: * the paragraph (4.8) -- what a line opening none of these is.
#: * the blank line (4.9) -- impossible after :func:`_normalized`: no
#:   terminator survives.
#:
#: A backtick and `>` are stated here a second time for that reason (already
#: escaped by :data:`_INLINE`, so unreachable through this tuple): this one
#: states the list-line site's own grammar rather than the other pass's
#: leftovers -- `test_the_recorded_block_starter_population_is_the_codecs_own_tuple`
#: is what holds the two spellings to that vocabulary rather than letting
#: either drift.
_BLOCK_STARTERS: Final[tuple[str, ...]] = ("#", "`", "~", ">", "-", "+", "*", "=", "_")

#: Group 1 of each is the character a backslash goes in front of, which is what
#: lets :func:`escape_markdown_list_line` try them in turn. The leading run is
#: ` *` rather than ` {0,3}`: how many columns of indentation put the note inside
#: an indented code block instead depends on the list prefix the *exporter*
#: renders it behind, so no column window is exact here and the rule fires at
#: every indent.
_LINE_START_BLOCK: Final = re.compile("^ *([" + re.escape("".join(_BLOCK_STARTERS)) + "])")

#: An ordered-list marker: a run of digits closed by `.` or `)`. The escape goes
#: in front of the closing character and not the digits, because CommonMark
#: escapes punctuation only -- `\1` is a literal backslash and still leaves `1.`
#: opening a list.
_LINE_START_ORDERED: Final = re.compile(r"^ *[0-9]+([.)])")


def escape_markdown_link_text(text: str) -> str:
    """Escape *text* for a Markdown link's bracketed label (index entries).

    Whitespace is normalized first, which is the precondition the rest is valid
    under: every construct escaped here is an inline one, and a second physical
    line inside a label ends the paragraph the link sits in -- which orphans the
    real link and lets the next line open any block construct at all. This site
    has no line start of its own, so :data:`_INLINE` is the whole of its rule.
    """
    return _escape_inline(_normalized(text))


def escape_markdown_list_line(text: str) -> str:
    """Escape *text* for a relation note rendered as its own list line.

    This site owns a line start, so it takes :data:`_INLINE` *and* the line-start
    rule: after the normalized line's leading spaces, a :data:`_BLOCK_STARTERS`
    member is escaped, or an ordered-list marker is escaped at its `.` or `)`.
    One backslash defeats the whole construct, since the line no longer opens
    with it -- a `#` run stops splitting the section, and a `---` stops being a
    thematic break, which is not a list item at all and would take this row out
    of the rendered list.

    The cost of firing at every indent is one visible backslash in front of a
    note that was indented four spaces or more *and* began with one of those
    characters; the alternative is a column window whose exactness would depend
    on the exporter's list prefix.
    """
    line = _escape_inline(_normalized(text))
    opener = _LINE_START_BLOCK.match(line) or _LINE_START_ORDERED.match(line)
    if opener is None:
        return line
    point = opener.start(1)
    return f"{line[:point]}\\{line[point:]}"


# ---------------------------------------------------------------------------
# The decoder (S3): every theurian_* field optional, a malformed concept
# refuses without raising.
# ---------------------------------------------------------------------------

_FRONT_MATTER_FENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL
)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedGeneratedBy:
    """OKF `generated`, decoded (§5.2, §7): unconstrained, unlike :class:`GeneratedBy`.

    That type's `by` is checked at construction against the export tool's own
    actor form (`theurian/<version>`) -- a rule about what *this exporter*
    writes, not about what a decoded bundle may contain. A vanilla bundle's
    `generated.by` can be `human:<id>`, `process:<id>`, or another producer's
    tool form entirely (§7): nothing says another producer's actor looks like
    Theurian's, so the decoder never raises on it.
    """

    by: str
    at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedSourceEntry:
    """One decoded OKF `sources[]` entry.

    `anchor` is `None` for a vanilla bundle's entry, which carries no
    `theurian_anchor` -- unlike :class:`SourceEntry`, whose anchor the exporter
    always sets.
    """

    resource: str
    anchor: SourceAnchorProjection | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedConcept:
    """One concept document's front matter, decoded (ADR-0037 decision 6).

    `kind`, `title` and `status` are OKF's own required fields for a concept
    this importer can act on; every other OKF field and every `theurian_*`
    extension is optional, since a vanilla bundle carries none of the latter.
    Values here are untrusted data, never governance (decision 1): nothing in
    this module reads one to decide anything.
    """

    kind: str
    title: str
    status: str
    labels: tuple[str, ...] = ()
    stale_after: str | None = None
    generated: DecodedGeneratedBy | None = None
    sources: tuple[DecodedSourceEntry, ...] = ()
    theurian_export_version: int | None = None
    theurian_item_id: str | None = None
    theurian_revision_id: str | None = None
    theurian_status: str | None = None
    theurian_namespace: str | None = None
    theurian_owner: str | None = None
    theurian_trust_level: str | None = None
    theurian_sensitivity: str | None = None
    theurian_content_type: str | None = None
    theurian_body_file: str | None = None
    theurian_relations: tuple[RelationEntry, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedConceptDocument:
    """One concept file, decoded: its front matter and the body text that follows."""

    front_matter: DecodedConcept
    body: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecodedManifest:
    """`theurian-bundle.md`'s front matter, decoded. Both fields optional.

    A vanilla OKF bundle carries no manifest at all; even a Theurian-exported
    one is read as untrusted data like every other concept (decision 1).
    """

    theurian_export_version: int | None = None
    theurian_bundle_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ConceptDecodeRefusal:
    """Why one concept file could not be decoded. Never raised -- returned.

    A malformed file is one bad member of a bundle, not a reason to abort the
    walk: the caller records this and moves to the next file.
    """

    reason: str


def _split_front_matter(text: str) -> tuple[str, str] | None:
    """The front-matter block's raw YAML and the body after it, or `None`.

    `None` when *text* opens with no `---` fence: OKF requires no front matter
    at all beyond a concept's own `type` (§4.1), so a fenceless file is not
    itself malformed -- it is simply not one this importer's stricter
    requirement (`kind`/`title`/`status`) can act on, which
    :func:`decode_concept_document` reports as a refusal.
    """
    match = _FRONT_MATTER_FENCE_PATTERN.match(text)
    if match is None:
        return None
    return match.group(1), text[match.end() :]


def _decode_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _optional_str(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) else None


def _optional_int(mapping: dict[str, object], key: str) -> int | None:
    value = mapping.get(key)
    # `bool` is an `int` subclass; a front-matter `theurian_export_version:
    # true` is not a version number and must not decode as one.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(item for item in _decode_list(value) if isinstance(item, str))


def _decode_generated(value: object) -> DecodedGeneratedBy | None:
    if not isinstance(value, dict):
        return None
    by, at = value.get("by"), value.get("at")
    return DecodedGeneratedBy(by=by, at=at) if isinstance(by, str) and isinstance(at, str) else None


def _decode_anchor(value: object) -> SourceAnchorProjection | None:
    """`provider` required; the other five fields optional and independent.

    Matches the encoder's own convention: an unset field is omitted from
    `theurian_anchor` rather than emitted null, so a vanilla or hand-authored
    bundle's anchor may carry `provider` alone. A malformed optional field
    decodes to `None` rather than refusing the whole anchor, the same rule
    every other optional front-matter field in this module follows.
    """
    if not isinstance(value, dict):
        return None
    provider = value.get("provider")
    if not isinstance(provider, str):
        return None
    return SourceAnchorProjection(
        provider=provider,
        repository=_optional_str(value, "repository"),
        commit_sha=_optional_str(value, "commit_sha"),
        file_path=_optional_str(value, "file_path"),
        line_start=_optional_int(value, "line_start"),
        line_end=_optional_int(value, "line_end"),
    )


def _decode_source_entry(value: object) -> DecodedSourceEntry | None:
    if not isinstance(value, dict):
        return None
    resource = value.get("resource")
    if not isinstance(resource, str):
        return None
    return DecodedSourceEntry(resource=resource, anchor=_decode_anchor(value.get(THEURIAN_ANCHOR)))


def _decode_relation_entry(value: object) -> RelationEntry | None:
    if not isinstance(value, dict):
        return None
    type_, target = value.get("type"), value.get("target")
    if not isinstance(type_, str) or not isinstance(target, str):
        return None
    note = value.get("note")
    return RelationEntry(type=type_, target=target, note=note if isinstance(note, str) else None)


def _decode_concept_mapping(mapping: dict[str, object]) -> DecodedConcept | ConceptDecodeRefusal:
    kind = _optional_str(mapping, OKF_TYPE)
    title = _optional_str(mapping, OKF_TITLE)
    status = _optional_str(mapping, OKF_STATUS)
    if not kind or not title or not status:
        missing = [
            name
            for name, value in ((OKF_TYPE, kind), (OKF_TITLE, title), (OKF_STATUS, status))
            if not value
        ]
        return ConceptDecodeRefusal(
            reason=f"missing or empty required key(s): {', '.join(missing)}"
        )
    return DecodedConcept(
        kind=kind,
        title=title,
        status=status,
        labels=_string_tuple(mapping.get(OKF_TAGS)),
        stale_after=_optional_str(mapping, OKF_STALE_AFTER),
        generated=_decode_generated(mapping.get(OKF_GENERATED)),
        sources=tuple(
            source_entry
            for raw in _decode_list(mapping.get(OKF_SOURCES))
            if (source_entry := _decode_source_entry(raw)) is not None
        ),
        theurian_export_version=_optional_int(mapping, THEURIAN_EXPORT_VERSION),
        theurian_item_id=_optional_str(mapping, THEURIAN_ITEM_ID),
        theurian_revision_id=_optional_str(mapping, THEURIAN_REVISION_ID),
        theurian_status=_optional_str(mapping, THEURIAN_STATUS),
        theurian_namespace=_optional_str(mapping, THEURIAN_NAMESPACE),
        theurian_owner=_optional_str(mapping, THEURIAN_OWNER),
        theurian_trust_level=_optional_str(mapping, THEURIAN_TRUST_LEVEL),
        theurian_sensitivity=_optional_str(mapping, THEURIAN_SENSITIVITY),
        theurian_content_type=_optional_str(mapping, THEURIAN_CONTENT_TYPE),
        theurian_body_file=_optional_str(mapping, THEURIAN_BODY_FILE),
        theurian_relations=tuple(
            relation_entry
            for raw in _decode_list(mapping.get(THEURIAN_RELATIONS))
            if (relation_entry := _decode_relation_entry(raw)) is not None
        ),
    )


def decode_concept_document(text: str) -> DecodedConceptDocument | ConceptDecodeRefusal:
    """Decode one concept file's front matter and body.

    Never raises: a fence that will not parse, front matter that is not a
    mapping, front matter past `load_yaml_mapping`'s own 4 MiB cap (smaller
    than `read_source_file`'s 8 MiB file-level cap, so a file under the file
    cap can still overrun this one), or a mapping missing
    `type`/`title`/`status` each come back as a :class:`ConceptDecodeRefusal`
    naming why, so a caller walking many files can record one and move on
    rather than aborting the bundle.
    """
    split = _split_front_matter(text)
    if split is None:
        return ConceptDecodeRefusal(reason="carries no front-matter block")
    raw, body = split
    try:
        loaded = load_yaml_mapping(raw)
    except InputTooLargeError as exc:
        # Safe to echo: the message is `str`/`int` limits and counts, never a
        # path or bundle text (`domain/errors.py::InputTooLargeError`).
        return ConceptDecodeRefusal(reason=str(exc))
    except (ValueError, yaml.YAMLError) as exc:
        return ConceptDecodeRefusal(reason=f"front matter is not valid YAML: {exc}")
    decoded = _decode_concept_mapping(loaded)
    if isinstance(decoded, ConceptDecodeRefusal):
        return decoded
    return DecodedConceptDocument(front_matter=decoded, body=body)


def decode_manifest_front_matter(text: str) -> DecodedManifest | None:
    """Decode `theurian-bundle.md`'s front matter, or `None` if it will not parse.

    Both fields are optional (decision 2's manifest carries no other key), so
    this never reports *why* a decode failed -- there is nothing beyond the two
    constants for a caller to act on either way.
    """
    split = _split_front_matter(text)
    if split is None:
        return None
    raw, _ = split
    try:
        loaded = load_yaml_mapping(raw)
    except (ValueError, yaml.YAMLError):
        return None
    return DecodedManifest(
        theurian_export_version=_optional_int(loaded, THEURIAN_EXPORT_VERSION),
        theurian_bundle_digest=_optional_str(loaded, THEURIAN_BUNDLE_DIGEST),
    )


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
    "ConceptDecodeRefusal",
    "ConceptFrontMatter",
    "DecodedConcept",
    "DecodedConceptDocument",
    "DecodedGeneratedBy",
    "DecodedManifest",
    "DecodedSourceEntry",
    "GeneratedBy",
    "ManifestFrontMatter",
    "RelationEntry",
    "SourceAnchorProjection",
    "SourceEntry",
    "decode_concept_document",
    "decode_manifest_front_matter",
    "encode_concept_front_matter",
    "encode_manifest_front_matter",
    "encode_root_index_front_matter",
    "escape_markdown_link_text",
    "escape_markdown_list_line",
    "sidecar_extension",
]
