"""The OKF front-matter codec, encoder and decoder (ADR-0037 decisions 2, 4, 6, 7).

Scoped to this module's own functions and types. The bundle-level properties --
the two-corpora battery, cross-run determinism over a whole export, the
reserved-name collision escape -- are a later assignment's exporter batteries.

Three families are pinned here.

**Row text against structure**, over :data:`_TERMINATOR_CASES`: every string
position a :class:`ConceptFrontMatter` carries is encoded and re-read by
:func:`test_a_forged_terminator_stays_inside_its_own_value_at_every_position`,
at both payload lengths -- the second is long enough that PyYAML folds the value
across physical lines, and
:func:`_assert_no_value_line_reaches_column_zero` is what says the fold reaches
no structure. Both Markdown escapes are driven over the same terminator set,
and over the two escape populations
(:data:`_INLINE_MEMBERS`, :data:`_BLOCK_STARTER_MEMBERS`) at every
:data:`_LEADING_WHITESPACE` variant. The position list is not hand-written --
:func:`_string_positions` reflects over the four dataclasses, so a new string
field fails
:func:`test_every_string_position_is_either_forge_scanned_or_recorded_unforgeable`
until it is either scanned or recorded as constrained at construction.

**Every population here is an independent literal, never read from the constant
it pins.** :data:`_TERMINATOR_CASES` was spelled ``(*sorted(LINE_TERMINATORS),)``
for one round, which made shrinking that constant to ``{"\\n"}`` drop every
non-LF case from its own parametrization rather than fail;
:func:`test_the_recorded_terminator_population_is_the_five_single_characters` is
the equality that reddens instead.

**The emission shape.** Three golden blocks hold the exact bytes of a fully
populated concept, of the manifest and of the root index, each value but
``generated.at`` naming its own field so a swapped pair reads as a swap rather
than as two plausible strings.
:func:`test_each_dump_setting_moves_the_golden_bytes` and
:func:`test_a_long_value_folds_at_the_width_dump_yaml_is_given` are why those
bytes pin the serializer settings and not only the field mapping.

**The two key vocabularies**, read against ADR-0037 itself through
``assert_the_adr_states`` rather than against a second copy of the same literals.

No Markdown renderer is imported. ``grep -n markdown-it-py pyproject.toml
packages/theurian-core/pyproject.toml`` answers nothing, and in ``uv.lock`` the
same grep answers two lines -- the package's own stanza, and ``rich``'s
``dependencies`` -- so a pin resting on it would rest on a package nothing here
declares. Every Markdown claim below is therefore structural: what the escape
returns, not what a renderer makes of it.

**The decoder's own tests are scoped narrower**, at the tail of this module:
that it round-trips a Theurian-exported concept and accepts a vanilla one
carrying none of the `theurian_*` extensions, without touching the properties
above.
"""

from __future__ import annotations

import dataclasses
import re
import textwrap
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Final

import pytest
import yaml
from adr_0037_support import assert_the_adr_states

from theurian.application import okf_codec
from theurian.application.okf_codec import (
    CONCEPT_FRONT_MATTER_KEY_ORDER,
    EXPORT_VERSION,
    LINE_TERMINATORS,
    OKF_KEYS,
    OKF_SOURCES,
    OKF_SPEC_VERSION,
    OKF_VERSION,
    SOURCE_ANCHOR_KEY_ORDER,
    THEURIAN_KEYS,
    ConceptDecodeRefusal,
    ConceptFrontMatter,
    DecodedConceptDocument,
    GeneratedBy,
    ManifestFrontMatter,
    RelationEntry,
    SourceAnchorProjection,
    SourceEntry,
    _present,
    decode_concept_document,
    decode_manifest_front_matter,
    encode_concept_front_matter,
    encode_manifest_front_matter,
    encode_root_index_front_matter,
    escape_markdown_link_text,
    escape_markdown_list_line,
    sidecar_extension,
)
from theurian.domain.errors import InvariantViolationError
from theurian.domain.values import MediaType

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

pytestmark = pytest.mark.unit

_GENERATED = GeneratedBy(by="theurian/0.4.0", at="2026-09-24T12:00:00+00:00")

#: The six spellings a line terminator reaches a structural site as: the five
#: single characters the codec records, plus the ``\r\n`` pair, which folds to
#: one space rather than two and so is its own case. Spelled out here, never read
#: from ``LINE_TERMINATORS`` --
#: :func:`test_the_recorded_terminator_population_is_the_five_single_characters`
#: is the one place the two are compared.
_TERMINATOR_CASES: Final[tuple[str, ...]] = ("\n", "\r", "\r\n", "\x85", "\u2028", "\u2029")

#: Every scan below splits on this rather than on ``\n``: ``re.MULTILINE``'s
#: ``^`` matches after ``\n`` and after nothing else, so an instrument built on
#: it is blind to exactly the terminators the codec was found passing through.
#: Longest first, so the ``\r\n`` pair is one split and not two.
_TERMINATOR_SPLIT = re.compile(
    "|".join(re.escape(case) for case in sorted(_TERMINATOR_CASES, key=len, reverse=True))
)

#: A forged row string that carries both structural attacks at once: a
#: governance-looking ``key: value`` line, and a bare ``---`` that a consumer
#: splitting front matter on fences would truncate at.
_FORGED = "value{t}---{t}theurian_sensitivity: public"

#: :data:`_FORGED` padded on both sides past ``width=100``, so PyYAML folds the
#: value across physical lines with the forgery inside the fold. This is the
#: payload the *one physical line* reading of ``_represent_str`` did not have.
_FORGED_LONG = "padding " * 12 + _FORGED + " padding" * 12

#: Both payloads, so every forge scan runs at a length that folds and one that
#: does not.
_FORGED_PAYLOADS: Final[tuple[str, ...]] = (_FORGED, _FORGED_LONG)

#: :data:`_FORGED_LONG` with its terminator positions filled by an ordinary
#: space instead. Every case in :data:`_FORGED_PAYLOADS` carries a terminator,
#: so :func:`_represent_str` forces double-quoted style on all of them --
#: the only fold the column-0 scan below has ever driven. A value with no
#: terminator goes through PyYAML's own style choice instead, and this
#: payload's ``theurian_sensitivity: `` -- colon then space -- rules out plain
#: (unquoted) style too, so what PyYAML resolves it to is single-quoted:
#: still a fold, still no trailing backslash on a continuation line, and so
#: still a style the scan had never exercised.
_FORGED_PLAIN: Final = _FORGED_LONG.format(t=" ")

_FENCE_LINE = re.compile(r"^(---|\.\.\.)\s*$")

#: The inline constructs neither Markdown site may leave live, spelled as
#: literals rather than imported from the module under test: a check that ranged
#: over the production set would range over one fewer the moment a member was
#: dropped, and still pass.
_INLINE_MEMBERS: Final[tuple[str, ...]] = ("[", "]", "<", ">", "`")

#: The characters that open a block construct at a line start, spelled as
#: literals for the same reason. The ordered-list marker is not a character but
#: a shape, so it has its own cases below.
_BLOCK_STARTER_MEMBERS: Final[tuple[str, ...]] = ("#", "`", "~", ">", "-", "+", "*", "=", "_")

#: What a note's indentation can be spelled as before the block-starter rule has
#: to see through it. ``"\t"`` and ``" \t"`` are the round-2 faces: CommonMark
#: counts columns after tab expansion, so both indent a heading run that a rule
#: written in literal spaces reads as no indentation at all. ``"    "`` is past
#: any three-space window, where the escape fires anyway.
_LEADING_WHITESPACE: Final[tuple[str, ...]] = ("", " ", "\t", " \t", "   ", "    ")

#: A line start that still opens a block construct: a leading
#: :data:`_BLOCK_STARTER_MEMBERS` member, or a digit run closed by ``.`` or
#: ``)``. An escaped one does not match -- the backslash sits between the line
#: start and the character.
_LIST_LINE_OPENER = re.compile(
    "^ *(?:[" + re.escape("".join(_BLOCK_STARTER_MEMBERS)) + r"]|[0-9]+[.)])"
)

#: Every occurrence of an inline member, with the backslash run in front of it.
_INLINE_OCCURRENCE = re.compile(r"(\\*)([" + re.escape("".join(_INLINE_MEMBERS)) + "])")

#: A top-level ``key:`` is anything up to the first colon with no whitespace in
#: it, not only ``[A-Za-z_]+``: a forged key the encoder must never open can be
#: spelled with a digit or a dash, and a scan blind to those reports a clean
#: block for a forgery it simply could not see.
_TOP_LEVEL_KEY = re.compile(r"^([^\s:]+):")


