"""The OKF front-matter codec's encoder half (ADR-0037 decisions 2, 4, 7).

Scoped to this module's own functions and types. The bundle-level properties
-- the two-corpora battery, cross-run determinism over a whole export, the
reserved-name collision escape -- are a later assignment's exporter batteries;
what is pinned here is that the codec itself never lets a row's own text forge
YAML or Markdown structure, and that its emission shape (key order, what is
omitted, what is normalized) is fixed rather than incidental.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from theurian.application.okf_codec import (
    CONCEPT_FRONT_MATTER_KEY_ORDER,
    OKF_KEYS,
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
from theurian.domain.values import MediaType

pytestmark = pytest.mark.unit

_GENERATED = GeneratedBy(by="theurian/0.4.0", at="2026-09-24T12:00:00+00:00")


def _concept(**overrides: object) -> ConceptFrontMatter:
    fields: dict[str, object] = {
        "kind": "architecture",
        "title": "Authentication and authorization policy",
        "status": "stable",
        "theurian_status": "approved",
        "generated": _GENERATED,
        "theurian_export_version": 1,
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


def _front_matter_mapping(block: str) -> dict[str, Any]:
    """Parse an encoded block's YAML body, stripping the two `---` fences."""
    assert block.startswith("---\n") and block.endswith("---\n"), (
        f"{block!r} is not fenced front matter"
    )
    return dict(yaml.safe_load(block[len("---\n") : -len("---\n")]))


