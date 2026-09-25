"""The OKF front-matter codec's encoder half (ADR-0037 decisions 2, 4, 7).

Scoped to this module's own functions and types. The bundle-level properties --
the two-corpora battery, cross-run determinism over a whole export, the
reserved-name collision escape -- are a later assignment's exporter batteries.

Three families are pinned here.

**Row text against structure**, over :data:`_TERMINATOR_CASES`: every string
position a :class:`ConceptFrontMatter` carries is encoded and re-read by
:func:`test_a_forged_terminator_stays_inside_its_own_value_at_every_position`,
and both Markdown escapes are driven over the same set. The position list is not
hand-written -- :func:`_string_positions` reflects over the four dataclasses, so
a new string field fails
:func:`test_every_string_position_is_either_forge_scanned_or_recorded_unforgeable`
until it is either scanned or recorded as constrained at construction.

**The emission shape.** Three golden blocks hold the exact bytes of a fully
populated concept, of the manifest and of the root index, each value naming its
own field so a swapped pair reads as a swap rather than as two plausible
strings. :func:`test_each_dump_setting_moves_the_golden_bytes` and
:func:`test_a_long_value_folds_at_the_width_dump_yaml_is_given` are why those
bytes pin the serializer settings and not only the field mapping.

**The two key vocabularies**, read against ADR-0037 itself through
``assert_the_adr_states`` rather than against a second copy of the same literals.

No Markdown renderer is imported. ``grep markdown-it-py pyproject.toml
packages/theurian-core/pyproject.toml`` answers nothing, and in ``uv.lock`` the
package appears only under ``rich``'s own ``dependencies`` -- so a pin resting on
it would rest on a package nothing here declares. Every Markdown claim below is
therefore structural: what the escape returns, not what a renderer makes of it.
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

from theurian.application.okf_codec import (
    CONCEPT_FRONT_MATTER_KEY_ORDER,
    LINE_TERMINATORS,
    OKF_KEYS,
    OKF_SOURCES,
    OKF_SPEC_VERSION,
    OKF_VERSION,
    SOURCE_ANCHOR_KEY_ORDER,
    THEURIAN_KEYS,
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
from theurian.domain.errors import InvariantViolationError
from theurian.domain.values import MediaType

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

pytestmark = pytest.mark.unit

_GENERATED = GeneratedBy(by="theurian/0.4.0", at="2026-09-24T12:00:00+00:00")

#: The six spellings a line terminator reaches a structural site as: the five
#: single characters the codec records, plus the ``\r\n`` pair, which folds to
#: one space rather than two and so is its own case.
_TERMINATOR_CASES: Final[tuple[str, ...]] = ("\r\n", *sorted(LINE_TERMINATORS))

#: A forged row string that carries both structural attacks at once: a
#: governance-looking ``key: value`` line, and a bare ``---`` that a consumer
#: splitting front matter on fences would truncate at.
_FORGED = "value{t}---{t}theurian_sensitivity: public"

_FENCE_LINE = re.compile(r"^(---|\.\.\.)\s*$")

#: The inline delimiters a link label must not leave live, spelled as literals
#: rather than imported from the module under test: a check that ranged over the
#: production set would range over one fewer the moment a member was dropped,
#: and still pass.
_INLINE_DELIMITERS: Final[tuple[str, ...]] = ("[", "]", "<", ">")

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
    """*block* split on every :data:`LINE_TERMINATORS` member, not on ``\\n``.

    ``re.MULTILINE``'s ``^`` matches after ``\\n`` and after nothing else, so a
    scan built on it is blind to exactly the terminators the codec was found
    passing through. Splitting on the codec's own population is what makes the
    key and fence scans below see what a `\\r`- or NEL-split consumer sees.
    """
    return re.split("\r\n|[" + "".join(sorted(LINE_TERMINATORS)) + "]", block)


def _top_level_keys(block: str) -> list[str]:
    """Every unindented ``key:`` in *block*, in document order.

    Distinct from parsing the YAML into a dict: a dict cannot show two entries
    sharing one key, so this is what actually answers "did a second
    `theurian_sensitivity:` appear", regardless of what the parser resolves it to.
    """
    return [
        match.group(1) for line in _physical_lines(block) if (match := _TOP_LEVEL_KEY.match(line))
    ]


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

#: Every value names its own field, so a swapped pair reads as a swap rather
#: than as two plausible strings. Three properties ride on the specific values:
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
    that checks the key *set*. Every value here names the field it belongs to, so
    a swapped pair, a reordered key, a `note: null` where an omission belongs, a
    lost anchor field, and the ordering the relation tie resolves to are all one
    diff against these bytes.
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
@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_a_forged_terminator_stays_inside_its_own_value_at_every_position(
    position: str, terminator: str
) -> None:
    """GWT: any row string, any line terminator, both structural forgeries.

    Given a value carrying a terminator followed by a bare `---` and by a
    governance-looking `theurian_sensitivity: public` line, when it is encoded at
    *position* and re-parsed: the block opens exactly the keys the encoder wrote,
    no physical line inside it is a front-matter fence, and the value comes back
    byte-identical -- terminator and all.

    Three assertions, three consumers: one that parses YAML, one that splits on
    `---` first (which is how front matter gets found at all), and the importer
    S3 will build, for which an altered value is data loss rather than forgery.
    """
    forged = _FORGED.format(t=terminator)
    block = encode_concept_front_matter(_FORGE_BUILDERS[position](forged))

    assert _top_level_keys(block) == list(CONCEPT_FRONT_MATTER_KEY_ORDER)

    inner = _physical_lines(block[len("---\n") : -len("---\n")])
    assert not [line for line in inner if _FENCE_LINE.match(line)], block

    assert forged in set(_string_values(_front_matter_mapping(block))), block


@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_a_forged_terminator_stays_inside_the_manifest_digest(terminator: str) -> None:
    """The manifest's own string position, held to the same three assertions.

    :data:`_FORGE_BUILDERS` covers :class:`ConceptFrontMatter`;
    :class:`ManifestFrontMatter` carries one string of its own, and it reaches a
    front-matter block through the same serializer.
    """
    forged = _FORGED.format(t=terminator)
    block = encode_manifest_front_matter(ManifestFrontMatter(theurian_bundle_digest=forged))

    assert _top_level_keys(block) == ["type", "theurian_export_version", "theurian_bundle_digest"]

    inner = _physical_lines(block[len("---\n") : -len("---\n")])
    assert not [line for line in inner if _FENCE_LINE.match(line)], block

    assert _front_matter_mapping(block)["theurian_bundle_digest"] == forged


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
# Markdown-syntax escapes: the single-line fold, then that line's own grammar.
# ---------------------------------------------------------------------------


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

    assert not LINE_TERMINATORS & set(escaped), repr(escaped)
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

    assert not LINE_TERMINATORS & set(escaped), repr(escaped)
    assert escaped == "benign  ## Relations  * x.y"


@pytest.mark.parametrize("terminator", _TERMINATOR_CASES)
def test_a_leading_terminator_folds_into_indentation_the_atx_rule_still_covers(
    terminator: str,
) -> None:
    """The fold moves the heading run, it does not put it out of reach.

    A terminator at the front becomes one space of indentation, which is inside
    CommonMark's three-space ATX window -- so the rule has to fire on the folded
    line, not on the line the caller passed.
    """
    assert escape_markdown_list_line(f"{terminator}# Injected") == " \\# Injected"


@pytest.mark.parametrize("delimiter", _INLINE_DELIMITERS)
def test_every_inline_delimiter_is_escaped_by_the_link_text_rule(delimiter: str) -> None:
    """One case per member, so dropping one reddens here.

    The parity re-derivation below ranges over what it finds in the *output*: if
    `<` stopped being escaped it would range over one delimiter fewer and still
    pass. This is the half that cannot.
    """
    assert escape_markdown_link_text(f"a{delimiter}b") == f"a\\{delimiter}b"


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


def test_every_escaped_delimiter_leaves_an_odd_backslash_run_before_it() -> None:
    """The parity argument, re-derived rather than asserted about one input.

    A caller-supplied backslash must not pair with an inserted escape and cancel
    it, which would resurrect the live `](` the escape exists to kill. What
    makes CommonMark consume the run as an escape *of that delimiter* is its
    odd length, so that is what is checked -- for every delimiter in the output,
    on an input asserted below to carry all four.
    """
    hostile = "evil\\](x) and \\[this\\] and <https://attacker/h> and >"

    assert set(_INLINE_DELIMITERS) <= set(hostile), (
        f"{hostile!r} does not carry every member of {_INLINE_DELIMITERS}, so the "
        f"re-derivation below would range over a subset and pass for the wrong reason"
    )

    escaped = escape_markdown_link_text(hostile)
    found = re.findall(r"(\\*)([\[\]<>])", escaped)

    assert len(found) == sum(escaped.count(member) for member in _INLINE_DELIMITERS)
    for run, delimiter in found:
        assert len(run) % 2 == 1, f"{delimiter!r} at even parity in {escaped!r}"


def test_escape_markdown_list_line_escapes_a_leading_heading_run() -> None:
    assert escape_markdown_list_line("#Attack") == "\\#Attack"
    assert escape_markdown_list_line("### Attack") == "\\### Attack"


def test_escape_markdown_list_line_escapes_within_atx_indentation() -> None:
    assert escape_markdown_list_line("  ##Attack") == "  \\##Attack"
    assert escape_markdown_list_line("   #Attack") == "   \\#Attack"


def test_escape_markdown_list_line_leaves_non_heading_text_untouched() -> None:
    assert escape_markdown_list_line("plain note") == "plain note"
    assert escape_markdown_list_line("a # mid-line hash") == "a # mid-line hash"
    # Four leading spaces is outside the three-space window `_ATX_HEADING_PREFIX`
    # is written against; what a renderer makes of it depends on the block the
    # exporter renders the note into, which is not this rule's claim.
    assert escape_markdown_list_line("    #not-a-heading") == "    #not-a-heading"


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_encoding_is_a_pure_function_of_its_input() -> None:
    front_matter = _populated()

    assert encode_concept_front_matter(front_matter) == encode_concept_front_matter(front_matter)
    assert encode_root_index_front_matter() == encode_root_index_front_matter()