def _concept(**overrides: object) -> ConceptFrontMatter:
    fields: dict[str, object] = {
        "kind": "architecture",
        "title": "Authentication and authorization policy",
        "status": "stable",
        "theurian_status": "approved",
        "generated": _GENERATED,
        "theurian_item_id": "architecture.auth-policy",
        "theurian_revision_id": "01K1REV00101234567890ABCDE",
        "theurian_namespace": "backend",
        "theurian_owner": "platform-team",
        "theurian_trust_level": "reviewed",
        "theurian_sensitivity": "internal",
        "theurian_content_type": "text/markdown",
    }
    fields.update(overrides)
    return ConceptFrontMatter(**fields)  # type: ignore[arg-type]


def _populated(**overrides: object) -> ConceptFrontMatter:
    """A concept carrying every optional field, so the expected top-level key
    list is the whole of :data:`CONCEPT_FRONT_MATTER_KEY_ORDER` for every case.
    """
    base: dict[str, object] = {
        "labels": ("a-label",),
        "stale_after": "2027-01-01",
        "sources": (_source(),),
        "theurian_body_file": "auth-policy.json",
        "theurian_relations": (RelationEntry(type="relates_to", target="x.y", note="n"),),
    }
    return _concept(**{**base, **overrides})


def _anchor(**overrides: object) -> SourceAnchorProjection:
    fields: dict[str, object] = {
        "provider": "github",
        "repository": "acme/a",
        "commit_sha": "deadbeef",
        "file_path": "a.md",
        "line_start": 1,
        "line_end": 2,
    }
    fields.update(overrides)
    return SourceAnchorProjection(**fields)  # type: ignore[arg-type]


def _source(*, resource: str = "https://example.com/a", **anchor: object) -> SourceEntry:
    return SourceEntry(resource=resource, anchor=_anchor(**anchor))


def _front_matter_mapping(block: str) -> dict[str, Any]:
    """Parse an encoded block's YAML body, stripping the two `---` fences."""
    assert block.startswith("---\n") and block.endswith("---\n"), (
        f"{block!r} is not fenced front matter"
    )
    return dict(yaml.safe_load(block[len("---\n") : -len("---\n")]))


def _physical_lines(block: str) -> list[str]:
    """*block* split on every :data:`_TERMINATOR_CASES` member, not on ``\\n``.

    Splitting on the independently spelled population is what makes the key and
    fence scans below see what a `\\r`- or NEL-split consumer sees -- and see it
    whatever ``LINE_TERMINATORS`` says, which is the point of not reading the
    instrument off the constant under test.
    """
    return _TERMINATOR_SPLIT.split(block)


def _top_level_keys(block: str) -> list[str]:
    """Every unindented ``key:`` in *block*, in document order.

    Distinct from parsing the YAML into a dict: a dict cannot show two entries
    sharing one key, so this is what actually answers "did a second
    `theurian_sensitivity:` appear", regardless of what the parser resolves it to.
    """
    return [
        match.group(1) for line in _physical_lines(block) if (match := _TOP_LEVEL_KEY.match(line))
    ]


def _assert_no_value_line_reaches_column_zero(
    block: str, *, order: tuple[str, ...], sequence_items: int
) -> None:
    """Every physical line of *block* is one the encoder wrote, never one a value did.

    Four arms, total over the block: the two ``---`` fences; a top-level key
    from *order*; a block-sequence entry at the emitter's own indent, counted
    against *sequence_items* so a folded continuation cannot hide behind this
    arm; and an indented line, which is every nested mapping line and every
    continuation PyYAML's ``width=100`` fold writes.

    The complement of :func:`_top_level_keys`: that scan catches a forged line
    whose text *is* a key the encoder also writes, this one catches a forged
    line whose text is anything else.
    """
    lines = [line for line in _physical_lines(block) if line]

    assert lines[0] == "---" and lines[-1] == "---", block
    body = lines[1:-1]
    assert len([line for line in body if line.startswith("- ")]) == sequence_items, block

    for line in body:
        if line.startswith((" ", "- ")):
            continue
        key = _TOP_LEVEL_KEY.match(line)
        assert key is not None and key.group(1) in order, f"{line!r} is in no arm:\n{block}"


def _assert_every_inline_member_is_escaped(escaped: str) -> None:
    """No :data:`_INLINE_MEMBERS` member is left live anywhere in *escaped*.

    Live means reachable by a renderer, which is a parity question rather than a
    presence one: what makes CommonMark consume a backslash run as an escape *of
    the delimiter behind it* is the run's odd length, so an even run -- a
    caller's own backslash pairing with an inserted one -- leaves the construct
    open. Both halves are asserted: every occurrence is found, and every one of
    them sits behind an odd run.
    """
    found = _INLINE_OCCURRENCE.findall(escaped)

    assert len(found) == sum(escaped.count(member) for member in _INLINE_MEMBERS), escaped
    for run, member in found:
        assert len(run) % 2 == 1, f"{member!r} at even parity in {escaped!r}"


def _assert_opens_no_block_construct(escaped: str) -> None:
    """*escaped* opens no block construct, and carries no tab that could.

    The tab is half the assertion because CommonMark counts indentation in
    columns after tab expansion: a rule written in literal spaces sees no
    indentation in ``"\\t## Relations"`` at all, which is how the heading forgery
    round 1 closed came back.
    """
    assert "\t" not in escaped, repr(escaped)
    assert _LIST_LINE_OPENER.match(escaped) is None, repr(escaped)