def _top_level_keys(block: str) -> list[str]:
    """Every unindented ``key:`` in *block*, in document order.

    Distinct from parsing the YAML into a dict: a dict cannot show two entries
    sharing one key, so this is what actually answers "did a second
    `theurian_sensitivity:` appear", regardless of what the parser resolves it to.
    """
    return re.findall(r"^([A-Za-z_]+):", block, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Key vocabulary.
# ---------------------------------------------------------------------------


def test_the_theurian_key_vocabulary_is_the_thirteen_keys_the_adr_spells() -> None:
    assert THEURIAN_KEYS == (
        "theurian_export_version",
        "theurian_bundle_digest",
        "theurian_item_id",
        "theurian_revision_id",
        "theurian_status",
        "theurian_namespace",
        "theurian_owner",
        "theurian_trust_level",
        "theurian_sensitivity",
        "theurian_content_type",
        "theurian_body_file",
        "theurian_relations",
        "theurian_anchor",
    )


def test_the_okf_key_vocabulary_is_the_eight_keys_the_export_emits() -> None:
    assert OKF_KEYS == (
        "type",
        "title",
        "tags",
        "status",
        "stale_after",
        "generated",
        "sources",
        "okf_version",
    )


# ---------------------------------------------------------------------------
# Concept front matter: key order, omission, and the YAML escaping property.
# ---------------------------------------------------------------------------


def test_the_concept_key_order_is_fixed_and_every_key_is_present_when_populated() -> None:
    front_matter = _concept(stale_after="2027-01-01", theurian_body_file="auth-policy.json")
    block = encode_concept_front_matter(front_matter)

    assert list(_front_matter_mapping(block)) == list(CONCEPT_FRONT_MATTER_KEY_ORDER)


def test_stale_after_and_body_file_are_omitted_rather_than_emitted_null() -> None:
    block = encode_concept_front_matter(_concept())
    parsed = _front_matter_mapping(block)

    assert "stale_after" not in parsed
    assert "theurian_body_file" not in parsed
    assert parsed["tags"] == []
    assert parsed["sources"] == []
    assert parsed["theurian_relations"] == []


def test_a_title_carrying_a_forged_sensitivity_line_stays_one_title_and_one_key() -> None:
    """GWT: a title with an embedded newline followed by a governance-looking line.

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


@pytest.mark.parametrize(
    "row_field",
    ["title", "theurian_owner"],
)
def test_every_row_string_is_yaml_escaped_not_only_the_title(row_field: str) -> None:
    """The same forgery through the other row strings the acceptance criterion names."""
    forged = "value\ntheurian_sensitivity: public"
    block = encode_concept_front_matter(_concept(**{row_field: forged}))

    assert _top_level_keys(block).count("theurian_sensitivity") == 1
    assert _front_matter_mapping(block)[row_field] == forged


def test_a_forged_label_and_note_are_also_yaml_escaped() -> None:
    forged = "value\ntheurian_sensitivity: public"
    block = encode_concept_front_matter(
        _concept(
            labels=(forged,),
            theurian_relations=(RelationEntry(type="relates_to", target="x.y", note=forged),),
        )
    )

    assert _top_level_keys(block).count("theurian_sensitivity") == 1
    parsed = _front_matter_mapping(block)
    assert parsed["tags"] == [forged]
    assert parsed["theurian_relations"][0]["note"] == forged


# ---------------------------------------------------------------------------
# Ordering: relations are normalized, sources and labels are not.
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


def test_sources_and_labels_keep_the_callers_own_order() -> None:
    sources = (
        SourceEntry(
            resource="https://example.com/z",
            anchor=SourceAnchorProjection(
                provider="git",
                repository="acme/z",
                commit_sha="deadbeef",
                file_path="z.md",
                line_start=1,
                line_end=2,
            ),
        ),
        SourceEntry(
            resource="https://example.com/a",
            anchor=SourceAnchorProjection(
                provider="git",
                repository="acme/a",
                commit_sha="cafebabe",
                file_path="a.md",
                line_start=3,
                line_end=4,
            ),
        ),
    )
    front_matter = _concept(labels=("z-label", "a-label"), sources=sources)
    parsed = _front_matter_mapping(encode_concept_front_matter(front_matter))

    assert parsed["tags"] == ["z-label", "a-label"]
    assert [entry["resource"] for entry in parsed["sources"]] == [
        "https://example.com/z",
        "https://example.com/a",
    ]


def test_the_source_anchor_carries_exactly_the_six_served_fields() -> None:
    source = SourceEntry(
        resource="https://example.com/a",
        anchor=SourceAnchorProjection(
            provider="github",
            repository="acme/a",
            commit_sha="deadbeef",
            file_path="a.md",
            line_start=1,
            line_end=2,
        ),
    )
    parsed = _front_matter_mapping(encode_concept_front_matter(_concept(sources=(source,))))
    anchor = parsed["sources"][0]["theurian_anchor"]

    assert set(anchor) == {
        "provider",
        "repository",
        "commit_sha",
        "file_path",
        "line_start",
        "line_end",
    }


# ---------------------------------------------------------------------------
# Root index and manifest front matter.
# ---------------------------------------------------------------------------


def test_the_root_index_front_matter_is_okf_version_and_nothing_else() -> None:
    parsed = _front_matter_mapping(encode_root_index_front_matter())

    assert parsed == {"okf_version": "0.2"}
    assert isinstance(parsed["okf_version"], str)


def test_the_manifest_front_matter_has_no_generated_and_no_content_type() -> None:
    manifest = ManifestFrontMatter(theurian_export_version=1, theurian_bundle_digest="ab" * 32)
    parsed = _front_matter_mapping(encode_manifest_front_matter(manifest))

    assert parsed == {
        "type": "Theurian Bundle",
        "theurian_export_version": 1,
        "theurian_bundle_digest": "ab" * 32,
    }


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
# Markdown-syntax escapes.
# ---------------------------------------------------------------------------


def test_escape_markdown_link_text_leaves_plain_text_untouched() -> None:
    assert escape_markdown_link_text("architecture.auth-policy") == "architecture.auth-policy"


def test_escape_markdown_link_text_escapes_brackets() -> None:
    assert escape_markdown_link_text("a[b]c") == "a\\[b\\]c"


def test_escape_markdown_link_text_survives_a_preexisting_backslash() -> None:
    """A caller-supplied backslash must not pair with an inserted escape and
    cancel it, which would resurrect the live `](` the escape exists to kill.
    """
    escaped = escape_markdown_link_text("evil\\](x)")

    assert escaped == "evil" + "\\" * 3 + "](x)"
    # Re-derive the parity argument directly: every backslash run immediately
    # preceding a literal "]" or "[" in the output must have odd length, which
    # is what makes CommonMark consume it as an escape of that bracket rather
    # than resolving to a live one.
    for match in re.finditer(r"(\\*)[\[\]]", escaped):
        assert len(match.group(1)) % 2 == 1, escaped


def test_escape_markdown_list_line_escapes_a_leading_heading_run() -> None:
    assert escape_markdown_list_line("#Attack") == "\\#Attack"
    assert escape_markdown_list_line("### Attack") == "\\### Attack"


def test_escape_markdown_list_line_escapes_within_atx_indentation() -> None:
    assert escape_markdown_list_line("  ##Attack") == "  \\##Attack"
    assert escape_markdown_list_line("   #Attack") == "   \\#Attack"


def test_escape_markdown_list_line_leaves_non_heading_text_untouched() -> None:
    assert escape_markdown_list_line("plain note") == "plain note"
    assert escape_markdown_list_line("a # mid-line hash") == "a # mid-line hash"
    # Four leading spaces is a code block in CommonMark, never an ATX heading.
    assert escape_markdown_list_line("    #not-a-heading") == "    #not-a-heading"


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_encoding_is_a_pure_function_of_its_input() -> None:
    front_matter = _concept(
        stale_after="2027-01-01",
        theurian_relations=(RelationEntry(type="relates_to", target="x.y", note="n"),),
    )

    assert encode_concept_front_matter(front_matter) == encode_concept_front_matter(front_matter)
    assert encode_root_index_front_matter() == encode_root_index_front_matter()