def _string_values(value: object) -> Iterator[str]:
    """Every string *value* in a parsed block, keys excluded.

    Keys are excluded on purpose: a row string that became a key is the forgery,
    so it must not count as the value having survived.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


# ---------------------------------------------------------------------------
# Key vocabulary, read against the ADR rather than against a second literal.
# ---------------------------------------------------------------------------


def test_the_theurian_key_vocabulary_is_the_thirteen_keys_the_adr_spells() -> None:
    spelled = ", ".join(f"`{key}`" for key in THEURIAN_KEYS)

    assert_the_adr_states(
        f"so S2 has nothing to invent: {spelled}.",
        because=(
            "ADR-0037's *Neutral* third bullet fixes these spellings in this order. The "
            "sentence is derived from the constant, so a renamed or reordered key reddens "
            "here -- where a second copy of the same literals would pass while the "
            "document and the code disagreed."
        ),
    )


def test_the_okf_key_vocabulary_is_the_eight_keys_the_export_emits() -> None:
    emitted = ", ".join(
        f"`{key}[]`" if key == OKF_SOURCES else f"`{key}`" for key in OKF_KEYS if key != OKF_VERSION
    )

    assert_the_adr_states(
        "the OKF-side emissions",
        because=(
            "The Compliance section's emission-inventory sentence names this population as "
            "the walk's subject. Its own clause, so rewording the enumeration beside it "
            "does not redden the claim that the population exists."
        ),
    )
    assert_the_adr_states(
        f"{emitted} and the body.",
        because=(
            "Seven of the eight keys are spelled in that sentence, `sources` with its "
            "`[]`. Derived from the constant, so adding an OKF-side emission the ADR does "
            "not record reddens here."
        ),
    )
    assert_the_adr_states(
        f'`{OKF_VERSION}: "{OKF_SPEC_VERSION}"` on the root `index.md`, fixed by this ADR '
        f"from the spec version it targets",
        because=(
            "The eighth key is a bundle-structural constant instead, recorded with its "
            "value in decision 2's byte-source table. Key and value are both derived from "
            "the code, so bumping the spec version reddens until the table moves with it."
        ),
    )


# ---------------------------------------------------------------------------
# Golden bytes: the field-to-key mapping, in full, for all three encoders.
# ---------------------------------------------------------------------------

#: Every value but ``generated.at`` names its own field, so a swapped pair reads
#: as a swap rather than as two plausible strings; that one is timestamp-shaped,
#: so the golden also pins the quoting PyYAML gives it. Three more properties
#: ride on the specific values:
#: the title carries CJK (so ``allow_unicode`` moves these bytes), the second
#: source carries a provider-only anchor (so the anchor's omission is in the
#: bytes), and the two relations share a `(type, target)` pair (so the note tie
#: break is too).
_GOLDEN_CONCEPT: Final = ConceptFrontMatter(
    kind="kind-value",
    title="title-value 署名付きトークンを持つ",
    labels=("label-b", "label-a"),
    status="status-value",
    theurian_status="theurian-status-value",
    stale_after="stale-after-value",
    generated=GeneratedBy(by="theurian/9.9.9-by-value", at="2026-01-02T03:04:05+00:00"),
    sources=(
        SourceEntry(
            resource="resource-value-of-the-first-source",
            anchor=SourceAnchorProjection(
                provider="provider-value",
                repository="repository-value",
                commit_sha="commit-sha-value",
                file_path="file-path-value",
                line_start=11,
                line_end=22,
            ),
        ),
        SourceEntry(
            resource="resource-value-of-the-second-source-which-carries-a-provider-only-anchor",
            anchor=SourceAnchorProjection(provider="provider-value-of-the-second-anchor"),
        ),
    ),
    theurian_item_id="item-id-value",
    theurian_revision_id="revision-id-value",
    theurian_namespace="namespace-value",
    theurian_owner="owner-value",
    theurian_trust_level="trust-level-value",
    theurian_sensitivity="sensitivity-value",
    theurian_content_type="content-type-value",
    theurian_body_file="body-file-value",
    theurian_relations=(
        RelationEntry(type="relation-type-value", target="relation-target-value", note="note-b"),
        RelationEntry(type="relation-type-value", target="relation-target-value", note="note-a"),
    ),
)

_GOLDEN_CONCEPT_BLOCK: Final = textwrap.dedent("""\
    ---
    type: kind-value
    title: title-value 署名付きトークンを持つ
    tags:
    - label-b
    - label-a
    status: status-value
    stale_after: stale-after-value
    generated:
      by: theurian/9.9.9-by-value
      at: '2026-01-02T03:04:05+00:00'
    sources:
    - resource: resource-value-of-the-first-source
      theurian_anchor:
        provider: provider-value
        repository: repository-value
        commit_sha: commit-sha-value
        file_path: file-path-value
        line_start: 11
        line_end: 22
    - resource: resource-value-of-the-second-source-which-carries-a-provider-only-anchor
      theurian_anchor:
        provider: provider-value-of-the-second-anchor
    theurian_export_version: 1
    theurian_item_id: item-id-value
    theurian_revision_id: revision-id-value
    theurian_status: theurian-status-value
    theurian_namespace: namespace-value
    theurian_owner: owner-value
    theurian_trust_level: trust-level-value
    theurian_sensitivity: sensitivity-value
    theurian_content_type: content-type-value
    theurian_body_file: body-file-value
    theurian_relations:
    - type: relation-type-value
      target: relation-target-value
      note: note-a
    - type: relation-type-value
      target: relation-target-value
      note: note-b
    ---
    """)


def test_the_concept_block_is_these_exact_bytes() -> None:
    """One assertion over the whole block, because the failures are all swaps.

    A test that reads the mapping key by key and asserts each value is a string
    cannot tell `type: kind-value` from `type: status-value`; neither can one
    that checks the key *set*. Every value here but `generated.at` names the
    field it belongs to, so a swapped pair, a reordered key, a `note: null` where
    an omission belongs, a lost anchor field, and the ordering the relation tie
    resolves to are all one diff against these bytes -- and `generated.at`, being
    timestamp-shaped, pins the quoting PyYAML gives such a value.
    """
    assert encode_concept_front_matter(_GOLDEN_CONCEPT) == _GOLDEN_CONCEPT_BLOCK


def test_the_manifest_block_is_these_exact_bytes() -> None:
    assert encode_manifest_front_matter(
        ManifestFrontMatter(theurian_bundle_digest="ab" * 32)
    ) == textwrap.dedent("""\
        ---
        type: Theurian Bundle
        theurian_export_version: 1
        theurian_bundle_digest: abababababababababababababababababababababababababababababababab
        ---
        """)


def test_the_root_index_block_is_these_exact_bytes() -> None:
    assert encode_root_index_front_matter() == textwrap.dedent("""\
        ---
        okf_version: '0.2'
        ---
        """)


def _redumped(
    parsed: dict[str, Any],
    *,
    sort_keys: bool = False,
    allow_unicode: bool = True,
    default_flow_style: bool = False,
    width: int = 100,
) -> str:
    """*parsed* re-emitted with ``_dump_yaml``'s own settings, overrides applied."""
    return str(
        yaml.safe_dump(
            parsed,
            sort_keys=sort_keys,
            allow_unicode=allow_unicode,
            default_flow_style=default_flow_style,
            width=width,
        )
    )


def test_each_dump_setting_moves_the_golden_bytes() -> None:
    """The golden pins ``_dump_yaml``'s settings only if its own values move.

    ``width`` is the one this cannot reach in both directions: nothing in the
    golden is long enough to fold, so a *larger* width leaves these bytes alone.
    That half is :func:`test_a_long_value_folds_at_the_width_dump_yaml_is_given`.
    """
    body = _GOLDEN_CONCEPT_BLOCK[len("---\n") : -len("---\n")]
    parsed: dict[str, Any] = yaml.safe_load(body)

    assert _redumped(parsed) == body, (
        "the golden was not produced by the settings this test perturbs"
    )
    assert _redumped(parsed, sort_keys=True) != body
    assert _redumped(parsed, allow_unicode=False) != body
    assert _redumped(parsed, default_flow_style=True) != body
    assert _redumped(parsed, width=8) != body


def test_a_long_value_folds_at_the_width_dump_yaml_is_given() -> None:
    """The other half of the ``width`` pin: where a value too long to fit breaks.

    PyYAML folds at the first word boundary *past* the width it is given, so
    every folded line but the last lands just over it. The two lengths below are
    what ``width=100`` produces for a title of forty five-letter tokens; a
    narrower or a wider setting moves both.
    """
    block = encode_concept_front_matter(_concept(title=" ".join(["token"] * 40)))
    folded = [line for line in block.splitlines() if line.startswith(("title:", "  token"))]

    assert len(folded) > 1, f"nothing folded, so this pins no width:\n{block}"
    assert [len(line) for line in folded[:-1]] == [102, 103]


def test_the_string_representer_reaches_no_dumper_but_the_codecs_own() -> None:
    """The style override sits on a ``SafeDumper`` subclass, not on ``SafeDumper``.

    Registered on the class itself it would force double-quoted style for every
    ``yaml.safe_dump`` in the process, migration and proposal writers included.
    Measured with this module imported: a plain ``safe_dump`` still emits U+0085
    raw and single-quoted, and ``safe_load`` still folds it to a space -- the
    defect the codec fixes for its own blocks and must not fix for anybody's.
    """
    plain = yaml.safe_dump({"k": "a\x85b"}, allow_unicode=True)

    assert "\x85" in plain, plain
    assert '"' not in plain, plain
    assert yaml.safe_load(plain)["k"] == "a b"


# ---------------------------------------------------------------------------
# Concept front matter: key order, omission, the exporter's own constant.
# ---------------------------------------------------------------------------


def test_the_concept_key_order_is_fixed_and_every_key_is_present_when_populated() -> None:
    block = encode_concept_front_matter(_populated())

    assert list(_front_matter_mapping(block)) == list(CONCEPT_FRONT_MATTER_KEY_ORDER)


def test_stale_after_and_body_file_are_omitted_rather_than_emitted_null() -> None:
    block = encode_concept_front_matter(_concept())
    parsed = _front_matter_mapping(block)

    assert "stale_after" not in parsed
    assert "theurian_body_file" not in parsed
    assert parsed["tags"] == []
    assert parsed["sources"] == []
    assert parsed["theurian_relations"] == []


def test_the_export_version_is_the_exporters_own_constant_not_a_caller_field() -> None:
    """A caller cannot set it, and both encoders emit the same value.

    It is a byte-source constant of the exporter version (decision 2), so the
    constructor carrying it would make it a projected row value -- and two
    documents in one bundle could then disagree about which mapping produced
    them.
    """
    with pytest.raises(TypeError):
        _concept(theurian_export_version=2)
    with pytest.raises(TypeError):
        ManifestFrontMatter(theurian_export_version=2, theurian_bundle_digest="ab" * 32)  # type: ignore[call-arg]

    concept = _front_matter_mapping(encode_concept_front_matter(_concept()))
    manifest = _front_matter_mapping(
        encode_manifest_front_matter(ManifestFrontMatter(theurian_bundle_digest="ab" * 32))
    )

    assert concept["theurian_export_version"] == manifest["theurian_export_version"] == 1


# ---------------------------------------------------------------------------
# Row text against structure: every string position, every line terminator.
# ---------------------------------------------------------------------------

#: How a string position is reached, one entry per position
#: :func:`_string_positions` reports. Written out rather than derived, so that a
#: position whose forgery needs a differently shaped fixture -- a nested anchor,
#: a sequence member -- is built the way the exporter would build it.
_FORGE_BUILDERS: Final[dict[str, Callable[[str], ConceptFrontMatter]]] = {
    "kind": lambda value: _populated(kind=value),
    "title": lambda value: _populated(title=value),
    "labels": lambda value: _populated(labels=(value,)),
    "status": lambda value: _populated(status=value),
    "theurian_status": lambda value: _populated(theurian_status=value),
    "stale_after": lambda value: _populated(stale_after=value),
    "generated.at": lambda value: _populated(generated=GeneratedBy(by="theurian/0.4.0", at=value)),
    "sources[].resource": lambda value: _populated(sources=(_source(resource=value),)),
    "sources[].anchor.provider": lambda value: _populated(sources=(_source(provider=value),)),
    "sources[].anchor.repository": lambda value: _populated(sources=(_source(repository=value),)),
    "sources[].anchor.commit_sha": lambda value: _populated(sources=(_source(commit_sha=value),)),
    "sources[].anchor.file_path": lambda value: _populated(sources=(_source(file_path=value),)),
    "theurian_item_id": lambda value: _populated(theurian_item_id=value),
    "theurian_revision_id": lambda value: _populated(theurian_revision_id=value),
    "theurian_namespace": lambda value: _populated(theurian_namespace=value),
    "theurian_owner": lambda value: _populated(theurian_owner=value),
    "theurian_trust_level": lambda value: _populated(theurian_trust_level=value),
    "theurian_sensitivity": lambda value: _populated(theurian_sensitivity=value),
    "theurian_content_type": lambda value: _populated(theurian_content_type=value),
    "theurian_body_file": lambda value: _populated(theurian_body_file=value),
    "theurian_relations[].type": lambda value: _populated(
        theurian_relations=(RelationEntry(type=value, target="x.y", note="n"),)
    ),
    "theurian_relations[].target": lambda value: _populated(
        theurian_relations=(RelationEntry(type="relates_to", target=value, note="n"),)
    ),
    "theurian_relations[].note": lambda value: _populated(
        theurian_relations=(RelationEntry(type="relates_to", target="x.y", note=value),)
    ),
}

#: The one string position no forgery can reach, and why. `generated.by` is
#: constrained to the tool form at construction, so a row string never lands
#: there at all -- pinned by
#: :func:`test_the_generated_actor_refuses_anything_but_the_tool_form`.
_UNFORGEABLE: Final[frozenset[str]] = frozenset({"generated.by"})

_STRING_ANNOTATIONS: Final[frozenset[str]] = frozenset({"str", "str | None", "tuple[str, ...]"})
_IGNORED_ANNOTATIONS: Final[frozenset[str]] = frozenset({"int", "int | None"})
_NESTED_ANNOTATIONS: Final[dict[str, type[Any]]] = {
    "GeneratedBy": GeneratedBy,
    "SourceAnchorProjection": SourceAnchorProjection,
}
_SEQUENCE_ANNOTATIONS: Final[dict[str, type[Any]]] = {
    "tuple[SourceEntry, ...]": SourceEntry,
    "tuple[RelationEntry, ...]": RelationEntry,
}


def _string_positions(cls: type[DataclassInstance], prefix: str = "") -> Iterator[str]:
    """Every position a row string can occupy under *cls*, as a dotted path.

    Reflected rather than listed, so the scanned population is the one the type
    carries: a string field added to any of the four dataclasses reddens a test
    instead of quietly widening the unscanned set. An annotation in none of the
    four sets above raises, so a new *shape* is classified before it can pass.
    """
    for field in dataclasses.fields(cls):
        annotation = str(field.type)
        position = f"{prefix}{field.name}"
        if annotation in _STRING_ANNOTATIONS:
            yield position
        elif annotation in _NESTED_ANNOTATIONS:
            yield from _string_positions(_NESTED_ANNOTATIONS[annotation], f"{position}.")
        elif annotation in _SEQUENCE_ANNOTATIONS:
            yield from _string_positions(_SEQUENCE_ANNOTATIONS[annotation], f"{position}[].")
        elif annotation not in _IGNORED_ANNOTATIONS:
            raise AssertionError(
                f"{cls.__name__}.{field.name} is annotated {annotation!r}, which this "
                f"reflection does not classify. Sort it into _STRING_ANNOTATIONS (a row "
                f"string reaches it), _NESTED_ANNOTATIONS / _SEQUENCE_ANNOTATIONS (it "
                f"carries row strings one level down), or _IGNORED_ANNOTATIONS (no row "
                f"string reaches it)."
            )


#: Every type a caller hands front-matter values to, against the positions this
#: file covers for it. Both are here because the population is every string a
#: front-matter block is built from, not the concept's alone -- the manifest's
#: digest is a caller's value like any other. The root index takes no argument,
#: so it has no position to scan.
_SCANNED_ROOTS: Final[tuple[tuple[type[Any], frozenset[str]], ...]] = (
    (ConceptFrontMatter, frozenset(_FORGE_BUILDERS) | _UNFORGEABLE),
    (ManifestFrontMatter, frozenset({"theurian_bundle_digest"})),
)


@pytest.mark.parametrize(
    ("root", "covered"), _SCANNED_ROOTS, ids=lambda case: getattr(case, "__name__", "")
)
def test_every_string_position_is_either_forge_scanned_or_recorded_unforgeable(
    root: type[Any], covered: frozenset[str]
) -> None:
    """The completeness half: the scanned population is the type's, not a subset.

    :data:`_FORGE_BUILDERS` is hand-written, so alone it is an enumeration a new
    field falls out of silently. The reflected set must equal what this file
    covers plus the recorded :data:`_UNFORGEABLE` one.
    """
    reflected = set(_string_positions(root))

    assert reflected == set(covered), (
        f"{root.__name__}'s string positions have drifted from what this file scans:\n"
        f"  unscanned: {sorted(reflected - covered)}\n"
        f"  no longer a position: {sorted(covered - reflected)}\n\n"
        "A new string field is a new way for a row to reach the front matter. "
        "Either scan it, or record it in _UNFORGEABLE beside the construction-time "
        "constraint that keeps row text out of it."
    )


def test_the_recorded_terminator_population_is_the_five_single_characters() -> None:
    """The one place the literal above and the codec's constant are compared.

    Every other check here drives :data:`_TERMINATOR_CASES`, so shrinking
    ``LINE_TERMINATORS`` cannot quietly shrink what they range over -- it
    reddens here instead, naming the two sides.
    """
    assert set(_TERMINATOR_CASES) - {"\r\n"} == LINE_TERMINATORS


def test_the_recorded_block_starter_population_is_the_codecs_own_tuple() -> None:
    """The one place :data:`_BLOCK_STARTER_MEMBERS` and the codec's constant are compared.

    Every block-starter check below drives the literal, so shrinking
    ``_BLOCK_STARTERS`` -- dropping the backtick and `>`, both unreachable
    through it anyway because :data:`_INLINE` escapes them first -- cannot
    quietly shrink what those checks range over; it reddens here instead,
    naming the two sides.
    """
    assert _BLOCK_STARTER_MEMBERS == okf_codec._BLOCK_STARTERS


@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_the_block_scans_see_a_forgery_behind_every_terminator(terminator: str) -> None:
    """The instruments' positive control: a clean report has to be a measurement.

    :func:`_top_level_keys` and :func:`_FENCE_LINE` report nothing forged for
    every block the encoder produces below. A key scan that split on ``\\n``
    alone, or matched only ``[A-Za-z_]+:``, would report exactly the same clean
    result for a block that carried a forgery -- so both are shown a hand-built
    block that does, and must see it. ``2nd-key`` is in there because a forged
    key is spellable with a digit and a dash.
    """
    forged = f"title: ok{terminator}theurian_sensitivity: public{terminator}2nd-key: x{terminator}"

    assert _top_level_keys(forged) == ["title", "theurian_sensitivity", "2nd-key"]
    assert [
        line
        for line in _physical_lines(f"a{terminator}---{terminator}b")
        if _FENCE_LINE.match(line)
    ] == ["---"]


@pytest.mark.parametrize("position", sorted(_FORGE_BUILDERS))
def test_the_folding_payload_folds_at_every_position(position: str) -> None:
    """The positive control for the `folds` half of the forge scan below.

    :func:`_assert_no_value_line_reaches_column_zero` reports a clean block
    whether or not anything folded, so the payload has to be measured rather than
    assumed long enough. The only way the long payload can add physical lines is
    the fold -- its own terminators are written as escape sequences -- so a wider
    ``width`` or a shorter payload reddens here instead of quietly retiring the
    half of the scan that covers folding.
    """
    short = encode_concept_front_matter(_FORGE_BUILDERS[position](_FORGED.format(t="\n")))
    folded = encode_concept_front_matter(_FORGE_BUILDERS[position](_FORGED_LONG.format(t="\n")))

    assert len(_physical_lines(folded)) > len(_physical_lines(short)), folded


@pytest.mark.parametrize("position", sorted(_FORGE_BUILDERS))
@pytest.mark.parametrize("payload", _FORGED_PAYLOADS, ids=("short", "folds"))
@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_a_forged_terminator_stays_inside_its_own_value_at_every_position(
    position: str, payload: str, terminator: str
) -> None:
    """GWT: any row string, any line terminator, both structural forgeries.

    Given a value carrying a terminator followed by a bare `---` and by a
    governance-looking `theurian_sensitivity: public` line, when it is encoded at
    *position* and re-parsed: the block opens exactly the keys the encoder wrote,
    no physical line inside it is a front-matter fence, every physical line is
    one the encoder wrote, and the value comes back byte-identical -- terminator
    and all.

    Four assertions, four consumers: one that parses YAML, one that splits on
    `---` first (which is how front matter gets found at all), one that reads a
    line at a time, and the importer S3 will build, for which an altered value is
    data loss rather than forgery. The `folds` payload is the one that answers
    the line reader honestly: past `width=100` PyYAML breaks the value across
    physical lines, so the property is that no line a value occupies starts at
    column 0 -- not that the value occupies one line.
    """
    forged = payload.format(t=terminator)
    front_matter = _FORGE_BUILDERS[position](forged)
    block = encode_concept_front_matter(front_matter)

    assert _top_level_keys(block) == list(CONCEPT_FRONT_MATTER_KEY_ORDER)

    inner = _physical_lines(block[len("---\n") : -len("---\n")])
    assert not [line for line in inner if _FENCE_LINE.match(line)], block

    _assert_no_value_line_reaches_column_zero(
        block,
        order=CONCEPT_FRONT_MATTER_KEY_ORDER,
        sequence_items=(
            len(front_matter.labels)
            + len(front_matter.sources)
            + len(front_matter.theurian_relations)
        ),
    )

    assert forged in set(_string_values(_front_matter_mapping(block))), block


@pytest.mark.parametrize("payload", _FORGED_PAYLOADS, ids=("short", "folds"))
@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_a_forged_terminator_stays_inside_the_manifest_digest(
    payload: str, terminator: str
) -> None:
    """The manifest's own string position, held to the same four assertions.

    :data:`_FORGE_BUILDERS` covers :class:`ConceptFrontMatter`;
    :class:`ManifestFrontMatter` carries one string of its own, and it reaches a
    front-matter block through the same serializer.
    """
    forged = payload.format(t=terminator)
    block = encode_manifest_front_matter(ManifestFrontMatter(theurian_bundle_digest=forged))

    manifest_keys = ("type", "theurian_export_version", "theurian_bundle_digest")
    assert _top_level_keys(block) == list(manifest_keys)

    inner = _physical_lines(block[len("---\n") : -len("---\n")])
    assert not [line for line in inner if _FENCE_LINE.match(line)], block

    _assert_no_value_line_reaches_column_zero(block, order=manifest_keys, sequence_items=0)

    assert _front_matter_mapping(block)["theurian_bundle_digest"] == forged


@pytest.mark.parametrize("position", sorted(_FORGE_BUILDERS))
def test_a_terminator_free_forgery_folds_with_no_backslash_and_stays_off_column_zero(
    position: str,
) -> None:
    """The style branch every terminator-driven scan above never exercises.

    Every :data:`_FORGED_PAYLOADS` case is forced double-quoted by
    :func:`_represent_str` because it carries a terminator; :data:`_FORGED_PLAIN`
    carries none, so PyYAML picks the style itself -- single-quoted here,
    because ``theurian_sensitivity: `` rules out plain too -- and folds with no
    trailing backslash on a continuation line. The column-0 property has to
    hold on that fold as well, not only on the double-quoted one.
    """
    front_matter = _FORGE_BUILDERS[position](_FORGED_PLAIN)
    block = encode_concept_front_matter(front_matter)

    assert len(_physical_lines(block)) > 1, f"nothing folded, so this pins nothing:\n{block}"
    assert "\\" not in block, f"a backslash means this is not the style being pinned:\n{block}"

    _assert_no_value_line_reaches_column_zero(
        block,
        order=CONCEPT_FRONT_MATTER_KEY_ORDER,
        sequence_items=(
            len(front_matter.labels)
            + len(front_matter.sources)
            + len(front_matter.theurian_relations)
        ),
    )
    assert _FORGED_PLAIN in set(_string_values(_front_matter_mapping(block))), block


def test_a_title_carrying_a_forged_sensitivity_line_stays_one_title_and_one_key() -> None:
    """The acceptance criterion's own worked example, spelled out.

    Given a title ending a physical line with `theurian_sensitivity: public`,
    when encoded and re-parsed, the mapping holds the title intact -- the
    newline preserved as data -- and the block carries exactly the keys the
    encoder wrote, not a second `theurian_sensitivity`.
    """
    forged_title = "Auth policy\ntheurian_sensitivity: public"
    block = encode_concept_front_matter(_concept(title=forged_title))

    expected_keys = [
        key
        for key in CONCEPT_FRONT_MATTER_KEY_ORDER
        if key not in {"stale_after", "theurian_body_file"}
    ]
    top_level = _top_level_keys(block)
    assert top_level.count("title") == 1
    assert top_level.count("theurian_sensitivity") == 1
    assert top_level == expected_keys

    parsed = _front_matter_mapping(block)
    assert parsed["title"] == forged_title
    assert parsed["theurian_sensitivity"] == "internal"
    assert set(parsed) == set(expected_keys)


# ---------------------------------------------------------------------------
# `generated.by`: the exporter's actor, not a row string.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor",
    [
        "platform-team",
        "engineer@example.com",
        "theurian",
        "theurian/",
        "theurian/0.4.0 and more",
        " theurian/0.4.0",
        "not-theurian/0.4.0",
        *[f"theurian/0.4.0{terminator}" for terminator in _TERMINATOR_CASES],
    ],
)
def test_the_generated_actor_refuses_anything_but_the_tool_form(actor: str) -> None:
    """§7 fixes `generated.by` as a constant of the exporter version.

    The trailing-terminator cases are the reason the check is `fullmatch` and not
    `^theurian/\\S+$`: `$` also matches before a final newline, so the anchored
    pattern would admit `theurian/0.4.0\\n` -- and `generated.by` is the one
    front-matter value :data:`_UNFORGEABLE` records as out of a row's reach.
    """
    with pytest.raises(InvariantViolationError) as raised:
        GeneratedBy(by=actor, at="2026-09-24T12:00:00+00:00")

    message = str(raised.value)
    assert "GeneratedBy.by" in message
    assert "theurian --version" in message, (
        "the refusal must name something the reader can run, not only what is wrong"
    )


def test_the_generated_actor_accepts_the_tool_form() -> None:
    """The positive control: without it the refusal above could reject everything."""
    assert GeneratedBy(by="theurian/0.4.0", at="2026-09-24T12:00:00+00:00").by == "theurian/0.4.0"
    assert GeneratedBy(by="theurian/1.0.0-rc.1+build.7", at="x").by.startswith("theurian/")


# ---------------------------------------------------------------------------
# Ordering: relations are normalized on a total key; sources and labels are not.
# ---------------------------------------------------------------------------


def test_relations_are_normalized_to_type_target_order_regardless_of_input_order() -> None:
    out_of_order = (
        RelationEntry(type="supersedes", target="b"),
        RelationEntry(type="depends_on", target="z"),
        RelationEntry(type="depends_on", target="a"),
    )
    front_matter = _concept(theurian_relations=out_of_order)

    assert [(r.type, r.target) for r in front_matter.theurian_relations] == [
        ("depends_on", "a"),
        ("depends_on", "z"),
        ("supersedes", "b"),
    ]

    parsed = _front_matter_mapping(encode_concept_front_matter(front_matter))
    assert [(r["type"], r["target"]) for r in parsed["theurian_relations"]] == [
        ("depends_on", "a"),
        ("depends_on", "z"),
        ("supersedes", "b"),
    ]


def test_two_edges_sharing_a_type_and_target_are_ordered_by_their_notes() -> None:
    """The tie that `(type, target)` alone leaves to the caller's argument order.

    Both directions are asserted, because the failure is not a wrong order but a
    *caller-dependent* one: two callers assembling the same set differently have
    to encode to the same bytes (decision 2).
    """
    edges = (
        RelationEntry(type="t", target="x", note="note-b"),
        RelationEntry(type="t", target="x", note="note-a"),
    )
    forward = _concept(theurian_relations=edges)
    backward = _concept(theurian_relations=tuple(reversed(edges)))

    assert [entry.note for entry in forward.theurian_relations] == ["note-a", "note-b"]
    assert encode_concept_front_matter(forward) == encode_concept_front_matter(backward)


def test_an_absent_note_and_an_empty_note_are_two_different_sort_keys() -> None:
    """`note or ""` alone collapses these two onto one key, and a collapsed key
    is the caller's argument order deciding again -- just one field further in.
    """
    edges = (
        RelationEntry(type="t", target="x", note=None),
        RelationEntry(type="t", target="x", note=""),
    )
    forward = _concept(theurian_relations=edges)
    backward = _concept(theurian_relations=tuple(reversed(edges)))

    assert [entry.note for entry in forward.theurian_relations] == ["", None]
    assert encode_concept_front_matter(forward) == encode_concept_front_matter(backward)


def test_sources_and_labels_keep_the_callers_own_order() -> None:
    sources = (
        _source(resource="https://example.com/z", repository="acme/z", file_path="z.md"),
        _source(resource="https://example.com/a", repository="acme/a", file_path="a.md"),
    )
    front_matter = _concept(labels=("z-label", "a-label"), sources=sources)
    parsed = _front_matter_mapping(encode_concept_front_matter(front_matter))

    assert parsed["tags"] == ["z-label", "a-label"]
    assert [entry["resource"] for entry in parsed["sources"]] == [
        "https://example.com/z",
        "https://example.com/a",
    ]


# ---------------------------------------------------------------------------
# `theurian_anchor`: the served fields, and the ones a source may not have.
# ---------------------------------------------------------------------------


def test_the_source_anchor_carries_exactly_the_six_served_fields_in_order() -> None:
    parsed = _front_matter_mapping(encode_concept_front_matter(_concept(sources=(_source(),))))
    anchor = parsed["sources"][0]["theurian_anchor"]

    assert list(anchor) == list(SOURCE_ANCHOR_KEY_ORDER)


def test_the_anchor_key_order_names_exactly_the_projections_own_fields() -> None:
    """`_anchor_mapping` reads the projection's fields, the constant orders them.

    The two populations are one or the emission is wrong in one of two
    directions: a field the constant does not name would be emitted nowhere, and
    a name the projection does not carry would be a key with nothing behind it.
    Both refuse at `_present` rather than passing; this is the equality that
    says which.
    """
    assert set(SOURCE_ANCHOR_KEY_ORDER) == {
        field.name for field in dataclasses.fields(SourceAnchorProjection)
    }


def test_an_order_tuple_that_is_not_the_mapping_refuses_with_the_constant_named() -> None:
    """`_present` reads a mapping *through* an order tuple, so they must agree.

    Dropping a key from the constant silently dropped it from the block, and
    adding one raised a bare `KeyError` with nothing to act on. Both now name the
    constant to amend and a command to run.
    """
    cases: tuple[tuple[dict[str, object], tuple[str, ...]], ...] = (
        ({"a": 1, "b": 2}, ("a",)),
        ({"a": 1}, ("a", "b")),
    )

    for values, order in cases:
        with pytest.raises(InvariantViolationError) as raised:
            _present(values, order, constant="A_KEY_ORDER")

        message = str(raised.value)
        assert "A_KEY_ORDER" in message
        assert "'b'" in message, message
        assert "uv run pytest" in message, (
            "the refusal must name something the reader can run, not only what is wrong"
        )


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        (SourceAnchorProjection(provider="web"), ["provider"]),
        (
            SourceAnchorProjection(provider="web", file_path="a.md"),
            ["provider", "file_path"],
        ),
        (
            SourceAnchorProjection(provider="git", repository="acme/a", line_start=1),
            ["provider", "repository", "line_start"],
        ),
    ],
)
def test_an_unset_anchor_field_is_omitted_and_never_emitted_null(
    anchor: SourceAnchorProjection, expected: list[str]
) -> None:
    """FR-R5's external-URI source is the shape this exists for.

    A source reached by URI has a provider and a `resource`, and no repository,
    commit or line range at all -- so it must construct and encode, and the
    fields it does not have must be absent rather than null. The expected lists
    also pin that what survives comes back in :data:`SOURCE_ANCHOR_KEY_ORDER`
    rather than in the order the caller happened to pass.
    """
    source = SourceEntry(resource="https://example.com/a", anchor=anchor)
    parsed = _front_matter_mapping(encode_concept_front_matter(_concept(sources=(source,))))

    assert list(parsed["sources"][0]["theurian_anchor"]) == expected


# ---------------------------------------------------------------------------
# The sidecar extension rule: total, and never `.md`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("media_type", "extension"),
    [
        ("application/json", ".json"),
        ("application/schema+json", ".json"),
        ("application/vnd.oai.openapi+json", ".json"),
        ("application/yaml", ".yaml"),
        ("text/x-yaml", ".yaml"),
        ("application/vnd.aai.asyncapi", ".txt"),
        ("application/vnd.oai.openapi", ".txt"),
        ("text/markdown", ".txt"),
        ("text/plain", ".txt"),
    ],
)
def test_sidecar_extension_matches_decision_7s_three_arms(media_type: str, extension: str) -> None:
    assert sidecar_extension(MediaType(media_type)) == extension


def test_sidecar_extension_never_raises_and_never_returns_md() -> None:
    for media_type in ("banana/unknown", "application/octet-stream", "text/csv"):
        extension = sidecar_extension(MediaType(media_type))
        assert extension != ".md"
        assert extension in {".json", ".yaml", ".txt"}


# ---------------------------------------------------------------------------
# Markdown-syntax escapes: the whitespace fold, then each site's whole grammar.
#
# One shared inline set at both sites, plus the line-start constructs at the
# list line, which is the only one of the two that owns a line start.
# ---------------------------------------------------------------------------

#: Both sites, so a population pinned at one is pinned at the other. The inline
#: set was the link label's alone for one round, and a relation note rendered
#: live links, live autolinks and raw HTML into the `## Relations` section.
_MARKDOWN_SITES: Final[tuple[Callable[[str], str], ...]] = (
    escape_markdown_link_text,
    escape_markdown_list_line,
)


@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_the_link_text_escape_folds_every_line_terminator(terminator: str) -> None:
    """A title's second line orphans the entry's link and opens its own block.

    Unfolded, `Auth policy<T><T>* <https://evil/forged>` renders the index entry
    inert -- the label never closes on the line the `](...)` is on -- and the
    next line is a list item of the attacker's choosing. Every member of
    :data:`_TERMINATOR_CASES` folds to the same bytes, so one expectation covers
    the whole parametrization.
    """
    escaped = escape_markdown_link_text(f"Auth policy{terminator}{terminator}* <https://evil/f>")

    assert not set("".join(_TERMINATOR_CASES)) & set(escaped), repr(escaped)
    assert escaped == "Auth policy  * \\<https://evil/f\\>"


@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_the_list_line_escape_folds_every_line_terminator(terminator: str) -> None:
    """A note's second line forges a section heading and an edge under it.

    Unfolded, `benign<T><T>## Relations<T><T>* **supersedes** x.y` splits the
    enclosing section and puts an attacker-chosen edge in the new one, past an
    escape anchored on the first line.
    """
    note = f"benign{terminator}{terminator}## Relations{terminator}{terminator}* x.y"
    escaped = escape_markdown_list_line(note)

    assert not set("".join(_TERMINATOR_CASES)) & set(escaped), repr(escaped)
    assert escaped == "benign  ## Relations  * x.y"


@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_a_leading_terminator_folds_into_indentation_the_atx_rule_still_covers(
    terminator: str,
) -> None:
    """The fold moves the heading run, it does not put it out of reach.

    A terminator at the front becomes one space of indentation, so the rule has
    to fire on the folded line, not on the line the caller passed.
    """
    assert escape_markdown_list_line(f"{terminator}# Injected") == " \\# Injected"


@pytest.mark.parametrize(
    ("indent", "folded"),
    [("\t", " "), (" \t", "  "), ("\t\t", "  "), ("\n\t", "  "), ("\r\n\t", "  ")],
    ids=repr,
)
def test_a_tab_indented_heading_run_is_escaped_like_a_space_indented_one(
    indent: str, folded: str
) -> None:
    """The tooth the round-1 rule did not have: it matched literal spaces only.

    CommonMark counts a line's indentation in columns after tab expansion, so
    `<TAB>## Relations` is an indented heading run -- while a rule spelled
    ` {0,3}` sees a line beginning with a tab, matches no indentation, and
    returns the note unchanged. Round 2 rendered that note with a CommonMark
    renderer and got a second `<h2>Relations</h2>` with a forged edge beneath it.
    Folding every tab to a space ahead of the rule is what makes the two
    spellings one case; `" \\t"` and `"\\n\\t"` are here because a fold or a
    stray space leaves a tab that is no longer first.
    """
    escaped = escape_markdown_list_line(f"{indent}## Relations")

    assert escaped == f"{folded}\\## Relations"
    _assert_opens_no_block_construct(escaped)


@pytest.mark.parametrize("site", _MARKDOWN_SITES, ids=lambda site: site.__name__)
@pytest.mark.parametrize("member", _INLINE_MEMBERS)
def test_no_inline_construct_survives_either_markdown_site(
    site: Callable[[str], str], member: str
) -> None:
    """One case per member at each site.

    The parity helper catches a member that stopped being escaped -- an
    unescaped occurrence carries a zero-length backslash run, which is even --
    and the exact expectation says which member, and what the output should have
    been instead.
    """
    assert site(f"a{member}b") == f"a\\{member}b"

    escaped = site(f"Auth\tpolicy{member}https://evil{member} tail")

    assert "\t" not in escaped, repr(escaped)
    _assert_every_inline_member_is_escaped(escaped)


def test_escape_markdown_link_text_leaves_plain_text_untouched() -> None:
    assert escape_markdown_link_text("architecture.auth-policy") == "architecture.auth-policy"


def test_escape_markdown_link_text_escapes_the_autolink_and_raw_html_door() -> None:
    """`<...>` reopens link syntax with no terminator in the title at all.

    `<https://attacker/hijack>` is a CommonMark autolink and nests a second
    anchor inside the entry's own; `<img ...>` is raw inline HTML through the
    same character. Both were live while the escape covered `[`, `]` and `\\`
    alone.
    """
    assert escape_markdown_link_text("<https://attacker/hijack>") == (
        "\\<https://attacker/hijack\\>"
    )
    assert escape_markdown_link_text("<img src=x onerror=alert(1)>") == (
        "\\<img src=x onerror=alert(1)\\>"
    )


def test_a_backtick_in_a_title_opens_no_code_span_over_the_entrys_own_link() -> None:
    """A code span runs to the next backtick on the *rendered* line.

    An index entry's line carries text after the link, so one backtick in the
    title and one anywhere in that text form a span across the `](destination)`
    between them: the entry renders with no link at all, and the real target
    comes out as literal text.
    """
    assert escape_markdown_link_text("Auth `policy") == "Auth \\`policy"


def test_every_escaped_delimiter_leaves_an_odd_backslash_run_before_it() -> None:
    """The parity argument, re-derived rather than asserted about one input.

    A caller-supplied backslash must not pair with an inserted escape and cancel
    it, which would resurrect the live `](` the escape exists to kill. What
    makes CommonMark consume the run as an escape *of that delimiter* is its odd
    length, so that is what :func:`_assert_every_inline_member_is_escaped`
    checks -- on an input asserted here to carry every member.
    """
    hostile = "evil\\](x) and \\[this\\] and <https://attacker/h> and > and `code`"

    assert set(_INLINE_MEMBERS) <= set(hostile), (
        f"{hostile!r} does not carry every member of {_INLINE_MEMBERS}, so the "
        f"re-derivation below would range over a subset and pass for the wrong reason"
    )

    _assert_every_inline_member_is_escaped(escape_markdown_link_text(hostile))


@pytest.mark.parametrize("run_length", (0, 1, 2, 3))
@pytest.mark.parametrize("member", _INLINE_MEMBERS)
def test_a_callers_backslash_run_of_any_length_leaves_an_odd_run_before_the_escape(
    member: str, run_length: int
) -> None:
    """The doubling line's own battery, not only the one hostile string above.

    :func:`_escape_inline` doubles a caller's own backslashes before inserting
    an escape, so an even caller run pairs off entirely and a fresh odd run is
    left behind the escape, while an odd caller run leaves one caller
    backslash behind the doubled pairs plus that same fresh run -- both land
    on odd, at every length the hostile string above did not happen to cover.
    """
    escaped = escape_markdown_link_text("\\" * run_length + member)

    _assert_every_inline_member_is_escaped(escaped)


@pytest.mark.parametrize("starter", _BLOCK_STARTER_MEMBERS)
@pytest.mark.parametrize("repeat", (1, 3), ids=("once", "thrice"))
@pytest.mark.parametrize("leading", _LEADING_WHITESPACE, ids=repr)
def test_no_block_construct_opens_a_relation_note_at_any_indent(
    leading: str, repeat: int, starter: str
) -> None:
    """Total over the block-starter population, not over examples.

    Each member is a door into the `## Relations` section's own structure: a
    heading that splits it, a fence that swallows it, a list marker that forges
    a row, a thematic break that deletes one. Every indentation spelling is here
    too, because the rule has to see through the spelling to the column.
    """
    escaped = escape_markdown_list_line(f"{leading}{starter * repeat} forged")

    _assert_opens_no_block_construct(escaped)
    assert escaped.startswith(f"{' ' * len(leading)}\\{starter}"), repr(escaped)


@pytest.mark.parametrize("marker", ("1.", "0.", "9)", "12.", "1234567890."))
@pytest.mark.parametrize("leading", _LEADING_WHITESPACE, ids=repr)
def test_no_ordered_list_marker_opens_a_relation_note_at_any_indent(
    leading: str, marker: str
) -> None:
    """The one block construct that is a shape rather than a character.

    The escape goes in front of the `.` or `)`, which is the CommonMark-effective
    spelling: a backslash before a digit is a literal backslash, and `1.` would
    still open a list behind it.
    """
    escaped = escape_markdown_list_line(f"{leading}{marker} forged edge")

    _assert_opens_no_block_construct(escaped)
    assert escaped == f"{' ' * len(leading)}{marker[:-1]}\\{marker[-1]} forged edge"


def test_escape_markdown_list_line_escapes_a_leading_heading_run() -> None:
    assert escape_markdown_list_line("#Attack") == "\\#Attack"
    assert escape_markdown_list_line("### Attack") == "\\### Attack"


def test_escape_markdown_list_line_escapes_within_atx_indentation() -> None:
    assert escape_markdown_list_line("  ##Attack") == "  \\##Attack"
    assert escape_markdown_list_line("   #Attack") == "   \\#Attack"


def test_a_note_of_a_thematic_break_keeps_its_own_row() -> None:
    """`- ---` is not a list item at all.

    The `---` renders as an `<hr>`, so the row disappears from the rendered list
    and takes with it the relation it was annotating -- a deletion rather than a
    forgery, and the only face of this class that removes something.
    """
    for note in ("---", "***", "___"):
        assert escape_markdown_list_line(note) == f"\\{note}"


def test_a_link_reference_definition_note_keeps_its_own_row() -> None:
    """`[foo]: https://evil.example/f` (§4.7) opens the way an HTML block does.

    Unescaped, CommonMark consumes the whole line into a link reference
    definition and renders it as nothing at all -- the row disappears from the
    rendered list the way the thematic break above does. It is
    :data:`_INLINE`'s own `[`, not :data:`_BLOCK_STARTERS`, that keeps this row
    live; every `[` in the output stays behind an odd backslash run.
    """
    escaped = escape_markdown_list_line("[foo]: https://evil.example/f")

    assert escaped == "\\[foo\\]: https://evil.example/f"
    _assert_every_inline_member_is_escaped(escaped)


def test_a_relation_note_renders_no_live_link_autolink_or_raw_html() -> None:
    """The inline set at the site that had none of it.

    Each of these renders into the `## Relations` section as live markup: a link
    to an attacker's target beside the real edges, an autolink, an inline image
    that fetches a URL when the bundle is read, and raw HTML.
    """
    assert escape_markdown_list_line("[Fake](https://evil)") == "\\[Fake\\](https://evil)"
    assert escape_markdown_list_line("<https://evil>") == "\\<https://evil\\>"
    assert escape_markdown_list_line("<img src=x>") == "\\<img src=x\\>"
    assert escape_markdown_list_line("![x](url)") == "!\\[x\\](url)"


def test_escape_markdown_list_line_leaves_text_that_opens_nothing_untouched() -> None:
    assert escape_markdown_list_line("plain note") == "plain note"
    assert escape_markdown_list_line("a # mid-line hash") == "a # mid-line hash"
    assert escape_markdown_list_line("note 1. not a marker") == "note 1. not a marker"


def test_a_note_indented_past_any_column_window_is_escaped_too() -> None:
    """The rule fires at every indent, and this is what that costs.

    How many columns of indentation put the note inside an indented code block
    instead depends on the list prefix the *exporter* renders it behind, so no
    column window stated here would be exact. The trade is one visible backslash
    in front of a note that was indented four spaces or more and began with a
    block starter anyway.
    """
    assert escape_markdown_list_line("    #not-a-heading") == "    \\#not-a-heading"


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_encoding_is_a_pure_function_of_its_input() -> None:
    front_matter = _populated()

    assert encode_concept_front_matter(front_matter) == encode_concept_front_matter(front_matter)
    assert encode_root_index_front_matter() == encode_root_index_front_matter()


# ---------------------------------------------------------------------------
# The decoder (S3, ADR-0037 decision 6): round trip, and a vanilla bundle.
# ---------------------------------------------------------------------------

_BODY = "\n# Authentication and authorization policy\n\nBody prose.\n"


def _decoded(document: str) -> DecodedConceptDocument:
    decoded = decode_concept_document(document)
    assert isinstance(decoded, DecodedConceptDocument), decoded
    return decoded


def test_a_theurian_exported_concept_round_trips_every_theurian_value() -> None:
    """Given a Theurian-exported concept, decoding round-trips every `theurian_*` value."""
    front_matter = _concept(
        stale_after="2027-01-01",
        theurian_body_file="auth-policy.json",
        theurian_relations=(RelationEntry(type="relates_to", target="x.y", note="n"),),
    )
    document = encode_concept_front_matter(front_matter) + _BODY

    decoded = _decoded(document).front_matter

    assert decoded.kind == front_matter.kind
    assert decoded.title == front_matter.title
    assert decoded.status == front_matter.status
    assert decoded.stale_after == front_matter.stale_after
    assert decoded.generated is not None
    assert decoded.generated.by == front_matter.generated.by
    assert decoded.generated.at == front_matter.generated.at
    assert decoded.theurian_export_version == EXPORT_VERSION
    assert decoded.theurian_item_id == front_matter.theurian_item_id
    assert decoded.theurian_revision_id == front_matter.theurian_revision_id
    assert decoded.theurian_status == front_matter.theurian_status
    assert decoded.theurian_namespace == front_matter.theurian_namespace
    assert decoded.theurian_owner == front_matter.theurian_owner
    assert decoded.theurian_trust_level == front_matter.theurian_trust_level
    assert decoded.theurian_sensitivity == front_matter.theurian_sensitivity
    assert decoded.theurian_content_type == front_matter.theurian_content_type
    assert decoded.theurian_body_file == front_matter.theurian_body_file
    assert [(entry.type, entry.target, entry.note) for entry in decoded.theurian_relations] == [
        (entry.type, entry.target, entry.note) for entry in front_matter.theurian_relations
    ]


def test_a_theurian_exported_concepts_sources_round_trip_with_their_anchor() -> None:
    front_matter = _concept(
        sources=(
            SourceEntry(
                resource="https://example.invalid/doc",
                anchor=SourceAnchorProjection(
                    provider="git",
                    repository="acme/repo",
                    commit_sha="a" * 40,
                    file_path="README.md",
                    line_start=1,
                    line_end=2,
                ),
            ),
        ),
    )
    document = encode_concept_front_matter(front_matter) + _BODY

    decoded = _decoded(document).front_matter

    assert len(decoded.sources) == 1
    assert decoded.sources[0].resource == "https://example.invalid/doc"
    assert decoded.sources[0].anchor == front_matter.sources[0].anchor


def test_a_sources_anchor_with_only_provider_present_decodes_the_rest_as_none() -> None:
    """A vanilla or hand-authored `theurian_anchor` may omit every optional field."""
    document = (
        "---\n"
        "type: architecture\n"
        "title: A concept with a bare anchor\n"
        "status: stable\n"
        "sources:\n"
        "  - resource: https://example.invalid/doc\n"
        "    theurian_anchor:\n"
        "      provider: git\n"
        "---\n" + _BODY
    )

    decoded = _decoded(document).front_matter

    assert len(decoded.sources) == 1
    anchor = decoded.sources[0].anchor
    assert anchor is not None
    assert anchor.provider == "git"
    assert anchor.repository is None
    assert anchor.commit_sha is None
    assert anchor.file_path is None
    assert anchor.line_start is None
    assert anchor.line_end is None


def test_a_vanilla_okf_concept_with_no_theurian_keys_decodes_okf_fields_and_nothing_else() -> None:
    """Given a vanilla OKF concept with zero `theurian_*` keys, every extension is None/empty."""
    document = "---\ntype: architecture\ntitle: A vanilla concept\nstatus: stable\n---\n" + _BODY

    decoded = _decoded(document).front_matter

    assert decoded.kind == "architecture"
    assert decoded.title == "A vanilla concept"
    assert decoded.status == "stable"
    assert decoded.labels == ()
    assert decoded.stale_after is None
    assert decoded.generated is None
    assert decoded.sources == ()
    assert decoded.theurian_export_version is None
    assert decoded.theurian_item_id is None
    assert decoded.theurian_revision_id is None
    assert decoded.theurian_status is None
    assert decoded.theurian_namespace is None
    assert decoded.theurian_owner is None
    assert decoded.theurian_trust_level is None
    assert decoded.theurian_sensitivity is None
    assert decoded.theurian_content_type is None
    assert decoded.theurian_body_file is None
    assert decoded.theurian_relations == ()


def test_a_boolean_export_version_decodes_as_absent_not_as_the_integer_it_coerces_to() -> None:
    """`_optional_int`'s own guard: `bool` is an `int` subclass in Python, so
    `theurian_export_version: true` must decode as absent, not as `1` --
    and the concept still decodes rather than refusing.
    """
    document = (
        "---\ntype: architecture\ntitle: T\nstatus: stable\ntheurian_export_version: true\n---\n"
        + _BODY
    )

    decoded = _decoded(document).front_matter

    assert decoded.theurian_export_version is None


def test_a_vanilla_concepts_body_decodes_unchanged() -> None:
    document = "---\ntype: architecture\ntitle: A vanilla concept\nstatus: stable\n---\n" + _BODY

    assert _decoded(document).body == _BODY


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("# No front matter at all\n", id="no-fence"),
        pytest.param("---\ntitle: Missing type and status\n---\nbody\n", id="missing-type"),
        pytest.param("---\ntype: architecture\nstatus: stable\n---\nbody\n", id="missing-title"),
        pytest.param("---\ntype: architecture\ntitle: T\n---\nbody\n", id="missing-status"),
        pytest.param("---\ntype: [\n---\nbody\n", id="invalid-yaml"),
        pytest.param("---\n- a\n- b\n---\nbody\n", id="not-a-mapping"),
    ],
)
def test_a_malformed_concept_refuses_rather_than_raising(document: str) -> None:
    decoded = decode_concept_document(document)

    assert isinstance(decoded, ConceptDecodeRefusal)
    assert decoded.reason


def test_the_manifest_round_trips_its_two_constants() -> None:
    manifest = ManifestFrontMatter(theurian_bundle_digest="sha256:abc")
    document = encode_manifest_front_matter(manifest) + _BODY

    decoded = decode_manifest_front_matter(document)

    assert decoded is not None
    assert decoded.theurian_export_version == EXPORT_VERSION
    assert decoded.theurian_bundle_digest == manifest.theurian_bundle_digest


def test_a_manifest_with_no_front_matter_decodes_to_none() -> None:
    assert decode_manifest_front_matter("no fence here\n") is None
